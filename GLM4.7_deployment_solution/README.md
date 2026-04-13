# 华为云 ModelArts GLM-4.7-Flash-30B-A3B 私有化部署方案

> **客户**: N 游戏公司
> **模型**: GLM-4.7-Flash-30B-A3B（智谱 AI 开源 MoE 模型）
> **核心需求**: P99 输入 24K tokens，承载 1300 QPS 推理请求
> **平台**: 华为云 ModelArts 专属资源池 + 昇腾 Ascend 910B

---

## 一、方案概述

本方案基于**华为云 ModelArts 专属资源池**部署 GLM-4.7-Flash-30B-A3B 大模型，通过**云专线（Direct Connect）/ VPN 网关**与 N 游戏公司自有机房打通私有网络，对外提供稳定可靠的 RESTful API 推理服务。

针对 1300 QPS @ 24K P99 的大规模推理需求，采用 **Prefill/Decode 分离架构** + **三级上下文池** + **Prefix Caching 游戏场景优化**的分层解耦设计。

### 核心目标

- 提供私有化、独享的大模型推理 API 服务，承载 **1300 QPS**
- P99 输入 **24K tokens** 的长上下文推理能力
- 针对游戏 NPC 对话、剧情生成、内容审核等场景优化
- 客户机房与华为云之间安全可靠的专线/VPN 互联
- 高可用、可弹性伸缩的推理服务架构

### 模型架构关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 总参数量 | ~30B | MoE 架构全量参数需加载到显存 |
| 激活参数量 | ~3B (A3B) | 每 token 仅激活 Top-4 Expert |
| num_hidden_layers | 47 | 1 Dense + 46 MoE 层 |
| num_key_value_heads | 20 | GQA 分组查询注意力 |
| hidden_size | 2048 | |
| max_position_embeddings | 202,752 | ~128K 上下文窗口 |
| 路由专家数 | 64 + 1 共享专家 | MoE 稀疏激活 |
| MTP 层 | 1 层 | 多 Token 预测（推测解码加速） |

> **关键特性**: MoE 架构使得每 token 仅激活 ~3B 参数，**计算量远低于 Dense 30B**，但全量 30B 权重仍需常驻显存，**KV Cache 是显存瓶颈**。

---

## 二、资源需求测算

### 2.1 单请求显存分析

#### KV Cache 计算

```
KV Cache / token = 2 × num_layers × num_kv_heads × head_dim × bytes
                 = 2 × 47 × 20 × head_dim × precision_bytes
```

**保守估算**（head_dim = 128）:

| 精度 | KV Cache / token | 24K tokens/请求 | 4K tokens/请求 |
|------|-----------------|-----------------|----------------|
| FP16 | ~0.92 MB | **~22 GB** | ~3.7 GB |
| INT8 | ~0.46 MB | **~11 GB** | ~1.8 GB |
| FP8 | ~0.23 MB | **~5.5 GB** | ~0.92 GB |

> 若 head_dim = 64（需从实际 config.json 确认），上述数值减半。

#### 模型权重显存占用

| 精度 | 权重大小 | 单卡 910B (64GB) 是否可承载 |
|------|---------|---------------------------|
| FP16/BF16 | ~60 GB | **无法单卡承载** |
| INT8 | ~30 GB | 可承载，剩余 ~27 GB 给 KV Cache |
| INT4 (GPTQ/AWQ) | ~15 GB | 可承载，剩余 ~42 GB 给 KV Cache |

#### 单卡容量分析（Ascend 910B, 64GB HBM, INT8 权重 + INT8 KV Cache）

| 上下文长度 | KV Cache/请求 | 可用显存 | 单卡并发数 |
|-----------|--------------|---------|-----------|
| 4K tokens | ~1.8 GB | ~27.6 GB | **~15** |
| 8K tokens | ~3.6 GB | ~27.6 GB | **~7** |
| 24K tokens | ~11 GB | ~27.6 GB | **~2** |

> **结论**: 24K P99 请求单卡仅能并发 2 个，**必须使用 Tensor Parallelism 分布式推理**。

### 2.2 1300 QPS 规模推算

#### 游戏场景流量分布假设

| 场景 | 占比 | QPS | 平均输入 | 平均输出 | 预估延迟 |
|------|------|-----|---------|---------|---------|
| NPC 对话 | 60% | 780 | ~2K | ~200 tok | ~1.8s |
| 剧情/内容生成 | 25% | 325 | ~6K | ~500 tok | ~4.0s |
| 长文本分析/审核 | 15% | 195 | ~18K | ~300 tok | ~4.5s |
| **加权平均** | | **1300** | | | **~3.0s** |

#### 推荐方案：三级上下文池 + Prefill/Decode 分离

