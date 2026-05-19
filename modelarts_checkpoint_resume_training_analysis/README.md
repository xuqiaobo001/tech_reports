# 华为云 ModelArts Standard 断点续训能力详解与使用场景分析

> 基于华为云 ModelArts 官方文档，深度分析 ModelArts Standard 训练作业的断点续训（Checkpoint Resume Training）能力，包括核心机制、两种配置方式、代码适配要点、8 大使用场景，以及不同框架的断点续训配置方法。

---

## 一、核心机制

断点续训通过 **Checkpoint 机制**实现：在模型训练过程中不断保存训练状态，训练中断后加载 Checkpoint 恢复训练。

```
┌──────────────────────────────────────────────────────────┐
│              Checkpoint 保存的内容                         │
│                                                          │
│  ① 模型权重（Model State Dict）                           │
│  ② 优化器状态（Optimizer State Dict）                     │
│  ③ 学习率调度器状态（Scheduler State）                    │
│  ④ 训练进度（Epoch / Step / Global Batch Count）          │
│  ⑤ 随机数生成器状态（RNG State）（可选）                   │
└──────────────────────────────────────────────────────────┘
```

断点续训是 ModelArts 所有故障恢复策略（原地恢复、Pod 重调度、Job 重调度、卡死重启）的**基础前提**。不配置断点续训，恢复后训练将从零开始，之前所有训练进度丢失。

---

## 二、两种配置方式

### 2.1 方式一：训练输出（train_url）

通过"训练输出"参数 `train_url` 配置，适用于旧版训练作业。

| 配置项 | 说明 |
|--------|------|
| 训练输出参数名 | `train_url` |
| 容器本地路径 | `/home/ma-user/modelarts/outputs/train_url_0` |
| 预下载至本地目录 | **必须选择"下载"** |
| 存储后端 | OBS |

**工作原理**：
```
创建训练作业：
  1. 设置 train_url → OBS 存储路径（如 obs://bucket/checkpoints/）
  2. 选择"预下载至本地目录" = "下载"
  3. 系统在训练启动前自动将 OBS 中的文件下载到容器本地

训练运行中：
  1. 训练代码将 Checkpoint 保存到容器本地路径
  2. 系统自动将本地文件同步到 OBS

训练中断后恢复：
  1. 新作业启动前 → 系统自动从 OBS 下载之前的 Checkpoint
  2. 训练代码检测到已有 Checkpoint → 加载 → 从断点继续
```

### 2.2 方式二：存储挂载（推荐）

通过"存储挂载"功能配置，支持 **OBS 和 SFS Turbo**。

| 配置项 | 说明 |
|--------|------|
| 存储类型 | OBS 或 SFS Turbo |
| 挂载方式 | 在训练作业中配置存储挂载 |
| 本地路径 | 用户自定义的容器内挂载路径 |
| 数据同步 | 实时同步（SFS Turbo）/ 异步同步（OBS） |

**SFS Turbo vs OBS 对比**：

| 维度 | OBS 存储 | SFS Turbo 存储 |
|------|---------|---------------|
| 数据同步 | 异步上传，可能丢失最近 CKPT | 实时写入，不丢失 |
| CKPT 读取 | 作业启动前批量下载 | 直接读取（已挂载） |
| 恢复速度 | 较慢（需等待下载） | 快（无需下载） |
| 成本 | 低 | 较高 |
| 推荐场景 | 低频 CKPT 保存 | 大模型高频 CKPT 保存 |

---

## 三、代码适配要点

### 3.1 PyTorch 标准 Checkpoint 保存与加载

```python
import os
import torch

# 获取训练输出路径
train_url = os.environ.get('train_url', '/home/ma-user/modelarts/outputs/train_url_0')

def save_checkpoint(model, optimizer, epoch, step, path):
    """定期保存 Checkpoint"""
    checkpoint = {
        "net": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "epoch": epoch,
        "step": step
    }
    torch.save(checkpoint, os.path.join(path, f'ckpt_step_{step}.pth'))

def load_latest_checkpoint(model, optimizer, path):
    """加载最新 Checkpoint，返回起始 epoch"""
    if not os.path.exists(path) or not os.listdir(path):
        return 0  # 从头训练

    ckpts = sorted([f for f in os.listdir(path) if f.endswith('.pth')])
    if not ckpts:
        return 0

    latest = os.path.join(path, ckpts[-1])
    checkpoint = torch.load(latest)
    model.load_state_dict(checkpoint['net'])
    optimizer.load_state_dict(checkpoint['optimizer'])
    return checkpoint['epoch']

# 训练循环
start_epoch = load_latest_checkpoint(model, optimizer, train_url)
for epoch in range(start_epoch, total_epochs):
    for step, batch in enumerate(dataloader):
        # 训练逻辑 ...
        if step % save_interval == 0:
            save_checkpoint(model, optimizer, epoch, step, train_url)
```

