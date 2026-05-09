# 5PB 级 CPFS 数据迁移方案：存量+并发更新场景

> 设计日期：2026-05-08
> 场景：5PB 存量数据迁移，迁移期间源端持续有数据更新
> 核心挑战：全量迁移周期长达数周，期间数据持续变更，如何保证最终一致性

---

## 一、问题分析

### 1.1 规模估算

```
存量数据量: 5 PB = 5,120 TB
预估文件数: 数千万~数亿（取决于平均文件大小）

全量迁移时间估算:
┌────────────┬──────────────┬────────────────┐
│ 网络带宽    │ 理论时间      │ 实际时间(70%)  │
├────────────┼──────────────┼────────────────┤
│ 10 Gbps    │ ~52 天       │ ~74 天         │
│ 40 Gbps    │ ~13 天       │ ~19 天         │
│ 100 Gbps   │ ~5.2 天      │ ~7.5 天        │
└────────────┴──────────────┴────────────────┘

核心问题:
迁移期间（7-74天），CPFS 上的数据持续被训练任务读写。
新数据写入、旧数据修改、文件删除 都会在迁移窗口内发生。
简单的一次性全量同步无法保证最终一致性。
```

### 1.2 变更特征分析

```
AI 训练数据集的变更模式:

┌─────────────────────────────────────────────────────┐
│ 数据类型          │ 变更模式       │ 变更频率/日     │
├─────────────────────────────────────────────────────┤
│ 训练数据集(jsonl)  │ 新增为主       │ 1-10 TB/天     │
│ 模型权重(bin/pt)   │ 新增+覆盖      │ 0.5-5 TB/天    │
│ Checkpoint 文件    │ 新增+过期删除   │ 1-20 TB/天     │
│ 配置/元数据        │ 覆盖修改       │ 极少           │
│ 日志文件           │ 追加写入       │ 0.1-1 TB/天    │
└─────────────────────────────────────────────────────┘

关键洞察:
1. 变更以"新增"为主（新增数据集、新增 checkpoint），覆盖修改较少
2. 日增量约 2-35 TB（占存量的 0.04%-0.7%）
3. 迁移周期越长，累积增量越大
4. 存在"写入中"的文件（训练过程中的 checkpoint），需跳过或等待完成
```

---

## 二、总体策略：多轮收敛 + 最终切换

### 2.1 核心思想

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Round 0: 全量迁移（耗时最长，处理 5PB 存量）                │
│  Round 1: 第一轮增量（处理 R0 期间的变更）                    │
│  Round 2: 第二轮增量（处理 R1 期间的变更，变更量骤降）         │
│  Round 3: 第三轮增量（变更量极小，接近收敛）                  │
│  Final:    冻结源端 → 最终增量 → 数据校验 → 切换             │
│                                                             │
│  每轮增量 = 上一轮期间的新增/修改/删除                        │
│  随着轮次推进，增量越来越小，最终趋于零                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 收敛过程示意

```
数据量
  │
  │ ████████████████████████████████████████████ 5 PB
  │                                            ← Round 0: 全量
  │                                         ██ ← R0期间增量 ~35 TB
  │                                        █  ← Round 1: 增量
  │                                       █   ← R1期间增量 ~3.5 TB
  │                                      █    ← Round 2: 增量
  │                                     ■     ← R2期间增量 ~350 GB
  │                                    ■      ← Round 3: 增量
  │                                    ▬      ← Final: 冻结后增量 <10 GB
  │
  └────────────────────────────────────────── 时间 →
     R0(~14天)  R1(~1天)  R2(~2h)  R3(~30min)  Final
```

---

## 三、Phase 0：变更追踪基础设施（迁移前部署）

在全量迁移开始前，必须在源端建立变更追踪能力。

### 3.1 CPFS Data Flow 自动元数据更新

```
操作:
1. 创建 CPFS Fileset 覆盖全部待迁移目录
2. 创建 Data Flow（CPFS → OSS 中转桶）
3. 开启"自动元数据更新"功能
4. Data Flow 会持续监控 CPFS 的文件变更事件

约束:
- 每个文件系统最多 10 个 Data Flow
- 每个文件集最多 100 万个文件/目录
- 超过限制需拆分为多个 Fileset + Data Flow

建议按目录拆分:
  Fileset 1: /datasets/training    → Data Flow 1 → OSS prefix: datasets/training/
  Fileset 2: /datasets/evaluation  → Data Flow 2 → OSS prefix: datasets/evaluation/
  Fileset 3: /models               → Data Flow 3 → OSS prefix: models/
  Fileset 4: /checkpoints          → Data Flow 4 → OSS prefix: checkpoints/
  …
```

