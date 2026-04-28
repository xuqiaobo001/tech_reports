# vLLM-Ascend 分布式部署架构分析

> 分析日期：2026-04-27
> 目标：分析 vLLM 框架在 Ascend NPU (D 节点) 上的分布式部署架构，说明 Head/Worker 节点的部署要求和分离部署方案

---

## 1. 整体架构

vLLM 的分布式部署采用 **Head-Worker 分层架构**：

| 角色 | 职责 | 关键组件 |
|------|------|----------|
| **Head 节点** | 运行 API Server（FastAPI），接收请求，调度任务 | `api_server.py` + `MultiprocExecutor` / `RayExecutor` |
| **Worker 节点** | 执行模型推理，管理 NPU 设备 | `Worker` + `ModelRunner` |

### 1.1 Head 节点（主节点）

- 运行 OpenAI 兼容的 API Server（基于 FastAPI）
- 负责接收 HTTP 请求，调度推理任务到 Worker
- 可运行多个 API Server 进程实现负载均衡（`--api-server-count`）
- 支持 headless 模式（`--headless`）使节点仅作为 Worker

### 1.2 Worker 节点

- 基类：`WorkerBase`（`vllm/v1/worker/worker_base.py`）
- 平台特定实现：`GPUWorker`、`CPUWorker`、`XPUWorker`
- 每个 Worker 进程处理模型的一个子集（Tensor Parallel 分片或 Pipeline Stage）

### 1.3 Executor 类型

| Executor | 说明 | 适用场景 |
|----------|------|----------|
| `MultiprocExecutor` | Python 多进程管理 Worker | 单节点/多节点 |
| `RayExecutor` | 使用 Ray 框架编排 | 分布式集群 |
| `UniProcExecutor` | 单进程执行 | 调试/小模型 |

---

## 2. 通信机制

Head 和 Worker 之间依赖 **两层通信**：

### 2.1 PyTorch Distributed（HCCL 后端）

- 用于 Tensor Parallel / Pipeline Parallel 的集合通信
- Ascend NPU 使用 **HCCL**（Huawei Collective Communication Library）替代 NCCL
- 通过 `tcp://MASTER_IP:PORT` 进行初始化握手

```python
# parallel_state.py 中的 NPU 通信器初始化
self.npu_communicator: Optional[NpuCommunicator] = None
if use_npu_communicator and self.world_size > 1:
    self.npu_communicator = NpuCommunicator(group=self.device_group)
```

### 2.2 数据并行 RPC（ZMQ）

- Data Parallel 场景下各 rank 间的状态同步
- 使用 `--data-parallel-rpc-port` 指定的端口

### 2.3 进程组类型

- **TP (Tensor Parallel)**: 张量并行组
- **PP (Pipeline Parallel)**: 流水线并行组
- **DP (Data Parallel)**: 数据并行组
- **EP (Expert Parallel)**: 专家并行组（MoE 模型）

---

## 3. 关键部署参数

### 3.1 Pipeline Parallel 跨节点部署

```bash
# Head 节点 (Node 0)
vllm serve $MODEL \
    --tensor-parallel-size 8 \      # 每节点 TP 度
    --pipeline-parallel-size 2 \     # PP 度（跨节点）
    --nnodes 2 \                     # 总节点数
    --node-rank 0 \                  # 当前节点 rank
    --master-addr 10.99.48.128 \     # Head 节点 IP
    --master-port 29501              # 分布式初始化端口

# Worker 节点 (Node 1)
vllm serve $MODEL \
    --tensor-parallel-size 8 \
    --pipeline-parallel-size 2 \
    --nnodes 2 \
    --node-rank 1 \                  # 注意 rank 不同
    --master-addr 10.99.48.128 \     # 指向 Head 节点
    --master-port 29501
```

### 3.2 Data Parallel 跨节点部署

```bash
# Head 节点
vllm serve $MODEL \
    --data-parallel-size 4 --data-parallel-size-local 2 \
    --data-parallel-address 10.99.48.128 --data-parallel-rpc-port 13345

# Worker 节点（无 API Server）
vllm serve $MODEL \
    --headless \                     # 关键：不启动 API Server
    --data-parallel-size 4 --data-parallel-size-local 2 \
    --data-parallel-start-rank 2 \
    --data-parallel-address 10.99.48.128 --data-parallel-rpc-port 13345
```

### 3.3 完整参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--nnodes` | 总节点数 | 1 |
| `--node-rank` | 当前节点 rank（0-based） | 0 |
| `--master-addr` | Head 节点 IP 地址 | 127.0.0.1 |
| `--master-port` | 分布式初始化端口 | 29501 |
| `--tensor-parallel-size` | Tensor Parallel 度 | 1 |
| `--pipeline-parallel-size` | Pipeline Parallel 度 | 1 |
| `--data-parallel-size` | 总 DP rank 数 | 1 |
| `--data-parallel-size-local` | 本节点 DP rank 数 | - |
| `--headless` | 不启动 API Server（仅 Worker） | false |

---

## 4. Head/Worker 能否分开部署？

### 4.1 可以分离的场景

| 场景 | 说明 | 部署方式 |
|------|------|----------|
| Pipeline Parallel | PP > 1 时，不同节点持有不同 pipeline stage，天然分离 | `--pipeline-parallel-size` + `--nnodes` |
| Data Parallel + `--headless` | Worker 节点用 headless 模式，不启动 API Server | `--headless` + `--data-parallel-start-rank` |
| Ray 集群 | 使用 `RayExecutor`，Head/Worker 完全解耦 | Ray Actor 部署 |

