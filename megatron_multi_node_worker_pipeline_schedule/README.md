# Megatron 多机训练 主/从架构、流水线调度与昇腾启动 技术报告

> 分析对象：Megatron-LM / Megatron Core v0.15.0（昇腾上对应 MindSpeed/MindSpeed-MM）
> 关注问题：多机训练中是否存在"主 worker / 从 worker"？主从之间的管理、交互逻辑是什么？并展开流水线并行的 P2P 调度（1F1B / 气泡填充）与昇腾 msrun / rank_table 启动。
> 核心结论：**没有主从 worker，是对等（peer-to-peer）的 SPMD 架构**；"主"的概念只存在于两个层面——启动期的**会合主节点**（临时）和应用层的 **rank 0 协调者**（逻辑角色）。

---

## 0. 结论速览

| 问题 | 答案 |
|---|---|
| 有没有"主 worker / 从 worker"？ | **没有**。Megatron 是 SPMD（单程序多数据）对等架构，所有 rank 平等 |
| `MASTER_ADDR` 那台机器是 master 吗？ | 它是**启动期的会合主节点（rendezvous）**，只负责建联，训练时是普通 rank |
| `rank 0` 是主吗？ | 它是**协调者/发言人**（日志、数据集、ckpt 元数据），训练计算上完全对等 |
| 梯度谁汇总？ | 没有 master 汇总，靠 **all-reduce**（ring/tree 集合通信，全员对称） |
| 流水线的第一段是 master 吗？ | 不是，stage 间是**数据依赖**（send/recv），不是主从 |
| 昇腾有特殊主从架构吗？ | 没有。MindSpeed 架构与 Megatron 一致，仅替换 NCCL→HCCL、CUDA→NPU、torchrun→msrun |

---

## 1. 架构本质：对等 SPMD，无主从

| 模型 | 特征 | 是否主从？ |
|---|---|---|
| Parameter Server（参数服务器） | 一个 master 持有全局参数，worker 拉取/推送梯度 | ✅ 真正主从 |
| TensorFlow Chief Worker | chief 写 ckpt、初始化变量，其他只算 | ✅ 主从 |
| **Megatron / MindSpeed** | **所有 rank 跑同一份代码、各自算梯度、all-reduce 平均梯度** | ❌ **对等，无主从** |

每个 rank（一张卡上一个进程）都平等：各自持有模型（DP 下是完整副本，TP/PP 下是分片）、各自做前向/反向、各自更新参数。没有任何 worker 去"指挥/分配任务"给其他 worker。

---

## 2. 两个容易被当成"主从"的概念

### 概念 A：启动器（Launcher）层的"会合主节点" —— 临时，仅用于建联

属于 torchrun / msrun（启动器），不属于 Megatron 训练逻辑，**只在启动阶段存在**。

- 多机启动时选一台服务器作**会合点**，IP/端口写入 `MASTER_ADDR`/`MASTER_PORT`。SLURM 典型写法：`MASTER_ADDR=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)`。
- 该节点跑一个 **TCPStore（键值存储）**。其余进程启动后向它"报到"，交换 IP/rank/world_size。
- **建联完成后，rank 之间点对点直连（RDMA/NIC）通信，"主节点"协调使命结束**，它变回普通 rank。

代码落地：`torch.distributed.init_process_group(backend=..., store=store, world_size=..., rank=...)`（`megatron/training/initialize.py:352`），`store` 即会合主节点上的 TCPStore。

```
节点0 (MASTER_ADDR)            节点1            节点2
  ┌────────────┐
  │ TCPStore    │◄── 报到 ◄── rank... ◄── 报到 ◄── rank...
  │ (会合主节点) │
  └────────────┘
        │ 建联完成后 ──────────────────────────────┐
        ▼                                          ▼
   所有 rank 两两直连（HCCL/NCCL over RDMA），对等通信
```

> **MASTER_ADDR 那台机器是"报到的集合点"，不是"指挥训练的主 worker"。**

### 概念 B：应用层 `rank 0` —— 协调者/发言人逻辑角色，非主 worker

rank 0（全局 0 号进程）承担一些**协调性/IO 性**工作，但仍和大家跑完全一样的训练循环、平等参与 all-reduce。其"特权"仅限：

