# 阿里云 CPFS → 华为云 SFS Turbo + OBS 数据迁移方案

> 设计日期：2026-05-08
> 场景：AI 训练数据集从阿里云 CPFS 存量+增量迁移至华为云 SFS Turbo + OBS
> 目标：实现零数据丢失、最小停机窗口、可验证一致性的跨云数据迁移

---

## 一、迁移架构全景

```
┌─────────────────── 阿里云 ───────────────────┐     ┌─────────────────── 华为云 ───────────────────┐
│                                                │     │                                                │
│  ┌─────────┐   Data Flow    ┌─────────┐       │     │  ┌─────────┐   OMS/rclone   ┌─────────┐   │
│  │  CPFS   │ ─────────────→ │   OSS   │       │     │  │   OBS   │ ←────────────── │ 传输节点 │   │
│  │ (源端)  │   内置导出     │ (中转)  │───────│─────│  │ (目标)  │                │ (ECS)   │   │
│  └─────────┘                └─────────┘       │     │  └────┬────┘                └────┬────┘   │
│                                                │     │       │ obsutil sync             │        │
│  ┌─────────┐   NFS mount                       │     │       ▼                          │        │
│  │ ECS     │ ←────────────                      │     │  ┌─────────┐   NFS mount         │        │
│  │ (POSIX) │  rsync/tar                         │     │  │ SFS     │ ←──────────────────┘        │
│  └─────────┘                                    │     │  │ Turbo   │   NFS vers=3                │
│                                                │     │  │ (目标)  │                             │
│                                                │     │  └─────────┘                             │
└────────────────────────────────────────────────┘     └────────────────────────────────────────────┘
```

### 迁移路径选择

| 路径 | 方法 | 适用场景 | 推荐度 |
|------|------|---------|--------|
| **路径 A** | CPFS → Data Flow → 阿里云 OSS → OMS/rclone → 华为云 OBS → SFS Turbo 互通 | 大规模存量迁移（推荐） | ★★★★★ |
| **路径 B** | CPFS NFS mount → 阿里云 ECS → rclone → 华为云 OBS → SFS Turbo 互通 | 需要细粒度控制 | ★★★★ |
| **路径 C** | CPFS → Data Flow → 阿里云 OSS → rclone → 华为云 ECS → NFS 直写 SFS Turbo | 小规模快速迁移 | ★★★ |

---

## 二、Phase 0：迁移前准备

### 2.1 源端盘点

```bash
# 在阿里云 ECS 上挂载 CPFS（NFS 方式）
# 安装 CPFS-NFS 客户端
wget https://cpfs-hangzhou-nfs-client.oss-cn-hangzhou.aliyuncs.com/aliyun-alinas-utils-latest.el.noarch.rpm
rpm -ivh aliyun-alinas-utils-latest.el.noarch.rpm

# 挂载 CPFS
mount -t alinas -o nolock,hard <cpfs-mount-domain>:/<subdir> /mnt/cpfs

# 数据盘点
du -sh /mnt/cpfs/*                           # 各目录大小
find /mnt/cpfs -type f | wc -l               # 文件总数
find /mnt/cpfs -type f -size +1G | wc -l     # 大文件数量
find /mnt/cpfs -type f -size -1M | wc -l     # 小文件数量
```

### 2.2 目标端准备

```bash
# 1. 创建华为云 OBS 桶（与 SFS Turbo 同区域）
# 控制台: 对象存储服务 → 创建桶
# 桶名: ai-datasets-migration
# 区域: cn-north-4（与 SFS Turbo 一致）
# 存储类别: 标准（热数据）/ 低频（温数据）

# 2. 创建 SFS Turbo 文件系统
# 控制台: SFS Turbo → 创建文件系统
# 类型: 250 MB/s/TiB 或 500 MB/s/TiB（支持 OBS 互通 API）
# 容量: 根据数据量评估
# VPC: 与训练集群同一 VPC

# 3. 配置 SFS Turbo + OBS 互通
# 控制台: SFS Turbo → 文件系统详情 → OBS 互通
# 绑定已创建的 OBS 桶

# 4. 在华为云 ECS 上挂载 SFS Turbo
mount -t nfs -o vers=3,timeo=600,noresvport,nolock,tcp \
  <sfs-turbo-address>:/share /mnt/sfsturbo

# 5. 安装 obsutil
wget https://obs-community.obs.cn-north-1.myhuaweicloud.com/obsutil/current/obsutil_linux_amd64.tar.gz
tar -xzf obsutil_linux_amd64.tar.gz
./obsutil_linux_amd64_*/obsutil config \
  -i=your_ak -k=your_sk -e=obs.cn-north-4.myhuaweicloud.com
```

