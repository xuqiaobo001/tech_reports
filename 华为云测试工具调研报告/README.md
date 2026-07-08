# 华为云测试工具调研测试报告

| 项目 | 内容 |
|---|---|
| 报告名称 | 华为云测试工具调研测试报告 |
| 调研对象 | 华为云帮助中心文档（`support.huaweicloud.com`）覆盖的全部云服务 |
| 调研日期 | 2026-07-07 |
| 覆盖测试场景 | API 功能与性能、UI 功能与性能、网络故障模拟、网络抓包、数据迁移、数据一致性校验 |
| 报告版本 | V1.0（合并版） |

---

## 执行摘要

本报告通过深入华为云各云服务（ECS / RDS / DDS / GeminiDB / DCS / DMS / ELB / CCE / VPC 对等连接 / 云连接 CC / DRS / ASM / MAS / CodeArts 等）的**文档正文**，横向对比各服务在测试/验证时实际依赖的工具，回答两个核心问题：

1. **不同云服务测试时，是否存在公共/通用的测试工具？**
   → **存在，但只集中在最底两层**：连通性层（ping / telnet / MTR / curl / nslookup）与 API 调试层（API Explorer）。其中**真正华为原生、覆盖全服务的公共工具只有 API Explorer 一个**，其余连通性工具均为标准 OS 工具的复用。

2. **针对六大测试场景，华为云提供了哪些工具？**
   → 高阶场景（性能、UI、故障注入、迁移、一致性校验）**没有跨服务的公共工具**，均为独立产品或未覆盖。

**核心判断**：华为云目前**没有统一的"测试平面"**——底层靠通用 OS 工具拼装、上层靠一个个独立产品支撑，测试能力呈碎片化形态。这是当前最大的系统性问题。

---

## 一、调研背景与目标

- **背景**：在测试华为云不同云服务时，测试人员需要为每个服务学习一套测试方法与工具，重复学习成本高、工具散落。
- **目标**：
  1. 盘点六大测试场景下华为云可用的工具/服务；
  2. 横向分析这些工具是否可被不同服务复用（即"公共测试工具"是否存在）；
  3. 总结使用现状、依赖与问题；
  4. 给出体系化改进建议。

---

## 二、调研范围与方法

- **范围**：计算（ECS/CCE）、网络（VPC/ELB/CC/EIP）、数据库（RDS/DDS/GeminiDB）、缓存与消息（DCS/DMS）、数据迁移（DRS/CDM/OMS/SMS/UGO/MgC）、安全与可观测（AOM/APM）、测试与运维（CodeArts/ASM/MAS/COC）。
- **方法**：抓取各服务"测试 / 连通性 / 验证 / 调试"相关文档正文 → 提取每个服务测试时使用的工具 → 横向对比识别复用模式。

---

## 三、核心发现：公共测试工具是否存在？

### 3.1 跨服务横向对比（各服务测试时用什么）

| 云服务 | 连通性测试 | 功能/应用层测试 | API 测试 | 备注 |
|---|---|---|---|---|
| **ECS** | ping / telnet / MTR(WinMTR) / traceroute | curl | API Explorer | ping 需安全组放 ICMP |
| **RDS** | ping（需放 ICMP，部分禁 ping）、telnet | mysql / psql 客户端 | API Explorer | "ping→telnet→客户端"三步标准 |
| **DDS / GeminiDB** | ping（云 HBase 等禁 ping）、telnet | 各自客户端 | API Explorer | 必须 telnet 兜底 |
| **DCS（Redis）** | ping / telnet 6379 | redis-cli | API Explorer | 同 VPC 内网优先 |
| **DMS（Kafka 等）** | ping / telnet；跨 VPC 用 VPCEP | Kafka 控制台/客户端 | API Explorer | 公网建议 SSL |
| **ELB** | curl 复现健康检查路径 | 内置健康检查 | API Explorer | 后端可用性靠 curl 验证 |
| **CCE** | kubectl 起 busybox→ping/curl/nslookup | kubectl exec / curl Service | API Explorer | 临时调试 Pod 是通用套路 |
| **VPC 对等连接 / 云连接 CC** | ping / telnet + 路由表检查 | —— | —— | 验证跨 Region/跨 VPC 打通 |
| **DRS** | 内置"测试连接" + ping / telnet | 数据对比（行数/内容） | API Explorer | 少数自带连通性按钮的服务 |
| **几乎所有服务** | —— | —— | **API Explorer** | 唯一真正全服务通用工具 |

### 3.2 公共 vs 非公共工具判定

| 工具 | 覆盖范围 | 是否公共 | 是否华为原生 |
|---|---|---|---|
| **ping / telnet / MTR / curl / nslookup** | 几乎所有服务 | ✅ 公共（被反复复用） | ❌ 标准 OS 工具 |
| **API Explorer** | 全量服务 API | ✅ 公共 | ✅ **唯一原生通用工具** |
| 各服务内置"测试连通性"按钮 | 零散（DRS 等少数） | ❌ 不公共（UX 不统一） | ✅ |
| 各服务客户端（mysql/redis-cli/kubectl…） | 单服务 | ❌ 不公共 | 部分 |
| 性能/迁移/故障注入/一致性 | 独立产品 | ❌ 不公共 | ✅ |

