# 基于 obscmdbench 工具的 OBS 性能测试用例

> 工具来源：https://github.com/huaweicloud-obs/obscmdbench
> 编写日期：2026-06-09
> 说明：以下测试用例基于 obscmdbench 工具的 config.dat 参数配置体系，通过修改对应参数生成不同测试场景。

---

## 测试用例总览

> 共 **5 大场景、72 个用例**，覆盖顺序/随机读写、IOPS 峰值、带宽峰值等核心性能指标。

### 场景一：单客户端全覆盖测试（48 个用例）

> 测试目标：在单个客户端上覆盖 4 种读写类型 × 3 种并发 × 4 种对象大小的全组合，验证 OBS 基础读写功能正确性与不同负载下的性能基线。

| 用例编号 | 读写类型 | 对象大小 | 并发数 | 测试点 | config 关键配置 |
|:--------:|:-------:|:-------:|:------:|-------|---------------|
| S1-SEQ-W-4K-1 | 顺序写 | 4KB | 1 | 单线程顺序写入 4KB 对象，验证 PutObject 字典序命名、TPS 基线 | `Testcase=201, ObjectLexical=true` |
| S1-SEQ-W-4K-10 | 顺序写 | 4KB | 10 | 10 并发顺序写 4KB，验证并发线性扩展能力 | `ThreadsPerUser=10` |
| S1-SEQ-W-4K-100 | 顺序写 | 4KB | 100 | 100 并发顺序写 4KB，验证单客户端高并发写 TPS 上限 | `ThreadsPerUser=100` |
| S1-SEQ-W-32K-1 | 顺序写 | 32KB | 1 | 单线程顺序写入 32KB 对象，对比 4KB 的 TPS 与吞吐差异 | `ObjectSize=32768` |
| S1-SEQ-W-32K-10 | 顺序写 | 32KB | 10 | 10 并发顺序写 32KB，验证中等块大小的并发扩展性 | `ObjectSize=32768, ThreadsPerUser=10` |
| S1-SEQ-W-32K-100 | 顺序写 | 32KB | 100 | 100 并发顺序写 32KB，验证 32KB 写 TPS 上限 | `ObjectSize=32768, ThreadsPerUser=100` |
| S1-SEQ-W-1M-1 | 顺序写 | 1MB | 1 | 单线程顺序写入 1MB 对象，验证大对象写入延迟和吞吐基线 | `ObjectSize=1048576` |
| S1-SEQ-W-1M-10 | 顺序写 | 1MB | 10 | 10 并发顺序写 1MB，验证大对象并发写吞吐扩展 | `ObjectSize=1048576, ThreadsPerUser=10` |
| S1-SEQ-W-1M-100 | 顺序写 | 1MB | 100 | 100 并发顺序写 1MB，验证大对象高并发写吞吐上限 | `ObjectSize=1048576, ThreadsPerUser=100` |
| S1-SEQ-W-4M-1 | 顺序写 | 4MB | 1 | 单线程顺序写入 4MB 对象，验证最大块写入延迟 | `ObjectSize=4194304` |
| S1-SEQ-W-4M-10 | 顺序写 | 4MB | 10 | 10 并发顺序写 4MB，验证 4MB 并发写吞吐 | `ObjectSize=4194304, ThreadsPerUser=10` |
| S1-SEQ-W-4M-100 | 顺序写 | 4MB | 100 | 100 并发顺序写 4MB，验证最大块高并发写吞吐上限 | `ObjectSize=4194304, ThreadsPerUser=100` |
| S1-RAND-W-4K-1 | 随机写 | 4KB | 1 | 单线程随机对象名写入 4KB，验证随机命名写入正确性 | `ObjectLexical=false` |
| S1-RAND-W-4K-10 | 随机写 | 4KB | 10 | 10 并发随机写 4KB，对比顺序写的 TPS 差异 | `ObjectLexical=false, ThreadsPerUser=10` |
| S1-RAND-W-4K-100 | 随机写 | 4KB | 100 | 100 并发随机写 4KB，验证高并发随机写性能 | `ObjectLexical=false, ThreadsPerUser=100` |
| S1-RAND-W-32K-1 | 随机写 | 32KB | 1 | 单线程随机写 32KB 对象 | `ObjectSize=32768, ObjectLexical=false` |
| S1-RAND-W-32K-10 | 随机写 | 32KB | 10 | 10 并发随机写 32KB | `ObjectSize=32768, ObjectLexical=false, ThreadsPerUser=10` |
| S1-RAND-W-32K-100 | 随机写 | 32KB | 100 | 100 并发随机写 32KB | `ObjectSize=32768, ObjectLexical=false, ThreadsPerUser=100` |
| S1-RAND-W-1M-1 | 随机写 | 1MB | 1 | 单线程随机写 1MB 对象 | `ObjectSize=1048576, ObjectLexical=false` |
| S1-RAND-W-1M-10 | 随机写 | 1MB | 10 | 10 并发随机写 1MB | `ObjectSize=1048576, ObjectLexical=false, ThreadsPerUser=10` |
| S1-RAND-W-1M-100 | 随机写 | 1MB | 100 | 100 并发随机写 1MB | `ObjectSize=1048576, ObjectLexical=false, ThreadsPerUser=100` |
| S1-RAND-W-4M-1 | 随机写 | 4MB | 1 | 单线程随机写 4MB 对象 | `ObjectSize=4194304, ObjectLexical=false` |
| S1-RAND-W-4M-10 | 随机写 | 4MB | 10 | 10 并发随机写 4MB | `ObjectSize=4194304, ObjectLexical=false, ThreadsPerUser=10` |
| S1-RAND-W-4M-100 | 随机写 | 4MB | 100 | 100 并发随机写 4MB | `ObjectSize=4194304, ObjectLexical=false, ThreadsPerUser=100` |
| S1-SEQ-R-4K-1 | 顺序读 | 4KB | 1 | 单线程顺序读取 4KB 对象，验证 GetObject 字典序遍历正确性 | `Testcase=202, IsRandomGet=false` |
| S1-SEQ-R-4K-10 | 顺序读 | 4KB | 10 | 10 并发顺序读 4KB，验证读并发线性扩展 | `Testcase=202, IsRandomGet=false, ThreadsPerUser=10` |
| S1-SEQ-R-4K-100 | 顺序读 | 4KB | 100 | 100 并发顺序读 4KB，验证高并发顺序读 TPS 上限 | `Testcase=202, IsRandomGet=false, ThreadsPerUser=100` |
| S1-SEQ-R-32K-1 | 顺序读 | 32KB | 1 | 单线程顺序读 32KB，对比 4KB 读取 TPS 与吞吐差异 | `Testcase=202, ObjectSize=32768, IsRandomGet=false` |
| S1-SEQ-R-32K-10 | 顺序读 | 32KB | 10 | 10 并发顺序读 32KB | `Testcase=202, ObjectSize=32768, IsRandomGet=false, ThreadsPerUser=10` |
| S1-SEQ-R-32K-100 | 顺序读 | 32KB | 100 | 100 并发顺序读 32KB | `Testcase=202, ObjectSize=32768, IsRandomGet=false, ThreadsPerUser=100` |
| S1-SEQ-R-1M-1 | 顺序读 | 1MB | 1 | 单线程顺序读 1MB，验证大对象读取延迟和吞吐基线 | `Testcase=202, ObjectSize=1048576, IsRandomGet=false` |
| S1-SEQ-R-1M-10 | 顺序读 | 1MB | 10 | 10 并发顺序读 1MB | `Testcase=202, ObjectSize=1048576, IsRandomGet=false, ThreadsPerUser=10` |
| S1-SEQ-R-1M-100 | 顺序读 | 1MB | 100 | 100 并发顺序读 1MB | `Testcase=202, ObjectSize=1048576, IsRandomGet=false, ThreadsPerUser=100` |
| S1-SEQ-R-4M-1 | 顺序读 | 4MB | 1 | 单线程顺序读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=false` |
| S1-SEQ-R-4M-10 | 顺序读 | 4MB | 10 | 10 并发顺序读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=false, ThreadsPerUser=10` |
| S1-SEQ-R-4M-100 | 顺序读 | 4MB | 100 | 100 并发顺序读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=false, ThreadsPerUser=100` |
| S1-RAND-R-4K-1 | 随机读 | 4KB | 1 | 单线程随机读取 4KB 对象，验证随机访问正确性 | `Testcase=202, IsRandomGet=true` |
| S1-RAND-R-4K-10 | 随机读 | 4KB | 10 | 10 并发随机读 4KB，对比顺序读的 TPS 差异 | `Testcase=202, IsRandomGet=true, ThreadsPerUser=10` |
| S1-RAND-R-4K-100 | 随机读 | 4KB | 100 | 100 并发随机读 4KB，验证高并发随机读 TPS | `Testcase=202, IsRandomGet=true, ThreadsPerUser=100` |
| S1-RAND-R-32K-1 | 随机读 | 32KB | 1 | 单线程随机读 32KB | `Testcase=202, ObjectSize=32768, IsRandomGet=true` |
| S1-RAND-R-32K-10 | 随机读 | 32KB | 10 | 10 并发随机读 32KB | `Testcase=202, ObjectSize=32768, IsRandomGet=true, ThreadsPerUser=10` |
| S1-RAND-R-32K-100 | 随机读 | 32KB | 100 | 100 并发随机读 32KB | `Testcase=202, ObjectSize=32768, IsRandomGet=true, ThreadsPerUser=100` |
| S1-RAND-R-1M-1 | 随机读 | 1MB | 1 | 单线程随机读 1MB | `Testcase=202, ObjectSize=1048576, IsRandomGet=true` |
| S1-RAND-R-1M-10 | 随机读 | 1MB | 10 | 10 并发随机读 1MB | `Testcase=202, ObjectSize=1048576, IsRandomGet=true, ThreadsPerUser=10` |
| S1-RAND-R-1M-100 | 随机读 | 1MB | 100 | 100 并发随机读 1MB | `Testcase=202, ObjectSize=1048576, IsRandomGet=true, ThreadsPerUser=100` |
| S1-RAND-R-4M-1 | 随机读 | 4MB | 1 | 单线程随机读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=true` |
| S1-RAND-R-4M-10 | 随机读 | 4MB | 10 | 10 并发随机读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=true, ThreadsPerUser=10` |
| S1-RAND-R-4M-100 | 随机读 | 4MB | 100 | 100 并发随机读 4MB | `Testcase=202, ObjectSize=4194304, IsRandomGet=true, ThreadsPerUser=100` |

### 场景二：4KB 块大小 IOPS 峰值测试（6 个用例）

> 测试目标：使用 4KB 小块大小，在 100/500 高并发下压测 OBS 的 IOPS 极限能力，分别测试纯读、纯写、读写混合（2:1）场景。

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 | config 关键配置 |
|:--------:|:-------:|:-------:|:------:|-------|---------------|
| S2-READ-4K-100 | 混合读（纯读） | 4KB | 100 | 100 并发纯 GetObject，测试 4KB 稳态读 IOPS 峰值，记录 P50/P90/P99 延迟 | `MixOperations=202, RunSeconds=300` |
| S2-READ-4K-500 | 混合读（纯读） | 4KB | 500 | 500 并发纯 GetObject，极限压测 4KB 读 IOPS 上限，验证并发提升带来的 IOPS 增益 | `MixOperations=202, ThreadsPerUser=500, RunSeconds=300` |
| S2-WRITE-4K-100 | 混合写（纯写） | 4KB | 100 | 100 并发纯 PutObject，测试 4KB 稳态写 IOPS 峰值 | `MixOperations=201, RunSeconds=300` |
| S2-WRITE-4K-500 | 混合写（纯写） | 4KB | 500 | 500 并发纯 PutObject，极限压测 4KB 写 IOPS 上限 | `MixOperations=201, ThreadsPerUser=500, RunSeconds=300` |
| S2-MIX-4K-100 | 混合读写 2:1 | 4KB | 100 | 100 并发混合读写（2 Get:1 Put），测试 4KB 混合负载下总 IOPS 峰值及读写比例是否符合预期 | `MixOperations=202,202,201, RunSeconds=300` |
| S2-MIX-4K-500 | 混合读写 2:1 | 4KB | 500 | 500 并发混合读写 2:1，极限压测 4KB 混合 IOPS 上限 | `MixOperations=202,202,201, ThreadsPerUser=500, RunSeconds=300` |

### 场景三：32KB 块大小 IOPS 峰值测试（6 个用例）

> 测试目标：使用 32KB 中等块大小，在 100/500 高并发下压测 IOPS 极限能力，与 4KB 对比 IOPS 下降幅度和吞吐提升幅度。

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 | config 关键配置 |
|:--------:|:-------:|:-------:|:------:|-------|---------------|
| S3-READ-32K-100 | 混合读（纯读） | 32KB | 100 | 100 并发纯 GetObject，测试 32KB 读 IOPS 峰值，与 S2-READ-4K-100 对比 IOPS/吞吐差异 | `ObjectSize=32768, MixOperations=202, RunSeconds=300` |
| S3-READ-32K-500 | 混合读（纯读） | 32KB | 500 | 500 并发纯 GetObject，极限压测 32KB 读 IOPS 上限 | `ObjectSize=32768, MixOperations=202, ThreadsPerUser=500, RunSeconds=300` |
| S3-WRITE-32K-100 | 混合写（纯写） | 32KB | 100 | 100 并发纯 PutObject，测试 32KB 写 IOPS 峰值 | `ObjectSize=32768, MixOperations=201, RunSeconds=300` |
| S3-WRITE-32K-500 | 混合写（纯写） | 32KB | 500 | 500 并发纯 PutObject，极限压测 32KB 写 IOPS 上限 | `ObjectSize=32768, MixOperations=201, ThreadsPerUser=500, RunSeconds=300` |
| S3-MIX-32K-100 | 混合读写 2:1 | 32KB | 100 | 100 并发混合读写 2:1，测试 32KB 混合总 IOPS 峰值 | `ObjectSize=32768, MixOperations=202,202,201, RunSeconds=300` |
| S3-MIX-32K-500 | 混合读写 2:1 | 32KB | 500 | 500 并发混合读写 2:1，极限压测 32KB 混合 IOPS 上限 | `ObjectSize=32768, MixOperations=202,202,201, ThreadsPerUser=500, RunSeconds=300` |

### 场景四：1MB 块大小带宽峰值测试（6 个用例）

> 测试目标：使用 1MB 大块大小，在 100/500 高并发下压测 OBS 的吞吐量（带宽）极限能力，关注 MB/s 或 Gbps 级别的带宽表现。

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 | config 关键配置 |
|:--------:|:-------:|:-------:|:------:|-------|---------------|
| S4-READ-1M-100 | 混合读（纯读） | 1MB | 100 | 100 并发纯 GetObject，测试 1MB 稳态读带宽峰值（MB/s），提取 RecvBytes 计算吞吐 | `ObjectSize=1048576, MixOperations=202, RunSeconds=300` |
| S4-READ-1M-500 | 混合读（纯读） | 1MB | 500 | 500 并发纯 GetObject，极限压测 1MB 读带宽上限，验证是否触及 OBS 或网络带宽瓶颈 | `ObjectSize=1048576, MixOperations=202, ThreadsPerUser=500, RunSeconds=300` |
| S4-WRITE-1M-100 | 混合写（纯写） | 1MB | 100 | 100 并发纯 PutObject，测试 1MB 稳态写带宽峰值，提取 SendBytes 计算吞吐 | `ObjectSize=1048576, MixOperations=201, RunSeconds=300` |
| S4-WRITE-1M-500 | 混合写（纯写） | 1MB | 500 | 500 并发纯 PutObject，极限压测 1MB 写带宽上限 | `ObjectSize=1048576, MixOperations=201, ThreadsPerUser=500, RunSeconds=300` |
| S4-MIX-1M-100 | 混合读写 2:1 | 1MB | 100 | 100 并发混合读写 2:1，测试 1MB 混合总带宽峰值，验证读带宽≈2×写带宽 | `ObjectSize=1048576, MixOperations=202,202,201, RunSeconds=300` |
| S4-MIX-1M-500 | 混合读写 2:1 | 1MB | 500 | 500 并发混合读写 2:1，极限压测 1MB 混合带宽上限 | `ObjectSize=1048576, MixOperations=202,202,201, ThreadsPerUser=500, RunSeconds=300` |

### 场景五：4MB 块大小带宽峰值测试（6 个用例）

> 测试目标：使用 4MB 最大块大小，在 100/500 高并发下压测 OBS 极限带宽能力，验证最大吞吐量是否达到 OBS 规格上限。

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 | config 关键配置 |
|:--------:|:-------:|:-------:|:------:|-------|---------------|
| S5-READ-4M-100 | 混合读（纯读） | 4MB | 100 | 100 并发纯 GetObject，测试 4MB 稳态读带宽峰值，对比 1MB 场景的带宽提升 | `ObjectSize=4194304, MixOperations=202, RunSeconds=300` |
| S5-READ-4M-500 | 混合读（纯读） | 4MB | 500 | 500 并发纯 GetObject，极限压测 4MB 读带宽上限，验证是否达到 OBS 或网络物理瓶颈 | `ObjectSize=4194304, MixOperations=202, ThreadsPerUser=500, RunSeconds=300` |
| S5-WRITE-4M-100 | 混合写（纯写） | 4MB | 100 | 100 并发纯 PutObject，测试 4MB 稳态写带宽峰值 | `ObjectSize=4194304, MixOperations=201, RunSeconds=300` |
| S5-WRITE-4M-500 | 混合写（纯写） | 4MB | 500 | 500 并发纯 PutObject，极限压测 4MB 写带宽上限 | `ObjectSize=4194304, MixOperations=201, ThreadsPerUser=500, RunSeconds=300` |
| S5-MIX-4M-100 | 混合读写 2:1 | 4MB | 100 | 100 并发混合读写 2:1，测试 4MB 混合总带宽峰值，验证读带宽≈2×写带宽 | `ObjectSize=4194304, MixOperations=202,202,201, RunSeconds=300` |
| S5-MIX-4M-500 | 混合读写 2:1 | 4MB | 500 | 500 并发混合读写 2:1，极限压测 4MB 混合带宽上限 | `ObjectSize=4194304, MixOperations=202,202,201, ThreadsPerUser=500, RunSeconds=300` |

---

## 全局预置条件（适用于所有场景）

### 1. 环境准备

| 序号 | 预置条件 | 说明 |
|:---:|---------|------|
| 1 | 测试客户端已安装 Python 2.7.9+ | obscmdbench 依赖 Python 2.6.x/2.7.x 环境 |
| 2 | 已下载 obscmdbench 工具 | `git clone https://github.com/huaweicloud-obs/obscmdbench.git` |
| 3 | 已创建华为云 OBS 桶 | 桶已创建且可正常访问，记录桶名 |
| 4 | 已配置 AK/SK 测试账号 | 在 `users.dat` 中配置测试账号 |
| 5 | 测试客户端与 OBS 服务端网络连通 | 延迟 < 5ms，带宽充足 |
| 6 | 已关闭 DNS 缓存服务 | `service nscd stop`（若使用域名方式） |

