# SGLang Router Server 转发请求到 Decode 节点失败场景分析

> 分析日期：2026-04-21
> 分析对象：SGLang 源码 sgl-project/sglang
> 分析范围：Router Server 双发机制、断路器、健康检查、重试逻辑、Decode 节点内部失败路径
> 部署场景：GLM-4.7-Flash-30B-A3B 7P1D PD 分离部署

---

## 一、结论

**是的，存在多种导致 Router 偶发转发请求到 Decode 失败的场景。** 最可能的根因是断路器误触发（级联故障）、健康检查滞后（60 秒盲区）和网络瞬时抖动。

---

## 二、架构概述：Router 双发（Dual Dispatch）机制

SGLang PD 分离架构中，Router 的核心机制是**双发（Dual Dispatch）**——同时将同一个请求发送给 Prefill 和 Decode 节点：

```rust
// sgl-model-gateway/src/routers/http/pd_router.rs:579-580
let (prefill_result, decode_result) =
    tokio::join!(prefill_request.send(), decode_request.send());
```

使用 `tokio::join!` 并发发送，等待两个都完成后处理结果。

### 关键流程

```
客户端请求
  │
  ▼
Router.execute_dual_dispatch()
  │
  ├── 1. RetryExecutor 包装（最多 5 次重试）
  │     │
  │     ├── 2. select_pd_pair() 选择 P/D 节点对
  │     │     │
  │     │     ├── 检查 is_available()（健康 + 断路器）
  │     │     └── 按 Policy 选择具体节点
  │     │
  │     ├── 3. inject_bootstrap_into_value() 注入 bootstrap 元数据
  │     │     ├── bootstrap_host
  │     │     ├── bootstrap_port
  │     │     └── bootstrap_room
  │     │
  │     └── 4. execute_dual_dispatch_internal()
  │           │
  │           ├── tokio::join!(prefill.send(), decode.send()) ← 并发双发
  │           │
  │           ├── 先检查 decode_result
  │           │     ├── OK → 处理 prefill_result（合并 logprobs）
  │           │     └── Err → 返回 502 Bad Gateway
  │           │
  │           └── record_outcome() 更新断路器状态
  │                 ├── prefill.record_outcome(not_error)
  │                 └── decode.record_outcome(not_error)
  │
  ▼
客户端响应
```

### 关键配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_retries` | 5 | 最大重试次数 |
| `initial_backoff_ms` | 50 | 初始退避时间 |
| `max_backoff_ms` | 30000 | 最大退避时间 |
| `backoff_multiplier` | 1.5 | 退避乘数 |
| `jitter_factor` | 0.2 | 抖动因子（±20%） |
| `failure_threshold` | 10 | 断路器打开的连续失败次数 |
| `timeout_duration_secs` | 60 | 断路器打开后的冷却时间 |
| `success_threshold` | 3 | 断路器关闭需要的连续成功次数 |
| `check_interval_secs` | 60 | 健康检查间隔 |
| `health_failure_threshold` | 3 | 标记不健康的连续失败次数 |

---

## 三、Router 层面失败场景（6 类）

### 场景 1：Decode 节点网络不可达

**代码位置**：`sgl-model-gateway/src/routers/http/pd_router.rs:685-692`

```rust
Err(e) => {
    error!(
        decode_url = %decode.url(),
        error = %e,
        "Decode request failed"
    );
    error::bad_gateway("decode_server_error", format!("Decode server error: {}", e))
}
```

**触发条件**：
- Decode 进程崩溃（OOM、CUDA Error）
- 网络分区
- DNS 解析失败
- 防火墙阻断连接

**影响**：Router 直接返回 502 Bad Gateway，Prefill 的 GPU 计算资源被浪费

**偶发概率**：中等 — 网络抖动或 Decode 节点负载过高时偶发

---

### 场景 2：断路器（Circuit Breaker）级联打开（最严重）

**代码位置**：`sgl-model-gateway/src/routers/http/pd_router.rs:355-358` + `sgl-model-gateway/src/core/circuit_breaker.rs`

```rust
// pd_router.rs:355-358 — 关键问题：双发结果同时惩罚两个节点
let status = response.status();
let not_error = status.is_success() || status.is_client_error();
prefill.record_outcome(not_error);   // ← Prefill 也被惩罚
decode.record_outcome(not_error);    // ← Decode 也被惩罚
```

**断路器状态机**：

