# 5PB 迁移方案：阿里云 ECS 传输集群规格与数量设计

> 设计日期：2026-05-08
> 场景：5PB 数据 14 天完成全量迁移，确定阿里云侧 ECS VM 的数量与规格

---

## 一、吞吐目标倒推

### 1.1 核心计算

```
迁移总量: 5 PB = 5,120 TB = 5,242,880 GB
目标周期: 14 天 = 1,209,600 秒
实际有效传输时间占比: ~80%（排除重试、元数据操作、网络抖动）

所需持续吞吐:
  5,242,880 GB ÷ (1,209,600 × 0.8) ≈ 5.41 GB/s

换算为带宽:
  5.41 GB/s × 8 = 43.3 Gbps

即需要 ~44 Gbps 的有效出口带宽
```

### 1.2 网络路径分析

```
数据流:  CPFS →(Data Flow, 内网免费)→ 阿里云 OSS →(公网/专线, 收费)→ 华为云 OBS →(互通)→ SFS Turbo

关键瓶颈:
  1. 阿里云 ECS → 阿里云 OSS 读取: 内网，不限速，免费
  2. 阿里云 ECS → 华为云 OBS 写入: 公网出口，按带宽或流量计费 ← 瓶颈所在

因此 ECS 的公网出口带宽是决定性因素
```

---

## 二、单节点吞吐能力评估

### 2.1 rclone 单进程性能模型

```
rclone 内存公式:
  Memory = transfers × upload_concurrency × chunk_size

  推荐配置:
    transfers = 32
    upload_concurrency = 8
    chunk_size = 32 MB

    Memory = 32 × 8 × 32 MB = 8 GB

  加上系统开销和其他进程: 需要约 12-16 GB 可用内存

rclone CPU 消耗:
  主要用于 MD5/SHA256 校验和计算 + 加密传输
  32 transfers 约需 4-8 vCPU
```

### 2.2 网络吞吐上限

```
阿里云 ECS 公网带宽模式:

模式 A: 按固定带宽计费
  最大可选: 单实例 100 Gbps（仅特定规格）
  常见上限: 单实例 10 Gbps
  实际有效: 约 70-80%（TCP/IP + TLS 开销）

模式 B: 按使用流量计费（推荐迁移场景）
  无固定带宽上限，按 GB 流出计费
  实际吞吐取决于实例规格网络能力
  流出费用: ¥0.50/GB（中国大陆）
```

### 2.3 阿里云 ECS 网络能力对照

| 实例规格族 | 网络带宽能力 | 内网带宽 | 适用场景 |
|-----------|-------------|---------|---------|
| ecs.g8i.xlarge (4C/16G) | 最高 10 Gbps | 最高 10 Gbps | 轻量传输 |
| ecs.g8i.2xlarge (8C/32G) | 最高 16 Gbps | 最高 16 Gbps | 中等传输 |
| ecs.g8i.4xlarge (16C/64G) | 最高 32 Gbps | 最高 32 Gbps | 大规模传输 |
| ecs.g8i.8xlarge (32C/128G) | 最高 50 Gbps | 最高 50 Gbps | 高性能传输 |
| ecs.ebmg8i.24xlarge (96C/384G) | 最高 100 Gbps | 最高 100 Gbps | 极致性能 |

> 注: 公网带宽需要在购买时指定或通过弹性公网 IP (EIP) 绑定

---

## 三、方案设计：三种配置选项

### 方案 A：均衡方案（推荐）

```
ECS 规格: ecs.g8i.4xlarge (16 vCPU / 64 GB RAM)
每台绑定 EIP: 按流量计费，不限带宽
每台运行: 1 个 rclone 实例，32 transfers
每台预期吞吐: ~3 GB/s（内网读 OSS + 公网写华为 OBS）

节点数量计算:
  总需求: 5.41 GB/s
  单节点: ~3 GB/s
  节点数: 5.41 ÷ 3 ≈ 1.8 → 取 3 节点（含冗余）

  考虑冗余和波动: 取 4 节点
  实际总吞吐: 4 × 3 = 12 GB/s（富余，可缩短迁移周期）

迁移时间:
  5,242,880 GB ÷ (4 × 3 GB/s × 86400 × 0.8) ≈ 6.3 天 ✓

费用估算（14 天周期）:
  ECS 计算: 4 × 16C/64G × ¥7.68/h × 24h × 14d ≈ ¥10.3 万
  EIP 流量: 5PB × ¥0.50/GB = ¥262 万
  合计: ~¥272 万
```

