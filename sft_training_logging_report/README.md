# LLaMA-Factory SFT 训练关键日志信息报告

> 分析版本：基于 LLaMA-Factory 当前代码库
> 分析范围：SFT（Supervised Fine-Tuning）场景
> 分析日期：2026-05-07

---

## 概述

本报告针对 LLaMA-Factory 框架在 SFT（监督微调）场景下的训练流程，从三个维度梳理了框架在运行过程中输出的关键日志信息：

1. **训练数据加载阶段** — 数据集加载、格式转换、质量校验等日志
2. **模型训练活动启动阶段** — 模型初始化、参数统计、优化器配置等日志
3. **模型训练过程中** — 训练步进、评估指标、Checkpoint 保存等日志

---

## 一、训练数据加载阶段

### 1.1 数据集加载信息

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Loading dataset {dataset_attr}...` | `data/loader.py:58` | 开始加载数据集，打印数据集属性 |
| `Sampled {num} examples from dataset {dataset_attr}.` | `data/loader.py:155` | 采样后的样本数量 |
| `Loaded tokenized dataset from {path}.` | `data/loader.py:295` | 从磁盘加载预处理数据集 |
| `Tokenized dataset is saved at {path}.` | `data/loader.py:333` | 预处理数据集保存路径 |
| `Loading dataset from disk will ignore other data arguments.` | `data/loader.py:289` | 磁盘加载时忽略其他参数的警告 |

**进度条提示：**

- `"Running tokenizer on dataset"` — tokenizer 处理进度 (`loader.py:252`)
- `"Converting format of dataset"` — 数据格式转换进度 (`converter.py:416`)

### 1.2 模板与 Tokenizer 配置

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Add eos token: {token}` / `Replace eos token: {token}` | `template.py:182,184` | 特殊 token 变更 |
| `Add pad token: {token}` | `template.py:204` | Padding token 配置 |
| `Add {words} to stop words.` | `template.py:213` | 停用词设置 |
| `Using tool format: {format}` | `template.py:647` | 工具调用格式 |
| `Using default system message: {msg}` | `template.py:653` | 系统提示词 |
| 模板未指定时尝试从 tokenizer 解析或使用 `empty` 模板 | `template.py:632,635` | 模板自动检测警告 |

### 1.3 数据质量与校验

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Dropped invalid example: {content}` | `processor/supervised.py:114,157` | 丢弃格式异常样本 |
| `Dropped lengthy example with length {len} > {cutoff}` | `processor/supervised.py:173` | 超长样本被丢弃 |
| `Invalid role tag in {messages}` / `Invalid message count` | `converter.py:162,176` | ShareGPT 格式校验警告 |
| `Media {path} does not exist in media_dir` | `converter.py:61` | 多模态文件缺失警告 |

### 1.4 数据样例预览

当 `logging_steps > 0` 时，框架会打印第一条样例的完整信息（`processor/supervised.py:139-142`）：

```
training example:
input_ids:     [token_id 列表]
inputs:        [decode 后的完整文本，含特殊 token]
label_ids:     [label token_id 列表]
labels:        [decode 后的标签文本]
```

该功能可帮助用户快速验证数据格式和标签构建是否正确。

### 1.5 数据混合策略

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `The samples between different datasets will not be mixed in streaming mode.` | `data_utils.py:60` | 流式模式不支持混合 |
| `We recommend using mix_strategy=concat in non-streaming mode.` | `data_utils.py:66` | 推荐使用 concat 策略 |

---

## 二、训练活动启动阶段

### 2.1 框架初始化

**欢迎横幅** (`launcher.py:44-54`)：

```
----------------------------------------------------------
| Welcome to LLaMA Factory, version {VERSION}             |
|                                                          |
| Project page: https://github.com/hiyouga/LLaMA-Factory  |
----------------------------------------------------------
```

**分布式训练初始化** (`launcher.py:69-71`)：

- `Initializing {n} distributed tasks at: {addr}:{port}`
- `Multi-node training enabled: num nodes: {n}, node rank: {r}`

### 2.2 模型加载与量化

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Loading {bits}-bit {method}-quantized model.` | `model/loader.py:130` | 量化模型加载（GPTQ/AWQ等） |
| `Quantizing model to {bits} bit with bitsandbytes/HQQ/EETQ.` | `model/loader.py:194,206,216` | 在线量化方式 |

