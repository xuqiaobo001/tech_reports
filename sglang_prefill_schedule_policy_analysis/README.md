# SGLang Prefill 节点请求调度算法全解析

> 分析日期：2026-04-18
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：Prefill 节点的请求调度策略、Radix Cache 淘汰策略、Prefill Delayer 协调机制

---

## 一、概述

SGLang 的 prefill 节点调度涉及**三层决策机制**，不是单一算法：

| 层级 | 决策内容 | 配置参数 | 默认值 |
|------|---------|---------|--------|
| **Schedule Policy** | 请求以什么顺序进入 prefill batch | `--schedule-policy` | `fcfs` |
| **Prefill Delayer** | 是否允许本次 prefill（DP Attention 协调） | `--enable-prefill-delayer` | 关闭 |
| **Eviction Policy** | GPU 内存不足时淘汰哪些 KV cache | `--radix-eviction-policy` | `lru` |

---

## 二、请求调度策略（Schedule Policy）

决定等待队列中的请求**以什么顺序**被选入 prefill batch。

**配置参数**：`--schedule-policy`（默认 `fcfs`）
**可选值**：`lpm`, `random`, `fcfs`, `dfs-weight`, `lof`, `priority`, `routing-key`
**代码位置**：`python/sglang/srt/managers/schedule_policy.py:80-94`

策略分为两大类：

- **Cache-Aware**（感知 RadixTree 缓存）：`lpm`、`dfs-weight`
- **Cache-Agnostic**（不感知缓存）：`fcfs`、`lof`、`random`、`routing-key`

---

### 2.1 FCFS — 先来先服务（默认策略）

**枚举定义**：`CacheAgnosticPolicy.FCFS`
**默认值**：是
**需要 Tree Cache**：否

**原理**：按请求到达时间排序，先到的先处理。

**实现**（`schedule_policy.py:120-125`）：

```python
if self.policy == CacheAgnosticPolicy.FCFS:
    if self.enable_priority_scheduling:
        SchedulePolicy._sort_by_priority_and_fcfs(waiting_queue, self.priority_sign)
    return False  # 不需要计算前缀匹配
```

实际上不做排序，保持等待队列的原始插入顺序。开启 `--enable-priority-scheduling` 后，先按 priority 排序，同优先级按到达时间排序：

```python
def _sort_by_priority_and_fcfs(waiting_queue, priority_sign):
    waiting_queue.sort(
        key=lambda x: (
            x.priority * priority_sign,
            x.time_stats.wait_queue_entry_time,
        )
    )
```

**适用场景**：
- 通用在线服务，对延迟公平性有要求
- 默认策略，适合绝大多数场景
- 配合优先级调度可用于 VIP 用户优先处理

---

### 2.2 LPM — 最长前缀匹配（Cache-Aware）

**枚举定义**：`CacheAwarePolicy.LPM`
**需要 Tree Cache**：是

**原理**：计算每个请求与 RadixTree 中已有 KV cache 的前缀匹配长度，**匹配越长越优先调度**，从而最大化 KV cache 复用。

**核心排序逻辑**（`schedule_policy.py:246-256`）：

```python
def _sort_by_longest_prefix(waiting_queue, temporary_deprioritized):
    waiting_queue.sort(
        key=lambda r: (
            -len(r.prefix_indices)           # 前缀匹配越长，排序越靠前
            if r.rid not in temporary_deprioritized
            else float("inf")                # 批内前缀重复的请求降优先级
        )
    )
```

**特殊机制 — In-Batch 前缀缓存**（`schedule_policy.py:218-242`）：

如果多个请求的前缀在 RadixTree 中匹配很短（`<= IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD`，默认32），但彼此之间前缀相同且超过 `IN_BATCH_PREFIX_CACHING_DEPRIORITIZE_THRESHOLD`（默认32），则将这些请求**降优先级**，避免重复 prefill 相同前缀。