```
                    10次连续失败
  Closed ──────────────────────→ Open
    ↑                              │
    │  3次连续成功                  │ 60秒冷却
    │                              ↓
    └──────── HalfOpen ←──────────┘
                  │
                  │ 1次失败
                  ↓
                Open（重新计时）
```

**级联故障过程**：

```
T=0   Decode 偶发 503（如 GC、调度延迟）
T=1   断路器记录 failure_count += 1
      ↓
      同时 Prefill 断路器也记录 failure_count += 1（连坐惩罚）
      ↓
T=N   累积 10 次失败
      ↓
      Decode 断路器 → Open（拒绝所有请求 60 秒）
      Prefill 断路器 → 也趋向 Open
      ↓
      后续所有请求失败：
      "No available decode workers (all circuits open or unhealthy)"
```

**断路器默认配置**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | 10 | 连续失败次数阈值 |
| `timeout_duration_secs` | 60 | Open 状态冷却时间 |
| `success_threshold` | 3 | HalfOpen→Closed 需要的成功次数 |

**偶发概率**：**高 — 这是最可能的生产环境偶发故障根因**

**特别说明**：HalfOpen 状态下，**1 次失败就立即回到 Open**，在网络不稳定时可能导致断路器永远无法恢复（在 Open ↔ HalfOpen 之间反复跳动）。

---

### 场景 3：健康检查状态滞后（最长 60 秒盲区）

**代码位置**：`sgl-model-gateway/src/config/types.rs:410-421` + `sgl-model-gateway/src/core/worker.rs:891-921`

```rust
// 健康检查配置
HealthCheckConfig {
    failure_threshold: 3,
    success_threshold: 2,
    timeout_secs: 5,
    check_interval_secs: 60,   // ← 每 60 秒才检查一次
}
```

```rust
// worker.rs — 健康状态缓存检查
fn is_available(&self) -> bool {
    self.is_healthy() && self.circuit_breaker().can_execute()
}
```

**时间线**：

```
T=0s  : 健康检查通过，Decode 标记为健康
T=30s : Decode 进程 OOM 崩溃
T=30s ──── T=90s : Router 仍然认为 Decode 健康，持续路由请求
                   → 每个请求都失败（502/503）
                   → 断路器记录失败
T=90s : 下一次健康检查失败，标记不健康
```

**影响**：
- 最长 60 秒的窗口内，Router 会将请求路由到已经崩溃的 Decode 节点
- 这些失败请求会累积到断路器计数器，可能导致断路器打开
- **健康检查失败 + 断路器打开 = 双重故障**

**偶发概率**：**高 — Decode 节点异常重启时必然出现**

---

### 场景 4：重试耗尽后仍选择相同故障节点

**代码位置**：`sgl-model-gateway/src/core/retry.rs:85-129` + `sgl-model-gateway/src/routers/http/pd_router.rs:301-391`

```rust
// retry.rs — 重试循环
loop {
    let response = operation(attempt).await;
    let is_last = attempt + 1 >= max;

    if !should_retry(&response, attempt) {
        return response;
    }

    if is_last {
        on_exhausted();
        return response;
    }

    let delay = BackoffCalculator::calculate_delay(config, attempt);
    tokio::time::sleep(delay).await;
    attempt = next_attempt;
}
```

```rust
// pd_router.rs — 重试时重新选择节点
let (prefill, decode) = match self.select_pd_pair(...).await {
    Ok(pair) => pair,
    Err(e) => return Self::handle_server_selection_error(e),
};
```

**重试退避序列**（带 ±20% jitter）：

| 重试次数 | 延迟 | 累计时间 |
|---------|------|---------|
| 第 1 次 | 0ms | 0ms |
| 第 2 次 | 40-60ms | ~50ms |
| 第 3 次 | 60-90ms | ~140ms |
| 第 4 次 | 90-135ms | ~275ms |
| 第 5 次 | 135-202ms | ~475ms |

**可重试状态码**（`retry.rs:10-20`）：

```rust
pub fn is_retryable_status(status: StatusCode) -> bool {
    matches!(
        status,
        StatusCode::REQUEST_TIMEOUT          // 408
            | StatusCode::TOO_MANY_REQUESTS  // 429
            | StatusCode::INTERNAL_SERVER_ERROR  // 500
            | StatusCode::BAD_GATEWAY        // 502
            | StatusCode::SERVICE_UNAVAILABLE  // 503
            | StatusCode::GATEWAY_TIMEOUT    // 504
    )
}
```

