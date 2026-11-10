# Mooncake 在 Ascend 上部署时与华为通信库 HIXL 的交互关系分析报告

> 分析日期：2026-11-09
> 代码库：Mooncake（`/root/mooncake/Mooncake`）
> 分析范围：mooncake-transfer-engine 中 Ascend Direct Transport 与 CANN HIXL/ADXL 库的交互

---

## 一、总体架构：Mooncake 做控制面，HIXL 做数据面

Mooncake 在昇腾（Ascend）NPU 上部署时**不自行实现 RDMA/HCCS 传输**，而是把 NPU 间、Host↔Device 的数据面完全委托给 CANN 工具包中的 HIXL 通信库，自己保留元数据管理、拓扑维护、批量调度、超时重试等控制面逻辑。

代码库中存在**两代 Ascend 直连传输实现**：

```
┌─────────────────────────────────────────────────────────────┐
│  Mooncake Transfer Engine                                    │
│  (控制面: 元数据/segment 管理/批量调度/超时重试)                  │
├────────────────────────────┬────────────────────────────────┤
│ TENT 新版 (tent/)           │ 旧版 (src/transport/            │
│ AscendDirectTransport       │  ascend_transport/) TE v1      │
│        ↓ 直接封装            │        ↓ 本地声明 + 弱符号       │
│ HixlEngine (Vendor 抽象层)   │ adxl_compat.h                  │
│        ↓                    │ (adxl::AdxlEngine, ABI 与       │
│  hixl::Hixl (libhixl)       │  hixl MemDesc 兼容, 144 字节)    │
│                             │  实现由 CANN libllm_datadist     │
│                             │  等库运行时提供                  │
├────────────────────────────┴────────────────────────────────┤
│  CANN: HIXL / ADXL → HCCS / RDMA / Fabric Mem (A3)           │
└─────────────────────────────────────────────────────────────┘
```

### 1.1 TENT 新版实现

- **全仓库唯一直接调用 hixl API 的文件**：`mooncake-transfer-engine/tent/src/transport/ascend/hixl_engine.cpp`
- 头文件 `<hixl/hixl.h>`，链接 CANN 的 `cann_hixl` CMake 目标（`tent/src/transport/ascend/CMakeLists.txt:6`）
- 通过 `HixlEngine::Vendor` 抽象接口封装 `hixl::Hixl` 对象（`RealHixlVendor`），支持测试注入 fake 实现，无需真实 CANN 环境

### 1.2 旧版实现（TE v1）

- 不 include `<hixl/hixl.h>`，而是通过 `include/transport/ascend_transport/ascend_direct_transport/adxl_compat.h` 本地声明 `adxl::AdxlEngine` 接口
- 该头文件由华为版权持有，注释明确说明结构体布局 "stays 144 bytes and matches the hixl MemDesc ABI"（ADXL 与 HIXL 共享 ABI，ADXL 是 HIXL 的前身/同构接口）
- `TransferAsync`/`GetTransferStatus`/`MallocMem`/`ExportToShareableHandle`/`FreeMem`/`GetCapability` 声明为**弱符号**（`__attribute__((weak))`），在旧版 CANN 上运行时优雅降级；实现由链接的 CANN 库（`llm_datadist`、`graph` 等）提供

---

## 二、TENT 新版调用的 HIXL 函数清单

`hixl_engine.cpp` 中 `RealHixlVendor`（持有 `hixl::Hixl hixl_` 成员）调用的全部 hixl API：

| HIXL 函数 | 调用位置 | 用途 |
|---|---|---|
| `Initialize(name, options)` | hixl_engine.cpp:39 | 每个 NPU 一个 HIXL 实例，名字为 `host_ip:port` |
| `Finalize()` | hixl_engine.cpp:43 | 释放引擎资源 |
| `RegisterMem(MemDesc, MEM_HOST/MEM_DEVICE, handle&)` | hixl_engine.cpp:51 | 注册本地内存（host 内存或 device 内存） |
| `DeregisterMem(handle)` | hixl_engine.cpp:58 | 注销内存 |
| `TransferAsync(remote, READ/WRITE, op_descs, args, req&)` | hixl_engine.cpp:75 | 异步批量传输（不显式 Connect，依赖 AutoConnect） |
| `GetTransferStatus(req, status&)` | hixl_engine.cpp:84 | 轮询传输状态 WAITING/COMPLETED/TIMEOUT/FAILED |
| `Disconnect(remote, timeout)` | hixl_engine.cpp:109 | 超时后/销毁前断链（容忍 `103902 NOT_CONNECTED`） |
| `Connect(remote, timeout)` | hixl_engine.cpp:113 | 仅保留在 Vendor 接口供测试断言"从不调用"，生产路径不调用 |