**结论**：公共性只存在于**连通性层 + API 调试层**；更高阶场景无公共工具。

---

## 四、按六大测试场景的工具清单

### 4.1 API 功能与性能
- **功能测试**：CodeArts APITest（接口自动化 + 检查点 + Mock）、**API Explorer**（全服务通用调试）、curl。
- **性能测试**：CodeArts PerfTest（原 CPTS）——支持 HTTP/HTTPS/TCP/UDP/HLS/RTMP/WebSocket/MQTT；共享资源组上限 1000 并发 / 100Mbps，私有资源组支持内/外网与弹性扩缩容。

### 4.2 UI 功能与性能
- **功能测试**：CodeArts TestPlan（测试管理）、App 兼容性测试平台（移动端真机）；**Web UI 自动化无原生工具**，需在流水线集成 Selenium/Playwright。
- **性能测试**：无专门前端性能工具，依赖 APM 间接度量。

### 4.3 网络故障模拟
- ASM（应用服务网格）故障注入：Abort（中止）/ Delay（延迟）——**仅覆盖网格内服务**。
- MAS-CAST 混沌工程 / COC 故障演练：内存高使用率、节点宕机等系统级故障。

### 4.4 网络抓包
- VPC 流量镜像（网络层，**仅 CCE Turbo 的 Pod 独立 ENI 可镜像**；CCE Standard 不行）。
- VPC 流日志（仅元数据）、ECS/CCE 内 tcpdump。
- CloudNetDebug（专业双向抓包，但**仅华为云 Stack 版**，公有云不提供）。

### 4.5 数据迁移
- DRS（数据库实时迁移/增量同步）、CDM（大数据批量）、OMS（对象存储）、SMS（主机 P2V/V2V）、UGO（库结构/语法转换）、MgC（迁移中心统一平台）。

### 4.6 数据一致性校验
- DRS 数据对比（行数对比 + 内容对比）、SMS 一致性校验、DataArts Studio 数据质量模块（数仓级）。

---

## 五、当前使用情况

1. **"ping + telnet" 是事实标准**：几乎每篇连通性文档都按"放通安全组 → ping → telnet → 客户端连接"三步走，是最高频公共套路。
2. **API Explorer 是开发/API 测试主力**：免登录、全量服务、在线调试 + 代码生成。
3. **MTR 用于公网链路排障**：ECS/EIP "Ping 不通/丢包"的标准公共手段。
4. **curl 复现健康检查**：ELB 后端、CCE Service 验证的常规做法。
5. **性能测试（PerfTest）成熟度最高**：与 AOM/APM 形成可观测闭环，是混沌演练的施压基座。
6. **迁移工具体系最完整但最碎片**：DRS/CDM/OMS/SMS/UGO 各管一段，MgC 提供统一入口但侧重应用迁移。
7. **Web UI 自动化是薄弱环节**：无原生产品，靠自建或第三方。
8. **混沌工程较新**：通用客户采用率低于压测类工具。

---

## 六、依赖与存在的问题

### 6.1 公共连通性工具（ping/telnet/MTR）的问题
- **不感知业务语义**：只测 L3/L4 通断，测不出鉴权、SSL、连接数上限、超时——"端口通了但连不上"是常态。
- **强依赖安全组/网络 ACL**：ping 需放 ICMP、telnet 需放端口，配置不对会误判为不通。
- **禁 ping 规则不统一**：云 HBase、部分 GeminiDB 连接地址禁 ping，规则散落各文档。
- **纯手动、无编排无报告**：每个测试一次性命令，结果散落，无跨服务汇总。

### 6.2 API Explorer 的问题
- 只覆盖 API 层；偏向单次调试，缺批量用例编排/断言/回归（该能力在 CodeArts APITest，二者未打通）；只验证"接口能调通"，不验证业务正确性。

### 6.3 性能测试（PerfTest）的问题
- 共享资源组上限 1000 并发 / 100Mbps；走公网，被测系统有访问限制时连不通；内网压测缺公网链路因素（CDN/弱网），不够真实。

### 6.4 网络抓包的问题
- 流量镜像受集群类型制约（仅 CCE Turbo 可对单 Pod 镜像）；集群类型创建后不可变更；公有云缺产品化抓包分析工具；流日志只给元数据。

### 6.5 数据一致性校验的问题
- DRS 对比硬超时 30 分钟（大表无法完成）；内容对比资源消耗大；缺异构语义级校验（类型/精度/时区差异难发现）。

