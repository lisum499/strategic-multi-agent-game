"""Audited mock, scripted, DeepSeek and OpenAI-compatible model providers."""

from __future__ import annotations

import json
import http.client
import math
import os
import random
import time
import threading
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from .tools.render_user import PROMPT_DIR

ENV_PROMPT_FILE = PROMPT_DIR / "30-env-summary.md"


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "base"
    model = "none"

    @property
    def last_call_info(self) -> dict[str, Any]:
        local = getattr(self, "_call_local", None)
        return getattr(local, "info", {}) if local is not None else {}

    @last_call_info.setter
    def last_call_info(self, value: dict[str, Any]) -> None:
        if not hasattr(self, "_call_local"):
            self._call_local = threading.local()
        self._call_local.info = value

    def decide(self, phase: str, system: str, user: str,
               situation: dict[str, Any]) -> str:
        raise NotImplementedError

    def summarize(self, round_no: int, public_events: list[dict[str, Any]],
                  public_state: dict[str, Any],
                  previous_summary: dict[str, Any] | None,
                  public_declarations: list[dict[str, Any]] | None = None,
                  public_actions: list[dict[str, Any]] | None = None) -> str:
        raise NotImplementedError

    def summarize_with_feedback(self, round_no: int, public_events: list[dict[str, Any]],
                                public_state: dict[str, Any],
                                previous_summary: dict[str, Any] | None,
                                public_declarations: list[dict[str, Any]] | None,
                                public_actions: list[dict[str, Any]] | None,
                                feedback: str) -> str:
        """兼容离线及自定义 provider；可由真实模型适配器使用错误反馈。"""
        return self.summarize(round_no, public_events, public_state, previous_summary,
                              public_declarations, public_actions)


def _basis() -> dict[str, str]:
    return {"依据": "按当前公开信息选择", "风险": "对手可能反制", "不确定": "对手意图不明"}


class MockProvider(Provider):
    """离线、确定性的替代模型：只用来验证流程与校验，不代表模型行为。"""

    name = "mock"
    model = "mock-script-v1"

    def decide(self, phase: str, system: str, user: str,
               situation: dict[str, Any]) -> str:
        country = situation["self"]["country_id"]
        round_no = situation["round"]
        rng = random.Random(zlib.crc32(f"{country}|{round_no}|{phase}".encode()))

        if phase != "sealed":
            raise ProviderError(f"只支持 sealed 决策协议：{phase}")
        from .protocol import make_fallback
        response = make_fallback(situation)
        declarations = situation["legal_declarations"]
        # Mix action types for an offline flow probe; never interpret mock output
        # as observed model behavior or a calibrated crisis distribution.
        chosen = rng.choice(declarations)
        execution = rng.choice(chosen["execution_options"])
        response["declaration"] = {"option_id": chosen["option_id"], "public_statement": "维护本国安全与利益"}
        response["execution"] = {"option_id": execution["option_id"], "public_statement": "按既定方针行动"}
        response["private_rationale"] = "离线固定种子流程测试"
        for war_id, context in situation["negotiation_contexts"].items():
            if situation["negotiation_mode"] == "offers":
                response["negotiation"][war_id] = (
                    {"offer_id": rng.choice(context["offers"])["offer_id"]}
                    if context["role"] == "defender" else
                    {"accept_offer_ids": ["P1", "P2", "P3", "P4"]})
            elif context["role"] == "attacker":
                response["negotiation"][war_id] = {"accept": True, "min_total_gain": 0.5,
                    "min_gains": {"c": 0.0, "e": 0.0, "m": 0.0}}
        return json.dumps(response, ensure_ascii=False)

    def summarize(self, round_no: int, public_events: list[dict[str, Any]],
                  public_state: dict[str, Any],
                  previous_summary: dict[str, Any] | None,
                  public_declarations: list[dict[str, Any]] | None = None,
                  public_actions: list[dict[str, Any]] | None = None) -> str:
        facts = []
        for event in public_events[-5:]:
            facts.append({"text": event["text"][:60], "source_ids": [event["id"]]})
        if not facts:
            facts = [{"text": "本轮公开状态快照可供查看", "source_ids": ["state"]}]
        return json.dumps({
            "round": round_no,
            "facts": facts,
            "interpretations": [],
            "open_questions": [],
        }, ensure_ascii=False)


class ScriptedProvider(Provider):
    """按 (国家, 回合, 阶段) 返回固定文本；测试校验与重试用。"""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: dict[str, list[str]] | dict[str, str]):
        self.responses = {key: (value if isinstance(value, list) else [value])
                          for key, value in responses.items()}
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def key(country_id: str, round_no: int, phase: str) -> str:
        return f"{country_id}|{round_no}|{phase}"

    def decide(self, phase: str, system: str, user: str,
               situation: dict[str, Any]) -> str:
        key = self.key(situation["self"]["country_id"], situation["round"], phase)
        self.calls.append((key, user))
        queue = self.responses.get(key)
        if not queue:
            raise ProviderError(f"没有为 {key} 准备响应")
        return queue[0] if len(queue) == 1 else queue.pop(0)

    def summarize(self, round_no: int, public_events: list[dict[str, Any]],
                  public_state: dict[str, Any],
                  previous_summary: dict[str, Any] | None,
                  public_declarations: list[dict[str, Any]] | None = None,
                  public_actions: list[dict[str, Any]] | None = None) -> str:
        key = f"env|{round_no}"
        queue = self.responses.get(key)
        if not queue:
            raise ProviderError(f"没有为 {key} 准备环境摘要")
        return queue[0] if len(queue) == 1 else queue.pop(0)


