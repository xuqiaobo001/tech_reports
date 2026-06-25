# vLLM 1P1D GLM5.1 昇腾部署超时与异常响应分析报告

> 分析对象：基于 `vllm-project/vllm`（已全量迁移至 V1 架构）源码，围绕 **1P1D、GLM5.1、昇腾双机、proxy 与 P 进程同容器** 部署场景，梳理各类请求超时点及其导致客户端 500 / 异常响应的成因。
>
> 分析日期：2026-06-25

---

## 〇、总体结论

**vLLM 引擎层本身几乎没有请求级超时。** 超时行为分散在四个层级里：

```
HTTP 前端 → disagg proxy → KV 传输 connector → 引擎内部
```

不同 connector 的超时语义差异极大，这正是排查「为什么 500 / 为什么卡死」时最容易踩坑的地方。

请求链路与跨网络脆弱点：

```
Client ──HTTP①──► Proxy(与P同容器) ──HTTP②(本地loopback)──► P进程(预填充)
                                │
                                ├──HTTP③(跨容器/跨机)──► D进程(解码)──► 流式回包
                                │
                          KV传输④(跨机, P──►D, 走 connector: Mooncake/NIXL/MoRIIO)
```

四个跨网络的脆弱点是：① 外部到 proxy、②③ proxy 到 P/D、④ P 到 D 的 KV 传输。其中 **④（两台昇腾机之间的实际数据搬运）是最容易出 500 / 卡死的地方**，因为 D 解码必须在 KV 到位后才能开始。

> **平台说明**：此 checkout 是上游 vLLM，无内建 Ascend 平台 / HCCL 代码，也无 GLM-5 模型注册（只有 GLM-4 系列和 `glm45`/`glm47` 解析器别名）。Ascend 平台支持来自外挂的 `vllm-ascend` 插件，GLM5.1 模型实现也在插件侧；Mooncake 的 `store/` 子路径明确标注 "Adapted from vllm-project/vllm-ascend"。PD 模式通过 `--kv-transfer-config '{"kv_connector":"...", "kv_role":"kv_producer"|"kv_consumer"|"kv_both", ...}'` 启用，角色用 **producer/consumer/both**。

---

## 一、逐层超时点分析

### Layer 1：Client ↔ Proxy（HTTP 前端）

**结论：几乎没有请求超时。** 前端只配了一个 keep-alive，**没有请求级超时、没有 body 读取超时、没有流式卡顿超时。**

| 超时项 | 位置 | 值 | 触发 | 客户端表现 |
|---|---|---|---|---|
| `timeout_keep_alive` | `entrypoints/openai/api_server.py:602,647`；`envs.py:100,1015` | **5s** | 空闲 keep-alive 连接 | 连接被关闭，需重连 |
| uvicorn `timeout`（请求整体）| 未设置 | **None / 无限** | — | 无上限 |
| `timeout_graceful_shutdown` | 未设置 | **None** | — | 无优雅退出截止 |
| `limit_concurrency` | 未设置 | **None(无限)** | — | 无 503 背压 |
| body 读取超时 | 未设置 | **无限** | 上传慢 | 永不超时 |
| `generate()` 截止时间 | `entrypoints/openai/chat_completion/serving.py:451,804` 等 | **无 `asyncio.wait_for`** | 引擎卡住 | **挂起**（无 token、无报错）|
| 流式迭代超时 | `chat_completion/api_router.py:74` StreamingResponse | **无** | SSE 停滞 | **静默挂起**，连接不断 |

关键代码证据 —— 生成循环就是个无截止的 `async for`（`vllm/v1/engine/async_llm.py:575-584`）：

```python
while not finished:
    out = q.get_nowait() or await q.get()   # 没有任何超时，引擎停摆就永远等
```

