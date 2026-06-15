# Mooncake KV Cache 跨 P/D 共享的网络面归属分析

> 面向平台：华为昇腾（Ascend NPU）
> 涉及组件：Mooncake Transfer Engine、vLLM KV Connector V1（mooncake）
> 结论一句话：**KV Cache 数据搬运走参数面网络（RoCE/HCCS），控制/元数据走业务面网络（TCP）。**

---

## 1. 背景与问题

在 vLLM 的 **PD 分离（Prefill/Decode Disaggregated）部署**中，Prefill 节点（P）与 Decode 节点（D）需要把已计算好的 KV Cache 在节点间搬运，以避免在 D 侧重复 prefill。Mooncake 作为 KV Cache 的缓存与传输中间件接入 vLLM，承担这部分跨节点搬运。

在昇腾平台上，集群通常划分两张物理网络：

- **参数面网络（parameter-plane）**：NPU 之间的高速 RoCE/HCCS 网络，传统用于集合通信（HCCL）中搬运梯度/参数。
- **业务面网络（business-plane）**：普通 TCP/Ethernet，承载 RPC、调度、元数据等信令。

本文要回答的核心问题是：

> 当 Mooncake 的 KV Cache 在多个 P、D 之间共享时，KV Cache 数据到底走参数面网络还是业务面网络？

---

## 2. 结论

Mooncake 在昇腾平台上对流量做了**数据面 / 控制面分离**：

| 流量类型 | 物理网络 | 承载内容 |
|---|---|---|
| **数据面（KV Cache 张量本体）** | **参数面（NPU RoCE/HCCS）** | KV Cache 的 D2D / 跨节点拷贝 |
| 控制面 / 带外 | 业务面（TCP/Ethernet） | segment 注册、RPC、握手、vLLM 旁路信令 |

因此，对“KV Cache 缓存共享”这一问题本身：**KV Cache 数据基于参数面网络传输**。业务面网络只负责元数据与信令协调。

这也意味着部署时：**P、D 节点的 NPU 参数面网卡必须在同一 RoCE 网络内可达**，而业务面只需普通 TCP 可达。

---

## 3. 关键证据（源码与文档）

### 3.1 数据面：Ascend Transport 使用参数面 NIC

最直接的证据来自 Mooncake 官方设计文档 `Mooncake/docs/source/design/transfer-engine/ascend_transport.md`（Endpoint Management 一节，第 104 行）：

> Each Huawei NPU card has a dedicated **parameter-plane NIC** and should be managed by a single `TransferEngine` instance responsible for all its data transfers.

数据传输协议的选择（`ascend_direct_transport.md:91`）：

- 节点内（A2 服务器 / A3 超节点）：默认 **HCCS**；
- 跨节点：使用 **RDMA（RoCE）**，可通过 `HCCL_INTRA_ROCE_ENABLE=1` 强制开启 RoCE。

这两者都跑在 NPU 的参数面网卡上。

此外，`local_server_name` 在昇腾下必须携带物理 NPU 卡号（格式 `ip:port:npu_x`），见 `transfer_engine_impl.cpp:113-119`、`193-194`：

```cpp
std::string mutable_server_name =
    local_server_name_ + ":npu_" + std::to_string(devicePhyId);
```

其根本原因正是**每张 NPU 卡各有一块独立的参数面网卡**，TransferEngine 必须绑定到具体那张卡的参数面 NIC。

### 3.2 segment 注册的 protocol 为 "ascend"

在 `ascend_direct_transport.cpp:149`，segment 描述里写入的协议就是 `"ascend"`：

```cpp
int AscendDirectTransport::allocateLocalSegmentID() {
    ...
    desc->protocol = "ascend";
    ...
}
```

是否启用 RoCE 由环境变量决定（`ascend_direct_transport.cpp:152-160`）：

```cpp
char *roce_enable_str = std::getenv("HCCL_INTRA_ROCE_ENABLE");
if (roce_enable_str) {
    std::optional<int32_t> roce_enable = parseFromString<int32_t>(roce_enable_str);
    if (roce_enable.has_value() && roce_enable.value() == 1) {
        roce_mode_ = true;
    }
}
```

### 3.3 协议映射：vLLM 传的 "rdma" 实际落到参数面 transport

vLLM 侧的 Mooncake 连接器默认配置为（`vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:759-765`）：

```python
protocol = kv_transfer_config.kv_connector_extra_config.get(
    "mooncake_protocol", "rdma"
)
...
ret_value = self.engine.initialize(self.hostname, "P2PHANDSHAKE", protocol, "")
```

即默认 `mooncake_protocol="rdma"`、metadata 用 `P2PHANDSHAKE`。

但在昇腾上，`"rdma"` **不会真的使用通用 RDMA transport**，而是被映射回参数面的 ascend transport。见 `multi_transport.cpp:447-460`（`selectTransport`）：

```cpp
#ifdef USE_ASCEND_HETEROGENEOUS
    // When USE_ASCEND_HETEROGENEOUS is enabled:
    // - Target side directly reuses RDMA Transport
    // - Initiator side uses heterogeneous_rdma_transport
    if (target_segment_desc->protocol == "rdma") {
        proto = "ascend";
    }
#endif
```

同时，engine 初始化的昇腾分支只安装 `ascend` transport，不安装通用 RDMA（`transfer_engine_impl.cpp:205-208`）：

```cpp
#if defined(USE_ASCEND) || defined(USE_ASCEND_DIRECT)
    Transport* ascend_transport =
        multi_transports_->installTransport("ascend", local_topology_);
    if (!ascend_transport) {
        LOG(ERROR) << "Failed to install Ascend transport";
        return -1;
    }
```

