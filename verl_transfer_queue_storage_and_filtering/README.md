# verl TransferQueue 存储内容与筛选标准分析

> **分析对象**：verl 0.6.x 的 TransferQueue（TQ）与 ReplayBuffer 采样机制
> **代码基线**：`/root/verl_rl/verl`
> **报告日期**：2026-11-03
> **系列文章**（本篇为第五篇，数据层专题）：
> ①《verl Agent RL 训练-推理协同机制源码分析》— `verl_agent_rl_coordination_analysis`
> ②《verl Agent RL 华为云最佳实践部署架构方案》— `verl_huaweicloud_deployment_architecture`
> ③《verl Agent RL 启动逻辑与完整业务流分析》— `verl_agent_rl_startup_and_business_flow`
> ④《verl Agent RL 自提升飞轮：双环模型分析》— `verl_agent_rl_flywheel_dual_loop_model`
> **核心结论**：TransferQueue 本身是"傻存储"，真正的筛选逻辑在其上的 `ReplayBuffer`。TQ 的 **tag 就是筛选标准的载体**：`status` 决定"能不能训"（GRPO 组状态机）、`global_steps` 差值决定"还新不新鲜"（staleness ≤ 阈值个模型版本，drop 或 wait）、组内指标方差决定"值不值得训"（DAPO 全同组驱逐）、最后按最老优先 FIFO 挑满一批。被选中的组**取出即删**，每条轨迹最多被消费一次。所有 `file:line` 引用均已在代码基线核实。

---

## 目录