### 2. users.dat 配置（所有场景通用）

```
testuser,<your_access_key>,<your_secret_key>
```

> **说明**：单客户端场景只需配置 1 行账号；高并发场景若需多账号，可配置多行。

### 3. 关键参数说明

| 参数 | 含义 | 说明 |
|------|------|------|
| `Testcase` | 测试操作类型 | 201=PutObject, 202=GetObject, 900=MixOperation |
| `Users` | 用户数 | 对应 users.dat 中的行数 |
| `ThreadsPerUser` | 每用户并发线程数 | **总并发 = Users × ThreadsPerUser** |
| `ObjectSize` | 对象大小（字节） | 4096=4KB, 32768=32KB, 1048576=1MB, 4194304=4MB |
| `MixOperations` | 混合操作序列 | 900 模式下配置，逗号分隔的操作码序列 |
| `MixLoopCount` | 混合操作循环次数 | 配合 MixOperations 使用 |
| `IsRandomGet` | 是否随机读取 | true=随机读，false=顺序读 |
| `ObjectLexical` | 对象名是否字典序 | true=顺序写，false=随机名 |
| `LongConnection` | 是否复用连接 | 默认 true（长连接） |
| `RunSeconds` | 运行时长（秒） | 0 或空=按请求数完成退出 |

