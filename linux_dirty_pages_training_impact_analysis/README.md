# Linux vm.dirty_background_bytes 对大模型训练性能的影响分析

> 基于 Linux 内核脏页写回机制，深度分析 `vm.dirty_background_bytes` 及相关参数在大模型训练场景（特别是华为云 ModelArts）中的影响，覆盖 Checkpoint 保存、数据加载、SFS Turbo 挂载等核心场景，提供调优建议与诊断方法。

---

## 一、参数定义与机制

### 1.1 什么是脏页（Dirty Pages）

当用户进程通过 `write()` 系统调用写入文件时，数据先进入**Page Cache（页缓存）**并被标记为"脏页"，随后由内核在合适的时机异步写回磁盘。

```
┌──────────────────────────────────────────────────────────┐
│              Linux 脏页写回机制                             │
│                                                            │
│  用户进程写入数据（如 torch.save()）                        │
│     │                                                      │
│     ▼                                                      │
│  ┌───────────────────────┐                                │
│  │   Page Cache（内存）   │ ← 数据先写入内存（标记为脏页） │
│  │   Dirty Pages         │                                │
│  └───────────┬───────────┘                                │
│              │                                             │
│     脏页数量达到 vm.dirty_background_bytes ？              │
│     ┌────────┴────────┐                                   │
│     │ 否               │ 是                                │
│     │                  ▼                                   │
│     │  ┌──────────────────────────────┐                   │
│     │  │ 后台写回线程（pdflush/kworker）│                   │
│     │  │ 在后台将脏页异步写入磁盘       │                   │
│     │  │ 用户进程不受阻塞               │                   │
│     │  └──────────────────────────────┘                   │
│     │                                                      │
│     │  脏页数量达到 vm.dirty_bytes ？                       │
│     │     └─ 是 → 用户进程被阻塞，强制同步写回              │
│     │                                                      │
│     ▼                                                      │
│  继续在内存中积累脏页                                      │
└──────────────────────────────────────────────────────────┘
```

### 1.2 脏页参数矩阵

| 参数 | 含义 | 触发行为 |
|------|------|---------|
| `vm.dirty_background_bytes` | 后台异步写回阈值（字节数） | 脏页达到此值 → 后台线程异步写回，**不阻塞用户进程** |
| `vm.dirty_bytes` | 强制同步写回阈值（字节数） | 脏页达到此值 → **阻塞**产生脏页的进程，强制同步写回 |
| `vm.dirty_background_ratio` | 同 `dirty_background_bytes`，但用内存百分比 | 与 bytes 参数二选一，设置 bytes 后 ratio 自动归零 |
| `vm.dirty_ratio` | 同 `dirty_bytes`，但用内存百分比 | 与 bytes 参数二选一 |
| `vm.dirty_expire_centisecs` | 脏页过期时间（厘秒） | 脏页存在超过此时间后必须写回（默认 3000 = 30秒） |
| `vm.dirty_writeback_centisecs` | 写回线程唤醒间隔（厘秒） | 后台写回线程多久唤醒一次检查（默认 500 = 5秒） |

**关键关系**：
```
dirty_background_bytes < dirty_bytes
       ↓                     ↓
  触发异步写回          触发同步阻塞写回
  （后台，不阻塞）     （前台，阻塞用户进程）
```

---

## 二、对大模型训练的影响场景

### 2.1 场景一：Checkpoint 保存（影响最大）

大模型训练中，Checkpoint 保存是产生脏页的主要来源。

```
大模型 Checkpoint 保存流程：

  训练进程调用 torch.save()
     │
     ├─ Step 1: 将模型权重序列化到内存 buffer
     │   → 假设千亿参数模型，CKPT ≈ 70GB
     │   → 瞬间产生约 70GB 脏页
     │
     ├─ Step 2: write() 系统调用写入文件
     │   → 数据进入 Page Cache（脏页）
     │
     └─ Step 3: 脏页写回磁盘
          │
          ├─ dirty_background_bytes 合理：
          │   → 后台线程平滑写回
          │   → 训练进程几乎不受影响
          │   → CKPT 保存"瞬间完成"（实际还在后台写盘）
          │
          ├─ dirty_background_bytes 过小：
          │   → 后台线程频繁触发写回
          │   → 与训练的磁盘 I/O（数据加载）竞争带宽
          │
          └─ dirty_bytes 过小（最危险）：
              → 70GB CKPT 很快达到强制写回阈值
              → 训练进程在 write() 时被阻塞
              → 训练暂停数秒甚至数十秒
```

**典型影响量级**：

| CKPT 大小 | dirty_bytes 设置 | 阻塞时长（本地 SSD） | 阻塞时长（SFS Turbo） |
|-----------|-----------------|--------------------|--------------------|
| 10GB | 1GB | 数秒 | 数秒 |
| 50GB | 1GB | 十余秒 | 数十秒 |
| 100GB | 1GB | 30s+ | 60s+ |
| 100GB | 32GB | 几乎无感知 | 几乎无感知 |

### 2.2 场景二：训练数据加载与缓存

