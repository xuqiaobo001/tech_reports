# SGLang 推理速度优化部署参数全景图

> 基于 SGLang 源码深度分析，系统梳理可调整的部署参数，形成推理速度优化的完整全景视图。
>
> 分析代码路径：`/root/vllm_ascend/sglang/`

---

## 一、全景总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     SGLang 推理速度优化全景图                              │
├───────────────┬───────────────┬───────────────┬─────────────────────────┤
│ ① 内存与缓存   │ ② 调度与批处理  │ ③ 并行与分布式  │ ④ 编译与图优化          │
│ mem_fraction  │ schedule_policy│ tp_size       │ disable_cuda_graph      │
│ page_size     │ max_running    │ dp_size       │ cuda_graph_max_bs       │
│ kv_cache_dtype│ chunked_prefill│ ep_size       │ enable_torch_compile    │
│ radix_cache   │ max_prefill    │ pp_size       │ piecewise_cuda_graph    │
│ hicache       │ schedule_conserv│ load_balance │ cuda_graph_bs           │
├───────────────┼───────────────┼───────────────┼─────────────────────────┤
│ ⑤ 注意力后端   │ ⑥ 量化与精度    │ ⑦ 推测解码      │ ⑧ 重叠调度与PD分离      │
│ attention_    │ quantization  │ speculative_  │ disable_overlap_schedule│
│ backend       │ dtype         │ algorithm     │ enable_two_batch_overlap│
│ flashinfer    │ kv_cache_dtype│ eagle_topk    │ enable_mixed_chunk      │
│ fa3/fa4       │ fp8_gemm      │ num_draft_    │ disaggregation_mode     │
│ triton        │ mxfp4/nvfp4   │ tokens        │ num_continuous_decode   │
└───────────────┴───────────────┴───────────────┴─────────────────────────┘
```

---

## 二、各维度详细参数

### 1. 内存与缓存管理

**核心源码**：`python/sglang/srt/server_args.py:342-367`、`python/sglang/srt/mem_cache/`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--mem-fraction-static` | 自动计算(约0.88) | **↑吞吐**: 手动调高; **↓风险**: 调低 | 控制GPU显存中用于模型权重+KV缓存的比例。公式：`(GPU总显存 - 预留内存) / GPU总显存` |
| `--page-size` | 自动(通常1) | **MLA模型**: 保持默认; **标准模型**: 保持默认 | KV缓存的页大小。影响内存分配粒度 |
| `--kv-cache-dtype` | auto | **省显存**: fp8_e4m3; **精度优先**: auto | KV缓存数据类型。FP8→显存减半→并发翻倍 |
| `--max-total-tokens` | None(自动) | **手动控制**: 按需设置 | KV缓存池总token容量上限。覆盖mem-fraction-static的计算 |
| `--disable-radix-cache` | False | **保持False** | Radix Cache是SGLang的核心优势，关闭会失去前缀复用能力 |
| `--radix-eviction-policy` | lru | **热点数据**: lfu; **通用**: lru | Radix Cache淘汰策略。lfu适合有热点前缀的场景 |
| `--cpu-offload-gb` | 0 | **长上下文**: 设置CPU内存GiB数 | 将KV缓存卸载到CPU，支持更长上下文 |
| `--enable-hierarchical-cache` | False | **超长上下文/重复prompt**: 开启 | 层级缓存(HiCache)，利用SSD/Host内存扩展缓存 |
| `--hicache-ratio` | 2.0 | **更多缓存**: ↑ | HiCache相对于GPU缓存的扩展比例 |
| `--hicache-size` | 0 | **手动控制**: 设置字节数 | HiCache显存大小 |

**`mem-fraction-static` 自动计算逻辑**（源码：`server_args.py:1356-1403`）：

```
reserved_mem = 512MB (基础)
             + chunked_prefill_size × 1.5 (激活内存)
             + cuda_graph_max_bs × 2 (CUDA Graph缓冲)
             + tp_size × pp_size / 8 × 1024 (并行开销)

mem_fraction_static = (gpu_mem - reserved_mem) / gpu_mem
```

