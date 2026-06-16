# Mooncake 元数据后端（etcd / P2P / redis / http）解决的问题对比

> 问题：Mooncake 的 etcd / p2p / redis / http 后端，各自解决什么问题？
> 核心结论：**这几种后端本质解决同一个核心问题——"跨节点 peer 发现"，只是用不同方式解决。etcd/redis/http 是中央 KV 存储的三种实现，P2P 是去中心化的点对点交换。**

---

## 1. 它们共同解决的核心问题：peer 发现

Mooncake 要做跨节点零拷贝传输，P 必须知道 D 的"传输入口信息"——**D 的内存在哪、用什么协议、怎么 RDMA 访问**。这些信息（`SegmentDesc`：segment 名、协议、rank 信息、buffer 地址、RDMA key 等）**不能凭空获得**，必须有个地方存和查。

这几种后端**全都为了解决这个问题**：让节点能注册自己的 segment 信息、并查到别人的 segment 信息。源码里它们实现的是同一个接口 `MetadataStoragePlugin`（`transfer_metadata_plugin.h:28-30`），只有三个操作：

```cpp
virtual bool get(key, value) = 0;     // 查别人的 segment
virtual bool set(key, value) = 0;     // 注册自己的 segment
virtual bool remove(key) = 0;         // 注销 segment
```

**所以本质上，etcd/redis/http 是"同一个抽象的三种实现"——都是中央 KV 存储，只是换了后端。**

---

## 2. 关键区分：中央存储 vs P2P

真正有本质区别的是**两类架构**：

| 模式 | 后端 | 怎么解决 peer 发现 | 对应源码分支 |
|---|---|---|---|
| **中央存储** | etcd / redis / http | 所有节点把 segment 信息写到**一个共享存储**，需要时去查 | `storage_plugin_->get/set` |
| **去中心化** | **P2PHANDSHAKE** | 节点间**直接点对点交换** segment 信息，本地缓存，**无中央存储** | `handshake_plugin_->exchangeMetadata` |

源码里 `getSegmentDesc`（`transfer_metadata.cpp:877-910`）清晰地分了这两条路径：

```cpp
if (p2p_handshake_mode_) {
    // P2P 模式: 直接和目标节点交换 segment 信息
    ret = handshake_plugin_->exchangeMetadata(ip, port, local_json, ...);
} else {
    // 中央存储模式: 去共享存储查
    storage_plugin_->get(getFullMetadataKey(segment_name), ...);
}
```

这是最核心的设计选择：**要不要一个中心化的元数据服务**。

---

## 3. 每种后端各自解决的具体问题

### 3.1 P2PHANDSHAKE —— 解决"零依赖快速起步"

**解决的问题**：不想部署任何额外服务（不想装 etcd/redis），节点间直接发现彼此。

- **机制**：节点间用 TCP socket 直接握手交换 segment JSON，各自本地缓存（`SocketHandShakePlugin`）；
- **解决的核心痛点**：**部署简单、无单点故障、无外部依赖**；
- **代价**：节点必须预先知道对端地址（P2P 模式下 `segment_id` 要带对端 IP:port）；不适合大规模动态发现。

**适用**：开发、测试、小规模、PD 分离（vLLM connector 默认就是它，`mooncake_connector.py:765`）。

### 3.2 etcd —— 解决"大规模强一致动态发现"

**解决的问题**：几十上百个节点的集群，节点动态加入/退出，需要可靠的全局发现。

- **机制**：所有节点把 segment 写进 etcd，需要时查；etcd 保证一致性和 watch 通知；
- **解决的核心痛点**：**强一致（etcd 用 Raft）、高可用（集群）、动态发现（节点随便加退）、watch 机制（变化实时推送）**；
- **代价**：要部署维护 etcd 集群（3~5 节点），有运维成本。

**适用**：大型长期生产集群、多租户共享、节点频繁变动。

### 3.3 redis —— 解决"已有 redis 基础设施时的复用"

**解决的问题**：集群里已经有 redis，不想再装 etcd，复用现有基础设施。

- **机制**：和 etcd 一样是中央 KV，但用 redis 做 backend；
- **解决的核心痛点**：**复用现有 redis、更低延迟（redis 单线程内存）、单机够用**；
- **代价**：redis 默认非强一致（除非开 AOF+主从），单点风险（除非 redis 集群）；不支持 etcd 那样的 watch/lease 一致性语义。

**适用**：已有 redis 栈、对一致性要求不高、追求低延迟。

### 3.4 http —— 解决"Master 嵌入式单机简化部署"

**解决的问题**：用 Store 模式时，Master 本来就是个进程，让它内嵌一个 HTTP metadata server，省得再装 etcd。

- **机制**：Master 进程起一个 HTTP 服务，提供 segment 的增删查改（`--enable_http_metadata_server`）；
- **解决的核心痛点**：**单机/小集群零外部依赖**（Master 自带），部署最简单；
- **代价**：Master 是单点（HTTP server 也单点），无 HA，不适合大规模。

