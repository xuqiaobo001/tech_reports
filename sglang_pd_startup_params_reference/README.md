# SGLang PD 分离架构：P / D / Router 启动参数全解

> 分析日期：2026-04-19
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：详细说明 Prefill、Decode、Router 三个进程全部启动参数的含义、默认值、影响范围和调优建议

---

## 一、参数总览

SGLang PD 分离架构涉及三类进程，各自的参数体系如下：

| 进程 | 参数来源 | 参数数量（核心） | 配置方式 |
|------|---------|----------------|---------|
| **Prefill (P)** | `server_args.py` | ~15 个 disaggregation 相关 | CLI + 环境变量 |
| **Decode (D)** | `server_args.py` | ~15 个 disaggregation 相关 | CLI + 环境变量 |
| **Router** | `router_args.py` | ~50+ 个 | CLI |

---

## 二、Prefill / Decode 共享参数（server_args.py）

以下参数通过 `python -m sglang.launch_server` 传入，P 和 D 均可使用。

---

### 2.1 核心模型参数

#### `--model-path`

| 属性 | 值 |
|------|---|
| **默认值** | 必填 |
| **P 设置** | `THUDM/GLM-4.7-Flash-30B-A3B` |
| **D 设置** | `THUDM/GLM-4.7-Flash-30B-A3B`（必须与 P 相同） |
| **含义** | HuggingFace 模型 ID 或本地路径 |
| **影响** | 决定模型架构、权重、tokenizer。P 和 D **必须加载同一个模型**，否则 KV cache 格式不兼容导致传输失败 |
| **注意事项** | 路径不存在或模型不匹配会直接报错 |

---

#### `--host`

| 属性 | 值 |
|------|---|
| **默认值** | `127.0.0.1` |
| **P 设置** | `0.0.0.0` 或具体 IP |
| **D 设置** | `0.0.0.0` 或具体 IP |
| **含义** | HTTP 服务监听地址 |
| **影响** | 设为 `0.0.0.0` 允许外部访问；设为 `127.0.0.1` 只能本机访问 |
| **注意事项** | 多机部署时**必须设为具体 IP 或 `0.0.0.0`**，否则 Router 无法连接 |

---

#### `--port`

| 属性 | 值 |
|------|---|
| **默认值** | `30000` |
| **P 设置** | `30000`（各 P 实例可不同） |
| **D 设置** | `30000`（需与 P 不同端口如果在同一机器） |
| **含义** | HTTP 服务端口号 |
| **影响** | Router 通过此端口连接 P/D 实例 |
| **注意事项** | 同一机器部署 P 和 D 时端口不能冲突 |

---

#### `--tp-size`

| 属性 | 值 |
|------|---|
| **默认值** | `1` |
| **P 设置** | `8` |
| **D 设置** | `8` |
| **含义** | Tensor Parallel 大小，模型分片到多少张 GPU |
| **影响** | 决定每张 GPU 的显存占用（模型权重 / TP）和计算量。KV cache 按 head 维度分片到各 TP rank |
| **P/D 关系** | P 和 D 的 TP size **不要求相同**。不同 TP 时使用 slice 传输模式 |
| **调优** | 大模型（70B+）需要 TP>=4；GLM-4.7-Flash-30B-A3B 建议 TP=8 |

---

#### `--dp-size`

| 属性 | 值 |
|------|---|
| **默认值** | `1` |
| **P 设置** | `1` 或更大 |
| **D 设置** | `1` 或更大 |
| **含义** | Data Parallel 大小，同一请求复制到多少组 GPU 上并行处理 |
| **影响** | 提高 DP size 可以增加吞吐量，但需要更多 GPU |
| **注意事项** | 开启 DP 时需同时开启 `--enable-dp-attention` |

---

### 2.2 Disaggregation 核心参数

#### `--disaggregation-mode`