---

### 2. 调度与批处理

**核心源码**：`python/sglang/srt/managers/scheduler.py`、`python/sglang/srt/server_args.py:341-366`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--schedule-policy` | fcfs | **公平**: fcfs; **优先级**: priority(需配合--enable-priority-scheduling) | 调度策略。fcfs为简单队列，priority支持请求优先级 |
| `--max-running-requests` | 自动 | **↑并发**: ↑; **↓延迟**: ↓ | 同时运行的最大请求数。直接控制并发能力 |
| `--max-queued-requests` | 自动 | **高负载**: ↑ | 等待队列中的最大请求数 |
| `--max-prefill-tokens` | 16384 | **大prompt**: ↑; **低延迟**: ↓ | 单次prefill最大token数 |
| `--chunked-prefill-size` | 自动(见下表) | **吞吐优先**: ↑; **延迟优先**: ↓ | Chunked prefill的分块大小。核心性能旋钮 |
| `--prefill-max-requests` | None | **控制prefill并发**: 手动设置 | 限制同时进行prefill的请求数 |
| `--schedule-conservativeness` | 1.0 | **激进(高吞吐)**: 0.5-0.8; **保守(低延迟)**: 1.2-1.5 | 调度保守度。影响新token比例估计，控制是否接纳更多请求 |
| `--enable-mixed-chunk` | False | **推荐开启**: 混合prefill和decode | 允许在同一批次中混合prefill和decode请求 |
| `--enable-prefill-delayer` | False | **高并发场景**: 开启 | 延迟prefill以优先处理decode请求 |
| `--enable-dynamic-chunking` | False | **PP场景**: 开启 | 动态调整chunk大小（仅PP有效） |
| `--scheduler-recv-interval` | 1 | **减少CPU开销**: ↑ | 调度器接收请求的间隔 |

**`chunked-prefill-size` 自动默认值**（源码：`server_args.py:1240-1303`）：

| GPU 显存 | chunked-prefill-size | cuda-graph-max-bs (tp<4) | cuda-graph-max-bs (tp≥4) |
|----------|---------------------|-------------------------|-------------------------|
| < 20GB (T4, 4080) | 2048 | 8 | 8 |
| 20-35GB (A10, 4090) | 2048 | 24 | 80 |
| 35-60GB (A100-40G, L40) | 4096 | 32 | 160 |
| 60-90GB (H100, A100-80G) | 8192 | 256 | 512 |
| 90-160GB (H200, H20) | 8192 | 256 | 512 |
| > 160GB (B200, MI300) | 16384 | 512 | 512 |

---

### 3. 并行与分布式

**核心源码**：`python/sglang/srt/server_args.py:370、444-448、524-552`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--tp-size` | 1 | **大模型**: 2-8 | 张量并行。增加→带宽增加→推理加速，但通信开销增加 |
| `--dp-size` | 1 | **多租户高吞吐**: ↑ | 数据并行。线性扩展吞吐，每个副本占完整显存 |
| `--pp-size` | 1 | **超大模型**: 2-4 | 流水线并行。支持更大模型，但引入流水线气泡 |
| `--ep-size` | 1 | **MoE模型**: 与TP配合 | 专家并行。将MoE专家分布到不同设备 |
| `--moe-dp-size` | 1 | **MoE高吞吐**: ↑ | MoE数据并行 |
| `--load-balance-method` | auto | **DP**: round_robin; **PD-prefill**: follow_bootstrap_room | 数据并行负载均衡策略 |
| `--attn-cp-size` | 1 | **超长序列**: ↑ | 注意力上下文并行 |
| `--disable-custom-all-reduce` | False | **保持False** | 自定义all-reduce比NCCL更快 |
| `--dist-init-addr` | None | **多节点**: 设置主节点地址 | 分布式初始化地址 |
| `--nnodes` | 1 | **多节点**: 设置节点数 | 参与推理的节点数 |