### 3.2 利用环境变量判断恢复类型

```python
import os

schedule_cnt = int(os.environ.get('MA_SCHEDULE_CNT', '1'))   # Pod 调度次数
proc_start_cnt = int(os.environ.get('MA_PROC_START_CNT', '1'))  # 进程启动次数

if schedule_cnt > 1:
    # 发生过 Job 级重调度 / Pod 重调度
    # → 容器是全新的，必须从 Checkpoint 恢复
    start_epoch = load_checkpoint(model, optimizer, ckpt_dir)

if proc_start_cnt > 1:
    # 发生过原地恢复 / 卡死重启
    # → 容器环境保留，但进程重启了
    # → 需要重建共享内存、跳过数据预处理
    rebuild_shared_memory()
    start_epoch = load_checkpoint(model, optimizer, ckpt_dir)
```

| 环境变量 | 初始值 | 何时 +1 | 用途 |
|----------|--------|---------|------|
| `MA_SCHEDULE_CNT` | 1 | 每次 Pod 重调度 | 判断是否发生过 Job/Pod 级恢复 |
| `MA_PROC_START_CNT` | 1 | 每次原地恢复/卡死重启 | 判断是否需要跳过数据加载/重建共享内存 |

### 3.3 Checkpoint 保存策略建议

| 训练类型 | 保存频率 | 保留数量 | 说明 |
|----------|---------|---------|------|
| 大模型预训练 | 每 100~1000 Step | 最近 3~5 个 | CKPT 文件大（数十 GB），避免存储溢出 |
| SFT 微调 | 每 1 Epoch | 最近 3~5 个 | 数据量相对小，保存频率可低 |
| RL 训练 | 每 50~200 Step | 最近 5~10 个 | 需更频繁保存以减少回滚损失 |
| 调试阶段 | 每 10~50 Step | 最近 2~3 个 | 快速迭代，频繁保存 |

---

## 四、8 大使用场景

### 场景 1：故障自动恢复（最核心场景）

**触发条件**：NPU 芯片故障、节点宕机、作业卡死等

```
配置要求：
  ✅ 代码中定期保存 Checkpoint
  ✅ 代码支持 reload CKPT
  ✅ 开启"自动重启"
  ✅ 训练输出选择"预下载至本地目录" = "下载"

恢复流程：
  故障发生 → 平台检测 → 自动恢复策略（原地/Pod/Job级）
  → 新作业启动 → 系统自动下载 CKPT
  → 代码检测并加载 → 从断点继续训练
```

### 场景 2：主动停止后恢复

**触发条件**：用户主动停止训练作业（如发现配置有误需要调整）

```
操作流程：
  1. 停止当前训练作业（CKPT 已保存在 OBS/SFS Turbo）
  2. 修改训练配置（如调整超参、修改数据路径）
  3. 创建新训练作业，挂载同一存储路径
  4. 新作业启动时自动下载已有 CKPT → 从断点继续
```

### 场景 3：增量训练（Incremental Training）

**触发条件**：在已训练模型基础上，用新数据继续训练，扩展模型能力

```
第一次训练（基础能力训练）：
  数据集 A → 训练 100 Epoch → 保存最终 CKPT

第二次训练（增量扩展）：
  加载 CKPT（epoch=100 的权重）
  → 数据集 B → 继续训练 50 Epoch
  → 模型同时具备数据集 A 和 B 的能力

与断点续训的区别：
  断点续训：同一数据集，同一超参，中断后继续
  增量训练：可能换数据集，可能调超参，扩展模型能力
  → 底层机制相同：加载 CKPT 继续训练
```

### 场景 4：超参调整后续训

**触发条件**：训练过程中发现学习率过大/过小，需要调整超参

```
操作流程：
  1. 观察训练曲线，发现 loss 震荡或收敛过慢
  2. 停止训练（CKPT 已保存）
  3. 调整学习率、batch size 等超参
  4. 创建新训练作业，从最近 CKPT 继续训练

注意：
  → 调整超参后续训时，优化器状态是否需要重置取决于调整幅度
  → 小幅调整：保留优化器状态（如 momentum）
  → 大幅调整：重置优化器状态，只加载模型权重
```

