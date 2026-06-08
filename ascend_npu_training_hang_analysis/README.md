# 昇腾 NPU 大模型训练常见卡死原因与处理方式

## 1. 概述

在华为昇腾 NPU（Ascend 910B/910C/950）上进行大模型分布式训练时，训练作业"卡死"（Hang）是最棘手的故障类型之一。与 crash/abort 不同，卡死时进程仍在运行、不产生错误日志，但训练进度完全停滞，GPU/NPU 利用率归零或持续 100%。

本文基于华为云 ModelArts 文档、昇腾社区案例及生产实践，系统梳理 **10 大类卡死场景**的根因、诊断方法与处理方式。

---

## 2. 卡死分类总览

| # | 卡死类型 | 典型现象 | 影响范围 | 排查难度 |
|---|----------|----------|----------|----------|
| 1 | HCCL 集合通信卡死 | AllReduce/AllGather 无响应 | 多卡/多节点 | ★★★★ |
| 2 | HBM 显存静默卡死 | 进程在、无报错、无输出 | 单卡/多卡 | ★★★★★ |
| 3 | DataLoader 多进程死锁 | 数据加载卡住、epoch 0 不推进 | 单卡/多卡 | ★★★ |
| 4 | Checkpoint IO 卡死 | 保存模型时卡住、IO 100% | 单节点 | ★★ |
| 5 | RoCE/RDMA 网络卡死 | 通信超时、大量 error cqe | 多节点 | ★★★★ |
| 6 | 自定义算子/CANN Kernel 卡死 | 单算子执行不返回 | 单卡 | ★★★★ |
| 7 | 分布式同步死锁 | Rank 间进度不一致、Barrier 卡住 | 多卡/多节点 | ★★★★ |
| 8 | Host-Device 传输卡死 | H2D/D2H 拷贝不完成 | 单卡 | ★★★ |
| 9 | 进程级僵尸/死锁 | 训练进程存在但不调度 | 单节点 | ★★★ |
| 10 | 存储 IO 竞争卡死 | SFS Turbo 写入阻塞、大量 IO wait | 单节点/多节点 | ★★ |

---

## 3. HCCL 集合通信卡死

### 3.1 根因分析

HCCL（Huawei Collective Communication Library）是昇腾的集合通信库，负责多卡/多节点间的 AllReduce、AllGather、Broadcast 等通信操作。HCCL 通信卡死是最常见的多节点训练故障。

**HCCL 三阶段架构**：

```
阶段 1：初始化（Initialize）
  → Rank 间建连（TCP/RDMA握手）
  → 通信域构建（ Communicator ）
  → 拓扑发现与路由规划

阶段 2：算子加载（Operator Loading）
  → 根据通信算法生成执行计划
  → 分配通信 buffer
  → 编译通信 kernel

阶段 3：执行（Execution）
  → 数据搬移
  → 集合通信算子执行
  → 结果同步
```

**卡死可能发生在任一阶段**：

| 阶段 | 卡死原因 | 典型表现 |
|------|----------|----------|
| 初始化 | Rank 间无法建连（网络不通/端口占用） | 停留在 "HCCL Initialize" 日志 |
| 初始化 | RDMA 网卡异常 | `HCCL_CONNECT_TIMEOUT` 触发 |
| 算子加载 | 通信算法编译卡住（AKG 编译 hang） | 日志停留在 "Compile" 阶段 |
| 执行 | Ring/Mesh 中某个 Rank 掉线 | `HCCL_EXEC_TIMEOUT` 触发 |
| 执行 | 通信 buffer 被破坏 | 无响应、无超时 |

### 3.2 关键超时参数

| 环境变量 | 含义 | 默认值 | 建议值 |
|----------|------|--------|--------|
| `HCCL_CONNECT_TIMEOUT` | Rank 建连超时（秒） | 120 | 600 |
| `HCCL_EXEC_TIMEOUT` | 通信算子执行超时（秒） | -1（无限） | 1800 |
| `HCCL_RDMA_TIMEOUT` | RDMA 操作超时（毫秒） | 无 | 30000 |
| `HCCL_IF_IP` | 指定通信网卡 IP | 自动 | 显式指定避免选错网卡 |

