# SGLang PD 分离架构：P / D / Router 三进程启动与协同全链路分析

> 分析日期：2026-04-19
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：基于一个请求的完整流量路径，梳理 Prefill、Decode、Router 三个进程的启动序列、参数配置、协同机制

---

## 一、架构全景

```
                         ┌─────────────────────────────────────────────┐
                         │              Client (HTTP)                  │
                         └──────────────────┬──────────────────────────┘
                                            │ POST /generate
                                            ▼
                         ┌─────────────────────────────────────────────┐
                         │         Router (sgl-model-gateway)          │
                         │  Rust 实现，负责 P/D 选择 + 双发请求         │
                         └──────┬──────────────────────┬───────────────┘
                                │                      │
                    bootstrap_host/port/room     同一份请求 JSON
                                │                      │
                 ┌──────────────▼──────┐    ┌──────────▼──────────────┐
                 │  Prefill 实例 (P1-P7) │    │  Decode 实例 (D1)       │
                 │  - Tokenize + Prefill │    │  - 接收 KV Cache        │
                 │  - 生成 KV Cache     │    │  - Decode 生成 token    │
                 │  - RDMA 传输 KV      │    │  - 返回结果给客户端      │
                 └──────────────────────┘    └─────────────────────────┘
```

**核心设计**：Router 将同一个请求**双发**（Dual Dispatch）给 P 和 D。P 负责 prefill 并通过 RDMA 将 KV cache 传输给 D，D 接收 KV cache 后执行 decode 生成最终结果。

---

## 二、三个进程的启动

### 2.1 Prefill 进程启动

```bash
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode prefill \          # 模式：prefill
  --host 0.0.0.0 --port 30000 \            # HTTP 服务端口
  --tp-size 8 \                             # Tensor Parallel
  --dp-size 1 \                             # Data Parallel
  --disaggregation-bootstrap-port 8998 \    # Bootstrap 端口（供 D 发现）
  --disaggregation-transfer-backend mooncake \ # KV 传输后端
  --disaggregation-ib-device mlx5_0 \       # RDMA 网卡
  --mem-fraction-static 0.85               # GPU 内存分配比例
```

**启动序列**：

```
python -m sglang.launch_server
  │
  ▼
launch_server.py:run_server()
  │
  ▼
srt/entrypoints/http_server.py:launch_server()
  │
  ├── Engine.__init__()
  │     ├── 加载模型权重
  │     ├── 初始化 KV Cache Pool
  │     └── _launch_subprocesses()
  │           ├── TokenizerManager 进程
  │           └── Scheduler 进程（TP workers）
  │
  ├── TokenizerManager.__init__()
  │     └── start_disagg_service()
  │           └── BootstrapServer 启动在 :8998  ← 关键：P 才启动
  │
  └── Scheduler 初始化
        └── PrefillBootstrapQueue.__init__()
              ├── 初始化 CommonKVManager（Mooncake 引擎）
              ├── 注册到 BootstrapServer（上报 TP/DP/CP/PP 信息）
              └── 启动 transfer worker 线程
```

**BootstrapServer 注册信息**（`common/conn.py`）：

```python
PUT /route
{
    "attn_tp_size": 8,          # TP 大小
    "attn_tp_rank": 0,          # TP rank
    "attn_cp_size": 1,          # Context Parallel 大小
    "attn_cp_rank": 0,          # CP rank
    "attn_dp_size": 1,          # DP 大小
    "attn_dp_rank": 0,          # DP rank
    "pp_size": 1,               # Pipeline Parallel 大小
    "pp_rank": 0,               # PP rank
    "rank_ip": "10.0.0.1",     # Prefill 节点 IP
    "rank_port": 12345,         # 传输引擎端口
    "page_size": 1,             # KV cache 页大小
    "kv_cache_dtype": "float16" # KV cache 数据类型
}
```

---

### 2.2 Decode 进程启动

```bash
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode decode \            # 模式：decode
  --host 0.0.0.0 --port 30001 \            # HTTP 服务端口
  --tp-size 8 \
  --max-running-requests 128 \              # 最大并发 decode 请求数
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_0
```

**启动序列**：

