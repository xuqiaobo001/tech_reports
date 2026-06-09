# 基于 obscmdbench 工具的 OBS 性能测试用例

> 工具来源：https://github.com/huaweicloud-obs/obscmdbench
> 编写日期：2026-06-09
> 总用例数：**72 个**（5 大场景）
> **核心说明**：每个用例均包含 **完整 config.dat 全部参数**，TE 可直接复制粘贴到 `config.dat` 文件，替换 `DomainName` 中的 `<region>` 后即可执行。无需自行拆解参数。

---

## 📂 文件导航

| 文件 | 内容 | 用例数 |
|------|------|:------:|
| [README.md](README.md) | 总览表 + 全局预置条件 + 结果分析方法（本文件） | - |
| [scenario_1_detail.md](scenario_1_detail.md) | 场景一：单客户端全覆盖（顺序写/随机写/顺序读/随机读 × 1/10/100并发 × 4KB/32KB/1MB/4MB） | **48** |
| [scenario_2_detail.md](scenario_2_detail.md) | 场景二：4KB 块大小 IOPS 峰值测试（混合读/混合写/混合读写2:1 × 100/500并发） | **6** |
| [scenario_3_detail.md](scenario_3_detail.md) | 场景三：32KB 块大小 IOPS 峰值测试（混合读/混合写/混合读写2:1 × 100/500并发） | **6** |
| [scenario_4_detail.md](scenario_4_detail.md) | 场景四：1MB 块大小带宽峰值测试（混合读/混合写/混合读写2:1 × 100/500并发） | **6** |
| [scenario_5_detail.md](scenario_5_detail.md) | 场景五：4MB 块大小带宽峰值测试（混合读/混合写/混合读写2:1 × 100/500并发） | **6** |
| [generate_obscmdbench_report.py](generate_obscmdbench_report.py) | Python 生成脚本（修改参数后重新运行即可更新报告） | - |

---

## 测试用例总览

> 共 **5 大场景、72 个用例**

### 场景一：单客户端全覆盖测试（48 个用例）

| 用例编号 | 读写类型 | 对象大小 | 并发数 | 测试点 |
|:--------:|:-------:|:-------:|:------:|-------|
| S1-SEQ-W-4K-1 | 顺序写 | 4KB | 1 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-4K-10 | 顺序写 | 4KB | 10 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-4K-100 | 顺序写 | 4KB | 100 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-32K-1 | 顺序写 | 32KB | 1 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-32K-10 | 顺序写 | 32KB | 10 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-32K-100 | 顺序写 | 32KB | 100 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-1M-1 | 顺序写 | 1MB | 1 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-1M-10 | 顺序写 | 1MB | 10 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-1M-100 | 顺序写 | 1MB | 100 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-4M-1 | 顺序写 | 4MB | 1 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-4M-10 | 顺序写 | 4MB | 10 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-SEQ-W-4M-100 | 顺序写 | 4MB | 100 | PutObject 字典序命名顺序写，验证TPS/延迟基线 |
| S1-RAND-W-4K-1 | 随机写 | 4KB | 1 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-4K-10 | 随机写 | 4KB | 10 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-4K-100 | 随机写 | 4KB | 100 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-32K-1 | 随机写 | 32KB | 1 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-32K-10 | 随机写 | 32KB | 10 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-32K-100 | 随机写 | 32KB | 100 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-1M-1 | 随机写 | 1MB | 1 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-1M-10 | 随机写 | 1MB | 10 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-1M-100 | 随机写 | 1MB | 100 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-4M-1 | 随机写 | 4MB | 1 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-4M-10 | 随机写 | 4MB | 10 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-RAND-W-4M-100 | 随机写 | 4MB | 100 | PutObject 随机对象名写入，验证随机写TPS/延迟 |
| S1-SEQ-R-4K-1 | 顺序读 | 4KB | 1 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-4K-10 | 顺序读 | 4KB | 10 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-4K-100 | 顺序读 | 4KB | 100 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-32K-1 | 顺序读 | 32KB | 1 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-32K-10 | 顺序读 | 32KB | 10 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-32K-100 | 顺序读 | 32KB | 100 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-1M-1 | 顺序读 | 1MB | 1 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-1M-10 | 顺序读 | 1MB | 10 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-1M-100 | 顺序读 | 1MB | 100 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-4M-1 | 顺序读 | 4MB | 1 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-4M-10 | 顺序读 | 4MB | 10 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-SEQ-R-4M-100 | 顺序读 | 4MB | 100 | GetObject 顺序遍历读取，验证TPS/吞吐 |
| S1-RAND-R-4K-1 | 随机读 | 4KB | 1 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-4K-10 | 随机读 | 4KB | 10 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-4K-100 | 随机读 | 4KB | 100 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-32K-1 | 随机读 | 32KB | 1 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-32K-10 | 随机读 | 32KB | 10 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-32K-100 | 随机读 | 32KB | 100 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-1M-1 | 随机读 | 1MB | 1 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-1M-10 | 随机读 | 1MB | 10 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-1M-100 | 随机读 | 1MB | 100 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-4M-1 | 随机读 | 4MB | 1 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-4M-10 | 随机读 | 4MB | 10 | GetObject 随机选取读取，验证TPS/吞吐 |
| S1-RAND-R-4M-100 | 随机读 | 4MB | 100 | GetObject 随机选取读取，验证TPS/吞吐 |