**客户端断连**：handler 阶段靠 `with_cancellation` 装饰器（`entrypoints/serve/utils/api_utils.py:52-94`）监听 `http.disconnect`；但**一旦返回了 StreamingResponse（200 已发出），它就停止监听**，断连检测交给 Starlette。流式阶段没有 vLLM 自己的 keepalive/ping。

### Layer 2：Proxy ↔ P / Proxy ↔ D（disagg proxy 的 HTTP 转发）

这一层取决于使用的 proxy（上游只有 example proxy，生产 proxy 通常在 vllm-ascend 侧或自研）。不同 proxy 的转发超时差别巨大：

| Proxy | 转发超时 | 失败处理 |
|---|---|---|
| `examples/disaggregated/disaggregated_serving/disagg_proxy_demo.py:34` | aiohttp `total=6*60*60`(6小时) | **502 + 把该 P/D 实例踢出轮询池**（`remove_instance_endpoint`），且无重试 |
| `disagg_epd_proxy.py:293` | aiohttp `total=100_000`(~27.8h) | 500/502 |
| `disagg_proxy_multiturn.py:343,357` | httpx `timeout=None`(**无限**) | `raise_for_status` → 500 / 不可达 D 时**TCP 层永久挂起** |
| `mooncake_connector_proxy.py:84,103` / lmcache proxy | httpx `timeout=None`(**无限**) | 启动期 `GET /health` 无限 1s 重试；就绪前返回 **503**；转发异常 → 500 |

**两个坑**：(1) **proxy 全部没有重试**，一次失败要么传播 5xx，要么把实例踢掉（demo proxy 踢掉 D 后，后续所有请求都会因为「无可用 D」而失败）；(2) httpx `timeout=None` 的 proxy，遇到 D 进程卡死/网络分区时，请求会在 TCP 层**永久挂起**，既不 500 也不回包。

### Layer 3：P → D 的 KV 传输（跨机，最脆弱的一环）⭐

这是两台昇腾机之间真正的数据搬运，**D 解码前必须等 KV 到位**。不同 connector 的超时行为天差地别：

**① Mooncake（昇腾侧 store 适配自此）**
- `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT = 480s`（`envs.py:215,1574`）
- D 等 P 标记 ready：`mooncake_connector.py:1060-1085` 用 `asyncio.wait(timeout=480)`，超时后**只打 warning「Timeout waiting for P side ready」，把 req 标记为 err_reqs**，但 `process_pulling_result` 并不把它加入 `finished_recving` —— **请求继续卡在 `WAITING_FOR_REMOTE_KVS`，没有干净报错 → 静默挂起**，只能靠 proxy 的 HTTP 超时或客户端超时兜底。
- P 侧 send 过期：`mooncake_connector.py:1469-1492`，480s 内 D 没来拉就强制释放 producer 块（防内存泄漏）。
- D 的 ZMQ `RCVTIMEO = 540s`（`mooncake_connector.py:1567`，故意比 480 大 60s）。
- Bootstrap server 注册：`mooncake_connector.py:923-940` **无限 1s 重试**，bootstrap 挂了 → 启动永久卡死。

**② NIXL**
- `kv_lease_duration = 30s`（默认，`nixl/scheduler.py:70-76`，heartbeat 间隔 = 30/6 = 5s）。
- P 把块 pin 住 30s 等 D 来读，**超时就释放**（`nixl/worker.py:1858-1875`）。→ 如果 D 因为队列满/解码慢来不及在 30s 内读，P 已释放 → D 后续读不到 → load error。
- D 的 metadata 握手 `zmq.RCVTIMEO = 5s`（`nixl/worker.py:576`）→ P 不可达时**快速失败 → load error**。
- `kv_recompute_threshold = 64`（`nixl/scheduler.py:141`）：远程 KV 小于 64 token 时 D 直接本地重算，不等待传输。
- bidirectional 模式下 D 侧 pin 用 `decoder_kv_blocks_ttl = 480s`。

