# 阿里云 PAI → 华为云 ModelArts 训练阶段迁移 GAP 分析报告

> 分析日期：2026-05-28
> 场景：将阿里云 PAI 客户的训练作业迁移到华为云 ModelArts 执行
> 焦点：训练阶段特性差异 + 迁移过程中可能出现的 GAP

---

## 一、训练阶段特性对比

### 1.1 训练作业生命周期

```
PAI-DLC 作业生命周期:
  Created → Bidding(竞价) → Queuing → EnvPreparing → Running → Succeeded/Failed
  + 超时告警: 环境准备/排队/运行 各阶段独立超时配置
  + 竞价实例: Spot 模式，被抢占时 AIMaster 自动保存 checkpoint 后优雅退出
  + 闲时共享: 借用空闲资源，需归还时自动 checkpoint + 退出

ModelArts 训练作业生命周期:
  创建 → 排队 → 运行 → 成功/失败
  + 自动停止: 配置超时时间
  + 故障恢复: 自动重启 (1-128次)
  + 排队超时: 30分钟自动退出
```

**GAP：PAI 的生命周期管理更精细，每个阶段可独立配置超时和告警。**

### 1.2 分布式训练框架支持

| 框架/模式 | PAI | ModelArts | 迁移影响 |
|----------|:---:|:---------:|---------|
| PyTorch DDP | ✅ | ✅ | 无影响 |
| PyTorch + DeepSpeed | ✅ 原生集成 | ⚠️ 需自行集成 | **需改代码** |
| PyTorch + Megatron-LM | ✅ 原生集成 | ⚠️ 需自行集成 | **需改代码** |
| TensorFlow PS | ✅ | ❌ | **不可迁移** |
| MPI (Horovod) | ✅ | ❌ | **不可迁移** |
| Ray 分布式 | ✅ 原生 | ❌ | **不可迁移** |
| Slurm 模式 | ✅ | ❌ | **不可迁移** |
| Custom 多角色 | ✅ (Actor/Critic/Reward) | ⚠️ 有限支持 | **需改代码** |
| MindSpore | ❌ | ✅ | 反向优势 |

### 1.3 Checkpoint 机制对比

```
PAI EasyCkpt:
  ┌──────────────────────────────────────────────────┐
  │  特性:                                             │
  │  - 接近零开销（异步分层保存）                         │
  │  - GPU → CPU 内存拷贝与计算重叠                       │
  │  - 网络感知异步存储（利用带宽空闲期）                   │
  │  - 兼容 Megatron 和 DeepSpeed                      │
  │  - 被抢占时自动触发保存                               │
  │                                                    │
  │  性能影响: < 1% 训练吞吐                             │
  └──────────────────────────────────────────────────┘

ModelArts Checkpoint:
  ┌──────────────────────────────────────────────────┐
  │  特性:                                             │
  │  - 标准 torch.save() / torch.load()               │
  │  - 保存到 SFS Turbo (实时) 或 OBS (异步)            │
  │  - 定期保存（用户配置频率）                           │
  │  - 重启时自动下载已有 checkpoint                     │
  │                                                    │
  │  性能影响: 5-15% 训练吞吐（同步保存时阻塞训练）        │
  └──────────────────────────────────────────────────┘
```

**GAP：PAI 的 EasyCkpt 几乎零开销，ModelArts 标准机制有显著开销。**

### 1.4 容错与故障恢复

| 能力 | PAI | ModelArts | 差距 |
|------|:---:|:---------:|------|
| 环境预检查 | SanityCheck 15+项 | 基础检查(DNS/磁盘/ulimit) | PAI 更全面 |
| 挂起检测 | AIMaster (stdout/stderr停滞) | 卡死检测 (30分钟) | PAI 更精细 |
| 故障诊断 | C4D + 函数栈快照 + 智能诊断 | 自动 Profiling | PAI 更智能 |
| 节点自愈 | AI助手 + AIMaster + EasyCkpt | 故障节点隔离 + 重调度 | PAI 更自动化 |
| 抢占感知 | 自动保存中间状态后退出 | 无 | **PAI 独有** |
| 自定义容错关键字 | ✅ (配置错误关键词触发重启) | ❌ | **PAI 独有** |
| 自然语言交互排障 | ✅ 智能诊断 v2 | ❌ | **PAI 独有** |

