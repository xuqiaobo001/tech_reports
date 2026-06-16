# Mooncake `batch_transfer_sync_write` 跨 P/D 节点 KV Cache 传输深度解析

> 场景：PD 分离部署，P 侧用 `batch_transfer_sync_write` 把一批 KV block 推送到 D 侧显存。
> 平台：华为昇腾（CANN ADXL / 参数面 RoCE）。
> 本文从 Python 调用一路追到 CANN ADXL 的实际 DMA，拆解 6 层调用链的每一层实现。

---

## 1. `batch_transfer_sync_write` 的本质

它是 mooncake Transfer Engine 暴露给上层的**同步批量写入接口**——把多个不连续的内存块，**一次性、批量地**从本地 P 卡的显存写到远端 D 卡的显存。在 PD 分离里，P 侧用它把一组 KV block 推给 D 侧。

`batch` 的关键价值：一次 `submitTransfer` 提交 N 个传输请求（对应 N 个 KV block），底层复用同一条 ADXL 链路、共享连接，**避免 per-block 的提交开销**。文档实测对小非连续块（128KB）能提升 100%+ 带宽。

---

## 2. 从 Python 到 ADXL 的完整调用链

```
vLLM connector (P 侧)
  └─ self.engine.batch_transfer_sync_write(remote_session, src_ptrs, dst_ptrs, lengths)
      │  (pybind 绑定, transfer_engine_py.cpp:1139)
      ▼
TransferEnginePy::batchTransferSyncWrite        [transfer_engine_py.cpp:362]
  └─ batchTransferSync(..., WRITE, ...)          [:491]
      ├─ openSegment(target_hostname)           // 解析远端 D 的 segment handle
      ├─ 构造 N 个 TransferRequest{WRITE, source, target_id, target_offset, length}
      ├─ allocateBatchID(N)                     // 分配批量描述符
      ├─ engine_->submitTransfer(batch_id, entries)   // 提交
      └─ while: getBatchTransferStatus(batch_id)      // 同步轮询直到 COMPLETED/FAILED
          ▼
TransferEngineImpl::submitTransfer              [transfer_engine_impl.h:119]
  └─ multi_transports_->submitTransfer(batch_id, entries)
      ▼
MultiTransport::submitTransfer                  [multi_transport.cpp:104]
  ├─ for each request: selectTransport(request)  // 选 transport (ascend)
  └─ transport->submitTransferTask(tasks)        // 分发给 AscendDirectTransport
      ▼
AscendDirectTransport::submitTransferTask       [ascend_direct_transport.cpp:245]
  ├─ for each request: InitializeSlice(...)      // src/dst/len → Slice
  └─ dispatcher_->enqueue(slice_list)            // 切片入队
      ▼
SliceDispatcher (Default / RoceDummyReal)       [slice_dispatcher.h]
  └─ 按 target_id 分组 → 派发到执行线程
      ▼
SyncTransferExecutor::execute                   [sync_transfer_executor.cpp:46]
  ├─ 构造 adxl::TransferOpDesc{local_addr=src, remote_addr=dst, len}
  ├─ checkAndConnect(target_adxl_engine_name)    // 建立/复用 ADXL 链路
  └─ engine->TransferSync(target, WRITE, op_descs, timeout)  // ★ CANN ADXL 实际 DMA
      ▼
  P 卡显存 ──参数面 RoCE──► D 卡显存 (零拷贝)
```

---

## 3. 逐层详解

### 第 1 层：Python 调用（vLLM connector）

`mooncake_connector_v1.py:595-607`，P 侧 `_send_blocks` 调用：

```python
ret_value = self.engine.batch_transfer_sync_write(
    remote_session, src_ptrs, dst_ptrs, lengths
)
```

四个参数的含义：
- `remote_session` = `"{D的hostname}:{D的rpc_port}"`，标识**目标 D 的哪张卡**（远端 segment）；
- `src_ptrs` = P 侧显存里各 KV block 的起始地址列表；
- `dst_ptrs` = D 侧显存里对应 block 的目标地址列表（D 在 metadata 里告知）；
- `lengths` = 每个 block 的字节数。

关键：`src_ptrs/dst_ptrs/lengths` 三个列表**一一对应**，每个三元组描述一个 KV block 的传输。**batch 的本质就是这一个调用里塞了 N 个 block**。

> `_build_transfer_params`（`:588-650`）会用 `group_consecutive_contiguous` 把**连续的 block 合并**成一个更大的传输描述符（`length = block_len × 连续块数`），减少 ADXL op_desc 数量，进一步提升效率。

