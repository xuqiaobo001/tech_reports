# vLLM 多 P 节点调度机制深度分析

> 分析日期：2026-04-27
> 源码路径：`/root/vllm_ascend/vllm`

---

## 核心结论

vLLM 的多 P 节点之间**没有全局调度器**，每个 P 节点是一个完全独立的调度域。所有跨 P 节点的请求分发决策由外部 Proxy 通过简单的 Round-Robin 完成。P-P 之间不共享任何调度状态、KV Cache 内容或负载信息。

---

## 一、每个 P 节点拥有独立的完整引擎栈

### 1.1 进程架构

一个 P 节点的进程结构如下：

```
┌────────────── P Node ──────────────┐
│                                      │
│  ┌─── API Server (FastAPI) ───────┐ │
│  │   /v1/completions               │ │
│  │   /v1/chat/completions          │ │
│  └──────────┬──────────────────────┘ │
│             │                         │
│  ┌──────────▼──────────────────────┐ │
│  │   EngineCore (独立进程)           │ │
│  │                                  │ │
│  │   ┌──────────────────────────┐  │ │
│  │   │  Scheduler (独立实例)      │  │ │
│  │   │  ├── waiting queue       │  │ │
│  │   │  ├── running queue       │  │ │
│  │   │  ├── requests map        │  │ │
│  │   │  └── KVConnector         │  │ │
│  │   │      (role=SCHEDULER)     │  │ │
│  │   └──────────────────────────┘  │ │
│  │                                  │ │
│  │   ┌──────────────────────────┐  │ │
│  │   │  KVCacheManager (独立)    │  │ │
│  │   │  └── 本地 block pool     │  │ │
│  │   └──────────────────────────┘  │ │
│  │                                  │ │
│  │   ┌──────────────────────────┐  │ │
│  │   │  Workers (Ray Actors)     │  │ │
│  │   │  └── KVConnector          │  │ │
│  │   │      (role=WORKER)        │  │ │
│  │   └──────────────────────────┘  │ │
│  └──────────────────────────────────┘ │
└──────────────────────────────────────┘
```

### 1.2 源码证据

**Scheduler 创建** — `vllm/v1/engine/core.py:145-152`：

```python
# 每个 EngineCore 创建自己的 Scheduler
self.scheduler: SchedulerInterface = Scheduler(
    vllm_config=vllm_config,
    kv_cache_config=kv_cache_config,
    structured_output_manager=self.structured_output_manager,
    include_finished_set=include_finished_set,
    log_stats=self.log_stats,
    block_size=scheduler_block_size,
)
```

**KV Connector 创建** — `vllm/v1/core/sched/scheduler.py:117-137`：

```python
# Create KVConnector for the Scheduler. Note that each Worker
# will have a corresponding KVConnector with Role=WORKER.
# KV Connector pushes/pull of remote KVs for P/D and offloading.
self.connector = None
if self.vllm_config.kv_transfer_config is not None:
    self.connector = KVConnectorFactory.create_connector(
        config=self.vllm_config,
        role=KVConnectorRole.SCHEDULER,
        kv_cache_config=self.kv_cache_config,
    )
```

**独立的状态** — `vllm/v1/core/sched/scheduler.py:67-183`：

```python
class Scheduler(SchedulerInterface):
    def __init__(self, ...):
        # 全部是本地状态，不与其他 P 节点共享
        self.waiting                 # 本地等待队列
        self.running                 # 本地运行队列
        self.requests                # 本地请求映射
        self.finished_recving_kv_req_ids  # 本地 KV 传输完成追踪
        self.failed_recving_kv_req_ids    # 本地 KV 传输失败追踪
        self.connector               # 本地 KV Connector
```

---

## 二、P 节点间的协调完全依赖外部 Proxy

### 2.1 Proxy 调度策略

vLLM 在调度层没有实现任何 P-P 协调机制。所有多 P 节点的请求分发由**外部 Proxy** 完成。

**唯一的内置策略：Round-Robin** — `examples/online_serving/disaggregated_serving/disagg_proxy_demo.py:336-341`：

```python
class RoundRobinSchedulingPolicy(SchedulingPolicy):
    def __init__(self):
        super().__init__()

    def schedule(self, cycler: itertools.cycle) -> str:
        return next(cycler)   # 轮询选择 P 节点
```

### 2.2 请求处理流程

**源码** — `disagg_proxy_demo.py:250-278`：

