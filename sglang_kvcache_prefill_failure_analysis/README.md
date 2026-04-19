# SGLang PD 分离架构下 "Failed to get kvcache from prefill instance" 错误全场景分析

> 分析日期：2026-04-18
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：Decode 节点报错 "Failed to get kvcache from prefill instance" 的所有触发场景及根因

---

## 一、错误概述

该错误出自 SGLang 的 **PD 分离架构**（Prefill-Decode Disaggregation），即 prefill 和 decode 运行在不同 GPU 实例上，通过 RDMA/网络传输 KV cache 来协同工作。

**错误位置**：`python/sglang/srt/disaggregation/mooncake/conn.py:1490-1493`

```python
elif status == KVPoll.Failed:
    self.record_failure(
        bootstrap_room,
        "Failed to get kvcache from prefill instance, it might be dead",
    )
```

当 KV 传输状态机返回 `KVPoll.Failed`（值为0）时触发。错误信息中的 **"it might be dead"** 已经点明了最常见的根因——**prefill 实例不可达或已崩溃**。

---

## 二、KVPoll 状态机

**定义位置**：`python/sglang/srt/disaggregation/base/conn.py:42-47`

```python
class KVPoll:
    Failed = 0            # 任何环节出错
    Bootstrapping = 1     # 建立连接中
    WaitingForInput = 2   # 等待 metadata
    Transferring = 3      # KV 数据传输中
    Success = 4            # 传输完成
```

**正常流转路径**：

```
Bootstrapping → WaitingForInput → Transferring → Success
                    ↓                  ↓
                Failed ← ← ← ← ← ← Failed
```

任何阶段出现异常，状态都会回退到 `Failed`，最终在 decode 端触发该错误。

---

## 三、全部触发场景

### 场景1：Prefill 实例宕机或网络不可达（最常见）

**严重程度**：高
**代码位置**：`mooncake/conn.py:1497-1549`

**根因**：prefill 节点 OOM 崩溃、进程被杀、网络中断

**机制**：decode 端的 `heartbeat_checker` 定期（默认2秒）对 prefill 节点做 HTTP 健康检查：

```python
def heartbeat_checker():
    while True:
        time.sleep(self.heartbeat_interval)
        for bootstrap_addr in addresses:
            response = session.get(f"http://{bootstrap_addr}/health", timeout=(2, 3))
            if response.status_code != 200:
                self.heartbeat_failures[bootstrap_addr] += 1
            # 连续失败超过 max_failures 次
            if self.heartbeat_failures[addr] >= self.max_failures:
                self._handle_node_failure(bootstrap_addr)
```

`_handle_node_failure` 会把该 prefill 节点上所有未完成的请求全部标记为 `KVPoll.Failed`。

**典型表现**：
- 批量请求同时报错
- 日志中先出现 `Losing connection with prefill instance`
- 大量请求同时失败
- prefill 侧日志有 CUDA OOM 或进程退出记录

---

### 场景2：Prefill 实例 OOM / 超长输入导致 prefill 崩溃

**严重程度**：高
**代码位置**：`prefill.py:254-262`

**根因**：输入长度过大（如36K），导致 prefill 实例在执行 forward pass 时 GPU OOM

**完整链路**：

```
36K input → prefill 实例分配 KV cache → GPU OOM → prefill 进程崩溃
    → decode 端 heartbeat 检测到不可达 → KVPoll.Failed → 报错
```

Prefill 端的容量检查（`prefill.py:254-262`）：

```python
def _check_if_req_exceed_kv_capacity(self, req):
    if req_len > self.max_total_num_tokens:
        raise Exception(
            f"Request {req.rid} exceeds the maximum number of tokens: {req_len} > {max_total}"
        )
```

**典型表现**：
- 单个或少数请求触发
- prefill 侧日志出现 `CUDA out of memory`
- 可能伴随 prefill 进程退出

---

### 场景3：Bootstrap 阶段超时（Prefill 端）

**严重程度**：中
**代码位置**：`mooncake/conn.py:1694-1708`

```python
if elapsed > SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT:
    logger.error(
        f"Request {bootstrap_room} timed out after {elapsed:.1f}s "
        f"in KVPoll.Bootstrapping"
    )
    return KVPoll.Failed
```