### 2.1 使用的类型与常量

| 类别 | 内容 |
|---|---|
| 类型 | `hixl::AscendString`、`hixl::MemDesc`、`hixl::MemHandle`、`hixl::TransferOpDesc`、`hixl::TransferReq`、`hixl::TransferArgs` |
| 枚举 | `hixl::MEM_HOST/MEM_DEVICE`、`hixl::READ/WRITE`、`hixl::TransferStatus::{WAITING, COMPLETED, TIMEOUT, FAILED}` |
| 状态码 | 仅识别 `0`（`hixl::SUCCESS`）与 `103902`（`kHixlNotConnected`，对应 `NOT_CONNECTED`） |

### 2.2 传给 HIXL 的初始化选项

由 `resource_config.cpp:325` 的 `BuildHixlInitOptions` 构建：

| 选项键 | 来源 | 说明 |
|---|---|---|
| `AutoConnect=1` | 固定 | 启用 HIXL 自动连接，Mooncake 不再显式 Connect |
| `LocalCommRes` | 环境变量 `ASCEND_LOCAL_COMM_RES` 或默认值 | 本地通信资源配置 |
| `RdmaTrafficClass` | `ASCEND_RDMA_TC` → `HCCL_RDMA_TC` | RDMA 流量类 |
| `RdmaServiceLevel` | `ASCEND_RDMA_SL` → `HCCL_RDMA_SL` | RDMA 服务等级 |
| `EnableUseFabricMem=1` | `ASCEND_ENABLE_USE_FABRIC_MEM` / 配置 `fabric_mem` | A3 fabric 内存直读远端 Host 内存 |
| `GlobalResourceConfig` | `ASCEND_GLOBAL_RESOURCE_CONFIG` 或配置项 | HIXL 全局资源 JSON；Mooncake 会向其中注入 `comm_resource_config.listen_port`（RoCE 模式，`WithHixlListenPort`）和 `comm_resource_config.nic_topo_path`（自定义 NIC 拓扑） |

---

## 三、上层调用流程（AscendDirectTransport 生命周期）

### 3.1 安装与发布

```
install()  (ascend_direct_transport.cpp:77)
  ├─ 每个 NPU: FindHixlListenPort() 分配端口
  │    (base_port + 物理设备 ID×100 区间内随机找空闲 TCP 端口)
  ├─ 创建 HixlEngine → initialize(name="[ip]:port", ACL context, device_id)
  │    (:166-177)
  └─ publishLocalEngines() (:211-232)
       把 hixl 引擎名写入元数据 segment 的
       device_attrs["hixl_name"] / device_attrs["hixl_names"]
```

### 3.2 内存注册

`addMemoryBuffer()`（:784）→ 用 `aclrtPointerGetAttributes` 判断 host/device 内存 → `HixlEngine::registerMem` → hixl `RegisterMem`。

### 3.3 传输路径

```
submitTransferTasks()
  ├─ resolveRequest(): 从元数据缓存取对端 SegmentDesc,
  │    ResolveRemoteHixlName() 按目标地址所在 buffer 的
  │    transport_attrs / npu:<id> location 选出对端 hixl 实例名
  ├─ 按 (本地引擎, 远端 hixl 名, 读写方向) 分组
  ├─ 远端 == 本机 hixl 名 → LocalCopyEngine (纯 ACL aclrtMemcpyAsync,
  │    不经过 HIXL)
  └─ 否则 → HixlEngine::transferAsync (:426)
       → hixl::Hixl::TransferAsync(remote, READ/WRITE, descs, ..., req)
```

关键点：同 batch 内同路由的多个请求合并为一个 hixl `TransferOpDesc` 列表，一次 `TransferAsync` 提交，减少调用次数。

### 3.4 状态轮询与超时处理

