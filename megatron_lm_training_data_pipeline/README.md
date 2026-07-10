# Megatron-LM 训练数据处理与加载全流程

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）源码
> 重点：训练过程中数据从离线预处理到流入模型 forward/loss 的完整处理与加载流程
> 配套报告：《Megatron-LM Dataloader 元数据处理与数据变换分析》（侧重元数据与差异）

---

## 全局视角：数据流的 7 个阶段

```
[阶段0 离线]   原始文本 --tokenize-->  .bin (token流) + .idx (元数据)
                                            │
[阶段1 启动]   .idx --memmap读取--> IndexedDataset (low-level)
                 │                       │ 多数据集混合
                 ├── split切分 ──> GPTDataset (mid-level)：构建 document/sample/shuffle 三套索引
                 │                       │
                 └──> BlendedDataset (top-level)：构建 dataset_index/dataset_sample_index 混合索引
                                            │
[阶段2]   Dataset + consumed_samples --> MegatronSampler (按DP rank切batch) --> torch DataLoader (多进程worker)
                                            │
[阶段3]   iter(dataloader) --> data_iterator (+ HybridCP 包装)
                                            │
[阶段4 训练循环]  while consumed_samples < train_samples:
                      train_step -> forward_step -> get_batch:
                          next(data_iterator) ──> to cuda ──> TP广播 ──> CP切片
                                            │
[阶段5]   tokens/labels/attention_mask/loss_mask/position_ids 流入模型 forward
[阶段6]   loss_mask 应用 --> 反向 --> 更新
```

---

## 阶段 0：离线预处理（生成 `.bin` + `.idx`）

发生在训练之前，由 `tools/preprocess_data.py` 等脚本完成：

1. 读取原始 json/jsonl 文本
2. 用 tokenizer 分词，得到每条 document 的 token id 序列
3. 调用 `IndexedDatasetBuilder`（`indexed_dataset.py:937-1037`）：
   - `add_document()` 把 token 写入 `.bin`，同时记录长度
   - `finalize()` 写出 `.idx`：根据每条 document 长度**累加算出 `sequence_pointers`**（字节偏移），维护 `document_indices` 前缀和，写入 header/version/dtype code

> 输出物：`.bin`（扁平 token 字节流）+ `.idx`（描述 document 结构的元数据）。这是训练时唯一的磁盘数据源。

---

## 阶段 1：启动期数据集构建（分布式 rank-aware）

入口：`build_train_valid_test_datasets` → `BlendedMegatronDatasetBuilder(...).build()`（`blended_megatron_dataset_builder.py`）。这是一个**三层嵌套**的 Dataset 构造过程。

### 1.1 三层 Dataset 架构

| 层级 | 类 | 职责 |
|------|----|----|
| Low-level | `IndexedDataset` | 读 `.idx`/`.bin`，按偏移取 token |
| Mid-level | `GPTDataset` | 把变长 document 拍平成定长样本，构建三套索引 |
| Top-level | `BlendedDataset` | 把多个 `GPTDataset` 按权重混合 |

### 1.2 构建顺序（关键）

1. **读 `.idx`**：对每个数据路径，`GPTDataset.build_low_level_dataset` → `IndexedDataset(path, mmap=True)`，用 `numpy.memmap` 把 `.idx` 切出 `sequence_lengths / sequence_pointers / document_indices / sequence_modes` 四个数组（`indexed_dataset.py:280-327`）。

2. **切分 train/valid/test**：`numel_low_level_dataset` 取 document 数，按 `split_matrix`（如 train=[0,0.969], valid=[0.969,1.0]）生成 `indexed_indices = arange(beg, end)`（`blended_megatron_dataset_builder.py:470-472`）。注意 **GPT 是按"序列/document"切分**，BERT 是按 document 切。

3. **构建 `GPTDataset`（mid-level）** —— 触发 `_build_document_sample_shuffle_indices`（`gpt_dataset.py:439`），生成训练用的三套索引：
   - `document_index`：文档顺序（shuffle + 按 epoch 平铺）
   - `sample_index`：C++ `build_sample_idx` 把文档拍平成 `seq_length+1` 定长样本，**可跨文档**
   - `shuffle_index`：样本全局打乱

4. **构建 `BlendedDataset`（top-level）** —— 若有多个数据集，`_build_indices`（`blended_dataset.py:110`）调用 C++ `build_blending_indices`（`helpers.cpp:77`）生成：
   - `dataset_index`：每个样本取自哪个子数据集
   - `dataset_sample_index`：取该子数据集的第几个样本
   - 用"累积误差最大化"算法保证混合比例严格逼近权重（不是简单按概率采样）

### 1.3 分布式 rank 协调（重要）

`build_generic_dataset`（`blended_megatron_dataset_builder.py:490`）实现 **rank-0 先建 → barrier → 其它 rank 后建**：

- rank 0 真正计算所有索引并**写入 `path_to_cache`**（按配置 md5 哈希命名的 `.npy`）
- 其它 rank 在 barrier 后走**缓存命中**路径，直接 mmap 加载

