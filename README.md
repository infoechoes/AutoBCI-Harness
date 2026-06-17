<h1 align="center">AutoBCI Harness</h1>

<p align="center">
  <b>你的 7×24 小时自动研究助手</b><br/>
  持续运行 AutoResearch,通过微信实时汇报进展,也能接收远程任务
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="license" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python" />
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="status" />
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey" alt="platform" />
</p>

<p align="center">
  <img src="docs/assets/autobci-research-loop.svg" alt="AutoBCI research control plane" />
</p>

---

## 为什么需要 AutoBCI

长期算法实验最麻烦的地方,不是"再跑一个模型"。

- 人不可能一直守着实验室电脑。
- Coding agent 会写代码,但不天然知道哪些边界不能动。
- 单次高分可能只是 lucky run,不是可靠进步。
- 多轮尝试如果只散在日志里,下一轮又会从头猜。

AutoBCI 把这些问题收进一个本地研究循环:人类冻结问题和指标,Worker 在受限沙盒里运行实验,固定评估器给出结果,ledger 写下证据,Research Tree 把多轮尝试压成下一轮可用的经验。

## 两种工作方式

| 模式 | 适合什么 | 怎么结束 |
| --- | --- | --- |
| **永续模式(Perp)** | 长期调参、评估漂移、跨轮次复盘 | 一直观察、运行、记录和总结,直到你暂停或停止 |
| **Goal 模式** | 单次验证、临时分析、快速试一个候选方向 | 目标完成并通过证据检查后,汇报并停下 |

Goal 模式参考 [Codex `/goal`](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex) 的思路:目标、验证面和完成条件先固定,完成后就停下。AutoBCI 主打的是 **永续模式(Perp)**。真实科研不是一次 prompt 能解决的:失败、漂移、反例和偶然高分都会积累成上下文。Research Tree 不是一份漂亮总结,而是由 Program、ledger、events、artifacts 和压缩摘要组成的研究记忆。

## 三个主打能力

### 1. 7×24 小时持续 AutoResearch

AutoBCI 会在本机持续推进研究循环:排队候选方向、运行受控实验、复盘结果、沉淀下一步。它适合有长期调参、评估漂移和结果复盘压力的团队,不是一次性脚本。

### 2. 随叫随到:微信汇报,也能接收远程任务

Hermes、OpenClaw、ClawBot、微信或 webhook 只负责传话和收报告:查状态、发报告、记录论文链接、触发白名单命令。你可以在通勤、散步或临时看到一篇论文时,随时把灵感交给它,也可以随时调整下一步研究方向。手机不是远程桌面,也不能变成任意 shell;科研真源仍在本机 Program、ledger、events 和 artifacts 里。

### 3. Research Tree:把多轮实验变成经验

每一次尝试都应该留下来:为什么选这条方向,跑了什么命令,改了什么文件,结果是不是可信,为什么下一轮要继续或放弃。上下文足够多时,AutoBCI 才会真正变强,因为它不是只记对话,而是在积累可复盘的研究证据。Research Tree 让它能跨轮次学习、进化和迭代,把前后几次实验的结论变成下一次实验的经验。

## 模型接口与远程联络 Agent

AutoBCI 不把用户锁死在某一家模型或某一个远程联络 agent 上。模型接口层负责连接推理、计划和代码 Worker 可用的供应商;远程联络 Agent/Worker 层负责消息流转、微信汇报和白名单控制。两层分开展示,不把 Claude 模型和 Claude Code 这类 coding agent 混成同一类。

<p align="center">
  <b>支持的模型供应商接口</b><br/>
  <img src="docs/assets/logos/openai.png" height="42" alt="OpenAI" title="OpenAI" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/claude.png" height="42" alt="Claude" title="Claude" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/deepseek.png" height="42" alt="DeepSeek" title="DeepSeek" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/minimax.png" height="42" alt="MiniMax" title="MiniMax" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/glm.png" height="42" alt="GLM" title="GLM" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/qwen.png" height="42" alt="Qwen" title="Qwen" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/kimi.png" height="42" alt="Kimi" title="Kimi" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/gemini.png" height="42" alt="Gemini" title="Gemini" />
  &nbsp;&nbsp;
  <img src="docs/assets/logos/xiaomimimo.png" height="42" alt="Xiaomi MiMo" title="Xiaomi MiMo" />