### 1.5 容错层级对比

```
PAI AIMaster 5 层容错:
  Layer 1: SanityCheck    (训练前 15+ 检查项)
  Layer 2: 挂起检测       (stdout/stderr 停滞分析)
  Layer 3: C4D 慢节点诊断
  Layer 4: 函数调用栈快照  (pystack/py-spy)
  Layer 5: 智能诊断       (自然语言交互)

ModelArts 3 层恢复:
  Layer 1: 进程级 (原地恢复, 挂起检测 30min)
  Layer 2: Pod 级 (Pod 重调度)
  Layer 3: Job 级 (隔离 Job 重调度)
```

### 1.6 高性能训练加速

| 加速能力 | PAI | ModelArts |
|---------|:---:|:---------:|
| TorchAcc 加速框架 | ✅ 内置 | ❌ |
| RDMA/eRDMA 高速网络 | ✅ | ❌ (NPU用HCCL, GPU用NCCL) |
| 本地缓存加速 | ✅ (数据预缓存到计算节点) | ⚠️ (SFS Turbo + OBS 分层) |
| Huge Pages + CPU 绑核 | ✅ | ✅ |
| 数据并行加速 | ✅ TorchAcc | ⚠️ 需手动优化 |

### 1.7 存储架构对比

```
PAI 训练存储栈:
  ┌──────────────────────────────────────────────────┐
  │  CPFS 智算版 (400MB/s/TiB, 2TB/s, 3000万IOPS)     │
  │  + OSS 自动数据流动 (Lazy-load 按需加载)            │
  │  + 本地缓存加速 (计算节点 NVMe 缓存)                │
  │  + POSIX 客户端 (单客户端 25 GB/s)                  │
  │  + nconnect 多连接                                  │
  └──────────────────────────────────────────────────┘

ModelArts 训练存储栈:
  ┌──────────────────────────────────────────────────┐
  │  SFS Turbo (最高 1000MB/s/TiB, 80GB/s, 100万IOPS) │
  │  + OBS 对象存储 (数据预加载到 SFS Turbo)             │
  │  + MOXing 文件接口 (OBS Python API 适配)            │
  │  + NFSv3 协议 (单客户端吞吐受限)                     │
  │  + 无本地缓存加速                                    │
  └──────────────────────────────────────────────────┘

性能差距:
  吞吐:  2,000 GB/s → 80 GB/s     (25x ↓)
  IOPS:  3,000 万   → 100 万       (30x ↓)
  延迟:  0.25 ms   → 1-3 ms       (4-12x ↓)
```

### 1.8 资源调度与配额管理

| 能力 | PAI | ModelArts |
|------|:---:|:---------:|
| 分层配额树 | ✅ (父子结构) | ❌ (仅工作空间级) |
| 调度策略 | 4种 (智能/均衡/遍历/FIFO) | 自动调度 |
| 多级任务抢占 | ✅ (训练/推理/开发 + 优先级) | ❌ |
| 闲时共享 | ✅ (默认启用) | ❌ |
| 竞价/Spot 实例 | ✅ | ❌ |
| 抢占感知回滚 | ✅ (自动保存 checkpoint) | ❌ |

### 1.9 计费模式对比

| 计费模式 | PAI | ModelArts |
|---------|:---:|:---------:|
| 按需/按量 | ✅ | ✅ |
| 包年包月 | ✅ | ✅ |
| 竞价/Spot | ✅ | ❌ |
| 节省计划 (1/3/5年) | ✅ | ❌ |
| vCPU 计算层级 (高/中/低) | ✅ | ❌ |
| 资源包/资源计划 | ✅ | ❌ |

---

## 二、迁移 GAP 全景

### GAP 分级定义

