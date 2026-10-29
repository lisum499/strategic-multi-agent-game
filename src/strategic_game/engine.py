"""整轮密封调度：合法组合、重试与默认和平、v4.1结算、审计和回放。"""

from __future__ import annotations

import hashlib
import copy
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import rules
from .providers import Provider, ProviderError, build_env_prompts
from .protocol import validate_package, make_fallback, build_sealed_prompts, match_negotiation
from .state_machine import SealedRound
from .scenario import public_context_source_ids, scenario_hash
from .state import STAT_KEYS, State, War, round_half
from .tools import rules_v4 as R
from .tools.render_user import RULE_CARD_FILE
from .validation import ResponseError, validate_decision, validate_env_summary

ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = "2"
DECISION_PROTOCOL = "sealed_v1"
FAILURE_POLICY = "peace_fallback_v1"
PROMPT_VERSION = "v4.1-sealed.1"
BASE_DIR = Path(__file__).resolve().parent
HASHED_FILES = (
    "protocol.py", "models.py", "state_machine.py", "engine.py", "rules.py", "state.py", "validation.py", "providers.py", "cli.py",
    "scenario.py", "report.py", "tools/rules_v4.py",
    "tools/render_user.py", "tools/gen_rule_card.py", "knowledge/label_dictionary.json",
    "prompts/sealed/00-system.md",
    "prompts/v2.1/20-规则卡-v2.0.md", "prompts/v2.1/30-env-summary.md",
)


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes() -> dict[str, str]:
    return {name: _file_sha256(BASE_DIR / name) for name in HASHED_FILES}


def role_tag_definitions(role_card: dict[str, Any]) -> dict[str, Any]:
    """仅解释本国已给出的标签；未给定和未知标签不推导。"""
    dictionary = json.loads((BASE_DIR / "knowledge/label_dictionary.json").read_text(encoding="utf-8"))
    dimensions = dictionary["dimensions"]
    result: dict[str, Any] = {}
    for field, dimension in (("archetype", "strategic_culture"),
                             ("war_aim", "war_aim"),
                             ("diplomatic_policy", "foreign_policy")):
        selected = role_card.get(field)
        values = selected if isinstance(selected, list) else [selected]
        definitions = {value: dimensions[dimension]["values"][value]["definition"]
                       for value in values if isinstance(value, str)
                       and value in dimensions[dimension]["values"]}
        if definitions:
            result[field] = definitions
    ideology = role_card.get("ideology")
    if isinstance(ideology, dict):
        definitions = {}
        salience = ideology.get("salience")
        if isinstance(salience, (int, float)) and not isinstance(salience, bool) and 1 <= salience <= 5:
            definitions["salience"] = dimensions["ideology"]["values"]["侧重程度"]["definition"]
        orientation = ideology.get("orientation")
        if isinstance(orientation, str) and orientation in ("狂热", "包容"):
            definitions["orientation"] = dimensions["ideology"]["values"][orientation]["definition"]
        if definitions:
            result["ideology"] = definitions
    return result