```
python -m sglang.launch_server
  │
  ▼
Engine.__init__()
  │
  ├── TokenizerManager.__init__()
  │     └── start_disagg_service() → 返回 None  ← D 不启动 BootstrapServer
  │
  └── Scheduler 初始化
        └── DecodePreallocQueue.__init__()
              ├── 初始化 CommonKVManager（Mooncake 引擎）
              ├── 创建 ZMQ PULL socket（接收 P 的连接）
              ├── 初始化 DecodeTransferQueue
              └── 启动 heartbeat_checker 线程（监控 P 节点健康）
```

**Decode 不启动 BootstrapServer**，而是作为客户端去查询 P 的 BootstrapServer。

---

### 2.3 Router 进程启动

```bash
python -m sglang_router.launch_router \
  --pd-disaggregation \                     # 启用 PD 分离模式
  --host 0.0.0.0 --port 8000 \             # 对外服务端口
  --prefill http://10.0.0.1:30000 8998 \   # P1 实例 + bootstrap 端口
  --prefill http://10.0.0.2:30000 8998 \   # P2 实例
  --prefill http://10.0.0.3:30000 8998 \   # P3 实例
  --prefill http://10.0.0.4:30000 8998 \   # P4 实例
  --prefill http://10.0.0.5:30000 8998 \   # P5 实例
  --prefill http://10.0.0.6:30000 8998 \   # P6 实例
  --prefill http://10.0.0.7:30000 8998 \   # P7 实例
  --decode http://10.0.0.8:30001 \         # D 实例（不需要 bootstrap 端口）
  --prefill-policy cache_aware \            # P 路由策略
  --decode-policy random                   # D 路由策略
```

**Router 启动序列**：

```
python -m sglang_router.launch_router
  │
  ├── 解析参数：prefill_urls + bootstrap_ports, decode_urls
  │
  ├── 初始化 MiniLoadBalancer（测试）或 Rust Router（生产）
  │     ├── 注册所有 P 实例：URL + bootstrap_port
  │     └── 注册所有 D 实例：URL
  │
  ├── 启动 HTTP 服务（:8000）
  │
  └── 健康检查：对每个 P/D 实例 /health 轮询
```

**Router 可选路由策略**（`sgl-model-gateway` Rust 实现）：

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| `random` | 随机选择 | 简单均匀负载 |
| `round_robin` | 轮询 | 均匀负载 |
| `cache_aware` | 感知 cache 命中率 | 多轮对话/RAG |
| `power_of_two` | Power-of-Two 选择 | 负载均衡优化 |
| `prefix_hash` | 前缀哈希路由 | 前缀聚合 |

---

### 2.4 三进程启动顺序

```
时间轴 ──────────────────────────────────────────────────────────────►

  ① 启动 Prefill 实例（7 个）
     P1 ──→ BootstrapServer :8998 就绪
     P2 ──→ BootstrapServer :8998 就绪
     ...
     P7 ──→ BootstrapServer :8998 就绪

  ② 启动 Decode 实例（1 个）
     D1 ──→ 连接 P1~P7 的 BootstrapServer，获取拓扑信息
         ──→ 建立 RDMA/Mooncake 连接池
         ──→ heartbeat_checker 线程启动

  ③ 启动 Router
     Router ──→ 对 P1~P7 和 D1 执行 /health 检查
            ──→ 注册所有 worker
            ──→ 开始接受客户端请求

  ④ 客户端请求到达 Router:8000
```

**启动依赖关系**：P 必须先于 D 启动（D 需要查询 P 的 BootstrapServer），Router 最后启动。

---

## 三、一个请求的完整流量路径

### 3.1 阶段概览

```
Client ──POST /generate──► Router
  │                          │
  │                    ① 选择 P + D 实例
  │                    ② 注入 bootstrap_host/port/room
  │                    ③ 双发请求到 P 和 D
  │                          │
  │               ┌──────────┴──────────┐
  │               ▼                     ▼
  │          Prefill 实例          Decode 实例
  │          ④ Tokenize           ⑤ 创建 KVReceiver
  │          ⑤ Prefill Forward    ⑥ Bootstrap 握手
  │          ⑥ 发送 KV Cache ────► ⑦ 接收 KV Cache
  │                               ⑧ Decode Forward
  │                               ⑨ 流式返回结果
  │               └──────────┬──────────┘
  │                          │
  ◄─────────────── SSE Stream ◄──────────
```