| rank 0 做的事 | 代码位置 | 为什么由它做 |
|---|---|---|
| 打印日志（`print_rank_0`） | 全代码随处可见 | 避免每个 rank 刷屏 |
| **先在 rank 0 构建数据集索引**，其余 barrier 等待 | `blended_megatron_dataset_builder.py:381-398` | 避免多 rank 同时写缓存冲突 |
| rank 0 编译 C++ dataset helper | `initialize.py:173` | 避免重复编译 |
| **广播配置/决策** `broadcast(..., 0)` | `training.py:4457`、`training.py:4244` | 统一各 rank 的 `do_train/valid/test`、eval_iters |
| 检查点元数据、tracker 文件 | checkpointing 相关 | 单点写元数据避免冲突 |

这些都是**逻辑角色**——rank 0 既不分配任务，也不汇总参数。

---

## 3. 多机"管理 + 交互"的真实逻辑

### 3.1 启动建联（rendezvous）
1. 启动器（NVIDIA `torchrun`/`torch.distributed.run`；昇腾 `msrun`）在每台机器拉起 `NPU/GPU 数` 个进程，设好 `RANK / LOCAL_RANK / WORLD_SIZE / MASTER_ADDR / MASTER_PORT`。
2. 每个进程向 MASTER_ADDR 上的 TCPStore 报到、互相发现。
3. 调 `init_process_group`，底层（NCCL/HCCL）建立 rank 间点对点通道。

### 3.2 进程组划分（`mpu.initialize_model_parallel`，`initialize.py:362`）
建联后把 `world_size` 个 rank 按 **TP/PP/DP/EP/CP** 切成多个进程组。每个 rank 同时属于多个组（如既是某 TP 组成员，又是某 DP/PP 组成员）。后续通信按"组"进行。

### 3.3 训练循环里的三类交互

**(a) 集合通信（collective）—— 梯度/参数主力，全员对称**
- **DP 梯度同步**：算完各自 batch 梯度后，对 **DP 组** `all-reduce` 取平均（`distributed_data_parallel.py`）。
- **分布式优化器**：`reduce-scatter` / `all-gather` 分发优化器状态（靠 CUDA stream 重叠，非线程）。
- **Loss/指标聚合**：`all-reduce`。
- **配置同步**：`broadcast` 从 rank 0 发给所有人。

**(b) 点对点（P2P）—— 流水线并行专用**
- 相邻 PP stage 间用 `send`/`recv` 传激活（forward）与梯度（backward）。详见第 4 节。

**(c) 同步屏障（barrier）—— 控制时序**
- 关键阶段 `torch.distributed.barrier()` 强制对齐（如 rank 0 构建数据集后 barrier）。

### 3.4 数据怎么分（各取所需，非主下发）
- **DP**：每个 rank 读**自己的数据分片**（`MegatronPretrainingSampler` 按 `data_parallel_rank` 切片）。
- **TP**：同组内只有 `tp_rank==0` 真正读数据，再 `broadcast` 给组内（`pretrain_gpt.py:119` + `get_batch_on_this_tp_rank`）——"组内广播"，非"主下发"。

### 3.5 checkpoint 协调（rank 0 当记录员）
- 各 rank 各写自己的模型/优化器分片到**共享文件系统**。
- rank 0 额外写"全局元数据/tracker"文件，记录迭代号等，方便恢复。

---

## 4. 流水线并行的 P2P 调度（1F1B / 气泡填充）—— 代码级展开

> 这是理解"stage 之间怎么协作"的核心。代码全部在 `megatron/core/pipeline_parallel/`。

### 4.1 调度选择（`get_forward_backward_func`，`schedules.py:48-163`）

| 配置 | 选用调度 | 代码位置 |
|---|---|---|
| `pp_size == 1` | `forward_backward_no_pipelining`（纯 DP/TP，无流水线） | `schedules.py:672` |
| `pp_size > 1`，无 VP | `forward_backward_pipelining_without_interleaving`（**标准 1F1B**） | `schedules.py:2127` |
| `pp_size > 1`，有 VP（虚拟流水线） | `forward_backward_pipelining_with_interleaving`（**交错/Interleaved 1F1B**） | `schedules.py:984` |
| 开 `overlap_moe_expert_parallel_comm` | `combined_1f1b_schedule_for_interleaved_pipelining`（组合 1F1B） | `schedules.py:1465` |

### 4.2 P2P 通信原语（`p2p_communication.py`）

相邻 stage 之间不是单向发，而是**双向成对**收发（用 NCCL/HCCL 的双向 `isend`/`irecv`，把发送和接收重叠以隐藏延迟）：

