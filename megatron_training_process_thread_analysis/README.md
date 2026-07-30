# Megatron-LM 训练过程 进程/线程 生成分析技术报告

> 分析对象：Megatron-LM / Megatron Core v0.15.0
> 关注问题：启动主训练进程后，整个训练过程会触发多少个子进程 / 子线程？
> 结论先行：**没有一个固定数字**——它取决于 `world_size`、`--num-workers`、`--async-save` 等配置。本报告给出逐项清单、计数公式、生命周期时间线与一个算例。

---

## 0. 结论速览（TL;DR）

Megatron 的训练**不是一个进程干活**，而是分两层并发：

| 层级 | 产生者 | 数量 | 性质 |
|---|---|---|---|
| **L0** | 外部 launcher（`torchrun`/`mpirun`） | `world_size` 个 | 主训练进程（每个 rank 一个 Python 进程） |
| **L1** | 每个 rank 进程内部 | 见下表 | 子进程 + 线程 |

**关键澄清**：

1. Megatron **从不**为“梯度 all-reduce 重叠 / 分布式优化器重叠 / MoE 通信重叠”创建任何 Python 线程或子进程——这部分全部是 **CUDA stream + NCCL 异步操作**（`async_ops=True`）实现的“单进程单线程多流”并发。不要把 CUDA stream 当成线程。
2. Megatron **自己创建**的进程/线程数量其实**很少且大多是 opt-in**（默认开关下几乎不产生额外线程）。
3. 真正“大量”的线程来自 **PyTorch / NCCL / CUDA 运行时内部**（intra-op 线程池、NCCL 通信线程、pin_memory 线程等），这些不是 Megatron 代码创建的，但运行时确实存在。
4. 所以**没有一个固定数字**——它取决于 `world_size`、`--num-workers`、`--async-save` 等配置。下面给出逐项清单 + 公式 + 一个算例。

---

## 1. 启动入口与 L0：主训练进程的创建

入口脚本 `pretrain_gpt.py`（`pretrain_gpt.py:489-515`）调用 `megatron.training.pretrain(...)`。但**主进程本身不是 Megatron 创建的**——它由外部 launcher 创建：

- `examples/gpt3/train_gpt3_175b_distributed.sh:77` 中的 `torchrun ${DISTRIBUTED_ARGS[@]} pretrain_gpt.py ...`
- `torchrun`（即 `torch.distributed.run`）按 `--nproc_per_node` × `--nnodes` fork 出 **`world_size` 个进程**，每个进程绑定一张 GPU，设好 `RANK/LOCAL_RANK/WORLD_SIZE` 等环境变量后执行 `pretrain_gpt.py`。

```
world_size = tensor_parallel(TP) × pipeline_parallel(PP) × data_parallel(DP)
             × context_parallel(CP) × (expert_parallel 切分)
```

> 注意：`ft_integration.setup()`（`ft_integration.py:76`）在 `initialize_megatron` 之前被调用，但若未开 `--enable-ft-package`（默认关，`ft_integration.py:79-80`）则立即 return，不产生任何东西。

---

## 2. L1：Megatron 在每个 rank 内部显式创建的 进程/线程

以下逐项给出**确切的产生位置、数量、触发条件**。分析范围排除了 `megatron/core/inference/`（推理专用，不在训练路径）和 tests。

### 2.1 子进程（multiprocessing）

| # | 来源 | 数量/进程 | 触发条件 | 代码位置 |
|---|---|---|---|---|
| P1 | **DataLoader worker 子进程** | `num_workers`（**默认 2**）× 每个 `iter()` 过的 loader | 始终（默认开）；`persistent_workers=True` 故长期驻留 | `data_samplers.py:105-113`；默认值 `arguments.py:2992` |
| P2 | **持久化 async-checkpoint worker 进程** | 1 / rank | `--async-save` **且** `--use-persistent-ckpt-worker`（forkserver） | `initialize.py:73-74` → `async_utils.py:58,91` |
| P3 | **每次保存的临时 async-save 进程** | 1 / 每次 checkpoint（非持久化路径） | `--async-save` 且未用持久化 worker | `async_utils.py:277-282`（`ctx=mp.get_context('fork')`） |
| P4 | **checkpoint 删除子进程** | 1 / 轮转删除 | `--async-save` 时用 fork 进程删旧 ckpt | `checkpointing.py:879-886` |
| P5 | **dist-checkpoint 持久化进程** | 1 / rank | 同 P2（分布式 ckpt 策略层） | `async_utils.py:380` |

