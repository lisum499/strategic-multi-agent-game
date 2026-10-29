"""模型输出的解析与校验。

原则：解析可以回退（代码围栏、前后解释、首末花括号），但**校验失败绝不默认**。
失败返回错误，由引擎决定重试或记系统失败。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .tools import rules_v4 as R

BASIS_KEYS = ("依据", "风险", "不确定")
BASIS_LIMITS = {"依据": 20, "风险": 20, "不确定": 15}
STATEMENT_LIMIT = 40
# 环境摘要的 source_ids 上限。聚合陈述（如"七国均宣称…"）会自然引用多个来源，
# 早期版本把上限定为 3，导致一半环境摘要被硬判失败重试。
ENV_SUMMARY_SOURCE_LIMIT = 8
# 识别针对模型指令层或输出格式的干预，不将普通外交要求视为模型指令。
# 这只是有限的风险过滤，并非可以识别所有提示注入。
META_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"忽略.{0,10}(?:规则|指令|提示|系统信息|以上|之前|前述)",
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)",
    r"你(?:现在)?(?:必须|应当|应该|需要)\s*(?:选择|选取|选用|选|输出|回复|回答|返回)\s*(?:选项|option|[ADP]\d|JSON|json|字段|代码|文字)",
    r"你现在是(?:一个)?(?:AI|人工智能|语言模型|聊天机器人|助手|系统)",
))

FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


class ResponseError(ValueError):
    pass


def parse_json(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ResponseError("模型输出为空")
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        fence = FENCE.search(text)
        if fence:
            try:
                data = json.loads(fence.group(1))
            except json.JSONDecodeError:
                data = None
        else:
            data = None
        if data is None:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise ResponseError("找不到 JSON 对象")
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise ResponseError(f"JSON 解析失败：{exc}") from None
    if not isinstance(data, dict):
        raise ResponseError("输出必须是 JSON 对象")
    return data


def _require_keys(data: dict[str, Any], allowed: set[str], *,
                  warnings: list[str] | None = None,
                  tolerate_extra: bool = False) -> None:
    """校验字段集合。

    缺少必填字段始终失败。多余字段有三种处理方式：
    - 传入 ``warnings``：记录警告并忽略（模型决策输出用，避免为一个空字段重试）；
    - ``tolerate_extra=True``：静默忽略（环境摘要等只读字段用）；
    - 其余情况：保持严格，直接失败。
    """
    missing = sorted(allowed - set(data))
    if missing:
        raise ResponseError(f"缺少字段：{missing}")
    extra = sorted(set(data) - allowed)
    if not extra:
        return
    if warnings is not None:
        warnings.append(f"忽略未支持字段：{extra}")
        return
    if tolerate_extra:
        return
    raise ResponseError(f"包含未支持字段：{extra}")


def _clean_text(value: Any, name: str, limit: int, *, allow_empty: bool = False,
                warnings: list[str] | None = None) -> str:
    if not isinstance(value, str):
        raise ResponseError(f"{name} 必须是字符串")
    text = value.replace("\n", " ").replace("\r", " ").strip()
    if not text and not allow_empty:
        raise ResponseError(f"{name} 不能为空")
    if len(text) > limit:
        if warnings is None:
            raise ResponseError(f"{name} 超过 {limit} 字")
        warnings.append(f"{name} 超长，已截断至 {limit} 字")
        text = text[:limit]
    return text


def validate_basis(data: dict[str, Any], warnings: list[str] | None = None) -> dict[str, str]:
    basis = data.get("decision_basis")
    if not isinstance(basis, dict):
        raise ResponseError("decision_basis 必须是对象")
    missing = sorted(set(BASIS_KEYS) - set(basis))
    if missing:
        raise ResponseError(f"decision_basis 缺少键：{missing}")
    extra = sorted(set(basis) - set(BASIS_KEYS))
    if extra:
        message = f"decision_basis 忽略未支持字段：{extra}"
        if warnings is None:
            raise ResponseError(message)
        warnings.append(message)
    return {key: _clean_text(basis[key], f"decision_basis.{key}", BASIS_LIMITS[key],
                             warnings=warnings)
            for key in BASIS_KEYS}


def sanitize_statement(value: Any) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if value is None:
        return "", warnings
    if not isinstance(value, str):
        raise ResponseError("public_statement 必须是字符串")
    text = value.replace("\n", " ").replace("\r", " ").strip()
    if any(pattern.search(text) for pattern in META_PATTERNS):
        warnings.append("public_statement 含针对模型或规则的指令，已丢弃（指令性语句）")
        return "", warnings
    if len(text) > STATEMENT_LIMIT:
        text = text[:STATEMENT_LIMIT]
        warnings.append("public_statement 超长，已截断")
    return text, warnings


STAT_KEYS = ("c", "e", "m")


def _validate_threat_observation(value: Any, situation: dict[str, Any],
                                 warnings: list[str] | None = None) -> dict[str, Any]:
    """验证私人主观判断的形式和可见来源，不判断判断是否为事实。"""
    if not isinstance(value, dict):
        raise ResponseError("threat_observation 必须是对象")
    _require_keys(value, {"target_country_id", "level", "basis", "source_ids"},
                  warnings=warnings)
    target = value["target_country_id"]
    others = {item["country_id"] for item in situation["state"]["countries"]
              if item["country_id"] != situation["self"]["country_id"]}
    if target is not None and (not isinstance(target, str) or target not in others):
        raise ResponseError("threat_observation.target_country_id 必须是可见的其他存活国家或 null")
    level = value["level"]
    if not isinstance(level, str) or level not in ("急迫", "中", "缓和", "未知"):
        raise ResponseError("threat_observation.level 必须是 急迫/中/缓和/未知")
    basis = _clean_text(value["basis"], "threat_observation.basis", 60, warnings=warnings)
    source_ids = value["source_ids"]
    if (not isinstance(source_ids, list) or len(source_ids) > 3
            or not all(isinstance(item, str) and item for item in source_ids)):
        raise ResponseError("threat_observation.source_ids 必须是最多三个来源 ID")
    if level != "未知" and not source_ids:
        raise ResponseError("威胁判断须引用可见来源；未知允许空来源")
    valid = set(situation["threat_observation_sources"])
    if any(item not in valid for item in source_ids):
        raise ResponseError("threat_observation.source_ids 包含不可见或不存在的来源")
    return {"target_country_id": target, "level": level, "basis": basis,
            "source_ids": list(source_ids)}


def _validate_changes(changes: Any) -> dict[str, dict[str, float]]:
    if not isinstance(changes, dict):
        raise ResponseError("changes 必须是对象")
    if set(changes) != {"attacker", "defender"}:
        raise ResponseError("changes 必须且只能有 attacker / defender 两个键")
    step = R.NEGOTIATION["step"]
    cleaned: dict[str, dict[str, float]] = {}
    for side in ("attacker", "defender"):
        values = changes[side]
        if not isinstance(values, dict) or set(values) != set(STAT_KEYS):
            raise ResponseError(f"changes.{side} 必须且只能有 c/e/m 三项")
        cleaned[side] = {}
        for key in STAT_KEYS:
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ResponseError(f"changes.{side}.{key} 必须是数字")
            value = float(value)
            if not math.isfinite(value):
                raise ResponseError(f"changes.{side}.{key} 必须是有限数字")
            if abs(value / step - round(value / step)) > 1e-9:
                raise ResponseError(f"changes.{side}.{key} 必须是 0.5 的整数倍")
            if side == "attacker":
                if not 0.0 <= value <= R.NEGOTIATION["attacker_gain_max_each"]:
                    raise ResponseError(f"进攻方所得越界：{side}.{key}={value}")
            else:
                if not R.NEGOTIATION["defender_loss_min_each"] <= value <= 0.0:
                    raise ResponseError(f"防守方所失越界：{side}.{key}={value}")
            cleaned[side][key] = round(value, 1)
        total = round(sum(cleaned[side].values()), 1)
        if side == "attacker":
            if total > R.NEGOTIATION["attacker_gain_max_sum"]:
                raise ResponseError(f"进攻方所得合计越界：{total}")
        else:
            if total < R.NEGOTIATION["defender_loss_min_sum"]:
                raise ResponseError(f"防守方所失合计越界：{total}")
    # v4.0 零和：谈判是纯转移，攻方所得逐项等于守方所失。
    for key in STAT_KEYS:
        if abs(cleaned["attacker"][key] + cleaned["defender"][key]) > 1e-9:
            raise ResponseError(
                f"谈判方案必须逐项守恒：attacker.{key} + defender.{key} = 0")
    return cleaned


def validate_decision(raw: str, phase: str, situation: dict[str, Any]
                      ) -> tuple[dict[str, Any], list[str]]:
    """返回 (规范化决定, 警告)。失败抛 ResponseError。"""
    data = parse_json(raw)
    warnings: list[str] = []

    if phase in ("declaration", "action"):
        expected = {"decision_basis", "option_id", "public_statement"}
        if phase == "action" and "threat_observation_sources" in situation:
            expected.add("threat_observation")
        _require_keys(data, expected, warnings=warnings)
        option_ids = {item["option_id"] for item in situation["legal_options"]}
        option_id = data.get("option_id")
        if not isinstance(option_id, str) or option_id not in option_ids:
            raise ResponseError(f"option_id 不在本次合法选项中：{option_id!r}")
        statement, statement_warnings = sanitize_statement(data.get("public_statement"))
        warnings.extend(statement_warnings)
        result = {
            "decision_basis": validate_basis(data, warnings),
            "option_id": option_id,
            "public_statement": statement,
        }
        if "threat_observation" in expected:
            result["threat_observation"] = _validate_threat_observation(data["threat_observation"], situation, warnings)
        return result, warnings

    if phase == "negotiation":
        role = situation.get("role")
        if role == "defender":
            if "offers" in situation:
                _require_keys(data, {"decision_basis", "offer_id"},
                              warnings=warnings)
                offers = situation.get("offers", [])
                offer_ids = {item["offer_id"] for item in offers}
                offer_id = data.get("offer_id")
                if not isinstance(offer_id, str) or offer_id not in offer_ids:
                    raise ResponseError(f"offer_id 不在本次方案中：{offer_id!r}")
                return {"decision_basis": validate_basis(data, warnings),
                        "offer_id": offer_id}, warnings
            if "offer_bounds" in situation:
                _require_keys(data, {"decision_basis", "changes"},
                              warnings=warnings)
                changes = _validate_changes(data.get("changes"))
                offer = {"offer_id": "SELF", "label": "模型自提方案",
                         "changes": changes}
                return {"decision_basis": validate_basis(data, warnings),
                        "changes": changes, "offer": offer}, warnings
            raise ResponseError("防守方谈判缺少 offers 或 offer_bounds")
        if role == "attacker":
            _require_keys(data, {"decision_basis", "decision"},
                          warnings=warnings)
            decision = data.get("decision")
            if decision not in ("accept", "reject"):
                raise ResponseError(f"decision 必须是 accept 或 reject：{decision!r}")
            return {"decision_basis": validate_basis(data, warnings), "decision": decision}, warnings
        raise ResponseError(f"未知谈判角色：{role!r}")

    raise ResponseError(f"未知阶段：{phase!r}")


def validate_env_summary(raw: str, expected_round: int,
                         valid_source_ids: set[str] | None = None) -> dict[str, Any]:
    data = parse_json(raw)
    _require_keys(data, {"round", "facts", "interpretations", "open_questions"},
                  tolerate_extra=True)
    if data.get("round") != expected_round:
        raise ResponseError(f"round 必须等于 {expected_round}：{data.get('round')!r}")

    def _items(name: str, limit: int, *, with_uncertainty: bool) -> list[dict[str, Any]]:
        items = data.get(name)
        if not isinstance(items, list):
            raise ResponseError(f"{name} 必须是数组")
        if len(items) > limit:
            raise ResponseError(f"{name} 最多 {limit} 条")
        cleaned: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ResponseError(f"{name} 的条目必须是对象")
            expected = {"text", "source_ids"}
            if with_uncertainty:
                expected.add("uncertainty")
            missing = sorted(expected - set(item))
            if missing:
                raise ResponseError(f"{name} 条目缺少字段：{missing}")
            text = _clean_text(item.get("text"), f"{name}.text", 60)
            ids = item.get("source_ids")
            if (not isinstance(ids, list) or not ids
                    or len(ids) > ENV_SUMMARY_SOURCE_LIMIT):
                raise ResponseError(
                    f"{name}.source_ids 必须是 1~{ENV_SUMMARY_SOURCE_LIMIT} 个 id 的数组")
            if not all(isinstance(x, str) and x for x in ids):
                raise ResponseError(f"{name}.source_ids 必须是非空字符串")
            if valid_source_ids is not None:
                unknown = [item for item in ids if item not in valid_source_ids]
                if unknown:
                    raise ResponseError(
                        f"{name}.source_ids 引用了不存在的来源：{unknown}")
            entry: dict[str, Any] = {"text": text, "source_ids": list(ids)}
            if with_uncertainty:
                entry["uncertainty"] = _clean_text(
                    item.get("uncertainty"), f"{name}.uncertainty", 30)
            cleaned.append(entry)
        return cleaned

    facts = _items("facts", 6, with_uncertainty=False)
    if not facts:
        raise ResponseError("facts 至少 1 条；可引用 state 描述当前公开快照中的事实")
    interpretations = _items("interpretations", 3, with_uncertainty=True)
    questions = data.get("open_questions")
    if not isinstance(questions, list) or len(questions) > 3:
        raise ResponseError("open_questions 必须是最多 3 条的数组")
    cleaned_questions = [_clean_text(item, "open_questions", 30) for item in questions]
    # 只计算内容字符，不包括 JSON 字段名、来源 ID 或格式化符号。
    total_chars = (sum(len(item["text"]) for item in facts)
                   + sum(len(item["text"]) + len(item["uncertainty"])
                         for item in interpretations)
                   + sum(len(item) for item in cleaned_questions))
    if total_chars > 500:
        raise ResponseError(f"环境摘要正文合计超过 500 字：{total_chars}")
    return {
        "round": expected_round,
        "facts": facts,
        "interpretations": interpretations,
        "open_questions": cleaned_questions,
    }
