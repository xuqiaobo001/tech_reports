# Megatron-LM Dataloader 元数据处理与数据变换分析

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）源码
> 重点：dataloader 对训练**元数据**做了哪些处理；dataloader 处理后的数据与原始加载的训练数据有何差异
> 典型路径：GPT 自回归语言模型预训练（BERT/T5 框架一致，细节略异）

---

## 一、概念厘清："原始训练数据" 与 "元数据"

Megatron-LM 的预训练数据落盘为**两个配套文件**：

| 文件 | 内容 | 角色 |
|------|------|------|
| `<prefix>.bin` | 一段**扁平的、连续的 token id 二进制流**（按 `.idx` 中记录的 dtype 存储） | 真正的训练数据 |
| `<prefix>.idx` | 描述 `.bin` 结构的索引信息 | **元数据** |

`.idx` 就是这里所说的"元数据"。它的二进制布局（`indexed_dataset.py:147-211` 写入；`indexed_dataset.py:264-327` 读取）：

```
[9B header "MMIDIDX\x00\x00"] + [8B version=1] + [1B dtype code]
+ [8B sequence_count] + [8B document_count]
+ sequence_lengths  : int32[count]   每个序列/document 的 token 长度
+ sequence_pointers : int64[count]   每个序列在 .bin 中的字节偏移
+ document_indices  : int64[doc+1]   标记每个 document 的边界（前缀和）
+ sequence_modes    : int8[count]    (仅 multimodal) 每个序列的模态
```

**原始数据的本质：一串变长的、按 document 分组的 token 序列。** `.idx` 只记录"哪段属于哪个 document、长度多少、字节偏移在哪"。

---

## 二、Dataloader 对元数据的处理（分四层链路）

### 第 0 层：预处理阶段如何生成元数据（`IndexedDatasetBuilder` / `_IndexWriter`）

`tools/preprocess_data.py` 等脚本在 tokenize 后调用 `IndexedDatasetBuilder`，写入 `.bin` 的同时：

- 根据每条 document 的长度**计算出 `sequence_pointers`**（字节偏移 = 累加 `length × dtype_size`）
- 维护 `document_indices` 前缀和
- `finalize()` 一次性写出 `.idx`（`indexed_dataset.py:213-230`、`937-1037`）

### 第 1 层：`IndexedDataset` 读取元数据（`_IndexReader`）

训练启动时，`_IndexReader` 用 `numpy.memmap` 把 `.idx` 整个映射进内存，**零拷贝切出四个数组**（`indexed_dataset.py:280-327`）：

- `sequence_lengths`
- `sequence_pointers`
- `document_indices`
- `sequence_modes`（仅 multimodal）

`__getitem__` 用 `lru_cache` 返回 `(pointer, length, mode)`（`indexed_dataset.py:350-365`）；取真实 token 时用偏移从 `.bin` 读取（`indexed_dataset.py:790-841`），支持 mmap / file / S3 / MSC 多种 reader。

### 第 2 层：`GPTDataset` 把元数据转成训练用的**三套索引**（核心处理）

这是 dataloader 对元数据最关键的加工，发生在 `_build_document_sample_shuffle_indices`（`gpt_dataset.py:439-665`）：

1. **用元数据算训练规模**
   - `_get_num_tokens_per_epoch` = `sum(sequence_lengths[indices])`（`gpt_dataset.py:667-673`）
   - `_get_num_epochs` = 重复几遍才能凑够 `num_samples × seq_length` 个 token（`gpt_dataset.py:675-695`）

2. **`document_index`（文档顺序）** —— `_build_document_index`（`gpt_dataset.py:698-729`）
   把暴露出的 document id 平铺 `num_epochs` 份，再全局 shuffle，决定**文档被消费的顺序**。

3. **`sample_index`（样本切分）** —— C++ `build_sample_idx`（`helpers.cpp:144-249`，Python 包装 `helpers.py:12-66`）
   **这是元数据 → 训练样本的核心变换**：它把所有 document（按 document_index 顺序）当成**一条连续的 token 流**，再每隔 `seq_length + 1` 切一个样本。每个样本记录 `(document_index 中的下标, 文档内 token 偏移)`。
   > 关键语义：**样本会跨越 document 边界**——前一个文档没填满就接着拼下一个文档。这意味着 `.idx` 里"document 是独立单元"的结构在这里被"拍平"成了一维 token 流。

