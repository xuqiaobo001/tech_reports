# Mooncake 控制面：业务面 ZMQ Side Channel 深度解析

> 场景：Mooncake connector 在 PD 分离部署中，P、D 之间除了传 KV 数据的参数面 RoCE 通道外，还有一条独立的 ZMQ 信令通道。
> 本文讲清这条 ZMQ 通道的拓扑、消息内容、与 RoCE 数据通道的分工。
> 一句话核心：**ZMQ 通道走业务面 TCP，专门传 KV 传输的"信令/控制信息"（拉取请求、显存地址交换、配对校验、结果通知）；RoCE 走参数面，传 KV 张量数据。**

---

## 1. 什么是"业务面 ZMQ"

它是 Mooncake connector 里一个**独立的 ZMQ（ZeroMQ）消息通道**，专门用来在 P、D 节点之间传递 **KV 传输的"信令/控制信息"**——**走业务面 TCP 网络，不传 KV 数据本身**。

为什么叫"业务面"：它跑的是普通 TCP（业务面/Ethernet），和传 KV 数据的参数面 RoCE 是两条物理网络。ZMQ 是这个通道用的消息库。

它的角色是**传输协调的"带外控制信道"**，和 KV 数据传输的"数据信道"分工明确。

---

## 2. 为什么需要它（核心动机）

KV 数据传输（`batch_transfer_sync_write`）需要一个前置条件：**P 必须知道"要把 KV 发到 D 的哪个显存地址"**。但这个地址信息不在 P 手里——它存在 D 的显存里（D 注册的 KV cache 基地址 + block 偏移）。

所以传输前必须先有一轮**信令交换**：

```
D → P: "我要拉这些 block,我的显存地址是这些,把 KV 写过来"
P:     校验 + 调 batch_transfer_sync_write 把 KV 写到 D 给的地址
P → D: "传完了"(或出错)
```

这条信令不能走参数面 RoCE（那是专用 DMA 通道，不传应用层消息），所以**用一个独立的 TCP 通道传**——这就是 ZMQ side channel。

---

## 3. 源码里的 ZMQ 拓扑

### P 侧：ROUTER socket，bind

`mooncake_connector.py:947-951`（`_mooncake_sender_listener`）：

```python
sock = self.async_zmq_ctx.socket(zmq.ROUTER)                          # ROUTER 类型
self.side_channel_port = sock.bind_to_random_port(f"tcp://{self.hostname}")
```

- P 侧每个 rank 起一个 **ROUTER** socket，bind 到业务面 IP 的一个随机端口（`side_channel_port`）；
- ROUTER 是 ZMQ 的"服务端"socket，能识别每个连接的 D 对端身份（identity），可异步处理多个 D 的请求。

P 侧把这个 `tcp://{hostname}:{side_channel_port}` 地址注册到 bootstrap server（`:917-921`），供 D 查询。

### D 侧：DEALER socket，connect

`mooncake_connector.py:1567-1572`（`receive_kv_from_single_worker`）：

```python
with make_zmq_socket(
    self.async_zmq_ctx, worker_addr, zmq.DEALER, bind=False, linger=0
) as sock:
    sock.setsockopt(zmq.RCVTIMEO, (VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT + 60) * 1000)
    await sock.send(encoded_data)        # 发请求
    while True:
        ret_msg = await sock.recv()      # 收响应
```

- D 侧用 **DEALER** socket（ZMQ 的"客户端"socket），connect 到 P 注册的 `worker_addr`；
- DEALER 支持异步收发，适合"发请求→等响应"的模式。

**ROUTER-DEALER 是 ZMQ 经典的"多客户端对一个服务端"异步模式**：多个 D rank 可同时连一个 P rank，P 侧 ROUTER 用 identity 区分谁是谁。

---

## 4. ZMQ 通道传什么内容

### D → P：`MooncakeXferMetadata`（拉取请求）

`mooncake_connector.py:1538-1552`，D 发给 P 的信令：

```python
metadata = MooncakeXferMetadata(
    remote_hostname=self.hostname,          # D 的地址(给 P 回信用)
    remote_port=self.rpc_port,              # D 的 mooncake RPC 端口
    remote_tp_size=self.tp_size,            # D 的 TP(给 P 做配对校验)
    remote_tp_rank=self.tp_rank,            # D 的 rank(P 校验配对用)
    req_blocks={                            # 要拉哪些 block
        req_id: (transfer_id, local_block_ids)
    },
    kv_caches_base_addr=self.kv_caches_base_addr,  # ★ D 的显存基地址
    block_lens=self.block_len_per_layer,           # block 长度
)
encoded_data = self._encoder.encode(metadata)      # msgspec 编码
await sock.send(encoded_data)
```

**关键内容**：
- `kv_caches_base_addr`：D 各层 KV cache 的显存基地址——P 据此算出 `dst_ptrs`，KV 才知道写到哪；
- `req_blocks`：要拉的 block id 列表（通过 `transfer_id` 关联请求）；
- `remote_tp_rank` / `remote_tp_size`：P 用它做 **TP rank 配对校验**（1:1 / 4:1 校验）。

这些全是**小尺寸控制信息**（几 KB），不包含 KV 张量数据。

### P → D：`MooncakeXferResponse`（传输结果）

P 处理完（调 `batch_transfer_sync_write` 把 KV 经参数面 RoCE 写过去）后，通过同一条 ZMQ socket 返回响应：

```python
response = MooncakeXferResponse(
    status=FINISH / ERROR,    # 传输成功/失败
    ok_reqs=[...],            # 成功的请求
    err_reqs=[...],           # 失败的请求
    err_msg="...",            # 错误信息(如 TP rank 配对失败)
)
await sock.send_multipart((identity, self._encoder.encode(response)))
```