### 场景 5：长时训练保障

**触发条件**：大模型预训练/SFT 持续数天甚至数周

```
长时训练的断点续训保障：

  ┌─ Checkpoint 保存 ────────────────────────────┐
  │  频率：每 500~1000 Step                       │
  │  保留：最近 5 个 CKPT                         │
  │  存储：SFS Turbo（实时同步，避免 CKPT 丢失）   │
  └──────────────────────────────────────────────┘

  ┌─ 容错配置 ───────────────────────────────────┐
  │  自动重启：开启（8~128次）                     │
  │  无条件自动重启：开启                          │
  │  卡死重启：开启                                │
  │  Pod 重调度：开启（3次）                       │
  └──────────────────────────────────────────────┘

  ┌─ 监控告警 ───────────────────────────────────┐
  │  CKPT 保存失败 → 告警                         │
  │  存储空间不足 → 告警                          │
  │  训练 loss 不下降 → 告警                      │
  └──────────────────────────────────────────────┘
```

### 场景 6：RL 训练多阶段恢复

**触发条件**：强化学习训练中 Actor/Critic/Reward 等角色需独立保存/恢复

```
RL 训练需保存的内容（比普通 SFT 更复杂）：

  ┌─ Actor 模型 ─────┐   ┌─ Critic 模型 ────┐
  │  模型权重          │   │  模型权重          │
  │  优化器状态        │   │  优化器状态        │
  │  调度器状态        │   │  调度器状态        │
  └───────────────────┘   └───────────────────┘

  ┌─ Reference Model ─┐   ┌─ Reward Model ───┐
  │  原始模型权重      │   │  模型权重（如有）  │
  │  （不可变，仅推理）│   │                   │
  └───────────────────┘   └───────────────────┘

  ┌─ 训练状态 ────────────────────────────────┐
  │  当前 Step / Episode 计数                   │
  │  KL 散度累计值                              │
  │  全局 Batch 计数                            │
  └────────────────────────────────────────────┘

恢复时需确保：
  → 各角色 CKPT 的 Step 编号一致
  → Reference Model 保持不变
  → 训练计数器正确恢复
```

### 场景 7：资源抢占恢复

**触发条件**：公共资源池作业被高优先级作业抢占

```
资源抢占场景：
  公共池 → 提交了低优先级训练作业 A
  → 高优先级作业 B 到达 → A 被驱逐
  → B 完成后 A 重新排队启动
  → A 从 CKPT 恢复训练

注意：
  → 作业被驱逐时不触发"无条件 Job 重调度"
  → 需要配合自动重启实现恢复
  → 建议使用专属资源池避免抢占
```

### 场景 8：训练作业卡死恢复

**触发条件**：训练进程 IO 30 分钟无变化

```
卡死检测 → 卡死重启流程：
  1. 协程检测到 IO 无变化超过 MA_HANG_DETECT_TIME（默认30min）
  2. 强制停止容器中的用户进程（容器不销毁）
  3. 重新运行训练作业启动命令
  4. 代码检测到 MA_PROC_START_CNT > 1 → 加载 CKPT → 继续训练

连续 3 次卡死重启后 → 作业终止为失败
```

---

## 五、不同框架的断点续训配置

### 5.1 PyTorch（原生）

```python
# 保存
checkpoint = {
    "net": model.state_dict(),
    "optimizer": optimizer.state_dict(),
    "epoch": epoch
}
torch.save(checkpoint, os.path.join(train_url, f'ckpt_{epoch}.pth'))

# 加载
if os.listdir(train_url):
    ckpts = sorted([f for f in os.listdir(train_url) if f.endswith('.pth')])
    latest = os.path.join(train_url, ckpts[-1])
    checkpoint = torch.load(latest)
    model.load_state_dict(checkpoint['net'])
    optimizer.load_state_dict(checkpoint['optimizer'])
    start_epoch = checkpoint['epoch'] + 1
```

### 5.2 VeRL 框架

修改训练配置 YAML 文件：

```yaml
# 从指定断点续训
backend_config:
  trainer:
    resume_mode: resume_path
    resume_path: /path/to/checkpoint_dir

# 或从最新的断点续训
backend_config:
  trainer:
    resume_mode: latest_checkpoint
```