**配置示例**：

```bash
export HCCL_CONNECT_TIMEOUT=600
export HCCL_EXEC_TIMEOUT=1800
export HCCL_RDMA_TIMEOUT=30000
export HCCL_IF_IP=10.0.0.1   # 显式指定 RoCE 网卡 IP
```

### 3.3 HCCL 错误码体系

HCCL 错误码格式为 `EI0001` ~ `EI9999`，按阶段分段：

| 范围 | 阶段 | 常见错误码 |
|------|------|------------|
| EI0001-EI0999 | 初始化阶段 | EI0001（建连失败）、EI0003（拓扑发现失败） |
| EI1001-EI1999 | 算子加载阶段 | EI1001（算子编译失败）、EI1003（buffer 分配失败） |
| EI2001-EI2999 | 执行阶段 | EI2001（通信超时）、EI2003（数据校验失败） |
| EI3001-EI3999 | 内部错误 | EI3001（内部状态机异常） |
| EI9001-EI9999 | 外部错误 | EI9001（网络不可达）、EI9002（设备异常） |

### 3.4 诊断方法

```bash
# 1. 查看 HCCL 日志（开启 DEBUG 级别）
export HCCL_LOG_LEVEL=DEBUG
export HCCL_LOG_FILE=/tmp/hccl_debug.log

# 2. 检查网络连通性
ping <对端 RoCE IP>
ibstat                    # 查看网卡状态
ibping -S                 # 服务端
ibping -L <对端 lid>      # 客户端测试

# 3. 检查 RDMA 状态
rdma link show
ibv_devinfo               # RDMA 设备信息
hccn_tool -i 0 -roce_mask -s     # RoCE 配置

# 4. 检查端口占用
netstat -tlnp | grep 29500    # 默认通信端口
ss -tlnp | grep 29500

# 5. 检查 rank 表
cat /usr/local/Ascend/ascend-toolkit/latest/ascend_toolkit_install.info
```

### 3.5 处理方式

| 场景 | 处理方式 |
|------|----------|
| 初始化超时 | 1. 检查所有节点 RoCE 网络连通<br>2. 检查端口是否被占用<br>3. 显式设置 `HCCL_IF_IP` |
| 执行超时 | 1. 开启 `HCCL_EXEC_TIMEOUT` 自动检测<br>2. 检查是否有 Rank 出现 OOM<br>3. 查看是否有节点负载异常 |
| 偶发超时 | 1. 增大超时参数<br>2. 检查网络质量（丢包、延迟）<br>3. 检查光模块状态 |
| 持续卡死 | 1. 降级到 TCP 模式测试：`export HCCL_WHITELIST_DISABLE=1`<br>2. 更新 HCCL 版本<br>3. 联系华为支持分析 coredump |

---

## 4. HBM 显存静默卡死

### 4.1 根因分析

昇腾 NPU 的 HBM（High Bandwidth Memory）显存耗尽时，与 GPU 的 CUDA OOM（抛出显式异常）不同，**部分场景下 NPU 会静默卡死**：

- 进程仍在运行，不产生 crash
- NPU 利用率持续 100% 或归零
- 无任何错误日志输出
- 训练进度完全停滞

**典型触发场景**：

| 场景 | 原因 |
|------|------|
| KV Cache 膨胀 | 推理/长序列训练中 KV Cache 占满 HBM |
| 梯度累积 | 微批次大小过大，梯度 buffer 超出容量 |
| 内存碎片化 | 频繁分配/释放导致碎片化，虽有可用总空间但无法满足连续分配 |
| Activations 重计算 | 开启 gradient checkpointing 后仍然 OOM |
| 混合精度异常 | FP32/FP16 转换产生临时 buffer 占用额外空间 |

### 4.2 诊断方法