**问题**：重试时虽然重新 `select_pd_pair()`，但在以下情况下可能选到同一故障节点：
- 断路器还未打开（需要 10 次失败）
- 健康检查还未发现（需要等下一个 60 秒周期）
- 只有 1 个 Decode 节点（7P1D 场景）

**偶发概率**：中等

---

### 场景 5：请求超时未针对 PD 模式显式配置

**代码位置**：`sgl-model-gateway/src/core/worker.rs:35-40`

```rust
static WORKER_CLIENT: LazyLock<reqwest::Client> = LazyLock::new(|| {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(DEFAULT_WORKER_HTTP_TIMEOUT_SECS)) // 30 seconds
        .build()
        .expect("Failed to create worker HTTP client")
});
```

对于 PD 模式，Decode 节点需要等待 Prefill 完成计算 + KV cache 传输后才能开始 decode，30 秒的 HTTP 超时在长序列场景下可能不够。

**偶发概率**：低 — 仅在长序列 + 网络慢时出现

---

### 场景 6：双发部分失败（Prefill 成功 + Decode 失败）

**代码位置**：`sgl-model-gateway/src/routers/http/pd_router.rs:579-620`

```rust
let (prefill_result, decode_result) = tokio::join!(...);

// 先检查 decode 结果
match decode_result {
    Ok(res) => {
        // Decode 成功 → 处理 prefill 结果（合并 logprobs）
    }
    Err(e) => {
        // Decode 失败 → 直接返回错误
        // Prefill 可能已经成功完成，GPU 计算资源被浪费
        error::bad_gateway("decode_server_error", format!("Decode server error: {}", e))
    }
}
```

**核心问题**：
1. `tokio::join!` 等待两个都完成后才处理
2. 处理逻辑**先检查 Decode 结果**
3. 如果 Decode 连接失败，Prefill 的计算结果被直接丢弃
4. **没有取消机制** — 当一方失败时，不会取消另一方的请求

**偶发概率**：**高 — 任何单次 Decode 请求失败都会触发**

---

## 四、Decode 节点内部失败场景（7 类）

即使 Router 成功将请求发到 Decode 节点，Decode 内部处理也可能失败：

### 场景 7：Bootstrap 握手失败

**代码位置**：`python/sglang/srt/disaggregation/decode.py:547-562`

```python
elif poll == KVPoll.Failed:
    error_message = f"Decode handshake failed for request rank={self.tp_rank} {decode_req.req.rid=}"
    try:
        decode_req.kv_receiver.failure_exception()
    except Exception as e:
        error_message += f" with exception {e}"
    logger.error(error_message)
    prepare_abort(
        decode_req.req, error_message,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    if self.scheduler.enable_metrics:
        self.scheduler.metrics_collector.increment_bootstrap_failed_reqs()
```

**触发**：Decode 与 Prefill 的 bootstrap 握手超时或被拒绝
**KVPoll 状态机**：`Bootstrapping → Failed`
**影响**：请求在 Decode 端被 abort

---

### 场景 8：Prefill 拓扑信息获取超时

**代码位置**：`python/sglang/srt/disaggregation/decode.py:594-600`

```python
if count >= self._max_ensure_retries:  # 15 次
    error_msg = f"Could not fetch prefill parallel info from {bootstrap_addr} after {count} attempts"
    logger.error(error_msg)
    for decode_req in reqs:
        decode_req.kv_receiver.abort()
    del self._ensure_retry_count[bootstrap_addr]
```

**配置**：
- `_max_ensure_retries` = 15 次（`decode.py:292`）
- `_ensure_retry_interval` = 1.0 秒（`decode.py:294`）

**触发**：Decode 无法获取 Prefill 的拓扑信息（TP/CP/PP 配置）
**影响**：该 bootstrap_addr 下的**所有请求全部 abort**

---

### 场景 9：KV Cache 传输超时

**代码位置**：`python/sglang/srt/disaggregation/mooncake/conn.py:1878-1888`

```python
if elapsed >= self.kv_mgr.waiting_timeout:  # 默认 300 秒
    logger.warning_once(
        "Some requests fail to receive KV Cache transfer done signal after bootstrapping. "
        "If a greater mean TTFT is acceptable, you can 'export SGLANG_DISAGGREGATION_WAITING_TIMEOUT=600' "
        "to relax the timeout condition."
    )
    self.kv_mgr.record_failure(
        self.bootstrap_room,
        f"Request {self.bootstrap_room} timed out after {elapsed:.1f}s in KVPoll.WaitingForInput",
    )
    self.conclude_state = KVPoll.Failed
```