| 属性 | 值 |
|------|---|
| **默认值** | `null` |
| **P 设置** | **`prefill`** |
| **D 设置** | **`decode`** |
| **可选值** | `null` / `prefill` / `decode` |
| **含义** | 决定当前进程的角色 |
| **影响** | |
| | `null`：不启用分离，单进程同时负责 prefill 和 decode |
| | `prefill`：仅负责 prefill，处理完请求后将 KV cache 传输给 D |
| | `decode`：仅负责 decode，接收 P 传输的 KV cache 后执行 token 生成 |
| **副作用** | |
| | `prefill` 模式：如果未启用 piecewise cuda graph，则强制禁用 cuda graph |
| | `decode` 模式：**强制禁用 Radix Cache**（`disable_radix_cache=True`），因为 D 端不需要前缀缓存复用 |
| | 两种模式：都强制禁用 piecewise cuda graph |
| **代码位置** | `server_args.py:697` |

---

#### `--disaggregation-transfer-backend`

| 属性 | 值 |
|------|---|
| **默认值** | `mooncake` |
| **P 设置** | `mooncake` |
| **D 设置** | `mooncake`（必须与 P 相同） |
| **可选值** | `mooncake` / `nixl` / `fake` / `ascend` / `mori` |
| **含义** | KV cache 传输的后端引擎 |
| **各选项说明** | |
| | `mooncake`：基于 RDMA 的高性能传输，需要 Mooncake Transfer Engine 和 IB/RoCE 网络 |
| | `nixl`：NVIDIA NIXL 抽象层，支持 UCX / Libfabric 后端 |
| | `fake`：内存拷贝模拟传输，仅用于测试，P 端不支持 |
| | `ascend`：华为 Ascend NPU 专用传输 |
| | `mori`：Mori 传输后端 |
| **影响** | 决定 KV cache 传输的性能和硬件依赖 |
| **代码位置** | `server_args.py:698` |

---

#### `--disaggregation-bootstrap-port`

| 属性 | 值 |
|------|---|
| **默认值** | `8998` |
| **P 设置** | `8998`（P 端启动 BootstrapServer 监听此端口） |
| **D 设置** | 不使用（D 端不启动 BootstrapServer） |
| **含义** | P 端 Bootstrap Server 的监听端口 |
| **作用** | BootstrapServer 提供 P 端的拓扑信息（TP/DP/CP/PP rank 地址）给 D 端查询 |
| **Router 侧** | Router 通过 `--prefill URL 8998` 将此端口传递给 D 端 |
| **影响** | D 端需要通过此端口与 P 端完成握手，端口不可达会导致 Bootstrap 超时 |
| **代码位置** | `server_args.py:699` |

---

#### `--disaggregation-ib-device`

| 属性 | 值 |
|------|---|
| **默认值** | `None`（自动检测） |
| **P 设置** | `mlx5_0` 或 `mlx5_0,mlx5_1` |
| **D 设置** | `mlx5_0` 或 `mlx5_0,mlx5_1` |
| **含义** | InfiniBand / RDMA 网卡设备名 |
| **格式** | 单设备：`mlx5_0`；多设备逗号分隔：`mlx5_0,mlx5_1` |
| **影响** | 决定 KV cache 通过哪块网卡传输。多网卡可实现带宽叠加 |
| **自动检测** | 默认 None 时，mooncake 后端会自动检测可用的 IB 设备 |
| **验证** | 指定的设备必须存在且可用，否则启动失败（`_validate_ib_devices`） |
| **代码位置** | `server_args.py:700`、`server_args.py:6122-6128` |

---

#### `--disaggregation-decode-enable-offload-kvcache`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **P 设置** | 不适用 |
| **D 设置** | `True`（可选） |
| **含义** | 在 D 端启用异步 KV cache 卸载 |
| **前置条件** | 必须同时配置 `--hicache-storage-backend` |
| **作用** | 将已接收的 KV cache 从 GPU HBM 异步卸载到 CPU/SSD，释放 GPU 显存给更多 decode 请求 |
| **影响** | 增加 D 端可承载的并发请求数，但需要 decode 时从 CPU/SSD 加载回 GPU |
| **限制** | 仅 D 端可用，P 端设置会报错 |
| **代码位置** | `server_args.py:701` |

---

#### `--num-reserved-decode-tokens`

