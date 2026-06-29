# Mooncake TTFT 随"相同前缀种类数"放大的根因分析

> **现象**：模型 TTFT 性能测试中，相同前缀种类数为 **16 种**时测得的 TTFT，约为 **76 种**时的 **1/2**（即 76 种 TTFT 是 16 种的 2 倍）。
> **问题**：为何性能差异如此大？是否是配置不对？
> **分析对象**：`/root/vllm_ascend/Mooncake`（Mooncake）、`/root/vllm_ascend/vllm`（vLLM）。
> **结论**：**不是某一个"配错的开关"，而是 P/D + Mooncake store 拓扑下 TTFT 本就会随"前缀 working set"放大。2× 差距的本质是 16 种前缀的 working set 能被缓存覆盖，76 种跨过了覆盖阈值——大量请求从"跳过 prefill + warm 传输"退化为"整段重算 + cold 传输"。**

---

## 0. 结论速览

1. **不是单一错误配置**，是这类 disaggregated KV 共享拓扑的固有行为：Mooncake 的"命中"也要在首 token 关键路径上跑一趟网络，**命中不是免费的**（和 vLLM 本地 `prefix_cache` 命中≈免费截然不同）。
2. **TTFT 由三段串行构成**：P 端同步 prefill（`max_tokens=1`）+ D 端同步 KV GET + D 端首 token decode。三段都在关键路径上。
3. **阈值效应**：working set = `前缀种类数 × 前缀长度`。16 种装得下缓存（warm，跳过 prefill），76 种超容量/没捂热（部分冷，整段重算）。TTFT 从 transfer-bound 跳到 prefill-bound，正好 2× 量级。
4. **可调杠杆**（按可能性排序）：① benchmark 预热 ② 本地 APC 容量 ③ 路由亲和 / P 实例数 ④ store 内存层大小 ⑤ 异步传输 `ASCEND_USE_ASYNC_TRANSFER=1`。

---

## 1. 拓扑与 TTFT 关键路径

该 benchmark（`Mooncake/benchmarks/xypd_benchmarks/`）用 proxy + `MooncakeStoreConnector`：P 端 `kv_producer`、D 端 `kv_consumer`，proxy 把同一个请求拆成两段。

### 1.1 proxy：同步两段式

`proxy_demo.py` 的 `create_completion`（`:248-275`）：

```python
# 第一段：把整个 prompt 发给 prefill 节点，max_tokens=1，阻塞等返回
kv_prepare_request = request.copy()
kv_prepare_request["max_tokens"] = 1
prefill_instance = self.schedule(self.prefill_cycler)   # round-robin
async for _ in self.forward_request(prefill_instance, kv_prepare_request):
    continue                                            # ★ 阻塞，prefill 完才继续

# 第二段：把请求发给 decode 节点真正解码
decode_instance = self.schedule(self.decode_cycler)     # round-robin
generator = self.forward_request(decode_instance, request)
```

要点：
- **round-robin 路由**：`prefill_cycler = itertools.cycle(...)`（`:57-58`），同一前缀会被轮到不同 P 实例 → 本地 APC 命中被稀释。
- **首 token 来自 decode 节点的流式响应**（prefill 的那 1 个 token 被 `continue` 丢弃）。
- 所以客户端 TTFT = **P 端 prefill 时间 + D 端取 KV 并出第一个 token 的时间**，两段串行、都在关键路径上。

### 1.2 TTFT 的三段式分解

```
TTFT = [P 端同步 prefill(max_tokens=1)]
     + [D 端同步 KV GET + miss 重算]
     + [D 端首 token decode]
```

关键：P 端能省多少计算、D 端要传/重算多少，都由**缓存命中**决定，而命中又由 working set 是否被覆盖决定。

---

## 2. 命中是如何计算的（本地 APC + store 相加）

vLLM scheduler 把本地命中与外部（store）命中**相加**扣减待计算 token：

```python
# vllm/v1/core/sched/scheduler.py:418-421
num_new_tokens = (
    request.num_tokens_with_spec
    + request.num_output_placeholders
    - request.num_computed_tokens        # = 本地APC命中 + 外部store命中
)
```

