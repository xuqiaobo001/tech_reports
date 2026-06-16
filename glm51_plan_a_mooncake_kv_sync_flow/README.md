# GLM-5.1 方案 A PD 分离部署：Mooncake KV Cache 缓存同步全流程分析

> 模型：GLM-5.1（754B 总参数 / 40B 激活 MoE，256 路由专家 + 1 共享专家，MLA 注意力，80 层）
> 平台：华为昇腾 Atlas 800 A3（64G × 16 卡/机）
> 方案 A：P 2 台（dp2 tp16）+ D 4 台（dp16 tp4），对应 vLLM-Ascend 官方文档 5.3 节 PD 分离示例。
> 一句话核心：**非对称 TP（P=16, D=4）本应导致 16→4 聚合传输，但 GLM-5.1 的 MLA 让 KV latent 在 TP 间复制，去重后实际只有 4 条有效数据流。**

---

## 1. 方案 A 精确拓扑

| 角色 | 节点 | DP | TP | EP | 每机副本 | 总副本 | 总卡数 |
|---|---|---|---|---|---|---|---|
| **P（prefill）** | 2 台 A3（16卡/机）| 2（跨2机，每机1）| **16** | 开 | 1 | **2 个 prefill 副本** | 32 卡 |
| **D（decode）** | 4 台 A3（16卡/机）| 16（跨4机，每机4）| **4** | 开 | 4 | **16 个 decode 副本** | 64 卡 |

共 **6 台 A3 = 96 卡**，全部 PP=1。

关键配置（`kv-transfer-config`，P 和 D 都配，`kv_role` 不同）：

```json
{
  "kv_connector": "MooncakeConnectorV1",
  "kv_role": "kv_producer",
  "kv_port": "30000",
  "kv_connector_extra_config": {
    "use_ascend_direct": true,
    "prefill": {"dp_size": 2,  "tp_size": 16},
    "decode":  {"dp_size": 16, "tp_size": 4}
  }
}
```

---

## 2. 两个决定流程的关键前提

### 前提 1：非对称 TP（P=16, D=4）→ 4:1 聚合配对

方案 A 与同构 TP 场景的根本区别。P 把模型切成 16 份，D 只切 4 份：

```
P#0 的 16 个 rank (rank 0-15)            D#5 的 4 个 rank (rank 0-3)
┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐   ┌──────┬──────┬──────┬──────┐
│r0│r1│r2│r3│r4│r5│r6│r7│r8│..│..│..│..│..│..│r15│   │ D r0 │ D r1 │ D r2 │ D r3 │
└──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘   └──────┴──────┴──────┴──────┘
 └────聚合────┘ └──聚合──┘ ...                       ↑      ↑      ↑      ↑
   →D r0          →D r1                              每个D rank 收 4 个 P rank 的KV
```

从 D 视角（D 主动拉），`handshake_target_ranks(P_tp=16)`（`utils.py:570-580`）：D 的 `tp_ratio = -(16//4) = -4`，返回 `[tp_rank*4 + i for i in range(4)]`：

- D#5 rank 0 → 拉 P#0 的 **rank 0,1,2,3**
- D#5 rank 1 → 拉 P#0 的 **rank 4,5,6,7**
- D#5 rank 2 → 拉 P#0 的 **rank 8,9,10,11**
- D#5 rank 3 → 拉 P#0 的 **rank 12,13,14,15**

即 **D 的每个 rank 要聚合 P 的 4 个 rank 的 KV**。这是 `target_remote_ranks` 的负数分支（"remote TP > local TP: read from |tp_ratio| remote workers"）。

### 前提 2：GLM-5.1 用 MLA → KV latent 在 TP 间复制 → 传输去重

MLA 把 KV 压缩成 latent 向量，hidden 维度不可切，**每个 TP rank 持有完整的 KV latent**（源码 `local_replicates_kv_cache = is_mla`，`utils.py:566-568`）。这带来关键优化：

P 侧发送计划 `_compute_sender_transfer_plan`（`mooncake_connector.py:136-174`），在 `producer_cache_replicated=True`（MLA）+ `tp_ratio=4` 时：

```python
if producer_cache_replicated:   # MLA → True
    return local_tp_rank % tp_ratio == 0, 0, 0, local_kv_block_len
    #     ^send?: 只有 rank 0,4,8,12 为 True
```

