# Strategic Multi-Agent Game

虚构国家的战略多智能体模拟。默认采用 [博弈规则 v6.0](docs/knowledge_base/博弈规则-v6.0.md)，每轮国家基于各自可见信息密封提交决策，再由程序统一结算。旧版研究资料归档在 `docs/archive/`，实验记录保留在 `runs/`。

## 安装和离线运行

需要 Python 3.10 或以上：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/strategic-game run --provider mock --rounds 4
.venv/bin/python -m unittest discover -s tests
```

## 桌面界面与 Windows EXE

源码环境可直接启动桌面界面：

```bash
PYTHONPATH=src python3 -m strategic_game.gui
```

界面支持增删和编辑任意数量的国家，设置国家存在值、常规与核力量、战略文化、意识形态、国家画像、世界背景、模型 API 和回合数。左侧实时显示模型调用与校验进度，右侧分别展示逐轮完整决策和世界状态变化；API Key 只传给后台进程，不写入场景或运行记录。运行中可停止，并可通过“断点续跑”选择原实验的 `run.json`。

在 Windows 10/11 上安装 Python 3.10 或以上后，可以先双击以下脚本直接运行源码版界面：

```text
scripts\run_windows_gui.bat
```

确认界面和 API 可用后，双击以下脚本构建独立 EXE：

```text
scripts\build_windows_exe.bat
```

脚本会自动查找可用的 Python 3.10+，创建 `.venv`、安装依赖、运行测试，并生成 `dist\StrategicMultiAgentGame.exe`。PyInstaller 不能在 Linux 上直接交叉编译 Windows EXE，因此正式 Windows 文件应在 Windows 环境中执行该脚本构建。

仓库同时提供 GitHub Actions 工作流 `Build Windows EXE`。在 GitHub 的 Actions 页面手动运行后，可从该次任务的 Artifacts 下载 `StrategicMultiAgentGame-windows`，其中包含构建完成的 EXE。

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

八国场景和断点续跑：

```bash
export OPENAI_API_KEY='你的密钥'
.venv/bin/strategic-game run --provider openai --base-url https://接口地址/v1 --model 模型名 \
  --scenario src/strategic_game/scenarios/eight_nations_dawn_strait.json --out runs/eight-nations