> P1 的 worker 子进程里，`worker_init_fn`（`data_samplers.py:78-95`）会关闭 GPU 设备 FD，并在 `--exit-signal-handler` 时注册信号处理（**不产生线程**，只 `signal.signal()`，见 `dist_signal_handler.py:60-70`）。

### 2.2 线程（threading）

| # | 来源 | 数量/进程 | 触发条件 | 代码位置 |
|---|---|---|---|---|
| T1 | **StragglerDetector 控制线程** | **仅 rank 0**，1 个 daemon | `--log-straggler`（默认关）；开一个小 TCP 服务接收开关指令 | `utils.py:1799-1802`；配置入口 `training.py:3475-3486` |
| T2 | **Workload Inspector Web 服务线程** | 1 个 daemon | `--run-workload-inspector-server`（默认关） | `training.py:3377-3385` |
| T3 | **数据集构建线程池** | `num_dataset_builder_threads`（**默认 1**，rank0 可能放大到 `min(2, device_count)` 倍）；**短暂存在**（`with` 退出即销毁） | 数据集构建阶段 | `blended_megatron_dataset_builder.py:357,387-396`；默认值 `arguments.py:3008` |
| T4 | **checkpoint 删除线程** | 1 / 删除 | **非** `--async-save` 路径 | `checkpointing.py:890-891` |
| T5 | **remove_iter_ckpts 线程** | 1 / 保存 | 旧迭代 ckpt 清理 | `checkpointing.py:1008` |
| T6 | **async-checkpoint 文件写入线程** | `len(write_buckets)-1`（在 P3/P5 worker 进程内部） | `--async-save` 时并行写多个分桶 | `filesystem_async.py:308-314`；分桶数受 `--dist-ckpt-workers`（默认 1）影响 `arguments.py:2732` |
| T7 | **蒸馏 decode / tar 预取线程池** | `logits_load_decode_threads`（**默认 4**） | 仅离线蒸馏 `--logits-load-dir` | `utils_logits.py:422-425`；`cached_logits_loss.py:705`；默认值 `arguments.py:3472` |
| T8 | **故障注入线程** | 1 daemon | 仅测试/故障注入 | `ft_integration.py:367` |

> **关键**：T1/T2/T7/T8 都是 opt-in，**默认开关下一个都不会起**。默认配置下，每个 rank 在稳态训练时由 Megatron **直接创建的常驻线程 ≈ 0**（只有 checkpoint 保存的瞬间会短暂起 T4/T5）。

---

## 3. PyTorch / NCCL / CUDA 运行时隐式创建的线程（非 Megatron 代码，但真实存在）

这部分才是“线程大头”，但它由底层库创建，与 Megatron 无关：

| 类型 | 数量（每 rank） | 说明 |
|---|---|---|
| **PyTorch intra-op 线程池** | ≈ 物理 CPU 核数（受 `OMP_NUM_THREADS` / `torch.set_num_threads` 控制） | CPU 算子并行。**Megatron 训练路径不调用 `set_num_threads`**（仅 `batch_invariant_kernels.py:173` 用 `get_num_threads()` 取 SM 数），故用 PyTorch 默认值 |
| **pin_memory 线程** | 1 / 每个 `pin_memory=True` 的 DataLoader | `data_samplers.py:109` 设 `pin_memory=True`，PyTorch 主进程内起 1 个搬运线程 |
| **NCCL 通信线程** | 每个 NCCL communicator 若干（Python 侧 watcher + 原生 work/代理线程） | Megatron 通过 `new_group`（`parallel_state.py:240`）为 TP/PP/DP/EP/CP 及其组合创建了**几十个**进程组，每个 `ProcessGroupNCCL` 都会有自己的内部线程 |
| **torch.distributed collective 监视线程** | 取决于后端 / flight-recorder 设置 | `initialize.py:298-332` 设置 `TORCH_NCCL_DEBUG_INFO_TEMP_FILE` 等时，PyTorch C++ 侧会起 trace/watchdog 线程 |
| **Transformer Engine（TE）内部线程** | TE 自管 | 仅当 `--tp-comm-overlap`（`initialize.py:157-159` → `initialize_ub`），TE 内部按 `tp_comm_bootstrap_backend` 创建通信器及其后台线程 |

