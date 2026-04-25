# vLLM vs SGLang：P/D 分离机制深度对比分析

> 分析日期：2026-04-21
> 源码路径：`/root/vllm_ascend/`（含 vllm 与 sglang 两个项目）

---

## 一、架构对比

| 维度 | vLLM | SGLang |
|------|------|--------|
| **设计哲学** | Connector 插件化架构，P/D 作为独立 vLLM 实例 | 内建 P/D 调度器，Scheduler 级别的原生 mixin 支持 |
| **核心抽象** | `KVConnectorBase_V1`（三层：Pipe → LookupBuffer → Connector） | `BaseKVSender`/`BaseKVReceiver` + `CommonKVManager` |
| **调度器集成** | 松耦合：Connector 注入 Scheduler，Scheduler 本身无 P/D 特化逻辑 | 紧耦合：`SchedulerDisaggregationPrefillMixin` / `SchedulerDisaggregationDecodeMixin` 分别实现独立事件循环 |
| **请求路由** | 无内建路由，需外部代理（`disagg_prefill_proxy_server.py`） | 内建 Rust Router（`pd_router.rs`），原生负载均衡 |

### 关键架构差异

**vLLM** 的 P/D 分离是一个"附加层"——Scheduler 和 Worker 的核心逻辑不变，通过 `KVTransferConfig` 注入一个可插拔的 Connector。这意味着：
- Prefill 和 Decode 是两个完全独立的 vLLM 引擎实例
- 两者之间没有协同调度，完全靠外部 proxy 路由

**SGLang** 的 P/D 分离是"内建能力"——Scheduler 本身被拆分为两套独立的 Mixin：
- `event_loop_normal_disagg_prefill`：管理 bootstrap → waiting → inflight 队列
- `event_loop_normal_disagg_decode`：管理 prealloc → transfer → waiting 队列
- 内建 heartbeat、超时、重试、KV pre-allocation 等机制

### 关键源码位置

**vLLM:**
- KV Connector Base: `vllm/vllm/distributed/kv_transfer/kv_connector/v1/base.py`
- Connector Factory: `vllm/vllm/distributed/kv_transfer/kv_connector/factory.py`
- KV Transfer Config: `vllm/vllm/config/kv_transfer.py`
- Main Scheduler: `vllm/vllm/v1/core/sched/scheduler.py`
- Worker Mixin: `vllm/vllm/v1/worker/kv_connector_model_runner_mixin.py`

**SGLang:**
- Prefill Logic: `sglang/python/sglang/srt/disaggregation/prefill.py`
- Decode Logic: `sglang/python/sglang/srt/disaggregation/decode.py`
- Common KV Manager: `sglang/python/sglang/srt/disaggregation/common/conn.py`
- Mooncake Backend: `sglang/python/sglang/srt/disaggregation/mooncake/conn.py`
- Scheduler Mixins: `sglang/python/sglang/srt/managers/scheduler.py`
- Rust Router: `sglang/sgl-model-gateway/src/routers/http/pd_router.rs`

---

## 二、KV Cache 传输机制对比

| 维度 | vLLM | SGLang |
|------|------|--------|
| **传输后端** | NCCL (P2P)、NIXL (UCX/GDS)、Mooncake (RDMA)、LMCache、HF3FS、Offloading、FlexKV | Mooncake (RDMA)、NIXL (UCX/LibFabric)、ASCEND (NPU)、Custom |
| **传输模式** | PUT / GET / PUT_ASYNC（异步流水线） | 同步 send + poll（轮询完成状态） |
| **辅助数据** | 无专用机制，KV 与元数据混传 | `AuxDataCodec` 独立序列化首 token、logprobs、hidden states |
| **异构 TP 支持** | 无（要求 Prefill/Decode TP 配置一致） | `StagingHandler`：GPU staging buffer 做 gather → bulk transfer → scatter |
| **层间优化** | `prefer_cross_layer_blocks` 将所有层 KV 打入连续 buffer | 无显式跨层优化 |

### 传输协议支持

