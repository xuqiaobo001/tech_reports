# MemCache vs Mooncake：KVCache 共享开源项目深度对比分析

> **报告日期**：2026-07-01
> **分析对象**：MemCache（华为昇腾）、Mooncake（Moonshot AI / Kimi）
> **分析维度**：① 共享机制差异 ② 场景侧重点 ③ 客户选型推荐策略
> **方法**：基于两份开源仓库的 README、设计文档、配置项与源码结构进行实证分析

---

## 0. 摘要（TL;DR）

| | **MemCache** | **Mooncake** |
|---|---|---|
| 出身 | 华为昇腾 Ascend 团队，2025/11 开源 | Moonshot AI (Kimi)，FAST'25 最佳论文 |
| 本质 | **昇腾原生的分布式 KVCache 存储引擎**，底座是自研 MemFabric | **硬件无关的 Tensor 数据面 / AI 基础设施**，核心是 Transfer Engine |
| 许可证 | MulanPSL2（国内合规友好） | Apache 2.0 |
| 硬件 | 昇腾 A2/A3 + 鲲鹏 K5（厂商绑定） | NVIDIA/华为/AMD/寒武纪/摩尔线程/AWS… 十余家 |
| 范围 | 聚焦"存储引擎"（推理 KV 共享） | 存储 + 传输 + EP/PG + 训练 checkpoint/RL，范围大得多 |

**核心结论**：MemCache 是"昇腾原生的、偏存储引擎层的高性能 KVCache 池"，场景窄但深；Mooncake 是"硬件无关的 Tensor 数据面"，覆盖推理（含多模态/MoE）+ 训练，生态最广、生产最成熟。两者在昇腾环境里是**重叠竞争**而非互斥（Mooncake 也支持昇腾 transport），选型主要取决于**硬件锁定、国产化要求、负载类型、生态成熟度**四个因素。

---

## 1. 机制差异

### 1.1 底座与传输层（最根本的差异）

**MemCache = 共享内存池 + 直连 RDMA 模型**

- 底座是自研 MemFabric，把"多级内存 + 异构网络传输"打包成一个池化底座。
- 传输语义直接面向昇腾硬件，配置项只有 6 条直连路径：`host_rdma`(A2/A3)、`host_urma`(鲲鹏K5)、`host_tcp`、`host_shm`(同节点共享内存)、`device_sdma`(A3)、`device_rdma`(A2)。
- 杀手锏是 **OneCopy 跨机跨介质直接访问**：H2D / D2H / **D2RH / RH2D**（Remote-Host ↔ Device），即远端主机内存与本地显存之间一次拷贝直达，绕开"远端内存→远端CPU→网络→本地CPU→本地显存"的中转路径。

**Mooncake = 传输引擎 + 对象存储分层模型**

- 底座是 Transfer Engine (TE)，一个**统一批量数据搬运框架**，传输与硬件完全解耦，插件化支持 15+ 种 transport：TCP / RDMA / AWS EFA / NVMe-oF / NVLink / HIP / Barex / CXL / CXI / MACA / Ascend / Kunpeng-UB / Sunrise-Link / intranode-NVLink。
- 强调"拓扑感知选路 + 多 NIC 带宽聚合 + 自动 failover"。宣传数据：128k token 的 KV（40GB）在 8×400Gbps RoCE 下可达 190 GB/s，比 TCP 快 4.6×。

> **一句话**：MemCache 是"把昇腾显存/内存揉成一个全局池，靠硬件直连访问"；Mooncake 是"造一个通用高速搬运层，上面架对象存储"。

### 1.2 数据与编址模型

| 维度 | MemCache | Mooncake |
|---|---|---|
| 内存贡献方式 | 每个 LocalService 贡献 `dram.size` + `hbm.size` 连续区到全局池 | Client 用 `MountSegment` 注册连续内存段到 Master |
| 加入模型 | **rank/world_size 集合式加入**（类似分布式训练 process group，上限 1024，连上后不可改、需重启 meta） | 段级动态 mount/unmount，节点随时加退 |
| 显存(HBM) | **一级池成员**，可直接 device 间 D2RH/RH2D | 有 device 内存支持，但定位是"通用 transport 之一" |
| 控制面/数据面 | MetaService 管池分配 + join/leave | **明确分离**：Master 只管元数据/分配、不走数据流；数据在 Client 间零拷贝直传 |

MemCache 的 `world_size`/`rank` 设计明显借鉴了分布式训练 process group 语义（还配了 `hcom_url`、HCCL 相关 TLS），说明它的目标场景里推理进程是按 rank 组织的、生命周期相对稳定。