**MoE 专家并行相关参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--moe-a2a-backend` | none | MoE All-to-All后端（"deepep"/"mooncake"/"nixl"/"flashinfer"） |
| `--moe-runner-backend` | auto | MoE计算后端（"deep_gemm"/"triton"/"flashinfer_trtllm"等） |
| `--deepep-mode` | auto | DeepEP模式（"normal"/"low_latency"） |
| `--enable-eplb` | False | 专家并行负载均衡 |
| `--enable-flashinfer-allreduce-fusion` | False | FlashInfer all-reduce融合 |

---

### 4. 编译与CUDA Graph优化

**核心源码**：`python/sglang/srt/server_args.py:608-651、1329-1414`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--disable-cuda-graph` | False | **生产**: False; **调试**: True | CUDA Graph是关键优化(2-3x)，关闭会显著降低性能 |
| `--cuda-graph-max-bs` | 自动(见上表) | **大batch**: ↑; **省显存**: ↓ | CUDA Graph捕获的最大batch size |
| `--cuda-graph-bs` | 自动生成 | **精确控制**: 手动指定列表 | CUDA Graph捕获的batch size列表 |
| `--enable-torch-compile` | False | **实验性优化**: 开启 | Torch编译优化（需配合torch.compile） |
| `--torch-compile-max-bs` | 32 | **大batch编译**: ↑ | Torch编译最大batch size |
| `--disable-piecewise-cuda-graph` | False | **保持False** | 分段CUDA Graph，进一步优化执行 |
| `--piecewise-cuda-graph-max-tokens` | 自动(=chunked_prefill_size) | **大token**: ↑ | 分段CUDA Graph最大token数 |
| `--num-continuous-decode-steps` | 1 | **多步decode**: >1 | 连续decode步数。>1可减少调度开销但增加延迟 |

**CUDA Graph batch size 自动生成策略**（源码：`server_args.py:1305-1327`）：
- 根据cuda_graph_max_bs自动生成 [1, 2, 4, 8, ...] 序列
- 大GPU (>60GB): 默认max_bs可达256-512
- 小GPU (<20GB): 默认max_bs仅8

---

### 5. 注意力后端

**核心源码**：`python/sglang/srt/server_args.py:130-156、476-492`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--attention-backend` | 自动 | **NVIDIA GPU**: flashinfer/fa3; **AMD**: aiter | 注意力计算后端 |
| `--decode-attention-backend` | 自动 | **单独设置decode后端**: 按需 | Decode阶段的注意力后端 |
| `--prefill-attention-backend` | 自动 | **单独设置prefill后端**: 按需 | Prefill阶段的注意力后端 |
| `--triton-attention-num-kv-splits` | 8 | **长序列**: ↑ | Triton注意力KV分片数 |
| `--disable-flashinfer-autotune` | False | **稳定性**: 开启; **性能**: False | FlashInfer自动调优 |

**可用注意力后端完整列表**（源码：`server_args.py:130-152`）：

| 后端 | 适用场景 |
|------|----------|
| `flashinfer` | NVIDIA GPU推荐，性能最优 |
| `fa3` | FlashAttention 3，H100+ |
| `fa4` | FlashAttention 4，Blackwell |
| `triton` | 通用后端，兼容性好 |
| `cutlass_mla` / `flashmla` | DeepSeek MLA模型 |
| `trtllm_mla` / `trtllm_mha` | TensorRT-LLM优化后端 |
| `aiter` / `wave` | AMD GPU (ROCm) |
| `nsa` | Native Sparse Attention |
| `ascend` | 华为NPU |

---

### 6. 量化与精度

**核心源码**：`python/sglang/srt/server_args.py:328-339、100-126`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--dtype` | auto | **H100/A100**: bfloat16; **其他**: float16 | 模型权重数据类型 |
| `--quantization` | None | **4bit**: awq/gptq; **8bit**: fp8/w8a8_fp8 | 模型量化方法 |
| `--kv-cache-dtype` | auto | **fp8_e4m3**: 显存减半 | KV缓存量化 |
| `--fp8-gemm-runner-backend` | auto | **性能优先**: deep_gemm | FP8 GEMM计算后端 |
| `--fp4-gemm-runner-backend` | auto | **Blackwell**: cutlass | FP4 GEMM计算后端（Blackwell架构） |

