# vLLM 推理框架部署参数全集（中文参考）

> 本文档基于本地 vLLM 源码（`vllm/engine/arg_utils.py`、`vllm/config/*.py`、`vllm/entrypoints/openai/cli_args.py`）逐项核对，覆盖 `vllm serve` / `-m vllm.entrypoints.openai.api_server` 暴露的全部 CLI 参数。每项给出 **CLI 标志 / 默认值 / 含义**。
>
> 阅读约定：
> - 「默认」列中的 `<factory>` 表示该值由工厂函数动态生成（多为空容器/空字典），「无」表示默认 `None`。
> - 布尔类参数通常自动支持 `--flag` 与 `--no-flag` 两种写法。
> - 也可用 YAML 配置文件统一管理：`vllm serve <model> --config serve.yaml`。

---

## 0. 如何启动

```bash
# 在线服务（OpenAI 兼容 HTTP API）
vllm serve <model_tag> [选项]

# 等价写法
python -m vllm.entrypoints.openai.api_server --model <model> [选项]

# 离线批量推理：在 Python 里用 LLM(...) 构造，参数与下面的 EngineArgs 同名
```

参数在代码中按 **Config 数据类** 分组（与 `--help` 的分组完全一致）。下文按分组逐一说明。

---

## 1. 模型参数（ModelConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--model` | `Qwen/Qwen3-0.6B` | HuggingFace 模型名或本地路径。未设 `--served-model-name` 时也作为对外暴露的模型名与 Prometheus 指标的 `model_name` 标签。`serve` 子命令中由位置参数 `model_tag` 给出。 |
| `--runner` | `auto` | 模型运行器类型（generate / pooling / encode 等）。一个实例仅支持一种。 |
| `--convert` | `auto` | 用 adapter 转换模型用途，常见是把文本生成模型转用于 pooling（嵌入/打分）任务。 |
| `--tokenizer` | 无 | tokenizer 名称或路径；不指定则与 `--model` 相同。 |
| `--tokenizer-mode` | `auto` | tokenizer 模式：`auto`（Mistral 模型优先用 mistral_common，否则 HF）/`hf`（优先 fast）/`slow`（强制慢速）/`mistral`/`deepseek_v32`/`deepseek_v4`。 |
| `--trust-remote-code` | `False` | 是否信任并执行来自 HF 的远程代码（自定义建模代码）。**有安全风险，仅在可信模型时开启。** |
| `--dtype` | `auto` | 权重与激活数据类型：`auto`（FP32/FP16→FP16，BF16→BF16）/`half`=`float16`（AWQ 推荐）/`bfloat16`/`float`=`float32`。 |
| `--seed` | `0` | 随机种子，保证多 TP worker 采样一致、结果可复现。 |
| `--hf-config-path` | 无 | 显式指定 HF config 文件路径；不指定则取 `--model` 路径。 |
| `--revision` | 无 | 模型版本（分支名/tag/commit id）。 |
| `--code-revision` | 无 | 模型代码版本（分支名/tag/commit id）。 |
| `--tokenizer-revision` | 无 | tokenizer 版本（分支名/tag/commit id）。 |
| `--max-model-len` | 无 | 模型上下文长度（prompt + 输出）。未设则自动从模型 config 推导。支持人类可读写法：`1k`=1000、`1K`=1024、`25.6k`=25600；`-1`/`auto`=按显存自动选最大可行长度。 |
| `--quantization` / `-q` | 无 | 权重量化方法（如 `awq`/`gptq`/`fp8`/`bitsandbytes`…）。为空时先读模型 config 里的 `quantization_config`。 |
| `--quantization-config` | 无 | 用户级量化配置（按层类型 linear/moe 细分 + 忽略模式）。 |
| `--allow-deprecated-quantization` | `False` | 是否允许已弃用的量化方法。 |
| `--enforce-eager` | `False` | 是否强制 eager 模式（关闭 CUDA Graph）。调试或避免捕获失败时用；关掉性能更好。 |
| `--max-logprobs` | `20` | 一次返回的最大 logprobs 数量（OpenAI 默认 20）。`-1`=无上限，可能 OOM。 |
| `--logprobs-mode` | `raw_logprobs` | logprobs 内容模式：`raw_logprobs`/`processed_logprobs`/`raw_logits`/`processed_logits`（raw=logit 处理前，processed=应用 temperature/top-k/top-p 后）。 |
| `--disable-sliding-window` | `False` | 关闭滑动窗口注意力（模型不支持时此项被忽略）。 |
| `--disable-cascade-attn` | `True` | 关闭级联注意力。默认 True，需手动 `--no-disable-cascade-attn` 开启（开启后仅在启发式判定有益时才启用）。 |
| `--skip-tokenizer-init` | `False` | 跳过 tokenizer/detokenizer 初始化；输入需直接给 `prompt_token_ids`，输出为 token id。 |
| `--served-model-name` | 无 | API 对外使用的模型名（可多个）。响应中的 model 字段取第一个；多个名都会响应。 |
| `--config-format` | `auto` | 加载模型 config 的格式：`auto`/`hf`/`mistral`。 |
| `--hf-token` | 无 | 访问远程模型文件的 HTTP Bearer Token；`True` 则用 `hf auth login` 的 token。 |
| `--hf-overrides` | 无 | 覆盖/更新 HF config，可传字典或可调用对象。 |
| `--generation-config` | `auto` | 生成 config 来源：`auto`（从模型路径加载）/`vllm`（不加载、用 vLLM 默认）/文件夹路径。若 config 含 `max_new_tokens` 则成为全服务输出上限。 |
| `--override-generation-config` | 无 | 覆盖生成参数，如 `{"temperature":0.5}`；与 `auto` 合并，与 `vllm` 则只保留覆盖项。 |
| `--enable-sleep-mode` | `False` | 引擎睡眠模式（仅 cuda/hip 平台）。睡眠可释放显存给其他进程。 |
| `--enable-cumem-allocator` | `False` | 启用自定义 cumem 分配器（支持多节点 NVLink 等高级显存特性）。睡眠模式会自动启用它。 |
| `--model-impl` | `auto` | 模型实现来源：`auto`（优先 vLLM，回退 Transformers）/`vllm`/`transformers`/`terratorch`。 |
| `--override-attention-dtype` | 无 | 覆盖注意力的数据类型。 |
| `--logits-processors` | 无 | 一个或多个 logits processor 的全限定类名或类定义。 |
| `--pooler-config` | 无 | pooling 模型的输出池化行为配置。 |
| `--allowed-local-media-path` | （空） | 允许 API 读取本地图/视频的目录。**安全风险，仅在可信环境开启。** |
| `--allowed-media-domains` | 无 | 仅允许来自这些域名的多模态媒体 URL。 |