> 这些线程**无法给出精确固定数**，依赖 PyTorch/NCCL/CUDA 版本与硬件拓扑。在一张 GPU 上，单个 rank 的线程总数通常在 **几十到一百多**量级，主要由 NCCL 多通信器和 PyTorch 线程池贡献。

---

## 4. “重叠/overlap” 机制：CUDA stream，不是线程 ⚠️

这是最容易被误解的点。Megatron 的所有通信-计算重叠都是**单进程单线程多 CUDA 流**：

- 梯度 reduce 重叠：`param_and_grad_buffer.py:638-660`，用 `_coalescing_manager(..., async_ops=True)` 异步发 NCCL，必要时在专用 `communication_stream` 上（`distributed_data_parallel.py:288`，仅 `num_distributed_optimizer_instances>1`）
- 分布式优化器 param all-gather 重叠：`distributed_data_parallel.py:493-509`，forward pre-hook 触发（`overlap_param_gather`）
- FSDP 旁路流：`megatron_fsdp.py:406-407`（2 个 stream）
- MoE 通信重叠：`moe_layer.py:431`、`token_dispatcher.py:450`、`shared_experts.py:186`、`paged_stash.py:399` 各自的 `torch.cuda.Stream`
- CPU offload 优化器 D2H/H2D：`hybrid_optimizer.py:220-221`（`--overlap-cpu-optimizer-d2h-h2d`）

**它们全部不调用 `threading.Thread` / `multiprocessing` / `subprocess`**（对这三个目录 grep 验证：0 命中）。并发靠 GPU 上的多 stream + event 同步（`wait_stream`/`record_event`）实现。

---

## 5. 生命周期时间线（按发生顺序）

```
torchrun 启动
  └─[L0] fork 出 world_size 个 pretrain_gpt.py 主进程（每个绑 1 GPU）
        │
        ├─ initialize_megatron (initialize.py:42)
        │    ├─ [P2] 若 async_save+persistent_worker: 起持久化 checkpoint worker 进程
        │    ├─ torch.distributed.init_process_group (initialize.py:352) + new_group ×N
        │    │     └─[隐式] NCCL 通信线程 / torch.distributed 线程  ← 大头
        │    └─ [隐式-TE] 若 tp_comm_overlap: TE 通信器及内部线程
        │
        ├─ 数据集构建 (BlendedMegatronDatasetBuilder)
        │    └─ [T3] ThreadPoolExecutor（短暂，构建完即销毁）
        │
        ├─ DataLoader 构建 (build_train_valid_test_data_loaders)
        │    └─[延迟] 首次 iter() 时:
        │          [P1] num_workers 个 worker 子进程（persistent_workers 常驻）
        │          [隐式] 1 个 pin_memory 线程 / loader
        │
        ├─ train() 训练循环 (training.py:3241)
        │    ├─ [T1] 若 --log-straggler: rank0 起 Straggler 控制线程
        │    ├─ [T2] 若 --run-workload-inspector-server: 起服务线程
        │    ├─ [隐式] PyTorch intra-op 线程池（持续存在）
        │    └─ forward/backward/optimizer: CUDA stream 重叠（非线程）
        │
        └─ checkpoint 保存 (save_checkpoint)
             ├─ async_save: [P3]/[P5] 起保存进程 → 进程内 [T6] 写线程
             │             [P4] 起删除进程
             └─ 同步保存: [T4]/[T5] 起删除/清理线程（短暂）
```

---

## 6. 计数公式与算例

### 公式（每 rank）

```
主进程数（全局）       = world_size = TP·PP·DP·CP·(EP 切分)

每个 rank 的子进程 ≈
    DataloaderConsumingRanks 内:  num_workers × (被 iter 的 loader 数)   [P1, 常驻]
    + (1 if async_save & persistent_worker else 0)                       [P2/P5]
    + (1 per async save if async_save & !persistent)                     [P3 瞬时]
    + (1 per ckpt rotation if async_save)                                [P4 瞬时]

每个 rank 的 Megatron 线程（常驻，默认 ≈ 0）=
    (1 if --log-straggler & rank==0)            [T1]
    + (1 if --run-workload-inspector-server)    [T2]
    + (logits_load_decode_threads if 蒸馏)      [T7]
    + 构建阶段瞬时: num_dataset_builder_threads [T3]

每个 rank 的隐式线程（运行时库，非 Megatron）=
    intra-op 线程池(~CPU核数) + pin_memory(1/loader) + NCCL×组数 + ...
```

