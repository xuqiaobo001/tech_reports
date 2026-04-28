# vLLM Ray 组件分析与 K8s 下 P/D 分离最佳部署方案

> 分析日期：2026-04-27
> 源码路径：`/root/vllm_ascend/vllm`、`/root/vllm_ascend/sglang`

---

## 第一部分：vLLM 中 Ray 组件用法深度分析

### 1.1 Ray 在 vLLM 中的角色

vLLM 将 Ray 作为**分布式执行后端**，核心用途：

| 用途 | 说明 |
|------|------|
| **Worker Actor 管理** | 每个 GPU 对应一个 Ray Actor（`RayWorkerProc`） |
| **Placement Group 调度** | 通过 PG 控制多节点 GPU 亲和性 |
| **分布式初始化** | 管理多节点间 `torch.distributed` 的 TCPStore 协调 |
| **环境变量传播** | 自动将 `VLLM_`、`NCCL_`、`UCX_` 等环境变量传递到 Worker |
| **健康监控** | 通过 `ray.wait()` 监测 Worker 存活状态 |

### 1.2 两代 Ray Executor 架构

vLLM 提供了两个版本的 Ray Executor：

#### RayExecutorV2（推荐，通过 `VLLM_USE_RAY_V2_EXECUTOR_BACKEND=1` 启用）

**源码**：`vllm/v1/executor/ray_executor_v2.py`

架构特点：
- 继承 `MultiprocExecutor`，复用 **MessageQueue（MQ）** 控制面
- Worker Actor 只做两件事：设置 CUDA 环境变量 + 启动 Worker 进程
- 使用 MQ（而非 Ray RPC）做 scheduler ↔ worker 通信，延迟更低

```
┌─────────────────────────────────────────────────┐
│                  API Server                      │
│                                                    │
│              Engine (MQ Client)                    │
│                 │       │                          │
│          ┌──────┘       └──────┐                   │
│          ▼                     ▼                   │
│   ┌─────────────┐      ┌─────────────┐            │
│   │  Worker 0   │      │  Worker 1   │            │
│   │ (Ray Actor) │      │ (Ray Actor) │            │
│   │   GPU 0     │      │   GPU 1     │            │
│   └─────────────┘      └─────────────┘            │
│                                                    │
│         Placement Group (PACK 策略)                │
└─────────────────────────────────────────────────┘
```

关键代码流程：

```python
# ray_executor_v2.py:331-357
# Actor 创建：绑定到 Placement Group 的特定 bundle
scheduling_strategy = PlacementGroupSchedulingStrategy(
    placement_group=placement_group,
    placement_group_bundle_index=bundle["bundle_id_idx"],
)
actor = ray.remote(RayWorkerProc).options(
    num_cpus=0,
    **resource_kwargs,           # {"num_gpus": 1} 或自定义资源
    scheduling_strategy=scheduling_strategy,
).remote(...)

# ray_executor_v2.py:84-101
# Worker 启动后自动发现 GPU ID
gpu_ids = ray.get_runtime_context().get_accelerator_ids()
os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpu_ids)
```

#### RayDistributedExecutor（旧版，使用 Compiled DAG）

**源码**：`vllm/v1/executor/ray_executor.py`

- 使用 `ray.dag.CompiledDAG` 构建优化执行图
- 支持 Pipeline Parallelism（PP）
- 通过 `execute_model_ray()` 方法在 Actor 间传递数据

### 1.3 Placement Group 策略

**源码**：`vllm/v1/executor/ray_utils.py:525-671`

```python
# 每个 GPU 创建一个 bundle
placement_group_specs = [
    {device_str: 1.0} for _ in range(parallel_config.world_size)
]

# 使用 PACK 策略：尽量把 bundle 放到同一节点
placement_group = ray.util.placement_group(
    placement_group_specs,
    strategy="PACK",
)
```

**自定义 bundle 顺序**：通过 `VLLM_RAY_BUNDLE_INDICES` 环境变量手动控制 bundle 映射。

### 1.4 Ray 环境变量传播

**源码**：`vllm/ray/ray_env.py`

