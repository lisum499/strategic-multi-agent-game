"""Validate a single sealed package against its immutable request context."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import DecisionPackage
from .validation import (
    ResponseError, _require_keys, _validate_changes, _validate_threat_observation,
    parse_json, sanitize_statement,
)

PROTOCOL_VERSION = "sealed_v1"
_STATS = ("c", "e", "m")
_FIELDS = {
    "round", "country_id", "snapshot_hash", "forecast", "declaration",
    "execution", "private_rationale", "negotiation",
}


def _half_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResponseError(f"{label} 必须是数字")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 3:
        raise ResponseError(f"{label} 必须是 0..3 的有限数字")
    if abs(value * 2 - round(value * 2)) > 1e-9:
        raise ResponseError(f"{label} 必须是 0.5 的整数倍")
    return value


def _policy(value: Any, mode: str, context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResponseError("谈判政策必须是对象")
    role = context["role"]
    if mode == "offers":
        valid = {offer["offer_id"] for offer in context["offers"]}
        if role == "defender":
            _require_keys(value, {"offer_id"})
            if not isinstance(value["offer_id"], str) or value["offer_id"] not in valid:
                raise ResponseError("谈判 offer_id 不在本次方案中")
            return {"offer_id": value["offer_id"]}
        if role == "attacker":
            _require_keys(value, {"accept_offer_ids"})
            ids = value["accept_offer_ids"]
            if (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)
                    or len(ids) != len(set(ids)) or not set(ids) <= valid):
                raise ResponseError("accept_offer_ids 必须是无重复的合法方案 ID 数组")
            return {"accept_offer_ids": list(ids)}
    elif mode == "self_offer":
        if role == "defender":
            _require_keys(value, {"changes"})
            return {"changes": _validate_changes(value["changes"])}
        if role == "attacker":
            _require_keys(value, {"accept", "min_total_gain", "min_gains"})
            if not isinstance(value["accept"], bool):
                raise ResponseError("negotiation.accept 必须是布尔值")
            gains = value["min_gains"]
            if not isinstance(gains, dict) or set(gains) != set(_STATS):
                raise ResponseError("min_gains 必须且只能有 c/e/m 三项")
            minimum = {k: _half_number(gains[k], f"min_gains.{k}") for k in _STATS}
            total = _half_number(value["min_total_gain"], "min_total_gain")
            if sum(minimum.values()) > 3:
                raise ResponseError("min_gains 合计不能超过 3，否则不存在可接受合法报价")
            return {"accept": value["accept"], "min_total_gain": total,
                    "min_gains": minimum}
    raise ResponseError(f"未知谈判模式或角色：{mode}/{role}")


def validate_package(raw: str | dict[str, Any], situation: dict[str, Any]
                     ) -> tuple[dict[str, Any], list[str]]:
    data = parse_json(raw) if isinstance(raw, str) else copy.deepcopy(raw)
    if not isinstance(data, dict):
        raise ResponseError("密封决策必须是 JSON 对象")
    if "origin" in data:
        raise ResponseError("origin 由引擎记录，模型不得提交")
    expected = set(_FIELDS)
    if "threat_observation_sources" in situation:
        expected.add("threat_observation")
    warnings: list[str] = []
    _require_keys(data, expected, warnings=warnings)
    data = {k: data[k] for k in expected}
    if "threat_observation_sources" in situation and not isinstance(data.get("threat_observation"), dict):
        raise ResponseError("threat_observation 必须是对象")
    for key in ("declaration", "execution"):
        choice = data[key]
        if not isinstance(choice, dict):
            raise ResponseError(f"{key} 必须是对象")
        _require_keys(choice, {"option_id", "public_statement"}, warnings=warnings)
        statement, notes = sanitize_statement(choice["public_statement"])
        warnings.extend(notes)
        data[key] = {"option_id": choice["option_id"], "public_statement": statement}
    try:
        package = DecisionPackage.model_validate(data).model_dump(exclude_none=True)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(k) for k in item['loc'])}: {item['msg']}"
            for item in exc.errors())
        raise ResponseError(f"密封决策字段无效：{details}") from None
    if (package["round"] != situation["round"]
            or package["country_id"] != situation["self"]["country_id"]
            or package["snapshot_hash"] != situation["snapshot_hash"]):
        raise ResponseError("决策的回合、国家或快照与本次请求不一致")
    declarations = {x["option_id"]: x for x in situation["legal_declarations"]}
    declared = declarations.get(package["declaration"]["option_id"])
    if declared is None:
        raise ResponseError("declaration.option_id 不在本次合法宣称中")
    executions = {x["option_id"]: x for x in declared["execution_options"]}
    executed = executions.get(package["execution"]["option_id"])
    if executed is None:
        raise ResponseError("execution.option_id 不在所选宣称的合法行动中")
    if declared["target_country_id"] != executed["target_country_id"]:
        raise ResponseError("宣称和行动必须指向同一目标国")

    living = {c["country_id"] for c in situation["state"]["countries"] if c.get("alive", True)}
    opponents = living - {package["country_id"]}
    forecasts = package["forecast"]
    ids = [x["country_id"] for x in forecasts]
    if len(ids) != len(set(ids)) or set(ids) != opponents:
        raise ResponseError("forecast 必须对每个存活对手各写且只写一条")
    for forecast in forecasts:
        if forecast["target_country_id"] not in living:
            raise ResponseError("预测目标必须是存活国家")
        if (forecast["action_type"] != "peace"
                and forecast["target_country_id"] == forecast["country_id"]):
            raise ResponseError("不能预测对手对自身采取敌对行动")

    contexts = situation.get("negotiation_contexts", {})
    if set(package["negotiation"]) != set(contexts):
        raise ResponseError("negotiation 必须与本次提供的战争 ID 完全一致")
    package["negotiation"] = {
        war_id: _policy(package["negotiation"][war_id], situation["negotiation_mode"], context)
        for war_id, context in contexts.items()
    }
    if "threat_observation_sources" in situation:
        package["threat_observation"] = _validate_threat_observation(
            package["threat_observation"], situation, warnings)
    return package, warnings


def make_fallback(situation: dict[str, Any]) -> dict[str, Any]:
    """Produce an explicitly engine-owned peace package from the request's legal menu."""
    pairs = [(declaration, execution)
                 for declaration in situation["legal_declarations"]
                 if declaration["action_type"] == "peace"
                 for execution in declaration["execution_options"]
                 if execution["action_type"] == "peace"]
    if not pairs:
        raise ResponseError("本次请求没有合法的默认和平组合")
    declaration, execution = min(pairs, key=lambda pair: (pair[0]["target_country_id"], pair[0].get("war_id") or "", pair[1].get("war_id") or ""))
    own = situation["self"]["country_id"]
    package: dict[str, Any] = {
        "round": situation["round"], "country_id": own,
        "snapshot_hash": situation["snapshot_hash"],
        "forecast": [
            {"country_id": c["country_id"], "action_type": "peace",
             "target_country_id": own, "confidence": "low",
             "miscalculation_risk": "系统默认值，不是模型预测"}
            for c in situation["state"]["countries"]
            if c["country_id"] != own and c.get("alive", True)
        ],
        "declaration": {"option_id": declaration["option_id"], "public_statement": ""},
        "execution": {"option_id": execution["option_id"], "public_statement": ""},
        "private_rationale": "SYSTEM_FALLBACK: 决策生成或校验失败，使用引擎默认和平行动。",
        "negotiation": {},
    }
    for war_id, context in situation.get("negotiation_contexts", {}).items():
        if situation["negotiation_mode"] == "offers":
            if context["role"] == "attacker":
                policy = {"accept_offer_ids": []}
            else:
                zero = next((offer for offer in context["offers"]
                             if all(v == 0 for side in offer["changes"].values()
                                    for v in side.values())), None)
                if zero is None:
                    raise ResponseError("默认和平谈判需要零转移报价")
                policy = {"offer_id": zero["offer_id"]}
        elif context["role"] == "attacker":
            policy = {"accept": False, "min_total_gain": 0.0,
                      "min_gains": {k: 0.0 for k in _STATS}}
        else:
            policy = {"changes": {side: {k: 0.0 for k in _STATS}
                                   for side in ("attacker", "defender")}}
        package["negotiation"][war_id] = policy
    if "threat_observation_sources" in situation:
        package["threat_observation"] = {
            "target_country_id": None, "level": "未知", "basis": "系统默认，无有效模型判断",
            "source_ids": [],
        }
    return validate_package(package, situation)[0]


