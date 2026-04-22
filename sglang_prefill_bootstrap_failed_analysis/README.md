# SGLang Prefill Bootstrap Failed 错误根因分析

> 分析日期：2026-04-21
> 分析对象：SGLang 源码 sgl-project/sglang
> 分析目标：`Prefill bootstrap failed for request rank=0` 错误的所有触发原因
> 部署场景：GLM-4.7-Flash-30B-A3B 7P1D PD 分离部署

---

## 一、错误来源

该错误来自 `python/sglang/srt/disaggregation/prefill.py:306-307`：

```python
elif poll == KVPoll.Failed:
    error_message = f"Prefill bootstrap failed for request rank={self.tp_rank} {req.rid=} {req.bootstrap_room=}"
    try:
        req.disagg_kv_sender.failure_exception()
    except Exception as e:
        error_message += f" with exception {e}"
    logger.error(error_message)
```

表示 Prefill 端的 `KVSender` 在 **Bootstrapping 阶段** poll 返回了 `KVPoll.Failed`。此阶段是 Prefill 向 Decode 发出握手请求后、等待 Decode 回复其 KV cache 接收地址的过程。

---

## 二、Bootstrap 阶段流程

```
Prefill 进程                                Decode 进程
    │                                            │
    │  1. KVSender.__init__()                    │
    │     设置状态为 Bootstrapping                 │
    │                                            │
    │  2. KVSender.poll()                        │
    │     检查状态是否从 Bootstrapping 变化         │
    │                                            │
    │          等待 Decode 回应...                  │
    │                                            │
    │  ←── 3. Decode 从 Bootstrap Server         │
    │        获取 Prefill rank 信息               │
    │        建立连接，发送 KV indices             │
    │        状态变为 WaitingForInput             │
    │                                            │
    │  4. Prefill poll 返回                       │
    │     WaitingForInput → Bootstrap 成功        │
    │     Failed → Bootstrap 失败（本文分析）       │
```

---

## 三、7 大触发原因详解

### 原因 1：Bootstrap 握手超时（概率最高）

**代码位置**：`mooncake/conn.py:1694-1708`

```python
# MooncakeKVSender.poll()
if status == KVPoll.Bootstrapping:
    if self.init_time is not None:
        now = time.time()
        elapsed = now - self.init_time
        if elapsed >= self.kv_mgr.bootstrap_timeout:
            logger.warning_once(
                "Some requests timed out when bootstrapping, "
                "which means prefill instances fail to receive the KV indices "
                "from the decode instance of this request. "
                "If a greater mean TTFT is acceptable, you can "
                "'export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=600' "
                "(10 minutes) to relax the timeout condition."
            )
            self.kv_mgr.record_failure(
                self.bootstrap_room,
                f"Request {self.bootstrap_room} timed out after "
                f"{elapsed:.1f}s in KVPoll.Bootstrapping",
            )
            self.conclude_state = KVPoll.Failed
            return KVPoll.Failed
```

**Mori 后端同样实现**（`mori/conn.py:894-904`）：

```python
# MoriKVSender.poll()
if status == KVPoll.Bootstrapping:
    elapsed = time.time() - self.init_time
    if elapsed >= self.kv_mgr.bootstrap_timeout:
        reason = (
            f"Request {self.bootstrap_room} timed out after {elapsed:.1f}s "
            "waiting for decode handshake"
        )
        self.kv_mgr.record_failure(self.bootstrap_room, reason)
        self.kv_mgr.update_status(self.bootstrap_room, KVPoll.Failed)
        self._finalize_failure(reason)
        return KVPoll.Failed
```

**含义**：Prefill 向 Decode 发出了 bootstrap 请求（"我需要把 KV cache 发给你，请告诉我你的接收地址"），但 Decode 一直**没有回应**。

**根因分析**：

| 子原因 | 说明 |
|--------|------|
| Decode 进程崩溃/OOM | 无法响应 bootstrap 请求 |
| Decode 节点负载过高 | 处理 bootstrap 请求的线程被阻塞 |
| 网络分区 | Prefill → Decode 的 HTTP 请求无法到达 |
| Bootstrap Server 端口异常 | 未正确监听 |
| Decode 在做长时间 GC | 无法及时响应 |

