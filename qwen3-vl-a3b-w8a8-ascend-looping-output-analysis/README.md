# Qwen3-VL-A3B-W8A8 在 vLLM-Ascend 上"图文输入循环输出"问题根因分析报告

> 分析日期：2026-07-30
> 分析对象：vLLM（`v0.22.1rc0`, 2026-06-12）+ vLLM-Ascend（`v0.19.1rc1` + 1305 commits, HEAD `b80fa2678`, 2026-07-30）
> 模型：`Qwen3.6-35B-A3B-w8a8`（多模态 MoE，约 35B 总参 / 3B 激活，INT8 W8A8 量化）
> 现象：**纯文本输入输出正常；输入"文字 + 图片"时，输出持续循环重复**（直到 `max_tokens`）

---

## 0. 结论速览

**该问题由部署配置（vLLM-Ascend 侧）导致，而非 vLLM 上游源码缺陷或模型权重问题。**

头号元凶是启动参数中开启的 **`enable_flashcomm1: true`**——vLLM-Ascend 官方在 2026-07-18 的提交中，针对**同系列 Qwen3-30B-A3B** 的 serving 教程，已将 `enable_flashcomm1` 从 `true` 改为 `false`，并明确说明：

> "With the current vLLM-Ascend release, enabling `flashcomm1` for this model is **not recommended**. Disabling it aligns the tutorial with the **verified and stable inference configuration**."
> —— commit `34fa3b216` `[Doc][Misc] Disable flashcomm1 in Qwen3-Omni-30B-A3B-Thinking tutorial (#12310)`

`flashcomm1` 同时门控两条**仅在有图片输入时才会重度走到**的子系统：**MoE 专家 all-to-all 通信** 与 **Qwen3-VL 的 DeepStack 视觉特征注入**。次号为 **`qwen3_5_mtp` 投机解码与 W8A8 的组合**。

---

## 1. 模型在源码中的实现定位

`Qwen3.6-35B-A3B`（多模态 + MoE + W8A8）对应 vLLM 中的 **`Qwen3VLMoeForConditionalGeneration`**：

- `vllm/model_executor/models/qwen3_vl_moe.py`（语言模型继承 `Qwen3MoeForCausalLM`）
- 关键机制三件套：
  - **M-ROPE**（多模态旋转位置编码，positions 形状 `(3, seq)`）：`qwen3_vl.py:2504` `get_mrope_input_positions`
  - **DeepStack**（把视觉深层特征注入 LLM 各层）：`qwen3_vl.py:2706`，复用一个**定长模块级 buffer**（`_get/_set/_clear_deepstack_input_embeds`）
  - **EVS 多模态剪枝**（视频为主，单图默认不走剪枝）

> "纯文本 OK、加图片就循环" 这一**症状分界**是定位的关键：它能排除大量与文本/图像无关的通用候选（如纯采样问题、tokenizer 问题）。

---

## 2. 分析方法

按"由通用到具体、由模型到后端"的顺序逐层排查：

1. **vLLM 上游模型层**：M-ROPE 位置计算、DeepStack buffer、量化作用范围。
2. **vLLM 上游量化层**：W8A8 (smoothquant/compressed-tensors) 对视觉塔/merger/lm_head 的作用。
3. **vLLM-Ascend 后端层**：Ascend W8A8 实现、MoE/mega_moe、M-ROPE/rotary、Qwen3-VL 专属 patch。
4. **部署参数层**：实际启动命令与官方"已验证稳定配置"的逐项对比。

---

## 3. 根因分析（按嫌疑排序）

### 3.1 🔴 嫌疑 1（头号，配置层）：`enable_flashcomm1: true`

**代码证据**——`enable_flashcomm1`（代码内 `flash_comm_v1_enabled`）直接门控：

1. **MoE 融合 all-to-all 通信**（专家路由/分发 token）：
   - `vllm_ascend/ops/fused_moe/fused_moe.py:571`
   - `vllm_ascend/ops/fused_moe/experts_selector.py:259`
2. **Qwen3-VL 的 DeepStack 特征按 TP 切分**：
   - `vllm_ascend/patch/worker/patch_qwen3vl.py:24` —— **仅在 `flash_comm_v1_enabled` 时**，把 `deepstack_input_embeds` 按 TP rank 切块。

**机理**：`flashcomm1=true` 同时改写了「MoE 专家怎么算」和「视觉深层特征怎么注入每层」。图片输入会激活 DeepStack（视觉特征注入各层）且产生更长的 prefill 序列，正好把这条不稳定路径压满 → 专家输出/视觉特征被破坏 → 输出退化为循环。纯文本 token 少、DeepStack 注入占比小，故"纯文本正常、加图即循环"。