即 `num_computed_tokens = local_APC + external_store`，二者叠加、互不冲突。

### 2.1 store connector 的命中判定：真实 RPC 查询，不是盲命中

`MooncakeStoreScheduler.get_num_new_matched_tokens`（`store/scheduler.py:74-117`）：

```python
# 按 block hash 实查哪些 block 在 store
num_external_hit_tokens = self.client.lookup(token_len, request.block_hashes)   # :85

# 减去本地 APC 已经覆盖的部分，剩下的才需要从 store 拉
if num_external_hit_tokens < num_computed_tokens:
    need_to_allocate = 0
else:
    need_to_allocate = num_external_hit_tokens - num_computed_tokens            # :95-98

return need_to_allocate, self.load_async      # load_async 默认 True (:57-59, :117)
```

返回 `load_async=True` 后，请求进入"等远端 KV"状态，**首 token 被推迟到 GET 完成**。

### 2.2 D 端 KV GET：单线程串行 + 在关键路径上

`KVCacheStoreLoadingThread.run` 是**单线程串行**处理（`store/worker.py:388-399`）：

```python
def run(self):
    self.ready_event.set()
    while True:
        request_data = self.request_queue.get()      # 一次只处理一个请求
        ...
        self._handle_request(request_data)
```

每个请求一次 `batch_get_into_multi_buffers` 同步 RDMA 拉（`store/worker.py:855`），并记录耗时（`:869` `_record_operation("load_get", ...)`）：

```python
res = self.store.batch_get_into_multi_buffers(batch_keys, batch_addrs, batch_sizes)
failed = [(key, value, block_id) for ... if value < 0]   # 未命中/失败的 block
```

**未命中的 block 会标记为 error（`_add_load_error_block_ids`），D 端必须重算**——miss 是"双重开销"：失败的 GET 等待 + 重算。

---

## 3. 为什么 16→76 会跳 ~2×（阈值效应）

working set = `前缀种类数 × 前缀长度`，它要被两层缓存覆盖：
- **① 本地 APC**（每实例的 vLLM prefix cache，受 HBM / num_blocks 限制，LRU 淘汰）；
- **② 分布式 store 内存层**（`global_segment_size`，`enable_offload` 默认 `False` 不落盘）。

| 维度 | 16 种前缀 | 76 种前缀 |
|---|---|---|
| working set vs 缓存 | 装得下 → warm | 超容量 / 没捂热 → 部分冷 |
| 请求主要落在 | APC 命中 + store warm GET | **整段重算 + 部分 GET miss 重算** |
| TTFT 性态 | transfer-bound（快） | **prefill-bound（慢）** |

跨过阈值后，TTFT 从"≈ 一次传输 + 1 个 decode"变成"≈ 整段 prefill + 传输 + decode"。对长 prompt / MLA（KV 小）类模型，**这个落差正好是 2× 量级**。

两个放大因素：

- **round-robin 稀释本地 APC**（`proxy_demo.py:57`）：同一前缀轮到不同 P 实例，单实例命中率被稀释为 ~1/P；前缀种类越多、每个前缀在单实例上重复次数越少，APC 越捂不热。
- **D 端 load 线程串行**（`store/worker.py:388`）：并发越高、未命中越多，GET 队列越堵。

### 3.1 为什么 MLA 模型也受影响（一个反直觉点）

GLM-5.1 等 MLA 模型 KV/token 很小，**纯容量溢出在 76 种时未必发生**。此时 2× 更可能来自：
- **benchmark 没预热**：76 种 = 76 条冷路径、16 种 = 16 条，均值 TTFT 自然翻倍（最常见）；
- **round-robin + 单实例重复次数不足**：76 种时每个前缀在单 P 实例上的出现次数比 16 种少 ~4.75×，APC 捂不热 → 整段重算。

---

## 4. "是不是配置不对"——核查清单（按可能性排序）

不是单一错误配置，但下面这些决定了**阈值在哪**：

1. **benchmark 预热（最可能元凶，给出干净的 2×）**
   测量前是否充分预热了 APC + store？没预热则 76 种 = 76 条冷路径。务必**分离"冷启动 TTFT"和"稳态 TTFT"分别报**。

