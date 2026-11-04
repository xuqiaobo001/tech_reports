# SGLang HiCache TTFT 机制源码分析与三方案对比报告

> 分析日期：2026-11-03
> 分析对象：SGLang 源码 sgl-project/sglang（本地路径 `/root/sglang_report/sglang/`）
> 分析范围：HiCache（hierarchical cache）与 TTFT 相关的全部机制——分层命中路径、写回时机、加载与调度重叠、page 粒度、MooncakeStore L3 组合、昇腾 NPU 适配；并给出与 vLLM APC / vLLM+Mooncake 的三方案 TTFT 对比
> 部署场景：单实例 GLM 5.3（GPU/NPU 在线推理，OpenAI 兼容服务）

---

## 一、结论摘要

1. **三方案的 TTFT 上限完全相同**：前缀 KV 命中设备侧（GPU/NPU 显存）本地缓存时，SGLang radix cache 与 vLLM APC 都是显存内零拷贝复用，免 prefill，TTFT 最优。任何层次化缓存都不能让"最好情况"更快。
2. **方案 3（SGLang HiCache）与方案 2（vLLM + Mooncake）本质是同一类优化**——在设备侧缓存之上增加 host DRAM 兜底层，把"本地未命中 → 全量重算"改写为"本地未命中 → H2D 加载"。差异在工程实现：
   - **HiCache 是进程内实现**：host 池是 scheduler 进程内的 pinned memory，无外部组件，一个 `--enable-hierarchical-cache` 参数开启；
   - **Mooncake 是进程外分布式 store**：需要 `mooncake_master` 等组件，换来跨实例共享、SSD 分层、重启存活能力。
3. **单次 host 层命中的 TTFT 惩罚，HiCache 通常更小**（源码级依据见第三节）：
   - 专用 NPU 传输 kernel（`kernel_ascend`），host 池 pinned memory 直接 H2D；
   - **加载与 prefill forward 逐层重叠**（`LayerDoneCounter` producer/consumer 事件机制），传输延迟大部分被计算隐藏；
   - host 命中直接**抵扣 prefill token 准入预算**（`real_input_tokens = extend_input_len - host_hit_length`），更多请求可提前进入批次，降低排队延迟。
4. **兜底容量上限与持久化，Mooncake 更强**：HiCache host 层默认 `hicache_ratio=2.0`（host 容量 = GPU 池 2 倍，可调），且随进程消亡；Mooncake store 可达数百 GB～TB、跨节点、独立于 vLLM 进程。单实例场景这些优势打折，多实例场景反转。
5. **昇腾生态成熟度差异显著**：SGLang 的 NPU backend 对 HiCache 是一等支持（自动切换 `kernel_ascend` I/O 后端与专用内存布局）；vLLM-Ascend 的 `MooncakeStoreConnector`（store 模式）无官方部署样例，需自行 PoC。
6. **SGLang HiCache 是方案 2 能力的超集**：`--hicache-storage-backend mooncake` 可把 Mooncake 挂为 L3 层（还支持 file/hf3fs/lmcache 等），即"方案 3 可以长成方案 2"。

---

## 二、架构背景：HiCache 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│                      HiRadixCache                            │
│                                                              │
│  L1: GPU/NPU radix tree（RadixCache 继承）                   │
│      显存 KV 池，命中=零拷贝复用（与 vLLM APC 等价）          │
│         │ 驱逐时保留 host_value（backuped 节点）              │
│         ▼                                                    │
│  L2: Host memory pool（MHATokenToKVPoolHost）                │
│      进程内 pinned DRAM，hicache_ratio=2.0×GPU 池             │
│      write_through 默认开启（prefill 后异步 D2H 备份）        │
│         │ 可选：--hicache-storage-backend                    │
│         ▼                                                    │
│  L3: Storage backend（可选，默认关闭）                        │
│      file / mooncake / hf3fs / lmcache / nixl / ...          │
│      请求在 waiting queue 时即预取到 L2（prefetch）           │
└─────────────────────────────────────────────────────────────┘
```

关键类：`HiRadixCache(RadixCache)`（`python/sglang/srt/mem_cache/hiradix_cache.py:65`）在 GPU radix 树之上扩展 host 层：每个 `TreeNode` 额外携带 `host_value`（host 池索引）、`backuped`/`evicted` 状态，形成"GPU 驱逐后降级到 host、host 驱逐后彻底删除"的两级生命周期。

---

## 三、详细分析（源码级）

### 3.1 命中路径：一次 match_prefix 同时返回设备命中与 host 命中

`hiradix_cache.py:1207-1243` `match_prefix()`：

```python
value, last_node = self._match_prefix_helper(self.root_node, key)  # GPU 命中
host_hit_length = 0
last_host_node = last_node
while last_node.evicted:                       # 节点被挤出 GPU 但 host 有备份
    host_hit_length += len(last_node.host_value)
    last_node = last_node.parent