**③ MoRIIO（RDMA，唯一一个超时会 raise 的）**
- `transfer_timeout = 30s`（`moriio_common.py:320`），RDMA 完成事件 30s 没到 → **`raise TransferError`**（`moriio_engine.py:526-566`）→ 默认 fail 策略下 `FINISHED_ERROR`。
- `defer_timeout = 60s`：丢失 `finished_sending` 通知时强制释放 producer 块（`moriio_connector.py:655-678`）。
- `VLLM_MORI_READ_ABORT_REQUEST_TIMEOUT = 3600s`：D 一直不来拉的兜底释放。
- 代码里明确写了一个已知坑（`moriio_engine.py:443`）：RDMA 通知模式下 `ibv_post_send` 失败会**让请求在 `WAITING_FOR_REMOTE_KVS` 无限挂起**，所以才默认关掉 notification。

### Layer 4：引擎内部（P 进程、D 进程各自的 vLLM engine）

| 超时项 | 位置 | 值 | 触发 | 客户端表现 |
|---|---|---|---|---|
| **请求级截止时间** | `request.py` 无 deadline 字段 | **无** | KV 等不到 | 卡在 `WAITING_FOR_REMOTE_KVS` **无限等** |
| `kv_load_failure_policy` | `config/kv_transfer.py:69`；`scheduler.py:1650-1652` | **`"fail"`(默认)** | connector 报 load error | `FINISHED_ERROR` → `GenerationError` → **HTTP 500** |
| 启动就绪握手 | `core_client.py:612-630`；`envs.py:27` | `VLLM_ENGINE_READY_TIMEOUT_S=600s` | 权重加载/建图慢 | 启动失败 `TimeoutError` |
| 前端握手（引擎侧）| `v1/engine/core.py:1098-1105` | `HANDSHAKE_TIMEOUT_MINS=5` | DP/分布式 init 死锁 | `RuntimeError` |
| 引擎死亡信号 | `core.py:1435-1447`；`core_client.py:451` | 5s flush / 4s linger | OOM/建图/权重/worker 崩溃 | `EngineDeadError` → **500 + 进程退出** |
| liveness 监控 | `core_client.py:682-709` | 守护线程 | 引擎进程死亡 | 所有请求 `EngineDeadError` |
| DP 排空超时 | `async_llm.py:978-992` | `drain_timeout=300s` | 弹性扩缩 | `TimeoutError` |
| `shutdown_timeout` | `config/vllm.py:384`；`launcher.py:113` | **0(直接 abort)** | SIGTERM | 在途请求立即被杀 |
| watchdog 轮询 | `launcher.py:162` | 5.0s | 引擎 errored | `should_exit=True` → 连接被重置 |

**错误 → HTTP 映射**（`entrypoints/serve/utils/error_response.py:16-72`、`server_utils.py:327-367`）：

- `EngineGenerateError`（可恢复，单请求）→ 500
- `EngineDeadError`（不可恢复，全局）→ 500，且若 `VLLM_KEEP_ALIVE_ON_ENGINE_DEATH=0`(默认) 则 `server.should_exit=True` 让进程退出
- `finish_reason=="error"` → `GenerationError` → 500（`engine/serving.py:192-199`）
- 流式请求**已发 200 之后**，异常无法改状态码，只能写成 SSE 错误 chunk；只能靠 watchdog 关进程
- **关键坑**：`VLLM_KEEP_ALIVE_ON_ENGINE_DEATH=1` 时，引擎死了进程却不退，在途请求**无限挂起**

---

## 二、具体超时场景导致客户端 500 / 异常

按「触发原因 → 根因超时点 → 客户端看到的现象」列举：

