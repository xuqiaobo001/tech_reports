# Mooncake 元数据与 etcd 角色深度分析（含 ER 图）

> 问题：Mooncake 的元数据是基于 etcd 吗？etcd 里存了哪些数据结构？
> 核心结论：**Mooncake 有两套独立元数据体系，etcd 角色完全不同。Transfer Engine 段元数据可选存 etcd（默认不用）；Store 对象元数据在 Master 内存（不进 etcd）；etcd 在 Store 仅 HA 模式存选主+oplog+snapshot。**

---

## 1. 关键澄清：Mooncake 有两套元数据，etcd 的角色不同

很多人以为"Mooncake 把所有元数据存 etcd"，**这是不准确的**。实际上 etcd 涉及**两套独立体系**，且角色完全不同：

| 体系 | 元数据内容 | 默认存储 | etcd 的角色 |
|---|---|---|---|
| **A. Transfer Engine 元数据**（peer 发现）| segment、buffer、rank 信息、RPC 地址 | **可选** etcd/redis/http，**默认推荐 P2PHANDSHAKE（无 etcd）** | 主存（可选之一）|
| **B. Mooncake Store 对象元数据**（Master 管理）| object→replica→slice→segment 映射 | **Master 进程内存**，不进 etcd | **仅 HA 模式**：选主 lease + oplog 复制 + snapshot catalog |

**核心结论：**
1. **Transfer Engine 的段元数据才是 etcd 能"主存"的那套**（PD 分离场景下，vLLM connector 默认用 P2PHANDSHAKE，根本不用 etcd）；
2. **Store 的对象元数据（KV cache 对象、副本位置）默认在 Master 内存**，**不存 etcd**；
3. etcd 在 Store 里只在 **HA 模式** 出现，存的是**选主信息 + 操作日志 + 快照目录**，不是对象数据本身。

---

## 2. etcd 里实际存的 key 结构

### 体系 A：Transfer Engine 元数据（当选用 etcd 时）

key 前缀（`transfer_metadata.cpp:146-182`）：

```
mooncake/{cluster_id}/                     ← MC_METADATA_CLUSTER_ID,默认空
├── rpc_meta/{segment_name}                ← RpcMetaDesc (RPC 地址)
├── ram/{segment_name}                     ← SegmentDesc (段描述,JSON)
└── ram/{segment_name} (含/已是路径)        ← 同上
```

每个 segment 对应一个 key（`getFullMetadataKey`），value 是序列化的 `SegmentDesc`（JSON）。

### 体系 B：Store HA 模式的 etcd（仅 enable_snapshot/HA 时）

```
{cluster_namespace}/...                    ← master_view 选主
├── master_view_key                        ← 当前 leader 地址 + lease(选主)
├── {cluster_id}/oplog/latest              ← 最新 oplog 序号
├── {cluster_id}/oplog/{seq}               ← 操作日志条目(PutStart/Remove 等)
└── {cluster_id}/snapshot_catalog/...      ← 快照目录索引
```

---

## 3. ER 图（两套体系的核心实体关系）

### 体系 A：Transfer Engine 元数据（peer 发现，可选存 etcd）

```
  ┌───────────────────┐         ┌──────────────────────┐
  │   RpcMetaDesc     │  1───*  │     SegmentDesc      │
  │ (rpc_meta/{name}) │────────▶│   (ram/{segment})    │
  ├───────────────────┤  1:1    ├──────────────────────┤
  │ ip_or_host_name   │ 每 seg  │ name (PK)            │
  │ rpc_port          │ 一条RPC │ protocol             │
  │ (barex_port)      │         │ timestamp            │
  └───────────────────┘         │ tcp_data_port        │
                                │ rdma_server_name     │
                                ├──────────────────────┤
                                │ ── *:1 ──────────────┤
                                │  ┌─ RankInfoDesc ─┐  │   (Ascend 专用)
                                │  │ rankId         │  │
                                │  │ hostIp/Port    │  │
                                │  │ deviceLogicId  │  │
                                │  │ devicePhyId    │  │
                                │  │ deviceIp/Port  │  │
                                │  │ endpoints[]    │  │── 每卡1个ADXL engine
                                │  └────────────────┘  │
                                └──────┬───────────────┘
                                       │ 1:* (一段可含多块内存)
                                       ▼
                                ┌──────────────────────┐
                                │     BufferDesc       │  (注册的内存块)
                                ├──────────────────────┤
                                │ name                 │
                                │ addr (显存/内存指针)  │
                                │ length               │
                                │ protocol             │
                                │ lkey[]/rkey[] (RDMA) │
                                │ shm_name (NVLink)    │
                                └──────────────────────┘
   关系: Segment 1──* Buffer    Segment 1──1 RankInfo(Ascend)
   ※ Transfer Engine 做 peer 发现用: "谁的哪块内存在哪,怎么RDMA访问"
```