```bash
# 1. 实时监控 NPU 显存使用
npu-smi info -t usages -i 0        # 查看显存使用率
watch -n 1 npu-smi info            # 实时刷新

# 2. 查看显存详细分配
npu-smi info -t board -i 0         # 板卡信息
cat /usr/local/Ascend/driver/sysdrv/hisi_fbdma   # DMA 状态

# 3. 开启显存溢出检测
export ASCEND_GLOBAL_LOG_LEVEL=3   # DEBUG 级别
export ASCEND_SLOG_PRINT_TO_STDOUT=0
export HCCL_DETECT_OOM=1           # 开启 OOM 检测（如支持）

# 4. 使用 msprof 分析显存
msprof --application="python train.py" --output=/tmp/profiling
# 在 profiling 结果中查看 Memory 视图

# 5. 开启进程级 OOM Killer
cat /proc/sys/vm/oom_kill_allocating_task
echo 1 > /proc/sys/vm/oom_kill_allocating_task  # 需要权限
```

### 4.3 处理方式

| 措施 | 说明 |
|------|------|
| 减小 batch size | 最直接有效，降低单步显存峰值 |
| 开启梯度检查点 | 用计算换显存，降低 Activation 占用 |
| 启用 ZeRO 优化 | ZeRO-1/2/3 分片策略降低单卡显存需求 |
| 使用显存监控脚本 | 定期 `npu-smi info`，超阈值自动告警 |
| 显存预分配 | `export PYTORCH_NPU_ALLOC_CONF=max_split_size_mb:128` |
| 清理碎片 | 定期 `torch.npu.empty_cache()` |
| 更新 CANN 版本 | 新版本改进了显存管理策略 |

---

## 5. DataLoader 多进程死锁

### 5.1 根因分析

PyTorch DataLoader 使用 `num_workers > 0` 时，通过 `fork()` 创建子进程。**昇腾 torch_npu 在初始化时会持有内部锁**，`fork()` 后子进程继承了父进程的锁状态，导致子进程在获取锁时永久阻塞。

**死锁触发条件**：

```
1. 主进程导入 torch_npu（触发 CANN 初始化，获取内部锁）
2. DataLoader fork() 创建 worker 子进程
3. 子进程继承父进程的内存映像（包括锁状态）
4. 子进程尝试获取锁 → 锁已被"持有"（继承的状态）→ 永久阻塞
```

### 5.2 诊断方法

```python
# 检查是否是 DataLoader 死锁
import torch.utils.data
# 如果设置 num_workers=0 训练正常，num_workers>0 卡死 → 确认是 fork 死锁

# 检查多进程启动方式
import torch.multiprocessing
print(torch.multiprocessing.get_start_method())
# 如果是 "fork" → 可能触发死锁
```

### 5.3 处理方式

**方案 1：改用 spawn 启动方式（推荐）**

```python
import torch.multiprocessing
torch.multiprocessing.set_start_method('spawn', force=True)

# 或在 DataLoader 中
from torch.utils.data import DataLoader
loader = DataLoader(dataset, num_workers=4, multiprocessing_context='spawn')
```

**方案 2：延迟导入 torch_npu**

```python
# 在 fork 之前不导入 torch_npu
# 在 worker_init_fn 中导入
def worker_init_fn(worker_id):
    import torch_npu  # 在子进程中初始化

loader = DataLoader(dataset, num_workers=4, worker_init_fn=worker_init_fn)
```

**方案 3：num_workers=0**

```python
# 单进程加载，牺牲速度但避免死锁
loader = DataLoader(dataset, num_workers=0)
```

---

## 6. Checkpoint IO 卡死

### 6.1 根因分析

大模型训练的 Checkpoint 文件可达数十 TB（如 70B 模型 FP16 约 140GB），保存时需要将整个模型状态序列化写入存储。

**卡死场景**：

| 场景 | 原因 |
|------|------|
| 同步保存 | `torch.save()` 阻塞主线程，训练完全暂停 |
| SFS Turbo 带宽饱和 | 多节点同时写 Checkpoint，SFS Turbo 带宽不足 |
| OBS 上传阻塞 | 使用 Moxing 上传大文件到 OBS，网络波动导致超时 |
| 文件系统 inode 耗尽 | 大量小文件占满 inode，无法创建新文件 |
| NFS 挂载点无响应 | SFS Turbo 服务端异常，NFS 请求无限等待 |

### 6.2 诊断方法