---

## 2. 模型加载参数（LoadConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--load-format` | `auto` | 权重加载格式：`auto`/`pt`/`safetensors`/`npcache`/`dummy`（随机权重，仅用于 profiling）/`tensorizer`/`sharded_state`（预分片 checkpoint，适合 TP）/`gguf`/`mistral`/`bitsandbytes`/`runai_streamer(_sharded)`/`instanttensor`/`modelexpress`。 |
| `--download-dir` | 无 | 权重下载目录，默认用 HF 缓存目录。 |
| `--safetensors-load-strategy` | 无 | safetensors 加载策略：无（mmap 懒加载，NFS 自动预取）/`lazy`/`eager`（整文件读入 CPU，适合网络存储）/`prefetch`（预读入 OS page cache）/`torchao`。 |
| `--safetensors-prefetch-num-threads` | 默认值 | 预取 safetensors checkpoint 的线程数。 |
| `--safetensors-prefetch-block-size` | 默认值 | 预取时每次读取的字节块大小。 |
| `--model-loader-extra-config` | 无 | 传给所选 loader 的额外配置（字典）。 |
| `--ignore-patterns` | `original/**/*` | 加载时要忽略的文件模式列表。 |
| `--use-tqdm-on-load` | `True` | 加载权重时是否显示进度条。 |
| `--pt-load-map-location` | `cpu` | pytorch checkpoint 的 `map_location`，如 `{"":"cuda"}` 或 `{"cuda:1":"cuda:0"}`。 |

---

## 3. 注意力参数（AttentionConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--attention-backend` | 无 | 注意力后端。`auto`/`None`=自动选择（如 `FLASH_ATTN`、`FLASHINFER` 等）。 |

> 说明：AttentionConfig 内部还有 `flash_attn_version`、`use_prefill_decode_attention`、`mla_prefill_backend`、`disable_flashinfer_q_quantization`、flex-attn 分块大小等字段，但只有 `--attention-backend` 作为常规 CLI 暴露；其余通过 `--attention-config`（`-ac`）JSON 配置。

---

## 4. Mamba / SSM 参数（MambaConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--mamba-backend` | `triton` | Mamba/SSU 后端。 |
| `--enable-mamba-cache-stochastic-rounding` | `False` | 将 SSM 状态写入 fp16 缓存时启用随机舍入，提升长序列数值稳定性。 |
| `--mamba-cache-philox-rounds` | `0` | 随机舍入的 Philox PRNG 轮数（0=Triton 默认；越大随机性越好、算力开销越大）。 |

---

## 5. 结构化输出与推理解析参数（StructuredOutputsConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--reasoning-parser` | （空） | 推理内容解析器（按模型选择），把 reasoning content 解析成 OpenAI 格式。 |
| `--reasoning-parser-plugin` | （空） | 可动态加载注册的推理解析器插件路径。 |

> 结构化输出后端（JSON schema/regex 等）默认 `auto`，可通过 `--structured-outputs-config` 配置；相关内部字段 `disable_any_whitespace`、`disable_additional_properties`、`enable_in_reasoning` 亦通过该 JSON 设置。

---

## 6. 并行参数（ParallelConfig）

> 部署中最重要的一组参数，控制张量/流水线/数据/专家并行与多机通信。

### 6.1 基础并行度

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--tensor-parallel-size` / `-tp` | `1` | 张量并行（TP）组数（同层权重切分到多卡）。 |
| `--pipeline-parallel-size` / `-pp` | `1` | 流水线并行（PP）组数（按层切分到多卡）。 |
| `--data-parallel-size` / `-dp` | `1` | 数据并行（DP）组数。MoE 层会按 TP×DP 切分。 |
| `--distributed-executor-backend` | 无 | 分布式执行后端：`mp`（多进程，单机优先）/`ray`（多机/TPU 必需）。TP×PP ≤ 本机 GPU 数时默认 `mp`。 |
| `--max-parallel-loading-workers` | 无 | TP 加载大模型时分批并行加载的最大 worker 数，避免主机内存 OOM。 |
| `--disable-custom-all-reduce` | `False` | 禁用自定义 all-reduce 内核，回退到 NCCL。 |

### 6.2 多机（mp 后端）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--master-addr` | `127.0.0.1` | mp 多机推理的主节点地址。 |
| `--master-port` | `29501` | mp 多机推理的主节点端口。 |
| `--nnodes` / `-n` | `1` | 节点数（mp 后端）。 |
| `--node-rank` / `-r` | `0` | 当前节点 rank（mp 后端）。 |
| `--distributed-timeout-seconds` | 无 | 分布式操作（如 init_process_group）超时秒数；None 用 PyTorch 默认（NCCL 600s）。多机下载慢时可调大。 |
| `--cpu-distributed-timeout-seconds` | 无 | CPU（gloo）通信组超时秒数；None 用默认（gloo 1800s）。 |

