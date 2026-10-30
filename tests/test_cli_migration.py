"""Exercise saved output, legacy entry syntax, and installed wheel resources."""

import contextlib
import io
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from strategic_game import cli
from strategic_game.providers import MockProvider, ProviderError


ROOT = Path(__file__).resolve().parents[1]


class BrokenProvider(MockProvider):
    def decide(self, *args, **kwargs):
        raise ProviderError("synthetic model outage")


class BrokenSummaryProvider(MockProvider):
    def summarize(self, *args, **kwargs):
        raise ProviderError("synthetic summary outage")


class CliMigrationTest(unittest.TestCase):
    def invoke(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if arguments[0] != "replay":
                arguments = (["run", "--rules-version", "v4"] + arguments[1:] if arguments[0] == "run" else ["run", "--rules-version", "v4"] + arguments)
            status = cli.main(arguments)
        return status, output.getvalue()

    def test_run_saves_new_records_public_export_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            status, log = self.invoke(["run", "--provider", "mock", "--rounds", "2", "--out", str(output)])
            self.assertEqual(status, 0, log)
            record = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["decision_protocol"], "sealed_v1")
            self.assertEqual(record["fallback_count"], 0)
            for filename in ("report.html", "report.md", "public.json"):
                self.assertTrue((output / filename).is_file(), filename)
            status, log = self.invoke(["replay", str(output / "run.json")])
            self.assertEqual(status, 0, log)

    def test_legacy_mock_entry_can_load_original_three_states(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            status, log = self.invoke(["--mock", "--rounds", "1", "--scenario", "default_three_states.json",
                                       "--output-dir", str(output)])
            self.assertEqual(status, 0, log)
            record = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["scenario"]["migration"]["country_name_to_id"]["Alpha"], "C1")
            self.assertEqual(len(record["final_state"]["countries"]), 3)

    def test_default_peace_is_completed_with_errors_and_can_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with patch.object(cli, "get_provider", return_value=BrokenProvider()):
                status, log = self.invoke(["run", "--provider", "mock", "--rounds", "1",
                                           "--retries", "0", "--out", str(output)])
            self.assertEqual(status, 1, log)
            record = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["run_status"], "completed_with_errors")
            self.assertEqual(record["fallback_count"], 7)
            self.assertEqual(self.invoke(["replay", str(output / "run.json")])[0], 0)

    def test_legacy_run_can_be_shown_but_not_replayed_as_sealed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps({"reports": [], "termination_reason": "stalemate"}), encoding="utf-8")
            self.assertEqual(self.invoke(["replay", str(path), "--show-only"])[0], 0)
            self.assertEqual(self.invoke(["replay", str(path)])[0], 1)

    def test_summary_failure_marks_completed_with_errors_without_peace_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with patch.object(cli, "get_provider", return_value=BrokenSummaryProvider()):
                status, log = self.invoke(["run", "--provider", "mock", "--rounds", "1",
                                           "--out", str(output)])
            self.assertEqual(status, 1, log)
            record = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["run_status"], "completed_with_errors")
            self.assertEqual(record["fallback_count"], 0)
            self.assertEqual(record["env_failures"], ["1"])
            self.assertEqual(self.invoke(["replay", str(output / "run.json")])[0], 0)

    def test_invalid_worker_count_fails_before_output(self):
        status, log = self.invoke(["run", "--mock", "--max-workers", "0"])
        self.assertEqual(status, 1)
        self.assertIn("max-workers", log)

    def test_built_wheel_resources_load_outside_checkout(self):
        # Build an isolated copy so test execution leaves no build metadata in
        # the checkout and proves the installed layout includes data files.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
            shutil.copytree(ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            wheels = root / "wheels"
            result = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                                     "--no-index", "--wheel-dir", str(wheels), str(source)],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            install = root / "installed"
            with zipfile.ZipFile(next(wheels.glob("*.whl"))) as wheel:
                wheel.extractall(install)
            environment = {**os.environ, "PYTHONPATH": str(install), "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run([sys.executable, "-c",
                "from strategic_game.scenario import load_scenario, DEFAULT_SCENARIO; "
                "from pathlib import Path; import strategic_game; "
                "s=load_scenario(DEFAULT_SCENARIO); assert len(s['countries'])==7; "
                "assert Path(strategic_game.__file__).parent.joinpath('knowledge/label_dictionary.json').is_file(); "
                "assert Path(strategic_game.__file__).parent.joinpath('prompts/v2.1/30-env-summary.md').is_file()"],
                cwd=root, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
