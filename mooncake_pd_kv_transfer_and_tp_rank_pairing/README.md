# Mooncake PD 分离 KV Cache 交互机制与 TP Rank 1:1 配对原理

> 面向平台：华为昇腾（Ascend NPU）
> 部署拓扑：1 个 P（Prefill）节点独占一台昇腾服务器（8 卡），1 个 D（Decode）节点独占一台昇腾服务器（8 卡），PD 之间通过 Mooncake 做 KV Cache 交互。
> 一句话核心：**控制流走业务面（TCP），数据流走参数面（RoCE），按 TP rank 1:1 配对、8 条链路并行零拷贝传输。**

---

## 1. 背景

在 vLLM 的 PD 分离（Prefill/Decode Disaggregated）部署中，P 节点完成 prefill 后，需要把算出的 KV Cache 传给 D 节点，让 D 直接续做 decode，避免在 D 侧重复 prefill。Mooncake（Transfer Engine）承担这部分跨节点 KV 搬运。

本文聚焦于一个典型生产拓扑：**P 机 8 卡、D 机 8 卡、每卡一条参数面网线**，讲清楚：

1. PD 之间基于 Mooncake 做 KV Cache 交互的**完整机制**；
2. 传输时为什么是 **TP rank 1:1 配对**，其底层原理是什么。

---

## 2. 总体架构：每张卡一条独立链路

该拓扑下，实际是 **8 条并行的、彼此独立的 P→D KV 传输通道**，每条由一对 (P 卡 k, D 卡 k) 组成：

```
P 机                       业务面(TCP)                D 机
┌─────────────┐    bootstrap/ZMQ 旁路信令    ┌─────────────┐
│ P rank0..7  │ ◄══════════════════════════► │ D rank0..7  │
│ 每卡1个TE   │                              │ 每卡1个TE   │
└──────┬──────┘                              └──────┬──────┘
       │ 参数面 RoCE (每卡1条网线)                   │
       └────────═══► 交换机 ◄─────────────────────┘
        KV数据 batch_transfer_sync_write/read
```

- **P 机 8 个 worker 进程**，每个绑定 1 张卡，各建 1 个 TransferEngine（`mooncake_connector.py:743-746`）；
- **D 机同理 8 个**；
- 每个 TE 把自己卡的 KV 显存注册成一个 segment，endpoint 绑在该卡的参数面 NIC（通过 `local_server_name` 携带 `npu_x` 物理卡号，见 `transfer_engine_impl.cpp:193-194`）。

> 关键设计：**一卡一 TransferEngine、一卡一 segment、一卡一条参数面 NIC**。Mooncake 不做"挑卡"选路，KV Cache 与物理 NPU 在注册阶段就 1:1 绑定。详见源码 `ascend_direct_transport.cpp:145-198` 的 `allocateLocalSegmentID()`。

---

## 3. 发现与建链（业务面，TCP）

P 和 D 之间不能凭空找到对方，需要协调：

1. **Bootstrap Server**（`mooncake_utils.py:44-130`）：一个轻量 HTTP 服务（FastAPI/uvicorn）。每个 P/D worker 启动时把自己的 `engine_id / dp_rank / tp_rank / pp_rank / 连接地址(IP:port)` 注册上去。Server 按 `(dp_rank → tp_rank → pp_rank)` 建立地址表，供对端查询。
2. **`transfer_id`**：外部 router/disaggregator 给每个请求分配一个全局 transfer_id，随请求的 `kv_transfer_params` 带到 P 和 D，作为双方配对的钥匙（在 `get_num_new_matched_tokens` 中读取 `remote_engine_id / remote_bootstrap_addr / transfer_id`）。
3. **ZMQ 旁路（业务面 TCP）**：D 侧拉一条 ZMQ side channel（`mooncake_connector.py:949-951`），用来向 P 发送 `MooncakeXferMetadata`（要哪些 block、自己的 segment 基地址、tp_rank 等）。**这条旁路只传信令，不传 KV 数据**。

---

## 4. KV Cache 交互全流程

### 4.1 D 侧决定"拉哪些 KV"（控制流）

D 收到一个 `do_remote_prefill` 请求时：

- `get_num_new_matched_tokens`（`mooncake_connector.py:548-580`）：告诉调度器"这个 prompt 的 KV 全部要从 P 拉"，返回需拉取的 token 数、并标记为**异步加载**（在调度 step 间隙执行，不阻塞 engine）。
- `update_state_after_alloc`（`:582-617`）：D 给请求分配本地 KV block 后，把"要从远端拉的 block id 列表"记入 `_reqs_need_recv`。
- `build_connector_meta`（`:637-669`）：把这些待拉请求打包成 `MooncakeConnectorMetadata`，下发给 worker。

### 4.2 P 侧决定"发哪些 KV"（控制流）

