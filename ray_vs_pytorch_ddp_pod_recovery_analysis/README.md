# Ray RL 训练 vs PyTorch DDP 训练故障恢复机制深度对比：为什么 Ray 支持 Pod 级恢复

> 基于华为云 ModelArts 故障恢复场景与 Ray 源码，深度分析为什么 Ray 的强化学习任务在节点故障时可以做 Pod 级重建（只替换故障 Pod），而 PyTorch DDP 等主流训练框架必须做 Job 级重建（销毁所有 Pod），从五个架构维度给出根因分析。

---

## 一、问题背景

在华为云 ModelArts 训练作业故障恢复体系中，有两种恢复粒度：

| 恢复策略 | 粒度 | 适用条件 |
|----------|------|---------|
| **Pod 重调度** | 只替换故障 Pod，保留正常 Pod | 训练框架支持运行中动态替换成员 |
| **隔离式 Job 重调度** | 销毁所有 Pod，完全重建 | 通用兜底，适用于所有框架 |

**核心问题**：为什么 Ray 的 RL 训练可以做 Pod 级恢复，而 PyTorch DDP / DeepSpeed / Megatron 等框架必须做 Job 级恢复？

答案在于 **Ray 与 PyTorch DDP 在架构设计上的五个根本差异**。

---

## 二、架构对比总览

```
================== Ray RL 训练架构 ==================

  ┌──────────────────────────────────────────────────────┐
  │                    GCS（全局控制面）                     │
  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
  │  │Actor Table│  │Object   │  │Placement Group    │  │
  │  │(ID→状态)  │  │Table    │  │Table              │  │
  │  └──────────┘  └──────────┘  └──────────────────┘  │
  └──────────┬───────────────────────────────────────────┘
             │ RPC 通信（非集合通信）
  ┌──────────┴───────────────────────────────────────────┐
  │                  Ray Worker 节点                       │
  │  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
  │  │Actor-A  │  │Actor-B  │  │Actor-C  │  ...         │
  │  │(EnvRun) │  │(Learner)│  │(EnvRun) │              │
  │  └────┬────┘  └────┬────┘  └────┬────┘              │
  │       │            │            │                     │
  │       └──── Object Store ───────┘                    │
  │         （共享内存对象池，去中心化）                     │
  └──────────────────────────────────────────────────────┘

  ✅ Pod 故障 → 重建对应 Actor → GCS 重新调度 → 其他 Actor 不受影响


================== PyTorch DDP 训练架构 ==================

  ┌──────────────────────────────────────────────────────┐
  │           NCCL/HCCL 通信组（静态拓扑）                  │
  │                                                        │
  │  Rank-0 ←──── AllReduce ────→ Rank-1                  │
  │    ↑                              ↑                    │
  │    │         AllReduce             │                   │
  │    ↓                              ↓                    │
  │  Rank-2 ←──── AllReduce ────→ Rank-3                  │
  │                                                        │
  │  world_size=4, 初始化时固定，运行中不可变                │
  └──────────────────────────────────────────────────────┘

  ❌ Pod 故障（如 Rank-2 宕机）→ AllReduce 死锁
     → Rank-0/1/3 等待 Rank-2 响应（永远不会来）
     → 新 Pod 无法加入已建立的通信组
     → 必须 Job 级重建
```

---

## 三、五大架构差异详解

### 3.1 差异一：身份标识 — Actor ID vs 固定 Rank

#### Ray：Actor ID 与物理位置解耦

```
Ray 的身份模型：

  Actor 创建时：
    1. GCS 分配全局唯一的 ActorID（如 a1b2c3d4...）
    2. ActorID 与当前所在节点无关
    3. ActorID 映射存储在 GCS Actor Table 中

  节点故障后：
    1. GCS 检测到节点下线
    2. 标记该节点上所有 Actor 为 DEAD
    3. 创建新的 Actor 实例（新的物理位置）
    4. ActorID 不变 → 其他 Actor 通过 ActorID 找到新实例
    5. GCS 更新 ActorID → 新物理位置的映射

  源码参考（ray/src/ray/gcs/gcs_actor_manager.cc）：
    → OnNodeDead() 方法处理节点故障
    → 重建 Actor：重新调度到健康节点
```

