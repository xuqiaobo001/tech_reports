# vLLM 推理速度优化部署参数全景图

> 基于 vLLM 源码深度分析，系统梳理可调整的部署参数，形成推理速度优化的完整全景视图。
>
> 分析版本：vLLM main 分支 (commit: ed6d30377)

---

## 一、全景总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    vLLM 推理速度优化全景图                            │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│  ① 内存与缓存 │  ② 调度与批处理│ ③ 并行与分布式 │  ④ 编译与图优化       │
│  gpu_mem_util│  max_batched │  tensor_para │  enforce_eager         │
│  block_size  │  max_seqs    │  pipeline_   │  cudagraph_capture     │
│  kv_dtype    │  chunked_    │  data_para   │  compilation_config    │
│  prefix_cache│  sched_policy│  expert_para │  performance_mode      │
├──────────────┼──────────────┼──────────────┼────────────────────────┤
│  ⑤ 注意力机制 │  ⑥ 量化与精度 │  ⑦ 推测解码    │  ⑧ PD分离部署         │
│  attn_backend│  quantization│  spec_method │  kv_connector          │
│  flash_attn  │  dtype       │  num_spec_   │  kv_role               │
│  sliding_win │  kv_cache_dt │  parallel_   │  kv_buffer_size        │
│  moe_backend │  turboquant  │  drafting    │  disaggregated_encoder │
└──────────────┴──────────────┴──────────────┴────────────────────────┘
```

---

## 二、各维度详细参数

### 1. 内存与缓存管理

**核心源码**：`vllm/config/cache.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--gpu-memory-utilization` | 0.92 | **↑吞吐**: 0.95+; **↓风险**: 0.80-0.90 | 控制KV缓存占用GPU显存比例。越高→缓存越大→并发越高→吞吐越大，但OOM风险增加 |
| `--block-size` | 16 | **短序列**: 8; **长序列**: 32 | KV缓存块粒度。小块→内存利用率高但管理开销大；大块→相反 |
| `--kv-cache-dtype` | auto | **省显存**: fp8_e4m3; **精度优先**: auto | FP8缓存→显存减半→并发翻倍→吞吐翻倍，有轻微精度损失 |
| `--enable-prefix-caching` | True | **RAG/多轮对话**: 开启 | 复用公共前缀(system prompt等)的KV缓存，避免重复计算 |
| `--max-model-len` | 自动 | **按需设置** | 最大序列长度。设置过大→浪费显存；设置过小→请求被拒 |
| `--kv-cache-memory-bytes` | None | 精确控制KV缓存大小 | 直接指定KV缓存字节数，覆盖gpu_memory_utilization |
| `--kv-offloading-size` | None | **长上下文**: 设置为CPU内存GiB数 | 将KV缓存卸载到CPU，支持更长上下文，但增加延迟 |

**详细说明**：

#### `gpu_memory_utilization` (源码：`vllm/config/cache.py:54-61`)
- 控制GPU显存中用于模型执行器的比例（0.0 到 1.0）
- 更高值（0.95+）：增加 KV 缓存大小 → 更高的批处理容量 → 更好的吞吐量，但有 OOM 风险
- 更低值（0.80-0.90）：对大型模型或多实例部署更安全，但会减少最大批处理大小
- 每个实例可独立设置；不解释其他实例的显存占用

#### `block_size` (源码：`vllm/config/cache.py:47-49`)
- 连续缓存块的 token 数量大小（默认：16）
- 更小（8-16）：对变长序列内存利用率更高，碎片更少，但管理开销更高
- 更大（32+）：减少元数据开销，对长序列更好，但可能浪费短请求的内存

#### `kv_cache_dtype` (源码：`vllm/config/cache.py:62-69`)
- KV 缓存的数据类型，可选值：
  - `"auto"` - 使用模型数据类型
  - `"fp8"` / `"fp8_e4m3"` / `"fp8_e5m2"` - FP8 格式，显存减半
  - `"turboquant_k8v4"` / `"turboquant_4bit_nc"` 等 - 高级量化
  - `"int8_per_token_head"` / `"fp8_per_token_head"` - 按 token/head 缩放

#### `enable_prefix_caching` (源码：`vllm/config/cache.py:79-80`)
- 缓存公共前缀的 KV 块（系统提示、重复文档）
- 对重复提示有显著加速（例如 RAG、带系统提示的聊天机器人）

#### `kv_offloading_size` (源码：`vllm/config/cache.py:155-159`)
- CPU 卸载缓冲区大小（GiB），默认 None（禁用）
- 允许更大的上下文长度或批处理大小，但会增加卸载块的延迟

---

### 2. 调度与批处理

**核心源码**：`vllm/config/scheduler.py`、`vllm/v1/core/sched/scheduler.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--max-num-batched-tokens` | 自动(H100:16384, 其他:8192) | **吞吐优先**: ↑; **延迟优先**: ↓ | 每次迭代处理的最大token数。是吞吐-延迟权衡的核心旋钮 |
| `--max-num-seqs` | 自动(H100:1024, 其他:256) | **并发高**: ↑; **低延迟**: ↓ | 每次迭代最大序列数。限制并发请求数 |
| `--enable-chunked-prefill` | True | **推荐开启** | 将长prompt分块处理，避免长请求阻塞短请求 |
| `--max-num-partial-prefills` | 1 | **多长prompt场景**: 2-4 | 并行分块prefill数量。增大可提高长prompt吞吐 |
| `--long-prefill-token-threshold` | max_model_len×0.04 | 按需调整 | 判定"长prompt"的阈值，控制短请求插队策略 |
| `--scheduling-policy` | fcfs | **公平**: fcfs; **优先级**: priority | 调度策略。priority模式可降低高优先级请求延迟 |
| `--async-scheduling` | 自动 | **推荐开启** | 异步调度，减少GPU空闲时间 |

**关键公式**（源码 `vllm/engine/arg_utils.py:2152-2233`）：

| 硬件 | `max_num_batched_tokens` (LLM) | `max_num_batched_tokens` (API Server) | `max_num_seqs` |
|------|------|------|------|
| H100/H200 (≥70GB) | 16384 | 8192 | 1024 |
| 其他 GPU | 8192 | 2048 | 256 |
| TPU | 按芯片变化(256-2048) | - | - |
| CPU | 4096 × world_size | - | 256 × world_size |

> **注意**：`performance_mode="throughput"` 时以上参数会自动翻倍。

**详细说明**：

#### `max_num_batched_tokens` (源码：`vllm/config/scheduler.py:49-54`)
- 每次迭代处理的最大 token 数，这是吞吐量与延迟权衡的**核心旋钮**
- 更高值：大批次更好的吞吐量，更高的内存压力，每次迭代延迟更长
- 更低值：每次请求延迟更低，适合交互式工作负载

#### `max_num_seqs` (源码：`vllm/config/scheduler.py:63-68`)
- 每次迭代处理的最大序列数
- 更高值：更好的请求并发性，更高的吞吐量
- 更低值：每次请求延迟更低，内存开销更少
- 约束：必须 ≤ `max_num_batched_tokens`

#### `enable_chunked_prefill` (源码：`vllm/config/scheduler.py:84-90`)
- 将长 prompt 分块处理，混合 prefill/decode
- 防止长 prompt 阻塞队列，更好的公平性，更高的整体吞吐量

#### `scheduling_policy` (源码：`vllm/config/scheduler.py:109-115`)
- `"fcfs"` - 先到先服务，公平、简单、无优先级
- `"priority"` - 优先级调度，需要优先级元数据，可减少尾延迟

#### `async_scheduling` (源码：`vllm/config/scheduler.py:146-149`)
- 异步调度，减少 GPU 利用率间隙
- 更好的延迟和吞吐量

---

### 3. 并行与分布式

**核心源码**：`vllm/config/parallel.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--tensor-parallel-size` | 1 | **大模型**: 2-8 | 张量并行。增加→带宽增加→推理加速，但通信开销增加 |
| `--pipeline-parallel-size` | 1 | **超大模型**: 2-4 | 流水线并行。增加→支持更大模型，但引入流水线气泡 |
| `--data-parallel-size` | 1 | **多租户高吞吐**: ↑ | 数据并行。线性扩展吞吐，每个副本占完整显存 |
| `--data-parallel-backend` | mp | **大规模**: ray | 数据并行后端。ray适合大规模集群 |
| `--enable-expert-parallel` | False | **MoE模型**: 开启 | MoE模型专家并行，减少通信开销 |
| `--all2all-backend` | allgather_reducescatter | **低延迟**: deepep_low_latency; **高吞吐**: deepep_high_throughput | MoE通信后端选择 |
| `--disable-custom-all-reduce` | False | **保持False** | 关闭→使用自定义allreduce核，比NCCL更快 |

**详细说明**：

#### `tensor_parallel_size` (源码：`vllm/config/parallel.py:113-114`)
- 张量并行的 GPU 数量
- 更高值：适合更大的模型，增加显存带宽
- 通信开销：TP 越多 = 更多 allreduce 操作
- 最佳范围：大多数模型 2-8

#### `pipeline_parallel_size` (源码：`vllm/config/parallel.py:111-112`)
- 流水线阶段数
- 更高值：支持更大的模型，但引入流水线气泡（空闲时间）
- 权衡：显存 vs 吞吐量

#### `data_parallel_size` (源码：`vllm/config/parallel.py:117-119`)
- 数据并行副本数
- 更高值：多租户服务的线性吞吐扩展
- 显存：每个副本需要完整的模型副本

#### 专家并行相关参数
- `enable_expert_parallel` (源码：`vllm/config/parallel.py:149`) - 启用 MoE 层的专家并行
- `enable_eplb` (源码：`vllm/config/parallel.py:158`) - 启用专家并行负载均衡
- `expert_placement_strategy` (源码：`vllm/config/parallel.py:162`) - 专家放置策略（"linear" / "round_robin"）

#### 上下文并行
- `prefill_context_parallel_size` (源码：`vllm/config/parallel.py:115`) - Prefill 上下文并行度
- `decode_context_parallel_size` (源码：`vllm/config/parallel.py:308`) - Decode 上下文并行度

---

### 4. 编译与图优化

**核心源码**：`vllm/config/compilation.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--enforce-eager` | False | **调试时**: True; **生产**: False | 关闭CUDA Graph会显著降低性能（生产环境必须False） |
| `--compilation-config` | O2 | **最快**: O2; **快速启动**: O0 | 编译优化级别。O2=完整优化(Dynamo+Inductor+full cudagraphs) |
| `--max-cudagraph-capture-size` | min(max_num_seqs×2, 512) | **大batch**: ↑ | CUDA Graph最大捕获batch size |
| `--cudagraph-capture-sizes` | 自动 | 精确指定捕获尺寸 | 指定具体batch size捕获，减少启动时间 |
| `--performance-mode` | balanced | **吞吐**: throughput; **低延迟**: interactivity | 全局性能模式。throughput自动翻倍batch参数 |

**详细说明**：

#### `enforce_eager` (源码：`vllm/config/model.py:209`)
- 禁用 CUDA Graph，强制 eager 模式
- `False`（默认）：使用 CUDA Graph 加速执行
- `True`：无 CUDA Graph，显著变慢，仅用于调试

#### `compilation_config.mode` (源码：`vllm/config/compilation.py:336-340`)
编译优化级别：
- **O0**：无优化，最快启动，最慢推理
- **O1**：快速优化（Dynamo+Inductor，分段 CUDA Graph）
- **O2**：完整优化（O1 + 完整 CUDA Graph，**默认**）
- **O3**：当前与 O2 相同

#### CUDA Graph 模式 (源码：`vllm/config/compilation.py:53-103`)
- `NONE` - 不使用 CUDA Graph
- `PIECEWISE` - 分段 CUDA Graph
- `FULL` - 完整 CUDA Graph
- `FULL_DECODE_ONLY` - 仅 decode 阶段完整 CUDA Graph
- `FULL_AND_PIECEWISE` - 完整 + 分段 CUDA Graph

#### `performance_mode` (源码：`vllm/engine/arg_utils.py:639`)
- `"interactivity"` - 偏好低延迟（小批次，细粒度图）
- `"throughput"` - 偏好高吞吐量（自动翻倍 `max_num_batched_tokens` 和 `max_num_seqs`）
- `"balanced"` - 中间路线（默认）

---

### 5. 注意力机制

**核心源码**：`vllm/config/attention.py`、`vllm/v1/attention/backends/`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--attention-backend` | 自动 | **NVIDIA GPU**: flashinfer | 注意力后端。flashinfer在NVIDIA上性能最优 |
| `--use-prefill-decode-attention` | False | 按需开启 | 分离prefill/decode注意力核，优化各自路径 |
| `--moe-backend` | auto | **性能优先**: flashinfer系列 | MoE计算后端选择 |