**场景 1：KV 传输 P→D 慢/失败（跨机网络抖动、昇腾侧 RDMA/NIC 拥塞）—— 最高频**
- NIXL：P 的 30s lease 先到 → 块被释放 → D 读不到 → load error → `fail` 策略 → `FINISHED_ERROR` → **客户端 500（GenerationError）**。
- MoRIIO：30s `transfer_timeout` → `raise TransferError` → 同样 **500**。
- Mooncake：480s 超时后 D **只 warning，请求继续卡在 WAITING_FOR_REMOTE_KVS** → **客户端无响应/挂起**（直到 proxy 的 6h 超时或客户端自己超时）。**这是最隐蔽的「既不 500 也不回包」卡死。**

**场景 2：D 进程不可达 / 卡死（解码队列堆积、D 进程 hang）**
- proxy→D 的 HTTP 调用：若 proxy 用 httpx `timeout=None` → **客户端挂起**；若用 demo proxy 的 aiohttp 6h → 极晚才 502，且**把 D 踢出池子**，之后所有请求因「无可用 D」而 5xx。
- 同时 P 侧的 KV 块因 lease/abort 超时被释放，资源泄漏被防住，但请求已废。

**场景 3：D 解码太慢、读 KV 不及时（负载高、长输出）**
- NIXL 下 D 在 30s 内没来读 P 的 KV → 块释放 → **500**。这是 PD 调参里最典型的「lease 太短」问题，建议把 `kv_lease_duration` 调大或减小 D 并发。

**场景 4：P 预填充失败（昇腾 OOM、建图失败、权重加载失败）**
- P 的 EngineCore 崩溃 → `_send_engine_dead()` → 客户端 **500(EngineDeadError)** + P 进程退出。
- 若 `VLLM_KEEP_ALIVE_ON_ENGINE_DEATH=1`，则 P 进程不退但请求**无限挂起**。

**场景 5：Proxy 踢实例后无 D 可用（用 demo proxy 时）**
- 一次 D 失败 → `remove_instance_endpoint` → 池里没 D 了 → 后续每条请求 **502/500**。需要重启 proxy 才能恢复，不会自愈。

**场景 6：Mooncake bootstrap server 挂了**
- `mooncake_connector.py:923-940` **无限 1s 重试** → P/D 都启动不了、注册不了 → 启动期**永久卡死**，服务起不来（表现为 readiness 一直不通，客户端 503/连接拒绝）。

**场景 7：客户端自己先超时（长 prefill + 慢 KV 传输导致整体 RTT 超过客户端 timeout）**
- 客户端断连 → proxy/前端 `CancelledError` → 引擎里 abort 请求（`async_llm.py:591-596`）。客户端看到的是**自己的 timeout**，而 P 侧 prefill + KV 传输的计算其实已经白做了。

**场景 8：流式生成中途引擎停滞（非崩溃）**
- 没有任何流式超时 → SSE **静默挂起**，客户端既收不到 token 也收不到结束，只能靠客户端 timeout。

**场景 9：启动慢/重部署**
- 权重加载或昇腾建图超过 600s → `VLLM_ENGINE_READY_TIMEOUT_S` → 启动直接 `TimeoutError`，容器起不来。

---

## 三、配置参数梳理（影响超时的全部参数）

配置参数分布在 **三个地方**：`--kv-transfer-config` 的 JSON 字段、环境变量（`envs.py`）、以及 `kv_connector_extra_config` 里的 connector 私有项。

### 表 1：`--kv-transfer-config` 顶层 JSON 字段（`vllm/config/kv_transfer.py`）

```bash
--kv-transfer-config '{"kv_connector":"PyNixlConnector","kv_role":"kv_producer","kv_rank":0,...}'
```