自动传播到 Worker 的环境变量前缀：
```python
default_prefixes = [
    "VLLM_",
    "LMCACHE_",
    "NCCL_",
    "UCX_",
    "HF_",
    "HUGGING_FACE_",
    "CUDA_",      # 通过 blacklist 过滤
]
```

排除列表（不传播的变量）：
- `CUDA_VISIBLE_DEVICES`（由 Ray 自动管理）
- `RAY_*`（Ray 内部变量）
- `NCCL_DEBUG`（避免日志风暴）

### 1.5 Ray 健康监控

**源码**：`vllm/v1/executor/ray_executor_v2.py:429-479`

```python
# 后台线程持续监测 Worker 存活
def _poll_workers(self):
    while self._running:
        ready, _ = ray.wait(self._worker_obj_refs, timeout=5)
        if ready:
            # Worker 异常退出
            self._failure_callback(ready)
```

### 1.6 DP（Data Parallel）的 Ray 调度

**源码**：`vllm/v1/engine/utils.py:448-605`

```python
def create_dp_placement_groups():
    # 三种 pack 策略
    # "strict": 每个 PG 恰好占一个节点
    # "fill":   填满节点再开新节点
    # "span":   每个 PG 尽量跨节点分布
```

通过 `VLLM_RAY_DP_PACK_STRATEGY` 环境变量配置。

### 1.7 关键 Ray 环境变量汇总

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `VLLM_USE_RAY_V2_EXECUTOR_BACKEND` | `false` | 使用 RayExecutorV2 |
| `VLLM_USE_RAY_COMPILED_DAG_CHANNEL_TYPE` | `auto` | DAG 通信通道：nccl/shm |
| `VLLM_USE_RAY_COMPILED_DAG_OVERLAP_COMM` | `false` | 通信计算重叠 |
| `VLLM_RAY_PER_WORKER_GPUS` | `1.0` | 每个 Worker GPU 数 |
| `VLLM_RAY_BUNDLE_INDICES` | `None` | 手动指定 bundle 映射 |
| `VLLM_RAY_DP_PACK_STRATEGY` | `strict` | DP Placement 策略 |
| `VLLM_RAY_EXTRA_ENV_VAR_PREFIXES_TO_COPY` | `""` | 额外传播的变量前缀 |

---

## 第二部分：K8s 下 P/D 分离最佳部署方案

### 2.1 部署架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                        │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                    Ray Head Pod                            │    │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐         │    │
│  │  │  Ray Head   │  │  Router /  │  │  P/D Proxy │         │    │
│  │  │  (GCS)      │  │  Gateway   │  │  Server    │         │    │
│  │  │  Port:6379  │  │  Port:80   │  │            │         │    │
│  │  └────────────┘  └────────────┘  └────────────┘         │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌─────────────────────────────┐  ┌─────────────────────────┐   │
│  │   Prefill Worker Pool (P)   │  │  Decode Worker Pool (D)  │   │
│  │                              │  │                          │   │
│  │  ┌────────┐  ┌────────┐    │  │  ┌────────┐  ┌────────┐ │   │
│  │  │ P-Node │  │ P-Node │    │  │  │ D-Node │  │ D-Node │ │   │
│  │  │  8×GPU │  │  8×GPU │    │  │  │  8×GPU │  │  8×GPU │ │   │
│  │  │ TP=8   │  │ TP=8   │    │  │  │ TP=8   │  │ TP=8   │ │   │
│  │  │kv_producer│kv_producer│   │  │  │kv_consumer│kv_consumer│ │
│  │  └────────┘  └────────┘    │  │  └────────┘  └────────┘ │   │
│  └─────────────────────────────┘  └─────────────────────────┘   │
│                                                                   │
│  ─ ─ ─ ─ ─ ─ RDMA / RoCE Network ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─    │
│                    (KV Cache Transfer)                            │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 方案一：基于 Ray Cluster + K8s Operator（推荐）

#### 架构设计

