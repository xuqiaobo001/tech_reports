# Mooncake:Decode 阶段 KVCache 卸载到内存/SSD 机制与配置指南

> 基于 Mooncake 源码与 `docs/source/` 设计/部署文档梳理。所有代码引用均已核对(`master.cpp`、`real_client_main.cpp`、`file_storage.cpp`、`storage_backend.cpp`、`allocator.h`、`replica.h`、`file_interface.h`)。

---

## 1. 背景与定位

**Mooncake 本身不执行 decode 计算**,它是 **以 KVCache 为中心的分布式存储/传输层**。所谓"把 decode 卸载到内存、磁盘",在 Mooncake 体系里指的是——把 decode 阶段不断产生的 KV cache,沿两条管道进行保存、迁移和驱逐:

1. **分层缓存管道(HiCache,L1/L2/L3)** —— 对应"卸载到内存"。
2. **存储分层管道(DRAM ↔ SSD)** —— 对应"卸载到磁盘"。

---

## 2. 三层缓存模型:HiCache(L1 / L2 / L3)

这是理解"卸载到内存"的总框架,见 `docs/source/design/hicache-design.md`。

| 层级 | 媒介 | 归属 | 角色 |
|------|------|------|------|
| **L1** | GPU 显存 | 单实例私有 | 最快、最小,计算直接访问 |
| **L2** | 主机内存 DRAM | 单实例私有 | 本机内存扩展 |
| **L3** | 分布式存储(**Mooncake 集群**) | 全集群共享 | 全局 KV cache 池 |

**关键点:decode 节点上的 HiCache 写回。** 文档第 120 行明确写道:

> HiCache can also be enabled on the decode nodes to **write computation results back to L3**.

decode 每步产生的 KV cache,通过 `write_backup_storage` 异步写回 Mooncake(L3),后续请求(跨实例、跨节点)就能命中复用,而不必重算。这就是"decode 卸载到内存"在应用侧的实现。

三种写回策略(`hicache-design.md:73-77`):

- `write_through`:每次访问立即写下一级(带宽充足时缓存收益最强)。
- `write_through_selective`:仅热度超过阈值才写(只备份热数据,降低 I/O)。
- `write_back`:仅在上层驱逐时才写(适合容量受限、需最大化内存利用率的场景)。

数据传输优化:零拷贝 RDMA(`hicache-design.md:94-99`)、按 page 组织的 `page first direct` 布局、prefetch 的 `best_effort`/`wait_complete`/`timeout` 三种终止策略。

---

## 3. Mooncake Store 内部的存储分层(DRAM ↔ SSD)

进入 Mooncake 集群内部,L3 本身又被分层为**分布式内存**和**本地 SSD**,这是 `docs/source/design/ssd-offload.md` 的核心。用 `ReplicaType` 区分媒介(`mooncake-store/include/allocator.h:21`):

```cpp
enum class ReplicaType {
    MEMORY,      // 分布式内存(DRAM)
    DISK,        // 主节点侧的磁盘记录
    LOCAL_DISK,  // 本地 SSD(NVMe)
    NOF_SSD,     // NVMe-oF SSD
    ALL,
};
```

每个对象可同时持有多种副本。`Replica` 用 `std::variant` 承载媒介相关数据(`mooncake-store/include/replica.h`):
`MemoryReplicaData`(DRAM)/ `LocalDiskReplicaData`(带 RPC `transport_endpoint`)/ `NoFReplicaData`(NVMe-oF)。

> 注意:Transfer Engine 的 `memory_location.h` 是**另一个概念**——它追踪 buffer 页的 NUMA/CPU 放置,而非内存/磁盘之分。磁盘 vs 内存在 Store 里用 `ReplicaType` 表达。磁盘数据必须先读入一个已注册的内存暂存区(`ClientBuffer`),Transfer Engine 才能搬运它。

### 3.1 卸载路径:内存 → SSD(心跳驱动,对应用透明)

由 **FileStorage 的心跳线程**在后台完成(`mooncake-store/src/file_storage.cpp`):

1. **心跳**:每隔 `MOONCAKE_OFFLOAD_HEARTBEAT_INTERVAL_SECONDS` 调 `OffloadObjectHeartbeat`,Master 返回需驱逐的 `{key→size}` 列表。
2. **选对象**(`master_service.cpp:5025` 的 `try_evict_or_offload`):当内存水位超过 `eviction_high_watermark_ratio` 时,按 near-LRU(优先 `lease_timeout` 小的)挑选;若 `offload_on_evict_` 开启且该对象尚无 `LOCAL_DISK` 副本,则 `PushOffloadingQueue` 把一个 MEMORY 副本**钉住排队**等待落盘,而非直接丢弃。
3. **落盘**:`StorageBackend::BatchOffload` 经 `PosixFile`/`UringFile`(io_uring,`file_interface.h`)写到本地 SSD。
4. **登记**:`NotifyOffloadSuccess` 让 Master 给该对象加一条 `LOCAL_DISK` 副本(内含持有者 RPC 地址)。

