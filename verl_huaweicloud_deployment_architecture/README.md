# verl Agent RL 华为云最佳实践部署架构方案

> **方案对象**：verl 0.6.x（V1 Trainer + engine_workers + agent_loop 架构）部署至华为云
> **涉及云服务**：CCE（昇腾训练集群）、AgentSphere（通算 sandbox 工具执行环境）、SFS-Turbo（共享文件存储）、VPC / ECS / EIP / VPC-EP（网络与运维）
> **报告日期**：2026-11-03
> **关联报告**：《verl Agent RL 训练-推理协同机制源码分析》（同仓库 `verl_agent_rl_coordination_analysis`）
> **核心结论**：把 verl 源码中"NPU 上训推分时共置、CPU 上轨迹流水、外置沙箱执行工具"的协同设计，原样映射为"**CCE Ascend 池 + CCE CPU 池 + AgentSphere**"三段式云拓扑；用 SFS-Turbo 承载唯一事实源（checkpoint），用 VPC-EP 把所有高频调用压在私网。

---

## 目录

1. [设计输入：从协同机制推导资源需求](#1-设计输入从协同机制推导资源需求)
2. [总体架构图](#2-总体架构图)
3. [服务职责映射表](#3-服务职责映射表)
4. [网络设计](#4-网络设计)
5. [计算层设计：三种 trainer_mode 对应三档拓扑](#5-计算层设计三种-trainer_mode-对应三档拓扑)
6. [存储层设计：SFS-Turbo 目录规划](#6-存储层设计sfs-turbo-目录规划)
7. [AgentSphere sandbox 集成](#7-agentsphere-sandbox-集成)
8. [关键 verl 配置到云资源映射](#8-关键-verl-配置到云资源映射)
9. [部署流程](#9-部署流程)
10. [最佳实践清单](#10-最佳实践清单)

---

## 1. 设计输入：从协同机制推导资源需求

verl 的训练-推理协同机制（详见关联报告）包含四大支柱，每一条都直接决定云上资源形态：

| verl 协同支柱 | 源码依据 | 对云资源的要求 |
|---|---|---|
| **时序协同**（sync / colocate_async / separate_async 三档重叠度） | `trainer/ppo/v1/trainer_base.py:2014` 注册表 | 训练与推理是否分池、池间是否需要网络直连 |
| **显存协同**（sleep/wake 分时复用） | `workers/engine_workers.py:487-748` hybrid engine | **训练与推理共置同一批 NPU**（同进程），必须整卡调度、不能虚拟化切分 |
| **权重协同**（update_weights 单通道） | naive 进程内直灌 / HCCL checkpoint engine（`checkpoint_engine/hccl_checkpoint_engine.py:103` 以 `"nccl"` 别名注册） | 共置时零额外网络；分离时训练池↔推理池需高带宽直通（HCCL over RoCE） |
| **数据协同**（agent loop CPU 轨迹流 + TransferQueue） | `experimental/agent_loop/agent_loop.py:1204`，CPU Ray actor | **大量廉价 CPU 算力**跑 asyncio 循环与工具调用，与 NPU 解耦 |
| Agent 工具执行（sandbox） | `verl/tools/base_tool.py:24` 生命周期接口 | 外部沙箱服务，经**私网低延迟**调用 |

**结论**：NPU 池（CCE Ascend 节点）只做两件事——训练与共置推理；CPU 池承担 driver、agent loop、reward、TransferQueue；工具执行外置到 AgentSphere。这是整个架构的骨架。

### verl 昇腾支持现状（方案可行性依据）

以下事实全部来自 verl 官方仓库（0.6.x 代码基线）：

- **官方昇腾镜像直接基于华为云 SWR**：`docker/ascend/Dockerfile.ascend_9.1.0_a2` 的基础镜像即 `swr.cn-south-1.myhuaweicloud.com/ascendhub/cann:...`，内含 CANN 9.x + torch-npu + vllm-ascend + MindSpeed + Megatron-LM + mbridge；
- **硬件支持**（`docs/ascend_tutorial/zh/get_start/quick_start.rst`）：Atlas 200T A2 Box16、Atlas 900 A2 PoD、Atlas 800T A3（另有 A5/950 镜像 tag）；A3 每卡 2 Die，需设 `n_gpus_per_node=16`；
- **设备自动识别**：GPU 脚本在昇腾上运行自动识别 NPU 设备，原则上无需显式设置 `trainer.device=npu`；
- **推理后端**：vllm-ascend 与 SGLang 双后端均有官方镜像（`docker/ascend/supported_tags.md`）；
- **分离式权重同步**：HCCL checkpoint engine 在 NPU 上以 `"nccl"` 后端名自动路由（`hccl_checkpoint_engine.py:103`）；分离传输后端白名单含 `"ascend"`（`workers/config/disaggregation.py:21`）。

---

## 2. 总体架构图

```
                          ┌────────────────── 公网 ──────────────────┐
                          │  运维入口(SSH/Console)   HF/pip(依赖下载)  │
                          └───────┬────────────────────┬─────────────┘
                                  │ EIP                │ EIP
                          ┌───────▼──────┐    ┌───────▼────────┐
                          │ ECS 管理节点  │    │ NAT Gateway    │
                          │ (跳板/作业提交)│    │ (集群出公网共享) │
                          └───────┬──────┘    └───────┬────────┘
                                  │                   │
═══════════════════════ VPC 10.0.0.0/16（纯私网训练面）══════════════════════
│                                                                                                │
│  Subnet-管理 10.0.32.0/24        Subnet-训练 10.0.0.0/19          Subnet-推理 10.0.64.0/20     │
│  ┌─────────────────┐   ┌──────────────────────────────┐   ┌───────────────────────┐         │
│  │ ECS 跳板机       │   │ CCE 节点池①：Ascend 训推池     │   │ CCE 节点池③：Ascend    │         │
│  └─────────────────┘   │ Atlas 800T A3 (8卡/节点)     │   │ 常驻推理池(可选,L3)     │         │
│                        │ ┌──────────────────────────┐ │   │ standalone rollout    │         │
│  ┌─────────────────┐   │ │ Ray Worker Pods          │ │   │ replicas + 弹性hybrid  │         │
│  │ CCE 节点池②：CPU  │   │ │ ActorRolloutRefWorker   │ │   │ (separate_async)      │         │
│  │ 通用型 x86       │   │ │ = FSDP/Megatron(训练)    │ │   └───────────┬───────────┘         │
│  │ ┌─────────────┐ │   │ │ + vLLM-Ascend/SGLang    │ │               │                     │
│  │ │Ray Head Pod │ │   │ │   (rollout, hybrid同进程)│ │               │ HCCL 权重同步        │
│  │ │=driver单控  │ │   │ └──────────────────────────┘ │◀──────────────┘ (RoCE参数面)         │
│  │ │TransferQueue│ │   │  Volcano gang-sched +       │                                     │
│  │ ├─────────────┤ │   │  Ascend Device Plugin       │   SFS-Turbo (RWX PVC)                │
│  │ │AgentLoop    │ │   └──────────────┬───────────────┘   /mnt/sfs/{models,data,ckpt}      │
│  │ │Workers×N    │─┼──── OpenAI协议 ──┘                    ▲           ▲                     │
│  │ ├─────────────┤ │        K8s Service                    │           │                     │
│  │ │RewardLoop   │ │   ┌──────────────────────────────┐   ┌───────────┴──────────┐          │
│  │ │Workers      │ │   │ CCE 双平面网络                │   │ 业务面: VPC(存储/工具) │          │
│  │ └─────────────┘ │   │ · 业务面: VPC 子网            │   │ 参数面: RoCE(HCCL通信)│          │
│  └────────┬────────┘   │ · 参数面: Atlas服务器 RoCE    │   └──────────────────────┘          │
│           │            └──────────────────────────────┘                                     │
│           │ VPC-EP (私网)                                                                    │
│  ┌────────▼───────────────────────┐      ┌────────────────────────────┐                    │
│  │ VPC Endpoint → AgentSphere     │      │ VPC Endpoint → SWR/OBS     │                    │
│  │ (通算 sandbox: 代码执行/搜索/   │      │ (私网拉镜像/冷数据归档)      │                    │
│  │  浏览器等 agent 工具运行时)     │      └────────────────────────────┘                    │
│  └────────────────────────────────┘                                                          │
════════════════════════════════════════════════════════════════════════════════════════════════
```

**四条数据流**：

- **控制流**：CPU 池的 AgentLoopWorker（asyncio，每样本一个协程）→ OpenAI 兼容协议 → NPU 池的 rollout 服务生成下一轮；
- **工具流**：AgentLoopWorker → VPC-EP → AgentSphere sandbox 执行工具 → 观察结果回流拼入 token 流；
- **轨迹流**：轨迹完成瞬间写入 Ray Head 的 TransferQueue → 训练侧凑批消费；
- **权重流**：训练 step 结束 `update_weights`——共置模式进程内直灌，分离模式走 HCCL checkpoint engine。

---

## 3. 服务职责映射表

| verl 组件（源码位置） | 华为云承载 | 规格/说明 |
|---|---|---|
| driver / single-controller（`ray_trainer` fit 循环） | CCE **CPU 节点池** Ray Head Pod | 2×8 vCPU 起；TransferQueue 常驻内存，建议 64GB+ |
| ActorRolloutRefWorker 训练引擎（FSDP-torch_npu / MindSpeed-Megatron） | CCE **Ascend 节点池①** | Atlas 800T A3 / 900 A2 PoD 裸金属加入节点池，整卡调度 |
| rollout 引擎（vllm-ascend / SGLang NPU） | 同上，**同进程 hybrid**（`workers/rollout/replica.py:126`） | 与训练引擎分时复用同一批 NPU（sleep/wake） |
| AgentLoopManager/Worker（`agent_loop.py:1204`，纯 asyncio） | CPU 节点池，Ray CPU actor | `rollout.agent.num_workers` 按并发轨迹数伸缩，是**主要 CPU 消耗者** |
| RewardLoopWorker / 自定义 reward | CPU 节点池；需 NPU 打分时复用池① | rule-based 打分与生成重叠（`agent_loop_tq.py:165`） |
| 工具执行（`verl/tools/base_tool.py:24`） | **AgentSphere** 通算 sandbox | 经 VPC-EP 私网调用；沙箱天然隔离恶意代码，契合 RL 探索期不可信输出 |
| 常驻推理副本（`replica.py:184` init_standalone，L3 形态） | CCE **Ascend 节点池③** | separate_async 模式，从不 sleep，接 `GlobalRequestLoadBalancer`（`rollout/router.py:133`） |
| 数据集 / 基座权重 / checkpoint | **SFS-Turbo**（RWX PVC） | 所有节点同读；checkpoint 断点续训 |
| 镜像仓库 | SWR（verl 昇腾镜像本就基于 SWR ascendhub CANN） | 私网 VPC-EP 拉取 |
| 权重同步（分离模式） | NPU 间 HCCL over **RoCE 参数面** | `hccl_checkpoint_engine.py:103`，backend 填 `nccl` 自动路由 HCCL |
| 运维入口 | ECS + EIP | 作业提交、调试；训练面无公网入口暴露 |

---

## 4. 网络设计

1. **双平面网络（关键）**：Atlas 服务器的 **RoCE 参数面**由华为云预置的参数面网络承担（HCCL/集合通信走它，与 VPC 无关）；**VPC 业务面**只承载 Ray RPC、OpenAI 协议调用、存储挂载、工具调用。两者物理隔离，互不抢带宽。
2. **VPC-EP 三终端**：
   - `AgentSphere` 终端——agent 工具调用走私网。RL 训练时工具 QPS 极高（并发轨迹数 × 每轨迹平均工具调用数），公网回环延迟会直接放大 rollout 周期；
   - `SWR` 终端——节点私网拉镜像，免流量费且更快；
   - （可选）`OBS` 终端——checkpoint 冷归档。
3. **EIP 策略**：仅管理 ECS 直挂 EIP；CCE 节点经 NAT 网关共享出公网（拉依赖），训练面零公网暴露。
4. **安全组**：训练子网仅放行 VPC 内网段 + VPC-EP 回程；AgentSphere 调用凭证用华为云 IAM token 定期轮转，配置经 K8s Secret 注入。
5. **子网收敛**：池①与池③（若分离部署）必须**同 AZ**——权重同步 HCCL 延迟敏感。

---

## 5. 计算层设计：三种 trainer_mode 对应三档拓扑

verl 源码中三种 trainer 模式（`main_ppo.py:145` 按 `trainer.v1.trainer_mode` 选择）天然对应云上三档拓扑，构成演进路径：

### L1 — 验证阶段：sync 模式（最小成本）

- 仅节点池①（1 台 8 卡 A3 即可跑通 `docs/ascend_tutorial` 的 GSM8K/Qwen3-0.6B 冒烟）；
- CPU 池可与 head 合并部署；AgentSphere 可先用 verl 内置 `FunctionTool`（本地执行）替代，链路验证通过后再切换；
- `trainer_sync.py:35-48`：`on_sample_end` sleep → `on_step_end` update_weights，严格串行，最易排障。

### L2 — Agent RL 标准形态：colocate_async 模式（推荐起点）

- **节点池①（Ascend）**：训练 + 推理分时复用（hybrid engine 内建 sleep/wake，`engine_workers.py:789` 的 update_weights 完整编排了"恢复权重→同步→barrier→恢复 KV cache"全流程）；
- **节点池②（CPU）**：agent loop 高并发。经验公式：`num_workers ≈ 并发轨迹数 / 200`，每个 worker 数百协程；
- 拓扑：工具调用出池② → AgentSphere；生成请求池② → 池①；
- 配置：`trainer.v1.trainer_mode=colocate_async`，`rollout.checkpoint_engine.backend=naive`（进程内同步，零网络开销）；
- 每步边界 `abort_replicas()` 中断在途请求 → 同步权重 → `resume_generation_replicas()` 立即恢复生成（`trainer_colocate_async.py:37-50`），一步以内的 off-policy。

### L3 — 生产高吞吐：separate_async 模式

- 增设**节点池③ 常驻推理池**（standalone replicas，`replica.py:184`），注册到 `GlobalRequestLoadBalancer`，**从不 sleep**，权重经 HCCL checkpoint engine 广播；
- 池①训练时，hybrid replica `switch_to_trainer()`（摘除 → abort → sleep）还卡给训练；生成压力大时 `switch_to_rollout()` 弹性扩容推理（`trainer_separate_async.py:405-433`，内建自适应阈值与收益-成本模型）——与 CCE 节点池自动伸缩联动；
- 配置：`trainer_mode=separate_async`，`rollout.checkpoint_engine.backend=nccl`（NPU 上自动 HCCL），分离传输后端可选 `ascend`。

### CCE 调度要点

- **Volcano gang scheduling**：Ray worker 必须全部同时拉起，否则 HCCL 组网超时——用 podgroup 打 `scheduling.k8s.io/group-name`；
- **Ascend 整卡分配**：pod 资源请求 `huawei.com/Ascend910: 8`；A3 每卡 2 Die，`n_gpus_per_node=16`；
- 镜像用官方 tag（如 `latest-vllm-a3-ubuntu` 或锁定 `v0.9.1-cann9.1.0-...`），托管到自己 SWR 组织。

---

## 6. 存储层设计：SFS-Turbo 目录规划

```
SFS-Turbo (标准型增强/性能型, RWX PVC: verl-sfs)
├── models/            # 基座权重，全节点共享只读；带宽 ≥ 首次加载并发读峰
├── data/              # parquet 数据集；仅 Ray Head 读取，压力小
├── checkpoints/       # verl trainer.default_local_dir 指向此处
│   └── global_step_{ts}/   # save_freq 定期落盘；断点续训直接恢复
└── tool_results/      # (可选) rollout_data_dir 轨迹落盘，供复盘/蒸馏
```

- **容量**：`模型权重×2 + checkpoint×(save_freq 窗口内保留数) + 数据集`。7B 模型 RL 全量状态（含优化器）单 checkpoint 约 100GB+，建议 SFS-Turbo ≥ 2TB 起步，冷数据用 OBS 生命周期归档；
- **性能**：checkpoint 写入是突发大 IO，选带宽随容量扩展的 SFS-Turbo 标准型增强；K8s 用 `ReadWriteMany` PVC 一次挂载全池；
- **断点续训事实源**：verl 的 `_reissue_inflight_prompts`（`trainer_base.py:910`）+ TransferQueue 状态随 checkpoint 保存/恢复——SFS 上的 checkpoint 是断点续训的唯一事实源，L3 弹性伸缩（节点回收）的安全性由此保证。

---

## 7. AgentSphere sandbox 集成

verl 工具层是干净的扩展接口（`BaseTool` 的 create → execute → calc_reward → release 生命周期），AgentSphere 以自定义工具接入——一个沙箱会话工具 + 一个执行工具：

```python
# agentsphere_tool.py —— 注册进 tool_config.yaml 即可被 agent loop 调度
from verl.tools import BaseTool, ToolResponse

class AgentSphereSandbox(BaseTool):
    """AgentSphere 通算沙箱：跨轮持有的代码执行环境"""

    async def create(self, instance_id: str):
        # 经 VPC-EP 私网域名创建 sandbox 会话（instance_id 即轨迹 id，天然一轨迹一沙箱）
        await self._client.post("/v1/sandboxes", json={"id": instance_id, "ttl": 1800})

    async def execute(self, instance_id: str, parameters: dict):
        r = await self._client.post(
            f"/v1/sandboxes/{instance_id}/exec",
            json={"language": "python", "code": parameters["code"]},
        )
        return ToolResponse(text=r["stdout"][:32000]), None, {}  # verl 侧按 max_tool_response_length 截断

    async def release(self, instance_id: str):
        await self._client.delete(f"/v1/sandboxes/{instance_id}")
```

```yaml
# tool_config.yaml（rollout.multi_turn.tool_config_path 指向）
tools:
  - id: code_interpreter
    class: agentsphere_tool.AgentSphereSandbox
    config:
      endpoint: ${AGENTSPHERE_VPCEP_URL}   # VPC-EP 私网地址，Secret 注入
      max_parallel_calls: 4
```

**协同语义**（关键）：沙箱执行结果作为工具观察回流时 `response_mask=0` 不参与梯度（`ct_merge_context_msg` 合并，见 `tool_agent_loop.py:386`），只有模型生成的工具调用语法 token `mask=1` 参与训练。所以**沙箱延迟只影响吞吐（wall-clock），不影响训练正确性**——这是 AgentSphere 外置不污染训练环路的根本保证，也意味着沙箱调优（网络、规格）与训练质量解耦，可以独立进行。

---

## 8. 关键 verl 配置到云资源映射

```yaml
# L2 形态（colocate_async）核心项
trainer:
  use_v1: true
  v1: { trainer_mode: colocate_async }
  default_local_dir: /mnt/sfs/checkpoints   # SFS-Turbo PVC 挂载点
  save_freq: 10
actor_rollout_ref:
  rollout:
    name: vllm                    # vllm-ascend 后端；或 sglang（官方均发镜像）
    mode: hybrid                  # 与训练同进程共置
    free_cache_engine: true       # 训练期释放 KV cache（显存协同开关）
    multi_turn:
      tool_config_path: /mnt/sfs/configs/tool_config.yaml   # AgentSphere 工具
      max_user_turns: 15
    agent:
      num_workers: 16             # CPU 池 Ray actor 数
    load_format: dummy_megatron   # 或 hf，指向 /mnt/sfs/models/...
data:
  files: /mnt/sfs/data/gsm8k.parquet

# L3 形态增量：
#   trainer.v1.trainer_mode: separate_async
#   actor_rollout_ref.rollout.checkpoint_engine.backend: nccl   # NPU 上自动路由 HCCL
```

---

## 9. 部署流程

按序执行：

1. **镜像**：SWR 组织内拉取 verl 官方昇腾镜像（或以 `docker/ascend/Dockerfile.ascend_9.1.0_a3` 为基，追加 AgentSphere SDK 后自构建推送至自己 SWR）；
2. **网络**：建 VPC / 三子网 / NAT / EIP → 创建 AgentSphere、SWR 两个 VPC-EP；
3. **存储**：创建 SFS-Turbo → 建 PVC → 上传基座权重与 parquet 数据集（管理 ECS 挂载同 SFS 直接拷入）；
4. **CCE**：建集群（Volcano 必装）→ 节点池②（CPU 通用型）→ 节点池①（Atlas 裸金属注册，Ascend Docker Runtime / Device Plugin 自动安装）→（L3）节点池③；
5. **Ray 集群**：KubeRay / StatefulSet 部署——Head（池②，标签调度）+ Worker（池①，`huawei.com/Ascend910: 8`，podgroup gang 调度）；
6. **冒烟**：跑 `ascend_tutorial` 的 Qwen3-0.6B + GSM8K 单步验证（验证点：vllm-ascend rollout 能生成 + 首个 step 完成）；
7. **切换 Agent RL**：注入 tool_config（AgentSphere）→ trainer_mode 改 colocate_async → 小规模轨迹跑通后放量；
8. **可观测**：AOM 收容器/NPU 指标 + verl metrics。重点盯 `training/off_policy/trajectory_staleness`——它是 CPU 池 / AgentSphere 是否拖后腿的直接信号。

---

## 10. 最佳实践清单

| 维度 | 要点 |
|---|---|
| 调度 | Volcano gang scheduling 必开；Ascend 整卡请求；A3 双 Die 记 `n_gpus_per_node=16` |
| 显存 | hybrid + `free_cache_engine: true`，训练期 KV cache 自动释放，单池跑下训推共存 |
| 网络 | 训推池同 AZ；工具/镜像全走 VPC-EP 私网；RL 高 QPS 工具调用严禁公网回环 |
| 存储 | checkpoint 只写 SFS-Turbo；权重/数据集 RWX 共享；OBS 归档冷 checkpoint |
| 弹性 | L3 用 separate_async 的 switch 机制 + CCE 节点池伸缩联动；TQ checkpoint 保证缩容安全 |
| 排障优先级 | staleness↑ → 先扩 CPU 池 num_workers → 再查 VPC-EP 延迟 → 最后才考虑扩 NPU 池（多数瓶颈在前两者） |
| 安全 | 训练面纯私网；IAM token 轮转 + Secret 注入；沙箱隔离 RL 探索期的不可信生成代码（AgentSphere 天然价值点） |
| 成本 | CPU 池按需伸缩（agent loop 是纯 asyncio，通用型即可）；NPU 池 sync → colocate_async → separate_async 逐步提升复用率 |

---

## 总结

本方案的核心逻辑一句话：**把 verl 源码中"NPU 上训推分时共置（hybrid engine + sleep/wake）、CPU 上轨迹流水（agent loop + TransferQueue）、外置沙箱执行工具（BaseTool 生命周期）"的协同设计，原样映射为"CCE Ascend 池 + CCE CPU 池 + AgentSphere"三段式云拓扑；SFS-Turbo 承载唯一事实源（checkpoint），VPC-EP 把所有高频调用压在私网。**

verl 的架构对云部署异常友好，原因有二：其一，官方昇腾镜像链路（SWR ascendhub → CANN/torch-npu/vllm-ascend/MindSpeed）与华为云原生服务天然同源；其二，训练-推理协同全部由 driver 单控制器显式编排（sleep/wake/权重同步都是确定性 hook），云上只需要提供"整卡调度 + 私网直通 + 共享存储"三件事，不需要对框架做任何侵入式改造。

---

*本报告基于 verl 0.6.x 源码静态分析与华为云服务特性生成，所有 verl `file:line` 引用均已在代码基线中核实；AgentSphere 接入示例基于 verl 工具扩展接口（`BaseTool`）编写，具体 API 字段以 AgentSphere 服务实际版本为准。*