| 级别 | 含义 | 处理方式 |
|:---:|------|---------|
| **P0-阻塞** | 无替代方案，训练无法运行 | 必须解决 |
| **P1-严重** | 有替代但需大量代码改造 | 需要专项开发 |
| **P2-中等** | 功能降级，影响效率 | 需要适配调整 |
| **P3-轻微** | 体验差异，不影响训练结果 | 可后续优化 |

### GAP 总表

| # | GAP 描述 | 级别 | 涉及 PAI 特性 | ModelArts 替代方案 | 改造量 |
|---|---------|:---:|-------------|------------------|:------:|
| G-01 | **DeepSpeed 集成缺失** | P0 | PAI 原生 DeepSpeed 集成 | 自行在容器中安装 DeepSpeed | 大 |
| G-02 | **Megatron-LM 集成缺失** | P0 | PAI 原生 Megatron 集成 | 自行集成 Megatron-LM + 通信适配 | 大 |
| G-03 | **RLHF/DPO/GRPO 框架缺失** | P0 | ChatLearn 对齐框架 | 需用 OpenRLHF/TRL 等开源框架替代 | 大 |
| G-04 | **EasyCkpt 零开销 Checkpoint 缺失** | P1 | EasyCkpt 异步分层保存 | 标准异步保存 + 本地 SSD 中转 | 中 |
| G-05 | **TorchAcc 加速框架缺失** | P1 | TorchAcc 内置加速 | 需手动优化数据加载/混合精度等 | 中 |
| G-06 | **AIMaster 容错引擎降级** | P1 | AIMaster 多层容错 | ModelArts 三级恢复机制 | 中 |
| G-07 | **SanityCheck 训练前检查缺失** | P1 | 15+项健康检查 | 手动编写预检脚本 | 小 |
| G-08 | **分层配额树→扁平配额** | P2 | 配额树+多级抢占 | 工作空间级配额 | 小 |
| G-09 | **竞价/Spot 实例缺失** | P2 | Spot 实例降本 | 仅按需/包年包月 | 无 |
| G-10 | **CPFS→SFS Turbo 性能降级** | P1 | CPFS 智算版高性能 | OBS+SFS Turbo 分层 + 预加载 | 大 |
| G-11 | **MOXing vs PAI SDK 接口差异** | P1 | PAI Python/Go SDK | ModelArts SDK + MOXing | 中 |
| G-12 | **作业提交方式差异** | P2 | PAI CLI/SDK/控制台 | ModelArts 控制台/CLI | 小 |
| G-13 | **自定义多角色作业** | P0 | Custom 分布式框架 | 需拆分为多个独立训练作业 | 大 |
| G-14 | **抢占感知回滚缺失** | P2 | 抢占时自动保存 | 需自行实现信号处理 | 小 |
| G-15 | **DataJuicer 数据预处理缺失** | P2 | 100+数据清洗算子 | 开源替代或手动实现 | 中 |
| G-16 | **本地缓存加速缺失** | P2 | 数据预缓存到计算节点 | SFS Turbo + obsutil 预加载 | 中 |
| G-17 | **GPU→NPU 训练迁移** | P0 | NVIDIA GPU + CUDA | Ascend NPU + CANN + 适配 | 极大 |
| G-18 | **训练监控指标差距** | P3 | 100+ 指标 | 基础监控 + 自定义 Metrics | 小 |

---

## 三、P0 级 GAP 详细分析与解决方案

### G-01：DeepSpeed 集成缺失

