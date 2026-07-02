# Mooncake:Prefill × Decode 全阶段 KVCache 卸载机制与配置指南

> 基于 Mooncake 源码与 `docs/source/` 设计/部署文档梳理,合并 decode 与 prefill 两阶段分析。所有代码引用均已核对(`master.cpp`、`real_client_main.cpp`、`file_storage.cpp`、`storage_backend.cpp`、`allocator.h`、`replica.h`、`file_interface.h`)。

---

## 1. 背景与定位

**Mooncake 本身不执行 prefill/decode 计算**,它是**以 KVCache 为中心的分布式存储/传输层**。所谓"把 KVCache 卸载到内存、磁盘",在 Mooncake 体系里沿**两条管道**进行:

1. **分层缓存管道(HiCache,L1/L2/L3)** —— 对应"卸载到内存"。
2. **存储分层管道(DRAM ↔ SSD)** —— 对应"卸载到磁盘"。

Prefill 与 decode 两个阶段**都有** KVCache 搬运,但模式不同:prefill 以**读**(prefetch 复用前缀)和**跨节点迁移**(PD 分离)为主,decode 以**增量写**为主。关键在于区分"应用侧的 phase 行为"与"Mooncake Store 本身的机制"。

---

## 2. 三层缓存模型:HiCache(L1 / L2 / L3)

`docs/source/design/hicache-design.md` 的总框架:

| 层级 | 媒介 | 归属 | 角色 |
|------|------|------|------|
| **L1** | GPU 显存 | 单实例私有 | 最快、最小,计算直接访问 |
| **L2** | 主机内存 DRAM | 单实例私有 | 本机内存扩展 |
| **L3** | 分布式存储(**Mooncake 集群**) | 全集群共享 | 全局 KV cache 池 |

HiCache 工作流三步(`hicache-design.md:23`):**local match → prefetch from L3 → write-back**。前两步主要发生在 prefill,write-back 在 prefill 完成后及 decode 期间持续进行。

三种写回策略(`:73-77`):

- `write_through`:每次访问立即写下一级。
- `write_through_selective`:仅热度超过阈值才写(只备份热数据)。
- `write_back`:仅在上层驱逐时才写(适合容量受限场景)。

---

## 3. Mooncake Store 内部存储分层(DRAM ↔ SSD)

进入 Mooncake 集群内部,L3 本身又被分层为**分布式内存**和**本地 SSD**,见 `docs/source/design/ssd-offload.md`。用 `ReplicaType` 区分媒介(`mooncake-store/include/allocator.h:21`):

```cpp
enum class ReplicaType {
    MEMORY,      // 分布式内存(DRAM)
    DISK,        // 主节点侧的磁盘记录
    LOCAL_DISK,  // 本地 SSD(NVMe)
    NOF_SSD,     // NVMe-oF SSD
    ALL,
};
```

`Replica` 用 `std::variant` 承载媒介相关数据(`mooncake-store/include/replica.h`):`MemoryReplicaData`(DRAM)/ `LocalDiskReplicaData`(带 RPC `transport_endpoint`)/ `NoFReplicaData`(NVMe-oF)。

> 注意:Transfer Engine 的 `memory_location.h` 是**另一个概念**——追踪 buffer 页的 NUMA/CPU 放置,而非内存/磁盘之分。磁盘数据必须先读入已注册的内存暂存区(`ClientBuffer`),Transfer Engine 才能搬运它。

### 3.1 卸载:内存 → SSD(心跳驱动,对应用透明)

由 **FileStorage 心跳线程**后台完成(`mooncake-store/src/file_storage.cpp`):

1. **心跳**:每隔 `MOONCAKE_OFFLOAD_HEARTBEAT_INTERVAL_SECONDS` 调 `OffloadObjectHeartbeat`,Master 返回需驱逐的 `{key→size}`。
2. **选对象**(`master_service.cpp:5025` 的 `try_evict_or_offload`):内存水位超 `eviction_high_watermark_ratio` 时,按 near-LRU 挑选;若 `offload_on_evict_` 开启且尚无 `LOCAL_DISK` 副本,则 `PushOffloadingQueue` 钉住排队落盘。
3. **落盘**:`StorageBackend::BatchOffload` 经 `PosixFile`/`UringFile`(io_uring)写本地 SSD。
4. **登记**:`NotifyOffloadSuccess` 让 Master 加一条 `LOCAL_DISK` 副本(含持有者 RPC 地址)。

> 已在 SSD 的对象再被驱逐时,内存**立即释放**,不重复落盘。

### 3.2 加载:SSD → 内存(零拷贝)

