# EvalScope 前缀相似度压测指南：构造丰富前缀模拟 90% 前缀相似度

> 本报告说明如何在使用 EvalScope（`evalscope perf`）进行推理性能压测时，构造**内容丰富、且请求间前缀相似度为 90%** 的测试负载，用于验证服务端 Prefix Caching（KV Cache 复用）的真实效果。

---

## 1. 背景与动机

### 1.1 什么是前缀相似度 / Prefix Cache

现代推理框架（vLLM、SGLang、LMDeploy、TensorRT-LLM 等）普遍支持 **Prefix Caching**（也称 Automatic Prefix Caching / Radix Tree）：当多个请求的 prompt **开头部分（前缀）的 token 序列完全相同**时，框架会复用这部分已计算的 KV Cache，从而：

- 显著降低 **TTFT**（Time To First Token）
- 降低 prefill 阶段的计算量与显存带宽占用

因此，压测 Prefix Cache 收益时，关键变量是 **请求之间共享的前缀长度占比**——即「前缀相似度」。

### 1.2 为什么需要「90% 相似度」而非 100%

| 场景 | 相似度 | 说明 |
|------|--------|------|
| 完全相同前缀 | 100% | 最理想命中；可测上限 |
| **部分重叠前缀** | **~90%** | **最贴近真实多会话场景**：共享系统提示 / RAG 文档，但各自有差异化开头 |
| 完全无关 | 0% | 基线，cache 完全不命中 |

真实业务里，多个会话往往共享一段较长的「系统提示 + 检索上下文」，但每个用户的首条提问不同。模拟 **90% 前缀相似度**，能比 100% 共享更真实地反映 cache 命中率、驱逐（eviction）行为和 TTFT 分布。

---

## 2. EvalScope 的前缀能力现状

EvalScope 的前缀压测逻辑位于 `evalscope/perf/plugin/datasets/random_dataset.py`，由 `--prefix-length N` 控制。其核心实现是：

```python
# random_dataset.py（节选）
self.prefix_ids = self.get_random_inputs(self.prefix_length)   # __init__ 中只生成一次
...
return self.prefix_ids + inner_seq                              # 每个请求 = 同一个 prefix + 各异的 inner_seq
```

官方文档 `docs/zh/user_guides/stress_test/examples.md` 明确写道：

> 在一次测试中所有请求 prefix 部分相同。

**结论：原生只支持「0% 共享」或「100% 共享」，不支持请求间部分重叠（如 90%）。** 要模拟 90%，需要自行构造请求间的公共前缀。

下文给出两种方案：
- **方案 A（推荐，零侵入）**：`custom` 数据集 + 预生成数据集
- **方案 B（进阶，改源码）**：扩展 `random` 插件，新增 `--prefix-share-ratio` 参数

---

## 3. 方案 A：`custom` 数据集 + 预生成数据集（推荐）

### 3.1 核心思路

每条 prompt 的结构：

```
[ 共享段 (90% token) ] [ 独有段 (10% token) ] [ 请求体 inner (各请求不同) ]
|<------- 公共前缀 ------>|
```

由于共享段在所有请求中**字节完全相同**，服务端 tokenize 后会得到**完全相同的公共 token 序列**，任意两条请求的最长公共前缀 ≈ 共享段长度 = 90%。

### 3.2 数据集生成器

`gen_prefix_dataset.py` 使用 `transformers` tokenizer 在 **token 级精确控制**相似度，并支持「单前缀」与「多 group 丰富前缀」两种模式：

```python
# gen_prefix_dataset.py
import argparse, random
from transformers import AutoTokenizer

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokenizer", required=True, help="如 Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--prefix-len", type=int, default=1024, help="每条 prompt 的前缀总长(token)")
    ap.add_argument("--similarity", type=float, default=0.90, help="前缀相似度, 0~1")
    ap.add_argument("--inner-len", type=int, default=512, help="非共享请求体长度(token)")
    ap.add_argument("--number", type=int, default=200, help="每个 group 的请求数")
    ap.add_argument("--groups", type=int, default=1, help="group 数, >1 时生成多个不同的丰富前缀")
    ap.add_argument("--corpus-file", default=None, help="可选: 富文本语料文件作为前缀来源")
    ap.add_argument("--out", default="prefix90.txt")
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.tokenizer)
    shared_len = int(args.prefix_len * args.similarity)          # 90% 公共前缀
    tail_len = args.prefix_len - shared_len                       # 10% 独有段

    # 多领域富文本(中英混合), 让前缀语义丰富; 也可用 --corpus-file 指定
    default_corpus = """
    【系统说明】你是一个专业助手。请根据给定上下文回答用户问题……(翻译/代码/法律/医疗/客服/RAG 等多领域说明, 尽量长一些)。
    The quick brown fox jumps over the lazy dog. In software engineering, a prefix cache stores
    the key-value tensors of already-computed tokens so that subsequent requests sharing the same
    prompt prefix can reuse them, dramatically reducing Time-To-First-Token (TTFT).
    """ * 20
    corpus_text = default_corpus if not args.corpus_file else open(args.corpus_file).read()

    all_ids = tok.encode(corpus_text, add_special_tokens=False)
    prompts = []
    for g in range(args.groups):
        start = (len(all_ids) // max(args.groups, 1)) * g        # 不同 group 取不同语料段 -> group 间低相似
        shared_ids = (all_ids[start:] + all_ids)[:shared_len]
        shared_text = tok.decode(shared_ids)                     # 同 group 内完全相同 -> 命中 prefix cache
        for _ in range(args.number):
            tail_ids = random.sample(range(1000, len(tok)), tail_len)
            inner_ids = random.sample(range(1000, len(tok)), args.inner_len)
            prompts.append(shared_text + "\n" + tok.decode(tail_ids) + tok.decode(inner_ids))

    random.shuffle(prompts)
    with open(args.out, "w") as f:
        f.write("\n".join(prompts))
    print(f"生成 {len(prompts)} 条, 每条共享前缀 {shared_len}/{args.prefix_len} = {args.similarity:.0%} -> {args.out}")

if __name__ == "__main__":
    main()
```