**vLLM** 传输协议：
- **RDMA**: Via Mooncake (GPUDirect)
- **UCX**: Via NIXL — 可通过 `UCX_TLS`, `UCX_NET_DEVICES` 环境变量配置
- **NCCL**: Via P2pNcclConnector（点对点 GPU 直传）
- **TCP/ZMQ**: 用于控制面元数据交换
- **LIBFABRIC**: 通过 NIXL 后端

**SGLang** 传输协议：
- **RDMA**: Mooncake 后端（默认，支持 GPUDirect）
- **UCX/LIBFABRIC**: NIXL 后端
- **NPU memfabric_hybrid**: ASCEND 后端（华为昇腾适配）

### 传输可靠性

**vLLM** 的容错机制：
- `failed_recving_kv_req_ids`：Scheduler 跟踪 KV 加载失败的请求
- `get_finished()` 返回 `sync_failed_req_ids` 和 `async_failed_req_ids`
- 失败请求可回退到部分 prefill 重算（`_update_requests_with_invalid_blocks`）
- 但 **无 heartbeat 机制**，节点宕机只能靠外部 proxy 检测

**SGLang** 的容错机制：
- `heartbeat_checker` 线程：周期性探测 Prefill 节点存活（默认 5s 间隔，最大失败 2 次）
- `KVPoll` 状态机：Failed → Bootstrapping → WaitingForInput → Transferring → Success
- `SGLANG_DISAGGREGATION_WAITING_TIMEOUT`（默认 300s）：传输超时自动失败
- Bootstrap 超时、清理间隔均可配置

---

## 三、调度策略对比

| 维度 | vLLM | SGLang |
|------|------|--------|
| **Prefill 调度** | 标准 vLLM 调度 + KV transfer 状态追踪 | 独立事件循环 + bootstrap/prefill/inflight 三队列 |
| **Decode 调度** | 标准 vLLM 调度 + KV load 完成检查 | 独立事件循环 + prealloc/transfer/waiting 三队列 |
| **负载均衡** | 无内建，依赖外部 proxy | Rust Router + 多种策略（FCFS/LOF/LPM/DFS-Weight/ROUTING-KEY） |
| **KV 预分配** | 无（Decode 端被动等待） | `DecodePreallocQueue`：提前分配 KV slot，实现流水线 |
| **缓存感知** | 标准 prefix caching | LPM（最长前缀匹配）、ROUTING-KEY（一致性哈希路由） |

### SGLang 调度策略详解

- **FCFS (First Come First Served)**：先进先出，最简单的公平调度
- **LOF (Longest Output First)**：优先处理预计输出最长的请求，优化吞吐
- **LPM (Longest Prefix Match)**：优先调度与前序请求有最长公共前缀的请求，最大化 KV cache 复用
- **DFS-Weight**：基于树缓存权重的深度优先搜索
- **ROUTING-KEY**：基于频率的一致性哈希路由，保证相同前缀的请求路由到同一 Prefill 节点

---

## 四、Worker 架构对比

### vLLM Worker

- **Base**: `vllm/v1/worker/worker_base.py`
- **GPU Worker**: `vllm/v1/worker/gpu_worker.py`
- **KV Connector Mixin**: `vllm/v1/worker/kv_connector_model_runner_mixin.py`
  - `start_load_kv()`：在 forward pass 前启动异步 KV 加载
  - `save_kv_layer()`：逐层保存 KV cache
  - `wait_for_layer_load()`：阻塞等待某层 KV 加载完成
  - `wait_for_save()`：确保所有保存操作完成

**GPU 内存管理：**
- Buffer 大小：`kv_buffer_size` 配置（默认 1GB）
- Buffer 设备：`kv_buffer_device`（cuda/cpu/xpu）
- P2pNcclConnector 内存池：`TensorMemoryPool`（默认 32GB）
- 跨层优化：`prefer_cross_layer_blocks=True` 时单连续 buffer

### SGLang Worker

**Prefill Worker** (`--disaggregation-mode prefill`)：
- `PrefillBootstrapQueue`：管理握手和预分配
- `PrefillAdder`：将请求添加到 prefill batch
- `CommonKVSender`：发起 KV 传输
- 优化目标：最大化计算吞吐，更大的 batch size

