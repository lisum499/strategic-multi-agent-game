"""pywebview bridge for the React desktop workbench."""
from __future__ import annotations

import copy
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from . import protocol, runtime
from .call_summaries import read_live_summaries, summarize_calls

DERIVED = {"global_presence", "strategic_culture_knowledge", "ideology_focus_knowledge", "tag_knowledge"}


def _serialized_action(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._action_lock:
            return method(self, *args, **kwargs)
    return wrapped


def _clean_countries(countries):
    return {cid: {key: copy.deepcopy(value) for key, value in country.items() if key not in DERIVED}
            for cid, country in countries.items()}


def _frontend_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = root / "web_dist" / "index.html"
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    return path


def _configured_key(provider):
    """Read a local or bundled credential without exposing it to the webview."""
    name = "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    if os.environ.get(name):
        return os.environ[name].strip()
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    try:
        for line in (root / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == name:
                return value.strip().strip('"\'')
    except FileNotFoundError:
        pass
    return ""


class DesktopApi:
    def __init__(self):
        self.process = None
        self.run_file = None
        self.scenario_path = None
        self.logs = []
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._consumer_active = False
        self._view_cache = None
        self._view_revision = 0
        self._status_revision = 0
        self._stop_requested = False
        self._progress = {"round": None, "countries": {}}

    def get_config(self):
        scenario_path = Path(__file__).parent / "scenarios" / "eight_nations_dawn_strait.json"
        scenario = protocol.validate_scenario(json.loads(scenario_path.read_text(encoding="utf-8")))
        return {"countries": _clean_countries(scenario["countries"]),
                "world_context": scenario.get("world_context", ""), "rounds": scenario.get("max_rounds", 30),
                "provider": "deepseek", "model": "deepseek-flash", "base_url": "",
                "deepseek_key_configured": bool(_configured_key("deepseek")),
                "output_parent": str(Path.cwd() / "runs")}

    def _view(self):
        if not self.run_file or not self.run_file.exists():
            return None
        try:
            live_file = self.run_file.with_name("live.json")
            running = self.process is not None and self.process.poll() is None
            data = json.loads((live_file if running and live_file.exists() else self.run_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        # Existing run.json records contain complete prompts and model responses.
        # Always regenerate the strictly limited summary when reading a full record.
        summaries = summarize_calls(data) if "records" in data else read_live_summaries(data)
        return {"run_status": data.get("run_status"), "result": data.get("result"),
                "call_summaries": summaries,
                "initial_state": {key: data.get("initial_state", {}).get(key) for key in ("name", "world_context", "countries")},
                "rounds": [{key: row.get(key) for key in ("round", "decisions", "state", "state_changes", "public", "settlement")}
                           for row in data.get("rounds", [])]}

    def _refresh_view(self):
        view = self._view()
        with self._lock:
            self._view_cache = view
            self._view_revision += 1
            self._status_revision += 1

    def get_run_view(self):
        with self._lock:
            return copy.deepcopy(self._view_cache)

    def get_status(self):
        with self._lock:
            running = self._consumer_active or (self.process is not None and self.process.poll() is None)
            code = None if running or self.process is None else self.process.returncode
            recorded = (self._view_cache or {}).get("run_status")
            if running:
                phase = "running"
            elif self._stop_requested:
                phase = "completed" if code == 0 and recorded == "completed" else "stopped"
            elif self.process is None:
                phase = {"interrupted": "stopped", "running": "unfinished"}.get(recorded, recorded or "idle")
            elif code == 0:
                phase = "completed"
            elif recorded == "completed_with_errors" and any(line.startswith("完成：") for line in self.logs):
                phase = "completed_with_errors"
            else:
                phase = "failed"
            status = {"idle": "就绪", "running": "运行中", "completed": "已完成",
                      "completed_with_errors": "完成（有错误）", "failed": "运行失败", "stopped": "已停止",
                      "unfinished": "未完成（运行状态未知）"}.get(phase, str(phase))
            return {"running": running, "status": status, "logs": self.logs[-300:],
                    "run_file": str(self.run_file) if self.run_file else None,
                    "phase": phase, "progress": copy.deepcopy(self._progress),
                    "revision": self._status_revision, "view_revision": self._view_revision}

    def _update_progress(self, line):
        match = re.search(r"\[第 (\d+) 轮\] (\S+) 尝试 (\d+)/(\d+)：(.*)", line)
        if not match:
            return
        round_no, country, attempt, total, text = match.groups()
        round_no = int(round_no)
        if self._progress["round"] != round_no:
            self._progress = {"round": round_no, "countries": {}}
        status = "started" if "正在请求模型" in text else ("completed" if "通过校验" in text else "failed")
        self._progress["countries"][country] = {"status": status, "attempt": int(attempt), "max_attempts": int(total)}

    def list_runs(self, output_parent):
        parent = Path(output_parent or Path.cwd() / "runs").expanduser()
        if not parent.is_dir():
            return []
        result = []
        for path in parent.glob("*/run.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("rules_version") != "v6.0" or not isinstance(data.get("rounds"), list):
                    continue
                status = data.get("run_status", "unknown")
                result.append({"path": str(path.resolve()), "name": path.parent.name,
                               "status": status, "rounds": len(data["rounds"]),
                               "modified": path.stat().st_mtime,
                               "resumable": status not in ("completed", "completed_with_errors")})
            except (OSError, ValueError, TypeError):
                continue
        return sorted(result, key=lambda item: item["modified"], reverse=True)

    @_serialized_action
    def open_run(self, path):
        if self._consumer_active or (self.process is not None and self.process.poll() is None):
            raise RuntimeError("请先停止当前推演，再打开历史记录")
        selected = Path(path).expanduser()
        try:
            data = json.loads(selected.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("无法读取运行记录，请选择有效的 run.json") from exc
        if not isinstance(data, dict) or data.get("rules_version") != "v6.0" or not isinstance(data.get("rounds"), list):
            raise ValueError("仅支持打开 v6.0 运行记录")
        self.process = None
        self.run_file = selected
        self._stop_requested = False
        with self._lock:
            self.logs = []
            self._progress = {"round": None, "countries": {}}
        self._refresh_view()
        return self.get_status()

    def _command(self, args):
        return [sys.executable, "--cli-worker", *args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "strategic_game.cli", *args]

    def _launch(self, args, provider, api_key):
        env = os.environ.copy()
        env["DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"] = api_key
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(self._command(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", env=env,
                                   creationflags=flags, bufsize=1)
        self.process = process
        with self._lock:
            self._consumer_active = True
            self._status_revision += 1
        scenario_path = self.scenario_path
        threading.Thread(target=self._consume, args=(process, scenario_path), daemon=True).start()

    def _consume(self, process, scenario_path):
        try:
            for line in process.stdout:
                with self._lock:
                    self.logs.append(line)
                    self._update_progress(line)
                    if len(self.logs) > 500:
                        del self.logs[:-500]
                    self._status_revision += 1
                if "结算完成；下一状态" in line or re.search(r"\[第 \d+ 轮\] \S+ 尝试 \d+/\d+：", line):
                    self._refresh_view()
        finally:
            try:
                process.wait()
                self._refresh_view()
                if scenario_path:
                    Path(scenario_path).unlink(missing_ok=True)
                    if self.scenario_path == scenario_path:
                        self.scenario_path = None
            finally:
                with self._lock:
                    self._consumer_active = False
                    self._status_revision += 1

    @_serialized_action
    def start_run(self, payload):
        if self._consumer_active or (self.process is not None and self.process.poll() is None):
            raise RuntimeError("已有推演正在运行")
        provider, model = payload.get("provider"), str(payload.get("model", "")).strip()
        key = str(payload.get("api_key") or "").strip() or _configured_key(provider)
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
        parent = Path(payload.get("output_parent") or Path.cwd() / "runs").expanduser()
        parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        json.dump(scenario, handle, ensure_ascii=False, indent=2); handle.close()
        self.scenario_path = handle.name
        out = parent / ("workbench-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        self.run_file = out / "run.json"
        args = ["run", "--provider", provider, "--model", model, "--scenario", handle.name,
                "--rounds", str(rounds), "--out", str(out)]
        if provider == "openai":
            args += ["--base-url", base_url]
        with self._lock:
            self.logs = []
            self._stop_requested = False
            self._progress = {"round": None, "countries": {}}
            self._view_cache = None
            self._view_revision += 1
            self._status_revision += 1
        try:
            self._launch(args, provider, key)
        except Exception:
            Path(self.scenario_path).unlink(missing_ok=True)
            self.scenario_path = None
            raise
        return self.get_status()

    @_serialized_action
    def resume_run(self, payload):
        if self._consumer_active or (self.process is not None and self.process.poll() is None):
            raise RuntimeError("已有推演正在运行")
        selected = payload.get("path")
        if not selected:
            import webview
            chosen = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=("运行记录 (*.json)",))
            if not chosen:
                return self.get_status()
            selected = chosen[0]
        path = Path(selected).expanduser()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("无法读取运行记录，请选择有效的 run.json") from exc
        if not isinstance(data, dict) or data.get("rules_version") != "v6.0" or not isinstance(data.get("rounds"), list):
            raise ValueError("仅支持续跑 v6.0 运行记录")
        if data.get("run_status") in ("completed", "completed_with_errors"):
            raise ValueError("该实验已经完成，不能续跑")
        provider, model = payload.get("provider"), payload.get("model")
        key = str(payload.get("api_key") or "").strip() or _configured_key(provider)
        if provider not in ("deepseek", "openai") or not model or not key:
            raise ValueError("服务、模型和 API Key 必须填写")
        if provider == "openai" and not str(payload.get("base_url") or "").strip():
            raise ValueError("OpenAI 兼容服务必须填写 Base URL")
        args = ["run", "--resume", str(path), "--provider", provider, "--model", model]
        if provider == "openai":
            args += ["--base-url", payload.get("base_url", "")]
        self.run_file = path
        self._refresh_view()
        with self._lock:
            self.logs = []
            self._stop_requested = False
            self._progress = {"round": None, "countries": {}}
            self._status_revision += 1
        self._launch(args, provider, key)
        return self.get_status()

    @_serialized_action
    def stop_run(self):
        if self.process is not None and self.process.poll() is None:
            with self._lock:
                self._stop_requested = True
                self._status_revision += 1
            try:
                self.process.terminate()
            except ProcessLookupError:
                with self._lock:
                    self._stop_requested = False
                    self._status_revision += 1
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
                                   width=1440, height=900, min_size=(760, 600), background_color="#f5f6f8")
    window.events.closing += lambda: api.stop_run()
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