#### PyTorch DDP：Rank 与进程强绑定

```
PyTorch DDP 的身份模型：

  训练启动时：
    1. 每个 Pod 被分配固定 rank（0, 1, 2, ...）
    2. rank 写入 NCCL/HCCL 通信组配置
    3. world_size 在初始化时确定

  节点故障后：
    1. Rank-2 所在 Pod 死亡
    2. 其他 Rank（0/1/3）持有的 NCCL Communicator 仍引用旧 Rank-2
    3. 新 Pod 即使使用相同 rank 编号，NCCL Communicator 也无法识别
       （Communicator 绑定了进程级的内部状态，不是简单的编号匹配）
    4. 无法动态重建通信组

  源码参考（PyTorch）：
    → torch.distributed.init_process_group() 初始化后不可变
    → ProcessGroupNCCL 创建后成员固定
```

**关键差异总结**：

| 维度 | Ray Actor | PyTorch DDP Rank |
|------|----------|-----------------|
| 标识类型 | 全局唯一 ActorID | 连续整数 rank |
| 与物理位置的关系 | 解耦（GCS 路由） | 强绑定（初始化时确定） |
| 可替换性 | 可在不同节点重建 | NCCL Communicator 绑定进程 |
| 全局状态存储 | GCS Actor Table（分布式 KV） | 各进程本地持有 Communicator |

---

### 3.2 差异二：通信模型 — RPC + Object Store vs 集合通信

#### Ray：点对点 RPC + 共享内存 Object Store

```
Ray 的通信架构：

  通信方式：
    1. RPC（Remote Procedure Call）：Actor 之间直接调用
       → 点对点通信，不要求所有 Actor 同时在线
    2. Object Store：共享内存对象池
       → 写入者将对象放入 Object Store
       → 读取者通过 ObjectID 获取
       → 去中心化，不依赖全局同步

  故障场景：
    Actor-A 向 Actor-B 发送 RPC 调用
      → Actor-B 所在节点故障
      → RPC 调用失败，返回错误
      → Actor-A 可以重试（GCS 提供新路由）
      → 不需要所有 Actor 同时参与

  源码参考：
    → src/ray/core_worker/core_worker.cc: SubmitTask()
    → RPC 通过 gRPC 实现，天然支持重试和超时
```

#### PyTorch DDP：集合通信（AllReduce/AllGather）

```
PyTorch DDP 的通信架构：

  通信方式：
    1. AllReduce：梯度聚合（所有 Rank 参与）
    2. AllGather：参数收集（所有 Rank 参与）
    3. Broadcast：参数广播（所有 Rank 参与）
    4. ReduceScatter：梯度分片（所有 Rank 参与）

  关键特征：
    → 每次梯度同步都需要所有 Rank 同时参与
    → 缺一个 Rank → AllReduce 阻塞 → 死锁
    → 没有"部分成员"的降级机制

  故障场景：
    Step N: 前向传播完成，开始反向传播
      → AllReduce 梯度同步
      → Rank-2 已死亡
      → Rank-0/1/3 在 AllReduce 中等待 Rank-2 的数据
      → 永远等不到 → 死锁
      → 整个训练卡住
```

**通信模型对比**：

| 维度 | Ray (RPC + Object Store) | PyTorch DDP (NCCL AllReduce) |
|------|--------------------------|------------------------------|
| 通信模式 | 点对点 | 集合通信（全成员同步） |
| 是否需要所有成员 | 否 | 是（缺一死锁） |
| 故障影响范围 | 仅影响通信对端 | 影响所有成员 |
| 重试能力 | RPC 天然支持重试 | NCCL 操作不支持部分重试 |
| 带宽利用 | 按需通信 | 固定模式的环形/树形通信 |

---

### 3.3 差异三：全局状态管理 — GCS vs 分布式共识

#### Ray：GCS（Global Control Service）全局视图