```
PAI 使用方式:
  # 提交训练时选择 MPI 框架，自动配置 DeepSpeed
  pai -name deepspeed_train \
    -D script=train.py \
    -D configure="deepspeed_config.json" \
    -D workers=8 \
    -D workerGPU=8

迁移到 ModelArts:
  # 需要自定义镜像 + 手动配置
  1. 基于官方 PyTorch 镜像，安装 DeepSpeed + 依赖
  2. 配置 SSH 免密 (DeepSpeed 多机通信需要)
  3. 配置 hostfile
  4. 编写启动脚本

改造步骤:
  ├── 自定义 Dockerfile (安装 deepspeed, apex, 等)
  ├── 修改启动脚本 (deepspeed launcher 适配)
  ├── 网络配置 (NCCL 环境变量, RDMA 参数)
  ├── 配置文件格式适配
  └── 测试验证 (单机→多机→全量)

解决方案 - 自定义 Dockerfile:
  FROM swr.cn-north-4.myhuaweicloud.com/modelarts/training:pytorch_2.1.0-cann_8.0.rc2-py_3.9-hce_2.0.2312-aarch64-snt9b
  RUN pip install deepspeed==0.14.0
  RUN pip install ninja
  # 配置 SSH (DeepSpeed 多机通信)
  RUN apt-get update && apt-get install -y openssh-server
  RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh
  COPY ssh_config /root/.ssh/config
```

### G-02：Megatron-LM 集成缺失

```
PAI 使用方式:
  # PAI 原生 Megatron 支持，自动配置 TP/PP/DP
  pai -name megatron_train \
    -D script=megatron_train.py \
    -D tensor_model_parallel_size=8 \
    -D pipeline_model_parallel_size=4

迁移到 ModelArts:
  # 需要完整手动集成
  1. 克隆 Megatron-LM 仓库到训练容器
  2. 配置 NCCL/HCCL 通信后端
  3. 如果使用 NPU → 需要 Megatron-Ascend 适配
  4. 调试 tensor/pipeline parallelism 在 ModelArts 的网络拓扑

关键风险:
  Megatron 依赖 NCCL 的特定拓扑感知能力
  ModelArts NPU 使用 HCCL, API 兼容但行为可能有差异
  TP/PP 的性能调优参数需要重新标定

解决方案:
  # GPU 场景: 直接使用开源 Megatron-LM
  git clone https://github.com/NVIDIA/Megatron-LM.git
  pip install -r requirements.txt
  
  # NPU 场景: 使用 Megatron-Ascend 适配版本
  git clone https://gitee.com/ascend/Megatron-LM.git
  # 按照 CANN 版本要求安装依赖
```

### G-03：RLHF/DPO/GRPO 对齐训练

```
PAI ChatLearn 架构:
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Actor    │  │ Critic   │  │ Reward   │  多角色训练
  │ Model    │  │ Model    │  │ Model    │  PAI 原生调度
  └────┬─────┘  └────┬─────┘  └────┬─────┘
       └──────────────┼──────────────┘
              ChatLearn 框架协调

ModelArts 替代方案:
  方案 A: 使用 OpenRLHF (开源, 兼容 ModelArts)
    → 需要自行配置多角色启动脚本
    → 资源分配需手动管理
    → git clone https://github.com/OpenRLHF/OpenRLHF
    
  方案 B: 使用 VeRL (ModelArts 有部分支持)
    → 查看是否支持目标算法 (DPO/GRPO)
    → ModelArts 文档有 VeRL 断点续训支持
    
  方案 C: 拆分为多个训练作业串行编排
    → Actor 训练 → Reward 计算 → PPO 更新
    → 需要中间存储和编排逻辑
```

### G-13：自定义多角色作业

```
PAI Custom 框架:
  # 支持自定义角色定义和资源分配
  {
    "roles": [
      {"name": "actor", "instances": 4, "gpu_per_instance": 8},
      {"name": "critic", "instances": 2, "gpu_per_instance": 8},
      {"name": "reward", "instances": 1, "gpu_per_instance": 8},
      {"name": "ref", "instances": 1, "gpu_per_instance": 8}
    ]
  }

ModelArts:
  # 训练作业只有单一角色 (所有节点运行相同脚本)
  # 需要通过环境变量手动区分角色
  # 资源分配通过 RANK_TABLE_FILE 或 MSCCL 配置

解决方案:
  方案 1: 单一训练作业 + 脚本内角色判断 (推荐)
    import os
    rank = int(os.environ.get("RANK", 0))
    local_rank = int(os.environ.get("LOCAL_RANK", 0))
    
    if rank < 32:        # 0-31: Actor
        run_actor(rank)
    elif rank < 48:      # 32-47: Critic
        run_critic(rank - 32)
    elif rank < 56:      # 48-55: Reward
        run_reward(rank - 48)
    else:                # 56-63: Reference
        run_ref(rank - 56)
  
  方案 2: 多个训练作业 + 外部编排
  方案 3: 使用 Ray on ModelArts (如果支持)
```

