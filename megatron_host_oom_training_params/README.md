# Megatron-LM 训练参数触发 Host 内存 OOM 分析报告

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）
> 核心问题：配置哪些训练启动参数会分别触发 **dataloader host OOM** 与 **主进程 host OOM**
> 日期：2026-07-06

---

## 目录

1. [为什么 host OOM 能干净地分两类](#1-为什么-host-oom-能干净地分两类)
2. [触发 Dataloader host OOM 的参数](#2-触发-dataloader-host-oom-的参数)
3. [触发主进程 host OOM 的参数](#3-触发主进程-host-oom-的参数)
4. [两类 host OOM 速查与区分](#4-两类-host-oom-速查与区分)
5. [Host OOM 与 ModelArts 重调度的关系](#5-host-oom-与-modelarts-重调度的关系)

---

## 1. 为什么 host OOM 能干净地分两类

与 HBM 不同，**host 内存两边都碰**：

- **dataloader 侧**：worker collate 拼 `[mbs, seq+1]` 张量、pin_memory 锁页拷贝、`num_workers × prefetch_factor` 个 batch 驻留共享内存、`.bin` 是否 mmap。
- **主进程侧**：`--use-cpu-initialization` 在 CPU 上初始化权重、`torch.load(ckpt, map_location='cpu')` 整体读 checkpoint、`--optimizer-cpu-offload` / `--cpu-offloading*` 把优化器状态/激活/权重搬到 CPU。

所以 `batch_size=65534 → OOM` 这一例，爆的就是 **dataloader worker 的 host 内存**（collate 一个超大 batch 时）。

---

## 2. 触发 Dataloader host OOM 的参数

> 失败位置：dataloader worker 子进程，主进程随后报 `DataLoader worker (pid X) died unexpectedly`。

`data_samplers.py:105-113`：`num_workers`、`pin_memory=True`、`persistent_workers=True`，prefetch_factor 走 PyTorch 默认 **2**（Megatron 标准 dataloader 不暴露这个 flag）。

**host 内存占用公式**：
```
≈ num_workers × prefetch_factor(2) × (micro_batch_size × seq_length × token字节)
  + pin_memory 锁页拷贝 + （若关闭 mmap）整份 .bin 驻留
```

| 参数 | 调哪个方向 | 机制 | 极端示例 |
|---|---|---|---|
| `--micro-batch-size` | ↑↑↑ | 每个 collate 张量按此线性放大 | `--micro-batch-size 65534` |
| `--seq-length` | ↑↑ | 单条样本变长，张量第二维放大 | `--seq-length 131072` |
| `--num-workers` | ↑ | 每个 worker 独立缓冲 `prefetch_factor(2)` 个 batch；fork 还复制父进程 RSS | `--num-workers 32` |
| `--no-mmap-bin-files` | **打开它** | 默认 `mmap_bin_files=True`（懒加载）；加此 flag 设为 False → 把整份 `.bin` 读进每个 worker 的物理内存 | 配合大数据集极易爆 |
| `--num-dataset-builder-threads` | ↑ | 多线程并行构建/索引数据集，host 峰值翻倍 | `--num-dataset-builder-threads 16` |
| `--data-cache-path` 指向超大缓存 | — | 预处理缓存把 mmap 文件驻留 | 数据集本身极大时 |

**最小复现组合**（dataloader worker host OOM）：
```bash
--micro-batch-size 65534 --seq-length 4096 --num-workers 8 --no-mmap-bin-files
```
→ 每个 worker 拼 `65534 × 4097 × 8B ≈ 2.1GB/batch × 2 prefetch × 8 workers`，再叠 8 ranks 同节点，瞬间打爆主机 RAM。

---

## 3. 触发主进程 host OOM 的参数

> 失败位置：主进程（main process），非 dataloader。
> 前提：把 dataloader 端保持温和，改去让**主进程**在 host 上吃满内存：模型 init / checkpoint 加载 / optimizer 与激活 offload。

| 参数 | 调哪个方向 | 机制 | 代码依据 |
|---|---|---|---|
| `--use-cpu-initialization` | **打开它** | 权重在 CPU RAM 上初始化后再搬到 GPU；超大模型在 init 阶段就把主机内存撑爆 | `model_parallel_config.py:90`；`initialize.py:139` 某些场景自动置 True |
| 大 checkpoint + `torch.load(..., map_location='cpu')` | checkpoint 越大越爆 | 加载时**整个 checkpoint 读进主机内存** | `checkpointing.py:1486,2273` 都是 `map_location='cpu'` |
| `--optimizer-cpu-offload`（+ `--optimizer-offload-fraction 1.0`） | **打开它** | 把优化器状态(Adam≈12×params)搬到 CPU RAM：省了 HBM，却吃主机内存 | `arguments.py:2621,2627` |
| `--cpu-offloading` / `--cpu-offloading-num-layers >0` | 打开 / ↑ | 把激活/权重 offload 到 CPU，主机内存随层数增长 | `arguments.py:1730-1731,2081-2084` |
| `--vocab-size` 极大（配合 cpu-init） | ↑ | 嵌入表 `vocab × hidden`，cpu-init 时在 host 上创建 | transformer_config 字段 |
| `--num-dataset-builder-threads` | ↑ | 主进程侧并行建数据集的峰值 | `arguments.py:3008` |

**最小复现组合**（主进程 host OOM，模型初始化阶段）：
```bash
# 超大模型 + CPU 初始化（dataloader 保持温和）
--hidden-size 12288 --num-layers 96 --vocab-size 256000 \
--use-cpu-initialization \
--micro-batch-size 1 --num-workers 0
```
→ init 阶段权重在主机 RAM 上一次性铺开，直接 host OOM（还没轮到 dataloader）。

**或 checkpoint 加载阶段爆**：直接拿一个超大 checkpoint 做 `--load` 加载，`torch.load(map_location='cpu')` 把它整体读进主机内存。

---

## 4. 两类 host OOM 速查与区分

| 目标 | 核心参数 | 爆在哪 / 观察特征 |
|---|---|---|
| **Dataloader host OOM** | 猛调 `--micro-batch-size`、`--seq-length`、`--num-workers`，加 `--no-mmap-bin-files` | worker 子进程被 OOM Killer 杀，主进程报 `DataLoader worker (pid X) died`；退出码 137 或 1 |
| **主进程 host OOM（init）** | `--use-cpu-initialization` + 极大模型/`--vocab-size` | 模型构造阶段主进程被杀；退出码 137 |
| **主进程 host OOM（ckpt load）** | 大 checkpoint + `--load` | `torch.load` 阶段爆；退出码 137 |
| **主进程 host OOM（offload）** | `--optimizer-cpu-offload` / `--cpu-offloading*` | 训练稳态后优化器/激活搬 CPU 累积爆；退出码 137 |

**一句话区分**：
- 要 **dataloader host OOM** → 把 `--micro-batch-size`/`--seq-length`/`--num-workers` 推到极端 + `--no-mmap-bin-files`。爆在 worker 子进程。
- 要 **主进程 host OOM** → dataloader 温和，但开 `--use-cpu-initialization`（超大模型/`--vocab-size`）、加载超大 checkpoint、或开 `--optimizer-cpu-offload`/`--cpu-offloading*`。爆在主进程。

---

## 5. Host OOM 与 ModelArts 重调度的关系

> 参考华为云 [训练作业故障恢复](https://support.huaweicloud.com/usermanual-standard-modelarts/develop-modelarts-0012.html)。

和 HBM OOM（瞬间崩溃、退出码 1）不同，**host OOM 有个特有的坑**：内存打满后，OOM Killer 不一定立刻触发，系统可能先**长时间 swap 颠簸**——此时进程"还活着"但几乎无进展，ModelArts 会判定为 **"业务无法中断一直处于运行状态" → 无法触发无条件 Job 级重调度**，只能靠"作业卡死重启"。

所以要稳定让 host OOM 触发重调度，建议：

1. 让分配**一次到位**（如 `--micro-batch-size` 直接拉到极端），让 OOM Killer **秒杀**而不是渐进拖垮；
2. 或同时开 `fault-tolerance/hang-retry=true`（卡死重启）兜住"swap 颠簸"分支；
3. 别在 worker 里用 mmap 慢加载 + 渐进式吃内存的组合（最容易陷入颠簸）。

若被 OOM Killer 干净杀掉 → SIGKILL → 退出码 **137（非0）** → 满足触发条件 ✅。

---

## 参考来源

- **华为云 ModelArts 文档**：[训练作业故障恢复](https://support.huaweicloud.com/usermanual-standard-modelarts/develop-modelarts-0012.html)
- **Megatron-LM 本地源码**：
  - `megatron/training/datasets/data_samplers.py:81-113`（worker 关闭 GPU FD、num_workers、pin_memory、persistent_workers）
  - `megatron/training/arguments.py:2569-3008`（batch-size/seq-length/num-workers/no-mmap-bin-files/num-dataset-builder-threads）
  - `megatron/training/checkpointing.py:1486,2273`（`torch.load(..., map_location='cpu')`）
  - `megatron/core/model_parallel_config.py:90`（`use_cpu_initialization`）
  - `megatron/core/datasets/blended_megatron_dataset_config.py:60`（`mmap_bin_files: bool = True`）
