"""Protocol tests exercise visibility barriers and semantic legality, without a model API."""

import copy
import json
import unittest

from strategic_game.protocol import (
    build_sealed_prompts, make_fallback, match_negotiation, validate_package,
)
from strategic_game.state_machine import SealedRound
from strategic_game.validation import ResponseError


def context(country="A", mode="offers", role="defender", negotiation=True):
    other = "B" if country == "A" else "A"
    zero = {"attacker": {"c": 0.0, "e": 0.0, "m": 0.0},
            "defender": {"c": 0.0, "e": 0.0, "m": 0.0}}
    first = copy.deepcopy(zero)
    first["attacker"]["c"], first["defender"]["c"] = 1.0, -1.0
    options = [{"option_id": "A1", "action_type": "peace", "target_country_id": other,
                "war_id": "w2", "support_country_id": None},
               {"option_id": "A2", "action_type": "war", "target_country_id": other,
                "war_id": None, "support_country_id": None}]
    return {
        "phase": "sealed", "round": 2, "self": {"country_id": country},
        "snapshot_hash": "snapshot-2", "negotiation_mode": mode,
        "state": {"countries": [{"country_id": "A", "alive": True},
                                 {"country_id": "B", "alive": True},
                                 {"country_id": "dead", "alive": False}]},
        "legal_declarations": [
            {"option_id": "D1", "action_type": "peace", "target_country_id": other,
             "war_id": "w1", "support_country_id": None, "execution_options": options}],
        "negotiation_contexts": {
            "w1": {"role": role, "offers": [
                {"offer_id": "P1", "label": "one", "changes": first},
                {"offer_id": "P4", "label": "zero", "changes": zero}],
                "offer_bounds": {"step": 0.5}}
        } if negotiation else {},
    }