利用 [KubeRay Operator](https://github.com/ray-project/kuberay) 管理 Ray 集群，将 Ray Head 作为 P/D 协调节点。

```
┌─────────────────────── KubeRay RayCluster CR ──────────────────────┐
│                                                                      │
│  Head Group (1 Pod, 0 GPU):                                         │
│    ├── ray start --head --port=6379                                 │
│    ├── Router Server (sidecar)                                      │
│    └── P/D Proxy (sidecar)                                          │
│                                                                      │
│  Worker Group "prefill" (N Pods, 8 GPU each):                      │
│    ├── ray start --address=HEAD:6379                                │
│    └── vllm worker (kv_role=kv_producer)                            │
│                                                                      │
│  Worker Group "decode" (M Pods, 8 GPU each):                       │
│    ├── ray start --address=HEAD:6379                                │
│    └── vllm worker (kv_role=kv_consumer)                            │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

#### RayCluster CR 示例

```yaml
apiVersion: ray.io/v1
kind: RayCluster
metadata:
  name: vllm-pd-cluster
  namespace: vllm-serving
spec:
  rayVersion: "2.40.0"

  headGroupSpec:
    rayStartParams:
      port: "6379"
      object-store-memory: "2000000000"
      dashboard-host: "0.0.0.0"
    template:
      spec:
        containers:
          - name: ray-head
            image: vllm/vllm-openai:latest
            resources:
              requests:
                cpu: "4"
                memory: "16Gi"
              limits:
                cpu: "4"
                memory: "16Gi"
            ports:
              - containerPort: 6379   # Ray GCS
                name: ray-gcs
              - containerPort: 8265   # Ray Dashboard
                name: ray-dashboard
              - containerPort: 80     # Router
                name: router
            env:
              - name: VLLM_USE_RAY_V2_EXECUTOR_BACKEND
                value: "1"

  workerGroupSpecs:
    # ──── Prefill Worker Group ────
    - groupName: prefill
      replicas: 2
      minReplicas: 1
      maxReplicas: 4
      rayStartParams:
        resources: '"{\"GPU\": 8}"'
      template:
        spec:
          containers:
            - name: ray-worker
              image: vllm/vllm-openai:latest
              resources:
                requests:
                  nvidia.com/gpu: "8"
                limits:
                  nvidia.com/gpu: "8"
              env:
                - name: VLLM_USE_RAY_V2_EXECUTOR_BACKEND
                  value: "1"
                - name: NCCL_IB_GID_INDEX
                  value: "3"
                - name: NCCL_IB_HCA
                  value: "=mlx5_0,mlx5_1,mlx5_2,mlx5_3"
              volumeMounts:
                - name: shm
                  mountPath: /dev/shm
                - name: model-cache
                  mountPath: /models
          volumes:
            - name: shm
              emptyDir:
                medium: Memory
                sizeLimit: "32Gi"
            - name: model-cache
              persistentVolumeClaim:
                claimName: model-pvc

    # ──── Decode Worker Group ────
    - groupName: decode
      replicas: 2
      minReplicas: 1
      maxReplicas: 4
      rayStartParams:
        resources: '"{\"GPU\": 8}"'
      template:
        spec:
          containers:
            - name: ray-worker
              image: vllm/vllm-openai:latest
              resources:
                requests:
                  nvidia.com/gpu: "8"
                limits:
                  nvidia.com/gpu: "8"
              env:
                - name: VLLM_USE_RAY_V2_EXECUTOR_BACKEND
                  value: "1"
                - name: NCCL_IB_GID_INDEX
                  value: "3"
              volumeMounts:
                - name: shm
                  mountPath: /dev/shm
                - name: model-cache
                  mountPath: /models
          volumes:
            - name: shm
              emptyDir:
                medium: Memory
                sizeLimit: "32Gi"
            - name: model-cache
              persistentVolumeClaim:
                claimName: model-pvc
```

#### 启动脚本（在 Head Pod 中运行）

```bash
#!/bin/bash
# deploy_pd_serving.sh - 在 Ray Head Pod 中执行

MODEL="meta-llama/Meta-Llama-3.1-70B-Instruct"
HEAD_IP=$(hostname -I | awk '{print $1}')

# ──── 1. 等待 Ray Worker 就绪 ────
python3 -c "
import ray, time
ray.init(address='auto')
expected = $(($PREFILL_REPLICAS + $DECODE_REPLICAS))
while True:
    alive = sum(1 for n in ray.nodes() if n['Alive'])
    if alive >= expected:
        print(f'Cluster ready: {alive} nodes')
        break
    time.sleep(5)
"

# ──── 2. 获取 Prefill/Decode 节点 IP ────
python3 << 'PYEOF'
import ray, json
ray.init(address='auto')
nodes = ray.nodes()

prefill_ips = []
decode_ips = []
for node in nodes:
    if not node["Alive"]:
        continue
    addr = node["NodeManagerAddress"]
    resources = node["Resources"]
    # 通过 Ray resource label 区分 P/D
    if "PREFILL" in str(resources):
        prefill_ips.append(addr)
    elif "DECODE" in str(resources):
        decode_ips.append(addr)

print(json.dumps({"prefill": prefill_ips, "decode": decode_ips}))
PYEOF

# ──── 3. 启动 Prefill 实例（Ray Job） ────
ray job submit --address="http://127.0.0.1:8265" --runtime-env-json='{}' -- bash -c "
vllm serve $MODEL \
    --distributed-executor-backend ray \
    --tensor-parallel-size 8 \
    --host 0.0.0.0 --port 8100 \
    --kv-transfer-config '{
        \"kv_connector\": \"MooncakeConnector\",
        \"kv_role\": \"kv_producer\",
        \"kv_rank\": 0,
        \"kv_parallel_size\": 2,
        \"kv_ip\": \"$(hostname -I | awk '{print $1}')\",
        \"kv_port\": \"14579\"
    }'