**触发条件**：
- 网络延迟高，bootstrap 握手无法完成
- Prefill 端负载过高，无法及时处理 bootstrap 请求
- RDMA 连接建立慢

**典型表现**：
- 单个请求超时失败
- 日志中有 `timed out ... in KVPoll.Bootstrapping`

---

### 场景4：Waiting 阶段超时（Decode 端）

**严重程度**：中
**代码位置**：`mooncake/conn.py:1874-1889`

```python
if elapsed > SGLANG_DISAGGREGATION_WAITING_TIMEOUT:
    logger.error(
        f"Request {bootstrap_room} timed out after {elapsed:.1f}s "
        f"in KVPoll.WaitingForInput"
    )
    return KVPoll.Failed
```

**触发条件**：
- Prefill 端处理慢，长时间未发送 metadata
- 大 batch prefill 耗时超过超时阈值
- 网络中断但 heartbeat 尚未检测到

**典型表现**：
- 日志中有 `timed out ... in KVPoll.WaitingForInput`
- 通常在高峰负载时出现

---

### 场景5：RDMA / Mooncake 传输失败

**严重程度**：中高
**代码位置**：`mooncake/conn.py:1266-1288`

```python
status = session.write(...)
if status != 0:
    logger.error(f"Failed to send kv chunk of {bootstrap_room} to {target}")
    session.mark_as_failed()
    # 后续 poll 返回 KVPoll.Failed
```

**触发条件**：
- RDMA 网卡故障
- Mooncake session 中断
- GPU Direct RDMA 内存注册失败
- 远端 decode 实例的 mooncake session 不存活

**典型表现**：
- 传输中断，部分请求失败
- 日志中有 `Failed to send kv chunk` 或 `remote mooncake session is not alive`

---

### 场景6：Bootstrap 信息获取失败

**严重程度**：中
**代码位置**：`common/conn.py:534-542`、`common/conn.py:600-609`

**场景6a**：Prefill 服务器查询时健康但 init 时已宕机

```python
if not is_healthy:
    raise Exception(
        "Prefill server with bootstrap_addr: X is healthy before, "
        "but now it is down"
    )
```

**场景6b**：无法获取 bootstrap 信息

```python
if not bootstrap_info:
    raise Exception(
        "Could not fetch bootstrap info for: "
        "prefill_dp_rank: X prefill_cp_rank: Y target_tp_rank: Z and target_pp_rank W"
    )
```

**典型表现**：
- 服务刚启动时、prefill 实例滚动更新期间
- 新请求分配到正在重启的 prefill 实例

---

### 场景7：Staging Buffer 分配失败

**严重程度**：中
**代码位置**：`mooncake/conn.py:406-417`

```python
if alloc_result == ALLOC_OVERSIZED:
    logger.error(
        "Chunk staging allocation permanently failed: "
        "chunk exceeds ring buffer total size"
    )
    return KVPoll.Failed
```

**触发条件**：单个请求的 KV chunk 过大（超长输入），超过了 staging ring buffer 的总容量

**典型表现**：
- 超长输入请求（如 32K+ tokens）触发
- 不会影响其他正常大小的请求

---

### 场景8：Prefill 端 Bootstrap Room 冲突

**严重程度**：低
**代码位置**：`common/conn.py:449-467`

```python
if dispatched_dp_rank != expected_dp_rank:
    raise Exception(
        f"follow_bootstrap_room conflict: dispatched to dp_rank {dispatched} "
        f"but bootstrap_room {room} implies dp_rank {expected}"
    )
```

**触发条件**：`follow_bootstrap_room` 模式启用时，请求被路由到错误的 DP rank

**典型表现**：配置错误导致，罕见

---

### 场景9：Metadata 损坏

**严重程度**：低
**代码位置**：`decode.py:1017-1041`

```python
if req_bootstrap_room != received_bootstrap_room:
    logger.error(
        f"Context corruption detected: Request {req.rid} "
        f"(bootstrap_room={req_bootstrap_room}) received metadata "
        f"from bootstrap_room={received_bootstrap_room}"
    )
```

**触发条件**：网络传输中 metadata 损坏，bootstrap_room 不匹配

**典型表现**：极端网络问题或内存损坏，非常罕见

---

### 场景10：AUX_DATA 传输损坏

**严重程度**：低
**代码位置**：`mooncake/conn.py:951-953`