### 3.2 变更日志采集

```bash
# 方案 A: 基于文件系统快照的变更检测
# 在 CPFS NFS 挂载点上，定期生成文件清单快照

cat > /opt/migration/snapshot.sh << 'EOF'
#!/bin/bash
SNAP_DIR="/opt/migration/snapshots"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $SNAP_DIR

# 生成文件清单（含大小、修改时间、MD5）
# 对于5PB/数亿文件，全量扫描约需数小时
# 建议按目录分片并行
find /mnt/cpfs/datasets -type f -printf '%T@ %s %p\n' | sort -k3 > "$SNAP_DIR/full_$DATE.txt"

# 与上次快照对比，提取变更
if [ -f "$SNAP_DIR/last_full.txt" ]; then
  diff "$SNAP_DIR/last_full.txt" "$SNAP_DIR/full_$DATE.txt" \
    | grep "^>" | awk '{print $3}' > "$SNAP_DIR/added_$DATE.txt"
  diff "$SNAP_DIR/last_full.txt" "$SNAP_DIR/full_$DATE.txt" \
    | grep "^<" | awk '{print $3}' > "$SNAP_DIR/deleted_$DATE.txt"
fi

ln -sf "$SNAP_DIR/full_$DATE.txt" "$SNAP_DIR/last_full.txt"
EOF

# Cron: 每小时生成一次增量快照
# 0 * * * * /opt/migration/snapshot.sh
```

```bash
# 方案 B: 基于时间戳的轻量增量检测（推荐）
# 利用 find -newer 高效定位变更文件

cat > /opt/migration/find_changes.sh << 'EOF'
#!/bin/bash
MARKER_FILE="/opt/migration/.last_sync_time"
CHANGE_LOG="/opt/migration/changes/changes_$(date +%Y%m%d_%H%M%S).txt"
mkdir -p /opt/migration/changes

if [ -f "$MARKER_FILE" ]; then
  # 查找比上次同步更新的文件
  find /mnt/cpfs -type f -newer "$MARKER_FILE" -printf '%T@ %s %p\n' > "$CHANGE_LOG"
else
  echo "No marker file, will do full scan"
fi

# 更新标记时间
touch "$MARKER_FILE"

echo "Found $(wc -l < $CHANGE_LOG) changed files"
EOF
```

```python
# 方案 C: 基于阿里云 OSS 事件记录的变更追踪（最精确）
# CPFS Data Flow 导出文件到 OSS 时，会产生 OSS 事件
# 通过 SMQ/MNS 消费这些事件，记录变更

import json
import oss2
from datetime import datetime

class ChangeTracker:
    """消费 OSS 事件，记录 CPFS→OSS 的变更"""

    def __init__(self, change_log_path):
        self.change_log_path = change_log_path

    def record_change(self, event):
        """
        OSS 事件格式:
        {
          "events": [{
            "eventName": "ObjectCreated:Put",
            "oss": {
              "object": {"key": "datasets/train/part_001.jsonl", "size": 1073741824},
              "bucket": {"name": "migration-bucket"}
            },
            "eventTime": "2026-05-08T10:30:00.000Z"
          }]
        }
        """
        for evt in event.get("events", []):
            record = {
                "time": evt["eventTime"],
                "action": evt["eventName"],
                "key": evt["oss"]["object"]["key"],
                "size": evt["oss"]["object"]["size"],
            }
            with open(self.change_log_path, "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

### 3.3 传输集群部署

```
5PB 规模需要多节点并行传输:

┌───────────────────────────────────────────────┐
│              传输集群架构                       │
├───────────────────────────────────────────────┤
│                                               │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐      │
│  │ ECS-01  │  │ ECS-02  │  │ ECS-03  │  ... │
│  │ rclone  │  │ rclone  │  │ rclone  │      │
│  │ 目录A-C │  │ 目录D-F │  │ 目录G-I │      │
│  └────┬────┘  └────┬────┘  └────┬────┘      │
│       │            │            │             │
│       └────────────┴────────────┘             │
│                    │                          │
│              华为云 OBS                        │
│                    │                          │
│              SFS Turbo                        │
└───────────────────────────────────────────────┘