1. 请求方 `BatchGet(keys)` → Master 返回 `LOCAL_DISK` 副本(含 `transport_endpoint`)。
2. 请求方 RPC `batch_get_offload_object`,持有方 `BatchLoad` 把 SSD 数据读进**预注册、O_DIRECT 对齐**的 `ClientBuffer`。
3. **Transfer Engine**(RDMA/TCP)零拷贝拉进应用 DRAM/VRAM(`real_client.cpp:5613`)。

### 3.3 回升:SSD → DRAM

Master 用频率草图(`promotion_sketch_`)决定哪些 SSD 数据读命中后值得晋升回内存,经 `ProcessPromotionTasks` 拷回 DRAM(`master_service.cpp` 的 `TryPushPromotionQueue`/`PromotionObjectHeartbeat`)。

### 3.4 两级驱逐策略

- **内存层**(`master_service.cpp` BatchEvict):near-LRU,`soft_pin`/`hard_pin` 保护热对象。
- **SSD 层**(`storage_backend.h:179`):`BucketEvictionPolicy { NONE, FIFO, LRU }`,受 `MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE` 限制。两阶段:先移除桶元数据并通知 Master(`BatchEvictDiskReplica`),再排空在途读,最后删文件。

---

## 4. Prefill 阶段:读密集 + 跨节点迁移(主战场)

prefill 是 KVCache 搬运最密集、收益最大的环节。

| 操作 | 方向 | 说明 |
|------|------|------|
| **Prefetch(读)** L3→L2 | 拉 | 跳过前缀重算;RDMA 从多远端节点并行读(`hicache-design.md:47`) |
| **Write-back(写)** L1→L2→L3 | 推 | prefill 完成后存回新算出的前缀 KV(`:83`),MLA 模型只让一个 rank 写回(`:114`) |
| **KV 迁移** prefill→decode | 推 | PD 分离下经 TransferEngine 把完整前缀 KV 交给 decode 节点 |

### 4.1 Prefetch 三种终止策略 + 动态超时(`:49-67`)

- `best_effort`:GPU 能算就立刻终止,零等待。
- `wait_complete`:必须等所有 prefetch 完成。
- `timeout`(实战最常用):超时或完成即止。`timeout = prefetch_timeout_base + prefetch_timeout_per_ki_token * num_token_to_fetch / 1024`。

### 4.2 计算-传输重叠

prefill 期间 CPU→GPU 搬 KV 时,N+1 层加载与 N 层计算重叠(`:111`),prefill 专属优化。

### 4.3 PD 分离(XpYd)

多 prefill + 多 decode,prefill 节点算完整个前缀后把**完整 KV 经 TransferEngine 迁移到 decode 节点**(`docs/source/design/transfer-engine/efa_transport.md`、`benchmarks/xypd_benchmarks/proxy_demo.py`)。要点:

- `--disaggregation-mode prefill` / `decode`,经 router 前置调度。
- prefill/decode host 必须用**对外可达 IP**,不能用 `127.0.0.1`(否则 KV 握手 `Connection refused`)。

文档 `:120` 明确:

> In the PD-disaggregation deployment mode, HiCache can be enabled on the **Prefill nodes** to optimize prefill performance... HiCache can also be enabled on the decode nodes to write computation results back to L3.

---

## 5. Decode 阶段:增量写为主

decode 每步增量产出小段 KV,经 `write_backup_storage` → `backup_queue` → `backup_thread_func` 异步写回 Mooncake(L3)。HiCache 在 decode 节点可开启,把计算结果写回 L3(`:120`),供后续请求/跨实例复用,避免重算。

> Decode 是"写密集",但每次写量小;prefill 是"读密集+大块迁移",单次体量大。

---

## 6. Prefill × Decode 对照

| 维度 | Prefill | Decode |
|------|---------|--------|
| 主导操作 | **读**(prefetch)+ **迁移**(PD) | **写**(逐 token) |
| 数据量 | 大(整段前缀) | 小(逐 token 增量) |
| HiCache 角色 | 前缀 KV 消费者 + 生产者 | 计算结果写回 L3 |
| 关键优化 | 终止策略、计算-传输重叠、MLA 单 rank 写回 | 异步 write-back 队列 |
| 是否经 Store SSD | 主要经 L3 分布式内存 + 迁移 | 写回 L3 后同走 SSD 卸载管道 |

---

## 7. 关键区分:Store 本身不区分 phase

最容易误解的点——**Mooncake Store 的 DRAM↔SSD 卸载管道是 phase-agnostic 的**:

- Store 只看到 `Put`/`Get` 的 KV 对象,**不知道也不关心**它来自 prefill 还是 decode。
- 内存高水位触发的 `offload-on-evict`、SSD 命中后的 promotion、驱逐策略——全部按**对象的访问热度/lease**决策,而非 phase。
- prefill 前缀 KV 与 decode 增量 KV,进 Store 后**走同一条 SSD 卸载路径**。差异纯粹由应用侧驱动(谁调 Put/Get、什么时机、什么粒度)。

### 全景图

```
        Prefill 节点                         Decode 节点
   ┌──────────────────┐                ┌──────────────────┐
   │ prefill 计算       │                │ decode 计算       │
   │  │ prefetch ◀─────┼────────────────┼─── (读 L3 前缀)   │
   │  ▼                 │  PD 迁移 KV    │  ▲                │
   │ write-back(写)──┼───────────────▶──┼──┘ write-back    │
   └────────┬─────────┘                └────────┬─────────┘
            │ Put                                  │ Put
            ▼                                      ▼
        ┌────────── Mooncake Store(L3,phase-agnostic)─────────┐
        │  分布式内存(DRAM)── offload-on-evict ──▶ 本地 SSD    │
        │       ▲──── promotion(命中回升)────▲                 │
        └──── Transfer Engine:RDMA/TCP 零拷贝 ─────────────────┘
```

---

## 8. 如何开启 SSD 卸载(Store 层配置)

### 8.1 两层开关缺一不可

1. **应用层**:SGLang 开启 HiCache 把 Mooncake 配为 L3 后端;或 vLLM 用 KV connector。写回策略 `write_back`/`write_through`。
2. **Store 层**:开启 Mooncake Store 的内存→SSD 卸载子系统(下面)。

### 8.2 步骤

**① 建 SSD 目录**(必须存在、绝对路径,不含 `..`/软链):

```bash
mkdir -p /nvme/mooncake_offload
```

**② 启动 Master**(`--enable_offload=true` 必带):

```bash
mooncake_master \
    --rpc_port=50051 \
    --enable_offload=true \
    --offload_on_evict=true \      # 可选:内存驱逐时才落盘(否则默认 PutEnd 时落)
    --promotion_on_hit=true        # 可选:SSD 命中后晋升回内存
```

**③ 启动 Real Client**:

```bash
export MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=/nvme/mooncake_offload
export MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR=bucket_storage_backend   # 推荐
export MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE=$((200 * 1024 * 1024 * 1024))  # 200 GB
export MOONCAKE_OFFLOAD_BUCKET_EVICTION_POLICY=lru          # none/fifo/lru
export MOONCAKE_OFFLOAD_USE_URING=true                      # 可选,io_uring

mooncake_client \
    --master_server_address="192.168.1.10:50051" \
    --host="192.168.1.10" \
    --device_names="eth0" \
    --protocol="rdma" \
    --port=50052 \
    --global_segment_size="4GB" \
    --enable_offload="true"
```

**④ 应用侧 Python SDK**:

```python
from mooncake.store import MooncakeDistributedStore
store = MooncakeDistributedStore()

# Mode A:嵌入式 Real Client(同进程)
store.setup(
    local_hostname="192.168.1.10",
    metadata_server="P2PHANDSHAKE",
    global_segment_size=4 * 1024**3,
    local_buffer_size=512 * 1024**2,
    protocol="rdma", rdma_devices="eth0",
    master_server_addr="192.168.1.10:50051",
    enable_ssd_offload=True,
    ssd_offload_path="/nvme/mooncake_offload",
)

# Mode B:独立 mooncake_client 进程,应用侧用 DummyClient 连它
store.setup_dummy(
    mem_pool_size=4 * 1024**3,
    local_buffer_size=512 * 1024**2,
    server_address="192.168.1.10:50052",
)
```

### 8.3 核心 SSD 配置速查

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `MOONCAKE_OFFLOAD_FILE_STORAGE_PATH` | `/data/file_storage` | SSD 存储目录(必须存在、绝对路径) |
| `MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR` | `bucket_storage_backend` | 后端:`bucket`/`file_per_key`/`offset_allocator` |
| `MOONCAKE_OFFLOAD_TOTAL_SIZE_LIMIT_BYTES` | 2 TB | 磁盘用量上限 |
| `MOONCAKE_OFFLOAD_HEARTBEAT_INTERVAL_SECONDS` | 10 | 心跳间隔(驱动落盘) |
| `MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES` | 1.25 GB | 读回暂存区 |
| `MOONCAKE_OFFLOAD_USE_URING` | `false` | io_uring 异步 I/O |
| `MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE` | `0`(=物理盘 90%) | Bucket 后端驱逐阈值 |
| `MOONCAKE_OFFLOAD_BUCKET_EVICTION_POLICY` | `none` | `none`/`fifo`/`lru` |

