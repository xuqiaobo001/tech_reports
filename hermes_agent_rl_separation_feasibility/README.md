# Hermes-Agent 常规对话与 RL 训练分离部署可行性分析

## 1. 核心结论：已经天然分离，可以独立部署，按需触发 RL 训练

从源码来看，hermes-agent 的常规 Agent 对话和 RL 训练在架构上已经做到了**单向依赖 + 依赖可选**，分离部署不仅可行，而且已经是设计意图。

## 2. 源码级证据

### 2.1 Atropos 依赖完全隔离在 `environments/` 目录

```
atroposlib 导入位置（全量搜索结果）：
──────────────────────────────────────
environments/hermes_base_env.py        ← 基类
environments/hermes_swe_env/           ← SWE 环境
environments/terminal_test_env/        ← 终端测试
environments/agentic_opd_env.py        ← OPD 环境
environments/web_research_env.py       ← Web 研究
environments/benchmarks/tblite/        ← tblite 评测
environments/benchmarks/terminalbench_2/ ← TB2 评测
environments/benchmarks/yc_bench/      ← YC 评测

核心 Agent 代码的 atroposlib 导入：
──────────────────────────────────────
run_agent.py     → 零导入 ✅
model_tools.py   → 零导入 ✅
cli.py           → 零导入 ✅
tools/*.py       → 仅 rl_training_tool.py 有 1 处 fallback 导入 ✅
```

### 2.2 反向依赖检查：核心代码不依赖 RL

```
从 environments/ 导入的位置：
──────────────────────────────────────
run_agent.py     → 无 ✅
model_tools.py   → 无 ✅
cli.py           → 无 ✅
```

**核心 Agent 完全不知道 RL 训练的存在。**

### 2.3 依赖声明已做可选隔离

`pyproject.toml:95-101`：
```toml
[project.optional-dependencies]
rl = [
  "atroposlib @ git+https://github.com/NousResearch/atropos.git@...",
  "tinker @ git+https://github.com/thinking-machines-lab/tinker.git@...",
  "fastapi>=0.104.0,<1",
  "uvicorn[standard]>=0.24.0,<1",
  "wandb>=0.15.0,<1",
]
```

```bash
pip install hermes-agent           # 常规对话：无需任何 RL 依赖
pip install hermes-agent[rl]       # RL 训练：按需安装
```

### 2.4 共享代码路径分析

两者共享的**唯一关键代码**是工具执行路径：

```
常规对话模式:
  AIAgent._invoke_tool()
    → model_tools.handle_function_call()  ←── 共享入口
      → tools.registry.dispatch()
        → 具体 tool 实现

RL 训练模式:
  HermesAgentLoop.run()
    → model_tools.handle_function_call()  ←── 同一个函数
      → tools.registry.dispatch()
        → 具体 tool 实现（同一个工具实例）
```

这**不是耦合问题**，而是**有意的设计**——确保 RL 训练时模型调用的工具行为与生产 Agent 完全一致。

## 3. 分离部署架构方案

### 方案 A：单进程双模式（当前架构，最简方案）

```
┌───────────────────────────────────────────────┐
│            hermes-agent 主进程                  │
│                                                │
│  ┌─────────────────┐  ┌─────────────────────┐ │
│  │  常规对话模式     │  │  RL 训练模式         │ │
│  │  cli.py / API    │  │  rl_cli.py          │ │
│  │  AIAgent         │  │  rl_training_tool   │ │
│  │                  │  │                      │ │
│  │  工具集: 全部     │  │  工具集: terminal,   │ │
│  │  (不含 rl)       │  │  web, rl            │ │
│  └────────┬─────────┘  └──────────┬──────────┘ │
│           │                       │            │
│           └───────┬───────────────┘            │
│                   ▼                            │
│         model_tools.handle_function_call()     │
│                   │                            │
│         tools/registry.dispatch()              │
│                   │                            │
│         ┌─────────┴──────────┐                 │
│         │  Terminal Backends  │                 │
│         │  (task_id 隔离)     │                 │
│         └────────────────────┘                 │
└───────────────────────────────────────────────┘
```