```bash
# 1. 检查 IO 状态
iostat -x 1 10                    # 查看 IO 利用率、等待时间
iotop                             # 查看哪个进程在等 IO

# 2. 检查 NFS 挂载状态
nfsstat -c                        # NFS 客户端统计
mount | grep nfs                  # 挂载参数
cat /proc/mounts | grep sfs       # SFS 挂载详情

# 3. 检查文件系统空间和 inode
df -h /mnt/sfs-turbo              # 空间使用
df -i /mnt/sfs-turbo              # inode 使用

# 4. 检查是否有进程卡在 D 状态（不可中断睡眠）
ps aux | awk '$8 ~ /D/'           # D 状态进程
cat /proc/<pid>/stack             # 查看内核调用栈
```

### 6.3 处理方式

| 措施 | 说明 |
|------|------|
| 异步保存 | 使用 `torch.distributed.checkpoint` 异步 API |
| 分片保存 | 每个 Rank 只保存自己的分片，避免集中写入 |
| 本地缓存 | 先保存到本地 `/cache`，后台上传到 OBS/SFS |
| 限速保存 | 控制写入速率，避免打满存储带宽 |
| AI Turbo 加速 | 开启 ModelArts AI Turbo 高性能存储 |
| Checkpoint 压缩 | 使用 `torch.save(state_dict, _use_new_zipfile_serialization=True)` |
| 错开保存时机 | 各节点随机延迟几秒后再保存，避免同时写入 |

```python
# 异步保存示例
import asyncio
import threading

def async_save_checkpoint(state_dict, path):
    def _save():
        torch.save(state_dict, path)
    t = threading.Thread(target=_save, daemon=True)
    t.start()
    return t

# 训练循环中使用
save_thread = async_save_checkpoint(model.state_dict(), ckpt_path)
# ... 继续训练 ...
save_thread.join(timeout=300)  # 设置超时，避免无限等待
```

---

## 7. RoCE/RDMA 网络卡死

### 7.1 根因分析

昇腾多节点训练依赖 RoCEv2（RDMA over Converged Ethernet）网络进行高速通信。网络异常是导致多节点训练卡死的高频原因。

**常见网络故障**：

| 故障类型 | 表现 | 根因 |
|----------|------|------|
| MTU 配置错误 | 通信超时、性能骤降 | RoCE 网卡 MTU 应为 9000（巨帧），默认 1500 导致分片 |
| PFC 死锁 | 所有流量停止 | Priority Flow Control 优先级配置错误导致死锁 |
| 光模块故障 | 间歇性丢包、error cqe | 光模块温度过高或老化 |
| 网卡固件异常 | 突然断连 | 固件 bug，需更新 |
| 交换机缓冲区溢出 | 大流量场景下丢包 | ECN/PFC 配置不当 |
| ARP 表溢出 | 新节点无法加入通信 | ARP 缓存太小 |

### 7.2 诊断方法

```bash
# 1. 检查 RDMA 链路状态
rdma link show
ibv_devinfo                          # RDMA 设备详情
hccn_tool -i 0 -roce_mask -g        # RoCE 掩码配置

# 2. 检查 error cqe（关键指标）
# error cqe 表示 RDMA 完成队列错误
cat /sys/class/infiniband/mlx5_0/ports/1/hw_counters/rx_write_qp_err
cat /sys/class/infiniband/mlx5_0/ports/1/hw_counters/rx_read_qp_err

# 3. 测试 RDMA 带宽和延迟
# 服务端
ib_write_bw -d mlx5_0 -s 65536
# 客户端
ib_write_bw -d mlx5_0 -s 65536 <server_ip>

# 4. 检查 MTU 配置
ifconfig <roce_nic> | grep MTU
# 应显示 MTU:9000

# 5. 检查丢包
ethtool -S <roce_nic> | grep -i drop
ethtool -S <roce_nic> | grep -i error

# 6. 检查光模块状态
ethtool -m <roce_nic>               # 光模块信息
ethtool <roce_nic> | grep "Link detected"
```

### 7.3 处理方式

| 措施 | 说明 |
|------|------|
| 统一 MTU 为 9000 | `ifconfig <nic> mtu 9000` 或在交换机侧配置 |
| 正确配置 PFC/ECN | 参考 Huawei 推荐的 RoCE 网络配置参数 |
| 定期检查光模块 | 监控光模块温度和功率 |
| 更新网卡固件 | 升级到华为认证版本 |
| 网络降级测试 | 降级到 TCP 模式排查是否是 RDMA 问题 |
| 使用独立网络平面 | 训练通信与业务网络隔离 |