**Decode Worker** (`--disaggregation-mode decode`)：
- `DecodePreallocQueue`：管理 KV 预分配
- `DecodeTransferQueue`：轮询传输完成
- `CommonKVReceiver`：接收 KV 传输
- 优化目标：最大化内存利用率，更多并发 decode 请求
- Retraction 机制：内存不足时将 KV 卸载到 CPU

---

## 五、配置与 API 对比

### vLLM 配置

核心参数通过 `--kv-transfer-config` JSON 字符串传入：

```bash
# Prefill 实例
vllm serve $MODEL \
  --kv-transfer-config '{
    "kv_connector":"MooncakeConnector",
    "kv_role":"kv_producer",
    "kv_rank":0,
    "kv_parallel_size":2,
    "kv_port":"14579"
  }'

# Decode 实例
vllm serve $MODEL \
  --kv-transfer-config '{
    "kv_connector":"MooncakeConnector",
    "kv_role":"kv_consumer",
    "kv_rank":1,
    "kv_parallel_size":2,
    "kv_port":"14580"
  }'
```

配置项：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `kv_connector` | Connector 名称 | None |
| `kv_role` | kv_producer / kv_consumer / kv_both | None |
| `kv_rank` | 实例 rank（0=prefill, 1=decode） | None |
| `kv_parallel_size` | 并行实例数 | 1 |
| `kv_buffer_device` | Buffer 设备 | cuda |
| `kv_buffer_size` | Buffer 大小（字节） | 1e9 |
| `kv_ip` | 传输 IP | 127.0.0.1 |
| `kv_port` | 传输端口 | 14579 |

### SGLang 配置

核心参数为一级命令行参数：

```bash
# Prefill 实例
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --disaggregation-mode prefill \
  --port 30000 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_roce0

# Decode 实例
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --disaggregation-mode decode \
  --port 30001 \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-ib-device mlx5_roce0

# Router
python -m sglang_router.launch_router \
  --pd-disaggregation \
  --prefill http://127.0.0.1:30000 \
  --decode http://127.0.0.1:30001 \
  --host 0.0.0.0 --port 8000
```

关键环境变量：
| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` | KV 传输线程池大小 | 8 |
| `SGLANG_DISAGGREGATION_QUEUE_SIZE` | 并行传输队列数 | 4 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | 心跳间隔（秒） | 5.0 |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | 最大心跳失败次数 | 2 |
| `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | 传输超时（秒） | 300 |
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | Bootstrap 超时（秒） | 300 |
| `SGLANG_DISAGG_STAGING_BUFFER` | 启用异构 TP staging buffer | 0 |
| `SGLANG_DISAGG_STAGING_BUFFER_SIZE_MB` | Staging buffer 大小 | 64 |
| `SGLANG_DISAGG_STAGING_POOL_SIZE_MB` | Staging buffer 池大小 | 4096 |

---

## 六、优劣势总结

### vLLM 优势

1. **高度模块化**：Connector 工厂模式，7+ 种后端可插拔，添加新后端只需实现 `KVConnectorBase_V1`
2. **异步传输**：`PUT_ASYNC` 模式允许 KV 传输与 forward 计算重叠
3. **层间优化**：`prefer_cross_layer_blocks` 减少 RDMA 往返次数
4. **后端丰富**：LMCache、HF3FS、Offloading 等覆盖更多场景（分布式 KV 存储、CPU 卸载）
5. **故障恢复**：支持 partial recomputation——KV 传输失败的 block 可以在 Decode 端重新计算
6. **灵活性**：`kv_role=kv_both` 允许同一实例既是 Producer 又是 Consumer

### vLLM 劣势

1. **无内建路由**：必须部署外部 proxy，增加运维复杂度
2. **无 Heartbeat**：节点故障只能靠外部检测
3. **无异构 TP**：Prefill 和 Decode 的 TP size 必须一致
4. **调度器无特化**：标准调度器不感知 P/D 分离，无法做全局优化
5. **配置复杂**：`--kv-transfer-config` 是 JSON 字符串，容易出错

