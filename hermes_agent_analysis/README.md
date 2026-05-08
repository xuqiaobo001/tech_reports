# Hermes Agent 源码深度分析报告

> 分析日期：2026-05-05
> 项目：[Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.12.0
> 作者：Nous Research
> 许可证：MIT

---

## 一、项目概览

**Hermes Agent** 是由 Nous Research 开发的开源 AI Agent（MIT 协议），当前版本 v0.12.0。它被定位为 **"The self-improving AI agent"** —— 一个自带学习闭环的智能代理，能够从经验中创建技能、在使用中改进技能、跨会话积累记忆、自我搜索历史对话。

### 核心定位

```
The self-improving AI agent built by Nous Research.
It's the only agent with a built-in learning loop — it creates skills from
experience, improves them during use, nudges itself to persist knowledge,
searches its own past conversations, and builds a deepening model of who
you are across sessions.
```

### 项目规模

| 指标 | 数值 |
|------|------|
| 核心代码 (run_agent.py) | ~12,000 LOC |
| CLI 模块 (cli.py) | ~11,000 LOC |
| 工具实现 (tools/) | 60+ 个 Python 文件 |
| 内置技能 (skills/) | 25 个类别 |
| 可选技能 (optional-skills/) | 15 个类别 |
| 网关平台 (gateway/platforms/) | 16 个适配器 |
| 记忆提供商 (plugins/memory/) | 8 个后端 |
| 测试套件 (tests/) | ~17,000 测试 / ~900 文件 |
| 终端后端 | 7 种 (Local, Docker, SSH, Modal, Daytona, Singularity, Vercel) |

---

## 二、为什么说是"可进化、可自行学习"的 Agent

Hermes Agent 的"自进化"并非营销术语，而是由**六个相互关联的技术子系统**构成的闭环架构。

### 闭环学习循环 (Closed Learning Loop)

这是 Hermes 最核心的设计哲学，体现为一个 **从经验 → 技能 → 使用 → 改进 → 记忆** 的完整闭环：

```
用户对话 → 复杂任务完成 → agent 自动创建技能 (skill_manage)
     ^                                    |
     |                                    v
  记忆注入 <--- Curator 后台审查 <--- 技能在使用中被 patch/改进
```

**关键代码位置：**

| 机制 | 文件 | 作用 |
|------|------|------|
| 技能创建 | `tools/skill_manager_tool.py` | Agent 在完成复杂任务后，可将成功方法抽象为可复用技能（SKILL.md + 目录结构） |
| 技能改进 | `tools/skill_manager_tool.py` 的 `patch` action | Agent 在使用技能时发现问题，可直接对技能进行 targeted find-and-replace 修改 |
| 自动审查 | `agent/curator.py` | 后台 Curator 周期性审查 agent 创建的技能，执行合并、归档、打补丁等维护操作 |
| 使用追踪 | `tools/skill_usage.py` | 侧边 `.usage.json` 追踪每个技能的 use_count、view_count、patch_count、last_activity_at |

---

### 2.1 多层记忆系统 (Multi-Layer Memory)

记忆不是单一的，而是分为**三个层次**：

#### 层次一：内置文件记忆 (`tools/memory_tool.py`)

- `MEMORY.md` — Agent 的个人笔记（环境事实、项目约定、工具特性）
- `USER.md` — 对用户的认知（偏好、沟通风格、期望、工作习惯）
- 会话开始时冻结注入 system prompt，会话中写入磁盘但**不破坏前缀缓存**

**安全扫描机制：** 记忆内容在写入前经过 13 种威胁模式扫描，包括：
- Prompt injection（`ignore previous instructions` 等）
- Role hijack（`you are now`）
- 数据泄露（`curl $API_KEY`）
- SSH 后门（`authorized_keys`）
- 不可见 Unicode 字符注入

#### 层次二：会话搜索 (`tools/session_search_tool.py` + `hermes_state.py`)

- SQLite + **FTS5 全文搜索引擎** 跨所有历史会话搜索
- **双 FTS5 表设计**：
  - `messages_fts` — 默认 unicode61 分词器（快速，词级别）
  - `messages_ftc_trigram` — Trigram 分词器（支持中文/日文/韩文子串匹配）
- 搜索 → 找到匹配会话 → 加载对话 → 用辅助模型摘要 → 返回精炼结果
- 让 Agent 能"回忆起"数周前的对话细节

#### 层次三：可插拔外部记忆提供商 (`plugins/memory/`)

8 个外部记忆后端，通过 `MemoryProvider` ABC 统一接口：

| 提供商 | 核心能力 |
|--------|----------|
| **Honcho** | **辩证式用户建模** — 通过 Q&A 对话自动构建用户画像（peer cards），语义搜索，跨会话推理 |
| **Hindsight** | 知识图谱 + 实体消解 + 多策略检索，支持云端/本地/嵌入式三种模式 |
| **Mem0** | 服务端 LLM 事实提取 + 语义搜索 + 重排序 |
| **Supermemory** | 分布式记忆存储 |
| **Byterover** | 记忆优化提供商 |
| **Holographic** | 向量化全息存储 |
| **OpenViking** | 记忆提供商 |
| **RetainDB** | 本地 SQLite 记忆存储 |

**MemoryProvider ABC 生命周期：**

```python
class MemoryProvider(ABC):
    # 核心生命周期
    is_available() -> bool          # 检查是否配置就绪
    initialize(session_id, **kwargs) # 初始化连接/资源
    system_prompt_block() -> str    # 返回静态系统提示文本
    prefetch(query) -> str          # 每轮前召回相关上下文
    sync_turn(user, assistant)      # 每轮后持久化对话
    get_tool_schemas() -> List[Dict] # 暴露给 LLM 的工具模式
    handle_tool_call(name, args)    # 分发工具调用
    shutdown()                      # 清理退出

    # 可选钩子
    on_turn_start(turn, message)       # 轮次开始通知
    on_session_end(messages)           # 会话结束提取
    on_session_switch(session_id)      # 会话切换
    on_pre_compress(messages) -> str   # 压缩前提取关键信息
    on_memory_write(action, content)   # 内置记忆写入镜像
    on_delegation(task, result)        # 子代理完成通知
```

**约束：** 只允许一个外部记忆提供商同时运行，防止工具 schema 膨胀和后端冲突。

---

### 2.2 Curator 技能生命周期管理器 (`agent/curator.py`)

这是 Hermes "自进化"的**关键差异化特性**。

#### 触发机制

- **空闲触发**：当 Agent 空闲超过 `min_idle_hours`（默认 2h）
- **周期触发**：距上次运行超过 `interval_hours`（默认 7 天）
- **手动触发**：`hermes curator run`（支持 `--dry-run` 预览模式）

#### 自动状态转换（纯函数，无 LLM）

```
active ──(30天无活动)──> stale ──(90天无活动)──> archived
  ^                         |
  └───(再次被使用)──────────┘
```

- **Pinned 技能**跳过所有自动转换
- 永远不自动删除，最高操作是归档（可恢复）

#### LLM 驱动的合并审查

Curator fork 一个辅助 AIAgent 执行审查，核心策略是 **"伞形构建"（Umbrella Building）**：

1. **前缀聚类**：按领域前缀分组（如 `pr-*`、`python-*`）
2. **识别伞形类别**：例如 "PR Review Workflows"
3. **合并同类技能**：将窄技能合并为类级别（class-level）的伞形技能
4. **降级子文件**：将细粒度内容降级为 `references/`、`templates/`、`scripts/`
5. **自动更新 Cron**：合并后自动重写 cron 任务的技能引用

**设计哲学：** "数百个每个只捕获一次会话的窄技能是**失败**，而不是特性。"

---

### 2.3 上下文压缩引擎 (`agent/context_compressor.py` + `agent/context_engine.py`)

#### 可插拔架构

```python
class ContextEngine(ABC):
    name -> str
    update_from_response(usage) -> None
    should_compress(prompt_tokens) -> bool
    compress(messages, current_tokens) -> List[Dict]
```

默认实现 `ContextCompressor` 的压缩策略：

1. **保护头部**（system prompt + 首轮对话）
2. **保护尾部**（最近 N 轮，按 token 预算而非固定消息数）
3. **仅压缩中间部分**
4. **工具输出预剪枝**（LLM 摘要前的低成本预过滤）
5. **按比例分配摘要预算**（压缩内容的 20%，上限 12,000 tokens）
6. **迭代摘要更新**（多次压缩保留关键信息）

**摘要模板：** 包含"已解决/待解决"问题追踪 + "活跃任务"标记 + 明确的交接框架。

---

### 2.4 轨迹压缩器 (`trajectory_compressor.py`)

为训练下一代工具调用模型准备：

- 后处理已完成的 Agent 轨迹，在保持训练信号质量的前提下压缩到目标 token 预算
- 保护首尾轮次，压缩中间的工具调用/结果序列
- 配合 Atropos RL 环境（`environments/`）用于强化学习训练
- 支持异步并发处理（最多 50 个并发摘要请求）
- 支持 15% 采样压缩用于大规模数据处理

---

### 2.5 提示词缓存保护机制 (`agent/prompt_caching.py`)

- 记忆注入采用**冻结快照**模式：system prompt 在会话开始时固定，中途记忆写入磁盘但不更新 system prompt
- 这保证了前缀缓存的有效性，避免因记忆更新导致的成本飙升
- `/skills install --now` 之类会破坏缓存的命令默认延迟到下个会话生效
- 上下文压缩是**唯一**允许修改上下文的时间点

---

### 2.6 RL 训练系统 (`environments/` + `rl_cli.py`)

#### Atropos RL 框架集成

```python
class HermesAgentBaseEnv(BaseEnv):
    setup()           # 加载数据集
    get_next_item()   # 获取下一个任务
    format_prompt()   # 转换为用户消息
    compute_reward()  # 评分 rollout
    evaluate()        # 周期性评估
```

#### On-Policy Distillation (OPD) 环境

高级训练环境 `agentic_opd_env.py`：
1. 运行标准 agent rollout
2. 从 next-state 信号（工具结果、错误）提取"提示"
3. 构建增强提示（原始 + 提示）
4. 在教师分布下为学生 token 评分
5. 产生 `distill_token_ids` / `distill_logprobs`

这实现了**每个工具交互的 token 级别训练信号**，而不仅仅是标量奖励。

---

## 三、整体技术架构

### 3.1 架构图

```
+------------------------------------------------------------------+
|                        用户接入层                                  |
|  +-----+ +-------+ +-------+ +-------+ +------+ +------------+   |
|  | CLI | |  TUI  | |Telegra| |Discord| |Slack | |WhatsApp等  |   |
|  |     | |(Ink)  | |  m    | |       | |      | |(16个平台)  |   |
|  +--+--+ +---+---+ +---+---+ +---+---+ +---+--+ +-----+------+   |
|     |        |         |         |         |           |           |
|  +--v--------v---------v---------v---------v-----------v--+       |
|  |              Gateway (网关统一层)                         |       |
|  |    session.py / run.py / 16个 platform adapters          |       |
|  +-----------------------+----------------------------------+       |
+------------------------------------------------------------------+
                           |
+------------------------------------------------------------------+
|                    AIAgent 核心引擎 (run_agent.py ~12k LOC)       |
|  +----------------------------------------------------------+    |
|  |  对话循环: user_msg -> LLM -> tool_calls -> tool_results   |    |
|  |           -> LLM -> ... -> final_response                  |    |
|  |  * 预算跟踪 * 中断检测 * 优雅退出（grace call）             |    |
|  +------+---------------------------------------------------+    |
|         |                                                        |
|  +------v-----------------+  +-----------------+  +-----------+  |
|  |  Tool 系统              |  |  记忆系统        |  | 技能系统   |  |
|  | (40+ 工具)              |  | (MemoryManager) |  | (Skills)   |  |
|  | tools/*.py              |  |                 |  |            |  |
|  | auto-discover           |  | * builtin文件   |  | * 25个类别 |  |
|  | registry.py             |  | * FTS5会话搜索  |  | * 可选Hub  |  |
|  |                         |  | * Honcho辩证式  |  | * 自动创建 |  |
|  | Delegation子代理         |  | * Hindsight知识 |  | * Curator  |  |
|  | 并行任务分发             |  | * 5个其他后端   |  | * skills.io|  |
|  +-------------------------+  +-----------------+  +-----------+  |
|                                                                   |
|  +----------------+  +------------------+  +--------------------+ |
|  | Cron 调度器     |  | 插件系统          |  | 上下文引擎         | |
|  | (定时任务)      |  | (PluginManager)  |  | (可插拔压缩)       | |
|  +----------------+  +------------------+  +--------------------+ |
+------------------------------------------------------------------+
                           |
+------------------------------------------------------------------+
|                      执行环境层                                     |
|  +------+ +------+ +------+ +------+ +------+ +------+ +------+  |
|  |Local | |Docker| | SSH  | |Modal | |Dayton| |Singu-| |Vercel|  |
|  |      | |      | |      | |Server| | a    | |larity| |Sandbx|  |
|  +------+ +------+ +------+ +------+ +------+ +------+ +------+  |
+------------------------------------------------------------------+
```

### 3.2 文件依赖链

```
tools/registry.py  (无依赖 — 被所有工具文件导入)
       ^
tools/*.py  (每个在导入时调用 registry.register())
       ^
model_tools.py  (导入 tools/registry + 触发工具发现)
       ^
run_agent.py, cli.py, batch_runner.py, environments/
```

### 3.3 项目目录结构

```
hermes-agent/
├── run_agent.py          # AIAgent 类 — 核心对话循环 (~12k LOC)
├── model_tools.py        # 工具编排，discover_builtin_tools(), handle_function_call()
├── toolsets.py           # Toolset 定义，_HERMES_CORE_TOOLS 列表
├── cli.py                # HermesCLI 类 — 交互式 CLI 编排器 (~11k LOC)
├── hermes_state.py       # SessionDB — SQLite 会话存储 (FTS5 搜索)
├── hermes_constants.py   # get_hermes_home(), display_hermes_home() — Profile 感知路径
├── hermes_logging.py     # setup_logging() — agent.log / errors.log / gateway.log
├── batch_runner.py       # 并行批处理
├── trajectory_compressor.py # 轨迹压缩（RL 训练数据）
├── rl_cli.py             # RL 训练专用 CLI
├── agent/                # Agent 内部模块
│   ├── memory_manager.py    # 记忆管理器
│   ├── memory_provider.py   # 记忆提供商 ABC
│   ├── curator.py           # Curator 技能审查系统
│   ├── context_engine.py    # 上下文引擎 ABC
│   ├── context_compressor.py # 上下文压缩器
│   ├── prompt_builder.py    # System prompt 组装
│   ├── auxiliary_client.py  # 辅助模型客户端
│   ├── skill_commands.py    # 技能斜杠命令
│   ├── display.py           # KawaiiSpinner 动画
│   └── ... (40+ 模块)
├── tools/                # 工具实现 — 通过 tools/registry.py 自动发现
│   ├── registry.py          # 中心注册表
│   ├── delegate_tool.py     # 子代理委派
│   ├── memory_tool.py       # 内置记忆工具
│   ├── skills_tool.py       # 技能查看工具
│   ├── skill_manager_tool.py # 技能创建/编辑工具
│   ├── session_search_tool.py # FTS5 会话搜索
│   ├── terminal_tool.py     # 终端执行
│   ├── browser_tool.py      # 浏览器自动化
│   └── environments/        # 终端后端 (7种)
├── gateway/              # 消息网关
│   ├── run.py               # 网关运行器
│   ├── session.py           # 会话管理
│   └── platforms/           # 16个平台适配器
├── plugins/              # 插件系统
│   ├── memory/              # 记忆提供商 (8个)
│   ├── context_engine/      # 上下文引擎插件
│   ├── kanban/              # 多代理看板
│   ├── image_gen/           # 图像生成提供商
│   └── ... (13个插件目录)
├── skills/               # 内置技能 (25个类别)
├── optional-skills/      # 可选技能 (15个类别)
├── environments/         # RL 训练环境 (Atropos)
├── cron/                 # 调度器
├── hermes_cli/           # CLI 子命令、设置向导、皮肤引擎
├── acp_adapter/          # ACP 服务器 (VS Code/Zed/JetBrains)
├── ui-tui/               # Ink (React) 终端 UI
├── tui_gateway/          # TUI 的 Python JSON-RPC 后端
├── website/              # Docusaurus 文档站
└── tests/                # Pytest 测试套件
```

---

## 四、核心特性详解

### 4.1 零锁定的多模型支持

- 200+ 模型通过 OpenRouter，直接支持 15+ provider
- `hermes model` 一键切换，无需改代码
- 辅助模型独立配置（curator、vision、embedding、title gen、session search 各自可配不同模型）
- 支持 Provider Profiles（声明式提供者定义）、Credential Pools（凭证轮换）、Fallback Chain（自动故障转移）

**已支持的 Provider：**

| Provider | 说明 |
|----------|------|
| OpenAI | 官方 API |
| Anthropic | Claude 系列 |
| Google Gemini | 含 Cloud Code / Code Assist / Native 适配器 |
| Mistral | Mistral AI |
| AWS Bedrock | 托管模型 |
| Nous Portal | Nous 自有平台 |
| OpenRouter | 200+ 模型聚合 |
| HuggingFace | 开源模型 |
| xAI | Grok 系列 |
| Moonshot / Kimi | 小米 MiMo、月之暗面 |
| MiniMax | MiniMax |
| LM Studio | 本地模型 |
| Ollama | 本地推理 |
| OpenAI Compatible | 自定义端点 |

### 4.2 40+ 工具的自动发现系统

```python
# tools/registry.py — 中心注册表
from tools.registry import registry

registry.register(
    name="web_search",
    toolset="search",
    schema={"name": "web_search", "description": "...", "parameters": {...}},
    handler=web_search_handler,
    check_fn=check_requirements,
    requires_env=["SEARCH_API_KEY"],
)
```

**Toolset 分组（30+ 个）：**

browser, clarify, code_execution, cronjob, debugging, delegation, discord, feishu_doc, feishu_drive, file, homeassistant, image_gen, kanban, memory, messaging, moa, rl, safe, search, session_search, skills, spotify, terminal, todo, tts, video, vision, web, yuanbao

**关键设计：**
- `tools/*.py` 导入时自动 `registry.register()`，无需手动维护导入列表
- 工具必须出现在某个 toolset 中才会暴露给 agent
- `check_fn` 验证 API key/依赖，不满足的工具自动隐藏（不崩溃）
- 插件也能注册工具，无需修改核心代码

### 4.3 Delegation 子代理系统 (`tools/delegate_tool.py`)

**两种形态：**
- **单一目标**：`delegate_task(goal="Fix auth bug", context="...")`
- **并行批量**：`delegate_task(tasks=[{goal: "Research A"}, {goal: "Research B"}])`

**角色分层：**
- `role="leaf"`（默认）— 纯执行，不能再委派，不能调用 clarify/memory/send_message/execute_code
- `role="orchestrator"` — 可继续派生，最多 `max_spawn_depth=2` 层

**安全机制：**
- 子代理获得全新的对话历史（无父级上下文泄露）
- 独立的 task_id（隔离的终端会话和文件操作缓存）
- 受限的 toolset
- 心跳线程保持父级活动时间戳刷新
- 并发上限：`max_concurrent_children=3`

### 4.4 多 Profile 隔离

```
~/.hermes/                    # 默认 Profile
~/.hermes/profiles/coder/     # Coder Profile
~/.hermes/profiles/assistant/ # Assistant Profile
```

每个 Profile 独立拥有：
- config.yaml（配置）
- .env（API 密钥）
- SOUL.md（人格文件）
- sessions（会话数据）
- skills（技能库）
- cron jobs（定时任务）
- gateway（消息网关）
- state.db（SQLite 数据库）
- logs（日志）

**核心机制：** `_apply_profile_override()` 在 `hermes_cli/main.py` 中于所有模块导入**之前**设置 `HERMES_HOME` 环境变量。所有 `get_hermes_home()` 引用自动指向活动 profile。

### 4.5 16 个消息平台网关

| 平台 | 适配器文件 | 特色 |
|------|-----------|------|
| Telegram | `telegram.py` | Bot commands、语音转录、inline queries |
| Discord | `discord.py` | Threads、Forum topics、Voice |
| Slack | `slack.py` | Bolt 框架、Slash commands |
| WhatsApp | `whatsapp.py` | 通过 Baileys bridge |
| Signal | `signal.py` | signal-cli bridge |
| Matrix | `matrix.py` | E2E 加密、Threads |
| Email | `email.py` | IMAP/SMTP |
| SMS | `sms.py` | HTTP SMS gateway |
| 钉钉 | `dingtalk.py` | Stream 协议 |
| 飞书 | `feishu.py` | Lark API |
| 企业微信 | `wecom.py` | 回调模式 |
| 微信公众号 | `weixin.py` | 消息加解密 |
| QQ Bot | `qqbot.py` | QQ 官方 Bot |
| Mattermost | `mattermost.py` | WebSocket |
| BlueBubbles | `bluebubbles.py` | iMessage bridge |
| 元宝 | `yuanbao.py` | 字节跳动元宝 |

### 4.6 7 种终端后端

| 后端 | 适用场景 | 特色 |
|------|---------|------|
| Local | 本地开发 | 直接执行 |
| Docker | 隔离环境 | 容器化执行 |
| SSH | 远程服务器 | 通过 SSH 连接 |
| Singularity | HPC 环境 | 科学计算集群 |
| Modal | Serverless | 按需唤醒，空闲休眠 |
| Daytona | Serverless | 持久化开发环境 |
| Vercel Sandbox | Serverless | 无状态隔离执行 |

### 4.7 Cron 定时任务系统 (`cron/`)

**支持 5 种调度格式：**
- Duration: `"30m"`, `"2h"`, `"1d"`
- "every" 短语: `"every 2h"`, `"every monday 9am"`
- 5 字段 cron 表达式: `"0 9 * * *"`
- ISO 时间戳（一次性）: `"2026-06-01T09:00:00Z"`

**每任务可配：**
- 特定技能预加载
- 模型/provider 覆盖
- 预运行数据收集脚本
- 多平台投递
- 上下文链式引用（任务 A 的输出注入任务 B 的 prompt）
- 工作目录（加载目录下的 AGENTS.md/CLAUDE.md）

**安全机制：**
- 3 分钟硬中断（防止失控 agent 循环）
- 文件锁（`~/.hermes/cron/.tick.lock`）防止重复 tick
- 至多一次语义（错过的任务快进而非爆发）

### 4.8 插件系统 (`hermes_cli/plugins.py`)

**插件来源：**
1. 仓库内置：`<repo>/plugins/<name>/`
2. 用户安装：`~/.hermes/plugins/<name>/`
3. 项目级：`./.hermes/plugins/<name>/`
4. Pip 安装：`hermes_agent.plugins` 入口点

**插件种类（kind）：**
- `standalone` — 通用插件
- `backend` — 后端服务
- `exclusive` — 独占插件（如记忆提供商）
- `platform` — 网关平台适配器
- `model-provider` — 模型提供商

**插件能力：**
- 注册工具 (`ctx.register_tool()`)
- 注册生命周期钩子 (`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, ...)
- 注册 CLI 子命令 (`ctx.register_cli_command()`)
- 注册平台适配器 (`ctx.register_platform()`)
- 注册 slash 命令

### 4.9 agentskills.io 开放标准兼容

技能采用 YAML frontmatter + Markdown 格式（SKILL.md）：

```yaml
---
name: skill-name
description: Brief description
version: 1.0.0
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [tag1, tag2]
    related_skills: [other-skill]
---
```

**Progressive Disclosure 架构（三层）：**
1. **元数据层**（`skills_list`）— 名称、描述、标签（token 高效）
2. **内容层**（`skill_view`）— 完整 SKILL.md 指令
3. **关联文件层**（`skill_view` + 路径）— references、templates、scripts 按需加载

### 4.10 安全防护体系

| 安全层 | 文件 | 防护内容 |
|--------|------|---------|
| 上下文文件扫描 | `agent/prompt_builder.py` | AGENTS.md/.cursorrules/SOUL.md 注入检测 |
| 记忆内容扫描 | `tools/memory_tool.py` | 13 种威胁模式 + 不可见 Unicode |
| 技能安全扫描 | `tools/skills_guard.py` | 外部技能安装扫描 |
| 文件路径安全 | `tools/path_security.py` | 路径遍历攻击防护 |
| 命令审批 | `tools/approval.py` | 可配置的命令白名单 |
| 工具护栏 | `agent/tool_guardrails.py` | 工具调用验证拦截 |
| URL 安全 | `tools/url_safety.py` | SSRF 防护 |
| 凭证安全 | `agent/redact.py` | 日志中的凭证脱敏 |

---

## 五、关键技术实现细节

### 5.1 AIAgent 对话循环核心逻辑

```python
# 简化的对话循环
while (api_call_count < self.max_iterations
       and self.iteration_budget.remaining > 0) \
        or self._budget_grace_call:
    if self._interrupt_requested:
        break

    # 记忆预取
    context = self._memory_manager.prefetch_all(user_message)

    # LLM 调用（带工具 schema）
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tool_schemas
    )

    if response.tool_calls:
        for tool_call in response.tool_calls:
            # 并行执行安全工具，串行执行有状态工具
            result = handle_function_call(
                tool_call.name, tool_call.args, task_id
            )
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        # 无工具调用 = 最终响应
        return response.content
```

**关键设计：**
- 迭代预算系统（`IterationBudget`）线程安全地消耗配额
- 优雅退出（grace call）：预算耗尽后的最后一轮调用
- 工具并行执行：安全工具（只读、无共享状态）通过 ThreadPoolExecutor 并发
- 工具参数自动修复：模型输出的畸形 JSON 自动修复

### 5.2 技能创建流程

```
1. Agent 完成复杂任务
2. 系统提示引导："After completing a complex task, save the approach as a skill..."
3. Agent 调用 skill_manage(action="create", name="...", description="...", content="...")
4. 系统创建目录结构：
   ~/.hermes/skills/my-skill/
   ├── SKILL.md
   ├── references/
   ├── templates/
   ├── scripts/
   └── assets/
5. .usage.json 记录元数据：created_by: "agent", state: "active"
6. 下次会话自动出现在 skills_list 中
```

### 5.3 记忆上下文注入流程

```
1. 会话开始：
   - 冻结 MEMORY.md 和 USER.md 快照注入 system prompt
   - 触发 memory_manager.build_system_prompt()

2. 每轮开始前：
   - memory_manager.prefetch_all(user_message) 召回相关记忆
   - 结果包装在 <memory-context> 标签中，标注 "NOT new user input"

3. 每轮结束后：
   - memory_manager.sync_all(user_msg, assistant_response)
   - 外部提供商异步持久化

4. 上下文压缩前：
   - memory_manager.on_pre_compress(messages)
   - 提供商提取即将被丢弃的关键信息

5. 流式输出：
   - StreamingContextScrubber 实时移除 <memory-context> 标签
   - 防止记忆上下文泄露到用户界面
```

### 5.4 Config 加载三路径

| 加载器 | 使用场景 | 位置 |
|--------|---------|------|
| `load_cli_config()` | CLI 模式 | `cli.py` — 合并 CLI 默认值 + 用户 YAML |
| `load_config()` | 工具配置、设置向导 | `hermes_cli/config.py` — 合并 DEFAULT_CONFIG + 用户 YAML |
| 直接 YAML 读取 | 网关运行时 | `gateway/run.py` + `gateway/config.py` |

---

## 六、使 Hermes "可进化"的关键技术总结

| 关键技术 | 实现方式 | 进化效果 |
|----------|----------|----------|
| **Skill 创建** | Agent 完成复杂任务后调用 `skill_manage(create)` 将成功方案抽象为技能 | 从单次经验 → 可复用知识 |
| **Skill patch** | 使用中发现技能不足，调用 `skill_manage(patch)` 定向修改 | 使用中持续改进 |
| **Curator** | 后台 fork 辅助 agent 周期性审查技能，合并、归档、改进 | 自动维护知识库质量 |
| **多层记忆** | 文件记忆 + FTS5 会话搜索 + 外部提供商（Honcho 辩证式建模） | 跨会话积累和检索经验 |
| **可插拔架构** | MemoryProvider ABC + ContextEngine ABC + PluginManager | 第三方可扩展新能力 |
| **Trajectory Compression** | 压缩 Agent 运行轨迹用于 RL 训练 | 训练下一代模型 |
| **OPD 训练** | On-Policy Distillation 提供 token 级别训练信号 | 精细化工具调用能力 |
| **Provider 无锁定** | OpenAI 兼容的统一接口，15+ provider 适配器 | 灵活切换最优模型 |
| **多平台网关** | 16 个平台适配器统一 Gateway 进程 | 跨环境一致性 |
| **Multi-Profile** | 独立 HERMES_HOME，profile 级别隔离 | 多 Agent 协作 |

---

## 七、设计哲学

Hermes Agent 的核心设计哲学是：**Agent 不仅仅是执行任务的工具，而是一个能从每次交互中学习、积累经验、自我改进的持续进化的系统。**

**层次化记忆架构：**

| 记忆类型 | 实现方式 | 类比 |
|----------|----------|------|
| 工作记忆 | 当前会话上下文（SQLite messages） | 短期记忆 |
| 情景记忆 | 完整会话历史 + FTS5 搜索 | 回忆能力 |
| 语义记忆 | 提取的事实（Hindsight, Mem0） | 知识积累 |
| 程序记忆 | 技能（how-to 知识） | 技能习得 |
| 用户模型 | Honcho 辩证式建模 | 理解用户 |
| 训练信号 | 轨迹压缩 + OPD | 进化基础 |

Curator + Skill 系统 + 多层记忆 + 可插拔架构，共同构成了一个**真正的闭环学习系统** — Agent 每次交互都在积累经验，每次使用技能都在改进知识，每次审查都在优化知识库。

---

*报告生成工具：Claude Code*
*项目地址：https://github.com/NousResearch/hermes-agent*
