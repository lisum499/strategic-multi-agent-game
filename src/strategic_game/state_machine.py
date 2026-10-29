"""A sealed simultaneous-round state machine with deterministic settlement."""

from __future__ import annotations

import copy
import random
from types import MappingProxyType
from typing import Mapping

from strategic_game.calculators import CombatCalculator
from strategic_game.calculators import CredibilityCalculator
from strategic_game.calculators import InterventionCalculator
from strategic_game.calculators import ReputationCalculator
from strategic_game.calculators import clamp
from strategic_game.errors import InvalidActionError
from strategic_game.errors import InvalidPhaseError
from strategic_game.errors import UndefinedRuleError
from strategic_game.models import Action
from strategic_game.models import ActionType
from strategic_game.models import AuditEvent
from strategic_game.models import CountryDelta
from strategic_game.models import DecisionPackage
from strategic_game.models import NuclearStatus
from strategic_game.models import RoundPhase
from strategic_game.models import RoundReport
from strategic_game.models import WarState
from strategic_game.models import WorldState


class SealedRound:
  """Collect one decision per country, reveal together, then settle together."""

  def __init__(
      self,
      world: WorldState,
      *,
      seed: int,
      combat: CombatCalculator | None = None,
  ) -> None:
    if world.winner:
      raise InvalidPhaseError("cannot start a round after the game has a winner")
    self._world = world
    self._snapshot = copy.deepcopy(world)
    self._phase = RoundPhase.COMMITMENT
    self._submissions: dict[str, DecisionPackage] = {}
    self._required = frozenset(
        name for name, country in world.countries.items() if country.alive
    )
    self._rng = random.Random(seed)
    self._combat = combat or CombatCalculator()
    self._report: RoundReport | None = None

  @property
  def phase(self) -> RoundPhase:
    return self._phase

  @property
  def submitted_count(self) -> int:
    return len(self._submissions)

  @property
  def required_count(self) -> int:
    return len(self._required)

  @property
  def ready_to_reveal(self) -> bool:
    return set(self._submissions) == self._required

  def submit(self, country_name: str, package: DecisionPackage) -> None:
    if self._phase != RoundPhase.COMMITMENT:
      raise InvalidPhaseError("submissions are accepted only during commitment")
    if country_name not in self._required:
      raise InvalidActionError(f"{country_name!r} is not an active country")
    if country_name in self._submissions:
      raise InvalidActionError(f"{country_name!r} already submitted")
    if package.round != self._snapshot.round:
      raise InvalidActionError(
          f"decision is for round {package.round}, expected {self._snapshot.round}"
      )
    self._validate_action(country_name, package.declaration)
    self._validate_action(country_name, package.execution)
    self._validate_forecasts(country_name, package)
    try:
      CredibilityCalculator.change(
          package.declaration.action, package.execution.action
      )
    except UndefinedRuleError as exc:
      raise InvalidActionError(str(exc)) from exc
    self._submissions[country_name] = copy.deepcopy(package)

  def reveal(self) -> Mapping[str, tuple[Action, Action]]:
    if self._phase != RoundPhase.COMMITMENT:
      raise InvalidPhaseError("round has already been revealed")
    if not self.ready_to_reveal:
      raise InvalidPhaseError(
          f"cannot reveal: {self.submitted_count}/{self.required_count} submitted"
      )
    self._phase = RoundPhase.REVEALED
    public = {
        country: (package.declaration, package.execution)
        for country, package in self._submissions.items()
    }
    return MappingProxyType(copy.deepcopy(public))

  def resolve(self) -> RoundReport:
    if self._phase != RoundPhase.REVEALED:
      raise InvalidPhaseError("resolve requires a fully revealed round")

    before = copy.deepcopy(self._world)
    try:
      return self._resolve_transaction()
    except Exception:
      self._world.round = before.round
      self._world.countries = before.countries
      self._world.wars = before.wars
      self._world.winner = before.winner
      raise

  def _resolve_transaction(self) -> RoundReport:
    """Resolve atomically; the public method rolls back any failed rule."""

    deltas = {name: CountryDelta() for name in self._snapshot.countries}
    events: list[AuditEvent] = []

    for actor in sorted(self._submissions):
      package = self._submissions[actor]
      credibility = CredibilityCalculator.change(
          package.declaration.action, package.execution.action
      )
      reputation = self._reputation_change(actor, package.execution)
      deltas[actor].credibility += credibility
      deltas[actor].reputation += reputation
      if (
          package.execution.action == ActionType.CALL_FOR_PEACE
          and reputation > 0
      ):
        self._world.countries[actor].last_rewarded_peace_round = (
            self._snapshot.round
        )
      events.append(
          AuditEvent(
              kind="public_metrics",
              actor=actor,
              details={
                  "credibility_change": credibility,
                  "reputation_change": reputation,
              },
          )
      )

    new_wars: list[WarState] = []
    ended_wars: set[str] = set()
    for actor in sorted(self._submissions):
      action = self._submissions[actor].execution
      if action.action == ActionType.CONVENTIONAL_WAR:
        assert action.target_state is not None
        war = WarState(
            id=self._new_war_id(actor, action.target_state),
            attacker=actor,
            defender=action.target_state,
            started_round=self._snapshot.round,
        )
        new_wars.append(war)
        events.append(
            AuditEvent(
                kind="war_started",
                actor=actor,
                target=action.target_state,
                details={"war_id": war.id, "remaining_rounds": 2},
            )
        )
      elif action.action == ActionType.NUCLEAR_STRIKE:
        self._resolve_nuclear(
            actor, action, deltas, ended_wars, events
        )
      elif action.action == ActionType.CALL_FOR_PEACE:
        self._resolve_peace(actor, action, deltas, ended_wars, events)
      elif action.action == ActionType.INTERVENE:
        self._resolve_intervention(actor, action, deltas, events)

    self._advance_existing_wars(deltas, ended_wars, events)
    self._apply_deltas(deltas, events)

    for war_id in ended_wars:
      if war_id in self._world.wars:
        self._world.wars[war_id].active = False
    for war in new_wars:
      self._world.wars[war.id] = war

    deaths = sorted(
        name
        for name, country in self._world.countries.items()
        if self._snapshot.countries[name].alive and not country.alive
    )
    eliminated_states = sorted(
        name for name, country in self._world.countries.items()
        if not country.alive
    )
    scores = {
        name: country.victory_score
        for name, country in self._world.countries.items()
        if country.alive
    }
    winner = self._detect_winner(scores)
    self._world.winner = winner
    self._report = RoundReport(
        round=self._snapshot.round,
        declarations={
            name: package.declaration
            for name, package in self._submissions.items()
        },
        executions={
            name: package.execution
            for name, package in self._submissions.items()
        },
        deltas=deltas,
        events=events,
        deaths=deaths,
        eliminated_states=eliminated_states,
        scores=scores,
        winner=winner,
    )
    self._world.round += 1
    self._phase = RoundPhase.RESOLVED
    return copy.deepcopy(self._report)

  def private_submission(
      self, *, requester: str, country_name: str
  ) -> DecisionPackage:
    """Return a private package only to its owner, never to another country."""
    if requester != country_name:
      raise InvalidActionError("a country cannot read another sealed package")
    try:
      return copy.deepcopy(self._submissions[country_name])
    except KeyError as exc:
      raise InvalidActionError("no submission exists for that country") from exc

  def _validate_action(self, actor: str, action: Action) -> None:
    country = self._snapshot.countries[actor]
    target = action.target_state
    if target:
      if target == actor and action.action != ActionType.CALL_FOR_PEACE:
        raise InvalidActionError("a hostile action cannot target its actor")
      if target not in self._snapshot.countries:
        raise InvalidActionError(f"unknown target country {target!r}")
      if not self._snapshot.countries[target].alive:
        raise InvalidActionError(f"target country {target!r} is not alive")

    if action.action == ActionType.NUCLEAR_STRIKE:
      if self._snapshot.round == 1:
        raise InvalidActionError("nuclear declarations and actions are banned in round 1")
      if country.nuclear_status == NuclearStatus.NONE:
        raise InvalidActionError("country has no nuclear weapons")
      if country.m < 4:
        raise InvalidActionError("military capability below 4 cannot support nuclear use")
      assert target is not None
      if target not in country.nuclear_reach:
        raise InvalidActionError(f"nuclear target {target!r} is out of reach")
    if (
        country.nuclear_status == NuclearStatus.SECOND_STRIKE and country.m < 5
    ):
      raise InvalidActionError("second-strike status requires military capability 5")

    active_wars = self._snapshot.active_wars_for(actor)
    if action.action == ActionType.CONVENTIONAL_WAR:
      if active_wars:
        raise InvalidActionError("a current belligerent cannot start another war")
      if country.cannot_start_war_until_round >= self._snapshot.round:
        raise InvalidActionError("country is temporarily forbidden to start war")
      assert target is not None
      if self._snapshot.active_wars_for(target):
        raise InvalidActionError("target is already a belligerent in an active war")
    elif action.action == ActionType.INTERVENE:
      assert action.target_war_id is not None
      war = self._snapshot.wars.get(action.target_war_id)
      if not war or not war.active:
        raise InvalidActionError("intervention requires an existing active war")
      if action.support_side not in (war.attacker, war.defender):
        raise InvalidActionError("support_side must be a belligerent in the war")
      if actor in (war.attacker, war.defender):
        raise InvalidActionError("a belligerent cannot intervene in its own war")
    elif action.action == ActionType.CALL_FOR_PEACE and action.target_war_id:
      war = self._snapshot.wars.get(action.target_war_id)
      if not war or not war.active:
        raise InvalidActionError("peace target must be an active war")
      if actor not in (war.attacker, war.defender):
        raise InvalidActionError("only a belligerent can call peace in a war")

  def _validate_forecasts(
      self, actor: str, package: DecisionPackage
  ) -> None:
    expected = {
        name
        for name, country in self._snapshot.countries.items()
        if name != actor and country.alive
    }
    predicted = [forecast.state for forecast in package.forecast]
    if len(predicted) != len(set(predicted)):
      raise InvalidActionError("forecast states must be unique")
    if set(predicted) != expected:
      raise InvalidActionError(
          "forecast must contain exactly one entry for every living opponent; "
          f"expected {sorted(expected)}"
      )
    living = {
        name
        for name, country in self._snapshot.countries.items()
        if country.alive
    }
    for forecast in package.forecast:
      if forecast.predicted_target not in living:
        raise InvalidActionError(
            f"forecast target {forecast.predicted_target!r} is not a living state"
        )
      if (
          forecast.predicted_action != ActionType.CALL_FOR_PEACE
          and forecast.predicted_target == forecast.state
      ):
        raise InvalidActionError("a forecast cannot predict a hostile self-target")

  def _reputation_change(self, actor: str, action: Action) -> int:
    if (
        action.action == ActionType.CALL_FOR_PEACE
        and self._snapshot.active_wars_for(actor)
        and not action.target_war_id
    ):
      return 0
    if action.action == ActionType.CALL_FOR_PEACE:
      last_reward = self._snapshot.countries[actor].last_rewarded_peace_round
      if last_reward is not None and self._snapshot.round - last_reward < 3:
        return 0
    if action.action != ActionType.INTERVENE:
      defensive = False
      if action.action == ActionType.NUCLEAR_STRIKE:
        defensive = self._is_defender_against(actor, action.target_state)
      return ReputationCalculator.change(
          action.action, defensive_nuclear_use=defensive
      )

    assert action.target_war_id and action.support_side
    war = self._snapshot.wars[action.target_war_id]
    other = war.defender if action.support_side == war.attacker else war.attacker
    supported_power = self._snapshot.countries[action.support_side].material_power
    other_power = self._snapshot.countries[other].material_power
    if supported_power == other_power:
      raise UndefinedRuleError(
          "intervention reputation for equally strong sides is undefined"
      )
    return ReputationCalculator.change(
        ActionType.INTERVENE,
        intervention_supports_stronger_side=supported_power > other_power,
    )

  def _resolve_nuclear(
      self,
      actor: str,
      action: Action,
      deltas: dict[str, CountryDelta],
      ended_wars: set[str],
      events: list[AuditEvent],
  ) -> None:
    assert action.target_state is not None
    target = action.target_state
    deltas[target].add(
        self._combat.random_capability_delta(
            count=1, amount=-2, rng=self._rng
        )
    )
    war = self._war_between(actor, target)
    if war:
      ended_wars.add(war.id)
      if actor == war.attacker:
        deltas[actor].add(
            self._combat.random_capability_delta(
                count=1, amount=0.5, rng=self._rng
            )
        )
        outcome = "attacker_nuclear_victory"
      else:
        deltas[actor].add(
            self._combat.random_capability_delta(
                count=1, amount=-1, rng=self._rng
            )
        )
        self._world.countries[actor].cannot_start_war_until_round = (
            self._snapshot.round + 1
        )
        outcome = "defender_nuclear_victory"
    else:
      outcome = "nuclear_strike"
    events.append(
        AuditEvent(kind=outcome, actor=actor, target=target)
    )

  def _resolve_peace(
      self,
      actor: str,
      action: Action,
      deltas: dict[str, CountryDelta],
      ended_wars: set[str],
      events: list[AuditEvent],
  ) -> None:
    if not action.target_war_id:
      events.append(AuditEvent(kind="peace_appeal", actor=actor, target=action.target_state))
      return
    war = self._snapshot.wars[action.target_war_id]
    if actor == war.attacker:
      deltas[actor].add(
          self._combat.random_capability_delta(
              count=1, amount=-1, rng=self._rng
          )
      )
      ended_wars.add(war.id)
      events.append(
          AuditEvent(
              kind="attacker_withdraws",
              actor=actor,
              target=war.defender,
              details={"war_id": war.id},
          )
      )
      return
    raise UndefinedRuleError(
        "defender peace requires a structured negotiated C/E/M transfer; "
        "free-text peace_terms cannot safely modify numeric state"
    )

  def _resolve_intervention(
      self,
      actor: str,
      action: Action,
      deltas: dict[str, CountryDelta],
      events: list[AuditEvent],
  ) -> None:
    assert action.target_war_id and action.support_side
    war = self._snapshot.wars[action.target_war_id]
    opposed_side = (
        war.defender if action.support_side == war.attacker else war.attacker
    )
    supported_power = self._snapshot.countries[action.support_side].material_power
    opposed_power = self._snapshot.countries[opposed_side].material_power
    success = supported_power > opposed_power
    intervener_delta, opposed_delta = InterventionCalculator.changes(
        success=success, rng=self._rng, combat=self._combat
    )
    deltas[actor].add(intervener_delta)
    deltas[opposed_side].add(opposed_delta)
    events.append(
        AuditEvent(
            kind="intervention_resolved",
            actor=actor,
            target=opposed_side,
            details={
                "war_id": war.id,
                "support_side": action.support_side,
                "success": success,
                "policy": "supported_side_pre_round_material_power",
            },
        )
    )

  def _advance_existing_wars(
      self,
      deltas: dict[str, CountryDelta],
      ended_wars: set[str],
      events: list[AuditEvent],
  ) -> None:
    for war_id, snapshot_war in sorted(self._snapshot.wars.items()):
      if not snapshot_war.active or war_id in ended_wars:
        continue
      world_war = self._world.wars[war_id]
      world_war.remaining_rounds -= 1
      if world_war.remaining_rounds > 0:
        continue
      attacker = self._snapshot.countries[snapshot_war.attacker]
      defender = self._snapshot.countries[snapshot_war.defender]
      result = self._combat.resolve(
          attacker_name=attacker.name,
          defender_name=defender.name,
          attacker_cem=(attacker.c, attacker.e, attacker.m),
          defender_cem=(defender.c, defender.e, defender.m),
          rng=self._rng,
      )
      deltas[attacker.name].add(result.attacker_delta)
      deltas[defender.name].add(result.defender_delta)
      ended_wars.add(war_id)
      events.append(
          AuditEvent(
              kind="war_resolved",
              actor=result.winner,
              details={
                  "war_id": war_id,
                  "attacker_power": result.attacker_power,
                  "defender_power": result.defender_power,
              },
          )
      )

  def _apply_deltas(
      self,
      deltas: dict[str, CountryDelta],
      events: list[AuditEvent],
  ) -> None:
    for name, delta in deltas.items():
      country = self._world.countries[name]
      raw_c = country.c + delta.c
      raw_e = country.e + delta.e
      raw_m = country.m + delta.m
      if min(raw_c, raw_e, raw_m) < 0:
        country.alive = False
        events.append(AuditEvent(kind="country_death", actor=name))
      country.c = clamp(raw_c, 0, 5)
      country.e = clamp(raw_e, 0, 5)
      country.m = clamp(raw_m, 0, 5)
      country.reputation = clamp(country.reputation + delta.reputation, -5, 5)
      country.credibility = clamp(country.credibility + delta.credibility, -5, 5)

  @staticmethod
  def _detect_winner(scores: dict[str, float]) -> str | None:
    if len(scores) < 2:
      return next(iter(scores), None)
    ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ranking[0][0] if ranking[0][1] - ranking[1][1] >= 10 else None

  def _new_war_id(self, attacker: str, defender: str) -> str:
    return f"war-r{self._snapshot.round}-{attacker}-{defender}"

  def _war_between(self, first: str, second: str) -> WarState | None:
    for war in self._snapshot.wars.values():
      if war.active and {war.attacker, war.defender} == {first, second}:
        return war
    return None

  def _is_defender_against(self, actor: str, target: str | None) -> bool:
    if target is None:
      return False
    war = self._war_between(actor, target)
    return bool(war and war.defender == actor)