### 方案 B：高性能方案

```
ECS 规格: ecs.g8i.8xlarge (32 vCPU / 128 GB RAM)
每台绑定 EIP: 按流量计费，不限带宽
每台运行: 2 个 rclone 实例（端口分离），每个 32 transfers
每台预期吞吐: ~5 GB/s

节点数量: 2 节点
  实际总吞吐: 2 × 5 = 10 GB/s

迁移时间:
  5,242,880 GB ÷ (10 × 86400 × 0.8) ≈ 7.6 天 ✓

费用估算:
  ECS 计算: 2 × 32C/128G × ¥15.36/h × 24h × 14d ≈ ¥10.3 万
  EIP 流量: 5PB × ¥0.50/GB = ¥262 万
  合计: ~¥272 万
```

### 方案 C：低成本方案

```
ECS 规格: ecs.g8i.2xlarge (8 vCPU / 32 GB RAM)
每台绑定 EIP: 按流量计费
每台运行: 1 个 rclone 实例，16 transfers
每台预期吞吐: ~1.5 GB/s

节点数量: 8 节点
  实际总吞吐: 8 × 1.5 = 12 GB/s

迁移时间:
  5,242,880 GB ÷ (12 × 86400 × 0.8) ≈ 6.3 天 ✓

费用估算:
  ECS 计算: 8 × 8C/32G × ¥3.84/h × 24h × 14d ≈ ¥8.2 万
  EIP 流量: 5PB × ¥0.50/GB = ¥262 万
  合计: ~¥270 万
```

---

## 四、推荐方案详细设计（方案 A）

### 4.1 资源清单

```
┌───────────────────────────────────────────────────────────────┐
│                    阿里云侧资源清单                              │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  传输集群（4 台 ECS）                                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ 规格: ecs.g8i.4xlarge (16 vCPU / 64 GB / 10 Gbps)    │    │
│  │ 系统: Ubuntu 22.04                                     │    │
│  │ 系统盘: 100 GB ESSD PL0                                │    │
│  │ 数据盘: 无（不需要本地存储）                              │    │
│  │ EIP: 弹性公网 IP，按流量计费                             │    │
│  │ 数量: 4 台                                              │    │
│  │ 用途: 运行 rclone，从 OSS 读、向华为 OBS 写             │    │
│  │ 部署: 与 OSS 同一区域（内网读 OSS 免费）                  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  管控节点（1 台 ECS）                                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ 规格: ecs.g8i.xlarge (4 vCPU / 16 GB)                  │    │
│  │ 系统: Ubuntu 22.04                                     │    │
│  │ 系统盘: 100 GB ESSD PL0                                │    │
│  │ EIP: 弹性公网 IP，按带宽计费 5 Mbps（管理用）            │    │
│  │ 用途: 调度调度、进度监控、变更快照、跳板机                 │    │
│  │ 部署: 挂载 CPFS NFS（用于数据盘点）                      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  阿里云侧合计: 5 台 ECS                                       │
│                                                               │
│  华为云侧（参考）                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ 1 台 ECS: 用于 obsutil sync（OBS → SFS Turbo）        │    │
│  │ 规格: 8 vCPU / 32 GB                                   │    │
│  │ 与 SFS Turbo 同 VPC                                    │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 4.2 传输节点部署详情

```
节点命名: migration-transfer-01 ~ migration-transfer-04

每节点软件栈:
  - rclone v1.68+ (S3 兼容传输)
  - tmux (会话保活)
  - nload / iftop (网络监控)
  - rclone 配置: 阿里云 OSS (内网 endpoint) + 华为云 OBS (公网 endpoint)

