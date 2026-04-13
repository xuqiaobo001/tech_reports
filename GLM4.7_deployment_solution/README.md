# 华为云 ModelArts GLM-4.7-Air-35B 私有化部署方案

## 一、方案概述

本方案基于**华为云 ModelArts 专属资源池**部署 GLM-4.7-Air-35B 大模型，通过**云专线（Direct Connect）/ VPN 网关**与客户自有机房打通私有网络，对外提供稳定可靠的 RESTful API 推理服务。

### 核心目标

- 提供私有化、独享的大模型推理 API 服务
- 客户机房与华为云之间安全可靠的专线/VPN互联
- 高可用、可弹性伸缩的推理服务架构
- 完善的监控、日志和安全防护体系

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         客户自有机房                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                        │
│  │ 业务应用  │   │ API 网关  │   │ 运维监控  │                        │
│  └────┬─────┘   └────┬─────┘   └──────────┘                        │
│       │              │                                              │
│       └──────┬───────┘                                              │
│              │                                                       │
│     ┌────────┴────────┐                                             │
│     │  客户侧路由器/    │                                             │
│     │  防火墙设备       │                                             │
│     └────────┬────────┘                                             │
└──────────────┼──────────────────────────────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │  云专线(DC) / VPN     │  ← 高速专线 或 IPsec VPN 加密隧道
    └──────────┬──────────┘
               │
┌──────────────┼──────────────────────────────────────────────────────┐
│         华为云 VPC（私有网络）                                        │
│              │                                                       │
│     ┌────────┴────────┐                                             │
│     │   虚拟网关 (VGW)  │  ← 专线虚拟网关 / VPN网关                   │
│     └────────┬────────┘                                             │
│              │                                                       │
│     ┌────────┴────────┐                                             │
│     │  WAF (Web应用     │  ← 防SQL注入/XSS/CC攻击                    │
│     │   防火墙)         │                                             │
│     └────────┬────────┘                                             │
│              │                                                       │
│     ┌────────┴────────┐                                             │
│     │  APIG (API网关)   │  ← 统一入口、限流、鉴权、计费               │
│     └────────┬────────┘                                             │
│              │                                                       │
│     ┌────────┴────────┐                                             │
│     │  ELB (弹性负载    │  ← 流量分发、健康检查                       │
│     │       均衡)       │                                             │
│     └────────┬────────┘                                             │
│              │                                                       │
│  ┌───────────┼────────────┐                                         │
│  │           │            │                                         │
│  ▼           ▼            ▼                                         │
│ ┌────────┐ ┌────────┐ ┌────────┐  ← ModelArts 专属资源池             │
│ │推理节点 │ │推理节点 │ │推理节点 │     (昇腾 NPU / GPU 集群)          │
│ │ GLM-4.7│ │ GLM-4.7│ │ GLM-4.7│                                    │
│ └────────┘ └────────┘ └────────┘                                     │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐                                 │
│  │  OBS 对象存储  │  │  CTS/CTS 监控 │                                │
│  │ (模型权重存储) │  │  (日志审计)   │                                │
│  └──────────────┘  └──────────────┘                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、详细部署方案

### 3.1 基础资源层 — 计算资源规划

#### 3.1.1 ModelArts 专属资源池

| 配置项 | 推荐规格 | 说明 |
|--------|---------|------|
| 资源池类型 | 专属资源池（Dedicated Pool） | 独享计算资源，不与其他用户共享 |
| 计算规格 | **Ascend 910B**（snt9b）或 **NVIDIA A100/H800** | GLM-4.7-Air-35B 推理推荐 NPU/GPU |
| 节点数量 | 最少 **2 节点**（生产推荐 3+ 节点） | 保障高可用 |
| 存储 | 挂载 SFS Turbo 或 EVS 高性能云盘 | 模型权重加载 |
| 网络 | 配置 ModelArts 网络关联到指定 VPC | 与 VPC 内其他服务互通 |

> **GLM-4.7-Air-35B** 模型约需 **70GB+ 显存**（FP16），推荐使用 INT4/INT8 量化推理以降低资源需求。单节点建议配置至少 1 张 Ascend 910B 或 2×A100-80G。

#### 3.1.2 模型权重管理

```bash
# 模型权重存储路径规划
OBS Bucket 结构:
  obs://your-bucket/
  ├── models/
  │   └── glm-4.7-air-35b/
  │       ├── config.json
  │       ├── model-00001-of-000XX.safetensors
  │       ├── tokenizer.model
  │       └── ...
  ├── custom-images/         # 自定义推理镜像
  └── logs/                  # 推理日志归档
```