### 2.3 网络方案选择

| 网络方案 | 带宽 | 成本 | 适用规模 | 推荐场景 |
|---------|------|------|---------|---------|
| 公网传输 | 取决于 ECS 带宽 | 按流量计费 | < 10 TB | 小规模快速迁移 |
| VPN 网关互联 | ~1 Gbps | 固定+流量 | 10-50 TB | 中等规模 |
| 专线互联 (DC+EC) | 1-100 Gbps | 高（按月） | 50 TB+ | 大规模生产迁移 |

**推荐方案**：华为云 OMS 走公网传输（零部署成本），大文件用 rclone 多线程加速。

---

## 三、Phase 1：存量数据全量迁移

### 3.1 Step 1：CPFS → 阿里云 OSS（内置 Data Flow）

利用 CPFS 原生 Data Flow 将数据导出到同区域 OSS。

```
操作步骤:
1. 创建 CPFS Fileset（管理子目录）
2. 创建 Data Flow（关联 Fileset 到 OSS 桶）
3. 创建导出任务（Export Task）

约束条件:
- CPFS 和 OSS 必须在同一区域
- 不支持归档/冷归档类型 OSS 对象
- 不支持导出硬链接、符号链接、空目录
- 单文件导出吞吐: 200 MB/s
- 多文件导出 OPS: 600（MB 级文件）
- 每个文件系统最多 10 个 Data Flow
```

**API 调用示例**：
```bash
# 创建 Data Flow
aliyun nas CreateDataFlow \
  --FileSystemId <cpfs-id> \
  --Description "migration-to-huawei" \
  --SourceSecurityType 1 \
  --FsetId <fset-id> \
  --FileSystemPath "/datasets" \
  --Bucket "oss://my-migration-bucket/datasets/" \
  --StorageType INFREQUENT_ACCESS \
  --Bandwidth 600

# 创建导出任务
aliyun nas CreateDataFlowTask \
  --FileSystemId <cpfs-id> \
  --DataFlowId <flow-id> \
  --TaskType Export \
  --SrcFilePath "/datasets" \
  --DstFilePath "datasets/"
```

### 3.2 Step 2：阿里云 OSS → 华为云 OBS（跨云传输）

#### 方案 A：华为云 OMS（推荐，最简）

华为云 OMS **原生支持**阿里云 OSS 作为源端。

```
操作步骤:
1. 华为云控制台 → 对象存储迁移服务 OMS
2. 创建迁移任务:
   - 源端: 阿里云 OSS
   - 源桶: my-migration-bucket
   - 源区域: oss-cn-hangzhou
   - 源 AK/SK: 阿里云只读 AK
   - 目标桶: ai-datasets-migration
   - 目标区域: cn-north-4
3. 高级配置:
   - 迁移范围: 全桶或指定前缀
   - 并行迁移数: 根据数据量调整
   - 带宽限制: 根据网络条件设置
4. 启动迁移 → 监控进度
```

**大规模场景**：使用 OMS 迁移任务组，批量处理多个桶/前缀。

#### 方案 B：rclone（更灵活，推荐大规模）

在阿里云 ECS 或独立传输节点上部署 rclone。