```
Ray GCS 的架构：

  ┌─────────────────────────────────────────────────┐
  │                    GCS Server                     │
  │                                                   │
  │  ┌──────────────┐  ┌──────────────────────────┐ │
  │  │ Actor Table   │  │ Placement Group Table    │ │
  │  │               │  │                          │ │
  │  │ ActorID → {   │  │ PGID → {                 │ │
  │  │   state,      │  │   bundles: [             │ │
  │  │   node_addr,  │  │     {node, resource},    │ │
  │  │   job_id,     │  │     ...                  │ │
  │  │   ...         │  │   ]                      │ │
  │  │ }             │  │ }                        │ │
  │  └──────────────┘  └──────────────────────────┘ │
  │                                                   │
  │  ┌──────────────┐  ┌──────────────────────────┐ │
  │  │ Object Table  │  │ Node Table              │ │
  │  │               │  │                          │ │
  │  │ ObjectID → {  │  │ NodeID → {              │ │
  │  │   location,   │  │   state,                │ │
  │  │   size,       │  │   resources,            │ │
  │  │   ...         │  │   ...                   │ │
  │  │ }             │  │ }                       │ │
  │  └──────────────┘  └──────────────────────────┘ │
  └─────────────────────────────────────────────────┘

  节点故障时 GCS 的处理：
    1. 检测到节点下线（心跳超时）
    2. 更新 Node Table：标记节点为 DEAD
    3. 遍历 Actor Table：标记该节点上所有 Actor 为 DEAD
    4. 触发 Actor 重建流程（gcs_actor_manager.cc）
    5. 触发 Placement Group 重调度（gcs_placement_group_scheduler.cc）
    6. 所有其他节点通过 GCS 订阅获得最新状态

  源码参考：
    → src/ray/gcs/gcs_actor_manager.cc: OnNodeDead()
    → src/ray/gcs/gcs_placement_group_scheduler.cc
    → src/ray/gcs/gcs_server.cc
```

#### PyTorch DDP：无全局状态管理

```
PyTorch DDP 的状态管理：

  状态分布：
    → 每个 Rank 独立持有自己的 NCCL Communicator
    → 没有"全局控制面"协调各 Rank 状态
    → world_size 和 rank_table 在初始化时加载，之后静态不变

  节点故障时的状态：
    → 存活 Rank 不知道故障 Rank 的状态
    → 存活 Rank 的 NCCL Communicator 仍持有旧配置
    → 没有机制通知存活 Rank 更新拓扑
    → 没有机制让新 Rank 加入已有通信组

  结论：
    → 没有 GCS 等全局控制面，无法在运行中重新协调
    → 必须销毁所有进程，从零重建整个通信拓扑
```

---

### 3.4 差异四：RL 训练框架的弹性设计 — EnvRunnerGroup

#### Ray RLlib：弹性 EnvRunnerGroup

```python
# ray/rllib/env/env_runner_group.py 核心逻辑

class EnvRunnerGroup:
    """
    RLlib 的 EnvRunner 组，支持弹性伸缩和故障恢复。

    关键配置：
      - restart_failed_env_runners=True  # 自动重启故障 EnvRunner
      - num_env_runners=N                # 目标 EnvRunner 数量
    """

    def __init__(self, ..., restart_failed_env_runners=True):
        self.restart_failed_env_runners = restart_failed_env_runners
        self.env_runners = {}  # worker_id → EnvRunner Actor

    def _restart_env_runner(self, worker_id):
        """
        单个 EnvRunner 故障后重建：

        1. 在健康节点上创建新 EnvRunner Actor
        2. 从 Learner 同步最新 Policy 权重
        3. 重新初始化环境（Environment）
        4. 恢复数据收集

        其他 EnvRunner 不受影响！
        """
        # 创建新 Actor
        new_runner = self._make_worker(worker_id)
        # 同步 Policy 权重
        new_runner.sync_policy_from(learner)
        # 替换旧引用
        self.env_runners[worker_id] = new_runner

    def on_worker_failure(self, failed_worker_id):
        """节点故障回调"""
        if self.restart_failed_env_runners:
            self._restart_env_runner(failed_worker_id)
            # 只重建一个 EnvRunner，其余继续运行
```

**RLlib 训练中的角色分离**：