**可用注意力后端**（源码：`vllm/v1/attention/backends/registry.py:34-88`）：

| 后端 | 适用场景 |
|------|----------|
| `FLASH_ATTN` | FlashAttention 标准后端 |
| `FLASH_ATTN_DIFFKV` | FlashAttention 不同 KV |
| `TRITON_ATTN` | Triton 注意力 |
| `ROCM_ATTN` | AMD GPU (ROCm) 注意力 |
| `FLASHINFER` | FlashInfer（NVIDIA 推荐） |
| `FLASHINFER_MLA` | FlashInfer MLA 后端 |
| `TURBOQUANT` | TurboQuant 注意力后端 |
| `CPU_ATTN` | CPU 注意力后端 |

**其他注意力参数**（源码：`vllm/config/attention.py`）：
- `flash_attn_max_num_splits_for_cuda_graph` = 32（CUDA Graph 最大分割数）
- `tq_max_kv_splits_for_cuda_graph` = 32（TurboQuant CUDA Graph 最大分割数）

---

### 6. 量化与精度

**核心源码**：`vllm/engine/arg_utils.py:491`、`vllm/model_executor/layers/quantization/__init__.py:12-44`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--dtype` | auto | **A100+/H100**: bfloat16; **其他**: half | 模型权重数据类型 |
| `--quantization` | None | **4bit**: awq/gptq; **8bit**: fp8 | 量化方法。4bit→显存减少4倍→吞吐提升；8bit→显存减少2倍 |
| `--kv-cache-dtype` | auto | **fp8**: 显存减半 | KV缓存量化。FP8是最实用的缓存量化选项 |

**支持的量化方法完整列表**：

| 量化方法 | 说明 | 精度损失 |
|----------|------|----------|
| `awq` / `awq_marlin` | 激活感知权重量化 | 中等 |
| `gptq` / `gptq_marlin` | GPT 量化 | 中等 |
| `fp8` | 8位浮点 | 低 |
| `modelopt` / `modelopt_fp4` | NVIDIA ModelOpt 量化 | 低-中 |
| `gguf` | GGUF 格式 | 视配置而定 |
| `compressed-tensors` | 压缩张量格式 | 视配置而定 |
| `bitsandbytes` | BitsAndBytes 量化 | 中等 |
| `experts_int8` | MoE 专家 Int8 | 低 |
| `torchao` | PyTorch AO 量化 | 视配置而定 |
| `mxfp4` / `mxfp8` | MX 格式 | 视配置而定 |

**量化性能排序参考**：

```
显存节省: fp8 > awq/gptq(int4) > gptq(int8) > bfloat16 > float32
推理速度: fp8 ≈ awq > gptq > 无量化（受限于显存带宽时量化更快）
精度损失: int4 > int8 > fp8 > 无
```

---

### 7. 推测解码

**核心源码**：`vllm/config/speculative.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--speculative-method` | None | **轻量**: ngram; **高加速**: eagle/medusa | 推测解码方法 |
| `--num-speculative-tokens` | 模型默认 | **保守**: 3-5; **激进**: 8-10 | 每步推测token数。越高→潜在加速越大，但接受率下降 |
| `--parallel-drafting` | False | **开启**: 并行推测 | 并行推测，进一步提升加速比 |

**可用的推测解码方法**（源码：`vllm/config/speculative.py:34-64`）：

| 方法 | 说明 | 预期加速 |
|------|------|----------|
| `ngram` | N-gram 推测（无额外模型） | 1.5-2x |
| `medusa` | Medusa 推测 | 2-3x |
| `mlp_speculator` | MLP 推测器 | 2-3x |
| `draft_model` | 小模型推测 | 2-3x |
| `eagle` / `eagle3` | EAGLE 推测 | 2-4x |
| `deepseek_mtp` | DeepSeek MTP 推测 | 模型相关 |
| `suffix` | 后缀解码 | 模型相关 |

**其他推测解码参数**：
- `draft_tensor_parallel_size` (源码：`vllm/config/speculative.py:89-91`) - Draft 模型 TP 大小
- `disable_padded_drafter_batch` (源码：`vllm/config/speculative.py:119-123`) - 禁用填充 drafter 批处理

---

### 8. PD分离部署（Prefill-Decode Disaggregation）

**核心源码**：`vllm/config/kv_transfer.py`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--kv-connector` | None | **分离部署**: 设置连接器 | KV缓存传输连接器 |
| `--kv-role` | None | **prefill节点**: kv_producer; **decode节点**: kv_consumer | PD分离角色 |
| `--kv-buffer-size` | 1e9 | 按网络带宽调整 | KV传输缓冲区大小 |
| `--kv-parallel-size` | 1 | **高并发**: ↑ | KV传输并行度 |

