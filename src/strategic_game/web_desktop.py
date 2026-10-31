"""pywebview bridge for the React desktop workbench."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from . import protocol, runtime

DERIVED = {"global_presence", "strategic_culture_knowledge", "ideology_focus_knowledge", "tag_knowledge"}


def _clean_countries(countries):
    return {cid: {key: copy.deepcopy(value) for key, value in country.items() if key not in DERIVED}
            for cid, country in countries.items()}


def _frontend_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / "web_dist" / "index.html"
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    return path


class DesktopApi:
    def __init__(self):
        self.process = None
        self.run_file = None
        self.scenario_path = None
        self.logs = []
        self._lock = threading.Lock()

    def get_config(self):
        scenario_path = Path(__file__).parent / "scenarios" / "eight_nations_dawn_strait.json"
        scenario = protocol.validate_scenario(json.loads(scenario_path.read_text(encoding="utf-8")))
        return {"countries": _clean_countries(scenario["countries"]),
                "world_context": scenario.get("world_context", ""), "rounds": scenario.get("max_rounds", 30),
                "provider": "deepseek", "model": "deepseek-flash", "base_url": "",
                "output_parent": str(Path.cwd() / "runs")}

    def _view(self):
        if not self.run_file or not self.run_file.exists():
            return None
        try:
            data = json.loads(self.run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return {"run_status": data.get("run_status"), "result": data.get("result"),
                "initial_state": {"countries": data.get("initial_state", {}).get("countries", {})},
                "rounds": [{key: row.get(key) for key in ("round", "decisions", "state", "state_changes", "public")}
                           for row in data.get("rounds", [])]}

    def get_status(self):
        with self._lock:
            running = self.process is not None and self.process.poll() is None
            code = None if running or self.process is None else self.process.returncode
            status = "运行中" if running else ("就绪" if self.process is None else ("已完成" if code == 0 else "已停止"))
            return {"running": running, "status": status, "logs": self.logs[-1200:],
                    "run_file": str(self.run_file) if self.run_file else None, "data": self._view()}

    def _command(self, args):
        return [sys.executable, "--cli-worker", *args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "strategic_game.cli", *args]

    def _launch(self, args, provider, api_key):
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"] = api_key
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(self._command(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", env=env,
                                        creationflags=flags, bufsize=1)
        threading.Thread(target=self._consume, daemon=True).start()

    def _consume(self):
        try:
            for line in self.process.stdout:
                with self._lock:
                    self.logs.append(line)
        finally:
            self.process.wait()
            if self.scenario_path:
                Path(self.scenario_path).unlink(missing_ok=True)
                self.scenario_path = None

    def start_run(self, payload):
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("已有推演正在运行")
        provider, model, key = payload.get("provider"), str(payload.get("model", "")).strip(), str(payload.get("api_key", "")).strip()
        if provider not in ("deepseek", "openai") or not model or not key:
            raise ValueError("服务、模型和 API Key 必须填写")
        rounds = int(payload.get("rounds", 30))
        base_url = str(payload.get("base_url", "")).strip()
        if provider == "openai" and not base_url:
            raise ValueError("OpenAI 兼容服务必须填写 Base URL")
        scenario = {"rules_version": "v6.0", "scenario_id": "web_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
                    "name": "桌面端自定义场景", "world_context": payload.get("world_context", ""),
                    "max_rounds": rounds, "countries": _clean_countries(payload.get("countries", {})),
                    "conflicts": {}, "threat_history": {}, "nuclear_used": []}
        protocol.validate_scenario(copy.deepcopy(scenario))
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        json.dump(scenario, handle, ensure_ascii=False, indent=2); handle.close()
        self.scenario_path = handle.name
        parent = Path(payload.get("output_parent") or Path.cwd() / "runs").expanduser()
        parent.mkdir(parents=True, exist_ok=True)
        out = parent / ("workbench-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.run_file = out / "run.json"
        args = ["run", "--provider", provider, "--model", model, "--scenario", handle.name,
                "--rounds", str(rounds), "--out", str(out)]
        if provider == "openai":
            args += ["--base-url", base_url]
        with self._lock:
            self.logs = []
        self._launch(args, provider, key)
        return self.get_status()

    def resume_run(self, payload):
        import webview
        chosen = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=("运行记录 (*.json)",))
        if not chosen:
            return self.get_status()
        path = Path(chosen[0])
        provider, model, key = payload.get("provider"), payload.get("model"), payload.get("api_key")
        args = ["run", "--resume", str(path), "--provider", provider, "--model", model]
        if provider == "openai":
            args += ["--base-url", payload.get("base_url", "")]
        self.run_file = path
        with self._lock:
            self.logs = []
        self._launch(args, provider, key)
        return self.get_status()

    def stop_run(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        return {"ok": True}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--cli-worker":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        from .cli import main as cli_main
        return cli_main(argv[1:])
    import webview
    api = DesktopApi()
    window = webview.create_window("战略多智能体推演", _frontend_path().as_uri(), js_api=api,
                                   width=1440, height=900, min_size=(1080, 700), background_color="#f5f6f8")
    window.events.closing += lambda: api.stop_run()
    webview.start(debug=False)
    return 0
