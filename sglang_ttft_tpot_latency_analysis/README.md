# SGLang PD 分离架构 TTFT/TPOT 时延变化趋势分析

> 分析日期：2026-04-24
> 分析对象：SGLang 源码 sgl-project/sglang
> 分析目标：不同输入/输出长度组合下 TTFT（首 Token 延迟）和 TPOT（每 Token 延迟）的变化趋势
> 部署场景：GLM-4.7-Flash-30B-A3B，7P+1D，context_length=32768，8卡 TP

---

## 一、分析场景

| | 场景 1 | 场景 2 |
|--|--------|--------|
| **输入 tokens** | 19,000 | 34,000 |
| **输出 tokens** | 300 | 64 |
| **总 tokens** | 19,300 | 34,064 |
| **输入/输出比** | 63:1 | 531:1 |

---

## 二、TTFT 时间组成（PD 分离架构）

### 2.1 请求生命周期时间戳

**代码位置**：`python/sglang/srt/observability/req_time_stats.py`

```
created_time ──────────────────────────────────────────────────► first_token_time
    │                                                                │
    ├── TOKENIZE ──► API_SERVER_DISPATCH ──► PREFILL_PREPARE        │
    │                                              │                │
    │                                        PREFILL_BOOTSTRAP      │
    │                                              │                │
    │                                        PREFILL_FORWARD        │
    │                                         (可能多个chunk)        │
    │                                              │                │
    │                                        PREFILL_TRANSFER       │
    │                                         (KV cache传输)        │
    │                                              │                │
    │                                        DECODE_BOOTSTRAP       │
    │                                              │                │
    │                                        DECODE_FORWARD         │
    │                                         (首token decode)      │
    └──────────────────────────────────────────────┴────────────────┘
                        TTFT = first_token_time - created_time
```

### 2.2 各阶段与输入长度的关系

| 阶段 | RequestStage | 代码位置 | 与输入长度关系 | 典型耗时 |
|------|-------------|---------|--------------|---------|
| Tokenize | `TOKENIZE` | `tokenizer_manager.py` | 线性 | ~10-18ms |
| Router Dispatch | `API_SERVER_DISPATCH` | `pd_router.rs` | **固定** | ~5ms |
| Bootstrap 握手 | `PREFILL_BOOTSTRAP` | `prefill.py:227-249` | **固定** | ~50ms |
| Prefill Forward | `PREFILL_FORWARD` | `scheduler.py:2412-2460` | **线性~超线性** | 主要耗时 |
| KV Transfer | `PREFILL_TRANSFER_KV_CACHE` | `mooncake/conn.py` | **线性** | 主要耗时 |
| Decode Bootstrap | `DECODE_BOOTSTRAP` | `decode.py:241-477` | **固定** | ~20ms |
| 首 Token Decode | `DECODE_FORWARD` | `scheduler.py` | **固定** | ~30-50ms |

---

## 三、Prefill Forward 时延分析

### 3.1 Chunked Prefill 机制

**代码位置**：`python/sglang/srt/server_args.py:1216-1303`

对于 80GB HBM GPU（H100/A100），默认 `chunked_prefill_size = 8192`。

**Chunked Prefill 的作用**：将长输入拆分为多个 chunk，每个 chunk 单独做一次 forward pass。避免单次 prefill 占用过多显存。

```
输入 19,000 tokens → [8192] [8192] [2616]  = 3 chunks, 3 次 forward
输入 34,000 tokens → [8192] [8192] [8192] [8192] [1198] = 5 chunks, 5 次 forward
```

| 场景 | 输入 tokens | chunk_size | 需要 chunk 数 | Prefill 迭代次数 |
|------|-----------|-----------|-------------|----------------|
| 场景 1 | 19,000 | 8,192 | ceil(19000/8192) = **3** | 3 次 forward |
| 场景 2 | 34,000 | 8,192 | ceil(34000/8192) = **5** | 5 次 forward |

### 3.2 Prefill 时间公式

```
T_prefill = N_chunks × T_chunk_forward + (N_chunks - 1) × T_chunk_overhead
```

其中：
- `T_chunk_forward`：单 chunk forward 时间（与 chunk_size 近似线性）
- `T_chunk_overhead`：chunk 间的调度开销（scheduler 重新排队、batch 重组）

**场景对比**：