**KV 角色说明**（源码：`vllm/config/kv_transfer.py:41`）：
- `kv_producer` - Prefill 节点，负责处理长 prompt 并产生 KV 缓存
- `kv_consumer` - Decode 节点，负责从 Prefill 节点接收 KV 缓存并执行 decode
- `kv_both` - 同时承担两种角色

**其他 KV 传输参数**：
- `kv_rank` (源码：`vllm/config/kv_transfer.py:45`) - KV 传输中的排名（prefill: 0, decode: 1）
- `kv_ip` (源码：`vllm/config/kv_transfer.py:54`) - KV 连接器 IP 地址（默认: 127.0.0.1）
- `kv_port` (源码：`vllm/config/kv_transfer.py:57`) - KV 连接器端口（默认: 14579）
- `kv_load_failure_policy` (源码：`vllm/config/kv_transfer.py:70`) - KV 缓存加载失败策略（"recompute" / "fail"）

**示例部署**：`vllm/examples/online_serving/disaggregated_encoder/`

---

## 三、场景化调优策略

### 场景 A：最大吞吐（Batch推理、离线处理）

```bash
python -m vllm.entrypoints.openai.api_server \
  --model <model> \
  --performance-mode throughput \          # 自动翻倍batch参数
  --gpu-memory-utilization 0.95 \          # 最大化KV缓存
  --max-num-batched-tokens 32768 \         # 大batch token
  --max-num-seqs 2048 \                    # 高并发
  --enable-prefix-caching \                # 开启前缀缓存
  --kv-cache-dtype fp8_e4m3 \              # FP8缓存
  --quantization fp8 \                     # FP8量化（如硬件支持）
  --tensor-parallel-size 8 \               # 充分利用多卡
  --disable-custom-all-reduce False \      # 自定义allreduce
  --compilation-config '{"mode":"O2"}'     # 最高编译优化
```