class DeepSeekProvider(Provider):
    """DeepSeek JSON 模式；没有 API key 时构造即报错。"""

    name = "deepseek"

    def __init__(self, model: str = "deepseek-flash", *, api_key: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 2800,
                 timeout: int = 90):
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("model 必须是非空字符串")
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ProviderError("temperature 必须在 0~2 之间")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise ProviderError("max_tokens 必须是正整数")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ProviderError("timeout 必须大于 0")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ProviderError("未设置 DEEPSEEK_API_KEY；请先配置环境变量")
        self.url = "https://api.deepseek.com/chat/completions"
        self.last_call_info: dict[str, Any] = {}

    def _chat(self, system: str, user: str, max_tokens: int) -> str:
        self.last_call_info = {"requested_model": self.model, "max_tokens": max_tokens}
        started_at = time.monotonic()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.name != "deepseek":
            body.pop("thinking", None)
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.last_call_info["http_status"] = getattr(response, "status", None)
                raw_response = response.read()
        except urllib.error.HTTPError as exc:
            self.last_call_info["http_status"] = exc.code
            self.last_call_info["duration_ms"] = round((time.monotonic() - started_at) * 1000)
            raise ProviderError(f"模型服务 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            self.last_call_info["duration_ms"] = round((time.monotonic() - started_at) * 1000)
            raise ProviderError("模型服务连接失败或超时") from None
        self.last_call_info["duration_ms"] = round((time.monotonic() - started_at) * 1000)
        try:
            payload = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError):
            raise ProviderError("模型服务返回非 JSON 响应") from None
        if not isinstance(payload, dict):
            raise ProviderError("模型服务返回格式无效")
        if isinstance(payload.get("model"), str):
            self.last_call_info["returned_model"] = payload["model"]
        if isinstance(payload.get("usage"), dict):
            # 不记录服务端错误文本或其它可能包含敏感内容的响应字段。
            self.last_call_info["usage"] = {
                key: val for key, val in payload["usage"].items()
                if key in ("prompt_tokens", "completion_tokens", "total_tokens",
                           "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
                and isinstance(val, (int, float)) and not isinstance(val, bool)
            }
        try:
            choice = payload["choices"][0]
            if not isinstance(choice, dict):
                raise TypeError("choice must be an object")
            self.last_call_info["finish_reason"] = choice.get("finish_reason")
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("模型服务返回格式无效") from None
        if choice.get("finish_reason") == "length":
            raise ProviderError("模型输出被截断（finish_reason=length）")
        if choice.get("finish_reason") not in ("stop", None):
            raise ProviderError("模型输出未正常完成")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型返回空内容")
        return content

    def decide(self, phase: str, system: str, user: str,
               situation: dict[str, Any]) -> str:
        return self._chat(system, user, self.max_tokens)

    def summarize(self, round_no: int, public_events: list[dict[str, Any]],
                  public_state: dict[str, Any],
                  previous_summary: dict[str, Any] | None,
                  public_declarations: list[dict[str, Any]] | None = None,
                  public_actions: list[dict[str, Any]] | None = None) -> str:
        system, user = build_env_prompts(
            round_no, public_events, public_state, previous_summary,
            public_declarations, public_actions)
        return self._chat(system, user, max(900, self.max_tokens))

    def summarize_with_feedback(self, round_no: int, public_events: list[dict[str, Any]],
                                public_state: dict[str, Any],
                                previous_summary: dict[str, Any] | None,
                                public_declarations: list[dict[str, Any]] | None,
                                public_actions: list[dict[str, Any]] | None,
                                feedback: str) -> str:
        system, user = build_env_prompts(round_no, public_events, public_state,
                                         previous_summary, public_declarations, public_actions)
        user += ("\n\n上次环境摘要未通过校验或调用失败：" + feedback
                 + "。请仅基于原始输入重新生成 JSON；每条 source_ids 最多 8 个，"
                   "可拆分事实，不得编造或引用不可见来源。")
        return self._chat(system, user, max(900, self.max_tokens))


class OpenAICompatibleProvider(DeepSeekProvider):
    """The colleague's local-endpoint capability on the common audited provider API."""

    name = "openai"

    def __init__(self, model: str = "local", *, base_url: str = "http://127.0.0.1:8000/v1",
                 api_key: str | None = None, **kwargs: Any):
        from urllib.parse import urlparse
        if not isinstance(base_url, str) or urlparse(base_url).scheme not in ("http", "https") or not urlparse(base_url).netloc:
            raise ProviderError("base_url 必须是 HTTP(S) 接口地址")
        super().__init__(model=model, api_key=api_key or os.environ.get("OPENAI_API_KEY", "local"), **kwargs)
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/chat/completions"


def get_provider(name: str, **kwargs: Any) -> Provider:
    if name == "mock":
        return MockProvider()
    if name == "scripted":
        return ScriptedProvider(kwargs.get("responses", {}))
    if name == "deepseek":
        return DeepSeekProvider(**kwargs)
    if name == "openai":
        return OpenAICompatibleProvider(**kwargs)
    raise ProviderError(f"未知 provider：{name}")


def build_country_prompts(phase: str, situation: dict[str, Any]) -> tuple[str, str]:
    if phase != "sealed":
        raise ProviderError("只支持 sealed 决策协议")
    from .protocol import build_sealed_prompts
    return build_sealed_prompts(situation)


def build_env_prompts(round_no: int, public_events: list[dict[str, Any]],
                      public_state: dict[str, Any],
                      previous_summary: dict[str, Any] | None,
                      public_declarations: list[dict[str, Any]] | None = None,
                      public_actions: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    system = ENV_PROMPT_FILE.read_text(encoding="utf-8").strip()
    payload = {
        "round": round_no,
        "previous_summary": previous_summary,
        "public_declarations": public_declarations or [],
        "public_actions": public_actions or [],
        "public_events": public_events,
        "public_state": public_state,
    }
    return system, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
