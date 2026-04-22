# SGLang PD 分离架构：KV Cache 传输后端对比与选型指南

> 分析日期：2026-04-21
> 分析对象：SGLang 源码（sgl-project/sglang）
> 分析目标：`--disaggregation-transfer-backend` 5 种后端的原理、能力、硬件要求与适用场景

---

## 一、参数定义

**CLI 参数**：`--disaggregation-transfer-backend`
**默认值**：`mooncake`
**可选值**：`mooncake` / `nixl` / `fake` / `ascend` / `mori`
**代码位置**：`server_args.py:698`、`server_args.py:6109-6113`

```bash
--disaggregation-transfer-backend mooncake
```

该参数决定 P 端和 D 端之间 KV cache 的传输方式。P 和 D **必须使用相同的后端**。

---

## 二、后端架构总览

```
                    KV Cache 传输架构
                          │
          ┌───────────────┼───────────────┐
          │               │               │
     RDMA 传输         模拟传输       D2D 传输
          │               │               │
    ┌─────┼─────┐         │               │
    │     │     │         │               │
 mooncake nixl mori     fake           ascend
    │     │     │      (测试用)       (华为NPU)
    │     │     │
    │     │     └── Mori IOEngine
    │     │         可配 QP/Worker
    │     │
    │     └── NIXL Agent
    │         插件化(UCX/Libfabric)
    │
    └── Mooncake Transfer Engine
        Staging Buffer + 多线程 + Session
```

---

## 三、各后端详解

### 3.1 mooncake（默认，生产推荐）

**代码位置**：`disaggregation/mooncake/conn.py`（~1900 行）
**传输方式**：RDMA（基于 Mooncake Transfer Engine）

#### 核心类

| 类名 | 职责 |
|------|------|
| `MooncakeKVManager` | 管理传输引擎、连接池、session |
| `MooncakeKVSender` | P 端发送 KV cache |
| `MooncakeKVReceiver` | D 端接收 KV cache |
| `MooncakeKVBootstrapServer` | Bootstrap 服务发现 |

#### 传输模式

| 模式 | 方法 | 说明 |
|------|------|------|
| 标准传输 | `send_kvcache()` | P/D 相同 TP size，直接 RDMA 写入 D 端 GPU 内存 |
| 切片传输 | `send_kvcache_slice()` | P/D 不同 TP size，按 head 维度切片 |
| Staging 传输 | `send_kvcache_staged()` | 异构 TP 场景：gather → bulk RDMA → scatter |
| HiSparse 传输 | `send_kvcache_hisparse()` | Token 粒度直传 host，用于 page_size 不匹配 |
| 辅助数据 | `send_aux()` | 传输 output tokens、logprobs 等，RDMA 或 TCP fallback |

#### 关键特性

**Staging Buffer**（异构 TP 支持）：

当 P 的 TP size 与 D 不同时（如 P=TP16, D=TP8），KV cache 的 head 分布不同，无法直接传输。Mooncake 通过 staging buffer 解决：

```
P 端 (TP=16)                         D 端 (TP=8)
  rank0~7 → gather → staging buffer ─RDMA→ staging buffer → scatter → rank0~3
  rank8~15 → gather → staging buffer ─RDMA→ staging buffer → scatter → rank4~7
```

环境变量控制：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_DISAGG_STAGING_BUFFER` | `0` | 启用 staging buffer |
| `SGLANG_DISAGG_STAGING_BUFFER_SIZE_MB` | `64` | 每个 queue 的 staging 大小 |
| `SGLANG_DISAGG_STAGING_POOL_SIZE_MB` | `4096` | staging 总池大小 |

**多线程并行传输**：

```
P 端传输线程架构：
  ┌── Transfer Worker Thread 1 ──→ D rank 0
  ├── Transfer Worker Thread 2 ──→ D rank 1
  ├── Transfer Worker Thread 3 ──→ D rank 2
  └── Transfer Worker Thread N ──→ D rank N-1
