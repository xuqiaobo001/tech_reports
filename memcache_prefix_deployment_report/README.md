# MemCache 前缀缓存场景部署参数影响分析报告

> 分析对象：MemCache（华为昇腾开源的高性能分布式 KVCache 存储引擎，用于 LLM / GR 推理场景）
> 分析问题：当**前缀种类数为 16 / 50 / 100**、**单前缀长度为 100k tokens** 时，对 MemCache 部署参数是否有影响？

---

## 修订说明（v2）

v1 版本给出了 `world_size = 16` 等 rank 数字，但**未先定义 DeepSeek-R1 的部署形态与硬件规格**，也未区分 "memcache 池 rank" 与 "模型 TP rank"，导致 rank 推算站不住脚。本版补充：

1. 明确的 **DeepSeek-R1 部署形态与硬件基线**（含权威来源）；
2. 基于**源码实证**的 `world_size` / `rank` 精确定义；
3. 用 grounded 的硬件参数**重算**池容量与 rank 数。

---

## 一、结论速览

**有影响，但影响面非常集中：几乎只落在「内存池容量」这一个维度（`dram.size` / `hbm.size`），并通过淘汰阈值间接牵连。**

- 前缀**种类数（16/50/100）**本身对部署参数的影响**可以忽略**——它只是一个对象计数，元数据开销极小。
- 真正决定部署参数的是 **「种类数 × 单前缀大小」这个总数据量**。
- 单前缀 100k tokens 属于**大对象**，会触发淘汰机制的"死区"约束，必须按"全部常驻"来规划容量。
- **memcache 的 `world_size` 是「缓存池的 rank 数（参与贡献内存的设备数）」，与模型的 TP/PP rank 不是同一回事**（虽常重合）。

---

## 二、DeepSeek-R1 部署形态与参数（grounded）

> 这是回答 "rank 信息从哪来" 的关键一节。所有数字均标注来源。

### 2.1 模型与单 token KV 字节

DeepSeek-R1（与 DeepSeek-V3 同构，671B MoE）采用 **MLA（Multi-head Latent Attention）**，每层每 token 的 KV cache 为：

| 分量 | 维度 | bf16 字节 |
|---|---|---|
| 压缩 KV（kv_lora_rank） | 512 | 1024 B |
| RoPE 部分（qk_rope_head_dim） | 64 | 128 B |
| **单层单 token 合计** | 576 | **1152 B** |

- 共 **61 层** → 单 token KV ≈ `1152 × 61 = 70,272 B ≈ 68.6 KiB`（bf16）。
- **与 README 自洽**：README 给出 DeepSeek-R1 单 block = `61×128K + 61×16K = 8784KB ≈ 8.57MB`（128 token/block），折算 `8.57MB ÷ 128 ≈ 67 KiB/token`，与上面一致 ✓。
- **量化（W8A8/fp8 KV）**约为 bf16 的一半：≈ 34.3 KiB/token。

### 2.2 单前缀（100k tokens）容量

| 精度 | 单前缀（100k token） |
|---|---|
| bf16 | ≈ **6.4 GiB**（68.6 KiB × 100k） |
| fp8 / W8A8 | ≈ **3.2 GiB** |

### 2.3 硬件基线（与 README 性能小节一致：Ascend A2）

