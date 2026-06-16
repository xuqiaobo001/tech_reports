# GLM-5.1 长输入(100k)短输出(120)场景的 PD 分离部署调整建议

> 模型：GLM-5.1（754B MoE，MLA 注意力，80 层）
> 平台：华为昇腾 Atlas 800 A3（64G × 16）
> 场景：长输入(~100k tokens) + 短输出(~120 tokens)
> 基线：方案 A（P 2台 dp2tp16 + D 4台 dp16tp4）
> 核心结论：**这是 PD 分离收益最大的场景，但需大幅调整 PD 比例（P≫D）、拉长 KV 保留超时、确保参数面 RoCE 带宽。**

---

## 1. 场景特征与核心矛盾

长输入(100k) + 短输出(120) 的本质是 **prefill 极重、decode 极轻**：

| 阶段 | 工作量 | 瓶颈类型 |
|---|---|---|
| Prefill | 100k tokens，attention 是 O(n²) | **算力极重**（可能数秒~十几秒）|
| KV 传输 | 100k tokens 的 latent 从 P→D | **带宽**（见下）|
| Decode | 仅 120 tokens | 极轻（访存为主）|

### 关键数字：KV latent 传输量有多大？

GLM-5.1 用 MLA（DeepSeek-V3 同款设计）。MLA 把每层每 token 的 KV 压缩成 latent 向量（DeepSeek-V3 的 `kv_lora_rank=512` + rope 维 ≈ 576 维）。**估算**（基于同款 MLA）：

- per token per layer ≈ 576 × 2 bytes(bf16) ≈ **1.1 KB**
- **100k tokens × 80 层 ≈ 8–9 GB**（P→D 要传的量）
- 120 tokens decode 新增 KV ≈ 80 × 120 × 1.1KB ≈ **10 MB**（可忽略）

> MLA 去重红利（前面分析过）：方案 A 下实际只有 4 条数据流（P rank 0,4,8,12 → D rank 0,1,2,3），每条传完整 latent。总传输量仍是 ~8GB，分摊到 4 条 RoCE 流。

**8–9 GB 的 KV 传输是这个场景的核心矛盾。** 直接决定下面的部署调整。

---

## 2. 对部署方案的 5 大影响

### 影响 1：PD 资源比例应严重向 P 倾斜（最关键）

长输入短输出，**prefill 是绝对瓶颈，D 侧利用率会很低**。方案 A 原本的比例（2P 副本 : 16D 副本）是为通用负载设计的，对本场景**不合理**——16 个 D 副本大部分时间闲置（每个请求只 decode 120 token 就结束）。

调整方向：
- **增加 P 副本**（扛 100k prefill 的算力）；
- **大幅减少 D 副本**（decode 轻，少几个副本就够）。

例如从方案 A 的 `P 2台 + D 4台`，改为更倾斜的比例，如 `P 4-5台 + D 1-2台`。具体比例要看 QPS 和 prefill 时延目标，但方向是 P ≫ D。

> 为什么 D 利用率低：一个请求 decode 120 token，假设 ~30 token/s，约 4 秒就结束。而它要等 P 算 100k prefill（可能 10s+）+ 传输 8GB。decode 阶段在整个生命周期占比很小。16 个 D 副本会让多数副本长期空转。

### 影响 2：P 侧配置——chunked prefill 是刚需

100k tokens 不可能一次性 prefill（显存和算力都撑不住），**必须用 chunked prefill 分块**。方案 A 已体现，但要确认：

```bash
--enable-chunked-prefill \           # 必须开
--max-num-batched-tokens 4096 \      # chunk size: 100k/4096 ≈ 25 个 chunk
--max-model-len 131072 \             # 必须 ≥ 100k + 120
```

**`max-num-batched-tokens` 的权衡**：
- 太小（如 4096）→ 25 个 chunk，prefill 调度开销大；
- 太大（如 32768）→ 单 chunk 显存峰值高，可能 OOM，尤其 100k 中间激活。

4096~8192 对 100k 输入是合理区间，但要监控显存。

### 影响 3：KV 保留超时必须拉长（长输入传输久）

这是**最容易踩坑的点**。方案 A 里：

```bash
export VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=480   # P 侧 KV 保留 480 秒
```

这个超时控制 P 侧 prefill 完成后**保留 KV 等待 D 拉取的时间**。8–9 GB 的 KV 传输（前面估算）+ D 侧聚合 + 可能的排队，**480 秒通常够**，但如果：
- 参数面 RoCE 带宽打不满；
- 多个请求并发传输抢占带宽；
- D 侧副本少导致拉取排队；

就可能超时。**长输入场景建议更保守（600~900）**，避免 P 侧过早释放 KV 导致 D 拉取失败（会触发 D 侧重算 prefill，灾难性）。

### 影响 4：D 侧应进一步降 TP、提并发

短输出场景，decode 副本的优化目标是**高并发吞吐**而非单请求延迟：

方案 A 的 D 配置 `tp=4, dp=16` 对短输出**过度配置**。可以考虑：
- D 的 `tp` 再降（如 tp=2 甚至 tp=1），MLA 下 KV 复制，单卡也能跑完整 decode；
- `--max-num-seqs` 提高（D 同时 decode 更多请求，因为每个很快就结束）；
- `--max-num-batched-tokens` 维持小（decode 每步 token 少）。

