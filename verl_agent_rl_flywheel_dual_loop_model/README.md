# verl Agent RL 自提升飞轮：双环模型分析

> **分析对象**：verl 0.6.x（V1 Trainer + engine_workers + agent_loop 架构）
> **代码基线**：`/root/verl_rl/verl`
> **报告日期**：2026-11-03
> **系列文章**（本篇为第四篇，视角层——统一前三篇）：
> ①《verl Agent RL 训练-推理协同机制源码分析》— `verl_agent_rl_coordination_analysis`（机制层）
> ②《verl Agent RL 华为云最佳实践部署架构方案》— `verl_huaweicloud_deployment_architecture`（部署层）
> ③《verl Agent RL 启动逻辑与完整业务流分析》— `verl_agent_rl_startup_and_business_flow`（运行层）
> **本篇定位**：从"自提升飞轮"这一 Agent RL 的统一抽象出发，论证经典的外环迭代（推理产数据 → 训练 → 再推理）与 verl 的内环在线 RL 是同一个循环的两档转速，并给出两者精确的机制映射与切换开关。所有 `file:line` 引用均已在代码基线核实。

---

## 目录

1. [自提升飞轮：Agent RL 的统一骨架](#1-自提升飞轮agent-rl-的统一骨架)
2. [逐步映射：经典三步在 verl 里的精确对应](#2-逐步映射经典三步在-verl-里的精确对应)
3. [认知升级点一：循环周期压缩（任务级 → step 级）](#3-认知升级点一循环周期压缩任务级--step-级)
4. [认知升级点二："优质数据集"的本质差异](#4-认知升级点二优质数据集的本质差异)
5. [verl 对解耦形态的显式支持：三件套](#5-verl-对解耦形态的显式支持三件套)
6. [三种 trainer_mode = 飞轮的三种耦合粒度](#6-三种-trainer_mode--飞轮的三种耦合粒度)
7. [华为云上的双环落位](#7-华为云上的双环落位)
8. [总结](#8-总结)

---

## 1. 自提升飞轮：Agent RL 的统一骨架

Agent RL 的核心直觉可以抽象为一个三步循环：

```
      ┌─────────────────────────────────────────┐
      │                                         │
      ▼                                         │
 ① 基于推理形成一个优质的训练数据集          ③ 模型训练完，继续推理生成优质数据
   （rollout + 筛选/打分）                     │
      │                                         │
      ▼                                         │
 ② 把训练数据集给模型训练 ◄────────────────────┘
   （policy update）
```

**推理引擎是数据生产者，训练引擎是数据消费者，权重是循环的载体。**这一骨架从早期的 STaR（自举推理微调）、RFT（拒绝采样微调）、best-of-n 蒸馏，到在线 GRPO/PPO 一脉相承——区别只在于循环的转速与耦合方式。

verl 的四大协同支柱（系列①的结论：时序协同、显存协同、权重协同、数据协同）全部是为把这个循环转起来服务的：

| 支柱 | 服务于飞轮的哪个环节 |
|---|---|
| 时序协同（三种 trainer_mode） | 控制 ① 与 ② 的重叠程度（飞轮转速档位） |
| 显存协同（sleep/wake 分时） | 让 ① 的推理引擎和 ② 的训练引擎共享同一批 NPU |
| 权重协同（update_weights 单通道） | ③ 的实现：新权重无歧义地回到推理引擎 |
| 数据协同（TQ + staleness） | ① 产出的数据以受控新鲜度流向 ② |

---

## 2. 逐步映射：经典三步在 verl 里的精确对应

| 经典框架 | verl 的实现 | 代码位置 |
|---|---|---|
| **① 基于推理形成优质训练数据集** | agent loop 持续产生多轮轨迹 → reward 打分 → 写入 TransferQueue；replay buffer 中的"数据集" = **带 staleness 标签的活数据流**，永不物化成静态文件 | 流式入队：`agent_loop_tq.py:155`；活数据集：`replay_buffer.py:64-514` |
| "优质"的判定 | 不是静态筛选（只留好的），而是 **GRPO 组内相对优势**：同一 prompt 的 n 条轨迹互为参照，advantage 归一化后天然实现"好轨迹正向加权、差轨迹负向加权"；DAPO 再过滤全同分组（零学习信号组直接驱逐） | 优势计算：`core_algos.py`；DAPO 过滤：`replay_buffer.py:261`（`_dapo_filtered_keys`） |
| **② 把数据集给模型训练** | `_step_once` 九阶段：old_log_prob → ref → value → **advantage（把"奖励"转化为"优质度权重"）** → critic/actor 更新 | `trainer_base.py:562-612` |
| **③ 训练完继续推理** | `update_weights` 把新权重灌回推理引擎 → `resume_generation_replicas` 恢复生成——**权重版本号 +1，循环回到 ①** | `engine_workers.py:789`；恢复生成：`checkpoint_engine/base.py:487` |

一句话：**verl 没有改变循环的结构，改变的是循环的周期与数据的形态。**

---

## 3. 认知升级点一：循环周期压缩（任务级 → step 级）

经典描述（①→②→③→① 每转一圈 = 一次独立作业，天级）是**外环飞轮**；verl 默认跑的是**内环飞轮**（每转一圈 = 一个 training step，分钟级）。两者是嵌套关系，不是替代关系：

```
外环（任务级，天）：
  checkpoint_N ─→ 独立生成作业 ─→ 聚合/筛选数据集 ─→ 独立训练作业 ─→ checkpoint_N+1
                   （①物化）        （静态数据集）        （②独立）
  └── verl 支持形态：rollout_data_dir 落盘 + skip.rollout_tq + 独立作业编排 ──┘

内环（step 级，分钟）：
  prompt ─→ 轨迹流（TQ） ─→ 凑批训练 ─→ update_weights ─→ 新权重推理
  └────────────── 每个 global_step 转一圈 ──────────────┘
```

内环之所以可行，靠两个机制兜底——它们是外环不存在、内环特有的问题：

1. **数据新鲜度（staleness）控制**：外环的数据集是静态的，无所谓新鲜度；内环里轨迹产生时的权重版本和消费时的权重版本可能差好几步。verl 的 `max_off_policy_threshold` + `drop`/`wait` 策略（`replay_buffer.py:520-564`）就是内环的"数据保质期"；staleness 指标 `training/off_policy/trajectory_staleness*` 持续观测（`trainer_base.py:1984-2011`）。
2. **π_old 基准重算**：权重漂移后，训练前重算 `old_log_probs`（`trainer_base.py:1645-1704`）保证重要性比值基准正确；`parameter_sync_step` 期间用 CPU 快照固定 π_old（`trainer_separate_async.py:201-225`），支持一个 rollout batch 支撑 N 次本地更新（Decoupled PPO）。

---

## 4. 认知升级点二："优质数据集"的本质差异

| 维度 | 经典外环（拒绝采样式） | verl 内环（在线 RL） |
|---|---|---|
| 数据形态 | 静态筛选后的"优质子集" | 全量轨迹 + 优势权重（好样本梯度为正、差样本梯度为负） |
| "优质"定义 | 绝对阈值（reward 过线才保留） | 组内相对（比同 prompt 的兄弟轨迹好就行） |
| 数据利用率 | 丢弃差样本 | 差样本贡献负梯度信号 |
| 侧重点 | 数据质量 | 探索覆盖度（差样本防止策略坍缩到局部最优） |
| 失败模式 | 筛选偏差导致分布收窄 | off-policy 过深导致训练不稳定（由 staleness 控制兜底） |

**本质**：经典外环把"优质"编码在**数据选择**里；在线内环把"优质"编码在**梯度权重**里。前者是数据集工程，后者是优化问题——verl 的 `compute_advantage` 阶段（`trainer_base.py:599`）正是从"奖励信号"到"优质度权重"的转换器。

另外，多轮 Agent 轨迹比单轮多一层"过程优质性"：`response_mask` 只对模型生成的 token（含工具调用语法）计梯度，工具观察 token（mask=0）被屏蔽（`tool_agent_loop.py:386`，`ray_trainer.py:1588-1592`）——"优质"只在模型可自主决定的 token 上定义，环境反馈不参与。

---

## 5. verl 对解耦形态的显式支持：三件套

verl 没有把用户锁死在内环，代码里有三个显式开关支撑外环（解耦）形态：

### ① `trainer.rollout_data_dir` —— 轨迹物化（① 的产物落盘）

`trainer_base.py:507-509`：每个 step 的轨迹可落盘成数据集文件（`_log_rollout_data`，`:1308`），离线用于 SFT/RFT/蒸馏，或喂给下一轮训练——这就是经典框架里"形成训练数据集"这一步的物化产物。

### ② `skip.rollout_tq.enable` —— 跳过推理，直接消费已有数据（② 独立成步）

`ppo_trainer.yaml:458-475`（SkipManager，`verl/utils/skip/`）：**跳过 rollout 阶段，直接消费 TransferQueue 里已有的数据训练**，支持 `cache` / `repeat` 两种 SkipAction。生成作业与数据消费作业彻底分离，还可以"训 N 步缓存数据、再恢复在线生成"做混合节奏——内环外环随意切换。

### ③ checkpoint 体系 —— 跨作业循环的接力棒（③ 独立成作业）

checkpoint 保存的不只是模型权重，还包括 dataloader 状态、TransferQueue 状态、replay buffer 状态（`trainer_base.py:999-1008`）。恢复时 `_reissue_inflight_prompts`（`:910-946`）重发在途 prompt，warmup 去重（`:637-644`）避免重复生成。这使 ③ 可以是"另一个独立作业"：训练作业产出 checkpoint 到共享存储 → 评估/生成作业加载它继续推理。

---

## 6. 三种 trainer_mode = 飞轮的三种耦合粒度

系列①分析的三种 trainer 模式，从飞轮视角看就是**同一循环的三档耦合度**：

| 模式 | 飞轮语义 | 循环特征 |
|---|---|---|
| **sync** | 最接近字面意义的 ①→②→③→①：完整生成一批 → 完整训练一批 → 换权重再来 | 严格串行，完全 on-policy，一圈 = 一个 step |
| **colocate_async** | ①③ 在后台持续跑，② 定期消费——**数据集变成流** | 一圈内 overlap，一步 off-policy |
| **separate_async** | ① 的算力独立扩缩（常驻推理池 + 弹性 hybrid），与 ② 的资源配比动态调整 | 飞轮两侧转速独立可调，多步 off-policy |

选择依据：任务越难（轨迹越长、工具越多），生成侧耗时占比越大，越值得往 coupled-async 形态迁移，用重叠掩盖生成延迟。

---

## 7. 华为云上的双环落位

结合系列②的部署架构：

| 飞轮形态 | 云上形态 |
|---|---|
| **内环**（单作业闭环） | 全部在一个 Ray 集群内：CPU 池轨迹流 + Ascend 池训推分时（hybrid）；checkpoint 落 SFS-Turbo |
| **外环**（多作业接力） | checkpoint（SFS-Turbo）是跨作业循环的接力棒：`rollout_data_dir` 轨迹物化 + `skip.rollout_tq` 支持"生成作业 / 训练作业"分离编排，天然适配 CCE 上多 Job 分阶段调度（Job A 生成 → Job B 训练 → Job A' 再生成） |
| **混合节奏** | 内环为主 + 定期外环采样评估（validation job 加载最新 checkpoint），两种节奏共存 |

外环的一个典型增值场景：训练中按 save_freq 落盘 checkpoint → 用独立推理作业跑大规模 best-of-n 评估/数据蒸馏 → 产出物化数据集回喂（RFT 混合）——这正是三件套设计的用武之地。

---

## 8. 总结

**经典外环与 verl 内环是同一个自提升飞轮的两档转速：**

- 循环结构不变（推理产数据 → 训练 → 新权重再推理），变的是**周期**（天级 → 分钟级）与**数据形态**（物化静态数据集 → 带新鲜度标签的活数据流）；
- "优质"的编码位置从**数据选择**（拒绝采样）移到**梯度权重**（组内相对优势），数据利用率与探索覆盖度同时提升；
- 内环引入外环没有的新问题（数据新鲜度），由 staleness 控制与 π_old 重算兜底；
- verl 用三件套（`rollout_data_dir` / `skip` / checkpoint）保留随时拆回外环的能力——**内环是性能优化，外环是工程兜底，两者按任务特性自由组合**。

从系列①的协同机制、②的云上部署、③的启动与业务流，到本篇的飞轮视角，四篇报告构成完整闭环：**机制层回答"怎么协同"，部署层回答"跑在哪"，运行层回答"何时发生"，视角层回答"为什么转"**。

---

*本报告基于 verl 0.6.x 源码静态分析生成，是"源码分析 → 云上落地"系列的第四篇（完）。*
