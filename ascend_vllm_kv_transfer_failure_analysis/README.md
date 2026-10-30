# Ascend NPU vLLM PD 分离集群副本故障分析报告（eki35 / 9r7q5）

> 分析日期：2026-10-29 ~ 2026-10-30
> 分析对象：Huawei Cloud ModelArts 弹性部署任务 `a35d55f779ad` 下的 `eki35` 与 `9r7q5` 两个推理任务
> 日志位置：`/mnt/sfs_turbo/logs/a35d55f779ad/{eki35,9r7q5}/`
> 集群形态：route_server（Go）+ vLLM（vllm-ascend 0.26.0 / vLLM v0.26.0）+ PD 分离 + mooncake/HIXL 跨节点 KV cache 传输，CANN 9.1.1 / PyTorch 2.10 / torch-npu，aarch64
> 模型：`GLM-5.3-w4a4c4-mxfp4-0902`（含 MTP 投机解码）

---

## 0. 背景与 TL;DR

平台侧现象：推理集群的两个任务副本（`eki35`、`9r7q5`）先后出现"容器副本启动失败"，最终被回收，由新任务 `492rq` 重建接管。

**核心结论：两个副本都不是"没启动起来"。**

- `9r7q5`：00:35 启动、00:39 就绪、稳定服务 9.5 小时后，于 **10:13:29** 被 NPU 驱动层错误（`rtMemcpy driver error:internal error`）打死 EngineCore；平台拉起的重启（10:14:46）尚在初始化阶段就被整体回收（10:16:29）。控制台看到的"启动失败"= 最后这次未能完成的重启。
- `eki35`：09:46 启动、09:50 就绪、服务约 25 分钟后，于 **10:13:31** 发生 NPU aicpu kernel 挂死（`HixlBatchGet` stream sync timeout）导致 KV 传输瘫痪、prefill 实例失联；route_server 重启后始终无实例注册，10:17:12 被整体拆除。
- 两个任务的致命故障**精确到秒级同步（仅差 2.3 秒）**、故障模式同源——这是**一场集群级的 NPU/互联传输层故障事件**打穿了两个任务，而非孤立的单副本问题。

---

## 1. 集群与日志结构速览

### 1.1 任务目录结构

```
/mnt/sfs_turbo/logs/a35d55f779ad/
├── 9r7q5/     4.1G   00:35 启动 → 10:16 死亡（本文第 3 节）
├── eki35/     656M   09:46 启动 → 10:17 死亡（本文第 2 节）
├── w2v7k/     480M   00:35 启动即死（未展开分析）
└── 492rq/     333M   10:16 启动，接管流量
```

每个任务含 5 个 role 目录：

| Role | 内容 | 角色 |
|------|------|------|
| `role-0-0-0` | `rs.log` | route_server（负载路由），监听 19800 / ops 19810 |
| `role-1-0-0` / `role-1-0-1` | `run.log` + `log/{run,debug}/{plog,device-*}/` | prefill 实例（8 卡/实例） |
| `role-2-0-0` / `role-2-0-1` | 同上 | decode 实例（8 卡/实例） |

日志分层：`run.log`（vLLM 主日志）、`plog-*`（CANN RUNTIME 层，20M 轮转）、`device-*/device-*`（HCCP/HCCL/TDT 设备侧日志）、`debug/` 下的 plog/device 日志**仅在错误发生时生成**（文件名时间戳 = 首个错误时间，是定位故障起点的关键线索）。

### 1.2 已识别的节点 IP

`192.168.0.44`（9r7q5 role-1-0-1）、`192.168.0.110`（eki35 role-1-0-1）、`192.168.0.164`（eki35 role-2-0-1）、`192.168.0.74`（eki35 route_server）；故障对端引擎：`192.168.0.119`、`192.168.0.192`（归属待定，疑似其他实例的 HIXL 传输引擎）。

---

## 2. 第一部分：eki35 副本分析

### 2.1 结论

**eki35 启动成功并服务约 25 分钟后，因 NPU 设备级挂死（aicpu kernel `HixlBatchGet` 无限等待）导致跨节点 KV cache 传输瘫痪、prefill 实例失联，route_server 重启后 0 实例注册、374 个请求全部 503，平台判定不可恢复，于 10:17 整体拆除。**

### 2.2 时间线（机器时区 +0800）