---

## 8. 自定义算子 / CANN Kernel 卡死

### 8.1 根因分析

昇腾 NPU 使用 CANN（Compute Architecture for Neural Networks）计算架构。自定义算子在以下场景可能卡死：

| 场景 | 根因 |
|------|------|
| 算子编译卡死 | AKG 编译器在处理复杂算子时进入无限循环 |
| Kernel 死循环 | 自定义 Kernel 代码存在无限循环或屏障死锁 |
| 内存越界 | Kernel 读写越界破坏设备内存，导致设备状态异常 |
| Cube/Vector 资源竞争 | 算子间资源冲突导致硬件死锁 |
| AICore 超时 | 算子执行时间超过硬件看门狗阈值 |

### 8.2 诊断方法

```bash
# 1. 开启算子 dump
export ASCEND_AICPU_PATH=/tmp/aicpu_dump
export DUMP_GE_GRAPH=2

# 2. 使用 msprof 定位卡死算子
msprof --application="python train.py" --output=/tmp/profiling \
    --aic-metrics="PipeUtilization" \
    --aic-overflow-detection=on

# 3. 查看算子编译日志
export ASCEND_GLOBAL_LOG_LEVEL=3
export ASCEND_AICPU_LOG_LEVEL=3
# 查看 /usr/local/Ascend/ascend-toolkit/latest/opr/build/ 下的编译日志

# 4. 检查 AICore 状态
cat /usr/local/Ascend/driver/sysdrv/aicore_status
npu-smi info -t usages -i 0
```

### 8.3 处理方式

| 措施 | 说明 |
|------|------|
| 设置算子超时 | `export ASCEND_AICORE_TIMEOUT=30000`（毫秒） |
| 跳过编译缓存 | `export ASCEND_OPP_COMPILER_CACHE_MODE=disable` |
| 算子拆分 | 将复杂算子拆分为多个简单算子 |
| 使用内置算子 | 优先使用 CANN 内置算子，避免自定义 |
| 更新 CANN 版本 | 新版本修复已知算子 bug |
| 算子精度调优 | 降低算子精度要求，减少计算复杂度 |

---

## 9. 分布式同步死锁

### 9.1 根因分析

多卡/多节点训练中，所有 Rank 必须执行相同的集合通信操作序列。**任何 Rank 的操作序列不一致都会导致死锁**。

**典型死锁场景**：

| 场景 | 根因 | 示例 |
|------|------|------|
| 条件通信 | 不同 Rank 走不同的代码分支 | Rank 0 执行 AllReduce，Rank 1 跳过 |
| 数据集长度不一致 | 不同 Rank 的 DataLoader 样本数不同 | 4 卡训练，数据集 101 样本，最后一个 Rank 多 1 样本 |
| 动态图分支 | 模型结构依赖输入数据 | 不同输入导致不同 Rank 执行不同算子 |
| Barrier 滥用 | 不必要的 Barrier 导致等不到对端 | `dist.barrier()` 在非对称流程中调用 |
| 梯度同步不一致 | 部分 Rank 未调用 `sync_gradients` | 条件跳过 optimizer step |

**条件通信死锁示例**：

```python
# ❌ 错误：条件 AllReduce，Rank 间条件不一致导致死锁
if local_loss > threshold:
    dist.all_reduce(loss_tensor, op=dist.ReduceOp.AVG)
# Rank 0 进 if，Rank 1 不进 → AllReduce 只有一端参与 → 死锁

# ✅ 正确：所有 Rank 都参与通信
dist.all_reduce(loss_tensor, op=dist.ReduceOp.AVG)
```

**数据集长度不一致示例**：

```python
# ❌ 错误：不同 Rank 数据量不同
# 1000 样本 / 3 卡 → Rank 0: 334, Rank 1: 333, Rank 2: 333
# 最后一轮 Rank 0 多一次迭代，其他 Rank 已退出 → 死锁

# ✅ 正确：Drop last 或填充到等长
sampler = DistributedSampler(dataset, num_replicas=world_size, rank=rank, drop_last=True)
```

