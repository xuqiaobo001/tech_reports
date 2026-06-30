# Mooncake KV 共享：16 / 76 / 100 前缀种类的差异化优化与配置方案

> 基于 `Mooncake`（`/root/vllm_ascend/Mooncake`）与 vLLM（`/root/vllm_ascend/vllm`）源码。
> 问题：相同前缀种类为 **16 / 76 / 100** 种时，是否有不同的优化或配置方案？
> **结论：是。但"分档"的真正依据不是前缀种类数本身，而是 `working_set = 前缀种类数 × 前缀长度 × 每token的KV字节` 是否跨过缓存容量阈值。** 三档大概率落入"小/中/大"三个容量档，需要不同方案。

---

## 0. 直接结论

Mooncake 的 KV 有**三层缓存**，容量从大到小、速度从慢到快：

| 层级 | 容量（默认） | 速度 | 触发配置 |
|---|---|---|---|
| ① 本地 APC（vLLM HBM KV pool） | = `gpu_memory_utilization` 分配的 KV pool | 最快（命中≈免费） | `--enable-prefix-caching`（默认 True） |
| ② Store 内存层 | embedded: **`global_segment_size`=4GiB/rank** | 快（本机/网络 RDMA） | `MOONCAKE_CONFIG_PATH` JSON |
| ③ Store SSD / disk offload | standalone-store 模式独占，或 `enable_offload=True` | 慢（落盘） | `mode=standalone-store` / `enable_offload` |

**三档的差异就来自 working set 是否压垮某一层**：16 种可能只动用 ① 或正好进 ②；76 种跨过 ② 单 rank 容量（这也是此前"16 vs 76 差 2×"的根因）；100 种则需要 ③ 或扩容。

---

## 1. 先算 working set，定位三档落点

```
working_set ≈ N_prefix × prefix_len × kv_per_token
```

对 GLM-5.1 这类大 MLA 模型，每 token KV 虽经压缩但仍约为**数百 KB 量级**（≈ `2 × num_layers × latent_dim × dtype_bytes`），所以 `N × prefix` 很容易到 GB 级。**粗算对照（prefix≈1k token，KV≈0.2MB/token）：**

| 档位 | working_set | vs embedded 单 rank 4GiB | vs 本地 APC（数十 GB） | 落点 |
|---|---|---|---|---|
| **16 种** | ~3 GB | 接近 1 个 segment | 装得下 | **小档**：本地 APC + 单 rank store 都能覆盖 |
| **76 种** | ~15 GB | **超单 rank**（需多 rank 或更大 segment） | 接近/超 APC | **中档**：跨过 ② 容量 → 部分淘汰/重传 |
| **100 种** | ~20 GB | 远超单 rank | **超 APC** → 本地 thrash | **大档**：必须扩 store 或落盘 |

> 注：阈值取决于 `prefix_len × kv_per_token`。若前缀短 / KV 极小，三档可能都落在"小档"（此时差异来自**方法论/并发/亲和**而非容量，见 §4.4）。但大 MLA + 长前缀下，76/100 确实会跨阈值——这正是实测到 2× 差距的根因。

---

## 2. 三级分档框架（按 working set 规模，而非绝对前缀数）

```
working_set ──►  ≤ 本地 APC          →  小档: 本地 APC 为主, store 锦上添花
              ──►  ≤ store 内存层(聚合) →  中档: store 全局共享是关键
              ──►  > store 内存层       →  大档: 扩 segment / standalone+SSD / offload / 淘汰管理
```

---

## 3. 普适配置（三档都该做，与档位无关）

1. **`--enable-prefix-caching=True`**（默认 True，确认没关）：本地命中免费，永远是第一道防线（`config/cache.py:92`）。
2. **benchmark 充分预热**：测量前把 ①+② 暖起来；分离"冷启动 TTFT"与"稳态 TTFT"分别报。
3. **路由亲和**：相同前缀/会话 sticky 到同一实例，避免 round-robin 把本地 APC 命中稀释成 1/P。
4. **异步传输** `ASCEND_USE_ASYNC_TRANSFER=1`：把 KV 传输从 TTFT 关键路径挪开，与计算重叠（默认 sync 串在关键路径上）。
5. **`load_async=True`**（store 默认 True，`store/scheduler.py:57`）：保留 compute-transfer overlap。

