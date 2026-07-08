# 华为云测试类工具调研分析报告

> 调研来源：华为云帮助中心文档索引页（`support.huaweicloud.com`）及各产品文档/最佳实践。
> 调研日期：2026-07-07
> 覆盖场景：API 功能与性能、UI 功能与性能、网络故障模拟、网络抓包、数据迁移、数据一致性校验。

---

## 一、按测试场景归类的工具清单

华为云没有一个单独叫"测试工具"的产品族，测试能力分散在 **CodeArts（软件开发生产线）**、**网络（VPC/ASM）**、**数据（DRS/CDM/OMS/SMS）**、**运维（MAS/COC/AOM/APM）** 等多条产品线中。下表按场景归类。

### 1.1 场景总览矩阵

| 测试场景 | 主要工具/服务 | 定位 |
|---|---|---|
| API 功能测试 | **CodeArts APITest**（接口自动化）、**CodeArts API**（接口设计/调试/Mock） | 接口用例编排、断言、Mock 解依赖 |
| API 性能测试 | **CodeArts PerfTest**（原 CPTS，云性能测试服务） | 多协议大并发压测 |
| UI 功能测试 | **CodeArts TestPlan**（测试管理）、**App 兼容性测试平台**（移动端真机）；Web UI 自动化**无原生工具**，需流水线集成 Selenium/Playwright | 用例管理 + 移动端兼容；Web 端靠开源框架 |
| UI 性能测试 | 依赖 **APM**（前端性能/调用链）+ PerfTest 间接覆盖 | 无专门前端性能工具 |
| 网络故障模拟 | **应用服务网格 ASM**（故障注入 Abort/Delay）、**MAS-CAST 混沌工程 / COC 故障演练** | 服务网格级 + 系统级故障注入 |
| 网络抓包 | **VPC 流量镜像**、**VPC 流日志**、ECS/CCE 内 **tcpdump**；华为云 Stack 版另有 **CloudNetDebug** | 网络层镜像 + 实例内抓包 |
| 数据迁移 | **DRS**（数据库）、**CDM**（大数据批量）、**OMS**（对象存储）、**SMS**（主机 P2V/V2V）、**UGO**（库结构/语法）、**MgC**（迁移中心统一平台） | 全场景迁移 |
| 数据一致性校验 | **DRS 数据对比**（行数对比+内容对比）、**SMS 一致性校验**、**DataArts Studio 数据质量模块** | 迁移/同步后校验 |

### 1.2 各工具要点说明

**API 测试**
- **CodeArts APITest / CodeArts API**：支持接口用例编排、检查点（断言）、**API Mock**（一键生成 Mock 规则、内置函数造数），解决依赖服务未上线/不稳定问题。可通过 `CreateApiTestCase` API 批量建用例，对接流水线。
- **CodeArts PerfTest（CPTS）**：支持 HTTP/HTTPS/TCP/UDP/HLS/RTMP/WebSocket/MQTT 等；测试模型可还原大并发业务链路；产出 TPS、吞吐量、响应时延等专业报告。资源组分**共享资源组**（公网发起，华为云版上限 1000 并发 / 100Mbps；Stack 版 10 万并发）和**私有资源组**（租户隔离、支持内网/外网、弹性扩缩容）。

**UI 测试**
- **CodeArts TestPlan**：一站式测试管理，沉淀华为 30+ 年测试方法，覆盖计划/设计/执行/评估，含手工测试与接口自动化用例。
- **App 兼容性测试平台**：海量真机上从安装/启动/运行/功能/UI 多维度定位 App 兼容性问题。
- **Web 端 UI 自动化为缺口**：官方主推"测试金字塔"优先接口层，Web UI 自动化需在 CodeArts 流水线中自行集成 Selenium/Appium/Playwright。

**网络故障模拟**
- **ASM 故障注入**：在服务网格内对虚拟机/容器服务注入 **Abort（中止）** 和 **Delay（延迟）** 两类故障。
- **MAS-CAST 混沌工程 / COC 故障演练**：故障模式库 + 演练编排，支持内存高使用率、节点宕机等系统级故障，自动生成演练报告。
- 典型闭环：CCE/ECS（被测）+ CPTS（施压）+ AOM/APM（稳态度量）→ 评估容错能力（见华为云 InfoQ 可靠性测试实践）。

**网络抓包**
- **VPC 流量镜像**：把弹性网卡符合筛选条件的报文复制到目的网卡（送安全分析/IDS/排障），不侵入业务。
- **VPC 流日志**：记录五元组/方向/字节等元数据，辅助分析，但不能替代完整报文。
- **ECS/CCE 内 tcpdump**：仅限本实例网卡流量。
- **CloudNetDebug**：专业双向抓包，但属**华为云 Stack（私有云）**产品，公有云不提供。

