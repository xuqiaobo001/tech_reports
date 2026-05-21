# ModelArts SFS Turbo IO 卡死故障注入测试方案

## 1. 测试目标

在 ModelArts 训练作业中，向 SFS Turbo（NFS 挂载）存储注入 IO 卡死故障，验证 ModelArts 的故障恢复能力，包括：

- 故障检测时间：ModelArts 多久能检测到训练作业异常
- 自动恢复行为：是否自动重启作业、是否支持断点续训
- 数据完整性：故障恢复后 checkpoint 数据是否损坏
- 恢复时间：从故障发生到训练恢复的总时间

## 2. 前提条件

| 项目 | 要求 |
|------|------|
| ModelArts 专属资源池 | 已创建，含至少 1 个训练节点 |
| SFS Turbo 文件系统 | 已创建，挂载到训练容器（如 `/mnt/sfs-turbo`） |
| 训练框架 | PyTorch |
| 容器权限 | 无 root 权限要求（Python 级注入） |

## 3. 文件清单

| 文件 | 说明 |
|------|------|
| `io_fault_injector.py` | 故障注入核心库，提供 5 种故障类型 |
| `train_with_fault_injection.py` | PyTorch 训练脚本（集成故障注入） |
| `fault_controller.py` | 外部故障触发控制器（通过标记文件控制） |

## 4. 故障注入原理

### 4.1 故障类型

| 故障类型 | 方法 | 原理 | 注入层面 |
|----------|------|------|----------|
| **read_hang** | `inject_read_hang()` | 在 SFS 路径上执行 `open().read()`，线程无限阻塞 | Python 线程 |
| **write_hang** | `inject_write_hang()` | 在 SFS 路径上 `open('w')` 写入数据，不关闭文件描述符 | Python 线程 |
| **checkpoint_hang** | `inject_checkpoint_hang()` | Monkey-patch `torch.save()`，调用时阻塞 | 函数替换 |
| **dataloader_hang** | `inject_dataloader_hang()` | Monkey-patch `Dataset.__getitem__()`，调用时阻塞 | 函数替换 |
| **full_io_hang** | `inject_full_io_hang()` | 替换 `builtins.open()`，对 SFS 路径的文件操作全部阻塞 | 内置函数替换 |

### 4.2 触发方式

| 触发方式 | 说明 | 适用场景 |
|----------|------|----------|
| **延时触发** | 训练开始 N 秒后自动注入 | 已知故障注入时机 |
| **触发文件** | 等待特定文件出现后注入 | 外部精确控制注入时机 |
| **释放文件** | 等待特定文件出现后解除 | 控制故障持续时间 |
| **环境变量** | 通过 `IO_FAULT_*` 环境变量配置 | ModelArts 训练作业配置 |

## 5. 测试场景

### 场景 1：数据读取 IO 卡死

**目的**：测试训练过程中数据读取完全卡死时，ModelArts 的检测与恢复能力。

```
启动命令：
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --fault-type read_hang \
    --fault-delay 30
```

**观察点**：
- [ ] 训练日志在 30s 后是否停止输出
- [ ] ModelArts 控制台是否显示训练异常
- [ ] 多久后 ModelArts 检测到异常（超时阈值？）
- [ ] 是否自动重启训练作业
- [ ] 重启后是否正确加载 checkpoint 续训

### 场景 2：Checkpoint 保存 IO 卡死

**目的**：测试模型保存时 IO 卡死，checkpoint 文件是否会损坏。

```
启动命令：
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --save-interval 1 \
    --fault-type checkpoint_hang \
    --fault-delay 60
```

**观察点**：
- [ ] `torch.save()` 调用后是否阻塞
- [ ] checkpoint 文件是否被创建但未写完（损坏文件）
- [ ] 损坏的 checkpoint 文件是否影响续训
- [ ] 之前保存的正常 checkpoint 是否可恢复

### 场景 3：数据加载 Worker 卡死

**目的**：测试 DataLoader 多 worker 场景下 worker 卡死的行为。

```
启动命令：
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --fault-type dataloader_hang \
    --fault-delay 20
```

**观察点**：
- [ ] DataLoader worker 是否被阻塞
- [ ] 主进程是否因 worker 阻塞而卡住
- [ ] 其他 worker 是否继续工作
- [ ] 训练是否最终超时

### 场景 4：全局 IO 卡死

**目的**：测试 SFS Turbo 所有 IO 操作完全卡死的最坏情况。

```
启动命令：
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --fault-type full_io_hang \
    --fault-delay 45
```

**观察点**：
- [ ] 所有 SFS 路径的 `open()` 是否被阻塞
- [ ] 训练日志输出是否停止（日志可能也写到 SFS）
- [ ] ModelArts 节点是否失联
- [ ] 是否触发 Pod 重调度
- [ ] 全局 IO 恢复后训练是否自动继续

### 场景 5：间歇性 IO 卡死

**目的**：测试周期性 IO 卡死对训练稳定性的影响。