ECS 规格: 16 vCPU / 64 GB RAM / 10 Gbps 带宽
节点数量: 根据目录数和数据量分配
建议: 8-16 个传输节点

每节点资源需求:
  rclone 内存 = transfers × upload_concurrency × chunk_size
  = 32 × 8 × 64MB = 16 GB
  加上系统开销，64 GB RAM 节点可支撑
```

---

## 四、Phase 1：Round 0 — 全量迁移

### 4.1 按目录分片并行

```bash
#!/bin/bash
# distribute_migration.sh
# 将 5PB 拆分为多个分片，分配给不同传输节点

# 全量迁移分片配置
declare -A SHARDS=(
  ["node01"]="datasets/training/part_00_09"
  ["node02"]="datasets/training/part_10_19"
  ["node03"]="datasets/training/part_20_29"
  ["node04"]="datasets/training/part_30_39"
  ["node05"]="datasets/evaluation"
  ["node06"]="datasets/preprocessing"
  ["node07"]="models/llama"
  ["node08"]="models/qwen"
  ["node09"]="models/deepseek"
  ["node10"]="checkpoints"
  ["node11"]="configs_and_scripts"
  ["node12"]="logs_and_metrics"
)

for node in "${!SHARDS[@]}"; do
  prefix="${SHARDS[$node]}"
  echo "Assigning $prefix to $node"

  # 在各节点上执行
  ssh $node "nohup rclone sync \
    aliyun-oss:migration-bucket/$prefix/ \
    huawei-obs:ai-datasets-5pb/$prefix/ \
    --progress \
    --transfers 32 \
    --checkers 64 \
    --s3-chunk-size 64M \
    --s3-upload-concurrency 8 \
    --fast-list \
    --retries 10 \
    --retries-sleep 5s \
    --timeout 10m \
    --log-file /var/log/rclone-r0-$node.log \
    --log-level INFO \
    --stats 10m \
    --stats-one-line \
    > /var/log/rclone-r0-$node-stdout.log 2>&1 &"
done
```

### 4.2 进度监控

```python
#!/usr/bin/env python3
# migration_dashboard.py
# 汇总所有传输节点的进度

import json
import subprocess
import time
from datetime import datetime

NODES = ["node01", "node02", "node03", "node04", "node05",
         "node06", "node07", "node08", "node09", "node10",
         "node11", "node12"]

TOTAL_SIZE_PB = 5.0

def get_node_progress(node):
    """从 rclone 日志提取进度"""
    try:
        result = subprocess.run(
            ["ssh", node, "tail -100 /var/log/rclone-r0-stdout.log"],
            capture_output=True, text=True, timeout=10
        )
        # 解析 rclone 的统计输出
        for line in reversed(result.stdout.split('\n')):
            if 'Transferred' in line and 'GiB' in line:
                # 提取已传输量
                return parse_transfer(line)
    except Exception as e:
        return {"status": "error", "error": str(e)}
    return {"status": "unknown"}

def parse_transfer(line):
    """解析 rclone 统计行"""
    # 示例: Transferred: 50.000 GiB / 100.000 GiB, 50%, 1.000 GiB/s
    parts = line.split('Transferred:')[1].strip().split(',')
    transferred = parts[0].strip().split('/')[0].strip()
    return {"status": "running", "transferred": transferred}

def main():
    while True:
        print(f"\n{'='*60}")
        print(f"Migration Dashboard - {datetime.now()}")
        print(f"{'='*60}")

        total_transferred = 0
        for node in NODES:
            progress = get_node_progress(node)
            print(f"  {node}: {progress}")

        print(f"\n  Target: {TOTAL_SIZE_PB} PB")
        time.sleep(600)  # 每10分钟刷新

if __name__ == "__main__":
    main()
```

### 4.3 全量迁移期间的保护措施

```
并行写入保护:

1. 跳过正在写入的文件:
   CPFS Data Flow 导出时，正在写入的文件会被标记
   rclone --exclude "*.tmp" --exclude "*.writing"

2. Checkpoint 文件处理:
   训练过程中产生的 checkpoint 文件持续增长
   策略: 只同步已完成的 checkpoint（.completed 标记文件）
   rclone --include "*.completed" --include-from=checkpoint_list.txt