**数据迁移**
- **DRS**：数据库实时迁移/增量同步，短暂故障可自动追平，迁移中源库可 DDL。
- **CDM**：大数据/批量迁移，不支持数据库实时增量。
- **OMS**：跨云对象存储（S3/OSS 等）→ OBS，HTTPS+KMS 加密，10+ 参数建任务。
- **SMS**：x86 物理机/虚机 → ECS（P2V/V2V）。
- **UGO**：异构数据库结构与 SQL 语法/存储过程/视图转换。
- **MgC（迁移中心）**：一站式统一迁移与现代化平台，承载方法论与最佳实践。

**数据一致性校验**
- **DRS 数据对比**：先**行数对比**（轻量），不一致再**内容对比**（定位差异），迁移管理界面可直接快捷对比。
- **SMS 一致性校验**：主机迁移场景的源/目的一致性。
- **DataArts Studio 数据质量模块**：数据仓库级（如 DWS→Hive）迁移前后质量对比。

---

## 二、当前使用情况

1. **API 性能测试（PerfTest/CPTS）成熟度最高**：是华为云内部和客户做接口/链路压测的主力，与 AOM/APM 形成可观测闭环，是混沌演练的"施压"基座。
2. **API 功能测试（APITest + Mock）普及中**：Mock 服务降低了对不稳定/未上线依赖的耦合，常用于流水线接口回归；体验版套餐下免费开放，门槛较低。
3. **测试管理（TestPlan）与 DevOps 深度绑定**：作为 CodeArts 一站式平台的一环，与需求/构建/部署打通，企业采用较广。
4. **Web UI 自动化是薄弱环节**：官方未提供原生 Web UI 自动化产品，实践中普遍"自建 Selenium/Playwright + 流水线"或采购第三方（如云测）。
5. **混沌工程（MAS-CAST/COC）属较新能力**：在金融等高可靠行业有白皮书/试点，但通用客户采用率低于压测类工具，更多停留在服务网格 ASM 层的轻量注入。
6. **网络抓包以"流量镜像 + 流日志"为主**：公有云没有产品化的抓包分析工具，深度报文分析仍依赖 ECS 内 tcpdump 或第三方 IDS。
7. **迁移工具体系最完整、最碎片**：DRS/CDM/OMS/SMS/UGO 各管一段，MgC 提供统一入口但侧重应用迁移与现代化。
8. **一致性校验高度依赖 DRS**：行数对比为主、内容对比为辅是主流做法；数仓场景另用 DataArts 数据质量。

---

## 三、依赖与存在的问题

### 3.1 API 测试类
| 工具 | 依赖 / 问题 |
|---|---|
| **APITest / CodeArts API** | ① Web 版接口调试受浏览器安全策略限制，**需先安装浏览器扩展**；② 关键字被用例引用时不可删除（错误码 `APITEST.00010021`）；③ 高级能力绑定 CodeArts 套餐；④ Mock 与真实服务行为可能不一致，导致"测得过但上线仍出问题"。 |
| **PerfTest / CPTS** | ① **共享资源组上限 1000 并发 / 100Mbps**，大流量/高并发成为瓶颈；② 共享组走公网，被测系统若有公网访问限制则**连不通**；③ **内网压测缺少公网链路因素**（CDN、全球延迟、弱网），与生产真实路径有偏差；④ 多种协议虽支持，但非 HTTP 类（TCP/UDP/MQTT）的脚本编排复杂度更高。 |

### 3.2 UI 测试类
- **无原生 Web UI 自动化工具**：需自行引入 Selenium/Playwright 并接入 CodeArts 流水线，脚本维护成本高、易碎（前端变更导致定位失效）。
- **移动端**仅 App 兼容性测试，缺少持续化的移动 UI 回归体系。
- **前端性能**没有专门工具，需借助 APM 间接度量。

### 3.3 网络故障模拟类
- **ASM 故障注入仅覆盖网格内服务**：未接入服务网格的应用无法注入。
- **MAS-CAST/COC 混沌平台较新**：故障类型库与编排/回滚能力相对竞品（如阿里云 AHAS Chaos）仍在完善。
- **缺少网络层统一混沌**：VPC 级别的丢包/延迟/抖动注入无单一产品化入口，需自行组合 ASM + 节点级脚本。

### 3.4 网络抓包类
- **VPC 流量镜像受集群类型制约**：仅 **CCE Turbo（云原生网络 2.0，Pod 独立 ENI）**可对单 Pod 镜像；**CCE Standard 集群 Pod 共享节点 ENI，无法对单个 Pod 镜像**。
- **镜像有筛选盲区**：特定协议/网段报文不会被镜像，可能漏抓。
- **集群类型创建后不可变更**（Standard 不能改 Turbo），早期选型失误会长期受限。
- **公有云缺产品化抓包分析工具**：CloudNetDebug 仅 Stack 版有；深度分析靠 tcpdump + 第三方。
- **流日志只给元数据**，不能还原完整报文内容。

### 3.5 数据迁移类
- **工具碎片化**：DRS/CDM/OMS/SMS/UGO 各自独立，跨场景需用户自行选型与串联；MgC 统一性仍以应用迁移为主。
- **异构兼容性问题**：跨数据库方言、对象、存储过程转换（UGO）可能不完整，需人工补齐。
- **网络打通前置依赖**：源端与华为云之间的专线/VPN/公网连通是迁移前提。
- **CDM 不支持数据库实时增量**，实时性要求高的库迁移只能用 DRS。

