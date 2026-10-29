"""Country decision agents backed by a local model or deterministic mock."""

from __future__ import annotations

from collections.abc import Sequence
import json
import re
import threading
from typing import Protocol

from pydantic import ValidationError

from strategic_game.model_client import OpenAICompatibleLanguageModel
from strategic_game.models import Action
from strategic_game.models import ActionType
from strategic_game.models import DecisionPackage
from strategic_game.models import Confidence
from strategic_game.models import Forecast
from strategic_game.models import RoundReport
from strategic_game.models import WorldState
from strategic_game.scenario import GameScenario


class CountryAgent(Protocol):

  def decide(
      self,
      *,
      country_name: str,
      scenario: GameScenario,
      world: WorldState,
      public_history: Sequence[RoundReport],
      error_feedback: str | None = None,
  ) -> DecisionPackage:
    """Return one sealed decision without seeing same-round submissions."""


class LocalModelCountryAgent:
  """Generate and validate a decision package using an OpenAI-compatible LLM."""

  def __init__(
      self,
      model: OpenAICompatibleLanguageModel,
      *,
      seed: int,
      temperature: float = 0.4,
  ) -> None:
    self._model = model
    self._seed = seed
    self._temperature = temperature
    self._attempts: dict[int, int] = {}
    self._attempt_lock = threading.Lock()

  def decide(
      self,
      *,
      country_name: str,
      scenario: GameScenario,
      world: WorldState,
      public_history: Sequence[RoundReport],
      error_feedback: str | None = None,
  ) -> DecisionPackage:
    prompt = build_country_prompt(
        country_name=country_name,
        scenario=scenario,
        world=world,
        public_history=public_history,
        error_feedback=error_feedback,
    )
    with self._attempt_lock:
      attempt = self._attempts.get(world.round, 0)
      self._attempts[world.round] = attempt + 1
    raw = self._model.sample_text(
        prompt,
        max_tokens=1400,
        temperature=self._temperature,
        top_p=0.9,
        seed=self._seed + world.round + attempt * 101,
    )
    try:
      return DecisionPackage.model_validate(_extract_json_object(raw))
    except ValidationError as exc:
      errors = "; ".join(
          f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
          for error in exc.errors()
      )
      raise ValueError(f"invalid decision fields: {errors}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
      raise ValueError(f"invalid decision JSON: {exc}") from exc


class PeacefulMockCountryAgent:
  """Deterministic agent used to exercise the full pipeline without a model."""

  def decide(
      self,
      *,
      country_name: str,
      scenario: GameScenario,
      world: WorldState,
      public_history: Sequence[RoundReport],
      error_feedback: str | None = None,
  ) -> DecisionPackage:
    del scenario, public_history, error_feedback
    targets = [
        name
        for name, country in sorted(world.countries.items())
        if name != country_name and country.alive
    ]
    if not targets:
      raise ValueError("no other living country remains")
    action = Action(
        action=ActionType.CALL_FOR_PEACE,
        target_state=targets[0],
        public_reason="呼吁通过谈判降低本轮冲突风险。",
    )
    forecasts = [
        Forecast(
            state=target,
            predicted_action=ActionType.CALL_FOR_PEACE,
            predicted_target=country_name,
            confidence=Confidence.LOW,
            miscalculation_risk="缺少同轮信息，预测可能错误。",
        )
        for target in targets
    ]
    return DecisionPackage(
        round=world.round,
        forecast=forecasts,
        declaration=action,
        execution=action,
        private_rationale="维持和平最符合当前国家利益。",
    )


def build_country_prompt(
    *,
    country_name: str,
    scenario: GameScenario,
    world: WorldState,
    public_history: Sequence[RoundReport],
    error_feedback: str | None,
) -> str:
  profile = scenario.profile_for(country_name)
  country = world.countries[country_name]
  public_state = {
      "round": world.round,
      "countries": {
          name: {
              "alive": state.alive,
              "C": state.c,
              "E": state.e,
              "M": state.m,
              "reputation": state.reputation,
              "credibility": state.credibility,
              "nuclear_status": state.nuclear_status,
              "score": state.victory_score,
          }
          for name, state in world.countries.items()
      },
      "active_wars": [
          war.model_dump(mode="json")
          for war in world.wars.values()
          if war.active
      ],
  }
  recent_history = [
      {
          "round": report.round,
          "declarations": {
              name: action.model_dump(mode="json")
              for name, action in report.declarations.items()
          },
          "executions": {
              name: action.model_dump(mode="json")
              for name, action in report.executions.items()
          },
          "events": [event.model_dump(mode="json") for event in report.events],
          "scores": report.scores,
          "deaths": report.deaths,
      }
      for report in public_history[-3:]
  ]
  legal_notes = _legal_action_notes(country_name, world)
  valid_targets = sorted(
      name
      for name, state in world.countries.items()
      if name != country_name and state.alive
  )
  repair = (
      f"\n上次提交被拒绝，仅修正以下错误：{error_feedback}\n"
      if error_feedback
      else ""
  )
  return f"""你是国家 {country_name} 的决策智能体。你不是主持人，不能修改规则或自行宣布结算。

本局：{scenario.name}
场景：{scenario.description}
体系：{scenario.polarity}；{scenario.system_environment}

本国稳定设定：
{json.dumps(profile.model_dump(mode='json'), ensure_ascii=False)}

本国核射程目标：{sorted(country.nuclear_reach)}
本轮公开状态：
{json.dumps(public_state, ensure_ascii=False)}

截至第 {world.round - 1} 轮的公开历史（最多3轮）：
{json.dumps(recent_history, ensure_ascii=False, separators=(',', ':'))}

硬规则：
1. 你看不到其他国家本轮提交，必须独立预测后一次性密封提交。
2. declaration 与 execution 可类型不同，但 target_state、target_war_id、support_side 必须完全一致。
3. 第一轮不得宣称或执行 nuclear_strike。
4. conventional_war/nuclear_strike 需要 target_state；intervene 需要现存 target_war_id 和 support_side；call_for_peace 需要国家或战争目标。
5. 只能使用：conventional_war、nuclear_strike、intervene、call_for_peace。
6. 不要输出思维链；private_rationale 只写简短决策依据。
7. {legal_notes}
8. 宣称和行动一致可信度+1；允许的不一致只有：战争/核武/干涉→和平(-1)，干涉→战争(-1)，和平→战争/干涉(-2)，战争/干涉→核武(-2)，和平→核武(-3)。
9. 当前可用国家目标只有：{valid_targets}。选择 call_for_peace 时，target_state 绝对不能为 null；没有目标战争时 target_war_id 才写 null。
10. forecast 必须对每个仍存活的其他国家各写且只写一条；state 与 predicted_target 都只能使用存活国家，不能预测已经出局的国家。
{repair}
只输出一个JSON对象，不要Markdown代码围栏，不要增加字段。按以下字段结构填写；尖括号表示你必须根据局势选择的值，不得原样输出：
{{
  "round": {world.round},
  "forecast": [{{"state":"<每个存活对手>","predicted_action":"<行动>","predicted_target":"<存活国家>","confidence":"<high|medium|low>","miscalculation_risk":"<简述>"}}],
  "declaration": {{"action":"<行动>","target_state":"<国家或null>","target_war_id":"<战争ID或null>","support_side":"<参战方或null>","public_reason":"<公开理由>","peace_terms":null}},
  "execution": {{"action":"<行动>","target_state":"<与宣称相同>","target_war_id":"<与宣称相同>","support_side":"<与宣称相同>","public_reason":null,"peace_terms":null}},
  "private_rationale":"简短私有理由"
}}
"""


def _legal_action_notes(country_name: str, world: WorldState) -> str:
  country = world.countries[country_name]
  notes: list[str] = []
  active_wars = world.active_wars_for(country_name)
  if active_wars:
    notes.append("本国已是交战方，不能再选择 conventional_war")
    for war in active_wars:
      if war.defender == country_name:
        notes.append(f"作为 {war.id} 防守方，只能作一般和平呼吁且不会获得声誉")
      else:
        notes.append(
            f"作为 {war.id} 进攻方，可指定该战争呼吁和平以撤军，或作不增加声誉的一般呼吁"
        )
  if world.round == 1 or country.nuclear_status == "none" or country.m < 4:
    notes.append("本轮不能选择 nuclear_strike")
  active_ids = [war.id for war in world.wars.values() if war.active]
  if not active_ids:
    notes.append("当前没有可供 intervene 的战争")
  else:
    notes.append(f"当前活动战争ID：{active_ids}")
  return "；".join(notes) or "按当前状态选择合法行动"


def _extract_json_object(raw: str) -> dict[str, object]:
  text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
  start = text.find("{")
  if start < 0:
    raise ValueError("response contains no JSON object")
  value, _ = json.JSONDecoder().raw_decode(text[start:])
  if not isinstance(value, dict):
    raise ValueError("response JSON must be an object")
  return value