| 原语 | 含义 | 方向 |
|---|---|---|
| `send_forward` | 把本 stage 的输出激活发给下一 stage | forward → |
| `recv_forward` | 从上一 stage 收激活 | ← forward |
| `send_backward` | 把输入梯度发给上一 stage | ← backward |
| `recv_backward` | 从下一 stage 收梯度 | backward → |
| `send_forward_recv_backward` | 发激活同时收梯度（forward↔backward 重叠） | 双向 |
| `send_backward_recv_forward` | 发梯度同时收下一个 microbatch 的激活 | 双向 |
| `send_forward_backward_recv_forward_backward` | 同时双向收发激活与梯度（最极致重叠） | 双向 |

### 4.3 1F1B 三阶段：warmup → steady → cooldown（气泡填充原理）

关键变量在 `get_pp_rank_microbatches`（`schedules.py:894`）。**非交错 1F1B** 的 warmup 数：

```python
num_warmup_microbatches = pipeline_parallel_size - pipeline_parallel_rank - 1   # schedules.py:922
```

含义：
- **第 0 段（stage 0）**：warmup = `pp_size - 1` —— 它要先做满 `pp_size-1` 个 forward，把整个流水线**填满**，让下游每个 stage 都有活干。
- **最后一段**：warmup = `0` —— 它不需要预热，激活一到就反向。
- 中间 stage：介于两者之间。

三阶段拆解（以 `num_microbatches = M`、`pp_size = P`、本 stage rank = `r` 为例）：

```
warmup : 做 num_warmup = P - r - 1 个 forward（只前向，填管道）
steady : 1F1B 稳态 —— 做 (M - num_warmup) 次 [1 个 forward + 1 个 backward] 交替
cooldown: 把剩余的 backward 做完（排空管道）
```

**为什么能省显存**：稳态阶段每个 stage 同时只持有"在飞"的 microbatch 激活（约 `P - r` 个），而非全部 `M` 个；越靠后的 stage 持有越少。

**气泡（bubble）**：填管道和排管道期间，首尾 stage 会空闲。气泡占比：

```
气泡占比 ≈ (P - 1) / M       （M = microbatch 数）
```

→ **增大 `M`（microbatch 数）可摊薄气泡**；这是为何流水线并行要配合大 global batch / 多 microbatch。

### 4.4 交错 1F1B（Virtual Pipeline，进一步压气泡）

当 `virtual_pipeline_model_parallel_size (VP) > 1` 时，每个 GPU 上放 **VP 个模型分块（model chunk）**，调度让同一 GPU 轮流跑不同 chunk，使流水线更"密"、气泡更小。warmup 数更复杂（`schedules.py:929-930`）：

```python
num_warmup_microbatches = (pipeline_parallel_size - pipeline_parallel_rank - 1) * 2
num_warmup_microbatches += (num_model_chunks - 1) * microbatch_group_size_per_vp_stage
```

**这是数据依赖驱动的协作，不是主从**——stage 0 喂激活给 stage 1，stage 1 算完给 stage 2；反向时梯度沿原路返回。每个 stage 都是平等的"工人"，靠 P2P 收发握手。

### 4.5 一张图：1F1B 时间线（P=4 stage，M=8 microbatch）

```
stage0: F0 F1 F2 | B0 F3 B1 F4 B2 F5 B3 F6 B4 F7 B5 | B6 B7   ← warmup=3,稳态1F1B,cooldown
stage1:    F0 F1 | B0 F2 B1 F3 ... | ...                           ← warmup=2
stage2:       F0 | B0 F1 B1 ... | ...                              ← warmup=1
stage3:          | B0 F0 B1 F1 ... | ...                           ← warmup=0
            [----气泡/填管道----][----稳态 1F1B----][----排空----]
```

---

## 5. 昇腾（MindSpeed）启动：msrun / rank_table

> 本仓库（Megatron-LM NVIDIA 版）不含昇腾启动器代码，本节为**昇腾侧约定**说明，确切 CLI 请以你安装的 MindSpeed / torch_npu 版本为准。

### 5.1 组件替换表（架构完全一致）

| 组件 | NVIDIA (Megatron-LM) | 昇腾 (MindSpeed) |
|---|---|---|
| 启动器 | `torchrun` / `torch.distributed.run` | **`msrun`**（带一个 scheduler/master 做会合，等价 TCPStore 主节点） |
| 会合主节点 | `MASTER_ADDR` 上 TCPStore | msrun 的 scheduler（同样仅启动期建联） |
| 通信后端 | NCCL（`backend='nccl'`） | **HCCL**（`backend='hccl'`） |
| 设备 | CUDA / `torch.cuda` | NPU / `torch_npu` |
| 集合/P2P 语义 | all-reduce/all-gather/send-recv | 完全对应（HCCL 提供相同原语） |
| rank 0 协调角色 | 同第 2 节 | 完全一致 |