```
Decode 池 (显存密集型, TP=4, INT8):
┌──────────────────────────────────────────────────────────────┐
│  短上下文池 (<4K):   52 实例 × 4 卡 = 208 卡  → 承载 780 QPS │
│  中上下文池 (4-8K):  41 实例 × 4 卡 = 164 卡  → 承载 325 QPS │
│  长上下文池 (8K+):   49 实例 × 4 卡 = 196 卡  → 承载 195 QPS │
│  Decode 小计: ~143 实例, ~568 卡                              │
└──────────────────────────────────────────────────────────────┘

Prefill 池 (计算密集型, TP=8, BF16):
┌──────────────────────────────────────────────────────────────┐
│  ~25 实例 × 8 卡 = 200 卡  → 快速处理全量输入 prefill         │
└──────────────────────────────────────────────────────────────┘

总计: ~168 实例, ~770~950 张 Ascend 910B
```

> **注**: 以上为保守估算。实际卡数取决于昇腾 910B 上的 benchmark 数据。**Phase 0 验证阶段必须实测校准。**

---

## 三、整体架构

```
┌───────────────────────────────────────────────────────────────────────────┐
│                    N 游戏公司 - 自有机房                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ NPC 服务  │  │ 剧情生成  │  │ 内容审核  │  │ 游戏运营  │                  │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘                  │
│        └──────────────┼────────────┼──────────────┘                       │
│              ┌────────┴────────────┴────────┐                              │
│              │    客户侧 API Gateway / 路由    │                            │
│              └──────────────┬────────────────┘                            │
│                    ┌────────┴────────┐                                    │
│                    │ 客户侧路由器/     │                                    │
│                    │ 防火墙设备        │                                    │
│                    └────────┬────────┘                                    │
└─────────────────────────────┼────────────────────────────────────────────┘
                              │
               ┌──────────────┴──────────────┐
               │  100Gbps 专线(主) + VPN(备)  │
               └──────────────┬──────────────┘
                              │
┌─────────────────────────────┼────────────────────────────────────────────┐
│                   华为云 VPC（私有网络）                                    │
│                              │                                            │
│                    ┌─────────┴─────────┐                                 │
│                    │  虚拟网关 (VGW)     │  ← 专线 VGW / VPN 网关          │
│                    └─────────┬─────────┘                                 │
│                              │                                            │
│                    ┌─────────┴─────────┐                                 │
│                    │  WAF (Web应用防火墙) │  ← 防注入/CC/Prompt Injection   │
│                    └─────────┬─────────┘                                 │
│                              │                                            │
│                    ┌─────────┴─────────┐                                 │
│                    │  APIG (API 网关)    │  ← 鉴权、限流、计量              │
│                    └─────────┬─────────┘                                 │
│                              │                                            │
│              ┌───────────────┴───────────────┐                            │
│              │     智能请求路由层 (Router)      │                           │
│              │  1. 解析 input tokens 数量       │                           │
│              │  2. 计算 prefix hash → 亲和路由  │                           │
│              │  ├→ 短上下文 (<4K)  → 短上下文池 │                           │
│              │  ├→ 中上下文 (4-8K) → 中上下文池 │                           │
│              │  └→ 长上下文 (8K+)  → 长上下文池 │                           │
│              └───────────────┬───────────────┘                            │
│                              │                                            │
│         ┌────────────────────┼────────────────────┐                      │
│         │       Prefill/Decode 分离层               │                     │
│         │                                            │                     │
│         │  ┌──────────────┐   KV Cache   ┌────────┐│                     │
│         │  │ Prefill 池    │ ──Transfer──→│Decode池││                     │
│         │  │ (计算密集)     │   (RDMA)     │(显存密集)│                     │
│         │  │ TP=8, BF16    │              │TP=4,INT8│                     │
│         │  │ ~25 实例      │              │~143实例 │                     │
│         │  └──────────────┘              └────────┘│                     │
│         └────────────────────────────────────────────┘                      │
│                                                                           │
│         ┌────────────────────────────────────────────┐                     │
│         │       Prefix Cache Pool (Redis/LMCache)     │                    │
│         │  ├─ NPC 人设模板 → 命中率 >90%                │                    │
│         │  ├─ 游戏世界观设定                             │                    │
│         │  └─ 常用 Prompt 模板                          │                    │
│         └────────────────────────────────────────────┘                     │
│                                                                           │
│         ┌────────────────────────────────────────────┐                     │
│         │    ModelArts 专属资源池 (~168 实例)          │                    │
│         │  ┌──────────┐ ┌──────────┐ ┌──────────┐   │                    │
│         │  │ 短上下文  │ │ 中上下文  │ │ 长上下文  │   │                    │
│         │  │ 推理池    │ │ 推理池    │ │ 推理池    │   │                    │
│         │  │ ~52 实例  │ │ ~41 实例  │ │ ~49 实例  │   │                    │
│         │  └──────────┘ └──────────┘ └──────────┘   │                    │
│         │  总计: ~770~950 张 Ascend 910B              │                    │
│         └────────────────────────────────────────────┘                     │
│                                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                               │
│  │  OBS      │  │  CES     │  │  LTS/CTS │                               │
│  │ 模型权重  │  │ 监控告警  │  │ 日志审计  │                               │
│  └──────────┘  └──────────┘  └──────────┘                               │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 四、详细部署方案

### 4.1 基础资源层 — 计算资源规划

#### 4.1.1 ModelArts 专属资源池

| 配置项 | 推荐规格 | 说明 |
|--------|---------|------|
| 资源池类型 | 专属资源池（Dedicated Pool） | 独享计算资源，不与其他用户共享 |
| 计算规格 | **Ascend 910B**（snt9b） | 64GB HBM, 峰值带宽 1.6TB/s |
| 节点数量 | **~110 节点**（8 卡/节点） | 需提前向华为云申请配额 |
| 存储 | SFS Turbo 高性能文件系统 | 模型权重跨节点共享加载 |
| 网络 | 关联到指定 VPC + RDMA 高速网络 | Prefill/Decode 间 KV Cache 传输 |

#### 4.1.2 模型权重管理

```bash
# 模型权重存储路径规划
OBS Bucket 结构:
  obs://n-game-glm-models/
  ├── models/
  │   └── glm-4.7-flash-30b-a3b/
  │       ├── config.json
  │       ├── model-00001-of-000XX.safetensors
  │       ├── tokenizer.model
  │       └── ...
  ├── custom-images/              # 自定义推理镜像
  ├── prefix-templates/           # NPC 人设等共享前缀模板
  │   ├── npc_blacksmith.json     # 铁匠 NPC 人设
  │   ├── npc_merchant.json       # 商人 NPC 人设
  │   └── game_world_setting.json # 世界观设定
  └── logs/                       # 推理日志归档
