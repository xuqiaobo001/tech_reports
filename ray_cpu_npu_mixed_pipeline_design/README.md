# 基于 Ray 的 CPU + NPU 混合管道离线推理与训练方案设计

> 利用 Ray 的异构资源调度能力，设计 CPU 节点做多模态图片数据格式转换、NPU 节点做模型训练的混合管道方案，覆盖数据流水线、集群部署、背压控制、弹性扩缩容与容错恢复。

---

## 一、方案背景与目标

### 1.1 问题场景

多模态大模型训练中，图片数据的预处理（解码、Resize、归一化、格式转换）是 CPU 密集型任务，而模型训练（前向/反向传播）是 NPU/GPU 密集型任务。传统方案通常在同一节点上串行执行预处理和训练，导致：

- **NPU 空闲等待**：预处理耗时长的场景下，NPU 在等待 CPU 完成数据准备
- **CPU 资源浪费**：NPU 训练时，节点上的 CPU 大部分时间空闲
- **无法独立扩展**：预处理瓶颈只能通过加更多"同构节点"解决，成本高

### 1.2 方案目标

| 目标 | 说明 |
|------|------|
| 异构解耦 | CPU 做预处理、NPU 做训练，各司其职，互不浪费 |
| 弹性伸缩 | CPU Worker 可按需扩缩容，NPU Worker 固定，成本最优 |
| 背压控制 | NPU 空闲时不堆积数据，CPU 慢时不浪费 NPU 算力 |
| 容错恢复 | Actor 级恢复，不需 Job 级重建，训练中断少 |
| 独立扩展 | 加 NPU 节点加速训练，加 CPU 节点加速预处理 |

### 1.3 为什么选择 Ray

| Ray 能力 | 解决的问题 |
|----------|-----------|
| 资源感知调度 | 自动将 CPU 任务调度到 CPU 节点、NPU 任务调度到 NPU 节点 |
| Ray Data Pipeline | 原生支持 map_batches() 分阶段处理，自动背压 |
| Actor 池 | 预处理和训练各用 Actor 池，有状态复用（如预加载 Processor/Model） |
| Object Store | CPU 和 NPU 之间零拷贝数据传输 |
| GCS 全局视图 | Actor 故障自动重建，支持 Pod 级恢复 |
| Autoscaler | CPU Worker 弹性扩缩容 |

---

## 二、整体架构

### 2.1 集群架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Ray Cluster                                 │
│                                                                   │
│  ┌─────┐        ┌──────────────────────┐        ┌────────────┐  │
│  │Head │◄──────►│    Ray GCS            │◄──────►│ Dashboard  │  │
│  │Node │        │  (资源视图+调度)       │        │ (监控)      │  │
│  └─────┘        └──────────┬───────────┘        └────────────┘  │
│                            │                                      │
│         ┌──────────────────┼──────────────────┐                  │
│         ▼                  ▼                  ▼                  │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐           │
│  │ CPU Worker-0 │   │ CPU Worker-1 │   │ CPU Worker-N │  ...     │
│  │              │   │              │   │              │           │
│  │ Resources:   │   │ Resources:   │   │ Resources:   │           │
│  │ CPU: 32      │   │ CPU: 32      │   │ CPU: 32      │           │
│  │ NPU: 0       │   │ NPU: 0       │   │ NPU: 0       │           │
│  │ MEM: 128GB   │   │ MEM: 128GB   │   │ MEM: 128GB   │           │
│  │              │   │              │   │              │           │
│  │ [Preprocess  │   │ [Preprocess  │   │ [Preprocess  │           │
│  │  Actor Pool] │   │  Actor Pool] │   │  Actor Pool] │           │
│  └─────────────┘   └─────────────┘   └─────────────┘           │
│         │                  │                  │                  │
│         │     Ray Object Store（共享内存）     │                  │
│         └──────────────────┼──────────────────┘                  │
│                            │                                      │
│         ┌──────────────────┼──────────────────┐                  │
│         ▼                  ▼                  ▼                  │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐           │
│  │ NPU Worker-0 │   │ NPU Worker-1 │   │ NPU Worker-N │  ...     │
│  │              │   │              │   │              │           │
│  │ Resources:   │   │ Resources:   │   │ Resources:   │           │
│  │ CPU: 8       │   │ CPU: 8       │   │ CPU: 8       │           │
│  │ NPU: 4       │   │ NPU: 4       │   │ NPU: 4       │           │
│  │ MEM: 256GB   │   │ MEM: 256GB   │   │ MEM: 256GB   │           │
│  │              │   │              │   │              │           │
│  │ [Trainer     │   │ [Trainer     │   │ [Trainer     │           │
│  │  Actor]      │   │  Actor]      │   │  Actor]      │           │
│  └─────────────┘   └─────────────┘   └─────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流水线总览

