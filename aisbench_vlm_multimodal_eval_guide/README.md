# AISBench 多模态模型精度测试指南（自定义图片 + 提示词）

> 基于 Huawei Ascend 版 **AISBench**，对 **Qwen3-VL 类多模态模型**（如 Qwen3-VL-30B-A3B）在**自定义图片 + 提示词**上做精度评测，并在精度测试过程中**记录每个请求的 TTFT / TPOT / 总完成时间（E2E）**。推理服务为 **vLLM（OpenAI 兼容接口）**。

---

## 0. 适用场景与重要前提

**场景**：有一段固定提示词 + 很多张图片，想让模型逐张推理，拿到输出，并统计每请求时延。

**三个必须先知道的前提**：

1. **`mm_custom` 默认评测器不算真实精度**。它的 `MMCustomEvaluator.score()` 写死返回 `accuracy: 1`，不比较 prediction 和 answer：

   ```python
   # ais_bench/benchmark/datasets/mm_custom.py
   class MMCustomEvaluator(BaseEvaluator):
       def score(self, predictions, references):
           result = {'accuracy': 1}   # 直接返回1
           return result
   ```
   所以精度测试 = 跑推理拿预测，**人工/判分模型看质量**；若要自动算准确率，需自备标准答案并改评测器（见文末附录）。

2. **预测文本与逐请求时延，默认分在两种模式的输出里，不在同一个文件**：

   | 模式 | 输出内容 | 文件 |
   |---|---|---|
   | `--mode infer` | 只有预测文本，**无时延** | `predictions/<模型abbr>/mm_custom.jsonl` |
   | `--mode perf` | 只有逐请求时延，**且会把预测文本 `content` 删掉** | `performances/<模型abbr>/mm_custom_details.jsonl` |

   底层原因：`perf_mode = (mode == "perf")`，时延指标只在 perf 模式经 `output.get_metrics()` 算出。

3. **TTFT / TPOT 必须流式（stream=True）**。时间戳 `record_time_point()` 虽然所有模式都采集，但粒度取决于 `stream`：
   - 非流式：只打 2 个点（请求发出 / 整段返回）→ 只能算 **E2E**，**算不出 TTFT/TPOT**。
   - 流式：每个 chunk 打一个点 → 能算 **TTFT / TPOT / ITL / E2E**。

---

## 1. 数据集构造（`.jsonl`）

一行一条样本，必填字段（来自校验函数 `check_mm_custom` 和 loader）：

| 字段 | 含义 | 必填 |
|---|---|---|
| `type` | `"image"` / `"video"` / `"audio"` | ✅ |
| `path` | 媒体文件**绝对路径列表**（可多张同类型） | ✅ |
| `question` | 提示词文本 | ✅ |
| `answer` | 标准答案（默认不参与打分，建议填便于自评） | 建议 |

**示例**（一段固定提示词 + 很多张图，每张图一行，`question` 填同一段提示词）：

```jsonl
{"type": "image", "path": ["/data/myimg/001.jpg"], "question": "请详细描述这张图片的内容", "answer": ""}
{"type": "image", "path": ["/data/myimg/002.jpg"], "question": "请详细描述这张图片的内容", "answer": ""}
{"type": "image", "path": ["/data/myimg/003.jpg"], "question": "请详细描述这张图片的内容", "answer": ""}
```

- **多图一条样本**：`"path": ["/data/a.jpg", "/data/b.jpg"]`
- 路径用**绝对路径**

存为例如 `/root/mydata/mm.jsonl`。

### 挂到 AISBench

编辑 `ais_bench/benchmark/configs/datasets/mm_custom/mm_custom_gen.py`：

```python
mm_custom_reader_cfg = dict(
    input_columns=['question', 'image', 'video', 'audio'],
    output_column='answer'      # 告诉框架哪一列是"标准答案"；无答案时见下
)

mm_custom_datasets = [
    dict(
        abbr='mm_custom',
        type=MMCustomDataset,
        path='/root/mydata/mm.jsonl',        # ← 改成你的 jsonl 绝对路径
        mm_type="path",                      # 本地路径用 "path"；服务端访问不到图片则改 "base64"
        num_frames=5,
        reader_cfg=mm_custom_reader_cfg,
        infer_cfg=mm_custom_infer_cfg,
        eval_cfg=mm_custom_eval_cfg
    )
]
```