### 3.3 生成与压测命令

```bash
# 1) 生成数据集 (--groups 5 = 5 个不同领域的丰富前缀, group 内 90% 相似)
python gen_prefix_dataset.py \
  --tokenizer Qwen/Qwen2.5-0.5B-Instruct \
  --prefix-len 1024 --similarity 0.90 \
  --inner-len 512 --number 200 --groups 5 \
  --out prefix90.txt

# 2) 用 custom 数据集压测
evalscope perf \
  --dataset custom --dataset-path prefix90.txt \
  --model Qwen2.5-0.5B-Instruct \
  --url http://127.0.0.1:8801/v1/chat/completions \
  --api openai \
  --tokenizer-path Qwen/Qwen2.5-0.5B-Instruct \
  --min-tokens 128 --max-tokens 128 \
  --parallel 20 --read-timeout 120
```

### 3.4 关键注意点

1. **用 `\n` 分隔共享段与独有段**：避免 tokenizer 在拼接处做 BPE 合并，把「90% 公共前缀」末尾的 token 融合掉，导致公共前缀缩短。只要共享段文本字节完全相同，服务端 tokenize 出来的公共 token 序列就一致 → 命中。
2. **`--groups N` 让前缀更丰富**：N 个不同领域的前缀池，每组内部仍 90% 相似，更接近真实多会话、多租户场景，避免「所有请求指向同一段文本」的退化。
3. **长度精度**：用 `transformers` tokenizer 生成，长度有少量 round-trip 误差，但相似度结构（前 90% 共享）是精确的。如需服务端收到精确 token 数，可改用 token-id 形式（见方案 B 的 `--tokenize-prompt` 路径）。

---

## 4. 方案 B：扩展 `random` 插件，原生支持部分共享

若不想维护外部数据文件，可给 `RandomDatasetPlugin` 新增一个 `--prefix-share-ratio`（如 `0.9`）参数，把前缀拆成「固定段 + 每请求独有段」。

### 4.1 改动点

**`evalscope/perf/arguments.py`** 新增字段：

```python
prefix_share_ratio: float = 1.0
"""Ratio of the prefix shared across requests (0~1). 1.0 = all requests share
the identical prefix (default). <1.0 = only this fraction is shared, the rest
is unique per request, to simulate partial prefix overlap (e.g. 0.9 for 90% similarity)."""
```

并在 CLI parser 中注册 `--prefix-share-ratio`。

**`evalscope/perf/plugin/datasets/random_dataset.py`** 改造前缀构造：

```python
# __init__ 中:
shared_len = int(self.prefix_length * self.query_parameters.prefix_share_ratio)
self.shared_prefix = self.get_random_inputs(shared_len)
self.unique_prefix_len = self.prefix_length - shared_len

# generate_token_ids_only / generate_token_sequence 中, 把
#   self.prefix_ids + inner_seq
# 改为
unique_prefix = self.get_random_inputs(self.unique_prefix_len)
return self.shared_prefix + unique_prefix + inner_seq
```

### 4.2 使用

```bash
evalscope perf \
  --dataset random \
  --prefix-length 1024 \
  --prefix-share-ratio 0.90 \
  --tokenizer-path Qwen/Qwen2.5-0.5B-Instruct \
  --tokenize-prompt \
  ...
```

复用 random 数据集成熟的精确 token 控制（`--tokenize-prompt` 直接发 token id、byte-fallback token 过滤等）。

---

## 5. 如何验证效果（看哪些指标）

在 EvalScope 的压测报告（`outputs/` 下的 summary 与可视化）中关注：

| 指标 | 含义 | 90% 相似下的预期 |
|------|------|------------------|
| **Cache Hit (%)** | `cached_tokens / prompt_tokens` | 接近 90%（共享段被复用）|
| **TTFT (ms)** | 首 token 延迟 | 相比 0% 相似基线显著下降 |
| **Subseq. TTFT** | 多轮后续请求 TTFT | 进一步下降 |

对比三组实验即可量化 Prefix Cache 收益：

1. **Baseline（0% 相似）**：`--prefix-length 0` 或 `--groups` 足够多使前缀互不相同。
2. **90% 相似（真实场景）**：本报告方案。
3. **100% 相似（上限）**：`--prefix-length 1024` 不加 share-ratio，或 `--similarity 1.0`。

---

## 6. 决策建议

| 需求 | 推荐方案 |
|------|----------|
| 快速验证、不改源码、需要丰富的多领域前缀 | **方案 A** |
| 希望像 `--prefix-length` 一样参数化、可复用、可进主仓库 | **方案 B** |
| 一次性实验 | 方案 A 即可 |

---

## 7. 参考资料

- 代码：`evalscope/perf/plugin/datasets/random_dataset.py`
- 参数：`evalscope/perf/arguments.py`（`prefix_length` 字段）
- 文档：`docs/zh/user_guides/stress_test/examples.md`（random 数据集）、`docs/zh/user_guides/stress_test/quick_start.md`（Cache Hit 指标说明）

---

*本报告由 EvalScope 仓库内的源码分析整理而成，命令与代码片段均基于当前 `main` 分支。*