### 2.3 微调方法声明

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Fine-tuning method: Full` | `adapter.py:47` | 全参数微调 |
| `Fine-tuning method: Freeze` | `adapter.py:66` | 冻结部分层 |
| `Fine-tuning method: LoRA` / `DoRA` | `adapter.py:151` | LoRA/DoRA 微调 |
| `Fine-tuning method: OFT` | `adapter.py:151` | OFT 微调 |
| `Set trainable layers: {layers}` | `adapter.py:138` | 冻结策略的可训练层 |
| `Loaded adapter(s): {paths}` | `adapter.py:202` | 已加载的适配器 |
| `Merged {n} adapter(s).` | `adapter.py:194` | 合并适配器 |

### 2.4 参数统计（核心信息）

文件 `model/loader.py:226-235`，这是训练启动时最关键的日志之一：

```
trainable params: 6,553,600 || all params: 6,898,644,992 || trainable%: 0.0950
```

该日志清晰展示了：
- **可训练参数数量** — 参与梯度更新的参数总数
- **模型总参数量** — 包含冻结层的完整参数
- **可训练参数占比** — 百分比形式，便于快速判断微调规模

### 2.5 精度配置

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Pure bf16/BAdam detected, remaining trainable params in half precision.` | `adapter.py:320` | 半精度训练 |
| `DeepSpeed ZeRO3 detected, remaining trainable params in float32.` | `adapter.py:322` | DeepSpeed ZeRO3 精度 |
| `Upcasting trainable params to float32.` | `adapter.py:324` | 精度上转 |
| `Using PiSSA initialization.` | `adapter.py:266` | PiSSA 初始化方式 |

### 2.6 FP8 配置（如果启用）

文件 `train/fp8_utils.py`：

- `Creating FP8 configuration with backend: {backend}`
- `FP8 training enabled with {backend} backend.`
- `Set ACCELERATE_MIXED_PRECISION=fp8`
- `FP8 training enabled with TorchAO backend. For optimal performance, ensure model layer dimensions are mostly divisible by 16.`

### 2.7 优化器信息

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Using GaLore optimizer with args: {kwargs}` | `trainer_utils.py:281` | GaLore 优化器 |
| `Using APOLLO optimizer with args: {kwargs}` | `trainer_utils.py:336` | APOLLO 优化器 |
| `Using LoRA+ optimizer with loraplus lr ratio {ratio}` | `trainer_utils.py:408` | LoRA+ 学习率比 |
| `Using BAdam optimizer with layer-wise/ratio-based update...` | `trainer_utils.py:446,465` | BAdam 分层/比例更新 |
| `Using Adam-mini optimizer.` | `trainer_utils.py:494` | Adam-mini |
| `Using Muon optimizer with {n} Muon params and {m} AdamW params.` | `trainer_utils.py:521` | Muon 优化器 |

### 2.8 配置警告

文件 `hparams/parser.py:404-438`，关键警告包括：

- 量化训练建议开启 `upcast_layernorm`
- 推荐使用混合精度训练
- GaLore/APOLLO 与混合精度的兼容性提醒
- 4/8-bit 模式下评估的注意事项

---

## 三、模型训练过程中

### 3.1 训练步进日志（核心，每个 `logging_steps` 触发）

**v0 架构（HuggingFace Trainer）** — `callbacks.py:270-308`：

输出示例：
```
{'loss': 2.3456, 'learning_rate': 5.0000e-05, 'epoch': 1.23}
```

完整记录字段：

| 字段 | 说明 | 触发条件 |
|------|------|---------|
| `current_steps` | 当前步数 | 始终 |
| `total_steps` | 总步数 | 始终 |
| `loss` | 训练损失 | 始终 |
| `eval_loss` | 验证损失 | 评估期间 |
| `lr` (learning_rate) | 当前学习率 | 始终 |
| `epoch` | 当前 epoch | 始终 |
| `percentage` | 训练进度百分比 | 始终 |
| `elapsed_time` | 已用时间（HH:MM:SS） | 始终 |
| `remaining_time` | 预估剩余时间（HH:MM:SS） | 始终 |
| `throughput` | 吞吐量（tokens/sec） | 需 `num_input_tokens_seen > 0` |
| `total_tokens` | 已处理总 token 数 | 需 `num_input_tokens_seen > 0` |
| `vram_allocated` | 显存占用 GB | 需设置 `RECORD_VRAM` |
| `vram_reserved` | 显存预留 GB | 需设置 `RECORD_VRAM` |

**v1 架构（自定义 Trainer）** — `v1/core/base_trainer.py:302-311`：

输出示例：
```
epoch: 1.0000, step: 100, loss: 2.3456, grad_norm: 1.2345, learning_rate: 0.0000500000, total_steps: 1000
```

v1 架构额外包含 `grad_norm`（梯度范数），并自动写入 `trainer_log.jsonl`。

### 3.2 时间统计机制

文件 `callbacks.py:204-211`：

```
elapsed_time = 当前时间 - 训练开始时间
remaining_time = (总步数 - 当前步数) × 平均每步耗时
```

时间格式为 `HH:MM:SS`，便于直观评估训练进度。

### 3.3 评估日志

**训练结束后** (`sft/workflow.py:146-149`)：

```python
metrics = trainer.evaluate(metric_key_prefix="eval", **gen_kwargs)
trainer.log_metrics("eval", metrics)
trainer.save_metrics("eval", metrics)
```

**评估指标** (`sft/metric.py`)：

| 指标 | 触发条件 | 说明 |
|------|---------|------|
| `eval_loss` | 始终 | 验证损失 |
| `eval_accuracy` | `compute_accuracy=True` | 预测准确率 |
| `rouge-1 / rouge-2 / rouge-l` | `predict_with_generate=True` | ROUGE 相似度 |
| `bleu-4` | `predict_with_generate=True` | BLEU-4 相似度 |

### 3.4 训练完成汇总

**训练结果** (`sft/workflow.py:110-126`)：

| 字段 | 说明 |
|------|------|
| `train_runtime` | 训练总时长（秒） |
| `train_samples_per_second` | 每秒处理样本数 |
| `train_steps_per_second` | 每秒执行步数 |
| `train_loss` | 平均训练损失 |
| `effective_tokens_per_sec` | 有效 token 吞吐量（需开启 `include_effective_tokens_per_second`） |

**最大步数到达** (`v1/core/base_trainer.py:317-321`)：

```
Reached max_steps ({n}), stopping training.
```

### 3.5 Checkpoint 保存日志

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Value head model saved at: {dir}` | `callbacks.py:95` | Value head 模型保存 |
| `Initial PiSSA adapter will be saved at: {dir}.` | `callbacks.py:138` | PiSSA 初始适配器保存 |
| `Converted PiSSA adapter will be saved at: {dir}.` | `callbacks.py:152` | PiSSA 转换后适配器保存 |
| `Model saved to {dir}` | `v1/core/base_trainer.py:340` | 最终模型保存路径 |

