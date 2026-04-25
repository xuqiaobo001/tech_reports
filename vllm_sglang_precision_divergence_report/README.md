# vLLM vs SGLang 加载同一模型的精度差异：源码级根因分析

> 分析日期：2026-04-21
> 源码路径：`/root/vllm_ascend/`（含 vllm 与 sglang 两个项目）

---

## 核心结论

即使加载**完全相同的模型权重**、使用相同的 `dtype`（如 BF16），vLLM 和 SGLang 也会在多个计算环节产生浮点数差异。**根本原因是两者在以下 9 个维度上使用了不同的实现路径**，而浮点运算不满足结合律/分配律，任何微小的计算顺序或中间精度差异都会逐层累积、放大。

---

## 一、Attention 实现（影响最大）

### vLLM：多后端自动选择

```
vllm/vllm/v1/attention/backends/
├── flash_attn.py        ← FlashAttention 2
├── flashinfer.py        ← FlashInfer
├── triton_attn.py       ← Triton 自定义 kernel
├── rocm_attn.py         ← ROCm
└── flex_attention.py    ← PyTorch flex
```

vLLM 通过 `selector.py` 在运行时自动选择后端。**不同后端的 softmax 归约顺序不同**：
- `triton_attn.py:50`：使用 `NUM_PAR_SOFTMAX_SEGMENTS = 16` 做并行分段 softmax，分段归约的精度与全量归约不同
- `flashinfer.py`：使用 FlashInfer 库的 tile-based softmax
- `flash_attn.py`：使用 FlashAttention 2 的 online softmax

### SGLang：同样多后端

```
sglang/python/sglang/srt/layers/attention/
├── flashinfer_backend.py   ← FlashInfer（默认）
├── triton_backend.py       ← Triton 自定义
└── torch_native_backend.py ← PyTorch SDPA
```

**关键差异**：

| 差异点 | vLLM | SGLang |
|--------|------|--------|
| 默认后端 | FlashAttention 2 | FlashInfer |
| softmax 归约精度 | FP32 累加器 | FP32 累加器，但 tile 大小可能不同 |
| FP8 KV cache | 支持 E4M3/E5M2 | 支持，含 FNUZ 格式（`k_scale *= 2`） |
| 分段 softmax | Triton: 16 segments | Triton: 动态 split tile size |

**精度影响**：即使都用 FlashInfer，两个框架集成时的 kernel 参数（tile size、split-k 策略）可能不同，导致 softmax 结果在最后几位 bit 产生差异。

---

## 二、Temperature / Sampling（直接影响 token 选择）

### vLLM：FP32 softmax

`vllm/v1/sample/sampler.py:226`：
```python
return logits.div_(temp.unsqueeze(dim=1))  # 原始 dtype（BF16）
```

`vllm/v1/sample/ops/topk_topp_sampler.py:112`：
```python
probs = logits.softmax(dim=-1, dtype=torch.float32)  # ← 强制 FP32 softmax
```

**vLLM 统一在 FP32 下做 softmax/采样**，即使 logits 是 BF16。

### SGLang：混合精度采样

`sglang/srt/layers/sampler.py:155-158`：
```python
logits.div_(sampling_info.temperatures)       # BF16 in-place 除法
logits[:] = torch.softmax(logits, dim=-1)     # ← BF16 softmax！
probs = logits
```

**这是最关键的差异之一**：SGLang 的标准采样路径在 **原始 dtype（通常是 BF16）** 下做 softmax，而 vLLM 统一上转到 **FP32**。

但在 RL on-policy 模式下（`sampler.py:127-128`），SGLang 甚至做了更激进的截断：
```python
logits_div_temperature = logits.bfloat16().div(temperatures).bfloat16()
```

**精度影响**：BF16 只有 7 位有效数字，FP32 有 23 位。softmax 中的指数运算对精度非常敏感，BF16 softmax 与 FP32 softmax 在高温/低温区域都会产生可观的概率分布差异，直接导致不同的 token 被采样。

---

## 三、RoPE（位置编码）

### vLLM：可选 FP32 计算

`vllm/model_executor/layers/rotary_embedding/common.py:130-134`：
```python
enable_fp32_compute: bool = False  # 默认关闭
```

`common.py:196-222`：当 `enable_fp32_compute=True` 时，先将 x/cos/sin 都转为 FP32 再计算。

### SGLang：平台相关精度

`sglang/srt/layers/rotary_embedding/base.py:71-73`：
```python
if not _is_cuda:
    cache = cache.to(dtype)   # 非 CUDA 平台用低精度
# CUDA 上 sin/cos cache 保持在 FP32
```

**精度影响**：
- vLLM 默认在模型 dtype（BF16）下计算 RoPE
- SGLang 在 CUDA 上 sin/cos cache 保持在 FP32
- 两者的 inv_freq 初始化路径不同（CPU vs GPU 张量），初始频率值可能有微小差异
- 多 token 累积后，位置编码的微小差异会被 attention 放大

