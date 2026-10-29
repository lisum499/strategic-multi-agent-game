import json
import unittest

from strategic_game.engine import Engine
from strategic_game.providers import DeepSeekProvider, Provider, ProviderError
from strategic_game.validation import validate_env_summary


SCENARIO = {"scenario_id": "test", "max_rounds": 1, "countries": [
    {"country_id": "A", "c": 3, "e": 3, "m": 3},
    {"country_id": "B", "c": 3, "e": 3, "m": 3},
]}


def response(ids):
    return json.dumps({"round": 1, "facts": [{"text": "当前公开信息", "source_ids": ids}],
                       "interpretations": [], "open_questions": []}, ensure_ascii=False)


class RetryProvider(Provider):
    def __init__(self, first, second):
        self.first, self.second, self.feedback = first, second, None

    def summarize(self, *args):
        if isinstance(self.first, Exception):
            raise self.first
        return self.first

    def summarize_with_feedback(self, *args):
        self.feedback = args[-1]
        if isinstance(self.second, Exception):
            raise self.second
        return self.second


class EnvironmentRetryTest(unittest.TestCase):
    def test_peace_statement_does_not_become_executed_negotiation(self):
        raw = json.dumps({"round": 1, "facts": [{"text": "A 已执行磋商",
                          "source_ids": ["a1_A"]}], "interpretations": [],
                          "open_questions": []}, ensure_ascii=False)
        engine = Engine(SCENARIO, RetryProvider(raw, raw))
        engine.actions = {"A": {"action_type": "peace", "target_country_id": "B",
                                "public_statement": "愿意磋商"}}
        result = engine._make_env_summary(1, {"executed": {"A": "executed"}})
        self.assertNotIn("磋商", json.dumps(result, ensure_ascii=False))
        self.assertIn("peace", result["facts"][0]["text"])
        self.assertEqual(engine.records[-1]["model_summary"]["facts"][0]["text"], "A 已执行磋商")

    def test_invalid_source_count_retries_without_silent_repair(self):
        raw = response(["state"] * 9)
        provider = RetryProvider(raw, response(["state"]))
        engine = Engine(SCENARIO, provider)
        result = engine._make_env_summary(1)
        self.assertEqual(result["facts"][0]["source_ids"], ["state"])
        record = engine.records[-1]
        self.assertEqual([a["type"] for a in record["attempts"]], ["failed", "ok"])
        self.assertEqual(record["attempts"][0]["raw"], raw)
        self.assertIn("source_ids", provider.feedback)

    def test_aggregate_statement_with_seven_sources_is_accepted(self):
        """回归：聚合陈述（如"七国均…"）曾因 source_ids>3 被硬判失败。"""
        ids = [f"d1_C{index}" for index in range(1, 8)]
        raw = json.dumps({"round": 1, "facts": [{"text": "七国均宣称和平",
                          "source_ids": ids}], "interpretations": [],
                          "open_questions": []}, ensure_ascii=False)
        summary = validate_env_summary(raw, 1, set(ids))
        self.assertEqual(summary["facts"][0]["source_ids"], ids)

    def test_env_summary_ignores_extra_fields(self):
        raw = json.dumps({"round": 1, "facts": [{"text": "当前公开信息",
                          "source_ids": ["state"], "confidence": 0.9}],
                          "interpretations": [], "open_questions": [],
                          "notes": "额外字段"}, ensure_ascii=False)
        summary = validate_env_summary(raw, 1, {"state"})
        self.assertEqual(summary["facts"][0], {"text": "当前公开信息",
                                               "source_ids": ["state"]})

    def test_provider_error_retries_once_and_records_both_failures(self):
        provider = RetryProvider(ProviderError("服务失败"), ProviderError("再次失败"))
        engine = Engine(SCENARIO, provider)
        self.assertIsNone(engine._make_env_summary(1))
        attempts = engine.records[-1]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["error"], "服务失败")
        self.assertEqual(attempts[1]["error"], "再次失败")

    def test_deepseek_retry_receives_error_without_external_call(self):
        provider = DeepSeekProvider(api_key="test")
        seen = []
        provider._chat = lambda system, user, max_tokens: seen.append(user) or response(["state"])
        provider.summarize_with_feedback(1, [], {}, None, [], [], "source_ids 超限")
        self.assertIn("source_ids 超限", seen[0])
        self.assertIn("最多 8 个", seen[0])


if __name__ == "__main__":
    unittest.main()