**超时配置**：`SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT`，默认 **300 秒**

**日志特征**：`timed out after XXXs in KVPoll.Bootstrapping`

---

### 原因 2：Decode 心跳检测判定 Prefill 死亡（概率高）

**代码位置**：`mooncake/conn.py:1497-1546`

```python
# Decode 端心跳检测线程
def heartbeat_checker():
    while True:
        time.sleep(self.heartbeat_interval)  # 默认 5 秒
        for bootstrap_addr in addresses:
            try:
                response = session.get(
                    f"http://{bootstrap_addr}/health",
                    timeout=(2, 3),
                    headers={"Connection": "keep-alive"},
                )
                if response.status_code == 200:
                    self.heartbeat_failures[bootstrap_addr] = 0
                else:
                    self.heartbeat_failures[bootstrap_addr] += 1
            except Exception:
                self.heartbeat_failures[bootstrap_addr] += 1

            if self.heartbeat_failures[bootstrap_addr] >= self.max_failures:
                self._handle_node_failure(bootstrap_addr)
```

当心跳失败达到阈值时，调用 `_handle_node_failure`（`mooncake/conn.py:1602-1631`）：

```python
def _handle_node_failure(self, failed_bootstrap_addr):
    possible_affected_rooms = self.addr_to_rooms_tracker.get(
        failed_bootstrap_addr, []
    )

    affected_rooms = []
    for room in possible_affected_rooms:
        if room in self.request_status and self.check_status(room) != KVPoll.Success:
            self.record_failure(
                room,
                f"Losing connection with prefill instance "
                f"(bootstrap_addr: {failed_bootstrap_addr})",
            )
            self.update_status(room, KVPoll.Failed)
            affected_rooms.append(room)
    logger.error(
        f"Losing connection with prefill instance "
        f"(bootstrap_addr: {failed_bootstrap_addr}), "
        f"{len(affected_rooms)} requests affected"
    )
```

**含义**：Decode 节点认为 Prefill 节点已经死亡，将所有关联该 Prefill 地址的请求标记为 Failed。

**根因分析**：

| 子原因 | 说明 |
|--------|------|
| Prefill 心跳接口连续失败 | 2 次（默认）非 200 响应 |
| Prefill 进程短暂 GC 停顿 | 可能导致心跳超时 |
| 网络瞬时抖动 | 心跳请求 2 秒连接超时、3 秒读取超时 |
| Prefill 进程实际崩溃 | 真正的节点故障 |

**影响范围**：**批量失败** — 所有关联该 Prefill 地址的请求全部失败

**配置参数**：

| 参数 | 默认值 |
|------|--------|
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 5 秒 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 2 次 |

**日志特征**：`Losing connection with prefill instance`

---

### 原因 3：Bootstrap Server 返回 404 或异常（概率中）

**代码位置**：`common/conn.py:624-641` → `common/conn.py:600-609`

```python
# 获取 bootstrap 信息
def _get_bootstrap_info_from_server(self, prefill_dp_rank, prefill_cp_rank,
                                     target_tp_rank, target_pp_rank):
    try:
        url = f"http://{self.bootstrap_addr}/route?prefill_dp_rank={...}..."
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Failed to get prefill server info: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching prefill info from bootstrap: {e}")
        return None
```

返回 `None` 时的处理：

```python
if bootstrap_info is not None:
    bootstrap_infos.append(bootstrap_info)
else:
    self.kv_mgr.record_failure(
        self.bootstrap_room,
        f"Could not fetch bootstrap info for: prefill_dp_rank: "
        f"{self.prefill_dp_rank} prefill_cp_rank: {target_cp_rank} "
        f"target_tp_rank: {target_tp_rank} and target_pp_rank {target_pp_rank}",
    )
    self.conclude_state = KVPoll.Failed
    self.kv_mgr.update_status(self.bootstrap_room, KVPoll.Failed)
    return
```

**根因分析**：