### G-17：GPU→NPU 训练迁移

```
如果迁移后使用华为云 Ascend NPU:

  CUDA → CANN 映射:
  ├── CUDA Kernel     → Ascend C / Cube 算子
  ├── NCCL            → HCCL
  ├── cuDNN           → AscendCL
  ├── CUDA Stream     → ACL Stream
  ├── torch.cuda      → torch_npu
  └── NVIDIA Driver   → CANN Driver

  代码改动:
  import torch_npu                          # 替代 import torch.cuda
  import torch_npu.npu                       # NPU 设备接口
  device = "npu:0"                           # 替代 "cuda:0"
  torch.npu.conv2d(...)                      # API 大部分兼容
  
  常见不兼容场景:
  - 部分 PyTorch 算子 NPU 不支持 → 需自定义算子或 CPU fallback
  - 混合精度策略不同 → AP/APEX → CAMP (Ascend Mixed Precision)
  - checkpoint 格式不兼容 → 需要权重转换
  - Flash Attention → 需 NPU 版本实现
  - xformers → 需确认 NPU 兼容性
  
  算子兼容性检查工具:
  torch_npu.npu.synchronize()               # 验证 NPU 可用
  # 使用 CANN 自带的算子兼容性检查脚本
```

---

## 四、P1 级 GAP 详细分析与解决方案

### G-04：Checkpoint 性能差距

```
PAI EasyCkpt 工作原理:
  训练进行中 ──────→ 触发保存
       │                  │
       │          GPU → CPU (与计算重叠)
       │                  │
       │          CPU → 压缩 (异步)
       │                  │
       │          压缩 → 存储 (网络感知, 利用带宽空闲)
       │
  训练继续 ← 性能影响 < 1%

ModelArts 替代方案:

  方案 1: 异步 Checkpoint + 本地 SSD 中转 (推荐)
    import threading
    import torch
    import os
    
    class AsyncCheckpoint:
        def __init__(self, local_dir="/cache/ckpt", remote_dir="/mnt/sfsturbo/ckpt"):
            self.local_dir = local_dir
            self.remote_dir = remote_dir
            self.thread = None
            
        def save(self, state_dict, step):
            # 同步保存到本地 NVMe SSD (极快, ~1-2秒)
            local_path = f"{self.local_dir}/ckpt_{step}.pt"
            torch.save(state_dict, local_path)
            
            # 异步上传到 SFS Turbo
            if self.thread and self.thread.is_alive():
                self.thread.join()  # 等待上一个上传完成
            remote_path = f"{self.remote_dir}/ckpt_{step}.pt"
            self.thread = threading.Thread(
                target=self._upload, args=(local_path, remote_path)
            )
            self.thread.start()
        
        def _upload(self, src, dst):
            os.system(f"cp {src} {dst}")
            os.system(f"rm -f {src}")  # 清理本地副本
    
  方案 2: 减少保存频率
    每 5000 steps 保存一次 (原来 1000)
    减少保存次数但增加故障恢复的数据丢失量
    
  方案 3: 只保存模型权重 (不含优化器状态)
    7B 模型: 56 GB (全量) → 14 GB (仅权重)
    保存时间降低 4x
```

### G-06：容错引擎降级