D 侧收到响应后，`process_pulling_result` 更新 `pull_tasks_count`（聚合 count 归零逻辑）。

---

## 5. 完整协作：ZMQ 信令 + RoCE 数据 的分工

以 D 拉一个请求的 KV 为例：

```
┌─── 业务面 TCP (ZMQ ROUTER↔DEALER) ──────────────────────┐
│                                                         │
│  D rank k                          P rank k              │
│  ┌──────────┐    MooncakeXferMetadata    ┌──────────┐   │
│  │ DEALER   │ ──────────────────────────▶│ ROUTER   │   │
│  │ connect  │  (要哪些block+D显存地址)    │ bind     │   │
│  └──────────┘                            └────┬─────┘   │
│       ▲                                       │         │
│       │ MooncakeXferResponse                  │         │
│       │ (FINISH/ERROR)                        │         │
│       │                                       ▼         │
│       │                              send_kv_to_decode  │
│       │                              校验TP配对         │
│       │                                       │         │
└───────┼───────────────────────────────────────┼─────────┘
        │                                       │
        │                  ┌─── 参数面 RoCE (ADXL) ────┐
        │                  │                          │
        │                  │  batch_transfer_sync_write│
        │                  │  P显存 ──DMA写──▶ D显存   │
        │                  │  (KV张量数据,零拷贝)       │
        │                  └──────────────────────────┘
        │                                       │
        └─────── D 收到 FINISH,count-1 ─────────┘
```

**两条通道各司其职**：
- **ZMQ（业务面 TCP）**：传信令——"拉哪些 block、D 的显存地址、配对校验、传输结果"。小数据、低频、可靠。
- **RoCE（参数面）**：传 KV 数据——大块张量零拷贝 DMA。大数据、高频、高带宽。

---

## 6. 为什么用 ZMQ 而不是普通 HTTP/gRPC

| 特性 | ZMQ (ROUTER/DEALER) | 普通 HTTP | 说明 |
|---|---|---|---|
| **异步多路** | ROUTER 天然支持多 D 并发连接 + identity 路由 | 需连接池 | D 多 rank 同时连 P 一个 rank |
| **长连接** | DEALER 持久连接,复用 | HTTP/1.1 keepalive | 每个 D rank 重复拉取,复用连接省开销 |
| **轻量** | 无 HTTP 头开销,二进制 msgspec 编码 | 文本头重 | 信令小,要省带宽 |
| **超时控制** | `RCVTIMEO` 精细控制 | 需额外配置 | 配合 `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` |
| **内置 asyncio** | `zmq.asyncio` | 需 httpx 等 | D 侧 receiver loop 是异步的 |

注意 bootstrap server（节点发现）用的是 **HTTP**（`mooncake_utils.py` 的 FastAPI/uvicorn），而**运行时的 per-request 信令**用 **ZMQ**。两者分工：
- bootstrap HTTP：节点启动时注册/查询地址（低频，一次性）；
- ZMQ side channel：每个请求的 KV 拉取信令（高频，运行时）。

---

## 7. 对应到 GLM-5.1 方案 A

在方案 A（P TP=16, D TP=4）下，ZMQ 通道的实际使用：

- D#5 的 rank 0 要从 P#0 的 rank 0,1,2,3 拉（4:1 聚合）；
- D#5 rank 0 起一个 DEALER，分别 connect 到 P#0 rank 0,1,2,3 的 4 个 ROUTER；
- 发 4 个 `MooncakeXferMetadata`（业务面 TCP）；
- P#0 的 rank 0,1,2,3 收到后，各自校验配对 + 调 `batch_transfer_sync_write`（参数面 RoCE）；
- MLA 去重后只有 rank 0 实际传 KV，rank 1,2,3 通过 ZMQ 返回响应但不传数据；
- D#5 rank 0 收齐 4 个 ZMQ 响应，count 归零，KV 就绪。

**所以方案 A 一次 P→D 传输，业务面 ZMQ 产生 4 对请求/响应（很小），参数面 RoCE 产生 1 条实际 KV 数据流（MLA 去重）**。ZMQ 信令开销相对 KV 数据可忽略。

---

## 8. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| P 侧 ROUTER bind | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:947-951` |
| P 侧注册地址到 bootstrap | `vllm/.../mooncake_connector.py:913-921` |
| D 侧 DEALER connect | `vllm/.../mooncake_connector.py:1567-1572` |
| D→P MooncakeXferMetadata 内容 | `vllm/.../mooncake_connector.py:1538-1552` |
| P→D MooncakeXferResponse | `vllm/.../mooncake_connector.py:1005-1018`（send_kv_to_decode）|
| bootstrap 用 HTTP | `vllm/.../mooncake/mooncake_utils.py:44-130` |

---

## 9. 总结

"业务面 ZMQ"是 Mooncake connector 里一个独立的 ZeroMQ 消息通道（P 侧 ROUTER bind、D 侧 DEALER connect，走业务面 TCP），专门在 P、D 之间传递 KV 传输的"信令"：

- D 告诉 P"要拉哪些 block、KV 写到 D 的哪个显存地址"（`MooncakeXferMetadata`）；
- P 完成后通过它回传结果（`MooncakeXferResponse`）。

它和传 KV 数据的参数面 RoCE 是分工协作的两条通道：**ZMQ 传小尺寸控制信令**（配对校验、地址交换、结果通知），**RoCE 传大块 KV 张量**（零拷贝 DMA）。没有 ZMQ 这条信令通道，P 就不知道该把 KV 写到 D 的哪里，参数面 RoCE 也就无从发起传输。