| 属性 | 值 |
|------|---|
| **默认值** | `512` |
| **含义** | 为 decode 阶段预留的 token 数量（配合 KV cache offload 使用） |
| **影响** | 当启用 `--disaggregation-decode-enable-offload-kvcache` 时，每个请求预留此数量的 token 空间在 GPU 上 |
| **调优** | 值越大越保守（占用更多 GPU 内存但更安全），值越小越节省内存但可能导致 decode 时内存不足 |
| **代码位置** | `server_args.py:702` |

---

#### `--disaggregation-decode-polling-interval`

| 属性 | 值 |
|------|---|
| **默认值** | `1` |
| **P 设置** | 不适用 |
| **D 设置** | `1` 或更大 |
| **含义** | D 端轮询请求状态的间隔 |
| **影响** | 值为 1 时每次都轮询（最低延迟但有 CPU 开销）；值 >1 可跳过部分轮询减少开销，但会轻微增加 decode 首 token 延迟 |
| **代码位置** | `server_args.py:704` |

---

### 2.3 内存与性能参数

#### `--mem-fraction-static`

| 属性 | 值 |
|------|---|
| **默认值** | `0.9` |
| **含义** | 静态内存占用比例（模型权重 + KV cache 占 GPU 总显存的比例） |
| **影响** | |
| | 值越大：KV cache 池越大，可容纳更多并发请求 |
| | 值越小：为动态分配（如临时 tensor）留更多空间，减少 OOM 风险 |
| **调优建议** | P 端建议 `0.85`（prefill 需要更多临时内存）；D 端可用 `0.90`（decode 内存需求较稳定） |

---

#### `--max-running-requests`

| 属性 | 值 |
|------|---|
| **默认值** | `48`（自动计算或显式指定） |
| **P 设置** | 较大值（如 200+），P 处理快、吞吐高 |
| **D 设置** | 较小值（如 128），受 GPU 显存限制更严格 |
| **含义** | 最大同时运行的请求数 |
| **影响** | 超过此限制的请求进入等待队列。P 端可设大因为 prefill 处理快；D 端受 KV cache 大小限制 |

---

#### `--max-prefill-tokens`

| 属性 | 值 |
|------|---|
| **默认值** | `16384` |
| **P 设置** | 可调大（如 `32768`），支持更大的 prefill batch |
| **D 设置** | 不适用 |
| **含义** | 单次 prefill forward pass 的最大 token 数 |
| **影响** | 值越大，单次 prefill 处理更多 token，吞吐更高但延迟增加；值越小，延迟更低但吞吐下降 |

---

#### `--context-length`

| 属性 | 值 |
|------|---|
| **默认值** | 模型 config.json 中的值 |
| **P/D 设置** | 可覆盖模型默认值 |
| **含义** | 最大上下文长度（输入 + 输出的总 token 数） |
| **影响** | 决定 KV cache 的最大尺寸。P 和 D 应设为相同值 |
| **注意事项** | 设为大于模型原生支持的值可能导致精度下降或 OOM |

---

#### `--schedule-policy`

| 属性 | 值 |
|------|---|
| **默认值** | `fcfs` |
| **P 设置** | 建议 `lpm`（最长前缀匹配，最大化 KV cache 复用） |
| **D 设置** | `fcfs`（D 端禁用了 Radix Cache，cache-aware 策略无意义） |
| **可选值** | `fcfs` / `lpm` / `dfs-weight` / `lof` / `random` / `routing-key` |
| **含义** | 请求调度策略（详见调度算法报告） |

---

### 2.4 并行与分布式参数

#### `--enable-dp-attention`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 启用 DP Attention 模式 |
| **影响** | 配合 `--dp-size` 使用，实现数据并行下的注意力计算优化 |
| **适用场景** | DeepSeek V3/R1 等 MoE 模型在 DP 模式下需要开启 |

---

#### `--dist-init-addr`

| 属性 | 值 |
|------|---|
| **默认值** | `None` |
| **含义** | 分布式初始化地址（如 `10.0.0.1:5000`） |
| **影响** | 多节点部署时各 TP rank 通过此地址互相发现 |
| **使用** | 配合 `--nnodes` 和 `--node-rank` 使用 |

---

#### `--nnodes` / `--node-rank`

