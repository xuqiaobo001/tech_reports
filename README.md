# Tech Reports 索引

> 本仓库汇集了 AI 基础设施、大模型推理框架、云服务技术等方面的深度分析报告。
> 每个目录包含一份独立的 Markdown 报告，点击目录名可查看详细内容。

---

## 目录

- [推理性能优化](#推理性能优化)
- [AI 推理框架（SGLang）](#ai-推理框架sglang)
- [AI 推理框架（vLLM）](#ai-推理框架vllm)
- [云存储服务对比](#云存储服务对比)
- [云存储迁移方案](#云存储迁移方案)
- [AI 编码工具与 Agent](#ai-编码工具与-agent)
- [华为云基础设施](#华为云基础设施)
- [华为云 AI / 昇腾](#华为云-ai--昇腾)
- [开源项目分析](#开源项目分析)
- [大模型训练与微调](#大模型训练与微调)
- [大模型技术演进](#大模型技术演进)
- [视频生成大模型](#视频生成大模型)
- [大模型部署方案](#大模型部署方案)
- [RL 训练系统](#rl-训练系统)

---

## 推理性能优化

| # | 目录 | 简介 |
|---|------|------|
| 1 | [vllm_inference_speed_optimization](./vllm_inference_speed_optimization/) | vLLM 推理速度优化部署参数全景图 — 8 大维度 50+ 参数深度分析，含场景化调优策略 |
| 2 | [sglang_inference_speed_optimization](./sglang_inference_speed_optimization/) | SGLang 推理速度优化部署参数全景图 — 8 大维度 60+ 参数深度分析，含 SGLang vs vLLM 对比 |

---

## AI 推理框架（SGLang）

| # | 目录 | 简介 |
|---|------|------|
| 3 | [sglang_pd_disaggregation_architecture_analysis](./sglang_pd_disaggregation_architecture_analysis/) | SGLang PD 分离架构全景分析 — P/D/Router 三进程启动序列、参数配置与协同机制 |
| 4 | [sglang_pd_startup_params_reference](./sglang_pd_startup_params_reference/) | SGLang PD 分离架构 P/D/Router 全部启动参数详解与调优建议 |
| 5 | [sglang_kvcache_prefill_failure_analysis](./sglang_kvcache_prefill_failure_analysis/) | "Failed to get kvcache from prefill instance" 错误的所有触发场景及根因分析 |
| 6 | [sglang_prefill_bootstrap_failed_analysis](./sglang_prefill_bootstrap_failed_analysis/) | Prefill Bootstrap Failed 错误根因分析，覆盖所有异常路径 |
| 7 | [sglang_router_decode_forwarding_failure_analysis](./sglang_router_decode_forwarding_failure_analysis/) | Router 转发请求到 Decode 节点失败场景分析 — 断路器、健康检查、重试逻辑 |
| 8 | [sglang_decode_memory_leak_analysis](./sglang_decode_memory_leak_analysis/) | 超长输入场景下 Decode 节点异常与内存/HBM 泄露分析 |
| 9 | [sglang_context_length_limit_analysis](./sglang_context_length_limit_analysis/) | SGLang 三层上下文长度限制体系与超限行为分析 |
| 10 | [sglang_prefill_schedule_policy_analysis](./sglang_prefill_schedule_policy_analysis/) | Prefill 节点请求调度算法、Radix Cache 淘汰策略与 Prefill Delayer 协调机制 |
| 11 | [sglang_transfer_backend_comparison](./sglang_transfer_backend_comparison/) | KV Cache 传输后端对比选型 — mooncake/nixl/ascend/fake/mori 五种后端 |
| 12 | [sglang_ascend_vs_mooncake_code_analysis](./sglang_ascend_vs_mooncake_code_analysis/) | SGLang Ascend vs Mooncake 传输后端代码差异与优化分析 |
| 13 | [sglang_ttft_tpot_latency_analysis](./sglang_ttft_tpot_latency_analysis/) | PD 分离架构下不同输入/输出长度组合的 TTFT/TPOT 时延变化趋势 |

---

## AI 推理框架（vLLM）

| # | 目录 | 简介 |
|---|------|------|
| 14 | [vllm_vs_sglang_pd_disaggregation_report](./vllm_vs_sglang_pd_disaggregation_report/) | vLLM vs SGLang P/D 分离机制深度对比 — 架构、调度、KV 传输全面比较 |
| 15 | [vllm_sglang_precision_divergence_report](./vllm_sglang_precision_divergence_report/) | vLLM vs SGLang 加载同一模型的 9 维精度差异源码级根因分析 |
| 16 | [vllm_ascend_distributed_deployment](./vllm_ascend_distributed_deployment/) | vLLM-Ascend 分布式部署架构 — Head/Worker 分层架构与分离部署方案 |
| 17 | [vllm_multi_p_node_scheduler_report](./vllm_multi_p_node_scheduler_report/) | vLLM 多 P 节点调度机制 — 独立调度域、Round-Robin 分发与无全局调度器设计 |
| 18 | [vllm_ray_k8s_pd_deployment_report](./vllm_ray_k8s_pd_deployment_report/) | vLLM Ray 组件用法分析 + K8s 下 P/D 分离最佳部署方案 |

---

## 云存储服务对比

| # | 目录 | 简介 |
|---|------|------|
| 19 | [huaweicloud_sfs_vs_aliyun_cpfs_report](./huaweicloud_sfs_vs_aliyun_cpfs_report/) | 华为云 SFS Turbo vs 阿里云 CPFS（通用版+智算版）深度对比 |
| 20 | [aliyun_oss_vs_huawei_obs_report](./aliyun_oss_vs_huawei_obs_report/) | 阿里云 OSS vs 华为云 OBS 对象存储深度对比 — 规格、场景、价格、技术竞争力 |
| 21 | [huaweicloud_obs_mount_nfs_protocol_analysis](./huaweicloud_obs_mount_nfs_protocol_analysis/) | 华为云 OBS 挂载机制与 NFS 协议支持分析 — obsfs FUSE 架构原理、SFS vs OBS 协议对比、NFS 挂载判断方法

---

## 云存储迁移方案

| # | 目录 | 简介 |
|---|------|------|
| 22 | [cpfs_5pb_ecs_sizing](./cpfs_5pb_ecs_sizing/) | 5PB 迁移方案：阿里云 ECS 传输集群规格与数量设计 — 14天全量迁移计算 |
| 23 | [cpfs_5pb_ecs_eip_sizing_v2](./cpfs_5pb_ecs_eip_sizing_v2/) | 5PB 迁移方案：阿里云 ECS 规格与 EIP 带宽完整设计（修订版）— 单 EIP 200-500Mbit/s |
| 24 | [cpfs_5pb_migration_with_updates](./cpfs_5pb_migration_with_updates/) | 5PB 级 CPFS 数据迁移方案：存量+并发更新场景 — 最终一致性保证 |
| 25 | [cpfs_to_sfsturbo_migration](./cpfs_to_sfsturbo_migration/) | 阿里云 CPFS → 华为云 SFS Turbo + OBS 数据迁移方案 — 零数据丢失跨云迁移 |

---

## AI 编码工具与 Agent

| # | 目录 | 简介 |
|---|------|------|
| 26 | [anthropic_engineering_report](./anthropic_engineering_report/) | Anthropic 工程技术理论与实践研究报告 — Agent 架构、上下文工程、评估体系、MCP 协议、多智能体、安全基础设施六大主题域 |
| 27 | [agent_teams_evaluation_report](./agent_teams_evaluation_report/) | Agent Teams / Multi-Agent AI Systems 技术评估 — 架构、优劣势、业界评价、适用场景 |
| 28 | [skills_vs_subagents_report](./skills_vs_subagents_report/) | Claude Code Skills 与 Subagents 技术调研 — 本质区别、场景侧重点、最佳封装形式 |
| 29 | [claude_coding_benchmarks_report](./claude_coding_benchmarks_report/) | Anthropic Claude 编码能力评估基准调研 — 9 个主要基准的三梯队分析 |
| 30 | [hermes_agent_analysis](./hermes_agent_analysis/) | Hermes Agent 源码深度分析报告 |

---

## 华为云基础设施

| # | 目录 | 简介 |
|---|------|------|
| 31 | [huawei_cloud_pay_per_use_billing](./huawei_cloud_pay_per_use_billing/) | 华为云按需计费机制分析 — 计费粒度、结算周期、扣费时间点详解 |
| 32 | [huawei_cloud_n_project_pay_per_use_billing_settlement](./huawei_cloud_n_project_pay_per_use_billing_settlement/) | N 项目按需资源计费结算时间分析 — APIG/EIP/ELB/ModelArts/VPC-EP 五服务结算详情 |
| 33 | [cce_pod_replica_unit_analysis](./cce_pod_replica_unit_analysis/) | 华为云 CCE Pod、实例、副本概念关系说明 |
| 34 | [华为云云服务QPS与连接数限制报告](./华为云云服务QPS与连接数限制报告/) | 华为云各云服务（ELB/APIG/WAF/NAT/RDS/DDS/DCS/CSE/FunctionGraph）QPS 和连接数限制汇总 |
| 35 | [ack_vs_cce_comparison](./ack_vs_cce_comparison/) | 阿里云 ACK vs 华为云 CCE 容器平台能力对比分析 |
| 36 | [huawei_vs_aliyun_saml_iam_comparison](./huawei_vs_aliyun_saml_iam_comparison/) | 华为云 IAM vs 阿里云 RAM：SAML 联邦认证权限管控粒度对比 |
| 37 | [huaweicloud_apig_eip_elb_architecture](./huaweicloud_apig_eip_elb_architecture/) | 华为云 APIG 挂载 EIP 与 ELB 架构分析 |
| 38 | [huawei_cloud_iam_saml_sso_reference](./huawei_cloud_iam_saml_sso_reference/) | 华为云 IAM SAML SSO 配置参考 — 虚拟用户 SSO 和 IAM 用户 SSO 全流程配置 |
| 39 | [huawei_cloud_iam_saml_sso_test_cases](./huawei_cloud_iam_saml_sso_test_cases/) | 华为云 IAM SAML 单点登录对接测试用例 — 虚拟用户 SSO 和 IAM 用户 SSO 两种模式 |
| 40 | [huaweicloud_dcs_redis_issues_analysis](./huaweicloud_dcs_redis_issues_analysis/) | 华为云 DCS (Redis) 客户常见问题与故障场景分析 — 15 类 TOP 问题根因与解决方案 |
| 41 | [sfsturbo_cbr_backup_analysis](./sfsturbo_cbr_backup_analysis/) | 华为云 SFS-Turbo + CBR 备份瓶颈、约束与故障场景深度分析 — 10 大故障场景与最佳实践 |

---

## 华为云 AI / 昇腾

| # | 目录 | 简介 |
|---|------|------|
| 42 | [华为昇腾AI芯片对比报告_910B_910C_950](./华为昇腾AI芯片对比报告_910B_910C_950/) | 华为昇腾 910B/910C/950 芯片规格对比、技术演进与 NVIDIA 竞品对比 |
| 43 | [huawei_ascend_cloud_servers_analysis](./huawei_ascend_cloud_servers_analysis/) | 华为云昇腾服务器种类分析 — ECS AI 加速型与 ModelArts 实例规格 |
| 44 | [GLM4.7_deployment_solution](./GLM4.7_deployment_solution/) | 华为云 ModelArts GLM-4.7-Flash-30B-A3B 私有化部署方案 — 1300 QPS @ 24K P99 |
| 45 | [modelarts_vs_pai_comparison](./modelarts_vs_pai_comparison/) | 华为云 ModelArts Standard vs 阿里云 PAI 平台能力对比分析 |
| 46 | [modelarts_workspace_types_analysis](./modelarts_workspace_types_analysis/) | 华为云 ModelArts 工作空间 PUBLIC/PRIVATE/INTERNAL 三种类型权限差异、使用场景与选型决策 |
| 47 | [modelarts_fault_recovery_panorama](./modelarts_fault_recovery_panorama/) | 华为云 ModelArts 模型训练与强化学习故障恢复能力全景分析 — 三级恢复五大策略 |

---

## 开源项目分析

| # | 目录 | 简介 |
|---|------|------|
| 48 | [openclaw_architecture_report](./openclaw_architecture_report/) | OpenClaw 源码架构分析 — 本地优先个人 AI 助手网关，插件化多通道架构 |
| 49 | [openclaw_skills_report](./openclaw_skills_report/) | OpenClaw 内置 68 个 Skills 完整分析 — 分类、功能详解与架构模式 |
| 50 | [opencode](./opencode/) | OpenCode 架构分析合集 — 整体架构、PTY 机制、Server API、与 Claude Code 对比（4 篇） |
| 51 | [openspec](./openspec/) | OpenSpec 开源项目分析 — AI 原生规范驱动开发系统 |
| 52 | [harbor](./harbor/) | Harbor Framework 源码架构深度分析合集 — 运行逻辑、并行执行、容器架构、CCE 适配（4 篇） |

---

## 大模型训练与微调

| # | 目录 | 简介 |
|---|------|------|
| 53 | [llamafactory_data_loading_analysis](./llamafactory_data_loading_analysis/) | LLaMA-Factory SFT 训练数据集加载机制深度分析 |
| 54 | [sft_training_logging_report](./sft_training_logging_report/) | LLaMA-Factory SFT 训练关键日志信息报告 |

---

## 大模型技术演进

| # | 目录 | 简介 |
|---|------|------|
| 55 | [DeepSeek_核心技术演进分析报告](./DeepSeek_核心技术演进分析报告/) | 基于 DeepSeek V3、V3.2、V4 三份技术报告的横向对比分析 |
| 56 | [残差变换与mHC详解](./残差变换与mHC详解/) | 基于 DeepSeek V3/V4 技术报告中残差连接机制的通俗解读 |
| 57 | [mHC双随机矩阵非膨胀原理详解](./mHC双随机矩阵非膨胀原理详解/) | DeepSeek V4 流形约束超连接中双随机矩阵保证残差变换不膨胀的原理分析 |

---

## 大模型部署方案

*(暂无报告)*

---

## RL 训练系统

| # | 目录 | 简介 |
|---|------|------|
| 58 | [hermes_agent_rl_training_analysis](./hermes_agent_rl_training_analysis/) | Hermes-Agent RL 训练系统技术分析 — 两阶段架构、Agent Loop、ToolContext 奖励验证、数据生成管线 |
| 59 | [hermes_agent_rl_separation_feasibility](./hermes_agent_rl_separation_feasibility/) | Hermes-Agent 常规对话与 RL 训练分离部署可行性分析 — 源码级耦合度评估与三种分离架构方案 |

---

## 视频生成大模型

| # | 目录 | 简介 |
|---|------|------|
| 60 | [video_generation_models_survey](./video_generation_models_survey/) | 视频生成大模型全景调研 — Wan2.1/HunyuanVideo/CogVideoX/Open-Sora 等模型参数量、训练框架（预训练/SFT/LoRA/RLHF）、使用场景、常见故障分析 |

---

## 统计

| 分类 | 报告数 |
|------|--------|
| 推理性能优化 | 2 |
| SGLang 推理框架 | 11 |
| vLLM 推理框架 | 5 |
| 云存储服务对比 | 3 |
| 云存储迁移方案 | 4 |
| AI 编码工具与 Agent | 5 |
| 华为云基础设施 | 11 |
| 华为云 AI / 昇腾 | 6 |
| 开源项目分析 | 5 |
| 大模型训练与微调 | 2 |
| 大模型技术演进 | 3 |
| 视频生成大模型 | 1 |
| 大模型部署方案 | 0 |
| RL 训练系统 | 2 |
| **合计** | **60** |
