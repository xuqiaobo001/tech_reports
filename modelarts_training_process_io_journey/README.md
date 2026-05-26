# ModelArts 训练作业进程 I/O 旅程图

> 从一个 PyTorch 训练进程的视角，梳理训练全生命周期中涉及的所有 I/O 操作路径。
> 涵盖：文件 I/O (SFS Turbo)、GPU/NPU 设备 I/O、网络 I/O、内存 I/O、IPC I/O。

---

## 全局架构：进程 I/O 分层模型

```
┌─────────────────────────────────────────────────────────────────────┐
│                        训练进程 (Python/PyTorch)                     │
│                                                                     │
│  torch.load()  DataLoader  model.forward()  torch.save()  logging  │
│       │              │            │               │            │     │
│       ▼              ▼            ▼               ▼            ▼     │
│  ┌────────┐   ┌──────────┐  ┌─────────┐   ┌────────┐   ┌───────┐  │
│  │文件 I/O │   │共享内存I/O│  │GPU驱动I/O│   │文件 I/O│   │文件I/O│  │
│  │(libc)  │   │(mmap/shm)│  │(ioctl)  │   │(libc)  │   │(libc) │  │
│  └───┬────┘   └────┬─────┘  └────┬────┘   └───┬────┘   └───┬───┘  │
└──────┼──────────────┼─────────────┼─────────────┼────────────┼──────┘
       │              │             │             │            │
═══════╪══════════════╪═════════════╪═════════════╪════════════╪═══════
       │         内核空间 │             │             │            │
       ▼              ▼             ▼             ▼            ▼
  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌─────────┐
  │  VFS    │  │虚拟内存   │  │ GPU/NPU  │  │  VFS    │  │  VFS    │
  │         │  │子系统    │  │ 驱动      │  │         │  │         │
  └────┬────┘  └────┬─────┘  └────┬─────┘  └────┬────┘  └────┬────┘
       │            │             │              │            │
       ▼            ▼             ▼              ▼            ▼
  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌─────────┐
  │NFS 客户端│  │ 页缓存   │  │PCIe总线  │  │NFS 客户端│  │NFS 客户端│
  └────┬────┘  └──────────┘  └────┬─────┘  └────┬────┘  └────┬────┘
       │                          │              │            │
       ▼                          ▼              ▼            ▼
  ┌─────────┐               ┌──────────┐  ┌──────────────────────┐
  │TCP/IP   │               │GPU/NPU   │  │    TCP/IP 网络栈      │
  │网络栈   │               │硬件      │  │                       │
  └────┬────┘               └──────────┘  └───────────┬──────────┘
       │                                                 │
       ▼                                                 ▼
  ┌──────────┐                                     ┌──────────┐
  │SFS Turbo │                                     │SFS Turbo │
  │(NFS 服务)│                                     │(NFS 服务)│
  └──────────┘                                     └──────────┘
```

---

## 阶段 0：训练作业启动

> ModelArts 平台调度训练作业到 ECS 节点，初始化运行环境。

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 目标存储 |
|------|---------|---------|---------|---------|
| 0.1 | 读取启动脚本 `start.sh` | `openat` → `read` | SFS Turbo → NFS Client → Page Cache → 进程内存 | SFS Turbo |
| 0.2 | 加载 Python 解释器 | `execve` → `open` → `read` → `mmap` | 本地磁盘 (ECS 系统盘) | 本地 SSD |
| 0.3 | 加载 PyTorch/CUDA 库 | `openat` → `read` → `mmap` | 本地磁盘 → Page Cache → 进程地址空间 | 本地 SSD |
| 0.4 | 初始化 CUDA/NPU 运行时 | `ioctl` (GPU driver) | 用户态 → 内核驱动 → PCIe → GPU | GPU HBM |
| 0.5 | 初始化 NCCL 通信后端 | `socket` → `connect` → `setsockopt` | 进程 → 内核网络栈 → 网卡 → 其他节点 | 网络栈 |
| 0.6 | 读取训练超参配置 | `openat` → `read` | SFS Turbo → NFS → Page Cache → 进程内存 | SFS Turbo |

```
I/O 类型:
  [文件I/O ──→ SFS Turbo]  启动脚本、配置文件、环境变量
  [文件I/O ──→ 本地磁盘]   Python 解释器、PyTorch/CUDA 共享库
  [设备I/O ──→ GPU驱动]    CUDA runtime 初始化, GPU 内存池建立
  [网络I/O ──→ TCP/RDMA]   NCCL 通信域建立, 节点间握手
```