### 场景 B：最低延迟（在线对话、实时交互）

```bash
python -m vllm.entrypoints.openai.api_server \
  --model <model> \
  --performance-mode interactivity \       # 低延迟模式
  --gpu-memory-utilization 0.90 \          # 稳健配置
  --max-num-batched-tokens 4096 \          # 小batch快速响应
  --max-num-seqs 128 \                     # 限制并发
  --enable-chunked-prefill \               # 分块prefill防阻塞
  --async-scheduling \                     # 异步调度
  --speculative-method eagle \             # 推测解码加速
  --num-speculative-tokens 5               # 适中推测数
```

### 场景 C：长上下文（RAG、文档处理）

```bash
python -m vllm.entrypoints.openai.api_server \
  --model <model> \
  --max-model-len 128000 \                 # 按需设置最大长度
  --enable-prefix-caching \                # 复用公共前缀
  --enable-chunked-prefill \               # 分块处理长prompt
  --max-num-partial-prefills 4 \           # 并行处理多个长prompt
  --kv-cache-dtype fp8_e4m3 \              # FP8缓存节省显存
  --kv-offloading-size 8 \                 # CPU卸载(8GiB)
  --block-size 16                          # 小块减少浪费
```

### 场景 D：PD分离部署（大规模在线服务）