### 6.3 NUMA 绑定

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--numa-bind` | `False` | 为 GPU worker 子进程启用 NUMA 绑定（默认绑定到 GPU 本地 NUMA）。 |
| `--numa-bind-nodes` | 无 | 每块 GPU 绑定的 NUMA 节点列表，如 `[0,0,1,1]`。 |
| `--numa-bind-cpus` | 无 | 每块 GPU 绑定的 CPU 列表，如 `["0-3","4-7"]`（走 `numactl --physcpubind`）。 |

### 6.4 数据并行（细粒度，多用于在线 MoE）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--data-parallel-size-local` / `-dpl` | `1` | 本节点运行的 DP 副本数。 |
| `--data-parallel-rank` / `-dpn` | — | 本实例的 DP rank。显式设置后启用 **外部负载均衡模式**（MoE DP 部署用）。 |
| `--data-parallel-start-rank` / `-dpr` | — | 次级节点的起始 DP rank。 |
| `--data-parallel-address` / `-dpa` | — | DP 集群 head 节点地址。 |
| `--data-parallel-rpc-port` / `-dpp` | — | DP RPC 通信端口。 |
| `--data-parallel-backend` / `-dpb` | `mp` | DP 后端：`mp` 或 `ray`。 |
| `--data-parallel-external-lb` / `-dpe` | `False` | 外部 LB 模式（K8s 每 rank 一个 pod 的 wide-EP），仅 MoE。 |
| `--data-parallel-hybrid-lb` / `-dph` | `False` | 混合 LB：节点内 vLLM 自行 LB，节点间由外部 LB。 |
| `--data-parallel-multi-port-external-lb` / `-dpm` | `False` | 每个本地 DP rank 各启一个 external-LB API server，supervisor 汇聚健康检查。 |

### 6.5 MoE 专家并行

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--enable-expert-parallel` / `-ep` | `False` | 对 MoE 层使用专家并行（而非张量并行）。 |
| `--enable-ep-weight-filter` | `False` | EP 激活时，每 rank 只读自己的专家权重，大幅减少 MoE 加载 I/O（DeepSeek/Mixtral/Kimi 等）。 |
| `--all2all-backend` | `allgather_reducescatter` | MoE EP 通信的 all2all 后端：`allgather_reducescatter`/`deepep_high_throughput`/`deepep_low_latency`/`mori_*`/`nixl_ep`/`flashinfer_nvlink_*`。 |
| `--enable-dbo` | `False` | 启用 Dual Batch Overlap（双批重叠）。 |
| `--ubatch-size` | `0` | ubatch（微批）大小。 |
| `--dbo-decode-token-threshold` | `32` | DBO 对纯 decode 批的 token 阈值，超过则分微批。 |
| `--dbo-prefill-token-threshold` | `512` | DBO 对含 prefill 批的 token 阈值。 |
| `--enable-elastic-ep` | `False` | 用无状态 NCCL 组启用弹性专家并行（DP/EP）。 |
| `--disable-nccl-for-dp-synchronization` | 无 | DP 同步改用 Gloo（async scheduling 时默认 True）。 |
| `--enable-eplb` | `False` | 启用 MoE 专家并行负载均衡（EPLB）。 |
| `--eplb-config` | 无 | EPLB 配置（字典）。 |
| `--expert-placement-strategy` | `linear` | 专家放置策略：`linear`（连续）或 `round_robin`（轮询，利于无冗余专家模型的负载均衡）。 |

### 6.6 上下文并行（CP / DCP / PCP）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--decode-context-parallel-size` / `-dcp` | `1` | decode 阶段上下文并行组数（复用 TP 卡，需 TP 整除 DCP）。 |
| `--dcp-comm-backend` | `ag_rs` | DCP 通信后端：`ag_rs`（AllGather+ReduceScatter）/`a2a`（All2All，MLA 模型每层 NCCL 从 3 降到 2）。 |
| `--dcp-kv-cache-interleave-size` | `1` | DCP 的 KV cache 交错大小（已由 `cp-kv-cache-interleave-size` 取代）。 |
| `--cp-kv-cache-interleave-size` | `1` | DCP/PCP 的 KV cache 交错大小（1=token 级对齐；=block_size=块级对齐）。 |
| `--prefill-context-parallel-size` / `-pcp` | `1` | prefill 阶段上下文并行组数。 |

