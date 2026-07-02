# Mooncake:Prefill 阶段是否也执行 KVCache 卸载?

> 基于 Mooncake 源码与 `docs/source/` 设计文档梳理。结论:**会,而且 prefill 阶段恰恰是 KVCache "卸载/搬运"最密集、收益最大的环节**——只是方向和模式与 decode 不同。关键在于区分"应用侧的 phase 行为"与"Mooncake Store 本身的机制"。

---

## 1. 核心结论

| 问题 | 回答 |
|------|------|
| Prefill 阶段有没有 KVCache 卸载/搬运? | **有**,且是主战场 |
| 与 decode 的区别? | Prefill 以**读**(prefetch 复用前缀)、**跨节点迁移**(PD 分离)为主;decode 以**增量写**为主 |
| 谁区分 prefill/decode? | **只有应用层**(SGLang HiCache / vLLM KV connector)区分 |
| Mooncake Store 本身区分吗? | **不区分**。Store 只看 `Put`/`Get` 的 KV 对象,DRAM↔SSD 卸载管道完全 phase-agnostic |

---

## 2. 应用侧:Prefill 的 KV 操作比 decode 更重

HiCache 工作流(`docs/source/design/hicache-design.md:23`)把每个请求的 KV 操作拆成三步,其中前两步主要发生在 **prefill**:

> "...first searches the local L1/L2 caches... For parts not found locally, it attempts to **prefetch from L3**. ... **Once the prefill computation is complete, the system considers storing the newly generated data into L2 or L3.**"

### 2.1 各阶段 KV 操作对照

| 阶段 | 主要 KV 操作 | 方向 | 说明 |
|------|------------|------|------|
| **Prefill** | **Prefetch(读)** L3→L2 | 拉 | 跳过前缀重算;prefill 节点是前缀 KV 的**主要消费者 + 生产者** |
| **Prefill(完成后)** | **Write-back(写)** L1→L2→L3 | 推 | 把新算出的前缀 KV 存回,供后续/跨实例复用 |
| **Prefill(PD 分离)** | **KV 迁移** prefill→decode 节点 | 推 | 经 TransferEngine 把完整前缀 KV 交给 decode 节点 |
| **Decode** | **Write-back(写)** 逐 token | 推 | 每步增量产出小段 KV,写回 L2/L3 |

### 2.2 Prefill 是"读密集"

prefetch 用 RDMA 从多个远端节点**并行读** L3,把命中前缀拉回 L2/GPU,避免重算长 prompt(`hicache-design.md:47`)。这正是"卸载"在 prefill 的核心价值:把存起来的 KV 重新拉回来复用。三种终止策略专为 prefill 调度而设计(`:49-67`):

- `best_effort`:GPU 能算就立刻终止,零等待,适合延迟极敏感场景。
- `wait_complete`:必须等所有 prefetch 完成,适合追求高命中率。
- `timeout`:超时或完成即止,兼顾延迟与命中率(实战最常用)。

动态超时:`timeout = prefetch_timeout_base + prefetch_timeout_per_ki_token * num_token_to_fetch / 1024`。

### 2.3 Prefill 结束后也是"写"

新生成的前缀 KV 经 `write_backup_storage` → `backup_queue` → `backup_thread_func` 异步写回 Mooncake(`:83`),且针对 **MLA 模型有专门优化**:只让一个 rank 写回,避免跨 rank 冗余存储(`:114`)。

### 2.4 Prefill 还做计算-传输重叠

prefill 期间 CPU→GPU 搬 KV 时,N+1 层加载与 N 层计算重叠(`:111`),prefill 专属优化。

文档第 120 行明确:

> In the PD-disaggregation deployment mode, HiCache can be enabled on the **Prefill nodes** to optimize prefill performance... HiCache can also be enabled on the decode nodes to write computation results back to L3.

---

## 3. PD 分离:Prefill 的 KV 迁移是单独一类操作

XpYd(多 prefill + 多 decode)分离架构下,prefill 节点算完整个前缀后,把**完整 KV 经 TransferEngine 迁移到 decode 节点**(`docs/source/design/transfer-engine/efa_transport.md`、`benchmarks/xypd_benchmarks/proxy_demo.py`)。这是 prefill 阶段独有的、体量最大的"KV 卸载/搬运"——直接点对点传,通常不经 Store 的 SSD 层。