3. 大文件分片容错:
   rclone sync ... \
     --s3-leave-parts-on-error \   # 保留已上传分片
     --retries 10 \                # 重试 10 次
     --retries-sleep 5s            # 重试间隔

4. 断点续传:
   rclone 天然支持: 重新执行相同命令自动跳过已完成文件
```

---

## 五、Phase 2：Round 1-N — 多轮增量收敛

### 5.1 增量轮次策略

```
Round 0 (全量): 5 PB        → 耗时约 14 天 (40 Gbps 专线)
Round 1 (增量): ~35 TB      → 耗时约 2 小时
Round 2 (增量): ~3.5 TB     → 耗时约 15 分钟
Round 3 (增量): ~350 GB     → 耗时约 2 分钟
Final  (增量): ~10 GB       → 耗时约 30 秒（冻结窗口内）

收敛公式:
  每轮增量 ≈ 上轮增量 × 变更率 × 上轮耗时
  若变更率 = 1%/天，Round 0 耗时 14 天
  R1 = 5000 TB × 1% × 14 = 700 TB ← 过于悲观

实际场景（AI 数据）:
  日增量约 10-35 TB（非全量变更率）
  R1 ≈ 14天 × 25 TB/天 = 350 TB
  R2 ≈ 2小时 × 25 TB/天 / 24 = 2 TB
  R3 ≈ 15分钟 × 25 TB/天 / 1440 = 260 GB
  Final ≈ 30秒 × 25 TB/天 / 86400 = 10 MB

注意: 如果日增量极大(350TB)，需要提高网络带宽或延长收敛轮次
```

### 5.2 增量同步执行

```bash
#!/bin/bash
# incremental_sync.sh
# 执行一轮增量同步

ROUND=$1  # 当前轮次
LOG_DIR="/var/log/migration"
mkdir -p $LOG_DIR

echo "[$(date)] Starting incremental sync Round $ROUND"

# ============================================================
# 方案 A: 基于 rclone --update（时间戳增量）
# 优点: 速度快，API 调用少
# 缺点: 可能遗漏时间戳不变的变更
# ============================================================
rclone copy aliyun-oss:migration-bucket huawei-obs:ai-datasets-5pb \
  --update \
  --use-server-modtime \
  --fast-list \
  --transfers 32 \
  --checkers 64 \
  --s3-chunk-size 32M \
  --log-file "$LOG_DIR/incr_r${ROUND}_$(date +%Y%m%d_%H%M%S).log" \
  --log-level INFO \
  --stats 5m \
  --stats-one-line

# ============================================================
# 方案 B: 基于 rclone --checksum（校验和增量，推荐）
# 优点: 最精确，不会遗漏
# 缺点: 需要计算 MD5，CPU 开销大
# ============================================================
# rclone copy aliyun-oss:migration-bucket huawei-obs:ai-datasets-5pb \
#   --checksum \
#   --fast-list \
#   --transfers 32 \
#   --checkers 64 \
#   --log-file "$LOG_DIR/incr_checksum_r${ROUND}_$(date +%Y%m%d_%H%M%S).log"

# ============================================================
# 方案 C: 基于变更日志的精准增量（最精确，5PB 场景推荐）
# 仅同步已知变更的文件，跳过全量扫描
# ============================================================
# if [ -f "/opt/migration/changes/changes_latest.txt" ]; then
#   rclone copy aliyun-oss:migration-bucket huawei-obs:ai-datasets-5pb \
#     --files-from /opt/migration/changes/changes_latest.txt \
#     --transfers 32 \
#     --log-file "$LOG_DIR/incr_targeted_r${ROUND}_$(date +%Y%m%d_%H%M%S).log"
# fi

# 同步到 SFS Turbo
./obsutil sync obs://ai-datasets-5pb/ /mnt/sfsturbo/ \
  -p=10 -ps=5

echo "[$(date)] Round $ROUND complete"
```

### 5.3 自动化收敛调度

```bash
#!/bin/bash
# convergence_loop.sh
# 自动执行多轮增量直到收敛

ROUND=0
THRESHOLD_GB=10  # 收敛阈值：增量小于 10GB 时停止
MAX_ROUNDS=20    # 最大轮次

