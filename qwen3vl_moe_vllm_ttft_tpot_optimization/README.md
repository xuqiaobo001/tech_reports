# Qwen3-VL-MoE 35B A3B (w8a8) 在 vLLM-Ascend 上的 TTFT/TPOT 优化报告

> 场景：用 vLLM 部署 `Eco-Tech/Qwen3.6-35B-A3B-w8a8`（Qwen3-VL MoE，总参 ~35B、激活 ~3B，w8a8 量化），做图片理解。
> 输入：1569 字符系统提示词（作为所有请求的**公共前缀**）+ 一张 4K 图片。
> 目标：优化推理的 **TTFT（首 token 延迟）** 与 **TPOT（单 token 生成延迟）**。

---

## 0. 环境与代码事实核对

| 项 | 结论 |
|---|---|
| vLLM 版本 | `v0.22.1rc0+`（V1 引擎，较新） |
| 模型架构 | 注册表中 `Qwen3VLMoeForConditionalGeneration` → 对应「Qwen3 VL MoE 35B A3B」；MoE、激活参数仅 ~3B |
| 投机解码可用性 | 注册表存在 `Eagle3Qwen3vlForCausalLM`、`Qwen3_5MoeMTP` → EAGLE3 / MTP 草稿头可开（VL-MoE 实际仅 EAGLE3/ngram 可行，见 §5） |
| w8a8 | 走 `--quantization ascend` 的量化 GEMM/算子路径 |
| 部署目标 | **昇腾（vLLM-Ascend + torch_npu 插件）**，NPU 平台由 `vllm_ascend` 插件注册 |
| Mooncake | 仓库携带 Mooncake → 可做 P/D 分离 / KV 传输 |

**场景本质：TTFT 的瓶颈在「4K 图片」，不在系统提示词。**
- 1569 字符系统提示词 ≈ 400–600 token。
- 一张 4K 图（3840×2160 ≈ 8.29M 像素）若不限制，视觉 token 数 ≈ `pixels / (patch_size² × merge_size²)`（Qwen3-VL 若 patch=16/merge=2 即 `/1024`）→ 约 **~8100 个视觉 token**。
- 因此「系统提示词是公共前缀」判断正确，但它对 TTFT 的帮助是次要的；**真正的大头是视觉编码 + 视觉 token 的 prefill**。

---

## 1. TTFT 优化（prefill 主导，按影响排序）

### 1.1 降低视觉 token 数 —— 单点最大杠杆（源码已验证 key）

4K 图全分辨率 ≈ 8100 token。多数图文任务 2–4M 像素已足够，先确认业务是否真需原生 4K：

```bash
--mm-processor-kwargs '{"max_pixels":2007040,"min_pixels":1003520}'
# 约 2M 像素 → ~1960 视觉 token（vs 4K ~8100）；需更高细节用 4014080(~3920 token)
```

> 源码位置：`vllm/model_executor/models/qwen3_vl.py:910-931` 直接读取 `min_pixels`/`max_pixels`。
> 取值公式：`vision_tokens ≈ max_pixels / (patch_size² × merge_size²)`，按 checkpoint 的 `vision_config.patch_size` 实算。

### 1.2 调大 `--max-num-batched-tokens`（默认 2048，偏小）

chunked prefill 下，长 prefill 被切成 ≤ 该值的块、每块一次迭代。4K 图数千 token + 默认 2048 → 要好几轮迭代才能出首 token。调到 **8192–16384** 让整段 prefill 尽量 1–2 块跑完，直接降 TTFT。代价是 prefill 峰值激活显存变大，需平衡。

### 1.3 系统提示词走前缀缓存（默认已开，勿关）

V1 默认 `enable_prefix_caching=True`。确保系统提示词在 prompt 最前、逐字节一致（Qwen3-VL 的 system message 本就在最前），首请求后后续请求复用这段 ~400–600 token 的 KV。收益真实但相对图片是小头。

- 顺带 `--mm-processor-cache-gb`（默认 4）：若同一张图复用，缓存已处理好的图像输入，省重跑 image processor。

### 1.4 视觉编码器加速

ViT 编码是 TTFT 重要组成：