### 4. 运行命令格式

```bash
./run.py [Testcase] [Users] [config_file]
```

---

## 场景一：单客户端全覆盖测试

### 场景说明

在单个测试客户端上，覆盖顺序读、随机读、顺序写、随机写 4 种读写类型，并发数覆盖 1/10/100，对象大小覆盖 4KB/32KB/1MB/4MB。

> **测试矩阵**：4 种读写类型 × 3 种并发数 × 4 种对象大小 = **48 个测试用例**

---

### 场景一-1：顺序写（PutObject, ObjectLexical=true）

#### 用例编号：S1-SEQ-W-4K-1

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-SEQ-W-4K-1 |
| **测试类型** | 顺序写 |
| **对象大小** | 4KB |
| **并发数** | 1 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建（如配置 `BucketNameFixed` 或通过 `BucketNamePrefix` 自动创建）
3. 桶内无同名对象（或可覆盖）

**config.dat 关键配置**：
```ini
# ===== 测试模型 =====
Testcase = 201
Users = 1
ThreadsPerUser = 1
ObjectSize = 4096
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = obj.seq.4k
PutTimesForOneObj = 1
BucketsPerUser = 1

# ===== 连接配置 =====
LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

# ===== 结果统计 =====
RecordDetails = true
StatisticsInterval = 3
LatencySections = 500,1000,3000,10000
PrintProgress = true
```

**操作步骤**：
1. 编辑 `config.dat`，按上述配置修改参数
2. 确认 `users.dat` 中已配置 1 行测试账号
3. 执行命令：`./run.py 201 1 config.dat`
4. 等待测试执行完成

**预期结果**：
1. 所有请求返回 200 OK，错误率 = 0%
2. 结果文件 `./result/` 下生成 `*_PutObject_1_brief.txt` 和 `*_PutObject_1_detail.csv`
3. 记录 TPS（每秒事务数）、平均延迟、吞吐量指标

---

#### 用例编号：S1-SEQ-W-4K-10

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-SEQ-W-4K-10 |
| **测试类型** | 顺序写 |
| **对象大小** | 4KB |
| **并发数** | 10 |

**预置条件**：同 S1-SEQ-W-4K-1

**config.dat 关键配置**：
```ini
Testcase = 201
Users = 1
ThreadsPerUser = 10
ObjectSize = 4096
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = obj.seq.4k.c10
LongConnection = true
RecordDetails = true
StatisticsInterval = 3
```

