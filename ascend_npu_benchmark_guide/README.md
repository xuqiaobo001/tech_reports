# 华为云 ModelArts 昇腾 NPU 性能测试方案

> **分析日期**：2026-05-20  
> **适用平台**：华为云 ModelArts（Lite Server / 专属资源池）  
> **芯片型号**：Ascend 910 / 910B / 910B3  
> **工具链版本**：CANN 8.x + MindCluster 7.1.RC1

---

## 一、ModelArts 昇腾实例规格

| 实例类型 | 芯片 | NPU 数量 | HBM/卡 | FP16 算力/卡 | FP8 | HBM 带宽(估) | 互联方式 |
|---------|------|---------|--------|-------------|-----|-------------|---------|
| **Snt9** | Ascend 910 | 8 | 32 GB HBM2 | ~256 TFLOPS | ❌ | ~1,200 GB/s | HCCS |
| **Snt9B** | Ascend 910B | 8 | 64 GB HBM2e | ~320 TFLOPS | ❌ | ~1,600 GB/s | HCCS (56 GB/s/NPU) |
| **Snt9B3** | Ascend 910B3 | 8 | 64 GB HBM2e | ~313 TFLOPS | ❌ | ~1,600 GB/s | HCCS |
| **Snt3P** | Ascend 310P | 可切分 | 8/16 GB | ~16 TFLOPS | ❌ | 较低 | PCIe |

> ⚠️ **关键事实：当前昇腾 910/910B/910B3 均不支持 FP8。** 华为提出的 HiFloat8 格式仍处于研究论文阶段（arXiv:2409.16626），尚未在量产芯片中实装。因此 **FP8 算力测试在当前硬件上无法执行**。

---

## 二、测试工具全景