**支持的量化方法完整列表**（源码：`server_args.py:100-126`）：

| 量化方法 | 说明 |
|----------|------|
| `awq` | 激活感知权重量化 |
| `fp8` | 8位浮点量化 |
| `gptq` / `gptq_marlin` | GPT量化/Marlin后端 |
| `awq_marlin` | AWQ + Marlin后端 |
| `bitsandbytes` | BitsAndBytes量化 |
| `modelopt` / `modelopt_fp8` / `modelopt_fp4` | NVIDIA ModelOpt系列 |
| `w8a8_int8` / `w8a8_fp8` | 8位权重/激活量化 |
| `mxfp4` / `mxfp8` | MX格式量化 |
| `gguf` | GGUF格式 |
| `marlin` | Marlin量化 |
| `compressed-tensors` | 压缩张量格式 |

**量化相关环境变量**（源码：`python/sglang/srt/environ.py:342-351`）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_INT4_WEIGHT` | False | 启用INT4权重 |
| `SGLANG_USE_DYNAMIC_MXFP4_LINEAR` | False | 动态MXFP4线性层 |
| `SGLANG_FORCE_FP8_MARLIN` | False | 强制FP8使用Marlin |
| `SGLANG_MOE_NVFP4_DISPATCH` | False | MoE NVFP4分发 |
| `SGLANG_QUANT_ALLOW_DOWNCASTING` | False | 允许量化精度降级 |

---

### 7. 推测解码

**核心源码**：`python/sglang/srt/server_args.py:494-522、3099-3195`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--speculative-algorithm` | None | **轻量**: EAGLE/NGRAM; **高加速**: EAGLE | 推测解码算法选择 |
| `--speculative-draft-model-path` | None | **设置draft模型**: 路径 | Draft模型路径 |
| `--speculative-num-steps` | 自动 | **EAGLE**: 1-5 | 每步推测步数 |
| `--speculative-eagle-topk` | 自动 | **速度**: 4-8; **精度**: 1-2 | EAGLE算法的top-k值 |
| `--speculative-num-draft-tokens` | 自动 | **保守**: 3-5; **激进**: 8-16 | 每步推测的draft token数 |
| `--speculative-accept-threshold-single` | 1.0 | **更宽松**: <1.0 | 单token接受阈值 |
| `--speculative-accept-threshold-acc` | 1.0 | **更宽松**: <1.0 | 累积接受阈值 |
| `--enable-multi-layer-eagle` | False | **EAGLE3**: 开启 | 多层EAGLE推测 |

**可用推测算法**：
- `EAGLE` / `NEXTN` - 基于模型头的推测（推荐）
- `NGRAM` - 基于N-gram的推测（无额外模型）
- `DFLASH` - Draft Flash推测
- `STANDALONE` - 独立draft模型推测

---

### 8. 重叠调度与PD分离

**核心源码**：`python/sglang/srt/server_args.py:629-636、696-704`、`python/sglang/srt/batch_overlap/`