| 子原因 | 说明 |
|--------|------|
| Bootstrap Server 未就绪 | `_is_ready()` 返回 false，GET `/route` 返回 404 |
| Bootstrap Server 尚未启动 | 进程启动顺序问题 |
| HTTP 请求超时 | 5 秒超时 |
| rank 参数不匹配 | 请求的 TP/PP rank 不在 Server 中注册 |
| Prefill rank 注册信息不完整 | 只有部分 rank 完成了注册 |

**Bootstrap Server 就绪检查逻辑**（`common/conn.py:740-752`）：

```python
def _is_ready(self) -> bool:
    if (self.attn_tp_size is None or self.attn_cp_size is None
        or self.pp_size is None or self.dp_size is None):
        return False
    expected = self.dp_size * self.attn_cp_size * self.attn_tp_size * self.pp_size
    return self._registered_count >= expected
```

Server 只有在**所有 Prefill rank 都注册完成**后才认为就绪。

**日志特征**：`Could not fetch bootstrap info for`

---

### 原因 4：Session 死亡（传输引擎层面）（概率中）

**代码位置**：`mooncake/conn.py:1182-1196`

```python
# transfer_worker 中检查 session 状态
with self.session_lock:
    if req.mooncake_session_id in self.failed_sessions:
        self.record_failure(
            kv_chunk.room,
            f"Decode instance could be dead, remote mooncake session "
            f"{req.mooncake_session_id} is not alive",
        )
        self.update_status(kv_chunk.room, KVPoll.Failed)
        self.sync_status_to_decode_endpoint(
            req.endpoint, req.dst_port, req.room,
            KVPoll.Failed, prefill_unique_rank,
        )
        break
```

**Session 失败的触发条件**（`mooncake/conn.py:1266-1288`）：

```python
ret = self.send_kvcache(...)
if ret != 0:
    with self.session_lock:
        self.session_failures[req.mooncake_session_id] += 1
        # 失败 1 次就标记 session 为死亡
        if self.session_failures[req.mooncake_session_id] >= 1:
            self.failed_sessions.add(req.mooncake_session_id)
            logger.error(f"Session {req.mooncake_session_id} failed.")
    self.record_failure(kv_chunk.room, f"Failed to send kv chunk...")
    self.update_status(kv_chunk.room, KVPoll.Failed)
```

**含义**：之前的 RDMA 传输请求失败过 **1 次**，该 session 被永久标记为 failed。后续所有使用该 session 的请求直接失败。

**根因分析**：

| 子原因 | 说明 |
|--------|------|
| RDMA 硬件错误 | 传输引擎返回非零状态 |
| Decode 端内存注册失败 | KV cache 内存区域未正确注册 |
| 网络瞬时中断 | 导致一次传输失败，session 被永久标记 |
| Session 级联失败 | 一个请求失败 → session 死亡 → 所有后续请求失败 |

**注意**：Session 级别的失败是**不可恢复的** — 一旦标记，该 session 上的所有请求都会直接失败。

**日志特征**：`Decode instance could be dead` / `is not alive` / `Session XXX failed`

---

### 原因 5：DP Rank 路由冲突（概率低）

**代码位置**：`common/conn.py:449-467`

```python
# CommonKVSender.__init__()
if self.kv_mgr.server_args.dp_size > 1:
    if self.kv_mgr.server_args.load_balance_method != "follow_bootstrap_room":
        self._register_prefill_dp_rank()
    elif (
        self.kv_mgr.attn_dp_rank
        != self.bootstrap_room % self.kv_mgr.server_args.dp_size
    ):
        if envs.SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK.get():
            self._register_prefill_dp_rank()
        else:
            self.kv_mgr.record_failure(
                self.bootstrap_room,
                f"follow_bootstrap_room conflict: dispatched to dp_rank "
                f"{self.kv_mgr.attn_dp_rank} but bootstrap_room "
                f"{self.bootstrap_room} implies dp_rank "
                f"{self.bootstrap_room % self.kv_mgr.server_args.dp_size}. "
                f"Set SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK=1 "
                f"to allow mixed routing.",
            )
            self.kv_mgr.update_status(self.bootstrap_room, KVPoll.Failed)
```

**含义**：Router 使用 `follow_bootstrap_room` 负载均衡策略时，请求被路由到了错误的 DP rank。bootstrap_room 的模运算结果与实际 Prefill 的 `dp_rank` 不匹配。

