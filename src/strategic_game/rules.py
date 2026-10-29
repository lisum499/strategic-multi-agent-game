"""Compile v4.1 legal options and consequences from the shared rule constants."""

from __future__ import annotations

from typing import Any

from .state import STAT_KEYS, State, War, round_half
from .tools import rules_v4 as R

MULTIPLIER = {
    # v3.0：防守方不再自动获得 W 倍率；S 修正仍有效（S+1 → 1.1）。
    -3: 0.25, -2: 0.5, -1: 0.75, 0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3,
}


def ce_m(country) -> float:
    return country.c + country.e + country.m


def multiplier_for(s_plus_w: float) -> float:
    key = max(-3, min(3, int(round(s_plus_w))))
    return MULTIPLIER[key]


def multi_front_penalty(state: State, country_id: str) -> int:
    """按交战方战争数量给出 S 的惩罚；数目语义见规则 §4.1 试运行补充。"""
    count = len(state.party_wars(country_id))
    if count <= 1:
        return 0
    if count == 2:
        return -1
    if count <= 6:
        return -3
    return -4


def war_power(state: State, war: War, side_id: str,
              *, s_bonus: float = 0.0) -> float:
    country = state.countries[side_id]
    s = multi_front_penalty(state, side_id) + s_bonus
    w = 0  # v3.0：防守方不再自动获得 W 倍率；仅在场景/规则显式给出时才有修正
    power = ce_m(country) * multiplier_for(s + w)
    for item in war.intervened_by:
        if item.get("round") not in (None, state.round):
            continue  # 干涉只在本轮有效，历史记录不参与后续战力
        if item["support_id"] == side_id:
            intervener = state.countries.get(item["intervener_id"])
            if intervener is not None and intervener.alive:
                power += ce_m(intervener) * 0.25
    return round(power, 4)


def resolve_war(state: State, war: War,
                *, attacker_s_bonus: float = 0.0,
                attacker_factor: float = 1.0,
                defender_factor: float = 1.0) -> dict[str, Any]:
    """返回 {'winner': id|None, 'attacker_power':x, 'defender_power':y}。

    v3.0：双方战力各乘一个 ±band 的战争意志随机修正（由引擎抽取并记录）。
    """
    attacker_power = war_power(state, war, war.attacker_id,
                               s_bonus=attacker_s_bonus) * attacker_factor
    defender_power = war_power(state, war, war.defender_id) * defender_factor
    if attacker_power > defender_power:
        winner = war.attacker_id
    elif defender_power > attacker_power:
        winner = war.defender_id
    else:
        winner = None
    return {
        "winner": winner,
        "attacker_power": attacker_power,
        "defender_power": defender_power,
    }


# ── 声誉/可信度 ─────────────────────────────────────────────────────────
def intervene_reputation(state: State, war: War, support_id: str) -> float:
    attacker_strength = ce_m(state.countries[war.attacker_id])
    defender_strength = ce_m(state.countries[war.defender_id])
    if attacker_strength == defender_strength:
        return R.REPUTATION["intervene_equal_strength"]
    weaker = war.defender_id if defender_strength < attacker_strength else war.attacker_id
    if support_id == weaker:
        return R.REPUTATION["intervene_support_weak"]
    return R.REPUTATION["intervene_support_strong"]


def reputation_delta(state: State, option: dict[str, Any]) -> float:
    action_type = option["action_type"]
    if action_type == "intervene":
        war = state.wars.get(option.get("war_id") or "")
        if war is None:
            return 0.0
        return intervene_reputation(state, war, option["support_country_id"])
    return float(R.REPUTATION[action_type])


def credibility_delta(declaration: dict[str, Any], option: dict[str, Any]) -> int:
    declared = declaration["action_type"]
    executed = option["action_type"]
    if declared != executed:
        return int(R.CREDIBILITY_MATRIX[declared][executed])
    if declaration["target_country_id"] != option["target_country_id"]:
        raise ValueError("目标国不同应为非法，不应进入合法性计算")
    if (declaration.get("war_id") == option.get("war_id")
            and declaration.get("support_country_id") == option.get("support_country_id")):
        return int(R.CREDIBILITY_RULES["exact_match"])
    return int(R.CREDIBILITY_RULES["same_type_different_binding"])


