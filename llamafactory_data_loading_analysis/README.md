# LLaMA-Factory SFT 训练数据集加载机制深度分析报告

> 分析代码库：/root/llamaFactory/LlamaFactory
> 分析日期：2026-05-07
> 分析范围：SFT 场景下训练数据集从配置到加载的完整流程

---

## 一、数据加载全景架构

```
用户 YAML 配置
    ↓
run_exp() [tuner.py] → get_train_args() → DataArguments 解析
    ↓
get_dataset() [loader.py:276]  ← 主入口
    ↓
┌─────────────────────────────────────────────────────┐
│ 1. 解析数据集配置  get_dataset_list() [parser.py:93]  │
│    → 从 dataset_info.json 读取 DatasetAttr            │
├─────────────────────────────────────────────────────┤
│ 2. 加载原始数据集  _load_single_dataset() [loader.py:51] │
│    → HuggingFace / ModelScope / OpenMind / 本地文件    │
├─────────────────────────────────────────────────────┤
│ 3. 格式对齐转换  align_dataset() [converter.py:393]    │
│    → Alpaca / ShareGPT / OpenAI → 标准内部格式         │
├─────────────────────────────────────────────────────┤
│ 4. 多数据集合并  merge_dataset() [data_utils.py:51]    │
│    → concat / interleave_under / interleave_over      │
├─────────────────────────────────────────────────────┤
│ 5. 训练/验证拆分  split_dataset() [data_utils.py:85]   │
│    → 按 val_size 或独立 eval_dataset 拆分              │
├─────────────────────────────────────────────────────┤
│ 6. Tokenization  _get_preprocessed_dataset() [loader.py:229] │
│    → SupervisedDatasetProcessor / PackedSupervisedDatasetProcessor │
└─────────────────────────────────────────────────────┘
    ↓
DatasetModule { train_dataset, eval_dataset }
```

---

## 二、入参详解：DataArguments 全量字段

**文件位置**：`src/llamafactory/hparams/data_args.py`

### 2.1 数据集选择参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `dataset` | `str \| None` | `None` | 训练数据集名称，逗号分隔多个数据集 |
| `eval_dataset` | `str \| None` | `None` | 评估数据集名称，逗号分隔多个数据集 |
| `dataset_dir` | `str` | `"data"` | 数据集文件夹路径，包含 dataset_info.json 和数据文件 |
| `media_dir` | `str \| None` | `None` | 多模态文件（图片/视频/音频）目录，默认等于 dataset_dir |
| `template` | `str \| None` | `None` | 构造 prompt 的模板名称 |

### 2.2 序列长度与训练行为参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `cutoff_len` | `int` | `2048` | Token 序列最大长度，超过的样本被截断 |
| `train_on_prompt` | `bool` | `False` | 是否对 prompt 部分也计算 loss（默认仅 response 计算 loss） |
| `mask_history` | `bool` | `False` | 是否屏蔽历史轮次，仅训练最后一轮 |
| `ignore_pad_token_for_loss` | `bool` | `True` | 计算 loss 时是否忽略 pad token |
| `val_size` | `float` | `0.0` | 验证集比例（0-1），不能与 eval_dataset 同时指定 |

### 2.3 流式与缓存参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `streaming` | `bool` | `False` | 是否启用流式加载（大数据集场景） |
| `buffer_size` | `int` | `16384` | 流式模式下随机采样的缓冲区大小 |
| `overwrite_cache` | `bool` | `False` | 是否覆盖已缓存的预处理数据 |
| `preprocessing_batch_size` | `int` | `1000` | 预处理时每批次的样本数量 |
| `preprocessing_num_workers` | `int \| None` | `None` | 预处理并行进程数 |
| `tokenized_path` | `str \| None` | `None` | 已 tokenized 数据集的保存/加载路径 |
| `data_shared_file_system` | `bool` | `False` | 是否使用共享文件系统（分布式训练场景） |

### 2.4 数据集混合参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mix_strategy` | `Literal` | `"concat"` | 多数据集混合策略：concat / interleave_under / interleave_over / interleave_once |
| `interleave_probs` | `str \| None` | `None` | 各数据集的采样概率（逗号分隔），仅 interleave 模式有效 |
| `max_samples` | `int \| None` | `None` | 调试用，截断每个数据集的最大样本数 |