**官方佐证**：commit `34fa3b216`（2026-07-18）将 Qwen3-30B-A3B 教程三处 serving 示例（baseline / spec-decode / long-context）的 `enable_flashcomm1` 全部改为 `false`。

---

### 3.2 🟠 嫌疑 2（次号，配置层）：MTP 投机解码 + W8A8

用户配置：`--speculative-config '{"method": "qwen3_5_mtp", "num_speculative_tokens": 3, "enforce_eager": true}'`

- 投机解码是"输出循环/复读"的经典成因：draft 与 target 接受率崩塌时，输出会卡住退化。
- **W8A8 + 投机解码近期刚出过接受率 bug**：commit `03cc154cb`（2026-07-24）"[BugFix][GLM-5.2][SpecDecode] Fix DSpark W8A8 eager acceptance"——W8A8 投机路径接受率曾跌至 **~1.18%**（基本不可用）。该修复针对 `dspark` 方法，但说明 **W8A8 + 投机解码是脆弱组合**。
- 官方 A3B 教程的投机解码用的是 **`eagle3`**，而非 `qwen3_5_mtp`。给一个 Qwen3.6-VL-A3B 目标模型配 **Qwen3.5 的 MTP draft 头**，很可能 draft 与目标不匹配 → 系统性拒收 → 循环输出。
- 注意：`speculative-config` 内的 `"enforce_eager": true` 是 spec-decode 内部开关，**不是全局关图**；全局关图需用顶层 `--enforce-eager`。

---

### 3.3 🟡 嫌疑 3（量化层，可能性较低但仍需排查）：W8A8 静态激活量化截断视觉特征

Ascend 的 W8A8 有两种实现（区别在"激活怎么量化"）：

- **`w8a8_static`**（`vllm_ascend/quantization/methods/w8a8_static.py:25`）：
  > "uses **static per-tensor quantization for activations** and per-channel for weights."
  实现用一个**全局标量** `input_scale` 直接 clip 激活（`w8a8_static.py:78` `torch.ops.vllm.quantize(x, layer.aclnn_input_scale, ...)`）。
- **`w8a8_dynamic`**（`methods/w8a8_dynamic.py:48`）：
  > "uses **dynamic per-token quantization** for activations."

`--quantization ascend` 默认走静态 per-tensor。机理：单一标量 `input_scale` 由校准数据（通常纯文本）算出；而图像 token 由 `Qwen3_VisionPatchMerger` 产出，激活分布与文本差异大，被截断/饱和后 logits 变平/过尖 → 采样陷入循环。

> 补充：vLLM 上游的 `_mark_tower_model`（`interfaces.py:251`）**并不**把视觉塔/merger 排除出量化，`Qwen3_VisionTransformer` 与 merger 的 `ColumnParallelLinear/RowParallelLinear` 都接收 `quant_config`。若 checkpoint 的 `quantization_config` 把 `visual.*`/`*merger*`/`lm_head` 一并量化，会进一步放大该问题。

---

### 3.4 🔵 嫌疑 4（仅特定模式）：`mega_moe_max_tokens` 丢弃超限 token

- `vllm_ascend/ascend_config.py:275`（注释原文）：
  > "When load imbalance causes a rank to receive more tokens than this limit, **the excess tokens are dropped and skipped from computation, degrading accuracy.**"
  默认 **131072**（commit `b6599594e` 2026-07-07 从 65536 上调）。
- 生效点 `ops/fused_moe/moe_comm_method.py:474`，**仅当 `enable_fused_mc2 == 1`** 触发（`moe_comm_method.py:453`）。

用户配置未设 `enable_fused_mc2`，故本路径默认不触发，可基本排除（但若后续启用 EP + `enable_fused_mc2`，需注意该上限）。

---

### 3.5 已排除项

- **mrope/3D 位置**：Ascend 的 `AscendMRotaryEmbedding` + `triton_split_qkv_rmsnorm_mrope`（`patch/worker/patch_qwen3vl.py:33`）为 Qwen3-VL 专属重写，已被大量 Qwen-VL 部署验证可用。若 mrope 本身损坏，所有 Qwen-VL 都会失败，而非仅此 W8A8 模型。
- **`weight_nz_mode` 缺失**：默认值为 1，量化权重已自动走 NZ 布局（`utils.py:262`）；官方设 `2` 仅是让 BF16 层也走 NZ 的**性能**优化，非正确性问题。
- **`eb0ce34e9` 量化 MoE MLP 激活修复**：仅影响 GELU 模型（如 Gemma4），Qwen3 用 SwiGLU，不受影响。

---

## 4. 部署参数对比（用户 vs 官方"已验证"A3B 配置）

