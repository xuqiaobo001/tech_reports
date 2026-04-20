# SGLang 上下文长度限制机制分析

> 分析日期：2026-04-20
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：启动参数对用户输入上下文长度的限制、超限时的错误信息与行为

---

## 一、三层长度限制体系

SGLang 通过三个层级的长度限制来保护系统：

```
请求到达
  │
  ├── 第1层：context_length（模型最大上下文）
  │     拦截点：TokenizerManager._validate_one_request()
  │     比较：input_tokens >= context_length
  │
  ├── 第2层：context_length（输入 + 输出总量）
  │     拦截点：TokenizerManager._validate_one_request()
  │     比较：input_tokens + max_new_tokens >= context_length
  │
  └── 第3层：max_req_input_len（KV cache 池容量限制）
        拦截点：Scheduler.validate_input_length()
        比较：input_tokens >= max_req_input_len
```

---

## 二、相关参数详解

### 2.1 `--context-length`（显式配置）

| 属性 | 说明 |
|------|------|
| **CLI 参数** | `--context-length` |
| **默认值** | `None`（使用模型 `config.json` 中的值） |
| **代码位置** | `server_args.py:304` |
| **含义** | 模型支持的最大上下文长度（输入 + 输出的总 token 数上限） |
| **支持格式** | 整数或人类可读格式（如 `8k`、`32k`、`128k`） |

**生效逻辑**（`model_config.py:391-420`）：

```python
def _derive_context_length(self, context_length):
    derived_context_len = get_context_length(self.hf_text_config)

    if context_length is not None:
        if context_length > derived_context_len:
            # 用户设置超过模型原生限制
            if SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN:
                self.context_len = context_length  # 允许覆盖
            else:
                raise ValueError(
                    "User-specified context_length is greater than the derived "
                    "context_length. Set SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 "
                    "to allow overriding."
                )
        else:
            self.context_len = context_length  # 正常设置
    else:
        self.context_len = derived_context_len  # 使用模型默认值
```

**注意事项**：
- 设为大于模型原生值时可能导致精度下降或 CUDA 错误
- 需要设置环境变量 `SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1` 才能覆盖

---

### 2.2 `max_req_input_len`（隐式计算，不可直接配置）

| 属性 | 说明 |
|------|------|
| **来源** | 自动计算 |
| **计算公式** | `min(max_total_num_tokens, context_len) - 5` |
| **代码位置** | `tp_worker.py:304` |
| **含义** | 单个请求允许的最大输入 token 数（受 KV cache 池容量约束） |

```python
# tp_worker.py:304
self.max_req_input_len = self.max_req_len - 5
assert self.max_req_len > 0 and self.max_req_input_len > 0, "Memory pool size is too small"
```

**与 `context_length` 的关系**：
- `max_req_input_len` 通常**小于** `context_length`
- 因为 KV cache 池的总容量受 GPU 显存限制，可能装不下 `context_length` 数量的 token
- 例如：`context_length=32768`，但 KV cache 池只能容纳 20000 个 token，则 `max_req_input_len ≈ 20000`

---

### 2.3 `--max-total-tokens`（显式配置，可选）

| 属性 | 说明 |
|------|------|
| **CLI 参数** | `--max-total-tokens` |
| **默认值** | `None`（自动计算） |
| **代码位置** | `server_args.py` |
| **含义** | KV cache 内存池的总 token 容量 |
| **影响** | 间接影响 `max_req_input_len` |

**自动计算逻辑**：

```
max_total_tokens = (GPU可用显存 × mem_fraction_static - 模型权重大小 - 激活值大小)
                    / 每个token的KV cache大小
```

---

### 2.4 `--allow-auto-truncate`（显式配置）

| 属性 | 说明 |
|------|------|
| **CLI 参数** | `--allow-auto-truncate` |
| **默认值** | `False` |
| **代码位置** | `server_args.py:656` |
| **含义** | 当输入超过限制时自动截断，而不是返回错误 |
| **影响** | 影响第1层和第2层拦截的行为（第3层也受影响） |

---

### 2.5 `--mem-fraction-static`

| 属性 | 说明 |
|------|------|
| **默认值** | `0.9` |
| **含义** | GPU 显存用于模型权重 + KV cache 的比例 |
| **间接影响** | 值越大 → KV cache 池越大 → `max_total_tokens` 越大 → `max_req_input_len` 越大 |

---

## 三、超限场景与错误信息

### 场景1：输入 token 数 >= `context_length`

**拦截位置**：`tokenizer_manager.py:819-832`

