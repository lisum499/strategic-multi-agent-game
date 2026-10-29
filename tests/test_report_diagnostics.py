"""报告诊断测试：退化告警（状态冻结 / 全员同型 / 胜负可达性）必须出现在回看里。

背景：2026-10-29 的 DeepSeek 运行 10 轮全部为 peace，第 5 轮声誉/可信度触顶后
状态完全冻结，但旧报告没有任何提示。这些测试守住"报告要能让人一眼看出退化"。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


from strategic_game.engine import Engine
from strategic_game.providers import Provider
from strategic_game.protocol import make_fallback
from strategic_game.report import render_html, render_markdown


class PeaceProvider(Provider):
    """每轮都选 peace 的确定性提供方，用来复现规则层面的退化轨迹。"""

    name = "peace"
    model = "peace-test"

    def decide(self, phase: str, system: str, user: str,
               situation: dict) -> str:
        if phase != "sealed":
            raise AssertionError(f"不应出现的阶段：{phase}")
        package = make_fallback(situation)
        package["private_rationale"] = "维持和平的离线固定回答。"
        package["declaration"]["public_statement"] = "愿以和平方式处理分歧。"
        package["execution"]["public_statement"] = "愿以和平方式处理分歧。"
        return json.dumps(package, ensure_ascii=False)

    def summarize(self, round_no: int, public_events: list, public_state: dict,
                  previous_summary: dict | None,
                  public_declarations: list | None = None,
                  public_actions: list | None = None) -> str:
        return json.dumps({
            "round": round_no,
            "facts": [{"text": "当前公开状态可查看", "source_ids": ["state"]}],
            "interpretations": [],
            "open_questions": [],
        }, ensure_ascii=False)


def _peace_run() -> dict:
    scenario = {
        "scenario_id": "degeneracy_test",
        "seed": 1,
        "max_rounds": 10,
        "countries": [
            {"country_id": "A", "name": "甲", "c": 3.0, "e": 3.0, "m": 3.0},
            {"country_id": "B", "name": "乙", "c": 3.0, "e": 3.0, "m": 3.0},
        ],
    }
    engine = Engine(scenario, PeaceProvider(), seed=1, log_prompts=False)
    engine.run()
    return engine.summary()


def _country(score: float, c: float = 3.0) -> dict:
    return {"name": "国", "alive": True, "c": c, "e": 3.0, "m": 3.0,
            "rep": 1.0, "cred": 1.0, "score": score}


def _dynamic_run() -> dict:
    """一局有战争、事件、状态变化的合成记录，用于确认不会误报退化。"""
    return {
        "scenario_id": "dynamic",
        "provider": "scripted",
        "model": "scripted",
        "rules_version": "v2.1",
        "prompt_version": "v2.7",
        "negotiation_mode": "offers",
        "seed": 1,
        "engine_version": "0.5.3",
        "result": {"winner": "A", "reason": "积分领先"},
        "final_state": {"countries": {"A": _country(5.0, 4.0),
                                      "B": _country(-5.0, 2.0)}},
        "rounds": [{
            "round": 1,
            "snapshot": {"countries": {"A": _country(10.0), "B": _country(10.0)}},
            "declarations": {"A": {"action_type": "war", "target_country_id": "B"},
                             "B": {"action_type": "peace", "target_country_id": "A"}},
            "actions": {"A": {"action_type": "war", "target_country_id": "B"},
                        "B": {"action_type": "peace", "target_country_id": "A"}},
            "settlement": {"executed": {"A": "executed", "B": "executed"},
                           "wars_settled": [{"war_id": "w_1", "winner": "A"}]},
            "events": [{"id": "e1", "text": "A 赢得战争"}],
            "state_after": {"countries": {"A": _country(13.0, 4.0),
                                          "B": _country(7.0, 2.0)}},
        }],
        "records": [],
    }


class ReportDiagnosticsTest(unittest.TestCase):
    def test_frozen_all_peace_run_is_flagged(self):
        text = render_markdown(_peace_run())
        self.assertIn("运行诊断", text)
        self.assertIn("数值冻结", text)
        self.assertIn("同一行动类型", text)
        self.assertIn("终局无胜者", text)
        self.assertIn("全运行 0 场战争", text)
        self.assertIn("- 数值变化：无", text)

    def test_html_reports_diagnostics(self):
        html = render_html(_peace_run())
        self.assertIn("运行诊断", html)
        self.assertIn("数值冻结", html)
        self.assertIn("最高分-第二名", html)

    def test_dynamic_run_has_no_degeneracy_warning(self):
        text = render_markdown(_dynamic_run())
        self.assertIn("运行诊断", text)
        self.assertIn("未发现明显的策略退化迹象", text)
        self.assertNotIn("数值冻结", text)
        self.assertNotIn("同一行动类型", text)
        self.assertNotIn("终局无胜者", text)


if __name__ == "__main__":
    unittest.main()