### 5.3 LLaMA-Factory 框架

操作步骤：
1. 进入训练结果输出目录
2. 获取指定 checkpoint 目录路径
3. 修改训练配置 YAML 中的断点续训配置
4. 重新启动训练任务

### 5.4 MindSpeed-LLM / MindSpeed-RL / MindSpeed-MM

操作步骤：
1. 确认存在已保存的权重文件
2. 修改训练配置 YAML 文件中的断点续训配置
3. 重新启动训练任务

### 5.5 各框架配置对比

| 框架 | 配置方式 | 关键参数 | 复杂度 |
|------|---------|---------|--------|
| **PyTorch（原生）** | 代码中实现 | `torch.save()` / `torch.load()` | 中（需自行实现） |
| **VeRL** | YAML 配置 | `resume_mode` + `resume_path` | 低 |
| **LLaMA-Factory** | YAML 配置 | 修改断点续训参数 | 低 |
| **MindSpeed-LLM** | YAML 配置 | `load-checkpoint` 相关参数 | 低 |
| **MindSpeed-RL** | YAML 配置 | `resume_mode` + `resume_path` | 低 |
| **MindSpeed-MM** | YAML 配置 | 断点续训配置项 | 低 |

---

## 六、断点续训与故障恢复的关系

```
┌──────────────────────────────────────────────────────────────┐
│          断点续训是所有故障恢复策略的基础                       │
│                                                                │
│  ┌───────────┐    ┌───────────┐    ┌───────────────────┐   │
│  │ 原地恢复   │    │ Pod 重调度 │    │ Job 级重调度       │   │
│  │ (进程级)  │    │ (Pod级)   │    │ (Job级)           │   │
│  └─────┬─────┘    └─────┬─────┘    └─────────┬─────────┘   │
│        │                │                     │              │
│        └────────────────┼─────────────────────┘              │
│                         ↓                                     │
│              ┌─────────────────────┐                          │
│              │  加载 Checkpoint    │ ← 断点续训               │
│              │  恢复训练进度       │                          │
│              └─────────────────────┘                          │
│                                                                │
│  没有 Checkpoint → 恢复后从零开始 → 之前所有训练进度丢失       │
│  有 Checkpoint   → 恢复后从断点继续 → 只丢失最后一次保存后的   │
│                                         训练进度              │
└──────────────────────────────────────────────────────────────┘
```

---

## 七、最佳实践总结

### 7.1 必须满足的前提条件

| 序号 | 条件 | 说明 |
|------|------|------|
| 1 | 代码定期保存 CKPT | 每隔一定 Step 或 Epoch 保存 |
| 2 | 代码支持 reload CKPT | 检测已有 CKPT 并加载 |
| 3 | 配置"预下载至本地目录" = "下载" | 旧版 train_url 方式必须选择 |
| 4 | 脚本可重入 | 跳过数据下载/预处理，重建共享内存等 |

### 7.2 推荐配置组合

| 场景 | 存储选择 | 保存频率 | 自动重启 |
|------|---------|---------|---------|
| 大模型预训练 | SFS Turbo | 每 500 Step | 8~128 次 |
| SFT 微调 | OBS/SFS Turbo | 每 1 Epoch | 3~8 次 |
| RL 训练 | SFS Turbo | 每 100 Step | 8~128 次 |
| 调试阶段 | OBS | 每 50 Step | 1~3 次 |

---

## 参考文档

- [设置断点续训练 — 华为云官方文档](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0023.html)
- [训练作业故障恢复](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/develop-modelarts-0019.html)
- [训练作业容错检查](https://support.huaweicloud.com/intl/zh-cn/usermanual-standard-modelarts/modelarts_trouble_0003.html)
- [断点续训最佳实践（LLM 大模型训练）](https://support.huaweicloud.com/bestpractice-modelarts/modelarts_llm_train_590616.html)
- [VeRL 断点续训配置](https://www.huaweicloud.com/guide/productsdesc-bms_6b9a0aa2f36be76b48152eedbdd4263asupport2)
- [LLaMA-Factory 断点续训配置](https://www.huaweicloud.com/guide/productsdesc-bms_6b9a0aa2f36be76b48152eedbdd4263asupport1)
- [MindSpeed-MM 断点续训配置](https://www.huaweicloud.com/guide/productsdesc-bms_6b9a0aa2f36be76b48152eedbdd4263asupport4)
