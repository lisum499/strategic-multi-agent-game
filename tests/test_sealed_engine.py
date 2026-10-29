"""Integration checks for the sealed protocol and unchanged v4.1 settlement."""

from __future__ import annotations

import copy
import json
import threading
import time
import unittest

from strategic_game.engine import Engine
from strategic_game.providers import MockProvider, ProviderError
from strategic_game.protocol import make_fallback


def scenario(*, rounds: int = 2, war: bool = False) -> dict:
    data = {
        "scenario_id": "sealed-integration",
        "rules_version": "v4.1",
        "max_rounds": rounds,
        "countries": [
            {"country_id": "A", "name": "甲", "c": 3., "e": 3., "m": 3.,
             "nuclear_status": "有", "role_card": {"private_marker": "secret-A"}},
            {"country_id": "B", "name": "乙", "c": 3., "e": 3., "m": 3.,
             "role_card": {"private_marker": "secret-B"}},
            {"country_id": "C", "name": "丙", "c": 3., "e": 3., "m": 3.,
             "role_card": {"private_marker": "secret-C"}},
        ],
    }
    if war:
        data["initial_wars"] = [{
            "war_id": "w_1", "attacker_id": "A", "defender_id": "B",
            "started_round": 1, "progress_round": 1,
        }]
    return data


def choose(package: dict, situation: dict, action_type: str, target: str,
           *, declaration_type: str | None = None) -> None:
    """Select a legal paired choice without duplicating engine legality rules."""
    declaration_type = declaration_type or action_type
    for declaration in situation["legal_declarations"]:
        if (declaration["action_type"] != declaration_type
                or declaration["target_country_id"] != target):
            continue
        for execution in declaration["execution_options"]:
            if execution["action_type"] == action_type:
                package["declaration"] = {
                    "option_id": declaration["option_id"],
                    "public_statement": "测试宣称",
                }
                package["execution"] = {
                    "option_id": execution["option_id"],
                    "public_statement": "测试行动",
                }
                return
    raise AssertionError(f"no legal {declaration_type}/{action_type} to {target}")


class PackageProvider(MockProvider):
    """Offline provider which records exactly what every country was shown."""

    name = "integration-script"
    model = "integration-script"

    def __init__(self, edit=None, *, delays=None, fail_country=None):
        self.edit = edit
        self.delays = delays or {}
        self.fail_country = fail_country
        self.inputs = []
        self.lock = threading.Lock()

    def decide(self, phase, system, user, situation):
        country_id = situation["self"]["country_id"]
        with self.lock:
            self.inputs.append((phase, copy.deepcopy(situation), system, user))
        time.sleep(self.delays.get(country_id, 0))
        if country_id == self.fail_country:
            raise ProviderError("offline failure for regression")
        if phase != "sealed":
            raise AssertionError(f"unexpected post-reveal decision phase: {phase}")
        package = make_fallback(situation)
        if self.edit:
            self.edit(package, situation)
        return json.dumps(package, ensure_ascii=False)


