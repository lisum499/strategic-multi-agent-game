"""Pure deterministic calculators for the strategic game."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Mapping

from strategic_game.errors import UndefinedRuleError
from strategic_game.models import ActionType
from strategic_game.models import CountryDelta


def clamp(value: float, lower: float, upper: float) -> float:
  return max(lower, min(upper, value))


class CredibilityCalculator:
  """Calculate credibility from a sealed declaration/execution pair."""

  _PENALTIES: Mapping[tuple[ActionType, ActionType], int] = {
      (ActionType.CONVENTIONAL_WAR, ActionType.CALL_FOR_PEACE): -1,
      (ActionType.NUCLEAR_STRIKE, ActionType.CALL_FOR_PEACE): -1,
      (ActionType.INTERVENE, ActionType.CALL_FOR_PEACE): -1,
      (ActionType.INTERVENE, ActionType.CONVENTIONAL_WAR): -1,
      (ActionType.CALL_FOR_PEACE, ActionType.CONVENTIONAL_WAR): -2,
      (ActionType.CALL_FOR_PEACE, ActionType.INTERVENE): -2,
      (ActionType.CONVENTIONAL_WAR, ActionType.NUCLEAR_STRIKE): -2,
      (ActionType.INTERVENE, ActionType.NUCLEAR_STRIKE): -2,
      (ActionType.CALL_FOR_PEACE, ActionType.NUCLEAR_STRIKE): -3,
  }

  @classmethod
  def change(
      cls, declaration: ActionType, execution: ActionType
  ) -> int:
    if declaration == execution:
      return 1
    try:
      return cls._PENALTIES[(declaration, execution)]
    except KeyError as exc:
      raise UndefinedRuleError(
          f"credibility change is undefined for {declaration} -> {execution}"
      ) from exc


class ReputationCalculator:
  """Calculate reputation change from the action actually executed."""

  @staticmethod
  def change(
      execution: ActionType,
      *,
      intervention_supports_stronger_side: bool | None = None,
      defensive_nuclear_use: bool = False,
      peace_is_legal: bool = True,
  ) -> int:
    if execution == ActionType.CONVENTIONAL_WAR:
      return -2
    if execution == ActionType.NUCLEAR_STRIKE:
      return 0 if defensive_nuclear_use else -3
    if execution == ActionType.CALL_FOR_PEACE:
      return 1 if peace_is_legal else 0
    if intervention_supports_stronger_side is None:
      raise UndefinedRuleError(
          "intervention reputation requires a pre-resolution strength comparison"
      )
    return -1 if intervention_supports_stronger_side else 2


class InterventionCalculator:
  """Calculate one-turn intervention effects after success is established."""

  @staticmethod
  def changes(
      *, success: bool, rng: random.Random, combat: CombatCalculator
  ) -> tuple[CountryDelta, CountryDelta]:
    intervener = combat.random_capability_delta(
        count=1, amount=0.5 if success else -1, rng=rng
    )
    opposed_side = combat.random_capability_delta(
        count=1, amount=-0.5, rng=rng
    )
    return intervener, opposed_side


@dataclass(frozen=True)
class CombatResult:
  attacker_power: float
  defender_power: float
  winner: str
  attacker_delta: CountryDelta
  defender_delta: CountryDelta


class CombatCalculator:
  """Resolve a conventional battle from explicit modifiers and a seeded RNG."""

  _MULTIPLIERS = {
      -3: 0.25,
      -2: 0.50,
      -1: 0.75,
      0: 1.00,
      1: 1.25,
      2: 1.50,
      3: 1.75,
  }

  def __init__(
      self,
      *,
      strategy_penalties: Mapping[int, int] | None = None,
      ties: str = "undefined",
      sample_without_replacement: bool = True,
  ) -> None:
    self._strategy_penalties = dict(
        strategy_penalties or {1: 0, 2: -1, 3: -3, 7: -4}
    )
    self._ties = ties
    self._sample_without_replacement = sample_without_replacement

  def strategy_modifier(self, fronts: int) -> int:
    try:
      return self._strategy_penalties[fronts]
    except KeyError as exc:
      raise UndefinedRuleError(
          f"strategy modifier is undefined for {fronts} fronts"
      ) from exc

  def multiplier(self, strategy: int, will: int) -> float:
    modifier = int(clamp(strategy + will, -3, 3))
    return self._MULTIPLIERS[modifier]

  def fighting_power(
      self, c: float, e: float, m: float, *, strategy: int, will: int
  ) -> float:
    return (c + e + m) * self.multiplier(strategy, will)

  def resolve(
      self,
      *,
      attacker_name: str,
      defender_name: str,
      attacker_cem: tuple[float, float, float],
      defender_cem: tuple[float, float, float],
      attacker_strategy: int = 0,
      attacker_will: int = 0,
      defender_strategy: int = 0,
      defender_will: int = 1,
      rng: random.Random,
  ) -> CombatResult:
    attacker_power = self.fighting_power(
        *attacker_cem, strategy=attacker_strategy, will=attacker_will
    )
    defender_power = self.fighting_power(
        *defender_cem, strategy=defender_strategy, will=defender_will
    )
    if attacker_power == defender_power:
      if self._ties == "attacker":
        attacker_wins = True
      elif self._ties == "defender":
        attacker_wins = False
      else:
        raise UndefinedRuleError("combat tie handling is not configured")
    else:
      attacker_wins = attacker_power > defender_power

    attacker_delta = CountryDelta()
    defender_delta = CountryDelta()
    if attacker_wins:
      self._change_random_dimensions(attacker_delta, count=1, amount=1, rng=rng)
      self._change_random_dimensions(defender_delta, count=1, amount=-2, rng=rng)
      winner = attacker_name
    else:
      self._change_random_dimensions(attacker_delta, count=2, amount=-1, rng=rng)
      winner = defender_name
    return CombatResult(
        attacker_power=attacker_power,
        defender_power=defender_power,
        winner=winner,
        attacker_delta=attacker_delta,
        defender_delta=defender_delta,
    )

  def random_capability_delta(
      self, *, count: int, amount: float, rng: random.Random
  ) -> CountryDelta:
    delta = CountryDelta()
    self._change_random_dimensions(delta, count=count, amount=amount, rng=rng)
    return delta

  def _change_random_dimensions(
      self,
      delta: CountryDelta,
      *,
      count: int,
      amount: float,
      rng: random.Random,
  ) -> None:
    dimensions = ["c", "e", "m"]
    if self._sample_without_replacement:
      if count > len(dimensions):
        raise ValueError("cannot sample more unique capabilities than exist")
      selected = rng.sample(dimensions, k=count)
    else:
      selected = rng.choices(dimensions, k=count)
    for dimension in selected:
      setattr(delta, dimension, getattr(delta, dimension) + amount)