```

#### 4.1.3 推理服务启动配置（vLLM-Ascend + MoE 优化）

```bash
# Decode 池启动命令（TP=4, INT8 权重 + INT8 KV Cache）
python -m vllm.entrypoints.openai.api_server \
  --model /opt/models/glm-4.7-flash-30b-a3b \
  --served-model-name glm-4.7-flash \
  --tensor-parallel-size 4 \
  --max-model-len 32768 \
  --kv-cache-dtype int8 \
  --quantization int8 \
  --enable-prefix-caching \
  --trust-remote-code \
  --port 8080 \
  --gpu-memory-utilization 0.92

# Prefill 池启动命令（TP=8, BF16, 专注 prefill）
python -m vllm.entrypoints.openai.api_server \
  --model /opt/models/glm-4.7-flash-30b-a3b \
  --served-model-name glm-4.7-flash \
  --tensor-parallel-size 8 \
  --max-model-len 32768 \
  --enable-prefix-caching \
  --trust-remote-code \
  --port 8080 \
  --gpu-memory-utilization 0.90
```

**关键参数说明**:
- `--tensor-parallel-size 4/8`: 张量并行，分摊权重和 KV Cache
- `--kv-cache-dtype int8`: KV Cache INT8 量化，显存占用减半
- `--enable-prefix-caching`: 前缀缓存，NPC 场景大幅降低 TTFT
- `--max-model-len 32768`: 覆盖 P99 的 24K 输入 + 输出余量
- `--quantization int8`: 模型权重 INT8 量化

#### 4.1.4 自定义推理镜像

```dockerfile
# Dockerfile for GLM-4.7-Flash MoE on Ascend NPU
FROM swr.cn-north-4.myhuaweicloud.com/modelarts/infer-pytorch:npu-ubuntu20.04-py3.10

# 安装 vLLM-Ascend 及 MoE 优化依赖
RUN pip install vllm-ascend==0.10.0 \
    && pip install transformers==4.45.0 \
    && pip install lmcache \
    && pip install accelerate

# 拷贝启动脚本
COPY start_decode.sh /opt/start_decode.sh
COPY start_prefill.sh /opt/start_prefill.sh

# 设置模型路径
ENV MODEL_PATH=/opt/models/glm-4.7-flash-30b-a3b
ENV MODEL_NAME=glm-4.7-flash

EXPOSE 8080

# 根据 POOL_TYPE 环境变量选择启动模式
CMD ["sh", "-c", "if [ \"$POOL_TYPE\" = 'prefill' ]; then /opt/start_prefill.sh; else /opt/start_decode.sh; fi"]
```

---

### 4.2 智能请求路由层

#### 4.2.1 路由策略

```python
# router.py - 智能路由服务
from fastapi import FastAPI, Request
from typing import Optional
import hashlib
import httpx

app = FastAPI()

# 三级上下文池配置
POOLS = {
    "short": {
        "endpoints": [f"http://pool-short-{i}:8080" for i in range(1, 53)],
        "token_range": (0, 4096),
    },
    "medium": {
        "endpoints": [f"http://pool-medium-{i}:8080" for i in range(1, 42)],
        "token_range": (4096, 8192),
    },
    "long": {
        "endpoints": [f"http://pool-long-{i}:8080" for i in range(1, 50)],
        "token_range": (8192, 32768),
    },
}