**所有后端均实现此超时**：

| 后端 | 代码位置 |
|------|---------|
| Mooncake | `mooncake/conn.py:1878` |
| Mori | `mori/conn.py:1076` |
| NIXL | `nixl/conn.py:1172` |

**配置**：`SGLANG_DISAGGREGATION_WAITING_TIMEOUT`，默认 300 秒

**触发**：Prefill 完成 KV cache 计算后，传输到 Decode 超时
**影响**：Decode 标记请求失败

---

### 场景 10：KV Cache 传输引擎失败

**代码位置**：`python/sglang/srt/disaggregation/decode.py:1106-1128`

```python
if poll == KVPoll.Failed:
    error_message = f"Decode transfer failed for request rank={self.tp_rank}"
    try:
        decode_req.kv_receiver.failure_exception()
    except Exception as e:
        error_message += f" with exception {e}"
    logger.error(error_message)
    prepare_abort(
        decode_req.req, error_message,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    self.scheduler.stream_output([decode_req.req], decode_req.req.return_logprob)
    release_kv_cache(decode_req.req, self.tree_cache, is_insert=False)
    if self.scheduler.enable_metrics:
        self.scheduler.metrics_collector.increment_transfer_failed_reqs()
```

**触发**：RDMA 传输失败、硬件错误
**影响**：请求 abort，KV cache 资源释放

**Prefill 端的传输失败**（`mooncake/conn.py:1275-1283`）：

```python
if ret != 0:
    with self.session_lock:
        self.session_failures[req.mooncake_session_id] += 1
        if self.session_failures[req.mooncake_session_id] >= 1:
            self.failed_sessions.add(req.mooncake_session_id)
            logger.error(f"Session {req.mooncake_session_id} failed.")
    self.record_failure(kv_chunk.room, f"Failed to send kv chunk...")
    self.update_status(kv_chunk.room, KVPoll.Failed)
```

**注意**：Session 级别的失败是**永久性的** — 一旦某个 Decode session 失败 1 次，所有后续使用该 session 的请求都会立即失败。

---

### 场景 11：Metadata 损坏检测

**代码位置**：`python/sglang/srt/disaggregation/decode.py:1023-1041`

```python
elif actual_room != expected_room:
    error_msg = (
        f"Context corruption detected: Request {decode_req.req.rid} "
        f"(bootstrap_room={expected_room}) received metadata from "
        f"bootstrap_room={actual_room}. "
        f"Metadata buffer index: {idx}. "
        f"This indicates metadata buffer index collision."
    )
    logger.error(error_msg)
    prepare_abort(
        decode_req.req, "Metadata corruption detected - bootstrap_room mismatch",
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
    )
```

**触发**：Bootstrap room 不匹配，表明 metadata buffer 索引冲突
**影响**：请求 abort

---

### 场景 12：DP Rank 路由冲突

**代码位置**：`python/sglang/srt/disaggregation/common/conn.py:457-467`

```python
if self.kv_mgr.attn_dp_rank != self.bootstrap_room % self.kv_mgr.server_args.dp_size:
    if envs.SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK.get():
        self._register_prefill_dp_rank()
    else:
        self.kv_mgr.record_failure(
            self.bootstrap_room,
            f"follow_bootstrap_room conflict: dispatched to dp_rank "
            f"{self.kv_mgr.attn_dp_rank} but bootstrap_room "
            f"{self.bootstrap_room} implies dp_rank "
            f"{self.bootstrap_room % self.kv_mgr.server_args.dp_size}."
        )
        self.kv_mgr.update_status(self.bootstrap_room, KVPoll.Failed)
```

**触发**：Router 将请求路由到错误的 DP rank
**影响**：请求直接标记为 Failed（除非设置了 `SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK=1`）

---

### 场景 13：Prefill 节点在 Bootstrap 后崩溃

**代码位置**：`python/sglang/srt/disaggregation/common/conn.py:535-542`