.venv/bin/strategic-game run --resume runs/eight-nations
```

也可以使用仓库脚本（默认 30 轮、`deepseek-flash`）：

```bash
export DEEPSEEK_API_KEY='你的密钥'
./scripts/run_eight_nations_deepseek.sh
./scripts/run_eight_nations_deepseek.sh 8 runs/eight-nations-test
./scripts/run_eight_nations_deepseek.sh --resume runs/eight-nations-test
```

可通过 `MODEL`、`MAX_TOKENS`、`TIMEOUT`、`RETRIES` 或 `PYTHON_BIN` 环境变量覆盖脚本默认值。

`run.json` 在每次请求开始、请求结束和整轮结算后原子更新，保存全部模型尝试、原始响应、校验结果、国家最终决策、统一结算、字段级 `state_changes` 和完整状态。中断发生在半轮时，续跑会保留已有调用记录并重新收集该轮全部在场国家的密封决策；已经完整结算的回合不会重复执行。API 密钥只从命令参数或环境变量读取，不写入记录。

## v6 场景、参数和回放

默认场景位于 `src/strategic_game/scenarios/three_states.json`，可复制后用 `--scenario 路径` 指定新场景。国家须显式填写 `economic_presence`、`military_presence`、`soft_presence`、常规力量、核力量和私人画像。`global_presence` 由程序按 `0.43E + 0.21M + 0.36S` 计算，场景不得直接填写；旧 `territory` 字段会被拒绝。

国家和国际环境可以显式配置原始资料中的标签；未设置表示未知，不自动推断。完整的虚构配置示例见 `src/strategic_game/scenarios/full_tags_example.json`，可用 `--scenario` 指定运行。

系统不保存 `nuclear_status` 或区域状态。当前公开 `nuclear` 直接、动态决定权限：`0` 不能选择核行动；`1–40` 只能选择门槛值低于 `45.0` 的核行动；`>=41` 可以选择所有核行动并具备自动二次打击。核行动不检查地理范围。借调、归还和战损会立即改变下一次决策与结算使用的权限。开局常规力量低于40时核力量必须为0，但回合中常规力量变化不会让 `nuclear` 自动增长。核力量至少41的国家受到升级性核行动、扩大核打击行动或战略核战争时，主持人会自动生成并审计报复行动；有限核武器使用不触发自动二次打击。

核行动的“行动门槛”是本轮开始时行动方对目标国的有向累计冲突值。例如 `conflicts[A][B] >= 45` 时，A 才具备对 B 选择“有限核武器使用”的门槛资格；`conflicts[B][A]` 不能代替，本轮新增冲突值也不能补足本轮自身门槛。完整默认门槛表见 `docs/knowledge_base/博弈规则-v6.0.md`。

`strategic_culture` 是国家顶层、静态且公开的状态，必须设为国际主义、民族主义、进步主义或现实主义。它不直接修改数值公式，但其完整定义进入提示词，要求国家按相应文化解释局势和说明决策。

`ideology_focus`（意识形态侧重性）也是国家顶层、静态且公开的必填状态，使用 1–10 的整数：1 表示淡漠，10 表示狂热，数值越高侧重程度越高。它进入提示词但不直接修改数值公式。旧 `tags.ideology` 写法不再接受。

`tags` 只允许配置私有 `stakeholders`：使用 `allies`、`adversaries`、`others` 数组引用其他国家 ID，也可用 `description` 描述未在场景中单独建模的集团。这些只是本国的利益攸关方设定或认知，不自动建立真实盟约，也不直接改变数值。其他旧标签均不再作为运行状态接受。

真实联盟成立后默认保密。只有一方从盟友借入且尚未归还的常规力量或核力量中，任一项累计达到 `1`，联盟关系才会强制公开；公开后不会因归还而重新变为秘密。合同、借调事实和具体数值始终只对双方可见。结盟限制中涉及第三国盟友时，只检查已经公开的盟友。

军事力量借调采用跨轮密封流程：借入方本轮通过 `borrows` 创建请求，出借方下一轮通过 `loan_responses` 接受或拒绝，未回应按拒绝处理。借入方可以用行动字段 `requires_loan_request_ids` 声明该行动依赖哪些到期请求；只有盟约仍有效、出借方接受且实际余额足够时才转移力量，失败时只取消对应的依赖行动。详细顺序见 `docs/当前系统状态与更新机制.md`。

词典 `src/strategic_game/knowledge/label_dictionary.json` 仍保存两份原始资料的正文、表格、脚注和对应释义，供追溯来源；除 `stakeholders` 外的旧私人标签不再是可配置运行状态。每轮要求模型填写私有 `threat_assessment`，可留空；上次有效判断仅供本国下一轮参考，是模型主观判断而非公开事实。完整运行记录保存实际使用的词典版本、哈希和释义，公开记录不会包含私人标签或威胁判断。

数值参数集中在 `src/strategic_game/scenarios/rules.json`；行动名称属于规则框架，应保持稳定。场景可通过 `rule_config` 覆盖需要调整的参数。每次运行保存数值快照和规则代码指纹：

存在转移只按双方本轮有效行动增量计算，历史累计冲突仅保留为行动门槛和威胁信息。同一国家连续对同一目标使用同类正增量行动会按 `1/0.75/0.5/0.25` 衰减，成本则按原始强度乘以 `1/2/4/8` 递增并封顶。外交协议必须通过结构化提案，并由目标国在下一轮明确接受后才能进入 `confirmed_facts`；自由文本声明只记为 `unverified_declarations`，不会自动改变世界状态。

```bash
.venv/bin/strategic-game replay runs/<目录>/run.json
.venv/bin/strategic-game replay runs/<目录>/run.json --report
```

回放使用已记录决策和参数快照重新结算，不重新请求模型；规则代码与原运行不一致时，不能进行精确回放。

旧版记录可用 `replay <记录> --show-only` 查看摘要；精确重算需要切回产生记录的 Git 版本。新运行按规则代码指纹和参数快照校验回放，单纯修改默认数值不影响已保存记录的参数。v6 模型请求目前顺序发送。

国家、场景和计算结果均为虚构研究设定，不是现实国家行为预测。