| JSON 字段 | 默认值 | 含义 | 对超时/500 的影响 |
|---|---|---|---|
| `kv_connector` | `None` | connector 类名（`PyNixlConnector`/`MooncakeConnector`/`MoRIIOConnector`/`LMCacheMPConnector` 等）| **决定 KV 传输走哪条超时路径** |
| `kv_role` | `None` | `kv_producer`(P) / `kv_consumer`(D) / `kv_both` | 必填，否则启动报错 |
| `kv_rank` | `None` | 0=prefill，1=decode（注释明确"目前只支持 1P1D"）| 角色定位 |
| `kv_parallel_size` | `1` | KV 并行实例数 | 1P1D 固定 1 |
| `kv_ip` | `127.0.0.1` | connector 建连 IP | **跨机必须改成对端可达 IP** |
| `kv_port` | `14579` | connector 端口 | 端口冲突/不通→连不上→超时/500 |
| `kv_buffer_device` | 平台默认 | 缓冲设备（cuda/cpu/xpu）| 缓冲不足→传输失败 |
| `kv_buffer_size` | `1e9`(~1GB) | 缓冲字节数 | 太小→大模型 KV 放不下→传输失败 |
| `kv_connector_extra_config` | `{}` | connector 私有项（见 **表 3**）| **NIXL/MoRIIO 的超时旋钮全在这里** |
| `kv_connector_module_path` | `None` | 外挂 connector 模块路径（V1 专用）| 用自研 connector 时指定 |
| **`kv_load_failure_policy`** ⭐ | **`"fail"`** | KV 读失败策略：`fail`=立即报错；`recompute`=本地重算 | **直接影响 500 率**。默认 `fail` → 任何 KV 读失败直接 `FINISHED_ERROR`→500；改 `recompute` 可兜底降 500 率 |
| `enable_permute_local_kv` | `False` | HND↔NHD KV 转换实验开关 | — |
| `engine_id` | 随机 uuid | KV 传输引擎标识 | — |

> **最关键的两个旋钮**：`kv_role`（必填）和 `kv_load_failure_policy`（默认 `fail` 是 500 高发的根因之一）。

### 表 2：环境变量（`vllm/envs.py`）

| 环境变量 | 默认 | 含义 | 影响的超时 / 场景 | 推荐调整 |
|---|---|---|---|---|
| **`VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT`** ⭐ | `480`(s) | Mooncake D 等 P-ready / P 释放 producer 块 / D ZMQ recv(=480+60=540) 的统一截止 | **场景 1（Mooncake 静默卡死）**：超时后 D 只 warning，请求仍卡在 `WAITING_FOR_REMOTE_KVS`→客户端无响应 | 按 RTT 调；超时本质不会让它变 500，需配合 proxy 转发超时 |
| `VLLM_MOONCAKE_BOOTSTRAP_PORT` | `8998` | Mooncake bootstrap 端口 | bootstrap 不通→注册无限重试→**场景 6 启动卡死** | 确保两机端口互通 |
| `VLLM_MOONCAKE_DISK_STAGING_USABLE_RATIO` | `0.9` | 磁盘 staging 可用比例 | 不足→传输失败 | — |
| `VLLM_NIXL_SIDE_CHANNEL_HOST` | `localhost` | NIXL 旁路通道 host | 跨机需改 | 确认可达 |
| `VLLM_NIXL_SIDE_CHANNEL_PORT` | `5600` | NIXL 旁路端口 | 不通→握手 5s 超时→load error | 确保可达 |
| `VLLM_NIXL_EP_MAX_NUM_RANKS` | `32` | NIXL 最大 rank 数 | — | — |
| **`VLLM_KEEP_ALIVE_ON_ENGINE_DEATH`** ⭐ | `0`(False) | 引擎死后是否保活进程 | **场景 4**：`0`=引擎死后 `EngineDeadError`→500+进程退出；`1`=进程不退但请求**无限挂起** | 通常保持 `0`；想保活排查问题可临时 `1` |
| **`VLLM_HTTP_TIMEOUT_KEEP_ALIVE`** | `5`(s) | uvicorn 唯一的 HTTP 超时（空闲连接）| 影响空闲 keep-alive，**不是请求超时** | 一般不用动 |
| `VLLM_ENGINE_READY_TIMEOUT_S` | `600`(s) | 启动时等 engine core 就绪的截止 | **场景 9**：权重加载/建图超 600s→启动 `TimeoutError` | 大模型/慢昇腾可调大 |
| `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS` | `300`(s) | 单次 `execute_model` 的 RPC 超时（multiproc/Ray）| forward 卡住超 300s→executor 失败→引擎死 | — |
| `VLLM_ENGINE_ITERATION_TIMEOUT_S` | `60`(s) | 引擎迭代超时（当前主要在 voxtral 实时用）| — | — |
| `VLLM_ELASTIC_EP_DRAIN_REQUESTS` | `0` | 弹性扩缩时排空请求 | 配合 drain_timeout(300s) 用 | 1P1D 一般用不到 |

