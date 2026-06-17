# MindSpeed-MM 训练框架 OOM 故障分析报告

> 本报告基于 MindSpeed-MM 源码分析，梳理训练框架中「主训练进程 / DataLoader 数据处理」的进程结构，并汇总试用阶段常见的 OOM 故障、根因与处置参数，便于在试用（PoC）阶段提前规避。

---

## 一、训练主进程与 DataLoader 的进程结构

### 调用链与进程边界

**主训练进程（每个 rank 一个）：**

```
pretrain_vlm.py: pretrain()
  → mindspeed_mm/training.py: pretrain() → train() → train_step()        (training.py:540, 736)
  → Megatron forward_backward_func(forward_step_func=forward_step)        (training.py:756)
  → forward_step() → get_batch() → next(data_iterator)                    (pretrain_vlm.py:195, 137)
```

`get_batch` 里的 `next(data_iterator)` 是从 DataLoader 取 batch 的唯一入口——它**本身不做解码**，只是从 worker 的结果队列里取数据，然后 `move_to_device` 把 CPU 张量搬到 NPU（`pretrain_vlm.py:140`）。H2D copy 确实在主进程。

**DataLoader 构造（决定进程数的关键）：**

`build_mm_dataloader` 把 Megatron 的 `args.num_workers` 透传给 `torch.utils.data.DataLoader`（`mindspeed_mm/data/__init__.py:128-134`、`dataloader.py:148`）。`--num-workers` 是 Megatron 参数（AE 路径默认 `default=2`，见 `models/ae/training/arguments.py:67`），示例脚本通常设为 8。

### 两种进程模型

| 组件 | `num_workers>0`（正常情况） | `num_workers==0` / IterableDataset |
|------|----------------|------------------------------------|
| 采样索引 | 主进程构造 sampler，worker 内迭代 | 主进程 |
| 解码/预处理（`__getitem__`） | **worker 子进程** | 主进程（阻塞训练） |
| collate | **worker 子进程** | 主进程 |
| H2D 搬运（`move_to_device`） | 主进程 | 主进程 |
| 前向/反向/优化器 | 主进程 | 主进程 |

- **`num_workers > 0`**：PyTorch 派生 `num_workers` 个子进程，解码/预处理/collate 在 worker 子进程完成，主进程只消费现成 batch，二者通过队列解耦、并行。
- **`num_workers == 0`**：退化为同进程串行，`__getitem__` 与 `collate_fn` 在主进程同步执行，阻塞训练循环。
- **特例**：当 dataset 为 `IterableDataset` 时，`prepare_sampler_dataloader` 强制 `num_workers = 0`（`dataloader.py:204-205`），如 `bagel_iterable_dataset`，此时数据处理回到主进程。

> 注意：`PrefetchGradAccDataLoader`（`dataloader.py:43-107`）会在主进程预取 `grad_acc_step` 个 batch 来统计 token 数，但它的"预取"只是从底层 `base_dataloader` 队列里多拉几份，**真正的解码仍在底层 DataLoader 的 worker 子进程里**，不改变进程划分。

---

## 二、DataLoader worker OOM 时，主进程能否感知？

### 结论

**不是完全感知不到，但"感知"很迟钝、且经常被掩盖成"训练卡死无报错"。** 分两层看：

### PyTorch 的检测机制

`num_workers>0` 时，`_MultiProcessingDataLoaderIter` 把 worker pid 注册到 `torch.utils.data._utils.signal_handling`，主进程在 POSIX 上装了 **SIGCHLD 处理器**，同时取数循环 `data_queue.get(timeout=MP_STATUS_CHECK_INTERVAL)`（默认 **5s**）有轮询兜底。worker 死亡后两条路径之一触发：

- SIGCHLD 看门狗发现死掉的是已注册 worker → 立刻打断主进程的 `get()`；
- 或轮询超时 → `_try_get_data` → `_check_workers` 发现 worker 不在了。

最终主进程抛出：
```
RuntimeError: DataLoader worker (pid(s) X) exited unexpectedly
```