4. **`shuffle_index`（样本打乱）** —— `_build_shuffle_index`（`gpt_dataset.py:732-761`）
   对样本做随机置换。若最后一个 epoch 样本数 < 阈值（80%），则**单独 shuffle**（`separate_final_epoch`），避免跨 epoch 的数据泄漏。

5. 这三套索引可按 `unique_description` 的 md5 哈希缓存为 `.npy`（`gpt_dataset.py:602-615`），下次训练命中缓存直接 mmap 加载，避免重复构建。

### 第 3 层：`__getitem__` 产出单个训练样本（`gpt_dataset.py:229-350`）

取数时 `_query_document_sample_shuffle_indices`（`gpt_dataset.py:352-437`）：`shuffle_index → sample_index → 拼接 token`，跨文档拼成 `seq_length+1` 的 `text`，不足则用 `_pad_token_id` 补齐。然后派生出模型真正需要的张量：

- `tokens = text[:-1]`、`labels = text[1:]`（`gpt_dataset.py:245-251`）—— `+1` extra token 的作用正在于此，保证 input 和 label 都恰好是 `seq_length`
- `_get_ltor_masks_and_position_ids`（`gpt_dataset.py:764-838`）生成：
  - `attention_mask`：下三角因果掩码
  - `loss_mask`：默认全 1，可按 EOD / padding 置 0
  - `position_ids`：`arange`，可在 EOD 处按文档重置（`reset_position_ids`）
- **padding 后处理**：`loss_mask` 在 pad 处置 0；`tokens`/`labels` 中的 pad id 改成 `0`（让 embedding 层可映射，`gpt_dataset.py:276-280`）
- `inter_document_masking` 时额外输出 `cu_seqlens` / `max_seqlen`，并按文档重置 position id（`gpt_dataset.py:286-333`）

> 关于 pad token：`_pad_token_id` 默认取 `tokenizer.pad`，**若与其它特殊 token（如 eod）冲突则回退为 `-1`**（`megatron_dataset.py:73-114`），随后在 `__getitem__` 中按上述规则被改写为 0 并在 loss 中屏蔽。

### 第 4 层：Sampler + `torch.utils.data.DataLoader`（`data_samplers.py`）

`build_pretraining_data_loader`（`data_samplers.py:19-113`）把 `GPTDataset` 包进标准 torch DataLoader，并配上 Megatron 自定义 batch sampler：

- `MegatronPretrainingSampler`：按 `data_parallel_rank` 切出本 rank 的连续 micro-batch 片段，维护 `consumed_samples` 支持断点续训（`data_samplers.py:115-183`）
- `MegatronPretrainingRandomSampler`（cyclic）：按 epoch 重新分桶 shuffle（`data_samplers.py:314-393`）
- 另有 `MegatronFullValidationSampler`、`HybridCPMegatronPretrainingSampler` 等变体

---

## 三、处理后的数据 vs 原始数据：差异对照

| 维度 | 原始加载的数据（`.bin` + `.idx`） | Dataloader 处理后的样本 |
|------|-----------------------------------|--------------------------|
| **基本单元** | 变长 document（独立 token 序列） | **固定长度** `seq_length` 的样本 |
| **结构关系** | document 之间相互独立 | 文档被**拍平拼接成一维 token 流**，样本可**跨 document 边界** |
| **长度** | `sequence_lengths[i]` 各异 | 统一切成 `seq_length`（内部多 1 个 extra token 用于移位） |
| **dtype** | 按 `.idx` 的 dtype（常 int32/uint16） | 转成 **int64**（`torch.from_numpy(text).long()`） |
| **字段** | 只有 token id | 新增 `tokens`/`labels`/`attention_mask`/`loss_mask`/`position_ids`（及可选 `cu_seqlens`/`max_seqlen`） |
| **tokens↔labels** | 单一序列 | 拆成 **input=text[:-1]** 与 **target=text[1:]** 的自回归平移对 |
| **顺序** | 磁盘原始顺序 | 经 `document_index`(文档shuffle) + `sample_index`(切分) + `shuffle_index`(样本shuffle) 三级重排，按 epoch 重复 |
| **尾部样本** | 无概念 | 不满 `seq_length` 的样本用 pad 补齐；`loss_mask` 在 pad 处置 0、pad id 改写为 0 |
| **EOD 处理** | EOD 只是普通 token | 可选按 EOD 重置 position id / 重置 attention mask / 屏蔽 EOD 处 loss |
| **分布切分** | 全量数据 | 由 Sampler 按 `data_parallel_rank` 切片，**每个 rank 只看到自己的子集** |