**配置文件** (`~/.config/rclone/rclone.conf`)：
```ini
[aliyun-oss]
type = s3
provider = Alibaba
env_auth = false
access_key_id = LTAI5tXXXXXXXX
secret_access_key = XXXXXXXXXXXXXXXX
endpoint = oss-cn-hangzhou.aliyuncs.com

[huawei-obs]
type = s3
provider = HuaweiOBS
env_auth = false
access_key_id = XXXXXXXXXXXXXXXX
secret_access_key = XXXXXXXXXXXXXXXX
region = cn-north-4
endpoint = obs.cn-north-4.myhuaweicloud.com
```

**全量迁移命令**：
```bash
# 先做 dry-run 验证
rclone sync aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration \
  --dry-run \
  --verbose \
  --fast-list

# 正式全量同步
rclone sync aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration \
  --progress \
  --transfers 32 \
  --checkers 64 \
  --s3-chunk-size 64M \
  --s3-upload-concurrency 8 \
  --fast-list \
  --retries 10 \
  --retries-sleep 5s \
  --timeout 10m \
  --log-file /var/log/rclone-full-migration.log \
  --log-level INFO \
  --stats 5m \
  --stats-one-line
```

**按数据类型调优**：

```bash
# AI 数据集（大量中小文件：json/jsonl/parquet/csv）
rclone sync aliyun-oss:bucket huawei-obs:bucket \
  --transfers 64 \
  --checkers 128 \
  --s3-chunk-size 5M \
  --fast-list

# 大模型文件（大文件：.bin/.safetensors/.pt，单文件 >1GB）
rclone sync aliyun-oss:bucket huawei-obs:bucket \
  --transfers 8 \
  --checkers 16 \
  --s3-chunk-size 64M \
  --s3-upload-concurrency 8 \
  --multi-thread-streams 8

# 混合类型（通用）
rclone sync aliyun-oss:bucket huawei-obs:bucket \
  --transfers 32 \
  --checkers 64 \
  --s3-chunk-size 32M \
  --s3-upload-concurrency 8 \
  --fast-list
```

### 3.3 Step 3：华为云 OBS → SFS Turbo（互通导入）

#### 方案 A：SFS Turbo + OBS 互通（原生，推荐）

```
操作步骤:
1. SFS Turbo 控制台 → 文件系统详情 → OBS 互通
2. 创建数据导入任务:
   - OBS 桶: ai-datasets-migration
   - OBS 路径: datasets/（或留空导入全部）
   - 目标目录: /mnt/sfsturbo/datasets/
3. 启动导入
4. 导入完成后验证文件完整性

注意: 仅 250/500/1000 MB/s/TiB 规格支持互通 API
```

#### 方案 B：obsutil sync（增量友好）

```bash
# 挂载 SFS Turbo
mount -t nfs -o vers=3,timeo=600,noresvport,nolock,tcp \
  <sfs-turbo-address>:/share /mnt/sfsturbo

# 从 OBS 同步到 SFS Turbo
./obsutil sync obs://ai-datasets-migration/datasets/ /mnt/sfsturbo/datasets/ \
  -p=10 -ps=5

# 验证
find /mnt/sfsturbo/datasets -type f | wc -l
du -sh /mnt/sfsturbo/datasets
```

### 3.4 全量迁移时间估算

| 数据量 | 公网 1 Gbps | 公网 10 Gbps | 专线 10 Gbps |
|--------|------------|-------------|-------------|
| 1 TB | ~2.5 小时 | ~15 分钟 | ~15 分钟 |
| 10 TB | ~1.2 天 | ~2.5 小时 | ~2.5 小时 |
| 100 TB | ~12 天 | ~1.2 天 | ~1.2 天 |
| 500 TB | ~60 天 | ~6 天 | ~6 天 |

> 实际吞吐约为理论值的 60-80%。

---

## 四、Phase 2：增量数据持续同步

存量迁移完成后，源端可能有新增或变更数据。需要建立增量同步机制。

### 4.1 方案 A：rclone 定时增量同步（推荐）