**`output_column='answer'` 处理（无标准答案时二选一）**：
- 方案 A（推荐，零代码改动）：保持不动，jsonl 每行写 `"answer": ""`。
- 方案 B（改一行）：改成 `output_column=''`，jsonl 不用写 `answer` 字段。

---

## 2. 模型配置（vLLM 服务化）

### ⚠️ 多模态必须用 chat 版本

只有 `/v1/chat/completions` 支持 `image_url`：

| 配置名 | 端点 | 支持图片 | 用途 |
|---|---|---|---|
| `vllm_api_general` | `/v1/completions` | ❌ | 纯文本 |
| `vllm_api_general_chat` | `/v1/chat/completions` | ✅ 非流式 | 精度测试推荐 |
| **`vllm_api_stream_chat`** | `/v1/chat/completions` | ✅ **流式** | **要 TTFT/TPOT 必须用这个** |

### 编辑 `ais_bench/benchmark/configs/models/vllm_api/vllm_api_stream_chat.py`

```python
from ais_bench.benchmark.models import VLLMCustomAPIChat
from ais_bench.benchmark.utils.postprocess.model_postprocessors import extract_non_reasoning_content

models = [
    dict(
        attr="service",
        type=VLLMCustomAPIChat,
        abbr="vllm-api-stream-chat",
        path="",
        model="Qwen3-VL-30B-A3B",          # ← vLLM 服务加载的模型名；不确定留 "" 自动获取
        stream=True,                        # 流式：TTFT/TPOT 才有意义
        request_rate=0,
        use_timestamp=False,
        retry=2,
        api_key="",
        host_ip="192.168.x.x",             # ← vLLM 服务 IP
        host_port=8000,                    # ← vLLM 服务端口
        url="",                            # 非标准地址时填完整 url
        max_out_len=512,
        batch_size=1,
        trust_remote_code=False,
        generation_kwargs=dict(
            temperature=0,                 # 精度测试建议 0（确定性输出）
            ignore_eos=False,
        ),
        pred_postprocessor=dict(type=extract_non_reasoning_content),  # 自动剥离 <think>
    )
]
```

> 提示：vLLM 服务端必须能访问图片路径（模板默认 `file://{image}`）。若服务在**别的机器/容器**访问不到本地图片，把 `mm_custom_gen.py` 的 `mm_type="path"` 改成 `mm_type="base64"`。

---

## 3. 启动命令

```bash
# 拿预测文本
ais_bench --models vllm_api_stream_chat --datasets mm_custom_gen --mode infer --debug

# 拿逐请求时延（TTFT/TPOT/ITL/E2E）
ais_bench --models vllm_api_stream_chat --datasets mm_custom_gen --mode perf --debug
```

- `--mode infer`：只推理出结果。**不要用 `all`**（无标准答案，eval 出的 accuracy 是假的 1）。
- `--mode perf`：性能评测，逐请求时延落盘。
- `--debug`：第一次加，报错直接看屏。试少量样本可加 `--num-prompts 5`。
- 两次跑的 `mm_custom_gen.py` 数据范围要一致（否则 `id` 对不齐）。

**输出路径**：
- infer：`outputs/default/<时间戳>/predictions/vllm-api-stream-chat/mm_custom.jsonl`
- perf：`outputs/default/<时间戳>/performances/vllm-api-stream-chat/mm_custom_details.jsonl`（+ 同目录 `mm_custom_details.db`）

---

## 4. 同时拿到"精度 + 每请求时延"（方案：两次运行后整合）

由于预测和时延分在两次运行的输出里，需做整合。**对齐键是 `id`（= 数据集行号，两次运行稳定一致）**，不要用 `uuid`（每次随机）。

### 关键事实