while not last_host_node.backuped:
    last_host_node = last_host_node.parent
return MatchResult(device_indices=value, ..., host_hit_length=host_hit_length)
```

调度器拿到 `host_hit_length` 后即知道"有多少 token 可以从 host 捞回来"，这是 TTFT 优化的起点。

### 3.2 加载路径①：准入时发起加载 + 抵扣 token 预算

`schedule_policy.py:799` 与 `schedule_policy.py:824`（`add_one_req`）：

```python
# host 命中直接抵扣 prefill token 预算 —— 更多请求可提前准入
real_input_tokens = req.extend_input_len - req.host_hit_length
...
if req.host_hit_length > 0:
    new_indices, req.last_node = self.tree_cache.init_load_back(...)
    req.prefix_indices = torch.cat([req.prefix_indices, new_indices])
    req.set_extend_input_len(len(req.fill_ids) - len(req.prefix_indices))
```

`hiradix_cache.py:940-1007` `load_back()`：沿 radix 树收集 evicted-but-backuped 节点的 host 索引，调用 `cache_controller.load()` 入队。**两个保护阈值**（`hiradix_cache.py:977`）：

- `load_back_threshold = 10`：待加载 token < 10 直接放弃加载、走重算（短前缀加载不划算）；
- `mem_quota` 检查：加载量超过当前配额则放弃，避免挤压在跑请求。

### 3.3 加载路径②：与 forward 逐层重叠（TTFT 惩罚被计算隐藏）

这是 HiCache 最核心的 TTFT 工程优化，链路如下：

1. 批次组装完成后，`scheduler.py:2621` 调用 `ready_to_load_host_cache()` → `cache_controller.start_loading()`（`cache_controller.py:749-786`）；
2. 加载在**专用 load_stream** 上执行，**逐层** H2D，每完成一层调用 `producer_event.complete(i)` 记录 device event；
3. forward 侧通过 `tp_worker.py:457` `set_hicache_consumer(batch.hicache_consumer_index)` 设置消费者，模型每层计算前 `wait(layer_index)` 等待对应层的加载完成事件（`cache_controller.py:57-105` `LayerDoneCounter`/`LayerLoadingEvent`；内存池经 `register_layer_transfer_counter` 注册，`memory_pool.py:728`）。

```
时间轴（host 命中 N 层 KV 的请求）：
load_stream:   [L0 拷贝][L1 拷贝][L2 拷贝][L3 拷贝]......
forward:          wait L0 → 计算L0 → 计算L1（L1已就绪）→ ...   ← 传输与计算流水重叠
                 ↑ TTFT 惩罚 ≈ max(整体计算时间, 整体传输时间)，
                   而非两者之和