> 注意：**Mooncake 没有任何 `kv_connector_extra_config` 超时项**，它的传输超时只能靠环境变量 `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT` 控制。NIXL/MoRIIO 的超时旋钮在 `extra_config` 里（见表 3）。

### 表 3：`kv_connector_extra_config` 私有项（按 connector）

放在 `--kv-transfer-config` 的 `"kv_connector_extra_config": {...}` 里。

**NIXL（`PyNixlConnector`）— 全部可调超时：**

| 键 | 默认 | 含义 | 影响 / 场景 | 推荐值 |
|---|---|---|---|---|
| **`kv_lease_duration`** ⭐ | `30`(s) | P 把 KV 块 pin 住等 D 来读的时间；heartbeat 自动取 `lease//6`=5s，lease 续期取 `lease*2//3`=20s | **场景 3（lease 太短致 500）**：D 没在 30s 内读完→P 释放块→D 读失败→`fail`→500 | **按 D 实际解码/排队延迟调大**，如 60~120 |
| **`decoder_kv_blocks_ttl`** | `480`(s) | bidirectional 模式下 D 侧 pin 块的时间 | 多轮/双向 KV 复用时块存活时间 | 多轮场景调大 |
| `kv_recompute_threshold` | `64`(token) | 远程 KV 小于此值时 D 直接本地重算，不等传输 | 短 prompt 跳过传输，避免小传输超时 | 视模型/网络权衡 |
| `engine_ttl` | `3600`(s) | 远端 engine 状态空闲超时回收 | 防握手状态泄漏，非请求超时 | 一般不动 |

**MoRIIO（`MoRIIOConnector`）— 唯一会 `raise` 的 connector：**

| 键 | 默认 | 含义 | 影响 / 场景 | 推荐值 |
|---|---|---|---|---|
| **`transfer_timeout`** ⭐ | `30.0`(s) | RDMA 完成等待，超时**直接 `raise TransferError`**→`fail`→500 | **场景 1（MoRIIO）**：唯一把传输超时变成硬报错的 | NIC/网络拥塞时调大，如 60~120 |
| **`defer_timeout`** | `60.0`(s) | 丢失 `finished_sending` 通知时强制释放 producer 块 | 防内存泄漏，防卡死 | 视通知可靠性 |
| `notify_port` | `61005` | notify 端口 | 不通→通知丢失 | 确认可达 |
| `handshake_port` | `6301` | handshake 端口 | 不通→握手失败 | 确认可达 |
| `backend` | `rdma` | 传输后端 | — | 昇腾用 rdma |
| `read_mode` | `false` | 读取模式 | — | — |
| `proxy_ip`/`proxy_ping_port`/`http_port` | 必填 | router/proxy 通信地址 | 不通→ping 重试（`MAX_PING_RETRIES=100`，`PING_INTERVAL=3s`）| 确认可达 |
| `num_workers`/`qp_per_transfer`/`post_batch_size` | 1/1/-1 | RDMA 并发参数 | 并发不足→传输慢→可能触发 transfer_timeout | 按硬件调 |

> MoRIIO 还有一个硬编码常量 `VLLM_MORI_READ_ABORT_REQUEST_TIMEOUT=3600s`（producer 兜底释放，不可配）。