- 两次 jsonl 都带稳定 `id`（数据集行号）。
- perf 明细里的 `time_points` 是 numpy 数组，被存进**同目录 `.db`** 文件（表 `numpy_store(id, arr_blob)`），jsonl 里只剩 `{"__db_ref__": <rowid>}` 引用 → 算 TTFT/TPOT 要解码出来。
- 时延公式（`time_points` 单位为秒，perf_counter 时间戳）：
  - `E2E  = (tp[-1] - tp[0]) * 1000` ms
  - `TTFT = (tp[1] - tp[0]) * 1000` ms（首 token）
  - `TPOT = mean(diff(tp)[1:]) * 1000` ms（token 间平均，排除首段 TTFT）

---

## 5. 结果整合脚本 `consolidate_results.py`

脚本与本报告同目录（`consolidate_results.py`）。功能：读三份输入 → 按 `id` 对齐 → 输出 Excel（明细 + 汇总）。

### 用法

```bash
pip install openpyxl   # 仅此一个；numpy/pandas 已是 ais_bench 依赖

python3 consolidate_results.py \
    --infer  outputs/default/<时间戳1>/predictions/vllm-api-stream-chat/mm_custom.jsonl \
    --perf   outputs/default/<时间戳2>/performances/vllm-api-stream-chat/mm_custom_details.jsonl \
    --dataset /root/mydata/mm.jsonl \
    --out    result.xlsx
```

### 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--infer` | ✅ | infer 运行的 `mm_custom.jsonl`（拿 prediction） |
| `--perf` | ✅ | perf 运行的 `mm_custom_details.jsonl`（拿逐请求时延） |
| `--dataset` | ❌ | 原始 `mm.jsonl`（按行号补图片路径/问题/答案列） |
| `--out` | ❌ | 输出 Excel，默认 `result.xlsx` |

### 输出 `result.xlsx`

- **明细 sheet**（每请求一行）：`id / image / question / answer / prediction / success / input_tokens / output_tokens / num_chunks / ttft_ms / tpot_ms / itl_median_ms / e2e_ms`
- **汇总 sheet**：总数、成功率，E2E/TTFT/TPOT 的平均/中位数/P90，token 吞吐估算

### 脚本核心逻辑

- 自动定位 perf 同目录同名 `.db`，解码 `time_points`（兼容内联 list 与 `__db_ref__` 两种形式）。
- 按 `id` 对齐 infer 预测 + perf 时延 + 原始数据集（0 基行号）。
- 预测优先取 infer，回退 perf。

---

## 附录 A：若要自动算准确率（需标准答案）

把 `ais_bench/benchmark/datasets/mm_custom.py` 的评测器改为真实比较：

```python
class MMCustomEvaluator(BaseEvaluator):
    def score(self, predictions, references):
        correct = sum(1 for p, r in zip(predictions, references)
                      if str(r).strip() and str(r).strip() in str(p))
        acc = correct / len(predictions) if predictions else 0
        return {'accuracy': acc}
```

（上例为"答案包含在预测中即算对"，按需改严格相等。）然后用 `--mode all`，汇总里即有真实 accuracy。

## 附录 B：相关代码位置速查

| 内容 | 路径 |
|---|---|
| 多模态自定义数据集配置 | `ais_bench/benchmark/configs/datasets/mm_custom/mm_custom_gen.py` |
| 数据加载 + 评测器 | `ais_bench/benchmark/datasets/mm_custom.py` |
| 数据校验 | `ais_bench/benchmark/utils/file/file.py` → `check_mm_custom` |
| 多模态提示模板 | `ais_bench/benchmark/openicl/icl_prompt_template/icl_prompt_template_mm.py` |
| 提示标签/解析 | `ais_bench/benchmark/utils/prompt/prompt.py` |
| vLLM chat 模型配置 | `ais_bench/benchmark/configs/models/vllm_api/vllm_api_stream_chat.py` |
| 时延采集/落盘 | `ais_bench/benchmark/models/api_models/base_api.py`、`.../output_handler/base_handler.py` |
| 时间戳数组存储 | `ais_bench/benchmark/openicl/icl_inferencer/output_handler/db_utils.py` |
| 中文文档（自定义数据集） | `docs/source_zh_cn/advanced_tutorials/custom_dataset.md` |
| 中文文档（多模态基准） | `docs/source_zh_cn/advanced_tutorials/multimodal_benchmark.md` |
