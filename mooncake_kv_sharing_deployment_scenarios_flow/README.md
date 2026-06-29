# Mooncake KV 共享全部署场景与 TTFT/TPOT 精确流程图

> 基于 `Mooncake`（`/root/vllm_ascend/Mooncake`）与 vLLM（`/root/vllm_ascend/vllm`）源码。
> 覆盖所有典型部署场景，逐一举例并画出请求级精确流程图，标注 connector 钩子触发点、时间戳记录点、TTFT/TPOT 各阶段归属、KV 数据流方向。
> **核心结论**：开启 Mooncake 后，scheduler 在"调度"与"首 token"之间插入 `WAITING_FOR_REMOTE_KV` 阶段；命中时该阶段（KV 传输等待）被完整计入 TTFT（落在 `queued_time`），`prefill_time` 只剩未命中部分，**TPOT 基本不变**。

---

## 0. 两种 connector 与角色

| Connector | 类 | 用途 | KV 数据流 | 角色 |
|---|---|---|---|---|
| **Store** (`MooncakeStoreConnector`) | `MooncakeDistributedStore`，hash 去重 | 跨实例/前缀全局共享 | 任意节点 PUT/GET 共享池 | `kv_producer` / `kv_consumer` / **`kv_both`** |
| **TE** (`MooncakeConnector`) | TransferEngine，显存零拷贝直传 | 极致 P/D 分离 | 严格单向 P→D | `kv_producer`（P）/ `kv_consumer`（D） |

关键源码差异：
- **Store**：`save_kv_layer`/`wait_for_save`/`start_load_kv` 均 No-op，真正的 PUT 在 `get_finished()`→`KVCacheStoreSendingThread._handle_request`→`batch_put`（`store/connector.py:275-303`、`store/worker.py:509+`）；GET 在 `get_finished()`→`KVCacheStoreLoadingThread`→`batch_get_into_multi_buffers`（`store/worker.py:855`，单线程串行）。命中判定用 `LookupKeyClient.lookup`（`store/scheduler.py:85`）。
- **TE**：consumer 走 `do_remote_prefill`→`receive_kv`（`mooncake_connector.py:577-586,1643`）；producer 走 `do_remote_decode`→`send_kv_to_decode`（`mooncake_connector.py:629-635,1003`），`batch_transfer_sync_write`；bootstrap server 在 producer。

---

## 1. 通用骨架（任何 connector 共用的 scheduler 状态机）

```
[Client/API]
   │ add_request
   ▼  arrival_time = time.time()  ────────► EngineCoreEventType.QUEUED
[Scheduler · waiting 队列]
   │ schedule()
   │   ├─ get_computed_blocks()                      # 本地 APC 命中 → num_new_local_computed
   │   └─ connector.get_num_new_matched_tokens()     # 外部命中 → ext_tokens, load_kv_async
   │        (Store: LookupKeyClient.lookup / TE: do_remote_prefill)
   │
   │   num_computed = 本地命中 + 外部命中
   │
   ├──【未命中 ext_tokens=0】load_kv_async=False ──► 直接走本地 prefill（无传输等待）
   │
   └──【命中 ext_tokens>0】load_kv_async=True:
         num_new_tokens = 0                          # 本 step 不发 forward (scheduler.py:739-742)
         allocate_slots(delay_cache_blocks=True)     # 只占位不写 cache
         status = WAITING_FOR_REMOTE_KVS             # ★ 不记 SCHEDULED，不进 running
         │
         ▼  [Worker get_finished()]  ──► 发起 KV 拉取
[后台 KV 传输]  Store: batch_get_into_multi_buffers (RDMA/hostcopy)
   (TE: receive_kv ← producer)        ◄── 计入 TTFT，落在 queued_time
         │ connector_output.finished_recving
         ▼
[Scheduler] _update_from_kv_xfer_finished → finished_recving_kv_req_ids (scheduler.py:2286-2313)
   _update_waiting_for_remote_kv → cache_blocks()（写本地 cache），status → WAITING (:2219-2268)
         │
         ▼  schedule() 再调度（load_kv_async=False）
[Scheduler]  record SCHEDULED ──► scheduled_ts        # ★ 此时才记，已含上面传输时间
   num_new_tokens = num_tokens - num_computed         # 只 prefill 未命中 token
         │
         ▼
[Prefill forward]（仅未命中 token）
         │ 产出首 token
         ▼  first_token_ts  ──► TTFT = iter_ts - arrival_time (stats.py:369)
[Decode loop]（每步 1 token，无 connector 等待）
   │ … last_token_ts
   ▼ request_finished
   TPOT = (last_token_ts - first_token_ts) / (N-1)   (stats.py:455)
```