### 9.2 诊断方法

```bash
# 1. 对比各 Rank 的通信操作日志
export HCCL_LOG_LEVEL=DEBUG
# 比较各 Rank 的 HCCL 调用序列是否一致

# 2. 检查各 Rank 的数据集大小
python -c "
import torch.distributed as dist
dist.init_process_group('hccl')
print(f'Rank {dist.get_rank()}: dataset size = {len(dataset)}')
"

# 3. 使用 nsight systems 追踪
nsys profile -t nvtx,osrt,cuda --output=profile_rank${RANK} python train.py
```

### 9.3 处理方式

| 措施 | 说明 |
|------|------|
| 确保所有 Rank 执行相同通信序列 | 所有 if/else 分支中的集合通信必须对称 |
| 使用 `drop_last=True` | 确保各 Rank 数据量一致 |
| 避免条件分支中的通信 | 将通信移到条件分支外 |
| 统一随机种子 | 确保数据增强等操作在各 Rank 间一致 |
| 添加 Watchdog | 在训练循环中添加超时检测 |
| 使用 DDP | `DistributedDataParallel` 自动处理梯度同步 |

---

## 10. Host-Device 传输卡死

### 10.1 根因分析

Host（CPU）与 Device（NPU）之间的数据传输（H2D/D2H）依赖 DMA 和 PCIe 总线。以下场景可能导致传输卡死：

| 场景 | 根因 |
|------|------|
| PCIe 链路异常 | PCIe 降速或链路训练失败 |
| HBM 带宽饱和 | 多个传输任务争抢 HBM 带宽 |
| DMA 地址映射失败 | 虚拟地址到物理地址映射异常 |
| 大页内存不足 | 启用大页后大页内存不够导致 DMA 映射失败 |
| Device 端 busy | NPU 正在执行算子，无法接收传输 |

### 10.2 诊断方法

```bash
# 1. 检查 PCIe 链路状态
lspci -vvv | grep -A 20 "Ascend"     # PCIe 设备状态
lspci -vvv | grep "LnkSta"           # 链路速率和宽度

# 2. 检查大页内存
cat /proc/meminfo | grep HugePages
cat /proc/meminfo | grep Hugepagesize

# 3. 查看 NPU 设备状态
npu-smi info -t board -i 0
npu-smi info -t usages -i 0

# 4. 使用 msprof 分析数据传输
msprof --application="python train.py" \
    --output=/tmp/profiling \
    --host-sys=cpu --device-sys=aicore
```

### 10.3 处理方式

| 措施 | 说明 |
|------|------|
| 检查 PCIe 链路 | 确保 PCIe Gen4 x16 或更高，无降速 |
| 预取数据 | `torch.npu.Stream` 异步传输，与计算重叠 |
| 开启大页内存 | 减少地址转换开销，提升 DMA 效率 |
| 开启绑核 | NUMA 亲和性，减少跨 NUMA 传输 |
| 减少小包传输 | 合并小 tensor 为大 tensor 一次性传输 |

---

## 11. 进程级僵尸 / 死锁

### 11.1 根因分析

| 场景 | 根因 |
|------|------|
| Python GIL 死锁 | 多线程中 C 扩展持有 GIL 时阻塞 |
| fork 后多线程状态异常 | fork 只复制调用线程，其他线程的锁处于随机状态 |
| 信号处理不当 | SIGCHLD/SIGTERM 处理函数中调用了非信号安全函数 |
| 资源泄漏 | 文件描述符/socket 耗尽 |
| 内存 OOM Killer | 系统杀掉训练进程但子进程仍在 |

### 11.2 诊断方法

```bash
# 1. 检查进程状态
ps aux | grep python
top -H -p <pid>                     # 查看线程状态

# 2. 查看进程调用栈
py-spy dump --pid <pid>             # Python 调用栈
gdb -p <pid> -batch -ex "thread apply all bt"  # C 调用栈

# 3. 检查文件描述符
ls /proc/<pid>/fd | wc -l           # 打开的 fd 数
cat /proc/<pid>/limits | grep "open files"

# 4. 检查系统日志
dmesg | grep -i "oom"
dmesg | grep -i "kill"
journalctl -u modelarts-* -since "1 hour ago"
```