```
RLlib 训练架构（以 PPO 为例）：

  ┌────────────────────────────────────────────────┐
  │              Learner Actor（策略学习）            │
  │  - 持有 Policy 模型                              │
  │  - 接收各 EnvRunner 的经验数据                    │
  │  - 计算 PPO loss 并更新参数                       │
  │  - 广播新 Policy 权重给所有 EnvRunner             │
  └───────────────┬────────────────────────────────┘
                  │ Object Store（经验数据传输）
  ┌───────────────┼────────────────────────────────┐
  │          EnvRunner Group                        │
  │                                                │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐       │
  │  │Runner-0  │ │Runner-1  │ │Runner-2  │ ...   │
  │  │(Actor)   │ │(Actor)   │ │(Actor)   │       │
  │  │环境交互   │ │环境交互   │ │环境交互   │       │
  │  └──────────┘ └──────────┘ └──────────┘       │
  │                                                │
  │  每个 Runner 独立运行，互不依赖                    │
  └────────────────────────────────────────────────┘

  Runner-1 故障 → 只重建 Runner-1
    → Learner 继续接收 Runner-0/2 的数据
    → Runner-1 重建后同步最新 Policy 权重
    → 训练不中断
```

#### PyTorch DDP：刚性通信拓扑

```
PyTorch DDP 的刚性设计：

  每个 Step 的梯度同步流程：
    1. 前向传播（各 Rank 独立计算）
    2. 反向传播（各 Rank 独立计算梯度）
    3. AllReduce 梯度（所有 Rank 必须同时参与） ← 瓶颈
    4. 更新参数（各 Rank 独立）

  Step 3 中任何一个 Rank 缺失 → AllReduce 阻塞 → 死锁

  没有"跳过故障 Rank"的机制：
    → NCCL 的 AllReduce 要求 world_size 个成员全部参与
    → 不支持 world_size 的动态变更
    → 不支持运行中添加/移除成员
```

---

### 3.5 差异五：数据恢复 — Lineage Reconstruction

#### Ray：基于 Lineage 的对象重建

```
Ray 的 Lineage 机制：

  任务执行产生对象：
    Task-A(obj_1) → Task-B(obj_2) → Task-C(obj_3)
                                        ↓
                                    存入 Object Store

  节点故障，obj_2 丢失：
    1. Ray 检测到 obj_2 不可达
    2. 查询 Lineage：obj_2 由 Task-B 产生
    3. Task-B 的输入是 obj_1 → 检查 obj_1 是否存在
    4. obj_1 存在 → 重新执行 Task-B → 重建 obj_2
    5. 下游 Task-C 可以继续

  源码参考：
    → src/ray/core_worker/task_manager.cc
    → Lineage 重建逻辑：根据任务 DAG 重新执行丢失的任务
```

#### PyTorch DDP：无数据恢复机制

```
PyTorch DDP 的数据状态：

  训练状态保存在：
    1. 模型权重 → 分布式保存在各 Rank 的 GPU/NPU 内存
    2. 优化器状态 → 各 Rank 独立持有
    3. 梯度缓冲区 → AllReduce 的中间状态

  Rank 故障后：
    → 该 Rank 的模型分片丢失（张量并行场景）
    → 该 Rank 的优化器状态丢失
    → 正在进行的 AllReduce 中间状态丢失
    → 没有 Lineage 机制恢复这些数据
    → 只能从 Checkpoint 恢复（需要 Job 级重建）
```

---

## 四、RL 训练 vs 大模型训练的部署架构差异

### 4.1 RL 训练的天然优势

RL 训练的架构天然适合 Pod 级恢复，因为其**角色分离**和**异步通信**的设计：

```
RL 训练（如 PPO/GRPO）的数据流：

  ┌─────────┐   经验数据   ┌─────────┐   Policy更新   ┌─────────┐
  │ 环境     │ ─────────→ │ Learner │ ────────────→ │ EnvRun  │
  │ 交互     │  (异步)     │ 训练    │  (异步广播)    │ 群组     │
  │ (多个)   │             │ (1个)   │               │ (多个)   │
  └─────────┘             └─────────┘               └─────────┘

  特征：
    ✅ EnvRunner 之间互不依赖（独立环境交互）
    ✅ 数据传输是异步的（通过 Object Store）
    ✅ Learner 是单点的，但可以快速重建
    ✅ 不需要 AllReduce 等集合通信
```

### 4.2 大模型训练的刚性约束

大模型训练（如 LLM 预训练）使用数据并行 + 张量并行 + 流水线并行，每一步都需要全局同步：

