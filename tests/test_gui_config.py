import unittest
import copy
import json

from strategic_game.gui import bundled_scenario, format_decision, make_scenario
from strategic_game.protocol import validate_scenario


class GuiConfigTests(unittest.TestCase):
    def test_bundled_scenario_can_be_rebuilt_for_custom_round_count(self):
        source = bundled_scenario()
        rebuilt = make_scenario(source["countries"], source["world_context"], 7)
        self.assertEqual(len(rebuilt["countries"]), 8)
        self.assertEqual(rebuilt["max_rounds"], 7)
        self.assertTrue(all(country["alive"] for country in rebuilt["countries"].values()))
        self.assertNotIn("strategic_culture_knowledge", rebuilt["countries"]["C1"])
        # The GUI writes this object to JSON and the CLI validates it again.
        validate_scenario(json.loads(json.dumps(rebuilt, ensure_ascii=False)))

    def test_custom_country_count_is_not_fixed(self):
        countries = copy.deepcopy(dict(list(bundled_scenario()["countries"].items())[:3]))
        for country in countries.values():
            country.pop("tags", None)
        rebuilt = make_scenario(countries, "测试世界", 2)
        self.assertEqual(len(rebuilt["countries"]), 3)

    def test_structured_decision_is_rendered_as_readable_text(self):
        decision={"assessment":"局势紧张","forecast":"对方可能让步","declaration":"呼吁磋商",
                  "actions":[{"target_id":"C2","action_id":"加强外交施压","conventional":0,"nuclear":0}],
                  "diplomatic_proposals":[{"target_id":"C2","kind":"consultation","terms":"公开检查标准"}],
                  "diplomatic_responses":[],"alliance_invites":[],"alliance_responses":[],
                  "alliance_breaks":[],"borrows":[],"loan_responses":[],"returns":[],
                  "rationale":"控制升级","threat_assessment":"中等"}
        rendered=format_decision(decision,{"C2":"贝罗"})
        self.assertIn("对 贝罗（C2）执行“加强外交施压”",rendered)
        self.assertIn("提出 consultation 协议：公开检查标准",rendered)
        self.assertNotIn('{',rendered)


if __name__ == "__main__":
    unittest.main()