</p>

<p align="center">
  <b>远程联络 Agent / Worker</b><br/>
  <img src="docs/assets/logos/hermes.jpg" height="58" alt="Hermes" title="Hermes" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="docs/assets/logos/openclaw.png" height="58" alt="OpenClaw" title="OpenClaw" />
</p>

---

## 核心模块

| 模块 | 作用 |
| --- | --- |
| **Program** | 冻结研究问题、主指标、数据边界和禁止事项 |
| **Guard** | 防止 agent 为了高分改题、换指标、碰 raw data 或吃未来信息 |
| **Worker Sandbox** | 让代码修改和实验运行发生在受限范围内 |
| **Fixed Evaluator** | 用固定评估器区分 smoke、候选结果和可信结果 |
| **Trace + Ledger** | 记录命令、diff、stdout/stderr、指标、artifact 和回滚线索 |
| **Research Tree** | 把多轮尝试、失败证据和下一步理由压成可复用研究记忆 |
| **Storage Budget** | 防止自动研究悄悄复制数据、写爆 checkpoint 或 artifacts |
| **Dashboard / Mobile Gateway** | Dashboard 看现场,微信/Hermes/OpenClaw 收报告和发白名单任务 |

---

## 为什么先从 BCI 开始

真实世界的大脑数据充满个体差异、跨 session 漂移和长尾异常。依靠人工穷举超参、反复对齐预处理、手动调试算法结构,既耗费心力,也无法覆盖组合空间。通用 coding agent 能写代码,但不天然理解科研边界:它可能改评价指标、改数据划分、吃未来信息,或在一次偶然高分后停止验证。

AutoBCI Harness 是为 BCI 等科研场景设计的 research-loop engineering harness(研究循环工程框架)。它的第一原则是:人类定义问题边界、主指标和禁止事项;AI 在边界内持续探索;每一步必须可追踪、可回滚、可审计。

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

## 📱 手机网关:远程观察与受控授权

AutoBCI 的默认产品入口是 **headless CLI + agent 对话**。它不要求用户打开 TUI,也不要求切到第三方窗口。Claude Code、Codex、Cursor、Workbody、Hermes、ClawBot 或其它 agent 只需要调用稳定命令:

```bash
autobci doctor --json
autobci status --json
autobci ask "现在进展如何？" --json
autobci-agent research-loop status --json
```

手机微信 / Hermes / OpenClaw 这类网关只负责传话和收报告。科研真源仍然在本机的 Program、ledger、events 和 artifacts 里。

配置教程见 [`docs/mobile_gateway_setup.md`](docs/mobile_gateway_setup.md)。

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

## 🤝 社区与技术交流

开源本框架的初衷,是为了在更广泛、复杂的真实科研与临床场景中验证并打磨系统。

**适合交流的场景:**

- 有长期调参、评估漂移、结果复盘压力的 BCI 或算法团队
- 想在不破坏现有私有代码库的前提下,接入本地 Agent Loop
- 需要把模型 API、校园网 / 医院内网、跳板机和本地执行边界拆清楚
- 已经有 runner / evaluator,但缺少可审计的自动研究循环

**不适合:**

- 希望拿到一个托管云 SaaS
- 希望手机任意远控 shell
- 希望把单次最高分包装成可靠科研结论

我们希望了解真实使用场景中的约束、失败模式和接入边界,也欢迎围绕本地部署、数据管线、评估器和远程观察链路展开技术讨论。

**📫 联系方式:**
- **微信**: `Submartinga11e`
- **备注**: `AutoBCI + 你的实验类型`

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

Apache-2.0. See `LICENSE`.
