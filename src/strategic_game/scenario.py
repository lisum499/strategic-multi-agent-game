"""Scenario configuration for the end-to-end strategic game runner."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from strategic_game.models import CountryState
from strategic_game.models import NuclearStatus
from strategic_game.models import StrictModel
from strategic_game.models import WorldState


class CountryProfile(StrictModel):
  strategic_culture: str
  ideology: str
  threat_perception: str
  foreign_policy: str
  war_aim: str
  stakeholders: list[str] = Field(default_factory=list)
  goal: str = Field(min_length=1)
  geography: str = ""


class CountryConfig(StrictModel):
  name: str = Field(min_length=1)
  c: float = Field(ge=0, le=5)
  e: float = Field(ge=0, le=5)
  m: float = Field(ge=0, le=5)
  reputation: float = Field(default=0, ge=-5, le=5)
  credibility: float = Field(default=0, ge=-5, le=5)
  nuclear_status: NuclearStatus = NuclearStatus.NONE
  nuclear_reach: set[str] = Field(default_factory=set)
  profile: CountryProfile

  def to_state(self) -> CountryState:
    return CountryState(
        name=self.name,
        c=self.c,
        e=self.e,
        m=self.m,
        reputation=self.reputation,
        credibility=self.credibility,
        nuclear_status=self.nuclear_status,
        nuclear_reach=self.nuclear_reach,
    )


class GameScenario(StrictModel):
  name: str = Field(min_length=1)
  description: str = Field(min_length=1)
  polarity: str
  system_environment: str
  max_rounds: int = Field(default=10, ge=1)
  countries: list[CountryConfig] = Field(min_length=2)

  def initial_world(self) -> WorldState:
    states = {country.name: country.to_state() for country in self.countries}
    if len(states) != len(self.countries):
      raise ValueError("scenario contains duplicate country names")
    return WorldState(countries=states)

  def profile_for(self, country_name: str) -> CountryProfile:
    for country in self.countries:
      if country.name == country_name:
        return country.profile
    raise KeyError(country_name)


def load_scenario(path: Path) -> GameScenario:
  return GameScenario.model_validate_json(path.read_text(encoding="utf-8"))


def dump_scenario(scenario: GameScenario) -> str:
  return json.dumps(
      scenario.model_dump(mode="json"), ensure_ascii=False, indent=2
  )
