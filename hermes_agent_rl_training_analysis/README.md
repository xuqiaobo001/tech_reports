# Hermes-Agent RL 训练系统技术分析报告

## 1. 项目概览

Hermes-Agent 是 Nous Research 构建的**自改进 AI Agent 框架**，核心设计是在通用工具调用 Agent 能力之上构建完整的 **RL 后训练管线**。

该系统有**双重角色**：
- **独立 Agent 系统**：通用工具调用型 AI Agent
- **RL 数据生成与训练引擎**：为工具调用模型的 RL 训练提供完整环境

## 2. 系统架构

```
┌───────────────────────────────────────────────────────────────┐
│                        用户交互层                               │
│   cli.py (交互式) │ rl_cli.py (RL专用) │ batch_runner.py (批量)│
└──────────┬────────────────┬───────────────────┬──────────────┘
           │                │                   │
┌──────────▼────────────────▼───────────────────▼──────────────┐
│                     Agent 核心层                               │
│   run_agent.py (AIAgent)  │  model_tools.py (工具注册表)       │
│   agent/prompt_builder    │  agent/trajectory (轨迹保存)       │
│   toolset_distributions   │  agent/context_compressor         │
└──────────┬────────────────────────────────┬──────────────────┘
           │                                │
┌──────────▼──────────┐     ┌───────────────▼──────────────────┐
│    工具层 (40+)      │     │        RL 训练环境层              │
│ terminal_tool       │     │ hermes_base_env.py (基类)         │
│ file_tools          │     │ agent_loop.py (多轮引擎)          │
│ browser_tool        │     │ tool_context.py (奖励验证)        │
│ web_search          │     │ tool_call_parsers/ (12种解析器)   │
│ vision_tool         │     │ hermes_swe_env/ (SWE环境)        │
│ rl_training_tool    │     │ benchmarks/ (TB2/tblite/yc_bench) │
└──────────┬──────────┘     └───────────────┬──────────────────┘
           │                                │
┌──────────▼────────────────────────────────▼──────────────────┐
│                      外部服务层                                │
│  Atropos Framework │ VLLM/SGLang │ WandB │ Modal/Docker      │
└──────────────────────────────────────────────────────────────┘
```

## 3. RL 训练管线核心设计

### 3.1 两阶段训练架构

系统采用 **Phase 1 / Phase 2** 架构（`hermes_base_env.py:328-344`）：

| 维度 | Phase 1 (OpenAI Server) | Phase 2 (VLLM ManagedServer) |
|------|------------------------|------------------------------|
| **用途** | SFT 数据生成、验证器测试、评估 | 完整 RL 训练 |
| **API 端点** | `/v1/chat/completions` | VLLM `/generate` |
| **工具调用解析** | 服务端原生解析 | 客户端解析器（12种格式） |
| **Token 数据** | 占位符 Token（不适合训练） | 精确 Token ID + Logprobs + Masks |
| **数据输出** | `ScoredDataItem` 含 messages | `SequenceNode` 含精确 tokens/masks |
| **推理追踪** | 多提供商格式兼容 | `<think` block 保留 |

### 3.2 核心基类: HermesAgentBaseEnv

位于 `environments/hermes_base_env.py:221`，继承自 Atropos 框架的 `BaseEnv`。

**关键生命周期：**
```
collect_trajectories()        ← 每组调用一次，解析工具集
  └── collect_trajectory()    ← 被调用 group_size 次，并行执行
        ├── format_prompt()   ← 格式化用户消息
        ├── HermesAgentLoop.run() ← 多轮工具调用循环
        ├── compute_reward()  ← 通过 ToolContext 计算奖励
        └── 构造 ScoredDataItem ← 封装 tokens/masks/scores
```

**子类只需实现 5 个抽象方法：**
- `setup()` — 加载数据集
- `get_next_item()` — 返回下一个训练样本
- `format_prompt()` — 数据条目 → 用户消息
- `compute_reward()` — 使用 ToolContext 打分
- `evaluate()` — 周期性评估

### 3.3 多轮 Agent 循环: HermesAgentLoop

位于 `environments/agent_loop.py:119`，是 RL 训练中 Agent 的**多轮工具调用引擎**。

**核心循环：**
```
for turn in range(max_turns):
    response = server.chat_completion(messages, tools=tool_schemas)

    if response.tool_calls:
        for tc in response.tool_calls:
            result = run_in_executor(handle_function_call, ...)
            messages.append({"role": "tool", "content": result})
    else:
        return AgentResult(messages, finished_naturally=True)
```

**关键设计决策：**

1. **线程池隔离**（`agent_loop.py:33`）：工具执行通过 128 线程的 `ThreadPoolExecutor` 运行，避免 Modal/Docker 后端的 `asyncio.run()` 与 Atropos 事件循环死锁