**只有 P#0 的 rank 0,4,8,12 实际发送 KV latent**，其余 12 个 rank **跳过数据传输**（latent 相同，重复发无意义）。

所以实际数据流不是 16→4 全量，而是 **4 条有效流**：P rank 0→D r0, P rank 4→D r1, P rank 8→D r2, P rank 12→D r3。

---

## 3. Mooncake 缓存同步全流程（以 router 选 P#0 prefill、D#5 decode 为例）

### 步骤 1：启动注册（bootstrap，业务面 TCP）

所有 P/D 副本的每个 rank 启动时，向 bootstrap server（P rank 0）注册 `(engine_id, dp_rank, tp_rank, pp_rank, addr)`（`mooncake_utils.py:29-44`）。注册表结构（`mooncake_utils.py:40, 92-130`）：

```
bootstrap server 持有:
  P#0: {engine_id="P#0", {tp_rank 0-15: addr}}      ← 16 个 P rank 地址
  P#1: {engine_id="P#1", {tp_rank 0-15: addr}}
  D#0~D#15: 各 {tp_rank 0-3: addr}                   ← 每个 D 副本 4 个 rank
```

### 步骤 2：请求路由（上层 router 决定）

router 给请求分配 `transfer_id`，写入 `remote_engine_id="P#0"`、`remote_bootstrap_addr=P#0地址`，把请求分别送到 P#0 做 prefill、送到 D#5 做 decode。**Mooncake connector 本身不选 P/D**，只按请求自带的 `remote_engine_id` 寻址。

### 步骤 3：P#0 prefill（16 卡并行算 KV latent）

P#0 的 16 张卡并行 prefill。**MoE/EP 只影响 expert 层**——每 token 由 router 选 top-8 专家 + 1 共享专家计算 FFN，256 个专家分布在 16 卡上（EP）。但 **KV latent 产生在 attention 层，按 MLA 复制**：16 个 rank **各自持有一份完整 KV latent**。prefill 完成后，`request_finished` 把待发 KV 入队。

### 步骤 4：D#5 决定拉取（控制流）

D#5 收到带 `transfer_id` + `remote_engine_id="P#0"` 的请求：

- `get_num_new_matched_tokens`（`mooncake_connector.py:548-580`）标记"KV 全部从 P#0 远程拉"，异步加载；
- `update_state_after_alloc` 分配本地 KV block，记入 `_reqs_need_recv`；
- `build_connector_meta`（`:637-669`）打包，`remote_engine_id="P#0"` 作为 key。

### 步骤 5：D#5 发现 P#0（bootstrap /query）

D#5 各 rank 首次遇到 P#0 时，`handle_new_engine_id`（`:1665-1684`）触发 `_connect_to_prefiller_bootstrap`（`:1615-1641`）向 P#0 的 bootstrap `/query`，**一次性拉回 P#0 的全部 16 个 rank 地址**，存入 D#5 各 rank 的 `_remote_agents["P#0"]`：

```
D#5 的每个 rank 持有 _remote_agents["P#0"] = {0..15: addr_p0_r0..r15}
```

每个 D 副本都要能连 2 个 P（P#0、P#1），连接表是 `2 P × 16 rank = 32 条`。16 个 D 副本各自独立维护。

### 步骤 6：D#5 发起 4 路聚合拉取（核心，业务面 ZMQ + 参数面）

D#5 的 `receive_kv`（`:1643-1662`）对每个 rank 计算 `handshake_target_ranks(P_tp=16)`，发起并行拉取：

```python
# D#5 rank 0:
remote_tp_ranks = handshake_target_ranks(16) = [0,1,2,3]   # 4 个 P rank
count = len(remote_tp_ranks) = 4
pull_meta.pull_tasks_count = 4                              # 要聚合 4 路才就绪
for remote_tp_rank in [0,1,2,3]:
    worker_addr = _remote_agents["P#0"][remote_tp_rank]
    asyncio.create_task(receive_kv_from_single_worker(worker_addr, ...))  # 4 个并行任务
```

每个 `receive_kv_from_single_worker`（`:1533-1611`）经**业务面 ZMQ** 向对应 P rank 发 `MooncakeXferMetadata`（要哪些 block、自己 segment 地址、`remote_tp_rank`）。D#5 的 4 个 rank 共向 P#0 的 16 个 rank 发起 **16 个 ZMQ 信令连接**。