### 两种 OOM，可见性天差地别

| 情况 | worker 怎么死 | 主进程能否看到原因 |
|------|------------|----------------|
| **软 OOM**（Python `MemoryError`） | worker 内分配失败抛 `MemoryError` | ✅ 能看到。异常对象经结果队列回传、主进程原样 re-raise，可见完整 traceback |
| **硬 OOM**（内核 OOM killer 发 `SIGKILL`） | 进程被直接杀，无 Python 代码运行 | ❌ 几乎看不到。worker 凭空消失，主进程只拿到笼统的 `exited unexpectedly`，真正原因只在 `dmesg -T \| grep -i "killed process"` 里 |

训练场景里 DataLoader worker 的 OOM 大多是**硬 OOM**（CPU 内存撑爆，内核直接 SIGKILL）。

### 为什么实际常表现为"完全感知不到 / 卡死"

1. **主进程通常不在 `next(data_iterator)` 里**，而是卡在前向/反向的 **HCCL collective** 上。worker 死亡不能直接打断进行中的集合通信 → 表现为 **collective 超时**（10～30 分钟），即"训练卡死、无任何错误"。
2. **节点级 OOM**：worker 被杀说明整机内存吃紧，OOM killer 可能进一步动到训练进程本身，导致 HCCL abort / rank 掉线。
3. **`persistent_workers=True` + fork**：worker 继承主进程页表，叠加 `prefetch_factor` 个预取 batch，峰值 RSS 容易爆；一旦 worker 死亡，**整个 iterator 被毒化，且本代码库无任何重建逻辑**。
4. **无上层捕获**：`get_batch` 里 `next(data_iterator)` 裸调用（`pretrain_vlm.py:137`），没有 `error_callback`、`faulthandler`、SIGCHLD 处理，崩了就崩。

---

## 三、设备显存 OOM（NPU HBM）—— 多模态主要战场

### 故障 1：变长 batch 导致激活显存波动（间歇性 OOM）
- **代码位置**：`sampler.py:690` `VariableVideoBatchSampler`，每桶 batch 大小由 `self.bucket.get_batch_size(bucket_id)` 动态决定（`sampler.py:803`）。`BucketBatchSampler`（`sampler.py:485`）同理。
- **原因**：不同分辨率/帧数的桶 yield 不同大小 micro-batch，**大桶那一步显存瞬间冲高**。
- **关键上限参数**（默认偏大，试用易踩）：`max_pixels=12845056`（`bucket_manager.py:363`）、`max_num_frame=12`、`max_dynamic_patch=6`（`multimodal_dataset.py:64,66`）。
- **处置**：收紧 `bucket_config` 的 `max_pixels / max_frames / max_dynamic_patch`；大桶用更小 batch。

### 故障 2：collate 把整批 pad 到最大样本（长尾放大）
- **代码位置**：`data_collator.py:43` `max_item_length = max(batch_lens)`；`:106` `max_n_patches = max(...)`；`:118` `max_n_images = max(...)`。
- **原因**：一个 batch 混入一张高分辨率大图/长序列，**整个 batch 所有样本都被 pad 到这个最大值**，激活显存被长尾单点放大。
- **处置**：用分桶（`BucketBatchSampler`）聚相近长度，避免长短混批；对样本做长度上限过滤。

### 故障 3：`hetero_encoder_mbs_scale` 放大视觉 encoder 的 batch
- **代码位置**：`arguments.py:118`，`pretrain_vlm.py:208` `args.micro_batch_size = pp_mbs * args.hetero_encoder_mbs_scale`。
- **原因**：异构并行时 ViT/audio encoder 的 MBS 成倍放大，**视觉侧激活显存直接翻倍**。
- **处置**：显存紧时调小该值，评估视觉 encoder 占比。