### 6.7 Worker 相关

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--worker-cls` | `auto` | worker 类全名；`auto` 由平台决定。 |
| `--worker-extension-cls` | （空） | worker 扩展类（动态继承注入），用于 `collective_rpc` 注入新方法。 |
| `--ray-workers-use-nsight` | `False` | 是否用 nsight 分析 Ray worker。 |

---

## 7. KV 缓存参数（CacheConfig）

> 直接决定能并发多少请求、多少 token。

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--block-size` | 无（实际 16） | KV cache 连续块大小（每块 token 数）。常见 16/32/64。 |
| `--gpu-memory-utilization` | `0.92` | 单实例可占用的 GPU 显存比例（0~1）。同一卡多实例时需各自调小（如各 0.5）。 |
| `--kv-cache-memory-bytes` | 无 | 每卡 KV cache 字节数。设置后**忽略** `gpu-memory-utilization`，更精细地控制显存。 |
| `--kv-cache-dtype` | `auto` | KV cache 存储精度：`auto`（同模型精度）/`fp8`(=`fp8_e4m3`)/`fp8_e5m2`/`bfloat16`。可省显存。 |
| `--num-gpu-blocks-override` | 无 | 直接指定 KV cache 块数（覆盖 profiling 结果，多用于测试/抢占）。 |
| `--enable-prefix-caching` | `True` | 是否启用前缀缓存（重复前缀复用 KV，命中即免重算）。V1 默认开。 |
| `--prefix-caching-hash-algo` | `sha256` | 前缀缓存哈希算法：`sha256`/`sha256_cbor`/`xxhash`/`xxhash_cbor`（后者更快但非密码学安全，多租户慎用）。 |
| `--calculate-kv-scales` | `False` | （已弃用，0.19 移除）fp8 KV cache 时动态计算 k_scale/v_scale。 |
| `--kv-cache-dtype-skip-layers` | 无 | 跳过 KV 量化的层（按层索引或注意力类型名，如 `sliding_window`）。 |
| `--kv-sharing-fast-prefill` | `False` | KV 共享模型（如 YOCO、Gemma3n）跳过部分 prefill token 的实验性优化。 |
| `--mamba-cache-dtype` | `auto` | Mamba 缓存（conv+ssm）精度。 |
| `--mamba-ssm-cache-dtype` | `auto` | Mamba ssm state 精度（conv 仍由 mamba-cache-dtype 控制）。 |
| `--mamba-block-size` | 无 | Mamba cache 块大小（须为 8 的倍数）。 |
| `--mamba-cache-mode` | `none` | Mamba 缓存策略：`none`/`all`（缓存块边界处所有 token 状态）/`align`（仅缓存每步最后一个 token）。 |
| `--kv-offloading-size` | 无 | KV cache 卸载到 CPU 的缓冲大小（GiB，TP>1 时为各 rank 之和）。设置即开启卸载。 |
| `--kv-offloading-backend` | `native` | KV 卸载后端：`native` / `lmcache`。 |

---

## 8. 权重卸载参数（OffloadConfig）

> 当权重放不下显存时，用 CPU 内存承载部分权重。

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--offload-backend` | `auto` | 权重卸载后端：`auto`（按子配置自动选）/`uva`（统一虚拟地址零拷贝）/`prefetch`（异步预取分组卸载）。 |
| `--cpu-offload-gb` | `0` | 每卡卸载到 CPU 的显存大小（GiB）。0=不卸载。可理解为「虚拟放大显存」（24G 卡+10 → 当 34G 用）。需快速 CPU-GPU 互联。 |
| `--cpu-offload-params` | 无 | 选择性卸载的参数名片段集合（按段精确匹配，如 `experts` 匹配 `mlp.experts.w2_weight` 但不匹配 `expert`）。 |
| `--offload-group-size` | `0` | 每 N 层一组进行分组卸载（0=禁用）。例 size=8,num_in_group=2 卸载 6,7,14,15…。 |
| `--offload-num-in-group` | `1` | 每组卸载的层数（≤ group_size）。 |
| `--offload-prefetch-step` | `1` | 预取提前的层数（越大越能隐藏延迟，但更费显存）。 |
| `--offload-params` | 无 | 预取卸载的参数名片段集合。 |

---

## 9. 多模态参数（MultiModalConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--language-model-only` | `False` | 关闭所有多模态输入（各模态上限置 0）。 |
| `--limit-mm-per-prompt` | 每模态 999 | 每条 prompt 各模态最大输入项数/选项。如 `{"image":16,"video":{"count":1,"num_frames":32,"width":512,"height":512}}`。 |
| `--enable-mm-embeds` | `False` | 允许传入多模态嵌入（tensor / chat 里 `*_embeds` 类型）。**形状错误可能崩溃，仅可信用户。** |
| `--media-io-kwargs` | 无 | 按模态传递的媒体处理参数，如 `{"video":{"num_frames":40}}`。 |
| `--mm-processor-kwargs` | 无 | 转发给模型 processor（如 image processor）的参数覆盖。 |
| `--mm-processor-cache-gb` | `4` | 多模态 processor 缓存大小（GiB）。每 API 进程与每 engine core 进程各一份（总量 ×(api_server_count+dp_size)）。0=禁用。 |
| `--mm-processor-cache-type` | `lru` | processor 缓存类型：`lru`（镜像 LRU）/`shm`（共享内存 FIFO）。 |
| `--mm-shm-cache-max-object-size-mb` | `128` | shm 缓存单对象大小上限（MiB）。 |
| `--mm-encoder-only` | `False` | 仅运行多模态编码器、跳过语言模型（用于分离式 encoder 进程）。 |
| `--mm-encoder-tp-mode` | `weights` | 编码器 TP 优化方式：`weights`（切权重）/`data`（切 batch 数据，各 rank 持全权重）。 |
| `--mm-encoder-attn-backend` | 无 | 覆盖 ViT 编码器注意力后端（如 `FLASH_ATTN`）。 |
| `--mm-encoder-attn-dtype` | 无 | ViT 编码器注意力精度覆盖；设 `fp8` 走 FlashInfer cuDNN 量化。 |
| `--mm-encoder-fp8-scale-path` | 无 | ViT FP8 Q/K/V scale 的 JSON 文件路径（提供=静态 scale，否则动态）。 |
| `--mm-encoder-fp8-scale-save-path` | 无 | 动态 scale 校准完成后保存到该路径，供下次静态使用。 |
| `--mm-encoder-fp8-scale-save-margin` | `1.5` | 自动保存 scale 的安全余量（>1 留出余量防溢出）。 |
| `--interleave-mm-strings` | `False` | 在 `--chat-template-content-format=string` 下启用多模态完全交错支持。 |
| `--skip-mm-profiling` | `False` | 跳过多模态显存 profiling（仅 profile 语言骨干），加快启动但需自行估算编码器峰值显存。 |
| `--video-pruning-rate` | 无 | 视频剪枝率 [0,1)，按比例裁剪各视频的媒体 token。 |
| `--mm-tensor-ipc` | `direct_rpc` | 多模态张量进程间通信：`direct_rpc`（msgspec 序列化）/`torch_shm`（零拷贝共享内存）。 |