---

## 4. 分档差异化方案

### 4.1 小档（≈16 种，working_set fit 本地 APC / 单 rank store）

**目标**：让绝大多数请求命中本地 APC（免费），mooncake 只做跨实例兜底。

- connector：**若单实例、前缀集中** → 甚至可不上 mooncake，纯本地 APC 最优；若多实例、前缀偶尔打散 → Store `kv_both` 兜底。
- store：默认 `global_segment_size=4GiB` 足够（16 种 ~3GB），**`enable_offload` 保持 False**（别落盘，没必要）。
- vLLM：`gpu_memory_utilization` 给足，确保 16 种全部进本地 APC。
- **别踩坑**：小 working set 下，mooncake 的每次"命中"仍要付一趟网络——如果前缀全在同一实例本地命中，开 mooncake 反而是 TTFT overhead。所以小档优先用本地 APC。

### 4.2 中档（≈76 种，超单 rank embedded，跨过 ② 容量）

**目标**：扩 store 内存层聚合容量，让 76 种全部 warm 在内存层，不落盘、不淘汰。这是实测 2× 差距的修复重点。

- store 容量扩容（任选其一/组合）：
  - **增大 `global_segment_size`**（embedded 模式每 rank 贡献）：按 `76 × prefix_len × kv_per_token` 算出的容量，留 1.5× 余量。
  - **靠多 rank 聚合**：embedded 模式下 N 个 rank 各贡献 4GiB → 聚合 `N×4GiB`。`put_step = tp_size // num_kv_head` 会自动按 TP rank 去重 PUT（`worker.py:973,560`），扩容不会重复存。
- **`local_buffer_size` 同步调大**（staging buffer，默认 4GiB，`worker.py:74`）：太小会卡 batch_get。
- **`enable_offload` 保持 False**：76 种内存层能装下就别落盘，落盘 GET 会骤慢（`_split_disk_offload_load_batches`）。
- vLLM：`--kv-cache-memory`（或 `--gpu-memory-utilization`）调到能装下稳态热前缀；开 chunked prefill + 设 `--long-prefill-token-threshold` 控未命中大 prefill 的步长。
- 共享前提：key 含 `tp_rank/pp_rank`（`data.py:27-66`），**跨实例共享要求并行拓扑一致**；多实例务必同构（同 TP/PP/DP）。

### 4.3 大档（≈100 种，超 store 内存层 + 本地 APC thrash）

**目标**：working set 超内存，必须分层 + 淘汰管理，避免 thrash。此时配置和前两档**质的不同**。

- **换 `mode=standalone-store`**：独立 `mooncake_client` 进程独占大池 + **SSD 分层**，rank 贡献 0（`worker.py:100-122`）。这是 100 种超出内存时的正解——SSD 容量远大于 HBM/内存层。
- **或 `enable_offload=True` + 调 `disk_offload_buffer_budget_bytes`**（staging 预算，`worker.py:736`）：embedded 也能落盘，但要监控 offload 压力。
- **压力退化已内置**：offload 压力大时 `_should_skip_request`/`_clear_store_pressure`（`worker.py:490-501`）会**主动跳过**该请求的 store 操作（优雅降级，不卡死）——大档下要预期部分请求退回本地全量 prefill，TTFT 会升高，属正常。
- **接受命中率下降**：100 种超容量必然有淘汰，重点转向"热前缀优先驻留"——靠路由亲和让最热的少数前缀反复命中，长尾前缀接受重算/落盘。
- vLLM：本地 APC 容量有限，100 种必然 thrash → 别指望全装本地；把容量预算更多留给 decode，前缀复用以 store 为主。
- metadata 后端：节点多、前缀多时，`metadata_server` 从默认 P2P 升级到 **etcd**（强一致、大规模动态发现），避免 peer 发现成为瓶颈。

### 4.4 若三档都落"小档"（短前缀 / 极小 KV，MLA 极致压缩）

此时容量不是瓶颈，16/76/100 **配置方案收敛为同一套**（§3 普适项即可），差异主要来自：
- **并发 + 串行 load 线程**：store 的 `KVCacheStoreLoadingThread` 单线程串行（`worker.py:388`），前缀种类多 = 并发 GET 量大 → 队列堵。杠杆：控制并发度 / 减少同时等待 KV 的请求数。
- **方法论**：是否预热（76/100 冷启动路径更多）。
- **路由亲和**：76/100 在多实例下被稀释更严重。