" &

# ──── 4. 启动 Decode 实例（Ray Job） ────
ray job submit --address="http://127.0.0.1:8265" --runtime-env-json='{}' -- bash -c "
vllm serve $MODEL \
    --distributed-executor-backend ray \
    --tensor-parallel-size 8 \
    --host 0.0.0.0 --port 8200 \
    --max-model-len 8192 \
    --kv-transfer-config '{
        \"kv_connector\": \"MooncakeConnector\",
        \"kv_role\": \"kv_consumer\",
        \"kv_rank\": 1,
        \"kv_parallel_size\": 2,
        \"kv_ip\": \"$(hostname -I | awk '{print $1}')\",
        \"kv_port\": \"14580\"
    }'
" &

# ──── 5. 启动 P/D Proxy ────
python3 disagg_prefill_proxy_server.py \
    --prefill-url http://127.0.0.1:8100 \
    --decode-url http://127.0.0.1:8200 \
    --port 80 &

wait
```

### 2.3 方案二：基于 LWS + 独立 Service（适合大规模 MoE）

对于 DeepSeek-V3 等需要跨节点 TP=16 的超大规模模型，推荐使用 Kubernetes LeaderWorkerSet（LWS）。

```
┌──────────────────────────────────────────────────────────────┐
│                     K8s Namespace: vllm-pd                    │
│                                                                │
│  ┌──────────────────────┐    ┌──────────────────────┐         │
│  │  LWS: prefill-pool   │    │  LWS: decode-pool    │         │
│  │  Size: 2 nodes/group │    │  Size: 2 nodes/group │         │
│  │                      │    │                      │         │
│  │  Leader Pod:         │    │  Leader Pod:         │         │
│  │  ├─ vllm serve (P)   │    │  ├─ vllm serve (D)   │         │
│  │  ├─ TP=16, DP=4     │    │  ├─ TP=16, DP=4     │         │
│  │  └─ nnodes=2        │    │  └─ nnodes=2        │         │
│  │                      │    │                      │         │
│  │  Worker Pod:         │    │  Worker Pod:         │         │
│  │  └─ vllm worker (P)  │    │  └─ vllm worker (D)  │         │
│  └──────────────────────┘    └──────────────────────┘         │
│       │  Port: 8100              │  Port: 8200                │
│       │                          │                             │
│       └──────────┬───────────────┘                             │
│                  │                                             │
│         ┌────────▼────────┐                                    │
│         │  Router Service │                                    │
│         │  (Deployment)   │                                    │
│         │  Port: 80       │                                    │
│         │  LoadBalancer   │                                    │
│         └─────────────────┘                                    │
└──────────────────────────────────────────────────────────────┘
```

#### LWS Prefill CR 示例

```yaml
apiVersion: leaderworkerset.x-k8s.io/v1
kind: LeaderWorkerSet
metadata:
  name: vllm-prefill
  namespace: vllm-pd