**操作步骤**：
1. 修改 `ThreadsPerUser = 10`
2. 执行：`./run.py 201 1 config.dat`

**预期结果**：
1. 错误率 = 0%
2. TPS 约为并发1的 8~12 倍（线性扩展）
3. 平均延迟与并发1基本持平

---

#### 用例编号：S1-SEQ-W-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-SEQ-W-4K-100 |
| **测试类型** | 顺序写 |
| **对象大小** | 4KB |
| **并发数** | 100 |

**预置条件**：同 S1-SEQ-W-4K-1

**config.dat 关键配置**：
```ini
Testcase = 201
Users = 1
ThreadsPerUser = 100
ObjectSize = 4096
ObjectsPerBucketPerThread = 500
ObjectLexical = true
ObjectNamePrefix = obj.seq.4k.c100
LongConnection = true
RecordDetails = true
StatisticsInterval = 3
```

**操作步骤**：
1. 修改 `ThreadsPerUser = 100`，`ObjectsPerBucketPerThread = 500`（降低以控制总时长）
2. 执行：`./run.py 201 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. TPS 应接近或达到单客户端瓶颈
3. 记录最大 TPS 和平均延迟

---

#### 用例编号：S1-SEQ-W-32K-{1/10/100}

**与 S1-SEQ-W-4K 系列配置差异**：

| 参数 | 并发1 | 并发10 | 并发100 |
|------|:-----:|:------:|:-------:|
| `ObjectSize` | 32768 | 32768 | 32768 |
| `ThreadsPerUser` | 1 | 10 | 100 |
| `ObjectNamePrefix` | obj.seq.32k | obj.seq.32k.c10 | obj.seq.32k.c100 |
| `ObjectsPerBucketPerThread` | 1000 | 1000 | 500 |

执行命令分别为：
```bash
./run.py 201 1 config.dat    # 并发1
./run.py 201 1 config.dat    # 并发10
./run.py 201 1 config.dat    # 并发100
```

---

#### 用例编号：S1-SEQ-W-1M-{1/10/100}

**与 S1-SEQ-W-4K 系列配置差异**：

| 参数 | 并发1 | 并发10 | 并发100 |
|------|:-----:|:------:|:-------:|
| `ObjectSize` | 1048576 | 1048576 | 1048576 |
| `ThreadsPerUser` | 1 | 10 | 100 |
| `ObjectNamePrefix` | obj.seq.1m | obj.seq.1m.c10 | obj.seq.1m.c100 |
| `ObjectsPerBucketPerThread` | 200 | 100 | 50 |

---

#### 用例编号：S1-SEQ-W-4M-{1/10/100}

**与 S1-SEQ-W-4K 系列配置差异**：

| 参数 | 并发1 | 并发10 | 并发100 |
|------|:-----:|:------:|:-------:|
| `ObjectSize` | 4194304 | 4194304 | 4194304 |
| `ThreadsPerUser` | 1 | 10 | 100 |
| `ObjectNamePrefix` | obj.seq.4m | obj.seq.4m.c10 | obj.seq.4m.c100 |
| `ObjectsPerBucketPerThread` | 100 | 50 | 20 |

---

### 场景一-2：随机写（PutObject, ObjectLexical=false）

#### 用例编号：S1-RAND-W-4K-1

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-RAND-W-4K-1 |
| **测试类型** | 随机写 |
| **对象大小** | 4KB |
| **并发数** | 1 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**config.dat 关键配置**：
```ini
Testcase = 201
Users = 1
ThreadsPerUser = 1
ObjectSize = 4096
ObjectsPerBucketPerThread = 1000
ObjectLexical = false              # ← 关键差异：随机对象名
BucketsPerUser = 1
LongConnection = true
RecordDetails = true
StatisticsInterval = 3
```

**操作步骤**：
1. 编辑 `config.dat`，按上述配置修改参数
2. 执行：`./run.py 201 1 config.dat`

**预期结果**：
1. 错误率 = 0%
2. 对象名为随机生成（长度 15~1024 字节）
3. 记录 TPS、平均延迟

> **随机写其他组合**：修改 `ObjectSize` 和 `ThreadsPerUser` 参数，与顺序写类似。
> 
> | 用例编号 | ObjectSize | ThreadsPerUser |
> |---------|:----------:|:--------------:|
> | S1-RAND-W-4K-10 | 4096 | 10 |
> | S1-RAND-W-4K-100 | 4096 | 100 |
> | S1-RAND-W-32K-1 | 32768 | 1 |
> | S1-RAND-W-32K-10 | 32768 | 10 |
> | S1-RAND-W-32K-100 | 32768 | 100 |
> | S1-RAND-W-1M-1 | 1048576 | 1 |
> | S1-RAND-W-1M-10 | 1048576 | 10 |
> | S1-RAND-W-1M-100 | 1048576 | 100 |
> | S1-RAND-W-4M-1 | 4194304 | 1 |
> | S1-RAND-W-4M-10 | 4194304 | 10 |
> | S1-RAND-W-4M-100 | 4194304 | 100 |

---

### 场景一-3：顺序读（GetObject, IsRandomGet=false）

#### 用例编号：S1-SEQ-R-4K-1

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-SEQ-R-4K-1 |
| **测试类型** | 顺序读 |
| **对象大小** | 4KB |
| **并发数** | 1 |

**预置条件**：
1. 全局预置条件已满足
2. **前置步骤已完成**：已通过 PutObject（ObjectLexical=true）上传对应大小的对象到目标桶
3. 读取的对象名规则与上传时一致（`ObjectNamePrefix`、`ObjectsPerBucketPerThread` 等参数匹配）

**config.dat 关键配置**：
```ini
Testcase = 202                      # ← GetObject
Users = 1
ThreadsPerUser = 1
IsRandomGet = false                 # ← 关键：顺序读
Range =                             # 空=全量读取
ObjectLexical = true
ObjectNamePrefix = obj.seq.4k       # ← 需与上传时一致
ObjectsPerBucketPerThread = 1000    # ← 需与上传时一致
BucketsPerUser = 1
LongConnection = true
RecordDetails = true
StatisticsInterval = 3
```

**操作步骤**：
1. 确认目标桶中已有 4KB 对象（由前置 PutObject 作业上传）
2. 编辑 `config.dat`，按上述配置修改参数
3. 执行：`./run.py 202 1 config.dat`

**预期结果**：
1. 所有请求返回 200 OK，错误率 = 0%
2. 顺序遍历桶内所有对象进行读取
3. 记录 TPS、平均延迟、下载吞吐量（RecvBytes/s）

> **顺序读其他组合**：修改 `ObjectSize` 和 `ThreadsPerUser` 参数，需确保对应大小的对象已预先上传。
>
> | 用例编号 | ObjectSize | ThreadsPerUser | 前置上传 ObjectNamePrefix |
> |---------|:----------:|:--------------:|--------------------------|
> | S1-SEQ-R-4K-10 | 4096 | 10 | obj.seq.4k |
> | S1-SEQ-R-4K-100 | 4096 | 100 | obj.seq.4k |
> | S1-SEQ-R-32K-1 | 32768 | 1 | obj.seq.32k |
> | S1-SEQ-R-32K-10 | 32768 | 10 | obj.seq.32k |
> | S1-SEQ-R-32K-100 | 32768 | 100 | obj.seq.32k |
> | S1-SEQ-R-1M-1 | 1048576 | 1 | obj.seq.1m |
> | S1-SEQ-R-1M-10 | 1048576 | 10 | obj.seq.1m |
> | S1-SEQ-R-1M-100 | 1048576 | 100 | obj.seq.1m |
> | S1-SEQ-R-4M-1 | 4194304 | 1 | obj.seq.4m |
> | S1-SEQ-R-4M-10 | 4194304 | 10 | obj.seq.4m |
> | S1-SEQ-R-4M-100 | 4194304 | 100 | obj.seq.4m |

---

### 场景一-4：随机读（GetObject, IsRandomGet=true）

#### 用例编号：S1-RAND-R-4K-1

| 项目 | 内容 |
|------|------|
| **用例编号** | S1-RAND-R-4K-1 |
| **测试类型** | 随机读 |
| **对象大小** | 4KB |
| **并发数** | 1 |

**预置条件**：
1. 全局预置条件已满足
2. **前置步骤已完成**：已通过 PutObject 上传足够数量的对象到目标桶
3. 对象名需为字典序（ObjectLexical=true 时上传的对象）

**config.dat 关键配置**：
```ini
Testcase = 202                      # ← GetObject
Users = 1
ThreadsPerUser = 1
IsRandomGet = true                  # ← 关键：随机读
Range =
ObjectLexical = true
ObjectNamePrefix = obj.seq.4k       # ← 需与上传时一致
ObjectsPerBucketPerThread = 1000    # ← 需与上传时一致
BucketsPerUser = 1
LongConnection = true
RecordDetails = true
StatisticsInterval = 3
```

**操作步骤**：
1. 确认目标桶中已有足够数量的 4KB 对象
2. 编辑 `config.dat`，按上述配置修改参数
3. 执行：`./run.py 202 1 config.dat`

**预期结果**：
1. 错误率 = 0%
2. 随机选取桶内对象进行读取，不按顺序遍历
3. 记录 TPS、平均延迟、下载吞吐量

> **随机读其他组合**：
>
> | 用例编号 | ObjectSize | ThreadsPerUser |
> |---------|:----------:|:--------------:|
> | S1-RAND-R-4K-10 | 4096 | 10 |
> | S1-RAND-R-4K-100 | 4096 | 100 |
> | S1-RAND-R-32K-1 | 32768 | 1 |
> | S1-RAND-R-32K-10 | 32768 | 10 |
> | S1-RAND-R-32K-100 | 32768 | 100 |
> | S1-RAND-R-1M-1 | 1048576 | 1 |
> | S1-RAND-R-1M-10 | 1048576 | 10 |
> | S1-RAND-R-1M-100 | 1048576 | 100 |
> | S1-RAND-R-4M-1 | 4194304 | 1 |
> | S1-RAND-R-4M-10 | 4194304 | 10 |
> | S1-RAND-R-4M-100 | 4194304 | 100 |

---

### 场景一：完整测试矩阵一览

| 读写类型 | 对象大小 | 并发1 | 并发10 | 并发100 |
|:-------:|:-------:|:-----:|:------:|:-------:|
| 顺序写 | 4KB | S1-SEQ-W-4K-1 | S1-SEQ-W-4K-10 | S1-SEQ-W-4K-100 |
| 顺序写 | 32KB | S1-SEQ-W-32K-1 | S1-SEQ-W-32K-10 | S1-SEQ-W-32K-100 |
| 顺序写 | 1MB | S1-SEQ-W-1M-1 | S1-SEQ-W-1M-10 | S1-SEQ-W-1M-100 |
| 顺序写 | 4MB | S1-SEQ-W-4M-1 | S1-SEQ-W-4M-10 | S1-SEQ-W-4M-100 |
| 随机写 | 4KB | S1-RAND-W-4K-1 | S1-RAND-W-4K-10 | S1-RAND-W-4K-100 |
| 随机写 | 32KB | S1-RAND-W-32K-1 | S1-RAND-W-32K-10 | S1-RAND-W-32K-100 |
| 随机写 | 1MB | S1-RAND-W-1M-1 | S1-RAND-W-1M-10 | S1-RAND-W-1M-100 |
| 随机写 | 4MB | S1-RAND-W-4M-1 | S1-RAND-W-4M-10 | S1-RAND-W-4M-100 |
| 顺序读 | 4KB | S1-SEQ-R-4K-1 | S1-SEQ-R-4K-10 | S1-SEQ-R-4K-100 |
| 顺序读 | 32KB | S1-SEQ-R-32K-1 | S1-SEQ-R-32K-10 | S1-SEQ-R-32K-100 |
| 顺序读 | 1MB | S1-SEQ-R-1M-1 | S1-SEQ-R-1M-10 | S1-SEQ-R-1M-100 |
| 顺序读 | 4MB | S1-SEQ-R-4M-1 | S1-SEQ-R-4M-10 | S1-SEQ-R-4M-100 |
| 随机读 | 4KB | S1-RAND-R-4K-1 | S1-RAND-R-4K-10 | S1-RAND-R-4K-100 |
| 随机读 | 32KB | S1-RAND-R-32K-1 | S1-RAND-R-32K-10 | S1-RAND-R-32K-100 |
| 随机读 | 1MB | S1-RAND-R-1M-1 | S1-RAND-R-1M-10 | S1-RAND-R-1M-100 |
| 随机读 | 4MB | S1-RAND-R-4M-1 | S1-RAND-R-4M-10 | S1-RAND-R-4M-100 |

---

## 场景二：4KB 块大小 IOPS 峰值测试

### 场景说明

使用 4KB 对象大小，在 100 和 500 并发下测试 IOPS 峰值能力。覆盖混合读（纯读）、混合写（纯写）、混合读写（读写比 2:1）三种模式。

> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**

### 前置数据准备（场景二通用）

在执行读操作前，需先上传足够数量的 4KB 对象：

```ini
# ===== 前置数据准备 config =====
Testcase = 201
Users = 1
ThreadsPerUser = 500
ObjectSize = 4096
ObjectsPerBucketPerThread = 2000
ObjectLexical = true
ObjectNamePrefix = perf.4k
BucketsPerUser = 1
LongConnection = true
```

执行：`./run.py 201 1 config.dat`

---

### 场景二-1：混合读（纯读 IOPS 峰值）

#### 用例编号：S2-READ-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-READ-4K-100 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 4KB |
| **并发数** | 100 |
| **测试目标** | 测试 4KB 纯读 IOPS 峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶中已上传至少 200,000 个 4KB 对象（`ObjectsPerBucketPerThread × ThreadsPerUser` 足够大）
3. 对象名为字典序（ObjectLexical=true）

**config.dat 关键配置**：
```ini
# ===== 混合读 - 4KB - 100并发 =====
Testcase = 900                      # ← MixOperation 模式
Users = 1
ThreadsPerUser = 100
ObjectSize = 4096
ObjectsPerBucketPerThread = 2000
ObjectLexical = true
ObjectNamePrefix = perf.4k
BucketsPerUser = 1