| 时间 | 事件 | 证据 |
|------|------|------|
| 09:46–09:50 | ✅ 正常启动：role-1（prefill）8 卡加载权重 35.2GB/卡，role-2（decode）43.4GB/卡 | `role-1-0-0/run.log:649` "Loading model weights took 35.2089 GB"；`:1006` "Application startup complete" |
| 09:51–10:13 | 正常服务，decode 侧持续跨节点 KV transfer | `role-2-0-1/run.log` 大量 "KV cache transfer ... took xxx ms" |
| **10:13:31** | 🔴 根因点：role-1-0-1 device_id=2 `Stream synchronize timeout, timeout=10000ms`，卡死任务 `KERNEL_AICPU`（`HixlBatchGet`，NOTIFY_WAIT 等不到通知），对端 `192.168.0.192:20034` | `role-1-0-1/log/debug/plog/plog-1658_20261030101331453.log` 首行起 |
| 10:13:32 | HIXL 传输失败被锁存（ErrorNo 103901），后续所有批量传输直接被拒（503900） | 同上 `LatchTransferFailure` / `rejected after prior transfer failure` |
| 10:13:56–10:14:22 | vLLM 层连锁：`Failed to load blocks` / `Failed to get N keys`，error_codes `-704/-800/-707`，波及多 rank | `role-1-0-1/run.log:2517+` |
| 10:14:41 | role-1-0-1 日志戛然而止（进程挂死/被杀，无任何 Python Traceback） | `run.log` 最后一行 |
| 10:14:57 | route_server（192.168.0.74）重启，流量立即涌入，但**无任何实例注册** | `role-0-0-0/rs.log` 第 1 行即 10:14:57 启动 banner |
| 10:14:57–10:15:09 | **374 条** `RouteServer.5003: no available prefill instance found` | `grep -c` 统计 |
| 10:16:01 | route_server 刚 bootstrap 完成、开始监听 19800 —— 日志到此为止 | `rs.log` 末行 "Start to listening on 192.168.0.74:19800" |
| 10:17:12 | 设备侧资源拆除（HCCP `RsUbCtxRmemUnimport` / HCCL channel destroy），副本消亡 | `role-1-0-1/log/run/device-*/device-*_202610301017*.log` |

### 2.3 根因链

```
NPU aicpu kernel (HixlBatchGet) 挂死（NOTIFY_WAIT 永久等待）
  └─ Stream synchronize timeout (device_id=2, 10s)
       └─ HIXL 跨节点 KV 传输失败并锁存 (103901 → 503900 全拒)
            └─ mooncake/vLLM: Failed to load blocks (-704/-800/-707)
                 └─ prefill 实例 (role-1-0-1) 挂死、日志中断
                      └─ route_server 重启后 0 实例注册 → 374×5003
                           └─ 平台判定不可恢复 → 10:17 拆除副本 → 492rq 接管
```

### 2.4 关键判据

1. **不是启动问题**：`role-1-0-0/run.log:1006` 记录 09:50:24 `Application startup complete`，权重加载 / KV cache 初始化 / warmup 全部成功。
2. **是设备挂死而非进程崩溃**：所有 run.log 均无 Python Traceback，日志"干净地突然停止"，且 10:17:12 出现完整的 HCCP/HCCL 资源 unimport/destroy 记录——典型的外部 kill + 设备侧清理。
3. **排除干扰项**：09:50-09:51 一批仅 7.6K 的 `device-0/device-*` 小日志为 aicpu_scheduler 系统守护进程日志（内容止于正常的 "Start waiting event"），属正常现象，与故障无关。

---

## 3. 第二部分：9r7q5 副本分析

### 3.1 结论

**9r7q5 于 00:35 启动、00:39 就绪、稳定服务 9.5 小时。10:13:29 worker 进程在 MTP 投机解码的 rejection sampler 中触发 `rtMemcpy` NPU 驱动内部错误，EngineCore 致命退出、进程自杀；平台于 10:14:46 拉起重启，第二代进程停在初始化早期（10:14:57），10:16:29 全部日志终止——重启未完成即被回收，控制台呈现为"副本启动失败"。**

### 3.2 时间线（机器时区 +0800）