**根因分析**：
- Router 的负载均衡策略配置为 `follow_bootstrap_room`
- 但请求实际被路由到了不同的 Prefill DP rank
- 仅在 `dp_size > 1` 的场景下出现

**解决方法**：设置 `SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK=1`

**日志特征**：`follow_bootstrap_room conflict`

---

### 原因 6：跨 Rank 失败同步传播（概率低）

**代码位置**：`mooncake/conn.py:1489-1495`

```python
# Decode 端 decode_thread 收到 Prefill 端的失败状态同步
elif status == KVPoll.Failed:
    self.record_failure(
        bootstrap_room,
        "Failed to get kvcache from prefill instance, it might be dead",
    )
    self.update_status(bootstrap_room, status)
```

**含义**：同一个请求的另一个 Prefill TP rank 传输失败后，状态通过 ZMQ 同步到 Decode，Decode 更新状态为 Failed。当本 rank 再次 poll 时，发现状态已经是 Failed。

**根因分析**：
- 同一个请求在另一个 Prefill TP rank 上传输失败
- 跨 rank 状态同步传播到所有相关 rank
- 典型于 TP size > 1 的场景

**日志特征**：`Failed to get kvcache from prefill instance, it might be dead`

---

### 原因 7：用户主动 Abort 请求（概率极低）

**代码位置**：`common/conn.py:505-511`

```python
def abort(self):
    self.kv_mgr.record_failure(
        self.bootstrap_room,
        "Aborted by AbortReq.",
    )
    self.conclude_state = KVPoll.Failed
```

**含义**：客户端在 Bootstrap 阶段主动取消了请求（如客户端超时、用户取消操作），Router 将 abort 信号传播到 Prefill 节点。

**根因分析**：
- 客户端连接超时主动断开
- 用户手动取消请求
- Router 在重试时 abort 了前一次请求

**日志特征**：`Aborted by AbortReq`

---

## 四、KVPoll 状态机与失败点映射

```
                    ┌──────────────────────────────────────────┐
                    │            KVSender 生命周期               │
                    │                                          │
                    │  __init__()                               │
                    │    │                                      │
                    │    ▼                                      │
                    │  Bootstrapping ◄─── 原因1: 超时(300s)     │
                    │    │              原因2: 心跳失败           │
                    │    │              原因3: Server 404        │
                    │    │              原因5: DP Rank冲突       │
                    │    │              原因6: 跨Rank传播         │
                    │    │              原因7: 用户Abort          │
                    │    │                                      │
                    │    ▼ (Decode 回应)                         │
                    │  WaitingForInput ◄── 原因4: Session死亡    │
                    │    │                                      │
                    │    ▼ (Prefill 发送 KV cache)               │
                    │  Transferring                              │
                    │    │                                      │
                    │    ▼                                      │
                    │  Success                                  │
                    │                                          │
                    └──────────────────────────────────────────┘
```

---

## 五、按概率排序的原因汇总

| 排序 | 原因 | 概率 | 日志特征关键词 | 影响范围 |
|------|------|------|--------------|---------|
| **1** | Bootstrap 握手超时 | **最高** | `timed out after XXXs in KVPoll.Bootstrapping` | 单个请求 |
| **2** | Prefill 心跳失败被判定死亡 | **高** | `Losing connection with prefill instance` | 批量（所有关联请求） |
| **3** | Bootstrap Server 返回 404 | **中** | `Could not fetch bootstrap info for` | 单个请求 |
| **4** | Session 死亡 | **中** | `Decode instance could be dead` / `is not alive` | 批量（同 session 所有请求） |
| **5** | DP Rank 路由冲突 | **低** | `follow_bootstrap_room conflict` | 单个请求 |
| **6** | 跨 Rank 失败同步 | **低** | `Failed to get kvcache from prefill instance` | 单个请求 |
| **7** | 用户 Abort | **极低** | `Aborted by AbortReq` | 单个请求 |

---

## 六、排查方法

### 步骤 1：定位具体原因

在 Prefill 节点日志中搜索对应的 `bootstrap_room` 编号：