# 前缀亲和映射: prefix_hash → endpoint（提升缓存命中率）
prefix_affinity: dict[str, str] = {}


def estimate_tokens(messages: list[dict]) -> int:
    """估算输入 token 数"""
    total_chars = sum(len(m["content"]) for m in messages)
    return int(total_chars * 1.3)  # 中文约 1.3 chars/token


def get_prefix_hash(messages: list[dict]) -> Optional[str]:
    """提取系统提示前缀 hash（用于亲和性路由）"""
    if messages and messages[0]["role"] == "system":
        return hashlib.md5(messages[0]["content"][:500].encode()).hexdigest()
    return None


def select_endpoint(pool_key: str, prefix_hash: Optional[str]) -> str:
    """选择后端节点: 优先路由到前缀缓存命中的节点"""
    pool = POOLS[pool_key]
    if prefix_hash and prefix_hash in prefix_affinity:
        return prefix_affinity[prefix_hash]
    idx = hash(prefix_hash or "") % len(pool["endpoints"])
    endpoint = pool["endpoints"][idx]
    if prefix_hash:
        prefix_affinity[prefix_hash] = endpoint
    return endpoint


@app.post("/v1/chat/completions")
async def route_request(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    token_count = estimate_tokens(messages)

    # 按上下文长度选池
    pool_key = "long"
    for key, pool in POOLS.items():
        if pool["token_range"][0] <= token_count < pool["token_range"][1]:
            pool_key = key
            break

    # 前缀亲和路由
    prefix_hash = get_prefix_hash(messages)
    endpoint = select_endpoint(pool_key, prefix_hash)

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{endpoint}/v1/chat/completions", json=body)
        return resp.json()
```

#### 4.2.2 游戏场景 Prefix Caching 策略

```
NPC 对话典型 Prompt 结构:
┌────────────────────────────────────────┐
│ [共享前缀 - 可缓存] ~2000 tokens        │
│ System: 你是游戏《XX》中的NPC铁匠老张... │
│ 世界观: 大陆设定、NPC关系、物品系统...   │
│ 规则: 对话风格、安全约束...              │
├────────────────────────────────────────┤
│ [动态部分 - 不缓存] ~500-2000 tokens    │
│ 对话历史: 玩家之前的对话记录...           │
│ 当前输入: 玩家最新发言                   │
└────────────────────────────────────────┘
```

| 前缀模板 | 预估 tokens | 命中率 | 效果 |
|---------|------------|-------|------|
| NPC 人设 + 世界观 | ~2,000 | >90% | Prefill 节省 ~50%+, TTFT 降低 ~30% |
| 对话风格模板 | ~500 | >85% | TTFT 降低 ~20% |
| 内容审核规则 | ~1,000 | >95% | Prefill 节省 ~40% |

**实现**: vLLM [Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/) 实例内缓存 + [LMCache](https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/) 跨节点共享

---

### 4.3 网络层 — 云端与机房互联

#### 4.3.1 方案对比

| 维度 | 云专线 (Direct Connect) | VPN 网关 |
|------|------------------------|----------|
| 带宽 | 1Gbps ~ 100Gbps | 100Mbps~1Gbps |
| 延迟 | 低（<5ms 同城） | 较高（受公网影响） |
| 安全性 | 物理隔离，最高等级 | IPsec 加密隧道 |
| 成本 | 较高 | 较低 |
| 适用场景 | 生产环境 | 备份链路 |

#### 4.3.2 推荐：100Gbps 专线主 + VPN 备

```
主链路：客户机房 ←── 100Gbps 运营商专线 ──→ 华为云 Direct Connect 接入点
备链路：客户机房 ←── IPsec VPN ──→ 华为云 VPN 网关
```

> **带宽测算**: 1300 QPS × 24K tokens × 4 bytes = 峰值 ~125 GB/s 输入带宽（内网处理），客户机房到云端建议 **100Gbps 专线**。

#### 4.3.3 VPC 网络规划

```bash
# VPC 规划（需容纳 100+ 节点）
VPC: 10.10.0.0/16

子网规划:
  10.10.1.0/24   → 管理网络 (APIG, WAF, Router)
  10.10.10.0/20  → 短上下文推理池 (~52 实例)
  10.10.20.0/20  → 中上下文推理池 (~41 实例)
  10.10.30.0/20  → 长上下文推理池 (~49 实例)
  10.10.40.0/24  → Prefill 池 (~25 实例)
  10.10.50.0/24  → Redis (Prefix Cache)
  10.10.254.0/24 → 专线/VPN 对接网段
```

#### 4.3.4 Prefill ↔ Decode 高速互联

Prefill 和 Decode 实例间的 KV Cache 传输需要低延迟高带宽网络：
- 推荐使用 **RDMA / RoCE** 网络
- 同可用区部署，减少跨 AZ 延迟
- KV Cache 压缩传输（INT8 量化后传输）

---

### 4.4 服务层 — API 网关与负载均衡

#### 4.4.1 API 网关（APIG）配置

| 配置项 | 推荐值 |
|--------|--------|
| 网关类型 | **专属 APIG**（部署在客户 VPC 内） |
| 鉴权方式 | API Key + IAM AK/SK 双重认证 |
| 限流策略 | 按场景限流：NPC 对话 800 req/s，内容生成 350 req/s，审核 200 req/s |
| 请求大小 | 最大 32MB（支持 24K 长文本输入） |
| 超时时间 | 300s（大模型生成长文本需较长超时） |

```bash
# API 路由设计
POST   /v1/chat/completions     # 对话补全（主要接口）
POST   /v1/completions          # 文本补全
GET    /v1/models               # 模型列表
GET    /health                  # 健康检查
```

#### 4.4.2 分池弹性负载均衡（ELB）

```
                    ┌──────────────────┐
                    │  Router (智能路由) │
                    └────────┬─────────┘
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────┴───────┐ ┌───┴────────┐ ┌───┴────────┐
     │  ELB (短上下文) │ │ELB(中上下文)│ │ELB(长上下文)│
     └────────┬───────┘ └───┬────────┘ └───┬────────┘
         ┌────┼────┐    ┌───┼───┐      ┌───┼───┐
         │    │    │    │   │   │      │   │   │
        ▼    ▼    ▼   ▼   ▼   ▼     ▼   ▼   ▼
     [52个推理节点] [41个推理节点]  [49个推理节点]

ELB 配置:
- 算法: 加权轮询（Weighted Round Robin）
- 健康检查: GET /health, 间隔 10s, 超时 5s
- 前端协议: HTTPS（TLS 1.2+）
- 后端协议: HTTP
```

#### 4.4.3 Web 应用防火墙（WAF）

在 APIG 前部署 WAF，防护：
- SQL 注入、XSS 攻击
- CC 攻击（恶意高频调用）
- Prompt Injection 攻击防护
- 恶意内容过滤

---

### 4.5 高可用设计

#### 4.5.1 多层级冗余

| 层级 | 冗余策略 |
|------|---------|
| 网络层 | 100Gbps 专线主 + VPN 备双链路，虚拟网关主备冗余 |
| 接入层 | APIG 多节点 + WAF 多实例 |
| 路由层 | Router 多实例部署，无状态可水平扩展 |
| 负载层 | 每池独立 ELB，双可用区部署 |
| 推理层 | 分池部署，每池最少 30+ 节点，跨 AZ 分布 |
| 缓存层 | Redis Prefix Cache 集群模式（主从 + 哨兵） |
| 存储层 | OBS 多 AZ 冗余，SFS Turbo 双活 |

#### 4.5.2 故障自愈

```
推理节点故障:
  ELB 健康检查 → 剔除故障节点 → Router 重新分配流量
  → ModelArts 自动重启/迁移实例 → ELB 重新加入

专线中断:
  BGP/路由检测 → 自动切换到 VPN 备用链路
  → 专线恢复后自动回切

Prefill 池过载:
  Prefill 队列积压 → 新请求 fallback 到 Decode 池自行 prefill
  → Prefill 池扩容

游戏高峰突发:
  QPS 突增至 2000+ → 弹性伸缩自动扩容
  → APIG 限流保护 + 降级策略（截断长上下文到 8K）
```

#### 4.5.3 分池弹性伸缩

```yaml
scaling_policies:
  short_pool:
    min_instances: 40
    max_instances: 80
    scale_up: "QPS > 12/instance 持续 3 分钟 → +5 实例"
    scale_down: "QPS < 5/instance 持续 20 分钟 → -3 实例"
    schedule:
      - "0 18 * * 1-5 → 扩容到 70 实例"   # 工作日晚高峰
      - "0 10 * * 6-7 → 扩容到 75 实例"   # 周末高峰

  medium_pool:
    min_instances: 30
    max_instances: 60
    scale_up: "QPS > 6/instance 持续 5 分钟 → +3 实例"

  long_pool:
    min_instances: 35
    max_instances: 70
    scale_up: "QPS > 3/instance 持续 5 分钟 → +3 实例"

  prefill_pool:
    min_instances: 10
    max_instances: 40
    scale_up: "平均 prefill 延迟 > 2s 持续 5 分钟 → +5 实例"
```

---

### 4.6 安全体系

#### 4.6.1 网络安全

| 安全措施 | 说明 |
|---------|------|
| VPC 隔离 | 推理服务部署在隔离 VPC 内，不暴露公网 |
| 安全组 | 仅开放必要端口，分池隔离 |
| 网络ACL | 限制源 IP 范围为客户机房网段 |
| 专线/VPN加密 | 100Gbps 专线物理隔离 + VPN IPsec 加密 |
| TLS 1.2+ | API 调用全程 HTTPS 加密 |

#### 4.6.2 数据安全

| 安全措施 | 说明 |
|---------|------|
| 模型权重加密存储 | OBS SSE-KMS 服务端加密 |
| 推理日志脱敏 | 不记录玩家输入的敏感内容 |
| 数据不出域 | 推理数据仅在专属资源池内处理 |
| KMS 密钥管理 | API Key、AK/SK 通过 KMS 统一管理 |

#### 4.6.3 访问控制

```
鉴权流程:
  客户端请求 → APIG 验证 API Key → IAM AK/SK 签名校验
    → Router 路由 → ELB → 推理节点 → 返回结果
```

---

### 4.7 监控与运维

#### 4.7.1 监控指标（游戏场景增强）

| 监控维度 | 指标 | 告警阈值 | 游戏场景意义 |
|---------|------|---------|------------|
| NPC 对话 | TTFT (首 Token 延迟) | P99 > 2s | 影响 NPC 响应体验 |
| NPC 对话 | 端到端延迟 | P99 > 5s | 玩家等待时间 |
| 内容生成 | 生成吞吐量 (tokens/s) | < 50 tok/s | 内容生成效率 |
| 全局 | Prefix Cache 命中率 | < 70% | 缓存策略有效性 |
| 全局 | KV Cache 利用率 | > 85% 持续 10min | 需要扩容 |
| 全局 | 请求队列深度 | > 200 持续 5min | 过载前预警 |
| 全局 | MoE Expert 负载均衡度 | 偏差 > 30% | 路由策略需调优 |
| NPU | 显存利用率 | > 92% 持续 5min | 节点过载 |
| API | 错误率 (5xx) | > 1% | 服务异常 |
| 网络 | 专线带宽利用率 | > 80% | 带宽瓶颈 |
| 成本 | tokens/元 | 日环比下降 > 20% | 效率异常 |

#### 4.7.2 日志体系

```
日志采集链路:
  推理节点 → LTS (云日志服务) → 日志分析和检索

日志内容:
  - 每次推理请求的 token 用量、耗时、场景类型（NPC/生成/审核）
  - Prefix Cache 命中/未命中记录
  - MoE Expert 激活分布
  - 节点启动/停止/异常事件
  - API 调用审计日志（CTS 云审计服务）
```

#### 4.7.3 自定义监控指标

```yaml
metrics:
  - inference_request_duration_seconds      # 推理耗时分布
  - inference_ttft_seconds                  # 首 Token 延迟
  - inference_tokens_generated_total        # 生成 token 总数
  - inference_tokens_input_total            # 输入 token 总数
  - prefix_cache_hit_rate                   # 前缀缓存命中率
  - kv_cache_utilization_percent            # KV Cache 利用率
  - moe_expert_activation_distribution      # Expert 激活分布
  - npu_memory_used_bytes                   # NPU 显存使用量
  - npu_utilization_percent                 # NPU 计算利用率
  - request_queue_depth                     # 请求队列深度
```

---

## 五、API 接口规范

### 5.1 对话补全接口（兼容 OpenAI 格式）

```bash
curl -X POST https://<api-gateway-domain>/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-api-key>" \
  -d '{
    "model": "glm-4.7-flash",
    "messages": [
      {"role": "system", "content": "你是游戏《仙侠传》中的NPC铁匠老张，性格豪爽，擅长锻造各种神兵利器..."},
      {"role": "user", "content": "老张，帮我看看这把剑怎么样？"}
    ],
    "max_tokens": 512,
    "temperature": 0.7,
    "top_p": 0.9,
    "stream": true
  }'
