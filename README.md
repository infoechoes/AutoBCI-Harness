<h1 align="center">AutoBCI Harness</h1>

<p align="center">
  <b>把科研从手工调参地狱中解放出来</b><br/>
  人类定义边界 · AI 自动探索 · 每一步可审计
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="license" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python" />
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="status" />
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="platform" />
</p>

<p align="center">
  <img src="docs/assets/dashboard.png" width="820" alt="AutoBCI Mission Control dashboard" />
</p>

---

## 🎯 为什么需要 AutoBCI

做过一线 BCI 研究的人都清楚:**真实世界的大脑数据充满个体差异、跨 session 漂移和长尾异常**。依靠人工穷举超参、反复对齐预处理、手动调试算法结构,是一件极其耗费心力且无法覆盖组合空间的事。

更糟的是,当你把科研任务交给通用的 AI coding agent 时,它们往往会:
- 🚫 **为了拿高分偷偷改评价指标或数据划分**
- 🚫 **把完整的试错历史全部喂给大模型,导致 Token 费用爆炸**
- 🚫 **碰巧跑出一个好结果就停下,不验证鲁棒性**

**AutoBCI Harness** 是一个为 BCI 等科研场景设计的 **research-loop engineering harness**(研究循环工程框架)。它不是又一个聊天机器人包装,而是一套完整的自动化科研管线:

- ✅ **人类只需定义问题边界、评价指标和禁止事项**
- ✅ **AI agent 在这个空间里持续提方向、改代码、跑实验**
- ✅ **系统保证每一步可追踪、可回滚、指标固定、数据不被篡改**

---

## 🔥 三大工程底线:为真实科研环境而生

市面上存在众多通用 AI agent 框架,但它们往往无法适应科研现场的复杂生态。AutoBCI 在设计时严格坚守了以下工程底线:

### 1. 非侵入式接入(Non-invasive Wrapper)

各大高校和临床科室通常拥有自己的祖传代码仓库、成熟的数据管线,以及极其严格的**校园网/内网合规限制**。

AutoBCI **不造全家桶、不要求重构既有业务代码**。它被设计为一层极轻的外挂:
- 通过单一的 `program.md` 文件定义任务目标
- 只要定好验收边界,就能像调度插件一样套在你现有的本地管线上
- 支持对接 Codex、Claude Code、OpenCode 或自定义 runner

### 2. 状态压缩机制:拒当"消防水龙头"

新手编写科研 Loop 最容易犯的错:**将长周期的所有上下文与试错思维链全盘抛给大模型**,这会像"消防水龙头浇花"一样瞬间撑爆 Token,导致极高的资金消耗。

AutoBCI 内置了**中层状态压缩机制**:
- 每隔若干试次(如 5~20 次),系统强制对算法变动、尝试路径与结果进行降维总结
- **实测可将长线任务的 Token 消耗量降至传统方式的 1/10**
- 让实验室 24 小时无人值守的研究"跑得起"

### 3. Guard 护栏机制:防 AI 篡改题目

当赋予模型最高权限去追求单一优化指标时,AI 为了寻找捷径,第一反应往往是试图**篡改评价标准来"作弊"**(例如试图将复杂的三维坐标预测降维成简单的二分类任务)。

AutoBCI 内建了**强硬的 Guard 校验机制**:
- 严格剥夺 AI 对核心评价文件(Program、主指标、数据划分、原始数据)的越界修改权限
- 固定评估器(Fixed Evaluator)保证结果不能靠事后改指标包装成进步
- 审计记录(Ledger)追踪每一次命令、diff、指标和回滚线索

---

## 🧠 核心循环架构

```text
Human Gate
  -> Program (冻结任务定义)
  -> Direction Queue (研究方向队列)
  -> Worker Sandbox (受限执行区)
  -> Fixed Evaluator (固定评估器)
  -> Ledger / Artifacts (审计真源)
  -> Compression / Replan (中层压缩)
  -> Dashboard (运行态投影)
  -> Human Gate
```