| 时间 | 事件 | 证据 |
|------|------|------|
| 00:35–00:39 | ✅ 正常启动；主进程 PID 1162（role-1-0-0）/ 953（role-2-0-0）全程未换（02:59 的 plog 仅为 20M 轮转，非重启） | `run.log:1004-1006` "Started server process" / "Application startup complete" |
| 00:39–09:41 | 稳定服务（期间 03:30 / 04:11 / 04:19 / 04:54 / 05:38 / 05:42 / 10:08 有零星请求级报错） | `run.log` Traceback 分布 |
| **09:41:09** | 🟡 前置故障：role-1-0-1（192.168.0.44:20009）`HixlBatchGet` aicpu kernel 挂死 → stream sync timeout → 传输失败锁存，对端 `192.168.0.119:20071`；该 prefill 实例**带病运行 33 分钟** | `role-1-0-1/log/debug/plog/plog-1477_20261030094109393.log` 首行起 |
| **10:13:29.133** | 🔴 根因点：role-2-0-0 worker PID 2155（Device:0）在 rejection sampler 统计代码 `valid_mask.sum().item()` 执行 D2H 拷贝时底层报错 → `EngineDeadError` → uvicorn 干净退出（force kill 8 workers + SIGTERM） | `role-2-0-0/run.log:136180-136755` |
| 10:14:40 | 容器重启：PID 953 重新初始化 NPU runtime（新生成 846B 的 `plog-953_20261030101440981.log`） | plog 文件名时间戳 |
| 10:14:46 | 第二代进程开始 vLLM bootstrap | `run.log:136755` 启动 banner |
| 10:14:57 | 仍停在读取环境变量阶段（`PYTORCH_NPU_ALLOC_CONF` 等）——日志再无进展 | `run.log` 末 3 行 |
| 10:15:30 | role-2-0-1 报 `No available shared memory broadcast block found in 60 seconds`——多进程 EngineCore 握手失败/对端消失 | `role-2-0-1/run.log` 末 4 行 |
| 10:16:29 | 全部日志终止；10:16 起 `492rq` 接管 | 文件 mtime 分布 |

### 3.3 根因点原文（NPU 驱动错误）

```
ERROR vllm.v1.executor.multiproc_executor MainThread 2155
  File ".../vllm_ascend/sample/rejection_sampler.py", line 309, in _log_rejection_sampler_exit
    num_accepted = int(valid_mask.sum().item())
RuntimeError: operator():../torch_npu/csrc/aten/common/LocalScalarDenseNpu.cpp:25
  NPU function error: CalcuOpUtil::AclrtMemcpyWithModeSwitch(...)

[ERROR] 2026-10-30-10:13:29 (PID:2155, Device:0, RankID:-1) ERR00100 PTA call acl api failed
[Error]: An internal error occurs in the Driver module.
[PID: 2155] 2026-10-30-10:13:29.133.787 Invalid_Argument(EL0003): The argument is invalid.
        rtMemcpy execution failed, reason=driver error:internal error
        [FUNC:FuncErrorReason][FILE:error_message_manage.cc][LINE:69]

→ vllm.v1.engine.core MainThread 1465: EngineCore encountered a fatal error.
→ vllm.v1.engine.exceptions.EngineDeadError
```

触发代码位于 MTP 投机采样（`speculative_config method='mtp'`，模型 `GLM-5.3-w4a4c4-mxfp4-0902`）的 rejection sampler 统计路径，但错误本质是 **rtMemcpy 驱动内部错误**——设备/驱动栈已处于坏状态，采样代码只是"最后一个碰它的人"。

### 3.4 重启为何失败

- 第二代进程 10:14:46 启动后仅推进到早期 env/配置阶段（10:14:57），随后再无日志——结合 role-2-0-1 的 `shm_broadcast` 60 秒等待失败，说明多进程 EngineCore 在启动握手上就卡死/失去对端；
- 10:16:29 平台放弃并整体回收（与 eki35 的 10:17:12 拆除同属一波集群重整，`492rq` 于 10:16 重建接管），重启未获得完成的机会。

---

## 4. 关联分析：同一场集群级故障

| 维度 | 9r7q5 | eki35 |
|---|---|---|
| 首发传输故障 | 09:41:09，role-1-0-1（.44），对端 .119 | 10:13:31，role-1-0-1（.110），对端 .192 |
| 致命错误时刻 | **10:13:29.133**（rtMemcpy driver error） | **10:13:31.421**（stream sync timeout） |
| 致命错误间隔 | — | **仅 2.3 秒** |
| 故障层 | NPU Driver（rtMemcpy internal error） | NPU aicpu kernel（HixlBatchGet NOTIFY_WAIT 挂死） |
| 结局 | 重启未完成，10:16:29 回收 | 重启未完成，10:17:12 拆除 |