---

### 3.2 阶段 ①：Router 选择 P + D 实例

**代码位置**：`sgl-model-gateway/src/routers/http/pd_router.rs`

```rust
async fn select_pd_pair(&self, ...) -> Result<(Arc<dyn Worker>, Arc<dyn Worker>), String> {
    let prefill_workers = self.worker_registry.get_prefill_workers();
    let decode_workers = self.worker_registry.get_decode_workers();

    // 使用配置的策略选择 P 和 D
    let prefill = Self::pick_worker_by_policy_arc(&prefill_workers, prefill_policy).await?;
    let decode = Self::pick_worker_by_policy_arc(&decode_workers, decode_policy).await?;

    Ok((prefill, decode))
}
```

**以 7P1D 为例**：从 7 个 P 实例中选择 1 个（如 P3），从 1 个 D 实例中选择 D1。

---

### 3.3 阶段 ②：Router 注入 Bootstrap 信息

**代码位置**：`sgl-model-gateway/src/routers/http/pd_router.rs`

Router 在请求 JSON 中注入三个关键字段：

```rust
fn inject_bootstrap_into_value(mut original: Value, prefill_worker: &dyn Worker, ...) {
    obj.insert("bootstrap_host", Value::from(prefill_worker.bootstrap_host()));
    //                    → "10.0.0.3"（P3 的 IP）

    obj.insert("bootstrap_port", Value::from(prefill_worker.bootstrap_port()));
    //                    → 8998（P3 的 Bootstrap 端口）

    obj.insert("bootstrap_room", Value::from(generate_room_id()));
    //                    → 42（全局唯一房间 ID，用于 KV 传输标识）
}
```

**bootstrap_room** 是整个 PD 协同的核心标识：
- 由 Router 生成，全局唯一
- P 和 D 使用相同的 room ID 进行 KV cache 的发送和接收配对

---

### 3.4 阶段 ③：Router 双发请求（Dual Dispatch）

**代码位置**：`pd_router.rs`

```rust
async fn execute_dual_dispatch_internal(&self, json_request, prefill, decode, ...) {
    // 构造发给 P 和 D 的 HTTP 请求（携带相同的 bootstrap 信息）
    let prefill_request = self.build_post_with_headers(
        &self.client, prefill.url(), "/generate", &json_request, headers, false
    );
    let decode_request = self.build_post_with_headers(
        &self.client, decode.url(), "/generate", &json_request, headers, false
    );

    // 并发发送给 P 和 D
    let (prefill_result, decode_result) =
        tokio::join!(prefill_request.send(), decode_request.send());

    // 从 D 实例获取最终响应（流式）
    return decode_result;
}
```

**关键点**：P 和 D **同时**收到请求，但各自走不同的处理路径。

---

### 3.5 阶段 ④⑤：Prefill 端处理

**代码位置**：`disaggregation/prefill.py`

#### 4a. TokenizerManager 接收请求

```
HTTP POST /generate → TokenizerManager.generate_request()
  ├── _tokenize_one_request()：文本 → token_ids
  ├── _validate_one_request()：长度校验
  └── _send_one_request()：发送到 Scheduler 进程
```

#### 4b. Scheduler 处理

```python
# scheduler.py: handle_generate_request()
# 将请求加入 PrefillBootstrapQueue
```

#### 5a. PrefillBootstrapQueue 初始化 KV Sender

**代码位置**：`prefill.py:227-252`

```python
class PrefillBootstrapQueue:
    def add(self, req, num_kv_heads):
        # 创建 KV Sender
        kv_sender_class = get_kv_class(backend, KVClassType.SENDER)

        req.disagg_kv_sender = kv_sender_class(
            mgr=self.kv_manager,
            bootstrap_addr=f"{req.bootstrap_host}:{req.bootstrap_port}",
            #                    → "10.0.0.3:8998"（本 P 实例的 bootstrap 地址）
            bootstrap_room=req.bootstrap_room,
            #                    → 42（Router 分配的房间 ID）
            dest_tp_ranks=dest_tp_ranks,
            #                    → [0, 1, 2, ..., 7]（目标 D 实例的 TP ranks）
            pp_rank=self.pp_rank,
        )

        req.sampling_params.max_new_tokens = 1  # P 端只生成 1 个 token
        self.queue.append(req)
```

