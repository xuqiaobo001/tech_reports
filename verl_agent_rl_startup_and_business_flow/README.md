# verl Agent RL 启动逻辑与完整业务流分析

> **分析对象**：verl 0.6.x（V1 Trainer + engine_workers + agent_loop 架构）
> **代码基线**：`/root/verl_rl/verl`
> **报告日期**：2026-11-03
> **系列文章**（前三篇）：
> ①《verl Agent RL 训练-推理协同机制源码分析》— `verl_agent_rl_coordination_analysis`
> ②《verl Agent RL 华为云最佳实践部署架构方案》— `verl_huaweicloud_deployment_architecture`
> **本篇定位**：补齐"从敲下命令到第一条轨迹产生"的冷启动链，以及稳态运行时一个完整 step 的业务流闭环。所有 `file:line` 引用均已在代码基线核实。

---

## 目录

1. [总览：冷启动与稳态两段式生命周期](#1-总览冷启动与稳态两段式生命周期)
2. [冷启动链：六步严格有序的初始化](#2-冷启动链六步严格有序的初始化)
3. [冷启动的关键设计点](#3-冷启动的关键设计点)
4. [稳态业务流：一个 step 的完整闭环](#4-稳态业务流一个-step-的完整闭环)
5. [业务流六阶段总结](#5-业务流六阶段总结)
6. [失败恢复与断点续训](#6-失败恢复与断点续训)
7. [与华为云部署的映射](#7-与华为云部署的映射)

---

## 1. 总览：冷启动与稳态两段式生命周期

整个 Agent RL 生命周期可以清晰地分成两段：

- **冷启动（一条严格有序的初始化链）**：从 Hydra 命令行入口开始，经 Ray 组网、训练/推理引擎共置构建、checkpoint 恢复、首次权重同步、agent loop worker 就绪、预热 prompt 下发，直到第一条轨迹开始产生；
- **稳态循环（生成侧持续流 + 训练侧按步消费）**：生成侧 fire-and-forget 地持续产生轨迹并流式写入 TransferQueue，训练侧每个 global step 消费一批完整轨迹完成 PPO 更新，再把新权重同步回推理引擎。

两段的分界线是 `trainer.fit()` 的第一行 `on_train_begin()`。

```
┌──────────────── 冷启动（分钟级，一次性） ────────────────┐
│ 配置装配 → Ray组网 → 引擎共置构建 → sleep腾显存          │
│ → 加载checkpoint → 首次权重同步 → agent loop就绪        │
│ → 预热prompt下发                                        │
└──────────────────────┬──────────────────────────────────┘
                       ▼  on_train_begin()
┌──────────────── 稳态循环（小时/天级，每step一轮） ────────┐
│ [生成侧·持续] prompt下发 → 多轮轨迹状态机 → 流式入TQ      │
│ [训练侧·每步] 采样 → 前向 → 优势 → 更新 → 权重同步       │
│              → 恢复生成 → checkpoint/验证               │
└─────────────────────────────────────────────────────────┘
```

---

## 2. 冷启动链：六步严格有序的初始化

### 全景启动图

```
python -m verl.trainer.main_ppo  (Hydra 入口, main_ppo.py:170)
│
├─ ① 配置装配与校验
│    ppo_trainer.yaml + CLI override → validate_config → auto_set_device
│    (NPU 自动识别，无需显式 trainer.device=npu)
│
├─ ② run_ppo() (main_ppo.py:35)
│    ├─ 确定性环境变量传播 (VERL_FULL_DETERMINISM 等)
│    ├─ ray.init()  ← 连接已有 Ray 集群(K8s head)或拉起 local cluster
│    │                runtime_env 注入 TRANSFER_QUEUE_ENABLE=1 等全局环境
│    └─ TaskRunnerV1.remote() + ray.get(runner.run(config))
│         ★ 整个训练循环从此跑在这个 Ray actor 里(即 driver 单控制器)
│
├─ ③ TaskRunnerV1.run() (main_ppo.py:137) —— 六步严格有序
│    1. tq.init(config.transfer_queue)     ← TransferQueue(外部KV存储)初始化
│    2. get_trainer_cls(trainer_mode)      ← sync / colocate_async / separate_async
│    3. trainer.init()                     ← 见 ④，最重的一步
│    4. init_agent_loop_manager()          ← 见 ⑤
│    5. trainer.fit(agent_loop_manager)    ← 进入稳态循环
│    6. finally: logger.finish + tq.close()
│
├─ ④ trainer.init() = _setup() + on_init_end()  (trainer_base.py:215)
│    │
│    │ _setup() 十步 (trainer_base.py:224-395):
│    ├─ a. tokenizer / 训练数据集迭代器 / dump executor
│    ├─ b. ResourcePoolManager: Role→资源池映射, 创建 Ray placement group
│    │      (GPU bundle, STRICT_PACK 共置, trainer_base.py:231-232)
│    ├─ c. 角色类装配: ActorRolloutRefWorker / Ref / Critic → RayClassWithInitArgs
│    ├─ d. create_colocated_worker_cls(): 共享同 GPU 池的角色融合成 WorkerDict
│    ├─ e. RayWorkerGroup × 每池 → wg.init_model()   ★ GPU worker 拉起
│    │      (engine_workers.py:487: FSDP/Megatron 训练引擎
│    │       + vllm/sglang ServerAdapter 推理引擎 同进程构建;
│    │       NCCL/HCCL 进程组自组网; aggressive_empty_cache 显存协商)
│    ├─ f. RewardLoopManager (rule-based reward workers / RM)
│    ├─ g. [可选] TeacherModelManager (蒸馏)
│    ├─ h. ReplayBuffer 构建 (sync→ReplayBuffer / async→ReplayBufferAsync)
│    ├─ i. LLMServerManager.create()       ★ 推理服务成型 (trainer_base.py:373)
│    │      hybrid 副本: server.init_hybrid(worker_group) (llm_server.py:491)
│    │      standalone 副本: init_standalone() (llm_server.py:501)
│    │      → launch_servers() → get_client()=LLMServerClient(挂负载均衡器)
│    ├─ j. CheckpointEngineManager 创建 → sleep_replicas() ★先睡下腾显存
│    │      → _load_checkpoint() (找最新 ckpt: 训练状态+TQ+replay buffer)
│    │
│    │ on_init_end() (trainer_sync.py:31 / trainer_colocate_async.py:30):
│    └─ k. checkpoint_manager.update_weights()   ★第一次权重同步
│           (resume weights → get_per_tensor_param → rollout.update_weights
│            → offload → barrier → resume kv_cache, engine_workers.py:789)
│
├─ ⑤ init_agent_loop_manager() (main_ppo.py:112)
│    ├─ 选 manager 类 (默认 AgentLoopManagerTQ, 可配 FQN 自定义)
│    └─ AgentLoopManagerTQ.create(config, llm_client, teacher_client,
│                                 reward_loop_worker_handles)
│         └─ _init_agent_loop_workers():
│              创建 num_workers 个 CPU Ray actor (round-robin 跨节点分布)
│              每个 worker: load_all_tools() ★ AgentSphere 工具在此实例化
│                           + agent loop 注册表 (tool_agent/single_turn_agent/...)
│
└─ ⑥ fit() 启动瞬间 (trainer_base.py:413)
     on_train_begin():
     ├─ sync: 无特殊动作
     └─ colocate_async/separate_async: _add_async_warmup_batches(N)
          ★ 预热窗口: 一次性下发 N×train_batch_size 条 prompt
          → agent_loop_manager.generate_sequences(batch)  ← fire-and-forget
          → batch.chunk(num_workers) → 每 worker asyncio.create_task/样本
     进入 while (epoch, global_steps) 主循环 → 稳态
```

### 各步骤职责说明

| 步骤 | 代码位置 | 职责 | 为什么必须在这个位置 |
|---|---|---|---|
| ray.init | `main_ppo.py:63` | 组建/连接 Ray 集群，注入全局环境变量 | 一切 Ray actor 的前提 |
| tq.init | `main_ppo.py:153` | TransferQueue（外部 KV 存储）客户端初始化 | 稳态数据流的主干道 |
| wg.init_model | `trainer_base.py:330` → `engine_workers.py:487` | 训练引擎 + 推理引擎同进程构建，NCCL/HCCL 组网 | 引擎不存在则无从谈 sleep/权重同步 |
| sleep_replicas | `trainer_base.py:392` | 释放推理引擎 KV cache | **必须在 `_load_checkpoint` 之前**，否则显存 OOM |
| _load_checkpoint | `trainer_base.py` | 恢复训练状态 + TQ 状态 + replay buffer | 恢复的权重是下一步同步的源 |
| on_init_end → update_weights | `trainer_sync.py:33` | **首次权重同步**：把 checkpoint 权重灌进推理引擎 | 生成开始前推理引擎必须持有正确权重 |
| AgentLoopManagerTQ.create | `main_ppo.py:126` → `agent_loop_tq.py:242` | 创建 CPU agent loop worker + 工具实例化 | 拿到的是"已就绪的推理服务"（`llm_client` 由 trainer 传入，`main_ppo.py:128`） |
| _add_async_warmup_batches | `trainer_base.py:624` | 预热窗口：一次性下发 N 批 prompt | 异步模式的流水线从此填充；支持小数（如 1.5 批），且会跳过 TQ 恢复中已存在的 prompt 组避免重复 |

---

## 3. 冷启动的关键设计点

1. **顺序不可交换**：必须 `init_model`（引擎建好）→ `sleep_replicas`（腾显存）→ `_load_checkpoint`（恢复权重）→ `on_init_end.update_weights`（把权重灌进刚睡下的推理引擎）→ 才能开始生成。每一步都在为下一步腾资源。

2. **sleep 出现在加载 checkpoint 之前**（`trainer_base.py:392`，注释 "sleep all replicas to load checkpoint"）：推理引擎的 KV cache 必须先释放，checkpoint 加载才不会 OOM。这是显存协同（sleep/wake 状态机）在冷启动期的第一次应用。

3. **AgentLoopManager 与训练侧在初始化期完成握手**：`llm_client` 由 trainer 的 `LLMServerManager.get_client()` 传入（`main_ppo.py:128`），生成侧与训练侧的耦合点仅此一个句柄——这也是用户可整体替换 agent loop manager（配 `agent_loop_manager_class` FQN）的解耦基础。

4. **fire-and-forget 从第一天就是常态**：`AgentLoopManagerTQ.generate_sequences`（`agent_loop_tq.py:249`）下发后 `ray.get` 立即返回，worker 内部每样本 `asyncio.create_task` 后台执行——driver 从不等待轨迹完成，等待发生在训练侧 `replay_buffer.sample()`。

---

## 4. 稳态业务流：一个 step 的完整闭环

以 colocate_async（Agent RL 推荐模式）为例，生成侧与训练侧**重叠运行**：

```
━━━━━━━━━━━━ 生成侧（持续运行，从不整批等待） ━━━━━━━━━━━━

prepare_step() 每 step 补充一批 prompt 进窗口 (trainer_base.py:1536)
      │
      ▼
AgentLoopWorkerTQ.generate_sequences (agent_loop_tq.py:61)
      │ 每 prompt 一个后台任务
      ▼
_run_prompt (agent_loop_tq.py:112)
      │ uid 写入 TQ: status=running
      │ rollout.n 个 session 并行
      ▼
_run_agent_loop → 实例化 ToolAgentLoop → 状态机循环 (tool_agent_loop.py:170)
      │
      │  ┌─────────────── 轮次内循环 ───────────────┐
      │  │ GENERATING (:234):                        │
      │  │   llm_client.generate(完整runtime前缀)    │
      │  │   → OpenAI协议 → 负载均衡器 → rollout副本 │
      │  │   返回 tokens + logprobs (mask=1)         │
      │  │ 工具解析器抽取结构化工具调用 (:300)         │
      │  │ PROCESSING_TOOLS (:310):                  │
      │  │   BaseTool.execute                        │
      │  │   ├─ 本地路径: FunctionTool.call          │
      │  │   └─ 沙箱路径: VPC-EP→AgentSphere         │
      │  │      ②create沙箱 ③execute ⑤release       │
      │  │ 观察 ct_merge_context_msg 拼入 (mask=0)   │
      │  │ → 回 GENERATING，直至 TERMINATED           │
      │  └───────────────────────────────────────────┘
      ▼
_agent_loop_postprocess (agent_loop_tq.py:155)
      │ input_ids/position_ids/response_mask/loss_mask/奖励字段
      ▼
tq.async_kv_batch_put → "train" 分区  ★轨迹完成瞬间即写入
      │ + staleness tag (min/max_global_steps, agent_loop_tq.py:210)
      │ + RewardLoopWorker 异步打分 (与生成本身重叠, :165)
      ▼
_run_prompt settle: 所有 session 结束
      → status = finished / failure (GRPO 组状态机, :112-153)

━━━━━━━━━━━ 训练侧（每 global step 一轮） ━━━━━━━━━━━

_step_once (trainer_base.py:562)
 1. replay_buffer.sample()      ← TQ 凑批 (off-policy drop/wait 策略)
    └─ on_sample_end: abort_replicas + sleep_replicas ★中断在途请求,腾NPU
 2. [可选] colocated RM 打分 (:577)
 3. _balance_batch              (seq_len 跨 DP 均衡 + upsample, :582)
 4. actor.compute_log_prob  → old_log_probs (π_old) (:585)
 5. ref.compute_ref_log_prob → KL 参考 (:589)
 6. critic.infer_batch      → values (:594)
 7. compute_advantage       → GAE/GRPO (:599)
 8. critic.train_mini_batch × epochs (:604)
 9. actor.train_mini_batch  ← loss_mask 只训模型token,工具观察被屏蔽 (:608)
       (rollout batch → mini-batch × ppo_epochs 次梯度更新,
        extra_info 携带 global/mini_batch_size/epochs/seed)

on_step_end (fit 主循环, trainer_base.py:478-488)
 10. checkpoint_manager.update_weights()  ★权重同步回推理引擎
     (naive: 进程内 IPC 直灌 / detached: HCCL 广播; 完成后清 KV cache)
 11. resume_generation_replicas()  ★立即恢复被打断的生成
 12. [save_freq] checkpoint → 共享存储 (含 TQ 状态) (:482-486)
 13. [test_freq] validation (:492-501)
 14. metrics 上报 (重点: trajectory_staleness) (:504)
 15. tq.kv_clear → 下一个 step (prepare_step 补新 prompt) (:512)
```

### 业务流的同步点解析

稳态中只有**三个同步点**，其余全部异步：

1. **`replay_buffer.sample()`**——训练侧等待"凑够一批新鲜轨迹"，是生成→训练的唯一汇合点（off-policy 阈值决定等多久）；
2. **`actor/critic.train_mini_batch().get()`**——driver 等待梯度更新完成（权重在此刻变更）；
3. **`update_weights()`**——权重版本切换的原子时刻，完成后旧轨迹按 staleness 规则逐步退场。

除这三点外：轨迹生成、工具执行、打分、prompt 下发全部并发运行。

---

## 5. 业务流六阶段总结

| 阶段 | 触发者 | 关键动作 | 失败恢复 |
|---|---|---|---|
| **1. 提交与冷启动** | 用户/Hydra | Ray 组网 → 引擎共置构建 → sleep → 加载 ckpt → 首次权重同步 → agent loop worker 就绪 → 预热下发 | 任一步失败进程退出，无状态残留 |
| **2. Prompt 下发** | trainer 每步 `prepare_step` + 启动期 warmup | fire-and-forget，chunk 到 CPU worker，一 prompt 一协程 | TQ checkpoint 恢复后 `_reissue_inflight_prompts` 重发在途 prompt（`trainer_base.py:910`） |
| **3. 轨迹生成** | AgentLoopWorker（纯 CPU asyncio） | 多轮状态机，每轮全前缀重发（靠推理引擎 prefix cache 避免重算），工具并发执行 | 单轨迹异常 → 该 GRPO 组标 `failure`，不影响他组；沙箱 TTL 兜底回收 |
| **4. 流式回流** | 每条轨迹完成瞬间 | TQ put + staleness tag + 异步打分 | off-policy 超限 → drop+refill 或 wait |
| **5. 训练消费** | trainer `_step_once` 九阶段 | abort/sleep → 前向/优势/更新 → update_weights → resume | mini-batch 粒度可重试；π_old CPU 快照保证多次更新基准一致 |
| **6. 收尾** | fit 循环条件满足 | final validation → logger finish → tq.close | checkpoint 随时可供下次 `_load_checkpoint` |

---

## 6. 失败恢复与断点续训

断点续训的事实源是共享存储上的 checkpoint，其内容与恢复动作：

| checkpoint 内容 | 恢复动作 | 代码位置 |
|---|---|---|
| 训练器状态（模型/优化器/global_steps） | `_load_checkpoint` | `trainer_base.py`（init 期）/ `fit` 内 |
| TransferQueue 状态（在途/完成轨迹） | TQ 分区恢复 | `_save_checkpoint` 保存于 `trainer_base.py:1004-1008`，恢复于 `:902-908` |
| replay buffer（GRPO 组状态、staleness） | 缓冲区重建 | 同上 |
| 在途 prompt 列表 | `_reissue_inflight_prompts` 重新下发 | `trainer_base.py:910-946` |

冷启动路径上的 `_load_checkpoint` 与稳态中的定期保存使用同一套状态，**恢复后跳过已恢复的 prompt 组**（`_add_async_warmup_batches` 中 `missing_prompts = target - restored` 的去重逻辑，`trainer_base.py:637-644`），避免重复生成。

---

## 7. 与华为云部署的映射

结合系列第二篇（华为云部署架构），本篇的业务流各阶段在云上的落位：

| 业务流阶段 | 云上落位 |
|---|---|
| ①② Ray 组网 / TaskRunner | CCE CPU 池 Ray Head Pod |
| ④ 引擎构建（init_model） | CCE Ascend 池 Worker Pod（训练+推理同进程 hybrid） |
| ④ sleep_replicas + _load_checkpoint | Ascend 池（显存腾挪）+ SFS-Turbo（checkpoint 读取） |
| ⑤ agent loop worker + 工具实例化 | CCE CPU 池；AgentSphere 客户端在此建立（VPC-EP 私网） |
| 稳态阶段 3 工具子流程 | CPU 池 → VPC-EP → AgentSphere 沙箱（一轨迹一沙箱） |
| 稳态阶段 4 TransferQueue | CPU 池 Ray Head 内存 |
| 稳态阶段 5 训练 + 权重同步 | Ascend 池内（hybrid 同进程，naive 路径零网络；L3 分离形态走 HCCL/RoCE） |
| 稳态阶段 6 checkpoint | SFS-Turbo（冷数据可归档 OBS） |

---

## 总结

verl Agent RL 的启动逻辑是一条**资源递进式**的初始化链：每一步都为下一步腾出资源（引擎建好才能 sleep、sleep 才能加载、加载完才能同步、同步完才能生成）；而稳态业务流是一个**双速齿轮**：生成侧以轨迹粒度高频转动（fire-and-forget + 流式入队），训练侧以 step 粒度低速转动（凑批 → 九阶段更新），两个齿轮通过 TransferQueue 的 staleness 机制咬合，通过 `update_weights` 完成权重版本的定期切换。理解了"冷启动的顺序不可交换性"与"稳态的三个同步点"，就掌握了 verl Agent RL 运行时的全部主干逻辑。

---

*本报告基于 verl 0.6.x 源码静态分析生成，是"源码分析 → 云上落地"系列的第三篇。*