```bash
# 搜索错误日志
grep "Prefill bootstrap failed" prefill.log
# 输出示例：
# Prefill bootstrap failed for request rank=0 req.rid='xxx' bootstrap_room=12345

# 用 bootstrap_room 搜索具体失败原因
grep "bootstrap_room=12345" prefill.log decode.log
# 或
grep "12345" prefill.log decode.log | grep -i "failure\|failed\|error\|timeout"
```

### 步骤 2：根据日志关键词定位原因

| 关键词 | 对应原因 | 排查方向 |
|--------|---------|---------|
| `timed out` | 原因 1 | 检查 Decode 节点是否存活、网络连通性 |
| `Losing connection` | 原因 2 | 检查 Prefill 健康接口是否正常 |
| `Could not fetch bootstrap info` | 原因 3 | 检查 Bootstrap Server 启动日志 |
| `not alive` / `Session failed` | 原因 4 | 检查 RDMA/网络硬件状态 |
| `follow_bootstrap_room conflict` | 原因 5 | 检查 Router 负载均衡配置 |
| `Aborted by AbortReq` | 原因 7 | 检查客户端超时配置 |

### 步骤 3：通过 failure_exception 获取详细原因

错误日志中 `with exception` 后面的内容来自 `KVTransferError`（`mooncake/conn.py:1718-1729`）：

```python
def failure_exception(self):
    if self.conclude_state is None:
        self.conclude_state = KVPoll.Failed

    self.clear()

    with self.kv_mgr.failure_lock:
        failure_reason = self.kv_mgr.failure_records.pop(
            self.bootstrap_room,
            "Failed due to an unknown reason from another rank"
        )
    raise KVTransferError(self.bootstrap_room, failure_reason)
```

`failure_reason` 包含了 `record_failure()` 记录的具体原因字符串。

---

## 七、生产环境缓解建议

### 7.1 针对原因 1（握手超时）

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | 600 | 给 Decode 更多响应时间 |

### 7.2 针对原因 2（心跳误判）

| 配置 | 建议值 | 原因 |
|------|--------|------|
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 3-5 | 保持默认 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 3-5 | 提高容忍度，避免偶发网络抖动误判 |

### 7.3 针对原因 3（Bootstrap Server 未就绪）

- 确保 Decode 节点完全启动后再启动 Prefill 节点
- 或增大 Prefill 连接重试次数

### 7.4 针对原因 4（Session 死亡级联）

- 监控 `Session XXX failed` 日志，及时发现 RDMA 硬件问题
- 考虑实现 Session 自动恢复机制（当前代码中 session 失败是永久的）

### 7.5 针对原因 5（DP Rank 冲突）

```bash
export SGLANG_DISAGGREGATION_FORCE_QUERY_PREFILL_DP_RANK=1
```

---

## 八、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `disaggregation/prefill.py` | 306-325 | `Prefill bootstrap failed` 错误输出 |
| `disaggregation/mooncake/conn.py` | 1694-1708 | Bootstrap 握手超时检测（Mooncake） |
| `disaggregation/mori/conn.py` | 894-904 | Bootstrap 握手超时检测（Mori） |
| `disaggregation/common/conn.py` | 449-467 | DP Rank 路由冲突 |
| `disaggregation/common/conn.py` | 534-542 | Prefill 节点崩溃检测 |
| `disaggregation/common/conn.py` | 600-609 | Bootstrap info 获取失败 |
| `disaggregation/common/conn.py` | 624-641 | `_get_bootstrap_info_from_server()` |
| `disaggregation/common/conn.py` | 740-752 | Bootstrap Server 就绪检查 |
| `disaggregation/common/conn.py` | 505-511 | 用户 Abort 处理 |
| `disaggregation/mooncake/conn.py` | 1182-1196 | Session 死亡检测 |
| `disaggregation/mooncake/conn.py` | 1266-1288 | Session 失败标记 |
| `disaggregation/mooncake/conn.py` | 1497-1546 | 心跳检测线程 |
| `disaggregation/mooncake/conn.py` | 1602-1631 | `_handle_node_failure()` |
| `disaggregation/mooncake/conn.py` | 1489-1495 | 跨 Rank 失败同步 |
| `disaggregation/mooncake/conn.py` | 1718-1729 | `failure_exception()` 详细原因获取 |
