#!/usr/bin/env python3
"""Command-line entry point for the sealed strategic game."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .providers import ProviderError, get_provider

# Runtime output belongs to the working directory, never site-packages.
RUNS_DIR = Path("runs")


def _atomic_write(path: Path, content: str) -> None:
    """同目录临时文件加原子替换，避免中断留下半份 JSON。"""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".checkpoint-", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _model_settings(provider) -> dict:
    settings = {"kind": provider.name, "model": provider.model}
    for field in ("temperature", "max_tokens", "timeout", "base_url"):
        if hasattr(provider, field):
            settings[field] = getattr(provider, field)
    return settings


def cmd_run_v6(args: argparse.Namespace) -> int:
    from . import runtime as runtime, protocol as protocol, rules as rules
    if args.retries < 0:
        raise ValueError("retries 必须 ≥0")
    resumed = args.resume is not None
    if resumed:
        if args.scenario or args.out or args.rounds is not None:
            raise ValueError("--resume 不能与 --scenario、--out 或 --rounds 同时使用")
        resume_path = Path(args.resume)
        if resume_path.is_dir():
            resume_path = resume_path / "run.json"
        data = json.loads(resume_path.read_text(encoding="utf-8"))
        if data.get("rules_version") != "v6.0":
            raise ValueError("只能续跑 v6.0 记录")
        if data.get("run_status") in ("completed", "completed_with_errors"):
            raise ValueError("该实验已经完成，不能续跑")
        if data.get("source_fingerprint") != runtime.fingerprint():
            raise ValueError("规则代码与原运行不一致，不能续跑")
        config = data.get("rule_config")
        if data.get("config_fingerprint") != runtime.config_fingerprint(config):
            raise ValueError("运行参数快照已改变，不能续跑")
        run_dir = resume_path.parent
        state = copy.deepcopy(data["final_state"])
        history = [copy.deepcopy(row["public"]) for row in data.get("rounds", [])]
        stored_settings = data.get("model_settings", {})
        name = "mock" if args.mock else (args.provider or stored_settings.get("kind", "mock"))
    else:
        scenario_path = args.scenario or runtime.DEFAULT_SCENARIO
        scenario = protocol.validate_scenario(json.loads(Path(scenario_path).read_text(encoding="utf-8")))
        if args.rounds is not None:
            if not 1 <= args.rounds <= 30: raise ValueError("v6 rounds 必须在 1 到 30 之间")
            scenario["max_rounds"] = args.rounds
        state = copy.deepcopy(scenario); state["round"] = 1
        config = rules._config(scenario.get("rule_config")); config["max_rounds"] = scenario["max_rounds"]
        history = []
        stored_settings = {}
        name = "mock" if args.mock else (args.provider or ("openai" if args.base_url else "mock"))
    if args.mock and args.provider not in (None, "mock"): raise ValueError("--mock 不能与真实模型同时使用")
    explicit_model_args = {k:v for k,v in {
        "model": args.model, "temperature": args.temperature, "max_tokens": args.max_tokens,
        "timeout": args.timeout, "base_url": args.base_url, "api_key": args.api_key,
    }.items() if v is not None}
    supplied = {k:v for k,v in {
        "model": args.model if args.model is not None else stored_settings.get("model"),
        "temperature": args.temperature if args.temperature is not None else stored_settings.get("temperature"),
        "max_tokens": args.max_tokens if args.max_tokens is not None else stored_settings.get("max_tokens"),
        "timeout": args.timeout if args.timeout is not None else stored_settings.get("timeout"),
        "base_url": args.base_url if args.base_url is not None else stored_settings.get("base_url"),
        "api_key": args.api_key,
    }.items() if v is not None}
    if name == "mock":
        if explicit_model_args: raise ValueError("mock 不接受模型参数")
        supplied = {}
    if name == "deepseek" and args.base_url: raise ValueError("自定义 base-url 请使用 openai")
    provider = get_provider(name, **supplied)
    if resumed:
        data["run_status"] = "running"
        data.pop("error_type", None); data.pop("error_message", None)
        data.setdefault("resume_events", []).append({"resumed_at": datetime.now().isoformat(timespec="seconds"), "from_round": state["round"]})
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir = Path(args.out) if args.out else RUNS_DIR / f"{stamp}-v6-{name}"
        run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        data = {"rules_version":"v6.0","decision_protocol":"v6_sealed","source_fingerprint":runtime.fingerprint(),"config_fingerprint":runtime.config_fingerprint(config),"rule_config":config,"initial_state":copy.deepcopy(state),"rounds":[],"records":[],"resume_events":[],"run_status":"running","model_settings":_model_settings(provider),"fallback_count":0,"final_state":copy.deepcopy(state),"result":{}}
    def save():
        _atomic_write(run_dir / "run.json", json.dumps(data, ensure_ascii=False, indent=2))
    save(); print(f"{'续跑' if resumed else '实验'}目录：{run_dir}", flush=True)
    try:
        while not state.get("outcome", {}).get("finished"):
            decisions, calls, fallbacks = runtime.decisions_for(state, provider, history, config, args.retries, args.no_prompts, calls_sink=data["records"], on_call=save)
            data["fallback_count"] += fallbacks
            next_state, settlement = runtime.advance(state, decisions, config)
            data["fallback_count"] += len(settlement.get("settlement_fallbacks", []))
            public = runtime.public_round(settlement, decisions, next_state)
            data["rounds"].append({"round":settlement["round"],"decisions":decisions,"settlement":settlement,
                                   "state_changes":runtime.state_changes(state, next_state),
                                   "state":copy.deepcopy(next_state),"public":public})
            state = next_state; history.append(public)
            data["final_state"] = copy.deepcopy(state); data["result"] = copy.deepcopy(state.get("outcome", {})); save()
        data["run_status"] = "completed_with_errors" if data["fallback_count"] else "completed"
    except KeyboardInterrupt:
        data["run_status"] = "interrupted"; data["error_type"] = "KeyboardInterrupt"
    except Exception as exc:
        data["run_status"] = "failed"; data["error_type"] = type(exc).__name__; data["error_message"] = str(exc)[:500] if isinstance(exc, ValueError) else ""
    save()
    if data["run_status"] in ("completed", "completed_with_errors"):
        md, page = runtime.reports(data)
        _atomic_write(run_dir / "report.md", md); _atomic_write(run_dir / "report.html", page)
        _atomic_write(run_dir / "public.json", json.dumps({"rules_version":"v6.0","rounds":[r["public"] for r in data["rounds"]],"result":data["result"]}, ensure_ascii=False, indent=2))
        print(f"完成：{run_dir}\n回看：{run_dir / 'report.html'}\n胜者：{data['result'].get('winner_id') or '无'}（{data['result'].get('reason')}）")
        return 0 if data["run_status"] == "completed" else 1
    print(f"运行{data['run_status']}：{data.get('error_type')} {data.get('error_message','')}；记录：{run_dir / 'run.json'}", file=sys.stderr)
    return 130 if data["run_status"] == "interrupted" else 1


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.run)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 1
    run_data = json.loads(path.read_text(encoding="utf-8"))
    if run_data.get("rules_version") == "v6.0" and not getattr(args, "show_only", False):
        from . import runtime as runtime
        if run_data.get("source_fingerprint") != runtime.fingerprint():
            print("规则代码与原运行不一致，无法精确回放", file=sys.stderr); return 1
        if run_data.get("config_fingerprint") != runtime.config_fingerprint(run_data.get("rule_config")):
            print("运行参数快照已改变，无法精确回放", file=sys.stderr); return 1
        ok, message = runtime.replay(run_data)
        print(f"v6 回放：{message}")
        if args.report and ok:
            md, page = runtime.reports(run_data)
            _atomic_write(path.parent / "report.md", md); _atomic_write(path.parent / "report.html", page)
        return 0 if ok else 1
    if getattr(args, "show_only", False):
        print(json.dumps({"rules_version": run_data.get("rules_version"),
                          "decision_protocol": run_data.get("decision_protocol", "legacy"),
                          "rounds": len(run_data.get("rounds", run_data.get("reports", []))),
                          "result": run_data.get("result", run_data.get("termination_reason")),
                          "mode": "仅展示保存内容，未重新结算"},
                         ensure_ascii=False, indent=2))
        return 0
    print("该记录使用旧版引擎；可加 --show-only 查看摘要。", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Original CLI accepted options directly; retain its --mock/--rounds shape.
    if argv and argv[0] not in ("run", "replay", "-h", "--help"):
        argv.insert(0, "run")
    parser = argparse.ArgumentParser(description="战略多智能体模拟：整轮密封决策")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑一局")
    run.add_argument("--rules-version", choices=("v6",), default="v6", help="规则版本")
    run.add_argument("--provider", default=None, choices=("mock", "deepseek", "openai"))
    run.add_argument("--mock", action="store_true", help="兼容旧入口，等价于 --provider mock")
    run.add_argument("--scenario", default=None)
    run.add_argument("--resume", default=None, help="从已有实验目录或 run.json 的最后完整回合续跑")
    run.add_argument("--rounds", type=int, default=None, help="覆盖场景回合上限")
    run.add_argument("--model", default=None, help="真实模型 ID")
    run.add_argument("--base-url", default=None, help="OpenAI 兼容服务地址（含 /v1）")
    run.add_argument("--api-key", default=None, help="接口密钥；推荐使用环境变量")
    run.add_argument("--temperature", type=float, default=None, help="模型温度（0~2）")
    run.add_argument("--max-tokens", type=int, default=None, help="国家决策输出 token 上限")
    run.add_argument("--timeout", type=float, default=None, help="单次模型请求超时秒数")
    run.add_argument("--out", "--output-dir", dest="out", default=None, help="指定一个尚不存在的输出目录")
    run.add_argument("--retries", "--max-retries", dest="retries", type=int, default=3,
                     help="首次尝试失败后的最多重试次数")
    run.add_argument("--no-prompts", action="store_true",
                     help="日志不保存 system/user 原文")
    run.set_defaults(func=cmd_run_v6)

    replay = sub.add_parser("replay", help="用保存的决策重放并核对状态")
    replay.add_argument("run")
    replay.add_argument("--show-only", action="store_true", help="仅展示保存摘要，可用于旧记录；不重新结算")
    replay.add_argument("--report", action="store_true",
                        help="按当前报告模板重新生成同目录的 report.md / report.html")
    replay.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ProviderError, RuntimeError, ValueError, OSError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