### 几个最本质的差异

1. **从"文档"到"固定长度样本流"**：原始数据的 document 边界在样本构造时基本被打破（除非显式开启 `inter_document_masking` 用 `cu_seqlens` 重新标记）。这是 LLM 预训练 dataloader 与普通 CV/分类 dataloader 最大的不同——它不是一个 `__getitem__(i)` 直接对应一条原始记录，而是**对元数据做了三层索引映射**（document → token流 → 定长样本 → 全局shuffle）。

2. **元数据不只是"被读取"，而是"被重新组织"**：`sequence_lengths` 被用来算 epoch 数、被 C++ 用来切样本；`document_indices` 被用来定 document 边界；最终原始 token 没有被复制，全部通过 **memmap 偏移读取**按需取出，处理后的"样本"在物理上只是对 `.bin` 的一段段视图。

3. **处理是确定且可复现的**：所有 shuffle 用固定 `random_seed`，三套索引可缓存为 `.npy` 并按配置哈希命中，保证不同 rank、不同重启下产出一致，这是分布式训练断点续训的前提。

---

## 四、关键源码位置索引

| 关注点 | 文件 | 行号 |
|--------|------|------|
| `.idx` 二进制布局 / 写入 | `megatron/core/datasets/indexed_dataset.py` | 147-211, 937-1037 |
| `.idx` 读取（memmap 切数组） | `megatron/core/datasets/indexed_dataset.py` | 264-327 |
| `.bin` 多种 reader | `megatron/core/datasets/indexed_dataset.py` | 368-604 |
| 三套索引构建（核心） | `megatron/core/datasets/gpt_dataset.py` | 439-665 |
| 文档索引构建 | `megatron/core/datasets/gpt_dataset.py` | 698-729 |
| 样本索引构建（C++） | `megatron/core/datasets/helpers.cpp` | 144-249 |
| 样本索引构建（Python 包装） | `megatron/core/datasets/helpers.py` | 12-66 |
| `__getitem__` 样本派生 | `megatron/core/datasets/gpt_dataset.py` | 229-350 |
| mask / position 生成 | `megatron/core/datasets/gpt_dataset.py` | 764-838 |
| pad token 定义与冲突处理 | `megatron/core/datasets/megatron_dataset.py` | 73-114 |
| Sampler 与 DataLoader 包装 | `megatron/training/datasets/data_samplers.py` | 19-393 |
| 数据混合（blending）索引 | `megatron/core/datasets/helpers.cpp` | 22-142 |

---

## 五、小结

Megatron-LM 的 dataloader 并非简单地"读一条训练一条"。它把磁盘上的 `.idx` **元数据**作为输入，经过四层加工：

1. **读取**：memmap 把 `.idx` 切成 `sequence_lengths` / `sequence_pointers` / `document_indices` / `sequence_modes`；
2. **重组**：基于这些元数据构建 `document_index`、`sample_index`、`shuffle_index` 三套索引，把变长文档**拍平为定长样本流**；
3. **派生**：`__getitem__` 从 `.bin` 按偏移取 token，再生成 `tokens/labels/attention_mask/loss_mask/position_ids`；
4. **切分**：Sampler 按 data-parallel rank 切分、维护 `consumed_samples` 支持续训。

处理后的数据相对于原始数据，**结构（变长文档 → 定长样本）、字段（仅 token id → tokens/labels/masks/position）、顺序（三级 shuffle）、dtype（→int64）、padding/EOD 处理、分布切分** 全都发生了变换——而原始 token 字节始终未被复制，仅以 memmap 视图形式按需读取。

> 注：以上以 GPT 自回归路径为主线。BERT/T5 走 `build_mapping`（`helpers.cpp:268-564`），不跨 document 切分、按句打包并支持 short-sequence 概率，但"读 `.idx` 元数据 → 构建样本索引 → `__getitem__` 派生 mask/position"的整体框架一致。