```
数据流水线（Ray Data Pipeline）：

  ┌────────────┐    ┌──────────────┐    ┌────────────┐    ┌──────────┐
  │ Stage 1    │    │ Stage 2      │    │ Stage 3    │    │ Stage 4  │
  │ 读取原始   │───►│ 图片预处理   │───►│ 批量组装   │───►│ NPU训练  │
  │ 图片路径   │    │ (CPU节点)    │    │ (CPU节点)  │    │ (NPU节点)│
  └────────────┘    └──────────────┘    └────────────┘    └──────────┘
  资源: CPU=1       资源: CPU=4         资源: CPU=2       资源: NPU=1
  并发: 高          并发: 高            并发: 中          并发: 低

  Ray Data 的关键机制：
    → .read_parquet() / .read_images()：按 batch 读取
    → .map_batches(preprocess, compute=...)：指定 CPU 节点执行
    → .map_batches(train_step, compute=...)：指定 NPU 节点执行
    → Ray 自动在 CPU Worker 和 NPU Worker 之间搬运数据
    → 通过 Object Store 实现零拷贝传输
```

---

## 三、四阶段详细设计

### 3.1 Stage 1：数据读取（轻量）

**功能**：从存储系统读取训练数据清单，按块分割。

```
输入：训练数据清单文件（Parquet / JSONL 格式）
  每行包含：
    - 图片路径（SFS Turbo / OBS 路径）
    - 文本内容（对话/指令/标签）
    - 元信息（图片尺寸、格式等）

处理：
  - 按行读取清单文件
  - 分割为 N 个数据块（block）
  - 每个块包含若干行的数据描述

Ray Data 方式：
  ds = ray.data.read_parquet("sfs://data/train_manifest.parquet")
     .repartition(num_blocks=N)

输出：每个 block 是一个 Table（若干行数据描述）
资源：CPU=1（轻量读取，不需要太多算力）
```

### 3.2 Stage 2：图片预处理（CPU 节点，高并发）

**功能**：在 CPU 上完成所有图片处理和多模态格式转换。

```
预处理流程：

  ① 图片解码
     JPEG / PNG / WebP → numpy / PIL Image
     ↳ 使用 Pillow-SIMD 或 TurboJPEG 加速

  ② 图片变换
     Resize / Crop / Pad → 统一尺寸（如 336×336 / 448×448）
     ↳ 动态分辨率模型需保留原始尺寸信息

  ③ 归一化
     pixel_values / 255.0
     减均值 (mean=[0.485, 0.456, 0.406])
     除方差 (std=[0.229, 0.224, 0.225])

  ④ 多模态格式转换
     - pixel_values tensor: [B, C, H, W]
     - image_grid_pinpoint: 动态分辨率模型（如 Qwen2-VL）需要
     - image_attention_mask: 图片 token 的 attention mask

  ⑤ 文本 Tokenization
     - 使用对应模型的 Processor / Tokenizer
     - input_ids: [B, SeqLen]
     - attention_mask: [B, SeqLen]
     - labels: [B, SeqLen]

  ⑥ 组装模型输入格式
     输出 dict：
     {
       "pixel_values": Tensor[B, C, H, W],
       "input_ids":    Tensor[B, SeqLen],
       "attention_mask": Tensor[B, SeqLen],
       "labels":       Tensor[B, SeqLen],
       "image_grid_pinpoint": [...],
     }
```