---

## 10. LoRA 参数（LoRAConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--enable-lora` | `False` | 是否支持 LoRA adapter 处理。 |
| `--max-loras` | `1` | 单批中 LoRA 最大数量。 |
| `--max-lora-rank` | `16` | 最大 LoRA rank。 |
| `--lora-dtype` | `auto` | LoRA 精度；`auto` 同基础模型精度。 |
| `--max-cpu-loras` | 无 | CPU 内存中最多存放的 LoRA 数（≥ max_loras）。 |
| `--fully-sharded-loras` | `False` | LoRA 计算完全分片；高序列/高 rank/高 TP 时通常更快。 |
| `--lora-target-modules` | 无 | 限定应用 LoRA 的模块后缀，如 `["o_proj","qkv_proj"]`。 |
| `--default-mm-loras` | 无 | 模态→LoRA 路径映射（多模态模型专用）。 |
| `--enable-tower-connector-lora` | `False` | 启用多模态 tower/connector 的 LoRA（实验性，部分 MM 模型如 Qwen VL）。 |
| `--specialize-active-lora` | `False` | 按活动 LoRA 数量分别捕获 CUDA Graph（幂次到 max_loras），提升变长场景性能，代价是启动更慢更费显存。 |
| `--enable-mixed-moe-lora-format` | `False` | 强制 MoE LoRA 用统一 2D 包装，使 2D/3D 格式 MoE LoRA 可同仓部署。 |

---

## 11. 可观测性参数（ObservabilityConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--show-hidden-metrics-for-version` | 无 | 启用自某版本起被隐藏的已弃用 Prometheus 指标（迁移期逃生口）。 |
| `--otlp-traces-endpoint` | 无 | OpenTelemetry trace 上报目标 URL。 |
| `--collect-detailed-traces` | 无 | 在设了 OTLP endpoint 时，为指定模块采集详细 trace（有性能开销）。 |
| `--kv-cache-metrics` | `False` | 启用 KV cache 驻留指标（生命周期/空闲/复用间隔），采样以降开销。需开启 log stats。 |
| `--kv-cache-metrics-sample` | `0.01` | KV cache 指标采样率 (0,1]，默认 1%。 |
| `--cudagraph-metrics` | `False` | 启用 CUDA Graph 指标（填充/未填充 token 数、派发模式与频率）。 |
| `--enable-layerwise-nvtx-tracing` | `False` | 启用逐层 NVTX trace（与 CUDA Graph 不兼容）。 |
| `--enable-mfu-metrics` | `False` | 启用 MFU（Model FLOPs Utilization）指标。 |
| `--enable-logging-iteration-details` | `False` | EngineCore 详细记录每次迭代的请求/token 数与 CPU 耗时。 |

---

## 12. 调度器参数（SchedulerConfig）