P 处理完 prefill，请求 `request_finished` 时，`do_remote_decode` 触发把本地 KV block id 记入 `_reqs_need_send`，`build_connector_meta` 打包。worker 侧 `record_send_reqs`（`:1698`）就绪后 `send_meta.ready.set()`。

### 4.3 TP rank 配对与传输计划（关键）

`send_kv_to_decode`（`:1003-1018`）：P 收到 D 的 metadata 后，先做 **TP rank 配对校验**：

```python
remote_tp_ranks = self.transfer_topo.handshake_target_ranks(meta.remote_tp_size)
if meta.remote_tp_rank not in remote_tp_ranks:
    # This D worker does not pair with the P worker.
    msg = (f"This D tp_rank {meta.remote_tp_rank} is not paired with "
           f"P tp_rank {self.tp_rank}; expected one of {remote_tp_ranks}.")
    response = MooncakeXferResponse(status=ERROR, err_msg=msg)
```

即 P rank k 只接受 D rank k 的请求，其他直接拒绝（返回错误，不传数据）。

### 4.4 实际数据传输（参数面 RoCE，零拷贝）

- **P 侧发送**（`_send_blocks`，`:1357-1380`）：
  ```python
  ret_value = self.engine.batch_transfer_sync_write(
      remote_session, src_ptrs, dst_ptrs, lengths
  )
  ```
  `src_ptrs` 是 P 卡 k 显存里 KV block 的地址（已通过 `batch_register_memory` 注册 RDMA MR），`remote_session` 指向 D 卡 k 注册的 segment。这是**一次批量、非连续块的同步 RDMA write**，从 P 卡 k 显存经 P 卡 k 的参数面 NIC → 交换机 → D 卡 k 的参数面 NIC → D 卡 k 显存，**零拷贝**。
- **D 侧接收**：本质是被动的——P 用 RDMA write 直接写进 D 卡 k 已注册的显存段。D 侧 `receive_kv`（`:1643`）/ `_start_load_kv`（`:1687`）负责编排、状态轮询与完成通知。
- 完成后 D 在下一个调度 step 直接用这些 KV 继续 decode，**不在 D 上重做 prefill**。

### 4.5 一个请求的生命周期

1. Router 给请求分配 `transfer_id`，把请求路由到 P 机，并标记 D 机为目标。
2. **P 机 8 卡并行 prefill**，算出 KV；每卡 TE 把自己显存注册为 segment（绑各自参数面 NIC）。
3. P prefill 完成，`request_finished` → 各 rank 把待发 KV 入队。
4. **D 机某 rank k** 收到带 `transfer_id` 的请求，`get_num_new_matched_tokens` 标记需远程拉取。
5. D rank k 通过 bootstrap 找到 P rank k 的地址，经**业务面 ZMQ** 向 P rank k 发送 `MooncakeXferMetadata`（要拉哪些 block、自己的 segment 地址）。
6. P rank k 校验 TP rank 配对 → 计算 block 映射 → `batch_transfer_sync_write` 沿**参数面 RoCE** 把 KV 写进 D rank k 显存。
7. D rank k 轮询传输完成 → 释放等待 → 调度器拿到 KV，开始 decode。

---

## 5. TP Rank 1:1 配对原理（核心）

### 5.1 为什么需要配对？——TP 下 KV Cache 是"切开的"

Tensor Parallelism（张量并行，TP）把一个模型切成 8 份分摊到 8 张卡上。关键在于：**KV Cache 不是整块存在某张卡上，而是按 attention head 切片分散在 8 张卡上**。

以一个多头注意力模型为例（假设 32 个 KV head，TP=8）：

```
完整 KV Cache (32 个 KV head 的 K 和 V):
┌────────────────────────────────────────────────┐
│ head0..3  │ head4..7 │ head8..11 │ ... │ head28..31 │
└────────────────────────────────────────────────┘
     卡0          卡1        卡2            卡7

每张卡只持有 32/8 = 4 个 KV head 的 K 和 V
```

这是 TP 的数学要求：第 k 张卡在前向计算时，**只产生、也只需要**自己负责的那部分 KV head。其他卡的 head 根本不存在于这张卡上。

所以：**P 卡 0 的显存里只有 head0..3 的 KV；D 卡 0 也只算 head0..3。** 如果 D 卡 0 把 P 卡 3 的 KV（head12..15）拉过来，那是别人负责的 head，D 卡 0 用不上；而自己需要的 head0..3 反而没拿到。这就是为什么传输必须按 head 分片严格对应。

### 5.2 1:1 配对的本质 = head 分片的对应关系

"1:1 配对"指：**当 P 和 D 的 TP size 相同（都是 8）时，P 的 rank k 只能和 D 的 rank k 传输**。因为：