### 11.3 处理方式

| 措施 | 说明 |
|------|------|
| 使用 spawn 替代 fork | 避免 fork 后多线程状态异常 |
| 设置 fd limit | `ulimit -n 65535` |
| 添加心跳检测 | 训练脚本定期写心跳文件，超时自动重启 |
| 进程看门狗 | 外部监控脚本定期检查训练进程状态 |
| 资源清理 | 训练结束/异常时显式释放资源 |

---

## 12. 存储 IO 竞争卡死

### 12.1 根因分析

ModelArts 训练作业通常使用 SFS Turbo（NFS）挂载存储，在以下场景可能发生 IO 竞争卡死：

| 场景 | 根因 |
|------|------|
| 多作业共享 SFS Turbo | 多个训练作业同时读写同一 SFS Turbo，带宽竞争 |
| NFS 挂载参数不当 | `sync` 模式下每次写操作等待服务端确认 |
| SFS Turbo 容量打满 | 写满后所有写操作阻塞 |
| 脏页回写阻塞 | Linux 脏页积压导致写操作阻塞 |
| 元数据操作过多 | 大量小文件的 `stat`/`ls` 操作导致 NFS 延迟 |

### 12.2 诊断方法

```bash
# 1. 检查 NFS IO 延迟
nfsstat -c                          # NFS 客户端统计
nfsiostat 5 5                       # IO 延迟统计

# 2. 检查脏页
cat /proc/meminfo | grep Dirty
cat /proc/sys/vm/dirty_background_bytes
cat /proc/sys/vm/dirty_bytes

# 3. 检查 SFS Turbo 容量
df -h /mnt/sfs-turbo
df -i /mnt/sfs-turbo                # inode 使用

# 4. 检查 IO wait
iostat -x 1 10                      # %util、await
vmstat 1 10                         # wa（IO wait）
```

### 12.3 处理方式

| 措施 | 说明 |
|------|------|
| 使用本地 `/cache` | 训练数据先下载到本地 SSD，训练完上传结果 |
| 调整 NFS 挂载参数 | 使用 `async`、`noatime`、`rsize=1048576,wsize=1048576` |
| 调整脏页参数 | `vm.dirty_background_bytes=67108864`、`vm.dirty_bytes=134217728` |
| IO 隔离 | 不同作业使用不同的 SFS Turbo 实例 |
| 减少小文件 | 合并为大文件（如 WebDataset 格式） |
| 异步 Checkpoint | 避免同步写阻塞训练 |

---

## 13. 通用排查流程

面对训练卡死，建议按以下流程逐步排查：

```
Step 1：确认卡死现象
  ├─ npu-smi info → NPU 利用率是否归零？
  ├─ 训练日志是否停止输出？
  └─ 进程是否存在？ ps aux | grep python

Step 2：判断影响范围
  ├─ 单卡卡死 → 重点关注 HBM/算子/Host-Device
  ├─ 单节点多卡卡死 → 重点关注 HCCL/进程级问题
  └─ 多节点卡死 → 重点关注网络/分布式同步

Step 3：收集诊断信息
  ├─ HCCL DEBUG 日志
  ├─ npu-smi info 快照
  ├─ 进程调用栈（py-spy / gdb）
  ├─ IO 状态（iostat / nfsiostat）
  └─ 网络状态（rdma link / ibstat）

Step 4：定位根因
  ├─ 根据 HCCL 错误码定位通信问题
  ├─ 根据显存使用定位 OOM 问题
  ├─ 根据调用栈定位代码级死锁
  └─ 根据 IO 指标定位存储问题

Step 5：执行修复
  ├─ 调整参数/配置
  ├─ 修改代码
  ├─ 重启作业
  └─ 联系华为支持
```

### 快速诊断命令集