```bash
# 时间戳增量：仅传输比目标新的文件
rclone copy aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration \
  --update \
  --use-server-modtime \
  --fast-list \
  --transfers 16 \
  --log-file /var/log/rclone-incr-sync.log

# 或校验和增量：最精确（推荐）
rclone copy aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration \
  --checksum \
  --fast-list \
  --transfers 16 \
  --log-file /var/log/rclone-incr-sync.log
```

**Cron 配置**：
```bash
# 每 4 小时增量同步一次
0 */4 * * * /usr/bin/rclone copy aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration --update --use-server-modtime --fast-list --transfers 16 --log-file /var/log/rclone-incr-sync.log 2>&1

# 每天凌晨 2 点全量校验同步
0 2 * * * /usr/bin/rclone sync aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration --checksum --fast-list --transfers 32 --log-file /var/log/rclone-daily-sync.log 2>&1
```

### 4.2 方案 B：CPFS Data Flow 持续导出 + rclone 增量

```
架构:
  CPFS ──(Data Flow 自动导出)──→ 阿里云 OSS ──(rclone cron)──→ 华为云 OBS ──(obsutil sync)──→ SFS Turbo

Data Flow 配置:
1. 配置 Data Flow 的自动元数据更新（Auto Metadata Update）
2. CPFS 侧文件变更自动反映到 OSS
3. rclone cron 定期将 OSS 增量同步到华为云 OBS
4. obsutil sync 将 OBS 增量同步到 SFS Turbo

时序:
  CPFS 变更 → Data Flow 导出（延迟: 分钟级）
           → rclone 每 4h 同步
           → obsutil sync 每 4h 同步
  端到端延迟: 4-8 小时
```

### 4.3 方案 C：事件驱动近实时同步（最先进）

```
架构:
  CPFS ──(Data Flow)──→ 阿里云 OSS
    ↓ (OSS 事件通知)
  SMQ 消息队列
    ↓ (触发)
  函数计算 (FC)
    ↓ (rclone/SDK)
  华为云 OBS ──(互通/obsutil)──→ SFS Turbo

步骤:
1. 阿里云 OSS 配置事件通知规则:
   - 事件类型: ObjectCreated:Put, ObjectCreated:Post, ObjectCreated:Copy
   - 目标: SMQ 队列
2. 部署函数计算函数:
   - 触发器: SMQ 队列消息
   - 逻辑: 读取事件中的 object key → 下载 OSS 对象 → 上传到华为云 OBS
3. SFS Turbo 定期从 OBS 同步（obsutil sync cron）

端到端延迟: 秒级~分钟级
```

---

## 五、Phase 3：数据验证与切换

### 5.1 数据完整性验证

```bash
# 1. rclone 校验（OBS 层面）
rclone check aliyun-oss:my-migration-bucket huawei-obs:ai-datasets-migration \
  --checksum \
  --fast-list \
  --log-file /var/log/rclone-verify.log

# 2. 文件数量对比
# 阿里云侧
ossutil ls oss://my-migration-bucket/datasets/ -r -s | grep "total object count"
# 华为云侧
./obsutil ls obs://ai-datasets-migration/datasets/ -r -s | grep "total object count"

# 3. SFS Turbo 挂载点验证
find /mnt/sfsturbo/datasets -type f | wc -l
# 对比源端 CPFS 文件数
find /mnt/cpfs/datasets -type f | wc -l

# 4. 抽样 MD5 校验
md5sum /mnt/cpfs/datasets/sample_file.json
md5sum /mnt/sfsturbo/datasets/sample_file.json
```

### 5.2 切换流程

```
切换窗口（建议选择低峰期）:

T+0:  停止源端训练任务，确保 CPFS 无写入
T+5:  触发 CPFS Data Flow 最终导出
T+30: 触发 rclone 最终增量同步
T+60: 触发 obsutil 最终同步到 SFS Turbo
T+90: 数据完整性验证
T+120: 验证通过 → 切换 DNS/配置指向华为云 SFS Turbo
T+130: 恢复训练任务（指向华为云 SFS Turbo）
T+180: 确认无异常，迁移完成
```

