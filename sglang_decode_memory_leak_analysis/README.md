# SGLang 超长输入场景下 Decode 节点异常与内存/HBM 泄露分析报告

> 分析日期：2026-04-18
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：超长输入（如36K tokens）是否会导致 decode 节点 error，进而产生内存/HBM 泄露

---

## 一、结论摘要

**存在此风险，但不是直接因果链。** SGLang 在超长输入场景下确实存在多个可能导致内存/HBM 泄露的代码路径，但泄露的根本原因不是"输入超过限制 → decode error → 泄露"这条简单链路，而是更隐蔽的**异常处理路径不完善**导致的资源未释放。

---

## 二、超长输入的完整处理链路

当输入长度超过 `--context-length` 或 KV cache pool 容量时，SGLang 有**两层拦截机制**。

### 第1层：Tokenizer Manager 拦截

**代码位置**：`python/sglang/srt/managers/tokenizer_manager.py:809-859`

```
客户端请求 → HTTP Server → Tokenizer Manager 验证
  ├─ 如果 input_token_num >= context_len:
  │    ├─ 开启 --allow-auto-truncate → 截断输入
  │    └─ 未开启 → ValueError → 返回错误给客户端 → 干净退出，无资源泄露
  └─ 验证通过 → 发送到 Scheduler
```

**结论**：这一层是安全的，此时还未分配任何 GPU 资源。

### 第2层：Scheduler 验证

**代码位置**：`python/sglang/srt/managers/scheduler.py:1981-1989`

```python
error_msg = validate_input_length(req, self.max_req_input_len, ...)
if error_msg:
    req.set_finish_with_abort(error_msg)  # 将 input_ids 截断为 [0]
    self._add_request_to_queue(req)       # 仍加入等待队列
    return
```

`set_finish_with_abort` 的行为（`schedule_batch.py:1285-1295`）：

```python
def set_finish_with_abort(self, error_msg):
    if get_tensor_model_parallel_rank() == 0:
        logger.error(f"{error_msg}, {self.rid=}")
    self.multimodal_inputs = None
    self.grammar = None
    self.origin_input_ids = [0]   # 缩为1个token，使 prefill 开销极小
    self.return_logprob = False
    self.logprob_start_len = -1
    self.to_finish = FINISH_ABORT(error_msg, HTTPStatus.BAD_REQUEST, "BadRequestError")
```

这个请求仍会经过一次 prefill forward pass，但由于 input 被缩为 `[0]`，只分配1个 token 的 KV cache，不会导致大量 GPU 内存占用。之后通过正常的 `filter_batch()` → `release_kv_cache()` 释放。

**结论**：这一层也是安全的。

---

## 三、真正的内存/HBM 泄露风险点

### 风险1：Decode Forward Pass 异常无清理（最关键）

**代码位置**：`python/sglang/srt/managers/scheduler.py:1396-1399`

```python
# event_loop_normal 主事件循环
if batch:
    result = self.run_batch(batch)           # ← 无 try-except
    self.process_batch_result(batch, result) # ← 无 try-except
```

唯一的外层异常处理（`scheduler.py:3787-3790`）：

```python
except Exception:
    traceback = get_exception_traceback()
    logger.error(f"Scheduler hit an exception: {traceback}")
    parent_process.send_signal(signal.SIGQUIT)  # 直接杀进程
```

**泄露场景**：

1. 一个大输入请求（如36K tokens）成功通过了验证（因为未超过 context_len）
2. 在 decode 阶段，随着生成长度增长，KV cache 持续膨胀
3. 当 GPU HBM 不足时，`run_batch()` 中的 CUDA 操作抛出 OOM 或其他异常
4. **没有 try-except 包裹，异常直接传播到顶层**
5. 进程被 SIGQUIT 杀死，但 GPU 内存已经被 CUDA 分配，进程异常退出可能不会触发清理
6. **HBM 泄露**

同样的问题也存在于 overlap 模式的事件循环 `event_loop_overlap`（`scheduler.py:1409-1459`）。

---

### 风险2：Retract 解码中的边界条件

**代码位置**：`python/sglang/srt/managers/schedule_batch.py:2066-2082`

```python
if len(sorted_indices) <= 1 and not self.check_decode_mem(
    selected_indices=sorted_indices
):
    # 即使最后一个请求也无法放入内存
    last_req.to_finish = FINISH_ABORT("Out of memory ...")
    reqs_to_abort.append(last_req)
    self.release_req(last_idx, 0, server_args)
```

SGLang 有 retract 机制（按输出长度排序，驱逐最短输出的请求来释放空间），但当**只剩一个请求且内存仍然不够**时，会 abort 该请求。这里的 `release_req` 能正确释放，但如果 retract 过程中分配/释放操作出现异常，清理路径中断，就会泄露。

---

### 风险3：RadixTree 插入失败导致资源未释放

**代码位置**：`python/sglang/srt/mem_cache/common.py:465-513`