```python
if self.bootstrap_addr not in self.kv_mgr.prefill_info_table:
    self.kv_mgr.record_failure(
        self.bootstrap_room,
        f"Prefill server with bootstrap_addr: {self.bootstrap_addr} is healthy before, "
        f"but now it is down. Request (bootstrap_room: {self.bootstrap_room}) has been marked as failed."
    )
    self.conclude_state = KVPoll.Failed
    self.kv_mgr.update_status(self.bootstrap_room, KVPoll.Failed)
```

**触发**：Prefill 节点在 bootstrap 握手成功后崩溃
**影响**：请求立即标记为 Failed

---

## 五、KVPoll 状态机完整路径

```
                     Router 双发
                         │
          ┌──────────────┼──────────────┐
          ▼              │              ▼
     Prefill 进程        │         Decode 进程
          │              │              │
    Prefill KVSender     │      Decode KVReceiver
          │              │              │
    ┌─────┴─────┐        │       ┌──────┴──────┐
    │Bootstrapping│       │       │Bootstrapping │
    └─────┬─────┘        │       └──────┬──────┘
          │              │              │
    ┌─────┴─────┐        │       ┌──────┴──────┐
    │WaitingForInput│     │       │WaitingForInput│
    └─────┬─────┘        │       └──────┬──────┘
          │              │              │
    ┌─────┴─────┐   RDMA Transfer  ┌──────┴──────┐
    │Transferring│ ───────────────→ │Transferring │
    └─────┬─────┘                   └──────┬──────┘
          │                                │
    ┌─────┴─────┐                    ┌──────┴──────┐
    │  Success  │                    │   Success   │
    └───────────┘                    └──────┬──────┘
                                            │
                                     ┌──────┴──────┐
                                     │  Decode处理  │
                                     └─────────────┘

    任何阶段都可能 → Failed（超时、传输错误、节点崩溃等）
```

**每个阶段的超时配置**：

| 阶段 | 超时配置 | 默认值 |
|------|---------|--------|
| Bootstrapping | `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | 300 秒 |
| WaitingForInput | `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | 300 秒 |
| Transferring | 无独立超时 | — |
| 心跳检测 | `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 5 秒 |
| 心跳失败阈值 | `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 2 次 |

---

## 六、关键风险矩阵

| 风险等级 | 场景编号 | 场景名称 | 偶发概率 | 根因 | 影响 |
|---------|---------|---------|---------|------|------|
| **P0 严重** | 2 | 断路器级联打开 | **高** | 双发模式下 Prefill 被 Decode 失败连坐 | 所有请求被拒绝 60 秒 |
| **P0 严重** | 3 | 健康检查滞后 60 秒 | **高** | 检查间隔太长 | 路由到已崩溃节点 |
| **P1 中等** | 6 | 双发部分失败 | **高** | 无请求取消机制 | GPU 资源浪费 |
| **P1 中等** | 4 | 重试选到同一故障节点 | **中** | 断路器未打开前无法区分 | 重试全部失败 |
| **P1 中等** | 9 | KV 传输超时 | **中** | 网络或硬件抖动 | 请求失败 |
| **P2 低** | 1 | Decode 网络不可达 | **中** | 进程崩溃/网络问题 | 单次请求失败 |
| **P2 低** | 7 | Bootstrap 握手失败 | **低** | 网络条件差 | 单次请求失败 |
| **P2 低** | 10 | 传输引擎失败 | **低** | RDMA 硬件错误 | Session 级永久失败 |
| **P3 极低** | 11 | Metadata 损坏 | **极低** | 网络传输错误 | 单次请求失败 |
| **P3 极低** | 12 | DP Rank 路由冲突 | **极低** | Router 路由逻辑错误 | 单次请求失败 |

---

## 七、最可能的"偶发失败"根因排序

### 根因 1：断路器误触发（概率最高）

```
现象：每隔一段时间，所有请求突然失败 60 秒，然后自动恢复
原因：Decode 偶发响应慢（如 GC、调度延迟、批量请求排队）
      → 偶发 503/504
      → 断路器计数器累加
      → 10 次后断路器打开
      → 所有请求被拒绝 60 秒
      → HalfOpen 时又偶发失败 → 重新 Open
      → 循环
```

**影响范围**：**所有请求**（不仅是触发失败的那个请求）

### 根因 2：健康检查盲区

```
现象：Decode 节点重启后，约 30-60 秒内请求持续失败
原因：健康检查间隔 60 秒
      → 节点崩溃后，状态未及时更新
      → Router 持续路由到已崩溃节点
      → 失败请求加速断路器打开
```

### 根因 3：7P1D 单点风险