# ── 合法选项 ───────────────────────────────────────────────────────────
def _targets(state: State, country_id: str) -> list[str]:
    return [cid for cid in state.alive_ids() if cid != country_id]


def select_war(state: State, a: str, b: str) -> War | None:
    """同一对国家可能有多场反向战争；统一取最早开始的一场（再按 war_id）。

    提示词里的后果与实际结算必须用同一个选择，否则模型看到的角色会与实际相反。
    """
    wars = state.wars_between(a, b)
    if not wars:
        return None
    return min(wars, key=lambda war: (war.started_round, war.war_id))


def can_start_war(state: State, country_id: str) -> bool:
    country = state.countries[country_id]
    if not country.alive:
        return False
    if state.round in country.war_bans:
        return False
    return not state.is_party_in_any_war(country_id)


def can_use_nuclear(state: State, country_id: str) -> bool:
    country = state.countries[country_id]
    return country.alive and country.nuclear_status != "无" and state.round > 1


def _option(option_id: str, action_type: str, target: str | None, *,
            war_id: str | None = None, support: str | None = None,
            consequence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "option_id": option_id,
        "action_type": action_type,
        "target_country_id": target,
        "war_id": war_id,
        "support_country_id": support,
        "consequences": consequence or {},
    }


def _declaration_consequences(state: State, action_type: str,
                               target: str, war: War | None = None,
                               support: str | None = None,
                               country_id: str | None = None) -> dict[str, Any]:
    role = None
    if war is not None and country_id is not None:
        if war.attacker_id == country_id:
            role = "attacker"
        elif war.defender_id == country_id:
            role = "defender"

    if action_type == "intervene":
        rep = (intervene_reputation(state, war, support)
               if war is not None and support is not None else 0.0)
        if rep > 0:
            note = "支持弱方；声誉 +2"
        elif rep < 0:
            note = "支持强方；声誉 −1"
        else:
            note = "支持势均方；声誉 0"
        note += "；资源：先从被干涉方转移 0.5 点给干涉国"
        if war is not None:
            note += "；若支持方失败再转移 1 点回被干涉方；战争提前结束则行动取消"
    elif action_type == "peace":
        if war is None:
            rep = float(R.REPUTATION["no_war_peace"])
            note = ("无战争和平：常规表态，声誉 0、可信度 0；"
                    "若双方当事国同轮相互和平并达成危机安排，各 +1 声誉 +1 可信度")
        else:
            rep = float(R.REPUTATION["peace"])
        if war is None:
            pass
        elif role == "attacker":
            note = "进攻方求和：立即停战、向防守方转移 1 点（随机 C/E/M）、视为对方胜；声誉 +1"
        else:
            note = "防守方求和：进入谈判；声誉 +1"
    elif action_type == "nuclear":
        if war is None:
            rep = float(R.REPUTATION["nuclear"])
            note = ("无战争核使用：从目标取得 2 点、向目标转移 1 点（同一随机 C/E/M，净 +1）；"
                    "声誉 −3（不计分）")
        elif role == "attacker":
            rep = float(R.REPUTATION["nuclear"])
            note = ("进攻方核使用：该战立即判你胜；从目标取得 2 点、向目标转移 1 点"
                    "（同一随机 C/E/M，净 +1）；声誉 −3（不计分）；若无战争则同样只做转移")
        elif role == "defender":
            rep = 0.0  # 防守方核使用例外；若对方同时用核则仍按普通核使用扣声誉
            note = ("防守方核使用：若对方未同时用核，该战判你胜；从目标取得 2 点、"
                    "向目标转移 1 点（同一随机 C/E/M，净 +1）；本次不扣声誉、下一轮禁 war")
        else:
            rep = float(R.REPUTATION["nuclear"])
            note = "核使用：从目标取得 2 点、向目标转移 1 点（同一随机 C/E/M，净 +1）；声誉 −3（不计分）"
    else:
        rep = float(R.REPUTATION["war"])
        note = ("常规战争；声誉 −2；胜者随机一项 C/E/M +2、败者 −2；"
                "结算时双方战力各乘 ±10% 战争意志随机")
    cred_if_kept = int(R.CREDIBILITY_RULES["exact_match"])
    if action_type == "peace" and war is None:
        cred_if_kept = int(R.PEACE["no_war_credibility"])
    return {
        "reputation_delta": rep,
        "credibility_delta_if_kept": cred_if_kept,
        "note": note,
    }