所有索引（三套 + 两套混合）都可缓存为 `.npy`，按 `unique_description_hash` 命名，保证可复现、避免重复计算（`gpt_dataset.py:602`、`blended_dataset.py:130`）。

---

## 阶段 2：DataLoader 构建

入口：`build_pretraining_data_loader(dataset, consumed_samples)`（`data_samplers.py:19`）。

1. **选 batch sampler**（依据 `args.dataloader_type` 与 split）：
   - `single` → `MegatronPretrainingSampler`：顺序遍历样本，按 DP rank 切 micro-batch
   - `cyclic` → `MegatronPretrainingRandomSampler`：按 epoch 分桶重新 shuffle
   - `valid + full_validation` → `MegatronFullValidationSampler`：micro_batch=1，支持小数据集
   - `hybrid_context_parallel` → `HybridCPMegatronPretrainingSampler`：一次吐整个 global batch

2. **`MegatronPretrainingSampler` 的切分逻辑**（`data_samplers.py:169-183`）：
   ```python
   for idx in range(consumed_samples, total_samples):
       batch.append(idx)
       if len(batch) == micro_batch_size * data_parallel_size:
           # 每个 DP rank 拿自己那段连续的 micro_batch_size 个样本
           yield batch[rank*mbs : (rank+1)*mbs]
   ```
   - `consumed_samples` 决定**从第几个样本开始**——这是断点续训的关键
   - `drop_last` 丢弃最后不完整的 batch

3. **包进 torch DataLoader**（`data_samplers.py:105-113`）：
   ```python
   torch.utils.data.DataLoader(
       dataset, batch_sampler=batch_sampler,
       num_workers=args.num_workers,       # 多进程预取
       pin_memory=True,                    # 锁页内存，加速 H2D
       persistent_workers=...,             # worker 常驻，避免每 epoch 重建
       worker_init_fn=...,                 # worker 关闭 GPU fd，防止显存泄漏
   )
   ```

> `consumed_samples` 的来源：`build_train_valid_test_data_loaders`（`training.py:4377`）从 `args.consumed_train_samples` 取（checkpoint 恢复时填入），并按 phase（变长训练阶段）折算。

---

## 阶段 3：构建迭代器

`build_train_valid_test_data_iterators`（`training.py:4465`）对 train/valid/test 分别 `iter(dataloader)`，得到 `train_data_iterator` / `valid_data_iterator` / `test_data_iterator`。若开 hybrid CP，再包一层 `HybridCPDataLoaderWrapper`（`training.py:3375`）做负载均衡。

---

## 阶段 4：训练循环消费数据

### 4.1 外层循环（`training.py:1527`）

```python
consumed_samples = 0
while consumed_samples < args.train_samples:
    update_num_microbatches(consumed_samples)   # 决定本步切几个 micro-batch
    consumed_samples += get_current_global_batch_size()
    ... train_step(...) ...
```

### 4.2 `train_step` → `forward_step` → `get_batch`（数据真正被取出）

每个 micro-batch 调用一次 `next(data_iterator)`。完整路径在 `pretrain_gpt.py`：

1. **取数 + 上 GPU**（`pretrain_gpt.py:118-127`）：
   ```python
   if tp_rank == 0:
       batch = next(data_iterator)          # 拿到一个 micro-batch 的 dict
       for key in BATCH_KEYS:
           batch[key] = batch[key].cuda(non_blocking=True)   # pin_memory 异步传输
   ```
   - 注意：**只有 tensor-parallel rank 0 真正读 DataLoader**，其它 TP rank 不重复读

2. **TP 组广播**（`get_batch_on_this_tp_rank`，`pretrain_gpt.py:128`）：rank 0 把 batch broadcast 给同 TP 组其它 rank，保证 TP 各 rank 输入一致

3. **packed sequence 展平**（`flatten_batch_for_packed_sequences`）

4. **CP 组切片**（`get_batch_on_this_cp_rank`，`pretrain_gpt.py:162`）：context parallel 下，把一条长序列切成 `cp_size` 段，每个 CP rank 只拿自己那段（token + cu_seqlens + position_ids 都相应切片）

5. **返回** `[tokens, labels, attention_mask, loss_mask, position_ids, ...]`（按 `BATCH_KEYS` 固定顺序）

### 4.3 Pipeline Parallel 的数据分布

- **第一 stage**：拿到完整 batch，做 embedding，输出激活给下一 stage
- **中间 stage**：`get_batch` 在非首尾 stage 直接返回 `[None]*`（`pretrain_gpt.py:116`），它们只接收上一 stage 的激活张量，不碰数据迭代器
- **最后 stage**：拿到 `loss_mask`，计算 loss 并反向
- 因此**只有 PP 首/尾 stage（和带 cu_seqlens 的 stage）真正消费 DataLoader 数据**

---

## 阶段 5 / 6：流入模型与 loss

`GPTDataset.__getitem__` 产出的 dict（`tokens/labels/attention_mask/loss_mask/position_ids`）经上述广播/切片后进入模型 forward：

