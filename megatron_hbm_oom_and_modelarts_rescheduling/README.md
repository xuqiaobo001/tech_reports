# Megatron-LM HBM OOM 触发参数 与 ModelArts 无条件 Job 级重调度分析

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）+ 华为云 ModelArts 训练作业故障恢复
> 核心问题：① 配置哪些训练启动参数会触发**主进程 HBM OOM** vs **dataloader 阶段 HBM OOM**；② 这类 HBM OOM 能否触发 ModelArts 的**无条件 Job 级重调度**
> 日期：2026-07-06

---

## 目录

1. [背景与关键澄清](#1-背景与关键澄清)
2. [触发"dataloader 阶段"HBM OOM 的参数](#2-触发dataloader-阶段hbm-oom-的参数)
3. [触发"主进程计算阶段"HBM OOM 的参数](#3-触发主进程计算阶段hbm-oom-的参数)
4. [补充：初始化阶段 HBM OOM](#4-补充初始化阶段-hbm-oom)
5. [三类 HBM OOM 速查表](#5-三类-hbm-oom-速查表)
6. [HBM OOM 能否触发 ModelArts 无条件 Job 级重调度](#6-hbm-oom-能否触发-modelarts-无条件-job-级重调度)
7. [实操建议](#7-实操建议)

---

## 1. 背景与关键澄清

### 1.1 "dataloader HBM OOM" 的真相

技术上，dataloader worker 子进程**不碰 HBM**。Megatron 在 `megatron/training/datasets/data_samplers.py:81-93` 的 `worker_init_fn` 里有一段 `close_nvidia_fds()`，刻意**关闭 worker 继承的 `/dev/nvidia*` 文件描述符**，确保 worker 不持有 GPU 内存引用，即使 worker 失败 GPU 显存也能被回收。`pin_memory=True`（`data_samplers.py:109`）分配的是 **pinned host memory（锁页内存）**，由主进程的 pin_memory 线程处理，**不是 HBM**。

因此，所谓 `batch_size=65534 → dataloader OOM`，实际是指：**输入 batch 被搬上显存那一刻 / 第一个算子（embedding 查表）把 `[mbs, seq]` 撑成 `[mbs, seq, hidden]` 时，瞬间打爆 HBM**。这一阶段的 HBM 占用主要由 `micro_batch_size × seq_length` 决定。

### 1.2 HBM OOM 的行为特点（区别于 host RAM OOM）

- **立即抛异常，无拖泥带水**：显存分配失败时，PyTorch caching allocator 当场抛 `torch.cuda.OutOfMemoryError`（`RuntimeError: CUDA out of memory`）。
- **不像 host RAM OOM 会先 swap 颠簸**——HBM OOM 没有"内存颠簸"阶段，瞬间失败。
- **退出码非0（=1）**：Megatron 训练循环没有通用 try/except 吞掉该异常（只有 `mlp.py` 的 `cat_with_oom_fallback` 等局部重试，失败仍向上传播），异常传到进程顶层 → 退出码 1。

---

## 2. 触发"dataloader 阶段"HBM OOM 的参数

> 失败位置：训练**第一步、甚至首层 embedding** 就 `CUDA OOM`（早于进入稳态计算）。

这一阶段的 HBM 占用 = **输入 batch 本身 + 第一层 embedding 把 `[mbs, seq]` 撑成 `[mbs, seq, hidden]`**。

| 参数 | 调哪个方向 | 机制 | 极端示例 |
|---|---|---|---|
| `--micro-batch-size` | ↑↑↑ | 输入 batch 张量与首层激活都按它线性放大 | `--micro-batch-size 65534` |
| `--seq-length` | ↑↑ | 每条样本变长，`[mbs, seq, hidden]` 第二维暴涨 | `--seq-length 131072` |
| `--num-workers`（+ 默认 prefetch=2） | ↑ | 主进程 pin_memory 线程 + 共享内存里堆的 batch 变多 | `--num-workers 16`（主要放大 host 侧，间接推高输入阶段整体占用） |
| 多模态/多输入张量场景 | — | 每条样本有 image/text 等多个张量，单样本 HBM 占用翻倍 | vision token + text 同时大 |

**最小复现组合**（基本第一步就 HBM OOM）：
```bash
--micro-batch-size 65534 --seq-length 4096
```
→ 第一个 embedding 查表就要产出 `65534 × 4096 × hidden × 2B` 的张量，瞬间 `CUDA out of memory`。

> 注：`--batch-size` 在当前版本已废弃（`arguments.py:2569`），真正决定单卡每步内存的是 `--micro-batch-size`；`--global-batch-size` 只影响梯度累积步数，基本不吃 HBM。

---

## 3. 触发"主进程计算阶段"HBM OOM 的参数

> 失败位置：某层的 **forward 或 backward** 中 `CUDA OOM`。
> 前提：把 micro-batch/seq 控制在**温和**范围（避免一上来就卡在输入阶段），改去放大模型 + 砍并行 + 砍重算。

| 参数 | 调哪个方向 | 机制 | 极端示例 |
|---|---|---|---|
| `--num-layers` | ↑ | 激活与权重随层数线性增长 | `--num-layers 200` |
| `--hidden-size` / `--ffn-hidden-size` | ↑ | 权重 + 每层激活同时放大 | `--hidden-size 16384 --ffn-hidden-size 65536` |
| `--seq-length` | ↑↑ | 激活 + 注意力中间内存 | `--seq-length 32768`（但别大到第一步就爆） |
| `--num-attention-heads` 极端值 | — | 影响注意力中间 buffer | 让 head_dim 异常 |
| `--tensor-model-parallel-size` | **↓（设 1）** | TP 越小，单卡放越多权重/激活（默认 1 最吃显存） | 不切 TP |
| `--pipeline-model-parallel-size` | **↓（设 1）** | PP=1 时所有层堆在一卡 | 不开 PP |
| `--context-parallel-size` | **↓（设 1）** | CP=1 时长序列注意力全在一卡 | 长 seq + CP=1 |
| `--num-experts` ↑ / `--expert-model-parallel-size` ↓ | — | MoE 总 expert 参数翻倍且分得不散 | `--num-experts 256 --expert-model-parallel-size 1` |
| **不开 `--recompute-activations`** | 不开 | 不重算 → 激活全驻留显存 | 默认即最耗 |
| **不开 `--use-flash-attn`** | 不开 | 退回 O(seq²) 标准注意力，长 seq 必爆 | `--seq-length 16384` 不开 flash |
| **不开 `--use-distributed-optimizer`** | 不开 | Adam 优化器状态(≈12×params)全堆每卡 | 大模型必爆 |
| 不开 `--bf16`/`--fp16`/`--fp8`（fp32） | — | 精度翻倍，显存 ×2~×4 | fp32 + 大模型 |

**最小复现组合**（稳稳爆在计算阶段）：
```bash
--micro-batch-size 32 --seq-length 8192 --hidden-size 12288 --num-layers 96 \
--tensor-model-parallel-size 1 --pipeline-model-parallel-size 1
# 不加 --recompute-activations，不加 --use-distributed-optimizer
```

---

## 4. 补充：初始化阶段 HBM OOM

> 失败位置：模型/优化器**创建时**就 `CUDA OOM`，训练还没跑起来。

| 参数 | 方向 | 机制 |
|---|---|---|
| `--hidden-size`/`--num-layers` 极大 | ↑ | 权重张量在显存里创建时就超 |
| `--vocab-size` 极大 | ↑ | 嵌入表 `vocab × hidden` 每卡一份（除非切 EP/TP），常在 init 阶段爆 |
| 不开 `--use-distributed-optimizer` | 不开 | 优化器状态初始化时占满 |

---

## 5. 三类 HBM OOM 速查表

| 目标 | 核心参数 | 爆在哪 |
|---|---|---|
| **dataloader 阶段 HBM OOM** | 猛调 `--micro-batch-size` / `--seq-length` | 第一步、甚至首层 embedding 就 `CUDA OOM` |
| **主进程计算 HBM OOM** | micro-batch 温和，猛调 `--num-layers`/`--hidden-size`/`--seq-length`，TP/PP/CP 设 1，不开 recompute/distributed-optimizer | 某层 forward/backward `CUDA OOM` |
| **初始化 HBM OOM** | 极大模型 + 极大 `--vocab-size`，不开 distributed-optimizer | 模型/优化器创建时 `CUDA OOM` |

**一句话**：想复现"dataloader OOM"=把 `--micro-batch-size` 或 `--seq-length` 推到极端；想复现"主进程计算 OOM"=micro-batch 别太大，去放大模型规模、砍掉并行和重算。两者都是 HBM OOM（退出码 1），区别只是**爆在数据进显存那一刻，还是爆在计算过程中**——对应可观察的报错位置和迭代步数不同。

---

## 6. HBM OOM 能否触发 ModelArts 无条件 Job 级重调度

### 6.1 触发条件（来自华为云文档）

ModelArts "无条件 Job 级重调度"的硬性条件是：
1. **进程真的退出了**（被中断、终止，而非还活着）。文档原文：*"如果作业异常时业务无法中断一直处于运行状态，则无法触发Job重调度"*——针对**挂死（hang）**。
2. **退出码非0**。

### 6.2 结论：能触发，且非常干净

| 场景 | 是否真实存在 | 进程是否退出非0 | 能否触发 Job 重调度 |
|---|---|---|---|
| **主进程 HBM OOM**（CUDA out of memory） | ✅ 真实 | ✅ 退出码 1 | ✅ **能，且很干净** |
| **dataloader HBM OOM** | ❌ 基本不存在（worker 与 HBM 隔离） | — | 见下 |

- HBM OOM 是**瞬间崩溃**（无 swap 颠簸），不是挂死 → 正好命中触发条件。
- 主进程 HBM OOM 是教科书级触发场景：✅ 瞬间崩溃 → 退出码 1 → 无条件 Job 级重调度。
- "dataloader HBM OOM" 在标准 Megatron 里不成立：若超大 batch 最终打爆显存，那是**主进程在 `to(device)` 时**发生的 HBM OOM，归到上一条，✅ 触发。

### 6.3 唯一仍要防的坑：NCCL 挂起

HBM OOM 自身是干净退出，但分布式训练有连带问题：**单个 rank 因 HBM OOM 崩溃时，若崩溃发生在 NCCL 集合通信（backward 的 all-reduce）过程中，其他 rank 自己没 OOM，会卡在集合通信上等待死掉的 rank。**

- HBM OOM 是干净的进程退出 → torchrun（elastic agent）通过进程监控**能检测到**该 rank 死亡 → 把其他 rank 一并杀掉 → 整个 Pod 退出码非0 → ✅ 触发重调度。
- 所以 HBM OOM 比 host RAM 颠簸那种半死不活的状态**可靠得多**。
- 但为保险，建议显式设 NCCL 超时，避免极端情况下其他 rank 被拖成无限挂起（挂起就只能靠"卡死重启"）。

### 6.4 退出码参考

| 退出码来源 | 典型值 |
|---|---|
| Python 未捕获异常（含 CUDA OOM） | 1 |
| 被 OOM Killer 杀（SIGKILL，host 场景） | 137（=128+9） |
| C++ abort（某些 CUDA/MKL 断言） | 134（=128+6） |

全部非0，满足触发条件 2。

---

## 7. 实操建议

1. **想稳定触发重调度 → 优先构造 HBM OOM**（而非 host RAM OOM），因为它瞬间崩溃、不挂死。
2. **别吞异常**：训练循环里不要 catch OOM 后 continue，否则进程不退出 → 无法触发。
3. **设 NCCL 超时**：避免单 rank HBM OOM 把其他 rank 拖成无限挂起（被误判卡死）。
   ```bash
   export NCCL_TIMEOUT=<合理秒数>
   export TORCH_NCCL_BLOCKING_WAIT=1
   export NCCL_HEARTBEAT_TIMEOUT_SEC=<秒数>
   ```
4. **满足前提**：开启**断点续训 + 定期存 CKPT**（所有重调度策略的共同约束，否则重调度了也接不上进度）。
5. **同时开"作业卡死重启"**：把 OOM 的"挂死"分支也兜住——API 设 `"fault-tolerance/hang-retry": "true"`。
6. **API 开启方式**：
   ```json
   {
     "metadata": {
       "annotations": {
         "fault-tolerance/job-retry-num": "8",
         "fault-tolerance/job-unconditional-retry": "true",
         "fault-tolerance/hang-retry": "true"
       }
     }
   }
   ```

---

## 参考来源

- **华为云 ModelArts 文档**：[训练作业故障恢复](https://support.huaweicloud.com/usermanual-standard-modelarts/develop-modelarts-0012.html)
- **Megatron-LM 本地源码**：
  - `megatron/training/datasets/data_samplers.py:81-113`（worker 关闭 GPU FD + pin_memory）
  - `megatron/training/arguments.py:2569,2978,2992`（batch-size/seq-length/num-workers）
  - `megatron/training/config/training_config.py:12,16`（micro/global batch size）
  - `megatron/core/transformer/transformer_config.py`（num_layers/hidden_size/recompute_*）
  - `megatron/core/parallel_state.py:548-556`（TP/PP/CP/EP size）
  - `megatron/core/transformer/mlp.py`（`cat_with_oom_fallback`）