### 3.6 数据一致性校验类
- **DRS 对比有硬性时长限制**：单全量任务自动创建的对比任务**超时 30 分钟自动停止**，**大表无法在窗口内完成**，是典型痛点。
- **内容对比资源消耗大**：对源/目标库压力明显，常需低峰期执行。
- **缺少异构语义级校验**：跨库类型转换后的精度/时区/字符集差异，行数与逐行内容对比未必能发现。
- **DataArts 数据质量主要面向数仓**，不覆盖在线 DB 迁移场景。
- **API 调用超时 60 秒**、单次读取 20M、同表 DDL 需间隔 1 分钟等约束，限制自动化校验的批量执行。

---

## 四、改进建议

### 针对单点问题的改进

1. **PerfTest 突破并发/带宽瓶颈**：大流量场景默认走私有资源组并预留扩容；提供"公网+弱网模拟"一体化施压模型，弥补内网压测不真实的缺陷；开放 TCP/UDP/MQTT 的低代码编排模板。
2. **APITest 降低接入摩擦**：提供免浏览器扩展的云端代理调试；Mock 增加"录制回放"以贴近真实响应；放宽关键字删除约束（改为软删除/引用提示）。
3. **补齐 Web UI 自动化**：在 CodeArts 中提供原生或官方集成的 UI 自动化能力（Playwright 优先），引入 AI 自愈定位与用例生成，降低维护成本；前端性能指标纳入 TestPlan 报告。
4. **混沌能力统一化**：将 ASM 故障注入与 MAS-CAST/COC 演练统一为"网络+应用+基础设施"多维故障编排入口；扩充 VPC 网络层故障（丢包/延迟/抖动/带宽限制）的一键注入。
5. **网络抓包产品化**：把 CloudNetDebug 类能力下沉到公有云，提供"一键抓包 + 在线解析"的托管服务；放宽镜像筛选盲区；为 CCE Standard 提供 Pod 级流量采集替代方案（如 sidecar/ebpf），或在文档显著标注"抓包需选 Turbo"。
6. **迁移工具收敛入口**：以 MgC 为统一控制台，把 DRS/CDM/OMS/SMS/UGO 的选型、串联、进度、校验收口到一个工作流，减少用户在多个控制台切换。
7. **大表一致性校验破局**：取消/放宽 DRS 对比 30 分钟硬超时，改为**分片/分区分批对比 + 断点续跑**；提供哈希级/抽样一致性校验以兼顾大表与性能；增加异构库的类型映射差异专项检查。

### 体系化建议（横跨多工具）

8. **构建"测试-可观测"统一闭环**：把 PerfTest（施压）、MAS-CAST（故障）、AOM/APM（稳态度量）、DRS（数据校验）串成端到端可靠性流水线，一键产出"系统稳态画像"，避免工具孤岛。
9. **测试资产沉淀到 TestPlan**：API 用例、UI 脚本、混沌演练、迁移校验结果统一回流 TestPlan，形成可度量、可追溯的质量看板，支撑"测什么/如何测/如何评价"。
10. **完善最佳实践与样例库**：针对弱网、大表迁移、跨云对象存储、容器抓包等高频痛点，提供端到端参考实现（含 IaC 脚本），缩短用户从"知道有工具"到"用得好"的距离。

---

## 附：核心参考链接

- 索引页：<https://support.huaweicloud.com/intl/zh-cn/index.html>
- CodeArts PerfTest（CPTS）：<https://www.huaweicloud.com/product/cpts.html>｜使用流程：<https://support.huaweicloud.com/usermanualnew-cpts/cpts_02_0001.html>
- CodeArts TestPlan：<https://www.huaweicloud.com/product/cloudtest/getting-started.html>
- CodeArts API Mock：<https://support.huaweicloud.com/usermanual-codeartsapi/apiarts_01_0006.html>
- DRS 数据对比：<https://support.huaweicloud.com/realtimemig-drs/drs_02_0007.html>｜对比约束：<https://support.huaweicloud.com/realtimesyn-drs/drs_10_0012.html>
- CDM 与其他迁移服务区别：<https://support.huaweicloud.com/cdm_faq/cdm_01_0255.html>
- OMS：<https://www.huaweicloud.com/product/oms.html>｜SMS：<https://www.huaweicloud.com/product/sms.html>｜MgC：<https://www.huaweicloud.com/product/mgc.html>
- VPC 流量镜像：<https://support.huaweicloud.com/usermanual-vpc/vpc_mirror_02.html>
- ASM 故障注入：<https://support.huaweicloud.com/bestpractice-asm/asm_bestpractice_0015.html>
- 混沌工程 RES11-01：<https://support.huaweicloud.com/intl/zh-cn/usermanual-architecture/architecture_02_0075.html>｜MAS：<https://support.huaweicloud.com/usermanual-mas/mas_03_0096.html>
- AOM：<https://www.huaweicloud.com/product/aom.html>