1. [总体架构：存储与筛选的职责分离](#1-总体架构存储与筛选的职责分离)
2. [存储结构：写侧写入的两类条目](#2-存储结构写侧写入的两类条目)
3. [筛选标准：读侧的四层漏斗](#3-筛选标准读侧的四层漏斗)
4. [驱逐-补发矩阵：sync 与 async 的差异](#4-驱逐-补发矩阵sync-与-async-的差异)
5. [选中之后的处理](#5-选中之后的处理)
6. [配置与观测速查](#6-配置与观测速查)
7. [总结](#7-总结)

---

## 1. 总体架构：存储与筛选的职责分离

TransferQueue 是 verl V1 trainer 的数据主干道（外部 KV 存储，`import transfer_queue as tq`），但它本身只做存取；**"哪些数据能进训练"的筛选逻辑全部在 `ReplayBuffer` 里**（`verl/trainer/ppo/v1/replay_buffer.py`）：

```
AgentLoopWorker（写侧）                trainer（读侧）
      │ trajectory 完成                     │ 每 step
      ▼                                     ▼
 _agent_loop_postprocess              replay_buffer.sample()
 (agent_loop_tq.py:155)               (replay_buffer.py:593)
      │                                     │
      └────► TransferQueue ◄────────────────┘
             train / val 分区      四层筛选漏斗
             （傻存储）           （状态/新鲜度/信号/顺序）
```

两个实现：`ReplayBuffer`（sync 语义，`:64`）与 `ReplayBufferAsync`（async 语义，`:514`）。

---

## 2. 存储结构：写侧写入的两类条目

TQ 有 `train` / `val` 两个分区，每个分区内有两类条目（`replay_buffer.py:86-87`）：

### 2.1 prompt 元数据条目（GRPO 组状态机）

```
key = {uid}                    ← 数据集里原始 prompt 的 uid
tag = {"status": pending → running → finished/failure,
       "global_steps": 下发时的训练步号}
```

由 `_run_prompt` 写入（`agent_loop_tq.py:117` 写 running，`:149` 写 terminal status）。**terminal 状态只在所有 session settle 后才写**（`:143` 注释：防止 ReplayBuffer 清除失败组后又有兄弟轨迹写入）。

### 2.2 轨迹条目（真正被训练的数据）

```
key = {uid}_{session_id}_{index}
      session_id = rollout.n 采样序号（GRPO 组内第几条）
      index      = 多次输出序号（一个 agent loop 产出多条时）

fields（张量数据）：
  input_ids / position_ids        ← prompt+response 拼接后的完整序列
  prompts / responses             ← 分开的原始段
  response_mask (= loss_mask)     ★ 多轮损失屏蔽：模型token=1，工具观察/padding=0
  response_logprobs               ← 生成时的对数概率
  reward_score / rm_scores        ← 奖励
  extra_fields                    ← reward_extra_info、min/max_global_steps 等
  multi_modal_inputs              ← 多模态输入

tags（筛选依据——筛选标准全在这里）：
  status: "success"
  prompt_len / response_len / seq_len
  global_steps        ← 该样本来自 dataloader 的哪个 step
  min_global_steps    ← 轨迹开始生成时的模型权重版本
  max_global_steps    ← 轨迹结束生成时的模型权重版本
```

### 2.3 写侧门槛（`agent_loop_tq.py:155-232`）

1. **必须先完成 reward 打分才写入**：`_compute_score` 在 `kv_batch_put` 之前执行——TQ 里的轨迹永远带分数；
2. **多 output 的 reward 传播**：最后一个 output 的 `reward_score` 与 `reward_extra_info` 传播给所有前置 outputs（`:177-180`）；
3. **多模态原始数据不入库**：`multi_modal_data`（原始图片/视频）被 pop 掉，只存处理后的 `multi_modal_inputs`；
4. `loss_mask` 直接等于 `response_mask`（`:190`，TODO 注释表明二者语义待统一）。

---

## 3. 筛选标准：读侧的四层漏斗

`sample()` 的筛选是一个漏斗，每层对应一类 tag：

```
TQ 全部条目
   │
   ▼ ① 状态门槛：只有 finished 组可采样
   │    _sampleable_terminal_keys (:340)
   │      = (finished ∪ failure) − (stale ∪ dapo ∪ failed)
   │    running/pending 不算数；failure 组直接驱逐（async 下无条件）
   │
   ▼ ② 新鲜度过滤（staleness / off-policy 控制）★核心标准
   │    span = 当前训练步 − prompt下发步 + 1 ≤ max_off_policy_threshold（默认 8）
   │
   ▼ ③ 学习信号过滤（DAPO filter groups）
   │    组内所有轨迹指标完全相同（std=0 且 n>1）→ 零优势 → 无学习信号 → 驱逐
   │
   ▼ ④ 选择顺序：FIFO（最老优先）
        _select_prompt_uids (:386) 按 prompt_global_steps 升序取前 batch_size
        —— 天然最小化被选样本的平均 staleness
```

### 3.1 第①层：GRPO 组状态机

只有 `finished` 状态的 prompt 组具备采样资格；`pending/running` 未完成不可选，`failure` 组在 async 模式下无条件驱逐（`_terminal_eviction_reasons` async 版返回 `set(self.failure_keys)`，`:543`）。

### 3.2 第②层：新鲜度（staleness）的精确语义

（`replay_buffer.py:520-527` 计算，`:546-554` wait 判定，`:604-610` 采样后断言）

```python
# 一条轨迹（组）最多能跨越多少个"模型版本"（= 权重同步次数）
span = global_steps_now - prompt_global_steps[uid] + 1
if span > max_off_policy_threshold:   # 默认 8
    → 触发处理策略
```

三种策略（`ppo_trainer.yaml:291-303`）：

| 策略 | 行为 | 代价 |
|---|---|---|
| **`drop`**（默认） | 超限 finished 组**驱逐**（`kv_clear`）+ `refill_fn(k)` 补发 k 条新 prompt 保持窗口满 | 丢数据（白生成），但训练不等待 |
| **`wait`**（dropless） | 只要存在 in-flight（pending/running）的 prompt 已达阈值，**阻塞采样等它完成再训**（`_has_enough_samples`，`:546-554`） | 不丢数据，训练侧可能空转 |
| `null` | 完全关闭版本控制，off-policy 程度只受生成/消费速率差约束 | 最激进 |

drop 策略采样后还有断言兜底（`:604-610`）：选出的所有组 span 必须 ≤ 阈值，否则抛错——保证不会训到超龄数据。

tag 里的 `min/max_global_steps`（轨迹**自身**经历的权重版本跨度）用于更细粒度的 staleness 观测；`global_steps`（prompt 级）才是漏斗第②层的判据。

### 3.3 第③层：DAPO 学习信号过滤

（`_dapo_filtered_keys`，`replay_buffer.py:260-312`）

```python
# 指标来源二选一（filter_groups_metric）：
#   "reward" → 读 rm_scores（每条轨迹求和）        [FILTER_GROUPS_REWARD_METRIC, :32]
#   其他字符串 → 读 extra_fields.reward_extra_info[metric]（自定义指标，如格式奖励）
if 组内轨迹数 > 1 and np.std(组内所有轨迹的指标) == 0.0:
    → 驱逐该组 + refill_fn 补发
    → 记录诊断：每个无信号组坍缩到的指标值分布（DAPO_FILTERED_REWARD_COUNTS_KEY）
```

**为什么"全同 = 无信号"**：GRPO 的优势是组内归一化的——组内 reward 全同时所有轨迹优势恒为 0，梯度为零，训练等于白训还占算力。这是 DAPO 论文 filter groups 技巧的 verl 实现。两个边界：**val 分区不做任何过滤**（`:268`，评估要全量）；指标缺失会直接抛 `RuntimeError`（`:298-303`，防止静默漏筛）。

### 3.4 第④层：FIFO 选择

`_select_prompt_uids`（`:386-395`）把可采样组按 `prompt_global_steps` **升序**排序取前 `batch_size` 个——最老优先。这不是任意的：越老的组离驱逐线越近，优先消费它们最小化平均 staleness，也最小化 drop 概率。

---

## 4. 驱逐-补发矩阵：sync 与 async 的差异

`ReplayBuffer` 类文档给出完整矩阵（`replay_buffer.py:94-114`）：

| 触发原因 | sync 模式 | async 模式 |
|---|---|---|
| staleness 超限 | **NO-OP**（同步生成天然 on-policy，不存在过期数据） | 驱逐 k，补发 k |
| DAPO 全同组 | 驱逐 k，补发 **2k**（同步下 DAPO 需先超发再筛） | 驱逐 k，补发 k |
| failure 组 | 默认不补；可选 `sync_refill_failed_groups=True` 补 k（要求 `gen_batch_size=1`） | 驱逐 k，补发 k |
| 选择时机 | `has_enough_samples` **且**（未开 DAPO 或 in-flight 全部归零）（`:486`）；DAPO 开启时超发的 surplus 直接丢弃（`:491-497`） | 凑够即选（`wait_for_sampleable` 轮询） |

async 补发只需 k 的原因（`:107-113`）：`num_warmup_batches` 预热窗口已吸收重试成本。三类驱逐原因的并集被一次性清理（`_evict_terminal_groups`，`:346-383`），保证一个 prompt 不会被处理两次。

---

## 5. 选中之后的处理

1. **取出即删**：`_materialize_batch`（`:398-409`）对选中组执行 `tq.kv_clear`——TQ 没有缓存复用，**每条轨迹最多被消费一次**（要复用需走 `skip.rollout_tq` 的 cache 动作或 `rollout_data_dir` 物化，见系列④）；
2. **组完整性**：按 uid 聚合，选中 prompt 的**所有** session/index 轨迹一起进 batch（GRPO 需要完整组做组内归一化）；
3. **轻量引用返回**：`sample()` 返回 `KVBatchMeta(partition_id, keys, tags)`，张量不落 driver 内存，后续各阶段按需 `kv_batch_get(select_fields=...)` 拉取所需字段；
4. **批次整形（非筛选）**：训练前 `_balance_batch` 做 seq_len 跨 DP 均衡 + upsample padding，补齐样本带 `is_padding` 标记且不进 metrics。

---

## 6. 配置与观测速查

```yaml
trainer.v1.sampler:                    # ppo_trainer.yaml:287-303
  max_off_policy_threshold: 8          # 轨迹可跨越的最大模型版本数；null=关闭
  max_off_policy_strategy: drop        # drop / wait
  sync_refill_failed_groups: false     # sync 模式是否补发失败组

algorithm.filter_groups:               # yaml:84，经 _resolve_filter_groups_metric 解析
  metric: reward                       # "reward"(rm_scores) 或 reward_extra_info 自定义指标名
```

**观测指标**（驱逐全量上报，非静默）：

| 指标 | 含义 |
|---|---|
| `off_policy/evicted_samples` | drop 策略驱逐的组数 |
| `off_policy/evicted_samples_staleness/{mean,max,min}` | 被驱逐组的 staleness 分布 |
| `filter_groups/evicted_samples` | DAPO 驱逐的组数 |
| `_dapo_filtered_reward_counts` | 无信号组各自坍缩到的指标值分布（诊断奖励坍缩位置） |
| `rollout_failure/evicted_samples` | 失败组驱逐数 |
| `off_policy/trajectory_{spans,staleness}*` | 全量轨迹的跨度/staleness 分布（`trainer_base.py:1984-2011`） |

---

## 7. 总结

TransferQueue 的设计哲学是**存储与策略分离**：TQ 只提供带 tag 的 KV 存取，所有筛选决策集中在 ReplayBuffer 的四层漏斗——

| 层 | 判据（tag 字段） | 问题 | 结果 |
|---|---|---|---|
| ① 状态 | `status` | 能不能训 | running/pending 不选，failure 驱逐 |
| ② 新鲜度 | `global_steps` 差值 | 还新不新鲜 | 超过 8 个版本 drop 或 wait |
| ③ 信号 | 组内指标方差 | 值不值得训 | 全同组（零优势）驱逐 |
| ④ 顺序 | `prompt_global_steps` 升序 | 先训谁 | 最老优先 FIFO |

这四层合起来回答的正是系列④提出的内环飞轮特有问题——**"活数据集"的保质期管理**：外环（静态数据集）只需要第③层（筛优质），内环（流式数据）必须再加上①②④（状态机 + 保质期 + 消费顺序）。而被驱逐的数据并非全无价值：drop 的组贡献 staleness 统计，DAPO 的组贡献奖励坍缩诊断，它们是调 `max_off_policy_threshold` 与 reward 设计的第一手信号。

---

*本报告基于 verl 0.6.x 源码静态分析生成，是"源码分析 → 云上落地"系列的第五篇（数据层专题）。*
