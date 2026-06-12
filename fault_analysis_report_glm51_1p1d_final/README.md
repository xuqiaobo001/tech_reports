# GLM 5.1 1P1D 分离部署故障分析报告

> **版本**: v2.0 (完整版)
> **日期**: 2026-06-11
> **范围**: Mooncake + etcd + vLLM 全链路故障分析
> **部署形态**: 1P1D 分离部署 + Mooncake 一主两从 (K8s) + etcd 一主两从 (K8s) + 无反亲和

---

## 目录

1. [架构概述](#1-架构概述)
2. [故障场景总览](#2-故障场景总览)
3. [Mooncake Bootstrap Server 故障](#3-mooncake-bootstrap-server-故障)
4. [Mooncake Transfer Engine 故障](#4-mooncake-transfer-engine-故障)
5. [Mooncake Master Server 故障](#5-mooncake-master-server-故障)
6. [etcd / 元数据服务故障](#6-etcd--元数据服务故障)
7. [K8s 无反亲和导致的级联故障](#7-k8s-无反亲和导致的级联故障)
8. [vLLM Prefill 实例故障](#8-vllm-prefill-实例故障)
9. [vLLM Decode 实例故障](#9-vllm-decode-实例故障)
10. [Proxy 代理故障](#10-proxy-代理故障)
11. [网络故障](#11-网络故障)
12. [KV Cache 一致性故障](#12-kv-cache-一致性故障)
13. [资源耗尽故障](#13-资源耗尽故障)
14. [异步操作相关故障](#14-异步操作相关故障)
15. [vLLM 代码层面 HA 盲区分析](#15-vllm-代码层面-ha-盲区分析)
16. [综合风险评估](#16-综合风险评估)
17. [改进建议](#17-改进建议)
18. [结论](#18-结论)

---

## 1. 架构概述

### 1.1 系统组件

| 组件 | 角色 | 部署位置 |
|------|------|----------|
| **vLLM Prefill 实例 (P)** | KV Cache 生产者，执行 Prefill 计算并将 KV Cache 传输给 Decode | 昇腾服务器 A |
| **vLLM Decode 实例 (D)** | KV Cache 消费者，接收 KV Cache 并执行 Decode 生成 | 昇腾服务器 B |
| **Mooncake Transfer Engine** | RDMA/TCP 传输引擎，负责 KV Cache 的零拷贝跨节点传输 | 两台服务器均有 |
| **Mooncake Bootstrap Server** | 轻量级 HTTP 服务发现，P 端注册、D 端查询 P 端地址 | P 端全局 Rank 0 |
| **Mooncake Master Server** | 分布式存储协调器（Store 模式），管理元数据和段分配 | K8s 一主两从，端口 50051 |
| **Proxy 代理** | 请求路由，将客户端请求分发到 P 和 D 实例 | 可独立部署 |
| **etcd** | 元数据存储后端（Mooncake Master 内部使用） | K8s 一主两从 |

### 1.2 部署拓扑

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Kubernetes Cluster (控制面)                         │
│                                                                              │
│  ┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐   │
│  │     K8s Node A       │ │     K8s Node B       │ │     K8s Node C       │   │
│  │                      │ │                      │ │                      │   │
│  │  ┌───────────────┐   │ │  ┌───────────────┐   │ │  ┌───────────────┐   │   │
│  │  │ Mooncake      │   │ │  │ Mooncake      │   │ │  │ Mooncake      │   │   │
│  │  │ Master-0      │   │ │  │ Master-1      │   │ │  │ Master-2      │   │   │
│  │  │ (Leader)      │   │ │  │ (Follower)    │   │ │  │ (Follower)    │   │   │
│  │  └───────────────┘   │ │  └───────────────┘   │ │  └───────────────┘   │   │
│  │  ┌───────────────┐   │ │  ┌───────────────┐   │ │  ┌───────────────┐   │   │
│  │  │  etcd-0       │   │ │  │  etcd-1       │   │ │  │  etcd-2       │   │   │
│  │  │ (Leader)      │   │ │  │ (Follower)    │   │ │  │ (Follower)    │   │   │
│  │  └───────────────┘   │ │  └───────────────┘   │ │  └───────────────┘   │   │
│  │                      │ │                      │ │                      │   │
│  │  ⚠️ 无反亲和：         │ │                      │ │                      │   │
│  │  Mooncake+etcd 可能  │ │                      │ │                      │   │
│  │  共处同一 Node        │ │                      │ │                      │   │
│  └─────────────────────┘ └─────────────────────┘ └─────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        推理面 (物理机)                                        │
│                                                                              │
│   Client ──HTTP──► Proxy ──HTTP──►┐                    ┌──HTTP──►          │
│                                    │                    │                    │
│              ┌─────────────────────▼──────┐  ┌─────────▼──────────────────┐│
│              │  昇腾服务器 A                │  │  昇腾服务器 B               ││
│              │  vLLM Prefill (kv_producer) │  │  vLLM Decode (kv_consumer) ││
│              │  + Bootstrap Server (:8998) │  │                             ││
│              │  + Mooncake TransferEngine  │  │  + Mooncake TransferEngine ││
│              └──────────┬─────────────────┘  └──────────┬────────────────┘│
│                         │        RDMA / ZMQ              │                 │
│                         └────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 请求流转路径

```
Client → Proxy → Prefill (计算 KV Cache + max_tokens=1, do_remote_decode=True)
                  ↓ (KV Cache 通过 Mooncake RDMA 传输)
                Decode (加载 KV Cache + 生成剩余 tokens, do_remote_prefill=True)
                  ↓ (流式响应)
         Proxy → Client
```

### 1.4 关键部署参数

| 参数 | 值 | 风险说明 |
|------|-----|----------|
| Mooncake Master 副本数 | 3 (1 Primary + 2 Follower) | K8s 层面有 HA |
| etcd 副本数 | 3 (1 Primary + 2 Follower) | K8s 层面有 HA，仲裁需 2/3 |
| Mooncake ↔ etcd 反亲和 | **未配置** | Pod 可能共处同一 Node |
| vLLM 侧 `master_server_address` | **单一字符串** (`str`) | 仅指向一个端点，无 Failover |
| vLLM 侧重试/重连 | **无** | 操作失败直接记录日志 |
| P 端 Abort 超时 | 480 秒 | 异常请求长期占用 block |
| D 端 ZMQ 超时 | 540 秒 | 等于 P 端超时 + 60 秒 |

### 1.5 核心矛盾

> **K8s 层面做了 3 副本高可用，但 vLLM 代码层面完全无法利用。** `master_server_address` 是单一 `str`，`store.setup()` 只连接一个端点。即使 Mooncake Master 有 3 个副本，vLLM Worker 只认一个地址，该地址对应的 Pod 挂了就等于整个 Master 不可用。

---

## 2. 故障场景总览

本报告共识别 **43 个故障场景**，按严重程度分布如下：

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| 🔴🔴🔴 灾难级 | 1 | 系统永久不可用，需人工介入恢复 |
| 🔴🔴 极严重 | 1 | 双组件同时失效，恢复时间 10-30 秒 |
| 🔴 严重 | 21 | 系统或请求级故障，部分/全部不可用 |
| 🟡 中等 | 20 | 单请求失败或性能退化，系统整体可用 |

### 按组件分类

| 组件 | 🔴🔴🔴 | 🔴🔴 | 🔴 | 🟡 | 合计 |
|------|--------|------|-----|-----|------|
| Mooncake Bootstrap Server | - | - | 1 | 2 | 3 |
| Mooncake Transfer Engine | - | - | 3 | 1 | 4 |
| Mooncake Master Server | - | - | 1 | 1 | 2 |
| etcd | - | - | 2 | 1+1 | 4 |
| K8s 无反亲和级联 (K1-K8) | 1 | 1 | 5 | 1 | 8 |
| vLLM Prefill | - | - | 2 | 1 | 3 |
| vLLM Decode | - | - | 1 | 2 | 3 |
| Proxy | - | - | 1 | 2+1 | 4 |
| 网络 | - | - | 2 | 1 | 3 |
| KV Cache 一致性 | - | - | 2 | 2 | 4 |
| 资源耗尽 | - | - | - | 3 | 3 |
| 异步操作 | - | - | 1 | 1 | 2 |
| **合计** | **1** | **1** | **21** | **20** | **43** |

---

## 3. Mooncake Bootstrap Server 故障

### 3.1 Bootstrap Server 启动失败

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill 端全局 Rank 0 的 Bootstrap Server 无法启动（端口被占用、权限问题等） |
| **影响范围** | 所有新的 Decode 实例无法发现 Prefill 实例 |
| **影响程度** | 🔴 **严重** — 系统完全无法建立新的 P-D 连接 |
| **故障表现** | Decode 端调用 `_connect_to_prefiller_bootstrap` 查询 GET `/query` 时失败，日志报错但不会崩溃，KV 传输无法初始化 |
| **代码位置** | `mooncake_utils.py` (Bootstrap Server), `mooncake_connector.py` (查询端) |
| **恢复策略** | 无自动恢复机制。需手动排查端口冲突/权限问题后重启 Prefill 实例 |

### 3.2 Bootstrap Server 运行中崩溃

| 维度 | 描述 |
|------|------|
| **故障描述** | Bootstrap Server 在运行过程中因 OOM、线程异常等原因崩溃 |
| **影响范围** | 已建立的 P-D 连接不受影响；新的 Decode 连接或重连失败 |
| **影响程度** | 🟡 **中等** — 已有连接继续工作，但无法建立新连接 |
| **故障表现** | 运行中的请求正常完成。新请求到达 Decode 端时，若需查询新的 engine_id，GET `/query` 请求失败 |
| **代码位置** | `mooncake_utils.py:68-88` (uvicorn daemon thread) |
| **恢复策略** | Bootstrap Server 运行在 daemon 线程中，随主进程退出。无独立重启机制 |

### 3.3 Bootstrap Server 数据不一致

| 维度 | 描述 |
|------|------|
| **故障描述** | Worker 注册信息丢失或 engine_id 不一致（例如 Prefill 重启后 engine_id 变化但旧数据残留） |
| **影响范围** | Decode 端可能查询到过时的 Prefill 地址信息 |
| **影响程度** | 🟡 **中等** — 导致 KV 传输连接到错误或不存在的端点 |
| **故障表现** | Decode 端尝试连接过时的 Prefill 地址，RDMA 连接失败，请求超时 |
| **代码位置** | `mooncake_utils.py:111-122` (重复注册检测，engine_id 一致性校验返回 HTTP 400) |
| **恢复策略** | Bootstrap Server 验证 engine_id 一致性并拒绝不匹配的注册。需重启相关实例 |

---

## 4. Mooncake Transfer Engine 故障

### 4.1 RDMA 传输失败

| 维度 | 描述 |
|------|------|
| **故障描述** | `batch_transfer_sync_write` 返回非零错误码（RDMA 连接断开、NIC 故障、MR 注册失败等） |
| **影响范围** | 当前批次中所有请求的 KV Cache 传输失败 |
| **影响程度** | 🔴 **严重** — 批次内所有请求失败，用户收到错误响应或请求挂起 |
| **故障表现** | P 端 `_send_blocks` 日志警告，记录 failed_transfer 统计。D 端收不到 KV 数据，请求挂起直到超时（默认 480s + 60s） |
| **代码位置** | `mooncake_connector.py:1357-1386` |
| **恢复策略** | 无重试机制。根据 `kv_load_failure_policy`：`"fail"`（默认）请求标记为 `FINISHED_ERROR`；`"recompute"` 重新调度重计算 |

### 4.2 ZMQ 通道通信故障

| 维度 | 描述 |
|------|------|
| **故障描述** | P 端和 D 端之间的 ZMQ ROUTER/DEALER 通道断开（网络故障、socket 错误等） |
| **影响范围** | 所有正在进行的 KV 传输请求 |
| **影响程度** | 🔴 **严重** — 传输协调完全中断 |
| **故障表现** | D 端 `receive_kv_from_single_worker` 捕获 `zmq.ContextTerminated` 或其他异常，静默返回。P 端的请求块最终因超时被释放 |
| **代码位置** | `mooncake_connector.py:1561-1589` |
| **恢复策略** | 无自动重连机制。ZMQ context 终止后需要重新初始化整个连接 |

### 4.3 传输超时

| 维度 | 描述 |
|------|------|
| **故障描述** | P 端等待 block 就绪超过 `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT`（默认 480 秒） |
| **影响范围** | 超时请求的 KV Cache block 被释放，D 端收到不完整数据 |
| **影响程度** | 🟡 **中等** — 单个请求受影响，其他请求可继续 |
| **故障表现** | P 端 `fetch_finished_sending_reqs` 检测到过期请求，释放 block。D 端 ZMQ 接收超时（480s + 60s），记录 `record_failed_recv` |
| **代码位置** | `mooncake_connector.py:1066-1085` (等待超时), `mooncake_connector.py:1470-1490` (过期清理) |
| **恢复策略** | Block 被释放，请求失败。D 端根据 `kv_load_failure_policy` 处理 |

### 4.4 Transfer Engine 初始化失败

| 维度 | 描述 |
|------|------|
| **故障描述** | `TransferEngine.initialize()` 失败（RDMA 设备不可用、驱动问题、内存不足） |
| **影响范围** | 整个实例无法启动 |
| **影响程度** | 🔴 **严重** — 实例级别故障 |
| **故障表现** | 抛出 `RuntimeError`，worker 进程无法初始化 |
| **代码位置** | `mooncake_connector.py:746-767` |
| **恢复策略** | 需要排查 RDMA 环境（驱动、设备状态、内存）后重启实例 |

---

## 5. Mooncake Master Server 故障

### 5.1 Master Server 不可用

| 维度 | 描述 |
|------|------|
| **故障描述** | `mooncake_master` 进程崩溃或网络不可达（端口 50051） |
| **影响范围** | Store 模式下所有 KV Cache 存取操作失败 |
| **影响程度** | 🔴 **严重** — Store 模式完全不可用 |
| **故障表现** | `MooncakeDistributedStore` 的 `batch_put`/`batch_get` 操作返回错误码或超时。Lookup 返回 0（降级为全量重计算） |
| **代码位置** | `store/worker.py` (store 操作) |
| **恢复策略** | Store lookup 失败时降级为全量计算（返回 0 cache hit）。Put/Get 失败根据错误码处理。**vLLM 不会自动重连新 Leader** |

### 5.2 Master Server 段分配不均

| 维度 | 描述 |
|------|------|
| **故障描述** | Master Server 的 global segment 分配不均衡，导致某节点内存耗尽 |
| **影响范围** | 受影响节点的 Store 操作返回 `MOONCAKE_NO_AVAILABLE_HANDLE` (-200) |
| **影响程度** | 🟡 **中等** — 触发反压机制，部分请求被跳过 |
| **故障表现** | Store put 失败，`_store_pressure_active` 被激活，后续 put 请求被跳过直到压力缓解 |
| **代码位置** | `store/worker.py:647-701` (backpressure 处理) |
| **恢复策略** | 反压机制自动缓解。当后续 store 批次成功时，压力标记被清除 |

---

## 6. etcd / 元数据服务故障

> **说明**：etcd 作为 Mooncake Master Server 的内部元数据存储后端，部署为 K8s 上 1 主 2 从的 3 节点集群。

### 6.1 etcd 集群完全不可用

| 维度 | 描述 |
|------|------|
| **故障描述** | etcd 集群所有节点宕机或网络分区导致无法达成仲裁 |
| **影响范围** | Mooncake Master Server 无法读写元数据，无法协调段分配和 worker 注册 |
| **影响程度** | 🔴 **严重** — Store 模式下的新连接和段分配完全失败 |
| **故障表现** | Mooncake Master 的 gRPC 调用超时或返回错误。新加入的 worker 无法注册段，已有连接的 put/get 可能成功（使用缓存）也可能失败 |
| **恢复策略** | 依赖 Mooncake Master 的内部缓存和容错机制。需恢复 etcd 集群后才能接受新连接 |

### 6.2 etcd Leader 选举超时/频繁切换

| 维度 | 描述 |
|------|------|
| **故障描述** | etcd 集群网络抖动导致 Leader 频繁切换 |
| **影响范围** | 元数据读写延迟增大，可能出现短暂不可用窗口 |
| **影响程度** | 🟡 **中等** — 间歇性延迟，可能导致 Store 操作超时 |
| **故障表现** | Mooncake Master 对 etcd 的读写操作延迟升高，Store 操作 RT 增大 |
| **恢复策略** | etcd 自身的 Raft 协议会完成选举恢复。频繁切换需排查网络问题 |

### 6.3 etcd 数据损坏或丢失

| 维度 | 描述 |
|------|------|
| **故障描述** | etcd 数据目录损坏，段分配信息、worker 注册信息丢失 |
| **影响范围** | 所有依赖 etcd 元数据的 Store 操作可能使用过时或不一致的数据 |
| **影响程度** | 🔴 **严重** — 可能导致 KV Cache 数据定位错误 |
| **故障表现** | Worker 查询到不存在的段地址，RDMA 操作失败。或者多个 Worker 被分配到重叠的内存区域 |
| **恢复策略** | 需要从备份恢复 etcd 数据，或清空后重新注册所有 Worker |

### 6.4 etcd 性能退化（慢查询）

| 维度 | 描述 |
|------|------|
| **故障描述** | etcd 响应变慢（磁盘 I/O 瓶颈、大量 Key 等） |
| **影响范围** | Store 操作延迟增大，间接影响推理延迟 |
| **影响程度** | 🟡 **中低** — 推理延迟升高但功能正常 |
| **故障表现** | P99 延迟升高。Mooncake Store 的 `batch_is_exist`（Lookup）调用耗时增加 |
| **恢复策略** | 优化 etcd 性能（增加节点、使用 SSD、压缩历史版本） |

---

## 7. K8s 无反亲和导致的级联故障

> 本章是本报告最关键的新增内容。由于 Mooncake Master 和 etcd 均为 1 主 2 从部署在 K8s 上，且**没有配置 Pod 反亲和规则**，两者可能被调度到同一 K8s Node，导致单点故障被放大为级联故障。

### 数学背景：共处概率

3 个 Mooncake Pod 和 3 个 etcd Pod 分布在 3 个 Node 上，无反亲和约束时：
- 至少一个 Node 同时运行 Mooncake 和 etcd Pod 的概率 ≈ **77.8%**（生日问题变体）
- Mooncake Leader 和 etcd Leader 共处同一 Node 的概率 ≈ **33.3%**（假设 Leader 随机分布）

### 7.1 [K1] 单 Node 宕机导致 Mooncake + etcd 双 Leader 同时丢失

| 维度 | 描述 |
|------|------|
| **故障描述** | 由于无反亲和，Mooncake Master Leader 和 etcd Leader 的 Pod 可能被调度到同一个 K8s Node。该 Node 宕机导致两个 Leader 同时丢失 |
| **发生概率** | 🟠 **中等偏高** (~33% 概率双 Leader 共处) |
| **影响程度** | 🔴🔴 **极严重** — 双 Leader 同时丢失，恢复时间叠加 |

**级联路径**：

```
K8s Node A 宕机
  │
  ├─→ Mooncake Master Leader Pod 挂了
  │     ├─ Mooncake 内部触发 Leader 选举（剩余 2 个 Follower 争抢）
  │     ├─ 选举期间所有写操作阻塞
  │     └─ ⚠️ vLLM Worker 侧完全无感知
  │           ├─ store.setup() 已完成，持有旧连接
  │           ├─ 后续 batch_put/batch_get 调用超时或返回错误
  │           └─ ⚠️ 不会自动重连到新 Leader
  │
  └─→ etcd Leader Pod 也挂了（同 Node）
        ├─ etcd 集群触发 Leader 选举（需 2/3 票 = 2 票）
        ├─ 选举期间所有读写操作阻塞（1-5 秒）
        │
        ├─→ Mooncake 新 Leader 需要读 etcd 恢复状态
        │     └─ etcd 还在选举 → 读不到 → 等待 → 重试
        │
        └─→ 双重选举叠加
              ├─ 正常分离部署: ~2-5s 恢复
              ├─ 无反亲和共处: ~10-30s 恢复
              └─ ⚠️ 极端情况：互相等待 → 活锁
```

**关键代码佐证**：
- `store/worker.py:106` — `master_server_address: str`，单一端点
- `store/worker.py:997-1009` — `store.setup()` 无重试
- `store/worker.py:855-903` — 运行时 `batch_get` 异常仅 `logger.warning`

### 7.2 [K2] 网络抖动触发双组件同时选举（活锁风险）

| 维度 | 描述 |
|------|------|
| **故障描述** | 无反亲和导致 Mooncake 和 etcd 的 Leader 共处同一 Node。网络抖动同时触发两者的 Leader 切换 |
| **发生概率** | 🟠 **中等** — 网络不稳定时大概率同时触发 |
| **影响程度** | 🔴 **严重** — 选举窗口叠加，服务中断时间翻倍 |
| **极端风险** | 如果 Mooncake 选举依赖 etcd 读取状态 → etcd 不可用 → Mooncake 选举超时 → 互相等待 → 活锁 |
| **恢复时间** | 可能超过 30 秒，极端情况需人工介入 |

**影响量化**：假设网络抖动每 10 分钟发生一次：
- 有反亲和：恢复 ~5 秒，可用性损失 0.83%
- 无反亲和：恢复 ~20 秒，可用性损失 3.33%，P99 延迟飙升 4 倍

### 7.3 [K3] Node 资源争抢导致持续性能退化

| 维度 | 描述 |
|------|------|
| **故障描述** | Mooncake Master Pod 和 etcd Pod 共享同一 Node 的 CPU/内存/磁盘 I/O |
| **发生概率** | 🔴 **极高** (~78% 概率至少一对共处) |
| **影响程度** | 🟡 **中等** — 持续性延迟升高 |
| **量化影响** | etcd P99 延迟可能从 <10ms 升至 50-100ms，传导至 Store 操作延迟升高 5-10x |
| **隐性风险** | etcd 延迟升高可能导致 Mooncake 操作超时，触发不必要的 Leader 切换 → 恶性循环 |

### 7.4 [K4] 滚动更新期间服务反复中断

| 维度 | 描述 |
|------|------|
| **故障描述** | 对 Mooncake Master 或 etcd 进行滚动更新时，一次 Pod 终止可能同时影响两者 |
| **发生概率** | 🔴 **确定发生** — 每次滚动更新都会触发 |
| **影响程度** | 🔴 **严重** — 3 次滚动步骤，每次都可能触发双选举 |
| **总中断时间** | 3 × (Mooncake 选举 + etcd 选举) ≈ 30-90 秒 |

### 7.5 [K5] 两 Node 宕机 → etcd 丧失仲裁 → 永久不可用

| 维度 | 描述 |
|------|------|
| **故障描述** | 3 台 K8s Node 中有 2 台同时故障（机架级断电、ToR 交换机故障等） |
| **发生概率** | 🟢 **低** — 需要基础设施级别故障 |
| **影响程度** | 🔴🔴🔴 **灾难级** — 永久不可用，无法自动恢复 |
| **故障表现** | etcd 仅剩 1/3 成员无法达成仲裁，所有读写永久阻塞；Mooncake Master 最多仅剩 1 个副本，无法选举 |
| **恢复方式** | 从快照恢复 etcd → 重启 Mooncake Master → 重启 vLLM Worker |
| **恢复时间** | 30 分钟 - 数小时 |

### 7.6 [K6] vLLM Worker 连接的端点不是当前 Leader

| 维度 | 描述 |
|------|------|
| **故障描述** | `master_server_address` 指向固定 IP，Mooncake Leader 切换后旧地址变成 Follower |
| **发生概率** | 🔴 **极高** — 每次 Mooncake Leader 切换后必然发生 |
| **影响程度** | 🟡~🔴 **中等到严重** — 取决于 Mooncake Follower 是否转发请求 |
| **故障表现** | Follower 不转发：所有操作失败。Follower 转发：延迟翻倍（Follower → Leader） |
| **代码盲区** | vLLM 不知道 Follower 是否能处理请求，也不知道新 Leader 地址 |

### 7.7 [K7] K8s OOM Kill 同时杀掉同一 Node 上的 Mooncake + etcd

| 维度 | 描述 |
|------|------|
| **故障描述** | 高负载时内存压力导致 OOM Kill，可能同时杀掉同一 Node 上的 Mooncake 和 etcd Pod |
| **发生概率** | 🟠 **中等** — 高负载时内存压力增大 |
| **影响程度** | 🔴 **严重** — 同 K1，双组件同时丢失 |
| **加重因素** | Mooncake 段分配 + etcd KV 写入 → 内存峰值叠加 → 更容易触发 OOM |

### 7.8 [K8] vLLM Worker 初始化时 Master 正在选举 → 启动失败

| 维度 | 描述 |
|------|------|
| **故障描述** | Worker 启动时调用 `store.setup()`，恰好遇到 Mooncake Leader 选举中 |
| **发生概率** | 🟠 **中等** — 新部署/扩容/滚动更新时大概率撞上 |
| **影响程度** | 🔴 **严重** — Worker 启动失败 |
| **代码位置** | `store/worker.py:997-1009` 无重试，直接 `raise RuntimeError` |
| **恢复方式** | 等选举完成后手动重启 Worker（K8s restartPolicy 可自动重试，但有退避延迟） |

---

## 8. vLLM Prefill 实例故障

### 8.1 Prefill 进程崩溃

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill 实例因 OOM、NPU 错误、Python 异常等原因崩溃 |
| **影响范围** | 所有正在 Prefill 端处理的请求丢失，Bootstrap Server 随之不可用 |
| **影响程度** | 🔴 **严重** — 系统整体不可用（1P1D 架构无冗余） |
| **故障表现** | Proxy 检测到 Prefill 健康检查失败，返回 HTTP 502/503。正在传输 KV 的 Decode 端连接断开 |
| **恢复策略** | 需手动重启 Prefill 实例。Proxy 无自动恢复逻辑 |

### 8.2 Prefill Worker 内部错误

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill Worker 的 `engine_client` 进入 errored 状态 |
| **影响范围** | 该 Prefill 实例不再接受新请求 |
| **影响程度** | 🔴 **严重** — 所有后续请求失败 |
| **故障表现** | `serving.py` 中检测到 `self.engine_client.errored`，抛出 `dead_error` |
| **代码位置** | `serving.py:113-114` |
| **恢复策略** | 需重启 Prefill 实例，无自动恢复 |

### 8.3 Prefill 端 Block 资源耗尽

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill 端 GPU 显存不足以分配 KV Cache block |
| **影响范围** | 当前请求无法调度，等待 block 释放 |
| **影响程度** | 🟡 **中等** — 请求排队等待，延迟升高 |
| **故障表现** | Scheduler 无法为请求分配 block，请求保持在 WAITING 状态 |
| **恢复策略** | Scheduler 的 preemption 机制会驱逐低优先级请求以释放 block |

---

## 9. vLLM Decode 实例故障

### 9.1 Decode 进程崩溃

| 维度 | 描述 |
|------|------|
| **故障描述** | Decode 实例因 OOM、NPU 错误等原因崩溃 |
| **影响范围** | 所有正在 Decode 端生成的请求丢失。已 Prefill 完成但尚未传输的 KV Cache 被浪费 |
| **影响程度** | 🔴 **严重** — 系统整体不可用（1P1D 架构无冗余） |
| **恢复策略** | 需手动重启 Decode 实例 |

### 9.2 Decode 端 KV Cache 加载失败

| 维度 | 描述 |
|------|------|
| **故障描述** | Decode 端加载从 Prefill 传来的 KV Cache 时，部分 block 加载失败 |
| **影响范围** | 受影响请求的生成质量受损或请求失败 |
| **影响程度** | 🟡~🔴 **中等到严重** — 取决于失败 block 数量和恢复策略 |
| **故障表现** | Worker 通过 `get_block_ids_with_load_errors()` 报告失败 block。根据策略：`"fail"` 标记 `FINISHED_ERROR`；`"recompute"` 截断并重新计算 |
| **代码位置** | `scheduler.py:141` (策略检查), `kv_cache_coordinator.py` (block 管理) |
| **恢复策略** | `"recompute"` 策略可自动恢复。共享 block 的多请求场景中，仅第一个请求重计算 |

### 9.3 Decode 端生成超时

| 维度 | 描述 |
|------|------|
| **故障描述** | Decode 端因 KV Cache 不完整导致 attention 计算异常，生成质量严重下降或无限循环 |
| **影响范围** | 受影响请求可能输出乱码或永远不结束 |
| **影响程度** | 🟡 **中等** — 单请求受影响，但可能占用资源 |
| **恢复策略** | 依赖客户端超时设置。服务端可通过 `max_tokens` 限制保护 |

---

## 10. Proxy 代理故障

### 10.1 Proxy 进程崩溃

| 维度 | 描述 |
|------|------|
| **故障描述** | Proxy 进程崩溃（OOM、未处理异常等） |
| **影响范围** | 所有客户端连接断开。P 和 D 实例仍在运行但无法接收新请求 |
| **影响程度** | 🔴 **严重** — 系统对外不可用 |
| **恢复策略** | 需重启 Proxy。已 Prefill 但未 Decode 的请求数据被浪费 |

### 10.2 Proxy 到 Prefill 通信失败

| 维度 | 描述 |
|------|------|
| **故障描述** | Proxy 与 Prefill 之间的 HTTP 连接失败 |
| **影响范围** | 当前请求的 Prefill 阶段失败 |
| **影响程度** | 🟡 **中等** — 单请求失败 |
| **故障表现** | `disagg_proxy_demo.py` 返回 HTTP 502 Bad Gateway。`mooncake_connector_proxy.py` 版本直接抛异常 |
| **代码位置** | `disagg_proxy_demo.py:258-278` |
| **恢复策略** | 部分代理实现可自动移除不健康实例。1P1D 场景下移除后系统不可用 |

### 10.3 Proxy 到 Decode 通信失败

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill 已完成，但 Proxy 转发到 Decode 时连接失败 |
| **影响范围** | 已完成的 Prefill 计算和 KV Cache 传输被浪费 |
| **影响程度** | 🟡 **中等** — 请求失败，Prefill 资源已消耗 |
| **恢复策略** | 需客户端重试。Prefill 端 block 在超时后自动释放 |

### 10.4 Proxy 调度不均衡

| 维度 | 描述 |
|------|------|
| **故障描述** | Round-Robin 调度导致请求集中在某个实例 |
| **影响程度** | 🟡 **中低** — 1P1D 场景下影响较小 |
| **恢复策略** | XpYd 场景下可考虑加权调度或最少连接数调度 |

---

## 11. 网络故障

### 11.1 Prefill 与 Decode 之间网络分区

| 维度 | 描述 |
|------|------|
| **故障描述** | 两台昇腾服务器之间的网络完全断开 |
| **影响范围** | RDMA 传输和 ZMQ 通道全部中断 |
| **影响程度** | 🔴 **严重** — KV Cache 传输完全失败 |
| **故障表现** | RDMA 连接断开，ZMQ socket 错误。所有传输中的请求失败。P 端 block 超时释放，D 端接收超时 |
| **恢复策略** | 网络恢复后，已有连接可能需要重建。无自动重连机制，可能需重启实例 |

### 11.2 RDMA 网络降级（丢包/高延迟）

| 维度 | 描述 |
|------|------|
| **故障描述** | RDMA 网络质量下降，出现丢包或高延迟 |
| **影响范围** | KV Cache 传输变慢，吞吐量下降 |
| **影响程度** | 🟡 **中等** — 延迟升高但功能可能正常 |
| **恢复策略** | TCP 回退模式（如果配置了 `mooncake_protocol: "tcp"`）。否则需修复 RDMA 网络 |

### 11.3 MTU 不匹配导致 RDMA 传输失败

| 维度 | 描述 |
|------|------|
| **故障描述** | 两台服务器之间的 RDMA 网络路径存在 MTU 不匹配 |
| **影响范围** | 大块 KV Cache 传输失败 |
| **影响程度** | 🔴 **严重** — 传输间歇性失败 |
| **故障表现** | `batch_transfer_sync_write` 返回非零错误码，部分请求成功部分失败 |
| **恢复策略** | 统一网络 MTU 配置 |

---

## 12. KV Cache 一致性故障

### 12.1 Block 数量不匹配

| 维度 | 描述 |
|------|------|
| **故障描述** | P 端和 D 端对同一请求分配的 block 数量不一致 |
| **影响范围** | 该请求的 KV Cache 传输失败 |
| **影响程度** | 🟡 **中等** — 单请求失败 |
| **代码位置** | `mooncake_connector.py:1214-1250` |

### 12.2 Region 长度验证失败

| 维度 | 描述 |
|------|------|
| **故障描述** | 异步 Region 长度在 P 端和 D 端之间不匹配 |
| **影响程度** | 🟡 **中等** — 单请求失败 |
| **代码位置** | `mooncake_connector.py:193-244` |

### 12.3 Block 过期与释放竞态

| 维度 | 描述 |
|------|------|
| **故障描述** | P 端 block 过期释放后，D 端仍在尝试读取（极端时序场景） |
| **影响范围** | D 端读取到无效或已释放的内存区域 |
| **影响程度** | 🔴 **严重** — 可能导致 NPU 计算错误或进程崩溃 |
| **代码位置** | `mooncake_connector.py:1470-1490` (过期检查), `mooncake_connector.py:1567-1568` (D 端超时) |
| **缓解措施** | D 端 ZMQ 超时比 P 端多 60 秒（540s vs 480s） |

### 12.4 模型参数/配置不一致

| 维度 | 描述 |
|------|------|
| **故障描述** | P 端和 D 端加载的模型版本、block size、KV Cache 格式不一致 |
| **影响范围** | 所有请求的 KV Cache 传输结果不可用 |
| **影响程度** | 🔴 **严重** — 系统级故障 |
| **恢复策略** | 确保 `PYTHONHASHSEED=0` 一致性。需统一模型和配置后重启 |

---

## 13. 资源耗尽故障

### 13.1 Prefill 端 GPU 显存耗尽

| 维度 | 描述 |
|------|------|
| **故障描述** | Prefill 端因大量并发请求导致 GPU 显存不足 |
| **影响程度** | 🟡 **中等** — 排队等待或 OOM 崩溃 |
| **恢复策略** | Preemption 自动处理轻度场景。重度需重启实例并调整并发参数 |

### 13.2 Decode 端 GPU 显存耗尽

| 维度 | 描述 |
|------|------|
| **故障描述** | Decode 端因 KV Cache block 积累导致 GPU 显存不足 |
| **影响程度** | 🟡 **中等** — 与 Prefill 端类似 |
| **恢复策略** | 调整 `reserved_blocks` 参数。Preemption 自动处理 |

### 13.3 Mooncake Store 磁盘 Offload 缓冲区不足

| 维度 | 描述 |
|------|------|
| **故障描述** | Store 模式下磁盘 staging buffer 不足以存放 offload 的 KV Cache |
| **影响程度** | 🟡 **中等** — 部分请求无法从 Store 加载 |
| **代码位置** | `store/worker.py:810-829` |
| **恢复策略** | 增加 `VLLM_MOONCAKE_DISK_STAGING_USABLE_RATIO` 或扩大磁盘空间 |

---

## 14. 异步操作相关故障

### 14.1 异步 KV 加载未完成即进入 Attention

| 维度 | 描述 |
|------|------|
| **故障描述** | 异步 KV Cache 加载尚未完成，请求已被调度执行 attention |
| **影响程度** | 🔴 **严重** — 输出质量严重下降 |
| **代码位置** | `scheduler.py` (WAITING_FOR_REMOTE_KVS 状态管理) |
| **恢复策略** | 正常情况下 Scheduler 通过 `WAITING_FOR_REMOTE_KVS` 状态确保异步加载完成。异常时依赖 `kv_load_failure_policy` |

### 14.2 请求取消/客户端断连时的资源泄漏

| 维度 | 描述 |
|------|------|
| **故障描述** | 客户端断开连接后，P 端已分配的 KV Cache block 未被及时释放 |
| **影响程度** | 🟡 **中等** — 长时间运行后影响系统容量 |
| **代码位置** | `mooncake_connector.py:1066-1085` (abort timeout 清理) |
| **恢复策略** | `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` 确保超时后释放。默认 480 秒可能过长 |

---

## 15. vLLM 代码层面 HA 盲区分析

### 15.1 代码完全无法利用 Mooncake 3 副本

经深入分析代码，确认以下致命设计缺陷：

| 缺陷 | 代码位置 | 说明 |
|------|----------|------|
| **单端点配置** | `store/worker.py:106` | `master_server_address: str`，不是 `list[str]` |
| **初始化无重试** | `store/worker.py:997-1009` | `store.setup()` 失败直接 `raise RuntimeError` |
| **运行时无重连** | `store/worker.py:855-903` | `batch_get` 异常仅 `logger.warning`，不尝试重连 |
| **无 Failover 感知** | 整个 store 模块 | 无断路器、无备用端点、无健康探测 |
| **配置静态加载** | `store/worker.py:125-141` | `from_file()` 启动时一次性读取，无法动态更新 |
| **Transfer Engine 无重试** | `mooncake_connector.py:1357-1386` | `batch_transfer_sync_write` 失败不重试 |
| **ZMQ 无重连** | `mooncake_connector.py:1561-1589` | 连接断开后静默返回 |
| **Bootstrap 查询无重试** | `mooncake_connector.py` | D 端查询 Bootstrap 无重试（P 端注册有无限重试） |

**实际效果对比**：

```
vLLM Worker 视角:
  ┌──────────────────────────────────────────┐
  │ master_server_address = "10.0.0.5:50051" │  ← 只认这一个地址
  │                                           │
  │ 10.0.0.5 挂了 → 我也挂了                  │  ← 即使 10.0.0.6/7 还活着
  │ 10.0.0.5 切 Leader → 我不知道             │  ← 不会自动切到新 Leader
  └──────────────────────────────────────────┘

实际 K8s 状态:
  Master-0 (10.0.0.5)  ❌ Pod 被 Kill
  Master-1 (10.0.0.6)  ✅ 新 Leader  ← vLLM 不知道
  Master-2 (10.0.0.7)  ✅ Follower   ← vLLM 不知道
```

### 15.2 完整故障传导链路

```
K8s Node 故障
    │
    ├─→ Mooncake Master Pod 被杀/迁移
    │      └─→ vLLM 的 master_server_address 指向的端点不可达
    │             ├─→ 初始化阶段: RuntimeError → Worker 启动失败 → 整个实例不可用
    │             └─→ 运行阶段:
    │                    ├─→ batch_put 失败 → KV Cache 无法保存 → 后续 Decode 全量重计算
    │                    ├─→ batch_get 失败 → block 标记为 invalid
    │                    │     ├─ kv_load_failure_policy="fail" → 请求失败
    │                    │     └─ kv_load_failure_policy="recompute" → 延迟飙升
    │                    └─→ batch_is_exist 失败 → lookup 返回 0 → 全量 Prefill → P 端过载
    │
    └─→ etcd Pod 也被杀/迁移（同 Node，无反亲和）
           └─→ Mooncake Master 新 Leader 也无法工作（读不到元数据）
                  └─→ 即使 vLLM 碰巧重连到新 Master，Master 也因 etcd 不可用而无法服务
```

---

## 16. 综合风险评估

### 16.1 风险热力图

```
                        影响程度
                     低      中        高        极高/灾难
                  ┌────────┬────────┬──────────┬──────────┐
   确定性发生     │        │ K4,    │          │          │
                  │        │ 10.4   │          │          │
                  ├────────┼────────┼──────────┼──────────┤
   高概率发生     │        │ K3,    │ K6,      │          │
                  │        │ 6.4    │ 13.1-2   │          │
                  ├────────┼────────┼──────────┼──────────┤
   中概率发生     │        │ 3.3,   │ K1, K2,  │          │
                  │        │ 10.2-3 │ K7, K8,  │          │
                  │        │ 14.2   │ 4.1-2,   │          │
                  │        │        │ 5.1, 8.2,│          │
                  │        │        │ 9.2,     │          │
                  │        │        │ 12.3-4   │          │
                  ├────────┼────────┼──────────┼──────────┤
   低概率发生     │        │        │ 11.3     │ K5       │
                  │        │        │          │ 灾难级   │
                  └────────┴────────┴──────────┴──────────┘
```

### 16.2 反亲和配置对风险的影响对比

| 风险场景 | 有反亲和 | 无反亲和（当前） | 风险放大倍数 |
|----------|----------|------------------|-------------|
| 单 Node 宕机影响 | 仅影响 1 个组件 | 同时影响 Mooncake + etcd | **2x** |
| 双 Leader 同时丢失 | 概率 ≈ 0 | 概率 ≈ 33% | **∞** |
| 网络抖动恢复时间 | 2-5 秒 | 10-30 秒 | **4-6x** |
| 滚动更新中断次数 | 最多 3 次（单组件） | 最多 6 次（双组件交替） | **2x** |
| 活锁风险 | 几乎不可能 | 中等 | **显著增加** |
| 资源争抢 | 无 | 高概率 (~78%) | **新增风险** |

---

## 17. 改进建议

### 17.1 P0 — 立即执行（配置变更，不改代码）

#### 17.1.1 配置 Pod 反亲和

在 Mooncake Master 和 etcd 的 Deployment/StatefulSet 中添加反亲和规则，确保同一 Node 不会同时运行两者的 Pod：

```yaml
# Mooncake Master Deployment
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: app
              operator: In
              values: ["etcd"]  # 不与 etcd 共处
        topologyKey: "kubernetes.io/hostname"
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
              - key: app
                operator: In
                values: ["mooncake-master"]  # 自身也尽量分散
          topologyKey: "kubernetes.io/hostname"
```

**预期效果**：消除 K1/K2/K3/K4/K7 全部级联故障风险，ROI 最高。

#### 17.1.2 为 Mooncake Master 配置 K8s Service

创建 Headless Service 或 ClusterIP Service 暴露 Mooncake Master，使用 DNS 名称而非直接 Pod IP：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mooncake-master
spec:
  clusterIP: None  # Headless
  selector:
    app: mooncake-master
  ports:
    - port: 50051
      targetPort: 50051
```

配合 readinessProbe 确保只有健康的 Leader Pod 被注入 Endpoints。

### 17.2 P1 — 短期改进（代码变更，1-2 周）

| 编号 | 建议 | 代码变更 | 预期效果 |
|------|------|----------|----------|
| 1 | **支持多 Master 端点** | `master_server_address: str` → `master_server_addresses: list[str]` | 利用 3 副本，主端点不可用时切换到副本 |
| 2 | **增加 `store.setup()` 重试** | 初始化时添加指数退避重试（3 次，间隔 2/4/8 秒） | 避免 K8 场景：启动时撞上选举 |
| 3 | **运行时 Store 操作重试** | `batch_put`/`batch_get`/`batch_is_exist` 添加有限次重试（2-3 次） | 瞬时故障（Leader 切换、网络抖动）自动恢复 |
| 4 | **增加 Transfer Engine 传输重试** | `batch_transfer_sync_write` 失败后指数退避重试 | 减少瞬时 RDMA 故障影响 |
| 5 | **缩短默认超时** | `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` 从 480s 降至 60-120s | 减少异常请求对 block 资源的占用时间 |

### 17.3 P2 — 中期改进（1 个月）

| 编号 | 建议 | 说明 |
|------|------|------|
| 6 | **增加 Master 健康探测** | Worker 后台线程定期 ping Master，不可达时尝试重连 |
| 7 | **增加断路器** | 连续 N 次失败后熔断，定期尝试恢复 |
| 8 | **ZMQ 连接重连机制** | D 端在连接断开后自动重连 P 端 |
| 9 | **Proxy 健康检查与自动移除/恢复** | 自动感知实例健康状态 |
| 10 | **默认启用 `kv_load_failure_policy="recompute"`** | KV 加载失败时自动恢复而非直接失败 |
| 11 | **增加 Prometheus/Grafana 监控** | 实时感知传输失败率、延迟、资源使用 |

### 17.4 P3 — 架构级改进（季度）

| 编号 | 建议 | 说明 |
|------|------|------|
| 12 | **etcd 扩展到 5 节点** | 5 节点可容忍 2 节点故障，消除 K5 灾难场景 |
| 13 | **etcd 定期快照备份** | 为灾难场景提供快速恢复能力 |
| 14 | **vLLM Worker 增加 K8s 原生感知** | 通过 Kubernetes API Watch Mooncake Master Endpoints 变化 |
| 15 | **支持 TCP 回退自动切换** | RDMA 不可用时自动降级到 TCP |
| 16 | **支持 XpYd 弹性伸缩** | 1P1D 故障时自动扩展新实例替代 |

---

## 18. 结论

### 18.1 三层矛盾

当前系统存在三层叠加的可靠性矛盾：

```
┌─────────────────────────────────────────────────────────────┐
│ 第一层: 1P1D 架构无冗余                                      │
│   → P 或 D 任一宕机，系统整体不可用                            │
├─────────────────────────────────────────────────────────────┤
│ 第二层: vLLM 代码无 HA 感知                                   │
│   → master_server_address 是单一字符串，无重试、无重连、无 Failover │
│   → K8s 层面的 3 副本高可用形同虚设                            │
├─────────────────────────────────────────────────────────────┤
│ 第三层: 无反亲和导致级联放大                                   │
│   → Mooncake + etcd 可能共处同一 Node                        │
│   → 单 Node 故障同时影响两个组件                              │
│   → 恢复时间从 2-5 秒放大到 10-30 秒，极端情况活锁             │
└─────────────────────────────────────────────────────────────┘
```

### 18.2 量化风险

- **43 个潜在故障场景**，其中 1 个灾难级、1 个极严重、21 个严重
- **~78% 概率**至少有一对 Mooncake + etcd Pod 共处同一 Node（无反亲和）
- **~33% 概率**双 Leader 共处同一 Node（随机 Leader 分布假设）
- 滚动更新**必然触发**双选举，中断窗口 30-90 秒
- K8s 层面的 3 副本 HA 被 vLLM 代码**完全浪费**

### 18.3 最高优先级行动

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   1. 立即配置 Pod 反亲和 (P0)                                        │
│      → 不改一行代码，只改 K8s YAML                                   │
│      → 消除 78% 的级联故障风险                                       │
│      → ROI 最高                                                     │
│                                                                     │
│   2. vLLM 代码支持多端点 + 重试重连 (P1)                              │
│      → 让 K8s 层面的 3 副本 HA 真正生效                               │
│      → 预计 1-2 周开发                                               │
│                                                                     │
│   3. 监控告警体系建设 (P2)                                           │
│      → 在故障发生时第一时间感知                                       │
│      → 避免故障扩散                                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```