| 参数 | 默认值 | 调优方向 | 影响分析 |
|------|--------|----------|----------|
| `--disable-overlap-schedule` | False | **保持False(默认启用)** | 重叠调度是SGLang的关键优化，将GPU计算与CPU调度重叠 |
| `--enable-two-batch-overlap` | False | **高吞吐**: 开启 | 两批次重叠(TBO)，进一步重叠prefill和decode |
| `--enable-single-batch-overlap` | False | **实验性**: 开启 | 单批次重叠 |
| `--tbo-token-distribution-threshold` | 0.48 | **调整TBO行为**: 0.3-0.6 | TBO的token分布阈值 |
| `--enable-mixed-chunk` | False | **推荐与overlap一起开启** | 混合chunk，允许同一batch中混合prefill/decode |
| `--disaggregation-mode` | null | **PD分离**: "prefill"/"decode" | PD分离模式。prefill=仅处理prefill，decode=仅处理decode |
| `--disaggregation-transfer-backend` | mooncake | **高性能**: nixl; **通用**: mooncake | PD分离KV传输后端 |
| `--num-reserved-decode-tokens` | 512 | **PD分离调优**: 按需调整 | Decode KV缓存预留token数 |
| `--enable-lmcache` | False | **外部KV缓存**: 开启 | 启用LMCache外部KV缓存 |

**PD分离相关环境变量**（源码：`environ.py:236-267`）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` | 自动 | PD分离线程池大小 |
| `SGLANG_DISAGGREGATION_QUEUE_SIZE` | 4 | PD分离队列大小 |
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | 300 | 引导超时(秒) |
| `SGLANG_EMPTY_CACHE_INTERVAL` | -1 | 清空缓存间隔(秒)，解决长时运行内存累积 |

---

## 三、场景化调优策略

### 场景 A：最大吞吐（Batch推理、离线处理）

```bash
python -m sglang.launch_server \
  --model-path <model> \
  --mem-fraction-static 0.92 \
  --chunked-prefill-size 8192 \
  --max-running-requests 1024 \
  --max-prefill-tokens 16384 \
  --schedule-conservativeness 0.7 \
  --enable-mixed-chunk \
  --kv-cache-dtype fp8_e4m3 \
  --tp-size 8 \
  --dp-size 2 \
  --cuda-graph-max-bs 512 \
  --moe-runner-backend deep_gemm \
  --disable-custom-all-reduce False
```

### 场景 B：最低延迟（在线对话、实时交互）

```bash
python -m sglang.launch_server \
  --model-path <model> \
  --chunked-prefill-size 4096 \
  --max-running-requests 128 \
  --schedule-conservativeness 1.3 \
  --enable-mixed-chunk \
  --speculative-algorithm EAGLE \
  --speculative-eagle-topk 4 \
  --speculative-num-steps 3 \
  --enable-prefill-delayer
```

### 场景 C：长上下文（RAG、文档处理）

```bash
python -m sglang.launch_server \
  --model-path <model> \
  --context-length 128000 \
  --enable-hierarchical-cache \
  --hicache-ratio 3.0 \
  --cpu-offload-gb 16 \
  --chunked-prefill-size 4096 \
  --kv-cache-dtype fp8_e4m3 \
  --radix-eviction-policy lfu \
  --enable-prefill-context-parallel
```

### 场景 D：PD分离部署（大规模在线服务）

```bash
# Prefill节点
python -m sglang.launch_server \
  --model-path <model> \
  --disaggregation-mode prefill \
  --disaggregation-transfer-backend nixl \
  --chunked-prefill-size 16384 \
  --mem-fraction-static 0.95

# Decode节点
python -m sglang.launch_server \
  --model-path <model> \
  --disaggregation-mode decode \
  --disaggregation-transfer-backend nixl \
  --speculative-algorithm EAGLE \
  --speculative-eagle-topk 4 \
  --num-continuous-decode-steps 2
```

### 场景 E：MoE模型（DeepSeek-V3等）

```bash
python -m sglang.launch_server \
  --model-path <model> \
  --tp-size 8 \
  --ep-size 8 \
  --moe-a2a-backend deepep \
  --deepep-mode low_latency \
  --moe-runner-backend deep_gemm \
  --enable-flashinfer-allreduce-fusion \
  --attention-backend flashinfer \
  --fp8-gemm-runner-backend deep_gemm \
  --enable-eplb