```bash
-cc.compile_mm_encoder=true     # 编译/融合 ViT（昇腾→graph/算子融合）
--mm-encoder-tp-mode weights    # 默认即切权重跨卡并行编码，单大图保持此值
```

不要开 `--skip-mm-profiling`（默认 False），让它正确估算编码器峰值显存。

### 1.5 `--mm-tensor-ipc torch_shm`（默认 direct_rpc）

4K 图是大 tensor，`direct_rpc` 要 msgspec 序列化大对象；改 `torch_shm` 走零拷贝共享内存，省 API 进程→engine 的拷贝，降 TTFT。

### 1.6 P/D 分离（Mooncake / kv-transfer，高并发在线才值得）

若 QPS 高、prefill 与 decode 抢资源，可把 prefill 卸到专用 worker、KV 经 Mooncake 传给 decode worker，TTFT 与 decode 负载解耦：

```bash
--kv-transfer-config '{"role":"producer",...}'   # 需配套 consumer 实例
```

---

## 2. TPOT 优化（decode 主导，按影响排序）

### 2.1 投机解码（EAGLE3 / MTP）—— TPOT 最大杠杆

本模型注册表有 `Eagle3Qwen3vlForCausalLM` / `Qwen3_5MoeMTP`。decode 时每步出多 token，TPOT 常降 1.5–3×。详见 §5。

### 2.2 保持 Graph 模式（不要 `--enforce-eager`）

V1 默认 `cudagraph_mode=FULL_AND_PIECEWISE`，昇腾映射为 NPU graph 模式。decode 必须走 graph 才有低 TPOT。用 `--max-num-seqs` 和 `-cc.cudagraph-capture-sizes` 覆盖常见 decode 批大小，避免落到未捕获尺寸导致重放开销。

### 2.3 `--performance-mode interactivity`

在线低延迟选 `interactivity`（细粒度 graph、低延迟）；重吞吐/高并发选 `throughput`。

### 2.4 `--max-num-seqs` 适度

MoE 仅 3B 激活，decode 单 token 便宜，可批很多序列；但太大→KV 占满→抢占→反劣化 TPOT。按显存实测取平衡（常见 256–512）。

### 2.5 MoE 通信：专家并行（多卡时）

```bash
--enable-expert-parallel --all2all-backend <按昇腾拓扑选>
```

MoE decode 下 EP 常比纯 TP 更省延迟，取决于 910B/910C 卡间互联。

### 2.6 KV 精度

默认 `--kv-cache-dtype auto`（同 bf16）。若需更多并发可试 fp8 KV，但**昇腾 fp8 KV 必须先验证数值正确性与 CANN 算子支持**，否则保持 bf16。

---

## 3. 通用 / 并发（同时影响 TTFT 与 TPOT）

- `--tensor-parallel-size`：35B w8a8 权重约 35GB，按卡型选（910B 64G 多半 TP=2；910C 可能 TP=1–2），给 KV 和激活留余量。
- `--gpu-memory-utilization 0.90–0.95`：尽量给 KV cache。
- `--max-model-len`：按实际 prompt+输出设，别盲目拉长（长上下文吃 KV、影响并发与 TTFT）。
- `--scheduler-reserve-full-isl`（默认 True）：保持开，避免 chunked prefill 下过度准入引发 KV 抖动。
- `--async-scheduling`：V1 异步调度重叠 CPU 调度与 GPU 计算，降空闲；确认昇腾插件支持后开启。
- `-O3`：最佳性能（启动慢），生产可上。

---

## 4. 当前部署配置评估

### 当前命令

```bash
vllm serve Eco-Tech/Qwen3.6-35B-A3B-w8a8 \
  --host 0.0.0.0 --port 8000 \
  --data-parallel-size 1 \
  --tensor-parallel-size 2 \
  --enable-expert-parallel \
  --seed 1024 \
  --quantization ascend \
  --served-model-name qwen3.6 \
  --max-num-seqs 128 \
  --max-model-len 262144 \
  --max-num-batched-tokens 16384 \
  --trust-remote-code \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --additional-config '{"enable_cpu_binding":true, "enable_flashcomm1":true, "multistream_overlap_shared_expert": true}' \
  --async-scheduling
```