> 控制吞吐、延迟与并发，部署调优的核心。

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--max-num-batched-tokens` | `2048` | 单次迭代最多处理的 token 数（含 prefill+decode）。**吞吐与显存占用的关键旋钮。** |
| `--max-num-seqs` | `128` | 单次迭代最大并发序列数。 |
| `--max-num-partial-prefills` | `1` | chunked prefill 下可同时部分预填充的最大序列数。 |
| `--max-long-partial-prefills` | `1` | 超过 `long-prefill-token-threshold` 的长 prompt 可同时预填充数（< 前者可让短 prompt 插队，降延迟）。 |
| `--long-prefill-token-threshold` | `0` | 判定「长 prompt」的 token 阈值。 |
| `--scheduling-policy` | `fcfs` | 调度策略：`fcfs`（先到先服务）/`priority`（按优先级，值小优先，并列看到达时间）。 |
| `--enable-chunked-prefill` | `True` | 是否允许 prefill 分块（按剩余 `max_num_batched_tokens` 切分），让 prefill 与 decode 同批。**V1 默认开。** |
| `--disable-chunked-mm-input` | `False` | 开启后多模态输入项不会被部分调度（避免半张图的情况）。 |
| `--scheduler-cls` | 无 | 自定义 scheduler 类（默认 `vllm.v1.core.sched.scheduler.Scheduler`）。 |
| `--scheduler-reserve-full-isl` | `True` | 准入请求时检查**完整**输入序列是否放得下（而非仅首块），避免 chunked prefill 下过度准入导致 KV 抖动。 |
| `--watermark` | `0.0` | KV cache 预留空闲块比例 [0,1)。>0 时留出余量减少抢占；0（默认）禁用。 |
| `--disable-hybrid-kv-cache-manager` | 无 | 为 True 时对混合注意力层（full+sliding）也分配相同大小 KV cache。 |
| `--async-scheduling` | 无 | 是否启用异步调度（减少 GPU 空闲、改善延迟与吞吐）。 |
| `--stream-interval` | `1` | 流式输出间隔（token 数）。1=逐 token 立即发（更平滑）；更大=攒批减少主机开销。 |

---

## 13. 编译参数（CompilationConfig）

> 通过 `-cc.<key>=<value>` 简写设置，如 `-cc.mode=3`。

| 字段（`-cc.` 前缀） | 默认 | 含义 |
|---|---|---|
| `mode` | 无（V1 默认 3） | 编译模式：`0`=NONE（全 eager）/`1`=标准 torch.compile/`2`=DYNAMO_TRACE_ONCE/`3`=VLLM_COMPILE（自定义 Inductor 后端+分段编译+shape 特化）。 |
| `cudagraph-capture-sizes` | 无 | CUDA Graph 捕获的尺寸列表，如 `[1,2,4,8]`。 |
| `max-cudagraph-capture-size` | 无 | CUDA Graph 最大捕获尺寸；不指定则按 `min(max_num_seqs*2,512)` 并按 `[1,2,4]+range(8,256,8)+...` 自动生成。 |
| `cudagraph_mode` | 无（默认 FULL_AND_PIECEWISE） | CUDA Graph 模式：`NONE`/`PIECEWISE`/`FULL`/`FULL_DECODE_ONLY`/`FULL_AND_PIECEWISE`。 |
| `backend` | （空） | 编译后端：空=默认（`inductor`）/`eager`/`openxla`/全限定名。 |
| `custom_ops` | 无 | 精细开关自定义算子：`all`/`none`/`+op`/`-op`，如 `all,-op1`。 |
| `splitting_ops` | 无 | 分段编译的切分算子；空列表=不切（适合全图 CUDA Graph）。 |

> 其余字段（`cache_dir`、`inductor_compile_config`、`inductor_passes`、`use_inductor_graph_partition`、`compile_sizes`、`compile_mm_encoder`、`cudagraph_mm_encoder`、`encoder_cudagraph_token_budgets`、`fast_moe_cold_start` 等）均通过 `-cc` JSON 配置。

---

## 14. 内核参数（KernelConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--ir-op-priority` | 平台默认 | vLLM IR 算子在 forward 中的分发/降级优先级。 |
| `--enable-flashinfer-autotune` | 无 | kernel warmup 时跑 FlashInfer 自动调优。 |
| `--moe-backend` | `auto` | MoE 专家计算内核：`auto`/`triton`/`deep_gemm`/`deep_gemm_mega_moe`/`cutlass`/`flashinfer_trtllm`/`flashinfer_cutlass`/`flashinfer_cutedsl`/`marlin`/`humming`/`aiter`(ROCm)/`emulation` 等。 |
| `--linear-backend` | `auto` | 量化线性 GEMM 内核：`auto`/`cutlass`/`marlin`/`triton`/`deep_gemm`/`torch`/`flashinfer_*`/`machete`/`fbgemm`/`conch`/`exllama`/`aiter`(ROCm)/`emulation` 等。 |

---

## 15. vLLM 全局 / 高级参数（VllmConfig）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--speculative-config` / `-sc` | 无 | 投机解码配置（JSON）。见下「投机解码」子表。 |
| `--diffusion-config` / `-dc` | 无 | 扩散式 LLM（dLLM）配置。 |
| `--kv-transfer-config` | 无 | 分布式 KV cache 传输配置（P/D 分离、PD-disaggregation、Mooncake/NIXL 等后端）。 |
| `--kv-events-config` | 无 | KV cache 事件发布配置。 |
| `--ec-transfer-config` | 无 | 分布式 EC cache 传输配置。 |
| `--compilation-config` / `-cc` | 见 13 节 | 编译/CUDA Graph 配置（JSON 或 `-cc.k=v`）。 |
| `--attention-config` / `-ac` | 无 | 注意力后端等配置（JSON）。 |
| `--reasoning-config` | 无 | 推理模型配置。 |
| `--kernel-config` | 无 | 内核配置（JSON）。 |
| `--additional-config` | 无 | 平台扩展配置（不同平台支持不同项，内容需可哈希）。 |
| `--structured-outputs-config` | 无 | 结构化输出后端配置（JSON）。 |
| `--profiler-config` | 无 | 性能 profiling 配置。 |
| `--weight-transfer-config` | 无 | RL 训练时的权重传输配置。 |
| `--optimization-level` | `O2` | 优化级别 `-O0`~-`O3`：启动时间↔性能权衡，`-O0` 启动最快、`-O3` 性能最佳，默认 `-O2`。 |
| `--performance-mode` | `balanced` | 运行模式：`balanced`/`interactivity`（低延迟，细粒度 CUDA Graph）/`throughput`（高并发高吞吐，大 CUDA Graph、更激进批处理）。 |