| | 场景 1 | 场景 2 | 比值 |
|--|--------|--------|------|
| Prefill chunks | 3 | 5 | 1.67× |
| 纯 Forward 比值 | 3×T_chunk | 5×T_chunk | 1.67× |
| Chunk overhead | 2×T_overhead | 4×T_overhead | 2.0× |
| **总 Prefill 估算** | 基准 | ~1.7-1.8× 基准 | **~1.7-1.8×** |

---

## 四、KV Cache Transfer 时延分析

### 4.1 KV Pages 计算

**代码位置**：`python/sglang/srt/disaggregation/utils.py:439-451`

```python
def kv_to_page_num(num_kv_indices: int, page_size: int):
    return (num_kv_indices + page_size - 1) // page_size
```

### 4.2 KV Cache 数据量估算

GLM-4.7-Flash-30B-A3B 使用标准 MHA（非 MLA），每 page KV cache 大小：

```
bytes_per_page = 2 (K+V) × num_layers × num_kv_heads × head_dim × dtype_size
```

假设参数（GLM-4.7-Flash-30B-A3B，page_size=1，fp16）：
- `num_layers` ≈ 48
- `num_kv_heads` ≈ 8（GQA）
- `head_dim` = 128
- `dtype_size` = 2（fp16）

```
bytes_per_page ≈ 2 × 48 × 8 × 128 × 2 = ~196 KB/page
```

| 场景 | 输入 tokens | Pages | KV Cache 总量 | 占 8×80GB 比例 |
|------|-----------|-------|-------------|---------------|
| 场景 1 | 19,000 | 19,000 | **~3.6 GB** | ~0.6% |
| 场景 2 | 34,000 | 34,000 | **~6.4 GB** | ~1.0% |

### 4.3 Transfer 时延估算

RDMA 传输速度通常 50-100 Gbps（~6-12 GB/s）：

```
场景 1: 3.6 GB ÷ 8 GB/s ≈ 450ms
场景 2: 6.4 GB ÷ 8 GB/s ≈ 800ms
```

| | 场景 1 | 场景 2 | 比值 |
|--|--------|--------|------|
| KV Cache 数据量 | ~3.6 GB | ~6.4 GB | **1.79×** |
| RDMA Transfer 时延 | ~450ms | ~800ms | **1.79×** |

**KV Transfer 时延与输入 token 数严格线性。**

**Transfer 时延计算代码**（`req_time_stats.py:838-846`）：

```python
transfer_latency_s = self.completion_time - self.prefill_transfer_queue_entry_time
num_pages = kv_to_page_num(num_tokens, page_size)
total_bytes = bytes_per_page_all_layers * num_pages
total_mb = total_bytes / (1024 * 1024)
```

---

## 五、TPOT（每 Token Decode 延迟）分析

### 5.1 Decode 注意力机制

**代码位置**：`python/sglang/srt/layers/attention/flashinfer_backend.py:881-917`

SGLang 使用 FlashInfer 的 `BatchDecodeWithPagedKVCacheWrapper` 进行 decode。

GLM-4 使用标准 MHA（非 MLA），Decode 时每个新 token 需要：
1. **加载完整 KV cache**（与序列长度成正比的显存读取）
2. **计算注意力分数**（与 KV cache 长度线性相关）
3. **生成输出向量**

### 5.2 TPOT 与 KV Cache 长度的关系

```
TPOT ∝ seq_len（当前 KV cache 中的总 token 数）
```

**原因**：Decode 的主要瓶颈是**显存带宽**（memory-bound），不是计算（compute-bound）。每个新 token 都需要读取全部历史 KV cache。

| 阶段 | 场景 1（19k 输入） | 场景 2（34k 输入） | 说明 |
|------|----------------|----------------|------|
| 首 token 时 KV cache 长度 | 19,000 | 34,000 | 场景 2 = 1.79× |
| 末 token 时 KV cache 长度 | 19,300 | 34,064 | 几乎不变 |
| 单 token 计算量 | ∝ 19k | ∝ 34k | 场景 2 慢 ~1.79× |
| Decode 总迭代次数 | **300** | **64** | 场景 1 多 4.7× |

### 5.3 TPOT 估算

```
TPOT ≈ T_compute + T_memory_access

其中：
  T_compute ≈ 常数（单个 token 的投影/采样，与序列长度无关）
  T_memory_access ∝ seq_len × bytes_per_token_kv
```

| | 场景 1 | 场景 2 | 比值 |
|--|--------|--------|------|
| **平均 TPOT** | T₀（基准） | ~1.7×T₀ | **+70%** |
| **总 Decode 时间** | 300 × T₀ | 64 × 1.7×T₀ ≈ 109×T₀ | 场景 1 的 2.75× |