### 第 2 层：pybind → C++ `batchTransferSync`

`transfer_engine_py.cpp:491`，把 Python 列表转成 C++ 的 `TransferRequest` 向量：

```cpp
int TransferEnginePy::batchTransferSync(..., WRITE, ...) {
    pybind11::gil_scoped_release release;          // 释放 GIL,让传输并行
    // 1. 解析远端 segment handle (带缓存)
    handle = handle_map_[target_hostname];          // 命中缓存
    //   或 engine_->openSegment(target_hostname);  // 首次:从 metadata 取 D 的 segment 描述

    // 2. 构造 N 个 TransferRequest
    for (i in batch_size) {
        entry.opcode = TransferRequest::WRITE;
        entry.source = buffers[i];                  // P 侧显存地址
        entry.target_id = handle;                   // 远端 D segment handle
        entry.target_offset = peer_buffer_addresses[i];  // D 侧显存地址
        entry.length = lengths[i];
        entries.push_back(entry);
    }

    // 3. 提交 + 轮询
    for (retry...) {
        batch_id = allocateBatchID(batch_size);
        engine_->submitTransfer(batch_id, entries);
        while (!completed) {
            getBatchTransferStatus(batch_id, status);   // ★ 同步阻塞轮询
            if (COMPLETED) return 0;
            if (FAILED) { completed=true; break; }
            if (TIMEOUT) { completed=true; break; }
            // 超时检查: transfer_timeout + total_length (1GiB/s 估算)
        }
    }
}
```

几个关键设计：
- **`gil_scoped_release`**：传输期间释放 Python GIL，不阻塞其他 Python 线程；
- **`handle_map_` 缓存**：远端 segment handle 首次 `openSegment` 后缓存，后续传输复用，避免每次查 metadata；
- **批量提交**：N 个 entry 一次性 `submitTransfer`，共享一个 `batch_id`；
- **同步轮询**：`while` 循环调 `getBatchTransferStatus` 直到 COMPLETED/FAILED，这就是"sync"的含义——**调用线程阻塞直到整批传完**；
- **超时**：`transfer_timeout_nsec_ + total_length`（按 1GiB/s 估算传输时间），超时返回 -1；
- **重试**：`max_retry = numContexts + 1`，遍历所有本地 RNIC context 重试（容错：若某条参数面网线故障，换一条）。

### 第 3 层：submitTransfer → transport 选择与分发

`multi_transport.cpp:104`，`MultiTransport::submitTransfer`：

```cpp
for (auto& request : entries) {
    selectTransport(request, transport);   // 按 target segment 的 protocol 选 transport
    //   D 的 segment protocol = "ascend" → 选 AscendDirectTransport
    submit_tasks[transport].push_back(&task);
}
for (auto& entry : submit_tasks)
    entry.first->submitTransferTask(tasks);  // 分发给 AscendDirectTransport
```

`selectTransport`（`:442`）按目标 segment 的 `protocol` 字段选 transport。D 侧 segment 在注册时 protocol = `"ascend"`（`allocateLocalSegmentID`），所以这里选到 `AscendDirectTransport`。

### 第 4 层：AscendDirectTransport → Slice 初始化

`ascend_direct_transport.cpp:245`，`submitTransferTask` 把每个 request 转成一个 **Slice**：

```cpp
for (index : task_list) {
    InitializeSlice(request, current_engine_id, &task, slice);
    // slice->source_addr      = P 侧显存地址 (request.source)
    // slice->length           = block 长度
    // slice->opcode           = WRITE
    // slice->target_id        = D 的 segment handle
    // slice->ascend_direct.dest_addr = D 侧显存地址 (request.target_offset)
    // slice->ascend_direct.engine_id = 当前 P 卡的 engine id
    __sync_fetch_and_add(&task.slice_count, 1);  // 原子计数
    slice_list.push_back(slice);
}
dispatcher_->enqueue(std::move(slice_list));   // 切片入队
```

`Slice` 是传输的最小单位（一个 block 对应一个 slice）。`InitializeSlice`（`:50-64`）把 request 的 `source/target_offset/length` 映射到 slice 的 `source_addr/dest_addr/length`。

### 第 5 层：SliceDispatcher 分组派发

`slice_dispatcher.h:40-91`，dispatcher 按 `target_id`（目标 D segment）分组，派发到执行线程：