# ===== MixOperation 配置：纯读 =====
MixOperations = 202                 # ← 仅 GetObject
MixLoopCount = 50                   # 循环50次确保充分采样

# ===== 连接配置 =====
LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

# ===== 性能统计 =====
RecordDetails = true
StatisticsInterval = 3
LatencySections = 10,50,100,200,500,1000
PrintProgress = true
RunSeconds = 300                    # 运行5分钟，取稳态数据
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

**操作步骤**：
1. 确认前置数据已准备完毕
2. 编辑 `config.dat`，按上述配置修改
3. 执行：`./run.py 900 1 config.dat`
4. 测试运行 300 秒后自动结束
5. 从 `*_realtime.txt` 中取稳态区间的 TPS 均值作为 IOPS 峰值
6. 从 `*_brief.txt` 中读取 P50/P90/P99 延迟

**预期结果**：
1. 错误率 = 0%
2. IOPS（TPS）达到或接近 OBS 4KB 读性能规格上限
3. P99 延迟 < 100ms（具体以 OBS SLA 为准）
4. `*_realtime.txt` 中 TPS 曲线在稳态区间波动 < 10%

---

#### 用例编号：S2-READ-4K-500

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-READ-4K-500 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 4KB |
| **并发数** | 500 |

**预置条件**：同 S2-READ-4K-100，但前置数据量需更大