#### 3.1.3 推理镜像制作

```dockerfile
# 自定义推理镜像 Dockerfile 示例
FROM swr.cn-north-4.myhuaweicloud.com/modelarts/infer-pytorch:npu-ubuntu20.04-py3.10

# 安装推理框架依赖
RUN pip install transformers==4.45.0 \
    && pip install vllm==0.6.0 \
    && pip install accelerate

# 拷贝推理启动脚本
COPY serve.py /opt/serve.py
COPY start.sh /opt/start.sh

# 设置模型路径环境变量
ENV MODEL_PATH=/opt/models/glm-4.7-air-35b
ENV MODEL_NAME=glm-4.7-air-35b

# 暴露推理服务端口
EXPOSE 8080

CMD ["python", "/opt/serve.py", "--host", "0.0.0.0", "--port", "8080"]
```

```python
# serve.py - 推理服务启动脚本（简化示例）
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import os

app = FastAPI()

model_path = os.environ.get("MODEL_PATH", "/opt/models/glm-4.7-air-35b")
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    trust_remote_code=True,
    torch_dtype=torch.float16,
    device_map="auto"
)
model.eval()

class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9

class ChatResponse(BaseModel):
    id: str
    choices: list[dict]
    usage: dict

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    inputs = tokenizer.apply_chat_template(
        request.messages, return_tensors="pt"
    ).to(model.device)
    with torch.no_grad():
        outputs = model.generate(
            inputs,
            max_new_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
        )
    response = tokenizer.decode(outputs[0][inputs.shape[1]:], skip_special_tokens=True)
    return {
        "id": f"chatcmpl-{os.urandom(4).hex()}",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": response}}],
        "usage": {"prompt_tokens": inputs.shape[1], "completion_tokens": len(outputs[0]) - inputs.shape[1]}
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

---

### 3.2 网络层 — 云端与机房互联

#### 3.2.1 方案对比

| 维度 | 云专线 (Direct Connect) | VPN 网关 |
|------|------------------------|----------|
| 带宽 | 1Gbps ~ 100Gbps，可按需扩容 | 取决于公网带宽，通常 100Mbps~1Gbps |
| 延迟 | 低（<5ms 同城） | 较高（受公网波动影响） |
| 安全性 | 物理隔离，最高等级 | IPsec 加密隧道，较高 |
| 成本 | 较高（需运营商专线费用） | 较低（仅需 VPN 网关费用） |
| 部署周期 | 2~4 周（物理线路施工） | 1~2 天 |
| 适用场景 | 生产环境、高吞吐、低延迟 | 测试环境、低频调用、备份线路 |

#### 3.2.2 推荐：专线主 + VPN 备（双链路高可用）

```
主链路：客户机房 ←── 运营商专线 ──→ 华为云 Direct Connect 接入点
备链路：客户机房 ←── IPsec VPN ──→ 华为云 VPN 网关
```

#### 3.2.3 网络配置步骤

**Step 1 — 创建 VPC**

```bash
# 使用华为云 CLI 创建 VPC
hcloud VPC CreateVpc/v3 --cli-region=cn-north-4 \
  --body '{
    "vpc": {
      "name": "vpc-modelarts-glm",
      "cidr": "10.10.0.0/16"
    }
  }'

# 创建子网
hcloud VPC CreateSubnet/v3 --cli-region=cn-north-4 \
  --body '{
    "subnet": {
      "name": "subnet-modelarts",
      "cidr": "10.10.1.0/24",
      "vpc_id": "<vpc-id>",
      "gateway_ip": "10.10.1.1"
    }
  }'