```python
if len(r.prefix_indices) <= IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD:
    match_result = self.waiting_queue_radix_tree.match_prefix(...)
    in_batch_matching_prefixes = match_result.device_indices
    if len(in_batch_matching_prefixes) >= IN_BATCH_PREFIX_CACHING_DEPRIORITIZE_THRESHOLD:
        temporary_deprioritized.add(r.rid)  # 降优先级
    else:
        self.waiting_queue_radix_tree.insert(...)  # 注册到批内树
```

**降级策略**（`schedule_policy.py:161-165`）：当等待队列 > 128 个请求时，自动退化为 FCFS（因为 LPM 的前缀匹配计算开销大）：

```python
def _determine_active_policy(self, waiting_queue):
    if self.policy == CacheAwarePolicy.LPM and len(waiting_queue) > 128:
        return CacheAgnosticPolicy.FCFS  # 退化
    return self.policy
```

**适用场景**：
- **多轮对话**：大量请求共享 system prompt，前缀命中率极高
- **RAG 应用**：共享相同文档前缀的请求
- **需要最大化 KV cache 复用的场景**

---

### 2.3 DFS-Weight — 深度优先搜索加权（Cache-Aware）

**枚举定义**：`CacheAwarePolicy.DFS_WEIGHT`
**需要 Tree Cache**：是

**原理**：以 RadixTree 为基础，**沿着同一棵子树深度优先**收集请求，确保相邻请求共享尽可能多的前缀路径。

**核心逻辑**（`schedule_policy.py:259-278`）：

```python
def _sort_by_dfs_weight(waiting_queue, tree_cache):
    # Step 1: 将请求按其 last_node（RadixTree中匹配到的最后节点）分组
    last_node_to_reqs = defaultdict(list)
    for req in waiting_queue:
        last_node_to_reqs[req.last_node].append(req)

    # Step 2: 计算每个节点的权重（包含的请求数量，递归累加子节点）
    node_to_weight = defaultdict(int)
    for node in last_node_to_reqs:
        node_to_weight[node] = len(last_node_to_reqs[node])
    _calc_weight(tree_cache.root_node, node_to_weight)

    # Step 3: DFS 遍历，权重大的子树优先
    _get_dfs_priority(tree_cache.root_node, node_to_weight, last_node_to_reqs, waiting_queue)
```

**权重计算**（递归累加子节点权重）（`schedule_policy.py:348-351`）：

```python
def _calc_weight(cur_node, node_to_weight):
    for child in cur_node.children.values():
        _calc_weight(child, node_to_weight)
        node_to_weight[cur_node] += node_to_weight[child]
```

**DFS 遍历收集**（`schedule_policy.py:354-366`）：

```python
def _get_dfs_priority(cur_node, node_to_priority, last_node_to_reqs, q):
    children = [child for child in cur_node.children.values()]
    children.sort(key=lambda x: -node_to_priority[x])  # 权重大的子树优先
    for child in children:
        _get_dfs_priority(child, node_to_priority, last_node_to_reqs, q)
    q.extend(last_node_to_reqs[cur_node])  # 收集挂在该节点上的请求
```

**与 LPM 的区别**：

| 维度 | LPM | DFS-Weight |
|------|-----|-----------|
| 排序依据 | 单个请求的前缀匹配长度 | 子树中的请求总量 |
| 效果 | 长前缀请求先调度 | **同一路径的请求连续调度** |
| 优势 | 简单直接 | 更好的 cache 局部性 |
| 计算开销 | O(N) 排序 | O(N + Tree) 遍历 |

**适用场景**：
- **Tree cache 层次深**且多请求分布在相同子树
- 需要**极致的 cache 局部性**
- 比 LPM 更精细的 cache 复用优化

---

### 2.4 LOF — 最长输出优先

**枚举定义**：`CacheAgnosticPolicy.LOF`
**需要 Tree Cache**：否

**原理**：按 `max_new_tokens` 降序排序，输出越长的请求越先调度。

**核心逻辑**（`schedule_policy.py:281-295`）：

```python
def _sort_by_longest_output(waiting_queue, enable_priority_scheduling, priority_sign):
    if enable_priority_scheduling:
        waiting_queue.sort(
            key=lambda x: (
                x.priority * priority_sign,
                -x.sampling_params.max_new_tokens,
            )
        )
    else:
        waiting_queue.sort(key=lambda x: -x.sampling_params.max_new_tokens)
```