### TTFT / TPOT 归属（所有场景通用）

| 指标 | 公式 | Mooncake 影响 |
|---|---|---|
| **TTFT** | `iter_ts(首token) − arrival_time` | **命中时含 KV 传输等待**；未命中则含全量 prefill |
| `queued_time` | `scheduled_ts − queued_ts` | **含 WAITING_FOR_REMOTE_KV 传输时间**（`scheduled_ts` 推迟到传输后）|
| `prefill_time` | `first_token_ts − scheduled_ts` | 只剩未命中 token 的 prefill（变小）|
| **TPOT** | `(last−first)/(N−1)` | decode 阶段无等待，**基本不变** |

---

## 2. 场景 0（基线）：纯本地 `prefix_cache`，不开 Mooncake

```
[Client] → [单 vLLM 实例 (APC, HBM KV cache)] → 首 token → decode
```
```
arrival ─ QUEUED ─ SCHEDULED ─ prefill(全prompt,命中则跳过) ─ 首token ─ decode…
        │<── queued ──>│<──── prefill_time ────>│
        │<────────── TTFT ≈ queued + prefill ──────────>│
        命中:命中部分≈免费(只哈希查表,数据不动)        │<── TPOT ──>|
```
- **谁 PUT/GET**：无；命中只是 HBM 内哈希表 touch，数据零搬运。
- **首 token**：本实例 prefill step。
- **TTFT**：命中时逼近 1 个 decode step——这是 Mooncake 永远做不到的"免费命中"。

---

## 3. Store connector 场景（`MooncakeDistributedStore`，hash 去重）

### 3.1 场景 1：单实例 `kv_both`（跨请求/会话前缀复用）

> 用途：单实例内、本地 APC 装不下或想跨重启复用的大前缀，落分布式池（CPU/SSD）。

```
┌─────────────────────────────────────────┐
│  vLLM 实例 (kv_both)                     │
│   ┌────────────┐   PUT(batch_put)        │
│   │ GPU KV HBM │ ─────────────┐          │
│   │  + 本地 APC│              ▼          │
│   └─────┬──────┘      ┌──────────────┐   │
│         │ GET          │ MooncakeStore│   │
│   (batch_get)         │  (本机内存/SSD)│  │
│         └─────────────│  hash→block  │   │
│   WAITING_FOR_REMOTE  └──────────────┘   │
└─────────────────────────────────────────┘
```
```
请求R2(共享前缀, 上次R1已PUT):
arrival ─ QUEUED ─ lookup命中 ─ WAITING_FOR_REMOTE_KV ─ GET(host→GPU拷) ─ SCHEDULED ─ prefill(尾部) ─ 首token ─ decode…
                      │        │<── 传输(本机拷贝) ──>│
                      │        │<──── queued_time(含拷贝) ────>│<prefill>│
                      │<──────────── TTFT ─────────────────>│
  prefill后: build_connector_meta → get_finished → sending thread → batch_put 新块
```
- **谁 PUT/GET**：本实例既 PUT（新块）又 GET（命中块）；传输是 **本机** host↔GPU，无网络。
- **首 token**：本实例。
- **TTFT**：含本机拷贝时间（比网络快，但仍非免费）。