#### 5b. 执行 Prefill Forward Pass

```
Scheduler.event_loop()
  → get_next_batch_to_run()：从 waiting_queue 取出请求
  → run_batch()：执行 prefill forward pass
  → process_batch_result()：
      ├── req.output_ids.append(next_token_id)
      ├── tree_cache.cache_unfinished_req(req)
      └── send_kv_chunk(req, last_chunk=True)  ← 发送 KV cache
```

#### 6. 发送 KV Cache

**代码位置**：`prefill.py:750-828`

```python
def send_kv_chunk(self, req, last_chunk=False):
    # 获取 KV cache 的物理索引
    kv_indices = self.req_to_token_pool.req_to_token[
        req.req_pool_idx, start_idx:end_idx
    ].cpu().numpy()

    # 转换为页索引
    page_indices = kv_to_page_indices(kv_indices, page_size)

    # 通过 RDMA 发送
    req.disagg_kv_sender.send(page_indices, state_indices)
```

---

### 3.6 阶段 ⑤⑥⑦：Decode 端处理

**代码位置**：`disaggregation/decode.py`

#### 5c. TokenizerManager 接收请求

与 P 端相同路径，但 decode 模式下的 Scheduler 走不同逻辑。

#### 6a. DecodePreallocQueue 创建 KV Receiver

**代码位置**：`decode.py:241-300`

```python
class DecodePreallocQueue:
    def add(self, req, ...):
        # 创建 KV Receiver
        kv_receiver = kv_receiver_class(
            mgr=self.kv_manager,
            bootstrap_addr=f"{req.bootstrap_host}:{self.bootstrap_port}",
            #                    → "10.0.0.3:8998"（P3 的 bootstrap 地址）
            bootstrap_room=req.bootstrap_room,
            #                    → 42（与 P 端相同的房间 ID）
        )
```

#### 6b. Bootstrap 握手（D → P）

**Step 1：查询 P 的拓扑信息**

```python
# common/conn.py: try_ensure_parallel_info()
GET http://10.0.0.3:8998/route
    ?prefill_dp_rank=-1&prefill_cp_rank=-1&target_tp_rank=-1&target_pp_rank=-1

# 响应：PrefillServerInfo
{
    "attn_tp_size": 8,
    "attn_cp_size": 1,
    "dp_size": 1,
    "pp_size": 1,
    "page_size": 1,
    "kv_cache_dtype": "float16",
    "follow_bootstrap_room": false
}
```

**Step 2：获取目标 P rank 的连接信息**

```python
# common/conn.py: _setup_bootstrap_infos()
GET http://10.0.0.3:8998/route
    ?prefill_dp_rank=0&prefill_cp_rank=0&target_tp_rank=0&target_pp_rank=0

# 响应：PrefillRankInfo
{
    "rank_ip": "10.0.0.3",   # P 实例 IP
    "rank_port": 12345        # 传输引擎端口
}
```

**Step 3：D 向 P 注册 KV 参数**

```python
# mooncake/conn.py: _register_kv_args()
# 通过 ZMQ PUSH 发送到 P 实例
[
    "None",                    # 注册标记
    local_ip,                  # D 的 IP
    rank_port,                 # D 的端口
    session_id,                # Mooncake session ID
    packed_kv_data_ptrs,       # D 端 KV cache 内存指针
    packed_aux_data_ptrs,      # 辅助数据指针
    dst_tp_rank,               # D 的 TP rank
    dst_attn_tp_size,          # D 的 TP 大小
    dst_kv_item_len,           # KV item 长度
]
```

**Step 4：D 发送传输元数据**