### ✅ 已经做对的（保留）

- `--max-num-batched-tokens 16384`：8100+token 图片 prefill 基本能 1–2 块跑完，TTFT 友好。
- `--enable-expert-parallel` + `enable_flashcomm1` + `multistream_overlap_shared_expert`：MoE 通信/共享专家重叠，对 A3B MoE 正确。
- `cudagraph_mode:FULL_DECODE_ONLY`：多模态场景正确选择——decode 固定 shape 走全图，prefill 变长（图尺寸不一）走分段。
- `--async-scheduling` / `--enable-prefix-caching` / `enable_cpu_binding`。
- `--disable-chunked-mm-input` 未设（=False）：允许超大图按需分块。

### 🔴 仍未做的最大杠杆（建议立即补）

| 项 | 建议 | 影响 |
|---|---|---|
| `--mm-processor-kwargs '{"max_pixels":2007040}'` | 限制视觉 token | **TTFT 单点最大杠杆**，零成本 |
| 投机解码（EAGLE3/ngram） | 见 §5 | **TPOT 最大杠杆** |
| `-cc.compile_mm_encoder=true` + `--mm-tensor-ipc torch_shm` | 编码器加速 + 零拷贝图传 | TTFT |

### 🟡 调优项

| 项 | 建议 | 原因 |
|---|---|---|
| `--max-model-len 262144` | 降到 32768–65536 | 4K 图文任务实际 ~10K，256K 远超需求；过度放大会让单请求合法膨胀挤占 KV、影响 profiling |
| `--max-num-seqs 128` | 看实测调 | 每请求 ~8100 token ≈ 506 KV 块，128 并发很压 KV 池；限 token 后更稳，若 `vllm:num_preempted`>0 则下调或提 mem-util |
| `--performance-mode interactivity` | 加上 | 在线低延迟，细粒度 graph |
| `-O3` | 加上 | 最佳性能 |

### 🟢 验证 / 可选

- 确认 3 个 `additional-config` 昇腾开关（`enable_cpu_binding`/`enable_flashcomm1`/`multistream_overlap_shared_expert`）**真生效**：它们由 vllm-ascend 插件注入，key 写错会被静默忽略。看启动日志回显。
- `--all2all-backend`：**保持默认 `allgather_reducescatter`**。源码 `flashinfer_nvlink_*`/`deepep_*` 都是 CUDA 专用，昇腾用不上。
- `--kv-cache-dtype fp8`：w8a8 下可试增并发，但先验证正确性。
- `--mm-processor-cache-gb`（默认 4）：同图复用时加大。
- `-cc.cudagraph-capture-sizes`：日志出现 "cudagraph not captured for size" 警告时再 pin。

---

## 5. 投机解码配置

### 5.1 三条路：内置 MTP 对 VL-MoE 走不通

| 方法 | 对 VL-MoE 是否可用 | 说明 |
|---|---|---|
| **内置 MTP**（`method:"mtp"`） | ❌ 不可用 | `qwen3_vl_moe.py`/`qwen3_vl.py` 无 MTP 头实现，checkpoint 无对应权重 |
| **ngram（prompt lookup）** | ✅ 立即可用、零依赖 | 不需草稿模型；但对图文问答（输出几乎不复读提示词）**接受率通常很低** |
| **EAGLE3（外部头）** | ✅ 支持（注册表有 `Eagle3Qwen3vlForCausalLM`） | **TPOT 收益最大**，但需针对该模型训练好的 eagle3 head |

兼容性确认（现有配置都不用改）：
- ✅ `--async-scheduling` + 投机解码**可共存**（`v1/worker/gpu_input_batch.py:59`、`gpu_model_runner.py:632`）
- ✅ `--enable-expert-parallel` + 投机解码**无互斥**
- ✅ `FULL_DECODE_ONLY` 可保留（仅 deepseek_v32 的 MTP 强制 eager，Qwen 不受影响）
- ⚠️ 开投机解码后**不能用**：自定义 logits processor、`lm_format_enforcer` 引导解码

### 5.2 路径 A：ngram（5 秒可试，先做基线）