---

## 六、TTFT 完整对比

```
                    场景1 (19k→300)          场景2 (34k→64)
                    ┌──────────────┐        ┌──────────────┐
Tokenize           │    ~10ms     │        │    ~18ms     │  ×1.8
Router Dispatch    │    ~5ms      │        │    ~5ms      │  固定
Bootstrap 握手     │    ~50ms     │        │    ~50ms     │  固定
                   │              │        │              │
Prefill Forward    │    3 chunks  │        │    5 chunks  │  ×1.7
                   │  ~3×T_chunk  │        │  ~5×T_chunk  │
                   │              │        │              │
KV Cache Transfer  │    ~3.6 GB   │        │    ~6.4 GB   │  ×1.79
                   │    ~450ms    │        │    ~800ms    │
                   │              │        │              │
Decode Bootstrap   │    ~20ms     │        │    ~20ms     │  固定
首 Token Decode    │    ~30ms     │        │    ~50ms     │  ×1.7
                   ├──────────────┤        ├──────────────┤
TTFT 估算          │  ~1.0-1.5s   │        │  ~1.8-2.5s   │  ×1.7-1.8
                   └──────────────┘        └──────────────┘
```

**TTFT 组成占比分析**：

```
场景 1 TTFT 组成:
  Prefill Forward:  ~40-50%  ████████████████████
  KV Transfer:      ~30-35%  ██████████████
  固定开销:          ~15-20%  ████████
  其他:             ~5%      ██

场景 2 TTFT 组成:
  Prefill Forward:  ~40-50%  ████████████████████████████
  KV Transfer:      ~30-35%  ██████████████████████
  固定开销:          ~10-15%  ████████
  其他:             ~5%      ██
```

**关键结论**：TTFT 主要由 Prefill Forward + KV Transfer 主导，两者都与输入长度线性相关。**场景 2 的 TTFT 约为场景 1 的 1.7-1.8 倍**。固定开销（Bootstrap、Router Dispatch）占比随输入增长而降低。

---

## 七、TPOT 完整对比

```
                    场景1 (19k input)         场景2 (34k input)
                    ┌──────────────┐         ┌──────────────┐
KV cache 长度       │   19,000     │         │   34,000     │  ×1.79
                    │              │         │              │
单次 Decode 步骤:   │              │         │              │
  - 加载 KV cache  │   ∝19k       │         │   ∝34k       │  ×1.79
  - 注意力计算     │   ∝19k       │         │   ∝34k       │  ×1.79
  - 采样/投影      │   固定       │         │   固定        │  ×1.0
                    │              │         │              │
TPOT 估算          │  T₀          │         │  ~1.7×T₀     │  ×1.7
                    ├──────────────┤         ├──────────────┤
                    └──────────────┘         └──────────────┘
```

---

## 八、综合时延趋势图

```
时延
  ▲
  │         ┌────────── 场景2 TTFT (~1.8-2.5s)
  │         │
  │    ┌────┤────────── 场景1 TTFT (~1.0-1.5s)
  │    │    │
  │    │    └────────── 固定开销 (Bootstrap/Router)
  │    │
  │────┤────────────────────────────────────── 首 Token 输出
  │    │
  │    │  ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱  场景1 Decode
  │    │  ╱  TPOT = T₀, 300次迭代      (斜率低，但距离长)
  │    │ ╱
  │    │╱
  │    │
  │    │   ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱  场景2 Decode
  │    │  ╱  TPOT = 1.7T₀, 64次迭代  (斜率陡，但距离短)
  │    │ ╱
  │    │╱
  │────┤────────────────────────────────────── 场景2 结束 (64 tokens)
  │    │
  │    │
  │────┤────────────────────────────────────── 场景1 结束 (300 tokens)
  │
  └────────────────────────────────────────── 时间 →
```

---

## 九、量化总结

### 9.1 时延指标对比

| 维度 | 场景 1 (19k→300) | 场景 2 (34k→64) | 场景2/场景1 |
|------|-----------------|-----------------|-----------|
| **TTFT** | 基准 (1.0-1.5s) | **+70-80%** (1.8-2.5s) | ×1.7-1.8 |
| **TPOT** | 基准 T₀ | **+70%** (~1.7×T₀) | ×1.7 |
| **总 Decode 时间** | 300 × T₀ | ~109 × T₀ | 场景 1 的 2.75× |
| **端到端延迟 (E2E)** | TTFT₁ + 300T₀ | 1.8×TTFT₁ + 109T₀ | 取决于 T₀ 值 |