- **DefaultSliceDispatcher**：按 `target_id` 分组，丢进共享线程池；
- **RoceDummyRealSliceDispatcher**：每 ADXL engine 一个线程，按 `(engine_idx, target_id)` 分组（dummy-real 模式用）。

这一步把"一批 N 个 block"按目标分组，每组复用一条 ADXL 连接。

### 第 6 层：SyncTransferExecutor → ADXL 实际 DMA（★ 核心）

`sync_transfer_executor.cpp:46`，这是真正发起硬件传输的地方：

```cpp
ExecuteResult SyncTransferExecutor::execute(
    local_engine_idx, target_adxl_engine_name, WRITE, slice_list) {
    auto* engine = adxl_engines_[local_engine_idx].get();

    // 1. 建链 (复用连接)
    if (!auto_connect) checkAndConnect(target_adxl_engine_name);

    // 2. 把 slice 转成 ADXL 传输描述符
    for (slice : slice_list) {
        op_desc.local_addr  = slice->source_addr;              // P 侧显存
        op_desc.remote_addr = slice->ascend_direct.dest_addr;  // D 侧显存
        op_desc.len         = slice->length;
        op_descs.emplace_back(op_desc);
    }

    // 3. ★ 调用 CANN ADXL 执行批量同步传输
    auto status = engine->TransferSync(
        target_adxl_engine_name, WRITE, op_descs, transfer_timeout);

    // 4. 成功则标记所有 slice 完成
    if (status == SUCCESS)
        for (slice : slice_list) slice->markSuccess();
}
```

**`engine->TransferSync(...)` 就是 CANN ADXL 库的调用**，它：
- 经 P 卡的参数面 NIC（RoCE）建立到 D 卡的 RDMA 链路；
- 按 op_descs 列表，把 P 卡显存的各 block **零拷贝 DMA 写**到 D 卡显存；
- 同步等待全部完成（对应 `batch_transfer_sync_write` 的"sync"）。

`target_adxl_engine_name` = D 那张卡的 ADXL engine 名（`GenAdxlEngineName(host_ip, listen_port)`，每卡一个），决定了数据写到 D 的**哪张卡**。

---

## 4. 跨 P/D 节点的完整时序

把上面 6 层串起来，一次 `batch_transfer_sync_write` 的时序：

```
P 卡 k 显存里的 N 个 KV block
        │
        ▼  (1) vLLM _send_blocks → batch_transfer_sync_write
┌───────────────────────────────────────────┐
│ P 侧 TransferEngine (绑定到 P 卡 k)        │
│  • openSegment(D卡k) → 拿到 D segment handle│
│  • 构造 N 个 WRITE request                  │
│  • submitTransfer → AscendDirectTransport   │
│  • 每个 request → Slice{src=P显存, dst=D显存}│
└───────────────────┬───────────────────────┘
                    │ dispatcher 分组
                    ▼
┌───────────────────────────────────────────┐
│ SyncTransferExecutor (P 卡 k 的执行线程)    │
│  • N 个 slice → N 个 ADXL TransferOpDesc    │
│  • checkAndConnect(D卡k的ADXL engine)      │
│  • engine->TransferSync(WRITE, op_descs) ★ │
└───────────────────┬───────────────────────┘
                    │ 参数面 RoCE (P卡k的网线 → 交换机 → D卡k的网线)
                    ▼
┌───────────────────────────────────────────┐
│ D 卡 k 显存                                 │
│  • N 个 block 被零拷贝写入 (被动接收)        │
└───────────────────────────────────────────┘
                    │
                    ▼  (2) P 侧 getBatchTransferStatus 轮询到 COMPLETED
        P 侧调用返回 0,传输完成
```

---

## 5. 关键设计要点（为什么这么做）

| 设计 | 作用 | 源码位置 |
|---|---|---|
| **batch 提交** | N 个 block 一次 submitTransfer，共享 batch_id 和连接，省 per-block 开销 | `batchTransferSync:524-537` |
| **连续 block 合并** | `group_consecutive_contiguous` 把相邻 block 合成大描述符，减少 op_desc 数 | connector `_build_transfer_params` |
| **handle_map_ 缓存** | 远端 segment handle 首次解析后缓存，省 metadata 查询 | `batchTransferSync:499-510` |
| **gil_scoped_release** | 传输期间释放 GIL，多线程并行传输（多卡同时发） | `batchTransferSync:498` |
| **同步轮询** | while getBatchTransferStatus 直到完成，调用方阻塞 | `batchTransferSync:555-580` |
| **RNIC 重试** | max_retry = numContexts+1，遍历所有本地参数面 NIC，单条线故障可换 | `batchTransferSync:539` |
| **checkAndConnect 复用** | ADXL 链路建一次后复用（除非 use_short_connection） | `execute:57` |
| **零拷贝** | 直接 DMA 显存到显存，不经 CPU/主机内存 | ADXL TransferSync |

