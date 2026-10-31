"""Tk desktop front end for configuring and observing strategic simulations."""
from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import protocol, runtime


CULTURES = ("国际主义", "民族主义", "进步主义", "现实主义")
COUNTRY_FIELDS = ("name", "strategic_culture", "ideology_focus", "economic_presence",
                  "military_presence", "soft_presence", "conventional", "nuclear")


def bundled_scenario() -> dict:
    path = Path(__file__).parent / "scenarios" / "eight_nations_dawn_strait.json"
    return json.loads(path.read_text(encoding="utf-8"))


def make_scenario(countries: dict, world_context: str, rounds: int) -> dict:
    scenario = {
        "rules_version": "v6.0",
        "scenario_id": "gui_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "name": "桌面端自定义场景",
        "world_context": world_context,
        "max_rounds": rounds,
        "countries": copy.deepcopy(countries),
        "conflicts": {}, "threat_history": {}, "nuclear_used": [],
    }
    return protocol.validate_scenario(scenario)


class QueueWriter:
    def __init__(self, sink: queue.Queue):
        self.sink = sink

    def write(self, text):
        if text:
            self.sink.put(("log", text))

    def flush(self):
        pass


class CountryDialog(tk.Toplevel):
    def __init__(self, parent, country_id: str, country: dict, existing_ids: set[str]):
        super().__init__(parent)
        self.title("国家设置")
        self.resizable(True, True)
        self.result = None
        self.existing_ids = existing_ids - {country_id}
        self.vars = {"country_id": tk.StringVar(value=country_id)}
        for key in COUNTRY_FIELDS:
            self.vars[key] = tk.StringVar(value=str(country.get(key, "")))
        frame = ttk.Frame(self, padding=12); frame.pack(fill="both", expand=True)
        labels = (("country_id", "国家 ID"), ("name", "名称"),
                  ("strategic_culture", "战略文化"), ("ideology_focus", "意识形态 1-10"),
                  ("economic_presence", "经济存在 0-100"), ("military_presence", "军事存在 0-100"),
                  ("soft_presence", "软实力存在 0-100"), ("conventional", "常规力量 0-100"),
                  ("nuclear", "核力量 0-100"))
        for row, (key, label) in enumerate(labels):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if key == "strategic_culture":
                widget = ttk.Combobox(frame, textvariable=self.vars[key], values=CULTURES, state="readonly")
            else:
                widget = ttk.Entry(frame, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=3)
        row = len(labels)
        ttk.Label(frame, text="国家画像（本国私有）").grid(row=row, column=0, sticky="nw", pady=3)
        self.profile = tk.Text(frame, height=5, wrap="word"); self.profile.grid(row=row, column=1, sticky="nsew", pady=3)
        self.profile.insert("1.0", country.get("profile", ""))
        row += 1
        ttk.Label(frame, text="利益攸关方 ID（逗号分隔）").grid(row=row, column=0, sticky="w", pady=3)
        stakeholders = country.get("tags", {}).get("stakeholders", {})
        values = sorted(set(stakeholders.get("allies", []) + stakeholders.get("adversaries", []) + stakeholders.get("others", [])))
        self.stakeholders = ttk.Entry(frame); self.stakeholders.grid(row=row, column=1, sticky="ew", pady=3)
        self.stakeholders.insert(0, ",".join(values))
        buttons = ttk.Frame(frame); buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.accept).pack(side="right", padx=8)
        frame.columnconfigure(1, weight=1); frame.rowconfigure(len(labels), weight=1)
        self.transient(parent); self.grab_set(); self.protocol("WM_DELETE_WINDOW", self.destroy)

    def accept(self):
        cid = self.vars["country_id"].get().strip()
        if not cid or cid in self.existing_ids:
            messagebox.showerror("国家设置", "国家 ID 不能为空且不能重复", parent=self); return
        try:
            country = {
                "name": self.vars["name"].get().strip(),
                "strategic_culture": self.vars["strategic_culture"].get(),
                "ideology_focus": int(self.vars["ideology_focus"].get()),
                "economic_presence": float(self.vars["economic_presence"].get()),
                "military_presence": float(self.vars["military_presence"].get()),
                "soft_presence": float(self.vars["soft_presence"].get()),
                "conventional": float(self.vars["conventional"].get()),
                "nuclear": float(self.vars["nuclear"].get()),
                "alive": True,
                "profile": self.profile.get("1.0", "end").strip(),
            }
        except ValueError:
            messagebox.showerror("国家设置", "数值字段格式不正确", parent=self); return
        ids = [x.strip() for x in self.stakeholders.get().split(",") if x.strip()]
        if ids:
            country["tags"] = {"stakeholders": {"others": ids}}
        self.result = (cid, country)
        self.destroy()


class StrategicGameApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Strategic Multi-Agent Game")
        self.geometry("1380x860")
        self.minsize(1050, 680)
        self.events = queue.Queue()
        self.process = None
        self.run_file = None
        self.seen_rounds = 0
        self.countries = {}
        self._build()
        self.load_scenario_data(bundled_scenario())
        self.after(150, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        root = ttk.Panedwindow(self, orient="vertical"); root.pack(fill="both", expand=True)
        config = ttk.Frame(root, padding=8); root.add(config, weight=2)
        output = ttk.Panedwindow(root, orient="horizontal"); root.add(output, weight=5)

        scenario_box = ttk.LabelFrame(config, text="场景与国家", padding=8)
        scenario_box.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.country_tree = ttk.Treeview(scenario_box, columns=("name", "culture", "ideology", "presence", "forces"), show="headings", height=8)
        for key, title, width in (("name", "国家", 150), ("culture", "战略文化", 85), ("ideology", "意识形态", 70),
                                  ("presence", "存在 E/M/S", 130), ("forces", "常规/核", 100)):
            self.country_tree.heading(key, text=title); self.country_tree.column(key, width=width, anchor="center")
        self.country_tree.pack(fill="both", expand=True)
        self.country_tree.bind("<Double-1>", lambda _: self.edit_country())
        country_buttons = ttk.Frame(scenario_box); country_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(country_buttons, text="添加", command=self.add_country).pack(side="left")
        ttk.Button(country_buttons, text="编辑", command=self.edit_country).pack(side="left", padx=5)
        ttk.Button(country_buttons, text="删除", command=self.remove_country).pack(side="left")
        ttk.Button(country_buttons, text="载入场景", command=self.load_scenario_file).pack(side="right")

        settings = ttk.LabelFrame(config, text="运行设置", padding=8); settings.pack(side="right", fill="y")
        self.provider = tk.StringVar(value="deepseek"); self.model = tk.StringVar(value="deepseek-flash")
        self.base_url = tk.StringVar(); self.api_key = tk.StringVar(); self.rounds = tk.IntVar(value=30)
        self.output_parent = tk.StringVar(value=str(Path.cwd() / "runs"))
        rows = (("服务", ttk.Combobox(settings, textvariable=self.provider, values=("deepseek", "openai"), state="readonly")),
                ("模型", ttk.Entry(settings, textvariable=self.model)),
                ("Base URL", ttk.Entry(settings, textvariable=self.base_url)),
                ("API Key", ttk.Entry(settings, textvariable=self.api_key, show="*")),
                ("轮数", ttk.Spinbox(settings, from_=1, to=30, textvariable=self.rounds)),
                ("输出目录", ttk.Entry(settings, textvariable=self.output_parent)))
        for row, (label, widget) in enumerate(rows):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.world_context = tk.Text(settings, width=48, height=5, wrap="word")
        ttk.Label(settings, text="世界背景").grid(row=len(rows), column=0, sticky="nw", pady=3)
        self.world_context.grid(row=len(rows), column=1, sticky="nsew", padx=(8, 0), pady=3)
        controls = ttk.Frame(settings); controls.grid(row=len(rows)+1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(controls, text="开始运行", command=self.start); self.start_button.pack(side="left", expand=True, fill="x")
        self.resume_button = ttk.Button(controls, text="断点续跑", command=self.resume); self.resume_button.pack(side="left", expand=True, fill="x", padx=(6, 0))
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop, state="disabled"); self.stop_button.pack(side="left", expand=True, fill="x", padx=(6, 0))
        settings.columnconfigure(1, weight=1); settings.rowconfigure(len(rows), weight=1)

        log_box = ttk.LabelFrame(output, text="运行进度", padding=6); output.add(log_box, weight=2)
        log_scroll = ttk.Scrollbar(log_box); log_scroll.pack(side="right", fill="y")
        self.log = tk.Text(log_box, width=42, wrap="word", state="disabled", yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True); log_scroll.configure(command=self.log.yview)
        reports = ttk.Notebook(output); output.add(reports, weight=5)
        decision_frame = ttk.Frame(reports); world_frame = ttk.Frame(reports)
        decision_scroll = ttk.Scrollbar(decision_frame); decision_scroll.pack(side="right", fill="y")
        self.decisions = tk.Text(decision_frame, wrap="word", state="disabled", yscrollcommand=decision_scroll.set)
        self.decisions.pack(side="left", fill="both", expand=True); decision_scroll.configure(command=self.decisions.yview)
        world_scroll = ttk.Scrollbar(world_frame); world_scroll.pack(side="right", fill="y")
        self.world = tk.Text(world_frame, wrap="none", state="disabled", yscrollcommand=world_scroll.set)
        self.world.pack(side="left", fill="both", expand=True); world_scroll.configure(command=self.world.yview)
        reports.add(decision_frame, text="国家决策（每轮统一开封后显示）"); reports.add(world_frame, text="世界状态变化")

    def load_scenario_data(self, data):
        validated = protocol.validate_scenario(data)
        self.countries = {cid: {k: copy.deepcopy(v) for k, v in country.items()
                                if k not in ("global_presence", "strategic_culture_knowledge", "ideology_focus_knowledge", "tag_knowledge")}
                          for cid, country in validated["countries"].items()}
        self.rounds.set(validated.get("max_rounds", 30))
        self.world_context.delete("1.0", "end"); self.world_context.insert("1.0", validated.get("world_context", ""))
        self.refresh_countries()

    def refresh_countries(self):
        self.country_tree.delete(*self.country_tree.get_children())
        for cid, c in self.countries.items():
            self.country_tree.insert("", "end", iid=cid, values=(f"{cid} · {c['name']}", c["strategic_culture"], c["ideology_focus"],
                                  f"{c['economic_presence']}/{c['military_presence']}/{c['soft_presence']}", f"{c['conventional']}/{c['nuclear']}"))

    def add_country(self):
        n = 1
        while f"C{n}" in self.countries: n += 1
        country = {"name": f"国家{n}", "strategic_culture": "现实主义", "ideology_focus": 5,
                   "economic_presence": 50, "military_presence": 50, "soft_presence": 50,
                   "conventional": 50, "nuclear": 0, "alive": True, "profile": ""}
        self._country_dialog(f"C{n}", country)

    def edit_country(self):
        selected = self.country_tree.selection()
        if selected: self._country_dialog(selected[0], self.countries[selected[0]])

    def _country_dialog(self, cid, country):
        dialog = CountryDialog(self, cid, country, set(self.countries)); self.wait_window(dialog)
        if dialog.result:
            new_id, value = dialog.result
            if new_id != cid: del self.countries[cid]
            self.countries[new_id] = value; self.refresh_countries()

    def remove_country(self):
        for cid in self.country_tree.selection(): self.countries.pop(cid, None)
        self.refresh_countries()

    def load_scenario_file(self):
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"), ("所有文件", "*")))
        if not path: return
        try: self.load_scenario_data(json.loads(Path(path).read_text(encoding="utf-8")))
        except Exception as exc: messagebox.showerror("载入失败", str(exc), parent=self)

    def start(self):
        if self.process is not None: return
        try:
            scenario = make_scenario(self.countries, self.world_context.get("1.0", "end").strip(), int(self.rounds.get()))
        except Exception as exc:
            messagebox.showerror("场景无效", str(exc), parent=self); return
        provider, model, key = self.provider.get(), self.model.get().strip(), self.api_key.get().strip()
        if not model or not key:
            messagebox.showerror("运行设置", "模型和 API Key 不能为空", parent=self); return
        parent = Path(self.output_parent.get()).expanduser(); parent.mkdir(parents=True, exist_ok=True)
        out = parent / ("gui-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        json.dump(scenario, handle, ensure_ascii=False, indent=2); handle.close()
        args = ["run", "--provider", provider, "--model", model, "--scenario", handle.name,
                "--rounds", str(self.rounds.get()), "--out", str(out)]
        if provider == "openai":
            if not self.base_url.get().strip():
                Path(handle.name).unlink(missing_ok=True); messagebox.showerror("运行设置", "OpenAI 兼容服务必须填写 Base URL", parent=self); return
            args += ["--base-url", self.base_url.get().strip()]
        self._begin(args, provider, key, out / "run.json", handle.name)

    def resume(self):
        selected = filedialog.askopenfilename(title="选择 run.json", filetypes=(("运行记录", "run.json"), ("JSON", "*.json")))
        if not selected: return
        provider, model, key = self.provider.get(), self.model.get().strip(), self.api_key.get().strip()
        if not model or not key:
            messagebox.showerror("运行设置", "模型和 API Key 不能为空", parent=self); return
        args = ["run", "--resume", selected, "--provider", provider, "--model", model]
        if provider == "openai":
            if not self.base_url.get().strip():
                messagebox.showerror("运行设置", "OpenAI 兼容服务必须填写 Base URL", parent=self); return
            args += ["--base-url", self.base_url.get().strip()]
        self._begin(args, provider, key, Path(selected), None)

    def _begin(self, args, provider, key, run_file, scenario_path):
        env = os.environ.copy(); env["DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"] = key
        self.run_file = run_file; self.seen_rounds = 0
        self._set_text(self.decisions, ""); self._set_text(self.world, ""); self._append_log(f"实验目录：{run_file.parent}\n")
        self.start_button.configure(state="disabled"); self.resume_button.configure(state="disabled"); self.stop_button.configure(state="normal")
        threading.Thread(target=self._launch, args=(args, env, scenario_path), daemon=True).start()

    def _launch(self, args, env, scenario_path):
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--cli-worker", *args]
        else:
            command = [sys.executable, "-m", "strategic_game.cli", *args]
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
            for line in self.process.stdout:
                self.events.put(("log", line))
            code = self.process.wait(); self.events.put(("finished", code))
        except Exception as exc:
            self.events.put(("log", f"启动失败：{exc}\n")); self.events.put(("finished", 1))
        finally:
            if scenario_path:
                Path(scenario_path).unlink(missing_ok=True)

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate(); self._append_log("正在停止，已完成回合仍保留在 run.json。\n")

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log": self._append_log(value)
                elif kind == "finished":
                    self._append_log(f"后台进程结束，退出码 {value}。\n")
                    self.process = None; self.start_button.configure(state="normal"); self.resume_button.configure(state="normal"); self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self._refresh_run()
        self.after(200, self._poll)

    def _refresh_run(self):
        if not self.run_file or not self.run_file.exists(): return
        try: data = json.loads(self.run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return
        rows = data.get("rounds", [])
        if len(rows) == self.seen_rounds: return
        self.seen_rounds = len(rows)
        decision_parts = []
        world_parts = []
        names = {cid: c["name"] for cid, c in data["initial_state"]["countries"].items()}
        for row in rows:
            decision_parts.append(f"第 {row['round']} 轮\n" + "=" * 40)
            for cid, decision in row["decisions"].items():
                decision_parts.append(f"\n[{cid} · {names.get(cid, cid)}]\n"
                                      f"现状评估：{decision.get('assessment','')}\n"
                                      f"局势预测：{decision.get('forecast','')}\n"
                                      f"公开声明：{decision.get('declaration','')}\n"
                                      f"行动：{json.dumps(decision.get('actions',[]), ensure_ascii=False)}\n"
                                      f"联盟与借调：{json.dumps({key: decision.get(key, []) for key in ('alliance_invites','alliance_responses','alliance_breaks','borrows','loan_responses','returns')}, ensure_ascii=False)}\n"
                                      f"私人理由：{decision.get('rationale','')}\n"
                                      f"威胁评估：{decision.get('threat_assessment','')}")
            world_parts.append(f"第 {row['round']} 轮状态变化\n" + "=" * 40 + "\n"
                               + json.dumps(row.get("state_changes", []), ensure_ascii=False, indent=2)
                               + "\n\n世界状态\n" + json.dumps(row["state"], ensure_ascii=False, indent=2))
        self._set_text(self.decisions, "\n".join(decision_parts))
        self._set_text(self.world, "\n\n".join(world_parts))

    def _append_log(self, text):
        self.log.configure(state="normal"); self.log.insert("end", text); self.log.see("end"); self.log.configure(state="disabled")

    @staticmethod
    def _set_text(widget, value):
        widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", value); widget.configure(state="disabled")

    def close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("退出", "模拟仍在运行，是否终止并退出？", parent=self): return
            self.process.terminate()
        self.destroy()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--cli-worker":
        from .cli import main as cli_main
        return cli_main(argv[1:])
    StrategicGameApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