**字段说明：**

| 结构 | 字段 | 含义 |
|---|---|---|
| RpcMetaDesc | ip_or_host_name, rpc_port | 节点 RPC 地址（业务面）|
| SegmentDesc | name, protocol, timestamp | 段名（PK）、传输协议（ascend/rdma/tcp）|
| SegmentDesc | tcp_data_port, rdma_server_name | TCP/RDMA 服务名 |
| RankInfoDesc | rankId, hostIp/Port | 昇腾 rank 与主机地址 |
| RankInfoDesc | deviceLogicId/PhyId, deviceIp/Port | NPU 卡的逻辑/物理 ID、参数面 NIC 地址 |
| RankInfoDesc | endpoints[] | 每卡的 ADXL engine 名（前面讲的 GenAdxlEngineName）|
| BufferDesc | name, addr, length | 注册的内存/显存块地址与大小 |
| BufferDesc | lkey/rkey | RDMA 内存注册 key |
| BufferDesc | shm_name | NVLink 共享内存名 |

### 体系 B：Mooncake Store 对象元数据（Master 内存，HA 时 etcd 存 oplog）

```
  ┌─────────────────┐                                    ┌─────────────────┐
  │   Client/Node   │ 1───* (贡献内存)                   │     Object      │
  │ (贡献 segment)  │───────────────────────────────────▶│ (KV cache对象)  │
  ├─────────────────┤         拥有 segment                ├─────────────────┤
  │ client_id (PK)  │                                    │ object_key (PK) │
  │ ip/hostname     │           ┌──────────────────┐     │ (model/tp/pp/..)│
  │ segment_name ───┼──┐        │    ReplicaInfo   │     │ size            │
  │ capacity        │  │  1───*  │  (一个object的   │     │ status          │
  │ used            │  └────────▶│   多副本之一)    │◀────│                 │
  │ alive(heartbeat)│     1:*    ├──────────────────┤ 1:* │                 │
  └─────────────────┘            │ status           │     └─────────────────┘
                                 │ handles[] ───────┼──┐
                                 └──────────────────┘  │ 1:*
                                                       ▼
                                          ┌──────────────────────┐
                                          │      BufHandle        │ (一个副本的分片)
                                          ├──────────────────────┤
                                          │ segment_name (FK)    │──▶ 指向某 Client 的 segment
                                          │ buffer (addr/offset) │
                                          │ size                 │
                                          │ status(INIT/COMPLETE/│
                                          │   FAILED/UNREGISTERED)│
                                          └──────────────────────┘
   关系: Object 1──* Replica 1──* BufHandle ──▶ Client.segment
   ※ Store 的对象级存储语义: "一个KV对象有N副本,每副本切成slice放不同segment"
```

### 体系 B-HA：etcd 在 HA 模式下存的（选主 + 复制，不是对象数据）

```
  etcd:
  ┌─────────────────────────────┐
  │ {ns}/master_view (lease)    │ ← 选主: leader_address + lease TTL
  │ {cid}/oplog/latest          │ ← 最新 oplog 序号
  │ {cid}/oplog/{seq}  1──*     │ ← 操作日志(PutStart/Remove等的wal)
  │   ├ opcode                  │
  │   ├ object_key              │
  │   ├ replica/slice 变更      │
  │   └ term/index              │
  │ {cid}/snapshot_catalog/*    │ ← 快照目录(指向snapshot object store)
  └─────────────────────────────┘
   ※ follower master 通过 oplog 同步内存状态; leader 切换时从 snapshot+oplog 恢复
   ※ 对象元数据本身仍在 leader 内存,etcd 只存变更日志和选主
```

---

## 4. 对应到整体设计逻辑（为什么这么分）

Mooncake 的设计哲学是**"数据流与控制流彻底分离，元数据尽量轻量"**：

