# Strategic Multi-Agent Game

虚构国家的战略多智能体模拟。默认采用 [博弈规则 v5.0](docs/knowledge_base/博弈规则-v5.0.md)，每轮国家基于各自可见信息密封提交决策，再由程序统一结算。旧 v4.1 引擎和旧记录回放仍可使用。

## 安装和离线运行

需要 Python 3.11 或以上：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/strategic-game run --provider mock --rounds 4
.venv/bin/python -m unittest discover -s tests
```

运行结果在 `runs/` 下，包含完整研究记录 `run.json`、公开记录 `public.json` 和网页 `report.html`。完整记录包含私人信息，应限制分享。`mock` 仅验证流程，不能代表模型行为。

## 真实模型

```bash
export DEEPSEEK_API_KEY='你的密钥'
.venv/bin/strategic-game run --provider deepseek --model deepseek-flash --rounds 2
```

OpenAI 兼容服务：

```bash
.venv/bin/strategic-game run --provider openai --base-url http://127.0.0.1:8000/v1 --model 本地模型 --rounds 2
```

接口调用可能产生费用。先用短局检查模型响应和失败记录。`--retries` 控制重试，`--no-prompts` 不在日志保存提示词，`--out` 可指定尚不存在的结果目录。

## v5 场景、参数和回放

默认场景位于 `src/strategic_game/scenarios/v5_three_states.json`，可复制后用 `--scenario 路径` 指定新场景。国家初始领土、常规力量、核力量和私人画像应显式填写，旧版 C/E/M 场景不能直接当作 v5 场景使用。

数值参数集中在 `src/strategic_game/scenarios/v5_rules.json`；行动名称属于规则框架，应保持稳定。场景可通过 `rule_config` 覆盖需要调整的参数。每次运行保存数值快照和规则代码指纹：

```bash
.venv/bin/strategic-game replay runs/<目录>/run.json
.venv/bin/strategic-game replay runs/<目录>/run.json --report
```

回放使用已记录决策重新结算，不重新请求模型；规则代码或参数文件改变后不能声称精确复现。

旧版运行可用 `--rules-version v4` 显式选择，旧记录继续使用原回放入口。`--negotiation-mode` 是旧版选项。

国家、场景和计算结果均为虚构研究设定，不是现实国家行为预测。
