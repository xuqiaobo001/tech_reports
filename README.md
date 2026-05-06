# Tech Reports 索引

> 本仓库汇集了 AI 基础设施、大模型推理框架、云服务技术等方面的深度分析报告。
> 每个目录包含一份独立的 Markdown 报告，点击目录名可查看详细内容。

---

## 目录

- [AI 推理框架（SGLang）](#ai-推理框架sglang)
- [AI 推理框架（vLLM）](#ai-推理框架vllm)
- [云存储服务对比](#云存储服务对比)
- [AI 编码工具与 Agent](#ai-编码工具与-agent)
- [华为云基础设施](#华为云基础设施)
- [华为云 AI / 昇腾](#华为云-ai--昇腾)
- [开源项目分析](#开源项目分析)
- [大模型部署方案](#大模型部署方案)

---

## AI 推理框架（SGLang）

| # | 目录 | 简介 |
|---|------|------|
| 1 | [sglang_pd_disaggregation_architecture_analysis](./sglang_pd_disaggregation_architecture_analysis/) | SGLang PD 分离架构全景分析 — P/D/Router 三进程启动序列、参数配置与协同机制 |
| 2 | [sglang_pd_startup_params_reference](./sglang_pd_startup_params_reference/) | SGLang PD 分离架构 P/D/Router 全部启动参数详解与调优建议 |
| 3 | [sglang_kvcache_prefill_failure_analysis](./sglang_kvcache_prefill_failure_analysis/) | "Failed to get kvcache from prefill instance" 错误的所有触发场景及根因分析 |
| 4 | [sglang_prefill_bootstrap_failed_analysis](./sglang_prefill_bootstrap_failed_analysis/) | Prefill Bootstrap Failed 错误根因分析，覆盖所有异常路径 |
| 5 | [sglang_router_decode_forwarding_failure_analysis](./sglang_router_decode_forwarding_failure_analysis/) | Router 转发请求到 Decode 节点失败场景分析 — 断路器、健康检查、重试逻辑 |
| 6 | [sglang_decode_memory_leak_analysis](./sglang_decode_memory_leak_analysis/) | 超长输入场景下 Decode 节点异常与内存/HBM 泄露分析 |
| 7 | [sglang_context_length_limit_analysis](./sglang_context_length_limit_analysis/) | SGLang 三层上下文长度限制体系与超限行为分析 |
| 8 | [sglang_prefill_schedule_policy_analysis](./sglang_prefill_schedule_policy_analysis/) | Prefill 节点请求调度算法、Radix Cache 淘汰策略与 Prefill Delayer 协调机制 |
| 9 | [sglang_transfer_backend_comparison](./sglang_transfer_backend_comparison/) | KV Cache 传输后端对比选型 — mooncake/nixl/ascend/fake/mori 五种后端 |
| 10 | [sglang_ascend_vs_mooncake_code_analysis](./sglang_ascend_vs_mooncake_code_analysis/) | SGLang Ascend vs Mooncake 传输后端代码差异与优化分析 |
| 11 | [sglang_ttft_tpot_latency_analysis](./sglang_ttft_tpot_latency_analysis/) | PD 分离架构下不同输入/输出长度组合的 TTFT/TPOT 时延变化趋势 |

---

## AI 推理框架（vLLM）

| # | 目录 | 简介 |
|---|------|------|
| 12 | [vllm_vs_sglang_pd_disaggregation_report](./vllm_vs_sglang_pd_disaggregation_report/) | vLLM vs SGLang P/D 分离机制深度对比 — 架构、调度、KV 传输全面比较 |
| 13 | [vllm_sglang_precision_divergence_report](./vllm_sglang_precision_divergence_report/) | vLLM vs SGLang 加载同一模型的 9 维精度差异源码级根因分析 |
| 14 | [vllm_ascend_distributed_deployment](./vllm_ascend_distributed_deployment/) | vLLM-Ascend 分布式部署架构 — Head/Worker 分层架构与分离部署方案 |
| 15 | [vllm_multi_p_node_scheduler_report](./vllm_multi_p_node_scheduler_report/) | vLLM 多 P 节点调度机制 — 独立调度域、Round-Robin 分发与无全局调度器设计 |
| 16 | [vllm_ray_k8s_pd_deployment_report](./vllm_ray_k8s_pd_deployment_report/) | vLLM Ray 组件用法分析 + K8s 下 P/D 分离最佳部署方案 |

---

## 云存储服务对比

| # | 目录 | 简介 |
|---|------|------|
| 17 | [huaweicloud_sfs_vs_aliyun_cpfs_report](./huaweicloud_sfs_vs_aliyun_cpfs_report/) | 华为云 SFS Turbo vs 阿里云 CPFS（通用版+智算版）深度对比 |
| 18 | [aliyun_oss_vs_huawei_obs_report](./aliyun_oss_vs_huawei_obs_report/) | 阿里云 OSS vs 华为云 OBS 对象存储深度对比 — 规格、场景、价格、技术竞争力 |

---

## AI 编码工具与 Agent

| # | 目录 | 简介 |
|---|------|------|
| 19 | [anthropic_engineering_report](./anthropic_engineering_report/) | Anthropic 工程技术理论与实践研究报告 — Agent 架构、上下文工程、评估体系、MCP 协议、多智能体、安全基础设施六大主题域 |
| 20 | [agent_teams_evaluation_report](./agent_teams_evaluation_report/) | Agent Teams / Multi-Agent AI Systems 技术评估 — 架构、优劣势、业界评价、适用场景 |
| 21 | [skills_vs_subagents_report](./skills_vs_subagents_report/) | Claude Code Skills 与 Subagents 技术调研 — 本质区别、场景侧重点、最佳封装形式 |
| 22 | [claude_coding_benchmarks_report](./claude_coding_benchmarks_report/) | Anthropic Claude 编码能力评估基准调研 — 9 个主要基准的三梯队分析 |

---

## 华为云基础设施

| # | 目录 | 简介 |
|---|------|------|
| 23 | [huawei_cloud_pay_per_use_billing](./huawei_cloud_pay_per_use_billing/) | 华为云按需计费机制分析 — 计费粒度、结算周期、扣费时间点详解 |
| 24 | [huawei_cloud_n_project_pay_per_use_billing_settlement](./huawei_cloud_n_project_pay_per_use_billing_settlement/) | N 项目按需资源计费结算时间分析 — APIG/EIP/ELB/ModelArts/VPC-EP 五服务结算详情 |
| 25 | [cce_pod_replica_unit_analysis](./cce_pod_replica_unit_analysis/) | 华为云 CCE Pod、实例、副本概念关系说明 |
| 26 | [华为云云服务QPS与连接数限制报告](./华为云云服务QPS与连接数限制报告/) | 华为云各云服务（ELB/APIG/WAF/NAT/RDS/DDS/DCS/CSE/FunctionGraph）QPS 和连接数限制汇总 |

---

## 华为云 AI / 昇腾

| # | 目录 | 简介 |
|---|------|------|
| 27 | [华为昇腾AI芯片对比报告_910B_910C_950](./华为昇腾AI芯片对比报告_910B_910C_950/) | 华为昇腾 910B/910C/950 芯片规格对比、技术演进与 NVIDIA 竞品对比 |
| 28 | [huawei_ascend_cloud_servers_analysis](./huawei_ascend_cloud_servers_analysis/) | 华为云昇腾服务器种类分析 — ECS AI 加速型与 ModelArts 实例规格 |
| 29 | [GLM4.7_deployment_solution](./GLM4.7_deployment_solution/) | 华为云 ModelArts GLM-4.7-Flash-30B-A3B 私有化部署方案 — 1300 QPS @ 24K P99 |

---

## 开源项目分析

| # | 目录 | 简介 |
|---|------|------|
| 30 | [openclaw_architecture_report](./openclaw_architecture_report/) | OpenClaw 源码架构分析 — 本地优先个人 AI 助手网关，插件化多通道架构 |
| 31 | [openclaw_skills_report](./openclaw_skills_report/) | OpenClaw 内置 68 个 Skills 完整分析 — 分类、功能详解与架构模式 |
| 32 | [opencode](./opencode/) | OpenCode 架构分析合集 — 整体架构、PTY 机制、Server API、与 Claude Code 对比（4 篇） |
| 33 | [openspec](./openspec/) | OpenSpec 开源项目分析 — AI 原生规范驱动开发系统 |
| 34 | [harbor](./harbor/) | Harbor Framework 源码架构深度分析合集 — 运行逻辑、并行执行、容器架构、CCE 适配（4 篇） |

---

## 统计

| 分类 | 报告数 |
|------|--------|
| SGLang 推理框架 | 11 |
| vLLM 推理框架 | 5 |
| 云存储服务对比 | 2 |
| AI 编码工具与 Agent | 4 |
| 华为云基础设施 | 4 |
| 华为云 AI / 昇腾 | 3 |
| 开源项目分析 | 5 |
| **合计** | **34** |