```python
if len(data) != expected_len:
    logger.error(f"AUX_DATA length mismatch for bootstrap_room {bootstrap_room}")
```

**触发条件**：辅助数据（如 rope freq、custom fetch info）传输长度不匹配

---

### 场景11：显式 Abort 请求

**严重程度**：低（预期行为）
**代码位置**：`common/conn.py:505-511`、`common/conn.py:700-706`

```python
def abort(self):
    self.aborted = True
    self.update_status(bootstrap_room, KVPoll.Failed)
```

**触发条件**：
- 客户端主动取消请求
- 客户端超时断开
- 调度器 abort（如 grammar 错误、输入长度超限）

---

### 场景12：Grammar Accept Token 失败

**严重程度**：低
**代码位置**：`prefill.py:544-557`

```python
try:
    req.grammar.accept_token(next_token_id)
except ValueError as e:
    logger.error(f"Grammar accept_token failed for req {req.rid}...")
    self.abort_request(AbortReq(rid=req.rid))
```

**触发条件**：xgrammar 在接受 token 时抛出 ValueError（通常因为 grammar 配置错误或无效 token）

---

## 四、36K 超长输入场景的典型触发链路

### 链路A：Prefill OOM 导致崩溃

```
36K 输入请求到达 decode 端
  → decode 端向 prefill 端发起 bootstrap
  → prefill 端接受请求，开始 prefill forward pass
  → 分配 36K tokens 的 KV cache → GPU HBM 不足 → CUDA OOM
  → prefill 进程崩溃退出
  → decode 端 heartbeat 检测到 prefill 不可达
  → 标记所有关联请求为 KVPoll.Failed
  → 报错: "Failed to get kvcache from prefill instance, it might be dead"
```

### 链路B：KV Chunk 传输失败

```
36K 输入请求到达
  → prefill 成功完成 forward pass（GPU 内存足够）
  → 开始传输 KV chunk
  → KV chunk 过大，超过 staging buffer 容量 → 分配失败
  → 或 RDMA 传输超时
  → KVPoll.Failed
  → 报错同上
```

### 链路C：容量检查前置拦截

```
36K 输入请求到达
  → prefill 端 PrefillBootstrapQueue._check_if_req_exceed_kv_capacity() 检查
  → 36K > max_total_num_tokens → 直接拒绝
  → 请求失败，返回错误
```

---

## 五、KV 传输完整流程图

```
┌─────────────── Prefill 端 ───────────────┐     ┌─────────────── Decode 端 ───────────────┐
│                                           │     │                                          │
│  1. 请求到达 → PrefillBootstrapQueue      │     │  1. 请求到达 → DecodePreallocQueue       │
│     .add()                                │     │     .add()                               │
│                                           │     │                                          │
│  2. KVSender 初始化                       │     │  2. KVReceiver 初始化                    │
│     状态: Bootstrapping                   │     │     状态: Bootstrapping                  │
│     ↓                                     │     │     ↓                                    │
│  3. Bootstrap 完成                        │────→│  3. send_metadata()                      │
│     状态: WaitingForInput                 │     │     状态: WaitingForInput                │
│     ↓                                     │     │     ↓                                    │
│  4. Prefill Forward Pass                  │     │  4. poll() 轮询                          │
│     ↓                                     │     │     ↓                                    │
│  5. send_kv_chunk()                       │────→│  5. 接收 KV chunk                        │
│     状态: Transferring                    │     │     状态: Transferring                   │
│     ↓                                     │     │     ↓                                    │
│  6. 传输完成                              │     │  6. 接收完成                             │
│     状态: Success                         │     │     状态: Success                        │
│                                           │     │     ↓                                    │
│                                           │     │  7. 开始 decode forward pass             │
└───────────────────────────────────────────┘     └──────────────────────────────────────────┘

异常路径（任何阶段）：
  ┌── Bootstrap 超时 ────────────────────────→ KVPoll.Failed
  ├── Waiting 超时 ─────────────────────────→ KVPoll.Failed
  ├── RDMA 传输失败 ────────────────────────→ KVPoll.Failed
  ├── Prefill 宕机（heartbeat 检测）───────→ KVPoll.Failed
  ├── Staging buffer 不足 ─────────────────→ KVPoll.Failed
  ├── 显式 Abort ──────────────────────────→ KVPoll.Failed
  └── Metadata 损坏 ───────────────────────→ KVPoll.Failed
```

