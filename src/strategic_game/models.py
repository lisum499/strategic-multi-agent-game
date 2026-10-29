"""Pydantic schemas for sealed strategic-game decisions and world state."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


Score = Annotated[float, Field(ge=-5, le=5)]
Capability = Annotated[float, Field(ge=0, le=5)]


class StrictModel(BaseModel):
  """Reject unknown fields so model output cannot silently change the rules."""

  model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ActionType(StrEnum):
  CONVENTIONAL_WAR = "conventional_war"
  NUCLEAR_STRIKE = "nuclear_strike"
  INTERVENE = "intervene"
  CALL_FOR_PEACE = "call_for_peace"


class Confidence(StrEnum):
  HIGH = "high"
  MEDIUM = "medium"
  LOW = "low"


class NuclearStatus(StrEnum):
  NONE = "none"
  LIMITED = "limited"
  FULL = "full"
  SECOND_STRIKE = "second_strike"


class RoundPhase(StrEnum):
  COMMITMENT = "commitment"
  REVEALED = "revealed"
  RESOLVED = "resolved"


class Forecast(StrictModel):
  state: str = Field(min_length=1)
  predicted_action: ActionType
  predicted_target: str = Field(min_length=1)
  confidence: Confidence
  miscalculation_risk: str = Field(min_length=1, max_length=500)


class Action(StrictModel):
  action: ActionType
  target_state: str | None = None
  target_war_id: str | None = None
  support_side: str | None = None
  public_reason: str | None = Field(default=None, max_length=500)
  peace_terms: str | None = Field(default=None, max_length=1000)

  @model_validator(mode="after")
  def validate_action_fields(self) -> Action:
    if self.action == ActionType.CONVENTIONAL_WAR:
      if not self.target_state:
        raise ValueError("conventional_war requires target_state")
      if self.target_war_id or self.support_side:
        raise ValueError("conventional_war cannot target or support a war")
    elif self.action == ActionType.NUCLEAR_STRIKE:
      if not self.target_state:
        raise ValueError("nuclear_strike requires target_state")
      if self.support_side:
        raise ValueError("nuclear_strike cannot set support_side")
    elif self.action == ActionType.INTERVENE:
      if not self.target_war_id or not self.support_side:
        raise ValueError("intervene requires target_war_id and support_side")
      if self.peace_terms:
        raise ValueError("intervene cannot include peace_terms")
    elif self.action == ActionType.CALL_FOR_PEACE:
      if not self.target_state and not self.target_war_id:
        raise ValueError("call_for_peace requires a state or war target")
      if self.support_side:
        raise ValueError("call_for_peace cannot set support_side")
    return self

  def target_key(self) -> tuple[str | None, str | None, str | None]:
    return (self.target_state, self.target_war_id, self.support_side)


class DecisionPackage(StrictModel):
  round: int = Field(ge=1)
  forecast: list[Forecast] = Field(default_factory=list)
  declaration: Action
  execution: Action
  private_rationale: str = Field(min_length=1, max_length=2000)

  @model_validator(mode="after")
  def declaration_and_execution_share_target(self) -> DecisionPackage:
    if self.declaration.target_key() != self.execution.target_key():
      raise ValueError(
          "declaration and execution must have identical target fields"
      )
    if self.declaration.public_reason is None:
      raise ValueError("declaration requires public_reason")
    return self


class CountryState(StrictModel):
  name: str = Field(min_length=1)
  c: Capability
  e: Capability
  m: Capability
  reputation: Score = 0
  credibility: Score = 0
  nuclear_status: NuclearStatus = NuclearStatus.NONE
  nuclear_reach: set[str] = Field(default_factory=set)
  alive: bool = True
  cannot_start_war_until_round: int = Field(default=0, ge=0)
  last_rewarded_peace_round: int | None = Field(default=None, ge=1)

  @property
  def material_power(self) -> float:
    return self.c + self.e + self.m

  @property
  def victory_score(self) -> float:
    return self.material_power + self.reputation + self.credibility


class WarState(StrictModel):
  id: str = Field(min_length=1)
  attacker: str = Field(min_length=1)
  defender: str = Field(min_length=1)
  started_round: int = Field(ge=1)
  remaining_rounds: int = Field(default=2, ge=0)
  active: bool = True

  @model_validator(mode="after")
  def different_parties(self) -> WarState:
    if self.attacker == self.defender:
      raise ValueError("a country cannot wage war against itself")
    return self


class WorldState(StrictModel):
  round: int = Field(default=1, ge=1)
  countries: dict[str, CountryState]
  wars: dict[str, WarState] = Field(default_factory=dict)
  winner: str | None = None

  @model_validator(mode="after")
  def country_keys_match_names(self) -> WorldState:
    for key, country in self.countries.items():
      if key != country.name:
        raise ValueError(f"country key {key!r} does not match its name")
    return self

  def active_wars_for(self, country: str) -> list[WarState]:
    return [
        war
        for war in self.wars.values()
        if war.active and country in (war.attacker, war.defender)
    ]


class CountryDelta(StrictModel):
  c: float = 0
  e: float = 0
  m: float = 0
  reputation: float = 0
  credibility: float = 0

  def add(self, other: CountryDelta) -> None:
    self.c += other.c
    self.e += other.e
    self.m += other.m
    self.reputation += other.reputation
    self.credibility += other.credibility


class AuditEvent(StrictModel):
  kind: str
  actor: str | None = None
  target: str | None = None
  details: dict[str, object] = Field(default_factory=dict)


class RoundReport(StrictModel):
  round: int
  declarations: dict[str, Action]
  executions: dict[str, Action]
  deltas: dict[str, CountryDelta]
  events: list[AuditEvent]
  deaths: list[str]
  eliminated_states: list[str] = Field(default_factory=list)
  scores: dict[str, float]
  winner: str | None = None
