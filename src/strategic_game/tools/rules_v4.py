"""v4.1 numeric rules, with the sealed_v1 protocol overlay.

C/E/M changes are paired transfers; net scores exclude reputation/credibility.
Rules and scene parameters remain the author's v4.1 implementation. FLOW is
explicitly the integrated sealed protocol rather than the old two-stage flow.
The rule-card generator consumes this module as its single constant source.
"""

from __future__ import annotations

from typing import Final

# ── 版本 ────────────────────────────────────────────────────────────────────
RULES_VERSION: Final = "v4.1"
CARD_FORMAT: Final = "v4.1"

# ── 行动类型（小写英文是唯一对外枚举值）─────────────────────────────────────
ACTION_TYPES: Final = ("war", "nuclear", "intervene", "peace")
ACTION_LABELS_ZH: Final = {
    "war": "常规战争",
    "nuclear": "核使用",
    "intervene": "干涉",
    "peace": "和平",
}

PHASES: Final = ("commitment", "revealed", "resolved")

# ── 计分与生死（规则 §1.1–1.3 / L7–L9）─────────────────────────────────────
SCORING: Final = {
    "score_formula": "净 C/E/M 转移（相对开局；声誉/可信度不计分）",
    "score_conservation": "全局净积分之和恒为 0",
    "non_scoring_signals": ("rep", "cred"),
    "stat_ceiling": 5.0,          # C/E/M 每项上限
    "stat_floor": 0.0,            # 低于 0 即死亡；等于 0 存活
    "half_point_step": 0.5,       # 半点精度
    "rep_cred_min": -5.0,
    "rep_cred_max": 5.0,
    "rep_cred_initial": 0.0,
    "victory_margin_strictly_greater_than": 5.0,
}

DEATH_RULE: Final = "批次结算后任一 C/E/M < 0 死亡退出；= 0 存活；死亡后不再参与后续阶段"

WIN_RULE: Final = (
    "存活国家中净积分严格高于第二名 5 点者获胜；仅剩一国则该国胜；"
    "最高分并列无胜者；到回合上限无人达标为未决。全局净积分之和恒为 0"
)

# ── 回合流程（规则 §2 / L14–L16）───────────────────────────────────────────
FLOW: Final = {
    "phases": PHASES,
    "first_round_nuclear_ban": True,   # 第 1 轮禁止 nuclear 的宣称与执行；其他类型正常
    "declaration_then_action": False,   # 宣称收齐统一公布后才进入战斗阶段
    "target_fixed_across_phases": True,  # 目标国不可变；行动类型可变
    "decision_protocol": "sealed_v1",
    "failure_policy": "peace_fallback_v1",
    "negotiation_precommitted": True,
    "new_war_per_choice": 1,           # 一次选择最多新开一场战争
}

# ── 声誉（规则 §3.1 / L24–L26）─────────────────────────────────────────────
REPUTATION: Final = {
    "war": -2.0,
    "nuclear": -3.0,
    "peace": +1.0,                     # 仅限战争中的和平（求和/谈判）或达成危机安排
    "no_war_peace": 0.0,               # v3.0：无战争且未达成安排的和平是常规表态
    "crisis_agreement": +1.0,          # v3.0：双方共同达成危机安排
    "intervene_support_weak": +2.0,
    "intervene_support_strong": -1.0,
    "intervene_equal_strength": 0.0,
}
REPUTATION_STRENGTH_BASIS: Final = "战斗阶段开始快照按双方 C+E+M 比较；相等为 0"
REPUTATION_NUCLEAR_EXCEPTION: Final = (
    "防守方在双方既有战争中使用 nuclear，且非双方同时使用：该次不扣 nuclear 声誉"
)