### 投机解码（SpeculativeConfig，经 `-sc` 或 `--spec-*`）

| 标志 | 默认 | 含义 |
|---|---|---|
| `--spec-method` | 自动 | 投机方法（ngram / EAGLE / draft model 等），通常自动识别。 |
| `--spec-model` | 无 | 草稿模型 / eagle head / 额外权重名称。 |
| `--spec-tokens` | 无 | 每步投机 token 数（draft config 有则取之，否则必填）。 |
| `prompt_lookup_max/min` | — | ngram 方法下的 ngram 窗口最大/最小值。 |
| `draft_tensor_parallel_size` | 无 | 草稿模型 TP 度（仅 1 或与目标模型相同）。 |
| `parallel_drafting` | `False` | 并行起草（所有投机 token 并行生成，需模型支持，仅 EAGLE/draft model）。 |

> 投机解码还支持 `disable_padded_drafter_batch`、`use_local_argmax_reduction`、suffix-decoding 系列（`suffix_decoding_*`）、rejection/sample 方法（`rejection_sample_method`、`draft_sample_method`、`synthetic_acceptance_*`）等，按需以 JSON 配置。

---

## 16. 服务 / API 服务器参数（FrontendArgs，仅在线服务）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--host` | 无 | 监听主机名（如 `0.0.0.0`）。 |
| `--port` | `8000` | 监听端口。 |
| `--uds` | 无 | Unix 域 socket 路径；设置后忽略 host/port。 |
| `--uvicorn-log-level` | `info` | uvicorn 日志级别：critical/error/warning/info/debug/trace。 |
| `--disable-uvicorn-access-log` | `False` | 关闭 uvicorn 访问日志。 |
| `--disable-access-log-for-endpoints` | 无 | 不记录访问日志的端点路径（逗号分隔，如 `/health,/metrics`）。 |
| `--api-key` | 无 | 客户端须在 header 中出示的 API Key（可多个）。 |
| `--ssl-keyfile` / `--ssl-certfile` / `--ssl-ca-certs` | 无 | SSL 私钥/证书/CA 证书文件路径。 |
| `--enable-ssl-refresh` | `False` | 证书文件变化时刷新 SSL Context。 |
| `--ssl-cert-reqs` | `CERT_NONE` | 是否要求客户端证书。 |
| `--ssl-ciphers` | 无 | TLS 1.2 及以下密码套件。 |
| `--allow-credentials` | `False` | CORS 允许凭证。 |
| `--allowed-origins` / `--allowed-methods` / `--allowed-headers` | `["*"]` | CORS 允许的源/方法/头（JSON）。 |
| `--root-path` | 无 | 反向代理路径转发时的 FastAPI `root_path`。 |
| `--middleware` | `[]` | 额外 ASGI 中间件（可多次指定，传 import 路径）。 |
| `--enable-request-id-headers` | `False` | 响应加 `X-Request-Id` 头。 |
| `--disable-fastapi-docs` | `False` | 关闭 OpenAPI schema / Swagger / ReDoc。 |
| `--enable-offline-docs` | `False` | 离线 FastAPI 文档（气隙环境，用 vLLM 自带静态资源）。 |
| `--h11-max-incomplete-event-size` | 4 MiB | h11 解析器单条未完成 HTTP 事件最大字节数。 |
| `--h11-max-header-count` | 256 | h11 允许的最大 HTTP 头数量。 |
| `--enable-flash-late-interaction` | `True` | 在 GPU 上跑 pooling MaxSim，提升 late-interaction 打分性能。 |
| `--data-parallel-supervisor-port` | `9256` | 多端口 external LB 模式的汇聚健康检查 HTTP 端口。 |
| `--dp-supervisor-probe-*` | 见源码 | supervisor 探测间隔/超时/失败阈值（多端口 external LB）。 |

### 聊天模板与工具调用（前端参数）

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `--chat-template` | 无 | 聊天模板文件路径或单行模板。 |
| `--chat-template-content-format` | `auto` | 模板内消息内容渲染格式：`string` / `openai`（字典列表）。 |
| `--trust-request-chat-template` | `False` | 是否信任请求中自带的聊天模板（False 则始终用服务端模板）。 |
| `--default-chat-template-kwargs` | 无 | 模板默认 kwargs（与请求级合并），如 `{"enable_thinking":false}`。 |
| `--response-role` | `assistant` | `add_generation_prompt=true` 时返回的角色名。 |
| `--return-tokens-as-token-ids` | `False` | logprobs 时用 `token_id:N` 字符串表示无法 JSON 编码的 token。 |
| `--enable-auto-tool-choice` | `False` | 启用自动工具选择（须配合 `--tool-call-parser`）。 |
| `--tool-call-parser` | 无 | 工具调用解析器（按模型选择；内置可选或插件注册）。 |
| `--tool-parser-plugin` | （空） | 自定义工具解析器插件路径。 |
| `--tool-server` | 无 | 工具服务器 `host:port` 列表，或 `demo`（内置浏览器/Python 工具，**有安全风险**）。 |
| `--exclude-tools-when-tool-choice-none` | `False` | `tool_choice='none'` 时在 prompt 中排除工具定义。 |
| `--enable-prompt-tokens-details` | `False` | usage 中返回 `prompt_tokens_details`。 |
| `--enable-server-load-tracking` | `False` | 追踪 `server_load_metrics`。 |
| `--enable-force-include-usage` | `False` | 每个请求都包含 usage。 |
| `--enable-tokenizer-info-endpoint` | `False` | 启用 `/tokenizer_info` 端点（可能暴露聊天模板）。 |
| `--enable-log-requests` | `False` | 记录请求信息（INFO：ID/参数/LoRA；DEBUG：prompt 输入）。 |
| `--enable-log-outputs` | `False` | 记录模型生成输出（须配合 `--enable-log-requests`）。 |
| `--enable-log-deltas` | `True` | 是否记录输出增量（仅 `--enable-log-outputs` 时相关）。 |
| `--log-error-stack` | 见环境 | 记录错误响应的堆栈。 |
| `--log-config-file` | 环境值 | vllm 与 uvicorn 的日志配置 JSON 文件。 |
| `--max-log-len` | 无 | 日志中打印的 prompt 字符/token 上限（None=不限）。 |
| `--fingerprint-mode` | `full` | 响应 `system_fingerprint` 模式：`full`/`hash`/`custom`/`none`。 |
| `--fingerprint-value` | 无 | `custom` 模式下的指纹字面值。 |