class Engine:
    def __init__(self, scenario: dict[str, Any], provider: Provider | None, *,
                 seed: int | None = None,
                 decisions: dict[str, Any] | None = None,
                 env_summaries: dict[str, Any] | None = None,
                 failed_keys: set[str] | None = None,
                 env_failures: set[str] | None = None,
                 negotiation_mode: str | None = None,
                 log_prompts: bool = True, max_workers: int = 7, max_retries: int = 3):
        declared_rules = scenario.get("rules_version")
        if declared_rules is not None and declared_rules != R.RULES_VERSION:
            raise ValueError(
                f"场景规则版本 {declared_rules!r} 与当前引擎 {R.RULES_VERSION!r} 不一致")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers 必须为正整数")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries 必须为非负整数")
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.scenario = copy.deepcopy(scenario)
        self.provider = provider
        self.state = State.from_scenario(scenario)
        # The whole world_context is public; per-country observations are private.
        self.world_context = copy.deepcopy(scenario.get("world_context", {}))
        self.world_context_source_ids = public_context_source_ids(self.world_context)
        self.private_observations = {
            item["country_id"]: copy.deepcopy(item.get("private_observation", []))
            for item in scenario["countries"]
        }
        self.threat_observation_enabled = scenario.get("threat_observation_enabled", False)
        if not isinstance(self.threat_observation_enabled, bool):
            raise ValueError("threat_observation_enabled 必须是布尔值")
        self.private_threat_observations: dict[str, dict[str, Any]] = {}
        self._round_threat_observations: dict[str, dict[str, Any]] = {}
        self.negotiation_mode = (negotiation_mode
                                 or scenario.get("negotiation_mode", "offers"))
        if self.negotiation_mode not in ("offers", "self_offer"):
            raise ValueError(f"未知谈判模式：{self.negotiation_mode}")
        self.seed = int(seed if seed is not None else scenario.get("seed", 20261029))
        self.option_rng = random.Random(self.seed ^ 0x9E3779B9)
        self.settle_rng = random.Random(self.seed)
        self.decisions = decisions or {}
        self.env_summaries = env_summaries or {}
        self.failed_keys = failed_keys or set()
        self.env_failures = env_failures or set()
        self.replay_mode = decisions is not None or failed_keys is not None
        self.log_prompts = log_prompts
        self.declarations: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.packages: dict[str, dict[str, Any]] = {}
        self.origins: dict[str, str] = {}
        self.round_logs: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.rng_draws: list[dict[str, Any]] = []
        self._pending_stats: dict[str, dict[str, float]] = {}
        self.option_orders: dict[str, list[str]] = {}
        self.result: dict[str, Any] | None = None
        self._round_events: list[Any] = []
        self.system_hash = ""
        self.rule_card_hash = (_file_sha256(RULE_CARD_FILE)
                               if RULE_CARD_FILE.exists() else "")

    # ── 主循环 ──────────────────────────────────────────────────────────
    def run(self, on_round=None, *, max_completed_rounds: int | None = None) -> dict[str, Any]:
        while self.result is None and (max_completed_rounds is None or
                                      len(self.round_logs) < max_completed_rounds):
            self._play_round()
            if on_round is not None:
                on_round(self)
            if self.result is not None:
                break
            if self.state.round > self.state.max_rounds:
                self.result = {"winner": None, "reason": "回合上限未决"}
                break
        return self.summary()

    def _play_round(self) -> None:
        # Settlement is a transaction. Keep attempts for diagnosis, restore every
        # mutable world/RNG output on an exception, never commit a partial round.
        fields = ("state", "declarations", "actions", "packages", "origins",
                  "private_threat_observations", "_round_threat_observations",
                  "_pending_stats", "_round_events", "result", "option_orders", "_sealed_round")
        backup = {key: copy.deepcopy(getattr(self, key, None)) for key in fields}
        option_rng, settle_rng = self.option_rng.getstate(), self.settle_rng.getstate()
        round_count, draw_count = len(self.round_logs), len(self.rng_draws)
        try:
            self._play_round_transaction()
        except BaseException:
            for key, value in backup.items():
                setattr(self, key, value)
            self.option_rng.setstate(option_rng)
            self.settle_rng.setstate(settle_rng)
            del self.round_logs[round_count:]
            del self.rng_draws[draw_count:]
            raise

    def _play_round_transaction(self) -> None:
        round_no = self.state.round
        snapshot = self.state.to_dict()
        self._round_events = []
        self._round_threat_observations = {}
        self._collect_sealed()

        settlement = self._settle()
        self._sealed_round.mark_resolved()

        if abs(sum(c.score for c in self.state.countries.values())) > 1e-9:
            raise RuntimeError("零和守恒失败：本轮未提交")
        end = self._check_end()
        env_summary = self._make_env_summary(round_no, settlement)
        self.state.env_summary = env_summary

        self.round_logs.append({
            "round": round_no,
            "origins": dict(self.origins),
            "forecast_evaluation": self._evaluate_forecasts(),
            "snapshot": snapshot,
            "declarations": {cid: self._public_decision(d) for cid, d in
                             sorted(self.declarations.items())},
            "actions": {cid: self._public_decision(a) for cid, a in
                        sorted(self.actions.items())},
            "settlement": settlement,
            "events": [e.public_dict() for e in self._round_events],
            "state_after": self.state.to_dict(),
            "environment_summary": env_summary,
            "end_check": end,
            "option_orders": {k: v for k, v in self.option_orders.items()
                              if k.startswith(f"{round_no}:")},
        })
        if self.threat_observation_enabled:
            self.round_logs[-1]["private_threat_observations"] = dict(self._round_threat_observations)

        if end is not None:
            self.result = end
            return

        for war in self.state.ongoing_wars():
            if war.progress_round < 2:
                war.progress_round += 1
        self.state.round += 1

    def build_sealed_situation(self, country_id: str) -> dict[str, Any]:
        country = self.state.countries[country_id]
        situation: dict[str, Any] = {
            "round": self.state.round,
            "phase": "sealed",
            "rules_version": R.RULES_VERSION,
            "prompt_version": PROMPT_VERSION,
            "self": {
                "country_id": country_id,
                "name": country.name,
                "role_card": country.role_card,
                "role_tag_definitions": role_tag_definitions(country.role_card),
                "nuclear_status": country.nuclear_status,
                "own_stats": {"c": country.c, "e": country.e, "m": country.m,
                              "rep": country.rep, "cred": country.cred},
            },
            "state": self.state.to_public_state(),
            "standings": self.state.standings_for(country_id),
            "events": [e.public_dict() for e in self.state.public_events(country_id)],
            "environment_summary": self.state.env_summary,
        }
        if self.world_context:
            situation["world_context"] = copy.deepcopy(self.world_context)
        if self.private_observations.get(country_id):
            situation["self"]["private_observation"] = copy.deepcopy(self.private_observations[country_id])
        definitions = self.state.objective_definitions.get(country_id, [])
        if definitions:
            status = self.state.objective_states.get(country_id, {})
            situation["self"]["objectives"] = [
                {**{key: item[key] for key in ("id", "type", "description")
                    if key in item},
                 **({"condition": item["condition"]} if "condition" in item else {}),
                 **({"reward": item["reward"]} if "reward" in item else {}),
                 **({"penalty": item["penalty"]} if "penalty" in item else {}),
                 **({"response_actions": item["response_actions"]}
                    if "response_actions" in item else {}),
                 "status": status.get(item["id"], {})}
                for item in definitions
            ]
        if self.threat_observation_enabled:
            situation["self"]["previous_threat_observation"] = self.private_threat_observations.get(country_id)
        situation["snapshot_hash"] = hashlib.sha256(json.dumps(
            self.state.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        situation["decision_protocol"] = DECISION_PROTOCOL
        situation["negotiation_mode"] = self.negotiation_mode
        declarations = self._shuffle_options(
            rules.declaration_options(self.state, country_id),
            f"{self.state.round}:sealed:{country_id}:declaration")
        for declaration in declarations:
            declaration["execution_options"] = self._shuffle_options(
                rules.action_options(self.state, country_id, declaration),
                f"{self.state.round}:sealed:{country_id}:{declaration['option_id']}")
        situation["legal_declarations"] = declarations
        contexts = {}
        for war in self.state.wars_existing_at_round_start():
            if country_id not in war.parties():
                continue
            context = {"role": "attacker" if country_id == war.attacker_id else "defender",
                       "war": war.public_dict()}
            if self.negotiation_mode == "offers":
                context["offers"] = rules.negotiation_offers(self.state, war)
            else:
                context["offer_bounds"] = rules.negotiation_bounds()
            contexts[war.war_id] = context
        situation["negotiation_contexts"] = contexts
        if self.threat_observation_enabled:
            situation["threat_observation_sources"] = [
                "state", "self", *(["world_context"] if self.world_context else []),
                *self.world_context_source_ids,
                *[event["id"] for event in situation["events"]]]
        return situation

    def _collect_package(self, country_id: str, situation: dict[str, Any],
                         record: dict[str, Any]) -> tuple[dict, str]:
        key = record["key"]
        if self.replay_mode:
            if key not in self.decisions:
                raise ValueError(f"回放缺少密封决定：{key}")
            saved = self.decisions[key]
            package, _ = validate_package(saved["package"], situation)
            origin = saved.get("origin", "model")
            if origin not in ("model", "fallback"):
                raise ValueError("回放origin无效")
            record.update(adopted=package, origin=origin)
            return package, origin
        system, user = build_sealed_prompts(situation)
        self.system_hash = hashlib.sha256(system.encode()).hexdigest()
        if self.log_prompts:
            record.update(system=system, user=user)
        feedback = ""
        for attempt in range(1, self.max_retries + 2):
            raw = None
            try:
                # Each model receives its own detached snapshot, never a live
                # world, another submission, or a previously submitted package.
                raw = self.provider.decide("sealed", system, user + feedback,
                                           copy.deepcopy(situation))
                package, warnings = validate_package(raw, situation)
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ProviderError, ResponseError, ValueError)) else type(exc).__name__
                record["attempts"].append({"attempt": attempt, "type": "failed",
                    "error": error, "error_type": type(exc).__name__, "raw": raw,
                    "call_info": copy.deepcopy(getattr(self.provider, "last_call_info", {}) or {})})
                feedback = "\n上一次决策包无效：" + error + "。仅基于原输入重新输出完整JSON。"
                continue
            record["attempts"].append({"attempt": attempt, "type": "ok", "raw": raw,
                "warnings": warnings,
                "call_info": copy.deepcopy(getattr(self.provider, "last_call_info", {}) or {})})
            record.update(adopted=package, origin="model")
            return package, "model"
        package, _ = validate_package(make_fallback(situation), situation)
        record.update(adopted=package, origin="fallback", fallback_reason="decision_attempts_exhausted")
        return package, "fallback"

    def _collect_sealed(self) -> None:
        ids = sorted(self.state.alive_ids())
        # Build menus and shuffle in stable country order, before any workers run.
        situations = {cid: self.build_sealed_situation(cid) for cid in ids}
        records = {}
        for cid in ids:
            record = {"round": self.state.round, "phase": "sealed", "country_id": cid,
                "key": f"{self.state.round}:sealed:{cid}", "attempts": [], "adopted": None,
                "origin": None, "snapshot_hash": situations[cid]["snapshot_hash"],
                "shown_to": {"state": "public", "self": cid, "world_context": "public",
                    "events": "per_visibility", "forecast": cid, "negotiation": cid,
                    "private_rationale": cid, "declaration": "public_after_reveal",
                    "execution": "public_after_reveal"}}
            records[cid] = record
            if not self.replay_mode:
                self.records.append(record)
        if self.replay_mode:
            results = {cid: self._collect_package(cid, situations[cid], records[cid]) for cid in ids}
        else:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(ids)))) as pool:
                futures = {cid: pool.submit(self._collect_package, cid, situations[cid], records[cid]) for cid in ids}
                results = {cid: future.result() for cid, future in futures.items()}
        # A hard barrier: no reveal or state mutation before all valid packages or
        # audited fallbacks have arrived. Sorted installation is independent of I/O order.
        barrier = SealedRound(ids, round_no=self.state.round,
                              snapshot_hash=situations[ids[0]]["snapshot_hash"])
        for cid in ids:
            barrier.submit(cid, results[cid][0])
        barrier.reveal()
        self._sealed_round = barrier
        self.packages = {cid: barrier.private_submission(requester=cid, country_id=cid) for cid in ids}
        self.origins = {cid: results[cid][1] for cid in ids}
        self.declarations, self.actions = {}, {}
        for cid in ids:
            package = self.packages[cid]
            decl = copy.deepcopy(self._canonical_option(situations[cid]["legal_declarations"], package["declaration"]["option_id"]))
            action = copy.deepcopy(self._canonical_option(decl.pop("execution_options"), package["execution"]["option_id"]))
            for option, submitted in ((decl, package["declaration"]), (action, package["execution"])):
                option["public_statement"] = submitted["public_statement"]
                option["decision_basis"] = {"依据": package["private_rationale"]}
            self.declarations[cid], self.actions[cid] = decl, action
            if self.threat_observation_enabled:
                observation = {"round": self.state.round, "phase": "sealed", **package["threat_observation"]}
                self._round_threat_observations[cid] = observation
                self.private_threat_observations[cid] = observation

    def _evaluate_forecasts(self) -> list[dict[str, Any]]:
        evaluations = []
        for observer, package in sorted(self.packages.items()):
            for forecast in package["forecast"]:
                target = forecast["country_id"]
                eligible = self.origins.get(observer) == "model" and self.origins.get(target) == "model"
                item = {"observer_id": observer, "country_id": target, "eligible": eligible}
                if not eligible:
                    item["reason"] = "system_fallback"
                else:
                    action = self.actions[target]
                    item["action_hit"] = forecast["action_type"] == action["action_type"]
                    item["target_hit"] = forecast["target_country_id"] == action["target_country_id"]
                    item["joint_hit"] = item["action_hit"] and item["target_hit"]
                evaluations.append(item)
        return evaluations

    def _shuffle_options(self, options: list[dict[str, Any]],
                         key: str) -> list[dict[str, Any]]:
        self.option_rng.shuffle(options)
        self.option_orders[key] = [item["option_id"] for item in options]
        return options

    @staticmethod
    def _canonical_option(options: list[dict[str, Any]],
                          option_id: str) -> dict[str, Any]:
        for option in options:
            if option["option_id"] == option_id:
                return option
        raise RuntimeError(f"option_id 不在本次合法集中：{option_id}")

    @staticmethod
    def _public_decision(decision: dict[str, Any]) -> dict[str, Any]:
        data = {
            "action_type": decision["action_type"],
            "target_country_id": decision["target_country_id"],
            "war_id": decision.get("war_id"),
            "support_country_id": decision.get("support_country_id"),
        }
        if "public_statement" in decision:
            data["public_statement"] = decision["public_statement"]
        return data

    # ── 目标卡与危机辅助 ────────────────────────────────────────────────
    def _transfer(self, source_id: str, target_id: str, key: str,
                  amount: float) -> float:
        """v4.0 零和转移：source 失去、target 获得同一 C/E/M 项，返回实际转移量。"""
        if amount <= 0 or source_id == target_id:
            return 0.0
        source = self.state.countries.get(source_id)
        target = self.state.countries.get(target_id)
        if source is None or target is None:
            return 0.0
        if not source.alive or not target.alive:
            return 0.0
        if key not in STAT_KEYS:
            raise ValueError(f"零和只转移 C/E/M：{key}")
        capacity = float(R.SCORING["stat_ceiling"]) - target.stat(key)
        effective = min(float(amount), max(0.0, capacity))
        if effective <= 0:
            return 0.0
        source.add_stat(key, -effective)
        target.add_stat(key, effective)
        return effective

    def _transfer_random(self, source_id: str, target_id: str, amount: float,
                         *, kind: str = "transfer") -> float:
        """随机抽取一项 C/E/M 后转移；请求量与实际量都写入 rng_draws。"""
        if amount <= 0:
            return 0.0
        key = self.settle_rng.choice(list(STAT_KEYS))
        effective = self._transfer(source_id, target_id, key, amount)
        self.rng_draws.append({
            "round": self.state.round, "kind": kind,
            "source": source_id, "target": target_id, "stat": key,
            "requested": round(float(amount), 4),
            "effective": round(effective, 4),
        })
        return effective

    def _nuclear_transfer(self, user_id: str, target_id: str) -> dict[str, float]:
        """v4.0 核转移：用户从目标取得 2 点，并向目标转移 1 点作为代价（净 +1）。"""
        key = self.settle_rng.choice(list(STAT_KEYS))
        gained = self._transfer(target_id, user_id, key, R.NUCLEAR["target_transfer"])
        paid = self._transfer(user_id, target_id, key,
                              R.NUCLEAR["user_fallout_transfer"])
        self.rng_draws.append({
            "round": self.state.round, "kind": "nuclear", "stat": key,
            "user": user_id, "target": target_id,
            "gained": round(gained, 4), "paid": round(paid, 4),
        })
        return {"stat": key, "gained": gained, "paid": paid}

    def _apply_score_effect(self, country_id: str, effect: dict[str, Any], *,
                            counterparty: str | None = None,
                            direction: str = "in") -> None:
        """目标卡/危机效果：C/E/M 必须成对转移；声誉/可信度只记录、不计分。"""
        country = self.state.countries.get(country_id)
        if country is None or not country.alive:
            return
        for key, delta in effect.items():
            value = float(delta)
            if key in STAT_KEYS:
                if counterparty is None or counterparty == country_id:
                    continue
                amount = abs(value)
                if direction == "in":
                    self._transfer(counterparty, country_id, key, amount)
                else:
                    self._transfer(country_id, counterparty, key, amount)
            elif key == "rep":
                country.add_rep(value)
            elif key == "cred":
                country.add_cred(value)
            else:
                raise ValueError(f"未知数值项：{key}")

    def _war_power_factors(self, war_id: str) -> tuple[float, float]:
        band = float(R.WAR["power_random_band"])
        attacker = self.settle_rng.uniform(1.0 - band, 1.0 + band)
        defender = self.settle_rng.uniform(1.0 - band, 1.0 + band)
        self.rng_draws.append({
            "round": self.state.round, "kind": "war_power", "war_id": war_id,
            "attacker_factor": round(attacker, 4),
            "defender_factor": round(defender, 4),
        })
        return attacker, defender

    def _resolve_crisis(self, executed: dict[str, dict[str, Any]],
                        wars_settled: list[dict[str, Any]],
                        negotiations: list[dict[str, Any]]) -> dict[str, Any]:
        """判断危机本轮是否解决，返回记录（不写成本）。"""
        crisis = self.state.crisis
        record: dict[str, Any] = {"resolved": False, "resolution": None,
                                  "control": None, "agreement_parties": []}
        if crisis is None or crisis.resolved:
            return record
        alive_parties = [cid for cid in crisis.parties
                         if self.state.countries[cid].alive]
        if len(alive_parties) < len(crisis.parties):
            crisis.resolved = True
            crisis.resolution = "party_exit"
            crisis.resolved_round = self.state.round
            if crisis.control is None or not self.state.countries[crisis.control].alive:
                crisis.control = alive_parties[0] if alive_parties else None
            self._event(
                f"危机解决：当事国退出模拟，{crisis.control or '无'} 取得{crisis.stake_name}控制权",
                "crisis")
            record.update({"resolved": True, "resolution": "party_exit",
                           "control": crisis.control})
            return record
        # 战争胜者取得控制权
        for item in wars_settled:
            if item.get("winner") is None:
                continue
            war = self.state.wars.get(item["war_id"])
            if war is None:
                continue
            parties = {war.attacker_id, war.defender_id}
            if parties == set(crisis.parties) and item["winner"] in crisis.parties:
                crisis.resolved = True
                crisis.resolution = "war"
                crisis.resolved_round = self.state.round
                crisis.control = item["winner"]
                self._event(
                    f"危机解决：{item['winner']} 在战争中获胜，取得{crisis.stake_name}控制权",
                    "crisis")
                record.update({"resolved": True, "resolution": "war",
                               "control": crisis.control})
                return record
        # 当事国之间的谈判达成 → 战争结束、危机解决（控制权不变）
        for negotiation in negotiations:
            if not negotiation.get("accepted"):
                continue
            war = self.state.wars.get(negotiation.get("war_id") or "")
            if war is not None and set(war.parties()) == set(crisis.parties):
                crisis.resolved = True
                crisis.resolution = "negotiation"
                crisis.resolved_round = self.state.round
                self._event(
                    f"危机解决：{crisis.parties[0]} 与 {crisis.parties[1]} "
                    f"通过谈判结束战争，控制权不变", "crisis")
                record.update({"resolved": True, "resolution": "negotiation",
                               "control": crisis.control})
                return record
        # 双方当事国同轮相互和平 → 共同安排
        if len(crisis.parties) == 2:
            first, second = crisis.parties

            def mutual(source: str, target: str) -> bool:
                entry = executed.get(source)
                action = self.actions.get(source)
                return bool(entry and entry.get("status") == "executed"
                            and action and action["action_type"] == "peace"
                            and action.get("war_id") is None
                            and action["target_country_id"] == target)

            if mutual(first, second) and mutual(second, first):
                crisis.resolved = True
                crisis.resolution = "agreement"
                crisis.resolved_round = self.state.round
                self._event(
                    f"危机解决：{first} 与 {second} 同轮相互和平，达成{crisis.stake_name}共同安排",
                    "crisis")
                record.update({"resolved": True, "resolution": "agreement",
                               "control": crisis.control,
                               "agreement_parties": [first, second]})
                return record
        return record

    def _update_crisis_stage(self, crisis: Any,
                             wars_settled: list[dict[str, Any]]) -> None:
        if crisis.resolved:
            crisis.stage = "resolved"
            return
        parties = set(crisis.parties)
        party_wars = [war for war in self.state.ongoing_wars()
                      if set(war.parties()) == parties]
        settled = [item for item in wars_settled
                   if item.get("war_id") in self.state.wars
                   and set(self.state.wars[item["war_id"]].parties()) == parties]
        if party_wars:
            crisis.stage = "war"
            crisis.stalled_rounds = 0
            return
        tie = any(item.get("winner") is None for item in settled)
        if tie:
            crisis.stage = "stalemate"
            crisis.stalled_rounds += 1
            crisis.level += 1
            self._event(
                f"危机陷入停滞：当事国战争未分胜负，危机等级升至 {crisis.level}",
                "crisis")
            return
        if crisis.stage == "war":
            crisis.stage = "stalemate"
            crisis.stalled_rounds += 1
            crisis.level += 1
            self._event(
                f"危机陷入停滞：战争结束但未解决争端，危机等级升至 {crisis.level}",
                "crisis")
            return
        if crisis.stage == "stalemate":
            crisis.stalled_rounds += 1
        else:
            crisis.stage = "crisis"
            crisis.stalled_rounds += 1

    def _apply_crisis_round(self, executed: dict[str, dict[str, Any]],
                            resolution: dict[str, Any],
                            was_unresolved: bool,
                            attacked: set[str],
                            wars_settled: list[dict[str, Any]]) -> dict[str, Any]:
        """应用危机成本/控制收益/升级与目标卡，再统一结算数值。"""
        crisis = self.state.crisis
        record: dict[str, Any] = {"costs": {}, "escalated": None,
                                  "objectives": []}
        if crisis is None:
            return record
        self._update_crisis_stage(crisis, wars_settled)
        if was_unresolved:
            costs = rules.crisis_round_cost(crisis, self.state.round)
            groups: dict[str, list[str]] = {"parties": [], "dependents": [],
                                            "others": []}
            for cid in self.state.alive_ids():
                groups[rules.crisis_group(crisis, cid)].append(cid)
            details = []
            applied = False
            for group, cids in groups.items():
                effects = costs.get(group) or {}
                if not cids or not any(value != 0 for value in effects.values()):
                    continue
                applied = True
                details.append(f"{'/'.join(cids)} " + " ".join(
                    f"{key}{'+' if value > 0 else ''}{value}"
                    for key, value in effects.items()))
                for cid in cids:
                    self._apply_score_effect(
                        cid, effects, counterparty=crisis.control,
                        direction="out")
            if applied and crisis.control:
                details.append(f"成本转移至控制方 {crisis.control}")
            if details:
                self._event(
                    f"危机（等级 {crisis.level}）仍未解决：" + "；".join(details),
                    "crisis")
                record["costs"] = {"level": crisis.level,
                                   "detail": details}
            if (not crisis.resolved and crisis.stage in ("crisis", "stalemate")
                    and self.state.round in crisis.escalation_rounds):
                crisis.level += 1
                self._event(f"危机升级至等级 {crisis.level}，未解决成本将放大",
                            "crisis")
                record["escalated"] = crisis.level
        # 目标卡：只在结算后的状态上判定一次
        for cid in self.state.alive_ids():
            for objective in self.state.objective_definitions.get(cid, []):
                status = self.state.objective_states.setdefault(
                    cid, {}).setdefault(
                        objective["id"], {"achieved": False, "triggered": False})
                kind = objective.get("type")
                try:
                    met = rules.objective_condition_met(
                        objective.get("condition", {}), self.state, cid, attacked)
                except ValueError:
                    met = False
                if kind == "goal" and met and not status["achieved"]:
                    status["achieved"] = True
                    reward = objective.get("reward", {})
                    self._apply_score_effect(
                        cid, reward, counterparty=objective.get("reward_from"),
                        direction="in")
                    self._event(
                        f"{cid} 达成目标「{objective.get('description', objective['id'])}」"
                        f"：{', '.join(f'{k}{v:+}' for k, v in reward.items())}",
                        "objective")
                    record["objectives"].append(
                        {"country_id": cid, "objective_id": objective["id"],
                         "type": kind, "result": "achieved",
                         "effects": reward})
                elif kind == "red_line" and met and not status["triggered"]:
                    status["triggered"] = True
                    action = self.actions.get(cid)
                    responses = set(objective.get(
                        "response_actions", ["war", "nuclear", "intervene"]))
                    responded = bool(action and action["action_type"] in responses
                                     and executed.get(cid, {}).get("status") == "executed")
                    if not responded:
                        penalty = objective.get("penalty", {})
                        self._apply_score_effect(
                            cid, penalty, counterparty=objective.get("penalty_to"),
                            direction="out")
                        self._event(
                            f"{cid} 红线被触及且未采取回应行动"
                            f"（{objective.get('description', objective['id'])}）："
                            f"{', '.join(f'{k}{v:+}' for k, v in penalty.items())}",
                            "objective")
                        record["objectives"].append(
                            {"country_id": cid, "objective_id": objective["id"],
                             "type": kind, "result": "penalty",
                             "effects": penalty})
                    else:
                        self._event(
                            f"{cid} 红线被触及并采取了回应行动"
                            f"（{objective.get('description', objective['id'])}），不扣分",
                            "objective")
                        record["objectives"].append(
                            {"country_id": cid, "objective_id": objective["id"],
                             "type": kind, "result": "responded"})
        return record

    # ── 结算 ────────────────────────────────────────────────────────────
    def _settle(self) -> dict[str, Any]:
        pre = self.state.snapshot()
        crisis_was_unresolved = bool(self.state.crisis
                                     and not self.state.crisis.resolved)
        executed: dict[str, dict[str, Any]] = {}
        nuclear_rep_exempt: set[str] = set()
        deaths: list[str] = []
        rejected_peace_wars: set[str] = set()
        self._pending_stats: dict[str, dict[str, float]] = {}

        # 1. 核使用（先结算）
        nuke = {cid: act for cid, act in self.actions.items()
                if act["action_type"] == "nuclear"
                and self.state.countries[cid].alive}
        handled: set[str] = set()
        for cid in sorted(nuke):
            if cid in handled:
                continue
            act = nuke[cid]
            target = act["target_country_id"]
            partner = nuke.get(target)
            simultaneous = (partner is not None
                            and partner["target_country_id"] == cid)
            if simultaneous:
                handled.update({cid, target})
            else:
                handled.add(cid)
            war = self._find_war(pre, cid, target)
            if war is not None and war.status != "ongoing":
                war = None
            if simultaneous:
                self._nuclear_transfer(cid, target)
                self._nuclear_transfer(target, cid)
                if war is not None:
                    war.end(None, "核互击")
                    self._event(f"{cid} 与 {target} 互相使用核武；战争结束", "nuclear")
                executed[cid] = {"action": act, "status": "executed"}
                executed[target] = {"action": partner, "status": "executed"}
                continue
            self._nuclear_transfer(cid, target)
            if war is not None:
                if cid == war.defender_id:
                    country = self.state.countries[cid]
                    country.war_bans.add(self.state.round + 1)
                    nuclear_rep_exempt.add(cid)
                    self._event(
                        f"{cid} 对 {target} 使用核武；{cid} 获胜但下一轮不得发动战争",
                        "nuclear")
                else:
                    self._event(f"{cid} 对 {target} 使用核武；{cid} 获胜", "nuclear")
                war.end(cid, "核使用")
            else:
                self._event(f"{cid} 对 {target} 使用核武；双方无战争", "nuclear")
            executed[cid] = {"action": act, "status": "executed"}

        deaths.extend(self._apply_pending(executed))

        # 2. 和平（含防守方谈判）
        peace = {cid: act for cid, act in self.actions.items()
                 if act["action_type"] == "peace"
                 and self.state.countries[cid].alive}
        negotiations: list[dict[str, Any]] = []
        by_war: dict[str, list[str]] = {}
        for cid, act in sorted(peace.items()):
            if not self.state.countries[act["target_country_id"]].alive:
                executed[cid] = {"action": act, "status": "canceled",
                                 "reason": "目标已死亡"}
                continue
            war_id = act.get("war_id")
            if war_id:
                by_war.setdefault(war_id, []).append(cid)
            else:
                executed[cid] = {"action": act, "status": "executed"}
        for war_id in sorted(by_war):
            war = self.state.wars.get(war_id)
            actors = by_war[war_id]
            if war is None or war.status != "ongoing":
                for cid in actors:
                    executed[cid] = {"action": peace[cid], "status": "canceled",
                                     "reason": "战争已结束"}
                continue
            attacker_peace = war.attacker_id in actors
            defender_peace = war.defender_id in actors
            if attacker_peace and defender_peace:
                self._transfer_random(war.attacker_id, war.defender_id, 1.0,
                                      kind="sue_peace")
                war.end(war.defender_id, "进攻方求和")
                for cid in actors:
                    executed[cid] = {"action": peace[cid], "status": "executed"}
                self._event(f"{war.war_id} 结束：进攻方求和，防守方获胜", "peace")
            elif attacker_peace:
                self._transfer_random(war.attacker_id, war.defender_id, 1.0,
                                      kind="sue_peace")
                war.end(war.defender_id, "进攻方求和")
                executed[war.attacker_id] = {"action": peace[war.attacker_id],
                                             "status": "executed"}
                self._event(f"{war.war_id} 结束：进攻方求和，防守方获胜", "peace")
            elif defender_peace:
                accepted = self._run_negotiation(war, negotiations)
                executed[war.defender_id] = {"action": peace[war.defender_id],
                                             "status": "executed"}
                if accepted:
                    self._event(f"{war.war_id} 结束：双方接受和平方案", "peace")
                else:
                    rejected_peace_wars.add(war.war_id)
                    self._event(f"{war.war_id} 和平谈判未达成", "peace")

        deaths.extend(self._apply_pending(executed))

        # 3. 干涉与新开战争
        for cid in sorted(self.actions):
            act = self.actions[cid]
            if act["action_type"] == "intervene":
                war = self.state.wars.get(act.get("war_id") or "")
                if (not self.state.countries[act["target_country_id"]].alive
                        or war is None or war.status != "ongoing"
                        or war.war_id not in {w.war_id for w in pre.wars_existing_at_round_start()}
                        or cid in war.parties()
                        or not self.state.countries[cid].alive):
                    executed[cid] = {"action": act, "status": "canceled",
                                     "reason": "干涉目标战争不可用"}
                    continue
                self._transfer_random(act["target_country_id"], cid, 0.5,
                                      kind="intervene_pressure")
                war.intervened_by.append({
                    "intervener_id": cid,
                    "support_id": act["support_country_id"],
                    "round": self.state.round,
                })
                executed[cid] = {"action": act, "status": "executed"}
                self._event(
                    f"{cid} 干涉 {war.war_id}，支持 {act['support_country_id']}",
                    "intervene")
            elif act["action_type"] == "war":
                target = act["target_country_id"]
                legal = (self.state.countries[cid].alive
                         and self.state.countries[target].alive
                         and not pre.is_party_in_any_war(cid)
                         and self.state.round not in self.state.countries[cid].war_bans
                         and not pre.wars_between(cid, target))
                if not legal:
                    executed[cid] = {"action": act, "status": "canceled",
                                     "reason": "开战条件不满足"}
                    continue
                war = self.state.add_war(cid, target)
                executed[cid] = {"action": act, "status": "executed"}
                self._event(f"{cid} 对 {target} 发动常规战争（{war.war_id}）", "war")
        deaths.extend(self._apply_pending(executed))

        # 4. 常规战结算（第 2 轮末）
        # 先用同一份快照算完所有战果，再统一应用数值；
        # 否则先结算的战争会改变后续战争的参战数和数值。
        combat_snapshot = self.state.snapshot()
        planned: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for war in sorted(self.state.wars.values(), key=lambda item: item.war_id):
            if war.status != "ongoing" or war.progress_round < 2:
                continue
            snapshot_war = combat_snapshot.wars[war.war_id]
            bonus = (float(R.WAR["target_peace_attacker_s_bonus"])
                     if war.war_id in rejected_peace_wars else 0.0)
            attacker_factor, defender_factor = self._war_power_factors(war.war_id)
            outcome = rules.resolve_war(
                combat_snapshot, snapshot_war, attacker_s_bonus=bonus,
                attacker_factor=attacker_factor,
                defender_factor=defender_factor)
            planned.append((war.war_id, outcome, {
                "attacker_factor": round(attacker_factor, 4),
                "defender_factor": round(defender_factor, 4)}))

        wars_settled: list[dict[str, Any]] = []
        attacked: set[str] = set()
        for war_id, outcome, factors in planned:
            war = self.state.wars[war_id]
            winner = outcome["winner"]
            attacked.update({war.defender_id})
            if winner is not None:
                loser = war.other_side(winner)
                self._transfer_random(loser, winner, R.WAR["winner_transfer"],
                                      kind="war_transfer")
            for item in war.intervened_by:
                if item.get("round") != self.state.round:
                    continue
                if item["support_id"] != winner:
                    # 失败：把压力转移返还并额外付出 1 点（净 −0.5）
                    self._transfer_random(item["intervener_id"],
                                          war.other_side(item["support_id"]) or
                                          item["support_id"],
                                          1.0, kind="intervene_fail")
            war.end(winner, "常规战结算")
            wars_settled.append({
                "war_id": war.war_id,
                "winner": winner,
                "attacker_power": round(outcome["attacker_power"], 4),
                "defender_power": round(outcome["defender_power"], 4),
                "factors": factors,
            })
            self._event(
                f"{war.war_id} 常规战结算：{'平局' if winner is None else winner + ' 获胜'}",
                "war")

        deaths.extend(self._apply_pending(executed))
        attacked.update(war.defender_id for war in self.state.ongoing_wars())
        resolution = self._resolve_crisis(executed, wars_settled, negotiations)

        # 5. 声誉与可信度（只算实际执行的行动）
        agreement_parties = set(resolution.get("agreement_parties") or [])
        for cid, entry in sorted(executed.items()):
            if entry["status"] != "executed":
                continue
            action = entry["action"]
            country = self.state.countries[cid]
            if not country.alive:
                continue
            if action["action_type"] == "peace" and action.get("war_id") is None:
                # v3.0：无战争和平是常规表态；只有达成危机安排才计分。
                declaration = self.declarations.get(cid)
                consistent = bool(declaration
                                  and declaration.get("action_type") == "peace"
                                  and declaration.get("war_id") is None)
                country.add_rep(
                    float(R.REPUTATION["crisis_agreement"]
                          if cid in agreement_parties
                          else R.REPUTATION["no_war_peace"]))
                if cid in agreement_parties:
                    country.add_cred(
                        float(R.PEACE["crisis_agreement_credibility"])
                        if consistent and declaration is not None
                        else rules.credibility_delta(declaration, action)
                        if declaration is not None else 0)
                elif consistent:
                    country.add_cred(float(R.PEACE["no_war_credibility"]))
                elif declaration is not None:
                    country.add_cred(rules.credibility_delta(declaration, action))
                continue
            reputation = rules.reputation_delta(pre, action)
            if action["action_type"] == "nuclear" and cid in nuclear_rep_exempt:
                reputation = 0.0
            country.add_rep(reputation)
            declaration = self.declarations.get(cid)
            if declaration is not None:
                country.add_cred(rules.credibility_delta(declaration, action))

        deaths.extend(self._apply_pending(executed))

        # 6. 危机成本/控制收益/升级与目标卡（v3.0）
        crisis_record = self._apply_crisis_round(
            executed, resolution, crisis_was_unresolved, attacked,
            wars_settled)
        deaths.extend(self._apply_pending(executed))

        return {
            "deaths": deaths,
            "executed": {cid: entry["status"] for cid, entry in sorted(executed.items())},
            "canceled": {cid: entry.get("reason") for cid, entry in sorted(executed.items())
                         if entry["status"] == "canceled"},
            "wars_settled": wars_settled,
            "negotiations": negotiations,
            "crisis": resolution,
            "crisis_round": crisis_record,
            "score_sum": round_half(
                sum(country.score for country in self.state.countries.values())),
        }

    # ── 谈判 ────────────────────────────────────────────────────────────
    def _run_negotiation(self, war: War, negotiations: list[dict[str, Any]]) -> bool:
        # Only match policies committed before reveal. Never call a country model here.
        defender = self.packages[war.defender_id]["negotiation"][war.war_id]
        attacker = self.packages[war.attacker_id]["negotiation"][war.war_id]
        context = {"offers": rules.negotiation_offers(self.state, war)}
        offer, accepted = match_negotiation(self.negotiation_mode, defender, attacker, context)
        # Thresholds promise effective receipts, not nominal amounts that may
        # disappear at the capability ceiling. Match against this settlement's
        # current capacity, after earlier nuclear/peace transfers.
        effective = {key: min(float(delta), max(0.0, float(R.SCORING["stat_ceiling"]) - self.state.countries[war.attacker_id].stat(key)))
                     for key, delta in offer["changes"]["attacker"].items()}
        if self.negotiation_mode == "self_offer" and accepted:
            accepted = (sum(effective.values()) >= attacker["min_total_gain"]
                        and all(effective[key] >= attacker["min_gains"][key] for key in STAT_KEYS))
        record = {"war_id": war.war_id, "mode": self.negotiation_mode,
                  "effective_gains": effective,
                  "decision_protocol": DECISION_PROTOCOL, "defender": offer,
                  "attacker": "accept" if accepted else "reject", "accepted": accepted}
        negotiations.append(record)
        if not accepted:
            return False
        rules.validate_negotiation_offer(offer)
        for key, delta in offer["changes"]["attacker"].items():
            if delta > 0:
                self._transfer(war.defender_id, war.attacker_id, key, float(delta))
        war.end(war.defender_id, "和平谈判")
        return True

    # ── 随机与事件 ──────────────────────────────────────────────────────
    def _kill(self, executed: dict[str, dict[str, Any]]) -> list[str]:
        dead = self.state.kill_if_needed()
        for cid in dead:
            self._event(f"{cid} 因 C/E/M 出现负值退出模拟", "death")
            self._mark_dead_actions(executed)
        return dead

    def _mark_dead_actions(self, executed: dict[str, dict[str, Any]]) -> None:
        for cid in sorted(self.actions):
            if (not self.state.countries[cid].alive
                    and cid not in executed):
                executed[cid] = {"action": self.actions[cid],
                                 "status": "canceled", "reason": "行动前已死亡"}

    def _add_pending(self, country_id: str, key: str, delta: float) -> None:
        changes = self._pending_stats.setdefault(country_id, {})
        changes[key] = round_half(changes.get(key, 0.0) + delta)

    def _apply_pending(self, executed: dict[str, dict[str, Any]]) -> list[str]:
        """把本批次 C/E/M 变化先加总，再截断到上限 5，然后检查死亡。"""
        for country_id, changes in sorted(self._pending_stats.items()):
            country = self.state.countries.get(country_id)
            if country is None or not country.alive:
                continue
            for key, delta in changes.items():
                value = round_half(country.stat(key) + delta)
                if value > R.SCORING["stat_ceiling"]:
                    value = R.SCORING["stat_ceiling"]
                setattr(country, key, value)
        self._pending_stats = {}
        return self._kill(executed)

    def _change_random_stat(self, country_id: str, delta: float) -> None:
        country = self.state.countries.get(country_id)
        if country is None or not country.alive:
            return
        pool = list(STAT_KEYS)
        chosen = self.settle_rng.choice(pool)
        self._add_pending(country_id, chosen, delta)
        self.rng_draws.append({
            "round": self.state.round, "kind": "stat", "country_id": country_id,
            "pool": pool, "chosen": chosen, "delta": delta,
        })

    def _change_two_stats(self, country_id: str, delta: float) -> None:
        country = self.state.countries.get(country_id)
        if country is None or not country.alive:
            return
        pool = list(STAT_KEYS)
        first = self.settle_rng.choice(pool)
        remaining = [key for key in pool if key != first]
        second = self.settle_rng.choice(remaining)
        self._add_pending(country_id, first, delta)
        self._add_pending(country_id, second, delta)
        self.rng_draws.append({
            "round": self.state.round, "kind": "two_stats", "country_id": country_id,
            "pool": pool, "chosen": [first, second], "delta": delta,
        })

    def _damage(self, country_id: str, delta: float) -> None:
        self._change_random_stat(country_id, delta)

    def _find_war(self, snapshot: State, a: str, b: str) -> War | None:
        selected = rules.select_war(snapshot, a, b)
        if selected is None:
            return None
        return self.state.wars.get(selected.war_id)

    def _event(self, text: str, type_: str) -> None:
        event = self.state.add_event(self.state.round, type_, text, "public")
        self._round_events.append(event)

    # ── 结束与环境摘要 ──────────────────────────────────────────────────
    def _check_end(self) -> dict[str, Any] | None:
        alive = self.state.alive_countries()
        if len(alive) == 1:
            return {"winner": alive[0].country_id, "reason": "仅剩一国"}
        if not alive:
            return {"winner": None, "reason": "均死亡"}
        ranked = sorted(alive, key=lambda c: c.score, reverse=True)
        if ranked[0].score > ranked[1].score + R.SCORING["victory_margin_strictly_greater_than"]:
            return {"winner": ranked[0].country_id,
                    "reason": f"领先第二名超过 {R.SCORING['victory_margin_strictly_greater_than']} 点"}
        if self.state.round >= self.state.max_rounds:
            return {"winner": None, "reason": "回合上限未决"}
        return None

    def _make_env_summary(self, round_no: int,
                          settlement: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if str(round_no) in self.env_summaries:
            return self.env_summaries[str(round_no)]
        if self.replay_mode and str(round_no) in self.env_failures:
            return None
        if self.replay_mode:
            raise ValueError(f"回放缺少第 {round_no} 轮环境摘要或失败记录")
        public_events = [event.public_dict() for event in self._round_events]
        public_state = self.state.to_public_state()
        if self.world_context:
            public_state["world_context"] = copy.deepcopy(self.world_context)
        public_declarations = [
            {"id": f"d{round_no}_{cid}", "country_id": cid,
             **self._public_decision(decision)}
            for cid, decision in sorted(self.declarations.items())
        ]
        executed = (settlement or {}).get("executed") or {}
        public_actions = [
            {"id": f"a{round_no}_{cid}", "country_id": cid,
             **self._public_decision(action)}
            for cid, action in sorted(self.actions.items())
            if executed.get(cid) == "executed"
        ]
        valid_source_ids = ({"state"}
                            | ({"world_context"} if self.world_context else set())
                            | set(self.world_context_source_ids)
                            | {event["id"] for event in public_events}
                            | {item["id"] for item in public_declarations}
                            | {item["id"] for item in public_actions})
        system, user = build_env_prompts(
            round_no, public_events, public_state, self.state.env_summary,
            public_declarations, public_actions)
        record = {"round": round_no, "phase": "environment", "country_id": None,
                  "attempts": [], "adopted": None,
                  "system": system if self.log_prompts else None,
                  "user": user if self.log_prompts else None}
        # 允许中断时保留已完成尝试。
        self.records.append(record)
        feedback = ""
        args = (round_no, public_events, public_state, self.state.env_summary,
                public_declarations, public_actions)
        for attempt in range(1, 3):
            raw = None
            try:
                if attempt > 1 and callable(getattr(self.provider, "summarize_with_feedback", None)):
                    raw = self.provider.summarize_with_feedback(*args, feedback)
                else:
                    raw = self.provider.summarize(*args)
                summary = validate_env_summary(raw, round_no, valid_source_ids)
            except (ProviderError, ResponseError, ValueError) as exc:
                feedback = str(exc)
                record["attempts"].append({
                    "attempt": attempt, "type": "failed", "error": feedback, "raw": raw,
                    "call_info": dict(getattr(self.provider, "last_call_info", {}) or {}),
                })
                continue
            record["attempts"].append({
                "attempt": attempt, "type": "ok", "raw": raw,
                "call_info": dict(getattr(self.provider, "last_call_info", {}) or {}),
            })
            # 自由生成文本可能把声明升级成已执行事件；只将引擎构造的
            # 结构化事实注入下一轮，模型摘要保留在日志供研究者审阅。
            record["model_summary"] = summary
            facts = []
            for event in public_events[:2]:
                facts.append({"text": event["text"][:60], "source_ids": [event["id"]]})
            for label, items in (("实际行动", public_actions), ("公开宣称", public_declarations)):
                for offset in range(0, len(items), 3):
                    if len(facts) >= 6:
                        break
                    group = items[offset:offset + 3]
                    facts.append({"text": label + "：" + "；".join(
                        f"{item['country_id']}→{item['target_country_id']} {item['action_type']}"
                        for item in group), "source_ids": [item["id"] for item in group]})
            if not facts:
                facts = [{"text": "本轮公开状态快照可供查看", "source_ids": ["state"]}]
            adopted = {"round": round_no, "facts": facts, "interpretations": [], "open_questions": []}
            record["adopted"] = adopted
            return adopted
        return None

    # ── 输出 ────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        evaluations = [entry for item in self.round_logs for entry in item.get("forecast_evaluation", [])]
        metrics = {"eligible": sum(e["eligible"] for e in evaluations),
                   "excluded_fallback": sum(not e["eligible"] for e in evaluations)}
        for field in ("action_hit", "target_hit", "joint_hit"):
            metrics[field + "s"] = sum(bool(e.get(field)) for e in evaluations if e["eligible"])
        return {
            "schema_version": SCHEMA_VERSION,
            "decision_protocol": DECISION_PROTOCOL,
            "failure_policy": FAILURE_POLICY,
            "forecast_metrics": metrics,
            "fallback_count": sum(origin == "fallback" for item in self.round_logs for origin in item.get("origins", {}).values()),
            "max_workers": self.max_workers, "max_retries": self.max_retries,
            "engine_version": ENGINE_VERSION,
            "rules_version": R.RULES_VERSION,
            "prompt_version": PROMPT_VERSION,
            "scenario_id": self.scenario.get("scenario_id"),
            "scenario": self.scenario,
            "scenario_hash": scenario_hash(self.scenario),
            "seed": self.seed,
            "option_order_seed": self.seed ^ 0x9E3779B9,
            "provider": getattr(self.provider, "name", "none"),
            "negotiation_mode": self.negotiation_mode,
            "model": getattr(self.provider, "model", "none"),
            "system_hash": self.system_hash,
            "rule_card_hash": self.rule_card_hash,
            "code_hashes": code_hashes(),
            "result": self.result or {"winner": None, "reason": "未结束"},
            "final_state": self.state.to_dict(),
            "rounds": self.round_logs,
            "records": self.records,
            "rng_draws": self.rng_draws,
            "decisions": self._export_decisions(),
            "failed_keys": sorted(self._failed_keys()),
            "env_summaries": {str(item["round"]): item["environment_summary"]
                              for item in self.round_logs
                              if item["environment_summary"] is not None},
            "env_failures": [str(item["round"]) for item in self.round_logs
                             if item["environment_summary"] is None],
            "score_sum": round_half(
                sum(country.score for country in self.state.countries.values())),
        }

    def _failed_keys(self) -> set[str]:
        keys: set[str] = set()
        for record in self.records:
            if record.get("adopted") is None and record.get("country_id"):
                keys.add(record["key"])
        return keys

    def _export_decisions(self) -> dict[str, Any]:
        if self.replay_mode:
            return copy.deepcopy(self.decisions)
        return {record["key"]: {"package": copy.deepcopy(record["adopted"]), "origin": record["origin"]}
                for record in self.records if record["phase"] == "sealed" and record.get("adopted")}

    @classmethod
    def replay(cls, run_data: dict[str, Any],
               provider: Provider | None = None) -> tuple["Engine", bool, list[str]]:
        if run_data.get("decision_protocol") != DECISION_PROTOCOL or run_data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("旧协议运行记录只能展示；不能按 sealed_v1 重新结算并声称精确回放")
        problems: list[str] = []
        scenario = run_data.get("scenario")
        if not isinstance(scenario, dict):
            raise ValueError("run.json 缺少 scenario 快照，无法回放")
        if "final_state" not in run_data:
            problems.append("缺少 final_state，无法核对最终状态")

        engine = cls(
            scenario,
            provider=provider,
            seed=int(run_data["seed"]),
            decisions=run_data.get("decisions", {}),
            env_summaries=run_data.get("env_summaries", {}),
            failed_keys=set(run_data.get("failed_keys", [])),
            env_failures=set(run_data.get("env_failures", [])),
            negotiation_mode=run_data.get("negotiation_mode"),
            log_prompts=False,
            max_workers=run_data.get("max_workers", 7),
            max_retries=run_data.get("max_retries", 3),
        )
        saved_rounds = run_data.get("rounds") or []
        incomplete = run_data.get("run_status") in ("running", "failed", "interrupted")
        try:
            engine.run(max_completed_rounds=len(saved_rounds) if incomplete else None)
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            problems.append(f"回放无法重现已保存的决定：{exc}")
            return engine, False, problems

        for key, current in (
            ("schema_version", SCHEMA_VERSION),
            ("decision_protocol", DECISION_PROTOCOL),
            ("failure_policy", FAILURE_POLICY),
            ("engine_version", ENGINE_VERSION),
            ("rules_version", R.RULES_VERSION),
            ("prompt_version", PROMPT_VERSION),
            ("negotiation_mode", engine.negotiation_mode),
            ("scenario_hash", scenario_hash(engine.scenario)),
        ):
            if run_data.get(key) != current:
                problems.append(f"{key} 不一致：保存 {run_data.get(key)!r}，当前 {current!r}")

        if run_data.get("rule_card_hash") != engine.rule_card_hash:
            problems.append("规则卡哈希不一致：保存 "
                            f"{run_data.get('rule_card_hash')!r}，"
                            f"当前 {engine.rule_card_hash!r}")
        saved_hashes = run_data.get("code_hashes")
        current_hashes = code_hashes()
        if not saved_hashes:
            problems.append("缺少 code_hashes，无法核对代码与提示词版本")
        else:
            changed = [name for name, digest in current_hashes.items()
                       if saved_hashes.get(name) != digest]
            if changed:
                problems.append("代码或提示词文件已变化：" + "、".join(changed))

        actual_result = engine.result or {"winner": None, "reason": "未结束"}
        if run_data.get("result") != actual_result:
            problems.append(f"最终结果不一致：保存 {run_data.get('result')!r}，"
                            f"当前 {actual_result!r}")
        if run_data.get("rng_draws") != engine.rng_draws:
            problems.append("随机抽签记录不一致")

        if len(saved_rounds) != len(engine.round_logs):
            problems.append(f"轮数不一致：保存 {len(saved_rounds)}，当前 {len(engine.round_logs)}")
        for index, (saved, actual) in enumerate(zip(saved_rounds, engine.round_logs)):
            for field in ("round", "snapshot", "declarations", "actions",
                          "settlement", "events", "state_after",
                          "environment_summary", "option_orders", "end_check", "origins", "forecast_evaluation"):
                if saved.get(field) != actual.get(field):
                    problems.append(f"第 {actual.get('round', index + 1)} 轮的 {field} 不一致")
            if engine.threat_observation_enabled and saved.get("private_threat_observations") != actual.get("private_threat_observations"):
                problems.append(f"第 {actual.get('round', index + 1)} 轮的 private_threat_observations 不一致")

        if "final_state" in run_data and run_data["final_state"] != engine.state.to_dict():
            problems.append("最终状态与保存记录不一致")
        return engine, not problems, problems