### 3.6 异常检测

| 日志内容 | 文件位置 | 说明 |
|---------|---------|------|
| `Gradient norm is not finite: {val}` | `v1/core/base_trainer.py:280` | 梯度爆炸/NaN 警告 |
| `The displayed gradient norm will be all zeros in layerwise GaLore.` | `trainer_utils.py:249` | GaLore 梯度显示为 0 的提示 |
| `Batch generation can be very slow. Consider using scripts/vllm_infer.py` | `sft/workflow.py:153` | 批量生成性能警告 |

### 3.7 持久化日志文件

| 文件 | 格式 | 说明 |
|------|------|------|
| `trainer_log.jsonl` | JSON Lines | 每步训练日志，含 loss/lr/epoch/timing 等 |
| `running_log.txt` | `[INFO\|时间] 文件:行号 >> 消息` | 人可读的带时间戳日志 |
| `trainer_state.json` | JSON | HuggingFace Trainer 完整状态，含 log_history |
| `training_loss.png` | PNG 图片 | 训练/验证损失曲线图 |

---

## 总结：SFT 训练日志信息流转全景

```
启动 → [欢迎横幅 + 分布式初始化]
  ↓
模型加载 → [量化信息 + 微调方法 + 参数统计 + 精度配置]
  ↓
数据加载 → [数据集名称 + 样本数 + 格式转换 + 质量校验 + 样例预览]
  ↓
优化器初始化 → [优化器类型 + 特殊参数配置]
  ↓
训练循环 → [每 logging_steps: loss, lr, epoch, progress%, time]
  ↓
评估（可选）→ [eval_loss, accuracy, ROUGE, BLEU]
  ↓
训练结束 → [汇总: runtime, throughput, effective_tps + 损失曲线图]
```

### 日志级别说明

框架使用自定义的 `logger.info_rank0()` 和 `logger.warning_rank0()` 方法，确保在分布式训练时仅主进程输出日志，避免日志重复。关键信息使用 `info` 级别，需要用户注意的配置问题使用 `warning` 级别。

### 关键源码文件索引

| 文件路径 | 职责 |
|---------|------|
| `src/llamafactory/data/loader.py` | 数据集加载与 tokenization |
| `src/llamafactory/data/converter.py` | 数据格式转换与校验 |
| `src/llamafactory/data/processor/supervised.py` | SFT 数据处理器 |
| `src/llamafactory/data/template.py` | 模板与 tokenizer 配置 |
| `src/llamafactory/model/loader.py` | 模型加载与参数统计 |
| `src/llamafactory/model/adapter.py` | 微调方法与适配器管理 |
| `src/llamafactory/train/callbacks.py` | 训练回调与日志输出 |
| `src/llamafactory/train/sft/workflow.py` | SFT 训练工作流 |
| `src/llamafactory/train/sft/metric.py` | SFT 评估指标计算 |
| `src/llamafactory/train/trainer_utils.py` | 优化器与工具函数 |
| `src/llamafactory/hparams/parser.py` | 训练参数解析与校验 |
| `src/llamafactory/launcher.py` | 框架入口与分布式初始化 |