```
启动命令（使用外部控制器）：
# 终端 1：启动训练（监听触发文件）
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --fault-type read_hang \
    --fault-trigger-file /mnt/sfs-turbo/.fault_trigger \
    --fault-release-file /mnt/sfs-turbo/.fault_release

# 终端 2：周期性触发和释放
for i in $(seq 1 5); do
    echo "=== 第 $i 次故障注入 ==="
    python fault_controller.py trigger-and-release \
        --trigger-file /mnt/sfs-turbo/.fault_trigger \
        --release-file /mnt/sfs-turbo/.fault_release \
        --hold-seconds 30 \
        --watch-status /mnt/sfs-turbo/checkpoints/training_status.txt
    sleep 60
done
```

**观察点**：
- [ ] 间歇性卡死是否累积导致训练失败
- [ ] 每次恢复后训练是否正常继续
- [ ] checkpoint 在间歇性卡死中是否完整
- [ ] 训练总时长增加多少

### 场景 6：Checkpoint 卡死后断点续训

**目的**：验证故障导致训练中断后，能否通过 checkpoint 正确恢复。

```
# 步骤 1：正常训练 + checkpoint 卡死（无限卡死）
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10 \
    --fault-type checkpoint_hang \
    --fault-delay 60

# 等待训练卡死后，ModelArts 超时终止作业

# 步骤 2：重新启动训练（自动加载 checkpoint 续训）
python train_with_fault_injection.py \
    --data-dir /mnt/sfs-turbo/data \
    --checkpoint-dir /mnt/sfs-turbo/checkpoints \
    --epochs 10
```

**观察点**：
- [ ] 续训是否从正确的 epoch 恢复
- [ ] 模型参数是否与卡死前一致
- [ ] 损坏的 checkpoint（如果有）是否被跳过
- [ ] 续训的 loss 是否从卡死前的水平继续下降

## 6. 使用方法

### 6.1 ModelArts 训练作业配置

在 ModelArts 创建训练作业时：

**代码目录**：上传 `io_fault_injector.py` 和 `train_with_fault_injection.py` 到代码目录

**启动命令**（按场景选择）：

```bash
# 场景1：读 IO 卡死
python train_with_fault_injection.py \
    --data-dir ${SFS_MOUNT_PATH}/data \
    --checkpoint-dir ${SFS_MOUNT_PATH}/checkpoints \
    --epochs 10 \
    --fault-type read_hang \
    --fault-delay 30

# 场景4：全局 IO 卡死
python train_with_fault_injection.py \
    --data-dir ${SFS_MOUNT_PATH}/data \
    --checkpoint-dir ${SFS_MOUNT_PATH}/checkpoints \
    --epochs 10 \
    --fault-type full_io_hang \
    --fault-delay 45
```

**环境变量方式**（推荐，更灵活）：

```bash
# 在 ModelArts 训练作业的环境变量中设置
IO_FAULT_TYPE=checkpoint_hang
IO_FAULT_DELAY=60
IO_FAULT_SFS_PATH=/mnt/sfs-turbo
```

### 6.2 外部控制器使用

`fault_controller.py` 可在另一个能访问 SFS Turbo 的环境中运行：

```bash
# 触发故障
python fault_controller.py trigger \
    --trigger-file /mnt/sfs-turbo/.fault_trigger

# 触发后 60 秒自动释放
python fault_controller.py trigger-and-release \
    --trigger-file /mnt/sfs-turbo/.fault_trigger \
    --release-file /mnt/sfs-turbo/.fault_release \
    --hold-seconds 60 \
    --watch-status /mnt/sfs-turbo/checkpoints/training_status.txt

# 监控训练状态（超过 300 秒未更新则告警）
python fault_controller.py monitor \
    --status-file /mnt/sfs-turbo/checkpoints/training_status.txt \
    --interval 10 \
    --timeout 300
```

## 7. 测试结果记录模板

| 指标 | 场景1 | 场景2 | 场景3 | 场景4 | 场景5 | 场景6 |
|------|-------|-------|-------|-------|-------|-------|
| 故障注入成功 | | | | | | |
| 训练日志停止时间 | | | | | | |
| ModelArts 检测到异常 | | | | | | |
| 检测耗时（秒） | | | | | | |
| 自动重启 | | | | | | |
| 重启后正确续训 | | | | | | |
| Checkpoint 完整性 | | | | | | |
| 恢复后训练正确性 | | | | | | |
| 总恢复时间（秒） | | | | | | |
| 备注 | | | | | | |

## 8. 注意事项

1. **测试环境隔离**：请在测试专属的 SFS Turbo 实例上操作，避免影响生产数据
2. **故障释放**：使用 `duration` 参数或释放文件确保故障可解除，避免永久卡死
3. **日志观察**：ModelArts 控制台的训练日志和 AOM 日志都应观察
4. **资源释放**：测试完成后确认所有训练作业已停止，避免持续计费
5. **多次验证**：每个场景建议运行 3 次以上取平均值