每节点工作负载:
  - 1 个 rclone 进程
  - 32 并发传输 (transfers=32)
  - 64 并发校验 (checkers=64)
  - 8 并发分片上传 (upload_concurrency=8)
  - 32 MB 分片 (chunk_size=32M)
  - 预期内存: ~10 GB
  - 预期 CPU: 8-12 核满载

数据分片策略:
  Node-01: datasets/training/part_00 ~ part_09
  Node-02: datasets/training/part_10 ~ part_19
  Node-03: datasets/evaluation + datasets/preprocessing
  Node-04: models + checkpoints + configs

  按前缀分配，避免多节点竞争同一目录
```

### 4.3 部署脚本

```bash
#!/bin/bash
# setup_transfer_node.sh
# 在每台传输节点上执行

set -e

# ============ 安装 rclone ============
curl https://rclone.org/install.sh | sudo bash

# ============ 配置 rclone ============
mkdir -p ~/.config/rclone
cat > ~/.config/rclone/rclone.conf << 'EOF'
[aliyun-oss]
type = s3
provider = Alibaba
env_auth = false
access_key_id = ${ALI_AK}
secret_access_key = ${ALI_SK}
endpoint = oss-cn-hangzhou-internal.aliyuncs.com

[huawei-obs]
type = s3
provider = HuaweiOBS
env_auth = false
access_key_id = ${HW_AK}
secret_access_key = ${HW_SK}
region = cn-north-4
endpoint = obs.cn-north-4.myhuaweicloud.com
EOF

# 替换实际密钥
sed -i "s/\${ALI_AK}/${ALI_AK}/" ~/.config/rclone/rclone.conf
sed -i "s/\${ALI_SK}/${ALI_SK}/" ~/.config/rclone/rclone.conf
sed -i "s/\${HW_AK}/${HW_AK}/" ~/.config/rclone/rclone.conf
sed -i "s/\${HW_SK}/${HW_SK}/" ~/.config/rclone/rclone.conf
chmod 600 ~/.config/rclone/rclone.conf

# ============ 安装监控工具 ============
apt-get update && apt-get install -y tmux nload iftop htop iotop

# ============ 创建工作目录 ============
mkdir -p /var/log/migration
mkdir -p /opt/migration

# ============ 验证连通性 ============
echo "Testing Alibaba OSS connectivity..."
rclone lsd aliyun-oss:migration-bucket/

echo "Testing Huawei OBS connectivity..."
rclone lsd huawei-obs:ai-datasets-5pb/

echo "Transfer node setup complete."
```

### 4.4 启动传输脚本

```bash
#!/bin/bash
# start_transfer.sh
# 根据节点编号启动对应分片的传输任务

NODE_ID=$1  # 01, 02, 03, 04

# 分片配置
case $NODE_ID in
  01) PREFIX="datasets/training/part_00_09/" ;;
  02) PREFIX="datasets/training/part_10_19/" ;;
  03) PREFIX="datasets/evaluation/" ;;
  04) PREFIX="models/" ;;
  *) echo "Unknown node: $NODE_ID"; exit 1 ;;
esac

tmux new-session -d -s "migration-r0" \
  "rclone sync aliyun-oss:migration-bucket/${PREFIX} \
     huawei-obs:ai-datasets-5pb/${PREFIX} \
     --progress \
     --transfers 32 \
     --checkers 64 \
     --s3-chunk-size 32M \
     --s3-upload-concurrency 8 \
     --fast-list \
     --retries 10 \
     --retries-sleep 5s \
     --timeout 10m \
     --log-file /var/log/migration/rclone-r0-node${NODE_ID}.log \
     --log-level INFO \
     --stats 10m \
     --stats-one-line \
     --use-json-log \
     2>&1 | tee /var/log/migration/rclone-r0-node${NODE_ID}-stdout.log"