```python
# mooncake/conn.py: send_metadata()
[
    bootstrap_room,            # 房间 ID（42）
    local_ip,
    rank_port,
    session_id,
    kv_indices,                # D 端 KV cache 目标位置
    aux_index,
    state_indices,
    required_dst_info_num,     # 期望的响应数量
]
```

#### 7a. KV Cache 传输

```
P 端                          D 端
KVSender.send()               KVReceiver
    │                              │
    ├── RDMA Write (Mooncake) ────►│─── 写入 D 的 GPU 内存
    │                              │
    ├── sync_status() ────────────►│─── 状态同步：KVPoll.Success
    │                              │
    ▼                              ▼
release_kv_cache()             poll() → KVPoll.Success
释放 P 端 KV cache             KV cache 已就位
```

#### 7b. DecodeTransferQueue 检查传输状态

**代码位置**：`decode.py:1101-1131`

```python
class DecodeTransferQueue:
    def process(self):
        polls = poll_and_all_reduce_attn_cp_tp_group(
            [decode_req.kv_receiver for decode_req in self.queue], ...
        )

        for decode_req, poll in zip(self.queue, polls):
            if poll == KVPoll.Success:
                # 传输完成，提交到 waiting_queue
                should_remove = self._commit_transfer_to_req(decode_req)

            elif poll == KVPoll.Failed:
                # 传输失败，abort 请求
                prepare_abort(decode_req.req, error_message, ...)
                release_kv_cache(decode_req.req, self.tree_cache, is_insert=False)
```

#### 8. Decode Forward Pass

```
请求从 TransferQueue → waiting_queue → running_batch
  │
  ├── 构建 PrebuiltExtendBatch（跳过 prefill，直接使用已传输的 KV cache）
  ├── 执行 decode forward pass（逐 token 生成）
  └── 流式返回结果给 TokenizerManager
```

#### 9. 响应返回客户端

```
Decode Scheduler
  → stream_output() → TokenizerManager
  → HTTP SSE Response → Router → Client
```

---

## 四、参数级别详细对照表

### 4.1 启动参数（三进程共用）

| 参数 | 默认值 | P 设置 | D 设置 | Router 设置 | 说明 |
|------|--------|--------|--------|-------------|------|
| `--model-path` | 必填 | 相同模型 | 相同模型 | — | 模型路径，P/D 必须一致 |
| `--host` | `127.0.0.1` | `0.0.0.0` | `0.0.0.0` | `0.0.0.0` | 监听地址 |
| `--port` | `30000` | `30000` | `30001` | `8000` | 服务端口 |
| `--tp-size` | `1` | `8` | `8` | — | Tensor Parallel 大小 |
| `--dp-size` | `1` | `1` | `1` | — | Data Parallel 大小 |
| `--mem-fraction-static` | `0.9` | `0.85` | `0.85` | — | GPU 内存用于 KV cache 的比例 |

### 4.2 Disaggregation 参数

| 参数 | 默认值 | P 设置 | D 设置 | 说明 |
|------|--------|--------|--------|------|
| `--disaggregation-mode` | `null` | **`prefill`** | **`decode`** | 进程角色 |
| `--disaggregation-transfer-backend` | `mooncake` | `mooncake` | `mooncake` | KV 传输后端 |
| `--disaggregation-bootstrap-port` | `8998` | `8998` | — | Bootstrap 服务端口（仅 P） |
| `--disaggregation-ib-device` | `None` | `mlx5_0` | `mlx5_0` | RDMA 网卡设备 |
| `--disaggregation-decode-enable-offload-kvcache` | `False` | — | `True`（可选） | D 端 KV cache 卸载 |

### 4.3 Router 参数

| 参数 | 说明 |
|------|------|
| `--pd-disaggregation` | 启用 PD 分离路由模式 |
| `--prefill URL [BOOTSTRAP_PORT]` | P 实例 URL + bootstrap 端口（可多次指定） |
| `--decode URL` | D 实例 URL（可多次指定） |
| `--prefill-policy` | P 选择策略：`random`/`cache_aware`/`round_robin`/`power_of_two`/`prefix_hash` |
| `--decode-policy` | D 选择策略 |
| `--host` / `--port` | Router 对外服务地址 |

### 4.4 环境变量