while [ $ROUND -lt $MAX_ROUNDS ]; do
  ROUND=$((ROUND + 1))
  echo "=== Round $ROUND ==="

  START_TIME=$(date +%s)

  # 执行增量同步
  /opt/migration/incremental_sync.sh $ROUND

  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))

  # 解析本轮增量数据量
  INCR_SIZE=$(tail -1 /var/log/migration/incr_r${ROUND}_*.log | \
    grep -oP 'Transferred:\s+\K[\d.]+')
  INCR_UNIT=$(tail -1 /var/log/migration/incr_r${ROUND}_*.log | \
    grep -oP 'Transferred:[\s\d.]+\K(GiB|MiB|KiB|TiB)')

  # 转换为 GB
  INCR_GB=$(echo "$INCR_SIZE $INCR_UNIT" | awk '{
    val=$1; unit=$2;
    if(unit=="TiB") val=val*1024;
    if(unit=="GiB") val=val;
    if(unit=="MiB") val=val/1024;
    if(unit=="KiB") val=val/1024/1024;
    printf "%.1f", val
  }')

  echo "Round $ROUND: ${INCR_GB} GB transferred in ${DURATION}s"

  # 收敛判断
  if (( $(echo "$INCR_GB < $THRESHOLD_GB" | bc -l) )); then
    echo "Converged! Incremental size ${INCR_GB} GB < threshold ${THRESHOLD_GB} GB"
    echo "Ready for final cutover."
    break
  fi

  # 如果增量仍然很大，继续下一轮
  echo "Not yet converged. Waiting 30s before next round..."
  sleep 30
done

if [ $ROUND -ge $MAX_ROUNDS ]; then
  echo "WARNING: Did not converge after $MAX_ROUNDS rounds"
  echo "Consider wider freeze window or higher bandwidth"
fi
```

---

## 六、Phase 3：最终切换（冻结窗口）

### 6.1 切换时序图

```
时间线（以冻结时刻 T=0 为基准）

T-72h  ──────── Round N-2 增量同步（最后一大轮）
T-24h  ──────── Round N-1 增量同步
T-2h   ──────── 通知所有用户：即将切换
T-30m  ──────── 停止接受新的训练任务
T-5m   ──────── 等待正在运行的训练任务完成当前 epoch/step
                     ↓
T=0    ════════ 冻结源端 CPFS（标记只读 / 停止写入）
                     ↓
T+0m   ──────── 触发 CPFS Data Flow 最终导出（确保所有数据同步到 OSS）
T+10m  ──────── 触发 rclone 最终增量同步（OSS → OBS）
T+20m  ──────── 触发 obsutil 最终同步（OBS → SFS Turbo）
T+30m  ──────── 数据完整性校验
T+60m  ──────── 校验通过 → 切换训练配置指向华为云 SFS Turbo
T+65m  ──────── 恢复训练任务（在华为云 SFS Turbo 上运行）
T+120m ──────── 确认无异常 → 迁移完成
T+24h  ──────── 保留源端只读，作为回退备份
T+168h ──────── 确认无回退需求 → 释放源端资源
```

### 6.2 冻结操作

```bash
#!/bin/bash
# freeze_source.sh
# 冻结源端，停止所有写入

echo "[FREEZE] Stopping training jobs on CPFS..."

# 方法 1: 通过 PAI/DLC 停止训练任务
# aliyun paistop --all-jobs

# 方法 2: 将 CPFS 目录设为只读（需要 CPFS 管理权限）
# 通过 NFS 重新挂载为只读
mount -o remount,ro /mnt/cpfs

# 方法 3: 修改训练脚本写入路径（应用层面停止写入）
echo "[FREEZE] Source frozen at $(date)" > /opt/migration/freeze_time
```

### 6.3 最终同步

```bash
#!/bin/bash
# final_sync.sh
# 冻结后的最终数据同步

echo "[FINAL] Starting final sync at $(date)"

# Step 1: CPFS → OSS 最终导出
aliyun nas CreateDataFlowTask \
  --FileSystemId <cpfs-id> \
  --DataFlowId <flow-id> \
  --TaskType Export \
  --SrcFilePath "/" \
  --DstFilePath "/"

# 等待导出完成
echo "[FINAL] Waiting for CPFS Data Flow export to complete..."
aliyun nas WaitForDataFlowTaskComplete --TaskId <task-id>

# Step 2: OSS → OBS 最终增量
rclone sync aliyun-oss:migration-bucket huawei-obs:ai-datasets-5pb \
  --checksum \
  --fast-list \
  --transfers 32 \
  --checkers 64 \
  --log-file /var/log/migration/final_sync.log \
  --log-level INFO \
  --verbose

