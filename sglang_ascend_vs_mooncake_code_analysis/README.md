# SGLang Ascend vs Mooncake 传输后端代码差异与优化分析

> 分析日期：2026-04-21
> 分析对象：SGLang 源码 sgl-project/sglang
> 分析范围：`disaggregation/ascend/` 与 `disaggregation/mooncake/` 的代码差异
> 部署场景：GLM-4.7-Flash-30B-A3B 7P1D PD 分离部署

---

## 一、总体规模对比

| 维度 | Mooncake | Ascend |
|------|----------|--------|
| **总代码行数** | ~2000 行 | ~250 行（conn.py 144 + transfer_engine.py 104） |
| **类数量** | 4 个（全部有实际实现） | 4 个（3 个空壳继承 + 1 个覆盖） |
| **传输引擎** | `MooncakeTransferEngine` | `AscendTransferEngine`（继承 Mooncake） |
| **底层库** | `mooncake.TransferEngine` | `memfabric_hybrid.TransferEngine` |
| **源码文件** | `mooncake/conn.py` + 基类 | `ascend/conn.py` + `ascend/transfer_engine.py` |

---

## 二、类继承关系图

```
CommonKVManager
  └── MooncakeKVManager
        └── AscendKVManager         ← 覆盖 3 个方法

CommonKVSender
  └── MooncakeKVSender
        └── AscendKVSender          ← 空壳 pass

CommonKVReceiver
  └── MooncakeKVReceiver
        └── AscendKVReceiver        ← 空壳 pass

CommonKVBootstrapServer
  └── MooncakeKVBootstrapServer
        └── AscendKVBootstrapServer ← 空壳 pass

MooncakeTransferEngine
  └── AscendTransferEngine          ← 全新实现（4 个方法）
```

---

## 三、Ascend 新增代码详解

### 3.1 空壳继承类（3 个，无新增逻辑）

**文件**：`ascend/conn.py:134-143`

```python
class AscendKVSender(MooncakeKVSender):
    pass

class AscendKVReceiver(MooncakeKVReceiver):
    pass

class AscendKVBootstrapServer(MooncakeKVBootstrapServer):
    pass
```

这 3 个类完全复用 Mooncake 的实现，**0 行新增代码**。它们的存在仅是为了让工厂模式通过后端名称（`ascend`）创建对应的类实例。

---

### 3.2 有实际覆盖的类：`AscendKVManager`（3 个方法覆盖）

#### 覆盖 1：`init_engine()` — 替换传输引擎

**文件**：`ascend/conn.py:22-29`

```python
class AscendKVManager(MooncakeKVManager):
    def init_engine(self):
        local_ip = get_local_ip_auto()
        self.engine = AscendTransferEngine(
            hostname=local_ip,
            npu_id=self.kv_args.gpu_id,
            disaggregation_mode=self.disaggregation_mode,
        )
```

| 对比项 | Mooncake | Ascend |
|--------|----------|--------|
| 引擎实例化 | `get_mooncake_transfer_engine()` | `AscendTransferEngine(hostname, npu_id, mode)` |
| 底层库 | `mooncake.TransferEngine` | `memfabric_hybrid.TransferEngine` |
| 设备标识 | GPU ID | NPU ID（华为昇腾 NPU） |
| 服务发现 | Mooncake 内置机制 | `ASCEND_MF_STORE_URL` 环境变量 |

---

#### 覆盖 2：`register_buffer_to_engine()` — 简化注册逻辑

**文件**：`ascend/conn.py:31-41`

```python
def register_buffer_to_engine(self):
    self.engine.batch_register(self.kv_args.kv_data_ptrs, self.kv_args.kv_data_lens)
    self.engine.batch_register(self.kv_args.aux_data_ptrs, self.kv_args.aux_data_lens)
    if self.kv_args.state_data_ptrs and self.kv_args.state_data_lens:
        self.engine.batch_register(self.kv_args.state_data_ptrs, self.kv_args.state_data_lens)
```

**与 Mooncake 的差异**：

Mooncake 版本（`mooncake/conn.py:260-277`）对每个缓冲区类型都有 `if ptrs and lens` 的空值检查：

