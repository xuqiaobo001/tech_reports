# vLLM Prefix Caching vs Mooncake：单实例 GLM 部署 TTFT 优化分析报告

> **日期**: 2026-11-03
> **场景**: 基于 vLLM / vllm-ascend 在昇腾 NPU 上单实例部署 GLM 5.3，目标是**降低尽可能多请求的 TTFT**，且线上请求种类很多。
> **结论适用范围**: 单实例部署结论与多实例前瞻分析均在文中区分标注。

---

## TL;DR 核心结论

1. **两种方案的 TTFT 上限完全相同**——前缀 KV 命中 NPU 显存本地 APC 缓存时，都是零传输、免 prefill，Mooncake 不会让"最好情况"更快。
2. **Mooncake 的价值在容量而非速度**：把方案 1 中"本地缓存未命中 → 全量重算"改写为"本地未命中 → 从 store 加载 KV"。长前缀下加载显著快于重算，但永远慢于本地命中。
3. **"请求种类多"的场景通常利好 Mooncake**：种类多导致中频前缀被 LRU 挤出显存，APC 命中率随种类数增长而衰减；Mooncake 把可驻留前缀总量从显存量级（GB 级）扩到 CPU DRAM 量级（百 GB 级），把命中覆盖面从"头部热前缀"扩展到"中频长尾"。
4. **缓存只对"第二次出现"的前缀有效**：如果请求流大部分 prompt 一次性不复用，任何缓存层都无效，应投资 prefill 算力侧（P/D 分离、CP、更大 batch tokens、扩 TP）。
5. **决策应由数据驱动**：离线画"命中率–容量"曲线，显存容量点与 Mooncake 容量点的命中率差值，就是方案 2 能多覆盖的请求比例。

---

## 1. 问题定义

两个候选部署方案，在面临相同输入时对 TTFT 的优化是否相同：

| 方案 | 配置 |
|---|---|
| **方案 1** | vLLM 单实例 GLM 5.3，开启 prefix caching（APC） |
| **方案 2** | vLLM 单实例 GLM 5.3 + 部署 Mooncake 分布式 KV 缓存，同时开启 prefix caching |

---

## 2. 两种方案的缓存体系对比

| | 方案 1：仅 APC | 方案 2：APC + Mooncake |
|---|---|---|
| 缓存层次 | 单层：NPU 显存内 KV 池 | 两层：L1 = NPU APC，L2 = Mooncake 分布式池（CPU DRAM / SSD） |
| 容量 | 受 `--gpu-memory-utilization` 划定的池子限制，且与并发请求共享 | L2 可达数百 GB～TB 级，远超显存 |
| 生命周期 | 进程重启即全丢 | store 独立进程，实例重启后可恢复 |
| 命中开销 | 零拷贝复用 block，几乎为零 | L1 命中同左；L2 命中需付 PCIe/RDMA 传输 + 反序列化 |

**关键机制**（基于 vLLM 源码验证）：

- `MooncakeStoreConnector` 在调度阶段通过 `get_num_new_matched_tokens` **异步预取**匹配前缀（`load_async` 默认 true），加载与排队等待重叠，摊薄 TTFT 惩罚。
- 请求结束后经 `request_finished` **异步写回** store，不阻塞 TTFT。
- **APC 与 Mooncake 是互补关系而非二选一**：本地 APC 命中优先（零拷贝），store 只是兜底层。
- 源码位置：`vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/connector.py`

---

## 3. 分情形 TTFT 对比（相同输入）

| 情形 | 方案 1 | 方案 2 | 谁优 |
|---|---|---|---|
| 热前缀，KV 仍在 NPU 缓存中 | 免 prefill，最优 | 相同（多一次 store 查询，异步、可忽略） | **打平** |
| 前缀算过但已被挤出 NPU（工作集 > 显存缓存容量） | 全量重算 prefill | 从 store 加载 KV | **方案 2 显著优**（长前缀时） |
| 首次出现的冷前缀 | 全量计算 | 全量计算 + 异步写 store，TTFT 基本不受影响 | 打平（方案 2 微开销） |
| 服务重启后的热前缀 | 缓存冷启动，全量重算 | 从 store 恢复，接近热路径 | **方案 2 优** |
| 短前缀（几百 token）复用 | 重算很快 | 加载有固定延迟，可能不划算 | 方案 1 持平甚至微优 |