### 故障 4：部分模块不被 CP 切分（隐性显存瓶颈）
- **代码位置**：`pretrain_vlm.py:84-86` `vision_projector.context_parallel_size = 1`、`expert_model_parallel_size = 1`。
- **原因**：即便开了上下文并行，vision projector / expert 仍单卡承载，长序列/大图时是显存盲点。
- **处置**：增大 TP/PP 分摊这些不被 CP 切的模块。

### 故障 5：recompute（激活重计算）没开或配错
- **代码位置**：`arguments.py:107/111` `--recompute-skip-core-attention`、`--recompute-num-layers-skip-core-attention`（外加 Megatron 的 `--recompute-granularity/--recompute-method/--checkpoint-activations`）。
- **原因**：省激活显存最直接的开关，没开或 skip 层数配错 → 激活常驻显存过大。
- **处置**：试用阶段先开 full recompute 确定显存上限，再逐步放开。

### 故障 6：loss 计算侧的显存峰值
- **代码位置**：`utils.py:919` `compute_token_level_loss` 含 `torch.cat` / `.clone()` / `reporting_loss`；`get_tps`（`pretrain_vlm.py:154`）收集 logits。
- **原因**：token-level loss 路径多保留一份中间张量做归约，长序列下不可低估。
- **处置**：显存紧张时避免同时开 token-level 统计与 CP>1。

### 故障 7：encoder 数据平衡的重排开销
- **代码位置**：`utils.py:439` `EncoderBalanceComm.apply`，`pretrain_vlm.py:146`。内含 `all_to_all` + `torch.cat([output]+recv)`（`utils.py:485-486`）。
- **原因**：`--encoder-dp-balance` 开启后 pixel_values 在 DP 组间重排，**临时显存峰值上升**。
- **处置**：显存爆时先关该开关验证。

### 故障 8：显存碎片化
- **代码位置**：`training.py:768` 每步 `torch.cuda.empty_cache()`（受 `--empty-unused-memory-level` 控制）。
- **原因**：变长 batch 长时间交替分配，"剩余总量够但分配不出连续块"。
- **处置**：调高 `--empty-unused-memory-level`；别把 mbs 设到极限，给碎片留余量。

---

## 四、主机内存 OOM（CPU RAM）—— DataLoader worker 场景

### 故障 9：worker 并发解码 × 预取把整机 RAM 吃爆
- **代码位置**：`__init__.py:128-134` 透传 `num_workers`；`dataloader.py:147` `persistent_workers = True if num_workers>0`；`prefetch_factor` 默认。
- **原因**：`--num-workers=8` × 每个预取 batch 都在解码高分辨率图/长视频，叠加 fork 后 worker 继承主进程 RSS，节点 RAM 峰值极高 → 内核 OOM killer 杀 worker（静默失败）。
- **处置**：试用阶段先用小 `num-workers`（2~4）、调小 `prefetch_factor`。

### 故障 10：`pin_memory` + 预取队列堆积
- **代码位置**：`dataloader.py:116/168` 默认 `pin_memory=False`，但配置里可能开启。
- **原因**：开 pin_memory 额外占 pinned 内存，预取堆积时多份 batch 同时驻留。
- **处置**：调试期保持 `pin_memory=False`。

### 故障 11：IterableDataset 强制 `num_workers=0` → 退回主进程解码
- **代码位置**：`dataloader.py:204`。
- **原因**：用 `bagel_iterable_dataset` 等时，解码回到主进程**阻塞训练**，且无 worker 分摊峰值。
- **处置**：注意单样本解码体积，必要时对数据预压缩。

### 故障 12：动态 batch 的 buffer 在主机内存累积
- **代码位置**：`dynamic_batching_dataloader.py` + `batching_strategy.py` `DynBszBuffer._buffer`。
- **原因**：`DynamicBatchingDataLoader` 在主进程维护 buffer，按 `max_seq_len` 装填；`dynamic_batch_buffer_size` 偏大时 RAM 堆积。
- **处置**：调小 `dynamic_batch_buffer_size`。

