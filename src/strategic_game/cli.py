#!/usr/bin/env python3
"""Command-line entry point for the sealed v4.1 strategic game."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .engine import Engine
from .providers import ProviderError, get_provider
from .report import public_export, render_html, render_markdown
from .scenario import DEFAULT_SCENARIO, ScenarioError, load_scenario

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


def cmd_run(args: argparse.Namespace) -> int:
    if args.max_workers < 1 or args.retries < 0:
        raise ValueError("max-workers 必须 ≥1，retries 必须 ≥0")
    scenario = load_scenario(args.scenario)
    if args.rounds is not None:
        if args.rounds < 1:
            raise ValueError("--rounds 必须大于 0")
        scenario["max_rounds"] = args.rounds
        scenario["scenario_id"] = f"{scenario.get('scenario_id')}_r{args.rounds}"
    try:
        provider_name = "mock" if args.mock else args.provider
        if args.mock and args.provider not in (None, "mock"):
            raise ProviderError("--mock 不能与真实模型 provider 同时使用")
        if provider_name is None:
            provider_name = "openai" if args.base_url else "mock"
        supplied = {key: value for key, value in {
            "model": args.model, "temperature": args.temperature,
            "max_tokens": args.max_tokens, "timeout": args.timeout,
            "base_url": args.base_url, "api_key": args.api_key,
        }.items() if value is not None}
        if provider_name == "mock" and supplied:
            raise ProviderError("模型参数只能与 --provider deepseek/openai 一起使用")
        if provider_name == "deepseek" and args.base_url:
            raise ProviderError("自定义 base-url 请使用 --provider openai")
        provider = get_provider(provider_name, **supplied)
    except ProviderError as exc:
        print(f"无法创建提供方：{exc}", file=sys.stderr)
        return 1
    engine = Engine(scenario, provider, seed=args.seed,
                    negotiation_mode=args.negotiation_mode,
                    max_workers=args.max_workers, max_retries=args.retries,
                    log_prompts=not args.no_prompts)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_id = re.sub(r"[^\w.-]+", "-", str(scenario.get("scenario_id") or "scenario"))[:80].strip(".-") or "scenario"
    run_dir = Path(args.out) if args.out else (
        RUNS_DIR / f"{stamp}-{safe_id}-{provider.name}-{engine.seed}")
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    last_completed = None

    def checkpoint(current: Engine) -> None:
        nonlocal last_completed
        last_completed = copy.deepcopy(current.summary())
        last_completed["run_status"] = "running"
        last_completed["model_settings"] = _model_settings(provider)
        _atomic_write(run_dir / "run.json", json.dumps(last_completed, ensure_ascii=False, indent=2))

    # 即使首轮尚未完成也保留初始场景与配置。
    checkpoint(engine)
    print(f"实验目录：{run_dir}", flush=True)
    status = "completed"
    error_type = None
    try:
        engine.run(on_round=checkpoint)
    except KeyboardInterrupt:
        status, error_type = "interrupted", "KeyboardInterrupt"
    except Exception as exc:
        # 不写异常正文：服务端或第三方异常可能包含敏感数据。
        status, error_type = "failed", type(exc).__name__

    if status == "completed":
        last_completed = copy.deepcopy(engine.summary())
        if last_completed.get("fallback_count", 0) or last_completed.get("env_failures"):
            status = "completed_with_errors"
    else:
        completed_record_count = len(last_completed["records"])
        last_completed["incomplete_round"] = {
            "round": len(last_completed["rounds"]) + 1,
            "records": copy.deepcopy(engine.records[completed_record_count:]),
            "error_type": error_type,
        }
    last_completed["run_status"] = status
    last_completed["model_settings"] = _model_settings(provider)
    _atomic_write(run_dir / "run.json", json.dumps(last_completed, ensure_ascii=False, indent=2))
    if status in ("completed", "completed_with_errors"):
        _atomic_write(run_dir / "report.md", render_markdown(last_completed))
        _atomic_write(run_dir / "report.html", render_html(last_completed))
        _atomic_write(run_dir / "public.json", json.dumps(
            public_export(last_completed), ensure_ascii=False, indent=2))
    else:
        print(f"运行{status}（{error_type}），已保存完整回合与未完成回合的调用记录：{run_dir / 'run.json'}", file=sys.stderr)
        return 130 if status == "interrupted" else 1

    run_data = last_completed

    print(f"完成：{run_dir}")
    print(f"回看：{run_dir / 'report.html'}")
    print(f"胜者：{run_data['result'].get('winner') or '无'}（{run_data['result'].get('reason')}）")
    print("最终积分：" + "　".join(
        f"{cid}={item['score']}" for cid, item in
        sorted(run_data["final_state"]["countries"].items())))
    print(f"默认和平：{run_data.get('fallback_count', 0)}；轮数：{len(run_data['rounds'])}")
    return 0 if status == "completed" else 1


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.run)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 1
    run_data = json.loads(path.read_text(encoding="utf-8"))
    if getattr(args, "show_only", False):
        print(json.dumps({"rules_version": run_data.get("rules_version"),
                          "decision_protocol": run_data.get("decision_protocol", "legacy"),
                          "rounds": len(run_data.get("rounds", run_data.get("reports", []))),
                          "result": run_data.get("result", run_data.get("termination_reason")),
                          "mode": "仅展示保存内容，未重新结算"},
                         ensure_ascii=False, indent=2))
        return 0
    engine, ok, problems = Engine.replay(run_data)
    partial = run_data.get("run_status") in ("running", "failed", "interrupted")
    scope = f"已完成的 {len(run_data.get('rounds') or [])} 轮（原始运行未完成）" if partial else "完整运行"
    print(f"回放完成：{scope}{'一致' if ok else '不一致'}")
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    print(f"最终结果：{engine.result}")
    if getattr(args, "report", False):
        run_dir = path.parent
        _atomic_write(run_dir / "report.md", render_markdown(run_data))
        _atomic_write(run_dir / "report.html", render_html(run_data))
        print(f"已按当前代码重新生成：{run_dir / 'report.md'}、{run_dir / 'report.html'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Original CLI accepted options directly; retain its --mock/--rounds shape.
    if argv and argv[0] not in ("run", "replay", "-h", "--help"):
        argv.insert(0, "run")
    parser = argparse.ArgumentParser(description="v4.1 零和危机模拟：整轮密封决策")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑一局")
    run.add_argument("--provider", default=None, choices=("mock", "deepseek", "openai"))
    run.add_argument("--mock", action="store_true", help="兼容旧入口，等价于 --provider mock")
    run.add_argument("--scenario", default=DEFAULT_SCENARIO)
    run.add_argument("--rounds", type=int, default=None, help="覆盖场景回合上限")
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--model", default=None, help="真实模型 ID")
    run.add_argument("--base-url", default=None, help="OpenAI 兼容服务地址（含 /v1）")
    run.add_argument("--api-key", default=None, help="接口密钥；推荐使用环境变量")
    run.add_argument("--temperature", type=float, default=None, help="模型温度（0~2）")
    run.add_argument("--max-tokens", type=int, default=None, help="国家决策输出 token 上限")
    run.add_argument("--timeout", type=float, default=None, help="单次模型请求超时秒数")
    run.add_argument("--out", "--output-dir", dest="out", default=None, help="指定一个尚不存在的输出目录")
    run.add_argument("--max-workers", type=int, default=7, help="国家决策最大并发数")
    run.add_argument("--retries", "--max-retries", dest="retries", type=int, default=3,
                     help="首次尝试失败后的最多重试次数")
    run.add_argument("--no-prompts", action="store_true",
                     help="日志不保存 system/user 原文")
    run.add_argument("--negotiation-mode", choices=("offers", "self_offer"),
                     default=None,
                     help="密封谈判承诺：offers=预设方案；self_offer=自填报价与接受阈值")
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="用保存的决策重放并核对状态")
    replay.add_argument("run")
    replay.add_argument("--show-only", action="store_true", help="仅展示保存摘要，可用于旧记录；不重新结算")
    replay.add_argument("--report", action="store_true",
                        help="按当前 report.py 重新生成同目录的 report.md / report.html")
    replay.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ScenarioError, ProviderError, RuntimeError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