```

**Step 2 — 配置云专线（Direct Connect）**

1. 在华为云控制台购买物理连接（自建专线 / 一站式接入）
2. 创建虚拟网关，绑定目标 VPC
3. 创建虚拟接口，配置：
   - 本端网关（华为云侧）：`10.10.254.1/30`
   - 远端网关（客户侧）：`10.10.254.2/30`
   - 远端子网：客户机房网段（如 `172.16.0.0/16`）

**Step 3 — 配置 VPN 备份链路**

1. 创建 VPN 网关，绑定 VPC
2. 创建客户网关（填入客户机房公网 IP）
3. 创建 VPN 连接，配置 IKE/IPsec 策略

**Step 4 — 配置路由**

```bash
# VPC 路由表添加回程路由
# 目的地址: 172.16.0.0/16 (客户机房网段)
# 下一跳: 虚拟网关 / VPN 网关
```

#### 3.2.4 VPC 直连高速通道

通过 ModelArts 专属资源池的 **VPC 直连高速访问通道**，推理服务可直接通过 VPC 内网地址访问，无需经过公网，降低延迟、提升安全性。

---

### 3.3 服务层 — API 网关与负载均衡

#### 3.3.1 API 网关（APIG）配置

| 配置项 | 推荐值 |
|--------|--------|
| 网关类型 | **专属 APIG**（部署在客户 VPC 内） |
| 鉴权方式 | API Key + IAM AK/SK 双重认证 |
| 限流策略 | 按租户/按 API 级别限流（如 100 req/s） |
| 请求大小 | 最大 32MB（支持长文本输入） |
| 超时时间 | 300s（大模型生成长文本需较长超时） |

```bash
# API 路由设计
POST   /v1/chat/completions     # 对话补全（主要接口）
POST   /v1/completions          # 文本补全
GET    /v1/models               # 模型列表
GET    /health                  # 健康检查
```

#### 3.3.2 弹性负载均衡（ELB）

```
                    ┌─────────────┐
                    │   ELB VIP   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴─────┐ ┌───┴─────┐
        │ 推理节点1  │ │推理节点2│ │推理节点3│
        │ :8080     │ │ :8080   │ │ :8080   │
        └───────────┘ └─────────┘ └─────────┘

ELB 配置:
- 算法: 加权轮询（Weighted Round Robin）
- 健康检查: GET /health，间隔 10s，超时 5s
- 会话保持: 关闭（无状态服务）
- 后端协议: HTTP
- 前端协议: HTTPS（TLS 1.2+）
```

#### 3.3.3 Web 应用防火墙（WAF）

在 APIG 前部署 WAF，防护：
- SQL 注入、XSS 攻击
- CC 攻击（恶意高频调用）
- 恶意 Payload 注入（Prompt Injection 防护）

---

### 3.4 高可用设计

#### 3.4.1 多层级冗余

| 层级 | 冗余策略 |
|------|---------|
| 网络层 | 专线主 + VPN 备双链路，虚拟网关主备冗余 |
| 接入层 | APIG 多节点 + WAF 多实例 |
| 负载层 | ELB 双可用区部署 |
| 推理层 | 至少 3 个推理节点，跨可用区分布 |
| 存储层 | OBS 多 AZ 冗余，SFS Turbo 双活 |

#### 3.4.2 故障自愈

```
推理节点故障:
  ELB 健康检查检测到节点不可用 → 自动剔除故障节点
  → ModelArts 自动重启/迁移推理实例 → ELB 重新加入

专线中断:
  BGP/路由检测 → 自动切换到 VPN 备用链路 → 专线恢复后自动回切
```

#### 3.4.3 弹性伸缩

```yaml
# 伸缩策略示例
伸缩组:
  最小实例数: 2
  最大实例数: 8
  期望实例数: 3

扩容规则:
  - 条件: 平均 CPU 利用率 > 70% 或 GPU 显存利用率 > 85% 持续 5 分钟
  - 动作: 增加 1 个推理节点

缩容规则:
  - 条件: 平均 CPU 利用率 < 30% 且 GPU 显存利用率 < 50% 持续 15 分钟
  - 动作: 减少 1 个推理节点（冷却时间 30 分钟）
```

---

### 3.5 安全体系

#### 3.5.1 网络安全

| 安全措施 | 说明 |
|---------|------|
| VPC 隔离 | 推理服务部署在隔离 VPC 内，不暴露公网 |
| 安全组 | 仅开放必要端口（8080 到 ELB，ELB 到 APIG） |
| 网络ACL | 限制源 IP 范围为客户机房网段 |
| 专线/VPN加密 | 传输链路物理隔离或 IPsec 加密 |
| TLS 1.2+ | API 调用全程 HTTPS 加密 |

#### 3.5.2 数据安全

| 安全措施 | 说明 |
|---------|------|
| 模型权重加密存储 | OBS 服务端加密（SSE-KMS） |
| 推理日志脱敏 | 不记录用户输入的敏感文本内容 |
| 数据不出域 | 推理数据仅在专属资源池内处理 |
| KMS 密钥管理 | API Key、AK/SK 通过 KMS 统一管理 |

#### 3.5.3 访问控制

```
鉴权流程:
  客户端请求 → APIG 验证 API Key → IAM AK/SK 签名校验
    → 通过后转发到 ELB → 推理节点处理 → 返回结果
