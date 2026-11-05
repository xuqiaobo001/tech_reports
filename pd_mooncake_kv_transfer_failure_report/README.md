# vLLM PD 分离部署 Mooncake KV 传输失败分析报告

- **故障日期**：2026-11-05 11:54 – 12:31
- **环境**：vLLM 0.30.0 + vllm-ascend（git `v0.19.1rc1-3349-gfa06924da`），Ascend NPU ×8/节点
- **模型**：GLM-5.3-Flash-w8a8-mxfp8（mamba + MLA + sparse indexer 混合架构 + MTP 投机解码）
- **拓扑**：P 节点 `192.168.1.248`（kv_producer, TP=8+EP, port 9081）→ D 节点 `192.168.2.155`（kv_consumer, TP=8+EP, port 9900），MooncakeConnectorV1 P2P 传输，router 代理 8081
- **业务流量**：~256K token 长上下文请求，共享公共前缀，并发峰值 15+

---

## 1. 故障现象

D 节点（d0.log）从 **12:05:58** 起持续报 KV 拉取失败，直至日志结束（12:31:38）：

- `E19999: Server memory verification failed, memory is not registered`（HiXL 传输引擎，4992 次）
- `RuntimeError: Mooncake transfer failed, ret: -1`（8 个 TP worker 全部报错，2496 次）
- `Failing N request(s) due to KV load failure (failure_policy=fail)`（121 次，约 130 个请求被废弃）

失败请求 100% 为 ~262K token（256K 级）长请求，其中 108 个恰好 262155 token，另有 512K、1M 级批量失败。

**P 节点（p0.log）全程零 ERROR**——它不知道自己出了问题。

## 2. 错误链条

```
① P 侧内存校验拒绝读取（表象根因）
   E19999  Server memory verification failed, memory is not registered
   server_addr:0x4024..., buf_len:9216 bytes   [hixl_mem_store.cc:274]
        ↓
② transfer_executor_base.cpp:597  Transfer failed (重连+重试后仍失败)
        ↓
③ mooncake_connector.py:1066  RuntimeError: Mooncake transfer failed, ret: -1
        ↓
④ scheduler.py:3231  Failing request(s) due to KV load failure → 用户请求直接失败
```

## 3. 排除的假设

| 假设 | 排除依据 |
|---|---|
| P 节点重启/进程崩溃 | P 侧 session 端口 12:02→12:24 恒定不变；p0.log 无任何 ERROR |
| 网络链路故障 | 12:24:38 仍有一次 255~331ms 的全量成功传输 |
| D 节点 NPU/HCCL 故障 | d0.log 无任何 NPU/HCCL/watchdog/crash 错误，decode 与 HTTP 层始终正常 |
| 两侧 KV 池配置不一致 | P/D `num_gpu_blocks_override=4287` 完全一致 |
| 传输引擎注销了内存 | `register_kv_caches` 是**整池一次性注册**、运行期从不注销；若注销则 12:24 的成功无法解释 |
| 延迟释放超时强杀 | 强杀路径会打 ERROR 级 "Force freed expired request"，p0.log 中零出现 |

**结论**：`not registered` 只能是**计算出的远端地址落在 P 侧注册区之外**——不是内存被释放，而是块号配对错位。

## 4. 时间线（两侧日志对照）

| 时间 | P 节点 | D 节点 |
|---|---|---|
| 11:54:20 | 首个 256K 请求 prefill 完成，前缀缓存开始建立（hit rate 0%） | 拉取成功（241~308ms 全量） |
| 12:02–12:05 | 7 个请求正常，prefix hit rate 爬到 56.6% | 7 次拉取全部成功 |
| **12:05:58** | 一切"正常"（Delaying free 照常、HTTP 200） | **首次失败**：memory is not registered |
| 12:06–12:31 | prefix hit rate 升至 86.6%，KV usage 仅 0~6.7% | 几乎全部拉取失败（~130 请求） |
| 12:24:38 | 同形态请求 | 唯一一次成功（恰好全量拉取） |

**关键相关性**：D 侧本地 prefix cache hit rate 从 30% 爬到 ~39-60% 的时刻，与故障起点精确重合；并发爬坡（Running 3→6→15）与错误量爆发同步。

## 5. 根因分析

### 5.1 机制

MooncakeConnectorV1 P2P 模式下，D 拉取 KV 时必须回答"**从 P 拉哪些远端块 → 写进我哪些本地块**"。配对信息由三个来自不同世界的碎片拼成：