### 9.2 吞吐指标对比

| 维度 | 场景 1 (19k→300) | 场景 2 (34k→64) | 说明 |
|------|-----------------|-----------------|------|
| **首 Token 吞吐** | 较高 | 较低（-30~40%） | TTFT 更长 |
| **Decode 吞吐（tok/s）** | 较高 | 较低（-40%） | TPOT 更高 |
| **总吞吐** | 取决于并发 | 取决于并发 | 场景 2 单请求更快完成 |

### 9.3 趋势总结

| 趋势 | 说明 |
|------|------|
| TTFT ∝ 输入长度 | 线性关系，主要受 Prefill Forward 和 KV Transfer 影响 |
| TPOT ∝ 输入长度 | 线性关系，主要受 KV cache 显存带宽影响 |
| 场景 2 TTFT ≈ 1.7-1.8× 场景 1 | ≈ 34k/19k |
| 场景 2 TPOT ≈ 1.7× 场景 1 | ≈ 34k/19k（但略低，因有固定开销） |
| 场景 2 总延迟可能更低 | 输出 token 少（64 vs 300），Decode 总时间远小于场景 1 |
| 场景 2 每 token 平均延迟更高 | TTFT 分摊到更少的输出 token 上 |

---

## 十、性能优化建议

### 10.1 针对长输入场景（如场景 2）

| 优化方向 | 具体措施 | 预期效果 |
|---------|---------|---------|
| **增大 chunked_prefill_size** | 调为 16384 或更高 | 减少 chunk 数和调度开销 |
| **增大 page_size** | 调为 16 或 64 | 减少 KV Transfer 的 RDMA 操作次数 |
| **启用 MLA（如支持）** | 使用 FlashMLA backend | 大幅压缩 KV cache，降低 TPOT |
| **KV cache 量化** | 使用 fp8 KV cache | 传输量减半，TPOT 降低 |

### 10.2 针对多输出场景（如场景 1）

| 优化方向 | 具体措施 | 预期效果 |
|---------|---------|---------|
| **增加 Decode 节点** | 7P+1D → 6P+2D | 提高 Decode 并发能力 |
| **Speculative Decoding** | 启用 EAGLE/Medusa | 减少 Decode 迭代次数 |
| **Batch 优化** | 调整 `schedule_policy` | 提高 Decode batch 填充率 |

### 10.3 监控指标

**代码位置**：`python/sglang/srt/observability/metrics_collector.py`

| Prometheus 指标 | 说明 | 关注场景 |
|----------------|------|---------|
| `sglang:first_token_latency_seconds` | TTFT | 两个场景 |
| `sglang:inter_token_latency_seconds` | TPOT | 长输入场景 |
| `sglang:kv_transfer_latency_ms` | KV 传输时延 | 长输入场景 |
| `sglang:kv_transfer_speed_gb_s` | KV 传输速度 | 网络瓶颈诊断 |
| `sglang:prefill_bootstrap_ms` | Bootstrap 时延 | 固定开销参考 |

---

## 十一、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `server_args.py` | 346, 1216-1303 | `chunked_prefill_size` 默认值与 GPU 内存分级 |
| `server_args.py` | 358, 1662-2650 | `page_size` 默认值 |
| `scheduler.py` | 2412-2460 | `_get_new_batch_prefill_raw()` prefill batch 构建 |
| `scheduler.py` | 918-919, 2303-2357 | Decode running batch 管理 |
| `req_time_stats.py` | 97-192 | `RequestStage` 定义（所有时间阶段） |
| `req_time_stats.py` | 400-401 | `get_first_token_latency()` TTFT 计算 |
| `req_time_stats.py` | 823-885 | `compute_and_observe_kv_transfer_metrics()` KV 传输指标 |
| `flashinfer_backend.py` | 881-917 | Decode 注意力 forward |
| `flashinfer_backend.py` | 1104-1124 | KV indices 构建 |
| `forward_batch_info.py` | 81-158 | `ForwardMode.DECODE` 定义 |
| `metrics_collector.py` | 1439-1452 | `observe_inter_token_latency()` TPOT 指标 |
| `disaggregation/utils.py` | 439-451 | `kv_to_page_num()` KV 页数计算 |
| `models/glm4.py` | 110-150 | GLM-4 注意力层（标准 MHA，非 MLA） |
