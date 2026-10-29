#!/usr/bin/env python3
"""从 `rules_v4.py` 生成模型侧「规则卡」与引擎用 JSON。

为什么不直接注入规则全文：全文含研究者元话语、引擎实现细节与未定项，会稀释模型对
决策条款的注意力（见同目录上一级 `prompts/flash约束方案-第二轮.md`）。本脚本让
「结构化常量 → 模型卡 / 引擎 JSON」成为单向生成，避免手抄出第二份真相。

用法
    python3 strategic_game.tools.gen_rule_card            # 生成两个默认产物
    python3 strategic_game.tools.gen_rule_card --check     # 校验已提交产物是否最新（CI 用）
    python3 strategic_game.tools.gen_rule_card --stdout    # 只打印，不写文件

退出码：0 成功/一致；1 校验失败或产物过期；2 参数错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import rules_v4 as R

TOOLS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = TOOLS_DIR.parent / "prompts" / "v2.1"
DEFAULT_MD = PROMPTS_DIR / "20-规则卡-v2.0.md"
DEFAULT_JSON = PROMPTS_DIR / "21-规则卡.json"


# ── 规则卡正文（模型可读；数值全部来自 rules_v3，不手写）───────────────────
def _plain(value: float) -> str:
    """用于范围/上限：-3.0 -> −3；0.5 -> 0.5；3.0 -> 3。"""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _signed(value: float) -> str:
    """用于增量：+1 -> +1；-2.0 -> −2；0 -> 0。"""
    if float(value) == 0:
        return "0"
    text = _plain(value)
    return text if text.startswith("−") else f"+{text}"


def _matrix_rows() -> list[str]:
    rows: list[str] = []
    for declared in R.ACTION_TYPES:
        cells = R.CREDIBILITY_MATRIX[declared]
        parts = "｜".join(f"{executed} {_signed(cells[executed])}"
                          for executed in R.ACTION_TYPES)
        rows.append(f"    {declared}：{parts}")
    return rows


def build_sections() -> list[str]:
    score = R.SCORING
    rep = R.REPUTATION
    war = R.WAR
    nuc = R.NUCLEAR
    itv = R.INTERVENE
    neg = R.NEGOTIATION
    type_line = "、".join(f"{action} {R.ACTION_LABELS_ZH[action]}" for action in R.ACTION_TYPES)

    return [
        f"# 规则卡 {R.RULES_VERSION} · 模型侧唯一口径",
        "",
        "本卡是本版规则的唯一模型侧口径；本卡没有写的，不要自行推断。",
        "卡中的数值、倍率、随机、胜负、生死均由引擎执行或在输入中给出；不要自行计算。",
        "",
        "## 1 计分与胜负（零和）",
        f"1.1 净积分 = 当前 C+E+M − 开局 C+E+M；全员开局 0。"
        f"声誉、可信度只是公开信号，不计入积分。",
        "1.2 所有 C/E/M 变化都是转移：一方所得等于另一方所失；全局净积分之和恒为 0。"
        "和平行动本身不改变积分。",
        f"1.3 C/E/M 每项 ≤{_plain(score['stat_ceiling'])}，以 {_plain(score['half_point_step'])} 为单位；"
        f"批次结算后任一项 <0 死亡退出，=0 存活。",
        f"1.4 存活国家中净积分严格高于第二名 "
        f"{_plain(score['victory_margin_strictly_greater_than'])} 点者获胜；"
        f"仅剩一国则该国胜；最高分并列无胜者；回合上限未决。",
        "",
        "## 2 回合与合法性",
        "2.1 每轮开始固定快照；一次密封提交预测、宣称、行动与谈判政策；全部锁定后统一揭示再结算。宣称与行动目标国一致。",
        f"2.2 类型：{type_line}；nuclear 仅当 legal_options 提供时可选（拥核国家）。",
        "2.3 第 1 轮禁止 nuclear 的宣称与执行；其他类型正常。",
        "2.4 每个宣称选项已包含目标国（和平也有）；干涉选项包含 war_id 与 support_country_id。模型只输出所选 option_id，不额外输出这些字段。",
        "2.5 交战方在既有战争结束前不能新开常规战争；一次只新开一场；"
        "干涉不支自己、不当该战交战方；目标不能是自己或已退出国家。",
        "",
        "## 3 声誉（有效执行后）",
        "3.1 声誉/可信度是公开信号，不进入积分；其变化只影响他国判断。"
        f"war {_signed(rep['war'])}；nuclear {_signed(rep['nuclear'])}；"
        f"和平 {_signed(rep['no_war_peace'])}（无战争常规表态）；"
        f"达成危机安排 {_signed(rep['crisis_agreement'])}。",
        f"3.2 intervene：支持弱方 {_signed(rep['intervene_support_weak'])}、"
        f"支持强方 {_signed(rep['intervene_support_strong'])}、双方相等 0"
        f"（强弱按战斗阶段开始快照的 C+E+M 比较）。",
        "3.3 例外：防守方在既有战争中使用 nuclear 且非双方同时使用，该次不扣声誉。",
        "",
        "## 4 可信度（宣称类型 → 执行类型；类型与目标都相同才 +1）",
        "4.1 矩阵（行=宣称，列=执行）：",
        *_matrix_rows(),
        "4.2 目标国不同为非法，不能提交；类型相同但 war_id 或支持方/目标战争不同，按 −1 计。",
        "4.3 无实际执行时不计分；行动被取消按实际执行结果计分。",
        "4.4 v3.0：无战争且未达成危机安排的和平，即使宣称与执行一致也不加可信度；"
        "双方共同达成危机安排时各 +1 可信度。",
        "",
        "## 5 战争与核",
        f"5.1 war：新开战当轮为第 {war['new_war_counts_as_round']} 轮，"
        f"第 {war['settle_at_end_of_round']} 轮末比较战力一次（战力与倍率由引擎给出）；平局无战果。"
        f"防守方不再获得额外战力倍率（倍率 {_plain(war['defender_power_multiplier'])}）；"
        f"结算时双方战力各乘 ±{_plain(war['power_random_band'] * 100)}% 的战争意志随机修正。"
        f"若目标本轮对你执行了有效和平但未达成协议，该战进攻方 S "
        f"+{_plain(war['target_peace_attacker_s_bonus'])}。",
        f"5.2 战果：胜者（无论攻守）从败者处取得 {_plain(war['winner_transfer'])} 点"
        f"（同一随机 C/E/M 项）；败者失去同等点数；平局无转移。",
        f"5.3 nuclear：使用者从目标处取得 {_plain(nuc['target_transfer'])} 点，"
        f"并向目标转移 {_plain(nuc['user_fallout_transfer'])} 点作为代价（同一随机 C/E/M 项；净 +1）。"
        f"有战争时该战立即判使用者胜。双方同时使用：两次转移都生效，战争结束。"
        f"无战争时同样只做转移。声誉/可信度按规则卡另外变化，但不计分。",
        "",
        "## 6 干涉与和平",
        f"6.1 intervene：只对战斗阶段开始已存在、且经 nuclear/peace 后仍存在的战争生效；"
        f"本轮新开战不可被干涉。执行时先从被干涉方转移 {_plain(itv['success_transfer'])} 点给干涉国；"
        f"若支持方最终未获胜（含平局），干涉国再向被干涉方转移 {_plain(itv['failure_transfer'])} 点。"
        f"该战本轮不结算则不计成败；战争被提前结束则干涉取消。",
        "6.2 peace：和平行动本身不改变积分。无战争常规表态不改变积分；"
        "双方当事国同轮相互和平并达成危机安排时，按场景目标卡结算转移。"
        "进攻方求和 → 战争结束、从进攻方向防守方转移 1 点（同一随机 C/E/M）；"
        "防守方求和 → 谈判；谈判方案必须逐项守恒（攻方所得 = 守方所失）。",
        "",
        "## 7 谈判",
        f"7.1 数值以 {_plain(neg['step'])} 为单位；进攻方所得每项 "
        f"{_plain(neg['attacker_gain_min'])}~{_plain(neg['attacker_gain_max_each'])} 且合计 "
        f"≤{_plain(neg['attacker_gain_max_sum'])}，允许为 0；防守方所失每项 "
        f"{_plain(neg['defender_loss_min_each'])}~{_plain(neg['defender_loss_max_each'])} 且合计 "
        f"≥{_plain(neg['defender_loss_min_sum'])}。",
        "7.2 防守方报价和进攻方接受条件均在揭示前提交；揭示后机械匹配，不再询问模型。匹配接受则结束战争，不再结算常规战果；否则战争继续。",
        "",
        "## 8 并发与结算顺序",
        "8.1 nuclear 先结算；被 nuclear 结束的战争上的 peace/intervene 作废；"
        "干涉成败只在该战本轮结算时计算。",
        "8.2 声誉/可信度在同一批次先合计再截断；只有实际执行的行动计分，但它们不计入零和积分。",
        "8.3 零和校验：每轮结算后，所有存活与退出国家的净积分之和必须为 0；否则记为系统异常。",
        "",
        "## 9 危机与目标卡",
        "9.1 危机阶段机（v4.1）：crisis → war → stalemate → resolved。",
        "    - 第 1–2 轮为外交窗口：只记录、不扣分；",
        "    - 第 3/5/7/9 轮各升一级；C/E/M 成本转移给控制方；",
        "    - 进入战争阶段后只按 0.5/轮固定收取，不随等级升级；战争最长两轮；",
        "    - 战争打平或谈判失败 → 停滞阶段，危机再升一级并恢复全额成本；",
        "    - 共同安排、战争胜者、谈判达成、当事国退出 → resolved，成本停止。",
        "9.2 未解决时，每轮按输入中的 crisis.unresolved_cost 扣减：",
        "    - 直接当事国通常承担安全/伤亡成本（C）；依赖通道国承担贸易/供应成本（E）；其他相关方承担外交信誉成本（不计分）。",
        "    - 上述 C/E/M 成本全部转移给控制方（同一项）；控制方因此有动机拖延，其他方有动机夺权。",
        "    - 成本与解决都会生成公开事件。",
        "    - crisis.level 提升时成本按 escalation_multiplier 放大；具体轮次与倍率见输入。",
        "9.3 危机解决：双方当事国同轮相互执行有效和平 → 共同安排，控制权不变；"
        "当事国之间战争结束并出现胜者 → 胜者取得控制权；控制方死亡 → 控制权转移。",
        "9.4 目标卡：每个国家有私有目标（goal）与红线（red_line）。",
        "    - goal：条件首次成立时立即获得奖励，只计一次；",
        "    - red_line：条件首次成立时检查本国本轮实际行动，若未采取要求的回应类型则扣分，只计一次；",
        "    - C/E/M 奖励/惩罚必须按场景给出的 reward_from / penalty_to 做转移，不能凭空产生或销毁点数；",
        "    - 条件与数值只来自场景输入，不自行推断；所有变化都会写入公开事件或本国私有记录。",
        "    - 可用条件：control_is / crisis_resolved / crisis_level_at_least / attacked_by_any / war_any / "
        "stage_is / resolution_is / stalled_rounds_at_least，以及 any_of / all_of 组合。",
        "9.5 目标卡只改变既有数值，不新增积分维度；声誉/可信度变化不计分。",
    ]


def card_digest(lines: list[str]) -> str:
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def render_markdown() -> str:
    digest = card_digest(build_sections())
    header = (
        f"<!-- 生成物：请勿手改。生成器 strategic_game.tools.gen_rule_card；"
        f"rules_version={R.RULES_VERSION}；content_sha256={digest} -->"
    )
    return "\n".join([header, ""] + build_sections()) + "\n"


def render_json() -> str:
    payload = {
        "rules_version": R.RULES_VERSION,
        "card_format": R.CARD_FORMAT,
        "content_sha256": card_digest(build_sections()),
        "generated_by": "strategic_game.tools.gen_rule_card",
        "card_file": "strategic_game/prompts/v2.1/20-规则卡-v2.0.md",
        "constants": {
            "action_types": list(R.ACTION_TYPES),
            "action_labels_zh": R.ACTION_LABELS_ZH,
            "phases": list(R.PHASES),
            "scoring": R.SCORING,
            "death_rule": R.DEATH_RULE,
            "win_rule": R.WIN_RULE,
            "flow": R.FLOW,
            "reputation": R.REPUTATION,
            "reputation_strength_basis": R.REPUTATION_STRENGTH_BASIS,
            "reputation_nuclear_exception": R.REPUTATION_NUCLEAR_EXCEPTION,
            "credibility_matrix": R.CREDIBILITY_MATRIX,
            "credibility_rules": R.CREDIBILITY_RULES,
            "war": R.WAR,
            "intervene": R.INTERVENE,
            "peace": R.PEACE,
            "nuclear": R.NUCLEAR,
            "negotiation": R.NEGOTIATION,
            "legality_notes": list(R.LEGALITY_NOTES),
            "settlement_steady_state": list(R.SETTLEMENT_STEADY_STATE),
            "crisis": R.CRISIS,
            "objectives": R.OBJECTIVES,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/校验模型侧规则卡与引擎 JSON")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD, help="规则卡 Markdown 输出路径")
    parser.add_argument("--json", dest="json_path", type=Path, default=DEFAULT_JSON,
                        help="引擎用 JSON 输出路径")
    parser.add_argument("--check", action="store_true", help="只校验已提交产物是否为最新")
    parser.add_argument("--stdout", action="store_true", help="打印到标准输出，不写文件")
    args = parser.parse_args(argv)

    errors = R.validate()
    if errors:
        print("规则常量校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    markdown = render_markdown()
    payload = render_json()

    if args.stdout:
        print(markdown, end="")
        return 0

    if args.check:
        stale: list[str] = []
        for path, expected in ((args.md, markdown), (args.json_path, payload)):
            if not path.exists():
                stale.append(f"缺少产物：{path}")
            elif path.read_text(encoding="utf-8") != expected:
                stale.append(f"产物过期（请重新生成）：{path}")
        if stale:
            for item in stale:
                print(item, file=sys.stderr)
            return 1
        print(f"规则卡与 JSON 均为最新；rules_version={R.RULES_VERSION}；"
              f"sha256={card_digest(build_sections())[:16]}")
        return 0

    for path, content in ((args.md, markdown), (args.json_path, payload)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"已写入 {path}")
    print(f"rules_version={R.RULES_VERSION}；content_sha256={card_digest(build_sections())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