**当前已支持。** 启动方式：

```bash
# 常规对话
hermes "帮我写个 Python 函数"

# 按需触发 RL 训练（通过 Agent 自身的工具调用）
hermes "用 rl_start_training 开始训练 GSM8k 环境"

# 或者独立 RL CLI
python rl_cli.py "Train a model on GSM8k"
```

### 方案 B：双进程分离部署（推荐用于生产）

```
┌────────────────────────┐     ┌────────────────────────┐
│    Agent 服务进程       │     │    RL 训练进程          │
│    (常驻在线)           │     │    (按需启停)           │
│                        │     │                        │
│  pip install hermes-   │     │  pip install hermes-   │
│         agent          │     │        agent[rl]        │
│                        │     │                        │
│  常规对话 API           │     │  Atropos + Tinker      │
│  CLI / Gateway / 机器人 │     │  WandB 指标            │
│  技能系统 / 记忆系统    │     │  VLLM ManagedServer    │
│                        │     │                        │
│  不加载 RL 工具集       │     │  不加载消息网关         │
│  不需要 GPU             │     │  需要 GPU 集群         │
└────────────────────────┘     └────────────────────────┘
```

**实现步骤：**

1. **Agent 服务**（无 GPU 机器）：
```bash
pip install hermes-agent  # 不安装 RL 依赖
hermes --gateway telegram  # 或 API 模式
```

2. **RL 训练**（GPU 机器），按需触发：
```bash
pip install hermes-agent[rl]
python -m environments.hermes_swe_env serve \
  --openai.server_type vllm \
  --env.terminal_backend modal
```

3. **触发机制** — RL 训练由 Agent 通过 `rl_training_tool.py` 间接启动子进程：
```python
# tools/rl_training_tool.py:56-59
HERMES_ROOT = Path(__file__).parent.parent
TINKER_ATROPOS_ROOT = HERMES_ROOT / "tinker-atropos"
ENVIRONMENTS_DIR = TINKER_ATROPOS_ROOT / "tinker_atropos" / "environments"
```

训练工具通过 **子进程管理** 启动独立的 Atropos 训练流程，与主 Agent 进程完全隔离。

### 方案 C：完全微服务化（最大灵活性）

```
┌──────────────┐
│  API Gateway │ ← hermes-agent[gateway]
└──────┬───────┘
       │
       ├──→ ┌─────────────────┐
       │    │ Agent API 服务   │ ← 常规对话，无状态
       │    │ (K8s Deployment) │
       │    └─────────────────┘
       │
       └──→ ┌─────────────────┐
            │ RL Training Job  │ ← K8s Job / Modal Function
            │ (按需创建)       │
            │                  │
            │ 输入: 环境配置    │
            │ 输出: WandB 指标  │
            │       模型检查点  │
            └─────────────────┘
```

## 4. 关键分离点源码分析

### 4.1 工具集天然隔离

`toolsets.py` 中 RL 工具只是普通工具集的一个分类：

```python
# rl 工具集中的工具
"rl": ["rl_list_environments", "rl_select_environment",
       "rl_start_training", "rl_check_status", ...]
```

常规对话时只需不启用 `"rl"` 工具集即可完全隔离。

### 4.2 终端后端 task_id 隔离

`tools/terminal_tool.py` 中沙箱通过 `task_id` 隔离：

```python
# 全局状态（两个模式共享，但通过 task_id 逻辑隔离）
_active_environments: Dict[str, Any] = {}

# 每个 rollout 独立
task_id = str(uuid.uuid4())  # hermes_base_env.py:498
```

常规对话和 RL 训练的沙箱实例通过不同的 `task_id` 天然隔离，互不干扰。

### 4.3 RL 训练是子进程管理