```
Gap 分析:
  - 无训练前健康检查 → 需手动编写
  - 挂起检测阈值固定 (30min) → 不可自定义
  - 无函数栈快照 → 排查困难
  - 无自然语言诊断 → 需人工分析日志

解决方案:

  # 训练前健康检查脚本 (替代 SanityCheck)
  #!/usr/bin/env python3
  import torch
  import os
  
  def pre_training_check():
      # 1. GPU/NPU 可用性
      if torch.cuda.is_available():
          print(f"[OK] CUDA: {torch.cuda.device_count()} GPUs")
      # 2. 存储挂载检查
      for path in ["/mnt/sfsturbo", "/cache"]:
          if os.path.ismount(path) or os.path.exists(path):
              stat = os.statvfs(path)
              free_gb = stat.f_bavail * stat.f_frsize / (1024**3)
              print(f"[OK] {path}: {free_gb:.1f} GB free")
          else:
              print(f"[FAIL] {path} not mounted")
      # 3. 网络连通性 (NCCL/HCCL)
      # 4. 内存检查
      # 5. Checkpoint 目录可写
```

### G-10：存储性能降级

```
核心差距:
  CPFS 智算版 → SFS Turbo 1000MB/s/TiB
  
  吞吐:  2,000 GB/s → 80 GB/s     (25x ↓)
  IOPS:  3,000 万   → 100 万       (30x ↓)
  延迟:  0.25 ms   → 1-3 ms       (4-12x ↓)

解决方案:
  1. OBS + SFS Turbo 分层架构
     热数据(当前训练集) → SFS Turbo
     温数据(历史数据)   → OBS 标准 (按需导入)
     冷数据(归档)       → OBS 归档
  
  2. 训练数据预加载
     训练前将所需数据集一次性导入 SFS Turbo
     obsutil sync obs://datasets/ /mnt/sfsturbo/datasets/
  
  3. 增大 SFS Turbo 容量 (线性提升带宽)
     500MB/s/TiB × 20 TiB = 10 GB/s 总带宽
  
  4. Checkpoint 写入优化
     GPU → CPU → 本地 SSD → 异步到 SFS Turbo
```

---

## 五、训练类型迁移复杂度评估

| 训练类型 | 迁移复杂度 | 主要 GAP | 预估改造周期 |
|---------|:---------:|---------|:----------:|
| PyTorch DDP 单机多卡 | **低** | 存储路径、MOXing 适配 | 1-2 周 |
| PyTorch DDP 多机多卡 | **中** | + NCCL/HCCL 配置、启动脚本 | 2-4 周 |
| DeepSpeed ZeRO | **高** | G-01 自行集成、G-04 Checkpoint | 4-8 周 |
| Megatron-LM (TP/PP) | **高** | G-02 手动集成、G-17 GPU→NPU | 6-12 周 |
| RLHF/DPO/GRPO | **极高** | G-03 ChatLearn→OpenRLHF、G-13 多角色 | 8-16 周 |
| MindSpore 训练 | **反向优势** | ModelArts 原生支持 | 1-2 周 |
| 推理服务部署 | **中** | PAI-EAS → ModelArts 推理服务 | 2-4 周 |

---

## 六、推荐迁移路线图

```
Phase 0: 基础环境验证 (1-2 周)
  ├── 在 ModelArts 创建专用资源池
  ├── 测试 SFS Turbo 挂载和 I/O 性能基线
  ├── 验证 PyTorch 基础训练可运行
  └── 验证 Checkpoint 保存/恢复正常

Phase 1: 基础训练迁移 (2-4 周)
  ├── PyTorch DDP 训练作业迁移
  ├── 数据加载路径适配 (CPFS → SFS Turbo)
  ├── MOXing 接口适配 (替换 PAI SDK 调用)
  ├── 自定义镜像构建 (基础依赖)
  └── 监控告警配置

Phase 2: 分布式框架适配 (4-8 周)
  ├── DeepSpeed 集成和测试
  ├── 自定义启动脚本 (hostfile, SSH, NCCL)
  ├── NPU 适配 (如果使用 Ascend)
  └── 容错机制配置 (自动重启次数、超时)

Phase 3: 高级训练场景 (8-16 周)
  ├── RLHF/DPO 对齐训练框架适配
  ├── 多角色作业拆分和编排
  ├── 异步 Checkpoint 实现
  └── 性能调优和基线对比

Phase 4: 生产化 (4-8 周)
  ├── 监控告警完善
  ├── 故障恢复演练
  ├── CI/CD 流水线适配
  └── 文档和运维手册
```