| 环境变量 | 默认值 | 作用于 | 说明 |
|---------|--------|--------|------|
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | `300s` | P 端 | Bootstrap 阶段超时 |
| `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | `300s` | D 端 | 等待 KV 传输超时 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | `5s`（min 2s） | D 端 | 心跳检查间隔 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | `2` | D 端 | 最大心跳失败次数 |
| `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` | CPU 数 | P 端 | 传输线程池大小 |
| `SGLANG_DISAGGREGATION_QUEUE_SIZE` | `4` | P 端 | 并行传输队列数 |
| `SGLANG_DISAGG_STAGING_BUFFER` | `0` | P+D | 异构 TP 场景的 staging buffer |
| `SGLANG_DISAGG_STAGING_BUFFER_SIZE_MB` | `64` | P+D | 每个 queue 的 staging buffer 大小 |
| `SGLANG_DISAGG_STAGING_POOL_SIZE_MB` | `4096` | P+D | staging buffer 总池大小 |

---

## 五、Bootstrap Room 协同协议详解

`bootstrap_room` 是 P 和 D 协同的**核心标识**，贯穿整个请求生命周期。

### 5.1 Room 的生命周期

```
Router 生成 room=42
  │
  ├── 注入到请求 JSON → 同时发给 P 和 D
  │
  ├── P 端：
  │     PrefillBootstrapQueue.add(req)
  │       → KVSender(bootstrap_room=42)
  │       → 注册到 BootstrapServer: room 42 → (dp_rank=0, tp_rank=0)
  │
  ├── D 端：
  │     DecodePreallocQueue.add(req)
  │       → KVReceiver(bootstrap_room=42)
  │       → 查询 BootstrapServer: room 42 → 找到 P 的连接信息
  │
  ├── KV 传输：
  │     P: KVSender.send(kv_indices, room=42) ──RDMA──► D: KVReceiver.recv(room=42)
  │     P: sync_status(room=42, KVPoll.Success) ──────► D: poll() → KVPoll.Success
  │
  └── 完成：
        P: release_kv_cache(req)  → 释放 KV cache
        D: _commit_transfer_to_req()  → KV cache 已就位，开始 decode
```

### 5.2 多 TP Rank 的 Room 协调

当 TP > 1 时（如 TP=8），一个请求需要**多个 TP rank 协同传输**：

```
bootstrap_room=42 的请求：
  P rank 0 ──→ D rank 0  (传输 head 0~3 的 KV)
  P rank 1 ──→ D rank 1  (传输 head 4~7 的 KV)
  ...
  P rank 7 ──→ D rank 7  (传输 head 28~31 的 KV)

D 端汇总：
  required_prefill_response_num[42] = 8  # 期望收到 8 个 rank 的响应
  prefill_response_tracker[42] = {0, 1, 2, 3, 4, 5, 6, 7}

  当 8 个 rank 全部 Success → KVPoll.Success
```

---

## 六、D 端请求生命周期状态机

**代码位置**：`disaggregation/decode.py:1-18`

```
请求到达 D 端
  │
  ▼
PreallocQueue（预分配阶段）
  │  a. 创建 KVReceiver
  │  b. Bootstrap 握手（查询 P 的拓扑信息）
  │  c. 预分配 KV cache 空间
  │
  ▼
TransferQueue（传输阶段）
  │  a. poll() 轮询 KV 传输状态
  │  b. 传输完成 → 提交到 waiting_queue
  │  c. 传输失败 → abort + release_kv_cache
  │
  ▼
WaitingQueue（等待调度）
  │  构建 PrebuiltExtendBatch（跳过 prefill forward）
  │
  ▼
RunningBatch（Decode 执行）
  │  a. 合并到 running batch
  │  b. 逐 token decode
  │  c. 流式返回结果
  │
  ▼
Finished
   release_kv_cache() → 释放资源
```

---

## 七、P 端请求生命周期状态机

```
请求到达 P 端
  │
  ▼
TokenizerManager
  │  tokenize + validate
  │
  ▼
Scheduler.waiting_queue
  │  schedule_policy.calc_priority() 排序
  │
  ▼
PrefillBootstrapQueue
  │  创建 KVSender，max_new_tokens=1
  │
  ▼