- `getTransferStatus()`（:676）轮询 → `HixlEngine::getTransferStatus` → hixl `GetTransferStatus`，状态经 `FromHixlState` 映射回 mooncake 的 `TransferStatusEnum`
- **超时处理**：本地拷贝流失败重建（`failLocalCopyStream`）；远端路由超时 → `failEntireRoute(..., disconnect=true)` → `HixlEngine::disconnectOnTimeout` → hixl `Disconnect`，整条路由所有 in-flight 请求一起作废
- 传输失败（`TransferAsync` 返回非 0）时依赖 AutoConnect 已在 HIXL 内部拆除该路由，Mooncake 不重复断链

### 3.5 注销与销毁

- `removeMemoryBuffer()` → 遍历所有引擎 `deregisterMem`（:830）
- `HixlEngine::finalize()`（hixl_engine.cpp:212）：**先 Disconnect 全部远端会话，再逐个 DeregisterMem，最后 Finalize** —— 代码注释明确这是 HIXL 的硬性要求："HIXL requires those sessions to be gone before DeregisterMem / Finalize"

### 3.6 ACL 上下文管理

每次 hixl 调用（initialize/registerMem/deregisterMem/transferAsync/getTransferStatus/disconnect）都用 `ContextGuard` 包裹：保存当前 ACL 上下文 → `aclrtSetCurrentContext` 切到本引擎设备上下文 → 调 hixl → 析构时恢复。因为 **HIXL 在调用者的当前 ACL 上下文中工作**，多卡进程必须保证每次调用落在正确设备上。

---

## 四、旧版（TE v1）经 adxl 兼容层调用的函数

旧版用 `adxl::AdxlEngine`（弱符号，运行时由 CANN 库解析），调用面比 TENT 更宽：

| 类别 | 函数 | 调用位置 |
|---|---|---|
| 传输 | `TransferSync` | sync_transfer_executor.cpp:75（同步模式默认） |
| 传输 | `TransferAsync` + `GetTransferStatus` | async_transfer_executor.cpp:112/290（`ASCEND_USE_ASYNC_TRANSFER=1` 启用） |
| 连接 | `Initialize` | transfer_executor_base.cpp:341 |
| 连接 | **显式 `Connect`** | transfer_executor_base.cpp:380（旧版维护显式连接池） |
| 连接 | `Disconnect` | transfer_executor_base.cpp:424/450/475/498 |
| 内存 | `RegisterMem` / `DeregisterMem` | transfer_executor_base.cpp:590 / 527、643 |
| 静态方法 | `MallocMem` / `FreeMem` | ascend_allocator.cpp:369/673（Host 内存池，带直连 ACL VMM 回退） |
| 静态方法 | `ExportToShareableHandle` + `MallocMem`/`FreeMem` | ascend_shared_segment.cpp:184/191/274（A3 fabric 共享内存段） |
| 能力探测 | `GetCapability`（AUTO_CONNECT / CLIENT_SERVER_COMM） | transfer_executor_base.cpp:198-203 |

### 4.1 与 TENT 的行为差异

| 维度 | TENT 新版 | 旧版 TE v1 |
|---|---|---|
| 库接口 | `hixl::Hixl`（直接链接 `cann_hixl`） | `adxl::AdxlEngine`（本地头文件 + 弱符号） |
| 连接方式 | 仅 AutoConnect，从不 Connect | 显式 Connect + 连接池管理 |
| 同步传输 | 不支持（只走 TransferAsync） | `TransferSync`（默认模式） |
| 能力探测 | 无 | `GetCapability` 探测特性 |
| 内存分配 | 不用 hixl 分配（用 ACL VMM/共享段） | `AdxlEngine::MallocMem/FreeMem` |

---

## 五、非 API 层面的配合（配置镜像）

除直接调用外，还有若干代码**镜像 HIXL 内部行为/配置约定**而不调用其 API：

| 位置 | 内容 |
|---|---|
| ascend_shared_segment.cpp:210 | 复刻 HIXL `VirtualMemoryManager::ReserveMemAddress` 的地址预留策略：HIXL fabric mem 默认占用 [40TB, 72TB) 虚拟地址区间，Mooncake 在 A3 上改为 78TB 起预留以避让 |
| ascend_shared_segment.cpp:56 | 复刻 HIXL A3 (V3) SoC 集合判定，用于 NoUCMemory 预留 |
| ascend_allocator.cpp:113 | 复刻 hixl `GetSocTypeByName()` 的 SoC 名称分类逻辑 |
| utils.cpp（旧版）:230 | 代 HIXL 写 `comm_resource_config.nic_topo_path` 配置（`MC_CUSTOM_TOPO_JSON` 注入） |
| resource_config.cpp（TENT）:188 | `InjectNicTopoPathFromCustomTopoTent`：显式配置的 `nic_topo_path` 优先于自定义拓扑路径；优先嵌套形式，与 HIXL 解析器保持单一表示 |

