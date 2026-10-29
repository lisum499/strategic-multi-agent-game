# Strategic Multi-Agent Game

一个面向本地LLM的三国战略博弈最小实现。国家同时密封提交预测、公开宣称和实际
行动；确定性状态机统一校验、揭示和结算，公开记录与私有审计记录分离保存。

## 功能

- 四类行动：常规战争、核打击、干涉、呼吁和平
- Pydantic结构化决策和合法性校验
- 同轮信息隔离、并发决策、统一结算和事务回滚
- C/E/M、声誉、可信度、战争、死亡、胜利与僵局判定
- 固定随机种子，可复现实验
- 支持vLLM等OpenAI兼容本地接口

## 安装

```bash
git clone https://github.com/lisum499/strategic-multi-agent-game.git
cd strategic-multi-agent-game
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## 运行

先启动本地模型，例如：

```bash
vllm serve <MODEL_PATH> \
  --served-model-name qwen3-4b-local \
  --port 8000 \
  --gpu-memory-utilization 0.70
```

再启动博弈：

```bash
.venv/bin/strategic-game \
  --base-url http://127.0.0.1:8000/v1 \
  --model qwen3-4b-local \
  --rounds 100 \
  --seed 42
```

不调用模型的快速验证：

```bash
.venv/bin/strategic-game --mock --rounds 20
.venv/bin/pytest -q
```

结果写入 `output/public.json` 和 `output/private.json`。默认连续12轮全体一般和平且
没有活动战争时终止；使用 `--stalemate-rounds 0` 可禁用。

## 目录

```text
src/strategic_game/   状态、规则、Agent、状态机和CLI
scenarios/            三国场景配置
tests/                规则与完整流程测试
```