```python
async def create_completion(self, raw_request):
    # Step 1: 选一个 P 节点（Round-Robin）
    prefill_instance = self.schedule(self.prefill_cycler)

    # Step 2: 发送到 P 节点做 prefill（max_tokens=1）
    kv_prepare_request = request.copy()
    kv_prepare_request["max_tokens"] = 1
    async for _ in self.forward_request(
        f"http://{prefill_instance}/v1/completions", kv_prepare_request
    ):
        continue

    # Step 3: 选一个 D 节点
    decode_instance = self.schedule(self.decode_cycler)

    # Step 4: 发送到 D 节点做 decode（带原始 max_tokens）
    generator = self.forward_request(
        f"http://{decode_instance}/v1/completions", request
    )
```

### 2.3 故障摘除机制

Proxy 实现了简单的故障摘除（`disagg_proxy_demo.py:327-333`）：

```python
def remove_instance_endpoint(self, instance_type, instance):
    if instance_type == "decode" and instance in self.decode_instances:
        self.decode_instances.remove(instance)
        self.decode_cycler = itertools.cycle(self.decode_instances)
    if instance_type == "prefill" and instance in self.prefill_instances:
        self.prefill_instances.remove(instance)
        self.prefill_cycler = itertools.cycle(self.prefill_instances)
```

当 `forward_request` 抛出 `HTTPException` 时，该节点会被从可用列表中移除。但**没有健康恢复检测**——节点一旦摘除，需手动重启 Proxy 才能恢复。

---

## 三、多 P-D 对的全局架构

```
                    ┌─────────────────────────────────┐
                    │       Proxy / Router              │
                    │                                   │
                    │   策略: Round-Robin               │
                    │   P 选: self.prefill_cycler       │
                    │   D 选: self.decode_cycler        │
                    │                                   │
  User ──────────► │   请求流程:                        │
  Requests         │   1. 选 P 节点 → Prefill           │
                    │   2. 选 D 节点 → Decode            │
                    └──┬────────┬────────┬──────────────┘
                       │        │        │
              ┌────────┘        │        └────────┐
              ▼                 ▼                 ▼
        ┌──────────┐     ┌──────────┐     ┌──────────┐
        │ P Node 0 │     │ P Node 1 │     │ P Node 2 │
        │ (完全独立)│     │ (完全独立)│     │ (完全独立)│
        │          │     │          │     │          │
        │ Scheduler│     │ Scheduler│     │ Scheduler│
        │ KV Cache │     │ KV Cache │     │ KV Cache │
        │ Workers  │     │ Workers  │     │ Workers  │
        │ kv_producer│   │ kv_producer│   │ kv_producer│
        └────┬─────┘     └────┬─────┘     └────┬─────┘
             │                │                │
             │   KV Transfer (RDMA / NCCL / NIXL)
             │                │                │
             ▼                ▼                ▼
        ┌──────────┐     ┌──────────┐     ┌──────────┐
        │ D Node 0 │     │ D Node 1 │     │ D Node 2 │
        │ (完全独立)│     │ (完全独立)│     │ (完全独立)│
        │          │     │          │     │          │
        │ Scheduler│     │ Scheduler│     │ Scheduler│
        │ KV Cache │     │ KV Cache │     │ KV Cache │
        │ Workers  │     │ Workers  │     │ Workers  │
        │ kv_consumer│   │ kv_consumer│   │ kv_consumer│
        └──────────┘     └──────────┘     └──────────┘

   P↔P: 无通信  |  D↔D: 无通信  |  P→D: KV Transfer only
```

---

## 四、P-P 之间不共享的信息清单

| 维度 | 是否共享 | 源码位置 | 说明 |
|------|----------|----------|------|
| 调度决策 | **不共享** | `scheduler.py:166-169` | 每个 P 节点独立决定批大小、preempt 策略 |
| KV Cache 内容 | **不共享** | `kv_cache_manager.py:106-153` | 每个 P 有独立的 KVCacheManager 和 block pool |
| 请求队列状态 | **不共享** | `scheduler.py:166-169` | waiting/running queue 完全本地 |
| 负载信息 | **不共享** | `disagg_proxy_demo.py:336-341` | Proxy 不查询 P 节点负载，盲目 Round-Robin |
| 前缀缓存 | **不共享** | `kv_cache_manager.py:176-234` | 相同前缀可能被多个 P 节点重复计算 |
| GPU 利用率 | **不共享** | 无相关代码 | 没有全局资源视图 |
| KV 传输状态 | **不共享** | `scheduler.py:182-183` | `finished_recving_kv_req_ids` 仅本地有效 |