**LMCache（`LMCacheMPConnector`）：**

| 键 | 默认 | 含义 | 影响 |
|---|---|---|---|
| `lmcache.mp.mq_timeout` | `300`(s) | 消息队列请求超时（实际在 lmcache 包内执行）| 场景 1（LMCache）：MQ 卡住 300s 超时 |
| `lmcache.mp.heartbeat_interval` | `10`(s) | 心跳间隔 | — |

**HF3FS：**

| 键 | 默认 | 含义 |
|---|---|---|
| `hf3fs_client_numjobs` | `16` | 并发 IO 任务数；不足→传输慢。无显式传输超时（靠后端）|

### 表 4：服务器 / 进程层参数

| 参数 | 默认 | 含义 | 影响 / 场景 |
|---|---|---|---|
| `--shutdown-timeout`（`arg_utils.py:1535`，`config/vllm.py:384`）| **`0`** | SIGTERM 时排空/abort 在途请求的宽限。`0`=立即 abort | 重启/重部署时**直接杀在途请求**→客户端连接重置（场景 9）|
| `VLLM_HTTP_TIMEOUT_KEEP_ALIVE` | `5` | uvicorn keep-alive | 仅空闲连接，非请求超时 |
| `--uvicorn-log-level` | `info` | uvicorn 日志级别 | 排查用 |
| uvicorn `timeout` / `timeout_graceful_shutdown` / `limit_concurrency` | **均未设置（无限）** | — | **这是「引擎和前端都没有请求级超时」的根因**，无可配项 |

### 表 5：场景 → 该调哪个参数（速查）

| 超时场景 | 根因参数（默认）| 调整建议 |
|---|---|---|
| 场景 1 KV 传输慢（NIXL）| `kv_lease_duration`=30 太短 + `kv_load_failure_policy`=`fail` | 调大 lease 到 60~120；策略改 `recompute` |
| 场景 1 KV 传输慢（MoRIIO）| `transfer_timeout`=30 触发 raise | 调大 transfer_timeout 到 60~120 |
| 场景 1 KV 传输慢（Mooncake）| `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT`=480 且超时不报错 | 无法让它变 500；必须在 proxy 设有限转发超时 |
| 场景 3 D 读 KV 不及时 | NIXL `kv_lease_duration`=30 | 按解码延迟调大；或降低 D 并发 |
| 场景 4 P/D 引擎崩溃 | `kv_load_failure_policy`=`fail` + `VLLM_KEEP_ALIVE_ON_ENGINE_DEATH`=0 | 保持默认（500+退出）；排查昇腾 OOM/建图 |
| 场景 6 bootstrap 挂 | `VLLM_MOONCAKE_BOOTSTRAP_PORT` 不通 | 确保端口互通 |
| 场景 9 启动慢 | `VLLM_ENGINE_READY_TIMEOUT_S`=600 | 大模型调大 |
| 全场景兜底 | 引擎/proxy 无请求级超时 | **在 proxy 层设有限整体超时 + 重试**（proxy 转发超时在上游 example 里是**硬编码**的）|

---

## 四、最该警惕的三个风险点与建议

1. **Mooncake 的「静默卡死」**：480s 超时只 warning、请求不报错，是「既不 500 也不回包」的根因。若用 Mooncake，务必：① 在 proxy 层设**有限**的转发超时（别用 `timeout=None`），让客户端能拿到明确错误；② 监控 `WAITING_FOR_REMOTE_KVS` 停留时间。

2. **`kv_load_failure_policy="fail"` + 短 lease = 高 500 率**：默认 fail 策略下，任何 KV 读失败都直接 `FINISHED_ERROR` → 500。对稳定性敏感的场景可考虑 `kv_load_failure_policy="recompute"`（D 本地重算兜底，`scheduler.py:1650-1652`），代价是额外算力；同时把 NIXL 的 `kv_lease_duration` 按 D 的实际解码延迟调大。