| 模块 | 项目含义 |
| --- | --- |
| Human Gate | 人决定问题定义、边界、指标、是否允许改关键契约 |
| Program | 冻结的任务说明,写清楚要预测什么、用什么数据、主指标是什么、哪些动作禁止 |
| Direction Queue | 研究方向队列。每个 track 说明为什么做、怎么做、预计改哪些文件 |
| Worker Sandbox | 受限执行区。可接 Codex、Claude Code、OpenCode、自定义 runner 或内置 patch worker |
| Fixed Evaluator | 固定评估器。结果不能靠事后改指标包装成进步 |
| Ledger / Artifacts | 审计真源,记录命令、diff、stdout/stderr、指标、产物路径和回滚线索 |
| Compression / Replan | 多轮尝试后的中层压缩,把失败、证据和下一步方向重新整理给下一轮 |
| Dashboard | 运行态投影,用来现场观察当前在做什么；不是第二套真相 |

---

## 🚀 快速开始(5 分钟跑通最小闭环)

**前置要求**:
- Python 3.10+
- Node.js 22+ 和 npm
- macOS 或 Linux(Windows 有检查脚本,但不是 alpha 首个验收目标)

```bash
git clone https://github.com/your-org/AutoBci-public-harness.git
cd AutoBci-public-harness
bash scripts/install.sh
source .venv/bin/activate

# 环境检查
autobci doctor --json

# 跑不依赖真实模型 key 的本地 demo(推荐首次验证)
autobci demo onsite --skip-smoke

# 打开 Dashboard
autobci dashboard
```

**`--skip-smoke` 会跳过真实模型调用,只验证本地闭环和 Dashboard。** 要跑真实 provider smoke,需要先配置 API key(见下节)。

---

## 🔑 配置模型(可选,仅用于真实 agent 驱动)

查看当前 provider 和模型状态:

```bash
autobci model list --json
```

配置 MiniMax 中国区 API key:

```bash
autobci model key minimax-cn
autobci model set --agent intake --provider minimax-cn --model MiniMax-M3
autobci model test minimax-cn --model MiniMax-M3 --json
```

运行带真实 intake smoke 的现场 demo:

```bash
autobci demo onsite --provider minimax-cn --model MiniMax-M3
```

常用 provider 已内置适配:

| Provider | 协议 | Key |
| --- | --- | --- |
| `minimax-cn` | Anthropic Messages 兼容 | `MINIMAX_CN_API_KEY` |
| `minimax` | Anthropic Messages 兼容 | `MINIMAX_API_KEY` |
| `deepseek` | OpenAI Chat Completions 兼容 | `DEEPSEEK_API_KEY` |
| `glm` / `zhipu` | OpenAI Chat Completions 兼容 | `ZAI_API_KEY` |
| `qwen` / `dashscope` | OpenAI Chat Completions 兼容 | `DASHSCOPE_API_KEY` |
| `xiaomi` / `mimo` | pi-ai runtime | `XIAOMI_API_KEY` |
| `openai` | pi-ai runtime | `OPENAI_API_KEY` |

**注意**: ChatGPT Plus 网页订阅 ≠ 本仓库 provider runtime 的 API key。Codex App 或 Codex CLI 可以用 ChatGPT 账号登录,但 AutoBCI 自己的 provider smoke、director web search 和内置 worker 需要相应 provider 的 API key。缺 key 或模型不可用时必须显式失败,不能用本地 mock 冒充成功。

---

## 📱 手机网关:离开实验室,算法还在跑

AutoBCI 的默认产品入口是 **headless CLI + agent 对话**。它不要求用户打开 TUI,也不要求切到第三方窗口。Claude Code、Codex、Cursor、Workbody、Hermes、ClawBot 或其它 agent 只需要调用稳定命令:

```bash
autobci doctor --json
autobci status --json
autobci ask "现在进展如何？" --json
autobci-agent research-loop status --json
```

手机微信 / Hermes / OpenClaw 这类网关只负责传话和收报告。科研真源仍然在本机的 Program、ledger、events 和 artifacts 里。

适合宣传的一句话:

> 你可以离开实验室,但本地 AI 仍在边界内跑研究、写 ledger,并把关键进展推到你的手机。