| 信息 | 来源 | 特点 |
|---|---|---|
| `remote_block_ids` | **P 的分配器**（含 P 前缀命中时复用的旧块号） | 只反映 P 的缓存视图 |
| `local_block_ids` | **D 的分配器**（D 本地命中越多列表越短） | 只反映 D 的缓存视图 |
| 对齐依据 `num_computed_tokens` | **D 本地命中数**，用于切 P 的远端块列表 | 隐含假设：两侧命中边界完全对齐 |

部署版源码关键位置（`vllm_ascend/distributed/kv_transfer/kv_p2p/mooncake_connector.py`）：

```python
# :2956  用 D 本地命中数切 P 的远端块列表
remote_start_idx = meta.num_computed_tokens // remote_kernel_token_size
kernel_remote = kernel_remote[remote_start_idx:]
num_kernel_blocks = min(len(kernel_remote), len(kernel_local))
```

```python
# :1988  D 取本地"未命中新分配块"，命中越多列表越短
local_block_ids = blocks.get_unhashed_block_ids_all_groups()
```

D 关闭 prefix cache 时 `num_computed_tokens ≡ 0`，配对退化为"P 列表[0:] ↔ D 列表[0:]"，唯一确定可靠。**D 开启 prefix cache 后，"D 命中的前缀"与"P 给出的块列表头部"必须在 token 粒度完全一致**——任何一侧驱逐、边界不对齐、混合模型各 KV 组（attention 1152 对齐块 / mamba state / indexer 顶缓存）命中边界不同步，都会切错位置。切错后远端地址 = P 基址 + 错误块号 × stride，**仍然对齐但落在注册区外**（源码 `:2556` 注释自述："wrong but still aligned address"），引擎无法区分错位与越界 → E19999。

### 5.2 为什么两侧不一致是常态

P 和 D 是**两张独立演进、互相不可见的 LRU 缓存**：

1. 同一前缀在 P 侧还在缓存、在 D 侧可能已被驱逐（或相反）——两侧池都是 4287 块，但驱逐历史完全不同；
2. 请求到达有先后，时间差导致两侧命中集合漂移；
3. GLM-5.3-Flash 是 mamba+MLA+indexer **混合架构**，不同 KV 组的命中边界天然不同步，错位窗口更大；
4. MTP 投机解码引入 draft 层组，padding/对齐更复杂（`:2556` 注释："target and draft MLA groups can use different padding"）。

### 5.3 为什么 12:05:58 才爆发

首个共享前缀写入 D 本地缓存是 11:54:20；D 侧命中率随流量积累爬升，**第一次出现"D 命中边界与 P 列表头部不一致"的请求**恰在 12:05:58——这是缓存热身的自然结果，不是某个外部事件触发。12:24:38 的唯一成功是该请求引用的块恰好全部落在 D 未命中的区段（全量拉取路径）。

## 6. 为什么官方文档 PD 分离要 `--no-enable-prefix-caching`

单机时 prefix 命中 = 直接复用本机块号，一个分配器一张表，块号即真理。PD 分离时，上述三方配对依赖"两侧独立缓存视图对齐"这一脆弱假设，当前 vllm-ascend 的 P2P 连接器用该假设换取实现简单，关闭 D 侧 prefix cache 是该假设下的安全运行区（官方 mooncake 多节点文档 8 处命令全部 `--no-enable-prefix-caching`）。

### 收益量化（本案例实测数据）

| 操作 | 耗时 |
|---|---|
| 256K KV 全量 P→D 传输 | **92~331 ms** |
| 256K prefill 计算（P 侧 11796 tok/s） | **~22 s** |

- **P 侧 prefix cache**：命中一次省 ~22s 计算 → 收益大头，**保留**（P 侧只管写，不参与配对，非故障源）
- **D 侧 prefix cache**：命中一次只省 ~0.2s 传输 → 却承担整条配对链路错位风险 → **关闭**
- PD 分离下跨请求前缀复用本来就在通过 **P 侧缓存 + KV 传输**实现（本案例 `External prefix cache hit rate: 100%`），D 本地 cache 关闭后，业务感知的前缀加速绝大部分保留

### 强 D 侧复用需求的正统方案