### 步骤 7：P#0 侧配对校验 + MLA 去重发送计划

P#0 每个 rank 收到 D#5 拉取请求后，`send_kv_to_decode`（`:1003-1018`）先做配对校验：

```python
# P#0 rank 5 收到 D#5 rank 1 的请求 (remote_tp_rank=1):
remote_tp_ranks = handshake_target_ranks(D_tp=4) = [5//4] = [1]   # P rank 5 配 D rank 1
# 1 in [1] ✓ 通过校验
```

然后算发送计划（MLA 复制分支）：

```python
# P#0 rank 5: send = (5 % 4 == 0) = False → 跳过(同组 rank 4 已持相同latent)
# P#0 rank 4: send = (4 % 4 == 0) = True  → 实际发送完整 latent
```

**结果：16 个 P rank 中只有 rank 0,4,8,12 实际发送**，对应 D#5 的 rank 0,1,2,3。其余 12 个 rank 通过校验但不传数据（latent 相同，去重）。

### 步骤 8：参数面 RoCE 实际传输（4 条有效数据流）

实际数据走参数面（`use_ascend_direct=true` → ascend_direct_transport → ADXL/RoCE）。因 MLA 去重，**真正传输只有 4 条**：

```
P#0 rank0 显存(latent) ──P#0 rank0参数面NIC──→ 交换机 ──→ D#5 rank0 显存
P#0 rank4 显存(latent) ──P#0 rank4参数面NIC──→ 交换机 ──→ D#5 rank1 显存
P#0 rank8 显存(latent) ──P#0 rank8参数面NIC──→ 交换机 ──→ D#5 rank2 显存
P#0 rank12显存(latent) ──P#0 rank12参数面NIC──→交换机 ──→ D#5 rank3 显存
```

每条用 `batch_transfer_sync_write`（`:1365`）做批量 RDMA write，零拷贝。**MLA latent 很小**（相比 GQA 完整 K/V），带宽压力远小于非 MLA 模型。

> 若不是 MLA（GQA 模型），则 `producer_cache_replicated=False`，16 个 P rank 全部发送各自 head 子集，D rank 聚合 4 份拼接——真正的 16→4 全量传输。GLM-5.1 因 MLA 享受去重红利。

### 步骤 9：D#5 聚合 count 归零

D#5 各 rank 的 4 个拉取任务陆续完成（rank 0 带数据，rank 1,2,3 在 MLA 下空传输但仍握手确认）。`process_pulling_result`（`:~1612-1630`）每完成一个 `pull_tasks_count -= 1`：

```python
for req_id in ok_reqs:
    pull_meta.pull_tasks_count -= 1
    if pull_meta.pull_tasks_count == 0:   # 4 路都完成
        self.finished_recving_reqs.add(d_req_id)   # 请求 KV 就绪
```

4 路归零后，D#5 请求 KV 就绪。

### 步骤 10：D#5 decode

D#5 的 4 个卡各自拿到完整 KV latent（MLA，每个 rank 一份完整 latent），下一个调度 step 直接开始 decode。decode 期间 EP 继续工作（每 token 路由 top-8 专家），但 KV 已就位，无需再传。

---

## 4. 关键配置项（mooncake 缓存同步直接相关）

| 配置 | 值 | 作用 |
|---|---|---|
| `use_ascend_direct` | `true` | 数据走参数面 RoCE（ADXL），非 TCP |
| `kv_port` | P=30000 / D=30100 | mooncake RPC 端口（业务面） |
| `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` | `480`(秒) | P 侧 KV 保留 480 秒等 D 拉取，超时自动释放 |
| `ASCEND_AGGREGATE_ENABLE` | `1` | 启用聚合传输优化 |
| `ASCEND_TRANSPORT_PRINT` | `1` | 打印每条传输是否跨 HCCS、耗时（调试用）|
| P `--max-num-batched-tokens` | 4096 | P 大 batch 吃 prefill 算力 |
| D `--max-num-batched-tokens` | **32** | D 小 batch 高频 decode |
| P `--max-num-seqs` | 64 | P 高并发 prefill |
| D `--max-num-seqs` | 8 | D 每副本并发低（靠 16 副本扩容）|
| P `additional-config.layer_sharding` | `["q_b_proj","o_proj"]` | 支持 200k 上下文 prefill 的层分片 |

