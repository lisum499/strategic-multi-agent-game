"""Tests for the local deterministic strategic-game engine."""

import random
import unittest
from pathlib import Path

from pydantic import ValidationError

from strategic_game.calculators import CombatCalculator
from strategic_game.calculators import CredibilityCalculator
from strategic_game.agents import PeacefulMockCountryAgent
from strategic_game.errors import InvalidActionError
from strategic_game.errors import InvalidPhaseError
from strategic_game.errors import UndefinedRuleError
from strategic_game.models import Action
from strategic_game.models import ActionType
from strategic_game.models import CountryState
from strategic_game.models import DecisionPackage
from strategic_game.models import Confidence
from strategic_game.models import Forecast
from strategic_game.models import NuclearStatus
from strategic_game.models import RoundPhase
from strategic_game.models import WorldState
from strategic_game.models import WarState
from strategic_game.game import StrategicGame
from strategic_game.scenario import load_scenario
from strategic_game.state_machine import SealedRound


def _action(
    action: ActionType,
    target: str,
    *,
    reason: str | None = None,
) -> Action:
  return Action(
      action=action,
      target_state=target,
      public_reason=reason,
  )


def _decision(
    round_number: int,
    declaration: Action,
    execution: Action | None = None,
    *,
    forecast_state: str | None = None,
) -> DecisionPackage:
  opponent = forecast_state or declaration.target_state
  forecasts = []
  if opponent:
    forecasts.append(
        Forecast(
            state=opponent,
            predicted_action=ActionType.CALL_FOR_PEACE,
            predicted_target=opponent,
            confidence=Confidence.LOW,
            miscalculation_risk="test uncertainty",
        )
    )
  return DecisionPackage(
      round=round_number,
      forecast=forecasts,
      declaration=declaration,
      execution=execution or declaration,
      private_rationale="test rationale",
  )


def _world(round_number: int = 2) -> WorldState:
  return WorldState(
      round=round_number,
      countries={
          "Alpha": CountryState(
              name="Alpha",
              c=3,
              e=3,
              m=4,
              nuclear_status=NuclearStatus.FULL,
              nuclear_reach={"Beta"},
          ),
          "Beta": CountryState(name="Beta", c=3, e=3, m=3),
      },
  )