spec:
  replicas: 2
  leaderWorkerTemplate:
    size: 2  # 2 nodes per group → TP=16 across 2×8GPU
    leaderTemplate:
      metadata:
        labels:
          role: prefill
      spec:
        containers:
          - name: vllm-prefill-leader
            image: vllm/vllm-openai:latest
            command:
              - bash
              - -c
              - |
                export CUDA_VISIBLE_DEVICES=$(nvidia-smi -L | wc -l | tr -d ' ')
                vllm serve meta-llama/Meta-Llama-3.1-70B-Instruct \
                  --distributed-executor-backend ray \
                  --tensor-parallel-size 16 \
                  --host 0.0.0.0 --port 8100 \
                  --dist-init-addr $(LWS_LEADER_ADDRESS):20000 \
                  --nnodes $(LWS_GROUP_SIZE) --node-rank 0 \
                  --kv-transfer-config '{
                    "kv_connector": "MooncakeConnector",
                    "kv_role": "kv_producer",
                    "kv_rank": 0,
                    "kv_parallel_size": 4
                  }'
            resources:
              limits:
                nvidia.com/gpu: "8"
            env:
              - name: NCCL_IB_GID_INDEX
                value: "3"
              - name: NCCL_IB_HCA
                value: "=mlx5_0,mlx5_1,mlx5_2,mlx5_3"
              - name: NCCL_SOCKET_IFNAME
                value: "eth0"
            volumeMounts:
              - name: shm
                mountPath: /dev/shm
              - name: model-cache
                mountPath: /models
        volumes:
          - name: shm
            emptyDir:
              medium: Memory
              sizeLimit: "64Gi"
          - name: model-cache
            persistentVolumeClaim:
              claimName: model-pvc
    workerTemplate:
      spec:
        containers:
          - name: vllm-prefill-worker
            image: vllm/vllm-openai:latest
            command:
              - bash
              - -c
              - |
                vllm serve meta-llama/Meta-Llama-3.1-70B-Instruct \
                  --distributed-executor-backend ray \
                  --tensor-parallel-size 16 \
                  --host 0.0.0.0 --port 8100 \
                  --dist-init-addr $(LWS_LEADER_ADDRESS):20000 \
                  --nnodes $(LWS_GROUP_SIZE) --node-rank $(LWS_WORKER_INDEX) \
                  --kv-transfer-config '{
                    "kv_connector": "MooncakeConnector",
                    "kv_role": "kv_producer",
                    "kv_rank": 0,
                    "kv_parallel_size": 4
                  }'
            resources:
              limits:
                nvidia.com/gpu: "8"
            env:
              - name: NCCL_IB_GID_INDEX
                value: "3"
---
# Decode LWS
apiVersion: leaderworkerset.x-k8s.io/v1
kind: LeaderWorkerSet
metadata:
  name: vllm-decode
  namespace: vllm-pd