👉 **[点击查看场景一完整用例（48个）→](scenario_1_detail.md)**

---

### 场景二：4KB 块大小 IOPS 峰值测试（6 个用例）

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 |
|:--------:|:-------:|:-------:|:------:|-------|
| S2-READ-4K-100 | 混合读（纯读） | 4KB | 100 | 纯GetObject 100并发 |
| S2-READ-4K-500 | 混合读（纯读） | 4KB | 500 | 纯GetObject 500并发 |
| S2-WRITE-4K-100 | 混合写（纯写） | 4KB | 100 | 纯PutObject 100并发 |
| S2-WRITE-4K-500 | 混合写（纯写） | 4KB | 500 | 纯PutObject 500并发 |
| S2-MIX-4K-100 | 混合读写 2:1 | 4KB | 100 | 2Get+1Put 100并发 |
| S2-MIX-4K-500 | 混合读写 2:1 | 4KB | 500 | 2Get+1Put 500并发 |

👉 **[点击查看场景二完整用例（6个）→](scenario_2_detail.md)**

---

### 场景三：32KB 块大小 IOPS 峰值测试（6 个用例）

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 |
|:--------:|:-------:|:-------:|:------:|-------|
| S3-READ-32K-100 | 混合读（纯读） | 32KB | 100 | 纯GetObject 100并发 |
| S3-READ-32K-500 | 混合读（纯读） | 32KB | 500 | 纯GetObject 500并发 |
| S3-WRITE-32K-100 | 混合写（纯写） | 32KB | 100 | 纯PutObject 100并发 |
| S3-WRITE-32K-500 | 混合写（纯写） | 32KB | 500 | 纯PutObject 500并发 |
| S3-MIX-32K-100 | 混合读写 2:1 | 32KB | 100 | 2Get+1Put 100并发 |
| S3-MIX-32K-500 | 混合读写 2:1 | 32KB | 500 | 2Get+1Put 500并发 |

👉 **[点击查看场景三完整用例（6个）→](scenario_3_detail.md)**

---

### 场景四：1MB 块大小带宽峰值测试（6 个用例）

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 |
|:--------:|:-------:|:-------:|:------:|-------|
| S4-READ-1M-100 | 混合读（纯读） | 1MB | 100 | 纯GetObject 100并发 |
| S4-READ-1M-500 | 混合读（纯读） | 1MB | 500 | 纯GetObject 500并发 |
| S4-WRITE-1M-100 | 混合写（纯写） | 1MB | 100 | 纯PutObject 100并发 |
| S4-WRITE-1M-500 | 混合写（纯写） | 1MB | 500 | 纯PutObject 500并发 |
| S4-MIX-1M-100 | 混合读写 2:1 | 1MB | 100 | 2Get+1Put 100并发 |
| S4-MIX-1M-500 | 混合读写 2:1 | 1MB | 500 | 2Get+1Put 500并发 |

👉 **[点击查看场景四完整用例（6个）→](scenario_4_detail.md)**