class StrategicGameTest(unittest.TestCase):

  def test_decision_requires_same_target(self) -> None:
    with self.assertRaisesRegex(ValidationError, "identical target"):
      _decision(
          2,
          _action(ActionType.CALL_FOR_PEACE, "Beta", reason="peace"),
          _action(ActionType.CONVENTIONAL_WAR, "Gamma"),
      )

  def test_sealed_round_cannot_reveal_early_or_leak_other_package(
      self,
  ) -> None:
    sealed = SealedRound(_world(), seed=7)
    sealed.submit(
        "Alpha",
        _decision(
            2, _action(ActionType.CALL_FOR_PEACE, "Beta", reason="peace")
        ),
    )

    with self.assertRaisesRegex(InvalidPhaseError, "cannot reveal"):
      sealed.reveal()
    with self.assertRaisesRegex(InvalidActionError, "cannot read"):
      sealed.private_submission(requester="Beta", country_name="Alpha")
    self.assertEqual(sealed.submitted_count, 1)
    self.assertEqual(sealed.phase, RoundPhase.COMMITMENT)

  def test_round_reveals_and_applies_metrics_simultaneously(self) -> None:
    world = _world()
    sealed = SealedRound(world, seed=7)
    sealed.submit(
        "Alpha",
        _decision(
            2, _action(ActionType.CALL_FOR_PEACE, "Beta", reason="peace")
        ),
    )
    sealed.submit(
        "Beta",
        _decision(
            2, _action(ActionType.CALL_FOR_PEACE, "Alpha", reason="peace")
        ),
    )

    revealed = sealed.reveal()
    self.assertEqual(set(revealed), {"Alpha", "Beta"})
    report = sealed.resolve()

    self.assertEqual(report.deltas["Alpha"].credibility, 1)
    self.assertEqual(report.deltas["Beta"].credibility, 1)
    self.assertEqual(world.countries["Alpha"].reputation, 1)
    self.assertEqual(world.countries["Beta"].reputation, 1)
    self.assertEqual(world.round, 3)
    self.assertEqual(sealed.phase, RoundPhase.RESOLVED)

  def test_first_round_rejects_nuclear_declaration(self) -> None:
    sealed = SealedRound(_world(round_number=1), seed=7)
    nuclear = _action(ActionType.NUCLEAR_STRIKE, "Beta", reason="threat")
    with self.assertRaisesRegex(InvalidActionError, "banned in round 1"):
      sealed.submit("Alpha", _decision(1, nuclear))

  def test_credibility_undefined_pair_fails_closed(self) -> None:
    with self.assertRaises(UndefinedRuleError):
      CredibilityCalculator.change(
          ActionType.NUCLEAR_STRIKE, ActionType.CONVENTIONAL_WAR
      )

  def test_combat_calculator_is_seeded_and_uses_defender_will(self) -> None:
    calculator = CombatCalculator(ties="defender")
    result = calculator.resolve(
        attacker_name="Alpha",
        defender_name="Beta",
        attacker_cem=(3, 3, 3),
        defender_cem=(3, 3, 3),
        rng=random.Random(11),
    )

    self.assertEqual(result.attacker_power, 9)
    self.assertEqual(result.defender_power, 11.25)
    self.assertEqual(result.winner, "Beta")
    total_delta = (
        result.attacker_delta.c
        + result.attacker_delta.e
        + result.attacker_delta.m
    )
    self.assertEqual(total_delta, -2)

  def test_missing_strategy_front_count_fails_closed(self) -> None:
    with self.assertRaisesRegex(UndefinedRuleError, "4 fronts"):
      CombatCalculator().strategy_modifier(4)

  def test_failed_resolution_rolls_back_world_state(self) -> None:
    world = _world(round_number=3)
    world.wars["war-1"] = WarState(
        id="war-1",
        attacker="Alpha",
        defender="Beta",
        started_round=2,
    )
    before = world.model_dump()
    sealed = SealedRound(world, seed=7)
    alpha_peace = Action(
        action=ActionType.CALL_FOR_PEACE,
        target_war_id="war-1",
        public_reason="negotiate",
        peace_terms="unstructured terms",
    )
    beta_peace = Action(
        action=ActionType.CALL_FOR_PEACE,
        target_war_id="war-1",
        public_reason="negotiate",
        peace_terms="unstructured terms",
    )
    sealed.submit(
        "Alpha", _decision(3, alpha_peace, forecast_state="Beta")
    )
    sealed.submit(
        "Beta", _decision(3, beta_peace, forecast_state="Alpha")
    )
    sealed.reveal()

    with self.assertRaisesRegex(UndefinedRuleError, "structured negotiated"):
      sealed.resolve()
    self.assertEqual(world.model_dump(), before)

  def test_forecast_must_cover_each_living_opponent(self) -> None:
    package = DecisionPackage(
        round=2,
        forecast=[],
        declaration=_action(
            ActionType.CALL_FOR_PEACE, "Beta", reason="peace"
        ),
        execution=_action(ActionType.CALL_FOR_PEACE, "Beta"),
        private_rationale="test rationale",
    )
    with self.assertRaisesRegex(InvalidActionError, "every living opponent"):
      SealedRound(_world(), seed=7).submit("Alpha", package)

  def test_general_peace_during_war_does_not_farm_reputation(self) -> None:
    world = _world(round_number=3)
    world.wars["war-1"] = WarState(
        id="war-1",
        attacker="Alpha",
        defender="Beta",
        started_round=2,
    )
    sealed = SealedRound(world, seed=7)
    sealed.submit(
        "Alpha",
        _decision(
            3,
            _action(ActionType.CALL_FOR_PEACE, "Beta", reason="peace"),
        ),
    )
    sealed.submit(
        "Beta",
        _decision(
            3,
            _action(ActionType.CALL_FOR_PEACE, "Alpha", reason="peace"),
        ),
    )
    sealed.reveal()
    report = sealed.resolve()

    self.assertEqual(report.deltas["Alpha"].reputation, 0)
    self.assertEqual(report.deltas["Beta"].reputation, 0)

  def test_peace_reputation_has_three_round_cooldown(self) -> None:
    world = _world(round_number=3)
    world.countries["Alpha"].last_rewarded_peace_round = 2
    sealed = SealedRound(world, seed=7)
    sealed.submit(
        "Alpha",
        _decision(
            3,
            _action(ActionType.CALL_FOR_PEACE, "Beta", reason="peace"),
        ),
    )
    sealed.submit(
        "Beta",
        _decision(
            3,
            _action(ActionType.CALL_FOR_PEACE, "Alpha", reason="peace"),
        ),
    )
    sealed.reveal()
    report = sealed.resolve()

    self.assertEqual(report.deltas["Alpha"].reputation, 0)
    self.assertEqual(report.deltas["Beta"].reputation, 1)

  def test_complete_mock_game_pipeline(self) -> None:
    scenario = load_scenario(Path(__file__).parents[1] / "scenarios" / "default_three_states.json")
    agents = {
        country.name: PeacefulMockCountryAgent()
        for country in scenario.countries
    }
    result = StrategicGame(
        scenario=scenario,
        agents=agents,
        seed=42,
    ).play(max_rounds=2)

    self.assertEqual(len(result.reports), 2)
    self.assertEqual(len(result.private_decisions), 2)
    self.assertEqual(result.final_world.round, 3)
    self.assertEqual(
        set(result.reports[0].executions), {"Alpha", "Beta", "Gamma"}
    )

  def test_mock_game_stops_on_stalemate(self) -> None:
    scenario = load_scenario(Path(__file__).parents[1] / "scenarios" / "default_three_states.json")
    agents = {
        country.name: PeacefulMockCountryAgent()
        for country in scenario.countries
    }
    result = StrategicGame(
        scenario=scenario,
        agents=agents,
        seed=42,
        stalemate_rounds=4,
    ).play(max_rounds=20)

    self.assertEqual(len(result.reports), 4)
    self.assertEqual(result.termination_reason, "stalemate")


if __name__ == "__main__":
  unittest.main()