1. **Transfer Engine 元数据（体系 A）**：只存"谁在哪、内存怎么 RDMA 访问"这种**静态拓扑信息**，量小、读多写少。所以默认甚至不用 etcd——P2PHANDSHAKE 让节点间直接交换，去中心化。etcd 只是大型长期集群的可选项。

2. **Store 对象元数据（体系 B）**：存"哪个 object 的副本在哪些 slice"这种**高频动态映射**，量大、更新频繁（每次 Put/Get/evict 都改）。放 Master 内存是为了**低延迟**（每次 Get 都要查 replica 位置，进 etcd 太慢）。Master 挂了靠 snapshot+oplog 重建。

3. **etcd 在 Store 的定位**：不是"存对象元数据"，而是"**保证 Master 高可用**"——存选主 lease（谁是 leader）、oplog（状态变更日志，follower 同步）、snapshot catalog（恢复指引）。对象数据本身一条不进 etcd。

4. **数据流完全不碰元数据存储**：KV 数据走参数面 RoCE 直传，Master 永远不在数据路径上。etcd 也不在数据路径上。

---

## 5. 回到 PD 分离场景

对跑 vLLM + Mooncake connector 的 PD 分离：

- **Transfer Engine 元数据**：vLLM connector 默认 `metadata_server="P2PHANDSHAKE"`（`mooncake_connector.py:765`），**根本不用 etcd**——P、D 之间通过 bootstrap（HTTP）+ ZMQ 交换地址，segment 信息在握手时本地存储。
- **Store 对象元数据**：如果用 **TE connector**（`MooncakeConnectorV1`，前面所有方案用的），**根本不涉及 Store、不涉及 Master、不涉及 etcd**。KV 是 P→D 显存直传。
- **只有用 Store connector**（`MooncakeStoreConnector`，带 Master 缓存池）才可能涉及 etcd（且仅当开 HA）。

**所以：前面所有的 PD 分离分析（方案 A 等）实际不依赖 etcd。etcd 是 Mooncake 的可选项，不是必选项。**

---

## 6. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| etcd/redis/http/p2p 四种 metadata 后端 | `Mooncake/mooncake-transfer-engine/src/transfer_metadata_plugin.cpp:1-140` |
| etcd key 前缀结构（mooncake/、rpc_meta/、ram/）| `Mooncake/mooncake-transfer-engine/src/transfer_metadata.cpp:136-182` |
| SegmentDesc / BufferDesc 结构 | `Mooncake/mooncake-transfer-engine/include/transfer_metadata.h:52-105` |
| RankInfoDesc（Ascend 专用）| `Mooncake/mooncake-transfer-engine/include/transfer_metadata.h:75-87` |
| RpcMetaDesc | `Mooncake/mooncake-transfer-engine/include/transfer_metadata.h:123-128` |
| Store HA 后端（etcd/redis/k8s）| `Mooncake/mooncake-store/src/master.cpp:145-149` |
| Store 对象元数据在内存（BufHandle/ReplicaInfo proto）| `Mooncake/docs/source/design/mooncake-store.md` MasterService proto |
| etcd oplog key（latest/entry）| `Mooncake/mooncake-store/src/ha/oplog/etcd_oplog_store.cpp:37-80` |
| etcd leader 选主（master_view + lease）| `Mooncake/mooncake-store/src/ha/leadership/backends/etcd/etcd_leader_coordinator.cpp:46-128` |
| vLLM connector 默认 P2PHANDSHAKE | `vllm/.../mooncake/mooncake_connector.py:765` |

---

## 7. 总结

Mooncake 的元数据并非都存 etcd：

1. **Transfer Engine 段元数据**（SegmentDesc/BufferDesc/RpcMetaDesc/RankInfo，描述"内存拓扑 + RDMA 访问方式"）可选存 etcd，但**默认 P2PHANDSHAKE 不用**；
2. **Store 对象元数据**（Object→Replica→BufHandle→Client.segment，描述"KV 对象副本位置"）存在 **Master 内存，不进 etcd**；
3. **etcd 在 Store 仅 HA 模式**出现，存选主 lease + oplog 操作日志 + snapshot catalog，用于保证 Master 高可用，**对象数据一条不进**。

设计逻辑是**"数据流/控制流分离、元数据轻量化、Master 永不在数据路径"**——跑 vLLM PD 分离（TE connector）实际完全不依赖 etcd。
