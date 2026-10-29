"""Scenario migration must preserve meaning without importing old rules."""

import copy
import json
import unittest

from strategic_game.scenario import (DEFAULT_SCENARIO, SCENARIO_DIR, ScenarioError,
                                     load_scenario, normalize_scenario, scenario_hash,
                                     validate_scenario)
from strategic_game.state import State
from strategic_game.rules import can_use_nuclear


class ScenarioMigrationTest(unittest.TestCase):
    def legacy(self):
        return json.loads((SCENARIO_DIR / "default_three_states.json").read_text(encoding="utf-8"))

    def test_seven_country_default_preserves_native_data_and_sources(self):
        original = json.loads((SCENARIO_DIR / DEFAULT_SCENARIO).read_text(encoding="utf-8"))
        migrated = normalize_scenario(original)
        self.assertEqual(migrated, original)
        self.assertIsNot(migrated, original)
        self.assertEqual(len(migrated["countries"]), 7)
        validate_scenario(migrated)
        self.assertIn("crisis", migrated)
        self.assertIn("objectives", migrated)
        self.assertEqual(migrated["world_context"], original["world_context"])

    def test_legacy_country_ids_are_stable_and_names_preserved(self):
        original = self.legacy()
        snapshot = copy.deepcopy(original)
        migrated = normalize_scenario(original)
        self.assertEqual(original, snapshot)
        self.assertEqual([item["country_id"] for item in migrated["countries"]], ["C1", "C2", "C3"])
        self.assertEqual([item["name"] for item in migrated["countries"]], ["Alpha", "Beta", "Gamma"])
        self.assertEqual(migrated["migration"]["country_name_to_id"], {"Alpha": "C1", "Beta": "C2", "Gamma": "C3"})
        validate_scenario(migrated)
        self.assertEqual(normalize_scenario(migrated), migrated)

    def test_profile_and_nuclear_metadata_preserved_without_new_crisis(self):
        original = self.legacy()
        migrated = normalize_scenario(original)
        alpha = migrated["countries"][0]
        self.assertEqual(alpha["nuclear_status"], "二次核打击")
        self.assertEqual(alpha["role_card"]["archetype"], "现实主义")
        self.assertEqual(alpha["role_card"]["ideology"]["orientation"], "包容")
        self.assertEqual(alpha["role_card"]["stakeholders"], ["C2", "C3"])
        self.assertEqual(alpha["legacy_metadata"]["original_profile"], original["countries"][0]["profile"])
        self.assertEqual(alpha["legacy_metadata"]["original_nuclear_reach"], ["Beta", "Gamma"])
        self.assertFalse(alpha["legacy_metadata"]["reach_constraint_applied"])
        self.assertNotIn("crisis", migrated)
        self.assertNotIn("objectives", migrated)

    def test_legacy_material_initial_score_zero_and_no_old_m_threshold(self):
        original = self.legacy()
        original["countries"][0]["m"] = 1
        migrated = normalize_scenario(original)
        world = State.from_scenario(migrated)
        self.assertTrue(all(country.score == 0 for country in world.countries.values()))
        world.round = 2
        self.assertTrue(can_use_nuclear(world, "C1"))

    def test_duplicate_legacy_names_rejected(self):
        original = self.legacy()
        original["countries"][1]["name"] = "Alpha"
        with self.assertRaises(ScenarioError):
            normalize_scenario(original)

    def test_mixed_id_formats_rejected(self):
        original = self.legacy()
        original["countries"][0]["country_id"] = "C1"
        with self.assertRaises(ScenarioError):
            normalize_scenario(original)

    def test_unknown_legacy_nuclear_status_rejected(self):
        original = self.legacy()
        original["countries"][0]["nuclear_status"] = "invented"
        with self.assertRaises(ScenarioError):
            normalize_scenario(original)

    def test_unknown_legacy_reference_rejected(self):
        original = self.legacy()
        original["countries"][0]["nuclear_reach"] = ["Unknown"]
        with self.assertRaises(ScenarioError):
            normalize_scenario(original)

    def test_legacy_half_steps_still_validated(self):
        original = self.legacy()
        original["countries"][0]["c"] = 2.3
        with self.assertRaises(ScenarioError):
            validate_scenario(normalize_scenario(original))

    def test_explicit_old_rules_version_is_not_silently_relabelled(self):
        original = self.legacy()
        original["rules_version"] = "v3.0"
        with self.assertRaises(ScenarioError):
            normalize_scenario(original)

    def test_bundled_load_and_hash_ignore_location(self):
        scenario = load_scenario(DEFAULT_SCENARIO)
        relocated = {**scenario, "_path": "/another/machine/scenario.json"}
        self.assertEqual(scenario_hash(scenario), scenario_hash(relocated))
        self.assertEqual(load_scenario("default_three_states.json")["countries"][0]["country_id"], "C1")


if __name__ == "__main__":
    unittest.main()