---

## 4. 为什么"最好情况"打平：速度的物理上限

- **本地命中的开销是"指针复用"**——APC 命中直接复用显存中的 block，零传输。
- **Mooncake 命中的开销是"搬运"**——KV 要从 CPU DRAM/SSD/远端传回 NPU，带宽（PCIe Gen4 ~26GB/s、RDMA 200Gb≈25GB/s）比显存直接复用低一两个数量级。
- 因此 Mooncake **不可能让最好情况更快**，它的收益全部来自命中率提升，即把更多请求的 TTFT 从"重算档"拉到"加载档"。

## 5. 为什么长前缀下"加载 < 重算"：break-even 分析

- 重算成本 ∝ 前缀 token 数 × 模型活跃参数量（compute-bound，长前缀非常贵）
- 加载成本 ∝ KV 字节数 ÷ 传输带宽（I/O-bound）

粗略量级：GQA 架构下 32K token 的 KV 约为 GB 级，传输需几百 ms；大模型重算 32K prefill 通常为秒级——加载快约一个数量级。**前缀越短差距越小，存在 break-even 点**（短前缀重算反而更快）。

---

## 6. 面向"种类多、降低更多请求 TTFT"的目标分析

### 6.1 决策变量：前缀复用分布 vs 缓存容量

"请求种类多"本身不决定优劣，决定的是两件事：

1. **每个种类内部有没有共享前缀**（system prompt、工具定义、RAG 文档、多轮对话历史——请求尾部各异没关系，前缀共享才是可缓存的）
2. **这些共享前缀的总工作集是否超过显存缓存容量**

### 6.2 三种负载画像与结论

| 前缀复用画像 | 只开 APC | + Mooncake | 结论 |
|---|---|---|---|
| 种类多，每类内部有固定长前缀（多租户/多 agent/多文档库） | 只保住头部热前缀，中频被 LRU 挤出 | 长尾全部驻留，覆盖面最大化 | **方案 2 收益显著** |
| 种类多且每类共享前缀很短/总量小 | 全装得下 | 无增量 | 方案 1 足够 |
| 请求几乎不复用前缀（每次新文档、新内容） | 命中率≈0 | 命中率仍≈0 | **两个方案都无效**，TTFT 由 prefill 算力决定 |

### 6.3 容量粗算示例（示意值，实际按 GLM 5.3 config 代入）

```
KV 字节/token = 层数 × 2(K+V) × kv_heads × head_dim × dtype 字节数

GQA 示例: 80 层 × 2 × 8 × 128 × 2B ≈ 320 KB/token
显存中可划给缓存的余量 ~15GB → 仅能驻留 ~5 万 token 的前缀
    ≈ 十来个 4K system prompt 的工作集
种类 = 200 个租户 × 4K 前缀 = 80 万 token 工作集 → APC 只覆盖 ~6%
Mooncake store 400GB DRAM → 全覆盖，200 个种类的请求都能命中
```

> 若模型使用 MLA 类压缩 KV 或 fp8 KV cache，字节数小一个量级，结论方向不变、阈值后移。

### 6.4 单实例 vs 多实例的前瞻

单实例下 Mooncake 只兑现"容量扩展 + 持久化"两个价值（其核心设计价值是跨实例共享）。但若未来扩到多实例做负载均衡：

- **方案 1 系统性劣化**：种类多的请求被随机路由到不同实例，APC 每实例各存一份、互相不命中；
- **方案 2 全局共享一份**，命中率不随实例数衰减。
- 即"更多请求的 TTFT"目标在多实例下方案 1 会劣化，方案 2 不会。

---

## 7. 数据驱动决策流程（上线前一小时可完成）

1. **离线画"命中率–容量"曲线**：
   - 采样生产 prompt，按 `block_size`（16）token 做块级 hash，按频次做 LRU 模拟；
   - 输出"缓存容量 → 前缀命中率"曲线；
   - 取两个点：显存余量点 vs Mooncake 池容量点；
   - **命中率差 = 方案 2 能多覆盖的请求比例**，可直接换算为"多少请求的 TTFT 从秒级降到几百 ms"。