### 2.5 评估参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `eval_num_beams` | `int \| None` | `None` | 评估时 beam search 的 beam 数量 |
| `eval_on_each_dataset` | `bool` | `False` | 是否对每个数据集分别评估 |

### 2.6 Packing 参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `packing` | `bool \| None` | `None` | 启用序列打包（将多个短样本拼接到 cutoff_len） |
| `neat_packing` | `bool` | `False` | 启用无交叉注意力的打包（自动开启 packing） |

### 2.7 模板与工具参数

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `tool_format` | `str \| None` | `None` | 函数调用示例的工具格式 |
| `default_system` | `str \| None` | `None` | 覆盖模板中的默认 system message |
| `enable_thinking` | `bool \| None` | `True` | 是否启用推理模型的 thinking 模式 |
| `preserve_thinking` | `bool` | `False` | 是否在历史轮次中保留 thinking 内容 |

### 2.8 参数校验逻辑（`__post_init__`）

```
1. dataset / eval_dataset: 逗号字符串 → 拆分为 list[str]
2. media_dir: 未指定时默认等于 dataset_dir
3. val_size 与 dataset/eval_dataset 互斥校验
4. interleave_probs 仅在 interleave 模式有效，长度须与数据集数一致
5. streaming 模式下 val_size 必须为整数，不能使用 max_samples
6. mask_history 与 train_on_prompt 互斥
7. neat_packing 自动开启 packing
8. packing 开启时 cutoff_len 减 1（避免 pad_to_multiple_of 问题）
```

---

## 三、数据集配置解析：dataset_info.json

**文件位置**：`src/llamafactory/data/parser.py`

### 3.1 DatasetAttr 数据类

每个数据集在 `dataset_info.json` 中定义为一组属性，解析为 `DatasetAttr` 对象：

```python
@dataclass
class DatasetAttr:
    # 基本配置
    load_from: Literal["hf_hub", "ms_hub", "om_hub", "script", "file", "cloud_file"]
    dataset_name: str                    # 数据集名称/路径
    formatting: Literal["alpaca", "sharegpt", "openai"]  # 数据格式
    ranking: bool = False                # 是否为排序数据集（DPO）
    subset: str | None = None            # 子集名称
    split: str = "train"                 # 数据集拆分
    folder: str | None = None            # 子目录
    num_samples: int | None = None       # 采样数量

    # 通用列名映射
    system: str | None = None            # system 列名
    tools: str | None = None             # tools 列名
    images: str | None = None            # images 列名
    videos: str | None = None            # videos 列名
    audios: str | None = None            # audios 列名

    # DPO 列名映射
    chosen: str | None = None
    rejected: str | None = None
    kto_tag: str | None = None

    # Alpaca 格式列名
    prompt: str | None = "instruction"
    query: str | None = "input"
    response: str | None = "output"
    history: str | None = None

    # ShareGPT 格式标签
    messages: str | None = "conversations"
    role_tag: str | None = "from"
    content_tag: str | None = "value"
    user_tag: str | None = "human"
    assistant_tag: str | None = "gpt"
    observation_tag: str | None = "observation"
    function_tag: str | None = "function_call"
    system_tag: str | None = "system"
```

### 3.2 数据源识别逻辑

`get_dataset_list()` 根据 `dataset_info.json` 中的字段自动判断数据源：

```
含 "hf_hub_url"      → load_from="hf_hub"   (HuggingFace Hub)
含 "ms_hub_url"      → load_from="ms_hub"   (ModelScope Hub)
含 "om_hub_url"      → load_from="om_hub"   (OpenMind Hub)
含 "script_url"      → load_from="script"   (本地加载脚本)
含 "cloud_file_name" → load_from="cloud_file" (S3/GCS 云存储)
含 "file_name"       → load_from="file"     (本地文件)
```

### 3.3 dataset_info.json 配置示例

```json
{
  "my_sft_dataset": {
    "file_name": "my_data.json",
    "formatting": "sharegpt",
    "columns": {
      "messages": "conversations",
      "system": "system"
    },
    "tags": {
      "role_tag": "role",
      "content_tag": "content",
      "user_tag": "user",
      "assistant_tag": "assistant"
    }
  }
}
```