---

## 阶段 1：模型与配置加载

> 加载预训练模型权重、tokenizer、训练配置到 GPU/NPU 内存。

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 数据量 |
|------|---------|---------|---------|--------|
| 1.1 | `stat` 模型目录结构 | `stat` / `newfstatat` | NFS → 内核 | ~KB |
| 1.2 | 读取 `config.json` | `openat` → `read` → `close` | SFS Turbo → Page Cache → 用户态 | ~KB |
| 1.3 | 读取 `tokenizer.model` | `openat` → `read` → `close` | SFS Turbo → Page Cache → 用户态 | ~MB |
| 1.4 | 加载模型权重 `.safetensors` / `.bin` | `openat` → `read` / `mmap` | SFS Turbo → Page Cache → 用户态 | ~GB-TB |
| 1.5 | 反序列化权重到 Tensor | 用户态内存操作 | 进程堆内存 → PyTorch Tensor | ~GB-TB |
| 1.6 | 权重传输到 GPU/NPU | `ioctl` (GPU driver) / `write` (NPU driver) | CPU 内存 → PCIe → GPU HBM | ~GB-TB |
| 1.7 | 模型编译/优化 (可选) | GPU kernel 执行 | GPU HBM 内部 | N/A |

```
数据流示意:

  SFS Turbo (权重文件)
       │
       │ ① NFS READ RPC (rsize=1MB)
       ▼
  内核 Page Cache (热点数据缓存在主机内存)
       │
       │ ② read() / mmap() 系统调用
       ▼
  进程用户态内存 (PyTorch Tensor, CPU)
       │
       │ ③ cudaMemcpy(HostToDevice) → ioctl → PCIe
       ▼
  GPU/NPU HBM (模型权重加载完成)
```

**关键 I/O 特征:**
- 模型权重文件通常很大 (数 GB 到数十 GB)，**顺序读**
- 首次读取触发 NFS READ RPC，后续可能命中 Page Cache
- CPU→GPU 传输通过 PCIe，带宽约 12-32 GB/s (PCIe 3.0/4.0 x16)
- 如果使用 `torch.load()` 的 `mmap=True`，权重通过 mmap 按需加载

---

## 阶段 2：训练数据集初始化

> 构建数据集索引，准备 DataLoader。

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 数据量 |
|------|---------|---------|---------|--------|
| 2.1 | 扫描训练数据目录 | `stat` / `getdents64` (readdir) | SFS Turbo → NFS → 用户态 | 目录元数据 |
| 2.2 | 读取数据索引文件 (如 `.parquet` / `.json`) | `openat` → `read` → `close` | SFS Turbo → Page Cache → 用户态 | ~MB |
| 2.3 | 创建共享内存 (DataLoader workers) | `mmap(MAP_ANONYMOUS\|MAP_SHARED)` / `shmget` | 进程间共享内存区域 | ~MB |
| 2.4 | 创建 DataLoader worker 进程 | `fork` / `clone` | 进程创建 | N/A |
| 2.5 | 建立 worker 间管道 | `pipe2` / `socketpair` | 内核管道缓冲区 | 64KB 缓冲 |

```
I/O 类型:
  [文件I/O ──→ SFS Turbo]  目录扫描、索引文件读取
  [内存I/O ──→ 共享内存]    DataLoader worker 共享 buffer
  [IPC I/O ──→ 管道]       worker 进程间数据传递队列
  [进程管理]                fork/clone 创建 DataLoader workers
```

---

## 阶段 3：训练循环 (每个 iteration)

> 训练循环是整个 I/O 旅程中最核心、最高频的部分。
> 每个 training step 涉及：数据加载 → GPU 计算 → 梯度同步 → 指标记录。

### 3.1 数据加载 (DataLoader)