```bash
# D 侧短输出优化(示意)
--max-num-seqs 32 \          # 从 8 提高,短输出可堆更多并发
--max-num-batched-tokens 32 \ # decode 每步小
```

注意：**D 副本数少了，每个副本的 `max-num-seqs` 要相应提高**，否则并发吞吐上不去。

### 影响 5：KV 传输带宽成为关键性能因素

8–9 GB 传输在参数面 RoCE 上的耗时：
- 理论 200Gbps（25GB/s）→ ~0.35s；
- 实际多路 + 开销 → **1~3 秒**；
- 若带宽规划不当（P、D 不在同一 RoCE 域、TC/SL 配置差）→ 更久。

这意味着：
- **务必确保 P、D 在同一高带宽参数面 RoCE 域**；
- 关注 `ASCEND_RDMA_TC` / `ASCEND_RDMA_SL` 的 QoS 配置，保证 KV 传输的带宽优先级；
- 这是方案 A 用 `use_ascend_direct=true`（参数面 RoCE）而非 TCP 的根本原因——8GB 走 TCP 会慢一个量级。

**PD 分离的价值**：即使 KV 传输要 1~3 秒，也比 **D 侧重算 100k prefill**（可能 10s+）快得多。这正是长输入场景适合 PD 分离的核心。

---

## 3. 额外优化点

### Prefix Caching 价值极大

长输入场景，若有重复的 system prompt / 长上下文前缀（很常见），**prefix caching 能省掉重复部分的 prefill 和 KV 传输**：

```bash
--enable-prefix-caching \    # 必须开(方案A已有)
```

100k 输入里若有 80k 是固定前缀，prefix hit 后只需算 20k + 传 20k 的 KV，**收益数量级**。

### 是否值得 PD 分离？——强烈值得

长输入短输出是 PD 分离的**最佳匹配场景**：
- prefill(decode) 算力需求极度不均衡 → PD 分离让 P、D 各自最优配置；
- D 不重算 100k prefill → 节省巨大算力；
- 唯一代价是 8GB KV 传输（1~3s），远小于重算。

---

## 4. 具体配置调整建议（基于方案 A 改）

| 配置项 | 方案 A 原值 | 100k/120 场景建议 | 理由 |
|---|---|---|---|
| **PD 比例** | P2:D4(机) | **P:D ≈ 4~5 : 1~2(机)** | prefill 重,D 利用率低 |
| P/D 副本比 | 2P:16D | **更多 P 副本,更少 D 副本** | 同上 |
| `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` | 480 | **600~900** | 8GB KV 传输久,防过早释放 |
| `--enable-chunked-prefill` | 开 | **开(刚需)** | 100k 必须分块 |
| `--max-num-batched-tokens`(P) | 4096 | 4096~8192 | chunk size 平衡 |
| `--max-model-len` | 131072(P)/200000(D) | **≥101k 即可,可下调省显存** | 按 100k+120 设 |
| `--max-num-seqs`(D) | 8 | **16~32** | 短输出可堆并发 |
| D `tp` | 4 | 可试 **2** | MLA 复制,短输出单卡够 |
| `--enable-prefix-caching` | 开 | **务必开** | 长前缀复用收益巨大 |
| 参数面 RoCE QoS | 默认 | **配置 TC/SL 保 KV 带宽** | 8GB 传输带宽敏感 |

---

## 5. 风险与注意事项

1. **KV 传输超时风险**：8GB 传输若遇 RoCE 带宽不足或丢包重传，可能逼近超时。务必监控 `ASCEND_TRANSPORT_PRINT=1` 打印的传输耗时。
2. **PD 比例失衡的 router 适配**：P 增多 D 减少后，上层 router 的负载均衡策略要相应调整（P 按 prefill 队列、D 按 decode 并发）。
3. **精度问题仍在**：方案 A 涉及 TP16，官方 issue [vllm-ascend#8844](https://github.com/vllm-project/vllm-ascend/issues/8844) 的 TP16 PD 分离精度风险，长输入场景同样要验证。
4. **显存峰值**：100k tokens 的中间激活（prefill）+ KV latent 在 P 侧是显存大头，`gpu-memory-utilization` 留余量。
5. **MLA KV 估算说明**：本文 8–9GB 是基于 DeepSeek-V3 同款 MLA（`kv_lora_rank=512`）的估算，GLM-5.1 实际维度可能略有差异，建议用 `ASCEND_TRANSPORT_PRINT` 实测传输字节数校准。

---

## 6. 总结

长输入(100k)短输出(120)场景对 GLM-5.1 PD 分离部署的影响：

1. **PD 资源比例应大幅向 P 倾斜**（P≫D，prefill 极重、D 利用率低）；
2. **KV 传输成为关键瓶颈**——100k tokens 的 MLA latent 约 8–9GB 要从 P 经参数面 RoCE 传到 D，必须拉长 `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT`（防过早释放）、确保 RoCE 带宽/QoS；
3. **P 侧 chunked prefill 是刚需**；
4. **D 侧应降 TP、提 `max-num-seqs`**（短输出堆并发）；
5. **prefix caching 在长前缀场景收益巨大**。

这恰恰是 PD 分离收益最大的场景——用 1~3 秒的 KV 传输换掉 D 侧 10s+ 的 100k prefill 重算。
