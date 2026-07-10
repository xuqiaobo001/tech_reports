# Megatron-LM 数据存储选型：Dataloader 数据能否写入 NFS 并在训练节点使用

> 分析对象：Megatron-LM（NVIDIA/Megatron-LM）源码
> 问题：dataloader 产生的数据能否写入 NFS 文件系统？能否在训练循环节点直接使用 NFS 中的数据？
> 配套报告：
> - 《Megatron-LM Dataloader 元数据处理与数据变换分析》
> - 《Megatron-LM 训练数据处理与加载全流程》

---

## 结论速览

**可以**——把 `.bin`/`.idx` 原始数据和 `.npy` 索引缓存都放 NFS，配合 `--data-cache-path` + `--no-mmap-bin-files`，甚至用 `--dataloader-fast-cache-load` 实现"预备节点算索引写 NFS、训练节点只读缓存"的解耦架构。但**逐样本张量不要落盘**；且 NFS 在高并发随机读下容易成为瓶颈，规模大时应换并行文件系统或本地 NVMe 缓存。

---

## 一、先分清"dataloader 产生的数据"是哪三类

在 Megatron 里，"dataloader 产生的数据"对应三种不同的东西，它们放 NFS 的可行性和建议完全不同：

| 类别 | 是什么 | 产生时机 | 体量 |
|------|--------|----------|------|
| **A. 原始数据** `.bin` / `.idx` | token 流 + 元数据 | 离线 `preprocess_data` 生成，训练时只读 | 大（GB~TB） |
| **B. 索引缓存** `.npy` | `document_index` / `sample_index` / `shuffle_index` / `dataset_index` / `dataset_sample_index` + `description.txt` | **dataloader 启动期构建**（rank0 算、写缓存，其它 rank 命中） | 中（MB~GB） |
| **C. 逐样本张量** | `tokens/labels/attention_mask/loss_mask/position_ids` 的 dict | `__getitem__` **每个 micro-batch 实时产生**，流式进 GPU | 极小但极频繁 |

> 你想要的"写入 NFS、训练节点再用"，最契合的是 **B（索引缓存）**；A 也行但要注意性能；C **没有内置落盘路径，也不应该落盘**。

---

## 二、三类数据放 NFS 的结论

### ✅ B. 索引缓存 `.npy` 放 NFS —— 最推荐，这正是 Megatron 设计的用法

`path_to_cache` 就是一个普通目录路径，可以指向 NFS 挂载点。这样：

- 在**预备节点**（或 rank0）跑一次，把 document/sample/shuffle/blending 索引算好写到 NFS
- 所有**训练节点**命中缓存，直接 mmap 加载 `.npy`，**完全不重复计算**

这正是源码里 `rank-0 先建 → barrier → 其它 rank 走缓存`（`blended_megatron_dataset_builder.py:523`）模式的延伸——只是把它跨作业、跨节点用 NFS 串起来。

### ✅ A. 原始 `.bin`/`.idx` 放 NFS —— 可行，但建议关 mmap

NFS 上读 `.bin` 没问题，但有两个注意点：

- **`mmap` over NFS** 有已知的一致性/性能问题（页缓存、随机读放大）。源码里 `.bin` 默认用 `_MMapBinReader`（`indexed_dataset.py:389`），**NFS 场景应改成文件 reader**：加 `--no-mmap-bin-files`，走 `_FileBinReader`（seek+read，`indexed_dataset.py:431`）。
- `.idx` 用 `numpy.memmap` 读（`indexed_dataset.py:280`），体量小，NFS 上一般可接受。

### ❌ C. 逐样本张量 —— 不要落盘到 NFS

Megatron **没有**把逐样本张量写盘的机制，样本是 DataLoader worker 进程实时拼出来的（`gpt_dataset.py:229`）。即便自己改造去写，也得不偿失：

- 样本产生成本很低（就是按偏移取 token + 生成 mask），I/O 反而会成为瓶颈
- 写 NFS 的吞吐远低于 on-the-fly 生成 + 直接送 GPU 的吞吐
- **正确做法：样本永远在训练时实时生成**，只把"昂贵的索引计算结果（B）"持久化到 NFS

---

## 三、具体配置（数据 + 缓存都在 NFS）

```bash
# 原始数据在 NFS
--data-path /nfs/datasets/my_corpus
# 索引缓存写到 NFS（这是关键）
--data-cache-path /nfs/cache/megatron_indices
# NFS 上避免 mmap 读 .bin
--no-mmap-bin-files
```

### "预构建 → 训练节点直接消费" 的解耦流程

这就是想要的"一个节点产生、训练节点用"。用 **fast cache load** 路径：

1. **预备阶段**：在某个节点跑一次（正常模式），让 rank0 把索引算好写到 `--data-cache-path`（NFS）。索引文件名是按配置 md5 的 `unique_description_hash` 命名的（`gpt_dataset.py:476`），所以**只要配置不变，缓存就能被命中**。