```bash
--speculative-config '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4,"prompt_lookup_min":2}'
```

### 5.3 路径 B：EAGLE3（TPOT 最大收益，前提是有头）

```bash
--speculative-config '{"method":"eagle3","model":"/path/to/Eagle3-Qwen3VL-head","num_speculative_tokens":3}'
```

关键字段（`vllm/config/speculative.py`）：

| 字段 | 取值/说明 |
|---|---|
| `method` | `"eagle3"`；head 的 config 若是标准 `EAGLEConfig`（`model_type="eagle"`）可省略自动识别 |
| `model` | 训练好的 eagle3 head 路径/名。head 可保持 bf16（目标模型是 w8a8 也不影响，draft `quantization` 默认 None） |
| `num_speculative_tokens` | 每步投机 token 数，**必填**。从 `3` 起，接受率高再提 4–5 |
| `draft_tensor_parallel_size` | 仅能 `1` 或等于目标(=2)；head 小时设 `1` 通信更少，不确定就省略（继承 tp=2） |
| `parallel_drafting` | `true` 可并行起草所有投机 token（需 head 训练支持） |
| `disable_padded_drafter_batch` | 允许不等长投机批，仅 EAGLE 受影响 |

> **现实前提**：EAGLE3 head 必须**针对该具体模型单独训练**。没有 head 则此路不通，建议先训一个，或退回 ngram。

### 5.4 调参与验收

1. `num_speculative_tokens=3` 起步，跑实际图文流量。
2. 看接受率指标：`/metrics` 中的 `vllm:spec_decode_num_accepted_tokens_per_pos`。
   - 平均接受 ≥2–3 → TPOT 明显下降，可提 4–5。
   - 接受 ≈0 → 方法不匹配（ngram 自由生成场景常见），EAGLE3 才是正解。
3. 投机解码增加每步算力开销，**接受率低时反而拖慢 TPOT**，务必看指标取舍。

---

## 6. 推荐启动命令（昇腾示意）

```bash
vllm serve Eco-Tech/Qwen3.6-35B-A3B-w8a8 \
  --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size 2 \
  --enable-expert-parallel \
  --quantization ascend \
  --served-model-name qwen3.6 \
  --max-num-seqs 128 \
  --max-model-len 65536 \
  --max-num-batched-tokens 16384 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching \
  --async-scheduling \
  --performance-mode interactivity \
  -O3 \
  --mm-processor-kwargs '{"max_pixels":2007040,"min_pixels":1003520}' \
  --mm-tensor-ipc torch_shm \
  -cc.compile_mm_encoder=true \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --additional-config '{"enable_cpu_binding":true,"enable_flashcomm1":true,"multistream_overlap_shared_expert":true}' \
  --trust-remote-code
# （TPOT 进一步加）--speculative-config '{"method":"eagle3","model":"<draft-head>","num_speculative_tokens":3}'
```

---

## 7. 分层优化策略总结

| 指标 | 主杠杆 | 次要项 |
|---|---|---|
| **TTFT** | 限制 `max_pixels` 视觉 token | `max_num_batched_tokens`、`compile_mm_encoder`、`mm-tensor-ipc torch_shm`、prefix caching |
| **TPOT** | 投机解码（EAGLE3） | graph 模式（勿 eager）、`performance-mode interactivity`、EP/all2all、`max_num_seqs` |

**核心要点**：投机解码只优化 TPOT、不碰 TTFT（图片 prefill）。两者需分层叠加：
- TTFT → `--mm-processor-kwargs '{"max_pixels":...}'`
- TPOT → `--speculative-config '{...}'`

---

## 8. 昇腾（Ascend）验证清单

以下在 CUDA 上的语义，在 NPU 上随 CANN / torch_npu 版本变化，**以本机 `vllm serve --help` 与实际 benchmark 为准**：

- w8a8 与投机解码的兼容性、接受率
- fp8 KV 的数值正确性
- graph 模式（`-cc.cudagraph_*`）在 CANN 上的实际行为
- `async-scheduling` 是否被插件支持
- 3 个 `additional-config` 开关是否真生效（看启动日志）