```
DataLoader 读取训练数据文件
     │
     ├─ 文件首次读取 → 进入 Page Cache（干净页，不产生脏页）
     ├─ 新创建临时文件 → 产生脏页
     ├─ 数据预处理后写入本地 → 产生脏页
     │
     └─ 脏页积累过多的影响：
          ├─ 触发后台写回 → 与数据读取 I/O 竞争磁盘带宽
          │   → DataLoader 变慢 → GPU/NPU 等待数据
          │
          └─ 触发强制写回 → DataLoader 被阻塞
              → 训练出现卡顿
```

### 2.3 场景三：SFS Turbo / OBS 挂载写入

```
SFS Turbo 挂载场景（NFS 协议）：

  训练作业通过 NFS 挂载 SFS Turbo
     │
     ├─ CKPT 写入挂载目录
     │   → NFS 客户端缓存（Page Cache 脏页）
     │   → 内核将脏页通过 NFS 协议写入远端
     │
     └─ dirty_background_bytes 的影响：
          ├─ 设置过小 → 频繁触发 NFS 小包写入
          │   → NFS 协议开销大，有效带宽低
          │   → CKPT 写入变慢
          │
          └─ 设置过大 → 突发大量脏页通过 NFS 写回
              → 网络带宽瞬间打满
              → 影响参数面 NCCL/HCCL 通信（共享网络资源）
              → 分布式训练 AllReduce 延迟增大
```

### 2.4 场景四：分布式训练的间接影响

```
分布式训练中的间接影响链路：

  脏页写回 → 消耗 I/O 带宽和 CPU
     │
     ├─ 本地盘场景：
     │   → I/O 带宽占用 → 数据加载变慢 → GPU/NPU 利用率下降
     │
     ├─ SFS Turbo 场景：
     │   → 网络带宽占用 → 参数面通信延迟增大
     │   → AllReduce 性能下降 → 整体训练吞吐下降
     │
     └─ CPU 开销：
          → pdflush/kworker 线程消耗 CPU
          → 与训练进程竞争 CPU 时间片
          → NUMA 跨节点内存访问增加
```

---

## 三、不同设置的影响对比

### 3.1 设置过小

```
dirty_background_bytes = 64MB（某些默认值）

优势：
  ✅ 内存中脏页少，数据安全性高
  ✅ 系统崩溃时丢失的数据少

劣势：
  ❌ 后台写回线程频繁唤醒（每 5 秒 + 脏页超阈值）
  ❌ 与训练 I/O 竞争磁盘/网络带宽
  ❌ NFS 场景下频繁小包传输，有效带宽低
  ❌ 磁盘 I/O 调度延迟增加
  ❌ 整体训练吞吐下降 5%~15%
```

### 3.2 设置过大

```
dirty_background_bytes = 系统内存的 50%

优势：
  ✅ 减少后台写回频率，训练 I/O 不被打断
  ✅ 大文件顺序写入性能好（如 CKPT 保存）
  ✅ NFS 场景下减少小包传输

劣势：
  ❌ 突然需要大量内存时，回收脏页耗时
  ❌ 系统崩溃时可能丢失大量未写入的数据
  ❌ 接近 dirty_bytes 时可能触发突发大量写回
  ❌ 内存压力大时影响其他进程
```

### 3.3 推荐设置

| 训练场景 | 内存 | dirty_background_bytes | dirty_bytes | 原因 |
|----------|------|----------------------|------------|------|
| SFT/微调（CKPT < 10GB） | 128GB | 2GB | 16GB | CKPT 小，默认值即可 |
| 大模型预训练（CKPT 50-100GB） | 256GB | 8GB | 32GB | CKPT 大，需要更大缓冲 |
| 超大模型预训练（CKPT > 100GB） | 512GB+ | 16GB | 64GB | CKPT 极大，需要充足缓冲 |
| CKPT 保存到 SFS Turbo | 256GB | 4GB | 16GB | NFS 写入需平衡突发和频率 |
| CKPT 保存到本地 NVMe | 256GB | 8GB~16GB | 32GB~64GB | 本地 SSD 写入快，可承受大突发 |
| CKPT 保存到 OBS（obsfs） | 256GB | 4GB | 16GB | 对象存储上传有额外开销 |

---

## 四、查看与修改方法

### 4.1 查看当前值

```bash
# 查看当前设置
cat /proc/sys/vm/dirty_background_bytes
cat /proc/sys/vm/dirty_bytes

# 查看当前脏页量
cat /proc/meminfo | grep -i dirty
# Dirty:          1256324 kB   ← 当前脏页量
# Writeback:        32768 kB   ← 正在写回的量

# 查看写回统计
cat /proc/vmstat | grep -E "nr_dirty|nr_writeback|nr_written|pgpgout"
```

### 4.2 临时修改（重启失效）

```bash
# 设置后台写回阈值 8GB
sysctl -w vm.dirty_background_bytes=8589934592

# 设置强制写回阈值 32GB
sysctl -w vm.dirty_bytes=34359738368

# 验证修改
sysctl vm.dirty_background_bytes vm.dirty_bytes
```