---

## 六、相关环境变量配置

| 环境变量 | 作用 | 默认值 | 调优建议 |
|---------|------|--------|---------|
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | Prefill 端 bootstrap 超时 | — | 大模型/高负载时调大 |
| `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | Decode 端等待超时 | — | 根据最大 prefill 耗时调大 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 心跳检查间隔 | 2s | 网络不稳定时可减小 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 最大心跳失败次数 | 1 | 网络抖动环境可调大至 3-5 |
| `SGLANG_DISAGGREGATION_FAILURE_PROB` | 模拟故障概率（测试用） | 0 | 仅测试时使用 |

---

## 七、故障排查指南

### Step 1：检查 Prefill 实例状态

```bash
# 检查 prefill 进程是否存活
ps aux | grep sglang

# 检查 prefill 日志是否有 OOM
grep -i "out of memory\|cuda error\|killed" prefill.log
```

### Step 2：检查网络连通性

```bash
# 从 decode 节点检查 prefill 健康端点
curl http://<prefill_addr>:<port>/health

# 检查 RDMA 连接
ibv_devinfo
rdma link show
```

### Step 3：检查超时配置

确认 `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` 和 `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` 是否足够覆盖大请求的 prefill 时间。

### Step 4：检查 Staging Buffer

确认 staging ring buffer 容量足够容纳最大请求的 KV chunk。

### Step 5：分析日志关键词

| 关键词 | 含义 |
|--------|------|
| `timed out ... in KVPoll.Bootstrapping` | Bootstrap 超时 |
| `timed out ... in KVPoll.WaitingForInput` | Waiting 超时 |
| `Losing connection with prefill instance` | Prefill 节点不可达 |
| `Failed to send kv chunk` | RDMA 传输失败 |
| `remote mooncake session is not alive` | Mooncake session 中断 |
| `Chunk staging allocation permanently failed` | Staging buffer 不足 |
| `exceeds the maximum number of tokens` | 请求超过 KV 容量 |
| `CUDA out of memory` | GPU 内存不足 |

---

## 八、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `disaggregation/base/conn.py` | 42-47 | KVPoll 状态枚举定义 |
| `disaggregation/mooncake/conn.py` | 1490-1493 | 错误触发点 |
| `disaggregation/mooncake/conn.py` | 1497-1549 | Heartbeat 心跳检测 |
| `disaggregation/mooncake/conn.py` | 1602-1631 | 节点故障处理 |
| `disaggregation/mooncake/conn.py` | 1266-1288 | RDMA 传输失败处理 |
| `disaggregation/mooncake/conn.py` | 406-417 | Staging buffer 分配失败 |
| `disaggregation/mooncake/conn.py` | 1694-1708 | Bootstrap 超时 |
| `disaggregation/mooncake/conn.py` | 1874-1889 | Waiting 超时 |
| `disaggregation/common/conn.py` | 192-194 | record_failure |
| `disaggregation/common/conn.py` | 449-467 | Bootstrap room 冲突 |
| `disaggregation/common/conn.py` | 534-542 | Prefill 不可达检测 |
| `disaggregation/common/conn.py` | 600-609 | Bootstrap 信息获取失败 |
| `disaggregation/common/conn.py` | 505-511 | Abort 处理 |
| `disaggregation/prefill.py` | 254-262 | KV 容量检查 |
| `disaggregation/prefill.py` | 544-557 | Grammar 失败处理 |
| `disaggregation/decode.py` | 1017-1041 | Metadata 损坏检测 |
| `disaggregation/utils.py` | 546-570 | prepare_abort 工具函数 |

---

## 九、总结

| 问题 | 回答 |
|------|------|
| 该错误在什么架构下出现？ | PD 分离架构（Prefill-Decode Disaggregation） |
| 最常见的触发原因？ | Prefill 实例宕机/崩溃（OOM、进程异常） |
| 第二常见的触发原因？ | 网络传输超时（Bootstrap/Waiting 阶段） |
| 与超长输入的关联？ | 36K+ 输入可能导致 prefill OOM → 实例崩溃 → 报错 |
| 是否影响其他请求？ | 是，一个 prefill 节点宕机会导致其上所有请求失败 |
| 如何预防？ | 合理配置 context-length、增大超时、监控 prefill GPU 使用率 |