---

## 六、目标端存储分层策略

### 6.1 数据生命周期设计

```
┌──────────────────────────────────────────────────────┐
│                     华为云存储架构                      │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐  热数据    ┌──────────────┐        │
│  │  SFS Turbo   │ ← 训练    │   OBS 标准    │ ← 近期 │
│  │  (高频访问)  │    加载    │   (标准存储)  │    数据 │
│  └──────────────┘           └──────────────┘        │
│         ↑ 按需导入                  ↓ 生命周期规则    │
│                                  ┌──────────────┐    │
│                                  │ OBS 低频访问  │ ← 30天 │
│                                  │ (温数据)     │    后转 │
│                                  └──────────────┘    │
│                                         ↓ 生命周期规则 │
│                                  ┌──────────────┐    │
│                                  │  OBS 归档    │ ← 90天 │
│                                  │  (冷数据)    │    后转 │
│                                  └──────────────┘    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 6.2 OBS 生命周期规则

```json
{
  "LifecycleConfiguration": {
    "Rules": [
      {
        "ID": "datasets-tiering",
        "Prefix": "datasets/",
        "Status": "Enabled",
        "Transitions": [
          {
            "Days": 30,
            "StorageClass": "WARM"
          },
          {
            "Days": 90,
            "StorageClass": "COLD"
          }
        ]
      },
      {
        "ID": "cleanup-incomplete-uploads",
        "Prefix": "",
        "Status": "Enabled",
        "AbortIncompleteMultipartUpload": {
          "DaysAfterInitiation": 7
        }
      }
    ]
  }
}
```

### 6.3 SFS Turbo 容量规划

| 数据类型 | 存储位置 | SFS Turbo 空间 | 说明 |
|---------|---------|---------------|------|
| 当前训练数据集 | SFS Turbo | 全量 | 高频读写，需要低延迟 |
| 近期历史数据 | OBS 标准 | 按需导入 | 训练时按需加载到 SFS Turbo |
| 历史归档数据 | OBS 低频/归档 | 不占用 | 仅 OBS 存储，需要时恢复 |

---

## 七、监控与告警

### 7.1 迁移过程监控

```bash
# rclone 实时统计
rclone sync source:bucket dest:bucket \
  --stats 5m \
  --stats-one-line \
  --stats-log-level NOTICE \
  --log-file /var/log/rclone-migration.log \
  --use-json-log

# 日志示例输出
# {"level":"notice","msg":"\nTransferred: \t\t50.000 GiB / 100.000 GiB, 50%, 1.000 GiB/s, ETA 50s\nChecks: \t\t1000\nTransferred: \t\t500 / 1000\nElapsed time: \t\t50.0s"}
```

### 7.2 华为云 CES 监控

```
监控指标:
1. OBS 桶:
   - 对象数量 (ObjectCount)
   - 存储容量 (BucketSize)
   - 请求次数 (RequestCount)
   - 下行带宽 (DownloadBandwidth)

2. SFS Turbo:
   - 已用容量 (CapacityUsed)
   - 带宽使用率 (BandwidthUtilization)
   - IOPS (IOPS)
   - 客户端连接数 (ClientConnections)

告警规则:
- OBS 桶容量增长停滞 → 迁移可能中断
- SFS Turbo 容量超过 80% → 需扩容
- 迁移节点 CPU/内存/网络 → 资源瓶颈
```

### 7.3 自动化巡检脚本

```bash
#!/bin/bash
# migration_monitor.sh

ALI_BUCKET="my-migration-bucket"
HW_BUCKET="ai-datasets-migration"
LOG="/var/log/migration-monitor.log"

echo "[$(date)] Migration Monitor Start" >> $LOG

# 检查 OSS 源端对象数
ALI_COUNT=$(rclone size aliyun-oss:$ALI_BUCKET --json 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])")

# 检查 OBS 目标端对象数
HW_COUNT=$(rclone size huawei-obs:$HW_BUCKET --json 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])")

