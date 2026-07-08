# MemCache 前缀缓存场景部署参数影响分析报告

> 分析对象：MemCache（华为昇腾开源的高性能分布式 KVCache 存储引擎，用于 LLM / GR 推理场景）
> 分析问题：当**前缀种类数为 16 / 50 / 100**、**单前缀长度为 100k tokens** 时，对 MemCache 部署参数是否有影响？

---

## 一、结论速览

**有影响，但影响面非常集中：几乎只落在「内存池容量」这一个维度（`dram.size` / `hbm.size`），并通过淘汰阈值间接牵连。**

- 前缀**种类数（16/50/100）**本身对部署参数的影响**可以忽略**——它只是一个对象计数，元数据开销极小。
- 真正决定部署参数的是 **「种类数 × 单前缀大小」这个总数据量**。
- 单前缀 100k tokens 属于**大对象**，会触发淘汰机制的"死区"约束，必须按"全部常驻"来规划容量。

---

## 二、源码依据（关键路径）

本结论基于对 MemCache 源码以下关键路径的分析：

| 关注点 | 源码位置 | 关键逻辑 |
|---|---|---|
| 淘汰触发条件 | `mmc_global_allocator.h:302` | `usedSize × 100 > totalSize × high(默认 90)`，即用量 > 90% 才淘汰（`LEVEL_BASE = 100`） |
| 淘汰数量 | `mmc_meta_container_lru.cpp` `MultiLevelElimination` | `numEvictObjs = oriNum × (当前% − low) / high`，按**对象个数比例**淘汰，非按字节精确回收 |
| 大对象死区 | `mmc_global_allocator.h` `GetNeedEvictList` else 分支 | 单个 put 值 > 容量 1%（且未达 high 水位、剩余空间不足）时，**淘汰不会被触发** |
| 容量上限 | `mmc_config_const.h` | 单 rank `MAX_DRAM_SIZE = MAX_HBM_SIZE = 1TB`；池容量 = Σ(各 rank 贡献) |
| 阈值默认 | `mmc_config_const.h` | `evict_threshold_high = 90`，`evict_threshold_low = 80` |
| rank 数 | `mmc_config_const.h` | `world_size` 默认 256，范围 [1, 1024] |
| 副本数 | `mmc_def.h` | `MAX_BLOB_COPIES = 8` |
| 数据模型 | `mmc_def.h` / layers 接口 | KV cache 以多层 block 形式存储，一个 key = 一个对象 = 一份前缀 KV |

---

## 三、受影响的部署参数

| 参数 | 是否受影响 | 说明 |
|---|---|---|
| `ock.mmc.local_service.dram.size` / `hbm.size` | **强相关（核心）** | 池容量必须 ≥ 所有常驻前缀总量。全局容量 = Σ(各 rank 贡献)。单 rank 上限 1TB。 |
| `ock.mmc.local_service.world_size` | **相关** | 容量不足时靠加 rank / 节点扩容。默认 256，上限 1024。 |
| `ock.mmc.evict_threshold_high` / `low` | **需调整** | 默认 90/80。前缀是大对象，淘汰按"对象个数比例"算，大前缀场景必须留够 headroom。 |
| `replicaNum`（≤8） | **倍数效应** | 若开多副本，容量需求 × 副本数。 |
| `ock.mmc.local_service.max.dram.size` / `max.hbm.size` | 相关 | 当各 rank 贡献大小不一致时必须显式配置。 |

---

## 四、容量估算（以 README 的 DeepSeek-R1 为参照）

README 实测口径：

> 单个 block = `61×128K + 61×16K = 8784KB ≈ 8.57MB`（61 层 × KV 两份 = 122 个离散地址）。

这是 block_size（如 128 token）下的值，**换算到每 token ≈ 67 KB**。

### 单前缀 100k tokens ≈ 6.7 GB

### 不同种类数下的常驻总量（单副本）