spec:
  replicas: 2
  leaderWorkerTemplate:
    size: 2
    leaderTemplate:
      metadata:
        labels:
          role: decode
      spec:
        containers:
          - name: vllm-decode-leader
            image: vllm/vllm-openai:latest
            command:
              - bash
              - -c
              - |
                vllm serve meta-llama/Meta-Llama-3.1-70B-Instruct \
                  --distributed-executor-backend ray \
                  --tensor-parallel-size 16 \
                  --host 0.0.0.0 --port 8200 \
                  --dist-init-addr $(LWS_LEADER_ADDRESS):20000 \
                  --nnodes $(LWS_GROUP_SIZE) --node-rank 0 \
                  --max-running-requests 256 \
                  --kv-transfer-config '{
                    "kv_connector": "MooncakeConnector",
                    "kv_role": "kv_consumer",
                    "kv_rank": 1,
                    "kv_parallel_size": 4
                  }'
            resources:
              limits:
                nvidia.com/gpu: "8"
            volumeMounts:
              - name: shm
                mountPath: /dev/shm
              - name: model-cache
                mountPath: /models
        volumes:
          - name: shm
            emptyDir:
              medium: Memory
              sizeLimit: "64Gi"
          - name: model-cache
            persistentVolumeClaim:
              claimName: model-pvc
    workerTemplate:
      spec:
        containers:
          - name: vllm-decode-worker
            image: vllm/vllm-openai:latest
            command:
              - bash
              - -c
              - |
                vllm serve meta-llama/Meta-Llama-3.1-70B-Instruct \
                  --distributed-executor-backend ray \
                  --tensor-parallel-size 16 \
                  --host 0.0.0.0 --port 8200 \
                  --dist-init-addr $(LWS_LEADER_ADDRESS):20000 \
                  --nnodes $(LWS_GROUP_SIZE) --node-rank $(LWS_WORKER_INDEX) \
                  --max-running-requests 256 \
                  --kv-transfer-config '{
                    "kv_connector": "MooncakeConnector",
                    "kv_role": "kv_consumer",
                    "kv_rank": 1,
                    "kv_parallel_size": 4
                  }'
            resources:
              limits:
                nvidia.com/gpu: "8"
---
# Router Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-pd-router
  namespace: vllm-pd
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm-pd-router
  template:
    metadata:
      labels:
        app: vllm-pd-router
    spec:
      containers:
        - name: router
          image: vllm/vllm-openai:latest
          command:
            - python3
            - /app/disagg_prefill_proxy_server.py
            - --prefill-url
            - "http://vllm-prefill:8100"
            - --decode-url
            - "http://vllm-decode:8200"
            - --port
            - "80"
          ports:
            - containerPort: 80
          readinessProbe:
            httpGet:
              path: /health
              port: 80
---
# Router Service
apiVersion: v1
kind: Service
metadata:
  name: vllm-pd-router
  namespace: vllm-pd
spec:
  type: LoadBalancer
  selector:
    app: vllm-pd-router
  ports:
    - port: 80
      targetPort: 80
---
# Prefill Service (内部)
apiVersion: v1
kind: Service
metadata:
  name: vllm-prefill
  namespace: vllm-pd
spec:
  type: ClusterIP
  selector:
    role: prefill
    leaderworkerset.sigs.k8s.io/role: leader
  ports:
    - port: 8100
      targetPort: 8100
---
# Decode Service (内部)
apiVersion: v1
kind: Service
metadata:
  name: vllm-decode
  namespace: vllm-pd
spec:
  type: ClusterIP
  selector:
    role: decode
    leaderworkerset.sigs.k8s.io/role: leader
  ports:
    - port: 8200
      targetPort: 8200
```

### 2.4 方案三：纯 K8s Deployment（最简方案）

适合 TP=1 或 TP=8（单节点）的场景，不依赖 Ray。

```
┌──────────────────────────────────────────┐
│            K8s Namespace: vllm           │
│                                          │
│  Deployment: prefill (replicas: N)       │
│    └── vllm serve (kv_producer)          │
│                                          │
│  Deployment: decode (replicas: M)        │
│    └── vllm serve (kv_consumer)          │
│                                          │
│  Deployment: router (replicas: 2)        │
│    └── P/D proxy server                  │
│                                          │
│  Service: prefill-svc (ClusterIP)        │
│  Service: decode-svc (ClusterIP)         │
│  Service: router-svc (LoadBalancer)      │
└──────────────────────────────────────────┘
```

```yaml
# Prefill Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-prefill
  namespace: vllm
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm-prefill
  template:
    metadata:
      labels:
        app: vllm-prefill
    spec:
      containers:
        - name: vllm-prefill
          image: vllm/vllm-openai:latest
          command:
            - vllm
            - serve
            - meta-llama/Meta-Llama-3.1-70B-Instruct
            - --host
            - "0.0.0.0"
            - --port
            - "8100"
            - --tensor-parallel-size
            - "8"
            - --kv-transfer-config
            - '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer"}'
          resources:
            limits:
              nvidia.com/gpu: "8"
          ports:
            - containerPort: 8100
          readinessProbe:
            httpGet:
              path: /health
              port: 8100
            initialDelaySeconds: 120
            periodSeconds: 10
          volumeMounts:
            - name: shm
              mountPath: /dev/shm
            - name: model-cache
              mountPath: /models
      volumes:
        - name: shm
          emptyDir:
            medium: Memory
            sizeLimit: "32Gi"
        - name: model-cache
          persistentVolumeClaim:
            claimName: model-pvc