**适用场景**：
- **短输出请求多**时，先处理长输出请求，避免长请求被饥饿
- 配合优先级调度，可实现"高优先级长请求优先"
- 对**长文本生成**场景（如文档摘要、代码生成）有利

---

### 2.5 Random — 随机调度

**枚举定义**：`CacheAgnosticPolicy.RANDOM`
**需要 Tree Cache**：否

**原理**：随机打乱等待队列。

**核心逻辑**（`schedule_policy.py:298-300`）：

```python
def _sort_randomly(waiting_queue):
    random.shuffle(waiting_queue)
```

**适用场景**：
- 基准测试/对比实验
- 避免特定模式的请求集中处理导致 bias
- 生产环境一般不使用

---

### 2.6 Routing-Key — 路由键频率优先

**枚举定义**：`CacheAgnosticPolicy.ROUTING_KEY`
**需要 Tree Cache**：否

**原理**：统计当前 running batch 中各 `routing_key` 的出现频率，**优先调度与正在运行的请求具有相同 routing_key 的新请求**。

**核心逻辑**（`schedule_policy.py:315-341`）：

```python
def _sort_by_routing_key(waiting_queue, running_batch):
    # 统计 running batch 中各 routing_key 出现次数
    routing_key_counts = Counter(
        r.routing_key for r in running_batch.reqs if r.routing_key
    )

    if not routing_key_counts:
        return  # 无 routing_key，不做排序

    def sort_key(req):
        key = req.routing_key
        if key and key in routing_key_counts:
            count = routing_key_counts[key]
            return (0, -count, key)   # 有匹配的 routing_key 优先，频率越高越优先
        else:
            return (1, 0, key or "")   # 无匹配的排后面

    waiting_queue.sort(key=sort_key)
```

**排序效果**：
1. 与 running batch 有相同 routing_key 的请求排在前面
2. 在匹配的请求中，routing_key 出现频率越高越优先
3. 无匹配 routing_key 的请求排在后面

**适用场景**：
- **PD 分离架构**中，同一类请求最好集中在同一个 prefill 节点
- **多租户场景**：按 tenant routing_key 聚合，提高 cache 命中率
- **前缀模式多样的场景**：无法用 LPM 统一前缀时，按 routing key 聚类也能提升效率

---

### 2.7 策略对比总览

| 策略 | 类型 | 需要Tree Cache | 核心排序指标 | 最佳场景 |
|------|------|--------------|-------------|---------|
| **FCFS** | Agnostic | 否 | 请求到达时间 | 通用在线服务、默认场景 |
| **LPM** | Aware | 是 | 前缀匹配长度 | 多轮对话、RAG 应用 |
| **DFS-Weight** | Aware | 是 | 子树权重（DFS） | 深层前缀复用、极致 cache 局部性 |
| **LOF** | Agnostic | 否 | max_new_tokens | 长文本生成、避免长请求饥饿 |
| **Random** | Agnostic | 否 | 随机 | 测试/基准对比 |
| **Routing-Key** | Agnostic | 否 | routing_key 频率 | 多租户、PD 分离架构 |

---

### 2.8 优先级调度（Priority Scheduling）

**配置参数**：
- `--enable-priority-scheduling`：开启优先级调度
- `--schedule-low-priority-values-first`：低优先级值先调度（默认否）
- `--priority-scheduling-preemption-threshold`：抢占阈值（默认10）
- `--default-priority-value`：默认优先级值

**限制**（`server_args.py:6519-6522`）：优先级调度仅支持 `fcfs` 和 `lof` 策略：

```python
if self.enable_priority_scheduling:
    assert self.schedule_policy in ["fcfs", "lof"], \
        "To use priority scheduling, schedule_policy must be 'fcfs' or 'lof'."
```

**抢占机制**（`schedule_policy.py:894-963`）：当高优先级请求到达但 GPU 内存不足时，可以抢占正在运行的低优先级请求：