class SealedEngineTest(unittest.TestCase):
    def test_each_country_submits_one_package_per_round(self):
        provider = PackageProvider()
        run = Engine(scenario(), provider, seed=9, max_workers=3,
                     log_prompts=False).run()
        self.assertEqual(len(provider.inputs), 6)
        self.assertEqual({phase for phase, *_ in provider.inputs}, {"sealed"})
        self.assertEqual(len(run["rounds"]), 2)
        self.assertEqual(run["decision_protocol"], "sealed_v1")
        self.assertEqual(run["failure_policy"], "peace_fallback_v1")

    def test_country_inputs_do_not_contain_same_round_decisions_or_other_roles(self):
        def edit(package, situation):
            cid = situation["self"]["country_id"]
            package["declaration"]["public_statement"] = f"submitted-{cid}"

        provider = PackageProvider(edit)
        Engine(scenario(rounds=1), provider, max_workers=3, log_prompts=False).run()
        for phase, situation, system, user in provider.inputs:
            cid = situation["self"]["country_id"]
            text = json.dumps(situation, ensure_ascii=False) + system + user
            self.assertNotIn("declared_this_round", situation)
            self.assertNotIn("your_declaration", situation)
            self.assertIn(f"secret-{cid}", text)
            for other in {"A", "B", "C"} - {cid}:
                self.assertNotIn(f"secret-{other}", text)
            self.assertNotIn("submitted-", text)
        self.assertEqual(len({s["snapshot_hash"] for _, s, *_ in provider.inputs}), 1)

    def test_completion_order_does_not_change_rule_results(self):
        def edit(package, situation):
            cid = situation["self"]["country_id"]
            if situation["round"] == 1 and cid == "A":
                choose(package, situation, "war", "B")
            elif cid in {"A", "B"}:
                choose(package, situation, "peace", "C")
            else:
                choose(package, situation, "peace", "A")

        first = Engine(scenario(rounds=3), PackageProvider(edit, delays={"A": .015}),
                       seed=7, max_workers=3, log_prompts=False).run()
        second = Engine(scenario(rounds=3), PackageProvider(edit, delays={"C": .015}),
                        seed=7, max_workers=3, log_prompts=False).run()
        for field in ("final_state", "rounds", "rng_draws", "decisions", "result"):
            self.assertEqual(first[field], second[field], field)

    def test_saved_decisions_and_rng_replay_without_provider_calls(self):
        provider = PackageProvider()
        run = Engine(scenario(), provider, seed=11, log_prompts=False).run()
        _, consistent, problems = Engine.replay(run)
        self.assertTrue(consistent, problems)
        self.assertEqual(len(provider.inputs), 6)
        tampered = copy.deepcopy(run)
        tampered["decision_protocol"] = "sequential_legacy"
        with self.assertRaisesRegex(ValueError, "旧协议"):
            Engine.replay(tampered)

    def test_failed_country_uses_marked_peace_and_is_excluded_from_forecasts(self):
        provider = PackageProvider(fail_country="B")
        run = Engine(scenario(rounds=1), provider, max_retries=1,
                     max_workers=3, log_prompts=False).run()
        self.assertEqual(run["fallback_count"], 1)
        records = [record for record in run["records"]
                   if record.get("country_id") == "B"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["origin"], "fallback")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertTrue(record["adopted"])
        self.assertEqual(run["rounds"][0]["actions"]["B"]["action_type"], "peace")
        metrics = run["forecast_metrics"]
        self.assertEqual(metrics["eligible"], 2)
        self.assertEqual(metrics["excluded_fallback"], 4)
        _, consistent, problems = Engine.replay(run)
        self.assertTrue(consistent, problems)

    def test_malformed_package_retries_before_default_peace(self):
        class Malformed(PackageProvider):
            def decide(self, phase, system, user, situation):
                if situation["self"]["country_id"] == "A":
                    self.inputs.append((phase, copy.deepcopy(situation), system, user))
                    return '{"round": 1}'
                return super().decide(phase, system, user, situation)

        run = Engine(scenario(rounds=1), Malformed(), max_retries=1,
                     log_prompts=False).run()
        self.assertEqual(run["fallback_count"], 1)
        self.assertEqual(run["rounds"][0]["actions"]["A"]["action_type"], "peace")

    def test_settlement_failure_rolls_back_state_rng_and_completed_round_log(self):
        engine = Engine(scenario(rounds=1), PackageProvider(), seed=13,
                        log_prompts=False)
        before = copy.deepcopy(engine.state.to_dict())
        option_rng = engine.option_rng.getstate()
        settle_rng = engine.settle_rng.getstate()

        def broken_settlement():
            engine.state.countries["A"].c = .5
            engine.state.add_war("A", "B")
            engine.option_rng.random()
            engine.settle_rng.random()
            engine._event("must roll back", "test")
            engine.rng_draws.append({"kind": "must roll back"})
            raise RuntimeError("offline injected settlement failure")

        engine._settle = broken_settlement
        with self.assertRaisesRegex(RuntimeError, "injected settlement"):
            engine.run()
        self.assertEqual(engine.state.to_dict(), before)
        self.assertEqual(engine.option_rng.getstate(), option_rng)
        self.assertEqual(engine.settle_rng.getstate(), settle_rng)
        self.assertEqual(engine.round_logs, [])
        self.assertEqual(engine.rng_draws, [])
        self.assertEqual(engine.option_orders, {})
        self.assertTrue(engine.records, "failed round must retain request diagnostics")

    def test_private_observation_can_only_cite_pre_round_public_sources(self):
        data = scenario(rounds=1)
        data["threat_observation_enabled"] = True
        provider = PackageProvider()
        run = Engine(data, provider, log_prompts=False).run()
        self.assertEqual(run["fallback_count"], 0)
        for _, situation, *_ in provider.inputs:
            sources = situation["threat_observation_sources"]
            self.assertFalse(any(source.startswith("d1_") for source in sources))
            self.assertFalse(any(source.startswith("a1_") for source in sources))

    def test_private_reason_and_negotiation_policy_are_not_sent_to_opponents_next_round(self):
        def edit(package, situation):
            cid = situation["self"]["country_id"]
            package["private_rationale"] = f"private-decision-{cid}"
            choose(package, situation, "peace", "C" if cid in {"A", "B"} else "A")

        provider = PackageProvider(edit)
        Engine(scenario(rounds=2, war=True), provider, log_prompts=False).run()
        second_round = [item for item in provider.inputs if item[1]["round"] == 2]
        self.assertEqual(len(second_round), 3)
        for _, situation, system, user in second_round:
            own = situation["self"]["country_id"]
            text = json.dumps(situation, ensure_ascii=False) + system + user
            for other in {"A", "B", "C"} - {own}:
                self.assertNotIn(f"private-decision-{other}", text)
            self.assertNotIn("private_decisions", situation)

    def test_forecast_scores_the_committed_action_even_if_settlement_cancels_it(self):
        data = scenario(rounds=2)
        for key in ("c", "e", "m"):
            data["countries"][1][key] = .5

        def edit(package, situation):
            cid = situation["self"]["country_id"]
            choose(package, situation, "nuclear" if cid == "A" else "peace",
                   "B" if cid == "A" else "A")
            if cid == "C":
                prediction = next(item for item in package["forecast"]
                                  if item["country_id"] == "B")
                prediction.update(action_type="peace", target_country_id="A")

        engine = Engine(data, PackageProvider(edit), seed=21, log_prompts=False)
        engine.state.round = 2
        run = engine.run()
        round_log = run["rounds"][0]
        self.assertFalse(engine.state.countries["B"].alive)
        self.assertEqual(round_log["settlement"]["executed"]["B"], "canceled")
        prediction = next(item for item in round_log["forecast_evaluation"]
                          if item["observer_id"] == "C" and item["country_id"] == "B")
        self.assertTrue(prediction["eligible"])
        self.assertTrue(prediction["joint_hit"])