---

## 四、原始数据加载：_load_single_dataset()

**文件位置**：`src/llamafactory/data/loader.py:51-161`

### 4.1 数据源加载机制

```
┌──────────────────────────────────────────────────────────┐
│ hf_hub  → load_dataset(path, name, data_dir, split, …)  │
│ ms_hub  → MsDataset.load(dataset_name, subset_name, …)  │
│ om_hub  → OmDataset.load_dataset(path, name, …)         │
│ script  → load_dataset(本地脚本路径, …)                   │
│ cloud_file → Dataset.from_list(read_cloud_json(path))    │
│ file    → load_dataset(文件类型, data_files=[…], …)      │
└──────────────────────────────────────────────────────────┘
```

**文件类型自动识别**：通过 `FILEEXT2TYPE` 映射，支持 `.json`、`.jsonl`、`.csv`、`.tsv`、`.parquet` 等格式。

**本地文件加载细节**：
- 支持单文件和目录（目录下所有文件自动收集）
- 所有文件类型必须一致
- 目录路径 = `dataset_dir / dataset_name`

### 4.2 数据采样

```python
# dataset_info.json 中配置的 num_samples（确定性采样）
if dataset_attr.num_samples is not None:
    indexes = np.random.permutation(len(dataset))[:target_num]  # 先全排列
    if target_num > len(dataset):  # 需要重复采样
        expand_indexes = np.random.choice(len(dataset), target_num - len(indexes))
        indexes = np.concatenate((indexes, expand_indexes))
    dataset = dataset.select(indexes)

# DataArguments.max_samples（截断采样）
if data_args.max_samples is not None:
    dataset = dataset.select(range(min(max_samples, len(dataset))))
```

### 4.3 流式模式处理

```python
# HuggingFace Hub / script / om_hub：直接 streaming=True
dataset = load_dataset(…, streaming=True)

# 本地文件：先完整加载，再转为 IterableDataset
if data_args.streaming and dataset_attr.load_from == "file":
    dataset = dataset.to_iterable_dataset(num_shards=training_args.dataloader_num_workers)
```

---

## 五、格式对齐转换：align_dataset()

**文件位置**：`src/llamafactory/data/converter.py:393-425`

### 5.1 转换目标：统一内部格式

无论原始数据是 Alpaca、ShareGPT 还是 OpenAI 格式，都转换为以下标准格式：

```python
{
    "_prompt":   [{"role": "user", "content": "..."}],      # 用户轮次列表
    "_response": [{"role": "assistant", "content": "..."}],  # 助手回复列表
    "_system":   "system message",                           # 系统提示词
    "_tools":    "tool definitions",                         # 工具定义
    "_images":   [image_path, ...],                          # 图片列表
    "_videos":   [video_path, ...],                          # 视频列表
    "_audios":   [audio_path, ...],                          # 音频列表
}
```

### 5.2 三种转换器详解

#### Alpaca 格式转换器（`AlpacaDatasetConverter`）

**适用场景**：单轮/多轮指令数据

**原始格式**：
```json
{
  "instruction": "请翻译这句话",
  "input": "Hello World",
  "output": "你好世界",
  "system": "你是一个翻译器",
  "history": [["Q1", "A1"], ["Q2", "A2"]]
}
```

**转换逻辑**：
```
1. history → 转为 user/assistant 对话对加入 prompt
2. instruction + input → 拼接为 "\n".join(query) 作为最后一轮 user
3. output → 作为 assistant 回复
4. 支持 KTO 格式（kto_tag 布尔值标记正/负样本）
5. 支持 DPO 格式（chosen/rejected 对）
6. 多模态字段（images/videos/audios）→ 调用 _find_medias() 补全路径
```

**prompt 和 response 拼接规则**：
- `prompt` 字段非空 → 加入 query
- `query` 字段非空 → 追加到 query
- 最终 query = `"\n".join(query_list)`

#### ShareGPT 格式转换器（`SharegptDatasetConverter`）

**适用场景**：多轮对话数据