# Step 3: OBS → SFS Turbo 最终同步
./obsutil sync obs://ai-datasets-5pb/ /mnt/sfsturbo/ -p=10 -ps=5

echo "[FINAL] Final sync complete at $(date)"
```

### 6.4 数据校验

```bash
#!/bin/bash
# verify_migration.sh
# 全量数据完整性校验

echo "[VERIFY] Starting data integrity verification"

# 1. 对象数量对比
echo "--- Object Count ---"
SRC_COUNT=$(rclone size aliyun-oss:migration-bucket --json | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])")
DST_COUNT=$(rclone size huawei-obs:ai-datasets-5pb --json | python3 -c "import sys,json;print(json.load(sys.stdin)['count'])")
echo "Source: $SRC_COUNT objects"
echo "Target: $DST_COUNT objects"
if [ "$SRC_COUNT" != "$DST_COUNT" ]; then
  echo "WARNING: Object count mismatch!"
fi

# 2. 总大小对比
SRC_SIZE=$(rclone size aliyun-oss:migration-bucket --json | python3 -c "import sys,json;print(json.load(sys.stdin)['bytes'])")
DST_SIZE=$(rclone size huawei-obs:ai-datasets-5pb --json | python3 -c "import sys,json;print(json.load(sys.stdin)['bytes'])")
echo "Source: $SRC_SIZE bytes"
echo "Target: $DST_SIZE bytes"
DIFF=$((SRC_SIZE - DST_SIZE))
echo "Diff: $DIFF bytes"

# 3. 校验和抽样验证（随机抽取 1000 个文件）
echo "--- Checksum Sampling ---"
rclone check aliyun-oss:migration-bucket huawei-obs:ai-datasets-5pb \
  --checksum \
  --fast-list \
  --one-way \
  --sample-size 1000 \
  --log-file /var/log/migration/verify_checksum.log

# 4. SFS Turbo 文件数验证
SFS_COUNT=$(find /mnt/sfsturbo -type f | wc -l)
echo "SFS Turbo: $SFS_COUNT files"

# 5. 判断是否通过
if [ "$DIFF" -eq 0 ] && [ "$SRC_COUNT" -eq "$DST_COUNT" ]; then
  echo "[VERIFY] PASSED - Migration verified successfully"
  echo "READY FOR SWITCHOVER" > /opt/migration/verify_status
else
  echo "[VERIFY] WARNING - Size or count mismatch detected"
  echo "NEEDS INVESTIGATION" > /opt/migration/verify_status
fi
```

---

## 七、处理并发更新的特殊策略

### 7.1 正在写入的大文件（Checkpoint）

```
问题: 训练过程中每 N 步保存一次 checkpoint，文件可能数百 GB，写入过程中被截取

解决方案: 两阶段同步

1. 跳过活跃写入文件:
   通过训练框架配合，写入时使用临时后缀
   checkpoint-001.bin.writing → checkpoint-001.bin

   rclone 过滤:
   --exclude "*.writing"
   --exclude "*.tmp"
   --exclude "*.partial"

2. 只同步"完成态"文件:
   训练框架写入完成后创建 .done 标记文件
   checkpoint-001.bin + checkpoint-001.bin.done

   迁移脚本检查 .done 文件存在才同步对应的 checkpoint

3. 冻结窗口处理:
   冻结时等待最后一个 checkpoint 写入完成
   再执行最终同步
```

### 7.2 文件覆盖更新

```
问题: 同一路径的文件被覆盖更新

解决方案: rclone sync 天然处理覆盖
  - rclone sync 默认会比较源和目标
  - 源文件更新 → 自动重新传输
  - 源文件删除 → 目标也删除（sync 模式）

注意: 使用 copy 而非 sync 可以避免删除目标端多余文件（更安全）
```

### 7.3 文件删除

```
问题: 源端删除了文件，目标端是否需要同步删除

策略选择:
┌──────────────────────────────────────────────────┐
│ 策略          │ 命令         │ 适用场景           │
├──────────────────────────────────────────────────┤
│ 不同步删除    │ rclone copy  │ 保守策略，更安全    │
│ 同步删除      │ rclone sync  │ 精确一致           │
│ 延迟删除      │ 两步法(推荐) │ 平衡安全与一致性   │
└──────────────────────────────────────────────────┘