- `tokens` → embedding（注意 pad 位置 token id 已被改成 0，embedding 可映射）
- `position_ids` → 位置编码（inter_document_masking 时按文档重置）
- `attention_mask` / `cu_seqlens` 限制注意力范围（因果 + 文档内）
- `labels` 与模型 logits 计算 loss，再用 **`loss_mask` 屏蔽 pad/EOD 位置**（`pretrain_gpt.py:204` `loss_func`），只对有效 token 算梯度

---

## 贯穿全流程的关键机制汇总

| 机制 | 作用 | 位置 |
|------|------|------|
| **memmap 读 `.idx`/`.bin`** | 零拷贝按偏移取 token，原始数据从不被复制 | `indexed_dataset.py:280` |
| **三套索引（doc/sample/shuffle）** | 变长文档 → 定长样本 + 全局打乱 | `gpt_dataset.py:439` |
| **两套混合索引（dataset_index）** | 多数据集按权重精确混合 | `blended_dataset.py:110` |
| **索引缓存 `.npy`（md5 命名）** | 避免重复构建，跨重启可复现 | `gpt_dataset.py:602` |
| **rank-0 先建 → barrier** | 分布式下只算一次索引，其它 rank 走缓存 | `blended_megatron_dataset_builder.py:523` |
| **`consumed_samples`** | 断点续训从正确位置恢复 | `data_samplers.py:124`, `training.py:4408` |
| **DP rank 切 micro-batch** | 数据并行，每 rank 看不同样本 | `data_samplers.py:169` |
| **TP rank0 唯一读 + 广播** | 避免重复读数据 | `pretrain_gpt.py:119` |
| **CP rank 切序列段** | 长序列上下文并行 | `pretrain_gpt.py:162` |
| **`num_workers` 多进程预取 + pin_memory** | 隐藏 I/O，异步 H2D | `data_samplers.py:105` |
| **`persistent_workers` + worker 关 GPU fd** | worker 常驻且不持有显存 | `data_samplers.py:78-113` |
| **`drop_last` / `separate_final_epoch`** | batch 整齐 + 防止 epoch 间数据泄漏 | `data_samplers.py:131`, `gpt_dataset.py:535` |

---

## 数据结构演进（一张表看清"数据"在每个阶段是什么）

| 阶段 | 数据形态 | 形状/特征 |
|------|----------|-----------|
| 磁盘 `.bin` | 扁平 token 字节流 | 1-D，按 dtype 存储 |
| 磁盘 `.idx` | 元数据（lengths/pointers/doc_indices） | 描述 document 结构 |
| `IndexedDataset` | 变长 document 序列 | `len = sequence_count` |
| `GPTDataset` 三套索引后 | 定长样本的逻辑映射 | `len = num_samples`，每样本 `seq_length+1` |
| `GPTDataset.__getitem__` | 一个样本的 dict | tokens/labels/mask/position，各 `seq_length` |
| `BlendedDataset.__getitem__` | 同上 + `dataset_id` | 标注来自哪个子集 |
| Sampler yield | 一个 micro-batch 的下标列表 | `micro_batch_size` 个 int |
| DataLoader collate | batched dict | 各张量 `[mbs, seq_length]` |
| `get_batch` 后 | cuda 张量 + TP广播 + CP切片 | 每张量适配本 rank 并行度 |
| 进入模型 | tokens/labels/mask/position_ids | 喂入 forward，loss 用 loss_mask |

---

## 一句话总结

训练时的数据流是一条 **"离线 tokenize → memmap 读元数据 → 三套索引拍平打乱 → 多进程 DataLoader 按 DP 切 batch → TP 唯一读+广播 → CP 切序列段 → 喂模型并用 loss_mask 求损失"** 的流水线，其中 `.idx` 元数据是驱动整条流水线"如何切样本、如何混合、如何续训"的核心，而原始 token 字节始终以 memmap 视图按需读取、从不复制。

---

## 关键源码位置索引

| 关注点 | 文件 | 行号 |
|--------|------|------|
| 离线预处理（写入 .bin/.idx） | `tools/preprocess_data.py` + `megatron/core/datasets/indexed_dataset.py` | 937-1037 |
| 三层 Dataset 构建 | `megatron/core/datasets/blended_megatron_dataset_builder.py` | 77-551 |
| 多数据集混合索引 | `megatron/core/datasets/blended_dataset.py` | 110-242 |
| 三套训练索引（核心） | `megatron/core/datasets/gpt_dataset.py` | 439-665 |
| `.idx` memmap 读取 | `megatron/core/datasets/indexed_dataset.py` | 264-327 |
| 样本切分（C++） | `megatron/core/datasets/helpers.cpp` | 144-249 |
| DataLoader + Sampler | `megatron/training/datasets/data_samplers.py` | 19-393 |
| DataLoader 构建/续训折算 | `megatron/training/training.py` | 4377-4462 |
| 训练循环 | `megatron/training/training.py` | 1527-1530 |
| `get_batch`（取数+TP广播+CP切片） | `pretrain_gpt.py` | 92-176 |
| `loss_func`（loss_mask 应用） | `pretrain_gpt.py` | 204 |