```

### 3.4 写路径：write_through 默认异步备份，不阻塞 TTFT

写策略三种（`server_args.py:5504`），默认 `write_through`：

| 策略 | write_through_threshold | 行为 |
|---|---|---|
| `write_through`（默认） | 1 | 每次 prefill 完成即异步 D2H 备份（`_inc_hit_count` → `write_backup`，`hiradix_cache.py:702-712`） |
| `write_through_selective` | 2 | 节点被复用 ≥2 次才备份（省 host 内存/带宽，适合低复用负载） |
| `write_back` | — | 不主动备份，GPU 驱逐时写回 host |

备份在专用 write_stream 上异步执行（`cache_controller.py:663-707` `write()`/`start_writing()`），**不阻塞 prefill 与 TTFT**。host 池不足时先 `evict_host` 再重试（`hiradix_cache.py:672-683`）。

### 3.5 L3 预取：与排队等待重叠

启用 storage backend 后，请求还在 waiting queue 时即开始从 L3 预取到 L2（`hiradix_cache.py:1245-1293` `prefetch_from_storage`）：

- `prefetch_threshold` 默认 256 token（`hiradix_cache.py:561`），过短不预取；
- 预取策略 `hicache_storage_prefetch_policy` 默认 `best_effort`（`server_args.py:572`）：请求即将被调度时立即终止预取、用已到的部分（`scheduler.py:2543` `check_prefetch_progress` → `can_terminate_prefetch`，`hiradix_cache.py:1088-1125`），**不为等预取而阻塞调度**；
- 超时函数线性：`timeout_base(1s) + pages × per_page`（`hiradix_cache.py:1080-1086`）。

### 3.6 page 粒度

`page_size` 贯穿三层（`hiradix_cache.py:70`）：match 时对齐到 page（`match_prefix` 中 `page_aligned_len`），host 池按 page 组织（`page_first_direct` 等布局），传输 kernel 以 page 为单位。page 越大：传输越高效、元数据越少，但命中率边界损失越大——这是 HiCache 相比 vLLM APC（block_size=16）多出的一个调优维度。

### 3.7 与 MooncakeStore 的组合

`--hicache-storage-backend mooncake`（`server_args.py:5529-5534` choices 含 `file/mooncake/hf3fs/...`）把 Mooncake 挂为 L3。此时布局约束：mooncake 不支持 `layer_first`，自动切换（`server_args.py:3046-3066`）。典型栈：**L1 显存 radix → L2 进程内 DRAM → L3 Mooncake 分布式 store**，逐层兜底、逐层预取。

### 3.8 昇腾 NPU 一等支持

`python/sglang/srt/hardware_backend/npu/utils.py:83-88`：

```python
if args.enable_hierarchical_cache:
    args.hicache_io_backend = "kernel_ascend"
    if args.use_mla_backend():
        args.hicache_mem_layout = "page_first_kv_split"
    else:
        args.hicache_mem_layout = "page_first_direct"