```python
def register_buffer_to_engine(self):
    if self.kv_args.kv_data_ptrs and self.kv_args.kv_data_lens:      # 有空值检查
        self.engine.batch_register(...)
    if self.kv_args.aux_data_ptrs and self.kv_args.aux_data_lens:     # 有空值检查
        self.engine.batch_register(...)
    if self.kv_args.state_data_ptrs and self.kv_args.state_data_lens: # 有空值检查
        self.engine.batch_register(...)
```

Ascend 移除了 `kv_data` 和 `aux_data` 的空值检查（因为这两个缓冲区必然存在），仅保留 `state_data` 的条件检查。逻辑等价，代码更简洁。

---

#### 覆盖 3：`send_kvcache()` — 核心传输方法

**文件**：`ascend/conn.py:43-131`

这是 Ascend 相对 Mooncake **唯一有实质性逻辑差异**的方法。详细分析见第五节。

---

### 3.3 新增的传输引擎类：`AscendTransferEngine`

**文件**：`ascend/transfer_engine.py`（104 行，完全新增）

这是完全新增的代码，继承 `MooncakeTransferEngine` 并重写了 4 个方法：

| 方法 | 功能 |
|------|------|
| `__init__()` | 初始化 `memfabric_hybrid.TransferEngine`，设置角色和 NPU ID |
| `initialize()` | 根据协议类型初始化引擎，包含 hccl 预初始化逻辑 |
| `batch_register()` | 批量注册内存区域，增加异常处理 |
| `_get_transfer_protocol()` | 从环境变量读取传输协议配置 |

---

## 四、`AscendTransferEngine` 核心机制

### 4.1 双协议支持

**文件**：`ascend/transfer_engine.py:62-75`

```python
transfer_protocol = self._get_transfer_protocol()
if transfer_protocol is None or transfer_protocol == "sdma":
    trans_op_type = TransferEngine.TransDataOpType.SDMA       # 默认协议
else:
    trans_op_type = TransferEngine.TransDataOpType.DEVICE_RDMA # 高性能协议
```

| 协议 | 控制方式 | 特点 |
|------|---------|------|
| **SDMA**（默认） | `ASCEND_MF_TRANSFER_PROTOCOL=sdma` 或未设置 | 稳定通用，通过 CPU 中转 |
| **DEVICE_RDMA** | `ASCEND_MF_TRANSFER_PROTOCOL=device_rdma` | 直接设备端 RDMA，绕过 CPU，更低延迟 |

**环境变量配置**：

```bash
# 选择传输协议
export ASCEND_MF_TRANSFER_PROTOCOL=device_rdma  # 或 sdma

# 集中式存储地址（服务发现）
export ASCEND_MF_STORE_URL=your_store_url
```

---

### 4.2 hccl 预初始化（华为昇腾特有）

**文件**：`ascend/transfer_engine.py:68-75`

```python
# 仅在 DEVICE_RDMA 模式下执行
tmp_tensor = torch.zeros(1, device="npu")
output_tensor_list = [torch.empty_like(tmp_tensor) for _ in range(get_world_size())]
torch.distributed.all_gather(
    output_tensor_list, tmp_tensor, group=get_world_group().device_group
)
```

**原因**：昇腾 NPU 上 hccl（华为集合通信库）和 RDMA 初始化存在资源竞争。通过提前执行一次 `all_gather` 触发 hccl 初始化，确保后续 RDMA 引擎初始化不受干扰。

**这是华为硬件特有的 workaround，Mooncake 不需要。**

---

### 4.3 集中式存储发现

```python
self.store_url = os.getenv("ASCEND_MF_STORE_URL")
```

Mooncake 使用 Mooncake 自带的服务发现机制，Ascend 改为通过环境变量指定集中式存储地址，适配昇腾集群的部署模式。

---

## 五、`send_kvcache()` 方法深度对比

### 5.1 调用链对比

```
Mooncake 调用链（两层委托）:
  MooncakeKVManager.send_kvcache()
    → MooncakeKVManager._send_kvcache_generic()

Ascend 调用链（直接内联）:
  AscendKVManager.send_kvcache()
    → 直接实现（逻辑等价于 _send_kvcache_generic）
```

Ascend 将 Mooncake 中 `send_kvcache()` → `_send_kvcache_generic()` 的两层调用**内联展开**为单层，减少了一层函数调用开销，逻辑完全等价。

### 5.2 共享优化：`group_concurrent_contiguous()` 合并连续索引

**两个后端都使用**（来自 `disaggregation/common/utils.py`）：