2. **Fallback 工具解析**（`agent_loop.py:268-289`）：当 VLLM 的 `ToolCallTranslator` 无法解析时，自动回退到 Hermes XML 格式解析器

3. **推理内容提取**（`agent_loop.py:81-116`）：兼容三种提供商格式（`reasoning_content`、`reasoning`、`reasoning_details`）

4. **工具结果预算控制**（`agent_loop.py:458-480`）：超过阈值的工具结果自动持久化到沙箱磁盘，替换为预览摘要，防止单轮上下文溢出

### 3.4 ToolContext: 奖励函数与沙箱的桥梁

位于 `environments/tool_context.py:66`。

**核心原理：** 每个 rollout 的 `task_id` 在奖励计算阶段保持不变，因此 `ctx.terminal()` 访问的是**模型训练期间使用的同一个沙箱**——所有文件、进程、浏览器状态完整保留。

**奖励函数示例（SWE 任务）：**
```python
async def compute_reward(self, item, result, ctx):
    # 在模型的沙箱中运行测试
    test_result = ctx.terminal(f"cd /workspace && python3 -c '{test_code}'")
    if test_result["exit_code"] == 0:
        return 1.0  # 完全正确

    # 部分奖励：检查是否创建了文件
    file_check = ctx.terminal("find /workspace -name '*.py'")
    if file_check["output"].strip():
        return 0.1  # 部分完成

    return 0.0
```

**ToolContext 提供的能力：**

| 类别 | 方法 | 用途 |
|------|------|------|
| 终端 | `terminal(cmd)` | 在模型沙箱中执行命令 |
| 文件 | `read_file()`, `write_file()` | 读写沙箱文件 |
| 传输 | `upload_file()`, `download_file()` | 二进制安全的文件传输（支持分块） |
| 目录 | `upload_dir()`, `download_dir()` | 整目录递归传输 |
| 搜索 | `search(query)` | 在沙箱文件系统中搜索 |
| Web | `web_search()`, `web_extract()` | 网络搜索与内容提取 |
| 浏览器 | `browser_navigate()`, `browser_snapshot()` | 浏览器自动化 |
| 通用 | `call_tool(name, args)` | 调用任意工具的通用接口 |

## 4. 数据生成管线

### 4.1 批量运行器 (batch_runner.py)

**数据流：**
```
Dataset (JSONL)
    ↓
BatchRunner (多进程 Pool)
    ↓ (每个 prompt)
toolset_distributions.py → 概率采样工具集
    ↓
AIAgent.run_conversation() → 工具执行 → 轨迹保存
    ↓
checkpoint.json (断点续传) + batch_N.jsonl
    ↓ (最终合并)
trajectories.jsonl + statistics.json
```

**关键特性：**
- **内容匹配恢复**：基于 prompt 文本（非索引）匹配已完成条目，允许数据集变更后恢复
- **工具集概率采样**：支持 7 种预定义分布（`default`、`image_gen`、`research`、`science`、`development`、`safe`、`terminal_tasks`）
- **推理质量过滤**：自动丢弃零推理覆盖率的样本
- **Per-prompt 容器覆盖**：数据集行级别的 Docker 镜像覆盖

**工具集分布示例：**
```python
# development 分布
"development": {
    "terminal": 80,   # 80% 概率启用
    "file": 80,
    "moa": 60,        # 推理工具
    "web": 30,
    "vision": 10
}
```

### 4.2 轨迹压缩 (trajectory_compressor.py)

**目的**：将超长轨迹压缩到目标 Token 预算内（默认 15,250 tokens），保留训练信号。

**压缩策略：**
```
[system] [human] [gpt₁] [tool₁] | [gpt₂] [tool₂] ... [gptₙ] [toolₙ] | [gpt_final] [tool_final]
   ←── 受保护的头部 ──→         ←── 可压缩的中间区域 ──→           ←── 受保护的尾部 ──→
                                      ↓ LLM 摘要替换
                              [CONTEXT SUMMARY]: 模型执行了X，发现Y...
```

- 保护头部（system、human、首条 gpt、首个 tool）
- 保护尾部（最后 4 轮）
- 仅压缩所需的最少中间轮次
- 使用 Gemini Flash 等快速模型生成摘要
- 异步并行处理（最多 50 并发 API 调用）

**配置示例：**
```yaml
target_max_tokens: 15250
summary_target_tokens: 750
protect_last_n_turns: 4
tokenizer_name: "moonshotai/Kimi-K2-Thinking"
```

### 4.3 工具调用解析器

系统内置 12 种客户端工具调用解析器（`environments/tool_call_parsers/`），支持：