| 属性 | 值 |
|------|---|
| **默认值** | `1` / `0` |
| **含义** | 总节点数 / 当前节点编号 |
| **影响** | 多机部署时指定集群拓扑 |

---

### 2.5 环境变量参数

以下参数通过环境变量设置，不可通过 CLI 传入。

#### `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT`

| 属性 | 值 |
|------|---|
| **默认值** | `300`（秒） |
| **作用于** | P 端 |
| **含义** | Bootstrap 阶段超时时间 |
| **影响** | P 端在 Bootstrapping 状态等待 D 端连接的最长时间。超时后返回 `KVPoll.Failed` |
| **调优** | 大模型或网络慢时可调大，如 `600` |

---

#### `SGLANG_DISAGGREGATION_WAITING_TIMEOUT`

| 属性 | 值 |
|------|---|
| **默认值** | `300`（秒） |
| **作用于** | D 端 |
| **含义** | 等待 KV 传输完成超时时间 |
| **影响** | D 端在 WaitingForInput 状态等待 P 端发送 KV 的最长时间 |
| **调优** | 超长输入（32K+）的 prefill 耗时较长，需相应调大 |

---

#### `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL`

| 属性 | 值 |
|------|---|
| **默认值** | `5.0`（秒，最小 `2.0`） |
| **作用于** | D 端 |
| **含义** | D 端对 P 节点的心跳检查间隔 |
| **影响** | 间隔越短，故障检测越快但网络/ CPU 开销越大；间隔越长，P 故障后 D 发现越慢 |
| **调优** | 生产环境建议 `3-5` 秒 |

---

#### `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE`

| 属性 | 值 |
|------|---|
| **默认值** | `2` |
| **作用于** | D 端 |
| **含义** | 连续心跳失败多少次后判定 P 节点故障 |
| **影响** | 值越小，故障检测越敏感（可能误判）；值越大，故障容忍度越高但发现越慢 |
| **调优** | 网络稳定环境建议 `2-3`；网络抖动环境建议 `5` |

---

#### `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE`

| 属性 | 值 |
|------|---|
| **默认值** | CPU 核数 |
| **作用于** | P 端 |
| **含义** | KV 传输线程池大小 |
| **影响** | 控制并行传输的线程数，影响传输吞吐量 |

---

#### `SGLANG_DISAGGREGATION_QUEUE_SIZE`

| 属性 | 值 |
|------|---|
| **默认值** | `4` |
| **作用于** | P 端 |
| **含义** | 并行传输队列数 |
| **影响** | 控制同时进行的 KV 传输数量 |

---

#### `SGLANG_DISAGG_STAGING_BUFFER` / `SGLANG_DISAGG_STAGING_BUFFER_SIZE_MB` / `SGLANG_DISAGG_STAGING_POOL_SIZE_MB`

| 属性 | 值 |
|------|---|
| **默认值** | `0` / `64` / `4096` |
| **作用于** | P + D |
| **含义** | 异构 TP 场景（P 和 D 的 TP size 不同）的 staging buffer 配置 |
| **前提** | 仅 `mooncake` 后端支持 |
| **影响** | 启用后，KV 先 gather 到 staging buffer，再通过 bulk RDMA 传输，D 端 scatter 到正确位置 |

---

#### `SGLANG_DISAGGREGATION_NIXL_BACKEND`

| 属性 | 值 |
|------|---|
| **默认值** | `UCX` |
| **作用于** | P + D（仅 nixl 后端） |
| **可选值** | `UCX` / `LIBFABRIC` |
| **含义** | NIXL 传输的底层后端选择 |

---

## 三、Router 参数（router_args.py）

Router 通过 `python -m sglang_router.launch_router` 启动，参数定义在 `sgl-model-gateway/bindings/python/src/sglang_router/router_args.py`。

---

### 3.1 基础配置

#### `--host` / `--port`

| 属性 | 值 |
|------|---|
| **默认值** | `0.0.0.0` / `30000` |
| **含义** | Router 对外服务的监听地址和端口 |
| **影响** | 客户端通过此地址发送请求 |

---

### 3.2 PD 分离配置