**Ray 调度策略**：

```
Actor 池配置：
  - 每个 PreprocessActor 预加载 Processor（分词器+图片处理器）
  - 避免每次调用重新初始化
  - Actor 池大小根据 CPU Worker 数量动态调整

  资源标记：
    @ray.remote(num_cpus=4, num_gpus=0, resources={"NPU": 0})
    → Ray 调度器自动分配到 CPU Worker 节点

  每个 CPU Worker（32核）上运行 8 个 PreprocessActor（32/4=8）

  预处理 Actor 池：
    CPU Worker-0: PreprocessActor × 8
    CPU Worker-1: PreprocessActor × 8
    CPU Worker-N: PreprocessActor × 8
    → 总并发 = 8 × N 个 CPU Worker

  性能估算（单 Actor）：
    - 图片解码+resize: ~50-200 样本/秒（取决于图片大小）
    - Tokenization: ~500-2000 序列/秒
    - 综合: ~50-200 样本/秒/Actor
    - 10 个 CPU Worker × 8 Actor = 80 并发 → 4000-16000 样本/秒
```

### 3.3 Stage 3：批量组装与传输优化

**功能**：将预处理后的样本按 batch_size 组装，优化传输。

```
批量组装：
  - 将预处理后的样本按 batch_size 组装
  - 同 batch 内做动态 padding（对齐到最长序列）
  - 转换为 NPU 可直接消费的格式（torch.Tensor）

传输路径：
  CPU Worker 内存
    → Ray Object Store（共享内存，零拷贝）
      → NPU Worker 内存
        → NPU HBM（.npu()）

  传输效率：
    同物理节点：内存拷贝（~10GB/s）
    跨节点：    RDMA / TCP（~10-100Gbps）
    ↳ 推荐同一 CCE 集群内，减少跨节点传输
```

### 3.4 Stage 4：NPU 训练（NPU 节点）

**功能**：在 NPU 上执行模型训练。

```
训练流程：
  ① 从 Object Store 获取预处理好的 batch
  ② 将 tensor 搬到 NPU HBM（.npu()）
  ③ 前向传播（模型推理）
  ④ Loss 计算
  ⑤ 反向传播 + 梯度更新
  ⑥ 周期性保存 Checkpoint

Ray 调度：
  TrainerActor 常驻在 NPU 节点上
  资源标记：@ray.remote(num_cpus=2, resources={"NPU": 4})
  → Ray 调度器分配到 NPU Worker 节点

训练模式：

  ┌─────────────────────────────────────────────────────┐
  │ 模式一：单卡训练                                     │
  │   1 NPU per TrainerActor                             │
  │   适用于：SFT 微调、小模型                            │
  ├─────────────────────────────────────────────────────┤
  │ 模式二：数据并行                                     │
  │   N 个 NPU，每卡一个 TrainerActor                    │
  │   AllReduce 梯度同步（HCCL）                          │
  │   适用于：中等模型微调                                │
  ├─────────────────────────────────────────────────────┤
  │ 模式三：3D 并行（TP+PP+DP）                          │
  │   需使用 Ray Train 的 TorchTrainer 封装              │
  │   多个 NPU Worker 组成通信组                          │
  │   适用于：大模型预训练                                │
  └─────────────────────────────────────────────────────┘
```

---

## 四、集群部署架构

### 4.1 在 CCE / ModelArts 上的部署

