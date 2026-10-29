"""Immutable collection and reveal barriers for a sealed round."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from .models import DecisionPackage, RoundPhase


class SealedRound:
    """Freeze every submission before revealing only public decision fields."""

    def __init__(self, country_ids: Iterable[str], *, round_no: int | None = None,
                 snapshot_hash: str | None = None):
        ids = list(country_ids)
        if not ids or len(ids) != len(set(ids)) or not all(isinstance(x, str) and x for x in ids):
            raise ValueError("country_ids 必须是非空且无重复的国家 ID 集合")
        self._required = frozenset(ids)
        self._submissions: dict[str, dict[str, Any]] = {}
        self._round_no = round_no
        self._snapshot_hash = snapshot_hash
        self._phase = RoundPhase.COMMITMENT

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
        return self._submissions.keys() == self._required

    def submit(self, country_id: str, package: Mapping[str, Any] | DecisionPackage) -> None:
        if self._phase != RoundPhase.COMMITMENT:
            raise ValueError("揭示后不可提交")
        if country_id not in self._required or country_id in self._submissions:
            raise ValueError("国家不在本轮存活集合中或已提交")
        value = (package.model_dump(exclude_none=True) if isinstance(package, DecisionPackage)
                 else dict(package))
        value = DecisionPackage.model_validate(value).model_dump(exclude_none=True)
        if value.get("country_id") != country_id:
            raise ValueError("决策 country_id 与提交者不符")
        if self._round_no is not None and value.get("round") != self._round_no:
            raise ValueError("决策回合不符")
        if self._snapshot_hash is not None and value.get("snapshot_hash") != self._snapshot_hash:
            raise ValueError("决策快照不符")
        self._submissions[country_id] = copy.deepcopy(value)

    def reveal(self) -> dict[str, dict[str, Any]]:
        if self._phase != RoundPhase.COMMITMENT or not self.ready_to_reveal:
            raise ValueError("只有全部国家提交后才能统一揭示一次")
        self._phase = RoundPhase.REVEALED
        return {
            country_id: {
                "country_id": country_id, "round": package["round"],
                "declaration": copy.deepcopy(package["declaration"]),
                "execution": copy.deepcopy(package["execution"]),
            }
            for country_id, package in sorted(self._submissions.items())
        }

    def private_submission(self, *, requester: str, country_name: str | None = None,
                           country_id: str | None = None) -> dict[str, Any]:
        owner = country_id if country_id is not None else country_name
        if requester != owner:
            raise PermissionError("私有密封包仅可由所属国家读取")
        return copy.deepcopy(self._submissions[owner])

    def mark_resolved(self) -> None:
        if self._phase != RoundPhase.REVEALED:
            raise ValueError("结算前必须全部揭示")
        self._phase = RoundPhase.RESOLVED