### 3.2 场景 2：多实例 `kv_both` 全连接共享（最典型"KV 共享"）

> 用途：水平扩展多个 vLLM 实例，相同前缀被 LB 打散到不同实例也能全局复用。

```
       ┌─────────── 共享 MooncakeStore (Master + 内存/SSD 池, hash去重) ───────────┐
       │                                                                          │
  实例A(kv_both) ◄────PUT/GET────►│ ◄────PUT/GET────► 实例B(kv_both)  … 实例N     │
  (GPU HBM+APC)                    │                  (GPU HBM+APC)                │
       ▲ 请求(前缀P)                │                       ▲ 请求(同前缀P)        │
       │                            │                       │ (被LB轮到B)          │
       └──────────── 网络面 (RoCE/RDMA) ────────────────────┘
```
```
请求落到B(前缀P已被A PUT过):
arrival@B ─ QUEUED ─ lookup(查Store)命中 ─ WAITING_FOR_REMOTE_KV ─ batch_get(A的内存→B的GPU,过网络)
   ─ SCHEDULED@B ─ prefill(尾部未命中) ─ 首token@B ─ decode@B…
                 │  │<────── 网络传输 (A→B) ──────>│
                 │  │<──── queued_time(含网络) ───>│<─prefill─>│
                 │<────────── TTFT@B ────────────>│
  B prefill后: PUT 新块到 Store（供后续A/其他实例复用）
```
- **谁 PUT/GET**：A PUT，B GET（任意节点间全连接）。
- **首 token**：处理请求的那个实例（B）。
- **TTFT@B**：含跨实例网络传输（store 共享相对本地 APC 的代价）。

### 3.3 场景 3：P/D 分离 + Store connector（demo benchmark 场景）

> proxy 两段式：P 端 `max_tokens=1` 同步 prefill + PUT，D 端 GET + decode。

```
[Client] → [Proxy] ──① prompt(max_tokens=1)──► [Prefill实例 (kv_producer)]
                                                  │ prefill(全prompt)
                                                  │ get_finished→sending thread→batch_put KV 到 Store
                                                  ▼ 产出1 token(丢弃)
                  ──② prompt ──────────────────► [Decode实例 (kv_consumer)]
                                                  │ lookup→batch_get KV←Store (WAITING_FOR_REMOTE_KV)
                                                  │ decode…
                                                  ▼ 首 token (流式回 Client)
```
```
客户端视角(首token来自Decode):
请求到达Proxy ─► P端 prefill(全) ─► P端 PUT KV ─► D端 GET KV(WAITING_FOR_REMOTE_KV) ─► D端 首 decode ─► 首token
   │  │<──── P端 TTFT ────>│              │<──── D端传输 ───>│<─D decode─>│
   │<──────────────── 客户端 TTFT = P prefill + D(GET + 首 decode) ─────────────────>│
  D端 TPOT = decode循环, 基本不变
```
- **谁 PUT/GET**：P 端 producer PUT，D 端 consumer GET。
- **首 token**：D 端首个 decode step（P 端那 1 个被 proxy 丢弃）。
- **TTFT**：客户端 = P prefill + D 传输 + D 首 decode，两段串行、都在关键路径。

### 3.4 场景 4：多 P + Store connector 跨 P 共享（避免重复 prefill）

> 用途：多个 prefill 实例，router 把同前缀请求打散后，靠全局池让后续实例直接 GET，不重算。