**未开启 `--allow-auto-truncate`**：

```python
if input_token_num >= self.context_len:
    raise ValueError(
        f"The input ({input_token_num} tokens) is longer than the "
        f"model's context length ({self.context_len} tokens)."
    )
```

**客户端收到的响应**：

```json
{
    "error": {
        "message": "The input (36000 tokens) is longer than the model's context length (32768 tokens).",
        "type": "ValueError"
    }
}
```

- HTTP 状态码：**400 Bad Request**
- **不消耗任何 GPU 资源**（此时还未分配 KV cache）

**开启了 `--allow-auto-truncate`**：

```
WARNING: The input (36000 tokens) is longer than the model's context length (32768 tokens). Truncating the input.
```

- 输入被截断到 `context_length`（32768 tokens）
- 请求继续正常处理
- 客户端得到截断后的结果

---

### 场景2：输入 + max_new_tokens >= `context_length`

**拦截位置**：`tokenizer_manager.py:834-859`

**未开启 `--allow-auto-truncate`**：

```python
if (max_new_tokens + input_token_num) >= _max_req_len:
    raise ValueError(
        f"Requested token count exceeds the model's maximum context length "
        f"of {self.context_len} tokens. You requested a total of {total_tokens} "
        f"tokens: {input_token_num} tokens from the input messages and "
        f"{max_new_tokens} tokens for the completion. Please reduce the number "
        f"of tokens in the input messages or the completion to fit within the limit."
    )
```

**客户端收到的响应**：

```json
{
    "error": {
        "message": "Requested token count exceeds the model's maximum context length of 32768 tokens. You requested a total of 34000 tokens: 30000 tokens from the input messages and 4000 tokens for the completion. Please reduce the number of tokens in the input messages or the completion to fit within the limit.",
        "type": "ValueError"
    }
}
```

- HTTP 状态码：**400 Bad Request**
- **不消耗任何 GPU 资源**

**开启了 `--allow-auto-truncate`**：

```
WARNING: Requested token count (30000 input + 4000 new) exceeds the model's context length
(32768 tokens). Truncating max_new_tokens.
```

- `max_new_tokens` 被自动缩减为 `context_length - input_tokens`
- 即 `32768 - 30000 = 2768`
- 请求继续处理，但输出长度受限

---

### 场景3：输入 token 数 >= `max_req_input_len`（但 < context_length）

这种情况说明：**模型支持这么长的上下文，但 GPU 显存放不下这么多 KV cache**。

**拦截位置**：`scheduler.py:1981-1989` + `utils.py:113-143`

```python
# scheduler.py
error_msg = validate_input_length(req, self.max_req_input_len, self.server_args.allow_auto_truncate)
if error_msg:
    req.set_finish_with_abort(error_msg)
    self._add_request_to_queue(req)
    return
```

**未开启 `--allow-auto-truncate`**：

```
Input length (25000 tokens) exceeds the maximum allowed length (20000 tokens).
Use a shorter input or enable --allow-auto-truncate.
```

- HTTP 状态码：**400 Bad Request**
- `set_finish_with_abort()` 将 `origin_input_ids` 缩为 `[0]`（仅1个 token）
- 请求走一次极轻量的 prefill forward pass 后返回错误
- **GPU 资源消耗极少**

**开启了 `--allow-auto-truncate`**：

```
WARNING: Request length is longer than the KV cache pool size or the max context length.
Truncated. len(req.origin_input_ids)=25000, max_req_input_len=20000.
```

- 输入被截断到 `max_req_input_len`
- 请求继续正常处理

---

### 场景4：都没超过但 decode 时 GPU OOM

**没有拦截**。这种情况发生在：
- 输入通过了所有验证
- 但在 decode 阶段，随着生成 token 增多，KV cache 膨胀超过 GPU 显存

**错误表现**：
- CUDA OOM 异常
- 可能导致进程崩溃
- **可能导致 GPU 内存泄露**（如之前报告分析）

---

## 四、完整错误处理流程图