```
┌─────────────────────────────────────────────────────────────┐
│                    DataLoader I/O 路径                       │
│                                                             │
│  Worker 进程                    主进程                       │
│  ┌──────────┐                  ┌──────────┐                │
│  │ open()   │ ← SFS Turbo     │          │                │
│  │ read()   │ ← 读取训练样本   │          │                │
│  │ decode() │ ← 图像解码/文本处理│          │                │
│  │ transform│ ← 数据增强       │          │                │
│  └────┬─────┘                  │          │                │
│       │ 共享内存/管道            │          │                │
│       └──────────────────────→ │ batch    │                │
│                                │ collate  │                │
│                                └────┬─────┘                │
│                                     │ cudaMemcpy(H2D)      │
│                                     ▼                      │
│                                ┌──────────┐                │
│                                │ GPU HBM  │                │
│                                │ (batch   │                │
│                                │  tensor) │                │
│                                └──────────┘                │
└─────────────────────────────────────────────────────────────┘
```

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 频率 |
|------|---------|---------|---------|------|
| 3.1.1 | Worker 打开训练数据文件 | `openat` | SFS Turbo → NFS | 每个 sample |
| 3.1.2 | Worker 读取文件内容 | `read` / `pread64` | SFS Turbo → Page Cache → Worker 内存 | 每个 sample |
| 3.1.3 | 图像解码 (PIL/cv2) | 用户态计算 | Worker 内存 | 每个 sample |
| 3.1.4 | 数据增强 (transform) | 用户态计算 | Worker 内存 | 每个 sample |
| 3.1.5 | Worker → 主进程传递 batch | 共享内存读写 / `read(pipe)` | 共享内存 / 内核管道 | 每 batch |
| 3.1.6 | CPU Tensor → GPU Tensor | `ioctl` (GPU driver) | CPU 内存 → PCIe → GPU HBM | 每 batch |

**DataLoader 的两种共享策略:**

| 策略 | 机制 | 系统调用 | 特点 |
|------|------|---------|------|
| `shared_memory=True` | POSIX 共享内存 | `shmget` / `shmat` / `mmap(SHM)` | 零拷贝，高效 |
| 默认 (管道) | `multiprocessing.Queue` | `pipe2` → `write` / `read` | 序列化开销，但通用 |

### 3.2 前向传播 (Forward Pass)

```
┌─────────────────────────────────────────────────┐
│              前向传播 — 纯 GPU 内部 I/O            │
│                                                 │
│  ┌───────────┐     ┌───────────┐               │
│  │ 模型权重   │ ──→ │ 矩阵乘法   │ ──→ 激活值    │
│  │ (GPU HBM) │     │ (GPU CUDA │    (GPU HBM)  │
│  │  只读      │     │  Core)    │               │
│  └───────────┘     └───────────┘               │
│                                                 │
│  I/O 特征: 纯 GPU HBM 内部读写                    │
│  带宽: HBM ~1.5-3.2 TB/s (远超 PCIe)            │
│  不经过系统调用，不经过内核                         │
│  (除非触发 GPU page fault → ioctl → 驱动)         │
└─────────────────────────────────────────────────┘
```

| 步骤 | I/O 操作 | 机制 | 数据路径 |
|------|---------|------|---------|
| 3.2.1 | 读取模型权重 | GPU HBM 内部读取 | HBM → CUDA Core |
| 3.2.2 | 中间激活值写入 | GPU HBM 内部写入 | CUDA Core → HBM |
| 3.2.3 | 注意力机制 KV Cache | GPU HBM 读写 | HBM ↔ CUDA Core |
| 3.2.4 | 输出 logits | GPU HBM 写入 | CUDA Core → HBM |
| 3.2.5 | Loss 计算 | GPU HBM 读写 | HBM ↔ CUDA Core |

**关键: 前向传播的 I/O 完全在 GPU HBM 内部发生，不经过 PCIe，不经过系统调用，不属于传统意义上的"进程 I/O"。**

### 3.3 反向传播 (Backward Pass)

| 步骤 | I/O 操作 | 机制 | 数据路径 |
|------|---------|------|---------|
| 3.3.1 | 读取激活值 (前向传播缓存) | GPU HBM 内部读取 | HBM → CUDA Core |
| 3.3.2 | 计算梯度 | GPU HBM 读写 | HBM ↔ CUDA Core |
| 3.3.3 | 写入参数梯度 | GPU HBM 写入 | CUDA Core → HBM (gradient buffer) |
| 3.3.4 | 释放激活值 (可选 gradient checkpointing) | GPU HBM 释放 | HBM 空间回收 |

### 3.4 梯度同步 (Distributed Training)