```

### 场景 F：显存受限环境

```bash
python -m sglang.launch_server \
  --model-path <model> \
  --mem-fraction-static 0.82 \
  --quantization awq \
  --kv-cache-dtype fp8_e4m3 \
  --chunked-prefill-size 2048 \
  --max-running-requests 64 \
  --cuda-graph-max-bs 24 \
  --cpu-offload-gb 4
```

---

## 四、参数优先级与影响矩阵

```
影响程度 (★数量 = 对推理速度的影响程度)

★★★★★ 关键参数（必须调优）
  ┌─ mem-fraction-static          ─ 直接决定KV缓存大小 → 并发能力
  ├─ disable-cuda-graph           ─ CUDA Graph影响巨大(2-3x)，必须False
  ├─ chunked-prefill-size         ─ 吞吐-延迟的核心旋钮
  └─ disable-overlap-schedule     ─ 重叠调度是SGLang核心优化

★★★★  重要参数（显著影响）
  ┌─ tp-size / dp-size            ─ 多卡并行与数据并行
  ├─ kv-cache-dtype (fp8)         ─ 显存翻倍→并发翻倍
  ├─ max-running-requests         ─ 并发上限
  ├─ quantization                 ─ 量化节省显存带宽
  └─ attention-backend             ─ 注意力核函数选择

★★★  有益参数（锦上添花）
  ┌─ schedule-conservativeness    ─ 调度保守度
  ├─ speculative decoding         ─ 单请求延迟优化(1.5-4x)
  ├─ enable-mixed-chunk           ─ 混合批处理
  ├─ enable-two-batch-overlap     ─ 双批次重叠
  ├─ moe-runner-backend           ─ MoE计算后端
  └─ enable-hierarchical-cache    ─ 层级缓存(长上下文)

★★   场景参数（特定场景有效）
  ┌─ cpu-offload-gb               ─ 长上下文/显存受限
  ├─ ep-size / moe-a2a-backend    ─ MoE模型专用
  ├─ PD disaggregation            ─ 大规模分离部署
  ├─ enable-prefill-delayer       ─ 高并发降低decode延迟
  ├─ deepep-mode                  ─ DeepEP低延迟模式
  └─ radix-eviction-policy        ─ 缓存淘汰策略

★    微调参数（影响有限）
  ┌─ page-size                    ─ 内存管理粒度
  ├─ scheduler-recv-interval      ─ 调度接收间隔
  ├─ num-continuous-decode-steps  ─ 连续decode步数
  └─ triton-attention-num-kv-splits ─ Triton注意力分片数
```

---

## 五、环境变量调优

**核心源码**：`python/sglang/srt/environ.py`

### 调度器相关环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_INIT_NEW_TOKEN_RATIO` | 0.7 | 初始新token比例估计 |
| `SGLANG_MIN_NEW_TOKEN_RATIO_FACTOR` | 0.14 | 最小新token比例因子 |
| `SGLANG_NEW_TOKEN_RATIO_DECAY_STEPS` | 600 | 新token比例衰减步数 |
| `SGLANG_RETRACT_DECODE_STEPS` | 20 | 收回decode步数 |
| `SGLANG_EMPTY_CACHE_INTERVAL` | -1 | 清空缓存间隔(秒)，解决长时运行内存累积 |
| `SGLANG_SCHEDULER_MAX_RECV_PER_POLL` | -1 | 每次轮询最大接收请求数 |
| `SGLANG_DYNAMIC_CHUNKING_SMOOTH_FACTOR` | 0.75 | 动态chunking平滑因子 |