```
┌─────────────────────────────────────────────────────────┐
│              Ray on ModelArts / CCE                      │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Head Pod                                             │ │
│  │   ray start --head                                   │ │
│  │   --resources='{"CPU_node": 1}'                      │ │
│  │   GCS + Dashboard + Driver                           │ │
│  └──────────────────────┬──────────────────────────────┘ │
│                         │                                  │
│  ┌──────────────────────┼──────────────────────────────┐ │
│  │ CPU Worker Pool (弹性)                               │ │
│  │                                                       │ │
│  │  ┌─────────────────────────────────────────────┐     │ │
│  │  │ CPU Worker Pod                               │     │ │
│  │  │   ray start --address=HEAD:6379              │     │ │
│  │  │   --resources='{"CPU": 32, "CPU_only": 1}'   │     │ │
│  │  │   --num-cpus=32                              │     │ │
│  │  │                                               │     │ │
│  │  │   纯 CPU ECS 实例 或 昇腾服务器 CPU 侧        │     │ │
│  │  └─────────────────────────────────────────────┘     │ │
│  │   × M 个 Pod（按需扩缩容，2~20）                      │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ NPU Worker Pool（固定）                               │ │
│  │                                                       │ │
│  │  ┌─────────────────────────────────────────────┐     │ │
│  │  │ NPU Worker Pod                               │     │ │
│  │  │   ray start --address=HEAD:6379              │     │ │
│  │  │   --resources='{"CPU": 8, "NPU": 4}'         │     │ │
│  │  │   --num-cpus=8                               │     │ │
│  │  │                                               │     │ │
│  │  │   昇腾服务器（加载 CANN + PyTorch-NPU）       │     │ │
│  │  └─────────────────────────────────────────────┘     │ │
│  │   × K 个 Pod（固定，每个独占一台昇腾服务器）         │ │
│  └──────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 4.2 节点角色与资源规划

| 节点类型 | 规格 | 数量 | Ray 资源标记 | 用途 |
|----------|------|------|-------------|------|
| **Head** | 8C16G | 1 | `{"CPU_node": 1}` | GCS + 调度 + 监控 |
| **CPU Worker** | 32C128G | 2~20（弹性） | `{"CPU": 32, "CPU_only": 1}` | 图片预处理 |
| **NPU Worker** | 8C256G + 4×NPU | 固定 K | `{"CPU": 8, "NPU": 4}` | 模型训练 |

### 4.3 资源隔离策略

```
Ray 资源调度隔离：

  CPU Worker 的 PreprocessActor：
    @ray.remote(num_cpus=4, resources={"CPU_only": 1})
    → 不会调度到 NPU Worker（因为 NPU Worker 没有声明 CPU_only 资源）
    → 确保预处理只在 CPU 节点执行

  NPU Worker 的 TrainerActor：
    @ray.remote(num_cpus=2, resources={"NPU": 4})
    → 只调度到有 NPU 资源的节点
    → 确保训练只在 NPU 节点执行

  自定义资源标签（推荐）：
    CPU_only: 标记纯 CPU 节点
    NPU: 标记 NPU 数量
    → 双维度隔离，避免资源争抢
```

---

## 五、背压与流量控制

### 5.1 问题分析

```
CPU 预处理速度 vs NPU 训练速度不匹配

  CPU 预处理：~100-500 样本/秒/Actor（图片解码+resize+tokenize）
  NPU 训练：  ~1-10 步/秒（每步消耗 batch_size 个样本）

  如果 CPU 预处理太快 → Object Store 堆积大量 batch → 内存溢出
  如果 CPU 预处理太慢 → NPU 空闲等待 → 算力浪费