```bash
#!/bin/bash
# 一键诊断脚本 — 在训练节点上运行

echo "=== NPU 状态 ==="
npu-smi info

echo -e "\n=== 进程状态 ==="
ps aux | grep python | grep -v grep

echo -e "\n=== NPU 显存 ==="
for i in $(npu-smi info -l | grep "NPU ID" | awk '{print $NF}'); do
    echo "NPU $i:"
    npu-smi info -t usages -i $i
done

echo -e "\n=== HCCL 进程 ==="
ps aux | grep -E "hccl|distributed" | grep -v grep

echo -e "\n=== 网络状态 ==="
rdma link show 2>/dev/null || echo "RDMA not available"
ifconfig | grep -A 2 "mtu 9000" || echo "No MTU 9000 interface"

echo -e "\n=== IO 状态 ==="
iostat -x 1 1 | tail -20

echo -e "\n=== NFS 挂载 ==="
mount | grep nfs

echo -e "\n=== 内存状态 ==="
free -h
cat /proc/meminfo | grep -E "HugePages|Dirty"

echo -e "\n=== 文件描述符 ==="
ulimit -n

echo -e "\n=== D 状态进程 ==="
ps aux | awk '$8 ~ /D/'
```

---

## 14. 预防措施总结

| 类别 | 预防措施 |
|------|----------|
| **HCCL** | 设置合理的超时参数、显式指定通信网卡、使用最新 HCCL 版本 |
| **显存** | 监控 HBM 使用率、使用 ZeRO/Gradient Checkpointing、预分配显存 |
| **DataLoader** | 使用 `spawn` 启动方式、避免 `fork` |
| **Checkpoint** | 异步保存、分片保存、错峰保存 |
| **网络** | 统一 MTU 9000、正确配置 PFC/ECN、监控光模块 |
| **算子** | 优先使用内置算子、设置算子超时、更新 CANN |
| **同步** | 确保所有 Rank 通信序列一致、使用 `drop_last=True` |
| **Host-Device** | 开启大页内存、绑核、异步数据预取 |
| **进程** | 心跳检测、fd limit 调大、资源清理 |
| **存储** | 使用本地 `/cache`、调整 NFS 参数、调整脏页参数 |

---

## 15. 关键环境变量速查表

| 环境变量 | 用途 | 推荐值 |
|----------|------|--------|
| `HCCL_CONNECT_TIMEOUT` | HCCL 建连超时 | 600 |
| `HCCL_EXEC_TIMEOUT` | HCCL 执行超时 | 1800 |
| `HCCL_RDMA_TIMEOUT` | RDMA 操作超时 | 30000 |
| `HCCL_IF_IP` | 指定通信网卡 IP | 实际 RoCE IP |
| `HCCL_LOG_LEVEL` | HCCL 日志级别 | DEBUG（排查时） |
| `ASCEND_GLOBAL_LOG_LEVEL` | CANN 全局日志级别 | 3（排查时） |
| `ASCEND_AICORE_TIMEOUT` | AICore 算子超时 | 30000 |
| `PYTORCH_NPU_ALLOC_CONF` | NPU 显存分配策略 | `max_split_size_mb:128` |
| `ASCEND_OPP_COMPILER_CACHE_MODE` | 算子编译缓存 | `enable`（正常）/ `disable`（排查） |
| `HCCL_DETECT_OOM` | HCCL 显存溢出检测 | 1 |

---

*Report generated on 2026-05-28*

Sources:
- [HCCL 集合通信库使用指南 — 昇腾社区](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC2alpha003/apiref/hcclapi/hcclapi_0001.html)
- [HCCL 故障处理 — 昇腾社区](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC2alpha003/troubleshooting/hccl/hccl_0001.html)
- [PyTorch 训练迁移调优 — 昇腾社区](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC2alpha003/moddevg/ptdevg/ptdevg_0001.html)
- [NPU 故障处理指南 — 华为云 ModelArts](https://support.huaweicloud.com/trouble-modelarts/modelarts_13_0001.html)
- [分布式训练故障排查 — 华为云](https://support.huaweicloud.com/bestpractice-modelarts/modelarts_10_0042.html)
- [CANN 算子开发指南 — 昇腾社区](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC2alpha003/opdevg/opdevg_0001.html)
- [RoCE 网络配置最佳实践 — 昇腾社区](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC2alpha003/techno_reference/techno_reference_0001.html)
- [Linux 脏页参数对训练性能影响 — 华为云](https://support.huaweicloud.com/bestpractice-modelarts/modelarts_10_0068.html)
