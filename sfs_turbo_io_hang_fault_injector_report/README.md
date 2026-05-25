# 华为云 SFS Turbo IO 卡死故障注入工具集

> 本工具用于在授权测试环境中主动模拟华为云 SFS Turbo (Scalable File Service Turbo) 常见的 IO 卡死故障场景，适用于混沌工程测试、故障演练和应急响应培训。

## 背景

[SFS Turbo](https://support.huaweicloud.com/intl/zh-cn/sfsturbo/index.html) 是华为云提供的高性能弹性文件存储服务 (NAS)，基于 NFS 协议为 ECS、CCE、BMS 提供共享文件访问。在实际生产中，SFS Turbo 可能因网络、配置、后端等多种原因出现 IO 卡死问题，导致业务中断。

本工具集将华为云官方文档中记录的故障场景转化为可执行的故障注入脚本，帮助团队提前暴露问题、验证应急预案。

## 故障场景总览

| ID | 故障名称 | 注入原理 | 危险等级 |
|---|---|---|---|
| fault-01 | NFS hard mount D-state 卡死 | iptables DROP 到 SFS Turbo 的所有流量 | CRITICAL |
| fault-02 | 网络延迟注入 | tc netem 添加 1000ms±500ms 延迟 | MEDIUM |
| fault-03 | 网络丢包注入 | tc netem 添加 50% 丢包率 | HIGH |
| fault-04 | NFS 端口阻断 | iptables DROP 端口 111/2049/2051/2052/20048 | HIGH |
| fault-05 | 磁盘容量耗尽 | dd 持续写入填满磁盘空间 | HIGH |
| fault-06 | Inode 耗尽 | 批量创建 0 字节文件 | HIGH |
| fault-07 | IOPS 压力打满 | 多线程随机 4K IO (fio/dd) | MEDIUM |
| fault-08 | 文件锁竞争死锁 | 多进程 flock 排他锁互斥 | MEDIUM |
| fault-09 | Stale file handle | 打开 fd 后删除底层文件 | MEDIUM |
| fault-10 | 并发连接压力 | 200+ TCP 连接到 NFS 端口 | MEDIUM |
| fault-11 | DNS 解析故障 | 替换 /etc/resolv.conf | HIGH |
| fault-12 | VPC 路由黑洞 | ip route add blackhole | CRITICAL |
| fault-13 | 随机 IO 错误 | LD_PRELOAD 注入 EIO 返回 | HIGH |
| fault-14 | IO 带宽打满 | tc HTB 限速到 1kbit/s | MEDIUM |
| fault-15 | umount 残留状态 | IO 活跃期间执行 lazy umount | CRITICAL |

## 故障详细说明

### fault-01: NFS hard mount 进程 D-state 卡死

- **模拟场景**: SFS Turbo 后端完全不可达
- **注入方式**: 使用 iptables 将到 SFS Turbo IP 的所有出方向流量 DROP
- **预期现象**: 所有访问挂载点的进程进入 D-state (uninterruptible sleep)，ls/cat/df 等命令完全挂起，系统 load average 飙升，`kill -9` 无法终止
- **恢复方式**: 删除 iptables 规则；已进入 D-state 的进程可能需要重启 ECS
- **对应华为云告警**: 无（客户端侧现象）
- **参考文档**: [文件系统性能较差](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0115.html)

### fault-02: 网络延迟注入

- **模拟场景**: VPC 网络抖动，ECS 到 SFS Turbo 延迟异常升高
- **注入方式**: 使用 tc (traffic control) 的 netem qdisc，向到 SFS Turbo IP 的流量添加 1000ms±500ms 延迟
- **预期现象**: NFS RPC 请求响应时间从毫秒级升至秒级，IO 操作极慢但不完全失败
- **恢复方式**: `tc qdisc del dev <interface> root`
- **参考文档**: [文件系统挂载超时](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0348.html)

### fault-03: 网络丢包注入

- **模拟场景**: 网络质量极差，大量丢包导致 NFS 重传风暴
- **注入方式**: 使用 tc netem 向到 SFS Turbo IP 的流量注入 50% 丢包率（含 25% 相关性）
- **预期现象**: `nfsstat -c` 显示 retrans 计数急剧上升，IO 超时和错误频发
- **恢复方式**: `tc qdisc del dev <interface> root`

### fault-04: NFS 端口阻断

- **模拟场景**: 安全组规则被误修改，NFS 协议所需端口被阻断
- **注入方式**: iptables DROP 到 SFS Turbo IP 的 TCP 端口 111/2049/2051/2052/20048
- **预期现象**: 新挂载失败 (`mount.nfs: Connection timed out`)，已有连接陆续超时，`showmount -e` 失败
- **恢复方式**: 删除 iptables 规则
- **参考文档**: [文件系统写入失败](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0125.html)

### fault-05: 文件系统容量耗尽

- **模拟场景**: 业务持续写入导致 SFS Turbo 容量耗尽
- **注入方式**: 使用 dd 向挂载点写入大文件直到磁盘使用率 ≥ 99%
- **预期现象**: 写入返回 `No space left on device`，依赖写入的业务全面中断
- **恢复方式**: 删除填充文件 `.fault_injector_filler`

### fault-06: Inode 耗尽

- **模拟场景**: 大量小文件（如日志碎片）耗尽 inode
- **注入方式**: 批量创建 0 字节文件直到 inode 使用率 ≥ 99%
- **预期现象**: `df -h` 显示有空间但无法创建新文件（`No space left on device`），`df -i` 显示 100%
- **恢复方式**: 删除 `.fault_injector_inodes/` 目录

### fault-07: IOPS 压力打满

- **模拟场景**: 突发 IO 压力超过 SFS Turbo 规格限制，触发限流
- **注入方式**: 8 个线程同时执行 4K 随机读写 (优先使用 fio，备选 dd 循环)
- **预期现象**: 其他业务的 IO 延迟急剧升高，出现排队现象
- **恢复方式**: kill 所有压力进程，删除测试目录

### fault-08: 并发文件锁竞争

- **模拟场景**: NFS Lock Manager 异常，多进程锁竞争导致 IO 死锁
- **注入方式**: 1 个进程持有排他锁 (flock LOCK_EX) 不释放，5 个进程阻塞等待
- **预期现象**: 5 个等待进程阻塞在 flock() 系统调用，`strace -p <pid>` 可见 flock 阻塞
- **恢复方式**: kill 锁持有进程
- **参考文档**: [文件系统性能较差](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0115.html)

### fault-09: Stale NFS file handle

- **模拟场景**: SFS Turbo 后端升级/重启后，客户端缓存的文件句柄失效
- **注入方式**: 打开文件描述符后删除底层文件，fd 仍被进程持有
- **预期现象**: 通过 `/proc/<pid>/fd/` 访问已删除文件可能出现 stale 错误
- **恢复方式**: kill 持有 fd 的进程，删除测试目录

### fault-10: 大量并发连接压力

- **模拟场景**: 挂载 SFS Turbo 的 ECS 数量过多，超过连接数限制
- **注入方式**: 发起 200+ 个并发 TCP 连接到 SFS Turbo 的 2049 端口
- **预期现象**: 新的 NFS 操作被排队或超时
- **恢复方式**: 关闭所有测试连接

### fault-11: DNS 解析故障

- **模拟场景**: ECS 的 DNS 配置异常，无法解析 SFS Turbo 域名
- **注入方式**: 将 `/etc/resolv.conf` 中的 nameserver 替换为不可达地址
- **预期现象**: `nslookup` 解析失败，新挂载操作失败（已挂载的可能不受影响，因 IP 缓存在内核）
- **恢复方式**: 恢复 `/etc/resolv.conf` 备份
- **参考文档**: [文件系统挂载超时](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0348.html)

### fault-12: VPC 路由黑洞

- **模拟场景**: VPC 路由表被误修改，到 SFS Turbo 的流量被路由到黑洞
- **注入方式**: `ip route add blackhole <SFS_Turbo_IP>/32`
- **预期现象**: 所有到 SFS Turbo 的流量被内核丢弃，hard mount 进程进入 D-state，`ping` 返回 Destination Host Unreachable
- **恢复方式**: `ip route del blackhole <SFS_Turbo_IP>/32`

### fault-13: 随机 IO 错误注入

- **模拟场景**: SFS Turbo 后端存储出现随机 IO 错误 (告警 17321020082)
- **注入方式**: 编译 LD_PRELOAD 库拦截 read/write/open 系统调用，10% 概率返回 EIO
- **预期现象**: 使用 `LD_PRELOAD` 运行的程序随机遇到 Input/output error
- **恢复方式**: 删除注入库和测试文件
- **参考文档**: [17321020082 - File System Storage Backend I/O Error](https://support.huawei.com/enterprise/en/doc/EDOC1100374337/29fa681/17321020082-file-system-storage-backend-i-o-error)

### fault-14: IO 带宽打满

- **模拟场景**: 吞吐量达到 SFS Turbo 规格上限
- **注入方式**: 使用 tc HTB 将到 SFS Turbo 的带宽限制为 1kbit/s
- **预期现象**: 大文件传输几乎停滞，dd 写入 1MB 需要极长时间
- **恢复方式**: `tc qdisc del dev <interface> root`

### fault-15: NFS umount 残留状态

- **模拟场景**: 运维人员强制 umount 后 NFS 客户端状态残留
- **注入方式**: 在 dd 持续写入期间执行 `umount -l` (lazy umount)
- **预期现象**: 重新挂载可能失败，提示 device is busy 或 mount.nfs: mounting failed
- **恢复方式**: `umount -l` 清理残留，可能需要重启 ECS
- **参考文档**: [云服务器无法访问文件系统](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0058.html)

## 使用方法

本项目提供两种使用方式：**独立脚本**（推荐）和 **一体化工具备**。

### 方式一：独立故障注入脚本（推荐）

每个故障场景对应一个独立的 Python 脚本，可直接在已挂载 SFS Turbo 的 ECS 上运行，无需额外安装。

#### 脚本目录结构

```
sfs_turbo_fault_scripts/
├── common.py                              # 公共库（所有脚本共用）
├── fault-01-nfs-hard-mount-hang.py        # NFS hard mount D-state 卡死
├── fault-02-network-latency.py            # 网络延迟注入
├── fault-03-network-packet-loss.py        # 网络丢包注入
├── fault-04-nfs-port-block.py             # NFS 端口阻断
├── fault-05-disk-full.py                  # 磁盘容量耗尽
├── fault-06-inode-exhaustion.py           # Inode 耗尽
├── fault-07-iops-pressure.py              # IOPS 压力打满
├── fault-08-file-lock-deadlock.py         # 文件锁竞争死锁
├── fault-09-stale-file-handle.py          # Stale file handle
├── fault-10-concurrent-connections.py     # 并发连接压力
├── fault-11-dns-failure.py                # DNS 解析故障
├── fault-12-vpc-route-blackhole.py        # VPC 路由黑洞
├── fault-13-random-io-error.py            # 随机 IO 错误
├── fault-14-bandwidth-saturation.py       # IO 带宽打满
├── fault-15-umount-residual.py            # umount 残留状态
├── business-app-filelock.py               # 业务进程模拟器（配合 fault-08 验证）
├── pio-fault-01-ldpreload-full-error.py   # LD_PRELOAD 全量 I/O 错误注入
├── pio-fault-02-ptrace-syscall.py         # ptrace 系统调用拦截注入
├── pio-fault-03-cgroups-io-throttle.py    # cgroups v2 I/O 限流禁止
├── pio-fault-04-strace-inject.py          # strace 故障注入启动器
├── pio-fault-05-pidfd-send-signal.py      # 进程冻结/FD 关闭
└── pio-fault-06-ebpf-io-error.py          # eBPF I/O 错误注入
```

#### 脚本与依赖对照表

| 脚本 | 注入原理 | 系统依赖 |
|------|---------|---------|
| fault-01 | iptables DROP 全部流量 | iptables |
| fault-02 | tc netem 延迟 1000ms±500ms | iproute2 (tc) |
| fault-03 | tc netem 丢包 50% | iproute2 (tc) |
| fault-04 | iptables DROP NFS 端口 | iptables |
| fault-05 | dd 填满磁盘 | 无额外依赖 |
| fault-06 | touch 批量创建空文件 | 无额外依赖 |
| fault-07 | 多线程 4K 随机 IO | fio（可选，备选 dd） |
| fault-08 | flock 排他锁竞争 | Python 内置 |
| fault-09 | 删除已打开的文件 | Python 内置 |
| fault-10 | 200+ TCP 连接 | Python 内置 |
| fault-11 | 替换 resolv.conf | 无额外依赖 |
| fault-12 | ip route blackhole | iproute2 |
| fault-13 | LD_PRELOAD 注入 EIO | gcc |
| fault-14 | tc HTB 限速 1kbit/s | iproute2 (tc) |
| fault-15 | IO 活跃时 lazy umount | 无额外依赖 |
| business-app-filelock | 模拟业务进程读写（配合 fault-08） | Python 内置 |

#### 业务进程模拟器（business-app-filelock.py）

模拟正常业务进程，尝试获取文件排他锁后进行数据读写操作，配合 fault-08 验证文件锁竞争对业务的影响。

**工作流程：**

1. 非阻塞尝试获取排他锁（`LOCK_EX | LOCK_NB`），超时重试
2. 获取成功 → 读取文件最后 10 个字符 → 写入时间戳和随机文字 → 释放锁
3. 获取失败 → 打印超时信息，**不会卡死**，等待下一轮重试
4. 随机等待 3~8 秒后进入下一轮循环
5. 默认运行 1 小时后退出，打印统计信息

**关键设计：** 使用 `LOCK_NB`（非阻塞模式），即使锁被其他进程持有也不会卡死在 `flock()` 调用上，可以持续运行并观察故障影响。

```bash
# 默认运行 1 小时
sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo

# 自定义运行 2 小时
sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo --runtime 7200

# 自定义锁超时 5 秒
sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo --lock-timeout 5
```

**配合 fault-08 演练示例：**

```bash
# 终端 1: 启动业务进程模拟器（正常运行）
sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo --runtime 3600
# 正常输出:
#   [10:30:01] 第 1 轮 | 剩余 3599s | 尝试获取锁... 成功 (耗时 0.0s)
#           读取尾部: ...<空文件>
#           写入数据: [2026-05-24 10:30:01] PID=12345 data=aB3xKp9mN2qR

# 终端 2: 注入 fault-08 文件锁竞争故障
sudo python3 fault-08-file-lock-deadlock.py --mount-point /mnt/sfs_turbo

# 回到终端 1: 观察业务受影响情况
# 锁获取开始超时:
#   [10:31:15] 第 15 轮 | 剩余 3525s | 尝试获取锁... 获取失败 (耗时 10.0s, 累计超时 1 次)
#   [10:31:28] 第 16 轮 | 剩余 3512s | 尝试获取锁... 获取失败 (耗时 10.0s, 累计超时 2 次)

# 终端 3: 清理故障
sudo python3 fault-08-file-lock-deadlock.py --cleanup --mount-point /mnt/sfs_turbo

# 回到终端 1: 业务恢复正常
#   [10:32:01] 第 20 轮 | 剩余 3479s | 尝试获取锁... 成功 (耗时 0.0s)
```

**运行结束统计输出：**

```
============================================================
  运行统计
  总运行时间:  3600 秒 (60.0 分钟)
  总循环次数:  420
  获取锁成功:  380 次
  获取锁超时:  40 次
  异常次数:    0 次
  锁获取成功率: 90.5%
============================================================
```

#### 独立脚本使用方式

所有脚本遵循统一的参数规范：

```bash
# 注入故障（需 sudo）
sudo python3 fault-01-nfs-hard-mount-hang.py --mount-point /mnt/sfs_turbo

# 注入故障并定时自动恢复（60 秒后）
sudo python3 fault-02-network-latency.py --mount-point /mnt/sfs_turbo --duration 60

# 手动恢复/清理故障
sudo python3 fault-01-nfs-hard-mount-hang.py --cleanup --mount-point /mnt/sfs_turbo
```

各脚本特有参数：
- `fault-10`: 额外支持 `--count 200` 指定并发连接数

#### 典型演练示例

```bash
# === 示例 1: 模拟 NFS 后端完全不可达 (fault-01) ===

# 终端 1: 注入故障
sudo python3 fault-01-nfs-hard-mount-hang.py --mount-point /mnt/sfs_turbo --duration 120

# 终端 2: 观察 D-state 进程
watch -n 1 'ps aux | awk "\$8~/D/"'
ls /mnt/sfs_turbo   # 会卡住

# 120 秒后自动恢复

# === 示例 2: 模拟网络丢包导致 NFS 重传 (fault-03) ===

# 终端 1: 注入故障
sudo python3 fault-03-network-packet-loss.py --mount-point /mnt/sfs_turbo --duration 60

# 终端 2: 观察 NFS 重传统计
watch -n 1 'nfsstat -c | grep retrans'
time dd if=/dev/zero of=/mnt/sfs_turbo/test bs=1M count=10 oflag=direct

# === 示例 3: 模拟磁盘满导致写入失败 (fault-05) ===

# 注入
sudo python3 fault-05-disk-full.py --mount-point /mnt/sfs_turbo

# 验证
df -h /mnt/sfs_turbo
touch /mnt/sfs_turbo/test_file  # No space left on device

# 恢复
sudo python3 fault-05-disk-full.py --cleanup --mount-point /mnt/sfs_turbo

# === 示例 4: 模拟随机 IO 错误 (fault-13) ===

# 注入（编译 LD_PRELOAD 库）
sudo python3 fault-13-random-io-error.py --mount-point /mnt/sfs_turbo

# 使用注入库运行程序
LD_PRELOAD=/tmp/sfs_fault_ldpreload/fault_inject.so cp /etc/hosts /mnt/sfs_turbo/test_copy
# 约 10% 概率遇到 "Input/output error"

# 清理
sudo python3 fault-13-random-io-error.py --cleanup --mount-point /mnt/sfs_turbo
```

### 方式二：一体化工具

集成版脚本 `sfs_turbo_io_hang_fault_injector.py` 包含全部 15 个故障场景，通过参数选择注入。

```bash
# 查看所有故障场景
python3 sfs_turbo_io_hang_fault_injector.py --list

# 注入指定故障
sudo python3 sfs_turbo_io_hang_fault_injector.py --inject fault-01 --mount-point /mnt/sfs_turbo

# 注入并设置自动恢复时间
sudo python3 sfs_turbo_io_hang_fault_injector.py --inject fault-02 --mount-point /mnt/sfs_turbo --duration 60

# 清理指定故障
sudo python3 sfs_turbo_io_hang_fault_injector.py --cleanup fault-01

# 清理所有活跃故障
sudo python3 sfs_turbo_io_hang_fault_injector.py --cleanup all
```

### 环境要求

- Linux ECS 实例（已挂载 SFS Turbo）
- Python 3.6+
- Root 权限
- 工具依赖（按需）:
  - `iptables` — fault-01, fault-04
  - `tc` (iproute2) — fault-02, fault-03, fault-14
  - `gcc` — fault-13
  - `fio`（可选）— fault-07

## 安全设计

| 特性 | 说明 |
|------|------|
| **确认提示** | 注入前需手动输入 `YES` 确认 |
| **状态持久化** | 每个故障状态保存在 `/tmp/sfs_turbo_fault_injector/` |
| **定向注入** | 网络/路由故障仅影响 SFS Turbo IP，不影响其他流量 |
| **安全清理** | `--cleanup all` 会重置 iptables 和 tc，恢复网络正常 |
| **危险等级** | 每个故障标注 CRITICAL/HIGH/MEDIUM 级别 |
| **自动恢复** | `--duration` 参数支持定时自动恢复 |

## 故障分类统计

### 按严重程度

| 严重程度 | 数量 | 故障 ID |
|----------|------|---------|
| CRITICAL | 3 | fault-01, fault-12, fault-15 |
| HIGH | 6 | fault-03, fault-04, fault-05, fault-06, fault-11, fault-13 |
| MEDIUM | 6 | fault-02, fault-07, fault-08, fault-09, fault-10, fault-14 |

### 按故障分类

| 分类 | 数量 | 故障 ID |
|------|------|---------|
| NFS 客户端侧 | 4 | fault-01, fault-08, fault-09, fault-15 |
| 网络层 | 3 | fault-02, fault-03, fault-12 |
| 安全组/防火墙 | 1 | fault-04 |
| SFS 后端 | 3 | fault-07, fault-10, fault-14 |
| 容量/配额 | 2 | fault-05, fault-06 |
| DNS | 1 | fault-11 |
| IO 错误 | 1 | fault-13 |

## 进程级 I/O 故障注入 (pio-fault)

除了面向 SFS Turbo 文件系统级别的故障注入（fault-01~15），本工具集还提供**进程级** I/O 故障注入脚本（pio-fault-01~06），可精确控制目标进程的 I/O 行为，不影响系统其他进程。

### pio-fault 总览

| ID | 故障名称 | 注入原理 | 可附加运行中进程 | 性能开销 | 系统依赖 |
|---|---------|---------|:---:|:---:|------|
| pio-fault-01 | LD_PRELOAD 全量 I/O 错误 | 用户态库注入拦截 libc 函数 | 否 | 极低 | gcc |
| pio-fault-02 | ptrace 系统调用拦截 | ptrace 附加 + 修改寄存器返回值 | 是 | 极高 (10-100x) | Python ctypes |
| pio-fault-03 | cgroups v2 I/O 限流禁止 | 内核 I/O 调度层设置 bw/iops=0 | 是 | 极低 | cgroups v2 |
| pio-fault-04 | strace 故障注入启动器 | strace -e inject 内置功能 | 否 | 中 (2-10x) | strace >= 4.15 |
| pio-fault-05 | 进程冻结/FD 关闭 | SIGSTOP / gdb 关闭 fd | 是 | 无 | gdb (关闭fd时) |
| pio-fault-06 | eBPF I/O 错误注入 | 内核 eBPF 修改 syscall 返回值 | 是 | 低 (1-5%) | BCC + 内核 4.7+ |

### pio-fault-01: LD_PRELOAD 全量 I/O 错误

编译 C 共享库 (.so)，通过 LD_PRELOAD 环境变量注入到目标进程，在用户态拦截 libc 的全部 I/O 函数（read/write/open/close/fsync/stat/mkdir/unlink/rename/chmod）并返回指定错误。

```bash
# 注入（编译注入库）
sudo python3 pio-fault-01-ldpreload-full-error.py --mount-point /mnt/sfs_turbo

# 使用 — 100% 全部 I/O 返回 EIO
LD_PRELOAD=/tmp/sfs_fault_pio_01/full_io_error.so cp /etc/hosts /mnt/sfs_turbo/test

# 指定错误类型和失败率
sudo python3 pio-fault-01-ldpreload-full-error.py --error-type enospc --fail-rate 50

# 清理
sudo python3 pio-fault-01-ldpreload-full-error.py --cleanup
```

支持错误类型: `eio` / `eacces` / `emfile` / `enospc` / `enoent` / `enotdir`

### pio-fault-02: ptrace 系统调用拦截

利用 ptrace PTRACE_SYSCALL 模式附加到运行中进程，在每次系统调用退出时修改返回寄存器为错误码。可拦截直接 syscall 指令调用（静态编译程序也有效）。

```bash
# 附加到运行中进程
sudo python3 pio-fault-02-ptrace-syscall.py --pid 12345

# 指定错误和失败率
sudo python3 pio-fault-02-ptrace-syscall.py --pid 12345 --error-type eacces --fail-rate 50

# Ctrl+C 停止后自动 detach
```

### pio-fault-03: cgroups v2 I/O 限流禁止

将目标进程移入专用 cgroup，设置 io.max 的 rbytes/wbytes/riops/wiops 为 0，内核在 I/O 调度层直接拒绝该 cgroup 的所有 I/O 请求。

```bash
# 禁止进程 I/O（自动检测块设备）
sudo python3 pio-fault-03-cgroups-io-throttle.py --pid 12345

# 指定块设备
sudo python3 pio-fault-03-cgroups-io-throttle.py --pid 12345 --device sda1

# 恢复
sudo python3 pio-fault-03-cgroups-io-throttle.py --cleanup
```

### pio-fault-04: strace 故障注入启动器

利用 strace 内置的 `-e inject` 功能，在系统调用级别注入 I/O 错误。无需编写 C 代码，一行命令搞定。

```bash
# 全部 I/O 返回 EIO
sudo python3 pio-fault-04-strace-inject.py --error-type eio -- cp /etc/hosts /mnt/sfs_turbo/test

# 50% 概率返回 ENOSPC
sudo python3 pio-fault-04-strace-inject.py --error-type enospc --fail-rate 50 -- \
    dd if=/dev/zero of=/mnt/sfs_turbo/test bs=1M count=100
```

### pio-fault-05: 进程冻结 / 文件描述符关闭

三种子方式：
- `stop` — SIGSTOP 冻结整个进程（可 SIGCONT 恢复）
- `close-fd` — 关闭指定 fd（不可逆，后续 I/O 返回 EBADF）
- `close-nfs-fds` — 关闭所有 NFS 相关 fd

```bash
# 冻结进程
sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method stop

# 关闭指定 fd
sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method close-fd --fd 3,4,5

# 关闭所有 NFS fd
sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method close-nfs-fds

# 恢复（SIGSTOP 方式可恢复，关闭 fd 不可逆）
sudo python3 pio-fault-05-pidfd-send-signal.py --cleanup
```

### pio-fault-06: eBPF I/O 错误注入

加载 eBPF 程序到内核，挂载到 syscalls:sys_exit_read/write/openat 等追踪点，当目标 PID 的系统调用返回时修改返回寄存器为 -errno。性能开销极低（约 1-5%），是附加运行中进程的最优方案。

```bash
# 注入到运行中进程
sudo python3 pio-fault-06-ebpf-io-error.py --pid 12345

# 50% 失败率
sudo python3 pio-fault-06-ebpf-io-error.py --pid 12345 --fail-rate 50

# Ctrl+C 停止后 eBPF 程序自动卸载
```

需要安装 BCC: `apt install -y bpfcc-tools python3-bpfcc linux-headers-$(uname -r)`

### pio-fault 选型决策树

```
能否重启目标进程？
├── 是 → 追求简单？ → pio-fault-04 (strace)
│       └── 追求低开销？ → pio-fault-01 (LD_PRELOAD)
└── 否 → 环境有 BCC？
        ├── 是 → pio-fault-06 (eBPF) ← 最优
        └── 否 → 需要拦截直接 syscall？
                ├── 是 → pio-fault-02 (ptrace) ← 性能代价大
                └── 否 → pio-fault-03 (cgroups) ← NFS 效果有限
                         或 pio-fault-05 (SIGSTOP) ← 冻结整个进程
```

## 推荐的 SFS Turbo 挂载参数

```bash
mount -t nfs -o vers=3,nolock,hard,noresvport,rsize=1048576,wsize=1048576,timeo=600,retrans=2 \
    <SFS_Turbo_Address>:/! /mnt/sfs_turbo
```

参数说明：
- `vers=3`: NFSv3 协议
- `nolock`: 禁用 NFS 文件锁（减少 NLM 依赖）
- `hard`: 硬挂载（保证数据一致性）
- `noresvport`: 不使用保留端口
- `rsize/wsize=1048576`: 最大读写块 1MB
- `timeo=600`: RPC 超时 60 秒
- `retrans=2`: 重试 2 次

## 参考文档

- [SFS Turbo 产品文档](https://support.huaweicloud.com/intl/zh-cn/sfsturbo/index.html)
- [文件系统性能较差 - 故障排除](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0115.html)
- [云服务器无法访问文件系统](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0058.html)
- [文件系统写入失败](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0125.html)
- [文件系统挂载超时](https://support.huaweicloud.com/intl/zh-cn/trouble-sfsturbo/sfsturbo_01_0348.html)
- [17321020082 - Backend IO Error](https://support.huawei.com/enterprise/en/doc/EDOC1100374337/29fa681/17321020082-file-system-storage-backend-i-o-error)
- [17321020001 - Instance IO Error](https://support.huawei.com/enterprise/zh/doc/EDOC1100374335/c764a2a9)
- [17321020010 - showmount Abnormality](https://support.huawei.com/enterprise/zh/doc/EDOC1100374335/8da6bab4)
- [SFS Turbo 维护指南 (华为云Stack)](https://support.huawei.com/enterprise/zh/doc/EDOC1100450808/6ac7b3b9)
- [Red Hat - Using nfsstat and nfsiostat](https://www.redhat.com/en/blog/using-nfsstat-nfsiostat)