### SGLang 优势

1. **端到端内建**：从 Router → Scheduler → Transfer → Worker 全栈原生支持
2. **Rust Router**：高性能 HTTP 路由，内建 P/D 负载均衡和健康检查
3. **KV 预分配**：Decode 端提前分配 slot，减少传输等待
4. **异构 TP**：Staging buffer 支持 Prefill 16卡 + Decode 8卡等非对称部署
5. **缓存感知路由**：LPM/ROUTING-KEY 策略最大化 KV cache 复用
6. **Heartbeat + 超时**：完善的生命周期管理和故障检测
7. **辅助数据优化**：首 token 通过 RDMA 独立传输，减少 Decode 端等待
8. **NPU 支持**：ASCEND 后端适配华为昇腾

### SGLang 劣势

1. **耦合度高**：P/D 逻辑深度嵌入 Scheduler，不易独立替换/测试
2. **传输后端较少**：主要 Mooncake + NIXL，无 LMCache/HF3FS/Offloading 等选项
3. **无 partial recomputation**：KV 传输失败时缺乏回退机制
4. **轮询模型**：Decode 端 `poll()` 轮询传输状态，在高并发下可能消耗 CPU
5. **异步能力弱**：无 `PUT_ASYNC` 模式，传输与计算难以重叠

---

## 七、潜在故障场景分类

### 类别 A：网络/传输层故障

| 故障场景 | vLLM 行为 | SGLang 行为 |
|----------|-----------|-------------|
| **RDMA 链路中断** | NCCL/Mooncake 层报错，`failed_recving_kv_req_ids` 记录失败请求，可尝试 partial recomputation | `KVPoll.Failed`，heartbeat 检测到 Prefill 失联后标记节点不可用 |
| **传输超时** | 无内建超时机制，依赖 Connector 实现 | `SGLANG_DISAGGREGATION_WAITING_TIMEOUT=300s` 后自动失败 |
| **带宽抖动** | 异步模式（PUT_ASYNC）可容忍短暂波动 | 轮询模式下延迟增加直接影响 Decode 吞吐 |
| **连接建立失败** | 依赖握手协议（`get_handshake_metadata`），失败则实例启动失败 | Bootstrap 阶段超时（`BOOTSTRAP_TIMEOUT=300s`）后重试或失败 |

**风险等级**：高。RDMA 链路故障是 P/D 分离中最常见的生产故障。

**缓解建议**：
- 配置网络冗余路径
- 启用 vLLM 的 partial recomputation 或 SGLang 的超时回退
- 监控 RDMA 链路状态和传输延迟

### 类别 B：计算/内存层故障

| 故障场景 | vLLM 行为 | SGLang 行为 |
|----------|-----------|-------------|
| **Decode 端 OOM** | 标准 vLLM OOM 处理（preemption） | `DecodePreallocQueue` 预分配失败时触发 retraction（KV 卸载到 CPU） |
| **Prefill 端 OOM** | 标准 vLLM preemption | 降低 `--max-prefill-tokens`，Bootstrap 队列限流 |
| **KV Cache 损坏** | `get_failed_blocks()` 可检测，支持重算 | 无显式检测机制，可能导致 Decode 输出错误 |
| **GPU 故障** | 依赖外部 proxy 检测和摘除 | Heartbeat 检测 + Router 自动摘除 |

**风险等级**：中高。Decode 端 OOM 在长上下文场景下尤为突出。

**缓解建议**：
- 合理配置 `--max-running-requests` 和 `--num-reserved-decode-tokens`
- 启用 SGLang 的 `--disaggregation-decode-enable-offload-kvcache`
- 监控 GPU 内存利用率和 KV cache 使用率

### 类别 C：协调/调度层故障