```

### 2.5 三种方案对比

| 维度 | 方案一：KubeRay | 方案二：LWS | 方案三：纯 Deployment |
|------|-----------------|-------------|----------------------|
| **适用场景** | 中等规模，TP≤8，需要弹性伸缩 | 超大规模，跨节点 TP>8，MoE | 小规模，TP≤8，快速验证 |
| **Ray 依赖** | 是（KubeRay Operator） | 否（使用 vLLM 原生多节点） | 否（单节点） |
| **弹性伸缩** | Ray Autoscaler + HPA | LWS 原生伸缩 | K8s HPA |
| **GPU 感知调度** | Ray Placement Group | K8s Device Plugin | K8s Device Plugin |
| **P/D 协调** | Ray Job + 自定义 Proxy | K8s Service + Proxy | K8s Service + Proxy |
| **RDMA 支持** | 需配置 hostNetwork | hostNetwork + privileged | hostNetwork |
| **运维复杂度** | 高（需维护 KubeRay） | 中（原生 K8s CRD） | 低 |
| **推荐模型** | Llama-70B (TP=8) | DeepSeek-V3 (TP=16) | Llama-8B (TP=1) |

### 2.6 网络与存储最佳实践

#### RDMA 网络配置

```yaml
# 所有 Worker Pod 必须共享网络命名空间
spec:
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet
  containers:
    - name: vllm-worker
      securityContext:
        capabilities:
          add: ["IPC_LOCK"]
      env:
        # RDMA 配置
        - name: NCCL_IB_DISABLE
          value: "0"
        - name: NCCL_IB_GID_INDEX
          value: "3"
        - name: NCCL_SOCKET_IFNAME
          value: "eth0"
        - name: NCCL_DEBUG
          value: "WARN"
        # Mooncake 配置
        - name: VLLM_MOONCAKE_BOOTSTRAP_PORT
          value: "17860"
```

#### 共享存储（模型权重）

```yaml
# 推荐使用 ReadWriteMany PVC 或 HostPath（单集群）
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: model-pvc
  namespace: vllm
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: nfs-storage
  resources:
    requests:
      storage: 500Gi
```

#### K8s NetworkPolicy（P/D 隔离）

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: pd-network
  namespace: vllm
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Router 可访问所有 P/D
    - from:
        - podSelector:
            matchLabels:
              app: vllm-pd-router
    # P/D 之间需要 RDMA 通信
    - from:
        - podSelector: {}
      ports:
        - port: 14579  # KV Transfer
        - port: 17860  # Mooncake Bootstrap
  egress:
    - to: []
```

### 2.7 弹性伸缩策略

#### Prefill 组：基于队列深度缩容

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: prefill-scaler
  namespace: vllm
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: vllm-prefill
  minReplicas: 1
  maxReplicas: 8
  metrics:
    # 自定义指标：Prefill 请求队列深度
    - type: Pods
      pods:
        metric:
          name: vllm_prefill_queue_depth
        target:
          type: AverageValue
          averageValue: "10"
    - type: Resource
      resource:
        name: nvidia.com/gpu utilization
        target:
          type: Utilization
          averageUtilization: 70
```

#### Decode 组：基于并发请求数缩容

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: decode-scaler
  namespace: vllm
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: vllm-decode
  minReplicas: 1
  maxReplicas: 8
  metrics:
    - type: Pods
      pods:
        metric:
          name: vllm_decode_running_requests
        target:
          type: AverageValue
          averageValue: "128"
```