| 前缀种类数 | 常驻总量 |
|---|---|
| 16 | ≈ **107 GB** |
| 50 | ≈ **335 GB** |
| 100 | ≈ **670 GB** |

> 以上仅为 KV 数据本体。模型不同（层数 / 是否 MLA / fp16 vs fp8）按公式 `tokens × perTokenKV` 等比缩放即可。

---

## 五、淘汰机制带来的关键约束（最容易被忽略的坑）

源码逻辑（`mmc_global_allocator.h:302` + `mmc_meta_container_lru.cpp` 的 `MultiLevelElimination`）：

1. **触发条件**：`usedSize × 100 > totalSize × high(默认 90)`，即用量 > 90% 才淘汰。
2. **淘汰量**：`numEvictObjs = oriNum × (当前% − low) / high`，按**对象个数**等比例淘汰，而非按字节精确回收到某水位。
3. **大对象死区**（`GetNeedEvictList` else 分支 + 配置文档注）：当单个 put 值 > 容量的 1%（且未达 high 水位、剩余空间不足）时，**淘汰不会被触发**。
   - 一个 6.7 GB 的前缀想靠"按需淘汰腾位"几乎不可能，除非池容量 ≥ 670 GB。

### 部署建议

1. **按"工作集全部常驻"来配容量，别指望运行中淘汰**：
   `capacity ≥ 工作集 / low阈值 ≈ 工作集 / 0.8`（即预留 ~25% headroom）。
   - 例：100 前缀 ≈ 670 GB → 建议池容量 ≥ ~840 GB。
2. **容量跨 rank 摊**：若每节点/卡贡献 64 GB DRAM，100 前缀场景约需 13+ 个 rank 的容量；`world_size` 要覆盖。
3. **16/50/100 这个数对元数据无压力**（对象存在 `unordered_map` 里，百级 key 完全可忽略）——无需调 thread pool、meta 服务规格。

---

## 六、不受影响的参数

前缀**种类数（16/50/100）**作为对象计数，对以下均**无影响**：

- `meta_service_url`、`config_store_url`
- 线程池大小（`read/write_thread_pool.size`）
- `aggregate.num`（默认 122，正好是 DeepSeek 层数 × 2；**若换模型层结构需重看此项**）
- TLS、HA 配置
- `batch_option.chunk.size` / `chunk.count`（那是单次拷贝分片，与大对象总量无关）

---

## 七、参数配置清单（推荐）

| 前缀种类 | 工作集（单副本） | 推荐池容量下限（÷0.8） | 说明 |
|---|---|---|---|
| 16 | ~107 GB | ~134 GB | 单节点或少量 rank 即可 |
| 50 | ~335 GB | ~419 GB | 需多 rank 摊 |
| 100 | ~670 GB | ~838 GB | 必须多 rank/节点；开副本需再 ×副本数 |

**配参动作（100 前缀示例，单副本，DeepSeek-R1 口径）：**

```ini
# 每个 rank 贡献的 DRAM（按节点实际内存分配，≤1TB）
ock.mmc.local_service.dram.size = 64GB
# rank 总数需覆盖总容量（838GB / 64GB ≈ 14 → 配 16 留余量）
ock.mmc.local_service.world_size = 16
# 大对象场景建议保留默认 90/80，但务必保证工作集落在 80% 水位以下
ock.mmc.evict_threshold_high = 90
ock.mmc.evict_threshold_low = 80
```

---

## 八、一句话总结

参数调整**只跟「100k token × 种类数」算出来的总 KV 字节数有关**——把这个数 ÷0.8 当作 `dram + hbm` 总池容量下限去配 `dram.size/hbm.size` 与 `world_size`，并注意**大前缀无法触发按需淘汰**，其余配置不动。

---

*报告基于 MemCache 开源代码静态分析，容量数字以 DeepSeek-R1 实测口径估算，实际部署应按目标模型精确换算每 token KV 字节数。*