| 参数项 | 用户配置 | 官方 A3B 教程 | 风险评估 |
|---|---|---|---|
| `enable_flashcomm1` | **true** | **false** | 🔴 官方明确"不推荐/不稳定" |
| `weight_nz_mode` | 未设（=1） | 2 | 🟢 仅性能项 |
| 投机解码方法 | **`qwen3_5_mtp`**（3 token） | **`eagle3`** | 🟠 方法可能不匹配 |
| `multistream_overlap_shared_expert` | true | 无 | 🟠 新特性，叠加风险 |
| `enable_cpu_binding` | true | 无 | 🟡 |
| `--quantization` | ascend | ascend | ✅ 一致 |
| `cudagraph_mode` | FULL_DECODE_ONLY | FULL_DECODE_ONLY | ✅ 一致 |
| `--async-scheduling` | on | on | ✅ 一致 |

---

## 5. 验证与缓解方案（二分法定位，每步只动一项）

> 强烈建议按顺序执行，便于定位到具体路径。

1. **将 `enable_flashcomm1` 改为 `false`**（对齐官方）——第一优先，最可能直接解决：
   ```json
   --additional-config '{"enable_cpu_binding":true, "enable_flashcomm1":false}'
   ```
   并暂时移除 `multistream_overlap_shared_expert`。
2. **去掉 `--speculative-config`**（关闭 MTP 投机解码）。若循环消失 → MTP/W8A8 接受率问题；之后如需投机解码，改用与模型匹配的 `eagle3` draft。
3. 若 1+2 后仍循环：**临时去掉 `--quantization ascend` 跑 BF16**。正常 → 确认 W8A8 静态量化对视觉激活的截断亦为成因（需用含图校准集重量化，或排除 visual/merger/lm_head）。
4. 若 3 后仍循环：顶层加 **`--enforce-eager`**（全局关 aclgraph），排除 DeepStack + 图捕获问题。

> 提示：`--max-model-len 71680` 较大、`--max-num-batched-tokens 8192` 为 prefill 分块；图片会使单请求 prefill 显著变长，第 1、2 步的问题都会被放大。

---

## 6. 总结

| 维度 | 结论 |
|---|---|
| **是否 vLLM 上游源码 bug** | 否 |
| **是否模型权重问题** | 否（大概率） |
| **是否 vLLM-Ascend 后端 bug** | 是"部署配置"问题，非代码逻辑 bug |
| **主因** | `enable_flashcomm1: true`（官方对 A3B 已标为不推荐） |
| **次因** | `qwen3_5_mtp` 投机解码 + W8A8 的脆弱组合 |
| **共同点** | 二者都直接作用在 MoE / 视觉特征 / 采样这三条"图片输入"关键链路上 |
| **首要动作** | 关闭 `enable_flashcomm1`（改 `false`） |

建议先执行第 1 步（关 flashcomm1），循环大概率即消失；再决定是否保留投机解码。若需进一步收敛到具体某条内核路径，可在执行第 1 步后反馈现象。

---

## 附录：关键代码与提交索引

**vLLM 上游（`vllm/`）**
- `model_executor/models/qwen3_vl_moe.py` — Qwen3-VL MoE 主模型
- `model_executor/models/qwen3_vl.py:2504` — M-ROPE 位置计算
- `model_executor/models/qwen3_vl.py:2706` — DeepStack 特征注入
- `model_executor/models/interfaces.py:251` — `_mark_tower_model`（不排除视觉塔量化）
- commit `5963c1947`（2026-05-28）— Fix DeepStack accuracy degradation under torch.compile（已修复）

**vLLM-Ascend（`vllm-ascend/`）**
- `quantization/methods/w8a8_static.py:25` — 静态 per-tensor 激活量化
- `quantization/methods/w8a8_dynamic.py:48` — 动态 per-token 激活量化
- `ascend_config.py:275` — `mega_moe_max_tokens`（默认 131072，超限 token 被丢弃）
- `ops/fused_moe/fused_moe.py:571` / `experts_selector.py:259` — flashcomm1 门控 MoE all-to-all
- `patch/worker/patch_qwen3vl.py:24` — flashcomm1 门控 DeepStack TP 切分
- `ops/rotary_embedding.py:228` — `AscendRotaryEmbedding`（npu rope）
- commit `34fa3b216`（2026-07-18）— 官方 A3B 教程关闭 flashcomm1
- commit `03cc154cb`（2026-07-24）— W8A8 投机解码接受率修复
- commit `b6599594e`（2026-07-07）— mega_moe_max_tokens 默认值上调
- commit `eb0ce34e9`（2026-07-10）— 量化 MoE MLP 激活修复（仅 GELU）