| 测试指标 | NVIDIA 对应工具 | 昇腾对应工具 | 来源 |
|---------|---------------|-------------|------|
| 设备监控 | `nvidia-smi` | `npu-smi` | 系统自带 |
| FP16/FP32 算力 | 自定义 CUDA kernel | **`ascend-dmi -f`** | [官方文档](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0016.html) |
| HBM 带宽 | cuda-samples/bandwidthTest | **`ascend-dmi -b`** 或 ACL 自定义 | [官方文档](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0015.html) |
| P2P 带宽 | cuda-samples/p2pBandwidthLatencyTest | **HCCL Tests** | [Gitee](https://gitee.com/ascend/cann-hccl) |
| D2D/H2D 带宽 | cuda-samples/bandwidthTest | **`ascend-dmi -b`** + ACL 自定义 | [CANN 样例](https://gitee.com/Ascend/samples) |
| 算子级 Profiling | `ncu` / `nsight` | **`msprof`** | [官方文档](https://www.hiascend.com/document/detail/zh/canncommercial/601/devtools/auxiliarydevtool/atlasprofiling_16_0041.html) |

---

## 三、逐项测试方案

### 3.1 FP16 算力测试

**工具**：`ascend-dmi`（Ascend DMI 工具箱官方命令）

**原理**：通过构造矩阵乘 A(m,k) × B(k,n)，FP16 模式下 m=256, k=32, n=128，执行多次后根据运算量/耗时计算 TFLOPS。

#### 测试命令

```bash
# ===== 前提条件 =====
# 设备温度需稳定且低于 90°C，避免降频

# 1. 查看所有 NPU 设备
npu-smi info

# 2. 查看芯片逻辑 ID 映射关系
npu-smi info -m

# 3. 测试单卡 FP16 算力（Device 0，默认执行次数）
ascend-dmi -f -d 0

# 4. 测试单卡 FP16 算力（指定执行次数 80×10万，训练场景最大值）
ascend-dmi -f -d 0 --et 80

# 5. 测试单卡 BF16 算力（910B/910B3 支持）
ascend-dmi -f -t bf16 -d 0

# 6. 测试单卡 FP32 算力（910B/910B3 支持）
ascend-dmi -f -t fp32 -d 0

# 7. 测试单卡 INT8 算力
ascend-dmi -f -t int8 -d 0

# 8. 测试整机所有 NPU 的 FP16 总算力
ascend-dmi -f -q --all
```

#### 输出示例

```
------------------------------------------------------------------------
  Device      Execute Times     Duration(ms)    TFLOPS@FP16     Power(W)
------------------------------------------------------------------------
  all         360000000         1702            2509.719      206.625015
------------------------------------------------------------------------
```

#### 参数说明

| 参数 | 说明 | 取值 |
|------|------|------|
| `-f, --flops` | 算力测试 | 必填 |
| `-t, --type` | 算子运算类型 | fp16 / fp32 / hf32 / bf16 / int8（默认 fp16） |
| `--all` | 测试整机算力（所有 NPU 之和） | 不与 -d 同用 |
| `-et, --execute-times` | 矩阵乘法执行次数 | 训练场景：10-80（单位：十万）；推理场景：10-80（单位：百万） |
| `-d` | 指定 Device ID | 芯片逻辑 ID（通过 `npu-smi info -m` 查询） |

**参考**：[Ascend DMI 算力测试官方文档](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0016.html)

---

### 3.2 FP8 算力测试

**结论：当前昇腾 910B/910B3 硬件不支持 FP8，无法直接测试。**

| 芯片 | FP8 支持 | 说明 |
|------|---------|------|
| Ascend 910 | ❌ | 无 FP8 引擎 |
| Ascend 910B/B3 | ❌ | 无 FP8 引擎 |
| Ascend 910C（推测） | ⚠️ 可能 | 尚未官方确认 |
| Ascend 910D/950 | ⚠️ 预期 | 未来芯片可能支持 HiFloat8 |

**替代方案**：测试 **INT8 算力** 作为低精度计算能力的参考：

```bash
# INT8 算力测试（FP8 不可用时的最佳替代）
ascend-dmi -f -t int8 -d 0 --et 80
```

> 华为提出的 HiFloat8（HiF8）格式目前仅存在于研究论文（[arXiv:2409.16626](https://arxiv.org/html/2409.16626v1)），有开源模拟库（[GitHub: HiFloat8](https://github.com/global-computing-consortium/HiFloat8)），但无法在硬件上实际运行。

---

### 3.3 HBM 带宽测试

#### 方法 A：`ascend-dmi` 带宽测试

```bash
# 测试 Device 0 的所有方向带宽（H2D / D2H / D2D）
ascend-dmi -b -d 0

# 仅测试 H2D 方向
ascend-dmi -b -d 0 -s h2d

# 仅测试 D2H 方向
ascend-dmi -b -d 0 -s d2h

# 仅测试 D2D 方向（同一 NPU 内部拷贝）
ascend-dmi -b -d 0 -s d2d

# 查看带宽测试帮助
ascend-dmi -b -h
```

#### 方法 B：`npu-smi` 实时监控 HBM 利用率

```bash
# 实时监控所有 NPU 的 HBM 使用率和带宽利用率
npu-smi info watch

# 查看单卡板级信息（含 HBM 容量）
npu-smi info -t board -i 0

# 查看单卡使用率信息
npu-smi info -t usages -i 0
```

#### 方法 C：ACL 自定义带宽基准测试（Python / torch_npu）

```python
"""
HBM 带宽基准测试 — 使用 torch_npu
测试 NPU 内部 D2D（HBM 内部）拷贝带宽
"""
import torch
import torch_npu
import time

def test_hbm_bandwidth(device="npu:0", size_gb=1, iterations=100):
    """测试 D2D (HBM 内部) 拷贝带宽"""
    device = torch.device(device)
    size = size_gb * 1024 * 1024 * 1024 // 4  # float32 元素数
    src = torch.randn(size, dtype=torch.float32, device=device)
    dst = torch.empty_like(src)

    # Warmup
    for _ in range(10):
        dst.copy_(src)
    torch.npu.synchronize()

    # Benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        dst.copy_(src)
    torch.npu.synchronize()
    elapsed = time.perf_counter() - start

    total_bytes = size_gb * 1024**3 * 2 * iterations  # read + write
    bandwidth_gbps = total_bytes / elapsed / 1e9

    print(f"D2D Bandwidth: {bandwidth_gbps:.2f} GB/s")
    print(f"Data size: {size_gb} GB x {iterations} iterations")
    print(f"Total time: {elapsed:.3f} s")

test_hbm_bandwidth(device="npu:0", size_gb=1, iterations=100)
```

**参考**：[Ascend DMI 带宽测试文档](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0015.html)

---

### 3.4 P2P 带宽测试（NPU 间互联）

**工具**：HCCL Tests（华为集合通信库测试工具）

#### 安装与编译

```bash
# 1. 克隆 HCCL 测试仓库
git clone https://gitee.com/ascend/cann-hccl.git
cd cann-hccl

# 2. 设置 CANN 环境变量
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# 3. 编译
bash build.sh
```

#### 运行 P2P 带宽测试

```bash
# 单节点 8 卡 Send/Recv 测试（P2P 带宽）
mpirun -np 8 \
  ./bin/hccl_sendrecv_test \
  -b 8 \
  -e 256M \
  -f 2 \
  -g 1

# 参数说明：
# -b 8        : 起始消息大小 8 Bytes
# -e 256M     : 结束消息大小 256 MB
# -f 2        : 步长因子（每次 2 倍增长）
# -g 1        : 组数
```

#### 运行集合通信带宽测试

```bash
# AllReduce 带宽测试
mpirun -np 8 ./bin/hccl_allreduce_test -b 8 -e 256M -f 2 -g 1

# AllGather 带宽测试
mpirun -np 8 ./bin/hccl_allgather_test -b 8 -e 256M -f 2 -g 1

# ReduceScatter 带宽测试
mpirun -np 8 ./bin/hccl_reducescatter_test -b 8 -e 256M -f 2 -g 1

# Broadcast 带宽测试
mpirun -np 8 ./bin/hccl_broadcast_test -b 8 -e 256M -f 2 -g 1
```

#### 查询 HCCS 链路信息

```bash
# 查询指定 NPU 的 HCCS 链路带宽
npu-smi info -t hccs -i 0 -c 0

# 查询所有 NPU 设备列表
npu-smi info -l
```

#### 预期结果参考（Ascend 910B，8 卡 HCCS 互联）

| 测试类型 | 单 NPU→NPU 带宽 | 8 卡聚合带宽 |
|---------|----------------|------------|
| HCCS P2P（节点内） | ~56 GB/s | ~392 GB/s |
| RoCE（节点间，100Gbps/link） | ~12.5 GB/s/link | 取决于链路数 |

**参考**：
- [HCCL 官方仓库](https://gitee.com/ascend/cann-hccl)
- [PyTorch HCCL Tests](https://github.com/Algebraic-Programming/pytorch-hccl-tests)
- [CSDN: Ascend 通信带宽测试指南](https://blog.csdn.net/xyz3120/article/details/148772891)
- [npu-smi HCCS 链路带宽查询](https://support.huawei.com/enterprise/zh/doc/EDOC1100438699/8fe020e2)

---

### 3.5 D2D / H2D / D2H 带宽测试

#### 方法 A：`ascend-dmi` 带宽测试

```bash
# 测试 H2D、D2H、D2D 三个方向（默认）
ascend-dmi -b -d 0

# 仅测试 H2D 方向（Host → Device）
ascend-dmi -b -d 0 -s h2d

# 仅测试 D2H 方向（Device → Host）
ascend-dmi -b -d 0 -s d2h

# 仅测试 D2D 方向（Device → Device，同 NPU 内）
ascend-dmi -b -d 0 -s d2d
```

#### 方法 B：ACL API 自定义基准测试（Python / torch_npu）

```python
"""
D2D / H2D / D2H 带宽基准测试 — 使用 torch_npu
测试 Host-Device 和 Device-Device 传输带宽
"""
import torch
import torch_npu
import time

def test_transfer_bandwidth(sizes_mb=[1, 4, 16, 64, 256, 1024], device="npu:0"):
    """测试 H2D / D2H / D2D 带宽"""
    device = torch.device(device)

    print(f"{'Size(MB)':>10} | {'H2D(GB/s)':>10} | {'D2H(GB/s)':>10} | {'D2D(GB/s)':>10}")
    print("-" * 52)

    for size_mb in sizes_mb:
        num_elements = size_mb * 1024 * 1024 // 4  # float32
        host_tensor = torch.randn(num_elements, dtype=torch.float32)
        device_tensor = torch.randn(num_elements, dtype=torch.float32, device=device)
        dst_tensor = torch.empty_like(device_tensor)

        iterations = max(10, 1000 // size_mb)

        # --- H2D (Host to Device) ---
        torch.npu.synchronize()
        start = time.perf_counter()
        for _ in range(iterations):
            device_tensor.copy_(host_tensor)
        torch.npu.synchronize()
        h2d_bw = size_mb / ((time.perf_counter() - start) / iterations) / 1000

        # --- D2H (Device to Host) ---
        torch.npu.synchronize()
        start = time.perf_counter()
        for _ in range(iterations):
            host_tensor.copy_(device_tensor)
        torch.npu.synchronize()
        d2h_bw = size_mb / ((time.perf_counter() - start) / iterations) / 1000

        # --- D2D (Device to Device, 同一 NPU 内) ---
        torch.npu.synchronize()
        start = time.perf_counter()
        for _ in range(iterations):
            dst_tensor.copy_(device_tensor)
        torch.npu.synchronize()
        d2d_bw = size_mb / ((time.perf_counter() - start) / iterations) / 1000

        print(f"{size_mb:>10} | {h2d_bw:>10.2f} | {d2h_bw:>10.2f} | {d2d_bw:>10.2f}")

test_transfer_bandwidth()
```

#### 方法 C：跨 NPU 的 D2D 带宽测试

```python
"""
跨 NPU 的 D2D 带宽测试 — 通过 HCCS 互联
"""
import torch
import torch_npu
import time

def test_cross_npu_bandwidth(size_gb=1, iterations=50):
    """测试跨 NPU 的 Device-to-Device 带宽（通过 HCCS）"""
    src = torch.randn(size_gb * 1024 * 1024 * 1024 // 4,
                      dtype=torch.float32, device="npu:0")
    dst = torch.empty_like(src, device="npu:1")

    # Warmup
    for _ in range(5):
        dst.copy_(src)
    torch.npu.synchronize()

    start = time.perf_counter()
    for _ in range(iterations):
        dst.copy_(src)
    torch.npu.synchronize()
    elapsed = time.perf_counter() - start

    bw = size_gb * 2 * iterations / elapsed  # read+write
    print(f"Cross-NPU D2D (NPU0 -> NPU1): {bw:.2f} GB/s")

test_cross_npu_bandwidth()
```

#### 预期结果参考

| 传输方向 | 瓶颈 | Ascend 910B 预期带宽 |
|---------|------|---------------------|
| H2D (Host→Device) | PCIe Gen4 x16 | ~25 GB/s（理论 32 GB/s） |
| D2H (Device→Host) | PCIe Gen4 x16 | ~25 GB/s（理论 32 GB/s） |
| D2D (同一 NPU 内) | HBM 带宽 | ~1,200-1,600 GB/s |
| D2D (跨 NPU，节点内) | HCCS | ~56 GB/s |

---

## 四、一键测试脚本

```bash
#!/bin/bash
# ascend_npu_benchmark.sh — 昇腾 NPU 性能基准测试一键脚本
# 适用：华为云 ModelArts Lite Server（Ascend 910B/910B3）
# 前提：已安装 CANN 工具包和 ascend-dmi
# 用法：bash ascend_npu_benchmark.sh

echo "========================================="
echo "  昇腾 NPU 性能基准测试"
echo "========================================="

# 0. 设备信息
echo ""
echo "[0] 设备信息"
npu-smi info

# 1. FP16 算力
echo ""
echo "[1] FP16 算力测试（整卡）"
ascend-dmi -f -d 0 --et 80

# 2. BF16 算力
echo ""
echo "[2] BF16 算力测试（整卡）"
ascend-dmi -f -t bf16 -d 0 --et 80

# 3. FP32 算力
echo ""
echo "[3] FP32 算力测试（整卡）"
ascend-dmi -f -t fp32 -d 0 --et 80

# 4. INT8 算力（FP8 不可用时的低精度替代）
echo ""
echo "[4] INT8 算力测试（整卡）"
ascend-dmi -f -t int8 -d 0 --et 80

# 5. 整机 FP16 算力
echo ""
echo "[5] 整机 FP16 算力（所有 NPU）"
ascend-dmi -f -q --all

# 6. 带宽测试
echo ""
echo "[6] 带宽测试（H2D/D2H/D2D）"
ascend-dmi -b -d 0

# 7. 功耗测试
echo ""
echo "[7] 功耗测试"
ascend-dmi -p -d 0

echo ""
echo "========================================="
echo "  测试完成"
echo "  FP8 测试：当前硬件不支持，已跳过"
echo "  P2P 测试：需单独运行 HCCL Tests"
echo "========================================="
```

---

## 五、FP8 缺失的影响分析

当前昇腾 910B/910B3 不支持 FP8，这对大模型训练和推理有以下影响：

| 影响维度 | 具体影响 |
|---------|---------|
| **训练吞吐量** | 无法使用 FP8 混合精度训练，FP16 训练吞吐量约为 FP8 的一半 |
| **显存占用** | FP16 权重占用是 FP8 的两倍，限制了可训练的模型规模 |
| **推理性能** | INT8 量化推理可用，但需要额外的校准步骤 |
| **与 NVIDIA 对比** | H100/H800 支持 FP8 E4M3/E5M2，在 LLM 推理场景有约 2x 吞吐优势 |
| **未来预期** | 华为 HiFloat8 论文已发布，预计下一代芯片（910D/950）将支持 |

---

## 六、关键参考文档

| 文档 | URL |
|------|-----|
| Ascend DMI 算力测试 | https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0016.html |
| Ascend DMI 带宽测试 | https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/toolbox/toolboxug/toolboxug_0015.html |
| HCCL 官方仓库 | https://gitee.com/ascend/cann-hccl |
| npu-smi 命令介绍 | https://support.huawei.com/enterprise/en/doc/EDOC1100079295/7a356c41 |
| npu-smi HCCS 链路带宽查询 | https://support.huawei.com/enterprise/zh/doc/EDOC1100438699/8fe020e2 |
| msprof Profiling | https://www.hiascend.com/document/detail/zh/canncommercial/601/devtools/auxiliarydevtool/atlasprofiling_16_0041.html |
| ModelArts Lite Server NPU 配置 | https://support.huaweicloud.com/intl/en-us/usermanual-server-modelarts/usermanual-server-0011.html |
| ModelArts 超节点压测 | https://support.huaweicloud.com/usermanual-server-modelarts/usermanual-server-0036.html |
| ACL 样例代码 | https://gitee.com/Ascend/samples |
| HiFloat8 论文 | https://arxiv.org/html/2409.16626v1 |
| 华为昇腾 NPU 性能评测论文 | http://cjc.ict.ac.cn/online/onlinepaper/lwz-202286133321.pdf |
| ModelArts 模型性能测试最佳实践 | https://support.huaweicloud.com/bestpractice-modelarts/modelarts_10_2011.html |
| Ascend 基本性能验收测试 | https://support.huawei.com/enterprise/zh/doc/EDOC1100497035/a3e625f2 |
| ModelArts 实例规格 API | https://support.huaweicloud.com/intl/en-us/api-modelarts/ListDevServerResourceflavors.html |
| CSDN: Ascend 通信带宽测试指南 | https://blog.csdn.net/xyz3120/article/details/148772891 |
| Georgetown CSET: 华为 AI 芯片分析 | https://cset.georgetown.edu/publication/pushing-the-limits-huaweis-ai-chip-tests-u-s-export-controls/ |
