# verl Agent RL 训练-推理协同机制源码分析

> **分析对象**：verl 0.6.x（重构后架构：V1 Trainer + engine_workers + agent_loop）
> **代码基线**：`/root/verl_rl/verl`
> **报告日期**：2026-11-03
> **核心结论**：verl 的训练-推理协同是"单控制器驱动下，训练引擎与推理引擎同体共存、分时复用 GPU，以 `update_weights` 为唯一权重传播通道、以 TransferQueue 为轨迹回流通道、以 sleep/wake 状态机管理显存交接"的完整体系；Agent RL 的多轮工具循环作为独立的 asyncio 层嵌入该体系，通过 `response_mask` 与 staleness 元数据解决多轮轨迹的训练适配问题。

---

## 目录

1. [总纲](#1-总纲)
2. [控制平面：single_controller 编排层](#2-控制平面single_controller-编排层)
3. [训练引擎与推理引擎的同体共存：Hybrid Engine](#3-训练引擎与推理引擎的同体共存hybrid-engine)
4. [主循环时序：推理态与训练态的交替](#4-主循环时序推理态与训练态的交替)
5. [权重同步机制：协同的物理通道](#5-权重同步机制协同的物理通道)
6. [Agent 多轮工具循环的嵌入方式](#6-agent-多轮工具循环的嵌入方式)
7. [off-policy 治理：权重版本与数据新鲜度的对账](#7-off-policy-治理权重版本与数据新鲜度的对账)
8. [全景架构图](#8-全景架构图)
9. [总结：协同机制的四大支柱](#9-总结协同机制的四大支柱)

---

## 1. 总纲

verl 是业界主流的开源 RL 训练框架（volcengine/ByteDance 出品）。Agent RL（智能体强化学习）与普通 RLHF 的核心区别在于：**轨迹不是一次生成产生的，而是一个跨多次推理调用、夹杂工具执行与环境反馈的状态机过程**。这给训练-推理协同带来了三重新问题：

1. **显存问题**——训练引擎（FSDP/Megatron，带优化器状态）与推理引擎（vLLM/SGLang，带 KV cache）都极大，不可能同时全量驻留 GPU；
2. **权重一致性问题**——推理引擎必须持续拿到最新的策略权重，且权重更新后旧 KV cache 前缀缓存必须作废，否则会串权重版本；
3. **数据新鲜度问题**——生成与训练解耦后，轨迹产生时的权重版本和消费它时的权重版本可能不一致（off-policy 程度需要治理）。

verl 的解法可以概括为一句话：

> **单控制器（single-controller）驱动下，同一个 Ray actor 进程里同时驻留"训练引擎"与"推理引擎"，通过 sleep/wake 交替让渡 GPU 显存、通过 `update_weights` 把训练后的权重推回推理引擎、通过 agent loop 异步产生多轮轨迹并经 TransferQueue 流回训练端——三股协同时序围绕"权重版本"这一核心展开。**

---

## 2. 控制平面：single_controller 编排层

verl 采用**单控制器（single-controller）**模式：训练逻辑全部写在 driver 进程的一个 Python 循环里，worker 上没有任何决策代码。这是所有协同时序的"指挥中枢"。

### 2.1 抽象层次（`verl/single_controller/`）

| 抽象 | 位置 | 职责 |
|---|---|---|
| `Worker` | `base/worker.py:76` | 所有远端 worker 基类；Ray actor 创建时注入 `RANK/WORLD_SIZE/MASTER_ADDR/MASTER_PORT` 环境变量（`ray/base.py:644-652`），worker 方法内部用这些变量自建 NCCL 进程组 |
| `RayResourcePool` | `ray/base.py:113` | 把 GPU 组织成 Ray placement group，每 bundle 携带 `{CPU: max_colocate_count, GPU: 1}`，`STRICT_PACK` 策略保证同组 GPU 物理共置 |
| `ResourcePoolManager` | `ray/base.py:192` | 角色名 → 资源池映射；`max_colocate_count` = 共享同一 GPU 的 WorkerGroup 数（FSDP 下 actor/critic/ref/rollout/reward 共 3 个池） |
| `RayWorkerGroup` | `ray/base.py:430` | driver 侧句柄，持有 N 个 Ray actor 引用，提供 `execute_all_async` / `execute_rank_zero` 等执行原语 |

### 2.2 调用扇出机制

worker 方法用装饰器标注数据切分方式，driver 调用时自动完成"切分 → 扇出 → 收集"：

```python
@register(dispatch_mode=Dispatch.DP_COMPUTE_PROTO, blocking=True)
def update_actor(self, data): ...
```

完整调用链（`worker_group.py:185-255` → `ray/base.py:806-890`）：

1. `@register` 把 `{"dispatch_mode", "execute_mode", "blocking"}` 元数据挂在函数属性 `MAGIC_ATTR`（`decorator.py:440`）上；
2. WorkerGroup 构造时扫描 worker 类上所有带 `MAGIC_ATTR` 的方法，为每个方法在 driver 侧生成一个绑定闭包（`_bind_worker_method`）；
3. driver 调用 `wg.update_actor(data)` 时：`dispatch_fn` 先把 `DataProto` 切成 `world_size` 份 → 对每个 Ray actor 发 `worker.method.remote(该 rank 的切片)` → `ray.get` 阻塞等待 → `collect_fn` 把各 rank 结果按序拼接回来。

### 2.3 关键 dispatch 模式（`decorator.py:308-331`）

| 模式 | 语义 | 典型使用者 |
|---|---|---|
| `ONE_TO_ALL` | 数据广播到所有 worker | `update_weights`（所有 rank 必须参与 DTensor gather） |
| `ALL_TO_ALL` | 调用方直接传 per-worker 列表 | 自定义场景 |
| `DP_COMPUTE_PROTO` | 按 DP 维切分 DataProto / 拼回，带自动 padding 与裁剪 | `compute_log_prob`、`train_mini_batch` |
| `DP_COMPUTE_METRIC` | 同上但收集时只保留 per-rank 结果不拼接 | metrics 上报 |
| `make_nd_compute_dataproto_dispatch_fn(mesh)` | 通用化 Megatron 网格分发：先 RPC 查询每个 worker 在 tp/pp/dp mesh 中的 dp rank，按 dp 维切分后复制到共享同 dp rank 的所有 worker（tp/pp 副本） | `engine_workers.py:277,750,757` |

> **意义**：训练与推理的每一次阶段切换（生成 → 打分 → 前向 → 更新）都表现为 driver 的一次 worker group 方法调用。协同时序完全由 driver 的串行 Python 代码显式决定，可读、可断点、可插桩。

---

## 3. 训练引擎与推理引擎的同体共存：Hybrid Engine

`ActorRolloutRefWorker.init_model()`（`verl/workers/engine_workers.py:487-748`）在**一个 Ray actor 进程内**构建三样东西：

1. **ref 模型** `TrainingWorker`（`:629`）——KL 参考策略；
2. **actor 训练引擎**——FSDP 或 Megatron（`:690`）；
3. **rollout 推理引擎**（`:695-716`）——把训练的 DP 世界重新解释为推理网格 `(dp × infer_tp × infer_pp)`。例如训练 DP=4、TP=1 的 4 张卡，对推理引擎呈现为一个 TP=4 组。

行 `:748` 的 `aggressive_empty_cache(force_sync=True)` 先释放缓存，让随后启动的 vLLM 能通过 `cudaMemGetInfo` 看到真实可用显存——**两个引擎的显存协商从初始化那一刻就开始了**。

### 3.1 共部署模式（`verl/workers/rollout/replica.py:126-184`）

| 模式 | 方法 | 说明 |
|---|---|---|
| 进程内融合 | `init_hybrid()`（`:126`） | 推理引擎与训练引擎同进程，权重同步零序列化开销（默认） |
| 同组共置 | `init_colocated()`（`:155`） | 同 placement group、不同进程，经 CUDA-IPC 传权重 |
| 分离部署 | `init_standalone()`（`:184`） | 推理集群独立占用 GPU，权重经网络同步 |

### 3.2 显存协同：sleep / wake 分级状态机

训练和推理不可能同时占用全部显存，verl 用推理引擎的 sleep 分级解决：

**vLLM**（`verl/third_party/vllm/__init__.py:33-48`）：

- **level 1**：权重经 `CuMemAllocator.sleep(offload_tags=("weights",))` 卸载到 CPU（unmap 物理页），KV cache 释放；唤醒时 remap；
- **level 2**：全部丢弃，唤醒时从 CPU 备份完整恢复；
- 适配器（MTP drafter 等）存在时强制 level 1（`vllm_async_server.py:1514` 的 `_resolve_sleep_level()`）。

**SGLang**（`verl/workers/rollout/sglang_rollout/async_sglang_server.py:499-520`）：

- sleep 时发 `ReleaseMemoryOccupationReqInput(tags=[...])`；
- LoRA-adapter 模式只释放 `["kv_cache"]`（权重保留在 GPU），全量模式释放 `["kv_cache", "weights"]`（`sglang_rollout.py:288-303` 按 `sleep_level` 选择）；
- HYBRID/COLOCATED 模式强制开启 `enable_weights_cpu_backup`（`:392-400`），保证权重可从 CPU 恢复。

**KV cache 是训练期间最大的一块可回收显存**，因此：
- 训练开始前：`sleep_replicas()` 释放权重 + KV cache；
- 分离式权重同步前：`release_kv_cache_replicas()`（`checkpoint_engine/base.py:496`）只释放 KV cache，让 NCCL 直接写进推理引擎**存活的权重 buffer**（零拷贝接收）。

---

## 4. 主循环时序：推理态与训练态的交替

### 4.1 V1 trainer 的 fit() 结构

入口 `verl/trainer/main_ppo.py:170`，`trainer.use_v1: true` 时进入 V1（TransferQueue 版）。`fit()`（`verl/trainer/ppo/v1/trainer_base.py:413-531`）每个 global step 的完整时序：

```
on_step_begin
  └─ step()（trainer_base.py:533-560）
       ├─ prepare_step(): 异步下发下一批 prompt 给 rollout（流水线预取）
       └─ _step_once() × parameter_sync_step 次（:562-612）:
            1. replay_buffer.sample()          ← 从 TransferQueue 等待/取回轨迹
            2. _compute_reward_colocate()      ← （可选）共置 RM 打分
            3. _balance_batch()                ← 按 seq_len 跨 DP 均衡 + upsample
            4. actor.compute_log_prob()        ← old_log_prob（π_old 基准）
            5. ref.compute_ref_log_prob()      ← KL 参考对数概率
            6. critic.infer_batch()            ← values
            7. compute_advantage()             ← GAE / GRPO
            8. critic.train_mini_batch()       ← critic 更新
            9. actor.update_actor()            ← actor 更新 ←【此刻策略权重已变】
on_step_end
  └─ checkpoint_manager.update_weights()       ← 权重同步回推理引擎 ★核心协同点★
_validate()（按 test_freq）
tq.kv_clear(...)（清理 TransferQueue）
```

其中每次 worker-group 调用（第 4/5/6/8/9 步）都经过第 2 节的 dispatch/collect 机制扇出到所有 GPU worker。

### 4.2 三种 trainer 模式 = 三档"训练/推理重叠度"

模式在 `main_ppo.py:145` 按 `trainer.v1.trainer_mode` 选择（注册表见 `trainer_base.py:2014-2041`）：

#### ① sync —— 严格串行（`trainer_sync.py:35-48`）

```python
on_sample_end:  sleep_replicas()      # 采完 → 释放推理显存，GPU 全给训练
on_step_end:    update_weights()      # 训完 → 唤醒 + 换权重，GPU 全给推理
```

Agent 多轮轨迹在推理态内**完整跑完**（一个 batch 的所有轨迹到齐）才切训练态。完全 on-policy，实现最简单，但推理与训练互相空转等待。

#### ② colocate_async —— 一步重叠（`trainer_colocate_async.py:37-50`）

```python
on_sample_end:  abort_replicas() + sleep_replicas()   # 中断在途请求（partial rollout）
on_step_end:    update_weights() + resume_generation_replicas()  # 立即恢复生成
```

生成是流式的：训练第 N 步时，第 N+1 步的轨迹已经在后台生成（`AgentLoopManagerTQ` fire-and-forget，`agent_loop_tq.py:248-262`）。每个 step 边界处中断未完成的请求、同步权重后继续。**一步以内的 off-policy**。

#### ③ separate_async —— 双池弹性切换（`trainer_separate_async.py:405-433`）

推理集群与训练集群物理分离；训练 GPU 上的 hybrid replica 作为"弹性算力"按需借给生成侧：

```python
switch_to_rollout(): update_weights → resume_generation → 注册进全局负载均衡器
switch_to_trainer(): 从均衡器摘除 → abort(reject_request=True) → sleep
```

切换决策（`:340-407`）基于**自适应阈值**（`_adapt_switch_switch_threshold`，`:316`）与"生成收益 vs 切换成本"的效益模型（`:358-372`）。standalone 推理池**从不 sleep**，始终用最新广播的权重服务请求；弹性副本通过 `CheckpointEngineManager.add_replicas/remove_replicas`（`base.py:449-464`）动态加入/退出服务，并触发 NCCL 组的重建（`:436`）。

---

## 5. 权重同步机制：协同的物理通道

这是整个机制中最精细的部分。统一入口是 `ActorRolloutRefWorker.update_weights()`（`engine_workers.py:789`），标注 `@register(ONE_TO_ALL)`——**所有 rank 都要参与**，因为 FSDP 权重是分片的 DTensor，gather 本身是集合通信。

### 5.1 路径 A：naive 共置同步（同步训练默认）

```
训练引擎                                推理引擎
─────────                              ─────────
resume(tags=["weights"])         →    唤醒权重显存（engine_workers.py:844）
get_per_tensor_param()           →    逐张量产出完整 HF 权重生成器
    │  FSDP:     DTensor.full_tensor()   ← 所有 rank all-gather（fsdp/transformer_impl.py:967）
    │  Megatron: mcore→HF bridge 跨 PP 导出（megatron/transformer_impl.py:1024）
rollout.update_weights(per_tensor_param) →  流式逐 bucket 灌入
    │  vLLM:  ZMQ IPC + CUDA-IPC 句柄（bucketed_weight_transfer.py:73）
    │         → vLLMColocateWorkerExtension.update_weights_from_ipc（utils.py:263）
    │         → model.load_weights（:414，vLLM 自行按 TP 切分）
    │  SGLang: infer_tp 网格内 gather_object → HTTP update_weights_from_tensor
    │         （sglang_rollout.py:305-396，LocalSerializedTensor 每 TP rank 一份）
torch.distributed.barrier()      →   所有 trainer rank offload 完成后（:885）
resume(tags=["kv_cache"])        →   恢复 KV cache（:886）
```

**关键设计点**：

1. **并行切分不匹配的解耦**——训练侧 `full_tensor()` 把每个参数 gather 成完整张量（每个 rank 都持有全量副本），推理引擎的 `load_weights` 再按自己的 TP 切分。两侧并行度（如训练 DP=4 TP=1 vs 推理 TP=4）完全解耦。
2. **KV cache 作废**——同步完成后 `clear_kv_cache()`（vLLM，`vllm_rollout.py:248`）/ `flush_cache()`（SGLang，`sglang_rollout.py:394`）。旧权重下的前缀缓存若不清理，会导致新旧权重版本的前缀混用。
3. **barrier 防 OOM**（`:880-884` 注释）——ONE_TO_ALL 下所有 trainer rank 都进入该路径，各 rank 的 CPU offload / `empty_cache` 进度不一致会导致 peer 端 OOM，必须跨 rank 同步后才能由 leader 唤醒 rollout。
4. **流式 bucket 传输**——权重不落盘、不整块序列化，按 `update_weights_bucket_megabytes` 分桶，经 ZMQ IPC socket 配对（trainer rank r ↔ vLLM worker local rank r）+ CUDA-IPC 显存句柄直达，避免经 Ray object store 的大数据搬移。
5. **LoRA 双段同步**——`model.lora.merge=False` 时先同步一次 base 权重（`do_lora_base_sync`，仅首次），之后每次只同步 adapter（vLLM 走 `add_lora`，SGLang 走 `load_lora_adapter_from_tensor`），SGLang 侧 sleep_level 降为 1（权重不卸载）。

### 5.2 路径 B：checkpoint engine 分离式同步（分离部署 / 异步训练）

`CheckpointEngineManager.update_weights()`（`verl/checkpoint_engine/base.py:510-559`）编排四步：

1. **`release_kv_cache_replicas()`**（`:533`）——只释放 KV cache，NCCL 直接写进推理引擎**存活的权重 buffer**（docstring `:492-500` 详述了这一设计动机：避免权重双份显存）；
2. **`build_process_group()`**（`:422-447`）——所有 actor worker 和 rollout 端 `CheckpointEngineWorker` 通过 Ray 互换 endpoint 元数据（`prepare()` RPC），后端 `build_topology()` 计算每个 rank 的组网参数，然后全部 worker RPC `init_process_group`。NCCL 后端用 cuPy 的 `collective.init_collective_group(world_size, rank, "nccl", group_name)`（`nccl_checkpoint_engine.py:298`）——**这就是"训练进程组之外另建跨进程传输组"的机制**，endpoint 经 Ray + ZMQ 广播完成握手；
3. **actor 侧 `send_weights`**（`nccl_checkpoint_engine.py:315`）——张量分 bucket；元数据经 ZMQ PUB/SUB 广播（`_start_zmq_server:252`）；主 rank `collective.broadcast(bucket, src_rank=0)` 流水线推送；非 sender 的 actor rank 充当 **NVLink 中继**（`_relay_weights:407`）；
4. **rollout 侧 `receive_weights`**（`:437`，双缓冲接收 `:449`，`torch.cuda.synchronize` 后 yield 视图）→ `server_adapter.update_weights` 灌入共置推理服务器。

后端可插拔（`verl/checkpoint_engine/`）：NCCL / HCCL（NPU）/ NIXL / Mooncake / delta 增量引擎（只传上次同步以来的差分）。

---

## 6. Agent 多轮工具循环的嵌入方式

Agent 轨迹是跨多个推理调用的状态机，这是 Agent RL 协同复杂度的特有来源。verl 中该层位于 `verl/experimental/agent_loop/`。

### 6.1 架构倒置：不是 rollout worker 驱动，而是独立 agent loop 驱动

- **`AgentLoopManager`**（`agent_loop.py:1204`）——driver 侧管理器，创建 `rollout.agent.num_workers` 个 **CPU Ray actor**（round-robin 分布到各节点，`:1239-1259`），把 prompt 批分片下发（`:1278-1285`）；
- **`AgentLoopWorker.generate_sequences()`**（`:597`）——**每个样本一个 `asyncio.create_task`**（`:680-685`），同批上百条轨迹在 OpenAI 兼容协议上并发执行；
- 推理引擎以**标准推理服务器**形态暴露（vLLM/SGLang async server + `LLMServerClient`，`llm_server.py:43`；全异步变体 `FullyAsyncLLMServerClient:172`），多副本挂在 `GlobalRequestLoadBalancer`（`rollout/router.py:133`）后面，支持粘性会话缓存（`:263`）。

### 6.2 状态机（`tool_agent_loop.py:170-180`）

```
PENDING ──→ GENERATING ─⇄── PROCESSING_TOOLS ──→ TERMINATED
 初始模板化     调 server.generate    asyncio.gather 并发执行工具
 (工具schema    (注入stop_token_ids   (BaseTool.execute / FunctionTool.call)
  拼入prompt)    截断工具调用语法)      观察结果 re-tokenize 拼回流
```

- `_handle_pending_state`（`:215`）：消息 + 工具 schema 模板化为初始 token 流；
- `_handle_generating_state`（`:234`）：每轮把**完整 runtime 前缀**重发给服务器（依赖引擎内部 prefix caching 避免重算）；工具解析器（`tool_parser.py`：Hermes / GptOss / Qwen3-XML 三种格式）从输出中抽取结构化工具调用；
- `_handle_processing_tools_state`（`:310`）：`max_parallel_calls` 内的工具并发执行（`:317-321`），观察结果经 `ct_merge_context_msg` 重分词拼回 token 流；
- 每轮 LLM 输出经 `ct_merge_assistant_token` 合入 runtime 流（**mask=1**），工具观察合入时 **mask=0**；
- 终止时（`:183`）`response_ids = prompt_ids[-len(response_mask):]` —— **response 同时包含模型 token 和工具观察 token，靠 `response_mask` 区分**。

### 6.3 工具接口（`verl/tools/`）

| 组件 | 位置 | 说明 |
|---|---|---|
| `BaseTool` | `base_tool.py:24` | 有状态工具：`create(instance_id)` → `execute(instance_id, parameters) -> (ToolResponse, tool_reward, tool_metrics)` → `calc_reward` → `release`，全程 `@rollout_trace_op` 追踪 |
| `FunctionTool` | `function_tool.py:45` | 无状态：`@function_tool` 装饰的 Python 函数，schema 从签名自动推导（`_build_schema_from_fn:187`） |
| Schema | `schemas.py:41-98` | OpenAI function-calling 兼容格式 + `ToolResponse(text/image/video)` 多模态 |
| 注册 | `tool_registry.py:55` | YAML 声明式注册（类 + 配置 + 可选 schema 覆盖） |

配置入口：`rollout.multi_turn.*`（tool_config_path、max_user_turns、max_parallel_calls、max_tool_response_length、format）与 `rollout.agent.*`（num_workers、default_agent_loop、agent_loop_manager_class），见 `verl/workers/config/rollout.py:49-76`。

### 6.4 对训练-推理协同的三个直接影响

1. **损失屏蔽**：`response_mask` 一路传到 actor 更新作为 `loss_mask`（`ray_trainer.py:1588-1592`），并参与 KL（`:103-108`）与 advantage 掩码/白化（`core_algos.py:255-262`）——**只有模型自己生成的 token（含工具调用语法 token）参与梯度，环境反馈 token 被屏蔽**；
2. **轨迹长短剧变 → 训练侧抖动**：`_balance_batch()` 按 seq_len 重排跨 DP 均衡，`upsample_batch_to_divisible_size` 补齐到 `lcm(dp_size, mini_batch_size)` 整数倍（`trainer_base.py:1600-1643`）；
3. **打分流水线化**：rule-based reward 由 `RewardLoopWorker` 在 agent loop 内逐条异步打分（`agent_loop_tq.py:165`），与生成本身重叠；只有共置 RM 才走训练侧 `_compute_reward_colocate`（`trainer_base.py:1540-1592`，打分前唤醒 RM、打分后 sleep，`reward_loop.py:343-380`）。

### 6.5 轨迹回流：TransferQueue 流式通道

V1 下轨迹不再以 DataProto 整块返回：`AgentLoopWorkerTQ._run_prompt`（`agent_loop_tq.py:112-153`）在**每条轨迹完成的瞬间** `tq.async_kv_batch_put` 进 `"train"` 分区，并打上：

- **GRPO 组生命周期 tag**（`pending/running/finished/failure`）；
- **staleness 元数据**（`min/max_global_steps`，`agent_loop_tq.py:210-225`）。

训练侧 `ReplayBuffer.sample()`（`replay_buffer.py:422` 同步版 / `:593` 异步版）在 TQ 上等待凑批。driver 各阶段间只传递轻量的 `KVBatchMeta(partition_id, keys, tags)` 引用（`trainer_base.py:1675/1746/1759/1813` 的 `kv_batch_get/kv_batch_put` 按需拉取字段），DataProto 仅在单控制器局部计算（advantage、metrics）时使用。

---

## 7. off-policy 治理：权重版本与数据新鲜度的对账

生成与训练解耦后，核心矛盾变成：**轨迹产生时的权重版本 vs 消费它训练时的权重版本**。verl 的三层防线：

| 机制 | 位置 | 作用 |
|---|---|---|
| `max_off_policy_threshold` | `replay_buffer.py:520-564`；配置 `trainer.v1.sampler.*`（`ppo_trainer.yaml:287-303`） | 限制轨迹可跨越的权重版本数。策略 `drop`：驱逐过期 GRPO 组并 `refill_fn` 补发新轨迹；策略 `wait`：阻塞采样直到过期的 in-flight 请求完成 |
| `parameter_sync_step` | `trainer_base.py:533-560` | Decoupled PPO：一个 rollout batch 支撑 N 次本地更新，N 次内不做权重同步。π_old 快照保存在 CPU（`trainer_separate_async.py:201-225` 的 `save_model_to_cpu/restore_model_from_cpu`），保证 N 次更新的重要性比值基准一致 |
| `old_log_prob` 重算 | `trainer_base.py:1645-1704` | 训练前用当前 actor 重算 `old_log_probs`（除非显式 bypass 复用 `rollout_log_probs`）；另有 rollout correction 的重要性采样权重修正（`:1784`） |

辅助机制：

- **DAPO 组过滤**（`replay_buffer.py:261` `_dapo_filtered_keys`）：奖励全同的 GRPO 组（零优势）直接驱逐，省训练算力；
- **staleness 观测指标**：`training/off_policy/trajectory_{spans,staleness}*`（`trainer_base.py:1984-2011`）；
- **断点续训**：TQ 状态随 checkpoint 保存/恢复（`trainer_base.py:902-1008`），in-flight prompt 由 `_reissue_inflight_prompts`（`:910-946`）重新下发。

---

## 8. 全景架构图

```
┌─────────────────────── driver（单控制器） ───────────────────────────┐
│  fit(): prepare_step ─→ sample ─→ reward ─→ logprob/ref/value       │
│         ─→ advantage ─→ update_critic ─→ update_actor               │
│  on_step_end: update_weights ★  ← 唯一的权重传播时刻                │
└────┬──────────────────────────┬──────────────────────┬──────────────┘
     │ Ray worker-group         │ TransferQueue        │ Ray RPC
     ▼ (dispatch/collect)       ▼ (流式轨迹+staleness)  ▼
┌─ ActorRolloutRefWorker ─────┐  ┌─ AgentLoopWorker ────┐  ┌─ RewardLoopWorker ─┐
│  FSDP/Megatron 训练引擎      │  │  asyncio × N 轨迹    │  │  (流式打分)         │
│  vLLM/SGLang rollout 引擎   │  │  状态机 + 工具执行   │  └────────────────────┘
│    ↕ sleep/wake 显存交替     │  └───┬─────────────────┘
│    ↕ update_weights 权重回流 │      │ OpenAI 兼容协议
│      (CUDA-IPC / cuNCCL)    │      ▼
└─────────────────────────────┘  GlobalRequestLoadBalancer
                                        │
                        ┌───────────────┼────────────────┐
                        ▼               ▼                ▼
                 hybrid 副本 ×K   standalone 副本 ×M   （弹性 add/remove）
                 (训练GPU分时)    (常驻,从不sleep)
```

---

## 9. 总结：协同机制的四大支柱

| 支柱 | 核心组件 | 解决的问题 |
|---|---|---|
| **① 时序协同** | driver hooks（`on_sample_end` / `on_step_end`）+ 三种 trainer 模式（sync / colocate_async / separate_async） | 推理态与训练态的交替边界；重叠度从严格串行到完全解耦可调 |
| **② 显存协同** | sleep/wake 分级状态机（KV cache / +weights 两级）+ `aggressive_empty_cache` + barrier | 两个显存大户分时复用同一块 GPU，互不 OOM |
| **③ 权重协同** | `update_weights` 唯一通道：共置走 CUDA-IPC/进程内直灌，分离走 cuNCCL 组播（endpoint 经 Ray/ZMQ 握手），完成后强制清 KV cache | 推理引擎始终持有最新策略；权重版本与前缀缓存严格一致 |
| **④ 数据协同** | agent loop 逐条流式产出带 `response_mask`（多轮损失屏蔽）和 staleness tag 的轨迹 → TransferQueue → replay buffer 的 drop/wait 策略 | 多轮轨迹的训练适配 + off-policy 程度约束在算法可接受范围内 |

**一句话收束**：verl 把 Agent RL 中"训练"与"推理"这两个本质冲突的需求（一个要大显存更新权重、一个要大 KV cache 高吞吐采样、且后者必须始终用前者的最新权重），通过**单控制器显式编排 + 显存分时 + 权重单通道传播 + 数据新鲜度对账**四件套，变成了一个时序上完全可控、重叠度连续可调的系统工程问题。

---

*本报告基于 verl 0.6.x 源码静态分析生成，所有 `file:line` 引用均已在代码基线中核实。*