### 故障 13：per-token loss 的 grad-acc 预取在主进程多存 N 份 batch
- **代码位置**：`dataloader.py:43-107` `PrefetchGradAccDataLoader`，`_generate_batches` 预取 `grad_acc_step` 个 batch（`dataloader.py:88-94`）。
- **原因**：开 per-token loss 时，主进程一次性持有整组梯度累积的 batch（CPU 张量同时驻留），RAM 峰值 ×`grad_acc_step`。
- **处置**：RAM 紧时 grad_acc step 不宜过大。

### 故障 14：checkpoint 保存的 RAM 峰值
- **代码位置**：Megatron `save_checkpoint`（`training.py:651`）。
- **原因**：序列化 fp32 optimizer states 时主进程 RAM 出现尖峰，常与 worker 解码峰值叠加 → 节点级 OOM。
- **处置**：保存 ckpt 的 step 观察 RAM；必要时在该 step 临时清空预取。

### 故障 15：软泄漏（对象累积）
- **原因**：训练循环里把 batch/loss/metrics 存进不断增长的容器（日志缓冲），RSS 随 step 单调上涨，到某 step 才爆。
- **处置**：监控 RSS 曲线是否单调上升即可判断。

---

## 五、试用阶段排查 Checklist

### 先判清楚是哪一种 OOM（路径完全不同）

| 现象 | 判定 | 优先看 |
|------|------|--------|
| forward/backward 报 NPU/HBM OOM | 设备显存（故障 1~8） | `npu-smi info` + `mem_analysis.py` |
| `DataLoader worker ... exited unexpectedly`，主日志干净 | 主机 RAM 硬 OOM（9~13） | `dmesg -T \| grep -i "killed process"` |
| RSS 随 step 单调上涨 | 软泄漏（15） | `top`/RSS 曲线 |
| 只在保存 ckpt 的 step 炸 | ckpt 峰值（14） | 保存时 RAM 监控 |
| OOM **间歇性**、非每步 | 变长长尾（1/2） | profiler 看 token/pixel 数 |
| 训练卡死、无报错、最终 HCCL 超时 | worker 被静默杀（9） | `dmesg` + 缩短 HCCL timeout |

### 框架自带的诊断工具（源码确认存在）

- `mindspeed_mm/tools/mem_analysis.py`：把 NPU 显存拆解为 `OS + CANN + Driver + GE + PTA`，PTA 再拆 `碎片化 + 多流开销 + allocated`，allocated 再拆 `静态 + 激活 + workspace`——能直接看出是激活还是碎片。
- `mindspeed_mm/tools/mem_profiler.py`：`MemoryProfiler`，封装 `torch.npu.memory._record_memory_history` + `_dump_snapshot`，可导出显存快照定位分配栈。

### 试用期保守起步建议

1. 先用小 `micro-batch-size` + 开 full recompute 跑通，确定显存上限；
2. `num-workers` 从 2~4 起步，`prefetch_factor` 调小，`pin_memory=False`；
3. 收紧 `max_pixels / max_num_frame / max_dynamic_patch` 与 `bucket_config`；
4. 开 `mem_analysis.py` 看一次激活/碎片占比，再逐项放开并行度与开关。

---

## 六、总结

这个框架的 OOM 风险高度集中在**变长数据（故障 1/2）**和 **worker 并发解码（故障 9）**两条线上——前者吃 HBM、后者吃主机 RAM，且后者会静默失败成"卡死无报错"。试用阶段把 `max_pixels / max_frames / max_dynamic_patch`、`num-workers / prefetch_factor`、`micro-batch-size` 三个旋钮先压到保守值，再用框架自带的 `mem_analysis.py` 摸清显存结构，逐项放开，基本能避开绝大多数踩坑。

> 关键认知：主进程并非真的"瞎"，但 worker 硬 OOM（最常见情况）只会给主进程一句 `exited unexpectedly`，且通常因主进程正卡在集合通信上连这句话都抛不出来——看起来就是毫无感知地卡死。**真正的 OOM 证据永远在节点的 dmesg 里，不在训练日志里。**

---

*报告生成日期：2026-06-16 · 基于 MindSpeed-MM 源码分析*
