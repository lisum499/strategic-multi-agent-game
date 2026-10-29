"""End-to-end multi-agent game orchestration."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import copy
from dataclasses import dataclass
import warnings

from strategic_game.agents import PeacefulMockCountryAgent
from strategic_game.agents import CountryAgent
from strategic_game.calculators import CombatCalculator
from strategic_game.errors import InvalidActionError
from strategic_game.models import DecisionPackage
from strategic_game.models import ActionType
from strategic_game.models import RoundReport
from strategic_game.models import WorldState
from strategic_game.scenario import GameScenario
from strategic_game.state_machine import SealedRound


@dataclass(frozen=True)
class GameResult:
  scenario: str
  reports: list[RoundReport]
  private_decisions: list[dict[str, DecisionPackage]]
  final_world: WorldState
  termination_reason: str


class StrategicGame:
  """Run independent country agents through sealed simultaneous rounds."""

  def __init__(
      self,
      *,
      scenario: GameScenario,
      agents: dict[str, CountryAgent],
      seed: int,
      max_retries: int = 3,
      stalemate_rounds: int = 12,
      on_report: Callable[[RoundReport], None] | None = None,
  ) -> None:
    expected = {country.name for country in scenario.countries}
    if set(agents) != expected:
      raise ValueError("agents must match scenario countries exactly")
    self._scenario = scenario
    self._agents = agents
    self._seed = seed
    self._max_retries = max_retries
    if stalemate_rounds < 0:
      raise ValueError("stalemate_rounds cannot be negative")
    self._stalemate_rounds = stalemate_rounds
    self._on_report = on_report

  def play(self, max_rounds: int | None = None) -> GameResult:
    world = self._scenario.initial_world()
    reports: list[RoundReport] = []
    private_decisions: list[dict[str, DecisionPackage]] = []
    limit = max_rounds or self._scenario.max_rounds
    peaceful_rounds = 0
    termination_reason = "round_limit"

    while world.round <= limit and not world.winner:
      living = [
          name for name, country in world.countries.items() if country.alive
      ]
      if len(living) < 2:
        world.winner = living[0] if living else None
        termination_reason = "last_state_standing"
        break

      decisions = self._generate_concurrently(living, world, reports)
      combat = CombatCalculator(
          strategy_penalties={
              0: 0,
              1: 0,
              2: -1,
              3: -3,
              4: -4,
              5: -4,
              6: -4,
              7: -4,
          },
          ties="defender",
      )
      sealed = SealedRound(
          world, seed=self._seed + world.round, combat=combat
      )
      accepted: dict[str, DecisionPackage] = {}
      for country_name in sorted(living):
        package = decisions[country_name]
        for attempt in range(self._max_retries + 1):
          try:
            sealed.submit(country_name, package)
            accepted[country_name] = package
            break
          except (InvalidActionError, ValueError) as exc:
            if attempt >= self._max_retries:
              warnings.warn(
                  f"{country_name} repeatedly generated a rule-illegal "
                  f"decision; using an audited peaceful fallback. Error: {exc}",
                  stacklevel=2,
              )
              package = PeacefulMockCountryAgent().decide(
                  country_name=country_name,
                  scenario=self._scenario,
                  world=copy.deepcopy(world),
                  public_history=tuple(reports),
                  error_feedback=str(exc),
              )
              package.private_rationale = (
                  "SYSTEM_FALLBACK: 模型决策多次未通过规则合法性校验，"
                  "规则引擎采用和平默认行动。"
              )
              sealed.submit(country_name, package)
              accepted[country_name] = package
              break
            package = self._decide_with_retries(
                country_name,
                world,
                reports,
                initial_feedback=str(exc),
            )

      sealed.reveal()
      report = sealed.resolve()
      private_decisions.append(copy.deepcopy(accepted))
      reports.append(report)
      if self._on_report:
        self._on_report(report)
      all_general_peace = all(
          action.action == ActionType.CALL_FOR_PEACE
          and action.target_war_id is None
          for action in report.executions.values()
      )
      has_active_war = any(war.active for war in world.wars.values())
      peaceful_rounds = (
          peaceful_rounds + 1
          if all_general_peace and not has_active_war
          else 0
      )
      if world.winner:
        termination_reason = "victory_margin"
        continue
      if (
          self._stalemate_rounds
          and peaceful_rounds >= self._stalemate_rounds
      ):
        termination_reason = "stalemate"
        break

    return GameResult(
        scenario=self._scenario.name,
        reports=reports,
        private_decisions=private_decisions,
        final_world=world,
        termination_reason=termination_reason,
    )

  def _generate_concurrently(
      self,
      living: list[str],
      world: WorldState,
      reports: list[RoundReport],
  ) -> dict[str, DecisionPackage]:
    decisions: dict[str, DecisionPackage] = {}
    with ThreadPoolExecutor(max_workers=len(living)) as executor:
      futures = {
          executor.submit(
              self._decide_with_retries,
              name,
              world,
              reports,
          ): name
          for name in living
      }
      for future in as_completed(futures):
        name = futures[future]
        decisions[name] = future.result()
    return decisions

  def _decide_with_retries(
      self,
      country_name: str,
      world: WorldState,
      reports: list[RoundReport],
      initial_feedback: str | None = None,
  ) -> DecisionPackage:
    feedback = initial_feedback
    last_error: Exception | None = None
    for _ in range(self._max_retries + 1):
      try:
        return self._agents[country_name].decide(
            country_name=country_name,
            scenario=self._scenario,
            world=copy.deepcopy(world),
            public_history=tuple(reports),
            error_feedback=feedback,
        )
      except Exception as exc:  # Model/API/JSON errors all receive a repair retry.
        last_error = exc
        feedback = str(exc)
    warnings.warn(
        f"{country_name} failed to generate a valid package after retries; "
        f"using an audited peaceful fallback. Last error: {last_error}",
        stacklevel=2,
    )
    fallback = PeacefulMockCountryAgent().decide(
        country_name=country_name,
        scenario=self._scenario,
        world=copy.deepcopy(world),
        public_history=tuple(reports),
        error_feedback=str(last_error),
    )
    fallback.private_rationale = (
        "SYSTEM_FALLBACK: 模型多次未生成合法决策包，规则引擎采用和平默认行动。"
    )
    return fallback