**结论：无论 vLLM 配的是 `rdma` 还是 `ascend`，跨 P/D 的 KV 搬运最终都走 NPU 参数面 NIC 上的 ADXL/HCCL（RoCE/HCCS）。**

### 3.4 控制面 / 元数据：业务面（TCP）

承载“哪个 segment 在哪个 P/D、哪些 block 要迁移”这类信令的流量，全部是 TCP，走业务面：

- **RPC / 握手服务**：`transfer_engine_impl.cpp:141,175` 使用 `findAvailableTcpPort(...)` 启动 TCP RPC 服务，可用 `MC_TCP_BIND_ADDRESS` 指定业务面 IP。P2P 模式下日志会打印：
  ```
  Transfer Engine RPC using <method> listening on <IP>:<port>
  ```
- **Metadata 服务**：支持 etcd / redis / http / p2phandshake 四选一，全部基于 TCP/业务面（`ascend_transport.md:124-126`）。
- **vLLM 侧旁路通道**：连接器额外开了一条 **ZMQ over TCP** 的 side channel（`mooncake_connector.py:910,949-951`），在 P、D 间传递 `MooncakeXferMetadata`（要加载哪些 block 的索引信息），同样在业务面。
- **Ascend transport 内部带外 TCP**：transport 内部还会建立一条 host-side TCP（默认端口 `ASCEND_BASE_PORT` / 10000 一带）用于带外握手与状态同步。它**不承载 KV 数据**，属于控制/带外信道。

---

## 4. 两种 Ascend 数据 transport 的关系

昇腾平台下 Mooncake 提供两条数据传输实现，二者都跑在参数面网络上：

| Transport | 基础库 | 状态 | 数据链路 | 说明 |
|---|---|---|---|---|
| `ascend_direct_transport` | CANN **ADXL** | **推荐** | HCCS（节点内）/ RoCE（跨节点） | 新路径，支持 H2D/D2H/D2D、异步传输、fabric memory（A3） |
| `hccl_transport` | **HCCL** | 计划弃用 | RoCE / HCCS | 老路径，基于 HCCL 集合通信语义 |

二者的数据 DMA 都落在 NPU 参数面网卡，区别仅在于底层库（ADXL vs HCCL）与实现成熟度。

> 注：文档明确 `ASCEND TRANSPORT is scheduled for deprecation, please use ASCEND DIRECT TRANSPORT on ASCEND platform.`（`ascend_transport.md:5`）

---

## 5. 部署影响与建议

1. **网络规划**：P、D 节点的 NPU 必须在**参数面 RoCE 网络**内互通；KV Cache 搬运带宽直接受限于参数面 RoCE 带宽。
2. **必备配置**：容器内需挂载或拷贝 `/etc/hccn.conf`（`ascend_direct_transport.md:80`、`ascend_transport.md:63-64`），否则无法解析本地参数面网卡信息。
3. **RDMA 调优**：跨节点 RoCE 场景下，可能需要根据交换机/网卡规划调整 Traffic Class / Service Level（`ASCEND_RDMA_TC`、`ASCEND_RDMA_SL`），见 `ascend_direct_transport.md:93-95`。
4. **超时与重传**：建议 `ASCEND_TRANSFER_TIMEOUT` 略大于 `重传超时 × HCCL_RDMA_RETRY_CNT`，见 `ascend_direct_transport.md:86-89`。
5. **业务面要求低**：控制/元数据走 TCP，业务面只需普通可达，对带宽要求很低。
6. **vLLM 配置**：连接器侧 `mooncake_protocol` 在昇腾下保持默认 `rdma` 即可（会被映射到 ascend transport），无需手动改为 `ascend`。

---

## 6. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| 数据走参数面 NIC | `Mooncake/docs/source/design/transfer-engine/ascend_transport.md:104` |
| 数据协议 HCCS/RDMA | `Mooncake/docs/source/design/transfer-engine/ascend_direct_transport.md:91` |
| `local_server_name` 带 npu 卡号 | `Mooncake/mooncake-transfer-engine/src/transfer_engine_impl.cpp:113-119,193-194` |
| segment protocol = "ascend" | `Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp:149` |
| roce_mode 由环境变量控制 | `ascend_direct_transport.cpp:152-160` |
| "rdma" → "ascend" 映射 | `Mooncake/mooncake-transfer-engine/src/multi_transport.cpp:447-460` |
| 昇腾只装 ascend transport | `Mooncake/mooncake-transfer-engine/src/transfer_engine_impl.cpp:205-208` |
| 控制面 RPC = TCP | `Mooncake/mooncake-transfer-engine/src/transfer_engine_impl.cpp:141,175` |
| metadata 服务全 TCP | `ascend_transport.md:124-126` |
| vLLM 默认 protocol=rdma + P2PHANDSHAKE | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:759-765` |
| vLLM ZMQ TCP 旁路 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:910,949-951` |

---

## 7. 总结

Mooncake 接入 vLLM PD 分离部署后，KV Cache 的跨节点共享遵循“**数据走参数面、信令走业务面**”的经典分离原则：

- **KV Cache 张量本体** → 昇腾 NPU 参数面网络（RoCE / HCCS，经 ADXL/HCCL）；
- **元数据、RPC、握手、vLLM 旁路协调信令** → 业务面 TCP 网络。

因此，针对原问题“mooncake 的 kvcache 缓存在多个 P、D 共享时是基于参数面还是业务面”——**答案是参数面网络**。部署时需重点保障 P、D 之间参数面 RoCE 的连通性与带宽，业务面只需保证 TCP 可达。