`tools/rl_training_tool.py` 不直接调用 Atropos API，而是通过 **子进程** 管理：

```python
# rl_training_tool.py — 启动训练进程
subprocess.Popen(
    ["python", "-m", "tinker_atropos.launch_training", ...],
    ...
)
```

这意味着 RL 训练可以在完全独立的进程中运行，甚至可以在不同的机器上。

### 4.4 唯一需要注意的耦合点

`tools/rl_training_tool.py:253` 有一个 fallback 导入：

```python
try:
    from atroposlib.envs.base import BaseEnvConfig
    config_class = BaseEnvConfig
except ImportError:
    config_class = None  # 非 RL 模式下优雅降级
```

已做了 **ImportError 保护**——如果没安装 `atroposlib`，不会崩溃。

## 5. 按需触发 RL 训练的具体流程

```
用户对话 → Agent API → 识别训练意图
    │
    ├── 方式 1: Agent 工具调用
    │   rl_select_environment("gsm8k")
    │   rl_edit_config(total_steps=500)
    │   rl_start_training()
    │   → 子进程启动 Atropos 训练
    │
    ├── 方式 2: API 触发
    │   POST /api/rl/start
    │   → 同上
    │
    └── 方式 3: 定时任务
        cron: "0 2 * * *"  # 每天凌晨 2 点
        → 检查数据积累量
        → 满足阈值则自动触发训练
```

**训练状态查询：**
```
rl_check_status() → 读取子进程状态文件
rl_get_results()  → 解析 WandB 指标
```

**训练产物回流：**
```
Atropos 训练完成
    → 模型检查点保存到 HuggingFace / S3
    → WandB 记录指标
    → Agent 通知用户训练完成
    → 新模型部署到 VLLM 服务
```

## 6. 可行性总结

| 维度 | 当前状态 | 分离难度 | 说明 |
|------|----------|----------|------|
| 依赖隔离 | 已完成 | 无需改动 | `pip install hermes-agent` vs `hermes-agent[rl]` |
| 代码隔离 | 已完成 | 无需改动 | Atropos 仅在 `environments/` 中，核心代码零依赖 |
| 运行时隔离 | task_id 级 | 无需改动 | 沙箱实例通过 UUID 天然隔离 |
| 进程隔离 | 子进程管理 | 无需改动 | RL 训练已是独立子进程 |
| 触发机制 | 工具调用 / CLI | 需封装 | 可包装为 API 或消息队列触发 |
| 模型更新 | 手动 | 需开发 | 训练完成后自动更新推理服务需额外开发 |

**结论：从源码架构看，hermes-agent 的常规对话和 RL 训练已经是单向依赖、依赖可选的设计。分离部署不需要重构，只需要在部署层面做配置隔离。**

## 7. 架构依赖关系图

```
┌─────────────────────────────────────────────────────────────┐
│                    CONVERSATIONAL AGENT                      │
│  (run_agent.py, model_tools.py, tools/, cli.py, gateway/)   │
│                  ✅ Works independently                      │
└─────────────────────────────────────────────────────────────┘
                            ↑
                            │  Imports from model_tools.py
                            │  (handle_function_call, get_tool_definitions)
                            │
┌─────────────────────────────────────────────────────────────┐
│              RL ENVIRONMENTS (environments/)                 │
│  - hermes_base_env.py (imports from model_tools)            │
│  - agent_loop.py (uses handle_function_call)                │
│  - tool_context.py (uses handle_function_call)              │
└─────────────────────────────────────────────────────────────┘
                            ↑
                            │  Imports from atroposlib
                            │
┌─────────────────────────────────────────────────────────────┐
│                    ATROPOS LIBRARY                           │
│              (tinker-atropos submodule)                      │
└─────────────────────────────────────────────────────────────┘
```

依赖方向：**RL 环境 → Agent 核心 → 零 Atropos 依赖**。反向无依赖。

---

*Report generated on 2026-05-10*