配置教程见 [`docs/mobile_gateway_setup.md`](docs/mobile_gateway_setup.md)。营销稿草案见 [`docs/2026-06-17_mobile_gateway_marketing_draft.md`](docs/2026-06-17_mobile_gateway_marketing_draft.md)。

---

## 🎬 当前能跑什么

公开 alpha 故意保持窄路径,优先交付一条别人 clone 下来就能跑的最小闭环:

| 命令 | 用途 |
| --- | --- |
| `autobci` | 显示 headless CLI 入口和常用机器命令 |
| `autobci doctor --json` | 检查 Python、Node、provider 配置、Pi runtime、Dashboard 和 runner |
| `autobci status --json` | 读取当前 Program、研究循环、Dashboard 和 artifact 状态 |
| `autobci ask "现在进展如何？" --json` | 处理一次自然语言状态查询,默认不调用 live 模型 |
| `autobci data set /path/to/dataset` | 保存本地 BCI 数据目录或项目数据目录 |
| `autobci storage audit --json` | 非破坏性扫描本地记录目录,识别重复文件和可压缩记录 |
| `autobci demo onsite --skip-smoke` | 跑不依赖真实模型 key 的现场交付检查 |
| `autobci dashboard` | 打开本地 Dashboard,显示当前任务、动态任务流和审计面板 |
| `autobci-agent research-loop status` | 查看研究循环队列、ledger 和当前 phase |
| `autobci-agent director-plan --web on` | 使用 OpenAI web search 辅助生成研究方向(需配置 `OPENAI_API_KEY`) |

公开 alpha 不绑定某个具体公开任务。它先证明通用 BCI 研究闭环本身:Program、队列、runner、ledger、固定评估、Dashboard 和手机网关。真实课题需要用户在本地冻结自己的 Program,配置自己的数据目录、评估器和 runner。

主仓曾服务过 BCI/eCOG 严格因果解码研究;这个公开 harness 导出版只保留通用控制面和接入边界,不携带历史研究树、内部策略文档或真实科研数据。

---

## 🤝 免费管线诊断与技术合作

开源本框架的初衷,是为了在更广泛、复杂的真实科研与临床场景中打磨系统。

在与诸多一线科研团队交流后,我发现大家的痛点惊人一致:

1. **被海量异构脑数据的个体差异折磨,急需自动化调参解放生产力**
2. **需要在不破坏现有私有代码库的前提下,低成本接入 Agent Loop**
3. **受限于校园网 / 医院内网,面临大模型 API 调用的合规限制与本地跳板机部署难题**

如果您所在的课题组或科室正面临上述困境,欢迎随时与我联系。我目前正寻求真实的复杂业务场景进行压测,**可为您提供首次免费的「数据管线接入诊断」**,评估自动化改造的最简路径与网络合规方案。

若您有明确的科研经费或横向课题预算,我也非常期待接洽深度的技术服务与定制化私有部署合作。

**📫 联系方式:**
- **微信**: `[填入您的微信号]`
- **备注**: 管线诊断 / 技术合作

---

## 🔧 交给 Cursor / Codex / Claude Code

把这个仓库交给 coding agent 时,不要只说"帮我优化代码"。建议直接复制下面这段:

```text
Read README.md, AGENTS.md, DEMO_QUICKSTART.md, and docs/storage_budget.md.

Treat AutoBCI as a research-loop engineering harness, not as a generic coding task.
First run:

bash scripts/install.sh
source .venv/bin/activate
autobci doctor --json
autobci status --json
autobci ask "现在进展如何？" --json
autobci demo onsite --skip-smoke
autobci-agent research-loop status --json

Before proposing edits, report:
1. the current Program boundary;
2. the primary metric;
3. the allowed and forbidden files/actions;
4. the current research queue;
5. where ledger, events, and artifacts are written.

Do not modify data/raw, ProgramMD, data splits, primary metrics, or alignment logic unless I explicitly approve it.
```

如果要让 agent 搜论文或 GitHub 方向,先确认 provider key,再用:

```bash
autobci-agent director-plan \
  --web on \
  --web-provider openai_web_search \
  --min-tracks 10 \
  --json
```

---

## 📂 仓库结构

```text
.
├── README.md
├── DEMO_QUICKSTART.md
├── AGENTS.md              # 给 coding agent 的硬规则
├── pyproject.toml
├── src/bci_autoresearch/
├── scripts/
├── dashboard/
├── programs/
├── experiments/
├── configs/
├── tests/
└── .agents/skills/
```

**重要入口:**
- `AGENTS.md`:给 coding agent 的硬规则
- `DEMO_QUICKSTART.md`:最短现场演示路径
- `docs/storage_budget.md`:数据和产物的默认磁盘预算
- `programs/`:本地 Program 放置处；公开叙事不绑定具体任务
- `.agents/skills/autobci-harness/SKILL.md`:作为本地研究 harness 使用 AutoBCI
- `scripts/`:接入本地 runner、Dashboard 和安装脚本

---

## 🔬 Dashboard

Dashboard 是运行态投影,默认本地启动:

```bash
autobci dashboard
```

它会显示:
- 当前任务和 Program 摘要
- 动态任务流
- 分类指标或历史回归指标
- 研究队列和即将执行的 track
- ledger、events、artifacts 的位置
- 当前结果是否只是 smoke、候选,还是固定评估后的结果

**Dashboard 不是审计真源**。审计真源在这些文件里:

```text
artifacts/research_loop/<task_id>/events.jsonl
artifacts/research_loop/<task_id>/ledger.jsonl
artifacts/research_loop/<task_id>/runs/<run_id>/result.json
artifacts/monitor/demo_task_stream.json
```

---

## 🧪 本地数据目录

公开 harness 不规定某个固定数据 schema。真实 BCI 任务的数据布局应由冻结 Program、配置文件和本地 runner 明确声明。

本地路径只写入 `.autobci/data_paths.json`。这个文件被 Git 忽略,不会提交你的本机路径。

**磁盘安全边界:**
- 本仓库不会默认下载 Kaggle、BCI 原始数据或第三方数据集。
- runner 默认应拒绝超过预算的数据目录和 artifacts。
- 如确实需要更大数据,显式设置 `AUTOBCI_MAX_DATASET_BYTES=10G` 或 `AUTOBCI_MAX_ARTIFACT_BYTES=2G`。
- `kaggle/`、`artifacts/`、`data/`、`.autobci/` 和常见模型/数组产物都被 Git 忽略,不要把大产物放进公开提交。

详细策略见 [`docs/storage_budget.md`](docs/storage_budget.md)。

**原始科研数据边界:**
- `data/raw/` 永远只读
- 不允许为了拿高分修改原始数据、数据划分、主指标或标签定义
- 历史 BCI 训练代码必须保持严格因果:模型输入只能使用当前和过去样本,不能在预处理、归一化、平滑或目标构造中使用未来信息

---

## ⚙️ 开发检查

安装开发依赖:

```bash
AUTOBCI_INSTALL_DEV=1 bash scripts/install.sh
```

最小回归检查:

```bash
PYTHONPATH=src pytest -q tests/test_headless_cli.py
git diff --check
```

涉及 CLI、provider、Dashboard、runner 或 research-loop 的改动,至少跑对应单测和一个本地 smoke。**缺 key、缺 runner 或配置不兼容时必须显式失败,不能用本地兜底路径冒充成功**。

存储审计:

```bash
autobci storage audit --json
```

这条命令只读扫描 `artifacts/`、`output/`、`tmp/`、`.autobci/`,报告重复大文件和可压缩文本记录,不会删除、压缩或移动任何文件。

---

## ⚠️ 当前边界

- 公开 alpha 不附带真实业务数据
- 不承诺自主研究一定提升分数
- 不把单次最高分包装成可靠科研结论
- 不把内部 smoke fixture 包装成公开 BCI 成果
- 不允许 silent fake fallback
- 不允许 agent 自行改 Program、主指标、数据划分或 raw-data 边界

---

## 📄 License

Apache-2.0. See `LICENSE`.</p>
