"""把一局运行记录渲染成 markdown 与单文件 HTML 回看。"""

from __future__ import annotations

import html
import copy
import json
from collections import Counter
from typing import Any

from .tools import rules_v4 as R

STATUS_LABEL = {
    "executed": "已执行",
    "canceled": "已取消",
}
STAT_FIELDS = ("c", "e", "m", "rep", "cred")
VICTORY_MARGIN = R.SCORING["victory_margin_strictly_greater_than"]


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _signed(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        return f"+{value}"
    return str(value)


def _status_class(status: str) -> str:
    return "ok" if status == "executed" else "bad"


def _decision_summary(decision: dict[str, Any] | None) -> str:
    if not decision:
        return "无"
    return f"{decision.get('action_type', '?')} → {decision.get('target_country_id', '?')}"


def _countries(state: dict[str, Any] | None) -> dict[str, Any]:
    return (state or {}).get("countries") or {}


def _ranges(values: list[int]) -> str:
    """把 [2, 3, 4, 7] 渲染成 "2–4、7"。"""
    numbers = sorted(values)
    if not numbers:
        return ""
    spans: list[tuple[int, int]] = []
    start = prev = numbers[0]
    for value in numbers[1:]:
        if value == prev + 1:
            prev = value
            continue
        spans.append((start, prev))
        start = prev = value
    spans.append((start, prev))
    return "、".join(f"{a}" if a == b else f"{a}–{b}" for a, b in spans)


def _round_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    """单轮数值变化、行动类型分布与积分领先幅度。"""
    before = _countries(item.get("snapshot"))
    after = _countries(item.get("state_after"))
    changes: dict[str, dict[str, float]] = {}
    for cid, current in after.items():
        previous = before.get(cid)
        if not previous:
            continue
        delta = {
            key: round(current[key] - previous[key], 1)
            for key in STAT_FIELDS
            if key in previous and key in current and current[key] != previous[key]
        }
        if delta:
            changes[cid] = delta
    action_counts = Counter(
        action.get("action_type") for action in (item.get("actions") or {}).values())
    scores = sorted(
        (country.get("score", 0) for country in after.values()
         if country.get("alive", True)), reverse=True)
    return {
        "round": item.get("round"),
        "changes": changes,
        "action_counts": action_counts,
        "scores": scores,
        "margin": round(scores[0] - scores[1], 1) if len(scores) > 1 else None,
    }


def _diagnostics(run_data: dict[str, Any]) -> dict[str, Any]:
    """实验视角的退化检查：状态冻结、全员同型、零升级、胜负可达性、重试。"""
    rounds = run_data.get("rounds") or []
    details = [_round_diagnostics(item) for item in rounds]
    warnings: list[str] = []

    same_type = [item for item in details
                 if sum(item["action_counts"].values()) >= 2
                 and len(item["action_counts"]) == 1]
    if same_type:
        kinds = sorted({next(iter(item["action_counts"])) for item in same_type})
        warnings.append(
            f"第 {_ranges([item['round'] for item in same_type])} 轮所有国家采用同一行动类型"
            f"（{'/'.join(kinds)}）")

    frozen = [item["round"] for item in details if not item["changes"]]
    if frozen:
        warnings.append(
            f"数值冻结：第 {_ranges(frozen)} 轮没有任何国家的 C/E/M/声誉/可信度变化"
            f"（共 {len(frozen)} 轮）")

    escalation = Counter()
    wars = negotiations = events = 0
    for item in rounds:
        settlement = item.get("settlement") or {}
        wars += len(settlement.get("wars_settled") or [])
        negotiations += len(settlement.get("negotiations") or [])
        events += len(item.get("events") or [])
        for action in (item.get("actions") or {}).values():
            escalation[action.get("action_type")] += 1
    escalated = sum(escalation.get(key, 0) for key in ("war", "nuclear", "intervene"))
    crisis_final = (run_data.get("final_state") or {}).get("crisis")
    if (wars == 0 and negotiations == 0 and events == 0 and escalated == 0
            and not (crisis_final and crisis_final.get("resolved"))):
        warnings.append("全运行 0 场战争、0 次谈判、0 个事件、0 次升级类行动"
                        "（war/nuclear/intervene）")
    if crisis_final:
        if not crisis_final.get("resolved"):
            warnings.append(
                f"危机在结束时仍未解决（阶段 {crisis_final.get('stage')}，"
                f"等级 {crisis_final.get('level')}，"
                f"停滞 {crisis_final.get('stalled_rounds')} 轮）")
        elif crisis_final.get("stage") == "stalemate":
            warnings.append("危机以停滞阶段结束")
        elif crisis_final.get("resolution") == "agreement":
            warnings.append("危机通过共同安排解决，未发生战争或核升级")

    result = run_data.get("result") or {}
    final_scores = sorted(
        (country.get("score", 0)
         for country in _countries(run_data.get("final_state")).values()
         if country.get("alive", True)), reverse=True)
    final_margin = (round(final_scores[0] - final_scores[1], 1)
                    if len(final_scores) > 1 else None)
    if not result.get("winner") and final_margin is not None:
        if final_margin < VICTORY_MARGIN:
            warnings.append(
                f"终局无胜者：最高分 {final_scores[0]} 与第二名 {final_scores[1]} 相差 "
                f"{final_margin}，低于胜利门槛 >{VICTORY_MARGIN}")

    records = run_data.get("records") or []
    attempts = sum(len(record.get("attempts") or []) for record in records)
    retried = sum(1 for record in records
                  for attempt in (record.get("attempts") or [])
                  if attempt.get("type") != "ok")
    if retried:
        warnings.append(f"模型输出/调用重试 {retried} 次"
                        f"（共 {len(records)} 条记录、{attempts} 次尝试）")
    output_warnings = sum(
        len(attempt.get("warnings") or [])
        for record in records for attempt in (record.get("attempts") or []))
    if output_warnings:
        warnings.append(f"模型输出提示 {output_warnings} 条"
                        "（多余字段/截断等，见逐轮调用记录）")
    decision_failures = sum(
        1 for record in records
        if (record.get("adopted") is None or record.get("origin") == "fallback") and record.get("phase") != "environment")
    if decision_failures:
        warnings.append(f"国家决策系统失败 {decision_failures} 条，见下方失败明细")
    env_failures = sum(
        1 for record in records
        if record.get("adopted") is None and record.get("phase") == "environment")
    if env_failures:
        warnings.append(f"环境摘要失败 {env_failures} 条（下一轮以摘要不可用运行）")
    score_sum = run_data.get("score_sum")
    if score_sum is None:
        score_sum = round(sum(
            item.get("score", 0)
            for item in _countries(run_data.get("final_state")).values()), 1)
    if abs(float(score_sum)) > 1e-9:
        warnings.append(f"零和守恒失败：全局净积分之和为 {score_sum}，应为 0")
    return {"warnings": warnings, "details": details, "final_margin": final_margin,
            "retries": retried, "failures": decision_failures,
            "env_failures": env_failures, "score_sum": score_sum}


def _change_text(changes: dict[str, dict[str, float]]) -> str:
    if not changes:
        return "无"
    return "；".join(
        f"{cid} " + " ".join(f"{key}{_signed(delta)}" for key, delta in fields.items())
        for cid, fields in changes.items())


def render_markdown(run_data: dict[str, Any]) -> str:
    result = run_data["result"]
    lines = [
        "# 运行结果",
        "",
        f"- 场景：{run_data['scenario_id']}",
        f"- 提供方：{run_data['provider']} / {run_data['model']}",
        f"- 规则版本：{run_data['rules_version']}　提示词版本：{run_data['prompt_version']}",
        f"- 决策协议：{run_data.get('decision_protocol', 'legacy')}；谈判模式：{run_data.get('negotiation_mode', 'offers')}",
        f"- 默认和平：{run_data.get('fallback_count', 0)} 次（单独标记，按 v4.1 正常结算）",
        f"- 预测评估：{json.dumps(run_data.get('forecast_metrics', {}), ensure_ascii=False)}（仅统计模型对模型的密封行动预测，系统回退样本排除）",
        f"- 种子：{run_data['seed']}　引擎：{run_data['engine_version']}",
        f"- 结果：{result.get('winner') or '无胜者'}（{result.get('reason')}）",
        "",
        "## 最终数值",
        "",
        "| 国家 | 存活 | C | E | M | 声誉（不计分） | 可信度（不计分） | 净积分 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cid, item in sorted(run_data["final_state"]["countries"].items()):
        lines.append(
            f"| {cid} {item['name']} | {'是' if item['alive'] else '否'} | "
            f"{item['c']} | {item['e']} | {item['m']} | {item['rep']} | "
            f"{item['cred']} | {item['score']} |")
    score_sum = run_data.get("score_sum")
    if score_sum is None:
        score_sum = round(sum(
            item["score"] for item in run_data["final_state"]["countries"].values()), 1)
    lines += ["", f"总分（零和，应为 0）：{score_sum}"]

    diagnostics = _diagnostics(run_data)
    crisis = (run_data.get("final_state") or {}).get("crisis")
    if crisis:
        state_text = ("已解决（" + str(crisis.get("resolution")) + "）"
                      if crisis.get("resolved") else "未解决")
        lines += ["", "## 危机",
                  f"- {crisis.get('title')}：{state_text}；阶段 {crisis.get('stage')}；"
                  f"等级 {crisis.get('level')}；停滞 {crisis.get('stalled_rounds')} 轮；"
                  f"控制方 {crisis.get('control') or '无'}",
                  f"- 当事国 {'/'.join(crisis.get('parties') or [])}；"
                  f"依赖国 {'/'.join(crisis.get('dependents') or []) or '无'}"]
        statuses = (run_data.get("final_state") or {}).get("objective_states") or {}
        achieved = [(cid, oid) for cid, items in statuses.items()
                    for oid, value in items.items()
                    if value.get("achieved") or value.get("triggered")]
        if achieved:
            lines.append("- 目标卡触发：" + "、".join(
                f"{cid}/{oid}" for cid, oid in achieved))
    lines += ["", "## 运行诊断", ""]
    if diagnostics["warnings"]:
        lines += [f"- {item}" for item in diagnostics["warnings"]]
    else:
        lines.append("- 未发现明显的策略退化迹象。")
    lines += ["", "| 轮 | 行动类型分布 | 数值变化国家 | 最高分 | 与第二名差 |",
              "| ---: | --- | ---: | ---: | ---: |"]
    for item in diagnostics["details"]:
        counts = item["action_counts"]
        dist = "、".join(f"{key}×{value}" for key, value in sorted(counts.items())) or "—"
        top = item["scores"][0] if item["scores"] else "—"
        margin = item["margin"] if item["margin"] is not None else "—"
        lines.append(f"| {item['round']} | {dist} | {len(item['changes'])} | {top} | {margin} |")

    lines += ["", "## 每轮", ""]
    for index, item in enumerate(run_data["rounds"]):
        lines.append(f"### 第 {item['round']} 轮")
        declarations = item.get("declarations") or {}
        actions = item.get("actions") or {}
        if declarations:
            lines.append("- 宣称：" + "；".join(
                f"{cid} {dec['action_type']}→{dec['target_country_id']}"
                for cid, dec in declarations.items()))
        if actions:
            lines.append("- 行动：" + "；".join(
                f"{cid} {act['action_type']}→{act['target_country_id']}"
                f"（{STATUS_LABEL.get(item['settlement']['executed'].get(cid), '未提交')}）"
                for cid, act in actions.items()))
        if item["settlement"].get("wars_settled"):
            lines.append("- 战果：" + "；".join(
                f"{war['war_id']} {war['winner'] or '平局'}"
                for war in item["settlement"]["wars_settled"]))
        if item.get("events"):
            lines.append("- 事件：" + "；".join(event["text"] for event in item["events"]))
        lines.append("- 数值变化：" + _change_text(diagnostics["details"][index]["changes"]))
        round_crisis = (item.get("state_after") or {}).get("crisis")
        if round_crisis:
            lines.append(f"- 危机：阶段 {round_crisis.get('stage')}，"
                         f"等级 {round_crisis.get('level')}，"
                         + ("已解决" if round_crisis.get("resolved") else "未解决")
                         + f"；停滞 {round_crisis.get('stalled_rounds')} 轮；"
                         + f"控制方 {round_crisis.get('control') or '无'}")
        for cid, observation in (item.get("private_threat_observations") or {}).items():
            lines.append(f"- 私有威胁认知（{cid}）：{observation.get('level')}；"
                         f"对象 {observation.get('target_country_id') or '整体局势'}；"
                         f"依据 {observation.get('basis')}；来源 {observation.get('source_ids')}")
        lines.append("")

    records = run_data["records"]
    failures = [record for record in records if (record.get("adopted") is None or record.get("origin") == "fallback")
                and record.get("phase") != "environment"]
    env_failures = [record for record in records if record.get("adopted") is None
                    and record.get("phase") == "environment"]
    lines += ["## 系统失败", "",
              f"国家决策失败 {len(failures)} 条；环境摘要失败 {len(env_failures)} 条。"]
    for record in failures[:20]:
        attempt = (record.get("attempts") or [{}])[-1]
        lines.append(f"- 第 {record.get('round')} 轮 {record.get('phase')} "
                     f"{record.get('country_id') or ''}：{attempt.get('error')}")
    for record in env_failures[:20]:
        attempt = (record.get("attempts") or [{}])[-1]
        lines.append(f"- 第 {record.get('round')} 轮环境摘要：{attempt.get('error')}")
    return "\n".join(lines) + "\n"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_esc(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _facts(summary: dict[str, Any] | None) -> str:
    if not summary:
        return '<p class="muted">本轮无环境摘要（或不可用）。</p>'
    parts = ["<h4>公开事实</h4><ul>"]
    for item in summary.get("facts", []):
        ids = ", ".join(item.get("source_ids", []))
        parts.append(f"<li>{_esc(item.get('text'))} "
                     f'<span class="muted">[{_esc(ids)}]</span></li>')
    parts.append("</ul>")
    interpretations = summary.get("interpretations") or []
    if interpretations:
        parts.append("<h4>推断</h4><ul>")
        for item in interpretations:
            parts.append(f"<li>{_esc(item.get('text'))} "
                         f'<span class="muted">不确定：{_esc(item.get("uncertainty"))}</span></li>')
        parts.append("</ul>")
    questions = summary.get("open_questions") or []
    if questions:
        parts.append("<h4>未决</h4><ul>" + "".join(
            f"<li>{_esc(item)}</li>" for item in questions) + "</ul>")
    return "".join(parts)


def _records_for_round(run_data: dict[str, Any], round_no: int) -> list[dict[str, Any]]:
    return [record for record in run_data["records"]
            if record.get("round") == round_no]


def _render_record(record: dict[str, Any]) -> str:
    adopted = record.get("adopted")
    title = (f"第 {record.get('round')} 轮 · {record.get('phase')} · "
             f"{record.get('country_id') or '环境'}"
             f" · {'系统默认和平' if record.get('origin') == 'fallback' else ('成功' if adopted else '失败')}")
    parts = [f"<details><summary>{_esc(title)}</summary>"]
    attempts = record.get("attempts") or []
    parts.append(f'<p class="muted">尝试次数：{len(attempts)}</p>')
    for attempt in attempts:
        label = f"第 {attempt.get('attempt')} 次 · {attempt.get('type')}"
        parts.append(f"<p><b>{_esc(label)}</b></p>")
        if attempt.get("error"):
            parts.append(f'<p class="bad">错误：{_esc(attempt["error"])}</p>')
        if attempt.get("warnings"):
            parts.append(f'<p class="muted">提示：{_esc("；".join(attempt["warnings"]))}</p>')
        if attempt.get("raw"):
            parts.append(f"<pre>{_esc(attempt['raw'])}</pre>")
    if adopted:
        parts.append("<p><b>采用的决定</b></p>")
        parts.append(f"<pre>{_esc(json.dumps(adopted, ensure_ascii=False, indent=2))}</pre>")
    parts.append("</details>")
    return "".join(parts)


def render_html(run_data: dict[str, Any]) -> str:
    result = run_data["result"]
    rounds = run_data["rounds"]
    records = run_data["records"]
    failures = [record for record in records if (record.get("adopted") is None or record.get("origin") == "fallback")
                and record.get("phase") != "environment"]
    env_failures = [record for record in records if record.get("adopted") is None
                    and record.get("phase") == "environment"]
    winner = result.get("winner") or "无胜者"
    diagnostics = _diagnostics(run_data)

    nav = " ".join(f'<a href="#r{item["round"]}">第 {item["round"]} 轮</a>' for item in rounds)

    final_rows = []
    for cid, item in sorted(run_data["final_state"]["countries"].items()):
        alive = "是" if item["alive"] else "否"
        final_rows.append([
            _esc(f"{cid} {item['name']}"), _esc(alive),
            _esc(item["c"]), _esc(item["e"]), _esc(item["m"]),
            _esc(_signed(item["rep"])), _esc(_signed(item["cred"])),
            f"<b>{_esc(item['score'])}</b>",
        ])

    sections: list[str] = []
    detail_by_round = {item["round"]: item for item in diagnostics["details"]}
    for item in rounds:
        round_no = item["round"]
        settlement = item.get("settlement") or {}
        executed = settlement.get("executed") or {}
        canceled = settlement.get("canceled") or {}
        records = _records_for_round(run_data, round_no)

        decl_rows = [
            [_esc(cid), _esc(dec["action_type"]), _esc(dec["target_country_id"]),
             _esc(dec.get("war_id") or "—"), _esc(dec.get("support_country_id") or "—"),
             _esc(dec.get("public_statement") or "—")]
            for cid, dec in (item.get("declarations") or {}).items()
        ]
        action_rows = []
        for cid, act in (item.get("actions") or {}).items():
            status = executed.get(cid, "未提交")
            reason = canceled.get(cid) or ""
            css = _status_class(status) if status in STATUS_LABEL else "muted"
            action_rows.append([
                _esc(cid), _esc(act["action_type"]), _esc(act["target_country_id"]),
                _esc(act.get("war_id") or "—"), _esc(act.get("support_country_id") or "—"),
                _esc(act.get("public_statement") or "—"),
                f'<span class="{css}">{_esc(STATUS_LABEL.get(status, status))}</span>'
                + (f'<div class="muted">{_esc(reason)}</div>' if reason else ""),
            ])

        wars = settlement.get("wars_settled") or []
        war_rows = [[
            _esc(war["war_id"]), _esc(war.get("winner") or "平局"),
            _esc(war.get("attacker_power")), _esc(war.get("defender_power")),
        ] for war in wars]

        negotiations = settlement.get("negotiations") or []
        negotiation_rows = [[
            _esc(neg.get("war_id")), _esc(neg.get("mode") or run_data.get("negotiation_mode")),
            _esc((neg.get("defender") or {}).get("offer_id") or "—"),
            _esc(neg.get("attacker") or "—"),
            _esc("接受" if neg.get("accepted") else "未达成"),
        ] for neg in negotiations]

        events = item.get("events") or []
        event_html = "".join(f"<li>{_esc(event['text'])}</li>" for event in events) or \
            '<li class="muted">无</li>'

        options = item.get("option_orders") or {}
        option_html = "".join(
            f"<p><code>{_esc(key)}</code>：{_esc(' → '.join(ids))}</p>"
            for key, ids in options.items()) or "<p class=\"muted\">无</p>"
        threat_rows = [[_esc(cid), _esc(obs.get("target_country_id") or "整体局势"),
                        _esc(obs.get("level")), _esc(obs.get("basis")),
                        _esc(", ".join(obs.get("source_ids") or []))]
                       for cid, obs in (item.get("private_threat_observations") or {}).items()]

        sections.append(f"""
<section class="round" id="r{round_no}">
  <h2>第 {round_no} 轮</h2>
  <p class="muted">本轮数值变化：{_esc(_change_text(detail_by_round[round_no]["changes"]))}</p>
  <h3>宣称</h3>
  {_table(["国家", "类型", "目标国", "war_id", "支持方", "公开声明"], decl_rows) if decl_rows else '<p class="muted">无</p>'}
  <h3>行动与结果</h3>
  {_table(["国家", "类型", "目标国", "war_id", "支持方", "公开声明", "结果"], action_rows) if action_rows else '<p class="muted">无（可能全部系统失败）</p>'}
  <h3>常规战结算</h3>
  {_table(["战争", "胜者", "进攻方战力", "防守方战力"], war_rows) if war_rows else '<p class="muted">本轮无结算</p>'}
  <h3>谈判</h3>
  {_table(["战争", "模式", "方案", "进攻方决定", "结果"], negotiation_rows) if negotiation_rows else '<p class="muted">无</p>'}
  <h3>公开事件</h3>
  <ul>{event_html}</ul>
  <h3>环境摘要</h3>
  {_facts(item.get("environment_summary"))}
  <details><summary>本国私有威胁认知（研究者可见，分享前审阅）</summary>
  {_table(["国家", "对象", "程度", "依据", "来源"], threat_rows) if threat_rows else '<p class="muted">无有效记录或未启用</p>'}
  </details>
  <details><summary>本轮选项顺序</summary>{option_html}</details>
  <details><summary>模型调用与原始输出（{len(records)} 条）</summary>
    {''.join(_render_record(record) for record in records) or '<p class="muted">无</p>'}
  </details>
</section>""")

    failure_html = "".join(
        f"<li>第 {_esc(record.get('round'))} 轮 {_esc(record.get('phase'))} "
        f"{_esc(record.get('country_id') or '环境')}："
        f"{_esc((record.get('attempts') or [{}])[-1].get('error'))}</li>"
        for record in failures) or '<li class="muted">无</li>'
    env_failure_html = "".join(
        f"<li>第 {_esc(record.get('round'))} 轮环境摘要："
        f"{_esc((record.get('attempts') or [{}])[-1].get('error'))}</li>"
        for record in env_failures) or '<li class="muted">无</li>'

    diagnostic_items = "".join(
        f'<li class="warn">{_esc(item)}</li>' for item in diagnostics["warnings"]) or \
        '<li class="muted">未发现明显的策略退化迹象。</li>'
    diagnostic_rows = []
    for item in diagnostics["details"]:
        counts = item["action_counts"]
        dist = "、".join(f"{key}×{value}" for key, value in sorted(counts.items())) or "—"
        top = item["scores"][0] if item["scores"] else "—"
        margin = item["margin"] if item["margin"] is not None else "—"
        diagnostic_rows.append([_esc(item["round"]), _esc(dist),
                                _esc(len(item["changes"])), _esc(top), _esc(margin)])
    crisis = (run_data.get("final_state") or {}).get("crisis")
    final_margin = diagnostics["final_margin"]
    margin_text = "—" if final_margin is None else f"{final_margin}（胜利需 >{VICTORY_MARGIN}）"
    score_sum = run_data.get("score_sum")
    if score_sum is None:
        score_sum = round(sum(
            item["score"] for item in run_data["final_state"]["countries"].values()), 1)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>模拟回看 · {_esc(run_data['scenario_id'])}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #f6f7f9; color: #1f2328;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  h2 {{ margin: 0 0 12px; font-size: 18px; }}
  h3 {{ margin: 18px 0 8px; font-size: 14px; color: #57606a; }}
  h4 {{ margin: 10px 0 4px; font-size: 13px; }}
  .muted {{ color: #57606a; }}
  .bad {{ color: #cf222e; }}
  .warn {{ color: #9a6700; }}
  .diagnostics {{ margin: 6px 0 14px; padding-left: 20px; }}
  .ok {{ color: #1a7f37; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
          gap: 12px; margin: 16px 0; }}
  .card {{ background: #fff; border: 1px solid #d8dee4; border-radius: 10px; padding: 12px; }}
  .card b {{ display: block; font-size: 12px; color: #57606a; font-weight: 500; }}
  .card span {{ font-size: 15px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ border: 1px solid #d8dee4; padding: 6px 8px; font-size: 13px;
           text-align: left; vertical-align: top; }}
  th {{ background: #eef1f4; font-weight: 600; }}
  .round {{ background: #fff; border: 1px solid #d8dee4; border-radius: 12px;
           padding: 16px; margin: 18px 0; }}
  details {{ border: 1px solid #d8dee4; border-radius: 8px; padding: 8px 12px;
             margin: 8px 0; background: #fbfcfd; }}
  summary {{ cursor: pointer; font-weight: 600; }}
  pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f8fa;
        padding: 8px; border-radius: 6px; font-size: 12px; margin: 6px 0; }}
  nav a {{ margin-right: 12px; color: #0969da; text-decoration: none; }}
  ul {{ margin: 4px 0 0; padding-left: 20px; }}
  code {{ background: #eef1f4; padding: 1px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>模拟回看</h1>
  <p class="muted">场景 {_esc(run_data['scenario_id'])} · 提供方 {_esc(run_data['provider'])}/{_esc(run_data['model'])} ·
    规则 {_esc(run_data['rules_version'])} · 协议 {_esc(run_data.get('decision_protocol', 'legacy'))} · 提示词 {_esc(run_data['prompt_version'])} ·
    谈判模式 {_esc(run_data.get('negotiation_mode', 'offers'))} · 种子 {_esc(run_data['seed'])}</p>

  <div class="grid">
    <div class="card"><b>结果</b><span>{_esc(winner)}（{_esc(result.get('reason'))}）</span></div>
    <div class="card"><b>轮数</b><span>{len(rounds)}</span></div>
    <div class="card"><b>系统失败</b><span>{len(failures)}</span></div>
    <div class="card"><b>环境摘要失败</b><span>{len(env_failures)}</span></div>
    <div class="card"><b>重试</b><span>{diagnostics['retries']}</span></div>
    <div class="card"><b>最高分-第二名</b><span>{_esc(margin_text)}</span></div>
    <div class="card"><b>零和总分</b><span>{_esc(score_sum)}</span></div>
    {f'<div class="card"><b>危机</b><span>{_esc(crisis.get("title"))} · '
     f'{"已解决" if crisis.get("resolved") else "未解决"}（等级 {_esc(crisis.get("level"))}）'
     f'</span></div>' if crisis else ''}
    <div class="card"><b>引擎 / 规则</b><span>{_esc(run_data['engine_version'])} / {_esc(run_data['rules_version'])}</span></div>
  </div>

  <h2>最终数值</h2>
  {_table(["国家", "存活", "C", "E", "M", "声誉（不计分）", "可信度（不计分）", "净积分"], final_rows)}

  {f'<h2>危机</h2><p>{_esc(crisis.get("title"))}：'
     f'{"已解决" if crisis.get("resolved") else "未解决"} · '
     f'阶段 {_esc(crisis.get("stage"))} · 等级 {_esc(crisis.get("level"))} · '
     f'停滞 {_esc(crisis.get("stalled_rounds"))} 轮 · '
     f'控制方 {_esc(crisis.get("control") or "无")} · '
     f'当事国 {_esc("/".join(crisis.get("parties") or []))} · '
     f'依赖国 {_esc("/".join(crisis.get("dependents") or []) or "无")}</p>' if crisis else ''}
  <h2>运行诊断</h2>
  <ul class="diagnostics">{diagnostic_items}</ul>
  <h3>每轮状态变化</h3>
  {_table(["轮", "行动类型分布", "数值变化国家", "最高分", "与第二名差"], diagnostic_rows)}

  <h2>逐轮</h2>
  <nav>{nav}</nav>
  {''.join(sections)}

  <h2>系统失败明细</h2>
  <h3>国家决策</h3>
  <p>默认和平 {_esc(run_data.get('fallback_count', 0))} 次；与模型主动和平分别统计。预测指标（排除回退样本）：{_esc(json.dumps(run_data.get('forecast_metrics', {}), ensure_ascii=False))}</p>
  <ul>{failure_html}</ul>
  <h3>环境摘要</h3>
  <ul>{env_failure_html}</ul>
  <p class="muted">提示：完整的提示词与原始响应保存在同目录的 run.json。</p>
</div>
</body>
</html>
"""


def public_export(run_data: dict[str, Any]) -> dict[str, Any]:
    """Allowlist export: never serialize researcher-only scene/decision fields."""
    def public_state(state: dict[str, Any] | None) -> dict[str, Any]:
        state = state or {}
        public = copy.deepcopy({key: state[key] for key in (
            "scenario_id", "round", "max_rounds", "countries", "wars", "crisis",
            "events", "event_count", "environment_summary") if key in state})
        if not run_data.get("scenario", {}).get("visibility", {}).get("ce_m_public", True):
            for country in public.get("countries", {}).values():
                for key in ("c", "e", "m"):
                    country.pop(key, None)
        return public

    result = {key: copy.deepcopy(run_data[key]) for key in (
        "schema_version", "decision_protocol", "failure_policy", "rules_version",
        "engine_version", "prompt_version", "scenario_id", "seed", "result",
        "run_status", "score_sum", "fallback_count") if key in run_data}
    result["final_state"] = public_state(run_data.get("final_state"))
    result["rounds"] = []
    for item in run_data.get("rounds", []):
        public = {key: copy.deepcopy(item[key]) for key in (
            "round", "declarations", "actions", "events", "environment_summary",
            "end_check", "origins") if key in item}
        public["snapshot"] = public_state(item.get("snapshot"))
        public["state_after"] = public_state(item.get("state_after"))
        # Only the execution status is needed publicly; unused acceptance policies,
        # private goal bookkeeping and research measurements stay in run.json.
        public["execution_status"] = copy.deepcopy(item.get("settlement", {}).get("executed", {}))
        result["rounds"].append(public)
    return result