#### `--pd-disaggregation`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 启用 PD 分离路由模式 |
| **影响** | 启用后 Router 以双发模式工作：同时将请求发给 P 和 D，并在请求中注入 bootstrap 信息 |

---

#### `--prefill URL [BOOTSTRAP_PORT]`

| 属性 | 值 |
|------|---|
| **格式** | `--prefill http://10.0.0.1:30000 8998` |
| **可多次指定** | 是（每个 P 实例一次） |
| **含义** | Prefill 实例的 URL 和可选的 bootstrap 端口 |
| **BOOTSTRAP_PORT** | |
| | 数字（如 `8998`）：P 端的 BootstrapServer 端口 |
| | `none`：显式指定无 bootstrap 端口 |
| | 省略：默认无 bootstrap 端口 |
| **影响** | Router 将 bootstrap_host 和 bootstrap_port 注入请求 JSON，D 端通过此信息连接到正确的 P 实例 |

---

#### `--decode URL`

| 属性 | 值 |
|------|---|
| **格式** | `--decode http://10.0.0.8:30000` |
| **可多次指定** | 是（每个 D 实例一次） |
| **含义** | Decode 实例的 URL |
| **影响** | D 端不需要 bootstrap 端口（D 端不启动 BootstrapServer） |

---

#### `--mini-lb`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 使用 MiniLoadBalancer（Python 简易实现） |
| **影响** | 仅用于测试，生产环境使用 Rust 实现的 Router |

---

### 3.3 路由策略配置

#### `--policy`

| 属性 | 值 |
|------|---|
| **默认值** | `cache_aware` |
| **可选值** | `random` / `round_robin` / `cache_aware` / `power_of_two` / `manual` / `consistent_hashing` / `prefix_hash` |
| **含义** | 全局路由策略，PD 模式下同时用于 P 和 D（除非被 `--prefill-policy` / `--decode-policy` 覆盖） |

#### `--prefill-policy`

| 属性 | 值 |
|------|---|
| **默认值** | `None`（使用 `--policy`） |
| **可选值** | `random` / `round_robin` / `cache_aware` / `power_of_two` / `manual` / `bucket` / `consistent_hashing` / `prefix_hash` |
| **含义** | PD 模式下专门用于 P 实例的路由策略 |
| **策略详解** | |
| | `random`：随机选择 P 实例 |
| | `round_robin`：轮询 P 实例 |
| | `cache_aware`：感知 KV cache 命中率，优先路由到已有缓存前缀的 P 实例 |
| | `power_of_two`：随机选两个 P 实例，选负载更低的 |
| | `prefix_hash`：根据请求前缀 hash 路由，相同前缀总是路由到同一 P |
| | `manual`：手动指定 routing key 到 P 的映射 |
| | `bucket`：基于桶的分配策略 |
| | `consistent_hashing`：一致性哈希路由 |

#### `--decode-policy`

| 属性 | 值 |
|------|---|
| **默认值** | `None`（使用 `--policy`） |
| **可选值** | 同 `--prefill-policy`（不含 `bucket`） |
| **含义** | PD 模式下专门用于 D 实例的路由策略 |

---

#### `--cache-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `0.3` |
| **含义** | cache_aware 策略的缓存阈值（0.0-1.0） |
| **影响** | 前缀匹配度超过此阈值的请求才会被优先路由到已有缓存的 P 实例 |

---

#### `--balance-abs-threshold` / `--balance-rel-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `64` / `1.5` |
| **含义** | 负载均衡的绝对阈值和相对阈值 |
| **影响** | 当 `max_load - min_load > abs_threshold` **且** `max_load > min_load * rel_threshold` 时触发负载再均衡 |

---

#### `--eviction-interval-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `60` |
| **含义** | Router 端缓存驱逐操作的间隔（秒） |
| **影响** | Router 维护前缀缓存树，定期驱逐不活跃的缓存条目 |

---

#### `--max-tree-size`

| 属性 | 值 |
|------|---|
| **默认值** | `67108864`（2^26） |
| **含义** | Router 端近似前缀树的最大大小 |
| **影响** | 限制 Router 用于 cache_aware 路由的内存占用 |

---