**config.dat 关键配置**（与 S2-READ-4K-100 差异）：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 500                # ← 并发500
ObjectSize = 4096
ObjectsPerBucketPerThread = 2000
ObjectLexical = true
ObjectNamePrefix = perf.4k

MixOperations = 202                 # ← 纯读
MixLoopCount = 20

LongConnection = true
RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

**操作步骤**：
1. 修改 `ThreadsPerUser = 500`
2. 执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. IOPS 应显著高于 100 并发（接近或达到 OBS 规格 IOPS 上限）
3. P99 延迟可能略高于 100 并发场景
4. 如客户端 CPU/带宽成为瓶颈，建议使用多客户端分布式模式

---

### 场景二-2：混合写（纯写 IOPS 峰值）

#### 用例编号：S2-WRITE-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-WRITE-4K-100 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 4KB |
| **并发数** | 100 |
| **测试目标** | 测试 4KB 纯写 IOPS 峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**config.dat 关键配置**：
```ini
# ===== 混合写 - 4KB - 100并发 =====
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 4096
ObjectsPerBucketPerThread = 2000
ObjectLexical = true
ObjectNamePrefix = perf.4k.write
BucketsPerUser = 1

# ===== MixOperation 配置：纯写 =====
MixOperations = 201                 # ← 仅 PutObject
MixLoopCount = 50

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 编辑 `config.dat`，按上述配置修改
2. 执行：`./run.py 900 1 config.dat`
3. 从 `*_realtime.txt` 取稳态 TPS 作为写 IOPS 峰值

**预期结果**：
1. 错误率 = 0%
2. 写 IOPS 达到或接近 OBS 4KB 写性能规格上限
3. P99 延迟 < 200ms

---

#### 用例编号：S2-WRITE-4K-500

**config.dat 关键配置**（与 S2-WRITE-4K-100 差异）：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 500                # ← 并发500
ObjectSize = 4096
ObjectsPerBucketPerThread = 1000

MixOperations = 201                 # ← 纯写
MixLoopCount = 20

LongConnection = true
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

**操作步骤**：修改 `ThreadsPerUser = 500`，执行 `./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. 写 IOPS 显著高于 100 并发
3. 如达到 OBS 规格上限，TPS 不再随并发线性增长

---

### 场景二-3：混合读写 2:1（IOPS 峰值）

#### 用例编号：S2-MIX-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-MIX-4K-100 |
| **测试类型** | 混合读写（读:写 = 2:1） |
| **对象大小** | 4KB |
| **并发数** | 100 |
| **测试目标** | 测试 4KB 读写混合 IOPS 峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建
3. 前置数据已上传（供 GetObject 读取）

**config.dat 关键配置**：
```ini
# ===== 混合读写 2:1 - 4KB - 100并发 =====
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 4096
ObjectsPerBucketPerThread = 2000
ObjectLexical = true
ObjectNamePrefix = perf.4k.mix
BucketsPerUser = 1

# ===== MixOperation 配置：读:写 = 2:1 =====
MixOperations = 202,202,201         # ← 2个Get + 1个Put = 读:写 2:1
MixLoopCount = 100                  # 增大循环次数确保充分采样

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置数据已上传（桶内有可读对象）
2. 编辑 `config.dat`，按上述配置修改
3. 执行：`./run.py 900 1 config.dat`
4. 从 `*_realtime.txt` 取稳态总 TPS 作为混合 IOPS 峰值
5. 分别记录读 TPS 和写 TPS

**预期结果**：
1. 错误率 = 0%
2. 总 IOPS = 读 TPS + 写 TPS，其中读 TPS ≈ 2 × 写 TPS
3. 混合 IOPS 应介于纯读和纯写之间
4. P99 延迟 < 150ms

---

#### 用例编号：S2-MIX-4K-500

**config.dat 关键配置**（与 S2-MIX-4K-100 差异）：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 500                # ← 并发500
ObjectSize = 4096
ObjectsPerBucketPerThread = 1000

MixOperations = 202,202,201         # ← 读:写 2:1
MixLoopCount = 50

LongConnection = true
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

**操作步骤**：修改 `ThreadsPerUser = 500`，执行 `./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. 混合总 IOPS 达到或接近 OBS 规格 4KB 混合读写上限

---

### 场景二：测试矩阵一览

| 读写模式 | 并发100 | 并发500 |
|:-------:|:-------:|:-------:|
| 混合读（纯读） | S2-READ-4K-100 | S2-READ-4K-500 |
| 混合写（纯写） | S2-WRITE-4K-100 | S2-WRITE-4K-500 |
| 混合读写 2:1 | S2-MIX-4K-100 | S2-MIX-4K-500 |

---

## 场景三：32KB 块大小 IOPS 峰值测试

### 场景说明

使用 32KB 对象大小，在 100 和 500 并发下测试 IOPS 峰值能力。测试模式与场景二相同。

> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**

### 前置数据准备

```ini
Testcase = 201
Users = 1
ThreadsPerUser = 500
ObjectSize = 32768                  # ← 32KB
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = perf.32k
BucketsPerUser = 1
LongConnection = true
```

执行：`./run.py 201 1 config.dat`

---

### 场景三-1：混合读（纯读 IOPS 峰值）

#### 用例编号：S3-READ-32K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S3-READ-32K-100 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 32KB |
| **并发数** | 100 |
| **测试目标** | 测试 32KB 纯读 IOPS 峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 32KB 对象

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 32768                  # ← 32KB
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = perf.32k
BucketsPerUser = 1

MixOperations = 202                 # ← 纯读
MixLoopCount = 50

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 32KB 数据已上传
2. 编辑 `config.dat`，按上述配置修改
3. 执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 = 0%
2. 32KB 读 IOPS 应低于 4KB 读 IOPS（对象增大导致单请求耗时增加）
3. 32KB 读吞吐量（MB/s）应高于 4KB 读吞吐量
4. P99 延迟 < 100ms

---

#### 用例编号：S3-READ-32K-500

**config.dat 关键配置**（与 S3-READ-32K-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 500
MixLoopCount = 20
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. 500 并发 IOPS 高于 100 并发，达到或接近 OBS 32KB 读规格上限

---

### 场景三-2：混合写（纯写 IOPS 峰值）

#### 用例编号：S3-WRITE-32K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S3-WRITE-32K-100 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 32KB |
| **并发数** | 100 |

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 32768                  # ← 32KB
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = perf.32k.write
BucketsPerUser = 1

MixOperations = 201                 # ← 纯写
MixLoopCount = 50

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 编辑 `config.dat`
2. 执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 = 0%
2. 32KB 写 IOPS 达到或接近 OBS 32KB 写规格上限
3. P99 延迟 < 200ms

---

#### 用例编号：S3-WRITE-32K-500

**config.dat 关键配置**（与 S3-WRITE-32K-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 500
MixLoopCount = 20
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景三-3：混合读写 2:1（IOPS 峰值）

#### 用例编号：S3-MIX-32K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S3-MIX-32K-100 |
| **测试类型** | 混合读写（读:写 = 2:1） |
| **对象大小** | 32KB |
| **并发数** | 100 |
| **测试目标** | 测试 32KB 读写混合 IOPS 峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 32KB 对象

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 32768                  # ← 32KB
ObjectsPerBucketPerThread = 1000
ObjectLexical = true
ObjectNamePrefix = perf.32k.mix
BucketsPerUser = 1