要点(`efa_transport.md`):

- prefill 与 decode 各自启动,经 router 前置调度。
- `--disaggregation-mode prefill` / `decode`。
- prefill/decode host 必须用**对外可达 IP**,不能用 `127.0.0.1`(否则 KV 握手 `Connection refused`)。

---

## 4. 关键区分:Mooncake Store 本身不区分 prefill/decode

这是最容易误解的地方。前面讲的 DRAM↔SSD 卸载子系统(`ReplicaType`、`FileStorage` 心跳落盘、LRU/FIFO 驱逐)是**完全 phase-agnostic 的**:

- Store 只看到 `Put` / `Get` 的 KV cache 对象,**不知道也不关心**它来自 prefill 还是 decode。
- 内存高水位触发的 `offload-on-evict`、SSD 命中后的 promotion、驱逐策略——全部按**对象的访问热度/lease**,而非按 phase 决策。
- prefill 产出的前缀 KV 和 decode 产出的增量 KV,**进了 Store 之后走的是同一条 SSD 卸载管道**。差异纯粹由应用侧驱动(谁调 Put/Get、什么时机、什么粒度)。

### 4.1 全景图

```
        Prefill 节点                         Decode 节点
   ┌──────────────────┐                ┌──────────────────┐
   │ prefill 计算       │                │ decode 计算       │
   │  │ prefetch ◀─────┼────────────────┼─── (读 L3 前缀)   │
   │  ▼                 │  PD 迁移 KV    │  ▲                │
   │ write-back(写)──┼───────────────▶──┼──┘               │
   └────────┬─────────┘                └────────┬─────────┘
            │ Put                                  │ Put
            ▼                                      ▼
        ┌────────── Mooncake Store(L3,phase-agnostic)─────────┐
        │  分布式内存(DRAM)── offload-on-evict ──▶ 本地 SSD    │
        │       ▲──── promotion(命中回升)────▲                 │
        └──────────────────────────────────────────────────────┘
```

---

## 5. 对应到 vLLM / SGLang 的术语

框架侧把 phase 拆开,各接 Mooncake 的读写接口:

- **vLLM KV connector**:
  - `AsyncKVLoader` —— prefill 读(prefetch)。
  - `AsyncKVWriter` —— decode/产出写。
- **SGLang HiCache**:
  - `prefetch_thread_func` / `prefetch_io_aux_func` —— 读。
  - `write_backup`(L1→L2)/ `write_backup_storage`(L2→L3)—— 写。
  - prefill 节点和 decode 节点都可开启,角色不同:prefill 偏读+迁移,decode 偏写。

---

## 6. 一句话总结

> Prefill 阶段不仅有 KVCache 卸载,而且是**主战场**:prefill 以**读**(prefetch 复用前缀)和**跨节点迁移**(PD 分离)为主,decode 以**增量写**为主。但这些差异**只存在于应用层**(SGLang/vLLM);**Mooncake Store 的内存↔SSD 卸载管道本身不区分 phase**,任何 KV 对象都按热度走同一条 LRU/落盘/晋升路径。

---

## 7. 关键源码 / 文档索引

| 关注点 | 位置 |
|---|---|
| HiCache 工作流(prefetch / write-back) | `docs/source/design/hicache-design.md:23,37,69` |
| Prefetch 多线程 + RDMA 并行读 | `docs/source/design/hicache-design.md:47` |
| Prefetch 三种终止策略 + 动态超时 | `docs/source/design/hicache-design.md:49-67` |
| Write-back 异步队列 | `docs/source/design/hicache-design.md:79-85` |
| MLA write-back 优化 | `docs/source/design/hicache-design.md:114` |
| Prefill 计算-传输重叠 | `docs/source/design/hicache-design.md:111` |
| PD 分离 prefill/decode 角色 | `docs/source/design/hicache-design.md:116-120` |
| PD 分离部署(EFA) | `docs/source/design/transfer-engine/efa_transport.md:527-648` |
| XpYd 分离 prefilling demo | `benchmarks/xypd_benchmarks/proxy_demo.py` |
| Store phase-agnostic 的 Put/Get API | `mooncake-integration/store/store_py.cpp` |
| Store 卸载子系统(与 phase 无关) | `mooncake-store/src/file_storage.cpp`、`master_service.cpp` |