```

### 5.2 Ray Data 自动背压机制

```
Ray Data Pipeline 执行模型：

  read() → map_batches(preprocess) → map_batches(train)
            ↑ CPU 节点                  ↑ NPU 节点

  背压逻辑：
    → 下游 train Actor 全忙 → 上游 preprocess 暂停产出
    → Object Store 容量达上限 → 自动减慢上游速度
    → 下游消费加速 → 上游自动恢复产出

  配置方式：
    .map_batches(
        preprocess_fn,
        compute=ActorPoolStrategy(min=4, max=16),
        batch_size=32
    )
    → Actor 池大小根据下游消费速度自动伸缩
```

### 5.3 显式流量控制参数

```
可调参数：

  | 参数 | 说明 | 推荐值 |
  |------|------|--------|
  | batch_size | 每次 map_batches 处理的样本数 | 16~64（图片）/ 32~128（文本） |
  | min_actors | Actor 池最小大小 | 4 |
  | max_actors | Actor 池最大大小 | 16~32 |
  | prefetch_batches | 预取 batch 数量 | 2~4（NPU Worker 数量的 1~2 倍） |
  | Object Store 内存上限 | 防止堆积过多 | 集群内存的 30%~50% |

  调优原则：
    → 观察 NPU 利用率：如果 NPU 利用率 < 80%，增大 CPU 预处理并发
    → 观察 Object Store：如果积压 > 预期，减少预处理并发或增大 NPU 消费速度
    → 观察内存：如果 CPU Worker OOM，减小 batch_size
```

---

## 六、弹性扩缩容设计

### 6.1 CPU Worker 弹性伸缩

```
场景：训练数据量变化大，白天数据多晚上数据少

Ray Autoscaler 配置（cluster.yaml）：

  cpu_worker_group:
    min_workers: 2           # 最少 2 个 CPU Worker
    max_workers: 20          # 最多 20 个 CPU Worker
    target_utilization_fraction: 0.8
    upscaling_speed: 4       # 每次最多扩 4 个 Worker
    idle_timeout_minutes: 10 # 空闲 10 分钟后缩容

  扩容触发：
    → CPU 节点上待处理任务队列积压
    → Autoscaler 检测到资源不足
    → 自动创建新 CPU Worker Pod

  缩容触发：
    → CPU 节点空闲超过 idle_timeout
    → Autoscaler 自动销毁空闲 Worker
    → 下次需要时再扩容

  速度：
    CCE 上拉起新 Pod 约 30-60 秒
    ↳ 首次拉取镜像可能更慢（建议预加载镜像）
```

### 6.2 NPU Worker 固定

```
NPU 资源昂贵且数量有限，不适合弹性伸缩：

  - 固定 K 个 NPU Worker，全时运行
  - 训练速度由 NPU 数量决定
  - 不配置 autoscaling
  - 故障时通过 Ray Actor 重建或 Pod 重调度恢复
```

### 6.3 扩缩容与成本优化

```
成本优化策略：

  ┌──────────────────────────────────────────────────┐
  │ 场景一：训练初期（数据探查+调试）                   │
  │   CPU Worker: 2 个（最小值）                       │
  │   NPU Worker: 1 个                                │
  │   → 低成本，快速验证                               │
  ├──────────────────────────────────────────────────┤
  │ 场景二：正式训练（大规模数据）                      │
  │   CPU Worker: 8~16 个（弹性）                      │
  │   NPU Worker: 4~8 个（固定）                       │
  │   → 高吞吐，充分利用 NPU                           │
  ├──────────────────────────────────────────────────┤
  │ 场景三：推理阶段（无训练）                          │
  │   CPU Worker: 4 个（图片预处理）                   │
  │   NPU Worker: 2~4 个（模型推理）                   │
  │   → 同一套架构，切换工作模式                        │
  └──────────────────────────────────────────────────┘
