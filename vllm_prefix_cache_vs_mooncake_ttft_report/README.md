# vLLM Prefix Cache vs Mooncake KV Cache 共享:TTFT 提升对比与共存分析

> 基于 `vllm` 与 `Mooncake` 源码的代码级分析。
> 分析对象:`/root/vllm_ascend/vllm`(vLLM)、`/root/vllm_ascend/Mooncake`(Mooncake)。
> 关键指标:**TTFT(Time To First Token,首 token 延迟)**。

---

## 0. 结论速览

**问题一:方式一(vLLM prefix_cache)与方式二(vLLM 用 mooncake 做 KV cache 共享),哪种对 TTFT 提升更明显?**

> **单看 TTFT 指标,方式一(prefix_cache)的提升更明显、更直接、更可靠。**
> 因为一次**本地命中是"零成本"的**——KV 已经在本卡 HBM 里,直接跳过 prefill 计算,TTFT 可逼近单个 decode step。而 mooncake 哪怕"命中",也必须把整段前缀 KV 跑一趟网络(RDMA/NVLink/TCP)才能吐第一个 token,这段传输**就在首 token 的关键路径上**。
>
> 但这个结论在**跨实例、前缀被负载均衡打散**的场景下会翻转:此时方式一本地命中率塌到 ~1/N,而 mooncake 的全局共享是唯一能避免整段重算的手段。

**问题二:prefix_cache 与 mooncake 的两种机制能否同时开启?**

> - **prefix_cache + mooncake connector(任一种):✅ 能,且设计上鼓励组合**。调度器把两者相加(`num_computed_tokens = local + external`),本地命中优先、connector 补位,互不冲突、不重复计算。
> - **mooncake 两种机制彼此(P/D connector + store connector):❌ 不能在同一实例同时挂**。`kv_connector` 是单值配置,且两者是不同部署拓扑,集群级二选一。

---

## 1. 两种机制的源码本质

### 1.1 方式一:vLLM prefix_cache(本地 APC,Automatic Prefix Caching)

- **前缀匹配**:按 block 链式哈希(`hash_block_tokens`,父块哈希嵌进子块),只匹配满 block。
  - `vllm/v1/core/kv_cache_utils.py:563-708`
- **命中后扣减待计算 token**:
  ```python
  num_new_tokens = request.num_tokens - num_computed_tokens
  ```
  - `vllm/v1/core/sched/scheduler.py:748`
- **runner 在 `num_computed_tokens` 处物理切片**,前缀根本不进 forward:
  - `vllm/v1/worker/gpu_model_runner.py:2635-2660`
- **KV 物理位置:本卡 HBM**(`_allocate_kv_cache_tensors` 里 `device=self.device`):
  - `vllm/v1/worker/gpu_model_runner.py:6954-6973`
- **命中代价 ≈ 0**:只是哈希表查一次 + `touch` 增引用计数 + 从 LRU 队列摘链,**KV 数据不动**:
  - `vllm/v1/core/block_pool.py:402-417`
- **作用域:严格限于单个 vLLM worker 进程**(哈希表是 scheduler 进程里的普通 Python dict):
  - `vllm/v1/core/block_pool.py:162-171`
- **限制**:block 粒度、要求精确前缀(链式哈希,中间一个 token 不同则全断)、HBM 容量下 LRU 淘汰(`block_pool.py:365-400`)。

### 1.2 方式二:Mooncake KV Cache 共享

Mooncake 在 vLLM 中有**两种 connector**(`vllm/distributed/kv_transfer/kv_connector/v1/mooncake/`):

| Connector | 用途 | 关键代码 |
|---|---|---|
| `MooncakeConnector` | **P/D 分离**(prefill 节点算完,把 KV 推给 decode 节点) | `mooncake_connector.py`(传输 `batch_transfer_sync_write` 在 `:1365`) |
| `MooncakeStoreConnector` | **跨实例共享前缀池**(基于 `MooncakeDistributedStore`,hash 去重) | `store/{connector,worker,scheduler,data}.py` |