---

## 四、RMSNorm / LayerNorm

两者都在 RMSNorm 中使用 FP32 计算，但**实现方式不同**：

### vLLM

`vllm/model_executor/layers/layernorm.py` — 标准 PyTorch：
```python
x = x.to(torch.float32)
variance = x_var.pow(2).mean(dim=-1, keepdim=True)
x = x * torch.rsqrt(variance + variance_epsilon)
x = x.to(orig_dtype)
```

同时有 CUDA fused kernel 路径（`forward_cuda`）和 ROCm/XPU 特化路径。

### SGLang

`sglang/srt/layers/layernorm.py` — 使用 sgl_kernel 自定义 kernel：
```python
fused_add_rmsnorm(x, residual, self.weight.data, self.variance_epsilon)
```

**精度影响**：
- PyTorch 原生 `rsqrt` vs 自定义 CUDA kernel 的 `rsqrt` 实现可能有最后一位 bit 差异
- fused_add_rmsnorm 将 residual 加法和 RMSNorm 合并为一个 kernel，改变了计算顺序
- 对于 Gemma 模型，公式不同：`x * (1 + w)` vs `x * w`

---

## 五、Activation（SiLU / SwiGLU / GELU）

### vLLM

`vllm/model_executor/layers/activation.py` — Triton kernel 内部显式 FP32：
```python
gate = tl.load(x_row_ptr + offsets, mask=mask).to(tl.float32)
up = tl.load(x_row_ptr + offsets + d, mask=mask).to(tl.float32)
```

### SGLang

`sglang/srt/layers/activation.py` — 多种实现：
- CUDA: `silu_and_mul` kernel from sgl_kernel
- NPU: `torch_npu.npu_swiglu`
- Native: `F.silu(x[..., :d]) * x[..., d:]`

**精度影响**：不同 kernel 的中间计算精度（是否在 FP32 下做乘法）和融合策略不同。

---

## 六、KV Cache 存储/读取

### FP8 KV Cache 的量化差异

**vLLM** 的量化常量（`vllm/envs.py`）：
```python
Q_SCALE_CONSTANT: int = 200
K_SCALE_CONSTANT: int = 200
V_SCALE_CONSTANT: int = 100
```

**SGLang** 的量化（`sglang/srt/layers/quantization/kv_cache.py`）：
```python
if is_fp8_fnuz():
    k_scale *= 2
    v_scale *= 2
```

即使都使用 FP8 KV cache，两框架的 scale 计算方式不同（per-tensor vs per-token-head、动态 vs 静态），导致量化/反量化后的 KV 值不同。

**精度影响**：KV cache 在每个 decode step 都会被读取，量化误差逐 token 累积。

---

## 七、Matmul / 矩阵乘法

### vLLM

`vllm/v1/worker/gpu_worker.py:122-124`：
```python
precision = envs.VLLM_FLOAT32_MATMUL_PRECISION  # 默认 "highest"
torch.set_float32_matmul_precision(precision)
```

vLLM 可配置 FP32 matmul 精度（`highest`/`high`/`medium`），其中 `medium` 允许使用 BF16 tensor core 近似。

### SGLang

无显式 `set_float32_matmul_precision` 调用，使用 PyTorch 默认值（`highest`）。

**精度影响**：当 vLLM 配置为非 `highest` 时，线性层的矩阵乘法精度会降低。但即使都是 `highest`，两者使用的 GEMM kernel 可能不同（vLLM 的 CUTLASS/Marlin vs SGLang 的 sgl-kernel GEMM）。

---

## 八、Logit 处理

### vLLM：logit softcap

`vllm/model_executor/layers/logits_processor.py:36-72`：
```python
if self.soft_cap is not None:
    logits = logits / self.soft_cap
    logits = torch.tanh(logits)
    logits = logits * self.soft_cap
```

### SGLang

同样支持 logit softcap，但实现位置和 tanh 的计算路径可能不同。

**精度影响**：tanh 是非线性函数，BF16 下的 tanh 精度低于 FP32，softcap 区域内差异更大。

---

## 九、权重加载

### vLLM

`vllm/model_executor/model_loader/` — 支持多种加载器：
- `loader.py`：标准 safetensors 加载
- `bitsandbytes_loader.py`：量化加载，含显式 FP32 转换
- 加载时可自动做 dtype 转换（如 FP32 权重转 BF16）

### SGLang

`sglang/srt/model_loader/loader.py`：
- 不同的加载器实现
- 量化配置应用时机可能不同

**精度影响**：如果模型原始权重是 FP32，两个框架在转换为 BF16 时的舍入策略可能略有不同（round-to-nearest-even 的实现差异）。

---

## 精度差异逐层累积示意