```
[Router] ─ 请求(前缀P) ─► [P#0(kv_producer)]  (前缀P首次, prefill + PUT)
       └ 请求(前缀P) ─► [P#1(kv_producer)]  (lookup命中 → GET ←Store, 跳过重算 + PUT新块)
                          共享 Store ◄────── PUT/GET ─────►  [D节点(kv_consumer)]
```
```
P#1 视角(前缀P已被P#0 PUT过):
arrival@P#1 ─ lookup命中 ─ WAITING_FOR_REMOTE_KV ─ batch_get(P#0的KV→P#1 GPU) ─ SCHEDULED ─ prefill(尾部) ─ 首token
              │<────────── 含跨P网络传输, 计入TTFT ──────────>│
  prefill后 → PUT 新块(供P#0/D复用)
```
- **谁 PUT/GET**：P#0 PUT，P#1 GET（跨 P 全连接，store 独有能力，TE 做不到）。
- **首 token**：P#1。
- **价值**：把"第二个 P 上整段重算"换成"跨 P 取 KV"，前缀够长时省 prefill。

---

## 4. TE connector 场景（`MooncakeConnector`，单向 P→D + bootstrap 配对）

### 4.1 场景 5：1P1D TE 分离（生产 P/D）

> bootstrap 在 producer 起服务，consumer 配对后单向 P→D 显存直传。

```
[Client] → [Proxy] ─①(max_tokens=1)─► [P (kv_producer, bootstrap server)]
                                          │ prefill(全) → 首 token(丢弃)
                                          │ do_remote_decode → send_kv_to_decode
                                          ▼ batch_transfer_sync_write (P GPU → D GPU, 显存直传)
                ─②(prompt)─────────────► [D (kv_consumer, bootstrap client)]
                                          │ do_remote_prefill → receive_kv (WAITING_FOR_REMOTE_KV)
                                          │ decode…
                                          ▼ 首 token → Client
   bootstrap(producer) ◄──配对(transfer_id, remote_bootstrap_addr)──► bootstrap(client)
```
```
两节点各自 TTFT:
P端: arrival@P ─ SCHEDULED ─ prefill(全) ─ 首token@P   (KV发送是 prefill 后后台线程, 不在 P 端 TTFT 关键路径)
     │<──────── P端 TTFT ≈ prefill ────────>│
D端: arrival@D ─ do_remote_prefill → WAITING_FOR_REMOTE_KV ─ receive_kv(显存直传P→D) ─ SCHEDULED ─ 首 decode ─ 首 token@D
     │  │<──── 含显存直传, 计入D端TTFT ────>│
客户端 TTFT ≈ P端prefill + D端(接收+首decode) + 跨节点RTT
```
- **谁 PUT/GET**：P PUT（显存直传），D GET（receive_kv 拉）。
- **首 token**：D 端首 decode。
- **与 Store 区别**：TE 是**显存零拷贝直传**（不经主机内存池）、**严格单向 P→D**、靠 bootstrap 配对而非 hash。

### 4.2 场景 6：多 P 多 D TE 分离 + Router（带会话亲和）

```
                  ┌────────── Router (按 kv_rank 配对 1P↔1D, 会话亲和) ──────────┐
   会话S轮1 → P#0 (kv_producer) ──KV直传──► D#0 (kv_consumer) ──► decode
   会话S轮2 → (亲和) P#0 ──(本地APC命中前缀)───KV直传──► D#0 ──► decode
   会话T轮1 → P#1 ─────────────────────────KV直传──► D#1 ──► decode
                  └──────────────────────────────────────────────────────────────┘
   注意: TE 下 P#0↔P#1、D#0↔D#1 无 KV 连接 → 跨实例前缀不复用(靠 router 亲和或本地 APC)
```
```
单条请求(1P1D对)流程同场景5; 多对并行各走各的显存直传链路:
P#i: prefill → 首 token → 后台 send_kv → D#j
D#j: receive_kv(WAITING_FOR_REMOTE_KV) → 首 decode → 首 token
```
- **谁 PUT/GET**：每对 P#i→D#j 各自直传。
- **首 token**：D#j。
- **关键**：TE 无跨同类节点 KV 共享，前缀复用**全靠 router 会话亲和 + 本地 APC**；若轮到不同 P，则整段重算（这正是场景 4 想用 store 解决的问题）。

---

## 5. 总览对照表