3. **全链路没有任何请求级超时**：从 client→proxy→engine 都没有「请求总耗时上限」。最终兜底的只能是 **客户端侧 timeout** 和 **proxy 转发超时**。强烈建议在 proxy（最靠近客户端的统一收口点）配置合理的整体超时 + 失败重试/快速失败，而不是依赖 vLLM engine 自己（它不会主动杀超时请求）。

---

## 五、关于 proxy 转发超时——它不是「配置参数」，是硬编码

需要特别说明：**proxy 层的转发超时在上游 example 里是写死在源码里的，没有配置开关**：

| Proxy 源码 | 硬编码值 | 位置 |
|---|---|---|
| `disagg_proxy_demo.py` | `AIOHTTP_TIMEOUT = 6*60*60`（6h）| `:34` |
| `disagg_epd_proxy.py` | `ClientTimeout(total=100_000)` | `:293` |
| `disagg_proxy_multiturn.py` / mooncake proxy / lmcache proxy | `httpx.AsyncClient(timeout=None)`（**无限**）| 各自 client 构造处 |

也就是说：
- 上游 example proxy 的转发超时**不能通过配置改**，只能改源码。
- 实际 1P1D 昇腾部署若用 **vllm-ascend 侧的生产 proxy 或自研 proxy**，转发超时是那个 proxy 自己的配置项（不在这棵树里），需到对应 proxy 实现/部署配置里去找。
- **建议**：无论用哪个 proxy，确保它的转发超时是**有限值**（如 60~300s），而不是 `None`，否则遇到 D 卡死/网络分区会永久挂起（场景 2/8）。这是整条链路里唯一能兜住「引擎不报错」卡死的收口点。

---

## 附：关键代码位置索引

**HTTP 前端层**
- `vllm/entrypoints/openai/api_server.py:602,647` — `timeout_keep_alive`
- `vllm/entrypoints/openai/chat_completion/serving.py:451,789-814` — 无截止 generate 循环
- `vllm/entrypoints/serve/utils/error_response.py:16-72` — 异常 → HTTP 状态码映射
- `vllm/entrypoints/serve/utils/server_utils.py:327-378` — engine_error_handler / generation_error_handler
- `vllm/entrypoints/launcher.py:156-178` — watchdog_loop / `VLLM_KEEP_ALIVE_ON_ENGINE_DEATH`

**引擎核心层**
- `vllm/v1/engine/async_llm.py:575-584` — 无超时 generate 循环
- `vllm/v1/engine/core_client.py:612-630` — `VLLM_ENGINE_READY_TIMEOUT_S` 启动握手
- `vllm/v1/engine/core.py:1098-1105,1435-1447` — 前端握手 / `_send_engine_dead`
- `vllm/v1/core/sched/scheduler.py:870-890,2425-2468` — `WAITING_FOR_REMOTE_KVS` / `_handle_invalid_blocks`
- `vllm/config/kv_transfer.py:69` — `kv_load_failure_policy` 默认 `"fail"`
- `vllm/config/vllm.py:384` — `shutdown_timeout` 默认 0

**KV 传输 connector 层**
- `vllm/envs.py:215,1574` — `VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=480`
- `vllm/distributed/kv_transfer/kv_connector/v1/mooncake/mooncake_connector.py:1060-1085,1567,1706` — Mooncake 超时点
- `vllm/distributed/kv_transfer/kv_connector/v1/nixl/scheduler.py:70-76,141,154` — NIXL lease/threshold/ttl
- `vllm/distributed/kv_transfer/kv_connector/v1/nixl/worker.py:264,458,576,1858-1875` — NIXL worker 超时
- `vllm/distributed/kv_transfer/kv_connector/v1/moriio/moriio_common.py:301-324` — MoRIIO 常量
- `vllm/distributed/kv_transfer/kv_connector/v1/moriio/moriio_engine.py:526-566` — `transfer_timeout` raise
