import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch, Mock

from strategic_game.web_desktop import DesktopApi, _clean_countries, _frontend_path, _configured_key
from strategic_game.call_summaries import summarize_calls


class WebDesktopTests(unittest.TestCase):
    def test_default_config_is_safe_and_uses_eight_country_scenario(self):
        config = DesktopApi().get_config()
        self.assertEqual(len(config["countries"]), 8)
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertNotIn("global_presence", config["countries"]["C1"])
        self.assertNotIn("strategic_culture_knowledge", config["countries"]["C1"])

    def test_frontend_entry_has_stable_name(self):
        self.assertEqual(_frontend_path().name, "index.html")

    def test_key_is_loaded_from_bundled_env_and_environment_takes_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".env").write_text('DEEPSEEK_API_KEY="test-file-key"\n', encoding="utf-8")
            with patch("strategic_game.web_desktop.sys._MEIPASS", folder, create=True), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(_configured_key("deepseek"), "test-file-key")
                self.assertTrue(DesktopApi().get_config()["deepseek_key_configured"])
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-env-key"}):
                    self.assertEqual(_configured_key("deepseek"), "test-env-key")

    def test_start_uses_configured_key_and_manual_key_overrides_it(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), \
                patch("strategic_game.web_desktop._configured_key", return_value="configured-test-key"), \
                patch.object(api, "_launch") as launch:
            payload = api.get_config()
            payload["output_parent"] = folder
            for supplied, expected in (("", "configured-test-key"), ("manual-test-key", "manual-test-key")):
                payload["api_key"] = supplied
                api.start_run(payload)
                self.assertEqual(launch.call_args.args[2], expected)
                Path(api.scenario_path).unlink()

    def test_api_key_is_never_returned_by_status_or_config(self):
        api = DesktopApi()
        self.assertNotIn("api_key", api.get_config())
        self.assertNotIn("api_key", api.get_status())

    def test_call_summaries_distinguish_fallback_and_redact_sensitive_text(self):
        data = {"rounds": [{"round": 1}], "records": [
            {"round": 1, "country_id": "C1", "attempt": 1, "status": "failed", "error": "secret key", "response": "private output"},
            {"round": 1, "country_id": "C2", "attempt": 1, "status": "completed", "user": "private prompt"},
            {"round": 2, "country_id": "C1", "attempt": 1, "status": "failed", "error": "private data"},
        ]}
        rows = summarize_calls(data)
        self.assertEqual([row["fallback"] for row in rows], [True, False, None])
        self.assertNotIn("secret", json.dumps(rows))
        self.assertNotIn("private", json.dumps(rows))

    def test_history_view_summarizes_calls_without_original_response(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "run.json"
            run.write_text(json.dumps({"rules_version": "v6.0", "rounds": [{"round": 1}],
                "records": [{"round": 1, "country_id": "C1", "attempt": 1, "status": "failed",
                             "response": "private response", "error": "secret key"}]}), encoding="utf-8")
            api.open_run(str(run))
            view = api.get_run_view()
            self.assertTrue(view["call_summaries"][0]["fallback"])
            self.assertNotIn("secret", json.dumps(view))
            self.assertNotIn("private response", json.dumps(view))

    def test_status_is_lightweight_and_run_view_is_revisioned(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder:
            api.run_file = Path(folder) / "run.json"
            api.run_file.write_text(json.dumps({"run_status":"running", "initial_state":{"countries":{}},
                                                "rounds":[{"round":1,"decisions":{},"state":{}}]}), encoding="utf-8")
            api._refresh_view()
            status = api.get_status()
            self.assertNotIn("data", status)
            self.assertEqual(status["view_revision"], 1)
            self.assertEqual(api.get_run_view()["rounds"][0]["round"], 1)

    def test_history_loads_settlement_and_distinguishes_final_status(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "example" / "run.json"
            run.parent.mkdir()
            run.write_text(json.dumps({"rules_version": "v6.0", "run_status": "completed_with_errors",
                "initial_state": {"countries": {"C1": {"name": "历史国家"}}},
                "rounds": [{"round": 1, "settlement": {"actions": ["example"]}}]}), encoding="utf-8")
            history = api.list_runs(folder)
            self.assertEqual(len(history), 1)
            self.assertFalse(history[0]["resumable"])
            status = api.open_run(history[0]["path"])
            self.assertEqual(status["phase"], "completed_with_errors")
            view = api.get_run_view()
            self.assertEqual(view["initial_state"]["countries"]["C1"]["name"], "历史国家")
            self.assertEqual(view["rounds"][0]["settlement"]["actions"], ["example"])

    def test_progress_and_stopped_are_distinct_from_failure(self):
        api = DesktopApi()
        api._update_progress("[第 2 轮] C1 尝试 1/3：正在请求模型")
        api._update_progress("[第 2 轮] C1 尝试 1/3：失败（超时）")
        api._update_progress("[第 2 轮] C2 尝试 1/3：决策已通过校验")
        self.assertEqual(api._progress["countries"]["C1"]["status"], "failed")
        self.assertEqual(api._progress["countries"]["C2"]["status"], "completed")
        api.process = Mock(returncode=-15)
        api.process.poll.return_value = -15
        self.assertEqual(api.get_status()["phase"], "failed")
        api._stop_requested = True
        self.assertEqual(api.get_status()["phase"], "stopped")

    def test_failed_resume_does_not_misreport_old_completion(self):
        api = DesktopApi()
        api.process = Mock(returncode=1)
        api.process.poll.return_value = 1
        api._view_cache = {"run_status": "completed_with_errors"}
        self.assertEqual(api.get_status()["phase"], "failed")
        api.logs.append("完成：run-directory\n")
        self.assertEqual(api.get_status()["phase"], "completed_with_errors")

    def test_new_operation_waits_for_log_consumer(self):
        api = DesktopApi()
        api.process = Mock(returncode=0)
        api.process.poll.return_value = 0
        api._consumer_active = True
        self.assertTrue(api.get_status()["running"])
        with self.assertRaisesRegex(RuntimeError, "已有推演"):
            api.start_run({})
        with self.assertRaisesRegex(RuntimeError, "停止当前推演"):
            api.open_run("unused")

    def test_completed_record_rejected_before_launch_and_malformed_history_ignored(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder:
            complete = Path(folder) / "complete" / "run.json"
            complete.parent.mkdir()
            complete.write_text(json.dumps({"rules_version": "v6.0", "run_status": "completed", "rounds": []}), encoding="utf-8")
            invalid = Path(folder) / "bad" / "run.json"
            invalid.parent.mkdir()
            invalid.write_text("[]", encoding="utf-8")
            self.assertEqual(len(api.list_runs(folder)), 1)
            with patch.object(api, "_launch") as launch:
                with self.assertRaisesRegex(ValueError, "已经完成"):
                    api.resume_run({"path": str(complete)})
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