> 三种后端取舍:`bucket`(推荐,打包、FIFO/LRU、可断点恢复)/ `file_per_key`(易调试、小文件多)/ `offset_allocator`(高并发小对象,**重启不恢复**)。

### 8.4 验证 & 常见坑

- **是否落盘**:`/nvme/mooncake_offload` 下应出现 `*.bucket`/`*.meta`。
- **没触发?** 多半 `--global_segment_size` 相对写入数据太大,内存没到高水位——调小内存池(如 4GB)。
- **两端都带 `--enable_offload=true`**(Master 和 Real Client 同名 gflag)。
- **io_uring 报 `Failed to register buffer`**:`MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES` 超 `RLIMIT_MEMLOCK`。`ulimit -l unlimited` 或调小暂存区。
- **多节点**:每台各起 `mooncake_client`,`--host` 填**对外可达 IP**(不要 `127.0.0.1`),都指向同一 Master。

---

## 9. 框架侧术语映射

| 框架 | 读(prefill 为主) | 写(decode/产出) |
|------|----------------|----------------|
| **vLLM KV connector** | `AsyncKVLoader` | `AsyncKVWriter` |
| **SGLang HiCache** | `prefetch_thread_func`/`prefetch_io_aux_func` | `write_backup`(L1→L2)/ `write_backup_storage`(L2→L3) |

两阶段都可开 HiCache:prefill 偏读 + 迁移,decode 偏写。

---

## 10. 一句话总结

> Prefill 以**读**(prefetch 复用前缀)和**跨节点迁移**(PD 分离)为主,decode 以**增量写**为主;但这些差异**只存在于应用层**(SGLang/vLLM)。**Mooncake Store 的内存↔SSD 卸载管道本身不区分 phase**——任何 KV 对象都按热度走同一条 `ReplicaType` 分级、心跳落盘、LRU/FIFO 驱逐、命中晋升的路径,全程零拷贝 RDMA。

---

## 11. 关键源码 / 文档索引

| 关注点 | 位置 |
|---|---|
| 副本类型枚举 | `mooncake-store/include/allocator.h:21` |
| 副本 variant 数据模型 | `mooncake-store/include/replica.h:163,205` |
| 磁盘 I/O 类(PosixFile/UringFile) | `mooncake-store/include/file_interface.h` |
| 存储后端 + Bucket 驱逐策略 | `mooncake-store/include/storage_backend.h:179` |
| FileStorage 卸载/加载/晋升编排 | `mooncake-store/include/file_storage.h` |
| Master 卸载/晋升 API + 驱逐配置 | `mooncake-store/include/master_service.h` |
| Master offload-on-evict + near-LRU 逻辑 | `mooncake-store/src/master_service.cpp:5025,3244,3100,3197,3298,3331` |
| Master gflag 定义 | `mooncake-store/src/master.cpp:75-135` |
| Real Client gflag 定义 | `mooncake-store/src/real_client_main.cpp:21` |
| SSD 卸载 env 读取 | `mooncake-store/src/file_storage.cpp:45-90` |
| Bucket 后端 env 读取 | `mooncake-store/src/storage_backend.cpp:51-75` |
| 内存池 LRU/FIFO 驱逐策略 | `mooncake-store/include/eviction_strategy.h` |
| HiCache 工作流(prefetch/write-back) | `docs/source/design/hicache-design.md:23,37,69` |
| Prefetch 并行读 + 终止策略 + 动态超时 | `docs/source/design/hicache-design.md:47,49-67` |
| Write-back 异步队列 + MLA 优化 | `docs/source/design/hicache-design.md:79-85,114` |
| Prefill 计算-传输重叠 | `docs/source/design/hicache-design.md:111` |
| PD 分离角色 | `docs/source/design/hicache-design.md:116-120` |
| PD 分离部署(EFA) | `docs/source/design/transfer-engine/efa_transport.md:527-648` |
| XpYd 分离 prefilling demo | `benchmarks/xypd_benchmarks/proxy_demo.py` |
| SSD 卸载设计 | `docs/source/design/ssd-offload.md` |
| SSD 卸载部署指南 | `docs/source/deployment/ssd-offload.md` |
| NVMe-oF SSD 部署 | `docs/source/deployment/nvmf-ssd-deployment-guide.md` |