---

## 5. MLA 去重对方案 A 的意义

方案 A 选 D 的 `tp=4`（而非 `tp=16`）是为了 decode 吞吐（"decode 偏好小 TP"：访存瓶颈下小 TP 减通信、大 DP 提并发）。但这造成 P→D 非对称（16→4），**本需 16→4 聚合传输**。MLA 的复制特性化解了这个问题：

- **GQA 模型（非 MLA）**：D 的 4 个 rank 各要从 P 的 4 个 rank 拉 head 子集拼接，**16 条数据流**，P 的 16 卡全忙；
- **GLM-5.1（MLA）**：latent 复制，去重后**只有 4 条数据流**，P 的 16 卡只动 4 张，其余空闲。

**方案 A 的 P=16/D=4 非对称配置，在 MLA 下传输开销很低**——D 用小 TP 提吞吐，又不用承担沉重聚合传输代价。这是 GLM-5.1 + 方案 A 的隐性优势。

---

## 6. 注意事项

1. **精度风险**：官方 issue [vllm-ascend#8844](https://github.com/vllm-project/vllm-ascend/issues/8844) 报告**多节点 PD 分离 + TP16 + DP2 配置下 GPQA 准确率未达标**。方案 A 涉及 TP16，部署后**务必验证精度**。
2. **2 个 P 副本负载**：router 要在 P#0、P#1 间均衡 prefill 负载。
3. **16 个 D 副本**：router 要在 16 个 D 间均衡 decode 并发。
4. **EP 开启**：`--enable-expert-parallel` 必须开，否则 754B MoE 无法部署。EP 自动切分 256 专家到 16 卡，无需手动指定 EP 数值。
5. **PP=1 硬约束**：Mooncake TE connector 不支持 PP>1（`mooncake_connector.py:791-795`）。方案 A 全 PP=1，符合约束。

---

## 7. 引用文件清单

| 结论 | 证据位置 |
|---|---|
| 非对称 TP 配对 | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:570-580`（handshake_target_ranks）|
| tp_ratio 通用计算 | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:521-537` |
| MLA KV 复制 | `vllm/vllm/distributed/kv_transfer/kv_connector/utils.py:566-568` |
| P 侧发送计划（去重）| `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:136-174` |
| D 侧聚合拉取 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1643-1662` |
| D 侧单 rank 拉取 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1533-1611` |
| P 侧配对校验 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1003-1018` |
| bootstrap 注册/发现 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_utils.py:29-44, 92-130` |
| D bootstrap /query | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1615-1641` |
| 实际 RDMA write | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1365` |
| PP=1 约束 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:791-795` |
| 官方 PD 分离配置 | [vllm-ascend GLM-5/5.1 文档 5.3 节](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/GLM5.html) |
| GLM-5.1 MLA 架构 | [GLM-5 论文](https://arxiv.org/html/2602.15763v1) |

---

## 8. 总结

方案 A（P 2 台 dp2tp16 + D 4 台 dp16tp4）下，mooncake 缓存同步的完整流程：

1. **router** 选 1 个 P 副本 prefill、1 个 D 副本 decode，写入 `remote_engine_id`；
2. **D 副本**经 bootstrap 拉取该 P 的 16 个 rank 地址；
3. 因 **P TP=16 > D TP=4**，D 的每个 rank 按 `handshake_target_ranks` 向 P 的 4 个 rank 发起聚合拉取（共 16 个业务面 ZMQ 连接）；
4. 但因 **GLM-5.1 用 MLA、KV latent 在 TP 间复制**，P 侧发送计划去重后只有 rank 0,4,8,12 实际经参数面 RoCE 发送完整 latent（**4 条有效数据流**），其余 rank 跳过；
5. **D** 的 4 个 rank 各聚齐 4 路（count 归零）即拿到完整 KV latent，开始 decode。

**MLA 的复制特性让看似复杂的 16→4 非对称聚合，实际只产生 4 条传输**，是 GLM-5.1 + 方案 A 的关键红利——D 用小 TP 提吞吐，又不用承担沉重的非对称聚合传输代价。