```bash
# Prefill节点
python -m vllm.entrypoints.openai.api_server \
  --kv-role kv_producer \
  --kv-connector <connector> \
  --gpu-memory-utilization 0.95 \
  --max-num-batched-tokens 32768

# Decode节点
python -m vllm.entrypoints.openai.api_server \
  --kv-role kv_consumer \
  --kv-connector <connector> \
  --performance-mode interactivity \
  --speculative-method eagle
```

### 场景 E：显存受限环境

```bash
python -m vllm.entrypoints.openai.api_server \
  --model <model> \
  --gpu-memory-utilization 0.80 \          # 保守显存使用
  --kv-cache-dtype fp8_e4m3 \              # FP8缓存
  --quantization awq \                     # AWQ 4bit量化
  --max-num-seqs 64 \                      # 限制并发
  --kv-offloading-size 4 \                 # CPU卸载(4GiB)
  --max-model-len 4096                     # 限制序列长度
```

---

## 四、参数优先级与影响矩阵

```
影响程度 (★数量 = 对推理速度的影响程度)

★★★★★ 关键参数（必须调优）
  ┌─ gpu-memory-utilization     ─ 直接决定KV缓存大小 → 并发能力
  ├─ max-num-batched-tokens     ─ 吞吐-延迟的核心旋钮
  ├─ enforce-eager / CUDA Graph ─ 图优化影响巨大(2-3x)
  └─ performance-mode           ─ 全局性能基调

★★★★  重要参数（显著影响）
  ┌─ tensor-parallel-size       ─ 多卡并行核心
  ├─ kv-cache-dtype (fp8)       ─ 显存翻倍→并发翻倍
  ├─ enable-prefix-caching      ─ RAG场景2-10x加速
  ├─ enable-chunked-prefill     ─ 防止长请求阻塞
  └─ quantization               ─ 量化节省显存带宽

★★★  有益参数（锦上添花）
  ┌─ max-num-seqs               ─ 并发上限
  ├─ speculative decoding       ─ 单请求延迟优化
  ├─ data-parallel-size         ─ 线性吞吐扩展
  ├─ attention-backend           ─ 核函数选择
  └─ async-scheduling           ─ 减少GPU空闲

★★   场景参数（特定场景有效）
  ┌─ kv-offloading-size         ─ 长上下文
  ├─ max-num-partial-prefills   ─ 多长prompt
  ├─ PD disaggregation          ─ 大规模分离部署
  └─ moe-backend                ─ MoE模型专用

★    微调参数（影响有限）
  ┌─ block-size                 ─ 内存管理粒度
  ├─ scheduling-policy          ─ 调度策略
  └─ cudagraph-capture-sizes    ─ 图捕获尺寸
```

