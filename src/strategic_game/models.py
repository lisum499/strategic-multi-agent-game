"""Typed wire models for the sealed protocol; numeric game rules live in rules.py."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ActionType(StrEnum):
    WAR = "war"
    NUCLEAR = "nuclear"
    INTERVENE = "intervene"
    PEACE = "peace"


class RoundPhase(StrEnum):
    COMMITMENT = "commitment"
    REVEALED = "revealed"
    RESOLVED = "resolved"


class Forecast(StrictModel):
    country_id: str = Field(min_length=1)
    action_type: Literal["war", "nuclear", "intervene", "peace"]
    target_country_id: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    miscalculation_risk: str = Field(min_length=1, max_length=500)


class Choice(StrictModel):
    option_id: str = Field(min_length=1)
    public_statement: str = Field(max_length=40)


class DecisionPackage(StrictModel):
    round: StrictInt = Field(ge=1)
    country_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=1)
    forecast: list[Forecast]
    declaration: Choice
    execution: Choice
    private_rationale: str = Field(min_length=1, max_length=2000)
    negotiation: dict[str, dict[str, Any]]
    threat_observation: dict[str, Any] | None = None