---

## 七、GAP 清单速查表

| # | GAP | 级别 | PAI 特性 | ModelArts 现状 | 改造方案 |
|---|-----|:---:|---------|---------------|---------|
| G-01 | DeepSpeed 集成缺失 | **P0** | 原生集成 | 需自建 | 自定义镜像 + DeepSpeed 安装 |
| G-02 | Megatron-LM 缺失 | **P0** | 原生集成 | 需自建 | 手动集成 + NPU 适配 |
| G-03 | RLHF/DPO/GRPO 缺失 | **P0** | ChatLearn | 无对等 | OpenRLHF / VeRL 替代 |
| G-04 | 零开销 Checkpoint | **P1** | EasyCkpt | 标准同步保存 | 异步保存 + 本地 SSD 中转 |
| G-05 | TorchAcc 加速 | **P1** | 内置加速框架 | 无 | 手动优化数据加载/混合精度 |
| G-06 | 容错引擎降级 | **P1** | AIMaster 5层 | 3层恢复 | 编写预检脚本 + 配置自动重启 |
| G-07 | 训练前检查缺失 | **P1** | SanityCheck | 基础检查 | 自编写健康检查脚本 |
| G-08 | 扁平配额 | **P2** | 分层配额树 | 工作空间配额 | 多工作空间模拟层级 |
| G-09 | 无 Spot 实例 | **P2** | 竞价实例 | 按需/包月 | 优化包月资源利用率 |
| G-10 | 存储性能降级 | **P1** | CPFS 智算版 | SFS Turbo | OBS 分层 + 预加载 + 增大容量 |
| G-11 | SDK 接口差异 | **P1** | PAI SDK | MOXing | 重写数据访问层 |
| G-12 | 作业提交差异 | **P2** | PAI CLI | ModelArts CLI | 重写 CI/CD 流水线 |
| G-13 | 自定义多角色 | **P0** | Custom 框架 | 单一角色 | 脚本内角色判断 / 多作业编排 |
| G-14 | 抢占感知缺失 | **P2** | 自动保存 | 无 | SIGTERM 信号处理 + 自动保存 |
| G-15 | DataJuicer 缺失 | **P2** | 100+ 算子 | 无 | 开源替代 (data-juicer) |
| G-16 | 本地缓存缺失 | **P2** | 数据预缓存 | 无 | obsutil 预加载到 SFS Turbo |
| G-17 | GPU→NPU 适配 | **P0** | NVIDIA GPU | Ascend NPU | torch_npu 适配 + 算子验证 |
| G-18 | 监控指标差距 | **P3** | 100+ 指标 | 基础指标 | 自定义 Metrics 上报 |

---

## 参考文档

- [ModelArts vs PAI 平台能力对比](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_vs_pai_comparison)
- [ModelArts 断点续训分析](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_checkpoint_resume_training_analysis)
- [ModelArts 故障恢复全景](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_fault_recovery_panorama)
- [ModelArts Pod vs Job 重调度](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_pod_vs_job_rescheduling_analysis)
- [ModelArts MOXing 分析](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_moxing_analysis)
- [ModelArts MOXing + SFS Turbo](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_moxing_sfs_turbo_analysis)
- [ModelArts 专属资源池配额](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_dedicated_resource_pool_quotas)
- [SFS Turbo vs CPFS 对比](https://github.com/xuqiaobo001/tech_reports/tree/main/huaweicloud_sfs_vs_aliyun_cpfs_report)
- [ModelArts 训练进程 I/O 旅程图](https://github.com/xuqiaobo001/tech_reports/tree/main/modelarts_training_process_io_journey)
- [CPFS → SFS Turbo 迁移方案](https://github.com/xuqiaobo001/tech_reports/tree/main/cpfs_to_sfsturbo_migration)
- [5PB 迁移方案 (存量+增量)](https://github.com/xuqiaobo001/tech_reports/tree/main/cpfs_5pb_migration_with_updates)