```

---

### 3.6 监控与运维

#### 3.6.1 监控指标

| 监控维度 | 指标 | 告警阈值 |
|---------|------|---------|
| GPU/NPU | 显存利用率 | > 90% 持续 5 分钟 |
| GPU/NPU | 计算利用率 | > 85% 持续 10 分钟 |
| API | 请求 QPS | 根据业务设定上下限 |
| API | 平均响应延迟 | P99 > 30s |
| API | 错误率 (5xx) | > 1% |
| 节点 | CPU/内存/磁盘 | 磁盘 > 85% |
| 网络 | 专线带宽利用率 | > 80% |
| 网络 | VPN 隧道状态 | 隧道断开 |

#### 3.6.2 日志体系

```
日志采集链路:
  推理节点 → LTS (云日志服务) → 日志分析和检索

日志内容:
  - 每次推理请求的 token 用量、耗时、模型版本
  - 节点启动/停止/异常事件
  - API 调用审计日志（通过 CTS 云审计服务）
```

#### 3.6.3 Prometheus + Grafana 自定义监控（可选）

```yaml
# 推理服务暴露的自定义指标
metrics:
  - inference_request_duration_seconds    # 推理耗时分布
  - inference_tokens_generated_total      # 生成 token 总数
  - inference_tokens_input_total          # 输入 token 总数
  - inference_batch_size_current          # 当前批处理大小
  - gpu_memory_used_bytes                 # GPU 显存使用量
  - gpu_utilization_percent               # GPU 计算利用率
```

---

## 四、API 接口规范

### 4.1 对话补全接口（兼容 OpenAI 格式）

```bash
curl -X POST https://<api-gateway-domain>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-api-key>" \
  -d '{
    "model": "glm-4.7-air-35b",
    "messages": [
      {"role": "system", "content": "你是一个专业的AI助手。"},
      {"role": "user", "content": "请介绍一下华为云ModelArts的优势。"}
    ],
    "max_tokens": 2048,
    "temperature": 0.7,
    "top_p": 0.9,
    "stream": false
  }'
```

### 4.2 响应格式

```json
{
  "id": "chatcmpl-a1b2c3d4",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "glm-4.7-air-35b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "华为云ModelArts是..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 180,
    "total_tokens": 205
  }
}
```

### 4.3 流式输出（SSE）

```bash
curl -X POST https://<api-gateway-domain>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-api-key>" \
  -d '{
    "model": "glm-4.7-air-35b",
    "messages": [{"role": "user", "content": "写一首诗"}],
    "stream": true
  }'