### 1.3 元数据与高可用

| | MemCache | Mooncake |
|---|---|---|
| 元数据服务 | MetaService（集中管池空间分配） | Master Service（**纯控制面**，不碰数据） |
| 选主/HA | **K8S ClusterIP + Lease** 选主（强依赖 K8s） | **etcd 集群 + leader 选举 + 心跳**；也提供内置 HTTP metadata server（单点，开发用） |
| 状态恢复 | 尽力而为 HA + 元数据恢复 | Master **snapshot/restore**（fork CoW 周期快照） |

### 1.4 多级存储与淘汰

- **MemCache**：DRAM + HBM 池为主，也支持 SSD。淘汰按高低水位（默认 90%/80%）。
- **Mooncake**：DRAM + SSD/NVMe 持久化分级，后端可选 SPDK / cachelib / **3FS (hf3fs, USRBIO)**。淘汰机制丰富得多：近似 LRU + 高水位 95% + **lease(默认5s)** 保护读 + **soft pin(30min TTL)** + **hard pin(永久)** + 对象组(group_ids，给 K/V 张量这类逻辑单元做生命周期提示) + **多租户配额** + zombie 对象清理。

### 1.5 API 与一致性

两者都是对象级 `put/get/exist/remove` + 批量 + 多副本，表面相似：

- **MemCache**：批量/非批量 + 多层 KV Block 读写接口，`put` 指定副本数；批量聚合 IO（`aggregate.num` 默认 122，正好是 DeepSeek-R1 单 block 的离散地址数——说明按 DS-R1 调过参）。
- **Mooncake**：`Put/Get/Remove/Upsert/BatchUpsert` + `ReplicateConfig`(replica_num/soft_pin/hard_pin/preferred_segment/group_ids) + 异步 `CreateCopyTask/CreateMoveTask` + regex 查删。**强一致 Get**（PutEnd 后对象不可变，直到 Remove）。

---

## 2. 场景侧重点

### 2.1 MemCache 的侧重点

1. **昇腾生态的 LLM 推理**：已是 vllm-ascend 的 KV pool backend（2025/12）。
2. **GR（生成式推荐）推理**：README 明确写了"LLM推理、GR推理场景"——这是昇腾栈上一个区别于通用 LLM 的特色负载。
3. **极致低时延的显存级跨机共享**：RH2D/D2RH OneCopy 适合 PD 分离、prefix KV 跨实例复用，且把 HBM 直接当池成员，省去"远端→CPU→本地显存"的中转。
4. **单厂商硬件深度协同**：吃满 A2 `device_rdma`、A3 `device_sdma`、鲲鹏 `host_urma` 的硬件特性。

> **定位**：昇腾原生的、偏"存储引擎"层的高性能 KVCache 池，场景窄但深。

### 2.2 Mooncake 的侧重点

1. **大规模 LLM 服务（生产验证）**：Kimi 的实际 Serving 平台，真实负载下能多扛 75% 请求且满足 SLO。
2. **PD 分离 + HiCache 分级缓存 + 跨实例 prefix 共享**：深度集成 SGLang / vLLM / TRT-LLM / LMCache，尤其 agentic、多轮对话这类重复 prefix 的负载。
3. **多模态 EPD 分离**：把 ViT encoder 与 LM 解耦，Mooncake 做大 embedding 的零拷贝搬运。
4. **MoE 容错专家并行**：Mooncake EP/PG 给 DeepEP 风格的 EP 加了 rank 故障感知与恢复。
5. **训练侧**：checkpoint-engine（Kimi-K2 1T 参数千卡 ~20s 更新）、分布式 RL 权重 P2P 同步（SGLang，53s→7.2s，7×）、TorchSpec 隐状态解耦推理与训练。

> **定位**：硬件无关的"Tensor 数据面"，覆盖推理（含多模态/MoE）+ 训练，生态最广、生产最成熟。

---

## 3. 给客户的推荐策略

### 3.1 决策框架（按优先级回答 4 个问题）

**Q1 — 客户的加速卡是什么？**（最硬的约束）
- 昇腾为主 → MemCache 有原生优势（或 Mooncake 的 ascend transport 也行，但 MemCache 的 OneCopy RH2D 更极致）。
- NVIDIA / AMD / 异构多家 → **直接 Mooncake**，MemCache 不支持。

**Q2 — 是否有"自主可控/国产化"硬性要求？**
- 有 → MemCache（华为全栈 + MulanPSL2）更贴合国产化口径；若同时要跨厂商，Mooncake 的华为/寒武纪/摩尔线程后端可作为补充。
- 无 → Mooncake。