**原始格式**：
```json
{
  "conversations": [
    {"from": "human", "value": "你好"},
    {"from": "gpt", "value": "你好！有什么可以帮助你的？"},
    {"from": "human", "value": "解释量子力学"},
    {"from": "gpt", "value": "量子力学是…"}
  ]
}
```

**转换逻辑**：
```
1. tag_mapping: human→user, gpt→assistant, observation→observation, function_call→function, system→system
2. 奇偶校验：奇数位必须是 user/observation，偶数位必须是 assistant/function_call
3. 如果第一条是 system_tag → 提取为 system message
4. 正常样本：prompt = 除最后一条外的所有消息, response = 最后一条消息
5. DPO 样本：prompt = 所有对话, response = [chosen, rejected]
6. KTO 样本：prompt = 除最后一条, response = 最后一条 + 空/空 + 最后一条
7. 数据异常时：broken_data=True, prompt/response 设为空列表，该样本被跳过
```

**数据质量校验**：
- `Invalid role tag`：角色标签不在允许范围内
- `Invalid message count`：消息数量不符合奇偶要求（正常为奇数，ranking 为偶数）

#### OpenAI 格式转换器（`OpenAIDatasetConverter`）

**适用场景**：OpenAI API 风格数据、含工具调用

**原始格式**：
```json
{
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What's the weather?"},
    {"role": "assistant", "content": null, "tool_calls": [{"function": {"name": "get_weather", "arguments": "…"}}]},
    {"role": "tool", "content": "Sunny, 25°C"},
    {"role": "assistant", "content": "The weather is sunny, 25°C."}
  ]
}
```

**转换逻辑**：
```
1. 提取 system 消息
2. 遍历 messages：
   - assistant/function 角色：检查 tool_calls，如有则序列化为 JSON，角色改为 function
   - observation/tool 角色：收集 tool_responses，合并后作为 OBSERVATION 消息
3. 奇偶校验 + 消息数量校验
4. 自动注入 "detailed thinking off" system prompt（如无 system 且无 tools）
5. tools 字段：dict/list → JSON 序列化
```

### 5.3 多模态文件路径处理（`_find_medias()`）

```
对于 load_from="script" 或 "file" 的本地数据集：
  1. 遍历每个媒体文件路径
  2. 拼接 media_dir + 文件名
  3. 如果文件存在 → 使用完整路径
  4. 如果文件不存在 → 警告并保留原始路径

支持：
  - 单层列表：["img1.jpg", "img2.jpg"]
  - 嵌套列表（视频帧）：[["frame1.jpg", "frame2.jpg"], ["frame3.jpg"]]
```

---

## 六、多数据集合并：merge_dataset()

**文件位置**：`src/llamafactory/data/data_utils.py:51-82`

### 6.1 四种混合策略

| 策略 | 方法 | 说明 |
|------|------|------|
| `concat` | `concatenate_datasets()` | 直接拼接所有数据集，保持原始顺序 |
| `interleave_under` | `interleave_datasets(stopping_strategy="first_exhausted")` | 交替采样，最短数据集耗尽即停（欠采样） |
| `interleave_over` | `interleave_datasets(stopping_strategy="all_exhausted")` | 交替采样，所有数据集耗尽才停（过采样，短的会重复） |
| `interleave_once` | `interleave_datasets(stopping_strategy="all_exhausted_without_replacement")` | 交替采样，无放回遍历所有数据 |

**interleave_probs**：指定每个数据集的采样概率，如 `"0.5,0.3,0.2"`。

**注意事项**：
- 流式模式 + concat 策略：样本不会被混合（仅按顺序加载）
- 非流式模式 + interleave 策略：建议改用 concat（更高效）

---

## 七、训练/验证集拆分：split_dataset()

**文件位置**：`src/llamafactory/data/data_utils.py:85-131`

### 7.1 拆分逻辑

```
场景 1: 仅 dataset + val_size > 0
  → dataset.train_test_split(test_size=val_size)
  → train_dict["train"], eval_dict["validation"]

场景 2: 仅 dataset + val_size == 0
  → train_dict["train"] = dataset（全部用于训练）

场景 3: dataset + eval_dataset（独立指定）
  → train_dict["train"] = dataset
  → eval_dict["validation"] = eval_dataset
  → val_size 必须为 0（互斥校验）

场景 4: dataset + eval_on_each_dataset=True
  → eval_dataset 返回 dict，每个数据集独立评估
  → eval_dict["validation_dataset1"], eval_dict["validation_dataset2"], …
```