RunningBatch（Prefill Forward）
  │  执行 prefill forward pass
  │
  ▼
process_batch_result_disagg_prefill()
  │  send_kv_chunk(req, last_chunk=True)
  │
  ▼
InflightQueue（等待传输完成）
  │  poll() 轮询传输状态
  │  KVPoll.Success → release_kv_cache()
  │
  ▼
Finished
   流式返回 1 个 token 给 TokenizerManager
   P 端释放 KV cache（已传输给 D）
```

---

## 八、时间指标采集点

整个请求链路中，SGLang 在各阶段采集时间指标：

```
Client 发送请求
  │
  ├──① created_time                     请求创建
  │
  ├──② tokenize_finish_time             Tokenize 完成
  │
  ├──③ bootstrap_done_time              D 端 Bootstrap 握手完成
  │
  ├──④ prefill_finished_time            P 端 Prefill Forward 完成
  │
  ├──⑤ prefill_kv_transfer_finish_time  KV Cache 传输完成
  │
  ├──⑥ first_token_time                 第一个 decode token 生成
  │
  ├──⑦ completion_time                  请求完成
  │
  └──⑧ response_sent_to_client_time     响应发送给客户端
```

---

## 九、错误处理与容错

### 9.1 Router 层

| 错误场景 | Router 行为 |
|---------|------------|
| P 实例 /health 返回非 200 | 标记为不健康，不再路由请求到该 P |
| D 实例 /health 返回非 200 | 标记为不健康，不再路由请求到该 D |
| 所有 P/D 都不健康 | 返回 503 Service Unavailable |
| P 或 D 请求超时 | 重试或返回错误 |

### 9.2 D 端 P 节点故障检测

| 检测机制 | 间隔 | 失败阈值 | 触发动作 |
|---------|------|---------|---------|
| HTTP heartbeat | 5s（min 2s） | 2 次 | `_handle_node_failure()`：标记所有关联请求 Failed |
| Bootstrap 超时 | 300s | — | `KVPoll.Failed` |
| Waiting 超时 | 300s | — | `KVPoll.Failed` |

### 9.3 KV 传输失败处理

```
传输失败
  │
  ├── P 端：sync_status(room, KVPoll.Failed)
  ├── D 端：poll() → KVPoll.Failed
  │     ├── prepare_abort(req, error_message)
  │     ├── release_kv_cache(req, tree_cache, is_insert=False)
  │     └── stream_output() → 返回错误给客户端
  └── 清理：释放所有已分配的资源
```

---

## 十、7P1D 部署方案完整示例

### 10.1 Prefill 实例（×7）

```bash
# P1
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode prefill \
  --host 10.0.0.1 --port 30000 \
  --tp-size 8 \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_0 \
  --mem-fraction-static 0.85 \
  --schedule-policy lpm

# P2 ~ P7：修改 --host 为各自 IP，其余相同
```

### 10.2 Decode 实例（×1）

```bash
# D1
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode decode \
  --host 10.0.0.8 --port 30000 \
  --tp-size 8 \
  --max-running-requests 128 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_0 \
  --mem-fraction-static 0.85
```

### 10.3 Router

```bash
python -m sglang_router.launch_router \
  --pd-disaggregation \
  --host 0.0.0.0 --port 8000 \
  --prefill http://10.0.0.1:30000 8998 \
  --prefill http://10.0.0.2:30000 8998 \
  --prefill http://10.0.0.3:30000 8998 \
  --prefill http://10.0.0.4:30000 8998 \
  --prefill http://10.0.0.5:30000 8998 \
  --prefill http://10.0.0.6:30000 8998 \
  --prefill http://10.0.0.7:30000 8998 \
  --decode http://10.0.0.8:30000 \
  --prefill-policy cache_aware \
  --decode-policy random
```

### 10.4 请求流量路径（以 P3 被选中为例）

```
Client POST /generate {"text": "请介绍一下SGLang", "max_new_tokens": 512}
  │
  ▼