2. **训练阶段**：所有训练节点加 `--dataloader-fast-cache-load`，**完全跳过索引构建**，只从 NFS 读缓存：

   ```bash
   --data-cache-path /nfs/cache/megatron_indices
   --dataloader-fast-cache-load
   --no-mmap-bin-files
   # 注意：fast_cache_load 要求用 per-split 数据路径，不能用 --data-path
   --train-data-path /nfs/datasets/my_corpus
   ```

   ⚠️ 源码里有个硬约束：`fast_cache_load` 和 `--data-path`（即 `blend`）**不能同时用**，必须改用 `--train-data-path`/`--valid-data-path`/`--test-data-path`（`blend_per_split`）（`blended_megatron_dataset_config.py:105-109`）。并且它会断言缓存已存在（`:101-104`），所以预备阶段必须先完成。

---

## 四、NFS 场景的关键坑

| 坑 | 说明 | 对策 |
|----|------|------|
| **带宽瓶颈** | 所有 rank 同时随机读 `.bin`，很容易打满 NFS 单服务器带宽 | 按节点分片数据；或本地 NVMe staging；或换并行文件系统 |
| **mmap over NFS** | 页缓存一致性、随机读性能差 | `--no-mmap-bin-files` |
| **跨作业缓存命中** | 缓存按 `unique_description_hash` 命名，配置变了就 miss | 预构建和训练用的 `seq_length / split / weights / num_samples` 必须完全一致 |
| **写读竞态** | 单作业内 barrier 已处理；但跨节点预构建时，要确保写完且 `fsync`/关闭后再让训练读 | 预构建脚本结束后再启动训练 |
| **小文件元数据开销** | 加载多个 `.npy` 的小文件 open/stat 在 NFS 上偏慢 | 用 `--dataloader-defer-npy-index-mmap` 延迟到首次访问 |
| **不要放样本张量** | 见上文 C 类 | 保持 on-the-fly |

---

## 五、如果 NFS 成为瓶颈，更好的选择

NFS 本质是"单服务器 + 网络协议"，对大规模分布式训练的**随机并发读**并不友好。规模一大，建议升级到：

- **并行文件系统**：Lustre / GPFS(Spectrum Scale) / WekaIO / CephFS —— 为 HPC 高并发随机读设计，这是大模型训练的标配
- **本地 SSD/NVMe 缓存**：每节点先把数据 staging 到本地盘，再训练
- **对象存储 + Megatron 内置 S3 支持**：源码里有 `_S3BinReader`（`indexed_dataset.py:500`），支持按 `bin_chunk_nbytes` 分块缓存、`.idx` 下载到本地——`--object-storage-s3-config` 等参数即可启用，比裸 NFS 更适合云上

---

## 六、配置参数速查表

| 参数 | 作用 | NFS 场景建议 |
|------|------|--------------|
| `--data-path` | 指定 `.bin`/`.idx` 前缀（`blend`） | 指向 NFS 挂载点即可 |
| `--train/valid/test-data-path` | per-split 数据路径（`blend_per_split`） | 用 `fast_cache_load` 时必须用它 |
| `--data-cache-path` | `.npy` 索引缓存目录（`path_to_cache`） | **指向 NFS**，实现跨节点共享 |
| `--no-mmap-bin-files` | 关闭 `.bin` 的 mmap，改用 file reader | **NFS 上必加**，避免 mmap 一致性问题 |
| `--dataloader-fast-cache-load` | 跳过索引构建，只读已建缓存 | 预构建后训练用，要求缓存已存在 |
| `--dataloader-defer-npy-index-mmap` | 延迟 `.npy` 的 mmap 到首次访问 | 缓解 NFS 小文件元数据开销 |

---

## 七、关键源码位置索引

| 关注点 | 文件 | 行号 |
|--------|------|------|
| `path_to_cache` / `mmap_bin_files` / `fast_cache_load` 定义 | `megatron/core/datasets/blended_megatron_dataset_config.py` | 57-113 |
| `fast_cache_load` 与 `--data-path` 互斥断言 | `megatron/core/datasets/blended_megatron_dataset_config.py` | 101-109 |
| rank-0 先建 → barrier → 其它 rank 走缓存 | `megatron/core/datasets/blended_megatron_dataset_builder.py` | 523-548 |
| 索引缓存读写（`.npy`，按 hash 命名） | `megatron/core/datasets/gpt_dataset.py` | 475-665 |
| `.bin` mmap reader（默认） | `megatron/core/datasets/indexed_dataset.py` | 389-428 |
| `.bin` file reader（NFS 友好） | `megatron/core/datasets/indexed_dataset.py` | 431-497 |
| `.bin` S3 reader（云上替代） | `megatron/core/datasets/indexed_dataset.py` | 500-583 |
| CLI 参数定义 | `megatron/training/arguments.py` | 2966-2974 |

---

## 一句话总结

**可以**——把 `.bin`/`.idx` 原始数据和 `.npy` 索引缓存都放 NFS，用 `--data-cache-path` + `--no-mmap-bin-files`，甚至用 `--dataloader-fast-cache-load` 实现"预备节点算索引写 NFS、训练节点只读缓存"的解耦。但逐样本张量不要落盘；且 NFS 在高并发随机读下容易成为瓶颈，规模大时应换并行文件系统（Lustre/GPFS）或本地 NVMe 缓存。