def declaration_options(state: State, country_id: str) -> list[dict[str, Any]]:
    """主要阶段：宣称类型 + 目标国（干涉另带 war_id 与支持方）。"""
    country = state.countries[country_id]
    targets = _targets(state, country_id)
    options: list[dict[str, Any]] = []
    counter = 0

    def add(action_type: str, target: str, *, war_id=None, support=None,
            war: War | None = None) -> None:
        nonlocal counter
        counter += 1
        options.append(_option(
            f"D{counter}", action_type, target, war_id=war_id, support=support,
            consequence=_declaration_consequences(state, action_type, target,
                                                  war=war, support=support,
                                                  country_id=country_id),
        ))

    if can_start_war(state, country_id):
        for target in targets:
            add("war", target)

    if can_use_nuclear(state, country_id):
        for target in targets:
            wars = state.wars_between(country_id, target)
            add("nuclear", target, war=(select_war(state, country_id, target)
                                        if wars else None))
            if len(wars) > 1:
                options[-1]["consequences"]["note"] += "；多场战争按最早开始的一场结算"

    for war in state.wars_existing_at_round_start():
        if country_id in war.parties():
            continue
        # 支持防守方：目标是进攻方；支持进攻方：目标是防守方
        add("intervene", war.attacker_id, war_id=war.war_id,
            support=war.defender_id, war=war)
        add("intervene", war.defender_id, war_id=war.war_id,
            support=war.attacker_id, war=war)

    for target in targets:
        wars = state.wars_between(country_id, target)
        if wars:
            for war in wars:
                add("peace", target, war_id=war.war_id, war=war)
        else:
            add("peace", target)

    return options


def action_options(state: State, country_id: str,
                   declaration: dict[str, Any]) -> list[dict[str, Any]]:
    """战斗阶段：目标国固定，类型可变；后果里带可信度增量。"""
    country = state.countries[country_id]
    target = declaration["target_country_id"]
    options: list[dict[str, Any]] = []
    counter = 0

    def add(action_type: str, *, war_id=None, support=None,
            war: War | None = None) -> None:
        nonlocal counter
        counter += 1
        option = _option(f"A{counter}", action_type, target,
                         war_id=war_id, support=support)
        consequence = _declaration_consequences(
            state, action_type, target, war=war, support=support,
            country_id=country_id)
        consequence["credibility_delta"] = credibility_delta(declaration, option)
        consequence.pop("credibility_delta_if_kept", None)
        # v3.0：无战争且未达成危机安排的和平是常规表态，不因"说到做到"加分。
        if (action_type == "peace" and war is None
                and declaration.get("action_type") == "peace"
                and declaration.get("war_id") is None):
            consequence["credibility_delta"] = int(R.PEACE["no_war_credibility"])
            consequence["note"] = (consequence.get("note", "")
                                   + "；v3.0 常规表态：可信度 +0，需双方共同安排才计分")
        option["consequences"] = consequence
        options.append(option)

    if target in state.countries and state.countries[target].alive:
        if can_start_war(state, country_id) and not state.wars_between(country_id, target):
            add("war")
        if can_use_nuclear(state, country_id):
            wars = state.wars_between(country_id, target)
            add("nuclear", war=(select_war(state, country_id, target)
                                if wars else None))
            if len(wars) > 1:
                options[-1]["consequences"]["note"] += "；多场战争按最早开始的一场结算"
        for war in state.wars_existing_at_round_start():
            if country_id in war.parties():
                continue
            if target not in war.parties():
                continue
            support = war.other_side(target)
            if support is not None:
                add("intervene", war_id=war.war_id, support=support, war=war)
        wars = state.wars_between(country_id, target)
        if wars:
            for war in wars:
                add("peace", war_id=war.war_id, war=war)
        else:
            add("peace")

    # 防御：理论上至少应有 peace（目标存活时）；没有则返回空，由引擎记系统无可用行动
    return options


