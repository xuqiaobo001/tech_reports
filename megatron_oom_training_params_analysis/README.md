# Megatron-LM 训练参数触发 OOM 分析报告

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）
> 关注问题：通过配置训练启动参数，分别实现 **主进程 OOM** 与 **dataloader OOM**
> 日期：2026-07-05

---

## 目录

1. [故障注入子系统（框架内置）](#1-故障注入子系统框架内置)
2. [关键前提：内置故障类型不含 OOM](#2-关键前提内置故障类型不含-oom)
3. [数据流与内存来源](#3-数据流与内存来源)
4. [触发 Dataloader OOM 的参数](#4-触发-dataloader-oom-的参数)
5. [触发主进程 OOM 的参数](#5-触发主进程-oom-的参数)
6. [复现配方（最小组合）](#6-复现配方最小组合)
7. [两类 OOM 的区分速查](#7-两类-oom-的区分速查)

---

## 1. 故障注入子系统（框架内置）

Megatron-LM 提供了一套完整的故障注入调度子系统，用于弹性/韧性测试。

### 1.1 调用链

```
CLI args (--fault-injector-*)
   │
   ▼
megatron/training/arguments.py:3492   _add_fault_injector_args()    ← 解析参数
   │
   ▼
megatron/training/training.py:3265    构建 FaultInjectorConfig，在 train() 主循环里：
   ├─ 3285  should_setup_fault_injection_at_start → setup_fault_injection()
   └─ 3741  每个迭代 maybe_raise_workload_exception()   ← 仅主进程轮询
   │
   ▼
megatron/core/fault_injector.py:192   setup_fault_injection()
   ├─ rank0 采样故障类型/延迟 → broadcast 给所有 rank
   └─ dispatch_fault_injection(fault, delay, callback=None)   ← line 233
   │
   ▼
nvidia_resiliency_ext.shared_utils.inject_fault    ← 真正"放火"的后端
```

### 1.2 原生可配置参数

由 `FaultInjectorConfig`（`megatron/core/fault_injector.py:47-78`）自动生成 CLI，全部以 `--fault-injector-*` 开头：

| 参数 | 作用 |
|---|---|
| `--fault-injector-ranks "0,3,7"` | 显式指定注入哪些 rank |
| `--fault-injector-num-ranks N` | 随机选 N 个 rank 注入（与 ranks 互斥） |
| `--fault-injector-fault-types "hang,crash"` | 故障类型（逗号分隔，名字经 `Fault[name.upper()]` 解析） |
| `--fault-injector-fault-probabilities "2,1"` | 各类型概率（运行时归一化） |
| `--fault-injector-fault-delay SECONDS` | 从训练起点/锚点起，固定延迟多少秒触发 |
| `--fault-injector-delay-start-iteration N` | 把计时锚点改为"第 N 个迭代结束后" |
| `--fault-injector-mtti-seconds SECONDS` | 用指数分布按平均故障间隔采样延迟（delay 未设时生效） |
| `--fault-injector-offset-seconds SECONDS` | 叠加在采样延迟上的偏移 |
| `--fault-injector-seed SEED` | rank0 的 RNG 种子（保证可复现） |

**触发开关**：只要设置了 `ranks` 或 `num_ranks` 之一（`training.py:3272-3275`）。

**门槛**：必须安装 `nvidia_resiliency_ext`（`fault_injector.py:12-22`，缺失则报 `ModuleNotFoundError`）。

---

## 2. 关键前提：内置故障类型不含 OOM

后端 `nvidia_resiliency_ext/shared_utils/inject_fault.py` 的 `Fault` 枚举只有 **12 种**，**没有 OOM，也没有 dataloader-worker 维度**：

```python
class Fault(enum.Enum):
    GPU_ERROR, GPU_SLEEP, WORKLOAD_EXC, ASYNC_EXC, SIGNAL_EXC,
    OS_ABORT, LOCK_GIL, SEGFAULT, SIGINT, SIGKILL, SIGTERM, SIGSTOP
```

每种故障的投递方式决定了它影响谁——**全部在主进程**：

| 故障 | 行为 | 落点 |
|---|---|---|
| `WORKLOAD_EXC` | 设 `threading.Event`，主循环 `maybe_raise_workload_exception()` 轮询后抛异常 | 仅主训练循环 |
| `ASYNC_EXC` | 用 `ctypes` 向 `threading.main_thread()` 注入异步异常 | 仅主线程 |
| `GPU_ERROR` | 守护线程做非法 GPU 访问 `a[b]=0`（最接近"GPU 坏访存"） | 主进程的 CUDA context |
| `GPU_SLEEP` / `LOCK_GIL` | 守护线程把 GPU/GIL 长时间占住 → 模拟挂起 | 主进程 |
| `SEGFAULT` / `OS_ABORT` / `SIGKILL` 等 | 杀整个进程 | 主进程 |

`training.py:3741-3749` 的注释也印证了分类：*"Self-firing faults (signals, GIL, GPU) … workload-exception faults manifest on a later poll"*——**没有任何路径触达 dataloader worker**（worker 是 `torch.utils.data.DataLoader` 在 `data_samplers.py:105-113` 用 `num_workers` fork 出的独立子进程）。

### 结论

- 想做**主进程 OOM**：需用 `register_fault("OOM", …)` 自定义扩展 + `--fault-injector-fault-types OOM`。
- 想做 **dataloader worker OOM**：框架不直接支持，需主进程 handler 通过 `multiprocessing.Event` 通知 worker，在 `__getitem__` 中填爆内存。
- **更简单直接的方式**：把普通训练启动参数调到极端值，让其**自然 OOM**。下面详述。

---

## 3. 数据流与内存来源

Megatron 的数据流：

```
mmap .bin/.idx → worker.__getitem__ 取 1 条样本(seq_length+1 个 token id)
                 → worker collate 拼成 [micro_batch_size, seq_length+1] 张量(int64)
                 → pin_memory 拷到锁页内存
                 → 主进程接收 → 拷到 GPU → forward
```

因此：

- **单个 batch 在 CPU 上的内存** ≈ `micro_batch_size × (seq_length+1) × 8 字节`
- 再乘以 `num_workers × prefetch_factor(默认 2)` 个缓冲 batch
- 这个量级一旦超标，**还没轮到 GPU，dataloader worker 先 OOM**

> 注意：当前版本 `--batch-size` 已废弃（`arguments.py:2569`，提示改用 `--micro-batch-size`）。真正决定单卡每步内存的是 **`--micro-batch-size`**；`--global-batch-size` 只影响梯度累积步数，基本不吃内存。

---

## 4. 触发 Dataloader OOM 的参数

`data_samplers.py:105-113`：`num_workers`、`pin_memory=True`、`persistent_workers=True`，prefetch_factor 用 PyTorch 默认值 2。

| 参数 | 调哪个方向会 OOM | 机制 | 极端示例 |
|---|---|---|---|
| `--micro-batch-size` | ↑↑ | 每个 batch 张量按此线性放大，collate + pin_memory 双倍 | `--micro-batch-size 65534` |
| `--seq-length` | ↑↑ | 单条样本变长，batch 张量第二维放大 | `--seq-length 131072` |
| `--num-workers` | ↑ | 每个 worker 独立缓冲 `prefetch_factor(2)` 个 batch，且 fork 复制父进程 RSS | `--num-workers 32` |
| `--no-mmap-bin-files` | 打开它 | 默认是 mmap（懒加载）；加此 flag 把整个 `.bin` 读进每个 worker 的物理内存 | 同时数据集大时极易爆 |
| `--num-dataset-builder-threads` | ↑ | 多线程同时构建/索引数据集，峰值 host 内存翻倍 | `--num-dataset-builder-threads 16` + 大数据集 |
| `--mock-data` + `--seq-length` | ↑ | mock 数据会预生成大序列 | 配合大 seq-length |
| `--data-cache-path` 指向超大缓存 | — | 预处理缓存把 mmap 文件驻留 | 数据集本身极大时 |

---

## 5. 触发主进程 OOM 的参数

主进程 OOM 分两种：**GPU 显存 OOM**（forward/backward 阶段）和 **主机 RAM OOM**（初始化 / checkpoint 加载阶段）。

### 5.1 GPU 显存 OOM（最常见的训练中断）

| 参数 | 调哪个方向会 OOM | 机制 | 极端示例 |
|---|---|---|---|
| `--micro-batch-size` | ↑ | 激活值 ∝ mbs × seq × hidden × layers | `--micro-batch-size 4096` |
| `--seq-length` | ↑↑ | 激活 + 注意力随长度暴涨 | `--seq-length 65536` |
| `--num-layers` | ↑ | 层数线性放大激活与权重 | `--num-layers 200` |
| `--hidden-size` / `--ffn-hidden-size` | ↑ | 权重 + 激活同时放大 | `--hidden-size 16384 --ffn-hidden-size 65536` |
| `--num-attention-heads` 极端值 | — | 影响注意力中间内存 | 让 head_dim 过大/过小 |
| `--tensor-model-parallel-size` | **↓** | TP 越小，单卡放越多权重/激活（默认 1 最吃显存） | 不开 TP |
| `--pipeline-model-parallel-size` | **↓** | PP=1 时所有层堆在一卡 | 不开 PP |
| `--context-parallel-size` | **↓** | CP=1 时长序列全在一卡 | 长 seq + CP=1 |
| `--expert-model-parallel-size` / `--num-experts` | ↑ experts / ↓ EP | MoE 总 expert 参数翻倍且分得不散 | `--num-experts 256` |
| **关掉 `--recompute-activations`** / `--recompute-num-layers 0` | 不开 | 不重算激活 → 全部驻留显存 | 默认不重算时最耗显存 |
| **关掉 `--use-flash-attn`** | 不开 | 退回 O(seq²) 的标准注意力，seq 一长直接爆 | `--seq-length 32768` 不开 flash |
| 不开 `--use-distributed-optimizer` | 不开 | 优化器状态(Adam≈12×params)全在每卡 | 大模型必爆 |
| 不开 `--bf16`/`--fp16`/`--fp8`（fp32） | — | 精度翻倍，显存 ×2~×4 | fp32 + 大模型 |

### 5.2 主机 RAM OOM（主进程本身）

| 参数 | 调哪个方向会 OOM | 机制 |
|---|---|---|
| `--num-layers`/`--hidden-size` 超大 + `--use-cpu-initialization` | ↑ | 权重在 CPU 上初始化，峰值占满主机 RAM |
| `--vocab-size` / `--tokenizer-model` 巨大 | ↑ | 嵌入表(vocab×hidden)每个 rank 持有一份，加载/初始化吃 RAM |
| 大 checkpoint + `torch.load` | — | checkpoint 整体读进主机内存 |
| `--rampup-batch-size` 设置不当 | — | 训练初期偶发峰值 |

---

## 6. 复现配方（最小组合）

### 6.1 复现 Dataloader OOM

```bash
--micro-batch-size 65534 --seq-length 4096 --num-workers 8 --no-mmap-bin-files
```

（8 ranks 同节点时，每个 rank 的 worker 各自拼 `65534 × 4097 × 8B ≈ 2.1GB/batch × 2 prefetch × 8 workers`，瞬间打爆主机 RAM。）

### 6.2 复现主进程 GPU OOM（注意把 dataloader 端保持温和，否则会先 OOM 在 dataloader）

```bash
--micro-batch-size 256 --seq-length 32768 --hidden-size 12288 --num-layers 96 \
--tensor-model-parallel-size 1 --pipeline-model-parallel-size 1 \
# 不加 --recompute-activations，不加 --use-distributed-optimizer
```

### 6.3 复现主进程 Host RAM OOM

超大模型 + `--use-cpu-initialization`，或超大 `--vocab-size`/checkpoint。

---

## 7. 两类 OOM 的区分速查

| 目标 | 关键参数 | 打爆的内存 |
|---|---|---|
| **Dataloader OOM** | 猛调 `--micro-batch-size`、`--seq-length`、`--num-workers`，加 `--no-mmap-bin-files` | worker 子进程的 host RAM |
| **主进程 GPU OOM** | 保持 dataloader 温和，猛调 `--num-layers`/`--hidden-size`/`--seq-length`，TP/PP 设到 1，不开 `--recompute-activations`/`--use-distributed-optimizer` | 主进程的 GPU 显存 |
| **主进程 Host RAM OOM** | 超大模型 + `--use-cpu-initialization`，或超大 `--vocab-size`/checkpoint | 主进程的 host RAM |

**最关键的一点**：`--micro-batch-size` 同时影响两边，**值极大时 dataloader 必然先 OOM**；想稳定复现"纯 GPU OOM"，就要把 micro-batch 控制住、改去放大模型规模 / 砍掉并行与重算。

---

## 参考来源

- Megatron-LM 本地源码：`megatron/core/fault_injector.py`、`megatron/training/training.py`、`megatron/training/arguments.py`、`megatron/training/datasets/data_samplers.py`、`megatron/training/config/training_config.py`、`megatron/core/transformer/transformer_config.py`、`megatron/core/parallel_state.py`
- 后端库：[nvidia_resiliency_ext inject_fault.py](https://github.com/NVIDIA/nvidia-resiliency-ext/blob/main/src/nvidia_resiliency_ext/shared_utils/inject_fault.py)
- [NVIDIA Resiliency Extension（GitHub）](https://github.com/NVIDIA/nvidia-resiliency-ext)
- [nvidia-resiliency-ext 用法文档](https://nvidia.github.io/nvidia-resiliency-ext/inprocess/usage_guide.html)