---

### 场景五：4MB 块大小带宽峰值测试（6 个用例）

| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 |
|:--------:|:-------:|:-------:|:------:|-------|
| S5-READ-4M-100 | 混合读（纯读） | 4MB | 100 | 纯GetObject 100并发 |
| S5-READ-4M-500 | 混合读（纯读） | 4MB | 500 | 纯GetObject 500并发 |
| S5-WRITE-4M-100 | 混合写（纯写） | 4MB | 100 | 纯PutObject 100并发 |
| S5-WRITE-4M-500 | 混合写（纯写） | 4MB | 500 | 纯PutObject 500并发 |
| S5-MIX-4M-100 | 混合读写 2:1 | 4MB | 100 | 2Get+1Put 100并发 |
| S5-MIX-4M-500 | 混合读写 2:1 | 4MB | 500 | 2Get+1Put 500并发 |

👉 **[点击查看场景五完整用例（6个）→](scenario_5_detail.md)**

---

## 全局预置条件（适用于所有场景）

### 1. 环境准备

| 序号 | 预置条件 | 说明 |
|:---:|---------|------|
| 1 | 测试客户端已安装 Python 2.7.9+ | obscmdbench 依赖 Python 环境 |
| 2 | 已下载 obscmdbench 工具 | `git clone https://github.com/huaweicloud-obs/obscmdbench.git` |
| 3 | 已创建华为云 OBS 桶 | 桶已创建且可正常访问 |
| 4 | 已配置 AK/SK 测试账号 | 在 `users.dat` 中配置测试账号 |
| 5 | 测试客户端与 OBS 网络连通 | 延迟 < 5ms，带宽充足 |
| 6 | 已关闭 DNS 缓存服务 | `service nscd stop`（若使用域名方式） |

### 2. users.dat 配置（所有场景通用）

```
testuser,<your_access_key>,<your_secret_key>
```

### 3. 使用方法（3 步执行）

1. **复制配置**：将用例中的完整 config.dat 内容复制到工具目录下的 `config.dat` 文件（覆盖原有内容）
2. **修改域名**：将 `DomainName` 中的 `<region>` 替换为实际区域代码（如 `cn-north-4`）
3. **执行命令**：运行用例中给出的 `./run.py` 命令

### 4. 结果查看

- `./result/*_brief.txt`：汇总结果（TPS、平均延迟、延迟分布）
- `./result/*_detail.csv`：每个请求的详细结果
- `./result/*_realtime.txt`：实时性能统计（TPS、SendBytes、RecvBytes）

---

## 场景执行顺序建议

```
Step 1: 场景一 - 顺序写（12个用例）→ 产出桶内数据供后续读取
Step 2: 场景一 - 顺序读（12个用例）→ 使用 Step 1 上传的数据
Step 3: 场景一 - 随机写（12个用例）
Step 4: 场景一 - 随机读（12个用例）→ 使用 Step 1 上传的数据
Step 5: 场景二 - 前置数据准备 + 6个测试用例
Step 6: 场景三 - 前置数据准备 + 6个测试用例
Step 7: 场景四 - 前置数据准备 + 6个测试用例
Step 8: 场景五 - 前置数据准备 + 6个测试用例
```

---

## 结果分析方法

### IOPS 计算（场景二、三）

```
IOPS = TPS（从 *_brief.txt 中 Total TPS 字段读取）
读 IOPS = GetObject 的 TPS
写 IOPS = PutObject 的 TPS
混合总 IOPS = 读 IOPS + 写 IOPS
```

### 带宽计算（场景四、五）

```
读带宽（MB/s）= RecvBytes / 统计间隔 / 1048576
写带宽（MB/s）= SendBytes / 统计间隔 / 1048576
总带宽 = 读带宽 + 写带宽
```

从 `*_realtime.txt` 中提取稳态区间（去除前 30 秒预热期）的 RecvBytes / SendBytes 列计算。

### 延迟分析

```
从 *_brief.txt 中读取：
- AvgLatency：平均延迟
- LatencySections 分布：各延迟区间的请求占比
- LatencyPercentileMap：P10/P50/P90/P95/P99 延迟
```