def crisis_round_cost(crisis: Any, round_no: int) -> dict[str, dict[str, float]]:
    """v4.1：按阶段、等级与宽限期计算每轮成本（0.5 步长）。"""
    if crisis.resolved or round_no <= crisis.grace_rounds:
        return {}
    # v4.1：战争阶段只按基础成本固定收取、不随等级升级；
    # 战争打平/谈判失败进入停滞阶段后，恢复按等级放大。
    if crisis.stage == "war":
        return {
            group: {key: round_half(value) for key, value in costs.items()}
            for group, costs in crisis.unresolved_cost.items()
        }
    multiplier = crisis.escalation_multiplier ** max(0, crisis.level - 1)
    return {
        group: {key: round_half(value * multiplier)
                for key, value in costs.items()}
        for group, costs in crisis.unresolved_cost.items()
    }


def crisis_group(crisis: Any, country_id: str) -> str | None:
    if country_id in crisis.parties:
        return "parties"
    if country_id in crisis.dependents:
        return "dependents"
    return "others"


def objective_condition_met(condition: dict[str, Any], state: State,
                            country_id: str,
                            attacked_this_round: set[str]) -> bool:
    """目标卡条件求值；只支持规则卡列出的键（v4.1 增加阶段/解决方式/停滞轮数）。"""
    for key, value in condition.items():
        if key == "any_of":
            if not any(objective_condition_met(item, state, country_id,
                                               attacked_this_round)
                       for item in value):
                return False
        elif key == "all_of":
            if not all(objective_condition_met(item, state, country_id,
                                               attacked_this_round)
                       for item in value):
                return False
        elif key == "control_is":
            crisis = state.crisis
            if crisis is None or crisis.control != value:
                return False
        elif key == "crisis_resolved":
            if state.crisis is None or state.crisis.resolved != bool(value):
                return False
        elif key == "crisis_level_at_least":
            if state.crisis is None or state.crisis.level < int(value):
                return False
        elif key == "attacked_by_any":
            ongoing = any(war.defender_id == country_id
                          for war in state.ongoing_wars())
            if (ongoing or country_id in attacked_this_round) != bool(value):
                return False
        elif key == "war_any":
            if bool(state.party_wars(country_id)) != bool(value):
                return False
        elif key == "stage_is":
            if state.crisis is None or state.crisis.stage != value:
                return False
        elif key == "resolution_is":
            if state.crisis is None or state.crisis.resolution != value:
                return False
        elif key == "stalled_rounds_at_least":
            if (state.crisis is None
                    or state.crisis.stalled_rounds < int(value)):
                return False
        else:
            raise ValueError(f"未知目标卡条件：{key}")
    return True


def constraints_for(state: State, country_id: str, phase: str,
                    declaration: dict[str, Any] | None = None) -> list[str]:
    country = state.countries[country_id]
    lines: list[str] = []
    if state.round == 1:
        lines.append("第 1 轮：禁止 nuclear 的宣称与执行；其他类型正常")
    if state.round in country.war_bans:
        lines.append(f"第 {state.round} 轮：你被禁止发动 war")
    if country.nuclear_status == "无":
        lines.append("你没有核能力：nuclear 不可选")
    if state.is_party_in_any_war(country_id):
        lines.append("你有未结束的战争：本轮不能新开常规战争")
    lines.append("目标国不能是本国或已退出国家")
    if phase == "declaration":
        lines.append("宣称的目标国在 action 阶段不能改变；行动类型可以改变")
    else:
        lines.append(f"你本轮已宣称的目标国是 {declaration['target_country_id']}，行动目标必须一致")
        lines.append("类型一致且 war_id/支持方一致：可信度 +1；否则按情况扣分")
    lines.append("干涉时 target_country_id 是被干涉方，support_country_id 是被支持方；不能支持自己")
    return lines