#### `--dp-aware`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 启用 DP 感知调度 |
| **影响** | Router 在路由时考虑 DP rank，确保同一请求的所有 rank 路由到同一组实例 |

---

### 3.4 服务发现配置（Kubernetes）

#### `--service-discovery`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 启用 Kubernetes 服务发现 |
| **影响** | 不再需要手动指定 `--prefill` / `--decode`，Router 自动从 K8s 发现 Pod |

#### `--prefill-selector` / `--decode-selector`

| 属性 | 值 |
|------|---|
| **格式** | `role=prefill` |
| **含义** | Kubernetes label selector，用于筛选 P/D Pod |
| **影响** | Router 根据 selector 监听 Pod 变化，自动注册/注销 P/D 实例 |

#### `--bootstrap-port-annotation`

| 属性 | 值 |
|------|---|
| **默认值** | `sglang.ai/bootstrap-port` |
| **含义** | K8s Pod annotation 名，用于获取 P 端的 bootstrap 端口 |
| **影响** | Router 从 Pod 的此 annotation 读取 bootstrap 端口信息 |

---

### 3.5 健康检查配置

#### `--health-failure-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `3` |
| **含义** | 连续健康检查失败多少次后标记 worker 为不健康 |

#### `--health-success-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `2` |
| **含义** | 连续健康检查成功多少次后标记 worker 为健康 |

#### `--health-check-timeout-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `5` |
| **含义** | 单次健康检查请求的超时时间 |

#### `--health-check-interval-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `60` |
| **含义** | 健康检查间隔 |

#### `--health-check-endpoint`

| 属性 | 值 |
|------|---|
| **默认值** | `/health` |
| **含义** | 健康检查的 HTTP 端点路径 |

#### `--disable-health-check`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 完全禁用健康检查 |

---

### 3.6 重试配置

#### `--retry-max-retries`

| 属性 | 值 |
|------|---|
| **默认值** | `5` |
| **含义** | 请求失败后最大重试次数 |

#### `--retry-initial-backoff-ms` / `--retry-max-backoff-ms` / `--retry-backoff-multiplier`

| 属性 | 值 |
|------|---|
| **默认值** | `50` / `30000` / `1.5` |
| **含义** | 重试退避策略：初始延迟、最大延迟、退避倍数 |
| **影响** | 重试延迟公式：`min(initial * multiplier^attempt, max_backoff) * (1 ± jitter)` |

#### `--retry-jitter-factor`

| 属性 | 值 |
|------|---|
| **默认值** | `0.2` |
| **含义** | 重试抖动因子（0.0-1.0），防止多个请求同时重试导致雷群效应 |

#### `--disable-retries`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 禁用重试 |

---

### 3.7 熔断器配置

#### `--cb-failure-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `10` |
| **含义** | 熔断器触发打开的失败次数阈值 |

#### `--cb-success-threshold`

| 属性 | 值 |
|------|---|
| **默认值** | `3` |
| **含义** | 半开状态下连续成功多少次后关闭熔断器 |

#### `--cb-timeout-duration-secs` / `--cb-window-duration-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `60` / `120` |
| **含义** | 熔断器超时时间 / 滑动窗口时间 |

#### `--disable-circuit-breaker`

| 属性 | 值 |
|------|---|
| **默认值** | `False` |
| **含义** | 禁用熔断器 |

---

### 3.8 请求控制配置

#### `--max-concurrent-requests`

| 属性 | 值 |
|------|---|
| **默认值** | `-1`（不限制） |
| **含义** | 最大并发请求数 |
| **影响** | 超过限制的请求进入队列或返回 429 |

#### `--queue-size` / `--queue-timeout-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `100` / `60` |
| **含义** | 等待队列大小 / 队列中最大等待时间 |

#### `--request-timeout-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `1800`（30 分钟） |
| **含义** | 单个请求的最大处理时间 |

#### `--shutdown-grace-period-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `180`（3 分钟） |
| **含义** | 优雅关闭时等待在途请求完成的时间 |

---

### 3.9 Worker 启动配置