### 5.2 msrun 启动（动态 rendezvous，类 torchrun）

`msrun` 与 torchrun 同构，用 `--master_addr/--master_port/--nnodes/--nproc_per_node/--node_rank`，并在 master 上起一个独立 **scheduler** 进程做 rendezvous：

```bash
# 每台机器上都执行（MASTER_ADDR 指向会合主节点）
msrun \
  --master_addr=$MASTER_ADDR --master_port=$MASTER_PORT \
  --nnodes=$NNODES --nproc_per_node=$NPUS_PER_NODE \
  --node_rank=$NODE_RANK \
  pretrain_gpt.py <MEGATRON_ARGS>
```

> 训练脚本侧需 `backend='hccl'`、设备用 `torch_npu`。建联完成后所有 rank 对等，无主从。

### 5.3 rank_table.json（早期/静态 HCCL 拓扑方案）

早期 Ascend 用**静态拓扑描述文件** `rank_table.json`（由 `hccl_tools.py` 生成），描述整个集群的服务器数、每服务器卡数、各卡的 device IP 与 rank 映射，通过环境变量 `RANK_TABLE_FILE` 传给训练进程。这套比动态 rendezvous 更"中心化"——一个文件定义全局连接拓扑。但**无论哪种启动方式，训练运行时都是对等 SPMD**。

---

## 6. 一张图：多机训练中"谁是谁"

```
                    【启动期】有会合主节点（临时）
        msrun/torchrun 在节点0 起 scheduler/TCPStore (MASTER_ADDR)
        所有 rank 报到 → 互相建联 → 主节点使命结束
                              │
                              ▼
                    【训练期】全员对等 SPMD，无主从
   节点0: rank0* rank1  rank2  rank3      (* rank0 兼任"协调者":日志/数据集广播/ckpt元数据)
   节点1: rank4  rank5  rank6  rank7
   节点2: rank8  ...
            │
            ├─ DP组内 all-reduce 平均梯度        （全员对称）
            ├─ PP相邻 stage  send/recv 激活&梯度  （数据依赖，非主从；1F1B 调度）
            ├─ TP组内 broadcast 数据/集合         （组内广播）
            └─ barrier 关键点对齐
```

---

## 7. 常见误解澄清

| 误解 | 事实 |
|---|---|
| "MASTER_ADDR 那台机器是 master worker" | 仅**启动期会合点**，训练时是普通 rank |
| "rank 0 是主，其他是从" | rank 0 是**协调者/发言人**，计算上完全对等 |
| "需要一台机器汇总所有梯度" | 靠 **all-reduce**（ring/tree），无中心汇总 |
| "流水线第一段是 master" | stage 间是**数据依赖**（send/recv），非主从 |
| "昇腾有特殊主从架构" | MindSpeed 与 Megatron 架构一致，仅替换通信/设备/启动器 |

---

## 8. 参考代码索引

| 关注点 | 位置 |
|---|---|
| 分布式初始化 | `megatron/training/initialize.py:42` (`initialize_megatron`)、`:352` (`init_process_group`) |
| 进程组划分 | `megatron/training/initialize.py:362` (`mpu.initialize_model_parallel`) |
| 调度选择 | `megatron/core/pipeline_parallel/schedules.py:48-163` (`get_forward_backward_func`) |
| warmup/气泡计算 | `megatron/core/pipeline_parallel/schedules.py:894-933` (`get_pp_rank_microbatches`) |
| 标准 1F1B | `megatron/core/pipeline_parallel/schedules.py:2127` |
| 交错 1F1B | `megatron/core/pipeline_parallel/schedules.py:984` |
| 无流水线 | `megatron/core/pipeline_parallel/schedules.py:672` |
| P2P 收发原语 | `megatron/core/pipeline_parallel/p2p_communication.py`（`send_forward_recv_backward` 等） |
| rank 0 协调（广播/数据集/编译） | `training.py:4457`、`blended_megatron_dataset_builder.py:381`、`initialize.py:173` |

---

**一句话总结**：Megatron/MindSpeed 是**对等 SPMD**架构，无主从 worker。启动时有一台**临时会合主节点**（MASTER_ADDR / msrun scheduler，仅管建联），训练时所有 rank 平等跑同一份代码，靠 **集合通信（all-reduce/all-gather）+ P2P（send/recv，驱动 1F1B 流水线调度）+ barrier** 协作；`rank 0` 只承担日志、数据集、检查点元数据等协调杂活，并非主从中的 master。