```python
def preempt_to_schedule(self, req, server_args):
    # 按优先级排序正在运行的请求
    sorted_running_reqs = sorted(valid_running_reqs, key=lambda x: (x.priority * (-priority_sign), ...))

    # 收集可抢占的低优先级请求
    for running_req in sorted_running_reqs:
        priority_diff = (req.priority - running_req.priority) * (-priority_sign)
        if priority_diff > self.priority_scheduling_preemption_threshold:
            preemptible_reqs.append(running_req)
            # 累计释放的 token 是否足够
            min_tokens_to_remove -= self._get_running_request_total_token_offset(running_req)
            if min_tokens_to_remove <= 0:
                break
        else:
            break
```

---

## 三、Radix Cache 淘汰策略（Eviction Policy）

决定当 GPU 内存不足时，**淘汰哪些 KV cache**。

**配置参数**：`--radix-eviction-policy`（默认 `lru`）
**CLI 可选值**：`lru`、`lfu`、`slru`
**代码内部全部支持**：`lru`、`lfu`、`slru`、`fifo`、`mru`、`filo`、`priority`
**代码位置**：`python/sglang/srt/mem_cache/evict_policy.py:10-66`

所有淘汰策略继承自 `EvictionStrategy` 基类，实现 `get_priority(node)` 方法，**返回值越小越先被淘汰**。

---

### 3.1 LRU — 最近最少使用（默认）

```python
class LRUStrategy(EvictionStrategy):
    def get_priority(self, node):
        return node.last_access_time
```

**淘汰顺序**：最近最少访问的节点先淘汰

**适用场景**：通用默认策略，适合大多数在线服务场景。对于具有时间局部性的访问模式效果最好。

---

### 3.2 LFU — 最少频率使用

```python
class LFUStrategy(EvictionStrategy):
    def get_priority(self, node):
        return (node.hit_count, node.last_access_time)
```

**淘汰顺序**：先按访问次数升序，次数相同的再按 LRU

**适用场景**：热点数据集中的场景。某些前缀被频繁访问（如热门 system prompt），LFU 能保护这些高频 cache 不被淘汰。

---

### 3.3 SLRU — 分段最近最少使用

```python
class SLRUStrategy(EvictionStrategy):
    def __init__(self, protected_threshold=2):
        self.protected_threshold = protected_threshold

    def get_priority(self, node):
        is_protected = 1 if node.hit_count >= self.protected_threshold else 0
        return (is_protected, node.last_access_time)
```

**淘汰顺序**：分为两个段——
- **Probationary（试用期，segment=0）**：`hit_count < 2` 的节点，**优先被淘汰**
- **Protected（保护区，segment=1）**：`hit_count >= 2` 的节点，只有试用期节点全部淘汰后才会被淘汰
- 同一段内按 LRU 排序

**适用场景**：需要保护热点 cache 的场景。新插入的 cache 不会立即被保护，只有被访问过至少 2 次才进入保护区，避免一次性请求的 cache 挤占热点 cache 的空间。

---

### 3.4 FIFO — 先进先出

```python
class FIFOStrategy(EvictionStrategy):
    def get_priority(self, node):
        return node.creation_time
```

**淘汰顺序**：最早创建的节点先淘汰

**适用场景**：简单场景，不考虑访问模式，适合 cache 生命周期均匀的场景。

---

### 3.5 MRU — 最近最多使用

```python
class MRUStrategy(EvictionStrategy):
    def get_priority(self, node):
        return -node.last_access_time
```

**淘汰顺序**：最近访问的节点先淘汰

**适用场景**：特殊访问模式，如一次性的大请求（访问一次后不再复用），淘汰最近访问的反而更合理。

---

### 3.6 FILO — 先进后出

```python
class FILOStrategy(EvictionStrategy):
    def get_priority(self, node):
        return -node.creation_time
```

**淘汰顺序**：最近创建的节点先淘汰

**适用场景**：特殊场景，新创建的 cache 更容易被淘汰，保护老 cache。

---

### 3.7 Priority — 优先级感知淘汰

