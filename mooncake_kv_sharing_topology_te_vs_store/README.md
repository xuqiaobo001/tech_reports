# Mooncake PD 分离下 KV Cache 跨节点共享拓扑分析（TE 单向 vs Store 全连接）

> 问题：P、D 分离状态下，是否存在 KV cache 在不同 P 节点之间、不同 D 节点之间共享的情况？
> 核心结论：**取决于 connector。TE connector 严格单向 P→D，不支持 P↔P/D↔D 共享；Store connector 支持任意节点间全连接共享（含 kv_both 角色）。**

---

## 1. 答案：分两种 connector，结论完全不同

- **TE connector（`MooncakeConnectorV1`，前面所有方案用的）**：KV 共享严格单向 P→D，**P↔P 和 D↔D 都不共享**。
- **Store connector（`MooncakeStoreConnector`，带 Master 缓存池）**：任何节点都能 Put/Get 共享池，**P↔P、D↔D、P↔D 都可共享**（且有 `kv_both` 角色）。

---

## 2. TE connector：严格单向 P→D，无 P↔P / D↔D 共享

TE connector 是为**极致性能的 PD 分离**设计的，刻意做了单向简化。

### 证据 1：只有两个角色，且互斥

源码（`mooncake_connector.py:497-501`）：

```python
self.is_kv_producer = (kv_role == "kv_producer")   # P
self.is_kv_consumer = (kv_role == "kv_consumer")   # D
```

只有 `kv_producer`（P）和 `kv_consumer`（D）两个角色，**没有第三种**。而且代码里大量 `assert` 强制角色互斥（`:579/604/630/697/705`）：

```python
# D 侧逻辑(assert 不能是 producer)
assert not self.is_kv_producer
# P 侧逻辑(assert 不能是 consumer)
assert not self.is_kv_consumer
```

### 证据 2：传输方向硬编码为 P→D

- **D 主动拉**（`receive_kv`，`:1643`）：`is_kv_consumer` 才执行，D → P；
- **P 被动发**（`send_kv_to_decode`，`:1003`）：收到 D 的请求才发，P → D；
- bootstrap server 只在 **P（producer）** 上启动（`:826-829`，`should_launch_bootstrap_server`）。

**数据流只能是 P 显存 → D 显存，P 永远是源、D 永远是目的。**

### 证据 3：连接表只朝一个方向

**D 维护"到所有 P 的连接表"**（`_remote_agents`），P 注册地址供 D 查。**P 不维护到其他 P 的连接，D 不维护到其他 D 的连接**。所以 P↔P、D↔D 在 TE connector 里**根本没有连接通道**。

### TE connector 下的具体回答

| 共享场景 | TE connector 是否支持 | 原因 |
|---|---|---|
| **P→D（跨 P 节点到 D 节点）** | ✅ 支持 | 这是它的唯一职责 |
| **P↔P（不同 P 节点间共享 KV）** | ❌ 不支持 | P 只有 producer 角色，不会拉别人的 KV；P 间无连接 |
| **D↔D（不同 D 节点间共享 KV）** | ❌ 不支持 | D 只有 consumer 角色，不会发 KV；D 间无连接 |
| **D→P（反向）** | ❌ 不支持 | P 不消费、D 不生产 |

> 那 4 个 P 之间、2 个 D 之间靠什么协作？**靠上层 router 的请求调度，不靠 KV 共享**。比如同一个会话的两轮对话，router 把第一轮给 P#0，第二轮也尽量给 P#0（复用 P#0 的 prefix cache）；若给了不同 P，第二轮就要重新 prefill（除非另接 prefix cache 机制）。D 之间同理，一个会话尽量固定在一个 D 上 decode。

---

## 3. Store connector：任意节点间共享，含 P↔P / D↔D

Store connector 用**带 Master 的分布式 KV 缓存池**（Object→Replica→BufHandle 体系），共享语义完全不同。

### 证据 1：三个角色，含 `kv_both`

源码（`store/worker.py:1192,1261,1280`）：

```python
if self.kv_role in ["kv_producer", "kv_both"]:   # ← 有 kv_both!
    ...  # 启动发送线程(往共享池 Put)
```

角色有 `kv_producer` / `kv_consumer` / **`kv_both`**（既存又取）。`kv_both` 的节点**既是源又是目的**——这正是 P↔P 或 D↔D 共享的基础。

### 证据 2：Put/Get 共享池语义

Store connector 用 `batch_put` / `batch_get`（`worker.py:648/855`）往**共享缓存池**存/取 KV，key 编码 `(model_name, tp_rank, pcp_rank, dcp_rank, pp_rank)`。**任何节点都能按 key 查到任何其他节点存入的 KV**——包括 P 存的给另一个 P 取、D 存的给另一个 D 取。

### Store connector 下的具体回答