> 已在 SSD 上的对象再被驱逐时,内存**立即释放**,不会重复落盘。

### 3.2 加载路径:SSD → 内存(零拷贝)

1. 请求方 `BatchGet(keys)` → Master 返回 `LOCAL_DISK` 副本描述(含 `transport_endpoint`)。
2. 请求方 RPC `batch_get_offload_object` 到持有方;持有方 `BatchLoad` 把 SSD 数据读进**预注册、O_DIRECT 对齐**的 `ClientBuffer` 暂存区。
3. **Transfer Engine**(RDMA/TCP)把数据从 `ClientBuffer` **零拷贝**拉进应用 DRAM/VRAM(`real_client.cpp:5613`)。

### 3.3 回升:SSD → DRAM(L2→L1 类比)

Master 用频率草图(`promotion_sketch_`)决定哪些 SSD 数据读命中后值得"晋升"回内存,经 `ProcessPromotionTasks` 拷回 DRAM,即 `master_service.cpp` 中的 `TryPushPromotionQueue` / `PromotionObjectHeartbeat`。

### 3.4 两级驱逐策略

- **内存层**(`master_service.cpp` BatchEvict):near-LRU,先非软钉、后(可选)软钉对象;`soft_pin`/`hard_pin` 保护热对象。
- **SSD 层**(`storage_backend.h:179`):`BucketEvictionPolicy { NONE, FIFO, LRU }`,受 `MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE` 限制;LRU 按 `last_access_ns_` 驱逐,FIFO 按单调桶 ID(时间戳)驱逐。两阶段驱逐:先从元数据移除桶并通知 Master(`BatchEvictDiskReplica`),再排空在途读(`BucketReadGuard`),最后删文件。

### 3.5 全景图

```
Decode 节点
  │ GPU(L1)  ──write_back──▶  主机内存(L2)
  │                                │ write_backup_storage(RDMA 零拷贝)
  ▼                                ▼
                    ┌──────── Mooncake 集群(L3)────────┐
                    │  分布式内存(DRAM)  ──offload-on-evict──▶  本地 SSD(NVMe)
                    │       ▲──── promotion(命中回升)────▲
                    └──── Transfer Engine:RDMA/TCP 零拷贝 ────┘
```

**一句话总结**:decode 阶段的 KV cache 卸载分两段——**应用侧**靠 HiCache 把 decode 结果异步写回 Mooncake(L3);**Mooncake 内部**靠 `ReplicaType` + 心跳驱动的 `FileStorage` 把内存副本自动落到本地 SSD(并支持 LRU/FIFO 驱逐、命中后晋升回升),全程对应用透明、热路径走零拷贝 RDMA。

---

## 4. 如何开启 Decode 卸载到 SSD

### 4.1 先理清:两层开关缺一不可

1. **应用层**:decode 引擎(SGLang/vLLM)必须把 decode 产生的 KV cache **写进 Mooncake Store**,否则 Store 里没有数据,SSD 卸载无从谈起。
   - SGLang:开启 **HiCache** 并把 Mooncake 配成 L3 后端(`hicache-design.md:120` 明确 decode 节点可开启写回),或用 **PD 分离** 模式经 TransferEngine 传 KV。
   - 写回策略选 `write_back`(驱逐时写)或 `write_through`(立即写)。
2. **Store 层**:开启 Mooncake Store 自己的 **内存→SSD 卸载子系统**。下面都是这一层的配置。

### 4.2 步骤 1:建好 SSD 目录

必须是已存在的绝对路径,不能含 `..` 或软链:

```bash
mkdir -p /nvme/mooncake_offload
```

### 4.3 步骤 2:启动 Master(关键 flag:`--enable_offload=true`)

```bash
mooncake_master \
    --rpc_port=50051 \
    --enable_offload=true
```

若想要"内存满了才落盘 + 命中后回升"的分级缓存语义,再补两个 flag(默认都是 `false`,见 `master.cpp:127/131`):

```bash
mooncake_master \
    --rpc_port=50051 \
    --enable_offload=true \
    --offload_on_evict=true \      # 内存驱逐时才落盘(否则默认在 PutEnd 时就落)
    --promotion_on_hit=true        # SSD 数据读命中后晋升回内存
```

> `--offload_on_evict=false`(默认)时,对象在 `PutEnd`(写完)就被推进落盘队列;`=true` 则推迟到内存高水位驱逐时。两者都能让数据上 SSD,区别在于**内存占用时机**。

### 4.4 步骤 3:启动 Real Client(`--enable_offload=true` + SSD 环境变量)