---

## 五、KV Connector 只做 P→D 传输，不做 P↔P 协调

### 5.1 KV Connector 的两个角色

**源码** — `vllm/distributed/kv_transfer/kv_connector/v1/base.py`：

```python
class KVConnectorRole(Enum):
    SCHEDULER = "scheduler"  # Scheduler 进程中，管理元数据
    WORKER = "worker"        # Worker 进程中，执行实际传输
```

### 5.2 P 节点（kv_producer）的 KV Connector 行为

**Scheduler 侧** — `scheduler.py:956-959`：

```python
def _build_kv_connector_meta(self, connector, scheduler_output):
    return connector.build_connector_meta(scheduler_output)
```

**Worker 侧** — `vllm/v1/worker/kv_connector_model_runner_mixin.py`：

```python
# 逐层保存 KV cache 并发送到 D 节点
def save_kv_layer(self, layer_name, kv_tensors):
    self.kv_connector.save_kv_layer(layer_name, kv_tensors)

# 等待所有保存完成
def wait_for_save(self):
    self.kv_connector.wait_for_save()
```

### 5.3 传输方向

```
P Node (kv_producer)                    D Node (kv_consumer)
┌──────────────────┐                    ┌──────────────────┐
│ Scheduler        │                    │ Scheduler        │
│  build_meta() ───┼── metadata ───────►│  recv_meta()     │
│                  │                    │                  │
│ Worker           │                    │ Worker           │
│  save_kv_layer()─┼── KV tensors ─────►│  load_kv_layer() │
│  wait_for_save() │    (RDMA/NCCL)     │  wait_for_load() │
└──────────────────┘                    └──────────────────┘
          ▲                                      ▲
          │                                      │
          └──── 只有 P→D，没有 P→P 传输 ──────────┘
```

---

## 六、DPCoordinator 的角色（仅限 DP>1 场景）

如果单个 P 节点内部启用了 Data Parallelism（DP>1），存在一个 `DPCoordinator`。

**源码** — `vllm/v1/engine/coordinator.py:23-57`：

```python
class DPCoordinator:
    """Coordinator process used for data-parallel deployments (DP>1).

    * Collects stats from each DP engine (currently just waiting and
      running queue lengths), and publishes these to all front-ends
      for use in load-balancing decisions.

    * Keeps track of the current DP "request wave" number and running
      state of the engines.
    """
```

**关键点**：这个 Coordinator **只在单个 P 节点内部的 DP 副本之间**起作用，**不做跨 P 节点协调**。

DP 与 P/D 的关系：
- DP 是单个实例内部的并行策略（同一模型多副本）
- P/D 是跨实例的分离策略（不同实例承担不同角色）
- 两者正交：每个 DP 副本可以独立作为 P 或 D 节点
- DP 的 `engine_id` 会加上后缀区分（`vllm/v1/engine/utils.py:394-395`）：

```python
if dp_size > 1 and dp_vllm_config.kv_transfer_config is not None:
    dp_vllm_config.kv_transfer_config.engine_id = (
        f"{dp_vllm_config.kv_transfer_config.engine_id}_dp{local_index}"
    )
```

---

## 七、当前架构的问题分析

### 7.1 无负载感知

| 问题 | 影响 |
|------|------|
| Proxy 盲目 Round-Robin，不考虑 P 节点实际负载 | 高负载 P 节点可能 OOM，低负载 P 节点闲置 |
| 不考虑 P 节点的 GPU 显存占用 | 长序列请求可能集中到同一 P 节点导致 OOM |
| 不考虑 P 节点的 waiting queue 深度 | 尾部延迟不稳定 |

### 7.2 KV Cache 无复用

| 问题 | 影响 |
|------|------|
| 相同前缀发送到不同 P 节点，重复计算 | GPU 算力浪费，Prefill 延迟增加 |
| 无全局前缀缓存索引 | system prompt 等公共前缀无法跨 P 节点共享 |
| 无路由亲和性 | 同一用户的连续请求可能被分配到不同 P 节点 |

### 7.3 P-D 绑定松散