### 算例：1 节点 8 GPU，TP=2 / PP=1 / DP=4，全部默认参数（`num_workers=2`，无 async_save、无 straggler、无蒸馏）

- **L0 主进程**：`world_size = 2×1×4 = 8` 个 `pretrain_gpt.py` 进程。
- **每个 rank（默认开关）**：
  - Megatron 显式**常驻线程：0**（straggler/inspector/蒸馏全关）。
  - DataLoader（仅实际迭代数据的 rank，tp_src rank；core 数据集 `is_distributed=True` 时各 rank 都建 loader）：**train loader → 2 个 worker 子进程 + 1 个 pin_memory 线程**；eval 时 valid/test loader 再各加 `num_workers` 个。
  - **隐式线程（PyTorch/NCCL）**：intra-op 池（约 CPU 核数）+ 多个 NCCL 通信线程（TP/PP/DP/EP 组）+ pin_memory 线程 → 单 rank **约几十~上百**线程，绝大部分来自 NCCL 与 PyTorch 线程池。
- **全局合计**：8 主进程 + 约 8×2 = 16 个 dataloader worker 子进程 + 一批隐式线程。**整个作业可见进程数 ≈ 8（主） + 16（dataloader）(+ 评估时再增)**，远没有想象中那么多；线程多但绝大多数是库内部线程。

若再开 `--async-save --use-persistent-ckpt-worker`：每 rank **+1 个常驻 checkpoint worker 进程**；保存时该进程内短暂起若干写线程。

---

## 7. 给运维/调试的要点

1. **看真实线程/进程**：`htop -p <rank pid>`、`pstree -p <launcher pid>`、`py-spy dump --pid <pid>` 能看到上述清单实际落地。
2. **想限制线程数**（避免 oversubscription）：设 `OMP_NUM_THREADS` 控制 PyTorch intra-op 池；用 `--num-workers` 控制 DataLoader 子进程；NCCL 线程由 `NCCL_*` 环境变量与进程组数量决定。
3. **查“幽灵线程”**：默认配置下若看到很多线程，基本都来自 NCCL/PyTorch，而非 Megatron；Megatron 自己的线程只在开了 `--log-straggler` / `--run-workload-inspector-server` / 蒸馏 / async checkpoint 时出现。
4. **子进程僵尸**：async checkpoint 的删除/保存进程由 `finalize_deletion_processes`（`async_utils.py:142`）在 `maybe_finalize_async_save` 中回收，正常训练循环会定期清理。

---

## 8. 参考代码索引

| 关注点 | 位置 |
|---|---|
| 主入口 | `pretrain_gpt.py:489` → `megatron/training/training.py:1004` (`pretrain`) |
| 分布式初始化 | `megatron/training/initialize.py:42` (`initialize_megatron`)、`:352` (`init_process_group`) |
| DataLoader | `megatron/training/datasets/data_samplers.py:105-113` |
| 数据集构建线程池 | `megatron/core/datasets/blended_megatron_dataset_builder.py:357` |
| StragglerDetector 线程 | `megatron/core/utils.py:1799` |
| async checkpoint worker | `megatron/training/async_utils.py:58`、`megatron/core/dist_checkpointing/strategies/async_utils.py:279,380` |
| checkpoint 删除线程/进程 | `megatron/training/checkpointing.py:879-891,1008` |
| 重叠=CUDA stream（非线程） | `megatron/core/distributed/distributed_data_parallel.py:288`、`param_and_grad_buffer.py:638-660` |
| MoE stream 重叠 | `megatron/core/transformer/moe/moe_layer.py:431` 等 |

---

**一句话总结**：Megatron 训练由 `torchrun` 启动 `world_size` 个主进程；每个主进程在默认配置下由 Megatron **直接创建的常驻线程几乎为 0、子进程主要是 `num_workers`(默认2) 个 DataLoader worker**；大量线程来自 PyTorch intra-op 池与 NCCL 通信器；所有“通信重叠”都是 CUDA stream 而非线程；额外的 straggler/inspector/蒸馏/checkpoint 线程均为 opt-in。