### 核函数相关环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_ENABLE_JIT_DEEPGEMM` | True | JIT编译DeepGemm核 |
| `SGLANG_JIT_DEEPGEMM_PRECOMPILE` | True | DeepGemm预编译 |
| `SGLANG_JIT_DEEPGEMM_COMPILE_WORKERS` | 4 | DeepGemm编译worker数 |
| `SGLANG_USE_SGL_FA3_KERNEL` | True | 使用SGL FlashAttention 3核 |
| `SGLANG_FLASHINFER_WORKSPACE_SIZE` | 384MB | FlashInfer工作空间大小 |
| `SGLANG_ENABLE_TORCH_COMPILE` | False | Torch编译开关 |

### PD分离相关环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` | 自动 | PD分离线程池大小 |
| `SGLANG_DISAGGREGATION_QUEUE_SIZE` | 4 | PD分离队列大小 |
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | 300 | 引导超时(秒) |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 5.0 | 心跳间隔 |
| `SGLANG_DISAGGREGATION_NUM_PRE_ALLOCATE_REQS` | 0 | Decode节点预分配请求数 |

### DeepEP/MoE相关环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_DEEPEP_NUM_MAX_DISPATCH_TOKENS_PER_RANK` | 128 | DeepEP每rank最大分发token数 |
| `SGLANG_DEEPEP_LL_COMBINE_SEND_NUM_SMS` | 32 | DeepEP低延迟模式SMS数 |

### 其他性能相关环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SGLANG_WARMUP_TIMEOUT` | -1 | 预热超时(秒)，DeepGemm建议≥1800 |
| `SGLANG_USE_AITER` | False | AMD Aiter优化 |
| `SGLANG_CHUNKED_PREFIX_CACHE_THRESHOLD` | 8192 | 分块前缀缓存阈值 |
| `SGLANG_ENABLE_BREAKABLE_CUDA_GRAPH` | False | 可中断CUDA Graph |
| `SGLANG_NUMA_BIND_V2` | True | NUMA绑定v2 |
| `SGLANG_AUTO_NUMA_BIND` | False | 自动NUMA绑定 |
| `SGLANG_ENABLE_DETERMINISTIC_INFERENCE` | False | 确定性推理 |
| `SGLANG_SET_CPU_AFFINITY` | False | CPU亲和性设置 |

---

## 六、SGLang vs vLLM 关键差异

| 维度 | SGLang | vLLM |
|------|--------|------|
| **核心缓存** | Radix Cache（树形前缀匹配） | Block Manager（块级前缀匹配） |
| **重叠调度** | 默认启用 overlap schedule | async_scheduling |
| **分段CUDA Graph** | piecewise cuda graph | cudagraph capture sizes |
| **PD分离** | disaggregation-mode | kv-role/kv-connector |
| **推测解码** | EAGLE/NGRAM/DFLASH/STANDALONE | ngram/eagle/medusa/draft_model |
| **全局性能模式** | 无(手动调参) | performance-mode (throughput/interactivity) |
| **调度保守度** | schedule-conservativeness | 无直接对应 |
| **层级缓存** | HiCache (SSD/Host内存) | kv-offloading-size (CPU) |
| **MoE通信** | deepep/mooncake/nixl/flashinfer | all2all-backend |
| **动态chunking** | enable-dynamic-chunking | max-num-partial-prefills |
| **两批次重叠** | enable-two-batch-overlap | 无直接对应 |

---

## 七、关键源码文件索引