# ── 可信度（规则 §3.2 / L30–L39）───────────────────────────────────────────
# 行 = 主要阶段宣称的类型，列 = 战斗阶段实际执行的类型。
CREDIBILITY_MATRIX: Final = {
    "war":       {"war": +1, "nuclear": -2, "intervene": -1, "peace": -1},
    "nuclear":   {"war": -1, "nuclear": +1, "intervene": -1, "peace": -1},
    "intervene": {"war": -1, "nuclear": -2, "intervene": +1, "peace": -1},
    "peace":     {"war": -2, "nuclear": -3, "intervene": -2, "peace": +1},
}
CREDIBILITY_RULES: Final = {
    "exact_match": +1,  # 类型 + 目标国（干涉/和平还须 war_id、支持方/目标战争）完全一致
    "same_type_different_binding": -1,  # 类型相同但 war_id 或支持方/目标战争不同
    "different_target": "非法，不能提交",
    "not_executed": "无实际执行时不计分；行动被取消按实际执行结果计分",
}

# ── 常规战争（规则 §4.1 / L45–L49）─────────────────────────────────────────
WAR: Final = {
    "duration_rounds": 2,
    "new_war_counts_as_round": 1,
    "settle_at_end_of_round": 2,
    "draw": "战力相同则战争结束，双方无常规战果",
    "target_peace_attacker_s_bonus": 1.0,  # 目标本轮有效和平未达成时，进攻方 S+1
    "winner_transfer": +2.0,       # v4.0：胜者从败者处取得 2 点（同一随机项）
    "loser_transfer": -2.0,        # v4.0：败者失去 2 点（同一随机项）
    "defender_power_multiplier": 1.0,  # v3.0：防守方不再自动获得战力倍率
    "power_random_band": 0.1,      # v3.0：结算时双方战力各乘 [0.9, 1.1]
    "draw_effect": 0.0,
}

# ── 干涉（规则 §4.2 / L53–L55）─────────────────────────────────────────────
INTERVENE: Final = {
    "duration_rounds": 1,
    "applies_to": "战斗阶段开始时已存在、且经 nuclear/peace 结算后仍存在的战争；本轮新开战不可被干涉",
    "success_condition": "支持方在该战本轮常规结算中获胜；平局或未获胜算失败",
    "success_transfer": 0.5,     # v4.0：成功时从被干涉方转给干涉国
    "failure_transfer": 1.0,     # v4.0：失败时从干涉国转给被干涉方
    "target_penalty_each": -0.5,  # 兼容 v2/v3 的说明字段；v4 用 success_transfer
    "no_settlement": "战争本轮不结算时，干涉不计成功或失败",
    "voided_if_war_ends": "战争被提前结束则干涉取消，不产生声誉/伤害/成败变化",
}

# ── 和平（规则 §4.3 / L59–L61）─────────────────────────────────────────────
PEACE: Final = {
    "reputation": +1.0,
    "no_war_reputation": 0.0,          # v3.0：常规表态不计声誉
    "no_war_credibility": 0,           # v3.0：常规表态不计可信度
    "crisis_agreement_reputation": +1.0,
    "crisis_agreement_credibility": +1,
    "no_war_effect": "v3.0：常规表态，声誉/可信度 0；若双方在同一轮相互和平并达成危机安排，各 +1/+1",
    "attacker_peace": "战争立即结束、进攻方随机一项 -1、视为防守方获胜",
    "defender_peace": "进入单独谈判：防守方报价，进攻方接受或拒绝",
    "both_peace": "双方同时提出和平时，按进攻方和平处理一次",
    "accept_effect": "执行方案并结束该战争，不再另算常规战果",
    "negotiation_zero_sum": "攻方所得与守方所失逐项相等；谈判是纯转移",
}