```
┌──────────────────────────────────────────────────────────────────────┐
│                    梯度同步 I/O 路径                                    │
│                                                                      │
│  单节点多卡 (Node 内部)                多节点 (跨网络)                  │
│                                                                      │
│  GPU 0 ←──→ GPU 1 ←──→ GPU 2 ←──→ GPU 3    Node 0 ←────────→ Node 1│
│     │    NVLink      │    NVLink      │            RDMA/RoCE         │
│     │    300GB/s     │    300GB/s     │            100Gbps           │
│     ▼                ▼                ▼                               │
│  ┌──────┐        ┌──────┐        ┌──────┐                           │
│  │ HBM  │        │ HBM  │        │ HBM  │     NCCL AllReduce:       │
│  │梯度   │        │梯度   │        │梯度   │     Ring / Tree 算法      │
│  └──────┘        └──────┘        └──────┘     GPU Direct RDMA 可选   │
│                                                                      │
│  I/O 路径:                                                           │
│    Node 内: GPU HBM → NVLink → GPU HBM (不经过 CPU)                  │
│    Node 间: GPU HBM → PCIe → NIC → 网络 → NIC → PCIe → GPU HBM      │
│             或 GPU Direct RDMA: HBM → NIC → 网络 → NIC → HBM        │
└──────────────────────────────────────────────────────────────────────┘
```

| 步骤 | I/O 操作 | 机制 | 数据路径 | 涉及 syscall |
|------|---------|------|---------|-------------|
| 3.4.1 | Node 内梯度同步 | NVLink / PCIe | GPU HBM ↔ GPU HBM | 无 (硬件直连) |
| 3.4.2 | Node 间梯度同步 | NCCL → Socket/RDMA | GPU HBM → PCIe → NIC → 网络 → NIC → PCIe → GPU HBM | `socket` → `send` / `recv` |
| 3.4.3 | AllReduce 聚合 | 网络协议栈 | Ring/Tree 拓扑 | 内核网络栈 |
| 3.4.4 | 梯度平均后写回 | GPU HBM 写入 | GPU HBM | 无 |

**关键区别:**
- **Node 内部**: NVLink 直接连接 GPU，数据不经 CPU，无系统调用
- **Node 间**: 经过内核网络栈 (socket send/recv)，是进程网络 I/O 的一部分
- **GPU Direct RDMA**: 绕过 CPU，但仍然经过内核 RDMA 驱动

### 3.5 优化器更新 (Optimizer Step)

| 步骤 | I/O 操作 | 机制 | 数据路径 |
|------|---------|------|---------|
| 3.5.1 | 读取参数梯度 | GPU HBM 读取 | HBM → CUDA Core |
| 3.5.2 | 读取优化器状态 (Adam: m, v) | GPU HBM 读取 | HBM → CUDA Core |
| 3.5.3 | 更新优化器状态 | GPU HBM 写入 | CUDA Core → HBM |
| 3.5.4 | 更新模型权重 | GPU HBM 写入 | CUDA Core → HBM |

### 3.6 训练指标记录 (每个 step)

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 频率 |
|------|---------|---------|---------|------|
| 3.6.1 | GPU → CPU 传输 loss 值 | `ioctl` (GPU driver) | GPU HBM → PCIe → CPU 内存 | 每 step |
| 3.6.2 | 写入训练日志 (stdout) | `write` (fd=1) | 进程内存 → 内核管道 → ModelArts 日志收集 | 每 step |
| 3.6.3 | 写入 TensorBoard 事件文件 | `write` | 进程内存 → VFS → NFS → SFS Turbo | 每 N steps |
| 3.6.4 | flush 日志 (可选) | `fsync` / `fflush` | Page Cache → NFS WRITE RPC → SFS Turbo | 每 N steps |

---

## 阶段 4：Checkpoint 保存 (周期性)