延迟删除（推荐）:
1. 增量轮次使用 rclone copy（只管新增/修改，不管删除）
2. 切换前最终轮次使用 rclone sync（一次性同步删除）
3. 好处: 避免中间轮次误删，最终一致性有保障
```

### 7.4 海量小文件优化

```
问题: AI 数据集可能有数千万个小文件(jsonl 片段)
      每个文件单独传输，API 开销巨大

优化策略:

1. 源端预打包:
   在 CPFS 挂载点将小文件打包为 tar
   find /mnt/cpfs/datasets/training -name "*.jsonl" | tar -cf - -T - | \
     split -b 10G - training_jsonl.tar.part_

   rclone sync 只传输打包后的大文件

2. 分层 rclone:
   大文件(>100MB): --transfers 8, --s3-chunk-size 64M
   小文件(<100MB): --transfers 64, --s3-chunk-size 5M, --fast-list

   rclone copy source:bucket dest:bucket \
     --max-size 100M --transfers 64 --fast-list &  # 小文件并行
   rclone copy source:bucket dest:bucket \
     --min-size 100M --transfers 8 --s3-chunk-size 64M &  # 大文件分块
   wait

3. OSS 分片列表:
   --fast-list 使用 ListObjectsV2 批量列举
   每次 1000 个对象，大幅减少 API 调用
```

---

## 八、回退方案

### 8.1 回退触发条件

```
立即回退:
- 数据校验失败（文件数/大小差异 > 0.1%）
- SFS Turbo 性能不达标（延迟 > 预期 2 倍）
- 训练任务在华为云上出现数据加载异常

延迟回退（24小时内）:
- 训练 Loss 异常
- 数据加载速度不满足训练吞吐需求
- 发现遗漏的数据文件
```

### 8.2 回退操作

```
回退步骤:
1. 停止华为云侧训练任务
2. 解冻阿里云 CPFS（remount,rw）
3. 将训练配置切回阿里云 PAI/CPFS
4. 恢复训练任务
5. 保留华为云侧数据（不删除），用于诊断和后续重试

数据安全:
- 全程使用 rclone copy（非 sync），源端删除不会影响目标端
- 华为云 OBS 开启版本控制（Versioning），覆盖也可恢复
- SFS Turbo 定期快照备份
```

---

## 九、方案总结

### 9.1 关键设计决策

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 迁移路径 | CPFS→Data Flow→OSS→rclone→OBS→SFS Turbo | 利用 CPFS 原生导出，避免 POSIX 客户端瓶颈 |
| 全量策略 | 按目录分片，多节点并行 rclone | 5PB 单节点需 74 天，16 节点可缩至 ~5 天 |
| 增量策略 | 多轮 rclone copy + 最终 rclone sync | copy 不删目标文件更安全，最终 sync 保证一致 |
| 网络方案 | 40-100 Gbps 专线 | 5PB 公网传输不可行（太慢+太贵） |
| 切换策略 | 多轮收敛+冻结窗口 | 保证数据零丢失 |
| 小文件处理 | 预打包 tar + 分层 rclone 参数 | 减少 API 开销 |

### 9.2 时间线总览

```
Day 0:     部署变更追踪 + 传输集群
Day 1-14:  Round 0 全量迁移 (5 PB)
Day 14:    Round 1 增量 (~350 TB)
Day 15:    Round 2 增量 (~2 TB)
Day 15.5:  Round 3 增量 (~260 GB)
Day 16:    Final 冻结+最终同步+校验+切换（~2小时窗口）
Day 17:    确认稳定
Day 23:    释放源端资源（保留 7 天只读备份）
```

### 9.3 成本估算

| 费用项 | 估算 |
|--------|------|
| 阿里云 OSS 流出（5.4 PB × ¥0.5/GB） | ~¥275 万 |
| 专线（40Gbps × 14 天） | 按专线合同 |
| 华为云 OMS（5 PB × ¥0.01/GB） | ~¥5 万（如使用 OMS） |
| 传输 ECS 集群（16 节点 × 14 天） | ~¥5 万 |
| SFS Turbo (250MB/s/TiB × 5PB × 1月) | ~¥600 万/月 |
| OBS 标准 (5PB × ¥0.099/GB/月) | ~¥50 万/月 |