```python
def release_kv_cache(req, tree_cache, is_insert=True):
    # ...
    tree_cache.cache_finished_req(req, is_insert=is_insert)  # ← 可能抛异常

    # 以下代码在异常时不会执行
    start_p, end_p = req.pop_overallocated_kv_cache()
    if start_p < end_p:
        indices_to_free = tree_cache.req_to_token_pool.req_to_token[...]
        tree_cache.token_to_kv_pool_allocator.free(indices_to_free)
    tree_cache.req_to_token_pool.free(req)
```

如果 `cache_finished_req` 内部的 `insert()` 操作失败（如 OOM），后续的 `free()` 调用永远不会执行，导致：
- **request slot 泄露**（`req_to_token_pool`）
- **KV cache 泄露**（`token_to_kv_pool_allocator`）

---

### 风险4：Mamba 混合池分配的部分失败

**代码位置**：`python/sglang/srt/mem_cache/memory_pool.py`

```python
def alloc(self, reqs):
    select_index = super().alloc(reqs)  # 先分配 req_to_token slot
    # ...
    for req in reqs:
        mid = self.mamba_pool.alloc(1)
        if mid is None:
            assert False  # req_to_token 已分配但未回滚 → 泄露
```

`super().alloc()` 成功后，mamba pool 分配失败时直接 assert 崩溃，已分配的 `req_to_token` slot 没有回滚释放。

---

### 风险5：Prefill 结果处理中无异常保护

**代码位置**：`python/sglang/srt/managers/scheduler_output_processor_mixin.py:180-200`

```python
for i, (req, next_token_id) in enumerate(zip(batch.reqs, next_token_ids)):
    if req.finished() or req.is_retracted:
        continue

    # 这段代码没有 try-except 包裹
    req.output_ids.append(next_token_id)
    self._maybe_update_reasoning_tokens(req, next_token_id)
    req.check_finished()           # ← 可能抛异常

    if req.finished():
        release_kv_cache(req, self.tree_cache)  # 仅在 finished 时调用
    elif ...:
        self.tree_cache.cache_unfinished_req(req)  # 也可能抛异常
```

如果 `check_finished()` 或 `cache_unfinished_req()` 抛出异常：
- 请求的 KV cache 不会被释放
- 请求 slot 不会被回收
- 后续 `filter_batch()` 可能无法正确清理该请求

---

### 风险6：Grammar 错误处理路径

**代码位置**：`scheduler_output_processor_mixin.py:240-251`（prefill）和 `514-531`（decode）

```python
try:
    req.grammar.accept_token(next_token_id)
except ValueError as e:
    self.abort_request(AbortReq(rid=req.rid))
```

Grammar 错误通过 `abort_request` 处理，这个路径是安全的。但需要注意的是，`abort_request`（`scheduler.py:3313-3348`）主要处理等待队列中的请求，对于已在 running batch 中的请求，只设置 `to_finish` 标志，依赖后续的 `filter_batch()` 来清理。如果 `filter_batch()` 本身失败，泄露仍会发生。

---

## 四、36K 输入场景的具体行为分析

假设使用 `--context-length 32768` 启动框架：

| 场景 | 系统行为 | 是否泄露 |
|------|---------|---------|
| 36K > context_len (32K) | Tokenizer Manager 拦截，返回 ValueError | **不泄露** |
| 36K < context_len 但 > max_req_input_len | Scheduler 拦截，set_finish_with_abort | **不泄露**（只走1个token的prefill） |
| 36K < context_len 且 < max_req_input_len | 正常进入 prefill + decode | **正常不泄露** |
| 36K 正常进入但 decode 时 OOM | CUDA异常 → 进程崩溃 | **可能泄露 HBM** |
| 多个长输入并发导致内存压力 | retract 机制工作 → 边界情况 | **有泄露风险** |
| Chunked prefill 过程中异常 | 已分配的 KV cache 未释放 | **有泄露风险** |

---

## 五、内存管理核心架构图

```
┌─────────────────────────────────────────────────────┐
│                    客户端请求                         │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│          第1层拦截: Tokenizer Manager                 │
│  _validate_one_request() → 检查 context_len          │
│  超限 → ValueError / 自动截断                         │
│  (此时无 GPU 资源分配，安全)                           │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│          第2层拦截: Scheduler                         │
│  validate_input_length() → 检查 max_req_input_len    │
│  超限 → set_finish_with_abort() (input缩为[0])       │
│  (仅分配1 token KV cache，几乎无开销)                 │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│          正常处理流程                                  │
│  alloc_req_slots() → ReqToTokenPool 分配              │
│  alloc_token_slots() → TokenToKVPoolAllocator 分配    │
│  Prefill Forward Pass                                │
│  Decode Forward Pass ← 可能在此 OOM                   │
│  release_kv_cache() → 释放资源                       │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│           ⚠ 泄露风险区域                              │
│                                                      │
│  run_batch() 无 try-except                           │
│  → CUDA OOM 时进程崩溃                               │
│  → GPU 内存未释放                                    │
│                                                      │
│  release_kv_cache() 无 try-except                    │
│  → cache_finished_req() 异常                         │
│  → free() 不执行，资源泄露                            │
└─────────────────────────────────────────────────────┘
```