---

## 六、关键交互约定总结

1. **命名与发现机制**：hixl 引擎名 = `host_ip:port`，每卡一个实例；Mooncake 通过元数据服务发布本端全部 hixl 名（`hixl_names` JSON 数组），接收端用 `ResolveRemoteHixlName` 按目标虚拟地址所在 buffer 的 `npu:<id>` location / transport_attrs 选出对端正确的 hixl 实例，保证数据落在对的卡上。

2. **连接模型**：TENT 完全依赖 `AutoConnect=1`，生产代码路径**从不调用 Connect**（仅测试通过 Vendor 接口断言这一点）；超时路径上 Mooncake 主动 Disconnect 清理 HIXL 内部会话，`103902 NOT_CONNECTED` 视为成功。

3. **注册顺序约束**：传输前双方内存必须已在各自 HIXL 实例注册（host 内存用 `MEM_HOST`，device 内存用 `MEM_DEVICE`）；拆除时必须 **先 Disconnect 所有远端会话，才能 DeregisterMem / Finalize**。

4. **批量合并**：同（本地引擎， 远端， 读写方向）分组的任务合并为一次 `TransferAsync` 提交，用 hixl `TransferReq` 句柄组跟踪整组状态；组内任一失败/超时会级联作废整条路由。

5. **错误传播模型**：hixl 仅返回 uint32 状态码 + `aclGetRecentErrMsg()` 取错误消息；Mooncake 将 WAITING/COMPLETED/TIMEOUT/FAILED 一一映射为自身 `TransferStatusEnum`，TIMEOUT 触发断链重连，FAILED 触发整路由失败。

6. **可测试性设计**：`HixlEngine::SetVendorFactory` 允许注入 fake Vendor，`tent/tests/ascend_direct_transport_test.cpp` 无需真实 CANN 环境即可验证传输语义（含"AutoConnect-only 从不调用 Connect"的断言）。

7. **环境依赖**：需 CANN 9.0+（AutoConnect / fabric mem 需 HDK 26.0+）；`/etc/hccn.conf` 必须存在；HCCS 协议下 device 内存注册要求 2MB 对齐；`ASCEND_TRANSFER_TIMEOUT`（内层）需严格小于外层 `MC_TRANSFER_TIMEOUT` 的一半，因为内层超时后传输会重试。

---

## 附录：涉及的核心文件索引

| 文件 | 角色 |
|---|---|
| `tent/include/tent/transport/ascend/hixl_engine.h` | HixlEngine 与 Vendor 抽象接口定义 |
| `tent/src/transport/ascend/hixl_engine.cpp` | **唯一直接调用 hixl API 的文件**（RealHixlVendor） |
| `tent/src/transport/ascend/ascend_direct_transport.cpp` | TENT Ascend 直连传输主体（生命周期/批量/超时） |
| `tent/src/transport/ascend/resource_config.cpp` | HIXL 初始化选项构建、端口分配、远端名称解析 |
| `tent/src/transport/ascend/local_copy_engine.cpp` | 本机内拷贝（纯 ACL，不经 HIXL） |
| `include/transport/ascend_transport/ascend_direct_transport/adxl_compat.h` | 旧版 adxl 兼容层（ABI 对齐 hixl，弱符号） |
| `src/transport/ascend_transport/ascend_direct_transport/transfer_executor_base.cpp` | 旧版引擎生命周期（显式 Connect/Disconnect） |
| `src/transport/ascend_transport/ascend_direct_transport/{sync,async}_transfer_executor.cpp` | 旧版同步/异步传输执行器 |
| `src/shared_segment/ascend_shared_segment.cpp` | A3 fabric 共享内存段（AdxlEngine 静态方法） |
| `src/transport/ascend_transport/ascend_allocator.cpp` | Host 内存池（MallocMem/FreeMem + ACL VMM 回退） |