| 解析器 | 模型系列 |
|--------|----------|
| `hermes_parser` | Hermes 系列 |
| `mistral_parser` | Mistral 系列 |
| `llama_parser` | Llama 3 系列 |
| `qwen_parser` | Qwen 系列 |
| `qwen3_coder_parser` | Qwen3 Coder |
| `deepseek_v3_parser` | DeepSeek V3 |
| `deepseek_v3_1_parser` | DeepSeek V3.1 |
| `kimi_k2_parser` | Kimi K2 |
| `glm45_parser` / `glm47_parser` | GLM-4.5 / GLM-4.7 |
| `longcat_parser` | LongCat |

这些解析器在 Phase 2 中将模型原始文本输出转换为标准 OpenAI `tool_calls` 格式，使 `HermesAgentLoop` 无需关心底层模型差异。

## 5. 环境实现矩阵

| 环境 | 位置 | 任务类型 | 终端后端 | 奖励机制 |
|------|------|----------|----------|----------|
| `HermesSweEnv` | `hermes_swe_env/` | 软件工程 | Modal | 运行测试 + 文件创建部分奖励 |
| `TerminalBench2Eval` | `benchmarks/terminalbench_2/` | 终端任务 (Eval-only) | Docker/Modal | `test.sh` 二元通过/失败 |
| `TbliteEval` | `benchmarks/tblite/` | 轻量终端任务 | Docker/Modal | 测试执行验证 |
| `YcBenchEval` | `benchmarks/yc_bench/` | YC 基准评估 | Docker/Modal | 任务完成度验证 |

## 6. 关键技术特性

### 6.1 终端沙箱后端

支持 7 种终端后端（`HermesAgentEnvConfig.terminal_backend`）：

| 后端 | 隔离级别 | 适用场景 |
|------|----------|----------|
| `local` | 无隔离 | 开发调试 |
| `docker` | 容器隔离 | 本地训练 |
| `modal` | 云端隔离 | 生产 RL 训练 |
| `daytona` | 云端隔离 | 企业部署 |
| `ssh` | 远程机器 | 分布式 |
| `singularity` | HPC 环境 | 集群训练 |
| `vercel_sandbox` | 云端临时 | 快速评估 |

### 6.2 WandB 集成

系统在 `HermesAgentBaseEnv.wandb_log()` 中自动追踪：
- 训练奖励（`train/avg_reward`、`train/pass_rate`）
- 工具错误统计（`train/tool_errors_count`、`train/tool_error_details`）
- 结构化轨迹展示（工具调用、推理内容、工具结果）
- 评估指标

### 6.3 工具结果预算控制

`tools/budget_config.py` 提供三层预算控制：
1. **Per-tool 阈值**：每个工具的输出大小限制（如 `terminal: 10K chars`）
2. **Per-turn 聚合预算**：单轮内所有工具结果的总大小限制
3. **预览替换**：超限结果持久化到沙箱磁盘，内联显示前 N 字符预览

## 7. RL 训练端到端流程

```
1. 数据准备
   HuggingFace Dataset → batch_runner.py → 轨迹 (JSONL)
   → trajectory_compressor.py → 压缩后的训练数据

2. Phase 1: SFT 数据生成与验证
   vllm serve Model --tool-parser hermes
   python hermes_swe_env.py serve \
     --openai.server_type openai \
     --env.terminal_backend modal

   输出: SFT 训练数据 (占位符 tokens + messages)

3. Phase 2: 完整 RL 训练
   python hermes_swe_env.py serve \
     --openai.server_type vllm \
     --env.tool_call_parser hermes \
     --env.terminal_backend modal

   Atropos 框架:
   ├── 启动 VLLM ManagedServer
   ├── Worker 并行收集 group_size 个 rollout
   ├── 每个 rollout: Agent Loop → 工具执行 → 奖励计算
   ├── 构建 ScoredDataGroup (tokens + masks + scores)
   ├── GRPO/PPO 优化策略
   └── WandB 指标追踪

4. 评估
   TerminalBench2 / tblite / yc_bench
   → 并行运行所有任务 → 测试验证 → 聚合 pass rate
```

## 8. 设计亮点总结

1. **沙箱状态连续性**：ToolContext 通过 `task_id` 确保奖励函数访问的是模型训练时同一个沙箱，这是验证 Agent 行为的关键

2. **模型无关架构**：12 种工具调用解析器 + 两阶段模式使系统能训练任何开源模型（Llama、Qwen、DeepSeek、Mistral、Kimi、GLM 等）

3. **异步安全设计**：ThreadPoolExecutor 隔离工具执行，避免 Atropos 事件循环与沙箱后端（Modal/Docker）的 `asyncio.run()` 死锁

4. **渐进式训练**：Phase 1（便宜、快速）用于数据验证，Phase 2（昂贵、精确）用于实际 RL 训练，降低试错成本

5. **弹性数据生成**：内容匹配恢复 + 断点续传 + 工具集概率采样 + 推理质量过滤，确保大规模数据生成的可靠性

---

*Report generated on 2026-05-10*