### 4.2 不推荐分离的场景

| 场景 | 限制 |
|------|------|
| Tensor Parallel 跨节点 | TP 通常要求同一节点内设备，跨节点 TP 延迟极高 |
| 单节点部署 | Head 和所有 Worker 在同一进程组内，无法分离 |

### 4.3 分离部署的前提条件

1. 所有节点网络互通（TCP）
2. 模型文件在所有节点可访问（共享存储或各自本地副本）
3. HCCL 环境变量正确配置
4. 节点间时间同步（NTP）

---

## 5. Ascend NPU 特殊要求

### 5.1 必需的环境变量

```bash
export HCCL_BUFFSIZE=1000           # HCCL 通信缓冲区大小
export HCCL_OP_EXPANSION_MODE=AIV   # Ascend 算子扩展模式
export HCCL_SOCKET_IFNAME=eth0      # 跨节点通信网卡名（必须正确！）
```

### 5.2 关键注意事项

1. **网络接口** — `HCCL_SOCKET_IFNAME` 必须指向节点间实际互通的网卡，否则跨节点通信会失败
2. **HCCL 初始化** — 所有节点必须能通过 TCP 访问 `master-addr:master-port`，这是 `torch.distributed.init_process_group` 的前提
3. **时间同步** — 多节点间需要 NTP 时间同步，否则可能触发超时
4. **端口一致** — 所有节点的 `--master-addr` 和 `--master-port` 必须指向 Head 节点的同一地址
5. **模型路径** — 所有节点必须能访问相同的模型文件（共享存储或各自本地副本）

### 5.3 分布式初始化代码流程

```python
# parallel_state.py
def init_distributed_environment(
    world_size: int,
    rank: int,
    distributed_init_method: str = "env://",
    local_rank: int = -1,
    backend: str = "nccl",  # Ascend 上使用 "hccl"
):
    torch.distributed.init_process_group(
        backend=backend,
        init_method=distributed_init_method,
        world_size=world_size,
        rank=rank,
    )
```

---

## 6. 初始化流程

```
Head 节点启动
  ├── 启动 API Server (FastAPI)
  ├── 创建 Executor (MultiprocExecutor / RayExecutor)
  │     ├── fork Worker 进程（本地）
  │     └── 连接远程 Worker（通过 Ray 或 TCP）
  ├── 调用 torch.distributed.init_process_group(backend="hccl")
  │     └── 所有节点通过 tcp://MASTER_IP:PORT 握手
  └── 建立 process groups (TP/PP/DP/EP)

Worker 节点启动（--headless 或 --node-rank > 0）
  ├── 不启动 API Server（headless 模式）
  ├── 等待 Head 节点的 distributed init 信号
  ├── 加入 process group
  └── 加载模型分片，开始接收推理任务
```

---

## 7. 部署示例

### 7.1 DeepSeek-V3 双节点部署

```bash
# ========== Head 节点 (Node 0, IP: 10.99.48.128) ==========
export HCCL_BUFFSIZE=1000
export HCCL_OP_EXPANSION_MODE=AIV
export HCCL_SOCKET_IFNAME=eth0

vllm serve deepseek-ai/DeepSeek-V3 \
    --tensor-parallel-size 8 \
    --pipeline-parallel-size 2 \
    --nnodes 2 \
    --node-rank 0 \
    --master-addr 10.99.48.128 \
    --master-port 29501

# ========== Worker 节点 (Node 1, IP: 10.99.48.129) ==========
export HCCL_BUFFSIZE=1000
export HCCL_OP_EXPANSION_MODE=AIV
export HCCL_SOCKET_IFNAME=eth0

vllm serve deepseek-ai/DeepSeek-V3 \
    --tensor-parallel-size 8 \
    --pipeline-parallel-size 2 \
    --nnodes 2 \
    --node-rank 1 \
    --master-addr 10.99.48.128 \
    --master-port 29501
```

---

## 8. 总结

| 问题 | 答案 |
|------|------|
| Head/Worker 能否分开部署？ | **可以**，通过 `--headless`、`--nnodes`、`--node-rank` 等参数控制 |
| 推荐的分离方式 | PP 跨节点 或 DP + headless 模式 |
| 核心依赖 | 所有节点必须网络互通，HCCL 环境变量配置正确 |
| 最大风险点 | `HCCL_SOCKET_IFNAME` 配置错误导致跨节点通信失败 |
| Tensor Parallel 能否跨节点？ | 技术上可行但不推荐，延迟过高 |
| 最简分离方案 | Pipeline Parallel 2 节点 + 各自 8 卡 TP |

---

## 9. 关键代码文件索引

| 文件 | 说明 |
|------|------|
| `vllm/vllm/config/parallel.py` | 并行配置定义 |
| `vllm/vllm/engine/arg_utils.py` | CLI 参数解析 |
| `vllm/vllm/distributed/parallel_state.py` | 进程组管理 |
| `vllm/vllm/v1/executor/multiproc_executor.py` | 多进程 Worker 管理 |
| `vllm/vllm/entrypoints/openai/api_server.py` | API Server 入口 |
| `vllm/vllm/entrypoints/cli/serve.py` | CLI 入口 |
| `sglang/python/sglang/srt/distributed/parallel_state.py` | SGLang 分布式状态（含 HCCL） |
