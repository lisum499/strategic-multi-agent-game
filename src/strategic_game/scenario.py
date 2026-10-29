"""场景加载与校验。

场景是纯虚构数据：国家、初始数值、核状态、角色卡、回合上限、初始战争。
校验失败直接报错，不做静默修正。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .state import NUCLEAR_LEVELS, STAT_KEYS

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
DEFAULT_SCENARIO = "seven_nations_crisis_v4_1.json"
SCENARIO_SCHEMA_VERSION = "scenario_v1"


class ScenarioError(ValueError):
    pass


def normalize_scenario(data: dict[str, Any]) -> dict[str, Any]:
    """Convert the original name-keyed format without adding gameplay rules.

    Native v4.1 scenarios are copied intact. Legacy nuclear reach remains audit
    metadata: v4.1 does not impose the original project's reach/M thresholds.
    """
    if not isinstance(data, dict):
        raise ScenarioError("场景必须是 JSON 对象")
    result = copy.deepcopy(data)
    countries = result.get("countries")
    if not isinstance(countries, list) or not countries:
        raise ScenarioError("countries 必须是非空数组")
    if not all(isinstance(item, dict) for item in countries):
        raise ScenarioError("countries 的每一项必须是对象")
    has_ids = ["country_id" in item for item in countries]
    if all(has_ids):
        return result
    if any(has_ids):
        raise ScenarioError("不能混用 country_id 场景与旧版 name 主键场景")
    if result.get("rules_version") not in (None, "v4.1"):
        raise ScenarioError("旧版场景声明了不同规则版本；请先明确迁移到 v4.1")
    names = [item.get("name") for item in countries]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ScenarioError("旧版场景国家必须有非空 name")
    if len(set(names)) != len(names):
        raise ScenarioError("旧版场景国家 name 重复，无法建立 ID 映射")
    name_to_id = {name: f"C{index}" for index, name in enumerate(names, 1)}
    statuses = {"none": "无", "limited": "有但打击能力有限",
                "full": "有", "second_strike": "二次核打击"}
    migrated = []
    for original in countries:
        name = original["name"]
        profile = original.get("profile", {})
        if not isinstance(profile, dict):
            raise ScenarioError(f"{name}.profile 必须是对象")
        reach = original.get("nuclear_reach", [])
        stakeholders = profile.get("stakeholders", [])
        for field, values in (("nuclear_reach", reach), ("stakeholders", stakeholders)):
            if not isinstance(values, list) or any(
                    not isinstance(value, str) or value not in name_to_id for value in values):
                raise ScenarioError(f"{name}.{field} 必须引用已定义国家名称")
        nuclear = original.get("nuclear_status", "none")
        if not isinstance(nuclear, str) or nuclear not in statuses:
            raise ScenarioError(f"{name}.nuclear_status 非法：{nuclear!r}")
        role_card = copy.deepcopy(profile)
        role_card.update({
            "archetype": profile.get("strategic_culture"),
            "diplomatic_policy": profile.get("foreign_policy"),
            "ideology": {"orientation": profile.get("ideology"), "salience": None},
            "stakeholders": [name_to_id[value] for value in stakeholders],
            "strategic_objectives": [profile["goal"]] if profile.get("goal") else [],
        })
        item = {
            "country_id": name_to_id[name], "name": name,
            "rep": original.get("reputation", 0),
            "cred": original.get("credibility", 0),
            "nuclear_status": statuses[nuclear], "role_card": role_card,
            "legacy_metadata": {
                "original_profile": copy.deepcopy(profile),
                "original_nuclear_reach": copy.deepcopy(reach),
                "nuclear_reach_country_ids": [name_to_id[value] for value in reach],
                "reach_constraint_applied": False,
            },
        }
        for key in STAT_KEYS:
            if key not in original:
                raise ScenarioError(f"{name} 缺少 {key}")
            item[key] = original[key]
        migrated.append(item)
    result["countries"] = migrated
    result.setdefault("scenario_id", "legacy_three_states_migrated")
    result["schema_version"] = SCENARIO_SCHEMA_VERSION
    result["rules_version"] = "v4.1"
    result["migration"] = {
        "source_format": "strategic_game_0.1_name_keyed",
        "country_name_to_id": name_to_id,
        "rules_adapted_to": "v4.1",
        "note": "仅迁移设定；采用 v4.1 结算，无额外射程/M门槛，不自动生成危机。",
    }
    return result


def public_context_source_ids(world_context: dict[str, Any]) -> list[str]:
    """Return unique, unambiguous IDs for public initial facts and claims."""
    seen: set[str] = set()
    ordered: list[str] = []
    for section in ("public_facts", "claims"):
        entries = world_context.get(section, [])
        if not isinstance(entries, list):
            raise ScenarioError(f"world_context.{section} 必须是数组")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ScenarioError(f"world_context.{section} 的条目必须是对象")
            source_id = entry.get("id")
            if (not isinstance(source_id, str) or not source_id.strip()
                    or source_id != source_id.strip()):
                raise ScenarioError(f"world_context.{section} 的 id 必须是非空字符串")
            if (source_id in {"state", "self", "world_context"}
                    or re.fullmatch(r"e_\d+|[da]\d+_.+", source_id)):
                raise ScenarioError(f"公开背景来源 ID 与系统保留 ID 冲突：{source_id}")
            if source_id in seen:
                raise ScenarioError(f"公开背景来源 ID 重复：{source_id}")
            if not isinstance(entry.get("text"), str) or not entry["text"].strip():
                raise ScenarioError(f"公开背景 {source_id} 缺少非空 text")
            seen.add(source_id)
            ordered.append(source_id)
    return ordered


def _check_half_step(value: float, name: str) -> None:
    if abs(value * 2 - round(value * 2)) > 1e-9:
        raise ScenarioError(f"{name} 必须是 0.5 的整数倍：{value}")


def validate_scenario(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ScenarioError("场景必须是 JSON 对象")
    countries = data.get("countries")
    if not isinstance(countries, list) or len(countries) < 2:
        raise ScenarioError("countries 至少要有两个国家")

    seen: set[str] = set()
    for item in countries:
        if not isinstance(item, dict):
            raise ScenarioError("countries 的每一项必须是对象")
        cid = item.get("country_id")
        if not isinstance(cid, str) or not cid.strip() or ":" in cid:
            raise ScenarioError("每个国家都需要非空 country_id")
        if cid in seen:
            raise ScenarioError(f"country_id 重复：{cid}")
        seen.add(cid)
        for key in STAT_KEYS:
            if key not in item:
                raise ScenarioError(f"{cid} 缺少 {key}")
            if isinstance(item[key], bool) or not isinstance(item[key], (int, float)):
                raise ScenarioError(f"{cid}.{key} 必须是数字")
            value = float(item[key])
            if not math.isfinite(value):
                raise ScenarioError(f"{cid}.{key} 必须是有限数字")
            if not 0.0 <= value <= 5.0:
                raise ScenarioError(f"{cid}.{key} 必须在 0~5：{value}")
            _check_half_step(value, f"{cid}.{key}")
        for key in ("rep", "cred"):
            raw = item.get(key, 0.0)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ScenarioError(f"{cid}.{key} 必须是数字")
            value = float(raw)
            if not math.isfinite(value):
                raise ScenarioError(f"{cid}.{key} 必须是有限数字")
            if not -5.0 <= value <= 5.0:
                raise ScenarioError(f"{cid}.{key} 必须在 -5~5：{value}")
            _check_half_step(value, f"{cid}.{key}")
        status = item.get("nuclear_status", "无")
        if status not in NUCLEAR_LEVELS:
            raise ScenarioError(f"{cid}.nuclear_status 非法：{status!r}")
        if not isinstance(item.get("role_card", {}), dict):
            raise ScenarioError(f"{cid}.role_card 必须是对象")
        private_observation = item.get("private_observation", [])
        if not isinstance(private_observation, list) or not all(
                isinstance(text, str) and text.strip() for text in private_observation):
            raise ScenarioError(f"{cid}.private_observation 必须是非空字符串数组（可为空数组）")

    raw_rounds = data.get("max_rounds", 8)
    if isinstance(raw_rounds, bool) or not isinstance(raw_rounds, int):
        raise ScenarioError("max_rounds 必须是整数")
    max_rounds = raw_rounds
    if max_rounds < 1:
        raise ScenarioError("max_rounds 必须 ≥1")
    if data.get("max_rounds", 8) != max_rounds:
        raise ScenarioError("max_rounds 必须是整数")

    initial_wars = data.get("initial_wars", [])
    if not isinstance(initial_wars, list):
        raise ScenarioError("initial_wars 必须是数组")
    war_ids: set[str] = set()
    directed_wars: set[tuple[str, str]] = set()
    for war in initial_wars:
        if not isinstance(war, dict):
            raise ScenarioError("initial_wars 每一项必须是对象")
        for key in ("war_id", "attacker_id", "defender_id"):
            if key not in war:
                raise ScenarioError(f"initial_wars 缺少 {key}")
        wid = war["war_id"]
        if not isinstance(wid, str) or not wid.strip() or ":" in wid:
            raise ScenarioError("war_id 必须是非空字符串且不能包含冒号")
        if wid.startswith("w_") and not wid[2:].isdigit():
            raise ScenarioError("以 w_ 开头的 war_id 必须以数字结尾")
        if wid in war_ids:
            raise ScenarioError(f"war_id 重复：{wid}")
        war_ids.add(wid)
        if not all(isinstance(war[key], str) for key in ("attacker_id", "defender_id")):
            raise ScenarioError("initial_wars 的交战方必须是国家 ID 字符串")
        if war["attacker_id"] not in seen or war["defender_id"] not in seen:
            raise ScenarioError(f"initial_wars 引用了不存在的国家：{war}")
        if war["attacker_id"] == war["defender_id"]:
            raise ScenarioError("initial_wars 不能自己打自己")
        pair = (war["attacker_id"], war["defender_id"])
        if pair in directed_wars:
            raise ScenarioError(f"initial_wars 同方向战争重复：{pair}")
        directed_wars.add(pair)
        progress = war.get("progress_round", 1)
        if isinstance(progress, bool) or not isinstance(progress, int) or progress not in (1, 2):
            raise ScenarioError("initial_wars.progress_round 必须为 1 或 2")
        started = war.get("started_round", 1)
        if isinstance(started, bool) or not isinstance(started, int) or started > 1:
            raise ScenarioError("initial_wars.started_round 必须是不晚于第 1 轮的整数")

    crisis = data.get("crisis")
    if crisis is not None:
        if not isinstance(crisis, dict):
            raise ScenarioError("crisis 必须是对象")
        for key in ("crisis_id", "title", "parties", "control", "stake"):
            if key not in crisis:
                raise ScenarioError(f"crisis 缺少 {key}")
        if not isinstance(crisis["parties"], list) or len(crisis["parties"]) < 2:
            raise ScenarioError("crisis.parties 至少要有两个国家")
        if any(cid not in seen for cid in crisis["parties"]):
            raise ScenarioError("crisis.parties 引用了不存在的国家")
        if crisis["control"] not in seen:
            raise ScenarioError("crisis.control 必须是存在的国家")
        dependents = crisis.get("dependents", [])
        if not isinstance(dependents, list) or any(cid not in seen for cid in dependents):
            raise ScenarioError("crisis.dependents 必须是存在的国家数组")
        if set(dependents) & set(crisis["parties"]):
            raise ScenarioError("crisis.dependents 不能同时是当事国")
        for group in ("control_bonus", "unresolved_cost"):
            value = crisis.get(group, {})
            if not isinstance(value, dict):
                raise ScenarioError(f"crisis.{group} 必须是对象")
            if group == "control_bonus":
                groups = {"control": value}
            else:
                groups = value
                unknown = set(groups) - {"parties", "dependents", "others"}
                if unknown:
                    raise ScenarioError(f"crisis.unresolved_cost 含未知分组：{unknown}")
            for name, effects in groups.items():
                if not isinstance(effects, dict):
                    raise ScenarioError(f"crisis.{group}.{name} 必须是对象")
                for stat, delta in effects.items():
                    if stat not in ("c", "e", "m", "rep", "cred"):
                        raise ScenarioError(f"crisis 数值项非法：{stat}")
                    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                        raise ScenarioError(f"crisis.{group}.{name}.{stat} 必须是数字")
        rounds = crisis.get("escalation_rounds", [])
        if not isinstance(rounds, list) or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 1
                for item in rounds):
            raise ScenarioError("crisis.escalation_rounds 必须是正整数数组")
        multiplier = crisis.get("escalation_multiplier", 2.0)
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) \
                or multiplier < 1:
            raise ScenarioError("crisis.escalation_multiplier 必须 ≥1")
        grace = crisis.get("grace_rounds", 2)
        if isinstance(grace, bool) or not isinstance(grace, int) or grace < 0:
            raise ScenarioError("crisis.grace_rounds 必须是非负整数")

    objectives = data.get("objectives", [])
    if not isinstance(objectives, list):
        raise ScenarioError("objectives 必须是数组")
    if objectives and crisis is None:
        raise ScenarioError("v3.0 目标卡依赖危机状态；定义 objectives 时必须同时定义 crisis")
    condition_keys = {"control_is", "crisis_resolved", "crisis_level_at_least",
                      "attacked_by_any", "war_any", "any_of", "all_of",
                      "stage_is", "resolution_is", "stalled_rounds_at_least"}

    def _check_condition(condition: dict, where: str) -> None:
        if not isinstance(condition, dict) or not condition:
            raise ScenarioError(f"{where} 的 condition 必须是非空对象")
        for key, value in condition.items():
            if key not in condition_keys:
                raise ScenarioError(f"{where} 含未知条件：{key}")
            if key in ("any_of", "all_of"):
                if not isinstance(value, list) or not value:
                    raise ScenarioError(f"{where}.{key} 必须是非空数组")
                for item in value:
                    _check_condition(item, where)
            elif key == "control_is" and value not in seen:
                raise ScenarioError(f"{where}.control_is 必须是存在的国家")
            elif key == "crisis_level_at_least":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ScenarioError(f"{where}.crisis_level_at_least 必须是正整数")
            elif key in ("crisis_resolved", "attacked_by_any", "war_any"):
                if not isinstance(value, bool):
                    raise ScenarioError(f"{where}.{key} 必须是布尔值")
            elif key == "stage_is":
                if value not in ("crisis", "war", "stalemate", "resolved"):
                    raise ScenarioError(f"{where}.stage_is 非法：{value}")
            elif key == "resolution_is":
                if value not in ("agreement", "war", "negotiation", "party_exit"):
                    raise ScenarioError(f"{where}.resolution_is 非法：{value}")
            elif key == "stalled_rounds_at_least":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ScenarioError(f"{where}.stalled_rounds_at_least 必须是正整数")

    objective_ids: set[tuple[str, str]] = set()
    for item in objectives:
        if not isinstance(item, dict) or item.get("country_id") not in seen:
            raise ScenarioError("objectives 条目必须包含存在的 country_id")
        cid = item["country_id"]
        if not isinstance(item.get("objectives"), list):
            raise ScenarioError(f"{cid}.objectives 必须是数组")
        for objective in item["objectives"]:
            if not isinstance(objective, dict):
                raise ScenarioError("目标卡必须是对象")
            oid = objective.get("id")
            if not isinstance(oid, str) or not oid.strip():
                raise ScenarioError("目标卡缺少 id")
            if (cid, oid) in objective_ids:
                raise ScenarioError(f"目标卡 id 重复：{cid}/{oid}")
            objective_ids.add((cid, oid))
            if objective.get("type") not in ("goal", "red_line"):
                raise ScenarioError(f"{cid}/{oid}.type 必须是 goal 或 red_line")
            if not isinstance(objective.get("description"), str) \
                    or not objective["description"].strip():
                raise ScenarioError(f"{cid}/{oid} 缺少 description")
            _check_condition(objective.get("condition", {}), f"{cid}/{oid}")
            effect_key = "reward" if objective["type"] == "goal" else "penalty"
            effects = objective.get(effect_key, {})
            if not isinstance(effects, dict):
                raise ScenarioError(f"{cid}/{oid}.{effect_key} 必须是对象")
            for stat, delta in effects.items():
                if stat not in ("c", "e", "m", "rep", "cred"):
                    raise ScenarioError(f"{cid}/{oid}.{effect_key} 数值项非法：{stat}")
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    raise ScenarioError(f"{cid}/{oid}.{effect_key} 必须是数字")
            if any(stat in STAT_KEYS for stat in effects):
                transfer_field = "reward_from" if objective["type"] == "goal" else "penalty_to"
                counterparty = objective.get(transfer_field)
                if counterparty not in seen:
                    raise ScenarioError(
                        f"{cid}/{oid} 的 C/E/M 效果必须声明有效的 {transfer_field}")
                if counterparty == cid:
                    raise ScenarioError(f"{cid}/{oid} 的 {transfer_field} 不能是本国")
            if objective["type"] == "red_line":
                responses = objective.get(
                    "response_actions", ["war", "nuclear", "intervene"])
                if not isinstance(responses, list) or any(
                        action not in ("war", "nuclear", "intervene", "peace")
                        for action in responses):
                    raise ScenarioError(f"{cid}/{oid}.response_actions 含非法行动类型")

    visibility = data.get("visibility", {})
    if not isinstance(visibility, dict):
        raise ScenarioError("visibility 必须是对象")
    if "ce_m_public" in visibility and not isinstance(visibility["ce_m_public"], bool):
        raise ScenarioError("visibility.ce_m_public 必须是布尔值")
    if not isinstance(data.get("threat_observation_enabled", False), bool):
        raise ScenarioError("threat_observation_enabled 必须是布尔值")
    if data.get("negotiation_mode", "offers") not in ("offers", "self_offer"):
        raise ScenarioError("negotiation_mode 必须是 offers 或 self_offer")
    world_context = data.get("world_context", {})
    if not isinstance(world_context, dict):
        raise ScenarioError("world_context 必须是公开背景对象")
    public_context_source_ids(world_context)


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def scenario_hash(data: dict[str, Any]) -> str:
    # 源文件位置只用于追踪，不是场景内容；移动文件不应改变内容指纹。
    content = {key: value for key, value in data.items() if key != "_path"}
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def load_scenario(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_absolute():
        path = SCENARIO_DIR / path if (SCENARIO_DIR / path).exists() else path
    if not path.exists():
        raise ScenarioError(f"场景文件不存在：{path}")
    data = normalize_scenario(json.loads(path.read_text(encoding="utf-8")))
    validate_scenario(data)
    data["_path"] = str(path)
    return data