```
大模型训练（3D 并行）的数据流：

  ┌───────┐ AllReduce  ┌───────┐ AllReduce  ┌───────┐
  │ TP-0  │ ←────────→ │ TP-1  │ ←────────→ │ TP-2  │
  │ PP-0  │            │ PP-0  │            │ PP-0  │
  └───┬───┘            └───┬───┘            └───┬───┘
      │ P2P send          │ P2P send          │ P2P send
  ┌───┴───┐            ┌───┴───┐            ┌───┴───┐
  │ TP-0  │            │ TP-1  │            │ TP-2  │
  │ PP-1  │            │ PP-1  │            │ PP-1  │
  └───────┘            └───────┘            └───────┘

  每一步训练：
    → TP AllReduce（张量并行组内同步）
    → PP P2P send/recv（流水线阶段间传递）
    → DP AllReduce（数据并行组内梯度同步）
    → 任一环节中断 → 整个训练卡住
```

---

## 五、故障恢复流程对比

### 5.1 Ray RL 训练的 Pod 级恢复

```
节点故障发生（假设 Runner-1 所在节点宕机）
  │
  ├─ Step 1: GCS 检测到节点心跳超时
  │   → src/ray/gcs/gcs_server.cc: OnNodeDead()
  │
  ├─ Step 2: 标记该节点上所有 Actor 为 DEAD
  │   → src/ray/gcs/gcs_actor_manager.cc: OnNodeDead()
  │   → 更新 Actor Table
  │
  ├─ Step 3: 通知相关组件
  │   → Learner 收到通知（Runner-1 故障）
  │   → EnvRunnerGroup 收到通知
  │
  ├─ Step 4: EnvRunnerGroup 触发重建
  │   → restart_failed_env_runners=True
  │   → 在健康节点上创建新 Runner-1' Actor
  │   → src/ray/gcs/gcs_actor_manager.cc: Reschedule()
  │
  ├─ Step 5: 新 Runner-1' 恢复
  │   → 同步最新 Policy 权重
  │   → 重新初始化环境
  │   → 开始数据收集
  │
  └─ Step 6: 训练继续
      → Learner 继续接收所有 Runner 的数据
      → 整个过程中 Runner-0/2 没有中断

  恢复时间：秒级到分钟级
  影响范围：仅故障 EnvRunner，其他正常
```

### 5.2 PyTorch DDP 训练的 Job 级恢复

```
节点故障发生（假设 Rank-2 所在节点宕机）
  │
  ├─ Step 1: NCCL 检测到 Rank-2 不可达
  │   → AllReduce 操作超时
  │   → 抛出 NCCL Error
  │
  ├─ Step 2: 训练进程异常退出
  │   → Rank-0/1/3 的训练进程崩溃
  │   → 所有 Pod 的训练进程终止
  │
  ├─ Step 3: ModelArts 检测到作业异常
  │   → 触发故障恢复策略
  │   → 尝试 Pod 重调度（大概率失败，通信组无法重建）
  │   → 降级为隔离式 Job 重调度
  │
  ├─ Step 4: 隔离故障节点
  │   → K8s 打 taint 污点
  │   → 防止新 Pod 调度到故障节点
  │
  ├─ Step 5: Job 级重建
  │   → 销毁所有 Pod（包括正常的 Rank-0/1/3）
  │   → 在健康节点上创建全新 Pod
  │   → 重新建立 NCCL 通信拓扑
  │   → 从 Checkpoint 恢复训练状态
  │
  └─ Step 6: 训练继续
      → 从最近 Checkpoint 的 Step 继续训练
      → 丢失最近一次 CKPT 保存后的训练进度

  恢复时间：分钟级到十几分钟
  影响范围：所有 Rank 都需要重建
```

---

## 六、ModelArts 上的配置建议

### 6.1 Ray RL 训练

| 配置项 | 推荐值 | 原因 |
|--------|--------|------|
| 自动重启 | 开启，8~32 次 | Ray 框架本身有恢复能力，重启次数不需太多 |
| Pod 重调度 | 开启，3 次 | Ray 支持 Pod 级恢复，优先使用 |
| 无条件自动重启 | 开启 | 兜底非硬件故障 |
| 作业卡死重启 | 开启 | 防止训练卡死 |
| `restart_failed_env_runners` | True | RLlib 配置，自动重建故障 EnvRunner |
| Checkpoint 保存 | 每 100~200 Step | Ray 恢复也需要从 CKPT 加载 Policy |

