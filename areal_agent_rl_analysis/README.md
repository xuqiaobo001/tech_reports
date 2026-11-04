# AReaL Agent RL 业务逻辑全流程分析

> 分析对象：[AReaL](https://github.com/antinomyhq/areal)（基于本地源码 `/root/verl_rl/AReaL`）
> 分析日期：2026-11-03
> 关键词：Agentic RL、异步 rollout、多轮工具调用、staleness 控制、GRPO/PPO

---

## 0. 总体架构：角色与组网

AReaL 是**异步分离式**（disaggregated）RL 训练框架，推理（SGLang/vLLM）与训练（FSDP2/Megatron）跑在不同 GPU 上，靠 HTTP 控制面 + NCCL/RTensor 数据面组网：

```
┌─────────────────── Trainer 节点（CPU 编排 + GPU 训练）───────────────────┐
│  RLTrainer 主循环 (areal/trainer/rl_trainer.py:846)                      │
│    ├─ RolloutController (infra/controller/rollout_controller.py:165)     │
│    │    └─ BatchTaskDispatcher + StalenessManager                        │
│    └─ TrainController → FSDP/Megatron Training Workers                   │
└──────────────┬───────────────────────────────────────────────────────────┘
               │ HTTP RPC (Flask /call) + 回调 future        ▲ XCCL/磁盘推权重
               ▼                                           │ RTensor 拉轨迹
┌─────────────── Rollout 节点 ──────────────────────────────────────────────┐
│  WorkflowExecutor (infra/workflow_executor.py:870)                        │
│    └─ AsyncTaskRunner (uvloop 线程, 成百上千 workflow 协程并发)             │
│         └─ workflow.arun_episode() ⇄ 工具沙箱(Daytona)/奖励函数            │
│  SGLang/vLLM GPU 子进程 (continuous batching)  [+ Agent Proxy Server]     │
└────────────────────────────────────────────────────────────────────────────┘
```

**角色清单**：

| 角色 | 位置 | 职责 |
|---|---|---|
| RLTrainer | `areal/trainer/rl_trainer.py:200` | 训练主循环、版本发布、checkpoint |
| RolloutController | `areal/infra/controller/rollout_controller.py:165` | 分离模式下 trainer 侧的推理引擎代理，持有 dispatcher + staleness 管理器 |
| TrainController | `areal/infra/controller/train_controller.py:201` | 训练算子 RPC 分发、FFD 分片、结果合并 |
| WorkflowExecutor | `areal/infra/workflow_executor.py:870` | rollout worker 上执行 workflow episode |
| BatchTaskDispatcher | `areal/infra/workflow_executor.py:339` | 生产者-消费者双线程任务分发 |
| StalenessManager | `areal/infra/staleness_manager.py:20` | off-policy 程度准入控制 |
| AsyncTaskRunner | `areal/infra/async_task_runner.py:73` | uvloop event loop 线程，高并发协程执行 |
| Agent Proxy Server | `areal/experimental/openai/proxy/` | OpenAI/Anthropic 兼容代理 + InteractionCache |
| Daytona Sandbox | `areal/infra/sandbox/runner.py:102` | agent 工具执行沙箱（断网/限额） |

---

## 1. Agent RL 的两种接入模式

AReaL 支撑 Agent RL 的核心抽象是 `RolloutWorkflow.arun_episode(engine, data)`（`areal/api/workflow_api.py:14`）——**一次调用 = 一条完整 episode 轨迹**，内部可自由进行多轮推理/工具调用，返回 token 级训练数据（返回 `None` 表示拒绝该轨迹）。

| | **Native tensor 模式** | **SDK Agent / Proxy 模式** |
|---|---|---|
| 代表 | `workflow/multi_turn.py` | `experimental/openai/`（ArealOpenAI）、`workflow/anthropic/`、`workflow/langchain/` |
| 控制流 | workflow 手写多轮循环 | OpenAI Agents SDK / Claude Agent SDK / LangChain 驱动，工具循环在 SDK 内 |
| 与引擎交互 | `await engine.agenerate(ModelRequest)` 直接拿 token | 所有 LLM 调用被路由到 AReaL 的 OpenAI 兼容 proxy（或 `ArealOpenAI` 客户端直接绑 engine），proxy 侧自动记录 token + logprob |
| 训练数据 | workflow 拼装 tensor dict（loss_mask/turn_ids/versions） | `client.export_interactions()` 从 `InteractionCache` 导出 `InteractionWithTokenLogpReward` |
| reward | episode/每轮内调 `AsyncRewardWrapper` | `client.apply_reward_discount(turn_discount=0.9)` + episode 结束算分 |

**Proxy 关键端点**（`docs/en/reference/agent_workflow.md:383`）：

```
POST /grant_capacity      # staleness 控制的容量预留
POST /rl/start_session    # 返回 session_id + 会话级 API key
POST /v1/chat/completions # OpenAI 兼容生成（proxy 做 tokenize，engine 生成带 logprob）
POST /v1/responses        # OpenAI Responses API
POST /v1/messages         # Anthropic 兼容
POST /rl/set_reward       # 赋 reward
POST /rl/end_session      # 结束会话
POST /export_trajectories # 带 reward 折扣导出轨迹
```

会话生命周期：预留容量 → 建会话 → 多次 LLM 调用（token 级信息自动存入 `InteractionCache`）→ 赋 reward → 导出。

另有 **online 模式**（`rl_trainer.py:287`）：无训练数据集（`_EmptyDataLoader`），外部 agent 应用直接连 proxy API，样本经 staleness manager 按权重版本回收。

---

## 2. Rollout 侧：一条 prompt 如何变成训练轨迹

### 2.1 提交与准入控制（事前 staleness 控制）

1. **数据进入**：`RDataset`（远程数据集代理，懒取 prefetch，`infra/data_service/rdataset.py:157`）→ `cycle_dataloader` 无限生成器。
2. `DistRolloutCoordinator.prepare_batch`（`infra/dist_rollout.py:324`）只在 DP head 上跑，委托 `BatchTaskDispatcher.active_submit_and_wait`（`workflow_executor.py:758`）。
3. **Producer 线程**（`workflow_executor.py:442`）在条件变量上等待三闸门全开才提交任务：
   - 未被训练侧 `pause()`
   - `StalenessManager.get_capacity() > 0`
   - 输入队列未满
4. **StalenessManager**（`infra/staleness_manager.py:79`）的容量公式——异步 RL 的灵魂，**事前准入而非事后丢弃**：

```python
capacity = min(
    max_concurrent_rollouts - running,                    # 并发上限
    (max_head_offpolicyness + version + 1) * batch_size   # 相对当前推理权重版本的 off-policy 预算
        - (accepted + running))
```

推理引擎每收一次新权重 `version+1`，容量随之放宽；由此保证样本被消费时落后的策略版本数不超过 `max_head_offpolicyness`（典型 2~8）。

### 2.2 episode 执行（以 `MultiTurnWorkflow` 为例）

代码位置：`areal/workflow/multi_turn.py:58-142`。

每个任务在 uvloop event loop 中是一个协程，**等待推理时让出给其他 workflow**——这是高并发 rollouts 的来源（`max_concurrent_rollouts` 典型 128~256）。单条 episode 内部：

```python
while reward == 0.0 and t < self.max_turns:
    resp = await engine.agenerate(ModelRequest(input_ids, gconfig))   # ① 推理
    reward = await self.async_reward_fn(prompt_str, completions_str, ...)  # ② 奖励
    # ③ 轨迹增量拼接（核心：token 归属标注）
    seq       += resp.input_tokens[-input_len:] + resp.output_tokens
    logprobs  += [0.0]*input_len + resp.output_logprobs
    loss_mask += [0]*input_len + [1]*resp.output_len   # 只有本轮模型采样 token 计 loss
    versions  += [-1]*input_len + resp.output_versions # 每个生成 token 记录权重版本
    turn_ids  += [-1]*input_len + [t]*resp.output_len  # 标记属于第几轮
    # ④ 答错则拼接: 上一轮输出 + eos + "答案错误请重试"的 user turn，进入下一轮
    input_ids = input_ids + resp.output_tokens + [eos] + self.multi_turn_prompt_ids
    discount *= turn_discount                             # 多轮答对打折
```

**关键设计**：

- **loss_mask 语义**：prompt、工具/环境返回文本、以及上一轮自己生成的输出（在新轮次中成为 context）全部 mask=0；只有当前轮实际采样的 token mask=1。prefix 一致性由 `assert resp.input_tokens[:-input_len] == seq` 校验（`multi_turn.py:101`）。
- **多轮提示的构造**用 chat-template 差分法（`multi_turn.py:44-56`）规避 encode-decode 不一致。
- 真工具调用型 agent（搜索 agent、SWE agent）中"工具结果回填"由 SDK/沙箱完成：工具在 Daytona 沙箱执行（支持断网防作弊/资源限额），但**回填后的 token 同样以 mask=0 进序列**——与上面的模式一致。
- episode 结束返回：

```python
{input_ids, logprobs, loss_mask, versions, turn_ids,
 rewards, attention_mask, is_truncated}   # is_truncated 供 PPO reward masking/value bootstrap
```

### 2.3 接受/拒绝与跨节点收集

- 轨迹经 `concat_tensor_interactions` 拼装后由 `RolloutGroup` 校验（group_size 采样分组，供 GRPO 组归一化）；被拒（reward 失败/`should_accept_fn` 返回否）→ `on_rollout_rejected()` + 异步清理远端 RTensor 分片。
- 分离部署下，rollout worker 完成后 **HTTP POST 回 RolloutController 的 callback server**（`rollout_controller.py:848`）resolve future，拉回 RTensor 轨迹引用。
- 跨 DP rank：`_broadcast_and_redistribute_trajectories`（`dist_rollout.py:155`）ragged all-gather 全部轨迹 → FFD/KK 装箱**确定性负载均衡**分配到各训练 rank。
- "一批何时凑齐"的两层判定：局部 `active_submit_and_wait`（`accepted_cnt >= batch_size` 或 `dynamic_bs` 下 `accepted+rejected >= batch_size`）；全局 `_prepare_until_dispatchable`（跨 DP head all-gather，全局组数 ≥ DP world size，含卡死检测：连续 8 轮零新增且 ≥1800s 则协同抛错）。

---

## 3. 训练侧：一个 global step 的完整流水

`RLTrainer.train()` 主循环（`rl_trainer.py:929` 起）每步顺序：

| 步骤 | 代码位置 | 说明 |
|---|---|---|
| 1. 收集 batch | `rl_trainer.py:965` | `_collect_trainable_rollout_batch` 反复调 `actor.prepare_batch`，直到凑满 `min_consumer_batch_size` 个可训练轨迹组；保持 ≥2 个 batch 在途以最大化 rollout/训练重叠；colocate 模式先 `_offload_rollout()` 腾显存 |
| 2. Critic values | `rl_trainer.py:1000` | 可选；`critic.compute_values(rollout_batch)` → `traj["values"]` |
| 3. Ref/teacher logp | `rl_trainer.py:1019/1039` | KL 参考模型与蒸馏打分 |
| 4. Prox logp 重算 | `rl_trainer.py:1093` | 训练侧权重下重算 old_logp（`recompute_logprob: true`，处理 off-policy） |
| 5. 优势估计 | `trainer/ppo/advantage.py:295` | 见下文详解 |
| 6. Actor 更新 | `rl_trainer.py:1160` | `actor.ppo_update(adv_batch)` |
| 7. Critic 更新 | `rl_trainer.py:1185` | 可选 |
| 8. 权重同步 | `rl_trainer.py:1221` | `rollout.pause()` → `actor.update_weights(meta)` |
| 9. 版本发布 | `rl_trainer.py:687` | `set_version(step+1)`，放宽 StalenessManager 容量 |
| 10. 清理/eval/resume | `rl_trainer.py:1240+` | 保存 checkpoint → `clear_batches` 释放 RTensor 分片 → eval → `rollout.resume()` |

### 优势估计流水（`AdvantagePipeline`）

```
outcome shaping → reward bias/scale/clip → 组归一化(GRPO, 基于 rollout_groups)
→ next-token 对齐(roll(loss_mask,-1)) → KL reward → GAE → advantage 归一化 → 塑形
```

- **无 critic（默认 GRPO 路径）**：values=0，outcome reward 只挂在最后一个有效生成 token 的预测位置（`advantage.py:436`），advantage ≈ 组归一化 reward 广播到所有 loss_mask=1 的 token。
- **多轮 agent 专用 `turn_gae`**（`trainer/ppo/gae.py:91`）：以**每个 assistant 回合为一个 timestep** 的 MDP 化——按 `turn_ids` 聚合 reward、取该 turn 首个动作 token 的 value 做 bootstrap，turn 间反向递推 GAE；KL 只进 actor advantage 不进 critic target。
- **`token_gae`**（`gae.py:14`）：跨轮 gap 上用 loss_mask 门控传递 `lastgaelam`（prompt/tool gap 的 token 不贡献但也不打断 credit 传播）。
- `n_samples`（GRPO 组大小，典型 4~16）在 rollout 侧即分组采样，组归一化基于真实组大小（`data.py:2112 normalize_rollout_rewards`）。

### Actor 损失（`grpo_loss_fn`，`trainer/ppo/actor.py:922`）

- concat batch → micro-batch FFD 均衡切分/按需 packing（Megatron 用 `cu_seqlens`）→ 逐 micro-batch `engine.train_batch`。
- 损失分支：clip PPO ratio（`eps_clip`/`eps_clip_higher` 双侧）、CISPO、SAPO、score-centering REINFORCE。
- `mask_stale_tokens: true` 时按 `versions` 把超龄 token 从 loss 剔除（不影响 context/advantage）。
- loss 聚合模式：`token_mean` / `seq_mean` / `prompt_mean`（DAPO 式每 prompt 权重 1/K）。

### 权重同步三通道（`WeightUpdateMeta`，`rl_trainer.py:515-585`）

| 模式 | 机制 | 适用 |
|---|---|---|
| **xccl（默认）** | 训练进程建 NCCL group，权重张量经 XCCL 点对点直推 SGLang/vLLM（`POST /update_weights_from_distributed`，先 abort 在途请求） | 通用 |
| **disk** | 存 HF checkpoint + name-resolve 注册时间戳，rollout 端等通知后加载 | LoRA 必须走此路 |
| **awex** | colocate 显存 handover（meta-server） | Megatron+SGLang colocate |

---

## 4. 端到端时序图

```
Trainer                          RolloutWorker                    SGLang/vLLM
   │ prepare_batch ──HTTP submit──▶ WorkflowExecutor 入队            │
   │                               │ producer: capacity>0? ──submit─▶│(version=v)
   │                               │ uvloop: arun_episode() 协程     │
   │                               │   ├─ await engine.agenerate ───▶│ generate
   │                               │   ◀── token+logprob+versions ───│
   │                               │   ├─ sandbox 执行工具(mask=0)   │
   │                               │   └─ reward_fn 打分             │
   │                               │ 拼装 {loss_mask,turn_ids,...}   │
   │ ◀────── HTTP callback(轨迹) ──│ accepted/rejected               │
   │ (凑满 batch_size, 跨DP all-gather + FFD 装箱)                    │
   │ critic.values → ref.logp → prox.logp → advantage(GRPO/turn_gae) │
   │ actor.ppo_update (microbatch, clip loss, mask stale tokens)     │
   │ update_weights ──XCCL 直推─────────────────────────────────────▶│ weights
   │ set_version(v+1) ─放宽容量─▶ producer 恢复提交 ────────────────▶│(version=v+1)
   └── 下一 step（与 rollout 并行进行 = 异步 RL）                      │
```

---

## 5. 典型 Agent RL 配置参考

`examples/agent_workflow/config.yaml`（OpenAI Agents SDK + GSM8K 工具调用）：

```yaml
rollout:
  backend: "sglang:d4p1t1"
  max_concurrent_rollouts: 256
  consumer_batch_size: ${train_dataset.batch_size}   # 256
  max_head_offpolicyness: 2
  agent:
    mode: inline
    tool_call_parser: qwen25        # 文本层面解析工具调用
    reasoning_parser: qwen3
    export_style: individual
    turn_discount: 1.0
gconfig:
  n_samples: 4                      # GRPO 组大小
  max_new_tokens: 1024
  temperature: 1.0
actor:
  backend: "fsdp:d4p1t1"
  use_decoupled_loss: true          # Decoupled PPO objective（off-policy 处理）
  recompute_logprob: true
  weight_update_mode: xccl
  eps_clip: 0.4
```

`examples/search_agent/local_1.5b_example.yaml`（ASearcher 搜索 agent）：`max_concurrent_rollouts: 128`、`max_head_offpolicyness: 4`、`n_samples: 16`、`reward_norm: group`、`max_turns: 32`。

其他相关示例：`examples/multi_turn_math/`、`examples/tir/`（工具集成推理）、`examples/camel/`、`examples/openclaw/`（online proxy）、`examples/swe/`（端到端 SWE RL）、`examples/hermes/`（AReaL 2.0 online RL loop）。

---

## 6. 设计要点小结

1. **异步的本质**：rollout 与训练持续并行，靠三层机制管理 off-policy 度——事前准入（StalenessManager 容量公式）、token 级 `versions` 标记（训练时 `mask_stale_tokens`/decoupled loss 兜底）、`max_head_offpolicyness` 阈值（2~8）。
2. **Agent 语义进训练数据的三要素**：`loss_mask`（只训模型采样 token，环境/工具/历史轮 context 全 mask）、`turn_ids`（turn 级 credit 分配，供 turn_gae）、`versions`（staleness 处理）——全部由 workflow 在 rollout 侧生成，训练侧零特殊处理。
3. **训练/部署统一**：SDK agent 只需把 `base_url`/`api_key` 指到 AReaL proxy 即可接入 RL，token 级数据由 proxy 的 `InteractionCache` 自动收集，业务 agent 代码零改动。
4. **算法默认 GRPO 式**（无 critic、组归一化 outcome reward）；带 critic 时 `turn_gae` 是为多轮 agent 专门设计的 value-based 路径（turn 级 MDP 化）。
5. **数据面/控制面分离**：控制面全 HTTP（controller↔worker Flask RPC、worker↔SGLang/vLLM REST）；数据面轨迹由训练 worker 经 RTensor 拉取，权重由训练端经 XCCL(NCCL) 直接推送。