| 问题 | 影响 |
|------|------|
| Round-Robin 随机选 D 节点，不考虑 KV 传输拓扑 | 可能跨节点传输 KV，增加延迟 |
| 无机架感知 | 同一机架内的 P-D 配对未优先 |
| 无 NUMA 感知 | NUMA 绑定在 Ray 模式下被禁用 |

### 7.4 故障恢复不完善

| 问题 | 影响 |
|------|------|
| 节点摘除后无自动恢复检测 | 需手动重启 Proxy |
| 无心跳机制 | P 节点宕机只能等请求超时发现 |
| KV 传输失败后无回退路径 | D 节点收不到 KV 则请求失败 |

---

## 八、改进方向

### 8.1 短期改进（不改架构）

| 改进项 | 实现方式 | 复杂度 |
|--------|----------|--------|
| **最少负载优先路由** | Proxy 查询 P 节点的 `/health` 端点返回 `num_requests_waiting` | 低 |
| **一致性哈希路由** | 基于 `prompt[:64]` hash 路由到固定 P 节点 | 低 |
| **健康恢复检测** | Proxy 定期尝试被摘除节点，恢复后重新加入 | 中 |
| **P-D 拓扑配对** | Proxy 配置 P-D 亲和组，优先配对同机架 | 中 |

### 8.2 中期改进（需要架构变更）

| 改进项 | 实现方式 | 复杂度 |
|--------|----------|--------|
| **全局 KV Cache 索引** | P 节点向共享存储注册已缓存的 prefix hash | 高 |
| **LPM 路由策略** | 类似 SGLang，Proxy 查询最长前缀匹配的 P 节点 | 高 |
| **负载指标上报** | P 节点定期上报 GPU 利用率、queue depth 到 Proxy | 中 |
| **RDMA 拓扑感知** | 根据 NIC 亲和性选择最近的 D 节点 | 高 |

### 8.3 长期改进（参考 SGLang 架构）

| 改进项 | SGLang 参考 | 说明 |
|--------|-------------|------|
| **内建 Router** | `sglang_router` Rust Router | 将 Proxy 合并为内建组件 |
| **缓存感知路由** | LPM / ROUTING-KEY 策略 | 基于 prefix hash 路由 |
| **Heartbeat 机制** | `heartbeat_checker` 线程 | 5s 间隔检测节点存活 |
| **KV 预分配** | `DecodePreallocQueue` | D 节点提前分配 KV slot |

---

## 九、与 SGLang 的对比

| 维度 | vLLM | SGLang |
|------|------|--------|
| **多 P 节点调度** | 外部 Proxy，Round-Robin | 内建 Rust Router，多种策略 |
| **负载感知** | 无 | WorkerRegistry 动态负载上报 |
| **缓存感知路由** | 无 | LPM / ROUTING-KEY / DFS-Weight |
| **故障检测** | 请求超时摘除 | Heartbeat（5s 间隔） |
| **P-D 配对** | 随机 Round-Robin | 拓扑感知可选 |
| **弹性伸缩** | 手动更新 Proxy 配置 | Router 支持动态注册 |
| **KV 预分配** | 无 | `DecodePreallocQueue` 流水线 |

---

## 附录：关键源码文件索引

| 文件 | 说明 |
|------|------|
| `vllm/v1/core/sched/scheduler.py` | Scheduler 主类，独立调度域 |
| `vllm/v1/engine/core.py` | EngineCore，创建 Scheduler 实例 |
| `vllm/v1/engine/coordinator.py` | DPCoordinator（仅 DP>1 时使用） |
| `vllm/distributed/kv_transfer/kv_connector/v1/base.py` | KV Connector 基类 |
| `vllm/distributed/kv_transfer/kv_connector/v1/p2p/` | P2P NCCL Connector |
| `vllm/distributed/kv_transfer/kv_connector/v1/nixl/` | NIXL Connector |
| `vllm/distributed/kv_transfer/kv_connector/v1/mooncake/` | Mooncake Connector |
| `examples/online_serving/disaggregated_serving/disagg_proxy_demo.py` | P/D Proxy Demo |
| `benchmarks/disagg_benchmarks/disagg_prefill_proxy_server.py` | Prefill Proxy Server |
| `benchmarks/disagg_benchmarks/round_robin_proxy.py` | Round-Robin Proxy |
| `vllm/v1/worker/kv_connector_model_runner_mixin.py` | Worker 侧 KV Connector 集成 |