```bash
export MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=/nvme/mooncake_offload
export MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR=bucket_storage_backend   # 推荐
export MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE=$((200 * 1024 * 1024 * 1024))  # 200 GB 配额
export MOONCAKE_OFFLOAD_BUCKET_EVICTION_POLICY=lru          # none/fifo/lru
export MOONCAKE_OFFLOAD_USE_URING=true                      # 可选,开 io_uring 提升吞吐

mooncake_client \
    --master_server_address="192.168.1.10:50051" \
    --host="192.168.1.10" \
    --device_names="eth0" \
    --protocol="rdma" \
    --port=50052 \
    --global_segment_size="4GB" \
    --enable_offload="true"
```

应用侧用 Python SDK 连上(两种部署模式):

```python
# Mode A:嵌入式 Real Client(同进程)
from mooncake.store import MooncakeDistributedStore
store = MooncakeDistributedStore()
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
    server_address="192.168.1.10:50052",   # 上一步 mooncake_client 的 RPC 地址
)
```

### 4.5 核心 SSD 配置速查

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `MOONCAKE_OFFLOAD_FILE_STORAGE_PATH` | `/data/file_storage` | SSD 存储目录(必须存在、绝对路径) |
| `MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR` | `bucket_storage_backend` | 后端:`bucket` / `file_per_key` / `offset_allocator` |
| `MOONCAKE_OFFLOAD_TOTAL_SIZE_LIMIT_BYTES` | 2 TB | 磁盘用量上限 |
| `MOONCAKE_OFFLOAD_HEARTBEAT_INTERVAL_SECONDS` | 10 | 心跳间隔(驱动落盘) |
| `MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES` | 1.25 GB | 读回时的暂存区 |
| `MOONCAKE_OFFLOAD_USE_URING` | `false` | io_uring 异步 I/O |
| `MOONCAKE_OFFLOAD_BUCKET_MAX_TOTAL_SIZE` | `0`(=物理盘 90%) | Bucket 后端驱逐阈值 |
| `MOONCAKE_OFFLOAD_BUCKET_EVICTION_POLICY` | `none` | `none` / `fifo` / `lru` |

> **三种后端取舍**:
> - `bucket`(推荐):多对象打包、支持 FIFO/LRU、可断点恢复。
> - `file_per_key`:易调试、小文件多。
> - `offset_allocator`:高并发小对象,**重启不恢复**(初始化时 truncate 数据文件)。

### 4.6 验证 & 常见坑

- **看是否真的落盘**:`/nvme/mooncake_offload` 下应出现 `*.bucket` / `*.meta`(bucket 后端)。
- **没触发卸载?** 多半是 **`--global_segment_size` 相对写入数据太大**,内存没到高水位。把内存池调小(如示例的 4GB)即可触发。
- **必须两端都带 `--enable_offload=true`**:Master 和 Real Client 的 gflag 名字相同,都得开。
- **开 io_uring 报 `Failed to register buffer with UringFile`**:`MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES` 超过了 `RLIMIT_MEMLOCK`。`ulimit -l unlimited` 或把暂存区调小。
- **多节点**:每台机器各起一个 `mooncake_client`,`--host` 填**本机对外可达 IP**(不要用 `127.0.0.1`),都指向同一个 Master。

---

## 5. 关键源码索引

| 关注点 | 文件:行 |
|---|---|
| 副本类型枚举 | `mooncake-store/include/allocator.h:21` |
| 副本 variant 数据模型 | `mooncake-store/include/replica.h:163,205` |
| 磁盘 I/O 类(PosixFile/UringFile) | `mooncake-store/include/file_interface.h` |
| 存储后端 + Bucket 驱逐策略 | `mooncake-store/include/storage_backend.h:179` |
| FileStorage 卸载/加载/晋升编排 | `mooncake-store/include/file_storage.h` |
| Master 卸载/晋升 API + 内存驱逐配置 | `mooncake-store/include/master_service.h` |
| Master offload-on-evict + near-LRU 逻辑 | `mooncake-store/src/master_service.cpp:5025,3244,3100,3197,3298,3331` |
| 内存池 LRU/FIFO 驱逐策略 | `mooncake-store/include/eviction_strategy.h` |
| Master gflag 定义 | `mooncake-store/src/master.cpp:75-135` |
| Real Client gflag 定义 | `mooncake-store/src/real_client_main.cpp:21` |
| SSD 卸载 env 读取 | `mooncake-store/src/file_storage.cpp:45-90` |
| Bucket 后端 env 读取 | `mooncake-store/src/storage_backend.cpp:51-75` |

## 6. 参考文档

- `docs/source/design/hicache-design.md` —— HiCache 三级缓存设计(L1/L2/L3)。
- `docs/source/design/ssd-offload.md` —— SSD 卸载设计(架构图、卸载/加载时序图、三种后端、两阶段驱逐、io_uring)。
- `docs/source/deployment/ssd-offload.md` —— SSD 卸载部署指南(本文配置参数来源)。
- `docs/source/deployment/nvmf-ssd-deployment-guide.md` —— NVMe-oF SSD 部署。
- `docs/source/deployment/mooncake-store-deployment-guide.md` —— Store 整体部署。