```
Layer 1:
  RoPE:     差异 ~1e-7 (BF16 精度边界)
  Attention: 差异 ~1e-6 (softmax 归约顺序)
  RMSNorm:   差异 ~1e-7 (kernel 实现差异)
  FFN:       差异 ~1e-7 (activation + matmul)
  ─────────────────────────────────
  Layer 1 输出差异: ~1e-6

Layer 2:
  输入已有差异 → RoPE/Attention 放大
  ─────────────────────────────────
  Layer 2 输出差异: ~1e-5

...

Layer 32 (最后一层):
  输出差异: ~1e-3 ~ 1e-2
  Logits 差异足以改变 argmax 结果 → 不同 token！
```

---

## 关键差异优先级排序

| 优先级 | 差异源 | 影响程度 | 原因 |
|--------|--------|----------|------|
| **P0** | **Sampling softmax 精度** | 直接决定 token 选择 | vLLM 强制 FP32，SGLang 用原始 dtype |
| **P0** | **Attention backend** | 每层都影响 | 不同 kernel 的归约顺序和累加精度不同 |
| **P1** | **RoPE 实现** | 逐层累积 | sin/cos 精度和计算路径不同 |
| **P1** | **RMSNorm kernel** | 每层影响 | fused kernel vs PyTorch 原生 |
| **P2** | **Activation kernel** | 中等 | FP32 vs BF16 中间计算 |
| **P2** | **KV cache 量化** | 累积性 | 量化 scale 计算方式不同 |
| **P3** | **Matmul kernel** | 较小 | 不同 GEMM 实现的最后一位 bit |
| **P3** | **权重加载 dtype 转换** | 一次性 | 舍入策略差异 |

---

## 如何缩小/消除差异

| 序号 | 方法 | vLLM 操作 | SGLang 操作 |
|------|------|-----------|-------------|
| 1 | **统一 Attention 后端** | 使用 FlashInfer | 使用 FlashInfer |
| 2 | **统一 Sampling 精度** | 已是 FP32 | 添加 `dtype=torch.float32` 到 softmax |
| 3 | **统一 RoPE 精度** | 启用 `enable_fp32_compute=True` | 确认 CUDA 上 FP32 cache |
| 4 | **统一 RMSNorm 实现** | 使用 PyTorch 原生 | 使用 `forward_native` |
| 5 | **禁用 KV cache 量化** | `--kv-cache-dtype auto` | 不启用 FP8 KV cache |
| 6 | **禁用自定义 kernel** | 使用 debug 模式 | 使用 native 实现 |
| 7 | **固定随机种子** | `--seed 42` | `--seed 42` |

如果上述都统一后仍有差异，则主要来自 GEMM kernel 的最后一位 bit 差异，这是硬件层面的，无法完全消除。

---

## 附录：关键源码位置索引

### vLLM

| 组件 | 文件路径 |
|------|----------|
| Attention 后端选择 | `vllm/v1/attention/selector.py` |
| FlashAttention 后端 | `vllm/v1/attention/backends/flash_attn.py` |
| FlashInfer 后端 | `vllm/v1/attention/backends/flashinfer.py` |
| Triton Attention | `vllm/v1/attention/backends/triton_attn.py` |
| RoPE 实现 | `vllm/model_executor/layers/rotary_embedding/common.py` |
| RMSNorm | `vllm/model_executor/layers/layernorm.py` |
| Activation | `vllm/model_executor/layers/activation.py` |
| Sampling | `vllm/v1/sample/sampler.py` |
| Top-k/Top-p Sampler | `vllm/v1/sample/ops/topk_topp_sampler.py` |
| Logits Processor | `vllm/model_executor/layers/logits_processor.py` |
| KV Cache 量化 | `vllm/model_executor/layers/quantization/kv_cache.py` |
| Matmul 精度设置 | `vllm/v1/worker/gpu_worker.py` |
| 量化常量 | `vllm/envs.py` |

### SGLang

| 组件 | 文件路径 |
|------|----------|
| FlashInfer 后端 | `sglang/srt/layers/attention/flashinfer_backend.py` |
| Triton 后端 | `sglang/srt/layers/attention/triton_backend.py` |
| Torch Native 后端 | `sglang/srt/layers/attention/torch_native_backend.py` |
| RoPE 实现 | `sglang/srt/layers/rotary_embedding/base.py` |
| RMSNorm | `sglang/srt/layers/layernorm.py` |
| Activation | `sglang/srt/layers/activation.py` |
| Sampling | `sglang/srt/layers/sampler.py` |
| KV Cache 量化 | `sglang/srt/layers/quantization/kv_cache.py` |
| FP8 量化 | `sglang/srt/layers/quantization/fp8.py` |
| 权重加载 | `sglang/srt/model_loader/loader.py` |
| 自定义 CUDA Kernels | `sgl-kernel/csrc/` |
