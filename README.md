# Strategic Multi-Agent Game

虚构国家的战略多智能体模拟。整合 `noclear/simulation_v2` 的 v4.1 零和计分、危机阶段、目标卡、日志与回放，以及本项目的整轮密封决策和本地模型接入。

每轮国家基于同一轮初始信息，同时提交预测、公开宣称、实际行动和谈判承诺；全部提交完成后统一揭示并结算。模型请求可以重试，但不会在看到对手当轮宣称后改选。失败达到上限时由系统补充默认和平，并明确记录来源。

## 安装与离线运行

需要 Python 3.11 或以上。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/strategic-game run --provider mock --rounds 4
.venv/bin/pytest -q
```

默认场景为七国危机 v4.1。`mock` 是固定算法响应，只验证程序流程。兼容旧命令形状 `strategic-game --mock --rounds 4`；旧三国场景可以通过 `--scenario scenarios/default_three_states.json` 显式加载，加载时会记录国家名称到稳定 ID 的映射。

## 真实模型

DeepSeek：

```bash
export DEEPSEEK_API_KEY='...'
.venv/bin/strategic-game run --provider deepseek --model deepseek-flash --rounds 2
```

OpenAI 兼容本地服务，例如已启动的 vLLM：

```bash
.venv/bin/strategic-game run --provider openai \
  --base-url http://127.0.0.1:8000/v1 --model qwen3-4b-local --rounds 2
```

`--max-workers 7` 控制国家请求并发数；`--retries 3` 表示首次尝试后最多再重试三次。`--temperature`、`--max-tokens`、`--timeout` 控制模型请求。真实接口的可用性和模型行为需要在实际环境中验证。种子控制规则随机数和选项顺序，当前真实请求不传模型 seed；确定性回放以已保存决策为输入，不保证重新请求真实模型逐字一致。

## 运行记录与回放

输出默认写入当前工作目录的 `runs/<运行目录>/`，可用 `--out <尚不存在的目录>` 指定。`run.json` 保存完整研究记录；`report.html` 和 `report.md` 用于查看每轮决策、结算和诊断。完整记录与报告含角色卡、私人观察和预测，按研究者资料保存。`public.json` 只导出公开状态、已揭示行动和系统补充行动来源，供公开分析使用。

```bash
.venv/bin/strategic-game replay runs/<运行目录>/run.json
.venv/bin/strategic-game replay runs/<运行目录>/run.json --report
.venv/bin/strategic-game replay <旧记录.json> --show-only
```

发生默认和平或环境摘要最终失败时，运行标为 `completed_with_errors` 并返回非零退出码，记录仍可查看和回放。旧两阶段记录或原三国程序记录可展示保存摘要，不能当作新密封协议的精确回放。

## 规则与结构

- [整合说明与数据接口](docs/整合说明.md)
- [v4.1 危机机制](docs/游戏规则-v4.1.md)
- `src/strategic_game/`：领域状态、密封决策、v4.1 结算、模型适配、日志与报告。
- `scenarios/`：可编辑的示例场景；安装包同时包含默认场景资源。
- `tests/`：规则、密封信息隔离、默认和平、迁移及回放检查。

场景和数值均为虚构研究设定。v4.1 中的数值和危机参数属于模型设计，输出不能视为现实国家行为预测。