```

### 5.2 响应格式

```json
{
  "id": "chatcmpl-a1b2c3d4",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "glm-4.7-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "哈哈！小伙子，这把剑嘛..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "completion_tokens": 120,
    "total_tokens": 165
  }
}
```

---

## 六、部署步骤清单

### Phase 0：Benchmark 验证（第 1~2 周）⭐ 关键阶段

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 0.1 | 获取模型 config.json，确认 head_dim 等精确参数 | 算法工程师 | 模型参数表 |
| 0.2 | 单卡昇腾 910B benchmark（不同上下文长度） | 算法工程师 | 单卡性能基线 |
| 0.3 | 多卡 TP benchmark（TP=2/4/8） | 算法工程师 | 扩展效率报告 |
| 0.4 | vLLM Prefix Caching POC（MoE 模型） | 算法工程师 | 缓存效果报告 |
| 0.5 | Prefill/Decode 分离 POC | 算法工程师 | 可行性验证报告 |
| **0.6** | **基于实测数据修正资源规划** | 架构师 | **最终资源清单** |

> **这是整个项目最关键的阶段。** 1300 QPS @ 24K P99 需要巨大的资源投入，必须基于实测数据做准确规划，避免采购偏差。

### Phase 1：基础设施搭建（第 3~4 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 1.1 | 创建 VPC、子网、安全组（含分池网段规划） | 华为云运维 | 网络基础设施就绪 |
| 1.2 | 创建 ModelArts 专属资源池（~110 节点） | 华为云运维 | 计算资源就绪 |
| 1.3 | 申请 100Gbps 专线物理连接 | 运营商 + 华为云 | 物理链路就绪 |
| 1.4 | 配置 VPN 网关（备份链路） | 华为云运维 | VPN 隧道就绪 |
| 1.5 | 上传模型权重到 OBS | 算法工程师 | 模型文件就绪 |
| 1.6 | 部署 Redis Prefix Cache 集群 | 华为云运维 | 缓存基础设施就绪 |

### Phase 2：推理集群部署（第 5~7 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 2.1 | 制作 MoE 推理镜像（含 vLLM-Ascend） | 算法工程师 | 推理镜像就绪 |
| 2.2 | 部署短/中/长三个上下文 Decode 池 | 华为云运维 | 三级推理池就绪 |
| 2.3 | 部署 Prefill 池 | 华为云运维 | Prefill 池就绪 |
| 2.4 | 部署智能路由层 (Router) | 开发工程师 | 请求路由就绪 |
| 2.5 | 配置分池 ELB 负载均衡 | 华为云运维 | 多池流量分发 |
| 2.6 | 配置 APIG API 网关 | 华为云运维 | 统一 API 入口 |
| 2.7 | 部署 WAF 防火墙 | 华为云运维 | 安全防护就绪 |
| 2.8 | 配置 Prefix Caching 模板 | 算法工程师 | NPC 人设缓存就绪 |

### Phase 3：联调与压测（第 8~9 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 3.1 | 专线虚拟网关/虚拟接口配置 | 华为云 + 客户 | 云上云下网络互通 |
| 3.2 | 客户机房侧路由配置 | 客户网络运维 | 客户侧路由就绪 |
| 3.3 | 端到端联调测试 | 双方联合 | API 调用链路通畅 |
| 3.4 | **1300 QPS 全链路压测** | 测试工程师 | 性能基线报告 |
| 3.5 | NPC 对话专项测试（TTFT、缓存命中率） | 测试工程师 | NPC 场景报告 |
| 3.6 | 弹性伸缩测试（分池扩缩容） | 测试工程师 | 伸缩策略验证 |
| 3.7 | 故障演练（PD 分离、专线切换） | 测试工程师 | 故障恢复验证 |

### Phase 4：监控与交付（第 10 周）

| 序号 | 任务 | 负责方 | 产出 |
|------|------|--------|------|
| 4.1 | 配置游戏场景专项监控面板 | 华为云运维 | 监控大屏就绪 |
| 4.2 | 配置日志采集与检索 | 华为云运维 | 日志体系就绪 |
| 4.3 | 配置分池弹性伸缩策略 | 华为云运维 | 自动伸缩就绪 |
| 4.4 | 编写运维手册并交付 | 双方联合 | 运维文档交付 |
| 4.5 | 知识转移与培训 | 华为云 | 客户可自主运维 |

---

## 七、成本估算

### 7.1 华为云资源月费

| 资源项 | 规格 | 数量 | 预估月费 |
|--------|------|------|---------|
| **ModelArts 专属资源池** | Ascend 910B × 8卡/节点 | **~110 节点** | **¥165~220 万** |
| OBS 对象存储 | 2TB 标准存储 | 1 | ¥500 |
| ELB 弹性负载均衡 | 性能增强型 × 3 池 | 3 | ¥5,000 |
| APIG API 网关 | 专属实例 | 1 | ¥5,000 |
| WAF Web 防火墙 | 专业版 | 1 | ¥5,000 |
| VPN 网关 | 1Gbps（备份链路） | 1 | ¥2,000 |
| 云专线 (DC) | **100Gbps** | 1 | ¥15,000+ |
| Redis (Prefix Cache) | 128GB 集群 | 1 | ¥15,000 |
| 云监控/日志/审计 | 企业版 | 1 | ¥5,000 |
| **合计** | | | **¥175~240 万/月** |

> 注：实际以华为云官网最新价格和商务协议为准。专线运营商费用另计。

### 7.2 成本优化手段

| 优化手段 | 预估节省 | 说明 |
|---------|---------|------|
| 分时弹性伸缩 | 15~25% | 低谷期自动缩容（凌晨、非高峰时段） |
| INT8/INT4 量化 | 20~30% | 减少显存占用 → 减少卡数 |
| Prefix Caching | 10~15% | 减少 Prefill 计算量 |
| MTP 推测解码 | 5~10% | 加速 decode → 提高吞吐 |
| Prefill 池竞价实例 | 10~15% | Prefill 池可用竞价实例降低成本 |

---

## 八、华为云服务清单

| 服务 | 用途 |
|------|------|
| **ModelArts** | 大模型推理服务部署与管理（专属资源池） |
| **VPC（虚拟私有云）** | 网络隔离与分池子网规划 |
| **Direct Connect（云专线）** | 100Gbps 高速专线连接客户机房 |
| **VPN 网关** | IPsec VPN 备份链路 |
| **ELB（弹性负载均衡）** | 分池流量分发 |
| **APIG（API 网关）** | API 统一管理、鉴权、限流、计量 |
| **WAF（Web 应用防火墙）** | Web 安全防护 + Prompt Injection 防护 |
| **OBS（对象存储）** | 模型权重与数据存储 |
| **SFS Turbo（文件存储）** | 推理节点共享模型权重文件系统 |
| **DCC（专属分布式存储）** | 高性能本地存储 |
| **CES（云监控服务）** | 资源与服务监控告警 |
| **LTS（云日志服务）** | 日志采集与分析 |
| **CTS（云审计服务）** | 操作审计 |
| **KMS（密钥管理服务）** | 密钥与证书管理 |
| **IAM（身份与访问管理）** | 权限控制 |
| **DCS（分布式缓存服务）** | Redis Prefix Cache 共享缓存 |

---

## 九、风险与应对

| 风险 | 等级 | 应对措施 |
|------|------|---------|
| 昇腾 910B 上 MoE 推理性能不达预期 | **高** | Phase 0 Benchmark 先行，预留 GPU (H800) 备选方案 |
| 1300 QPS 下 PD 分离的 KV 传输瓶颈 | **高** | RDMA 网络 + KV Cache 压缩传输 |
| 游戏高峰 QPS 突增至 2000+ | 中 | 峰值弹性 + APIG 限流 + 降级策略（截断上下文） |
| Prefix Cache 跨节点同步延迟 | 中 | 亲和性路由 + 异步缓存预热 |
| MoE Expert 负载不均衡 | 中 | 监控 Expert 激活分布 + 调整路由策略 |
| 100Gbps 专线成本过高 | 中 | 评估 10Gbps 起步 + 按需升级的渐进方案 |
| 模型精度因量化降低 | 低 | INT8 量化通常精度损失 <1%，A/B 测试验证 |
| 模型权重泄露 | 低 | OBS 加密存储 + KMS + 严格 IAM 策略 |

---

## 十、总结

### 关键设计决策

| # | 决策 | 原因 |
|---|------|------|
| 1 | Prefill/Decode 分离 | 24K P99 下 KV Cache 巨大，分离后各自优化 |
| 2 | 三级上下文池 | 不同长度请求的显存需求差异 5~10 倍，分池提高资源利用率 |
| 3 | Prefix Caching + 亲和路由 | 游戏场景共享前缀比例高（NPC 人设、世界观），缓存命中率 >90% |
| 4 | INT8 权重 + INT8 KV Cache | 30B MoE 全量权重必须加载，INT8 将显存占用从 60GB 降至 30GB |
| 5 | Phase 0 Benchmark 先行 | ~880 张卡的采购需实测数据支撑，避免方向性错误 |

### 最关键建议

> **Phase 0（Benchmark 验证）是整个项目的命脉。** 1300 QPS @ 24K P99 在昇腾 910B 上的实际表现必须先验证。建议：
> 1. 先用 **2~4 张 Ascend 910B** 做完整单卡/多卡 benchmark
> 2. 基于实测数据做精确资源规划
> 3. 如昇腾性能不达标，备选方案为 **NVIDIA H800/H100 集群**

---

## 参考文档

- [GLM-4.7-Flash 模型 (HuggingFace)](https://huggingface.co/zai-org/GLM-4.7-Flash)
- [GLM-4.7-Flash 8×H100 Benchmark](https://www.mintlify.com/THUDM/slime/examples/glm4-7-30b-a3b)
- [vLLM-Ascend GLM-4.x 部署教程](https://docs.vllm.com.cn/projects/ascend/en/latest/tutorials/GLM4.x.html)
- [vLLM Automatic Prefix Caching](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)
- [vLLM Disaggregated Prefill](https://docs.vllm.ai/en/stable/features/disagg_prefill/)
- [DistServe: Prefill/Decode 分离 (OSDI'24)](https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf)
- [LMCache MoE 推理 10x 性能提升](https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/)
- [llm-d Prefix-Aware Routing](https://llm-d.ai/blog/kvcache-wins-you-can-see)
- [华为云 ModelArts 大模型部署](https://www.huaweicloud.com/special/tuijian-18604373)
- [ModelArts VPC 直连高速访问通道](https://support.huaweicloud.com/bestpractice-modelarts/modelarts_04_0233.html)
- [华为云专线 Direct Connect](https://www.huaweicloud.com/special/tuijian-18604972)
- [ModelArts 专属资源池说明](https://support.huaweicloud.com/helppanel-modelarts/ma_help_019.html)
- [华为云昇腾 AI 推理服务实战](https://bbs.huaweicloud.com/blogs/fbfefd033aac4e1da56d72525ff5b02f)
- [昇腾 910B 大模型部署调优](https://developer.aliyun.com/article/1650438)
- [企业上云专线最佳实践](https://support.huaweicloud.com/bestpractice-cc/cc_04_0004.html)