```
输入: prefill_indices = [3, 4, 5, 8, 9, 12]
输出: blocks = [[3,4,5], [8,9], [12]]
```

将 N 次小 RDMA 传输合并为 M 次（M << N）大块传输，减少 RDMA 操作次数。

### 5.3 双路径传输策略（两个后端完全一致）

```python
if self.enable_custom_mem_pool:
    # 路径A：逐层并行传输
    futures = [executor.submit(process_layer, ...) for layer in layers_params]
    for future in concurrent.futures.as_completed(futures):
        status = future.result()
        if status != 0:
            for f in futures:
                f.cancel()
            return status
else:
    # 路径B：批量合并传输
    return process_layers(layers_params)
```

| 路径 | 触发条件 | 传输方式 | 适用场景 |
|------|---------|---------|---------|
| **路径 A**（逐层并行） | `enable_custom_mem_pool=True` | 每层独立线程并行 | 自定义内存池，需要隔离每层传输 |
| **路径 B**（批量合并） | `enable_custom_mem_pool=False` | 所有层合并为一次调用 | 默认场景，更高效 |

### 5.4 Mooncake 独有但 Ascend 不覆盖的传输方法

| 方法 | 行号 | 功能 | Ascend 是否使用 |
|------|------|------|----------------|
| `send_kvcache_staged()` | mooncake/conn.py:473 | Staging Buffer 模式传输 | 继承使用 |
| `send_kvcache_hisparse()` | mooncake/conn.py:705 | HiSparse 按 token 粒度传输 | 继承使用 |
| `send_kvcache_slice()` | mooncake/conn.py:748 | M-to-N TP 切片传输 | 继承使用 |

---

## 六、优化点总结

### 6.1 两个后端共享的优化

| 优化点 | 说明 | 效果 |
|--------|------|------|
| `group_concurrent_contiguous()` | 合并连续 KV 索引为大块传输 | 减少 RDMA 操作次数 |
| 双路径策略 | custom_mem_pool 逐层并行 vs batch 批量合并 | 适应不同内存管理模式 |
| 线程池并行 | 使用 `ThreadPoolExecutor` 并行传输 KV 层 | 充分利用网络带宽 |

### 6.2 Ascend 独有的优化/适配

| 优化点 | 文件 | 说明 |
|--------|------|------|
| **SDMA / DEVICE_RDMA 协议选择** | transfer_engine.py:62-66 | DEVICE_RDMA 绕过 CPU，更低延迟 |
| **hccl 预初始化** | transfer_engine.py:68-75 | 规避昇腾 NPU 上 hccl 与 RDMA 冲突 |
| **集中式存储发现** | transfer_engine.py:43 | 适配昇腾集群部署模式 |
| **简化 buffer 注册** | conn.py:31-41 | 移除不必要的空值检查 |

---

## 七、关键源码文件索引

| 文件 | 行数 | 功能 |
|------|------|------|
| `ascend/conn.py` | 144 | Ascend KV Manager、Sender、Receiver、BootstrapServer |
| `ascend/transfer_engine.py` | 104 | Ascend 传输引擎（协议选择、hccl 预初始化） |
| `mooncake/conn.py` | ~2000 | Mooncake 完整实现（Ascend 的基类） |
| `common/utils.py` | — | `group_concurrent_contiguous()` 共享工具函数 |
| `base/conn.py` | — | KVArgs、KVPoll 等基础定义 |

---

## 八、结论

Ascend 后端的本质是 **"换引擎 + 硬件适配"**，而不是算法层面的优化：

1. **底层传输引擎替换**：从 `mooncake.TransferEngine` 替换为 `memfabric_hybrid.TransferEngine`，适配华为昇腾 NPU
2. **硬件特有适配**：SDMA/DEVICE_RDMA 协议选择、hccl 预初始化规避冲突、集中式存储发现
3. **上层接口一致**：`send_kvcache()` 的双路径策略（逐层并行 vs 批量合并）与 Mooncake 完全一致
4. **最大程度复用**：3 个类为空壳继承，3 个 Mooncake 独有传输方法直接继承使用

Ascend 的代码量（~250 行）仅为 Mooncake（~2000 行）的 12.5%，通过继承机制最大化复用了 Mooncake 的成熟逻辑，仅针对昇腾硬件做最小化适配。