echo "[$(date)] Source: $ALI_COUNT objects, Target: $HW_COUNT objects" >> $LOG

# 差异告警
DIFF=$((ALI_COUNT - HW_COUNT))
if [ $DIFF -gt 100 ]; then
  echo "[ALERT] $DIFF objects not yet migrated!" >> $LOG
  # 发送告警通知（邮件/钉钉/企微）
fi

# 检查 SFS Turbo 挂载点
SFS_COUNT=$(find /mnt/sfsturbo/datasets -type f | wc -l)
echo "[$(date)] SFS Turbo files: $SFS_COUNT" >> $LOG
```

---

## 八、容错与回退

### 8.1 断点续传

```bash
# rclone 内置断点续传
# 如果中断，重新执行相同的 sync/copy 命令即可
# rclone 会自动跳过已成功传输的文件
rclone sync aliyun-oss:bucket huawei-obs:bucket \
  --retries 10 \
  --retries-sleep 5s \
  --low-level-retries 30
```

### 8.2 大文件分片保护

```bash
# 分片上传失败时保留已上传分片（用于恢复）
rclone sync source:bucket dest:bucket \
  --s3-leave-parts-on-error

# 注意: 未完成的分片会产生存储费用，迁移后需清理
# 清理未完成分片
rclone cleanup huawei-obs:ai-datasets-migration
```

### 8.3 回退方案

```
迁移失败或需要回退时:
1. 切回阿里云 CPFS 源端（训练配置指向 CPFS）
2. 清理华为云 OBS 和 SFS Turbo 中的迁移数据
3. 诊断失败原因（检查日志）
4. 修复后重新发起迁移

回退触发条件:
- 数据完整性验证失败（文件数量/校验和不一致）
- 迁移超过计划时间窗口 2 倍
- 目标端性能不达标（SFS Turbo 延迟异常）
```

---

## 九、成本估算参考

### 9.1 数据传输成本

| 费用项 | 计费方式 | 说明 |
|--------|---------|------|
| 阿里云 OSS 流出 | ¥0.50/GB（公网） | CPFS→OSS 导出免费（同区域） |
| 华为云 OBS 流入 | 免费 | 入方向不收费 |
| 阿里云 ECS 带宽 | 按带宽/按流量 | 传输节点带宽费用 |
| 华为云 OMS | ¥0.01/GB | 托管迁移服务费用 |

### 9.2 存储成本（华为云侧）

| 存储类型 | 月费用参考 | 适用 |
|---------|-----------|------|
| SFS Turbo (250MB/s/TiB) | ¥1,200/TiB/月 | 热数据训练 |
| OBS 标准 | ¥0.099/GB/月 | 近期数据 |
| OBS 低频 | ¥0.055/GB/月 | 30天+数据 |
| OBS 归档 | ¥0.033/GB/月 | 90天+数据 |

---

## 十、推荐迁移方案总结

### 中小规模 (< 50 TB)

```
推荐路径: CPFS → Data Flow → OSS → 华为云 OMS → OBS → SFS Turbo 互通
时间: 1-3 天
工具: CPFS Data Flow + 华为云 OMS（全程托管）
增量: 迁移后使用 rclone cron 增量同步
```

### 大规模 (50-500 TB)

```
推荐路径: CPFS → Data Flow → OSS → rclone → OBS → SFS Turbo 互通
时间: 3-14 天
工具: CPFS Data Flow + rclone（高性能 ECS 传输节点）
增量: rclone cron 增量 + CPFS Data Flow 自动导出
网络: 建议专线或高带宽 ECS
```

### 超大规模 (500 TB+)

```
推荐路径: CPFS → Data Flow → OSS → rclone 并行 → OBS → SFS Turbo 互通
时间: 2-4 周
工具: CPFS Data Flow + 多节点 rclone + SFS Turbo 互通 API
增量: 事件驱动 (OSS→SMQ→FC→OBS) + obsutil sync
网络: 专线互联
特殊: 考虑阿里云 Data Transport 物理设备离线导出
```