```
现象：Decode 节点任何异常都影响全部请求
原因：7P1D 部署中只有 1 个 Decode 节点
      → 无备用节点
      → 断路器打开 = 完全不可用
      → 重试无意义（没有其他 Decode 节点可选）
```

---

## 八、生产环境缓解建议

### 8.1 断路器优化

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `failure_threshold` | 20-30 | 避免偶发失败导致断路器打开 |
| `timeout_duration_secs` | 30 | 缩短冷却时间 |
| `success_threshold` | 2 | 加快恢复速度 |
| `disable_circuit_breaker` | 考虑在 7P1D 场景下禁用 | 只有 1 个 D 节点，断路器无意义 |

### 8.2 健康检查优化

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `check_interval_secs` | 10-15 | 缩短检测盲区 |
| `failure_threshold` | 2 | 更快发现故障 |

### 8.3 重试优化

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `max_retries` | 3 | 7P1D 场景下多重重试无意义 |
| `initial_backoff_ms` | 100 | 给 Decode 更多恢复时间 |

### 8.4 Decode 节点优化

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | 600 | 避免长序列传输超时 |
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | 600 | 避免握手超时 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 3 | 更快发现 Prefill 节点异常 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 3 | 避免偶发心跳失败误判 |

---

## 九、关键源码文件索引

### Router 层（Rust）

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `sgl-model-gateway/src/routers/http/pd_router.rs` | 277-416 | `execute_dual_dispatch()` 重试包装 |
| `sgl-model-gateway/src/routers/http/pd_router.rs` | 533-694 | `execute_dual_dispatch_internal()` 核心双发逻辑 |
| `sgl-model-gateway/src/routers/http/pd_router.rs` | 355-358 | `record_outcome()` 断路器连坐问题 |
| `sgl-model-gateway/src/routers/http/pd_router.rs` | 783-828 | `pick_worker_by_policy_arc()` 节点选择 |
| `sgl-model-gateway/src/core/circuit_breaker.rs` | 147-155 | 断路器状态检查 |
| `sgl-model-gateway/src/core/circuit_breaker.rs` | 254-256 | HalfOpen 单次失败立即回到 Open |
| `sgl-model-gateway/src/core/retry.rs` | 10-20 | 可重试状态码定义 |
| `sgl-model-gateway/src/core/retry.rs` | 85-129 | 重试执行器 |
| `sgl-model-gateway/src/core/worker.rs` | 891-921 | 健康检查实现 |
| `sgl-model-gateway/src/config/types.rs` | 371-440 | 重试/断路器/健康检查配置 |

### Decode 层（Python）

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `python/sglang/srt/disaggregation/decode.py` | 547-562 | Bootstrap 握手失败处理 |
| `python/sglang/srt/disaggregation/decode.py` | 594-600 | Prefill 拓扑信息获取超时 |
| `python/sglang/srt/disaggregation/decode.py` | 1106-1128 | KV 传输失败处理 |
| `python/sglang/srt/disaggregation/decode.py` | 1023-1041 | Metadata 损坏检测 |
| `python/sglang/srt/disaggregation/common/conn.py` | 457-467 | DP Rank 路由冲突 |
| `python/sglang/srt/disaggregation/common/conn.py` | 535-542 | Prefill 节点崩溃检测 |
| `python/sglang/srt/disaggregation/common/conn.py` | 667-676 | ZMQ 连接管理 |
| `python/sglang/srt/disaggregation/mooncake/conn.py` | 1878-1888 | KV 传输超时 |
| `python/sglang/srt/disaggregation/mooncake/conn.py` | 1275-1283 | 传输引擎失败 |
| `python/sglang/srt/disaggregation/mooncake/conn.py` | 1183-1196 | Session 死亡检测 |

---

## 十、总结

SGLang Router Server 在 PD 分离模式下**确实存在偶发转发请求到 Decode 失败的情况**，主要根因有 3 个：

1. **断路器级联故障**：双发模式下 Prefill 被 Decode 失败连坐惩罚，导致断路器误触发
2. **健康检查盲区**：60 秒检查间隔导致节点故障后有较长时间的路由盲区
3. **7P1D 单点风险**：只有 1 个 Decode 节点，断路器打开后完全没有备用节点

这三个问题叠加，在生产环境中表现为"每隔一段时间出现一批请求失败，然后自动恢复"的偶发现象。建议根据第八节的配置优化建议进行调整。