# ── 核使用（规则 §4.4 / L65–L67）───────────────────────────────────────────
NUCLEAR: Final = {
    "target_random_one": -2.0,
    "no_war_effect": "仅造成目标 -2 与声誉变化",
    "target_transfer": 2.0,        # v4.0：使用者从目标处取得 2 点（同一随机项）
    "user_fallout_transfer": 1.0,  # v4.0：使用者向目标转移 1 点作为代价（净 +1）
    "attacker_war_note": "战争立即判定使用者胜；目标净损失、使用者净收益，总和为 0",
    "defender_war_reputation_exempt": True,
    "simultaneous": "两次核伤害都生效；无单方胜利奖励；双方均按普通 nuclear 扣声誉；战争结束",
    "requires_nuclear_status": True,   # 仅拥核国家可选；具体资格由场景/引擎过滤
}

# ── 危机时钟（v3.0 新增，场景可覆盖具体数值）──────────────────────────────
CRISIS: Final = {
    "stages": ("crisis", "war", "stalemate", "resolved"),
    "grace_rounds": 2,                 # v4.1：前 2 轮只记录、不扣分
    "war_stage_cost": "固定基础成本/轮，不随等级升级",  # v4.1
    "stalemate_level_penalty": 1,      # v4.1：战争打平/谈判失败 → 停滞并升一级
    "stage_schedule": "危机阶段第 3/5/7/9 轮各升一级；战争阶段只按 0.5/轮固定收取、不升级、最长 2 轮；"
                      "打平或谈判失败进入停滞阶段、成本恢复并升一级",
    "default_unresolved_cost": {
        "parties": {"c": -0.5},        # 直接当事国：安全/伤亡
        "dependents": {"e": -0.5},     # 依赖通道国：贸易/供应
        "others": {"rep": -0.2},       # 其他相关方：外交信誉
    },
    "default_control_bonus": {"e": +0.5},   # 危机未解决时，控制方每轮获得
    "costs_are_transfers": "未解决成本从承担方转移给控制方（同一 C/E/M 项）；控制方获得标的收益",
    "resolve_rule": (
        "满足任一条件则危机解决：双方当事国本轮均对彼此执行有效和平（共同安排，控制权不变）；"
        "或当事国之间战争结束并出现胜者（胜者取得控制权）；控制方死亡时控制权转移"
    ),
    "escalation": "未解决时在场景指定轮次提升等级；成本乘以场景给的倍率",
    "events": "每次成本与升级都生成公开事件，写明原因",
}

# ── 目标卡（v3.0 新增，具体条件与数值由场景定义）────────────────────────────
OBJECTIVES: Final = {
    "types": ("goal", "red_line"),
    "goal": "条件首次成立时立即获得奖励，只计一次",
    "red_line": "条件首次成立时检查本国本轮实际行动：若未采取 response_actions 中的类型，扣分；只计一次",
    "condition_keys": (
        "control_is", "crisis_resolved", "crisis_level_at_least",
        "attacked_by_any", "war_any", "any_of", "all_of",
        "stage_is", "resolution_is", "stalled_rounds_at_least",
    ),
    "score_effects": "只改变 C/E/M/声誉/可信度；C/E/M 奖励/惩罚必须声明来源/去向，成对转移",
    "transfer_fields": ("reward_from", "penalty_to"),
}

# ── 谈判（规则 §4.3 试运行补充 / L61）──────────────────────────────────────
NEGOTIATION: Final = {
    "step": 0.5,
    "attacker_gain_min": 0.0,
    "attacker_gain_max_each": 3.0,
    "attacker_gain_max_sum": 3.0,
    "defender_loss_min_each": -3.0,
    "defender_loss_max_each": 0.0,
    "defender_loss_min_sum": -3.0,
    "attacker_decision": ("accept", "reject"),
    "timeout": "无有效方案或超时则无协议、战争继续",
}

# ── 并发稳态后果（规则 §5 试运行补充 / L71–L77，只保留模型需要知道的）──────
SETTLEMENT_STEADY_STATE: Final = (
    "nuclear 先结算",
    "被 nuclear 结束的战争上的 peace/intervene 作废",
    "本轮新开的战争不可被干涉",
    "干涉的成败只在该战本轮结算时计算",
)