多轮对话/路由粘性场景若必须 D 侧复用，应使用**共享 KV 存储层**（store 模式）：P 算好的前缀 KV 放入共享池，P/D 均从池读写——单一缓存视图，不存在对齐问题。仓库已有雏形：`vllm_ascend/distributed/kv_transfer/kv_pool/kv_offload/`（ascend_store + mooncake backend memcache 模式）。

## 7. 参数优化清单

### 7.1 针对本次故障（最高优先级）

| # | 修改 | 依据 |
|---|---|---|
| 1 | **D 侧**：`--enable-prefix-caching` → `--no-enable-prefix-caching` | 切断故障路径：`num_computed_tokens ≡ 0`，配对退化为全量对齐 |
| 2 | 两侧补 `export VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=480` | 官方 GLM5.2 PD 教程要求（`mooncake_connector.py:230` 消费） |
| 3 | 两侧补 `export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=3000` | 启动日志明确警告：HCCL op 超时 1836s，RPC 超时必须大于它 |

### 7.2 修正与清理

| # | 项目 | 结论 |
|---|---|---|
| 4 | `use_ascend_direct` | **不要加**——此部署版本代码中不存在该 key，静默忽略 |
| 5 | D 的 `kv_buffer_device`/`kv_buffer_size` | P2P 连接器未使用，可删（无害） |
| 6 | `kv_load_failure_policy` | **保持 `fail`**——`platform.py:1300`：hybrid 模型用 `recompute` 直接 AssertionError 拒绝启动 |
| 7 | D：`"recompute_scheduler_enable": "True"` | 迁移为 `"scheduler_config": {"recompute_scheduler_enable": true}`（消除 deprecation 警告） |

### 7.3 性能与容量

| # | 项目 | 理由 |
|---|---|---|
| 8 | D：`--max-num-batched-tokens 120` → 240 | 启动警告 suboptimal；120 = 20×(1+5) 零余量 |
| 9 | D：`--max-num-seqs 20` → 18（或两侧同步提高 `num-gpu-blocks-override`） | 4287 块 ÷ 228 块/请求 ≈ 18.8 < 20；若调 override，**P/D 必须同值**（块地址映射前提） |
| 10 | 环境变量对齐教程（可选） | `VLLM_WORKER_MULTIPROC_METHOD=spawn`、`TASK_QUEUE_ENABLE=1`、`OMP_PROC_BIND=false`、`OMP_NUM_THREADS=20` |
| 11 | 生产环境去掉 `--profiler-config` | 两侧常驻 with_stack profiler 有开销 |

### 7.4 run_d.sh 关键 diff 示例

```bash
export VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=480
export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=3000

  --max-num-seqs 18 \
  --max-num-batched-tokens 240 \
  --no-enable-prefix-caching \
  --kv-transfer-config '{
    "kv_connector": "MooncakeConnectorV1",
    "kv_role": "kv_consumer",
    "kv_port": "30800",
    "engine_id": "1",
    "kv_load_failure_policy": "fail",
    "kv_connector_extra_config": {
      "prefill": {"dp_size": 1, "tp_size": 8},
      "decode": {"dp_size": 1, "tp_size": 8}
    }
  }'
```

run_p.sh 同样加两个环境变量；`--enable-prefix-caching` 第一轮保留观察，若复现再关闭。

## 8. 验证方案

1. 重启顺序：P → D → proxy
2. 重放压测：**共享 256K 前缀、并发 15+、持续 30 分钟以上**（原故障在 D 前缀缓存热身后约 11 分钟才触发，短测测不出）
3. 通过标准：`grep -c "memory is not registered" d0.log` 为 0，且 `KV cache transfer ... took` 日志持续出现
4. 若关 D 侧前缀后仍复现 → 两侧都关；再不行则属 `register_kv_caches` 与握手元数据在 hybrid+MTP padding 下的对齐 bug，携 `mooncake_connector.py:2928/2556` 代码位置向 vllm-ascend 提 issue

## 9. 总结

本次故障是 **hybrid 架构（mamba+MLA+indexer）长上下文流量下，D 侧本地 prefix caching 与 P2P KV 传输块配对逻辑的兼容性缺陷**：D 用本地命中数切 P 的远端块列表，两侧独立 LRU 缓存视图不一致时块号配对错位，远端地址落到注册区外被 HiXL 拒绝，`kv_load_failure_policy=fail` 将其放大为整请求失败。短期以"D 侧关闭 prefix caching + P 侧保留"安全运行，长期依赖 store 型共享 KV 缓存或连接器补上两侧缓存视图的显式协商。