---

## 6. 对应到 GLM-5.1 方案 A

在方案 A（P TP=16, D TP=4, MLA）下，`batch_transfer_sync_write` 的具体表现：

1. **谁调用**：P#0 的 rank 0/4/8/12（MLA 去重后只有这 4 个发送），各自独立调用；
2. **target**：每个 P rank 调用指向 D#5 的对应 rank（P rank0→D rank0，P rank4→D rank1...）；
3. **batch 内容**：一次调用里是该请求的全部 KV block（MLA latent block），经连续合并后是少量大描述符；
4. **并行**：4 个 P rank 的 4 次 `batch_transfer_sync_write` **并行执行**（不同线程、不同参数面 NIC），形成 4 条并行 RoCE 流；
5. **同步**：每个 P rank 的调用阻塞到自己的 batch 完成；
6. **完成聚合**：D 侧 `pull_tasks_count` 归零后请求就绪。

---

## 7. sync vs async 的选择

mooncake 还提供 `batch_transfer_async_write`（`transfer_engine_py.cpp:386`），区别：

| 接口 | 行为 | 适用 |
|---|---|---|
| `batch_transfer_sync_write` | 提交后**阻塞轮询**到完成 | 简单可靠，vLLM connector 默认用这个 |
| `batch_transfer_async_write` | 提交后立即返回 batch_id，后续 getTransferStatus 查询 | 重叠计算与传输，需 `ASCEND_USE_ASYNC_TRANSFER=1` |

PD 分离默认走 sync（`mooncake_connector.py:1365` 的 `batch_transfer_sync_write`），因为 KV 必须到位才能 decode，同步语义最直接。若要榨性能可切 async 让 prefill 计算与上一批 KV 传输重叠。

---

## 8. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| Python 调用入口 | `Mooncake/mooncake-wheel/mooncake/mooncake_connector_v1.py:595-607` |
| pybind 绑定 | `Mooncake/mooncake-integration/transfer_engine/transfer_engine_py.cpp:1139` |
| `batchTransferSync` 实现 | `Mooncake/mooncake-integration/transfer_engine/transfer_engine_py.cpp:491` |
| TransferRequest 结构 | `Mooncake/mooncake-transfer-engine/include/transport/transport.h:60-69` |
| TransferEngineImpl::submitTransfer | `Mooncake/mooncake-transfer-engine/include/transfer_engine_impl.h:119-125` |
| MultiTransport::submitTransfer | `Mooncake/mooncake-transfer-engine/src/multi_transport.cpp:104-143` |
| selectTransport | `Mooncake/mooncake-transfer-engine/src/multi_transport.cpp:442-460` |
| AscendDirectTransport::submitTransferTask | `Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp:245-275` |
| InitializeSlice | `Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp:50-64` |
| SliceDispatcher | `Mooncake/mooncake-transfer-engine/include/transport/ascend_transport/ascend_direct_transport/slice_dispatcher.h:40-91` |
| SyncTransferExecutor::execute (ADXL) | `Mooncake/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/sync_transfer_executor.cpp:46-93` |

---

## 9. 总结

`batch_transfer_sync_write` 跨 P/D 共享 KV Cache 的流程是：

1. P 侧把 N 个 KV block 的 `(源地址, 目标地址, 长度)` 打包成一批 WRITE 请求；
2. 经 pybind 进入 C++ `batchTransferSync` → `openSegment` 拿到 D 卡的 segment handle；
3. `submitTransfer` 按 D 的 "ascend" protocol 选到 AscendDirectTransport；
4. 每个 block 初始化为一个 Slice；
5. dispatcher 按 D 卡分组派发；
6. SyncTransferExecutor 把 slice 转成 ADXL TransferOpDesc 并调用 `engine->TransferSync(WRITE, op_descs)`，由 CANN ADXL 经 P 卡的参数面 RoCE 把显存零拷贝 DMA 写到 D 卡显存；
7. P 侧同步轮询 `getBatchTransferStatus` 到 COMPLETED 返回。

**batch 的价值在于一次提交复用连接、连续 block 合并，把 N 个非连续 KV block 高效地推过参数面。**