# ── 合法性要点（规则 §2.3–2.4 / L16–L17）───────────────────────────────────
LEGALITY_NOTES: Final = (
    "主要阶段必须声明一个目标国家；和平也要给目标",
    "干涉在宣称阶段就要指明战争与支持的一方",
    "目标国在两个阶段必须一致；行动类型可以改变",
    "交战方在既有战争结束前不能新开常规战争；一次只新开一场",
    "干涉不能支持自己，干涉国本轮不得是该场战争的交战方",
    "目标国不能是自己，也不能是已退出国家",
)


# ── 校验 ────────────────────────────────────────────────────────────────────
def validate() -> list[str]:
    """返回错误列表；空列表表示通过。供生成脚本与测试调用。"""
    errors: list[str] = []

    if set(CREDIBILITY_MATRIX) != set(ACTION_TYPES):
        errors.append("可信度矩阵行必须恰好覆盖四种行动类型")
    for row, cells in CREDIBILITY_MATRIX.items():
        if set(cells) != set(ACTION_TYPES):
            errors.append(f"可信度矩阵列不完整：{row}")
        for col, value in cells.items():
            if not isinstance(value, int) or not -3 <= value <= 1:
                errors.append(f"可信度取值异常：{row}->{col}={value!r}")
    for action in ACTION_TYPES:
        if CREDIBILITY_MATRIX[action][action] != CREDIBILITY_RULES["exact_match"]:
            errors.append(f"可信度对角线必须为 +1：{action}")

    if SCORING["victory_margin_strictly_greater_than"] <= 0:
        errors.append("胜利分差必须为正数")
    if SCORING["stat_floor"] >= SCORING["stat_ceiling"]:
        errors.append("C/E/M 上下限关系错误")

    step = NEGOTIATION["step"]
    if step <= 0:
        errors.append("谈判步长必须为正")
    else:
        for key in ("attacker_gain_max_each", "attacker_gain_max_sum",
                    "defender_loss_min_each", "defender_loss_min_sum"):
            value = NEGOTIATION[key]
            if abs(round(value / step) * step - value) > 1e-9:
                errors.append(f"谈判上限不是步长整数倍：{key}={value}")
    if NEGOTIATION["attacker_gain_min"] < 0:
        errors.append("进攻方所得不得为负")
    if NEGOTIATION["defender_loss_max_each"] > 0:
        errors.append("防守方所失不得为正")
    if NEGOTIATION["attacker_gain_max_sum"] < NEGOTIATION["attacker_gain_max_each"]:
        errors.append("进攻方合计上限小于单项上限")

    if not FLOW["first_round_nuclear_ban"]:
        errors.append("v4.0 仍规定第一轮禁止 nuclear")
    if WAR["duration_rounds"] != 2 or WAR["settle_at_end_of_round"] != 2:
        errors.append("常规战争按两轮、第二轮末结算（v4.0 沿用）")
    if WAR["winner_transfer"] <= 0 or WAR["loser_transfer"] >= 0:
        errors.append("v4.0 战争胜者转移必须为正、败者必须为负")
    if WAR["winner_transfer"] + WAR["loser_transfer"] != 0:
        errors.append("v4.0 战争转移必须守恒（胜者所得 = 败者所失）")
    if not 0 <= WAR["power_random_band"] < 1:
        errors.append("战争意志随机幅度必须在 [0,1)")
    for name, value in CRISIS["default_unresolved_cost"].items():
        if not isinstance(value, dict):
            errors.append(f"危机成本必须是对象：{name}")

    for name, value in REPUTATION.items():
        if not isinstance(value, (int, float)):
            errors.append(f"声誉取值必须是数值：{name}")
    return errors


def assert_valid() -> None:
    errors = validate()
    if errors:
        raise ValueError("规则常量校验失败：\n- " + "\n- ".join(errors))