echo "Migration started for node $NODE_ID, prefix: $PREFIX"
echo "Monitor: tmux attach -t migration-r0"
echo "Logs: tail -f /var/log/migration/rclone-r0-node${NODE_ID}.log"
```

---

## 五、费用明细

### 5.1 阿里云侧费用

| 费用项 | 规格 | 单价 | 数量/用量 | 小计 |
|--------|------|------|----------|------|
| **传输 ECS** | ecs.g8i.4xlarge (16C/64G) | ¥7.68/时 | 4 台 × 14 天 × 24 时 | ¥103,220 |
| **管控 ECS** | ecs.g8i.xlarge (4C/16G) | ¥1.92/时 | 1 台 × 21 天 × 24 时 | ¥967 |
| **EIP 流量** | 按流量计费 | ¥0.50/GB | 5 PB (5,242,880 GB) | ¥2,621,440 |
| **OSS 存储**（中转桶） | 标准存储 | ¥0.12/GB/月 | 5 PB × 0.5 月 | ¥314,573 |
| **OSS 请求** | PUT/GET | 按量 | 数亿次 | ~¥50,000 |
| **CPFS Data Flow** | 内置功能 | 免费 | — | ¥0 |

**阿里云侧合计: ~¥309 万**

### 5.2 华为云侧费用

| 费用项 | 规格 | 单价 | 数量/用量 | 小计 |
|--------|------|------|----------|------|
| **同步 ECS** | 8C/32G | ~¥3.84/时 | 1 台 × 21 天 × 24 时 | ~¥1,936 |
| **OBS 存储** | 标准 | ¥0.099/GB/月 | 5 PB × 0.5 月 | ~¥259,523 |
| **OBS 流入** | — | 免费 | — | ¥0 |
| **OBS 请求** | PUT/GET | 按量 | 数亿次 | ~¥30,000 |

**华为云侧合计: ~¥29 万**

### 5.3 费用占比分析

```
┌─────────────────────────────────────────────────────────┐
│ 费用结构                                                 │
│                                                         │
│ EIP 流量费  ████████████████████████████████████  85%    │
│ OSS 存储    ████                                 10%    │
│ ECS 计算    ██                                    4%    │
│ 其他         █                                    1%    │
│                                                         │
│ 流量费是绝对大头: 5PB × ¥0.5/GB = ¥262 万               │
│                                                         │
│ 优化方向:                                                │
│ 1. 使用 OSS 流量包预付费（可优惠 30-50%）                 │
│ 2. 使用 OSS 到华为云 OMS 迁移（OMS 费率更低）             │
│ 3. 专线接入可大幅降低流量成本                              │
└─────────────────────────────────────────────────────────┘
```

### 5.4 成本优化方案

```
方案 1: 华为云 OMS 替代 rclone 公网传输
  OMS 费率: ¥0.01/GB（vs EIP ¥0.50/GB）
  5PB OMS 费用: ¥5 万（vs ¥262 万）
  节省: ¥257 万（98% 流量费节省）
  限制: OMS 走内部通道，带宽可能受限，迁移时间可能更长

方案 2: 专线互联
  阿里云专线 → 中间网络 → 华为云专线
  专线月费: ~¥5-20 万（取决于带宽）
  流量免费（专线不走公网）
  适合: 有长期跨云需求的场景

方案 3: 阿里云 OSS 流量包
  预付费流量包，可享 30-50% 折扣
  5PB 折扣后约: ¥130-180 万