| 场景 | connector/角色 | KV 数据流 | 首 token 出在 | TTFT 含 | 跨同类节点共享 |
|---|---|---|---|---|---|
| 0 基线 | 无(本地 APC) | 不动 | 本实例 | queued+prefill | — |
| 1 单实例 | Store / kv_both | 本机 host↔GPU | 本实例 | +本机拷贝 | — |
| 2 多实例共享 | Store / kv_both | 任意节点 PUT/GET(网络) | 处理实例 | +跨实例网络传输 | ✅ 全连接 |
| 3 P/D+Store | Store / P-producer,D-consumer | P→Store→D | D 端 | P prefill + D 传输+decode | ✅(经池) |
| 4 多 P 共享 | Store / 多 producer | P#0→Store→P#1 | P#1 | +跨 P 网络 | ✅ |
| 5 1P1D TE | TE / P-producer,D-consumer | P 显存直传→D | D 端 | P prefill + D 接收+decode | ❌ |
| 6 多 P 多 D TE | TE / 多对 | 每对 P→D 直传 | 各 D | 各自 P prefill+D 接收 | ❌(靠亲和/APC) |

---

## 6. 共性结论

1. 所有 Mooncake 场景，**命中时 KV 传输都在 TTFT 关键路径上**，落在 `queued_time`（`scheduled_ts` 推迟到传输完成后才记录于 `scheduler.py:894`）；`prefill_time` 只剩未命中部分；**TPOT 基本不变**（decode 阶段不插入等待）。
2. 未命中（冷）则跳过 `WAITING_FOR_REMOTE_KV`，直接全量本地 prefill——此时 TTFT 含全量 prefill，不含传输。
3. Store 的独门优势是**任意节点全连接共享**（场景 2/4）；TE 的独门优势是**显存零拷贝直传、单向极简**（场景 5/6），但**不支持跨同类节点 KV 共享**。

---

## 7. 关键代码引用

| 环节 | 位置 |
|---|---|
| TTFT 记录公式 | `vllm/v1/metrics/stats.py:369` |
| arrival_time | `vllm/v1/request.py:95` |
| queued/prefill/decode 分项 + TPOT | `vllm/v1/metrics/stats.py:437-459` |
| 时间戳字段 | `vllm/v1/metrics/stats.py:211-214` |
| connector 外部命中 + 相加 | `vllm/v1/core/sched/scheduler.py:679-705` |
| `load_kv_async`→`num_new_tokens=0` | `scheduler.py:739-742` |
| 占位分配 + WAITING_FOR_REMOTE_KVS 挂起 | `scheduler.py:826-890` |
| SCHEDULED 记录点（KV 加载后才到） | `scheduler.py:894` |
| 完成信号回流 `finished_recving` | `scheduler.py:2286-2313` |
| 回 WAITING + 写本地 cache | `scheduler.py:2219-2268` |
| Store GET（get_finished 发起、串行） | `mooncake/store/connector.py:275-303`、`store/worker.py:855` |
| Store PUT（sending thread、batch_put） | `store/connector.py:297-303`、`store/worker.py:509+` |
| Store 命中判定 lookup | `mooncake/store/scheduler.py:85` |
| TE consumer 拉取（do_remote_prefill→receive_kv） | `mooncake/mooncake_connector.py:577-586,1643` |
| TE producer 发送（do_remote_decode→send_kv_to_decode） | `mooncake_connector.py:629-635,1003` |
| TE 完成信号 finished_recving_reqs | `mooncake_connector.py:1494-1524,1603` |

相关已发布报告：
- `mooncake_ttft_prefix_count_scaling_analysis/`（TTFT 随前缀种类数放大的根因）
- `vllm_prefix_cache_vs_mooncake_ttft_report/`（命中免费 vs 付网络传输）
- `mooncake_batch_transfer_sync_write_internals/`（sync 传输 6 层调用链）
- `mooncake_kv_sharing_topology_te_vs_store/`（TE 单向 vs store 全连接）

*本报告所有结论均基于上述源码路径与行号，可逐条核对。*