| 维度 | 核心文件 | 说明 |
|------|----------|------|
| 全局入口 | `python/sglang/launch_server.py` | 服务启动入口 |
| 服务参数 | `python/sglang/srt/server_args.py` | ServerArgs 定义，所有CLI参数(5000+行) |
| 环境变量 | `python/sglang/srt/environ.py` | 所有SGLANG_*环境变量 |
| 全局配置 | `python/sglang/global_config.py` | 全局常量配置 |
| 调度器 | `python/sglang/srt/managers/scheduler.py` | 调度器核心逻辑 |
| 调度策略 | `python/sglang/srt/managers/schedule_policy.py` | FCFS/Priority策略 |
| 调度批次 | `python/sglang/srt/managers/schedule_batch.py` | 批次管理 |
| Prefill延迟 | `python/sglang/srt/managers/prefill_delayer.py` | Prefill延迟控制器 |
| 批次重叠 | `python/sglang/srt/batch_overlap/` | TBO/SBO重叠调度 |
| 内存池 | `python/sglang/srt/mem_cache/memory_pool.py` | GPU内存池 |
| Radix Cache | `python/sglang/srt/mem_cache/radix_cache.py` | Radix Cache实现 |
| 层级缓存 | `python/sglang/srt/mem_cache/hiradix_cache.py` | HiCache实现 |
| 模型执行 | `python/sglang/srt/model_executor/` | 模型执行器 |
| 推测解码 | `python/sglang/srt/speculative/` | 推测解码实现 |
| 注意力层 | `python/sglang/srt/layers/attention/` | 注意力后端 |
| 量化层 | `python/sglang/srt/layers/quantization/` | 量化方法 |
| 离 disaggregation | `python/sglang/srt/disaggregation/` | PD分离实现 |
| 编译优化 | `python/sglang/srt/compilation/` | Torch编译/CUDA Graph |

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
│ • tp-size / dp-size            │
│ • mem-fraction-static          │
│ • dtype + quantization         │
│ • attention-backend            │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 3: KV缓存优化             │
│ • kv-cache-dtype (fp8?)        │
│ • 保持 radix-cache 开启        │
│ • radix-eviction-policy        │
│ • cpu-offload-gb               │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 4: 调度优化               │
│ • chunked-prefill-size         │
│ • max-running-requests         │
│ • schedule-conservativeness    │
│ • enable-mixed-chunk           │
│ • enable-prefill-delayer       │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 5: CUDA Graph与编译       │
│ • disable-cuda-graph = False   │
│ • cuda-graph-max-bs            │
│ • piecewise-cuda-graph         │
│ • fp8-gemm-runner-backend      │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 6: 重叠调度(默认启用)      │
│ • disable-overlap-schedule=False│
│ • enable-two-batch-overlap     │
│ • enable-mixed-chunk           │
│ • num-continuous-decode-steps  │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 7: 高级优化（可选）        │
│ • speculative decoding         │
│ • PD disaggregation            │
│ • enable-hierarchical-cache    │
│ • MoE后端优化(deep_gemm/deepep)│
│ • 环境变量微调                  │
└──────────┬────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Step 8: 基准测试验证           │
│ • 测试吞吐量 (tokens/s)        │
│ • 测试延迟 (TTFT, ITL)         │
│ • 监控显存使用                  │
│ • 迭代调整                     │
└───────────────────────────────┘
```

---

## 九、总结

SGLang 推理速度优化遵循 **"显存→调度→图优化→重叠→高级"** 的调优路径：

1. **显存优化**：通过 `mem-fraction-static` + `kv-cache-dtype` + `quantization` 最大化可用显存
2. **调度优化**：通过 `chunked-prefill-size` + `max-running-requests` + `schedule-conservativeness` 优化批处理
3. **图优化**：保持 CUDA Graph 启用(`disable-cuda-graph=False`)，合理设置 `cuda-graph-max-bs`
4. **重叠调度**：保持 overlap schedule 启用(默认)，可选启用 TBO(`enable-two-batch-overlap`)
5. **高级优化**：推测解码、PD分离、层级缓存、MoE后端选择等场景化优化

**SGLang 独有优势**：
- **Radix Cache**：比传统块级前缀缓存更高效的树形匹配，RAG场景效果显著
- **Overlap Schedule**：GPU计算与CPU调度自动重叠，减少空闲时间
- **Piecewise CUDA Graph**：分段CUDA Graph，更灵活的图优化
- **DeepGemm + DeepEP**：针对FP8/MoE的高性能核函数

> **关键提示**：调优应基于实际工作负载进行基准测试验证。SGLang提供了 `python -m sglang.bench_serving` 等基准测试工具。