```

- 线程池大小：`min(max(4, cpu_count // 16), 12)`
- 传输队列数：`SGLANG_DISAGGREGATION_QUEUE_SIZE`（默认 4）

**Session 管理**：

每对 P rank 和 D rank 建立独立的 Mooncake session，用于 RDMA 连接复用。Session 故障会被追踪（`session_failures`、`failed_sessions`），失败后标记请求为 Failed。

#### 硬件要求

| 组件 | 要求 |
|------|------|
| GPU | NVIDIA GPU |
| 网络 | InfiniBand 或 RoCE 网卡 |
| IB 设备 | 通过 `--disaggregation-ib-device` 指定 |
| 软件依赖 | Mooncake Transfer Engine |

#### 适用场景

- **跨机部署**：P 和 D 在不同服务器，通过 IB/RoCE 网络传输
- **异构 TP**：P 和 D 的 TP size 不同，需要 staging buffer
- **生产环境**：功能最全、经过最多验证

#### 已知限制

- NVLink 传输模式有 bug，aux 数据需 TCP fallback
- 配置项较多，需要理解 staging buffer、线程池等参数

---

### 3.2 nixl（NVIDIA 生态集成）

**代码位置**：`disaggregation/nixl/conn.py`（~1250 行）
**传输方式**：RDMA（基于 NIXL Agent + 插件后端）

#### 核心类

| 类名 | 职责 |
|------|------|
| `NixlKVManager` | 管理 NIXL Agent 和内存注册 |
| `NixlKVSender` | P 端发送 |
| `NixlKVReceiver` | D 端接收 |
| `NixlKVBootstrapServer` | Bootstrap 服务发现 |

#### 插件化架构

NIXL 通过插件选择底层传输实现：

```
NIXL Agent
  ├── UCX Backend     → 通用 RDMA 传输
  ├── Libfabric Backend → 高性能 RDMA 传输
  └── 其他插件        → 可扩展
```

选择方式：
```bash
--disaggregation-transfer-backend nixl
# 底层后端通过环境变量选择
SGLANG_DISAGGREGATION_NIXL_BACKEND=UCX      # 默认
SGLANG_DISAGGREGATION_NIXL_BACKEND=LIBFABRIC
```

#### 传输特点

- **统一接口**：`_send_kvcache_generic()` 同时支持 MHA 和 MLA 架构
- **通知机制**：基于 `agent.get_new_notifs()` 的异步完成通知
- **TP Slice**：支持 head 维度切片，处理 GQA 复制场景

#### 硬件要求

| 组件 | 要求 |
|------|------|
| GPU | **仅 NVIDIA GPU** |
| 网络 | RDMA 网络 |
| 软件依赖 | NIXL 库 + 对应 backend 插件 |

#### 适用场景

- 纯 NVIDIA GPU 集群
- 需要灵活切换底层传输（UCX / Libfabric）
- 与 NVIDIA 生态工具链集成

#### 与 Mooncake 的关键区别

| 维度 | Mooncake | NIXL |
|------|----------|------|
| Staging Buffer | 支持 | **不支持** |
| 非 MLA SWA/NSA 跨 TP | 支持 | **不支持** |
| 底层后端 | 固定 RDMA | 可选 UCX/Libfabric |
| 硬件范围 | 通用 | 仅 NVIDIA |

---

### 3.3 fake（测试模拟）

**代码位置**：`disaggregation/fake/conn.py`（~115 行）
**传输方式**：无（内存模拟，零数据传输）

#### 核心类

| 类名 | 职责 |
|------|------|
| `FakeKVManager` | 空操作管理器 |
| `FakeKVSender` | 模拟发送（直接返回 Success） |
| `FakeKVReceiver` | 模拟接收（直接返回 Success） |
| BootstrapServer | **未实现** |

#### 行为特点

```
状态转换（无实际传输）：
  Bootstrapping → WaitingForInput → Success（立即完成）
```

- `send()`：空操作，立即返回
- `poll()`：立即返回 `KVPoll.Success`
- 不拷贝任何数据

#### 硬件要求

无。纯软件模拟。

#### 适用场景

- **CI/CD 测试**：无需 RDMA 硬件即可测试 PD 分离功能
- **功能验证**：验证请求流转逻辑，不关心 KV cache 内容
- **Warmup 预热**：发送 warmup 请求预热系统

#### 限制

- **P 端不可用**：`server_args.py:3535` 中断言 prefill 不支持 fake
- 不传输实际数据，KV cache 内容不确定
- 仅用于测试，不能用于生产

---

### 3.4 ascend（华为 Ascend NPU）

**代码位置**：`disaggregation/ascend/conn.py`（~140 行）
**传输方式**：Ascend D2D（Device-to-Device）
**继承关系**：继承自 Mooncake 所有类

#### 核心类

| 类名 | 继承自 | 额外逻辑 |
|------|--------|---------|
| `AscendKVManager` | `MooncakeKVManager` | Ascend Transfer Engine 初始化 |
| `AscendKVSender` | `MooncakeKVSender` | NPU ID 支持、分组传输优化 |
| `AscendKVReceiver` | `MooncakeKVReceiver` | 无额外逻辑 |
| `AscendKVBootstrapServer` | `MooncakeKVBootstrapServer` | 无额外逻辑 |

#### 特有优化

- **NPU ID 支持**：使用 NPU ID 替代 GPU ID 进行内存注册
- **批量注册优化**：针对小内存块的批量注册性能优化
- **分组传输**：按 index 分组传输，提高效率

#### 硬件要求

| 组件 | 要求 |
|------|------|
| 计算设备 | **华为 Ascend NPU** |
| 传输引擎 | Ascend Transfer Engine |
| 连接方式 | D2D（Device-to-Device） |

#### 适用场景

- **华为 Ascend NPU 集群**的 PD 分离部署
- 信创/国产化环境
- 需要 Mooncake 的全部功能但运行在 Ascend 硬件上

#### 特点

- 代码量极少（~140 行），复用 Mooncake 的成熟基础设施
- 继承 Mooncake 的 staging buffer、TP slice、heartbeat 等全部特性

---

### 3.5 mori（RDMA 高可配）

**代码位置**：`disaggregation/mori/conn.py`（~1100 行）
**传输方式**：RDMA（基于 Mori IOEngine）

#### 核心类

| 类名 | 职责 |
|------|------|
| `MoriKVManager` | 管理 Mori IOEngine 和内存描述符 |
| `MoriKVSender` | P 端发送 |
| `MoriKVReceiver` | D 端接收 |
| `MoriKVBootstrapServer` | Bootstrap 服务发现 |

#### 细粒度 RDMA 调优参数

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_MORI_QP_PER_TRANSFER` | `1` | 每次传输使用的 Queue Pair 数量 |
| `SGLANG_MORI_POST_BATCH_SIZE` | `-1`（自动） | Work Request 批量大小 |
| `SGLANG_MORI_NUM_WORKERS` | `1` | Worker 线程数 |

#### 关键特性

**内存描述符系统**：

```python
@msgspec.struct
class MemoryDesc:
    addr: int        # 内存地址
    size: int        # 大小
    desc_bytes: bytes  # 序列化的内存描述
```

独立的 GPU/CPU 内存描述符，通过 msgspec 高效序列化传输。

**TP Slice 配置**：

```python
@dataclass
class TPSliceConfig:
    src_tp_size: int      # 源 TP 大小
    dst_tp_size: int      # 目标 TP 大小
    dst_tp_rank: int      # 目标 TP rank
    num_kv_heads: int     # KV head 数
    head_dim: int         # Head 维度
    qk_rope_head_dim: int # RoPE head 维度
```

精确控制 head 维度的切片逻辑，支持 GQA 复制场景。

**消息守卫**：

`MORI_GUARD` 机制验证传输消息的完整性，防止畸形消息。

#### 硬件要求

| 组件 | 要求 |
|------|------|
| GPU | 通用 GPU |
| 网络 | RDMA 网卡 |
| 软件依赖 | Mori 库 |

#### 适用场景

- 需要**细粒度 RDMA 参数调优**的部署
- 特定网络拓扑下的性能优化
- QP/Worker/批量参数有定制需求

---

## 四、功能对比矩阵

### 传输能力

| 能力 | mooncake | nixl | fake | ascend | mori |
|------|:--------:|:----:|:----:|:------:|:----:|
| RDMA 传输 | ✓ | ✓ | — | — | ✓ |
| NVLink 传输 | ✓(有bug) | ✓ | — | — | — |
| D2D 传输 | — | — | — | ✓ | — |
| 模拟传输 | — | — | ✓ | — | — |

### KV Cache 传输模式

| 模式 | mooncake | nixl | fake | ascend | mori |
|------|:--------:|:----:|:----:|:------:|:----:|
| 标准传输（同TP） | ✓ | ✓ | — | ✓(继承) | ✓ |
| TP Slice（不同TP） | ✓ | ✓ | — | ✓(继承) | ✓ |
| Staging Buffer（异构TP） | ✓ | ✗ | — | ✓(继承) | ✗ |
| HiSparse（token粒度） | ✓ | — | — | — | — |
| Mamba State 传输 | ✓ | ✓ | — | ✓(继承) | ✓ |

### 可靠性机制

| 机制 | mooncake | nixl | fake | ascend | mori |
|------|:--------:|:----:|:----:|:------:|:----:|
| Heartbeat 心跳 | ✓ | ✓ | — | ✓(继承) | ✓ |
| Session 故障追踪 | ✓ | — | — | ✓(继承) | — |
| 传输状态通知 | ✓ | ✓ | — | ✓(继承) | ✓ |
| 消息完整性校验 | — | — | — | — | ✓(GUARD) |

### 架构特性

| 特性 | mooncake | nixl | fake | ascend | mori |
|------|:--------:|:----:|:----:|:------:|:----:|
| 多线程并行传输 | ✓ | — | — | ✓(继承) | ✓(Worker) |
| 插件化后端 | ✗ | ✓ | — | ✗ | ✗ |
| 内存描述符 | — | — | — | — | ✓(msgspec) |
| BootstrapServer | ✓ | ✓ | ✗ | ✓ | ✓ |

---

## 五、硬件与依赖对比

| 后端 | 计算设备 | 网络要求 | 软件依赖 | 额外安装 |
|------|---------|---------|---------|---------|
| **mooncake** | NVIDIA GPU | IB/RoCE | Mooncake Transfer Engine | `pip install mooncake` |
| **nixl** | NVIDIA GPU | RDMA | NIXL + backend plugin | `pip install nixl` |
| **fake** | 任意 | 无 | 无 | 无 |
| **ascend** | Ascend NPU | D2D | Ascend Transfer Engine | 厂商提供 |
| **mori** | 通用 GPU | RDMA | Mori IOEngine | `pip install mori` |

---

## 六、性能特性对比

| 维度 | mooncake | nixl | fake | ascend | mori |
|------|----------|------|------|--------|------|
| **传输延迟** | 低(RDMA) | 低(RDMA) | 极低(模拟) | 低(D2D) | 低(RDMA) |
| **传输吞吐** | 高(多线程) | 中 | N/A | 高(继承) | 可调(QP/Worker) |
| **CPU 开销** | 中(线程池) | 低(通知) | 极低 | 中(继承) | 可调 |
| **内存开销** | 中(staging) | 低 | 极低 | 中(继承) | 中(描述符) |
| **可调优性** | 中 | 低 | 无 | 中(继承) | **高**(QP/Worker/Batch) |

---

## 七、环境变量汇总

### Mooncake 相关

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_DISAGGREGATION_THREAD_POOL_SIZE` | CPU 数 | 传输线程池大小 |
| `SGLANG_DISAGGREGATION_QUEUE_SIZE` | `4` | 并行传输队列数 |
| `SGLANG_DISAGG_STAGING_BUFFER` | `0` | 启用 staging buffer |
| `SGLANG_DISAGG_STAGING_BUFFER_SIZE_MB` | `64` | 每 queue 的 staging 大小 |
| `SGLANG_DISAGG_STAGING_POOL_SIZE_MB` | `4096` | staging 总池大小 |

### NIXL 相关

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_DISAGGREGATION_NIXL_BACKEND` | `UCX` | 底层传输后端 |

### Mori 相关

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_MORI_QP_PER_TRANSFER` | `1` | 每 transfer 的 QP 数 |
| `SGLANG_MORI_POST_BATCH_SIZE` | `-1` | WR 批量大小 |
| `SGLANG_MORI_NUM_WORKERS` | `1` | Worker 线程数 |
| `SGLANG_MORI_NUM_MAX_DISPATCH_TOKENS_PER_RANK` | `4096` | 每 rank 最大 dispatch token 数 |

### 通用

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT` | `300` | Bootstrap 超时（秒） |
| `SGLANG_DISAGGREGATION_WAITING_TIMEOUT` | `300` | 等待传输超时（秒） |
| `SGLANG_DISAGGREGATION_HEARTBEAT_INTERVAL` | `5` | 心跳间隔（秒） |
| `SGLANG_DISAGGREGATION_HEARTBEAT_MAX_FAILURE` | `2` | 最大心跳失败次数 |

---

## 八、选型决策树

```
你的硬件是什么？
  │
  ├── 华为 Ascend NPU ──→ ascend
  │
  ├── NVIDIA GPU
  │     │
  │     ├── 测试/CI/无RDMA硬件 ──→ fake（仅D端）
  │     │
  │     └── 有 RDMA 网络
  │           │
  │           ├── P 和 D 的 TP size 不同？
  │           │     ├── 是 ──→ mooncake（唯一支持 staging buffer）
  │           │     └── 否
  │           │           │
  │           │           ├── 需要 UCX/Libfabric 灵活切换？──→ nixl
  │           │           │
  │           │           ├── 需要细粒度 RDMA 调优？──→ mori
  │           │           │
  │           │           └── 默认/不确定 ──→ mooncake
  │           │
  │           └── 默认 ──→ mooncake
  │
  └── 其他 GPU + RDMA ──→ mooncake 或 mori
```

---

## 九、GLM-4.7-Flash-30B-A3B 7P1D 推荐配置

### 标准配置（推荐）

```bash
# P 端和 D 端
--disaggregation-transfer-backend mooncake
--disaggregation-ib-device mlx5_0
```

### 异构 TP（如 P=TP16, D=TP8）

```bash
# P 端和 D 端
--disaggregation-transfer-backend mooncake
--disaggregation-ib-device mlx5_0,mlx5_1

# 环境变量
SGLANG_DISAGG_STAGING_BUFFER=1
SGLANG_DISAGG_STAGING_POOL_SIZE_MB=8192
```

### 纯测试环境（无 RDMA）

```bash
# 仅 D 端
--disaggregation-transfer-backend fake
# P 端使用 mooncake 或其他真实后端
```

---

## 十、关键源码文件索引

| 文件 | 行数 | 功能 |
|------|------|------|
| `disaggregation/mooncake/conn.py` | ~1900 | Mooncake 后端（最完整） |
| `disaggregation/nixl/conn.py` | ~1250 | NIXL 后端 |
| `disaggregation/mori/conn.py` | ~1100 | Mori 后端 |
| `disaggregation/ascend/conn.py` | ~140 | Ascend 后端（继承 Mooncake） |
| `disaggregation/fake/conn.py` | ~115 | Fake 后端（测试） |
| `disaggregation/common/conn.py` | — | 公共基础设施 |
| `disaggregation/base/conn.py` | ~47 | KVPoll 枚举、基类定义 |
| `server_args.py:698` | — | 默认值 `mooncake` |
| `server_args.py:6109-6113` | — | CLI 参数定义 |