```

NPU 下的 H2D/D2H 由 `transfer_kv_dim_exchange` NPU kernel 完成（`memory_pool_host.py:494/603`），I/O 后端选项 `direct/kernel/kernel_ascend`（`server_args.py:5511`）。**即 SGLang 在昇腾上开 HiCache 是官方支持路径，参数自动归一化，无需手工适配。**

---

## 四、三方案 TTFT 对比矩阵

| 情形 | 方案 1：vLLM APC | 方案 2：vLLM + Mooncake store | 方案 3：SGLang HiCache | 谁优 |
|---|---|---|---|---|
| 热前缀，KV 在显存中 | 免 prefill | 相同 | 相同（radix 零拷贝） | **三者打平** |
| 前缀算过、被挤出显存 | 全量重算 | store → 显存，与等待重叠；进程外传输引擎 | host pinned → 显存，**与 forward 逐层重叠** + 准入预算抵扣 | **方案 3 单次惩罚通常最小**，方案 2 次之 |
| 冷前缀（首次） | 全量计算 | 计算 + 异步写 store | 计算 + 异步 D2H（write_through_selective 可省） | 打平 |
| 服务重启后 | 冷启动 | store 存活，恢复快 | host 层随进程消亡；**除非加 L3（file/mooncake）** | **方案 2 优**（方案 3 需 L3 才追平） |
| 兜底容量 | 显存池 | 数百 GB～TB、SSD、跨节点 | 默认 2× 显存池（`hicache_ratio` 可调，受限于本机 DRAM） | **方案 2 优** |
| 多实例扩容 | 命中率系统性衰减 | 全局共享，不衰减 | host 层各实例独立；加 mooncake L3 后全局共享 | 方案 2 优（方案 3 + L3 追平） |
| 部署复杂度 | 最低 | 最高（mooncake_master 等组件 + PYTHONHASHSEED 约束） | **一个参数开启**，零外部组件 | 方案 3 优 |
| 昇腾落地成熟度 | 成熟（APC） | store 模式无官方样例，需 PoC | **NPU backend 官方路径（kernel_ascend）** | 方案 3 优 |

### 结论与选型建议

- **"最好情况"三者完全一样**；差异全部出现在"显存未命中"的长尾上。
- **单实例 + 昇腾 + 目标是压低更多请求的 TTFT（P50/P99）**：方案 3 综合最优——host 层命中的单次惩罚最小（逐层重叠 + NPU 专用 kernel + 准入抵扣），部署最简单，官方支持路径。前提是工作集（活跃复用前缀总量）能在 `hicache_ratio`/`hicache_size` 放大后的 host 池里装下。
- **需要超大兜底容量（工作集数百 GB）、重启不冷、或未来多实例**：方案 2 更稳；或直接 **方案 3 + `--hicache-storage-backend mooncake`**（L3），兼得两者。
- **公平性提醒**：vLLM 与 SGLang 的 TTFT 差异不止缓存——调度器、chunked prefill 策略、kernel 效率都不同。以上对比只隔离了"缓存层"变量；框架间整体 TTFT 应同机实测（SGLang 另有 `--schedule-policy cache_aware` 可提升负载下命中率，默认 `fcfs`，`server_args.py:350`）。

---

## 五、源码索引

| 主题 | 文件:行号 | 关键类/函数 |
|---|---|---|
| 分层 radix 缓存主体 | `python/sglang/srt/mem_cache/hiradix_cache.py:65` | `HiRadixCache.__init__` |
| host 命中返回 | `hiradix_cache.py:1207-1243` | `match_prefix` |
| host→GPU 加载（含阈值） | `hiradix_cache.py:940-1007` | `load_back`（`load_back_threshold=10`） |
| 写回策略判定 | `hiradix_cache.py:702-712` | `_inc_hit_count` / `write_backup` |
| L3 预取 | `hiradix_cache.py:1245-1293` | `prefetch_from_storage`（threshold 默认 256） |
| 预取终止/超时 | `hiradix_cache.py:1080-1125` | `_prefetch_timeout_check_linear_func` / `can_terminate_prefetch` |
| 异步 I/O 控制器 | `python/sglang/srt/managers/cache_controller.py:247` | `HiCacheController` |
| 写（D2H，异步流） | `cache_controller.py:663-707` | `write` / `start_writing` |
| 加载（逐层 + 事件） | `cache_controller.py:749-786` | `start_loading` |
| 层间重叠事件机制 | `cache_controller.py:57-105` | `LayerLoadingEvent` / `LayerDoneCounter` |
| forward 消费者设置 | `python/sglang/srt/managers/tp_worker.py:402-407,457` | `set_hicache_consumer` |
| 准入预算抵扣 | `python/sglang/srt/managers/schedule_policy.py:799,824` | `add_one_req`（`real_input_tokens`） |
| 批次加载启动 | `python/sglang/srt/managers/scheduler.py:2621` | `ready_to_load_host_cache` |
| L3 预取门控 | `scheduler.py:2543` | `check_prefetch_progress` |
| host 内存池（NPU kernel） | `python/sglang/srt/mem_cache/memory_pool_host.py:494,603` | `transfer_kv_dim_exchange`（H2D/D2H） |
| 昇腾自动适配 | `python/sglang/srt/hardware_backend/npu/utils.py:83-88` | `hicache_io_backend="kernel_ascend"` |
| HiCache 参数定义 | `python/sglang/srt/server_args.py:565-572,5500-5535` | ratio/size/write_policy/io_backend/storage_backend |
| 参数归一化 | `server_args.py:3000-3095` | `_handle_hicache` |
| Mooncake L3 布局约束 | `server_args.py:3046-3066` | `_resolve_storage_layout_compatibility` |