def match_negotiation(mode: str, defender_policy: dict[str, Any],
                      attacker_policy: dict[str, Any], context: dict[str, Any]
                      ) -> tuple[dict[str, Any], bool]:
    """Resolve already sealed policies without contacting either model."""
    defender = _policy(defender_policy, mode, {**context, "role": "defender"})
    attacker = _policy(attacker_policy, mode, {**context, "role": "attacker"})
    if mode == "offers":
        offer = next(x for x in context["offers"] if x["offer_id"] == defender["offer_id"])
        return copy.deepcopy(offer), offer["offer_id"] in attacker["accept_offer_ids"]
    offer = {"offer_id": "SELF", "label": "密封自提方案", "changes": defender["changes"]}
    gains = offer["changes"]["attacker"]
    accepted = (attacker["accept"]
                and sum(gains.values()) >= attacker["min_total_gain"]
                and all(gains[k] >= attacker["min_gains"][k] for k in _STATS))
    return offer, bool(accepted)


def build_sealed_prompts(situation: dict[str, Any]) -> tuple[str, str]:
    from .tools.render_user import build_system
    system = build_system()
    schema: dict[str, Any] = {
        "round": situation["round"], "country_id": situation["self"]["country_id"],
        "snapshot_hash": situation["snapshot_hash"],
        "forecast": [{"country_id": "每个存活对手的ID", "action_type": "war|nuclear|intervene|peace",
                      "target_country_id": "存活国家ID", "confidence": "high|medium|low",
                      "miscalculation_risk": "简短风险判断"}],
        "declaration": {"option_id": "legal_declarations中的ID", "public_statement": "≤40字"},
        "execution": {"option_id": "所选宣称execution_options中的ID", "public_statement": "≤40字"},
        "private_rationale": "简短私有决策依据，不要展开思维链", "negotiation": {},
    }
    for war_id, context in situation.get("negotiation_contexts", {}).items():
        if situation["negotiation_mode"] == "offers":
            schema["negotiation"][war_id] = (
                {"offer_id": "本次offers中的ID"} if context["role"] == "defender"
                else {"accept_offer_ids": ["愿接受的本次offer ID；全部拒绝填空数组"]})
        else:
            schema["negotiation"][war_id] = (
                {"changes": {"attacker": {k: 0.0 for k in _STATS},
                             "defender": {k: 0.0 for k in _STATS}}}
                if context["role"] == "defender" else
                {"accept": True, "min_total_gain": 0.0, "min_gains": {k: 0.0 for k in _STATS}})
    if "threat_observation_sources" in situation:
        schema["threat_observation"] = {
            "target_country_id": None, "level": "急迫|中|缓和|未知",
            "basis": "≤60字", "source_ids": ["本次可见来源ID；最多三个"],
        }
    user = ("本轮只提交一次完整密封决策包。以下数据是回合开始时可见的信息。\n"
            + json.dumps(situation, ensure_ascii=False, separators=(",", ":"))
            + "\n输出字段结构如下；示例中的占位说明不是答案：\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
    return system, user