---

## 17. 服务器层（入口）参数

| CLI 标志 | 默认 | 含义 |
|---|---|---|
| `model_tag`（位置参数） | 无 | 要服务的模型标签（若在 `--config` 中指定则可省）。 |
| `--config` | 无 | 从 YAML 配置文件读取 CLI 选项（[serve_args 文档](https://docs.vllm.ai/en/latest/configuration/serve_args.html)）。 |
| `--api-server-count` / `-asc` | 无 | 运行多少个 API server 进程；不指定则等于 `data_parallel_size`。 |
| `--grpc` | `False` | 启动 gRPC 服务（而非 HTTP OpenAI 兼容服务）；需 `pip install vllm[grpc]`。 |
| `--headless` | `False` | 无头模式（多节点数据并行场景，不启本地 API server）。 |
| `--disable-log-stats` | `False` | 关闭统计日志。 |
| `--aggregate-engine-logging` | `False` | 数据并行时记录聚合而非逐引擎统计。 |
| `--fail-on-environ-validation` | `False` | 环境校验失败时直接报错。 |
| `--shutdown-timeout` | `0` | 关停宽限秒数；0=立即 abort，>0=等待在途请求完成。 |
| `--enable-log-requests` | `False` | （同 16 节）记录请求信息。 |

---

## 18. 速查：常见部署模板

**单卡 / 小模型**
```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --max-model-len 8192 --gpu-memory-utilization 0.9 \
  --max-num-seqs 256
```

**多卡张量并行（最常用）**
```bash
vllm serve Qwen/Qwen2.5-72B-Instruct \
  --tensor-parallel-size 4 \
  --max-model-len 32768 --gpu-memory-utilization 0.92 \
  --enable-prefix-caching
```

**多机 TP（Ray）**
```bash
# 每台机器
vllm serve <model> --tensor-parallel-size 8 --pipeline-parallel-size 2 \
  --distributed-executor-backend ray --nnodes 2 --node-rank <0|1>
```

**KV cache 节省显存（长上下文）**
```bash
--kv-cache-dtype fp8 --max-model-len 65536
```

**投机解码加速（ngram，无需额外模型）**
```bash
--speculative-config '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4,"prompt_lookup_min":2}'
```

**P/D 分离 / KV 传输**
```bash
--kv-transfer-config '{"role":"producer","kv_role":"producer","kv_connector_module":"..."}'
```

---

## 19. 昇腾（Ascend NPU）特别说明

> 本仓库为 vLLM-Ascend（`torch_npu` 后端）。绝大多数参数与上述一致，但有几点差异：

- **设备/后端**：通过 `vllm-ascend` 插件自动注册 NPU 平台；`--device` 通常无需手设。Ascend 相关扩展参数由 `vllm_ascend` 包的 platform 通过 `current_platform.pre_register_and_update(parser)` 注入（如 PGE/专家并行、`--mm-processor-kwargs` 等适配项），请以本机 `vllm serve --help` 输出为准。
- **注意力后端**：Ascend 使用专属 attention 实现（如 `--attention-backend` 的 NPU 变体），多数情况保持 `auto` 即可。
- **量化**：优先使用 Ascend 支持的量化路径（如 W8A8/部分 FP8 场景），`--quantization` 取值需与 `torch_npu` / CANN 版本匹配。
- **CUDA Graph / eager**：注意 `--enforce-eager`、CUDA Graph 相关（`-cc.cudagraph_*`）在 NPU 上语义为「Graph 模式 / eager」，效果依 CANN 与 `torch_npu` 版本而定。
- **KV cache**：`--kv-cache-dtype` 的可选项受 CANN 算子支持限制，长上下文建议先验证数值正确性。
- **环境变量**：大量行为由 `VLLM_*` 环境变量开关（见 `vllm/envs.py`），如 `VLLM_USE_V1`、`VLLM_USE_TORCH_COMPILE` 等，常与上述 CLI 参数配合。

---

*数据来源：本地 vLLM 源码 `engine/arg_utils.py`（221 个 add_argument）、`config/*.py`（字段默认值与文档串）、`entrypoints/openai/cli_args.py`（服务层参数），逐项核对生成。具体可用项以目标平台 `vllm serve --help` 为准。*