**Q3 — 负载类型？**
- 纯 LLM 推理 KV 共享 → 两者皆可，按硬件定。
- **生成式推荐 (GR)** 或显存级超低时延 → **MemCache**（这是它的特色主场）。
- 大规模 **MoE / 多模态 / PD 分离 / agentic 多轮** → **Mooncake**（EP/PG、HiCache、EPD、prefix pool 都现成）。
- **训练侧**（checkpoint、RL 权重同步）→ **Mooncake**（MemCache 基本不覆盖）。

**Q4 — 集成栈与成熟度风险偏好？**
- 客户已用 SGLang / vLLM / TRT-LLM → Mooncake 集成最深、文档最全、`pip` 即装。
- 客户已用 vllm-ascend 且团队有昇腾调优能力 → MemCache。
- 偏好"生产验证过、社区活跃、论文背书" → Mooncake（Kimi 生产 + FAST25）。MemCache 2025/11 才开源，相对新、生态还在起步。

### 3.2 推荐速查矩阵

| 客户画像 | 首选 | 理由 |
|---|---|---|
| 昇腾 + 推理为主 + 追求极致时延/国产化 | **MemCache** | 原生 OneCopy、HBM 池、GR 场景 |
| 昇腾 + vllm-ascend 已落地 | MemCache（首选）/ Mooncake store（备选） | 两者都进了 vllm-ascend |
| NVIDIA/AMD/异构 + 大规模 LLM 服务 | **Mooncake** | 硬件无关、生态最广、生产验证 |
| 需要 MoE 容错 / 多模态 EPD / 训练 RL | **Mooncake** | EP/PG + checkpoint + RL 权重同步，MemCache 无 |
| 不确定 / 想快速 POC | **Mooncake** | pip 装得上手快、SGLang/vLLM 一条龙、风险最低 |

### 3.3 落地建议（渐进策略）

1. **先 Mooncake 做 POC**：生态成熟、安装快、SGLang/vLLM 集成现成，能最快验证"KV 共享到底能给客户省多少 prefill、提多少吞吐"，用数据说话。
2. **若是昇腾环境且 POC 达不到时延目标**，再换/加 MemCache 评估 OneCopy RH2D 的增益——把"显存级跨机直访"作为差异化卖点去压测对比。
3. **避免二选一的误区**：两者并不互斥。Mooncake store 已支持昇腾 transport，理论上可以"用 Mooncake 的对象模型 + 昇腾传输后端"组合；MemCache 则适合作为昇腾推理栈内的专用 KV pool。建议表述为"**通用基础设施工具 vs 昇腾专用加速件**"，而不是非此即彼。

---

## 附录：能力对照总表

| 能力维度 | MemCache | Mooncake |
|---|---|---|
| 传输底座 | MemFabric（昇腾专用） | Transfer Engine（通用，15+ transport） |
| 跨机跨介质直访 | ✅ OneCopy D2RH/RH2D（核心卖点） | ✅ 零拷贝 GPUDirect RDMA（通用） |
| 显存(HBM)池化 | ✅ 一级池成员 | ✅ device 内存支持 |
| SSD/持久化分级 | ✅ 有 SSD 支持 | ✅ SPDK/cachelib/3FS，机制更完整 |
| 副本 | ✅ 多副本 | ✅ best-effort 多副本 + slice 级放置 |
| 一致性 | 对象级 | 强一致 Get + lease 保护 |
| 淘汰策略 | 高低水位 | LRU + lease + soft/hard pin + 对象组 + 多租户 |
| 高可用 | K8S ClusterIP + Lease | etcd + leader 选举 + snapshot/restore |
| 加入模型 | rank/world_size 集合式 | 段级动态 mount/unmount |
| MoE 专家并行 | ❌ | ✅ EP/PG（容错） |
| 训练支持 | ❌ | ✅ checkpoint-engine / RL 权重 / TorchSpec |
| 硬件厂商 | 昇腾 + 鲲鹏 | 十余家（含昇腾） |
| 推理框架集成 | vllm-ascend | SGLang/vLLM/TRT-LLM/LMCache/LMDeploy/NIXL… |
| 开源成熟度 | 2025/11 开源，较新 | FAST'25 最佳论文，Kimi 生产验证 |
| 许可证 | MulanPSL2 | Apache 2.0 |

---

*本报告基于两份开源仓库（MemCache @ gitcode.com/Ascend/memcache、Mooncake @ github.com/kvcache-ai/Mooncake）的实证分析生成。*