2. **线上观测**：
   - vLLM 侧：`--kv-cache-metrics` 看 APC 命中率；
   - Mooncake 侧：`VLLM_MOONCAKE_STORE_TIER_LOG=1` 观察 store 命中（内存 vs 磁盘分层）；
   - 对比两方案 TTFT 的 **P50/P99 分布**——种类多的负载下差异体现在 P99，均值看不出来。
3. **冷请求占比判断**：若曲线在低容量处已饱和（复用集中在极少数热前缀）→ 方案 1 足够；若长尾显著 → 方案 2；若几乎无复用 → 转向 prefill 算力优化。

---

## 8. vllm-ascend 落地清单与风险

| 事项 | 说明 |
|---|---|
| **store 模式成熟度需 PoC** | vllm-ascend 的 Mooncake 校验逻辑目前只识别 P/D 分离用的 `MooncakeConnector`/`MooncakeConnectorV1`（`vllm_ascend/utils.py: uses_mooncake_connector`），官方 examples 只有 P/D 分离指南，**无 store 模式样例**。建议先单实例 + `kv_role: kv_both` 验证命中率与 H2D 加载路径 |
| **参考配置** | `vllm/docs/features/mooncake_store_connector_usage.md`（store 模式完整配置：`mooncake_master`、`MOONCAKE_CONFIG_PATH`、embedded/standalone-store 模式、RDMA/TCP 选择） |
| **哈希一致性** | 跨进程共享 store 必须固定 `PYTHONHASHSEED`（如 `PYTHONHASHSEED=0`），否则相同 prompt 在不同进程产生不同 block hash，永远不会命中 |
| **更轻的替代** | `--kv-offloading-size` + `--kv-offloading-backend`（native/lmcache）可单实例扩容量且不引入 Mooncake 组件；若容量缺口只有 2~3 倍而非一个量级，落地成本更低 |
| **量化配合** | C8 KV cache 量化与 MooncakeConnector 在 GQA 模型上有兼容性限制（vllm-ascend 已有校验拦截），组合使用前先确认 |

---

## 9. 最终建议

1. **先测再选**：跑一次"命中率–容量"曲线分析（第 7 节），这是唯一可靠的决策依据。
2. **若长尾复用显著**（种类多且每类有固定前缀）：选方案 2，预期收益是 P99 TTFT 的显著改善与重启后缓存不冷；先在昇腾上 PoC `MooncakeStoreConnector`。
3. **若复用集中在头部或总量小**：方案 1 足够，避免引入额外运维组件。
4. **若冷请求为主**：缓存无效，投资 prefill 算力——P/D 分离、`--max-num-batched-tokens` 调大、长 prompt 用 CP、扩 TP。
5. **预期未来扩多实例**：优先方案 2，多实例随机路由下 APC 命中率系统性衰减，Mooncake 全局共享的优势随实例数放大。

---

## 附录：相关源码与文档位置

| 内容 | 位置 |
|---|---|
| MooncakeStoreConnector 实现 | `vllm/vllm/distributed/kv_transfer/kv_connector/v1/mooncake/store/connector.py` |
| store 模式使用文档 | `vllm/docs/features/mooncake_store_connector_usage.md` |
| Mooncake P2P（P/D 分离）使用文档 | `vllm/docs/features/mooncake_connector_usage.md` |
| vllm-ascend P/D 分离部署指南 | `vllm-ascend/examples/disaggregated_prefill_v1/mooncake_connector_deployment_guide.md` |
| vllm-ascend Mooncake 校验逻辑 | `vllm-ascend/vllm_ascend/utils.py`（`uses_mooncake_connector`）、`vllm_ascend/ascend_config.py`（`_check_mooncake_c8_kv_cache_quant`） |
| vLLM 部署参数全集（中文） | `vllm-deployment-params-zh.md`（含 `--kv-transfer-config`、`--kv-offloading-*`、`--kv-cache-metrics`） |