两个任务、不同节点（.44/.74/.110/.164，对端 .119/.192）、不同 role（prefill/decode），在 **2.3 秒内先后死于同一类 NPU 底层故障**。合理解释是共享基础设施（HIXL/UB 传输网络、mooncake KV 集群或 NPU 驱动本身）在同一时刻发生故障并连锁打穿整个推理集群；单副本视角的"启动失败"只是这场集群故障的下游表现。

另注：`w2v7k`（00:35 启动、rs.log 仅 11K 且 mtime 停在 00:35）为启动即失败的独立案例，本次未展开，建议单独核查。

---

## 5. 建议与后续动作

1. **查 NPU 驱动/固件事件日志**：各节点 `dmesg` 与 `/var/log/ascend/`（plog、device panic 记录），重点排查 09:41 与 10:13 两个时刻；优先看 `192.168.0.119` 与 `192.168.0.192`（两次首发故障的对端引擎所在节点）。
2. **查 RoCE/UB 网络链路**：交换机侧确认 09:41、10:13 前后是否有 flap/重置；日志中已出现 `hdc send exceed 10us` 类 WARN，可作辅助信号。
3. **向 vllm-ascend / CANN 提工单**：附本报告证据——
   - `HixlBatchGet`（KERNEL_AICPU，NOTIFY_WAIT）无限等待、10s stream sync timeout 后无恢复；
   - `LatchTransferFailure` 之后传输面永久锁死、实例无自愈；
   - `rtMemcpy driver error:internal error`（EL0003 / ERR00100）。
4. **完善实例摘除策略**：route_server 的 hang 关键词仅配置了 `KV cache load failed`，但 9r7q5 的故障实例从 09:41 带病运行到 10:13 才被处置（33 分钟）。建议把 HIXL `Transfer failure latched` / `Stream synchronize timeout` 也纳入实例健康判定与摘除条件，避免故障实例拖垮整个集群。
5. **核查 492rq 与 w2v7k**：确认现任存活任务 492rq 是否暴露同样隐患；补查 w2v7k 启动即死的原因。

---

## 附录 A：本次分析使用的关键命令

```bash
# 列出任务全部文件与时间线（定位日志代际与崩溃转储）
find /mnt/sfs_turbo/logs/a35d55f779ad/<job> -type f | xargs ls -lah --time-style="+%m-%d %H:%M:%S" | sort -k2,3

# debug 目录文件名时间戳 = 首个错误时间（定位故障起点）
ls -la <role>/log/debug/plog/

# NPU 层错误链
grep -E "Stream synchronize timeout|LatchTransferFailure|rtMemcpy|EL0003" <role>/log/debug/plog/*.log | head

# vLLM 层致命错误
grep -nE "EngineDeadError|EngineCore encountered a fatal error|Application startup complete" <role>/run.log

# route_server 实例注册与请求失败
grep -cE "no available prefill instance found" role-0-0-0/rs.log
grep -iE "register" role-0-0-0/rs.log
```

## 附录 B：日志文件索引（关键证据定位）

| 证据 | 文件 |
|------|------|
| eki35 启动成功 | `eki35/role-1-0-0/run.log`（09:50:24 Application startup complete） |
| eki35 NPU 挂死起点 | `eki35/role-1-0-1/log/debug/plog/plog-1658_20261030101331453.log`（10:13:31 stream sync timeout） |
| eki35 KV 层连锁 | `eki35/role-1-0-1/run.log`（10:13:56-10:14:22 Failed to load blocks） |
| eki35 路由层 0 注册 + 374×5003 | `eki35/role-0-0-0/rs.log`（10:14:57 起） |
| eki35 设备拆除 | `eki35/role-1-0-1/log/run/device-*/device-*_202610301017*.log`（HCCP unimport/destroy） |
| 9r7q5 前置故障（09:41:09） | `9r7q5/role-1-0-1/log/debug/plog/plog-1477_20261030094109393.log` |
| 9r7q5 致命错误与自杀 | `9r7q5/role-2-0-0/run.log` 行 136180-136755（rtMemcpy → EngineDeadError → shutdown → 10:14:46 重启 banner） |
| 9r7q5 重启未完成 | `9r7q5/role-2-0-0/run.log` 末 3 行 + `role-2-0-1/run.log` 末 4 行（shm_broadcast 60s 超时） |
