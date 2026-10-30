# Strategic Multi-Agent Game

虚构国家的战略多智能体模拟。默认采用 [博弈规则 v5.0](docs/knowledge_base/博弈规则-v5.0.md)，每轮国家基于各自可见信息密封提交决策，再由程序统一结算。旧版研究资料归档在 `docs/archive/`，实验记录保留在 `runs/`。

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

默认场景位于 `src/strategic_game/scenarios/three_states.json`，可复制后用 `--scenario 路径` 指定新场景。国家初始领土、常规力量、核力量和私人画像应显式填写，旧版 C/E/M 场景不能直接当作 v5 场景使用。

国家和国际环境可以显式配置原始资料中的标签；未设置表示未知，不自动推断。完整的虚构配置示例见 `src/strategic_game/scenarios/full_tags_example.json`，可用 `--scenario` 指定运行。

场景根对象的 `world_tags` 是公共信息：`polarity` 可选单极、两极、多极；`system_environment` 可选紧张/复杂、宽松/复杂、紧张/清晰、宽松/清晰；`strategic_environment` 可选包容、约束。

各国 `tags` 只向本国模型可见，支持：

- `strategic_culture`、`foreign_policy`、`war_aim`、`ideology`。意识形态使用 1–5 或“狂热”“包容”，两种口径不自动换算。
- `threat_perception`（急迫、中、缓和）、`comprehensive_power`（霸权、强国、中等、弱）和 `nuclear_status`（无、有，但发动打击能力有限、有、二次核打击）。
- `power_c`、`power_e`、`power_m`、`strategic_will` 使用 1–5，不自动转换成 v5 领土和军力数值。
- `stakeholders` 使用 `allies`、`adversaries`、`others` 数组引用其他国家 ID；也可用 `description` 描述未在场景中单独建模的集团；这些是本国私人设定或认知，不自动建立真实盟约。`relative_geography` 以其他国家 ID 为键，填写地理关系描述。

词典 `src/strategic_game/knowledge/label_dictionary.json` 保存两份 tag 资料的正文、表格、脚注和对应释义。原资料中的研究观点及相互分歧按来源保留；其中旧版 M 档位、核使用假说、射程和二次打击不自动变成 v5 结算或行动限制。每轮要求模型填写私有 `threat_assessment`，可留空；上次有效判断仅供本国下一轮参考，是模型主观判断而非公开事实。完整运行记录保存实际使用的词典版本、哈希和释义，公开记录不会包含私人标签或威胁判断。

数值参数集中在 `src/strategic_game/scenarios/rules.json`；行动名称属于规则框架，应保持稳定。场景可通过 `rule_config` 覆盖需要调整的参数。每次运行保存数值快照和规则代码指纹：

```bash
.venv/bin/strategic-game replay runs/<目录>/run.json
.venv/bin/strategic-game replay runs/<目录>/run.json --report
```

回放使用已记录决策和参数快照重新结算，不重新请求模型；规则代码与原运行不一致时，不能进行精确回放。

清理前的旧版和旧结构 v5 记录保留原文件，可用 `replay <记录> --show-only` 查看摘要；精确重算需要切回产生记录的 Git 版本。新运行按规则代码指纹和参数快照校验回放，单纯修改默认数值不影响已保存记录的参数。v5 模型请求目前顺序发送。

国家、场景和计算结果均为虚构研究设定，不是现实国家行为预测。