---

## 五、环境变量调优

**核心源码**：`vllm/envs.py`

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `VLLM_USE_DEEP_GEMM` | - | 启用 DeepGemm 优化 |
| `VLLM_USE_FUSED_MOE_GROUPED_TOPK` | - | 启用融合 MoE 操作 |
| `VLLM_DISABLE_COMPILE_CACHE` | - | 禁用编译缓存 |
| `VLLM_USE_AOT_COMPILE` | - | 启用 AOT 编译 |
| `VLLM_USE_TRITON_AWQ` | - | 使用 Triton 进行 AWQ 量化 |
| `VLLM_WORKER_MULTIPROC_METHOD` | fork | Worker 进程创建方式（"fork" / "spawn"） |
| `VLLM_ENABLE_V1_MULTIPROCESSING` | True | 启用 V1 多进程 |
| `VLLM_RAY_PER_WORKER_GPUS` | 1.0 | 每个 Ray Worker 的 GPU 数量 |

---

## 六、其他相关参数

### 结构化输出
**核心源码**：`vllm/config/structured_outputs.py`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--guided-decoding-backend` | auto | 结构化输出后端（"xgrammar" / "guidance" / "outlines" / "lm-format-enforcer"） |

### LoRA 配置
**核心源码**：`vllm/config/lora.py`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--enable-lora` | False | 启用 LoRA 适配器 |
| `--max-loras` | 1 | 批处理中最大 LoRA 数量 |
| `--max-lora-rank` | 16 | 最大 LoRA 秩 |
| `--lora-dtype` | auto | LoRA 数据类型 |
| `--fully-sharded-loras` | False | 使用张量并行分片 LoRA |

### NUMA 绑定
**核心源码**：`vllm/engine/arg_utils.py:421-423`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--numa-bind` | False | 启用 NUMA 绑定（CPU 优化） |
| `--numa-bind-nodes` | None | 指定 NUMA 节点 |
| `--numa-bind-cpus` | None | 指定 CPU 核心 |

### 流式输出
**核心源码**：`vllm/config/scheduler.py:151-155`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--stream-interval` | 1 | 流式输出缓冲 token 数 |