class PrecommittedNegotiationTest(unittest.TestCase):
    def _run(self, *, mode: str, accepts: bool):
        def edit(package, situation):
            cid = situation["self"]["country_id"]
            if cid == "A":
                choose(package, situation, "peace", "C")
                package["negotiation"]["w_1"] = (
                    {"accept_offer_ids": ["P2"] if accepts else []}
                    if mode == "offers" else
                    {"accept": accepts, "min_total_gain": 1.,
                     "min_gains": {"c": .5, "e": .5, "m": 0.}}
                )
            elif cid == "B":
                choose(package, situation, "peace", "A")
                package["negotiation"]["w_1"] = (
                    {"offer_id": "P2"}
                    if mode == "offers" else
                    {"changes": {"attacker": {"c": .5, "e": .5, "m": 0.},
                                 "defender": {"c": -.5, "e": -.5, "m": 0.}}}
                )
            else:
                choose(package, situation, "peace", "A")

        provider = PackageProvider(edit)
        engine = Engine(scenario(rounds=1, war=True), provider, seed=19,
                        negotiation_mode=mode, max_workers=3, log_prompts=False)
        return engine, engine.run(), provider

    def test_self_offer_threshold_uses_actual_receipts_after_capacity_cap(self):
        def edit(package, situation):
            cid = situation["self"]["country_id"]
            choose(package, situation, "peace", "C" if cid == "A" else "A")
            if cid == "A":
                package["negotiation"]["w_1"] = {
                    "accept": True, "min_total_gain": 1.,
                    "min_gains": {"c": .5, "e": .5, "m": 0.}}
            elif cid == "B":
                package["negotiation"]["w_1"] = {"changes": {
                    "attacker": {"c": .5, "e": .5, "m": 0.},
                    "defender": {"c": -.5, "e": -.5, "m": 0.}}}
        data = scenario(rounds=1, war=True)
        data["countries"][0].update(c=5., e=5., m=5.)
        run = Engine(data, PackageProvider(edit), negotiation_mode="self_offer").run()
        decision = run["rounds"][0]["settlement"]["negotiations"][0]
        self.assertFalse(decision["accepted"])
        self.assertEqual(sum(decision["effective_gains"].values()), 0.)
        self.assertEqual(run["fallback_count"], 0)
        _, ok, problems = Engine.replay(run)
        self.assertTrue(ok, problems)

    def test_offers_accept_and_reject_are_settled_without_additional_calls(self):
        for accepts in (True, False):
            with self.subTest(accepts=accepts):
                engine, run, provider = self._run(mode="offers", accepts=accepts)
                self.assertEqual(len(provider.inputs), 3)
                self.assertEqual(run["fallback_count"], 0)
                negotiation = run["rounds"][0]["settlement"]["negotiations"][0]
                self.assertEqual(negotiation["accepted"], accepts)
                self.assertEqual(engine.state.wars["w_1"].status,
                                 "ended" if accepts else "ongoing")
                self.assertEqual(run["score_sum"], 0.)
                if accepts:
                    self.assertEqual(engine.state.countries["A"].score, 1.)
                    self.assertEqual(engine.state.countries["B"].score, -1.)

    def test_self_offer_thresholds_are_precommitted_and_replayable(self):
        for accepts in (True, False):
            with self.subTest(accepts=accepts):
                _, run, provider = self._run(mode="self_offer", accepts=accepts)
                self.assertEqual(len(provider.inputs), 3)
                self.assertEqual(run["fallback_count"], 0)
                self.assertEqual(run["score_sum"], 0.)
                self.assertEqual(run["rounds"][0]["settlement"]["negotiations"][0]
                                 ["accepted"], accepts)
                _, consistent, problems = Engine.replay(run)
                self.assertTrue(consistent, problems)


if __name__ == "__main__":
    unittest.main()