---

## 六、修复建议

### 建议1：为 Decode 循环添加异常保护

**修改文件**：`python/sglang/srt/managers/scheduler.py:1396-1399`

```python
if batch:
    try:
        result = self.run_batch(batch)
        self.process_batch_result(batch, result)
    except Exception as e:
        logger.error(f"Batch execution failed: {e}, cleaning up {len(batch.reqs)} requests")
        for req in batch.reqs:
            if not req.finished() and req.req_pool_idx is not None:
                try:
                    release_kv_cache(req, self.tree_cache, is_insert=False)
                except Exception:
                    pass
        raise
```

### 建议2：为 release_kv_cache 添加异常保护

**修改文件**：`python/sglang/srt/mem_cache/common.py:465`

```python
def release_kv_cache(req, tree_cache, is_insert=True):
    if req.req_pool_idx is None:
        # ... mamba 早期释放逻辑不变
        return

    try:
        tree_cache.cache_finished_req(req, is_insert=is_insert)
    except Exception as e:
        logger.error(f"Failed to cache finished req {req.rid}: {e}, forcing free")

    # 以下释放操作应在 finally 中确保执行
    if req.req_pool_idx is None:
        return

    start_p, end_p = req.pop_overallocated_kv_cache()
    # ... 后续 free 逻辑不变
    tree_cache.req_to_token_pool.free(req)
```

### 建议3：生产环境配置建议

- 启用 `--allow-auto-truncate` 避免超长输入进入 decode 阶段
- 合理设置 `--context-length` 与 `--mem-fraction-static`，为 KV cache 留足余量
- 设置 `--max-running-requests` 限制并发请求数，降低 OOM 概率
- 监控 GPU HBM 使用趋势，检测是否存在缓慢增长的内存泄露

### 建议4：Mamba 混合池分配添加回滚

**修改文件**：`python/sglang/srt/mem_cache/memory_pool.py`

```python
def alloc(self, reqs):
    select_index = super().alloc(reqs)
    if select_index is None:
        return None

    allocated_mamba = []
    try:
        for req in reqs:
            mid = self.mamba_pool.alloc(1)
            if mid is None:
                # 回滚已分配的 mamba slots
                for prev_mid in allocated_mamba:
                    self.mamba_pool.free(prev_mid)
                # 回滚 req_to_token slots
                self.free(select_index)  # 需实现
                raise RuntimeError("Not enough space for mamba cache")
            allocated_mamba.append(mid)
            req.mamba_pool_idx = mid
    except Exception:
        # 确保回滚
        raise
```

---

## 七、涉及的关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `python/sglang/srt/managers/scheduler.py` | 1382-1459 | 主事件循环（泄露风险核心） |
| `python/sglang/srt/managers/scheduler.py` | 1981-1989 | 输入长度验证 |
| `python/sglang/srt/managers/scheduler.py` | 3313-3348 | abort_request 处理 |
| `python/sglang/srt/managers/scheduler.py` | 3787-3790 | 顶层异常处理（仅杀进程） |
| `python/sglang/srt/managers/schedule_batch.py` | 1285-1295 | set_finish_with_abort |
| `python/sglang/srt/managers/schedule_batch.py` | 2040-2098 | retract_decode |
| `python/sglang/srt/managers/schedule_batch.py` | 2263-2293 | filter_batch |
| `python/sglang/srt/managers/scheduler_output_processor_mixin.py` | 180-260 | prefill 结果处理 |
| `python/sglang/srt/managers/scheduler_output_processor_mixin.py` | 460-535 | decode 结果处理 |
| `python/sglang/srt/mem_cache/common.py` | 465-513 | release_kv_cache |
| `python/sglang/srt/mem_cache/radix_cache.py` | — | RadixTree 缓存管理 |
| `python/sglang/srt/mem_cache/memory_pool.py` | — | 内存池分配 |
| `python/sglang/srt/managers/utils.py` | 113-143 | validate_input_length |
| `python/sglang/srt/managers/tokenizer_manager.py` | 809-859 | 请求验证 |
| `python/sglang/srt/configs/model_config.py` | 391-420 | context_len 配置 |

---

## 八、总结

| 问题 | 回答 |
|------|------|
| 输入超过启动限制是否直接导致 decode error？ | **不是直接导致**。有两层拦截，超长输入会被提前拒绝或截断 |
| 是否存在 decode 阶段的内存泄露？ | **存在**，根本原因是异常处理路径不完善 |
| HBM 泄露的根本原因？ | `run_batch()`/`process_batch_result()` 无 try-except；CUDA异常时进程被杀但 GPU 内存未释放 |
| 泄露发生的典型条件？ | 超长输入 + 高并发 → decode 时 OOM → 异常无清理 → HBM 泄露 |
| 严重程度？ | **中高风险** — 在生产环境中，频繁的超长输入 + 高并发可能导致 GPU HBM 逐渐耗尽，最终需要重启服务 |