def negotiation_bounds() -> dict[str, Any]:
    """自提方案模式下给模型的约束（数值仍由引擎再校验一次）。"""
    return {
        "step": R.NEGOTIATION["step"],
        "attacker_gain": {
            "min_each": R.NEGOTIATION["attacker_gain_min"],
            "max_each": R.NEGOTIATION["attacker_gain_max_each"],
            "max_sum": R.NEGOTIATION["attacker_gain_max_sum"],
        },
        "defender_loss": {
            "min_each": R.NEGOTIATION["defender_loss_min_each"],
            "max_each": R.NEGOTIATION["defender_loss_max_each"],
            "min_sum": R.NEGOTIATION["defender_loss_min_sum"],
        },
        "note": "数值以 0.5 为单位；防守方用负数表示损失",
    }


def negotiation_offers(state: State, war: War) -> list[dict[str, Any]]:
    """给防守方的合法方案；包含双方无数值变化的停战选项。"""
    return [
        {
            "offer_id": "P1",
            "label": "进攻方 +1.5，防守方 −1.5",
            "changes": {
                "attacker": {"c": 0.0, "e": 1.5, "m": 0.0},
                "defender": {"c": 0.0, "e": -1.5, "m": 0.0},
            },
        },
        {
            "offer_id": "P2",
            "label": "进攻方 +1.0，防守方 −1.0（分两项）",
            "changes": {
                "attacker": {"c": 0.5, "e": 0.5, "m": 0.0},
                "defender": {"c": -0.5, "e": -0.5, "m": 0.0},
            },
        },
        {
            "offer_id": "P3",
            "label": "进攻方 +3.0，防守方 −3.0",
            "changes": {
                "attacker": {"c": 1.0, "e": 1.0, "m": 1.0},
                "defender": {"c": -1.0, "e": -1.0, "m": -1.0},
            },
        },
        {
            "offer_id": "P4",
            "label": "双方数值不变停战",
            "changes": {
                "attacker": {"c": 0.0, "e": 0.0, "m": 0.0},
                "defender": {"c": 0.0, "e": 0.0, "m": 0.0},
            },
        },
    ]


def validate_negotiation_offer(offer: dict[str, Any]) -> None:
    """校验方案是否满足 0.5 步长、上限、符号与零和对称；不合法直接报错。"""
    step = R.NEGOTIATION["step"]
    for side, sign in (("attacker", 1), ("defender", -1)):
        changes = offer["changes"][side]
        for key in STAT_KEYS:
            value = float(changes[key])
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"谈判数值必须是有限数字：{side}.{key}={value}")
            if abs(value / step - round(value / step)) > 1e-9:
                raise ValueError(f"谈判数值不是 0.5 的整数倍：{side}.{key}={value}")
            if sign > 0 and not (0.0 <= value <= R.NEGOTIATION["attacker_gain_max_each"]):
                raise ValueError(f"进攻方所得越界：{side}.{key}={value}")
            if sign < 0 and not (R.NEGOTIATION["defender_loss_min_each"] <= value <= 0.0):
                raise ValueError(f"防守方所失越界：{side}.{key}={value}")
        total = sum(float(changes[key]) for key in STAT_KEYS)
        if sign > 0 and total > R.NEGOTIATION["attacker_gain_max_sum"]:
            raise ValueError(f"进攻方合计越界：{total}")
        if sign < 0 and total < R.NEGOTIATION["defender_loss_min_sum"]:
            raise ValueError(f"防守方合计越界：{total}")
    for key in STAT_KEYS:
        if abs(float(offer["changes"]["attacker"][key])
               + float(offer["changes"]["defender"][key])) > 1e-9:
            raise ValueError(f"谈判方案必须逐项守恒：{key}")