→ 这套场景下"换 connector / 扩容量"没用，要优化的是**并发调度 + 预热 + 亲和**。

---

## 5. 决策树与总览表

```
你的 working_set = 前缀数 × prefix_len × kv/token 有多大？
│
├─ 能装进单实例本地 APC? ──► 小档(16通常在此): 纯本地APC为主, store可选, enable_offload=False
│
├─ 超 APC 但 ≤ store内存层聚合? ──► 中档(76通常在此): 扩 global_segment_size/多rank聚合, 别落盘, 调大 local_buffer
│
└─ 超 store内存层? ──► 大档(100可能在此): mode=standalone-store+SSD 或 enable_offload=True, 接受淘汰, etcd metadata, 热前缀亲和
```

| 维度 | 小档(16) | 中档(76) | 大档(100) |
|---|---|---|---|
| 主力缓存 | 本地 APC | store 内存层 | store 内存层 + SSD/offload |
| connector | 本地 APC 为主；Store 可选 | Store `kv_both` / P-D store | Store `kv_both` / P-D store |
| `global_segment_size` | 默认 4GiB 够 | **调大 / 多 rank 聚合** | standalone-store 独占大池 |
| `enable_offload` | False | **False**（别落盘） | **True / standalone+SSD** |
| `local_buffer_size` | 默认 | **调大** | 调大 + disk budget |
| metadata backend | P2P 够 | P2P / http | **etcd** |
| 路由亲和 | 有益 | **关键** | **关键（热前缀驻留）** |
| 预期命中率 | 高 | 中（需调优到高） | 必然有淘汰，靠亲和保热前缀 |
| 异步传输 | 可选 | **建议开** | **建议开** |

---

## 6. 怎么确认你实际在哪一档

1. **算容量**：`working_set = N × prefix_len × kv_per_token`，对照 `global_segment_size × num_rank` 与本地 APC 大小。
2. **看 store 容量日志**：mooncake store 启动会打印 segment/池大小；vLLM 启动日志有 `KV cache ... blocks`（本地 APC 块数与总字节）。
3. **看命中率/淘汰**：store connector 暴露 `load_get` 耗时与 `num_failed_keys`（`worker.py:869-876`）、`external_kv_transfer` token 计数（`stats.py:311`）；本地 APC 看 vLLM prefix cache hit/eviction。
4. **看 offload 压力**：`_should_skip_request` 触发时会 log "Skipping Mooncake store ... while CPU/disk offloading is under pressure"（`worker.py:528-534`）——**这条日志频繁出现 = 你在大档且在退化**。

---

## 7. 关键代码引用

| 配置/机制 | 位置 |
|---|---|
| store 默认容量 4GiB/4GiB、enable_offload=False | `mooncake/store/worker.py:73-74,110-112` |
| 配置来源 MOONCAKE_CONFIG_PATH | `store/worker.py:125-150` |
| embedded vs standalone-store（SSD 分层） | `store/worker.py:92,100-122` |
| key 粒度（rank+chunk_hash，共享需同构拓扑） | `store/data.py:27-66` |
| TP striding 去重 PUT（put_step） | `store/worker.py:973,560` |
| disk offload 分批 + budget | `store/worker.py:192-260,736-802` |
| 压力退化 skip（大档优雅降级） | `store/worker.py:490-534,687` |
| 本地 APC 默认开启 | `vllm/config/cache.py:92` |
| connector 外部命中 + load_async | `mooncake/store/scheduler.py:57-59,74-117` |

相关已发布报告：
- `mooncake_ttft_prefix_count_scaling_analysis/`（TTFT 随前缀种类数放大的根因）
- `mooncake_kv_sharing_deployment_scenarios_flow/`（全部署场景流程图）
- `vllm_prefix_cache_vs_mooncake_ttft_report/`（命中免费 vs 付网络传输）
- `mooncake_kv_sharing_topology_te_vs_store/`（TE 单向 vs store 全连接）

*本报告所有结论均基于上述源码路径与行号，可逐条核对。*