Router:8000
  ├── 选择 P3（10.0.0.3）+ D1（10.0.0.8）
  ├── 注入 bootstrap_host="10.0.0.3", bootstrap_port=8998, bootstrap_room=42
  ├── 并发 POST → P3:30000/generate + D1:30000/generate
  │
  ├── P3 处理：
  │     Tokenize → [15496, 312, ...]（~10 tokens）
  │     Prefill Forward（1 层 pass，生成 1 个 token）
  │     KVSender.send(kv_indices, room=42)
  │       → RDMA Write → D1 的 GPU 内存
  │     sync_status(room=42, Success) → D1
  │     release_kv_cache() → P3 释放 KV
  │
  └── D1 处理：
        KVReceiver(bootstrap_room=42)
          → BootstrapServer 查询 P3 拓扑
          → ZMQ 注册 + 发送 metadata
          → poll() → 等待 KV 传输
          → KVPoll.Success → KV cache 就位
        构建 PrebuiltExtendBatch（跳过 prefill）
        Decode Forward（逐 token 生成，512 个 token）
        SSE Stream → Router → Client
```

---

## 十一、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `launch_server.py` | 15-47 | 启动入口，模式路由 |
| `srt/entrypoints/http_server.py` | 701-735 | HTTP 请求入口 |
| `srt/entrypoints/engine.py` | — | Engine 初始化 |
| `srt/server_args.py` | 697-699 | disaggregation 启动参数 |
| `srt/managers/tokenizer_manager.py` | 444, 515-560 | TM 初始化 + 请求处理 |
| `srt/managers/disagg_service.py` | 14-44 | BootstrapServer 启动 |
| `srt/disaggregation/prefill.py` | 87-200 | PrefillBootstrapQueue |
| `srt/disaggregation/prefill.py` | 468-588 | prefill 结果处理 |
| `srt/disaggregation/prefill.py` | 589-702 | inflight queue 处理 |
| `srt/disaggregation/prefill.py` | 750-828 | send_kv_chunk |
| `srt/disaggregation/decode.py` | 1-18 | D 端请求生命周期注释 |
| `srt/disaggregation/decode.py` | 241-300 | DecodePreallocQueue |
| `srt/disaggregation/decode.py` | 540-562 | Bootstrap 握手 poll |
| `srt/disaggregation/decode.py` | 1101-1131 | TransferQueue 处理 |
| `srt/disaggregation/common/conn.py` | 709-962 | BootstrapServer 实现 |
| `srt/disaggregation/mooncake/conn.py` | 1490-1493 | KVPoll.Failed 处理 |
| `srt/disaggregation/mooncake/conn.py` | 1497-1549 | heartbeat_checker |
| `srt/disaggregation/mooncake/conn.py` | 1602-1631 | _handle_node_failure |
| `srt/disaggregation/base/conn.py` | 42-47 | KVPoll 枚举 |
| `sgl-model-gateway/src/routers/http/pd_router.rs` | — | Router PD 路由（Rust） |
| `sglang_router/launch_router.py` | — | Router Python 启动器 |
| `sglang_router/router_args.py` | 21-155 | Router 参数定义 |

---

## 十二、总结

| 维度 | Prefill (P) | Decode (D) | Router |
|------|-------------|------------|--------|
| **角色** | Tokenize + Prefill Forward | Decode Forward + 返回结果 | 请求路由 + 双发 |
| **启动参数** | `--disaggregation-mode prefill` | `--disaggregation-mode decode` | `--pd-disaggregation` |
| **Bootstrap** | 启动 BootstrapServer（:8998） | 查询 P 的 BootstrapServer | 不参与 |
| **KV Cache** | 生成后通过 RDMA 发送给 D | 接收 P 传输的 KV cache | 不参与 |
| **调度策略** | `--schedule-policy`（LPM/FCFS/...） | retract 机制 | `--prefill-policy`/`--decode-policy` |
| **请求结果** | 生成 1 个 token + 传输 KV | 生成全部 token + 流式返回 | 转发 D 的响应给客户端 |
| **内存管理** | 传输完成后释放 KV cache | decode 完成后释放 KV cache | 无状态 |
| **容错** | 进程崩溃 → D 端 heartbeat 检测 | 传输失败 → abort + release | 健康检查 + 熔断 |
