"""Migrated v4.1 rule regressions, independent of decision protocol."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


from strategic_game.engine import Engine
from strategic_game.providers import MockProvider, ScriptedProvider
from strategic_game.scenario import load_scenario
from strategic_game.tools import rules_v4 as R
from strategic_game.validation import ResponseError, validate_decision

BASIS = {"依据": "测试", "风险": "测试", "不确定": "测试"}


def make_scenario(**overrides) -> dict:
    data = {
        "scenario_id": "zero-sum-test",
        "rules_version": "v4.1",
        "max_rounds": 4,
        "countries": [
            {"country_id": "A", "name": "甲", "c": 3.0, "e": 3.0, "m": 3.0},
            {"country_id": "B", "name": "乙", "c": 3.0, "e": 3.0, "m": 3.0},
            {"country_id": "C", "name": "丙", "c": 3.0, "e": 3.0, "m": 3.0},
        ],
    }
    data.update(overrides)
    return data


def peace(target: str) -> dict:
    return {"action_type": "peace", "target_country_id": target,
            "war_id": None, "support_country_id": None}


def score_sum(engine: Engine) -> float:
    return round(sum(country.score for country in engine.state.countries.values()), 1)


class ZeroSumBasicsTest(unittest.TestCase):
    def test_initial_scores_are_zero(self):
        engine = Engine(make_scenario(), MockProvider(), log_prompts=False)
        self.assertEqual([country.score for country in
                          engine.state.countries.values()], [0.0, 0.0, 0.0])
        self.assertEqual(score_sum(engine), 0.0)

    def test_peace_does_not_change_scores(self):
        engine = Engine(make_scenario(), ScriptedProvider({}), log_prompts=False)
        engine.declarations = {"A": peace("B"), "B": peace("A"), "C": peace("A")}
        engine.actions = {"A": peace("B"), "B": peace("A"), "C": peace("A")}
        engine._settle()
        self.assertEqual(score_sum(engine), 0.0)
        self.assertEqual([country.score for country in
                          engine.state.countries.values()], [0.0, 0.0, 0.0])

    def test_war_transfer_is_conserved(self):
        scenario = make_scenario(initial_wars=[{
            "war_id": "w_1", "attacker_id": "A", "defender_id": "B",
            "started_round": 1, "progress_round": 2}])
        scenario["countries"][0]["m"] = 3.5
        engine = Engine(scenario, ScriptedProvider({}), log_prompts=False)
        engine.state.round = 2
        engine.declarations = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine.actions = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine._war_power_factors = lambda war_id: (1.0, 1.0)
        settlement = engine._settle()
        self.assertEqual(settlement["wars_settled"][0]["winner"], "A")
        self.assertEqual(score_sum(engine), 0.0)
        self.assertGreater(engine.state.countries["A"].score, 0)
        self.assertLess(engine.state.countries["B"].score, 0)

    def test_nuclear_transfer_is_conserved(self):
        engine = Engine(make_scenario(), ScriptedProvider({}), log_prompts=False)
        engine.actions = {
            "A": {"action_type": "nuclear", "target_country_id": "B",
                  "war_id": None, "support_country_id": None},
        }
        engine._settle()
        self.assertEqual(score_sum(engine), 0.0)

    def test_crisis_costs_transfer_to_controller(self):
        scenario = make_scenario(crisis={
            "crisis_id": "z", "title": "测试危机",
            "parties": ["A", "B"], "dependents": ["C"], "control": "A",
            "stake": "测试标的",
            "unresolved_cost": {"parties": {"c": -0.5},
                                "dependents": {"e": -0.5},
                                "others": {"rep": -0.2}},
            "escalation_rounds": [3], "escalation_multiplier": 1.5,
        })
        engine = Engine(scenario, ScriptedProvider({}), log_prompts=False)
        engine.state.round = 3   # 前 2 轮为外交窗口
        engine.declarations = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine.actions = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine._settle()
        self.assertEqual(score_sum(engine), 0.0)
        self.assertGreater(engine.state.countries["A"].score, 0.0)
        self.assertLess(engine.state.countries["B"].score, 0.0)
        self.assertLess(engine.state.countries["C"].score, 0.0)

    def test_objective_transfer_is_conserved(self):
        scenario = make_scenario(
            crisis={"crisis_id": "z", "title": "测试危机",
                    "parties": ["A", "B"], "dependents": ["C"], "control": "A",
                    "stake": "测试标的",
                    "unresolved_cost": {}, "escalation_rounds": [],
                    "escalation_multiplier": 1.5},
            objectives=[{"country_id": "A", "objectives": [
                {"id": "a_goal", "type": "goal", "description": "测试目标",
                 "condition": {"crisis_resolved": True},
                 "reward": {"e": 0.5}, "reward_from": "B"}]}],
        )
        engine = Engine(scenario, ScriptedProvider({}), log_prompts=False)
        engine.declarations = {"A": peace("B"), "B": peace("A"), "C": peace("A")}
        engine.actions = {"A": peace("B"), "B": peace("A"), "C": peace("A")}
        engine._settle()
        self.assertEqual(score_sum(engine), 0.0)

    def test_negotiation_must_be_symmetric(self):
        engine = Engine(make_scenario(), ScriptedProvider({}), log_prompts=False)
        situation = {"role": "defender", "offer_bounds": R.NEGOTIATION}
        bad = json.dumps({
            "decision_basis": BASIS,
            "changes": {"attacker": {"c": 1.0, "e": 0.0, "m": 0.0},
                        "defender": {"c": -0.5, "e": 0.0, "m": 0.0}},
        }, ensure_ascii=False)
        with self.assertRaises(ResponseError):
            validate_decision(bad, "negotiation", situation)

    def test_rule_constants_are_v4_zero_sum(self):
        self.assertEqual(R.RULES_VERSION, "v4.1")
        self.assertEqual(R.SCORING["score_conservation"], "全局净积分之和恒为 0")
        self.assertEqual(R.WAR["winner_transfer"] + R.WAR["loser_transfer"], 0.0)
        self.assertEqual(R.validate(), [])


class StageMachineTest(unittest.TestCase):
    def _crisis_scenario(self, **extra) -> dict:
        config = {
            "crisis_id": "stage", "title": "阶段测试",
            "parties": ["A", "B"], "dependents": ["C"], "control": "A",
            "stake": "测试标的",
            "unresolved_cost": {"parties": {"c": -0.5},
                                "dependents": {"e": -0.5},
                                "others": {"rep": -0.2}},
            "escalation_rounds": [3, 5], "escalation_multiplier": 1.5,
            "grace_rounds": 2, "war_stage_multiplier": 0.5,
        }
        config.update(extra)
        return make_scenario(crisis=config, **extra)

    def test_grace_rounds_have_no_cost(self):
        engine = Engine(self._crisis_scenario(), ScriptedProvider({}),
                        log_prompts=False)
        engine.state.round = 2
        engine.declarations = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine.actions = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine._settle()
        self.assertEqual(score_sum(engine), 0.0)
        self.assertEqual([country.score for country in
                          engine.state.countries.values()], [0.0, 0.0, 0.0])
        self.assertEqual(engine.state.crisis.stage, "crisis")

    def test_war_stage_charges_fixed_cost_without_escalation(self):
        scenario = self._crisis_scenario(initial_wars=[{
            "war_id": "w_1", "attacker_id": "A", "defender_id": "B",
            "started_round": 1, "progress_round": 1}])
        engine = Engine(scenario, ScriptedProvider({}), log_prompts=False)
        engine.state.round = 3
        engine.declarations = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine.actions = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine._settle()
        self.assertEqual(engine.state.crisis.stage, "war")
        self.assertEqual(score_sum(engine), 0.0)
        # 战争阶段固定 0.5/轮：B 的 C 与 C 的 E 各转移 0.5 给控制方 A。
        self.assertGreater(engine.state.countries["A"].score, 0.0)
        self.assertLess(engine.state.countries["B"].score, 0.0)
        self.assertLess(engine.state.countries["C"].score, 0.0)

    def test_war_tie_enters_stalemate_and_raises_level(self):
        scenario = self._crisis_scenario(initial_wars=[{
            "war_id": "w_1", "attacker_id": "A", "defender_id": "B",
            "started_round": 1, "progress_round": 2}])
        engine = Engine(scenario, ScriptedProvider({}), log_prompts=False)
        engine.state.round = 4
        engine.declarations = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine.actions = {"A": peace("C"), "B": peace("C"), "C": peace("A")}
        engine._war_power_factors = lambda war_id: (1.0, 1.0)
        engine._settle()
        self.assertEqual(engine.state.crisis.stage, "stalemate")
        self.assertGreaterEqual(engine.state.crisis.level, 2)
        self.assertEqual(score_sum(engine), 0.0)

    def test_stage_and_resolution_conditions(self):
        from strategic_game.rules import objective_condition_met
        engine = Engine(self._crisis_scenario(), ScriptedProvider({}),
                        log_prompts=False)
        self.assertTrue(objective_condition_met(
            {"stage_is": "crisis"}, engine.state, "C1", set()))
        self.assertFalse(objective_condition_met(
            {"stage_is": "war"}, engine.state, "C1", set()))
        engine.state.crisis.resolved = True
        engine.state.crisis.resolution = "war"
        engine.state.crisis.stage = "resolved"
        self.assertTrue(objective_condition_met(
            {"resolution_is": "war"}, engine.state, "C1", set()))
        self.assertFalse(objective_condition_met(
            {"resolution_is": "agreement"}, engine.state, "C1", set()))

    def test_obsolete_escalation_rounds_are_configured(self):
        scenario = load_scenario("seven_nations_crisis_v4_1.json")
        self.assertEqual(scenario["crisis"]["escalation_rounds"], [3, 5, 7, 9])
        self.assertEqual(scenario["crisis"]["grace_rounds"], 2)
        self.assertNotIn("war_stage_multiplier", scenario["crisis"])


class ZeroSumRunTest(unittest.TestCase):
    def test_mock_run_conserves_every_round(self):
        scenario = load_scenario("seven_nations_crisis_v4_1.json")
        engine = Engine(scenario, MockProvider(), log_prompts=False)
        run = engine.run()
        for item in run["rounds"]:
            self.assertEqual(item["settlement"]["score_sum"], 0.0,
                             f"第 {item['round']} 轮零和被破坏")
        self.assertEqual(run["score_sum"], 0.0)
        self.assertEqual(sum(country["score"] for country in
                             run["final_state"]["countries"].values()), 0.0)

    def test_report_has_no_conservation_warning(self):
        from strategic_game.report import _diagnostics
        scenario = load_scenario("seven_nations_crisis_v4_1.json")
        engine = Engine(scenario, MockProvider(), log_prompts=False)
        run = engine.run()
        warnings = _diagnostics(run)["warnings"]
        self.assertFalse(any("零和守恒失败" in item for item in warnings), warnings)


class VictoryBoundaryTest(unittest.TestCase):
    def test_reputation_and_credibility_do_not_contribute_to_net_score(self):
        engine = Engine(make_scenario(), MockProvider(), log_prompts=False)
        country = engine.state.countries["A"]
        country.rep = 5
        country.cred = 5
        self.assertEqual(country.score, 0)

    def test_victory_requires_strictly_more_than_five_net_points(self):
        engine = Engine(make_scenario(max_rounds=10), MockProvider(), log_prompts=False)
        a, b, c = (engine.state.countries[key] for key in ("A", "B", "C"))
        # All changes are an equal transfer, so the net score sum stays zero.
        a.c += 2
        a.e += .5
        b.c -= 2.5
        c.alive = False
        self.assertEqual(score_sum(engine), 0)
        self.assertIsNone(engine._check_end())
        a.e += .5
        b.e -= .5
        self.assertEqual(engine._check_end()["winner"], "A")

    def test_zero_capability_is_alive_but_negative_capability_dies(self):
        engine = Engine(make_scenario(), MockProvider(), log_prompts=False)
        engine.state.countries["A"].c = 0
        self.assertEqual(engine.state.kill_if_needed(), [])
        engine.state.countries["A"].c = -.5
        self.assertEqual(engine.state.kill_if_needed(), ["A"])


if __name__ == "__main__":
    unittest.main()