### 2.8 健康检查与故障恢复

```yaml
# Prefill/Decode 都需要配置完整的探针
livenessProbe:
  httpGet:
    path: /health
    port: 8100
  initialDelaySeconds: 180    # 模型加载需要时间
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health
    port: 8100
  initialDelaySeconds: 60
  periodSeconds: 10
  failureThreshold: 3

startupProbe:
  httpGet:
    path: /health
    port: 8100
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 30        # 最多等 300s 启动
```

### 2.9 关键调优参数

| 组件 | 参数 | 推荐值 | 说明 |
|------|------|--------|------|
| **Prefill** | `--max-num-seqs` | 64 | Prefill 并发批大小 |
| **Prefill** | `--max-model-len` | 8192 | Prefill 最大序列长度 |
| **Prefill** | `--gpu-memory-utilization` | 0.90 | Prefill GPU 显存利用率 |
| **Decode** | `--max-num-seqs` | 256 | Decode 并发批大小 |
| **Decode** | `--max-model-len` | 8192 | Decode 最大序列长度 |
| **Decode** | `--gpu-memory-utilization` | 0.95 | Decode 更激进使用显存 |
| **Router** | 连接池大小 | P 数 × 2 | 保持到每个 P/D 的连接 |
| **Router** | 超时时间 | 30s | Prefill 端超时 |
| **KV Transfer** | `kv_buffer_size` | 2e9 | KV 传输 buffer（2GB） |
| **KV Transfer** | `kv_buffer_device` | `cuda` | GPU 直传 |
| **Ray** | `VLLM_RAY_PER_WORKER_GPUS` | 1.0 | 每 Worker 1 GPU |
| **K8s** | `/dev/shm` 大小 | 32Gi | NCCL 通信所需共享内存 |

### 2.10 监控指标

推荐采集的 Prometheus 指标：

```yaml
# P/D 分离关键监控
- vllm:num_requests_running          # 运行中请求数
- vllm:num_requests_waiting          # 等待中请求数
- vllm:gpu_cache_usage_perc          # KV cache 使用率
- vllm:avg_generation_throughput     # 生成吞吐 (tokens/s)
- vllm:kv_transfer_latency           # KV 传输延迟
- vllm:kv_transfer_success_rate      # KV 传输成功率
- vllm:e2e_request_latency_seconds   # 端到端延迟
```

---

## 附录：源码关键文件索引

### vLLM Ray 相关

| 组件 | 文件路径 |
|------|----------|
| Ray Executor V2 | `vllm/v1/executor/ray_executor_v2.py` |
| Ray Distributed Executor | `vllm/v1/executor/ray_executor.py` |
| Ray Worker Wrapper | `vllm/v1/executor/ray_utils.py` |
| Ray 环境变量管理 | `vllm/ray/ray_env.py` |
| Ray PP 通信器 | `vllm/distributed/device_communicators/ray_communicator.py` |
| 多节点启动脚本 | `examples/online_serving/multi-node-serving.sh` |
| Ray 集群脚本 | `examples/online_serving/run_cluster.sh` |
| P/D Proxy | `benchmarks/disagg_benchmarks/disagg_prefill_proxy_server.py` |
| Helm Chart | `examples/online_serving/chart-helm/` |
| KV Transfer Config | `vllm/config/kv_transfer.py` |
| 并行配置 | `vllm/config/parallel.py` |

### SGLang 部署参考

| 组件 | 文件路径 |
|------|----------|
| K8s StatefulSet | `sglang/docker/k8s-sglang-distributed-sts.yaml` |
| K8s Service | `sglang/docker/k8s-sglang-service.yaml` |
| LWS PD 部署指南 | `sglang/docs/references/multi_node_deployment/lws_pd/lws_pd_deploy.md` |
| PD 分离文档 | `sglang/docs/advanced_features/pd_disaggregation.md` |
| Router Launcher | `sglang/sgl-model-gateway/bindings/python/src/sglang_router/launch_router.py` |
