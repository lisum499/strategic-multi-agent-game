"""模拟的数据对象：国家、战争、危机、目标卡状态、事件、状态。

只存数据，不做裁决；裁决在 rules.py，调度在 engine.py。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

STAT_KEYS = ("c", "e", "m")          # C 人口领土 / E 经济 / M 军事
NUCLEAR_LEVELS = ("无", "有但打击能力有限", "有", "二次核打击")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_half(value: float) -> float:
    """保留到 0.5，避免浮点尾巴影响胜负边界。"""
    return round(value * 2) / 2


@dataclass
class Country:
    country_id: str
    name: str
    c: float
    e: float
    m: float
    rep: float = 0.0
    cred: float = 0.0
    nuclear_status: str = "无"
    role_card: dict[str, Any] = field(default_factory=dict)
    alive: bool = True
    war_bans: set[int] = field(default_factory=set)   # 这些回合内不得发动 war
    base_score: float = 0.0   # 开局 C+E+M；v4.0 的积分是相对它的净变化

    def __post_init__(self) -> None:
        if not self.base_score:
            self.base_score = round_half(self.c + self.e + self.m)

    @property
    def score(self) -> float:
        """v4.0：净积分 = 当前 C+E+M − 开局 C+E+M；声誉/可信度不计分。"""
        return round_half(self.c + self.e + self.m - self.base_score)

    def stat(self, key: str) -> float:
        return getattr(self, key)

    def add_stat(self, key: str, delta: float) -> None:
        if key not in STAT_KEYS:
            raise ValueError(f"未知数值项：{key}")
        setattr(self, key, round_half(getattr(self, key) + delta))

    def add_rep(self, delta: float) -> None:
        self.rep = round_half(clamp(self.rep + delta, -5.0, 5.0))

    def add_cred(self, delta: float) -> None:
        self.cred = round_half(clamp(self.cred + delta, -5.0, 5.0))

    def public_dict(self, with_values: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "country_id": self.country_id,
            "name": self.name,
            "alive": self.alive,
            "nuclear_status": self.nuclear_status,
            "score": self.score,
            "base_score": self.base_score,
            "rep": self.rep,
            "cred": self.cred,
        }
        if with_values:
            data.update({k: round_half(getattr(self, k)) for k in STAT_KEYS})
        return data


@dataclass
class War:
    war_id: str
    attacker_id: str
    defender_id: str
    started_round: int
    progress_round: int = 1          # 1 = 战争第 1 轮；2 = 第 2 轮
    status: str = "ongoing"          # ongoing / ended
    winner_id: str | None = None
    ended_reason: str | None = None
    intervened_by: list[dict[str, str]] = field(default_factory=list)

    def parties(self) -> set[str]:
        return {self.attacker_id, self.defender_id}

    def other_side(self, country_id: str) -> str | None:
        if country_id == self.attacker_id:
            return self.defender_id
        if country_id == self.defender_id:
            return self.attacker_id
        return None

    def end(self, winner_id: str | None, reason: str) -> None:
        self.status = "ended"
        self.winner_id = winner_id
        self.ended_reason = reason

    def public_dict(self) -> dict[str, Any]:
        return {
            "war_id": self.war_id,
            "attacker_id": self.attacker_id,
            "defender_id": self.defender_id,
            "started_round": self.started_round,
            "progress_round": self.progress_round,
            "status": self.status,
            "winner_id": self.winner_id,
            "ended_reason": self.ended_reason,
            "interventions": [
                {"intervener_id": item["intervener_id"],
                 "support_id": item["support_id"],
                 "round": item.get("round")}
                for item in self.intervened_by
            ],
        }


@dataclass
class Crisis:
    """v3.0 危机时钟。状态只存数据，成本/解决的裁决在 rules.py 与 engine.py。"""
    crisis_id: str
    title: str
    parties: list[str]
    dependents: list[str]
    control: str | None
    stake_name: str
    control_bonus: dict[str, float]
    unresolved_cost: dict[str, dict[str, float]]
    escalation_rounds: list[int]
    escalation_multiplier: float
    grace_rounds: int = 2
    level: int = 1
    stage: str = "crisis"          # crisis / war / stalemate / resolved
    stalled_rounds: int = 0
    resolved: bool = False
    resolution: str | None = None
    resolved_round: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "crisis_id": self.crisis_id,
            "title": self.title,
            "parties": list(self.parties),
            "dependents": list(self.dependents),
            "control": self.control,
            "stake": self.stake_name,
            "level": self.level,
            "stage": self.stage,
            "stalled_rounds": self.stalled_rounds,
            "grace_rounds": self.grace_rounds,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "resolved_round": self.resolved_round,
            "control_bonus": dict(self.control_bonus),
            "unresolved_cost": {key: dict(value)
                                for key, value in self.unresolved_cost.items()},
            "escalation_rounds": list(self.escalation_rounds),
            "escalation_multiplier": self.escalation_multiplier,
        }


@dataclass
class Event:
    event_id: str
    round: int
    type: str
    text: str
    visibility: Any = "public"       # "public" 或 {"C1","C2"} 这样的国家集合

    def visible_to(self, country_id: str) -> bool:
        if self.visibility == "public":
            return True
        return country_id in self.visibility

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "round": self.round,
            "type": self.type,
            "text": self.text,
        }


@dataclass
class State:
    scenario_id: str
    max_rounds: int
    round: int
    countries: dict[str, Country]
    wars: dict[str, War]
    crisis: Crisis | None = None
    objective_definitions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    objective_states: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    env_summary: dict[str, Any] | None = None
    visibility: dict[str, Any] = field(default_factory=lambda: {"ce_m_public": True})
    _war_counter: int = 0
    _event_counter: int = 0
    _initial_war_ids: set[str] = field(default_factory=set)

    # ── 查询 ────────────────────────────────────────────────────────────
    def alive_ids(self) -> list[str]:
        return sorted(cid for cid, c in self.countries.items() if c.alive)

    def alive_countries(self) -> list[Country]:
        return [self.countries[cid] for cid in self.alive_ids()]

    def ongoing_wars(self) -> list[War]:
        return [w for w in self.wars.values() if w.status == "ongoing"]

    def wars_between(self, a: str, b: str) -> list[War]:
        return [w for w in self.ongoing_wars()
                if w.attacker_id in (a, b) and w.defender_id in (a, b)
                and {w.attacker_id, w.defender_id} == {a, b}]

    def party_wars(self, country_id: str) -> list[War]:
        return [w for w in self.ongoing_wars() if country_id in w.parties()]

    def is_party_in_any_war(self, country_id: str) -> bool:
        return bool(self.party_wars(country_id))

    def wars_existing_at_round_start(self) -> list[War]:
        """本轮开始时已存在的战争（本轮新开的不算）。"""
        return [w for w in self.ongoing_wars()
                if w.started_round < self.round or w.war_id in self._initial_war_ids]

    def public_events(self, country_id: str, rounds: int = 2) -> list[Event]:
        floor = self.round - rounds
        return [e for e in self.events
                if e.round >= floor and e.visible_to(country_id)]

    def standings(self) -> dict[str, Any]:
        ranked = sorted(self.alive_countries(), key=lambda c: c.score, reverse=True)
        if not ranked:
            return {"rank": None, "leader_id": None, "gap_to_leader": None}
        leader = ranked[0]
        return {
            "rank": [c.country_id for c in ranked].index(leader.country_id) + 1,
            "leader_id": leader.country_id,
            "leader_score": leader.score,
            "scores": {c.country_id: c.score for c in ranked},
        }

    def standings_for(self, country_id: str) -> dict[str, Any]:
        table = self.standings()
        scores = table.get("scores", {})
        own = scores.get(country_id)
        others = [v for k, v in scores.items() if k != country_id]
        if own is None:
            return {"rank": None, "leader_id": table.get("leader_id"),
                    "gap_to_leader": None, "score": None}
        best_other = max(others) if others else None
        rank = 1 + sum(1 for value in scores.values() if value > own)
        return {
            "rank": rank,
            "leader_id": table.get("leader_id"),
            "gap_to_leader": None if best_other is None else round_half(own - best_other),
            "score": own,
        }

    # ── 变更 ────────────────────────────────────────────────────────────
    def next_war_id(self) -> str:
        self._war_counter += 1
        return f"w_{self._war_counter}"

    def next_event_id(self) -> str:
        self._event_counter += 1
        return f"e_{self._event_counter}"

    def add_event(self, round_no: int, type_: str, text: str,
                  visibility: Any = "public") -> Event:
        event = Event(self.next_event_id(), round_no, type_, text, visibility)
        self.events.append(event)
        return event

    def add_war(self, attacker_id: str, defender_id: str) -> War:
        war = War(
            war_id=self.next_war_id(),
            attacker_id=attacker_id,
            defender_id=defender_id,
            started_round=self.round,
            progress_round=1,
        )
        self.wars[war.war_id] = war
        return war

    def kill_if_needed(self) -> list[str]:
        dead: list[str] = []
        for country in self.countries.values():
            if country.alive and any(country.stat(k) < 0 for k in STAT_KEYS):
                country.alive = False
                dead.append(country.country_id)
        for war in self.ongoing_wars():
            if not (self.countries[war.attacker_id].alive and self.countries[war.defender_id].alive):
                war.end(None, "交战方死亡")
        return dead

    def snapshot(self) -> "State":
        return copy.deepcopy(self)

    # ── 序列化 ──────────────────────────────────────────────────────────
    def to_public_state(self) -> dict[str, Any]:
        with_values = bool(self.visibility.get("ce_m_public", True))
        return {
            "turn_limit": self.max_rounds,
            "remaining_rounds": max(0, self.max_rounds - self.round + 1),
            "countries": [c.public_dict(with_values) for c in
                          (self.countries[cid] for cid in self.alive_ids())],
            "wars": [w.public_dict() for w in self.ongoing_wars()],
            "crisis": self.crisis.public_dict() if self.crisis is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "round": self.round,
            "max_rounds": self.max_rounds,
            "countries": {
                cid: {
                    "country_id": c.country_id, "name": c.name, "alive": c.alive,
                    "c": c.c, "e": c.e, "m": c.m, "rep": c.rep, "cred": c.cred,
                    "score": c.score, "base_score": c.base_score,
                    "nuclear_status": c.nuclear_status,
                    "war_bans": sorted(c.war_bans),
                }
                for cid, c in sorted(self.countries.items())
            },
            "wars": {wid: w.public_dict() for wid, w in sorted(self.wars.items())},
            "crisis": self.crisis.public_dict() if self.crisis is not None else None,
            "objective_states": copy.deepcopy(self.objective_states),
            "events": [e.public_dict() for e in self.events
                       if e.visibility == "public"],
            "event_count": len(self.events),
            "environment_summary": self.env_summary,
        }

    @classmethod
    def from_scenario(cls, scenario: dict[str, Any]) -> "State":
        countries = {
            item["country_id"]: Country(
                country_id=item["country_id"],
                name=item.get("name", item["country_id"]),
                c=float(item["c"]), e=float(item["e"]), m=float(item["m"]),
                rep=float(item.get("rep", 0.0)),
                cred=float(item.get("cred", 0.0)),
                nuclear_status=item.get("nuclear_status", "无"),
                role_card=dict(item.get("role_card", {})),
            )
            for item in scenario["countries"]
        }
        objective_definitions = {
            item["country_id"]: list(item.get("objectives", []))
            for item in scenario.get("objectives", [])
        }
        state = cls(
            scenario_id=scenario.get("scenario_id", "scenario"),
            max_rounds=int(scenario.get("max_rounds", 8)),
            round=1,
            countries=countries,
            wars={},
            visibility=dict(scenario.get("visibility", {"ce_m_public": True})),
            objective_definitions=objective_definitions,
        )
        crisis_config = scenario.get("crisis")
        if crisis_config:
            state.crisis = Crisis(
                crisis_id=str(crisis_config.get("crisis_id", "crisis")),
                title=str(crisis_config.get("title", "危机")),
                parties=list(crisis_config.get("parties", [])),
                dependents=list(crisis_config.get("dependents", [])),
                control=crisis_config.get("control"),
                stake_name=str(crisis_config.get("stake", "争议标的")),
                control_bonus=dict(crisis_config.get("control_bonus", {})),
                unresolved_cost={key: dict(value) for key, value in
                                 crisis_config.get("unresolved_cost", {}).items()},
                escalation_rounds=[int(x) for x in
                                   crisis_config.get("escalation_rounds", [])],
                escalation_multiplier=float(
                    crisis_config.get("escalation_multiplier", 2.0)),
                grace_rounds=int(crisis_config.get("grace_rounds", 2)),
            )
            state.objective_states = {
                cid: {item["id"]: {"achieved": False, "triggered": False}
                      for item in items}
                for cid, items in state.objective_definitions.items()
            }
        for item in scenario.get("initial_wars", []):
            war = War(
                war_id=item["war_id"],
                attacker_id=item["attacker_id"],
                defender_id=item["defender_id"],
                started_round=int(item.get("started_round", 1)),
                progress_round=int(item.get("progress_round", 1)),
            )
            state.wars[war.war_id] = war
            state._initial_war_ids.add(war.war_id)
            state._war_counter = max(state._war_counter,
                                     int(war.war_id.split("_")[-1]) if war.war_id.startswith("w_") else 0)
        return state