### 6.6 公共性整体缺失的问题
- **没有统一测试平面**：测 ECS、测 RDS、测 CCE 各学一套；"测试连通性"按钮有/无、入口、参数不一致；网络抓包靠 OS 工具 + 流量镜像拼凑；故障注入仅限 ASM 网格内。

---

## 七、改进建议

### 7.1 针对公共工具
1. **建设"服务感知的统一连通性诊断中心"**：一键对任意服务做"网络可达 → 端口可达 → 鉴权可达 → 延迟/丢包"四级测试，自动提示安全组/ACL，输出统一报告，取代散落的手工命令。
2. **统一"测试连通性"按钮**：所有 DB/中间件/数据类服务提供一致入口与参数，覆盖当前缺按钮的服务。
3. **API Explorer 与 CodeArts APITest 打通**：把单次调试升级为可保存用例、可断言、可回归的测试套件。
4. **服务感知测试客户端**：提供统一 DB 连接测试器（鉴权 + SSL + 延迟），而非仅 telnet 端口。

### 7.2 针对单点产品
5. **PerfTest**：默认私有资源组 + 扩容，增加公网弱网模拟模型；开放 TCP/UDP/MQTT 低代码编排模板。
6. **Web UI 自动化**：CodeArts 补齐原生 UI 自动化（Playwright 优先 + AI 自愈定位）。
7. **混沌能力统一化**：ASM + MAS-CAST/COC 合并为"网络+应用+基础设施"多维故障编排；扩 VPC 网络层故障（丢包/延迟/抖动）一键注入。
8. **网络抓包产品化**：CloudNetDebug 能力下沉公有云，提供"一键抓包 + 在线解析"托管服务。
9. **迁移工具收敛入口**：以 MgC 统一 DRS/CDM/OMS/SMS/UGO 选型、串联、进度、校验。
10. **大表一致性破局**：DRS 对比改分片分批 + 断点续跑/哈希抽样；增异构库类型映射差异专项检查。

### 7.3 体系化建议
11. **构建"测试-可观测"统一闭环**：PerfTest（施压）→ MAS-CAST（故障）→ AOM/APM（稳态度量）→ DRS（校验）端到端串联，一键产出"系统稳态画像"。
12. **测试资产沉淀到 TestPlan**：API 用例、UI 脚本、混沌演练、迁移校验结果统一回流，形成可度量质量看板。
13. **标准化测试文档结构**：所有服务统一"连通性 → 功能 → API → 一致性"章节，降低跨服务学习成本。

---

## 八、结论

- 华为云的**公共测试工具是存在的**，但仅限连通性层（ping/telnet/MTR/curl/nslookup，多为 OS 工具）与 API 调试层（API Explorer，唯一原生通用工具）。
- 在**性能、UI、故障注入、迁移、一致性校验**等高阶场景下，**没有跨服务的公共测试工具**，均为独立产品或未覆盖。
- 当前最大的系统性问题是**缺少统一的"测试平面"**，导致工具碎片化、学习成本高、测试结果难汇总。
- **首要改进方向**：建设"服务感知的统一连通性诊断中心 + 跨服务测试中心"，把散落的 OS 工具与独立产品收口到一个面向测试人员的统一平面。

---

## 附录：核心参考链接

- 文档索引：<https://support.huaweicloud.com/intl/zh-cn/index.html>
- API Explorer（全服务 API 调试）：<https://support.huaweicloud.com/usermanual-ivs/ivs_10_0005.html>
- Ping 不通/丢包链路测试（MTR/WinMTR）：<https://support.huaweicloud.com/trouble-ecs/zh-cn_topic_0191526703.html>
- 安全组配置（ping/telnet 放通）：<https://support.huaweicloud.com/usermanual-ecs/zh-cn_topic_0140323152.html>
- RDS 公网 ping 不通排查：<https://support.huaweicloud.com/rds-mysql_faq/rds_faq_0067.html>
- 云连接配置后测试连通性：<https://support.huawei.com/enterprise/zh/doc/EDOC1100295639/d3d99d6e>
- DRS 数据对比与约束：<https://support.huaweicloud.com/realtimesyn-drs/drs_10_0012.html>
- CodeArts PerfTest（CPTS）：<https://www.huaweicloud.com/product/cpts.html>
- CodeArts API Mock：<https://support.huaweicloud.com/usermanual-codeartsapi/apiarts_01_0006.html>
- VPC 流量镜像：<https://support.huaweicloud.com/usermanual-vpc/vpc_mirror_02.html>
- ASM 故障注入：<https://support.huaweicloud.com/bestpractice-asm/asm_bestpractice_0015.html>
- MAS-CAST 混沌工程：<https://support.huaweicloud.com/usermanual-mas/mas_03_0096.html>
- ELB 健康检查：<https://support.huaweicloud.com/usermanual-elb/elb_ug_shdg_0002_01.html>
- CCE kubectl 连接：<https://support.huaweicloud.com/usermanual-cce/cce_10_0107.html>
- MgC 迁移中心：<https://www.huaweicloud.com/product/mgc.html>