```

---

## 七、容错设计

### 7.1 Ray 原生容错能力

```
利用 Ray 架构的五大优势实现容错：

  ┌──────────────────────────────────────────────────────┐
  │ CPU 预处理 Actor 故障                                 │
  │                                                       │
  │   触发：CPU Worker 节点宕机 / OOM / 进程崩溃          │
  │                                                       │
  │   恢复流程：                                          │
  │   1. GCS 检测到节点下线（心跳超时）                   │
  │   2. 标记该节点上所有 PreprocessActor 为 DEAD          │
  │   3. 在健康 CPU 节点上重建新的 PreprocessActor         │
  │   4. 正在处理的 batch 丢失 → Ray Lineage 重放         │
  │   5. 其他 PreprocessActor 不受影响 → 预处理继续       │
  │                                                       │
  │   恢复时间：秒级                                      │
  └──────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────┐
  │ NPU Trainer Actor 故障                                │
  │                                                       │
  │   触发：NPU 芯片故障 / 节点宕机 / 训练 OOM            │
  │                                                       │
  │   恢复流程：                                          │
  │   1. GCS 检测到 NPU 节点下线                          │
  │   2. 标记该节点上 TrainerActor 为 DEAD                 │
  │   3. 在健康 NPU 节点上重建 TrainerActor                │
  │   4. 从最近 Checkpoint 恢复训练状态                    │
  │   5. 其他 NPU Trainer 不受影响（数据并行场景）         │
  │   6. 正在训练的 batch 丢失 → 上游重新投递              │
  │                                                       │
  │   恢复时间：分钟级（需加载 CKPT 到 NPU HBM）          │
  └──────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────┐
  │ Object Store 数据丢失                                 │
  │                                                       │
  │   恢复流程：                                          │
  │   1. Ray 检测到 ObjectID 不可达                       │
  │   2. 查询 Lineage：该 Object 由哪个 Task/Actor 产生   │
  │   3. 重新执行产生该 Object 的 Task → 重建 Object      │
  │   4. 下游消费不受影响                                 │
  │                                                       │
  │   自动恢复，无需人工干预                               │
  └──────────────────────────────────────────────────────┘
```

### 7.2 与 ModelArts 故障恢复的协同

```
在华为云 ModelArts 上部署时的协同策略：

  Ray 层容错（优先）：
    → Actor 故障：Ray GCS 自动重建
    → 数据丢失：Ray Lineage 重建
    → Pod 级恢复：Ray 原生支持

  ModelArts 层容错（兜底）：
    → NPU 芯片硬件故障：ModelArts 隔离式 Job 重调度
    → 节点宕机：ModelArts Pod 重调度 → Ray Actor 重建
    → 作业卡死：ModelArts 卡死重启

  推荐配置：
    | 配置项 | 值 | 原因 |
    |--------|---|------|
    | Ray Actor max_restarts | 3 | Actor 自动重试 |
    | ModelArts 自动重启 | 8~32 次 | 兜底 Job 级恢复 |
    | ModelArts Pod 重调度 | 3 次 | Ray 可利用 Pod 级恢复 |
    | Checkpoint 保存频率 | 每 100~500 Step | 恢复粒度 |
    | CKPT 存储位置 | SFS Turbo | 实时同步 |
```

---

## 八、批量推理模式

### 8.1 训练与推理共用架构

```
同一套 Ray 集群可切换为离线批量推理模式：

  ┌────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐
  │ Stage 1    │    │ Stage 2      │    │ Stage 3      │    │ Stage 4    │
  │ 读取推理   │───►│ 图片预处理   │───►│ 模型推理     │───►│ 后处理+    │
  │ 请求列表   │    │ (CPU节点)    │    │ (NPU节点)    │    │ 写回结果   │
  └────────────┘    └──────────────┘    └──────────────┘    └────────────┘

  与训练模式的区别：
    - Stage 3 不做反向传播，只做前向推理
    - Stage 4 做后处理（解码输出、写回结果）而非梯度更新
    - 不需要保存 Checkpoint
    - 不需要 HCCL AllReduce（除非张量并行）

  推理性能优化：
    → NPU 上使用 vLLM-Ascend 或 MindIE 做批量推理
    → 开启 Continuous Batching 提升吞吐
    → CPU 做图片预处理 + 后处理（token→文本）
    → NPU 专注于前向推理