# ===== 读:写 = 2:1 =====
MixOperations = 202,202,201         # ← 2个Get + 1个Put
MixLoopCount = 100

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 32KB 数据已上传
2. 编辑 `config.dat`
3. 执行：`./run.py 900 1 config.dat`
4. 从 `*_realtime.txt` 取稳态总 TPS

**预期结果**：
1. 错误率 = 0%
2. 总 IOPS = 读 TPS + 写 TPS，读 TPS ≈ 2 × 写 TPS
3. 混合 IOPS 介于纯读和纯写之间

---

#### 用例编号：S3-MIX-32K-500

**config.dat 关键配置**（与 S3-MIX-32K-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 500
MixOperations = 202,202,201         # ← 读:写 2:1
MixLoopCount = 50
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景三：测试矩阵一览

| 读写模式 | 并发100 | 并发500 |
|:-------:|:-------:|:-------:|
| 混合读（纯读） | S3-READ-32K-100 | S3-READ-32K-500 |
| 混合写（纯写） | S3-WRITE-32K-100 | S3-WRITE-32K-500 |
| 混合读写 2:1 | S3-MIX-32K-100 | S3-MIX-32K-500 |

---

## 场景四：1MB 块大小带宽峰值测试

### 场景说明

使用 1MB 对象大小，在 100 和 500 并发下测试带宽（吞吐量）峰值能力。覆盖混合读、混合写、混合读写（2:1）三种模式。

> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**

### 前置数据准备

```ini
Testcase = 201
Users = 1
ThreadsPerUser = 500
ObjectSize = 1048576                # ← 1MB
ObjectsPerBucketPerThread = 200
ObjectLexical = true
ObjectNamePrefix = perf.1m
BucketsPerUser = 1
LongConnection = true
```

执行：`./run.py 201 1 config.dat`

---

### 场景四-1：混合读（纯读带宽峰值）

#### 用例编号：S4-READ-1M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S4-READ-1M-100 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 1MB |
| **并发数** | 100 |
| **测试目标** | 测试 1MB 纯读带宽峰值（MB/s 或 Gbps） |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 1MB 对象（至少 100,000 个）

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 1048576                # ← 1MB
ObjectsPerBucketPerThread = 500
ObjectLexical = true
ObjectNamePrefix = perf.1m
BucketsPerUser = 1

MixOperations = 202                 # ← 纯读
MixLoopCount = 30

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 1MB 数据已上传
2. 编辑 `config.dat`
3. 执行：`./run.py 900 1 config.dat`
4. 从 `*_realtime.txt` 提取 RecvBytes 列，计算带宽峰值
5. 带宽 = RecvBytes / StatisticsInterval（转换为 MB/s 或 Gbps）

**预期结果**：
1. 错误率 = 0%
2. 读带宽达到或接近 OBS 1MB 读吞吐量规格上限
3. 稳态区间带宽波动 < 10%
4. P99 延迟 < 500ms

---

#### 用例编号：S4-READ-1M-500

**config.dat 关键配置**（与 S4-READ-1M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 200
MixLoopCount = 10
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. 500 并发读带宽显著高于 100 并发，达到或接近 OBS 带宽规格上限
3. 如客户端网络带宽成为瓶颈（如 10Gbps 网卡），需部署多客户端分布式测试

---

### 场景四-2：混合写（纯写带宽峰值）

#### 用例编号：S4-WRITE-1M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S4-WRITE-1M-100 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 1MB |
| **并发数** | 100 |
| **测试目标** | 测试 1MB 纯写带宽峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 1048576                # ← 1MB
ObjectsPerBucketPerThread = 500
ObjectLexical = true
ObjectNamePrefix = perf.1m.write
BucketsPerUser = 1

MixOperations = 201                 # ← 纯写
MixLoopCount = 30

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 编辑 `config.dat`
2. 执行：`./run.py 900 1 config.dat`
3. 从 `*_realtime.txt` 提取 SendBytes 列，计算写带宽

**预期结果**：
1. 错误率 = 0%
2. 写带宽达到或接近 OBS 1MB 写吞吐量规格上限
3. P99 延迟 < 800ms

---

#### 用例编号：S4-WRITE-1M-500

**config.dat 关键配置**（与 S4-WRITE-1M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 200
MixLoopCount = 10
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景四-3：混合读写 2:1（带宽峰值）

#### 用例编号：S4-MIX-1M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S4-MIX-1M-100 |
| **测试类型** | 混合读写（读:写 = 2:1） |
| **对象大小** | 1MB |
| **并发数** | 100 |
| **测试目标** | 测试 1MB 读写混合带宽峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 1MB 对象

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 1048576                # ← 1MB
ObjectsPerBucketPerThread = 500
ObjectLexical = true
ObjectNamePrefix = perf.1m.mix
BucketsPerUser = 1

# ===== 读:写 = 2:1 =====
MixOperations = 202,202,201         # ← 2个Get + 1个Put
MixLoopCount = 60

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 1MB 数据已上传
2. 编辑 `config.dat`
3. 执行：`./run.py 900 1 config.dat`
4. 分别从 SendBytes 和 RecvBytes 计算写带宽和读带宽
5. 总带宽 = 读带宽 + 写带宽

**预期结果**：
1. 错误率 = 0%
2. 读带宽 ≈ 2 × 写带宽（符合 2:1 配比）
3. 混合总带宽介于纯读和纯写之间

---

#### 用例编号：S4-MIX-1M-500

**config.dat 关键配置**（与 S4-MIX-1M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 200
MixOperations = 202,202,201
MixLoopCount = 30
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景四：测试矩阵一览

| 读写模式 | 并发100 | 并发500 |
|:-------:|:-------:|:-------:|
| 混合读（纯读） | S4-READ-1M-100 | S4-READ-1M-500 |
| 混合写（纯写） | S4-WRITE-1M-100 | S4-WRITE-1M-500 |
| 混合读写 2:1 | S4-MIX-1M-100 | S4-MIX-1M-500 |

---

## 场景五：4MB 块大小带宽峰值测试

### 场景说明

使用 4MB 对象大小，在 100 和 500 并发下测试带宽峰值能力。覆盖混合读、混合写、混合读写（2:1）三种模式。

> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**

### 前置数据准备

```ini
Testcase = 201
Users = 1
ThreadsPerUser = 500
ObjectSize = 4194304                # ← 4MB
ObjectsPerBucketPerThread = 100
ObjectLexical = true
ObjectNamePrefix = perf.4m
BucketsPerUser = 1
LongConnection = true
```

执行：`./run.py 201 1 config.dat`

> **注意**：4MB 对象上传耗时较长，前置数据准备可能需要较长时间。建议提前执行。

---

### 场景五-1：混合读（纯读带宽峰值）

#### 用例编号：S5-READ-4M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S5-READ-4M-100 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 4MB |
| **并发数** | 100 |
| **测试目标** | 测试 4MB 纯读带宽峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 4MB 对象（至少 50,000 个）

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 4194304                # ← 4MB
ObjectsPerBucketPerThread = 200
ObjectLexical = true
ObjectNamePrefix = perf.4m
BucketsPerUser = 1

MixOperations = 202                 # ← 纯读
MixLoopCount = 20

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 4MB 数据已上传
2. 编辑 `config.dat`
3. 执行：`./run.py 900 1 config.dat`
4. 从 `*_realtime.txt` 提取 RecvBytes 列，计算带宽峰值
5. 带宽（MB/s）= RecvBytes / StatisticsInterval / 1048576

**预期结果**：
1. 错误率 = 0%
2. 4MB 读带宽达到或接近 OBS 读吞吐量规格上限
3. 相比 1MB 场景，4MB 的单请求效率更高，带宽可能进一步提升
4. 稳态区间带宽波动 < 10%
5. P99 延迟 < 1000ms