### 7.2 流式模式拆分

```python
# 流式模式使用 take/skip 代替 train_test_split
eval_data = dataset.take(int(val_size))   # 取前 N 条
train_data = dataset.skip(int(val_size))  # 跳过前 N 条
# 注意：val_size 必须为整数（非比例）
```

---

## 八、Tokenization 预处理：SFT Processor

**文件位置**：`src/llamafactory/data/processor/supervised.py`

### 8.1 处理器选择逻辑

```
stage == "sft" and packing == False → SupervisedDatasetProcessor
stage == "sft" and packing == True  → PackedSupervisedDatasetProcessor
stage == "sft" and neat_packing     → PackedSupervisedDatasetProcessor（4D attention mask）
```

### 8.2 SupervisedDatasetProcessor 核心编码逻辑

**`_encode_data_example()`** 将一条原始样本编码为 `input_ids` 和 `labels`：

```
输入: prompt=[{user,msg1}], response=[{assistant,msg2}], system, tools, images, videos, audios

处理流程:
  1. 多模态处理: template.mm_plugin.process_messages(prompt+response, images, videos, audios)
  2. 多轮编码: template.encode_multiturn(tokenizer, messages, system, tools)
     → 返回 encoded_pairs = [(source_ids_1, target_ids_1), (source_ids_2, target_ids_2), …]
  3. 截断策略: 逐轮累加，总长度达到 cutoff_len 时停止
  4. Label 构建:
     - train_on_prompt=False → source_ids 对应的 label = [IGNORE_INDEX] * len(source_ids)
     - train_on_prompt=True  → source_ids 对应的 label = source_ids（也参与 loss）
     - efficient_eos + 非首轮 → source_label 首位为 eos_token_id，其余为 IGNORE_INDEX
  5. mask_history=True:
     - 反转 encoded_pairs（最后一轮优先级最高）
     - 非最后一轮的 target_label = [IGNORE_INDEX]
     - 逆序拼接 input_ids

最终输出:
  input_ids = [bos] + source_1 + target_1 + source_2 + target_2 + … + [eos]
  labels    = [IGNORE] * len(source_1) + target_1 + [IGNORE] * len(source_2) + target_2 + … + [eos]
```

### 8.3 Label 构建图示

**标准模式（train_on_prompt=False, mask_history=False）**：
```
input_ids: [BOS] [用户 tokens] [助手 tokens] [用户 tokens] [助手 tokens] [EOS]
labels:    [IGN] [IGN … IGN]   [目标 tokens] [IGN] [IGN … IGN]   [目标 tokens] [EOS]
                    ↑ 不计算 loss      ↑ 计算 loss      ↑ 不计算 loss      ↑ 计算 loss
```

**train_on_prompt=True**：
```
input_ids: [BOS] [用户 tokens] [助手 tokens] [EOS]
labels:    [BOS] [用户 tokens] [助手 tokens] [EOS]
                    ↑ 也计算 loss     ↑ 计算 loss
```

**mask_history=True（仅训练最后一轮）**：
```
input_ids: [BOS] [用户1] [助手1] [用户2] [助手2] [EOS]
labels:    [IGN] [IGN]   [IGN]   [用户2] [助手2] [EOS]
                                     ↑ 仅最后一轮的 source + target 计算 loss
```

### 8.4 PackedSupervisedDatasetProcessor 打包逻辑

**目标**：将多个短样本拼接到 cutoff_len，提高 GPU 利用率。

```
1. 遍历 batch 中所有样本，编码为 (input_ids, labels)
2. 过滤长度超过 cutoff_len 的样本
3. 贪心背包算法 (greedy_knapsack):
   - 按样本长度排序
   - 贪心填充每个"背包"直到接近 cutoff_len
4. 拼接同一背包内的样本:
   packed_input_ids = sample1_input_ids + sample2_input_ids + …
   packed_labels    = sample1_labels + sample2_labels + …
5. Padding 到 cutoff_len + 1:
   packed_input_ids += [pad_token_id] * pad_length
   packed_labels    += [IGNORE_INDEX] * pad_length

Neat Packing 模式:
   - attention_mask 用不同 ID 区分子序列: [1,1,1, 2,2,2, 0,0,0]
   - 生成 PackingParams（子序列边界、多模态索引）
   - 配合 4D attention mask 防止子序列间交叉注意力
```