> 训练过程中周期性保存模型快照到 SFS Turbo，这是最重的写入 I/O。

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Checkpoint 保存 I/O 路径                            │
│                                                                      │
│  GPU HBM                    CPU 内存                      SFS Turbo  │
│  ┌─────────────┐          ┌─────────────┐            ┌─────────────┐│
│  │ 模型权重     │  D2H     │ state_dict  │  write()   │ ckpt-       ││
│  │ 优化器状态   │ ──────→  │ 序列化      │ ────────→  │ step_1000/  ││
│  │ 训练步数     │  PCIe    │ pickle/     │  NFS WRITE │   model.bin ││
│  │ 随机数状态   │  ~25GB/s │ safetensors │  RPC       │   optim.bin ││
│  └─────────────┘          └─────────────┘            │   rng.bin   ││
│                                                       └─────────────┘│
│                                                                      │
│  ① cudaMemcpy(D2H)    ② torch.save / safetensors    ③ NFS 写入     │
│     ioctl(GPU驱动)       CPU 内存操作                   VFS → NFS     │
│                                                                      │
│  典型数据量:                                                          │
│    7B 模型: ~14GB (FP16)    70B 模型: ~140GB (FP16)                   │
│    含优化器状态: x3 (Adam m + v)                                      │
│    7B 全量: ~56GB            70B 全量: ~560GB                         │
└──────────────────────────────────────────────────────────────────────┘
```

| 步骤 | I/O 操作 | 系统调用 | 数据路径 | 数据量 |
|------|---------|---------|---------|--------|
| 4.1 | 创建 checkpoint 目录 | `mkdir` | VFS → NFS → SFS Turbo | 元数据 |
| 4.2 | GPU → CPU 传输模型权重 | `ioctl` (GPU driver) | GPU HBM → PCIe → CPU 内存 | ~GB-TB |
| 4.3 | GPU → CPU 传输优化器状态 | `ioctl` (GPU driver) | GPU HBM → PCIe → CPU 内存 | ~GB-TB (x2 for Adam) |
| 4.4 | 序列化 state_dict | 用户态内存操作 (pickle/safetensors) | CPU 内存 | ~GB-TB |
| 4.5 | 写入临时文件 (先写临时文件再 rename) | `openat` → `write` | CPU → Page Cache → NFS WRITE | ~GB-TB |
| 4.6 | `fsync` 确保数据落盘 | `fsync` / `fdatasync` | Page Cache → NFS COMMIT → SFS Turbo | 元数据 |
| 4.7 | 原子 rename 临时文件为最终文件 | `rename` | NFS RENAME RPC | 元数据 |
| 4.8 | 关闭文件描述符 | `close` | 内核 fd 释放 | N/A |

**Checkpoint 写入的 NFS I/O 特征:**
- **大块顺序写**: wsize=1MB，写入带宽取决于 SFS Turbo 规格
- **fsync 等待**: NFS COMMIT RPC 需等 SFS Turbo 确认持久化，延迟约数毫秒到数百毫秒
- **原子 rename**: 避免写到一半断电导致 checkpoint 损坏
- **多 rank 协调**: 分布式训练中通常只有 rank 0 保存 checkpoint

---

## 阶段 5：日志与监控 (持续)

| I/O 类型 | 目标 | 系统调用 | 写入频率 | 数据量 |
|---------|------|---------|---------|--------|
| TensorBoard 事件 | SFS Turbo | `write` → NFS | 每 100 steps | ~MB/hour |
| 训练日志 (stdout) | ModelArts 日志系统 | `write` (fd=1) | 每 step | ~KB/step |
| ModelArts 指标上报 | ModelArts API | `socket` → `send` (HTTP) | 每 step | ~B/step |
| MOXing 日志 | SFS Turbo / 本地 | `write` | 间歇 | ~KB |
| 进程资源统计 (/proc) | procfs | `read` (`/proc/self/stat`) | 采样 | ~B |

---

## 阶段 6：训练结束

| 步骤 | I/O 操作 | 系统调用 | 数据路径 |
|------|---------|---------|---------|
| 6.1 | 保存最终 checkpoint | 同阶段 4 | 同阶段 4 |
| 6.2 | 写入训练 summary | `write` | SFS Turbo |
| 6.3 | 上传 artifacts 到 OBS (可选) | `socket` → `send` (HTTPS/OBS API) | CPU 内存 → 网络栈 → OBS |
| 6.4 | 释放 GPU 内存 | `ioctl` (GPU driver) | GPU HBM 释放 |
| 6.5 | 关闭所有文件描述符 | `close` (批量) | 内核 fd 释放 |
| 6.6 | 进程退出 | `exit_group` | 内核清理 |

---

## 进程 I/O 全景分类总结

### 按存储目标分类

```
┌──────────────────────────────────────────────────────────────────┐
│                    训练进程 I/O 全景图                              │
│                                                                  │
│  ┌─────────────────────────────────────────────────┐            │
│  │           SFS Turbo (NFS 网络文件系统)             │            │
│  │                                                 │            │
│  │  读: 模型权重 ✦  训练数据 ✦✦✦  配置文件 ✦          │            │
│  │  写: Checkpoint ✦✦✦  TensorBoard ✦  日志 ✦       │            │
│  │  元数据: stat/readdir ✦  mkdir/rename ✦          │            │
│  │                                                 │            │
│  │  系统调用: openat read write close fsync stat    │            │
│  │            mkdir rename getdents64 mmap          │            │
│  └──────────────────┬──────────────────────────────┘            │
│                     │ NFS over TCP                               │
│  ┌──────────────────┴──────────────────────────────┐            │
│  │              TCP/IP 网络栈                        │            │
│  │                                                 │            │
│  │  NFS I/O ✦✦✦  NCCL AllReduce ✦✦✦               │            │
│  │  MOXing API ✦  OBS 上传 ✦                       │            │
│  │                                                 │            │
│  │  系统调用: socket connect send recv setsockopt   │            │
│  └──────────────────┬──────────────────────────────┘            │
│                     │                                            │
│  ┌──────────────────┴──────────────────────────────┐            │
│  │           本地内存 (DDR)                           │            │
│  │                                                 │            │
│  │  进程堆栈 ✦✦✦  DataLoader 共享内存 ✦✦             │            │
│  │  Page Cache (NFS 文件缓存) ✦✦✦                  │            │
│  │  PyTorch Tensor (CPU) ✦✦  pickle 序列化缓冲 ✦✦  │            │
│  │                                                 │            │
│  │  系统调用: mmap brk madvise mprotect             │            │
│  └──────────────────┬──────────────────────────────┘            │
│                     │ PCIe / NVLink                             │
│  ┌──────────────────┴──────────────────────────────┐            │
│  │          GPU/NPU 设备 (HBM)                       │            │
│  │                                                 │            │
│  │  模型权重 ✦✦✦  梯度 ✦✦✦  优化器状态 ✦✦✦          │            │
│  │  激活值 ✦✦✦  KV Cache ✦✦  训练 batch ✦✦         │            │
│  │                                                 │            │
│  │  API: cudaMemcpy(H2D/D2H) → ioctl               │            │
│  │       cuMemAlloc/cuMemFree → ioctl               │            │
│  │       CUDA kernel launch → ioctl                 │            │
│  └─────────────────────────────────────────────────┘            │
│                                                                  │
│  ✦ = 低频  ✦✦ = 中频  ✦✦✦ = 高频/大量                            │
└──────────────────────────────────────────────────────────────────┘
```

### 按系统调用分类

| I/O 类别 | 系统调用 | 触发场景 | 是否属于"进程 I/O" |
|---------|---------|---------|:---:|
| **文件读 (SFS Turbo)** | `openat` `read` `pread64` `mmap` | 加载模型、读取训练数据、读配置 | **是** |
| **文件写 (SFS Turbo)** | `write` `pwrite64` `fsync` `fdatasync` | 保存 checkpoint、写 TensorBoard、写日志 | **是** |
| **文件元数据** | `stat` `newfstatat` `getdents64` `mkdir` `rename` `unlink` | 目录扫描、创建目录、原子重命名 | **是** |
| **网络 I/O (NFS)** | 嵌入在 `read`/`write` 中 (NFS 客户端) | 所有 SFS Turbo 文件操作底层走 NFS | **是** |
| **网络 I/O (NCCL)** | `socket` `connect` `send` `recv` `setsockopt` | 分布式训练梯度同步 | **是** |
| **网络 I/O (API)** | `socket` `connect` `send` `recv` | MOXing 指标上报、OBS 上传 | **是** |
| **GPU 设备 I/O** | `ioctl` (GPU driver fd) `mmap` (GPU BAR) | CUDA 内存分配、H2D/D2H 传输、kernel 启动 | **是** |
| **GPU 内部计算** | 无系统调用 (GPU 硬件内部) | 前向/反向传播、优化器计算 (HBM 内部) | **否** |
| **共享内存 IPC** | `mmap(SHM)` `shmget` `shmat` | DataLoader worker 间数据传递 | **是** |
| **管道 IPC** | `pipe2` `read(pipe)` `write(pipe)` | DataLoader worker → 主进程 | **是** |
| **内存管理** | `brk` `mmap(ANON)` `madvise` `mprotect` | Python 对象分配、PyTorch 内存池 | **边界** |
| **进程管理** | `fork` `clone` `waitpid` `exit_group` | 创建 DataLoader workers | **否** |
| **本地文件 I/O** | `openat` `read` `write` | Python 库加载、临时文件、`/tmp` | **是** |

### 按故障影响范围分类

| I/O 路径 | pio-fault-01 (LD_PRELOAD) | pio-fault-02/06 (ptrace/eBPF) | pio-fault-03 (cgroups) | pio-fault-05 (SIGSTOP) |
|---------|:---:|:---:|:---:|:---:|
| SFS Turbo 文件读写 | **受影响** | **受影响** | 部分 (NFS 不走块设备层) | **受影响** |
| NCCL 网络通信 | **受影响** | **受影响** | 不受影响 | **受影响** |
| GPU HBM 内部计算 | 不受影响 | 不受影响 | 不受影响 | **受影响** (暂停) |
| GPU ↔ CPU 传输 | **受影响** | **受影响** | 不受影响 | **受影响** |
| 共享内存 IPC | 不受影响 | 不受影响 | 不受影响 | **受影响** (暂停) |
| CPU 内存操作 | 不受影响 | 不受影响 | 不受影响 | **受影响** (暂停) |

---

## 典型训练 Step 的 I/O 时序图

```
时间 ──────────────────────────────────────────────────────────────→

  ┌──── 数据加载 ────┐┌──── GPU 计算 ────────────────────────────┐┌─ 指标 ─┐
  │                  ││                                          ││        │
  │ NFS READ        ││  HBM R/W  HBM R/W  HBM R/W  HBM R/W    ││ ioctl  │
  │  ↓              ││   ↓        ↓        ↓        ↓          ││  ↓     │
  │ Page Cache      ││ forward   backward  allreduce optimizer ││ D2H    │
  │  ↓              ││           ↕ NVLink  ↕ Network ↕         ││  ↓     │
  │ 共享内存        ││          (node内)  (node间)               ││ write  │
  │  ↓              ││                                          ││  ↓     │
  │ ioctl(H2D)     ││           纯 GPU HBM 内部                  ││ NFS    │
  │  ↓              ││           (无 syscall)                    ││ WRITE  │
  │ GPU HBM         ││                                          ││        │
  └──────────────────┘└──────────────────────────────────────────┘└────────┘
  │← 进程 I/O 参与 →││← 不经过进程 I/O →││← 进程 I/O 参与 →││进程 I/O│
  │   (NFS+ioctl)   ││  (GPU 硬件内部)  ││ (NCCL socket)  ││(ioctl+│
  │                  ││                  ││                 ││NFS)   │

  syscall 热点:
  ├── read()        ████████████  (训练数据加载)
  ├── ioctl()       ██████████    (H2D/D2H 传输)
  ├── send()/recv() ██████        (NCCL 梯度同步)
  └── write()       ██            (指标/日志)