---

#### 用例编号：S5-READ-4M-500

**config.dat 关键配置**（与 S5-READ-4M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 100
MixLoopCount = 10
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

**预期结果**：
1. 错误率 < 0.1%
2. 500 并发读带宽高于 100 并发
3. 如达到 OBS 带宽规格上限或客户端网络瓶颈，TPS 不再线性增长

---

### 场景五-2：混合写（纯写带宽峰值）

#### 用例编号：S5-WRITE-4M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S5-WRITE-4M-100 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 4MB |
| **并发数** | 100 |
| **测试目标** | 测试 4MB 纯写带宽峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 4194304                # ← 4MB
ObjectsPerBucketPerThread = 200
ObjectLexical = true
ObjectNamePrefix = perf.4m.write
BucketsPerUser = 1

MixOperations = 201                 # ← 纯写
MixLoopCount = 20

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 编辑 `config.dat`
2. 执行：`./run.py 900 1 config.dat`
3. 从 `*_realtime.txt` 提取 SendBytes 列，计算写带宽

**预期结果**：
1. 错误率 = 0%
2. 4MB 写带宽达到或接近 OBS 写吞吐量规格上限
3. P99 延迟 < 1500ms

---

#### 用例编号：S5-WRITE-4M-500

**config.dat 关键配置**（与 S5-WRITE-4M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 100
MixLoopCount = 10
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景五-3：混合读写 2:1（带宽峰值）

#### 用例编号：S5-MIX-4M-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S5-MIX-4M-100 |
| **测试类型** | 混合读写（读:写 = 2:1） |
| **对象大小** | 4MB |
| **并发数** | 100 |
| **测试目标** | 测试 4MB 读写混合带宽峰值 |

**预置条件**：
1. 全局预置条件已满足
2. 桶中已上传足够的 4MB 对象

**config.dat 关键配置**：
```ini
Testcase = 900
Users = 1
ThreadsPerUser = 100
ObjectSize = 4194304                # ← 4MB
ObjectsPerBucketPerThread = 200
ObjectLexical = true
ObjectNamePrefix = perf.4m.mix
BucketsPerUser = 1

# ===== 读:写 = 2:1 =====
MixOperations = 202,202,201         # ← 2个Get + 1个Put
MixLoopCount = 40

LongConnection = true
IsHTTPs = true
UseDomainName = true
DomainName = obs.<region>.myhuaweicloud.com

RecordDetails = true
StatisticsInterval = 3
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
PrintProgress = true
```

**操作步骤**：
1. 确认前置 4MB 数据已上传
2. 编辑 `config.dat`
3. 执行：`./run.py 900 1 config.dat`
4. 分别从 SendBytes 和 RecvBytes 计算写带宽和读带宽
5. 总带宽 = 读带宽 + 写带宽

**预期结果**：
1. 错误率 = 0%
2. 读带宽 ≈ 2 × 写带宽（符合 2:1 配比）
3. 混合总带宽介于纯读和纯写之间

---

#### 用例编号：S5-MIX-4M-500

**config.dat 关键配置**（与 S5-MIX-4M-100 差异）：
```ini
ThreadsPerUser = 500                # ← 并发500
ObjectsPerBucketPerThread = 100
MixOperations = 202,202,201
MixLoopCount = 20
RunSeconds = 300
LatencyPercentileMap = true
LatencyPercentileMapSections = 10,50,90,95,99
```

执行：`./run.py 900 1 config.dat`

---

### 场景五：测试矩阵一览

| 读写模式 | 并发100 | 并发500 |
|:-------:|:-------:|:-------:|
| 混合读（纯读） | S5-READ-4M-100 | S5-READ-4M-500 |
| 混合写（纯写） | S5-WRITE-4M-100 | S5-WRITE-4M-500 |
| 混合读写 2:1 | S5-MIX-4M-100 | S5-MIX-4M-500 |

---

## 全场景测试用例汇总

### 总用例数统计

| 场景 | 用例数 | 说明 |
|:----:|:-----:|------|
| 场景一：单客户端全覆盖 | **48** | 4读写类型 × 3并发 × 4大小 |
| 场景二：4KB IOPS 峰值 | **6** | 3读写模式 × 2并发 |
| 场景三：32KB IOPS 峰值 | **6** | 3读写模式 × 2并发 |
| 场景四：1MB 带宽峰值 | **6** | 3读写模式 × 2并发 |
| 场景五：4MB 带宽峰值 | **6** | 3读写模式 × 2并发 |
| **合计** | **72** | |

### 场景执行顺序建议

```
Step 1: 场景一 - 顺序写测试（12个用例）
        └─ 产出：桶内数据（供后续读取使用）

Step 2: 场景一 - 顺序读测试（12个用例）
        └─ 使用 Step 1 上传的数据

Step 3: 场景一 - 随机写测试（12个用例）

Step 4: 场景一 - 随机读测试（12个用例）
        └─ 使用 Step 1 上传的数据

Step 5: 场景二 - 前置数据准备 + 6个测试用例

Step 6: 场景三 - 前置数据准备 + 6个测试用例

Step 7: 场景四 - 前置数据准备 + 6个测试用例

Step 8: 场景五 - 前置数据准备 + 6个测试用例
```

### 结果分析方法

#### IOPS 计算（场景二、三）

```
IOPS = TPS（从 *_brief.txt 中 Total TPS 字段读取）
读 IOPS = GetObject 的 TPS
写 IOPS = PutObject 的 TPS
混合总 IOPS = 读 IOPS + 写 IOPS
```

#### 带宽计算（场景四、五）

```
读带宽（MB/s）= RecvBytes / 统计间隔 / 1048576
写带宽（MB/s）= SendBytes / 统计间隔 / 1048576
总带宽 = 读带宽 + 写带宽
```

从 `*_realtime.txt` 中提取稳态区间（去除前 30 秒预热期）的 RecvBytes / SendBytes 列计算。

#### 延迟分析

```
从 *_brief.txt 中读取：
- AvgLatency：平均延迟
- LatencySections 分布：各延迟区间的请求占比
- LatencyPercentileMap：P10/P50/P90/P95/P99 延迟
```

---

## 附录：config.dat 参数速查表

### 场景一关键参数映射

| 读写类型 | Testcase | 关键参数 |
|:-------:|:--------:|---------|
| 顺序写 | 201 | `ObjectLexical=true` |
| 随机写 | 201 | `ObjectLexical=false` |
| 顺序读 | 202 | `IsRandomGet=false`，需匹配上传时的 `ObjectNamePrefix` |
| 随机读 | 202 | `IsRandomGet=true`，需匹配上传时的 `ObjectNamePrefix` |

### 场景二~五关键参数映射

| 读写模式 | MixOperations | 说明 |
|:-------:|:------------:|------|
| 混合读（纯读） | `202` | 仅 GetObject |
| 混合写（纯写） | `201` | 仅 PutObject |
| 混合读写 2:1 | `202,202,201` | 2 Get + 1 Put |

### 并发配置映射

| 目标并发 | Users | ThreadsPerUser | 说明 |
|:-------:|:-----:|:--------------:|------|
| 1 | 1 | 1 | 单线程 |
| 10 | 1 | 10 | 单用户10线程 |
| 100 | 1 | 100 | 单用户100线程 |
| 500 | 1 | 500 | 单用户500线程 |

### 对象大小映射

| 大小 | ObjectSize 值（字节） |
|:----:|:-----:|
| 4KB | 4096 |
| 32KB | 32768 |
| 1MB | 1048576 |
| 4MB | 4194304 |