```
请求到达 HTTP Server
  │
  ▼
TokenizerManager._tokenize_one_request()
  │  tokenize 输入文本 → input_ids
  │
  ▼
TokenizerManager._validate_one_request()
  │
  ├── input_tokens >= context_length?
  │     ├── 无 --allow-auto-truncate → ValueError → HTTP 400 ← 不消耗GPU
  │     └── 有 --allow-auto-truncate → 截断 input_ids → 继续
  │
  ├── (input_tokens + max_new_tokens) >= context_length?
  │     ├── 无 --allow-auto-truncate → ValueError → HTTP 400 ← 不消耗GPU
  │     └── 有 --allow-auto-truncate → 缩减 max_new_tokens → 继续
  │
  └── 验证通过 → 发送到 Scheduler
                    │
                    ▼
              Scheduler.validate_input_length()
                    │
                    ├── input_tokens >= max_req_input_len?
                    │     ├── 无 --allow-auto-truncate → abort（消耗极少GPU）
                    │     └── 有 --allow-auto-truncate → 截断 → 继续
                    │
                    └── 验证通过 → Prefill → Decode → 返回结果
                                          │
                                          └── 可能 OOM → 进程崩溃（无保护）
```

---

## 五、参数之间的关系图

```
GPU 总显存
  │
  × mem-fraction-static (默认 0.9)
  │
  ├── 模型权重
  └── KV Cache 池
        │
        └── max_total_num_tokens
              │
              ├── --max-total-tokens (显式设置, 可选)
              └── 自动计算 (默认)
                    │
                    ▼
              max_req_input_len = min(max_total_num_tokens, context_len) - 5
                                          │
                                          └── --context-length (显式设置, 可选)
                                                │
                                                └── 模型 config.json (默认)
```

---

## 六、GLM-4.7-Flash-30B-A3B 7P1D 场景示例

假设 `--context-length 32768`，8卡 TP，单卡 80GB HBM：

| 长度限制 | 典型值 | 由什么决定 |
|---------|--------|----------|
| `context_length` | 32768 | `--context-length` 或模型默认 |
| `max_total_num_tokens` | ~180000 | GPU 显存自动计算 |
| `max_req_input_len` | ~32763 | `min(180000, 32768) - 5` |

**用户输入 36K tokens 时**：

```
36000 >= 32768 (context_length)
  → 第1层拦截
  → ValueError: "The input (36000 tokens) is longer than the model's context length (32768 tokens)."
  → HTTP 400，不消耗 GPU
```

**用户输入 30K tokens + max_new_tokens=4000 时**：

```
30000 + 4000 = 34000 >= 32768 (context_length)
  → 第2层拦截
  → ValueError: "Requested token count exceeds the model's maximum context length..."
  → HTTP 400，不消耗 GPU
```

**用户输入 30K tokens + max_new_tokens=2000 时**：

```
30200 < 32768 ✓
30000 < 32763 (max_req_input_len) ✓
  → 通过所有验证
  → 正常处理
```

---

## 七、生产环境建议

### 必须配置

```bash
--allow-auto-truncate    # 超长输入自动截断，避免返回错误
--context-length 32768   # 明确指定，避免使用模型默认值带来的不确定性
```

### 按需调优

| 目标 | 调整方式 |
|------|---------|
| 支持更长的输入 | 增大 `--context-length`（需模型支持） |
| 支持更多并发 | 增大 `--mem-fraction-static` 或增加 GPU |
| 增大 KV cache 池 | 增大 `--max-total-tokens`（显式设置） |
| 减少 OOM 风险 | 减小 `--mem-fraction-static`（更保守） |

### 监控建议

| 监控指标 | 来源 | 告警阈值 |
|---------|------|---------|
| 启动日志中的 `max_req_input_len` | scheduler init info | 接近 `context_length` 的 80% 时关注 |
| KV cache 池使用率 | Prometheus metrics | > 85% 时告警 |
| 截断请求比例 | `--allow-auto-truncate` 日志 | > 1% 时检查输入分布 |
| CUDA OOM 频次 | 日志 | > 0 时告警 |

---

## 八、关键源码文件索引

| 文件 | 关键行号 | 功能 |
|------|---------|------|
| `server_args.py` | 304 | `context_length` 默认值 |
| `server_args.py` | 656 | `allow_auto_truncate` 默认值 |
| `server_args.py` | 4008-4012 | `--context-length` CLI 定义 |
| `server_args.py` | 5929-5931 | `--allow-auto-truncate` CLI 定义 |
| `model_config.py` | 391-420 | `_derive_context_length()` — context_length 推导与验证 |
| `tokenizer_manager.py` | 809-859 | `_validate_one_request()` — 第1、2层验证 |
| `scheduler.py` | 1981-1989 | 第3层验证入口 |
| `utils.py` | 113-143 | `validate_input_length()` — 第3层验证实现 |
| `tp_worker.py` | 304 | `max_req_input_len` 计算 |
| `schedule_batch.py` | 1285-1295 | `set_finish_with_abort()` — 超限请求处理 |