2. **本地 APC 容量**
   `--gpu-memory-utilization` / `--kv-cache-memory` 决定 num_blocks；确认 `76 × 前缀长度` 的 KV 装得下。看 vLLM 启动日志 `KV cache ... blocks` 与运行时淘汰率。
   `enable_prefix_caching` 默认 `True`（`config/cache.py:92`），确认没被关掉。

3. **路由亲和 / P 实例数**
   round-robin 稀释 APC；按前缀做 sticky 路由或减少 P 实例数能直接拉高 APC 命中率。

4. **store 内存层**
   `global_segment_size` 够不够装 working set；`enable_offload` 保持默认 `False`（`store/worker.py:112`），否则溢出到磁盘 GET 会骤慢。

5. **异步传输 `ASCEND_USE_ASYNC_TRANSFER=1`**
   把"同步 KV 传输"从关键路径上挪开，让 prefill 计算与上一批 KV 传输重叠。默认是 sync（`mooncake_connector.py` 的 `batch_transfer_sync_write`），串在关键路径上。

---

## 5. 如何快速确认卡在哪一段

- **APC 命中率**：看 prefill 节点 vLLM 日志，16 种时高、76 种时暴跌 → 命中问题（杠杆 2/3）。
- **GET 耗时与失败数**：store connector 的 `load_get` 操作耗时与 `num_failed_keys`（`store/worker.py:869-876`）→ 失败 key 多即 miss 重算（杠杆 4）。
- **预热对照实验**：固定并发、只改预热与否，TTFT 大变 → 冷启动问题（杠杆 1）。

---

## 6. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| proxy 同步两段式（max_tokens=1 阻塞） | `Mooncake/benchmarks/xypd_benchmarks/proxy_demo.py:248-275` |
| round-robin 路由稀释 APC | `proxy_demo.py:57-58` |
| 本地 + 外部命中相加扣减 | `vllm/v1/core/sched/scheduler.py:418-421` |
| store 真实命中判定 + load_async | `vllm/.../mooncake/store/scheduler.py:74-117`（lookup `:85`、`load_async :57-59`） |
| D 端单线程串行 load 线程 | `vllm/.../mooncake/store/worker.py:388-399` |
| D 端 batch_get + miss 标记 | `vllm/.../mooncake/store/worker.py:855-906`（`_add_load_error_block_ids`） |
| store 配置（global_segment_size / enable_offload=False） | `vllm/.../mooncake/store/worker.py:97-141` |
| `enable_prefix_caching` 默认 True | `vllm/config/cache.py:92` |
| 同步批量传输在关键路径（async 杠杆） | `Mooncake/mooncake-wheel/mooncake/mooncake_connector_v1.py:595-607`；`ASCEND_USE_ASYNC_TRANSFER` |

相关已发布报告：
- `vllm_prefix_cache_vs_mooncake_ttft_report/`（命中免费 vs 付网络传输）
- `mooncake_batch_transfer_sync_write_internals/`（sync 传输 6 层调用链）
- `mooncake_kv_sharing_topology_te_vs_store/`（TE 单向 vs store 全连接）

---

## 7. 总结

1. **不是单一错误配置**：这是 P/D + store 拓扑下 TTFT 随 working set 放大的固有行为，且 Mooncake 命中要付网络费、天然比本地 APC 更敏感。
2. **2× 差距 = 阈值效应**：16 种 working set 被缓存覆盖（warm，跳过 prefill），76 种跨过覆盖阈值（冷，整段重算 + 部分 miss 重算），TTFT 从 transfer-bound 跳到 prefill-bound。
3. **优先排查顺序**：benchmark 预热 → 本地 APC 容量 → 路由亲和 → store 内存层 → 异步传输。
4. 建议分离"冷启动 TTFT"与"稳态 TTFT"分别上报，并把 16/76 的具体参数（前缀长度、并发、P/D 实例数、是否预热）固定后做对照实验。

*本报告所有结论均基于上述源码路径与行号，可逐条核对。*
