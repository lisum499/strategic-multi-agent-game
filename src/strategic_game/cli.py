"""Command-line runner for the strategic multi-agent game."""

import argparse
import json
from pathlib import Path

from strategic_game.agents import LocalModelCountryAgent
from strategic_game.agents import PeacefulMockCountryAgent
from strategic_game.game import GameResult
from strategic_game.game import StrategicGame
from strategic_game.model_client import OpenAICompatibleLanguageModel
from strategic_game.models import RoundReport
from strategic_game.scenario import load_scenario


DEFAULT_SCENARIO = Path("scenarios/default_three_states.json")


def _print_report(report: RoundReport) -> None:
  actions = ", ".join(
      f"{name}={action.action.value}"
      for name, action in sorted(report.executions.items())
  )
  scores = ", ".join(
      f"{name}={score:g}" for name, score in sorted(report.scores.items())
  )
  print(f"第 {report.round} 轮: {actions} | 分数: {scores}")


def _serialize(result: GameResult, *, private: bool) -> dict[str, object]:
  data: dict[str, object] = {
      "scenario": result.scenario,
      "reports": [item.model_dump(mode="json") for item in result.reports],
      "final_world": result.final_world.model_dump(mode="json"),
      "termination_reason": result.termination_reason,
  }
  if private:
    data["private_decisions"] = [
        {
            name: decision.model_dump(mode="json")
            for name, decision in round_decisions.items()
        }
        for round_decisions in result.private_decisions
    ]
  return data


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
  parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
  parser.add_argument("--model", default="qwen3-4b-local")
  parser.add_argument("--api-key", default="local")
  parser.add_argument("--rounds", type=int)
  parser.add_argument("--seed", type=int, default=42)
  parser.add_argument("--temperature", type=float, default=0.4)
  parser.add_argument("--max-retries", type=int, default=3)
  parser.add_argument("--stalemate-rounds", type=int, default=12)
  parser.add_argument("--mock", action="store_true")
  parser.add_argument("--output-dir", type=Path, default=Path("output"))
  args = parser.parse_args()

  scenario = load_scenario(args.scenario)
  if args.mock:
    agents = {
        country.name: PeacefulMockCountryAgent()
        for country in scenario.countries
    }
  else:
    model = OpenAICompatibleLanguageModel(
        args.model, args.base_url, args.api_key
    )
    agents = {
        country.name: LocalModelCountryAgent(
            model,
            seed=args.seed + index * 1000,
            temperature=args.temperature,
        )
        for index, country in enumerate(scenario.countries)
    }

  result = StrategicGame(
      scenario=scenario,
      agents=agents,
      seed=args.seed,
      max_retries=args.max_retries,
      stalemate_rounds=args.stalemate_rounds,
      on_report=_print_report,
  ).play(max_rounds=args.rounds)

  args.output_dir.mkdir(parents=True, exist_ok=True)
  for filename, private in (("public.json", False), ("private.json", True)):
    (args.output_dir / filename).write_text(
        json.dumps(_serialize(result, private=private), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
  print(f"终止原因: {result.termination_reason}")


if __name__ == "__main__":
  main()