**关键证据——store 模式下,接收方取 KV 在首 token 关键路径上:**

- `get_num_new_matched_tokens` 返回 `load_async=True`(`store/scheduler.py:117`)
- 调度器随即把请求挂到 `WAITING_FOR_REMOTE_KVS`、该 step **`num_new_tokens=0`、不发 forward**(`vllm/v1/core/sched/scheduler.py:739-890`)
- 直到 `batch_get_into_multi_buffers`(`store/worker.py:855`)传输完成,请求才能继续跑出第一个 token

因此**接收方首 token 墙钟时间**为:

```
TTFT_receiver = 排队调度 + (整段前缀 KV 的网络传输时间) + 第一个 token 的 decode
```

它把 **"prefill 计算时间"换成了"KV 网络传输时间"**。

> **注意**:P/D 拓扑下,第一个 token 其实是在 **prefiller** 上产生的(proxy 里 `max_tokens=1`),传输发生在 prefill 之后的后台线程。所以 `MooncakeConnector` 主要优化**吞吐和 prefill/decode 干扰**,对单请求 TTFT 无直接收益。

---

## 2. TTFT 直接对比

| 维度 | 方式一 prefix_cache | 方式二 mooncake(store 模式) |
|---|---|---|
| 命中后首 token 关键路径 | **无数据搬运**,只跳过计算 | **必跑一次网络传输**(RDMA/NVLink/TCP) |
| 单次命中对 TTFT 的削减 | ∝ 命中比例,近乎 free | = prefill 时间 − 传输时间(净收益可能很小甚至为负) |
| 命中作用域 | 单实例内 | **跨实例全局**(任何实例都能取) |
| 容量 / 淘汰 | 受单卡 HBM 限制,负载下激进 LRU | 独立分布式池(可 CPU/远端 GPU/NVMe),大得多 |
| 短前缀 / 慢网络 | 稳定有益 | **可能反而抬高 TTFT**(传输 > 重算) |

**核心矛盾**:方式一的命中"免费",但作用域小(1/N);方式二作用域大(全局),但每次命中都要付网络传输费。

### 分场景判断

- **场景 A:单实例 / 重复前缀在同实例复用**(radix、system prompt、few-shot)
  → **方式一完胜**。命中即免费,TTFT 直降、逼近 decode 延迟。这是"TTFT 提升更明显"最典型的情形。

- **场景 B:多实例水平扩展、相同前缀被负载均衡打散**
  → 方式一本地命中率塌到 ~1/N,**大量 miss = 整段重算**;此时 mooncake 全局命中是**唯一能避免重算的手段**。此场景下方式二 TTFT 提升反而更明显。

- **场景 C:P/D 分离(MooncakeConnector)**
  → 强项是吞吐/尾延迟/资源解耦,不是单请求 TTFT。

---

## 3. 能否同时开启:prefix_cache × mooncake

### 3.1 prefix_cache + mooncake connector:✅ 能,且鼓励组合

调度器(`vllm/v1/core/sched/scheduler.py`)明确把两者相加:

```python
# 先算本地命中 (prefix cache)
new_computed_blocks, num_new_local_computed_tokens = (
    self.kv_cache_manager.get_computed_blocks(request))

# 再问 connector —— 把"本地命中长度"传进去
if self.connector is not None:
    ext_tokens, load_kv_async = (
        self.connector.get_num_new_matched_tokens(
            request, num_new_local_computed_tokens))   # 只看本地没覆盖的部分

# 两者相加,不分歧
num_computed_tokens = (
    num_new_local_computed_tokens + num_external_computed_tokens)
```

要点:

1. **不冲突、不重复计算**。本地命中长度被当作参数喂给 connector,connector 只返回**本地命中之外**的外部 token(`connector_prefix_cache_queries = request.num_tokens - num_new_local_computed_tokens`,`scheduler.py:697`)。

2. **本地命中天然优先 → 省网络**。Mamba hybrid 分支的注释直白说明(`scheduler.py:~656`):
   > "Using the FA hit **skips re-transferring FA blocks already cached on D-side**"
   
   即本地已有的 KV,不再从远端传一遍。**这是组合的最大收益**。