class ProtocolTests(unittest.TestCase):
    def test_fallback_is_legal_and_can_change_war_binding(self):
        situation = context()
        package = make_fallback(situation)
        self.assertEqual(package["declaration"]["option_id"], "D1")
        self.assertEqual(package["execution"]["option_id"], "A1")
        self.assertEqual(package["negotiation"], {"w1": {"offer_id": "P4"}})
        self.assertEqual(validate_package(package, situation)[0], package)
        self.assertNotIn("origin", package)

    def test_exact_snapshot_and_country_binding(self):
        situation = context()
        for field, value in (("round", 3), ("country_id", "B"), ("snapshot_hash", "old")):
            package = make_fallback(situation)
            package[field] = value
            with self.subTest(field=field), self.assertRaises(ResponseError):
                validate_package(package, situation)

    def test_missing_duplicate_or_dead_forecasts_are_rejected(self):
        situation = context()
        package = make_fallback(situation)
        for forecasts in ([], package["forecast"] * 2,
                          [{**package["forecast"][0], "country_id": "dead"}],
                          [{**package["forecast"][0], "target_country_id": "dead"}],
                          [{**package["forecast"][0], "action_type": "war", "target_country_id": "B"}]):
            with self.subTest(forecasts=forecasts), self.assertRaises(ResponseError):
                validate_package({**package, "forecast": forecasts}, situation)

    def test_execution_id_is_scoped_to_the_declaration(self):
        situation = context()
        package = make_fallback(situation)
        package["execution"]["option_id"] = "A9"
        with self.assertRaises(ResponseError):
            validate_package(package, situation)
        package["execution"]["option_id"] = "A1"
        situation["legal_declarations"][0]["execution_options"][0]["target_country_id"] = "A"
        with self.assertRaises(ResponseError):
            validate_package(package, situation)

    def test_origin_cannot_be_claimed_by_model(self):
        situation = context()
        with self.assertRaises(ResponseError):
            validate_package({**make_fallback(situation), "origin": "model"}, situation)

    def test_private_fields_never_revealed_and_inputs_cannot_mutate_commitment(self):
        first = make_fallback(context("A"))
        second = make_fallback(context("B"))
        sealed = SealedRound(["A", "B"], round_no=2, snapshot_hash="snapshot-2")
        sealed.submit("A", first)
        first["private_rationale"] = "mutated"
        with self.assertRaises(ValueError):
            sealed.reveal()
        with self.assertRaises(PermissionError):
            sealed.private_submission(requester="B", country_id="A")
        sealed.submit("B", second)
        public = sealed.reveal()
        self.assertNotIn("mutated", sealed.private_submission(requester="A", country_id="A")["private_rationale"])
        text = json.dumps(public)
        for key in ("forecast", "negotiation", "private_rationale", "threat_observation"):
            self.assertNotIn(key, text)
        public["A"]["execution"]["option_id"] = "bad"
        self.assertEqual(sealed.private_submission(requester="A", country_id="A")["execution"]["option_id"], "A1")
        with self.assertRaises(ValueError):
            sealed.submit("A", first)

    def test_offer_matching_and_default_rejection(self):
        situation = context()
        ctx = situation["negotiation_contexts"]["w1"]
        offer, accepted = match_negotiation("offers", {"offer_id": "P1"},
                                            {"accept_offer_ids": ["P1"]}, ctx)
        self.assertTrue(accepted)
        self.assertEqual(offer["changes"]["attacker"]["c"], 1.0)
        attacker = make_fallback(context(role="attacker"))["negotiation"]["w1"]
        self.assertFalse(match_negotiation("offers", {"offer_id": "P1"}, attacker, ctx)[1])

    def test_self_offer_matches_both_total_and_each_dimension(self):
        ctx = context(mode="self_offer")["negotiation_contexts"]["w1"]
        defender = {"changes": {"attacker": {"c": 1.0, "e": 0.5, "m": 0.0},
                                "defender": {"c": -1.0, "e": -0.5, "m": 0.0}}}
        attacker = {"accept": True, "min_total_gain": 1.5,
                    "min_gains": {"c": 0.5, "e": 0.5, "m": 0.0}}
        self.assertTrue(match_negotiation("self_offer", defender, attacker, ctx)[1])
        self.assertFalse(match_negotiation("self_offer", defender, {**attacker, "accept": False}, ctx)[1])
        self.assertFalse(match_negotiation("self_offer", defender,
                         {**attacker, "min_gains": {"c": 0.5, "e": 1.0, "m": 0.0}}, ctx)[1])

    def test_invalid_or_nonconserving_negotiation_policies_rejected(self):
        situation = context(mode="self_offer", role="attacker")
        package = make_fallback(situation)
        for gains in ({"c": 2.0, "e": 2.0, "m": 0.0},
                      {"c": 0.1, "e": 0.0, "m": 0.0},
                      {"c": float("nan"), "e": 0.0, "m": 0.0}):
            package["negotiation"]["w1"]["min_gains"] = gains
            with self.subTest(gains=gains), self.assertRaises(ResponseError):
                validate_package(package, situation)
        situation = context(mode="self_offer")
        package = make_fallback(situation)
        package["negotiation"]["w1"]["changes"]["attacker"]["c"] = 1.0
        with self.assertRaises(ResponseError):
            validate_package(package, situation)

    def test_no_unannounced_negotiation_war_ids(self):
        situation = context()
        package = make_fallback(situation)
        for policies in ({}, {"w2": {"offer_id": "P4"}}):
            with self.subTest(policies=policies), self.assertRaises(ResponseError):
                validate_package({**package, "negotiation": policies}, situation)

    def test_threat_sources_are_frozen_and_fallback_is_unknown(self):
        situation = context()
        situation["threat_observation_sources"] = ["state"]
        package = make_fallback(situation)
        self.assertEqual(package["threat_observation"]["level"], "未知")
        package["threat_observation"] = {"target_country_id": "B", "level": "中",
                                         "basis": "尚未揭示", "source_ids": ["d2_B"]}
        with self.assertRaises(ResponseError):
            validate_package(package, situation)
        package["threat_observation"] = None
        with self.assertRaises(ResponseError):
            validate_package(package, situation)

    def test_prompts_explain_private_policies_and_one_submission(self):
        situation = context(mode="self_offer", role="attacker")
        system, user = build_sealed_prompts(situation)
        self.assertIn("一次密封", system)
        self.assertIn("accept=false", system)
        self.assertIn('"min_total_gain"', user)
        self.assertIn("snapshot-2", user)


if __name__ == "__main__":
    unittest.main()