| 项 | 规格 | 来源 |
|---|---|---|
| NPU | 昇腾 910B / 910B4，单卡 **64 GB HBM2e**（可用 ~65.5 GB） | [8卡64G-910B4 部署报告](https://blog.csdn.net/m0_57112626/article/details/161767657) |
| 节点 | Atlas 800T A2 / 800I A2，**8 NPU/节点**，节点 HBM = **512 GB**；HCCS 互联 392 GB/s | [Atlas 800T A2 技术规格](https://support.huawei.com/enterprise/zh/doc/EDDOC1100317202/f3dba488)、[昇腾 AI 服务器](https://www.hiascend.com/hardware/ai-server) |
| 节点主机内存 | 1～数 TiB DDR（视配置） | — |

### 2.4 DeepSeek-R1 **推理**部署规模（仅模型权重 + 激活，不含 prefix 池）

| 推理方式 | 最低硬件 | 说明 | 来源 |
|---|---|---|---|
| BF16 权重 | **≥ 4 台 A2（32 NPU，~2 TiB HBM）** | 权重 ~1.2 TiB | [昇腾 ModelZoo DeepSeek-V3/R1](https://www.hiascend.com/software/modelzoo/models/detail/678bdeb4e1a64c9dae51d353d84ddd15) |
| W8A8 量化 | **≥ 2 台 A2（16 NPU，~1 TiB HBM）** | 量化部署 | 同上 |
| 16 卡私有化 | 2 台 A2（16 NPU） | 硅基流动方案 | [硅基流动私有化部署](https://www.cls.cn/detail/1943967) |

> 关键事实：**HBM 几乎被模型权重占满**。因此把 prefix 池放在 HBM（`device_rdma`）上不现实——只能用**剩余的少量 HBM**。大容量 prefix 工作集应放在**主机 DRAM 池（`host_rdma`）**，这是本报告的容量规划基础。

### 2.5 MemCache 在部署中的角色

MemCache 作为**分布式 KV 池**存在，有两种典型形态：

- **A. 与推理同进程（library 模式）**：vLLM-ascend 把 memcache 作为 KV pool backend 加载，**每个推理 worker = 一个 memcache rank**（贡献其设备/主机内存）。此时 `world_size` = 推理 worker 数 = 模型 TP（×PP）rank 数。
- **B. 独立内存提供者节点（disaggregated 模式）**：专门的节点仅作"内存提供者"贡献 DRAM/HBM，推理 worker 作 client。此时 `world_size` = 池节点数 × 每节点 rank 数，**与模型 TP 无关**。

README 对此的描述：LocalService 既是"客户端（被应用加载）"，也是"内存提供者（贡献一段连续内存）"。

---

## 三、`world_size` / `rank` 的精确定义（源码实证）

| 概念 | 定义 | 源码依据 |
|---|---|---|
| `rank`（bmRankId） | 一个 LocalService 实例 = 一个 rank，绑定一个 `deviceId`，对外暴露其**本地** HBM 与 DRAM 空间 | `mmc_bm_proxy.cpp`：`bmRankId_ = SmemBmGetRankId()`；`spaces_[MEDIA_HBM/DRAM] = SmemBmGetLocalMemSizeByMemType(...)` |
| `world_size` | **池中 rank 总数**（BM group 成员数）= 参与贡献内存的设备数 | `mmc_local_service_default.cpp`：`memberSize = options_.worldSize`；`mmc_config_const.h`：默认 256，范围 [1, 1024] |
| 池总容量 | Σ(各 rank 贡献的 `localDRAMSize` + `localHBMSize`) | `mmc_global_allocator.h`：`GetUsedInfo` 跨所有 rank/allocator 求和 |

**池容量公式（均匀配置）：**

```
pool_capacity = world_size × (per_rank dram.size + per_rank hbm.size)
```

> ⚠️ `world_size` 是**缓存池 rank 数**，不要和 DeepSeek-R1 的 TP/PP rank 直接画等号（仅 library 模式下二者重合）。

---

## 四、容量估算（按 2.1/2.2 的 grounded 数字）

### 4.1 全量前缀工作集（单副本）

| 前缀种类数 | bf16 工作集 | fp8/W8A8 工作集 |
|---|---|---|
| 16 | ≈ **102 GiB** | ≈ 51 GiB |
| 50 | ≈ **320 GiB** | ≈ 160 GiB |
| 100 | ≈ **640 GiB** | ≈ 320 GiB |

### 4.2 含淘汰 headroom 的池容量下限

源码：淘汰在用量 > `high(默认 90%)` 触发，回收到 `low(默认 80%)`。大对象无法按需淘汰（见 §六），故工作集应落在 **low 水位以下**：

```
pool_capacity ≥ 工作集 ÷ 0.8
```

| 前缀种类数 | bf16 池容量下限（÷0.8） | fp8 池容量下限 |
|---|---|---|
| 16 | ≈ **128 GiB** | ≈ 64 GiB |
| 50 | ≈ **400 GiB** | ≈ 200 GiB |
| 100 | ≈ **800 GiB** | ≈ 400 GiB |

> 多副本需再 × `replicaNum`（`MAX_BLOB_COPIES = 8`）。

---

## 五、Cache 池规模推算（重算 rank，替代 v1 的臆测）

以 §2.4 结论为前提——**prefix 池放主机 DRAM（`host_rdma`）**。每 rank 的 `dram.size` 是可配置旋钮；下面给出两种常见 per-rank 贡献下的所需 rank 数：

> 假设：A2 节点主机内存 ~1 TiB，每节点跑 8 rank（一卡一 rank）。故 **8 rank/节点，约 1 TiB DRAM/节点**。

| 前缀种类数 | bf16 池容量下限 | 每节点 ~1 TiB DRAM → **所需节点** | `world_size`（8 rank/节点） |
|---|---|---|---|
| 16 | ~128 GiB | **< 1 节点** | 8（1 节点内即可，甚至 2 rank 足够） |
| 50 | ~400 GiB | **1 节点** | 8 |
| 100 | ~800 GiB | **1 节点**（接近满，建议 2 节点冗余） | 8～16 |

对比 v1（写死的 `world_size=16`、`dram.size=64GB`）：在主机 DRAM 池形态下，**16～100 个 100k 前缀其实只需 1～2 个 A2 节点的 DRAM**即可承载，远没有 v1 暗示的"十几个 rank"那么夸张——因为单前缀虽然 6.4 GiB 不小，但 host DRAM 单节点就有 1 TiB 量级。

**HBM 池（`device_rdma`）替代估算**（仅供对比，不推荐做全量 prefix 池）：假设每 NPU 能挤出 ~10 GiB 残余 HBM 给池，则需 ~64 个 NPU 才能凑到 640 GiB——即把 prefix 池摊到整个 4 节点推理集群的残余 HBM 上，捉襟见肘，故不推荐。

---

## 六、淘汰机制带来的关键约束（最容易被忽略的坑）

源码（`mmc_global_allocator.h:302` + `mmc_meta_container_lru.cpp` 的 `MultiLevelElimination`）：

1. **触发条件**：`usedSize × 100 > totalSize × high(默认 90)`，即用量 > 90% 才淘汰。
2. **淘汰量**：`numEvictObjs = oriNum × (当前% − low) / high`，按**对象个数**等比例淘汰，而非按字节精确回收。
3. **大对象死区**（`GetNeedEvictList` else 分支 + 配置文档注）：单个 put 值 > 容量的 1%（且未达 high 水位、剩余空间不足）时，**淘汰不会被触发**。6.4 GiB 的前缀想靠"按需淘汰腾位"几乎不可能。

**部署建议**：按"工作集全部常驻"配容量（§4.2），别指望运行中淘汰。

---

## 七、参数配置清单（修订，含明确拓扑）

**示例场景**：100 个 100k-token 前缀，bf16，单副本，prefix 池用主机 DRAM（`host_rdma`），部署在 1 个专用 A2 内存提供者节点（8 rank）。

```ini
# —— 协议与池形态：主机 DRAM 池（大前缀推荐）——
ock.mmc.local_service.protocol = host_rdma

# —— 每 rank 贡献的主机 DRAM（8 rank × ~100GiB ≈ 800GiB，覆盖 800GiB 下限）——
ock.mmc.local_service.dram.size = 100GB
ock.mmc.local_service.hbm.size = 0

# —— 池 rank 数 = world_size；这里 1 节点 8 卡 → 8 rank ——
ock.mmc.local_service.world_size = 8

# —— 淘汰水位保留默认，但务必让工作集落在 low(80%) 以下 ——
ock.mmc.evict_threshold_high = 90
ock.mmc.evict_threshold_low = 80

# —— 若各 rank 贡献不均，需显式配 max ——
ock.mmc.local_service.max.dram.size = 100GB
```

| 参数 | 受 prefix 场景影响？ | 取值依据 |
|---|---|---|
| `dram.size` / `hbm.size` | **强相关（核心）** | 工作集 ÷ 0.8 ÷ world_size；大前缀走 DRAM |
| `world_size` | **相关** | 池 rank 数 = 所需容量 ÷ 每 rank 贡献 |
| `evict_threshold_high/low` | 需复核 | 默认 90/80；保证工作集 < low |
| `replicaNum` | 倍数 | 容量 × 副本数 |
| `max.dram.size`/`max.hbm.size` | 相关 | 各 rank 贡献不均时必配 |

---

## 八、不受影响的参数

前缀**种类数（16/50/100）**作为对象计数，对以下均**无影响**：

- `meta_service_url`、`config_store_url`
- 线程池大小（`read/write_thread_pool.size`）
- `aggregate.num`（默认 122 = DeepSeek 层数 ×2；**若换模型层结构需重看此项**）
- TLS、HA 配置
- `batch_option.chunk.size` / `chunk.count`（单次拷贝分片，与总量无关）

---

## 九、一句话总结

参数调整**只跟「100k token × 种类数」算出来的总 KV 字节数有关**：DeepSeek-R1 下单前缀 ≈ 6.4 GiB（bf16），16/50/100 前缀工作集约 102/320/640 GiB；按 ÷0.8 配 **主机 DRAM 池**（`host_rdma`），1～2 个 A2 节点（8～16 rank，`world_size`）即可承载；`world_size` 是**缓存池 rank 数**，不是模型 TP rank。大前缀无法触发按需淘汰，务必按全常驻规划容量。

---

## 参考资料

- [昇腾 ModelZoo：DeepSeek-V3/R1 部署（BF16 ≥4 台 8×64G，W8A8 ≥2 台）](https://www.hiascend.com/software/modelzoo/models/detail/678bdeb4e1a64c9dae51d353d84ddd15)
- [Atlas 800T A2 训练服务器技术规格（8×910B，HCCS 392GB/s）](https://support.huawei.com/enterprise/zh/doc/EDDOC1100317202/f3dba488)
- [昇腾 AI 服务器产品规格](https://www.hiascend.com/hardware/ai-server)
- [8卡64G-910B4 部署测试报告（单卡 64GB HBM2e）](https://blog.csdn.net/m0_57112626/article/details/161767657)
- [硅基流动：16 卡昇腾私有化部署 DeepSeek-R1/V3](https://www.cls.cn/detail/1943967)
- [华为昇腾 DeepSeek V3/R1 推理部署最佳实践](https://zhuanlan.zhihu.com/p/1910103648358368349)
- MemCache 源码：`mmc_global_allocator.h`、`mmc_meta_container_lru.cpp`、`mmc_local_service_default.cpp`、`mmc_bm_proxy.cpp`、`mmc_config_const.h`、`mmc_def.h`；README 性能小节（DeepSeek-R1 block = 8.57MB/128token）

*报告基于 MemCache 开源代码静态分析 + DeepSeek-R1/昇腾 A2 公开规格。容量数字以 DeepSeek-R1（61 层 MLA，bf16）为口径；实际部署应按目标模型精度与节点内存规格精确换算。*