| 故障场景 | vLLM 行为 | SGLang 行为 |
|----------|-----------|-------------|
| **Proxy/Router 宕机** | 单点故障，所有请求中断 | Rust Router 也存在单点风险（可多实例部署） |
| **请求路由错误** | Proxy 层错误路由，Prefill 实例收到 Decode 请求（或反之） | Router 根据 P/D 角色分发，配置错误仍可能导致问题 |
| **KV Block 映射不一致** | Prefill 和 Decode 的 block table 不匹配时传输失败 | Bootstrap 阶段交换拓扑信息，运行时 Rank mapping 校验 |
| **请求丢失** | Prefill 完成 KV 传输后，Decode 端未收到 → 请求悬空 | Heartbeat + 超时机制可检测并清理悬空请求 |

**风险等级**：中。Proxy/Router 是架构上的单点。

**缓解建议**：
- 部署多实例 Router（SGLang）或高可用 Proxy（vLLM）
- 添加请求超时和重试机制
- 监控 Router/Proxy 健康状态

### 类别 D：数据一致性故障

| 故障场景 | vLLM 行为 | SGLang 行为 |
|----------|-----------|-------------|
| **异构 TP KV 布局不匹配** | 不支持异构 TP，必须配置一致 | `StagingHandler` 做 gather/scatter 适配 |
| **TP rank 映射错误** | 依赖 `kv_rank` 手动配置，配置错误导致数据错乱 | Bootstrap 阶段自动协商 rank mapping |
| **Prefix cache 不一致** | 无跨实例缓存同步 | LPM 策略 + ROUTING-KEY 保证路由一致性 |
| **首 token 丢失** | 无独立辅助通道，首 token 在 KV 传输完成后重新生成 | `AuxDataCodec` 通过 RDMA 独立传输首 token |

**风险等级**：高。数据一致性故障往往导致静默错误（输出错误但不报错）。

**缓解建议**：
- 严格校验 Prefill/Decode 的 TP/PP/DP 配置一致性
- 添加输出正确性验证（spot check）
- vLLM 场景下仔细检查 `kv_rank` 配置

### 类别 E：扩展性故障

| 故障场景 | vLLM 行为 | SGLang 行为 |
|----------|-----------|-------------|
| **大规模 Prefill 集群** | 每个实例独立，无全局协调 | Router 支持 WorkerRegistry 动态注册 |
| **弹性扩缩容** | 需重启实例修改 `kv_parallel_size` | Heartbeat + 动态注册支持在线扩缩（部分场景） |
| **DP Attention 场景** | 无特殊支持 | 原生支持 `--enable-dp-attention` + `--moe-a2a-backend deepep` |

**风险等级**：中低。主要影响大规模部署场景。

---

## 八、决策矩阵

| 场景 | 推荐选择 | 理由 |
|------|----------|------|
| 快速原型 / 研究场景 | vLLM | Connector 插件化，易于实验新后端 |
| 生产环境部署 | SGLang | 端到端内建、Heartbeat、Router、故障恢复更完善 |
| 异构 TP / 非对称部署 | SGLang | Staging buffer 原生支持 |
| 需要多种 KV 存储后端 | vLLM | LMCache/HF3FS/Offloading 等 7+ 种后端 |
| KV 传输容错 | vLLM | partial recomputation 机制更成熟 |
| NPU (昇腾) 部署 | SGLang | ASCEND 后端原生支持 |
| DeepSeek 等 MoE 大模型 | SGLang | 原生 DP Attention + DeepEP 集成 |
| 多租户/多模型混部 | vLLM | `kv_both` 角色灵活性更高 |

---

## 九、总结

两者在 P/D 分离上的设计哲学截然不同：

- **vLLM 追求模块化和可扩展性**：通过可插拔的 Connector 架构支持多种传输后端，适合需要灵活定制传输层的场景。其 partial recomputation 机制为 KV 传输失败提供了优雅的降级策略。

- **SGLang 追求端到端集成和生产就绪**：从 Rust Router 到 Scheduler Mixin 到 Transfer Backend 全栈原生支持 P/D 分离，适合需要快速部署、稳定运行的生产环境。其异构 TP 支持和缓存感知路由是独特的竞争优势。

选择取决于部署场景、运维能力和硬件配置。在昇腾 NPU 场景下，SGLang 的 ASCEND 后端是当前唯一的选择。