#### `--worker-startup-timeout-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `1800`（30 分钟） |
| **含义** | 等待 Worker 启动注册的超时时间 |
| **影响** | 大模型加载耗时较长，此值需覆盖模型加载时间 |

#### `--worker-startup-check-interval`

| 属性 | 值 |
|------|---|
| **默认值** | `30` |
| **含义** | Worker 启动检查间隔 |

---

### 3.10 其他配置

#### `--max-payload-size`

| 属性 | 值 |
|------|---|
| **默认值** | `536870912`（512MB） |
| **含义** | 单个请求的最大 payload 大小 |

#### `--max-idle-secs`

| 属性 | 值 |
|------|---|
| **默认值** | `14400`（4 小时） |
| **含义** | routing key 最大空闲时间，超过后驱逐（manual 策略） |

#### `--assignment-mode`

| 属性 | 值 |
|------|---|
| **默认值** | `random` |
| **可选值** | `random` / `min_load` / `min_group` |
| **含义** | manual 策略中新 routing key 的分配模式 |

---

## 四、7P1D 完整启动命令参考

### Prefill 实例（×7）

```bash
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode prefill \
  --host 10.0.0.1 --port 30000 \
  --tp-size 8 \
  --mem-fraction-static 0.85 \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_0 \
  --schedule-policy lpm \
  --max-prefill-tokens 32768 \
  --max-running-requests 200 \
  --context-length 32768
```

### Decode 实例（×1）

```bash
python -m sglang.launch_server \
  --model-path THUDM/GLM-4.7-Flash-30B-A3B \
  --disaggregation-mode decode \
  --host 10.0.0.8 --port 30000 \
  --tp-size 8 \
  --mem-fraction-static 0.90 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_0 \
  --max-running-requests 128 \
  --context-length 32768
```

### Router

```bash
python -m sglang_router.launch_router \
  --pd-disaggregation \
  --host 0.0.0.0 --port 8000 \
  --prefill http://10.0.0.1:30000 8998 \
  --prefill http://10.0.0.2:30000 8998 \
  --prefill http://10.0.0.3:30000 8998 \
  --prefill http://10.0.0.4:30000 8998 \
  --prefill http://10.0.0.5:30000 8998 \
  --prefill http://10.0.0.6:30000 8998 \
  --prefill http://10.0.0.7:30000 8998 \
  --decode http://10.0.0.8:30000 \
  --prefill-policy cache_aware \
  --decode-policy random \
  --health-check-interval-secs 30 \
  --health-failure-threshold 3 \
  --retry-max-retries 3 \
  --request-timeout-secs 600
```

---

## 五、参数调优速查表

### P 端调优

| 场景 | 调优方向 | 参数 |
|------|---------|------|
| 吞吐不足 | 增大 batch | `--max-prefill-tokens`、`--max-running-requests` |
| GPU OOM | 减少内存占用 | `--mem-fraction-static`（减小）、`--context-length`（减小） |
| KV 传输慢 | 优化网络 | `--disaggregation-ib-device`（多网卡）、`SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` |
| Cache 命中低 | 优化调度 | `--schedule-policy lpm` |

### D 端调优

| 场景 | 调优方向 | 参数 |
|------|---------|------|
| 并发不足 | 增大容量 | `--max-running-requests`、`--mem-fraction-static` |
| 首 token 延迟高 | 减少轮询开销 | `--disaggregation-decode-polling-interval 1` |
| KV 接收超时 | 增大超时 | `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` |
| P 故障检测慢 | 加快心跳 | `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` |
| GPU 内存不够 | KV 卸载 | `--disaggregation-decode-enable-offload-kvcache` + `--hicache-storage-backend` |

### Router 调优

| 场景 | 调优方向 | 参数 |
|------|---------|------|
| P 负载不均 | 优化路由 | `--prefill-policy cache_aware` 或 `power_of_two` |
| Cache 命中差 | 前缀路由 | `--prefill-policy prefix_hash` |
| 请求失败多 | 增强容错 | `--retry-max-retries`、`--cb-failure-threshold` |
| Worker 启动慢 | 增大等待 | `--worker-startup-timeout-secs` |
| 请求排队 | 限流保护 | `--max-concurrent-requests`、`--queue-size` |