### 4.3 ModelArts 训练作业中修改

```python
import subprocess

def tune_dirty_page_params():
    """在训练代码开头调整脏页参数"""
    try:
        subprocess.run(['sysctl', '-w', 'vm.dirty_background_bytes=8589934592'], check=True)
        subprocess.run(['sysctl', '-w', 'vm.dirty_bytes=34359738368'], check=True)
        print("Dirty page params tuned: bg=8G, force=32G")
    except subprocess.CalledProcessError:
        print("Need elevated privileges to modify sysctl")

# 在训练脚本最开头调用
tune_dirty_page_params()
```

**注意**：ModelArts 专属资源池可通过节点级别配置永久修改；公共资源池通常需要在代码中临时修改（需要容器有相应权限）。

---

## 五、监控与诊断

### 5.1 实时监控

```bash
# 持续监控脏页量（每秒刷新）
watch -n 1 "cat /proc/meminfo | grep -i dirty"

# 监控写回活动
watch -n 1 "cat /proc/vmstat | grep -E 'nr_dirty|nr_writeback'"
```

### 5.2 问题诊断

| 现象 | 可能原因 | 诊断命令 | 解决方案 |
|------|---------|---------|---------|
| CKPT 保存时训练暂停数秒 | `dirty_bytes` 过小 | 观察保存时 Dirty 是否达到 dirty_bytes | 增大 `dirty_bytes` |
| 训练整体变慢，I/O 等待高 | `dirty_background_bytes` 过小 | `iostat -x 1` 查看 %util | 适当增大 |
| CKPT 保存后系统卡死 | 脏页量极大，突发写回占满 I/O | `cat /proc/meminfo \| grep Dirty` | 增大阈值 + 分段保存 |
| NFS 写入缓慢 | 脏页频繁触发 NFS 小包 | `nfsstat -c` 查看 NFS 调用次数 | 增大 `dirty_background_bytes` |
| 内存不足 OOM | 脏页占满内存无法回收 | `cat /proc/meminfo \| grep -i dirty` | 减小阈值或增加内存 |

### 5.3 典型问题：CKPT 保存导致的训练卡顿

```
问题现象：
  训练正常 → CKPT 保存 → 训练暂停 10~30 秒 → 恢复 → 继续训练

诊断流程：
  1. 确认 CKPT 大小：ls -lh /path/to/ckpt/
  2. 查看当前 dirty_bytes：cat /proc/sys/vm/dirty_bytes
  3. 如果 dirty_bytes < CKPT 大小 → 这是根因
     → write() 在写完 dirty_bytes 大小后被阻塞
     → 等待之前的脏页写回磁盘后才继续
  4. 解决：将 dirty_bytes 设置为 CKPT 大小的 2~4 倍
```

---

## 六、与其他内核参数的协同

| 参数 | 协同关系 | 建议配置 |
|------|---------|---------|
| `vm.dirty_bytes` | 必须大于 dirty_background_bytes | 2~8 倍于 dirty_background_bytes |
| `vm.dirty_expire_centisecs` | 脏页过期时间 | 默认 3000（30秒），大模型训练可设 6000（60秒） |
| `vm.dirty_writeback_centisecs` | 写回线程唤醒间隔 | 默认 500（5秒），可适当增大到 1000~3000 |
| `vm.swappiness` | 控制内核倾向使用 swap | 训练场景建议设为 0 或 1 |
| `vm.overcommit_memory` | 内存过量分配策略 | 训练场景建议设为 1（允许过量分配） |
| `vm.min_free_kbytes` | 最小空闲内存保留 | 建议 1GB~4GB，防止脏页回收时内存不足 |

---

## 七、总结

| 要点 | 说明 |
|------|------|
| **核心作用** | 控制内核何时在后台异步写回脏页，避免阻塞用户进程 |
| **最大影响场景** | Checkpoint 保存（瞬间产生数十 GB 脏页） |
| **设置过小的后果** | 频繁后台写回，与训练 I/O 竞争带宽，整体吞吐下降 |
| **设置过大的后果** | 突发大量写回，崩溃时数据丢失风险 |
| **推荐值** | `dirty_background_bytes` = 2G~16G，`dirty_bytes` = 16G~64G |
| **关键原则** | `dirty_bytes` 应大于最大 CKPT 文件大小的 2~4 倍 |
| **与 ModelArts 关系** | 专属资源池可自定义节点 sysctl；公共池在代码中临时修改 |

---

## 参考文档

- [Linux Kernel Documentation — vm.dirty_background_bytes](https://www.kernel.org/doc/Documentation/sysctl/vm.txt)
- [华为云 ModelArts 训练作业故障恢复](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0019.html)
- [华为云 ModelArts 断点续训练](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0023.html)
- [Red Hat Enterprise Linux — Tuning Virtual Memory](https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/9/html/managing_monitoring_and_updating_the_kernel/tuning-the-virtual-memory-manager_managing-monitoring-and-updating-the-kernel)