**适用**：Store 模式的单机部署、demo、入门（部署文档 Quick Start 用的就是它）。

---

## 4. 横向对比：为什么需要这四种

| 维度 | P2PHANDSHAKE | etcd | redis | http(嵌入Master) |
|---|---|---|---|---|
| **部署复杂度** | 最低（无外部服务）| 高（3-5节点集群）| 中（复用现有）| 低（Master自带）|
| **一致性** | N/A（点对点）| **强一致(Raft)** | 弱/最终一致 | 弱(单点) |
| **高可用** | 天然(无中心) | **集群 HA** | 需 redis 集群 | 单点 |
| **动态发现** | 需预知对端 | **强(任意加退)** | 中 | 弱 |
| **规模** | 小~中 | **大(上百节点)** | 中~大 | 小 |
| **外部依赖** | 无 | etcd | redis | 无(用Master) |
| **watch/推送** | 回调 | **支持** | pub/sub | 轮询 |
| **典型场景** | PD分离/测试 | 大型生产集群 | 有redis栈 | Store单机/demo |

---

## 5. 它们不解决的问题（重要边界）

这几种后端**只解决 Transfer Engine 的 peer 发现**，**不解决**：

1. **Store 对象元数据**（object→replica→slice 映射）—— 这个在 Master 内存，不进这些 KV 后端；
2. **KV 数据本身** —— 走参数面 RoCE，和元数据后端无关；
3. **请求路由**（谁 prefill、谁 decode）—— 由上层 disaggregator/router 决定，不靠这些后端；
4. **运行时信令**（per-request 拉取请求）—— 走 ZMQ，不靠这些后端。

---

## 6. 设计逻辑：为什么这么分

Mooncake 的元数据后端设计体现**"渐进式复杂度"**原则——**用最小的复杂度匹配场景**：

```
开发/小规模 ──▶ P2PHANDSHAKE (零依赖)
                  │
Store单机 ────────┼─▶ http (Master嵌入)
                  │
有redis栈 ────────┼─▶ redis (复用)
                  │
大型生产 ─────────┴─▶ etcd (强一致+HA)
```

- **没有"一刀切"**：不让所有场景都背 etcd 的运维成本；
- **同一抽象**：四种后端实现同一个 `MetadataStoragePlugin` 接口，上层 `TransferMetadata` 不感知差异；
- **P2P 是特例**：它根本不用 storage 抽象，而是 handshake 抽象，走完全去中心化的路径。

---

## 7. 回到 PD 分离场景

跑 vLLM + Mooncake 做 PD 分离：

- **用的是 P2PHANDSHAKE**（connector 默认）→ **解决了"零依赖起步"**，P、D 通过 bootstrap(HTTP) + ZMQ 互相发现，**不需要 etcd/redis/http 任何一个**；
- 只有当集群规模大到 P2P 不够用（节点动态发现难、规模上百），才需要升级到 etcd；
- 这几种后端**和 PD 分离的 KV 传输性能无关**——它们只在启动/节点变更时用一次，传输期间不碰。

---

## 8. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| 统一的 KV 抽象（get/set/remove）| `Mooncake/mooncake-transfer-engine/include/transfer_metadata_plugin.h:28-30` |
| 三种中央存储后端分发 | `Mooncake/mooncake-transfer-engine/src/transfer_metadata_plugin.cpp`（`MetadataStoragePlugin::Create`）|
| P2P 握手插件 | `Mooncake/mooncake-transfer-engine/src/transfer_metadata_plugin.cpp`（`HandShakePlugin::Create` → `SocketHandShakePlugin`）|
| P2P vs 中央存储分支 | `Mooncake/mooncake-transfer-engine/src/transfer_metadata.cpp:877-910`（`getSegmentDesc`）|
| vLLM connector 默认 P2PHANDSHAKE | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:765` |
| http 嵌入 Master | `Mooncake/docs/source/deployment/mooncake-store-deployment-guide.md`（`--enable_http_metadata_server`）|

---

## 9. 总结

Mooncake 的 etcd/p2p/redis/http 四种后端，本质都是解决同一个核心问题——**"跨节点 peer 发现"**（节点如何注册并查到彼此的 segment/RDMA 访问信息）。它们实现同一个 KV 抽象（get/set/remove），区别在于解决方式：

1. **P2PHANDSHAKE** 用点对点握手交换，零依赖、去中心化，解决"快速起步和小规模"；
2. **etcd** 用 Raft 强一致集群，解决"大规模动态发现和高可用"；
3. **redis** 复用现有内存 KV，解决"已有 redis 栈、低延迟"；
4. **http** 由 Master 内嵌，解决"Store 单机零外部依赖"。

它们**都不存 KV 数据本身**（数据走参数面 RoCE）、**不存 Store 对象元数据**（在 Master 内存）、**不管请求路由**（上层 router），只在**节点发现这一环用一次**。跑 vLLM PD 分离默认用 P2P，根本不需要 etcd。