```

---

## 关键结论

### 1. 哪些属于"进程 I/O"

从训练进程角度看，以下操作**经过系统调用，属于进程 I/O**:

| 类别 | 占训练 I/O 比例 | 途经 |
|------|:---:|------|
| 训练数据读取 (SFS Turbo → CPU → GPU) | **~40%** | `read` → `ioctl` |
| Checkpoint 保存 (GPU → CPU → SFS Turbo) | **~25%** | `ioctl` → `write` → `fsync` |
| 梯度同步 (跨节点) | **~20%** | `send` / `recv` (NCCL) |
| 模型加载 (SFS Turbo → GPU) | **~10%** | `read` → `ioctl` |
| 日志/元数据 (SFS Turbo) | **~5%** | `write` |

### 2. 哪些不属于"进程 I/O"

- **GPU/NPU HBM 内部计算** (前向/反向/优化器): 纯硬件内部，无 syscall
- **NVLink 通信** (Node 内 GPU 间): 硬件直连，无 syscall
- **CPU 纯计算** (数据预处理): 纯用户态，无 syscall (除非触发 page fault)

### 3. 故障注入影响分析

- **LD_PRELOAD 拦截**: 影响文件 I/O + 部分 GPU 传输 (如果 PyTorch 通过 libc 调用)
- **ptrace/eBPF 拦截**: 影响所有 syscall 路径 (文件+网络+ioctl)，**不**影响 GPU 内部计算
- **SIGSTOP**: 影响一切 (整个进程冻结)
- **cgroups 限流**: 主要影响块设备 I/O，对 NFS I/O 效果有限