```python
class PriorityStrategy(EvictionStrategy):
    def get_priority(self, node):
        return (node.priority, node.last_access_time)
```

**淘汰顺序**：先按 priority 升序（低优先级先淘汰），同优先级按 LRU

**适用场景**：多租户差异化服务，高优先级用户的 cache 被保护，低优先级用户的 cache 优先淘汰。

---

### 3.8 淘汰策略对比总览

| 策略 | CLI 可选 | 优先级公式 | 保护热点 | 适用场景 |
|------|---------|-----------|---------|---------|
| **LRU** | 是 | `last_access_time` | 中 | 通用默认 |
| **LFU** | 是 | `(hit_count, last_access_time)` | 强 | 热点集中 |
| **SLRU** | 是 | `(segment, last_access_time)` | 强 | 新老 cache 分离保护 |
| **FIFO** | 否 | `creation_time` | 无 | 简单均匀场景 |
| **MRU** | 否 | `-last_access_time` | 无 | 一次性访问模式 |
| **FILO** | 否 | `-creation_time` | 无 | 保护老 cache |
| **Priority** | 否 | `(priority, last_access_time)` | 按优先级 | 多租户差异化 |

---

## 四、Prefill Delayer 协调机制

**配置参数**：`--enable-prefill-delayer`
**代码位置**：`python/sglang/srt/managers/prefill_delayer.py`

这不是一个独立的调度算法，而是一个**跨 DP rank 的协调机制**，用于控制"是否允许现在做 prefill"。

**解决的问题**：在 DP Attention 场景下，不同 rank 的 prefill 进度不同步可能导致 GPU 空闲。

### 4.1 决策逻辑

```
收集所有 DP rank 的状态
  │
  ├── 所有 rank 都可以 prefill（"all"）→ 允许
  ├── 所有 rank 都不能 prefill（"none"）→ 允许（反正没影响）
  └── 部分 rank 可以 prefill（"mixed"）
      ├── token 使用率低于低水位 → 允许
      ├── 延迟次数超过 max_delay_passes（默认30）→ 允许
      └── 否则 → 延迟本次 prefill
```

### 4.2 关键配置

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `--prefill-delayer-max-delay-passes` | 30 | 最大延迟次数，超过后强制允许 prefill |
| `--prefill-delayer-token-usage-low-watermark` | None | token 使用率低于此阈值时强制允许 prefill |
| `--prefill-delayer-forward-passes-buckets` | None | forward pass 数量分桶（监控用） |
| `--prefill-delayer-wait-seconds-buckets` | None | 等待时间分桶（监控用） |

**适用场景**：`DP Attention` 模式下减少 GPU 空闲时间。

---

## 五、PrefillAdder — 请求准入控制

**代码位置**：`python/sglang/srt/managers/schedule_policy.py:375-964`

PrefillAdder 不是调度算法，而是在调度策略确定顺序后，**计算 token 预算并决定哪些请求能进入 batch** 的准入控制器。

### 5.1 核心预算计算

```python
@property
def rem_total_tokens(self):
    # 可用 token = 池中可用 + 可淘汰的 - 运行中请求的预留
    available_and_evictable = (
        self.token_to_kv_pool_allocator.available_size()
        + self.tree_cache.evictable_size()
    )
    return available_and_evictable - self.rem_total_token_offset
```

### 5.2 请求准入判断

对于每个请求，检查：
1. **总 token 预算**：`extend_input_len + max_new_tokens + page_size <= rem_total_tokens`
2. **输入 token 预算**：`extend_input_len <= rem_input_tokens`
3. **Chunk prefill 预算**：如果启用 chunked prefill，还需检查 chunk 内剩余 token
4. **SWA 预算**：如果启用 Sliding Window Attention，还需检查 SWA 内存
5. **最大请求数**：`max_running_requests` 限制
6. **Prefill Delayer**：是否允许本次 prefill

### 5.3 相关环境变量

