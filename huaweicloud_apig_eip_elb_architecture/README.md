# 华为云 APIG 挂载 EIP 与 ELB 架构分析报告

> 分析时间：2026-04-26

## 核心结论

**EIP 和 APIG 之间是否需要 ELB，取决于公网带宽需求：**

| 带宽需求 | 是否需要 ELB | 架构 |
|---------|------------|------|
| **≤ 2 Gbps** | **不需要** | EIP → APIG 实例（直接绑定） |
| **> 2 Gbps** | **需要** | EIP(共享带宽) → 独享型 ELB → APIG 实例 |

---

## 一、背景

APIG（API 网关）专享版实例部署在用户 VPC 内，默认仅支持 VPC 内部访问。如需从公网访问 API，需要为实例配置公网入口，通常通过绑定**弹性公网 IP（EIP）**实现。

---

## 二、常规场景：≤ 2 Gbps（不需要 ELB）

### 架构图

```
Internet ──→ EIP(≤2Gbps) ──→ APIG专享版实例(内置ELB) ──→ 后端服务(ECS/CCE)
```

### 说明

- APIG 专享版实例**内部自带 ELB**（创建实例时自动创建），具备负载均衡和高可用能力
- 在 APIG 控制台的实例详情页中，可直接**绑定 EIP**，无需额外创建 ELB
- 绑定后通过 `https://<EIP>:<端口>` 即可公网访问
- 默认端口通常为 HTTPS: **8443**、HTTP: **8080**

### 操作步骤

1. 创建 APIG 专享版实例，选择 VPC 和子网
2. 进入实例详情页 →「弹性公网 IP」→「绑定 EIP」
3. 确保安全组入方向放通对应端口
4. 通过 EIP 地址公网访问 API

---

## 三、高带宽场景：> 2 Gbps（必须引入 ELB）

### 为什么需要 ELB

- 华为云**单个 EIP 的最大带宽上限为 2000 Mbit/s（2 Gbps）**
- 直接将 EIP 绑到 APIG 实例时，公网带宽受限于 EIP 上限，无法突破 2 Gbps
- **共享带宽**可以超过 2 Gbps，但共享带宽需要绑定到 ELB 或 NAT 网关上，不能直接绑 APIG
- 独享型 ELB 支持绑定共享带宽，单个 ELB 实例可承载更高吞吐

### 推荐架构

```
客户端 (Internet)
       │
       ▼
  EIP (加入共享带宽, 如 5Gbps/10Gbps)
       │
       ▼
  独享型 ELB (七层监听器)
       │
       ├──── APIG 专享版实例 1
       ├──── APIG 专享版实例 2  (按需横向扩展)
       └──── APIG 专享版实例 N
              │
              ▼
         后端服务 (ECS / CCE)
```

### 操作步骤

1. 创建**共享带宽**（可设置 > 2 Gbps 的总带宽）
2. 创建**独享型 ELB**（七层/四层），将 EIP 绑到 ELB 上（EIP 加入共享带宽）
3. ELB 后端服务器组中添加 APIG 实例的内网地址
4. APIG 实例**不再单独绑 EIP**，改为纯内网模式
5. 配置 ELB 监听器端口与 APIG 实例端口对应

---

## 四、其他需要 ELB 的场景

即使带宽不超过 2 Gbps，以下场景也可能需要额外引入 ELB：

| 场景 | 原因 |
|------|------|
| **WAF 防护** | ELB 可以绑定 WAF（Web 应用防火墙），APIG 自身不直接绑 WAF |
| **多服务混合入口** | 一个公网入口同时路由到 APIG 和其他服务 |
| **SSL 证书统一管理** | 在 ELB 层统一做 SSL 卸载和证书管理 |
| **多实例负载均衡** | 多个 APIG 实例需要统一入口做流量分发 |
| **高级路由** | 需要基于域名/路径的复杂路由规则 |

---

## 五、决策速查

```
公网带宽需求 ≤ 2 Gbps？
  │
  ├── 是 → EIP 直接绑 APIG，不加 ELB     ✅ 简单省钱
  │
  └── 否 → 必须用 独享型ELB + 共享带宽
             │
             ├── 单个 APIG 能扛住流量？
             │     ├── 是 → ELB → 单个 APIG
             │     └── 否 → ELB → 多个 APIG 横向扩展
             │
             └── 还要 WAF？
                   └── ELB 前面串联 WAF
```

---

## 六、注意事项

1. **安全组配置**：无论哪种方案，确保 APIG 实例所在安全组入方向放通了对应端口
2. **EIP 费用**：EIP 会产生带宽/流量费用，共享带宽另计
3. **域名与证书**：使用自定义域名时，需在 APIG 中绑定域名并上传 SSL 证书
4. **APIG 认证**：绑定 EIP 暴露公网后，务必配置认证方式（APP认证/IAM认证），避免未授权访问
5. **APIG 流控**：建议配置访问控制和流量控制策略，保护后端服务
6. **区域差异**：EIP 和共享带宽的具体上限可能因区域而异，建议在控制台确认

---

## 七、参考资料

- [API网关 APIG 用户指南](https://support.huaweicloud.com/usermanual-apig/apig_03_0001.html)
- [APIG业务使用流程](https://support.huaweicloud.com/usermanual-apig/apig_03_0001.html)
- [弹性公网 IP EIP 文档](https://support.huaweicloud.com/eip/index.html)
- [弹性负载均衡 ELB 文档](https://support.huaweicloud.com/elb/index.html)
- [EIP 产品页](https://www.huaweicloud.com/product/eip.html)
- [ELB 产品页](https://www.huaweicloud.com/product/elb.html)