- P rank k 持有 head 分片 S_k；
- D rank k 也负责同一个分片 S_k；
- 所以 P rank k 的 KV 对 D rank k 是"正确且完整"的，对其他 rank 是"无关的"。

源码在 `handshake_target_ranks`（`utils.py:570-580`）：

```python
def handshake_target_ranks(self, remote_tp_size: int) -> list[int]:
    tp_ratio = self.tp_ratio(remote_tp_size)
    if tp_ratio > 0:
        return [self.tp_rank // tp_ratio]   # 同 size 时 tp_ratio=1 → [自己]
    abs_ratio = -tp_ratio
    return [self.tp_rank * abs_ratio + i for i in range(abs_ratio)]
```

当 P 和 D 都是 TP=8，`tp_ratio = 8//8 = 1`，`handshake_target_ranks` 返回 `[self.tp_rank // 1] = [self.tp_rank]` —— **P rank 3 只配对 D rank 3，只配一个，这就是"1:1"**。

### 5.3 配对的执行点与拒绝逻辑

这个配对在 P 侧 `send_kv_to_decode` 被强制校验（`mooncake_connector.py:1005-1018`，见 4.3）。**P rank k 是一个有状态的守门人**：D 发来的请求带着 `remote_tp_rank`，P 检查它是否等于自己的 k。不等就直接返回错误，根本不传数据。这从机制上杜绝了"传错卡"。

### 5.4 为什么是 1:1 而不是其他关系？——通用 tp_ratio

其实"1:1"只是配对的**特例**，源码设计的是通用的 `tp_ratio`（`utils.py:521-537`）：

```python
def tp_ratio(self, remote_tp_size: int) -> int:
    # local_tp >= remote_tp: 正数，本地多个 rank 共享同一个远端 rank
    # remote_tp > local_tp:  负数，本地一个 rank 要读多个远端 rank
    if self.tp_size >= remote_tp_size:
        return self.tp_size // remote_tp_size
    return -(remote_tp_size // self.tp_size)
```

| P TP size | D TP size | tp_ratio | 配对关系 | 含义 |
|---|---|---|---|---|
| 8 | 8 | **1** | **1:1** | P rank k ↔ D rank k（本文场景）|
| 8 | 4 | 2 | 2:1 | P rank0,1 → D rank0；P rank2,3 → D rank1 …（P 头更细，2 个 P rank 合对应 1 个 D rank）|
| 4 | 8 | -2 | 1:2 | P rank0 → D rank0,1（D 头更细，1 个 P rank 喂 2 个 D rank）|

本文场景 P、D 都是 8 卡，`tp_ratio=1`，所以是严格 1:1。源码用整数除法表达通用关系，1:1 只是 `tp_ratio==1` 的退化情况。

### 5.5 block 偏移：配对后还要对齐"同分片内的哪些 block"

配对确定了"P rank k ↔ D rank k"，但一个 rank 内还有**多个 KV block**（paged KV cache，每张卡上 N 个 block）。`_compute_sender_transfer_plan`（`mooncake_connector.py:136-174`）计算 block 偏移，保证 P rank k 的第 i 个 block 写到 D rank k 的第 i 个 block：

```python
if tp_ratio == 1:
    return True, 0, 0, local_kv_block_len
    #      ^send?, dst_offset=0, src_offset=0, len=完整block
```

1:1 时偏移都是 0、长度是完整 block——**block i 原样对应 block i**，无需重排。这是 1:1 最简单的形态。

### 5.6 完整心智模型

```
prompt 进来 → router 分配 transfer_id → 路由到 P 机

P 机 8 张卡并行 prefill:
  P卡0 产出 head分片0 的 KV  →  入队待发
  P卡1 产出 head分片1 的 KV  →  入队待发
  ...           (每卡只有自己的分片)
  P卡7 产出 head分片7 的 KV  →  入队待发

D 机 8 张卡准备接收:
  D卡0 需要 head分片0 ──┐
  D卡1 需要 head分片1 ──┤  各自经业务面ZMQ向对应P卡发起请求
  ...                   │   (D卡k 的请求带 remote_tp_rank=k)
  D卡7 需要 head分片7 ──┘

配对校验 (P侧):
  D卡0的请求(tp_rank=0) → P卡0 校验: 0 in [0//1]=[0] ✓ → 传
  D卡1的请求(tp_rank=1) → P卡1 校验: 1 in [1//1]=[1] ✓ → 传
  ...                                       (若D卡0误发给P卡3: 0 not in [3] ✗ 拒绝)

数据传输 (参数面RoCE, 8条并行):
  P卡0显存 ──P卡0参数面NIC──→ 交换机 ──→ D卡0参数面NIC ──→ D卡0显存
  P卡1显存 ──P卡1参数面NIC──→ 交换机 ──→ D卡1参数面NIC ──→ D卡1显存
  ...
  P卡7显存 ──P卡7参数面NIC──→ 交换机 ──→ D卡7参数面NIC ──→ D卡7显存

D 机 8 张卡各拿到自己分片 → 拼成完整 KV → 开始 decode
```