```

---

## 五、部署步骤清单

### Phase 1：基础环境搭建（第 1 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 1.1 | 创建 VPC、子网、安全组 | 华为云运维 | 网络基础设施就绪 |
| 1.2 | 创建 ModelArts 专属资源池 | 华为云运维 | 计算资源就绪 |
| 1.3 | 申请专线物理连接 | 运营商 + 华为云 | 物理链路就绪 |
| 1.4 | 配置 VPN 网关（备份链路） | 华为云运维 | VPN 隧道就绪 |
| 1.5 | 上传模型权重到 OBS | 算法工程师 | 模型文件就绪 |

### Phase 2：推理服务部署（第 2 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 2.1 | 制作/选择推理镜像 | 算法工程师 | 推理镜像就绪 |
| 2.2 | 在 ModelArts 创建在线推理服务 | 算法工程师 | 单节点推理服务可用 |
| 2.3 | 配置 ELB 负载均衡 | 华为云运维 | 多节点流量分发 |
| 2.4 | 配置 APIG API 网关 | 华为云运维 | 统一 API 入口 |
| 2.5 | 部署 WAF 防火墙 | 华为云运维 | 安全防护就绪 |

### Phase 3：网络打通与联调（第 3 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 3.1 | 专线虚拟网关/虚拟接口配置 | 华为云 + 客户 | 云上云下网络互通 |
| 3.2 | 客户机房侧路由配置 | 客户网络运维 | 客户侧路由就绪 |
| 3.3 | 端到端联调测试 | 双方联合 | API 调用链路通畅 |
| 3.4 | 性能压测 | 测试工程师 | 性能基线报告 |

### Phase 4：监控与交付（第 4 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 4.1 | 配置监控告警 | 华为云运维 | 监控大屏就绪 |
| 4.2 | 配置日志采集 | 华为云运维 | 日志检索就绪 |
| 4.3 | 配置弹性伸缩策略 | 华为云运维 | 自动伸缩就绪 |
| 4.4 | 编写运维手册并交付 | 双方联合 | 运维文档交付 |
| 4.5 | 知识转移与培训 | 华为云 | 客户可自主运维 |

---

## 六、成本估算参考

| 资源项 | 规格 | 预估月费（参考） |
|--------|------|-----------------|
| ModelArts 专属资源池 | Ascend 910B × 3 节点 | ¥45,000 ~ ¥60,000 |
| OBS 对象存储 | 500GB 标准存储 | ¥100 ~ ¥200 |
| ELB 弹性负载均衡 | 共享型 + 带宽 | ¥500 ~ ¥1,000 |
| APIG API 网关 | 专属实例 | ¥3,000 ~ ¥5,000 |
| WAF Web 防火墙 | 专业版 | ¥3,000 ~ ¥5,000 |
| VPN 网关 | 100Mbps | ¥1,500 ~ ¥2,000 |
| 云专线 | 1Gbps（运营商另计） | ¥2,000 ~ ¥3,000（华为云侧） |
| 云监控/日志 | 基础版 | ¥500 ~ ¥1,000 |
| **合计（华为云侧）** | | **约 ¥55,000 ~ ¥77,000/月** |

> 注：以上为参考价格，实际以华为云官网最新价格和商务协议为准。专线运营商费用另计。

---

## 七、华为云服务清单

| 服务 | 用途 |
|------|------|
| **ModelArts** | 大模型推理服务部署与管理 |
| **VPC（虚拟私有云）** | 网络隔离与私网通信 |
| **Direct Connect（云专线）** | 高速专线连接客户机房 |
| **VPN 网关** | IPsec VPN 备份链路 |
| **ELB（弹性负载均衡）** | 推理服务流量分发 |
| **APIG（API 网关）** | API 统一管理、鉴权、限流 |
| **WAF（Web 应用防火墙）** | Web 安全防护 |
| **OBS（对象存储）** | 模型权重与数据存储 |
| **SFS Turbo（文件存储）** | 推理节点共享文件系统 |
| **CES（云监控服务）** | 资源与服务监控告警 |
| **LTS（云日志服务）** | 日志采集与分析 |
| **CTS（云审计服务）** | 操作审计 |
| **KMS（密钥管理服务）** | 密钥与证书管理 |
| **IAM（身份与访问管理）** | 权限控制 |

---

## 八、风险与应对

| 风险 | 等级 | 应对措施 |
|------|------|---------|
| 专线中断 | 中 | VPN 备份链路自动切换，专线恢复后回切 |
| GPU/NPU 节点故障 | 中 | ELB 健康检查自动剔除，ModelArts 自动恢复 |
| 模型推理超时 | 低 | 设置合理 max_tokens，异步队列处理长请求 |
| 客户并发突增 | 中 | 弹性伸缩自动扩容，APIG 限流保护后端 |
| Prompt Injection 攻击 | 中 | WAF 规则 + 输入内容审核中间件 |
| 模型权重泄露 | 低 | OBS 加密存储 + KMS + 严格 IAM 策略 |

---

## 参考文档

- [华为云 ModelArts 大模型本地部署](https://www.huaweicloud.com/special/tuijian-18604373)
- [ModelArts 部署模型为在线服务](https://support.huaweicloud.com/usermanual-standard-modelarts/inference-modelarts-0018.html)
- [推理入门：一键完成 GLM-5 模型部署](https://support.huaweicloud.com/qs-modelarts/modelarts_06_0009.html)
- [ModelArts VPC 直连高速访问通道](https://support.huaweicloud.com/bestpractice-modelarts/modelarts_04_0233.html)
- [华为云专线 Direct Connect](https://www.huaweicloud.com/special/tuijian-18604972)
- [通过 ELB 连接内网访问在线服务](https://www.huaweicloud.com/guide/productsdesc-bms_4d106c8183477151b7df6ce915c76b9bsupport0)
- [ModelArts CreateService API](https://support.huaweicloud.com/api-modelarts/CreateService.html)
- [模型调用功能介绍](https://support.huaweicloud.com/model-call-modelarts/model-call-modelarts-0001.html)
- [专属资源池说明](https://support.huaweicloud.com/helppanel-modelarts/ma_help_019.html)
- [企业上云专线最佳实践](https://support.huaweicloud.com/bestpractice-cc/cc_04_0004.html)