### 8.5 preprocess_dataset() 批处理流程

```python
# 对每个 batch（默认 1000 条）:
for i in range(len(examples["_prompt"])):
    # 1. 校验: prompt 必须为奇数条，response 必须为 1 条
    if len(examples["_prompt"][i]) % 2 != 1 or len(examples["_response"][i]) != 1:
        logger.warning("Dropped invalid example: …")
        continue  # 跳过无效样本

    # 2. 编码
    input_ids, labels = _encode_data_example(…)

    # 3. 收集
    model_inputs["input_ids"].append(input_ids)
    model_inputs["attention_mask"].append([1] * len(input_ids))
    model_inputs["labels"].append(labels)
    model_inputs["images"].append(…)
    model_inputs["videos"].append(…)
    model_inputs["audios"].append(…)
```

---

## 九、完整数据流示例

以一个 ShareGPT 格式的 SFT 数据集为例，追踪数据从配置到模型输入的完整变换：

### Step 1: YAML 配置
```yaml
dataset: my_chat_data
dataset_dir: data
template: qwen
cutoff_len: 4096
val_size: 0.05
```

### Step 2: dataset_info.json
```json
{
  "my_chat_data": {
    "file_name": "chat_data.json",
    "formatting": "sharegpt",
    "columns": {"messages": "conversations"},
    "tags": {"role_tag": "role", "content_tag": "content",
             "user_tag": "user", "assistant_tag": "assistant"}
  }
}
```

### Step 3: 原始 JSON 数据
```json
{"conversations": [
  {"role": "user", "content": "解释量子纠缠"},
  {"role": "assistant", "content": "量子纠缠是…"}
]}
```

### Step 4: 格式对齐后（converter 输出）
```python
{
  "_prompt":   [{"role": "user", "content": "解释量子纠缠"}],
  "_response": [{"role": "assistant", "content": "量子纠缠是…"}],
  "_system":   "",
  "_tools":    "",
  "_images":   None,
  "_videos":   None,
  "_audios":   None,
}
```

### Step 5: Tokenization 后（processor 输出）
```python
{
  "input_ids":     [bos, sys_tokens, user_tokens, assist_tokens, eos],
  "attention_mask": [1, 1, 1, 1, 1],
  "labels":        [IGN, IGN, IGN, target_tokens, eos],
  "images": None, "videos": None, "audios": None
}
```

### Step 6: 拆分后
```python
DatasetModule = {
  "train_dataset": Dataset(9500 条),   # 95% 训练
  "eval_dataset":  Dataset(500 条),    # 5% 验证
}
```

---

## 十、关键源码文件索引

| 文件路径 | 行数 | 核心职责 |
|---------|------|---------|
| `hparams/data_args.py` | 193 | DataArguments 全量参数定义与校验 |
| `data/parser.py` | 150 | DatasetAttr 数据集属性解析 + dataset_info.json 读取 |
| `data/loader.py` | 337 | 数据加载主入口 get_dataset() + 单数据集加载 + 合并 + 预处理 |
| `data/converter.py` | 426 | Alpaca/ShareGPT/OpenAI 三种格式转换器 |
| `data/data_utils.py` | 204 | 数据集合并/拆分/工具函数 |
| `data/processor/supervised.py` | 253 | SFT Processor（标准 + Packed）编码与 Label 构建 |
| `data/processor/processor_utils.py` | 89 | DatasetProcessor 基类 + infer_seqlen/greedy_knapsack 工具 |
| `data/collator.py` | 609 | DataCollator（批处理/填充/4D attention mask） |
| `data/template.py` | ~660 | 模板系统（prompt 构造 + 多轮编码） |
| `data/mm_plugin.py` | — | 多模态数据（图片/视频/音频）处理插件 |
| `train/sft/workflow.py` | 161 | SFT 工作流，调用 get_dataset() 入口 |