3. **prefix_cache 默认就是开的**(`config/cache.py:92` `enable_prefix_caching: bool = True`),mooncake connector 不强制要求它(mooncake 目录无任何 `enable_prefix_caching` 断言),默认配置下两者已同时生效。

**三层作用域自然叠加:**

```
L1 本地 prefix_cache   (实例内 HBM, 命中≈免费, 作用域=1实例)
        ↓ 本地 miss 的部分
L2 mooncake store      (跨实例全局池, 付网络, 容量大/命中率高)
```

### 3.2 mooncake 两种机制彼此:❌ 不能同实例叠加

`KVTransferConfig`(`vllm/config/kv_transfer.py`)的关键字段是**单值**:

```python
kv_connector: str | None = None     # 一个实例只挂一个 connector
kv_role: KVRole | None = None       # 一个实例只扮一个角色(producer/consumer/both)
```

- 同一个 vLLM 实例**只能配一个 connector**,无法让 P/D connector 与 store connector 同时跑在同一 engine 上。
- 更本质的原因:这两种机制是**不同部署拓扑**,集群级二选一:
  - **P/D 拓扑**(`MooncakeConnector`):prefill 实例 `kv_producer`、decode 实例 `kv_consumer`,router 按 `kv_rank` 配对(1P1D)。
  - **Store 拓扑**(`MooncakeStoreConnector`):所有实例 `kv_both`,往同一 `MooncakeDistributedStore` 池按 block hash 去重读写。

---

## 4. 实践建议(面向 TTFT)

**推荐组合**:每个实例 `enable_prefix_caching=True`(默认)+ mooncake store connector(`kv_both`)。

- 本地能命中 → 免费,TTFT 最优;
- 本地 miss → 从全局池捞(比整段重算快,前提是 RDMA/NVLink 且前缀足够长);
- 避开 P/D 拓扑"首 token 在 prefill 节点出、对单请求 TTFT 无直接收益"的问题。

**何时该单独用哪种:**

| 负载特征 | 推荐 |
|---|---|
| 单实例、重复前缀集中 | 仅 prefix_cache(够用、最省) |
| 多实例、相同前缀被打散 | prefix_cache + mooncake store |
| 高吞吐、消除 prefill/decode 干扰 | mooncake P/D(`MooncakeConnector`) |
| 短前缀 + 慢网络 | 谨慎用 mooncake(传输可能 > 重算,反而抬升 TTFT) |

---

## 5. 关键代码引用索引

**prefix_cache:**
- 命中计算与扣减:`vllm/v1/core/kv_cache_manager.py:202-242`(`get_computed_blocks`)、`vllm/v1/core/sched/scheduler.py:748`
- KV 落于本卡 HBM:`vllm/v1/worker/gpu_model_runner.py:6954-6973`
- 命中零搬运:`vllm/v1/core/block_pool.py:402-417`(`touch`)
- 作用域单实例:`vllm/v1/core/block_pool.py:162-171`

**mooncake:**
- P/D connector:`vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py`(传输 `:1365`)
- store connector:`.../mooncake/store/{connector,worker,scheduler,data}.py`(get `worker.py:855`、put `worker.py:648`、lookup `worker.py:1394`)
- 首 token 关键路径门控:`vllm/v1/core/sched/scheduler.py:739-890`、`2219-2268`
- connector 基类契约:`vllm/distributed/kv_transfer/kv_connector/v1/base.py`
- 传输引擎 API:`Mooncake/mooncake-transfer-engine/include/transport/transport.h:363`
- store C API:`Mooncake/mooncake-store/include/store_c.h`

**共存:**
- local + external 相加:`vllm/v1/core/sched/scheduler.py:697-705`
- connector 单值配置:`vllm/config/kv_transfer.py`(`kv_connector`、`kv_role`)
- prefix_cache 默认开启:`vllm/config/cache.py:92`

---

*本报告所有结论均基于上述源码路径与行号,可逐条核对。*
