"""Public exports expose the resolved game while retaining researcher privacy."""

from __future__ import annotations

import copy
import json
import unittest

from strategic_game.engine import Engine
from strategic_game.providers import MockProvider
from strategic_game.report import public_export, render_html, render_markdown


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


def researcher_run():
    data = {
        "scenario_id": "public-export",
        "rules_version": "v4.1",
        "max_rounds": 1,
        "countries": [
            {"country_id": cid, "name": cid, "c": 3., "e": 3., "m": 3.,
             "role_card": {"private_marker": f"hidden-role-{cid}"}}
            for cid in ("A", "B", "C")
        ],
        "crisis": {"crisis_id": "export-crisis", "title": "公开危机",
                   "parties": ["A", "B"], "dependents": ["C"], "control": "A",
                   "stake": "公开标的", "unresolved_cost": {},
                   "escalation_rounds": [], "escalation_multiplier": 1.5},
        "objectives": [{"country_id": "A", "objectives": [{
            "id": "hidden-goal-A", "type": "goal", "description": "hidden-goal-description",
            "condition": {"crisis_resolved": True}, "reward": {"rep": 1.}}]}],
    }
    run = Engine(data, MockProvider(), seed=33, log_prompts=True).run()
    run["private_marker"] = "hidden-top-level"
    return run


class PublicReportTest(unittest.TestCase):
    def test_public_export_drops_private_records_predictions_and_objective_states(self):
        run = researcher_run()
        exported = public_export(run)
        forbidden = {
            "scenario", "records", "decisions", "forecast", "forecast_evaluation",
            "private_rationale", "negotiation", "private_threat_observations",
            "private_observation", "role_card", "objective_definitions", "objective_states",
        }
        self.assertFalse(set(walk_keys(exported)) & forbidden)
        public_text = json.dumps(exported, ensure_ascii=False)
        for secret in ("hidden-role-A", "hidden-role-B", "hidden-goal-A",
                       "hidden-goal-description", "hidden-top-level"):
            self.assertNotIn(secret, public_text)
        self.assertEqual(exported["scenario_id"], "public-export")
        self.assertEqual(exported["final_state"]["countries"],
                         run["final_state"]["countries"])
        self.assertEqual(exported["rounds"][0]["actions"], run["rounds"][0]["actions"])
        self.assertEqual(exported["rounds"][0]["origins"], run["rounds"][0]["origins"])

    def test_capability_visibility_applies_to_all_public_snapshots(self):
        run = researcher_run()
        run["scenario"]["visibility"] = {"ce_m_public": False}
        exported = public_export(run)
        snapshots = [exported["final_state"]]
        snapshots += [snapshot for item in exported["rounds"]
                      for snapshot in (item["snapshot"], item["state_after"])]
        for snapshot in snapshots:
            for country in snapshot["countries"].values():
                self.assertFalse({"c", "e", "m"} & set(country))
        self.assertIn("c", run["final_state"]["countries"]["A"])

    def test_export_is_detached_and_does_not_destroy_researcher_record(self):
        run = researcher_run()
        before = copy.deepcopy(run)
        exported = public_export(run)
        exported["final_state"]["countries"]["A"]["c"] = -999
        self.assertEqual(run, before)
        self.assertIn("scenario", run)
        self.assertIn("records", run)

    def test_fallback_is_visible_in_public_export_and_researcher_reports(self):
        class Failed(MockProvider):
            def decide(self, phase, system, user, situation):
                if situation["self"]["country_id"] == "B":
                    raise RuntimeError("offline intentional failure")
                return super().decide(phase, system, user, situation)

        data = {"scenario_id": "fallback-report", "rules_version": "v4.1", "max_rounds": 1,
                "countries": [{"country_id": cid, "c": 3, "e": 3, "m": 3}
                              for cid in ("A", "B")]}
        run = Engine(data, Failed(), max_retries=0, log_prompts=False).run()
        exported = public_export(run)
        self.assertEqual(exported["rounds"][0]["origins"]["B"], "fallback")
        self.assertEqual(exported["fallback_count"], 1)
        for rendered in (render_html(run), render_markdown(run)):
            self.assertIn("默认和平", rendered)
            self.assertIn("B", rendered)


if __name__ == "__main__":
    unittest.main()