```

### 8.2 训练与推理混合部署

```
同一集群内同时运行训练和推理（资源充足时）：

  Ray Placement Group 隔离：

  ┌─────────────────────────────────────┐
  │ Placement Group: Training           │
  │   4 × TrainerActor (NPU=4 each)    │
  │   8 × PreprocessActor (CPU=4 each) │
  └─────────────────────────────────────┘

  ┌─────────────────────────────────────┐
  │ Placement Group: Inference          │
  │   2 × InferenceActor (NPU=4 each)  │
  │   4 × PreprocessActor (CPU=4 each) │
  └─────────────────────────────────────┘

  → 两组 Placement Group 各自独占资源，互不干扰
  → 可以独立扩缩容
  → 适合"边训练边推理"的场景（如 RLHF 中 Actor/Critic 训练 + 推理）
```

---

## 九、方案对比与适用场景

### 9.1 与传统方案对比

| 维度 | 传统方案（同构节点） | 本方案（CPU+NPU 异构） |
|------|---------------------|----------------------|
| 资源利用率 | CPU 和 NPU 交替空闲 | CPU 和 NPU 各自充分利用 |
| 扩展方式 | 加同构节点（含 NPU，成本高） | CPU 瓶颈加 CPU 节点（成本低） |
| 预处理瓶颈 | 受限于单节点 CPU | 可弹性扩展 CPU Worker |
| 容错 | Job 级重建（全量恢复） | Actor 级恢复（局部恢复） |
| 成本 | 高（所有节点都需要 NPU） | 低（CPU 节点用普通 ECS） |

### 9.2 适用场景

| 场景 | 适用度 | 说明 |
|------|--------|------|
| 多模态大模型训练（图文对） | 高 | 图片预处理耗时大，CPU/NPU 分离收益明显 |
| 离线批量推理 | 高 | CPU 做预处理+后处理，NPU 做推理 |
| RLHF 训练（Actor/Critic + Reward） | 高 | 训练和推理可混合部署 |
| 数据预处理瓶颈明显的任务 | 高 | 弹性扩展 CPU Worker 解决瓶颈 |
| 纯文本训练 | 低 | 预处理开销小，CPU/NPU 分离收益不大 |
| 单机小规模训练 | 低 | Ray 调度开销大于收益 |

---

## 十、总结

| 要点 | 说明 |
|------|------|
| **核心思路** | CPU 做图片预处理，NPU 做模型训练，通过 Ray Data Pipeline 串联 |
| **关键 Ray 能力** | 资源感知调度 + Ray Data + Actor 池 + Object Store + Autoscaler |
| **数据流** | 读取 → CPU 预处理 → Object Store → NPU 训练 |
| **弹性** | CPU Worker 弹性扩缩容（2~20），NPU Worker 固定 |
| **背压** | Ray Data 自动背压，防止 Object Store 堆积 |
| **容错** | Ray Actor 级恢复 + ModelArts Job 级兜底 |
| **额外收益** | 同一架构支持离线批量推理、训练推理混合部署 |

---

## 参考文档

- [Ray Data Pipeline](https://docs.ray.io/en/latest/data/data-pipelines.html)
- [Ray Actor Fault Tolerance](https://docs.ray.io/en/latest/ray-core/fault_tolerance/actor-fault-tolerance.html)
- [Ray Autoscaling](https://docs.ray.io/en/latest/cluster/vms/user-guides/community/autoscaling.html)
- [Ray on Kubernetes](https://docs.ray.io/en/latest/cluster/kubernetes/index.html)
- [华为云 ModelArts 训练作业故障恢复](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0019.html)
- [vLLM-Ascend](https://github.com/vllm-project/vllm)