---

## 七、关键源码文件索引

| 维度 | 核心文件 | 说明 |
|------|----------|------|
| 全局入口 | `vllm/engine/arg_utils.py` | EngineArgs 定义，所有 CLI 参数 |
| 缓存配置 | `vllm/config/cache.py` | KV 缓存、前缀缓存、块大小 |
| 调度配置 | `vllm/config/scheduler.py` | 批处理、调度策略 |
| 调度实现 | `vllm/v1/core/sched/scheduler.py` | 调度器核心逻辑 |
| 请求队列 | `vllm/v1/core/sched/request_queue.py` | FCFS/Priority 队列实现 |
| KV缓存管理 | `vllm/v1/core/kv_cache_manager.py` | KV 缓存分配与回收 |
| 并行配置 | `vllm/config/parallel.py` | TP/PP/DP/EP 配置 |
| 编译配置 | `vllm/config/compilation.py` | CUDA Graph、编译优化级别 |
| 注意力配置 | `vllm/config/attention.py` | 注意力后端选择 |
| 注意力后端 | `vllm/v1/attention/backends/registry.py` | 注意力后端注册表 |
| 注意力工具 | `vllm/v1/attention/backends/utils.py` | Prefill/Decode 分离工具 |
| 推测解码 | `vllm/config/speculative.py` | 推测解码参数 |
| PD 分离 | `vllm/config/kv_transfer.py` | KV 传输、PD 分离 |
| 模型配置 | `vllm/config/model.py` | 模型数据类型、eager 模式 |
| 量化注册 | `vllm/model_executor/layers/quantization/__init__.py` | 量化方法注册 |
| 环境变量 | `vllm/envs.py` | 运行时环境变量 |
| 模型执行器 | `vllm/v1/worker/gpu/model_runner.py` | GPU 模型执行器 |
| 结构化输出 | `vllm/config/structured_outputs.py` | 结构化输出配置 |
| LoRA 配置 | `vllm/config/lora.py` | LoRA 适配器配置 |

---

## 八、调优决策流程

```
开始调优
    │
    ▼
┌───────────────────────────────┐
│ Step 1: 确定目标               │
│ 吞吐优先? 延迟优先? 显存受限?    │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 2: 基础配置               │
│ • performance-mode             │
│ • gpu-memory-utilization       │
│ • dtype + quantization         │
│ • tensor-parallel-size         │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 3: KV缓存优化             │
│ • kv-cache-dtype (fp8?)        │
│ • enable-prefix-caching        │
│ • block-size                   │
│ • max-model-len                │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 4: 调度优化               │
│ • max-num-batched-tokens       │
│ • max-num-seqs                 │
│ • enable-chunked-prefill       │
│ • async-scheduling             │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 5: 编译与图优化           │
│ • enforce-eager = False        │
│ • compilation-config (O2)      │
│ • cudagraph-capture-sizes      │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 6: 高级优化（可选）        │
│ • speculative decoding         │
│ • PD disaggregation            │
│ • attention-backend            │
│ • 环境变量                     │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 7: 基准测试验证           │
│ • 测试吞吐量 (tokens/s)        │
│ • 测试延迟 (TTFT, TPOT)        │
│ • 监控显存使用                  │
│ • 迭代调整                     │
└───────────────────────────────┘
```

---

## 九、总结

vLLM 推理速度优化遵循 **"显存→并发→计算→通信"** 的调优路径：

1. **显存优化**：通过 `gpu_memory_utilization` + `kv_cache_dtype` + `quantization` 最大化可用显存
2. **并发优化**：通过 `max_num_batched_tokens` + `max_num_seqs` + `enable_chunked_prefill` 提升并发
3. **计算优化**：利用 CUDA Graph (`enforce_eager=False`) + 编译优化 (`O2`) 加速计算
4. **通信优化**：通过 `tensor_parallel_size` + `data_parallel_size` 扩展到多卡多机
5. **高级优化**：推测解码、PD 分离、注意力后端选择等场景化优化

> **关键提示**：调优应基于实际工作负载进行基准测试验证，不同模型和硬件的最优参数组合可能差异显著。