---

## 6. 关键特性小结

| 维度 | 机制 | 网络 |
|---|---|---|
| 卡的绑定 | 每卡 1 个 TE / 1 个 segment / 1 条参数面 NIC，注册时 1:1 绑定 | 参数面 |
| 配对 | TP rank 1:1（`handshake_target_ranks` 校验，P 侧拒绝不匹配的请求） | — |
| 发现 | Bootstrap HTTP server + `transfer_id` | 业务面 TCP |
| 信令 | ZMQ side channel 传 `MooncakeXferMetadata` | 业务面 TCP |
| KV 传输 | `batch_transfer_sync_write`（批量 RDMA write，零拷贝） | **参数面 RoCE** |
| 传输单元 | 非连续 KV block 批量传输（一张卡多个 block 一次发） | 参数面 |
| 加载时机 | 异步，在调度 step 间隙执行，不阻塞 engine | — |
| 容错 | 传输失败 `record_failed_transfer`，超时 abort（`VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT`） | — |

---

## 7. 关于 TP Rank 1:1 配对的常见澄清

1. **"1:1"不是说只传一次**：是说 rank 之间的**映射关系**是 1 对 1。一次请求里每对 rank 仍会传该请求的全部 prompt block。
2. **1:1 不依赖 head 数量**：只要 P、D 的 TP size 相等就是 1:1，无论 32 head 还是 MLA。MLA（DeepSeek 那种）因为 hidden 维度不可切，KV 在 TP 间是**复制**的（`local_replicates_kv_cache`，`utils.py:566-568`），但配对关系仍是按 rank 1:1——只是这时每张卡的 KV 内容其实相同，传输选择上由 `producer_cache_replicated` 标志优化（`mooncake_connector.py:155-157`，只用 rank 0 发）。
3. **配对是静态数学，不需要协商**：`handshake_target_ranks` 纯粹由 `tp_rank` 和 `tp_size` 算出，不依赖运行时状态。两边只要 TP 配置一致，算出的配对天然吻合。
4. **配对失败不重试、直接报错**：P 侧校验不通过返回 `ERROR`，避免错误的 KV 污染 D 的 decode（decode 出来会是乱码）。

---

## 8. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| 每卡一个 TE + 绑 device | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:743-746,1351-1355` |
| `local_server_name` 带 npu 卡号 | `Mooncake/mooncake-transfer-engine/src/transfer_engine_impl.cpp:193-194` |
| segment 只注册当前卡 | `Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp:145-198` |
| Bootstrap 发现服务 | `vllm/.../mooncake/mooncake_utils.py:44-130` |
| ZMQ 业务面旁路 | `vllm/.../mooncake/mooncake_connector.py:949-951` |
| D 决定拉哪些 KV | `vllm/.../mooncake/mooncake_connector.py:548-617,637-669` |
| TP rank 配对校验（P 侧） | `vllm/.../mooncake/mooncake_connector.py:1003-1018` |
| block 偏移 / 传输计划 | `vllm/.../mooncake/mooncake_connector.py:136-174` |
| 实际 RDMA write | `vllm/.../mooncake/mooncake_connector.py:1357-1380` |
| `handshake_target_ranks` | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:570-580` |
| `tp_ratio` 通用配对 | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:521-537` |
| MLA / KV 复制 | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:566-568` |

---

## 9. 总结

在 1 个 P 节点（8 卡）+ 1 个 D 节点（8 卡）的昇腾 PD 分离拓扑下，Mooncake 的 KV Cache 交互机制遵循经典的**"数据面/控制面分离"**：

- **控制流**（谁找谁、传哪些 block）：经 Bootstrap HTTP server + `transfer_id` + 业务面 ZMQ side channel 完成，走**业务面 TCP**；
- **数据流**（KV 张量本体）：`batch_transfer_sync_write` 沿**参数面 RoCE** 零拷贝传输；
- **配对**：因为 TP 把 KV 按 attention head 切片分散到 8 张卡，每张卡只持有一个 head 子集，当 P、D TP size 相同时（都是 8），P 的第 k 张卡与 D 的第 k 张卡负责同一 head 子集，故必须且只需 1:1 配对传输。

源码用 `handshake_target_ranks`（`tp_rank // 1`）计算该 1:1 映射，并在 P 侧 `send_kv_to_decode` 强制校验——**只有 tp_rank 相等的 P/D 卡才能配对传输，否则直接拒绝**。这保证 8 条参数面链路各传各的 head 分片，最终在 D 侧拼出一份完整、正确的 KV Cache，D 直接续做 decode。