| 共享场景 | Store connector 是否支持 | 机制 |
|---|---|---|
| **P→D** | ✅ | P Put，D Get（等价 TE 的 P→D）|
| **P↔P** | ✅ | P#0 Put，P#1 Get（配置 `kv_both` 或都接 store）|
| **D↔D** | ✅ | D#0 Put（`kv_both`），D#1 Get |
| **多 P 复用同一 KV** | ✅ | 多个 P 从共享池 Get 同一份 KV（省重复 prefill）|
| **D 间迁移会话** | ✅ | D#0 把 decode 中途的 KV Put，D#1 Get 接力（KV migration）|

Store 的本质是**"中心化缓存池 + 任意节点读写"**，所以共享拓扑是**全连接**的，不受 P/D 角色限制。

---

## 4. 为什么 TE connector 不支持 P↔P / D↔D（设计取舍）

TE connector 是为**极致性能的 PD 分离**设计的，刻意做了单向简化：

1. **省控制开销**：P↔P / D↔D 共享需要额外的连接表、信令、一致性管理，TE connector 为降低延迟全部砍掉；
2. **匹配典型负载**：PD 分离的核心场景就是"prefill 完 → KV 给 decode"，单向足够；
3. **prefix cache 已由 vLLM 本地处理**：同节点内的请求复用靠 vLLM 自带的 prefix caching（`--enable-prefix-caching`），不需要跨节点。

而 Store connector 是为**"KV 缓存复用最大化"**设计的（多实例共享、淘汰、迁移），所以支持全连接共享，但代价是引入 Master、有额外延迟。

---

## 5. 对应到你的部署

前面所有方案（4P2D、方案 A）用的是 **TE connector**，所以：

- **P↔P 不共享 KV**：4 个 P 节点之间各自独立 prefill，不互相传 KV。若想跨 P 复用 prefix，要靠**上层 router 的会话亲和**（同一会话固定路由到同一 P），或换 Store connector；
- **D↔D 不共享 KV**：2 个 D 节点各自独立 decode，不互相传 KV。会话要固定在一个 D 上；
- **只有 P→D 单向**：这才是 Mooncake 在你部署里做的事。

如果确实需要 P↔P 或 D↔D 共享（如跨 P 的全局 prefix cache、D 间负载迁移），**要改用 Store connector**（`MooncakeStoreConnector`），它带 Master 缓存池支持全连接共享，但要注意：
- 引入 Master（额外组件 + 潜在单点，除非开 HA）；
- `kv_role` 可设 `kv_both`；
- KV 走共享池（Put/Get）而非显存直传，延迟略增。

---

## 6. 两种 connector 的共享拓扑对比

```
TE connector (单向, 你当前用的):
            P→D 单向
   P#0 ──┐
   P#1 ──┼──▶ D#0      P 之间无连接, D 之间无连接
   P#2 ──┤    D#1
   P#3 ──┘
   (P↔P ✗, D↔D ✗, 仅 P→D ✓)

Store connector (全连接, 可选):
         ┌──────── 共享缓存池 (Master) ────────┐
         │                                     │
   P#0◀──┼──▶ P#1◀──▶ P#2 ...   (P↔P ✓)
         │
   D#0◀──┼──▶ D#1                   (D↔D ✓)
         │                                     │
   P#0──▶│──▶ D#0  (P→D ✓)                  │
         └─────────────────────────────────────┘
   (任意节点都可 Put/Get, 全连接共享)
```

---

## 7. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| TE connector 仅 producer/consumer 两角色 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:497-501` |
| TE connector 角色互斥 assert | `mooncake_connector.py:579,604,630,697,705` |
| TE D 主动拉 | `mooncake_connector.py:1643`（receive_kv）|
| TE P 被动发 | `mooncake_connector.py:1003`（send_kv_to_decode）|
| bootstrap 只在 producer 启动 | `mooncake_connector.py:826-829` |
| Store connector 含 kv_both 角色 | `vllm/.../mooncake/store/worker.py:1192,1261,1280` |
| Store connector Put/Get 共享池 | `store/worker.py:648,855`（batch_put/batch_get）|
| Store key 编码 rank | `store/worker.py:984`（KeyMetadata）|

---

## 8. 总结

P、D 分离下 KV 是否跨同类节点共享，取决于 connector：

1. **TE connector（前面所有方案）**——KV 共享严格单向 P→D，**P↔P 和 D↔D 都不共享**（只有 kv_producer/kv_consumer 两角色、角色互斥、P 间和 D 间无连接通道），同类节点协作靠上层 router 的会话亲和而非 KV 传输；
2. **Store connector**——任意节点都能对共享缓存池 Put/Get（有 kv_producer/kv_consumer/kv_both 三角色，key 编码 rank），**P↔P、D↔D、P↔D 都可全连接共享**，支持跨 P 的全局 prefix 复用和 D 间会话迁移，代价是引入 Master 缓存池。

当前部署（TE connector）不支持 P↔P/D↔D 共享，如需则要换 Store connector。