```

---

## 六、最终推荐

### 6.1 推荐采购清单

```
┌─────────────────────────────────────────────────────────────────┐
│ 阿里云采购清单                                                   │
├──────────┬──────────────────────┬──────┬────────────────────────┤
│ 资源      │ 规格                  │ 数量 │ 说明                   │
├──────────┼──────────────────────┼──────┼────────────────────────┤
│ 传输 ECS  │ ecs.g8i.4xlarge      │ 4 台 │ 16C/64G, Ubuntu 22.04 │
│          │ 16 vCPU / 64 GB RAM   │      │ 内网读 OSS 免费        │
│          │ 系统盘 100G ESSD      │      │                        │
│          │ EIP 按流量计费         │      │ 或用 OMS 省流量费      │
├──────────┼──────────────────────┼──────┼────────────────────────┤
│ 管控 ECS  │ ecs.g8i.xlarge       │ 1 台 │ 4C/16G                │
│          │ 4 vCPU / 16 GB RAM    │      │ 挂载 CPFS NFS          │
│          │ 系统盘 100G ESSD      │      │ 调度/监控/盘点          │
│          │ EIP 按带宽 5Mbps      │      │ 管理跳板               │
├──────────┼──────────────────────┼──────┼────────────────────────┤
│ 中转 OSS  │ 标准/低频存储桶       │ 1 个 │ 5 PB 容量              │
│          │ 同区域同 CPFS         │      │ Data Flow 导出目标      │
└──────────┴──────────────────────┴──────┴────────────────────────┘

阿里云 ECS 合计: 5 台
  - 传输节点: 4 台 ecs.g8i.4xlarge
  - 管控节点: 1 台 ecs.g8i.xlarge

┌─────────────────────────────────────────────────────────────────┐
│ 华为云采购清单（参考）                                            │
├──────────┬──────────────────────┬──────┬────────────────────────┤
│ 同步 ECS  │ 通用型 8C/32G        │ 1 台 │ 与 SFS Turbo 同 VPC    │
│ OBS 桶   │ 标准存储              │ 1 个 │ 5 PB                   │
│ SFS Turbo│ 250MB/s/TiB          │ 1 个 │ 按 5PB 容量购买         │
└──────────┴──────────────────────┴──────┴────────────────────────┘

华为云 ECS 合计: 1 台
```

### 6.2 成本最优方案（强烈推荐）

```
┌─────────────────────────────────────────────────────────────────┐
│ 用华为云 OMS 替代 rclone 公网传输                                │
│                                                                 │
│ 传输路径:                                                       │
│   CPFS →(Data Flow)→ 阿里云 OSS →(华为云 OMS)→ 华为云 OBS       │
│                                                                 │
│ 阿里云侧只需要:                                                 │
│   1 台 ecs.g8i.xlarge (4C/16G)  — 管控+CPFS 挂载+盘点          │
│   不需要传输 ECS（OMS 是华为云托管服务，无需阿里云 ECS 中转）      │
│                                                                 │
│ OMS 费用:                                                       │
│   5PB × ¥0.01/GB = ¥5 万（vs rclone 公网 ¥262 万）             │
│                                                                 │
│ 阿里云侧降至: 1 台 ECS + OSS 存储 + Data Flow                   │
│ 阿里云总费用: ~¥32 万（OSS 存储占 ¥31 万）                       │
│ 华为云 OMS 费用: ~¥5 万                                         │
│ 总迁移费用: ~¥37 万（vs rclone 方案 ~¥338 万，节省 89%）         │
│                                                                 │
│ 代价: OMS 迁移速度可能慢于自建 rclone 集群                      │
│      可通过创建多个 OMS 迁移任务组加速                            │
│      大规模场景建议先用 OMS 做主体，再用 rclone 补增量            │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 方案对比总结

| 维度 | rclone 自建集群 | 华为云 OMS 托管 |
|------|---------------|----------------|
| 阿里云 ECS 数量 | 5 台 | 1 台 |
| ECS 月费 | ~¥10 万 | ~¥1,000 |
| 流量/迁移费 | ¥262 万（EIP 流量） | ¥5 万（OMS 费率） |
| OSS 存储费 | ¥31 万 | ¥31 万 |
| **总费用** | **~¥309 万** | **~¥37 万** |
| 迁移速度 | 可控，14天内 | 取决于 OMS 调度 |
| 技术复杂度 | 高（需部署运维集群） | 低（控制台操作） |
| 增量同步 | 灵活（rclone 参数丰富） | 受限（需配合 rclone） |
| 推荐场景 | 需要精细控制+快速迁移 | 成本优先+可接受较慢速度 |

**最终推荐**：采用 OMS 为主（存量）+ rclone 补充（增量）的混合方案，阿里云侧仅需 1 台管控 ECS。