### 6.2 PyTorch DDP / DeepSpeed 大模型训练

| 配置项 | 推荐值 | 原因 |
|--------|--------|------|
| 自动重启 | 开启，8~128 次 | 大模型训练耗时长，需要更多重试机会 |
| Pod 重调度 | 开启，3 次 | 大概率失败，但会自动降级 |
| 无条件自动重启 | 开启 | 兜底软件故障 |
| 作业卡死重启 | 开启 | 防止训练卡死（IO 30 分钟无变化） |
| Checkpoint 保存 | 每 500~1000 Step | 大模型 CKPT 大，频率不宜太高 |
| CKPT 存储位置 | SFS Turbo | 实时同步，避免 CKPT 丢失 |

### 6.3 对比总结

| 维度 | Ray RL 训练 | PyTorch DDP 训练 |
|------|-----------|-----------------|
| 恢复粒度 | Pod 级（只替换故障部分） | Job 级（完全重建） |
| 恢复速度 | 秒~分钟级 | 分钟~十几分钟级 |
| 训练中断范围 | 仅故障 EnvRunner | 所有 Rank |
| CKPT 依赖 | 需要（恢复 Policy 权重） | 必须（恢复全部训练状态） |
| 恢复成功率 | 高（框架原生支持） | 高（但代价大） |
| 资源浪费 | 小（正常 Pod 继续运行） | 大（正常 Pod 也要重建） |

---

## 七、各框架 Pod 级恢复能力总览

| 训练框架 | Pod 级恢复 | 原因 | 推荐策略 |
|----------|-----------|------|---------|
| **Ray RLlib** | 支持 | Actor 解耦 + RPC 通信 + GCS 全局视图 + 弹性 EnvRunner | Pod 重调度优先 |
| **Ray Train** | 支持 | Actor 替换 + Object Store + Lineage 重建 | Pod 重调度优先 |
| **TensorFlow PS** | 部分支持 | Worker 可替换，PS 不可变 | 可尝试 Pod 重调度 |
| **PyTorch DDP** | 不支持 | NCCL 静态 ProcessGroup | Job 级重调度 |
| **DeepSpeed ZeRO** | 不支持 | 依赖 NCCL 静态拓扑 | Job 级重调度 |
| **Megatron-LM** | 不支持 | TP+PP+DP 三维静态拓扑 | Job 级重调度 |
| **MindSpore** | 不支持 | 静态 RankTable | Job 级重调度 |
| **Horovod** | 不支持 | 静态 MPI 通信组 | Job 级重调度 |

---

## 八、总结

| 问题 | 回答 |
|------|------|
| Ray 为什么支持 Pod 级恢复？ | 五大架构优势：Actor ID 解耦、RPC 通信、GCS 全局视图、弹性 EnvRunner、Lineage 重建 |
| PyTorch DDP 为什么必须 Job 级恢复？ | NCCL ProcessGroup 不可变 + AllReduce 需全成员参与 + 无全局控制面 |
| 根本差异是什么？ | Ray 是"解耦式"架构（Actor+RPC+GCS），PyTorch DDP 是"紧耦合"架构（Rank+NCCL+无中心） |
| 在 ModelArts 上如何配置？ | Ray 优先 Pod 重调度，PyTorch DDP 开启所有恢复策略兜底 |
| 哪些框架支持 Pod 级恢复？ | Ray（RLlib/Train）、TF PS 模式；其余主流框架均不支持 |

---

## 参考文档

- [Ray GCS Architecture](https://docs.ray.io/en/latest/ray-core/architecture.html)
- [Ray Actor Reconstruction](https://docs.ray.io/en/latest/ray-core/fault_tolerance/actor-fault-tolerance.html)
- [RLlib Fault Tolerance](https://docs.ray.io/en/latest/rllib/fault-tolerance.html)
- [PyTorch Distributed Data Parallel](https://pytorch.org/tutorials/intermediate/ddp_tutorial.html)
- [华为云 ModelArts 训练作业故障恢复](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0019.html)
- [Ray 源码仓库](https://github.com/ray-project/ray)