| 环境变量 | 默认值 | 含义 |
|---------|--------|------|
| `SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION` | 4096 | 裁剪 max_new_tokens 估算，避免调度过于保守 |
| `IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD` | 32 | In-batch 前缀缓存检查阈值 |
| `IN_BATCH_PREFIX_CACHING_DEPRIORITIZE_THRESHOLD` | 32 | In-batch 前缀缓存降优先级阈值 |

---

## 六、三层机制协同工作流

```
请求到达 → 加入等待队列
              │
              ▼
     ┌─── Schedule Policy ──────────────┐
     │  FCFS / LPM / DFS-Weight / LOF / │  ← 决定请求进入 batch 的顺序
     │  Random / Routing-Key            │
     └───────────┬──────────────────────┘
                 ▼
     ┌─── Prefill Delayer ──────────────┐
     │  跨 DP rank 协调                  │  ← 决定是否允许本次 prefill
     │  (仅 DP Attention 场景生效)       │
     └───────────┬──────────────────────┘
                 ▼
     ┌─── PrefillAdder ─────────────────┐
     │  Token 预算计算                   │  ← 决定能装下多少请求
     │  Chunked Prefill 分块             │
     │  内存充足性检查                   │
     │  优先级抢占（可选）               │
     └───────────┬──────────────────────┘
                 ▼
     ┌─── Eviction Policy ──────────────┐
     │  LRU / LFU / SLRU / ...          │  ← 内存不足时淘汰哪些 cache
     └───────────┬──────────────────────┘
                 ▼
         执行 Prefill Forward Pass
```

---

## 七、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `managers/schedule_policy.py` | 80-94 | 调度策略枚举定义 |
| `managers/schedule_policy.py` | 96-367 | SchedulePolicy 类（排序逻辑） |
| `managers/schedule_policy.py` | 375-964 | PrefillAdder 类（准入控制） |
| `managers/schedule_policy.py` | 894-963 | 优先级抢占机制 |
| `managers/scheduler.py` | 2412-2529 | get_new_batch_prefill（调度入口） |
| `managers/scheduler.py` | 972-999 | init_schedule_policy |
| `managers/prefill_delayer.py` | 37-234 | PrefillDelayer（DP 协调） |
| `managers/prefill_delayer.py` | 237-264 | PrefillDelayerSinglePassExecutor |
| `mem_cache/evict_policy.py` | 10-66 | 所有淘汰策略实现 |
| `mem_cache/radix_cache.py` | 313-331 | 淘汰策略初始化 |
| `mem_cache/radix_cache.py` | 582-609 | 执行淘汰 |
| `server_args.py` | 350 | schedule_policy 默认值 |
| `server_args.py` | 361 | radix_eviction_policy 默认值 |
| `server_args.py` | 4279-4291 | CLI 参数定义 |

---

## 八、选型建议

### 按应用场景选择调度策略

| 应用场景 | 推荐 Schedule Policy | 推荐 Eviction Policy | 原因 |
|---------|---------------------|---------------------|------|
| 通用在线服务 | `fcfs` | `lru` | 公平、简单、高效 |
| 多轮对话 | `lpm` | `lru` | 共享 system prompt，最大化 cache 复用 |
| RAG 应用 | `lpm` 或 `dfs-weight` | `slru` | 文档前缀复用 + 保护热点文档 cache |
| 多租户服务 | `routing-key` + 优先级 | `priority` | 按租户聚合 + 差异化 cache 保护 |
| PD 分离架构 | `routing-key` 或 `lpm` | `lru` | 按请求类型聚合，减少 KV 传输开销 |
| 长文本生成 | `lof` | `lru` | 避免长请求饥饿 |
| 基准测试 | `random` | 任意 | 消除调度 bias |

### 环境变量调优

| 环境变量 | 调优方向 |
|---------|---------|
| `SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION` | 长输出场景可调大（如8192），避免调度过于保守 |
| `IN_BATCH_PREFIX_CACHING_CHECK_THRESHOLD` | 前缀复用场景可调大（如64），增强批内 cache 去重 |
| `SGLANG_ROUTING_KEY_POLICY_DEBUG_LOG` | 设为 1 可开启 routing-key 策略调试日志 |
