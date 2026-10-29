"""Exercise provider request/response handling with an in-memory HTTP transport."""

from __future__ import annotations

import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError

from strategic_game.providers import (
    DeepSeekProvider, OpenAICompatibleProvider, ProviderError, get_provider,
)


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def payload(*, model="served-model", content='{"ok": true}', finish="stop"):
    return {
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18,
                  "ignored_sensitive_field": "must not be logged"},
    }


class ProviderTransportTest(unittest.TestCase):
    def test_openai_compatible_request_uses_selected_url_without_deepseek_extensions(self):
        provider = get_provider("openai", model="local-qwen",
                                base_url="http://127.0.0.1:9123/v1/",
                                api_key="local-test-key", max_tokens=2345)
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        with patch("strategic_game.providers.urllib.request.urlopen",
                   return_value=Response(payload())) as transport:
            self.assertEqual(provider.decide("sealed", "system", "user", {}),
                             '{"ok": true}')
        request = transport.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:9123/v1/chat/completions")
        self.assertEqual(body["model"], "local-qwen")
        self.assertEqual(body["max_tokens"], 2345)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("thinking", body)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        info = provider.last_call_info
        self.assertEqual(info["returned_model"], "served-model")
        self.assertEqual(info["usage"]["total_tokens"], 18)
        self.assertNotIn("ignored_sensitive_field", info["usage"])
        self.assertNotIn("local-test-key", json.dumps(info))

    def test_deepseek_request_preserves_backend_specific_non_thinking_mode(self):
        provider = DeepSeekProvider(api_key="deepseek-test-key", model="deepseek-flash")
        with patch("strategic_game.providers.urllib.request.urlopen",
                   return_value=Response(payload())) as transport:
            provider.decide("sealed", "system", "user", {})
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(json.loads(request.data)["thinking"], {"type": "disabled"})
        self.assertNotIn("deepseek-test-key", json.dumps(provider.last_call_info))

    def test_truncated_output_is_failure_even_when_content_is_valid_json(self):
        provider = OpenAICompatibleProvider()
        with patch("strategic_game.providers.urllib.request.urlopen",
                   return_value=Response(payload(finish="length"))):
            with self.assertRaisesRegex(ProviderError, "length"):
                provider.decide("sealed", "system", "user", {})
        self.assertEqual(provider.last_call_info["finish_reason"], "length")

    def test_backend_error_does_not_expose_response_body_or_key(self):
        provider = OpenAICompatibleProvider(api_key="secret-key")
        error = HTTPError(provider.url, 503, "private server error detail", {}, None)
        with patch("strategic_game.providers.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderError) as caught:
                provider.decide("sealed", "system", "user", {})
        self.assertIn("503", str(caught.exception))
        self.assertNotIn("private server", str(caught.exception))
        self.assertNotIn("secret-key", str(caught.exception))

    def test_concurrent_calls_keep_metadata_in_the_calling_thread(self):
        provider = OpenAICompatibleProvider()
        barrier = threading.Barrier(2)

        def transport(request, timeout):
            body = json.loads(request.data)
            label = body["messages"][-1]["content"]
            barrier.wait(timeout=2)
            return Response(payload(model=f"served-{label}", content=label))

        def invoke(label):
            text = provider.decide("sealed", "system", label, {})
            return text, copy.deepcopy(provider.last_call_info)

        with patch("strategic_game.providers.urllib.request.urlopen", side_effect=transport):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(invoke, ("A", "B")))
        for label, info in results:
            self.assertEqual(info["returned_model"], f"served-{label}")
            self.assertEqual(info["finish_reason"], "stop")
        self.assertEqual(provider.last_call_info, {})

    def test_invalid_endpoint_and_model_configuration_fail_before_transport(self):
        for url in ("", "ftp://example.test/v1", "relative/path", None):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                OpenAICompatibleProvider(base_url=url)
        for settings in ({"model": ""}, {"max_tokens": 0}, {"temperature": float("nan")},
                         {"timeout": 0}):
            with self.subTest(settings=settings), self.assertRaises(ProviderError):
                OpenAICompatibleProvider(**settings)


if __name__ == "__main__":
    unittest.main()
