# Mooncake 源码分析与基于华为云 CCE K8s Lease 的选主机制设计

> 分析对象：Mooncake（KVCache-centric Disaggregated Architecture）Store 的 HA 子系统
> 目标：基于华为云 CCE（Cloud Container Engine，CNCF 认证托管 Kubernetes）的 `coordination.k8s.io` Lease 锁，设计 mooncake_master 多副本高可用部署的 leader 选取机制
> 日期：2026-06-17

---

## 一、Mooncake Store 的架构与 HA 现状

### 1.1 Master 是控制面，不是数据面

Mooncake Store 的 `mooncake_master` 只负责**元数据控制面**（对象注册、分配、segment 管理、eviction），真正的 KVCache 数据走 Transfer Engine 在 client↔client 之间点对点传输。所以"master 高可用"本质是**让控制面元数据服务不成为单点**，与数据路径解耦——这点很关键，它意味着选主只影响元数据 RPC 的路由。

- 二进制入口：`mooncake_master`（`mooncake-store/src/master.cpp:950`，`add_executable(mooncake_master master.cpp)` 在 `src/CMakeLists.txt:269`）
- 非 HA 模式（`master.yaml` 默认 `enable_ha: false`）：单 master，是 SPOF（部署文档 `docs/source/deployment/mooncake-store-deployment-guide.md:239` 明确写了 *"the master is a single point of failure"*）
- HA 模式（`--enable_ha=true`）：`master.cpp:1108-1111` 走 `MasterServiceSupervisor(...).Start()`

### 1.2 所有副本对等，角色由选举动态产生

Mooncake **没有独立的 SlaveServer 类**。每个 `mooncake_master` 实例运行同一个 `MasterServiceSupervisor::RunSupervisorLoop` 状态机（`mooncake-store/src/ha/leadership/master_service_supervisor.cpp:190-450`），副本始终处于以下 7 个状态之一（`include/ha/ha_types.h:114-122`）：

```
kStarting → kStandby → kCandidate → (kRecovering/kCatchingUp) → kLeaderWarmup → kServing
```

对外只有两个角色（`ha_types.h:144-157` `MasterRuntimeRoleToString`）：`kLeaderWarmup`/`kServing` ⇒ **leader**，其余 ⇒ **standby**。被选中则启动 `coro_rpc_server` 对外服务，否则运行 `HotStandbyService` 跟随主。

### 1.3 选主抽象：`LeaderCoordinator`（可插拔后端）

接口在 `include/ha/leadership/leader_coordinator.h`，工厂在 `src/ha/leadership/leader_coordinator_factory.cpp:12-51`，按 `spec.type` 分发到三种后端：

| 后端 | `--ha_backend_type` | 机制 |
|---|---|---|
| etcd | `etcd`（默认） | lease + CAS 写 master-view key + keepalive |
| redis | `redis` | `SET NX` + 过期 + 续约线程 |
| **k8s** | `k8s` | **Kubernetes `coordination.k8s.io/Lease` + client-go leaderelection** ✅ 已实现 |

接口语义（这是后面映射到 K8s Lease 的关键）：

- `ReadCurrentView()` — 读当前 leader（返回 `MasterView{leader_address, view_version}`）
- `TryAcquireLeadership(leader_address)` — 抢主，返回 `ACQUIRED`/`CONTENDED`
- `RenewLeadership(session)` / `StartLeadershipMonitor(...)` — 续约 + 丢主回调
- `WaitForViewChange(version, timeout)` — 等视图变化

### 1.4 复制模型：OpLog + Snapshot，pull-based

- Leader 写 OpLog + 周期性 snapshot；standby 通过 `HotStandbyService` → `OpLogReplicator` 拉取并回放，promotion 时 `Promote()` + `ExportMetadataSnapshot()` 种子化新主。
- **重要限制**：`mooncake-store/src/ha/standby_controller.cpp:41` —— `has_oplog_following = (spec.type == HABackendType::ETCD)`。**OpLog 连续跟随目前仅 etcd 后端开启**；redis/k8s 后端的 standby 只能用 snapshot bootstrap（需 `--enable_snapshot_restore`），**没有连续增量复制**，RPO 取决于 snapshot 间隔。这是 K8s 方案必须正视的约束（见第五节 Gap 2）。

---

## 二、现有 K8s Lease 选主机制（源码级解析）

Mooncake **已经内置了** 基于 K8s Lease 的选主后端，默认编译关闭（`CMakeLists.txt:49` `option(STORE_USE_K8S_LEASE ... OFF)`）。下面是它的工作原理，这正是要在 CCE 上跑的东西。

### 2.1 Go c-shared 选主 shim（`mooncake-common/k8s-lease/k8s_lease_wrapper.go`）

核心是 `runElection()`（`:113-184`），用的是 Kubernetes 官方 leader-election 标准姿势：

```go
lock := &resourcelock.LeaseLock{
    LeaseMeta: metav1.ObjectMeta{Name: leaseName, Namespace: namespace},
    Client:    globalClient.CoordinationV1(),
    LockConfig: resourcelock.ResourceLockConfig{Identity: identity},   // ← identity = rpc_address:port
}
le, _ := leaderelection.NewLeaderElector(leaderelection.LeaderElectionConfig{
    Lock: lock,
    LeaseDuration:   5 * time.Second,   // kDefaultLeaseDurationSec (k8s_leader_coordinator.cpp:21)
    RenewDeadline:   3 * time.Second,   // kDefaultRenewDeadlineSec
    RetryPeriod:     1 * time.Second,   // kDefaultRetryPeriodSec
    ReleaseOnCancel: true,
    Callbacks: { OnStartedLeading: ..., OnStoppedLeading: ... },
})
go le.Run(ctx)
```

- 集群内连接：`initClient()`（`:81-110`）优先 `rest.InClusterConfig()`（ServiceAccount token），回退 `KUBECONFIG`/`~/.kube/config`。
- `getHolder()`（`:187-218`）读 `Lease.Spec.HolderIdentity`，并把**过期的 lease 视为无 holder**——让 C++ supervisor 去抢主而不是傻等 standby。
- 暴露的 cgo 符号（`//export`）：`K8sLeaseInit/RunElection/WaitElected/WaitLost/CancelElection/GetHolder/WatchHolder/CancelWatch`，C++ 侧由 `mooncake-store/src/k8s_lease_helper.cpp` 适配为 `ErrorCode`。

### 2.2 C++ 协调器 `K8sLeaderCoordinator`（`src/ha/leadership/backends/k8s/k8s_leader_coordinator.cpp`）

把 client-go 的语义映射到 `LeaderCoordinator` 接口，两个映射点决定了部署形态：

1. **leader 身份 = `--rpc_address:rpc_port`**。`TryAcquireLeadership(config.local_hostname)`（`master_service_supervisor.cpp:239`），而 `local_hostname = rpc_address + ":" + rpc_port`（`include/master_config.h:232`）。这个字符串会写进 K8s Lease 的 `holderIdentity` 字段，成为 standby 找主、客户端路由的依据。⇒ **`--rpc_address` 必须是稳定且可被集群内路由的每 Pod 唯一地址**（第四节核心）。
2. **`view_version = leaseTransitions`**（`k8s_leader_coordinator.cpp:104-105`），用 Lease 的 `leaseTransitions` 字段做单调递增的"任期版本"，客户端可据此检测 epoch 切换。
3. **connstring 格式 = `namespace/lease-name`**（`ParseConnstring` `:422-451`），只传一次，无 host/port（集群内 SA 直连 API Server）。例：`mooncake-system/mooncake-master`。

### 2.3 故障切换状态机（`RunSupervisorLoop`）

```
standby 跟随当前主
  → ReadCurrentView()：若 lease 无 holder → TryAcquireLeadership(本机 rpc_address:port)
  → 抢到 → PromoteStandby() → kLeaderWarmup（续约满一个 lease_ttl 稳定期）
  → kServing：起 coro_rpc_server，StartLeadershipMonitor(丢主回调)
  → 丢主：server.stop() + ReleaseLeadership() → 回到 standby，其余副本重新抢主
```

丢主回调（`master_service_supervisor.cpp:385-394`）会先 `SetServiceAvailable(false)` 再 `server.stop()`，确保旧 leader 在重新选举前就停止接单。

---

## 三、华为云 CCE 的适配要点

[CCE（Cloud Container Engine）](https://www.huaweicloud.com/intl/en-us/product/cce.html) 是 CNCF 认证的**标准托管 Kubernetes**，因此：

1. **`coordination.k8s.io/v1` Lease API 原生支持**——`kube-node-lease` 命名空间本身就用 Lease 做节点心跳，Mooncake 用同一 API 群组做应用层选主，行为与社区 K8s 完全一致。
2. **InClusterConfig + ServiceAccount token 自动挂载** 可直接用，无需改 Go shim。
3. **RBAC**：选主 SA 需要对 `leases` 资源有 `get/list/watch/create/update/patch` 权限（client-go leaderelection 在 `ReleaseOnCancel:true` 时会 delete/update lease）。
4. CCE 特性不影响选主逻辑；网络模式（VPC 路由 / CCE Turbo ENI）只影响 Pod 间可达性，集群内 headless Service DNS 在两种模式下都通。

> 结论：**不需要改任何选主代码**就能在 CCE 上跑，重点全在"部署形态 + RBAC + Pod 身份 + 客户端路由"四件事。

---

## 四、基于 CCE K8s Lease 的多副本高可用部署设计（推荐方案）

核心思路：**StatefulSet 给每个副本稳定身份 → headless Service 让 lease 身份可路由 → ClusterIP Service + readiness 门控让客户端只连到 leader → RBAC 授权选主**。

### 4.1 拓扑

```
                ┌──────────────────────────────────────────┐
   client ─────►│  Service: mooncake-master (ClusterIP)    │  readiness=只有 /role==leader 的 Pod Ready
                │   selector: app=mooncake-master          │  ⇒ K8s 自动把流量只路由到 leader Pod
                └──────────────────────────────────────────┘
                              │ endpoints = {leader pod IP}
        ┌─────────────────────┼─────────────────────┐
   ┌────▼────┐  ┌─────────────▼──────────┐  ┌──────▼───────┐
   │ Pod-0   │  │ Pod-1 (standby)        │  │ Pod-2(standby)│
   │ leader  │  │ HotStandbyService      │  │              │
   │ rpc :50051│ │ 跟随 Pod-0 的 lease 身份 │  │              │
   └─────────┘  └────────────────────────┘  └──────────────┘
        ▲   所有副本共同竞争 K8s Lease: mooncake-system/mooncake-master
        │   (coordination.k8s.io/v1, holderIdentity = pod-fqdn:50051)
   Headless Service: mooncake-master-hs  ⇒  pod-fqdn 可被 standby 解析去拉 OpLog
```

为什么用三件套（StatefulSet + 两个 Service）：

- **StatefulSet**：稳定 `Pod 名 + PVC`。snapshot/OpLog 本地存储（`localfs_oplog_store`）需要稳定的挂载点，副本身份也要稳定。
- **Headless Service `mooncake-master-hs`**：让每个 Pod 拿到稳定 FQDN `mooncake-master-<i>.mooncake-master-hs.<ns>.svc.cluster.local`，用作 `--rpc_address` ⇒ lease `holderIdentity` 全集群唯一且跨重启稳定，standby 也能解析它去连主。
- **ClusterIP Service `mooncake-master`**：**不解决选主，只解决客户端路由**。靠 readiness 门控，K8s endpoints 里只保留 leader Pod，客户端连这个稳定 DNS 永远命中 leader，**无需客户端读 lease**，也无需给客户端加 `k8s://` 协议（Mooncake 目前只有 etcd HA 的 `etcd://` scheme，没有 k8s scheme——这个 Service 正好绕开了这个缺口）。

### 4.2 RBAC：让 SA 能操作 Lease

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mooncake-master
  namespace: mooncake-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: mooncake-master-leader-election
  namespace: mooncake-system
rules:
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # client-go leaderelection 默认会 record Event（可选，失败不影响选主）
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: mooncake-master-leader-election
  namespace: mooncake-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: mooncake-master-leader-election
subjects:
  - kind: ServiceAccount
    name: mooncake-master
    namespace: mooncake-system
```

### 4.3 Headless Service + StatefulSet + 客户端 Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mooncake-master-hs          # headless: 给每个 Pod 稳定 FQDN
  namespace: mooncake-system
spec:
  clusterIP: None
  selector:
    app: mooncake-master
  ports:
    - name: rpc
      port: 50051
      targetPort: 50051
    - name: admin
      port: 9003
      targetPort: 9003
---
apiVersion: v1
kind: Service
metadata:
  name: mooncake-master             # ClusterIP: 客户端连这个，只路由到 leader
  namespace: mooncake-system
spec:
  selector:
    app: mooncake-master
  ports:
    - name: rpc
      port: 50051
      targetPort: 50051
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mooncake-master
  namespace: mooncake-system
spec:
  serviceName: mooncake-master-hs
  replicas: 3                       # ≥3 建议（quorum 友好，单点故障仍可选举）
  selector:
    matchLabels:
      app: mooncake-master
  template:
    metadata:
      labels:
        app: mooncake-master
    spec:
      serviceAccountName: mooncake-master
      containers:
        - name: mooncake-master
          image: <你的 CCE 镜像，构建加 -DSTORE_USE_K8S_LEASE=ON>
          # 用 downward API 拿稳定 Pod 名/命名空间，拼出全集群唯一 FQDN 作为选主身份
          env:
            - name: POD_NAME
              valueFrom: { fieldRef: { fieldPath: metadata.name } }
            - name: POD_NAMESPACE
              valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
          command:
            - /bin/sh
            - -c
            - >
              exec mooncake_master
              --enable_ha=true
              --ha_backend_type=k8s
              --ha_backend_connstring="$(POD_NAMESPACE)/mooncake-master"
              --rpc_address="$(POD_NAME).mooncake-master-hs.$(POD_NAMESPACE).svc.cluster.local"
              --rpc_port=50051
              --metrics_port=9003
              --enable_http_metadata_server=true
              --http_metadata_server_port=8080
          ports:
            - { name: rpc,   containerPort: 50051 }
            - { name: admin, containerPort: 9003 }
            - { name: meta,  containerPort: 8080 }
          # 关键：只有 leader 的 /role 返回 leader 才 Ready ⇒ Service 只路由 leader
          readinessProbe:
            httpGet: { path: /role, port: 9003 }
            periodSeconds: 2
            failureThreshold: 1
          livenessProbe:
            httpGet: { path: /health, port: 9003 }
            initialDelaySeconds: 30
            periodSeconds: 10
  volumeClaimTemplates:             # snapshot/本地 OpLog 存储用稳定卷
    - metadata: { name: data }
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: { requests: { storage: 50Gi } }
```

> `/role`（`rpc_service.cpp:433`）、`/health`（`:427`）、`/leader`（`:450`）、`/ha_status`（`:441`）这些端点都是 Mooncake 已经内置的，**直接拿来当探针，零代码改动**。

### 4.4 客户端路由（无需改 client）

- 集群内客户端：`master_server_address = mooncake-master.mooncake-system.svc:50051`。K8s 把它解析到 leader Pod（只有 leader Ready）。failover 时旧 leader readiness 掉线、新 leader 上线，endpoints 自动更新，客户端用 `RealClient` 已有的重连/退避（`real_client.cpp` ~`:5652`）自然恢复。
- 集群外客户端：给 `mooncake-master` Service 配 LoadBalancer/Ingress。
- **standby→leader 的复制路径**走的是 lease 里的 `holderIdentity`（= pod FQDN），不经 ClusterIP Service，避免负载均衡把 standby 请求打到非 leader，所以 headless FQDN 身份是必须的。

### 4.5 故障切换时序（基于源码常量）

```
T+0   leader Pod 故障，coro_rpc 停止 → readinessProbe /role 失败
      → K8s 立即把该 Pod 移出 mooncake-master Service endpoints（~1 tick ≈ 2s）
T+0   leader 停止续约 K8s Lease
T+5s  LeaseDuration(5s) 到期，Lease 过期
      其余 standby 副本的 getHolder() 判定"无 holder"（k8s_lease_wrapper.go:211-216）
      → 进入 candidate → TryAcquireLeadership → 抢主
T+~6s 新 leader 产生，Warmup 满 5s → kServing → /role=leader → readiness 通过
      → K8s endpoints 加入新 leader Pod
客户端重连退避期间(<10s)感知新 leader
```

RTO ≈ lease 过期 + 选举 + warmup ≈ **10~15s**；RPO 取决于 snapshot 间隔（见 Gap 2）。

---

## 五、必须先补齐的代码 Gap（源码层面，否则 K8s 后端跑不起来）

| # | 位置 | 问题 | 修复 |
|---|---|---|---|
| **1（阻断性）** | `mooncake-store/include/ha/ha_types.h:71-72` | `ValidateHABackendAvailability` 对 `K8S` **无条件**返回 `UNAVAILABLE_IN_CURRENT_MODE`，且**没有** `#ifdef STORE_USE_K8S_LEASE` 保护（etcd/redis 都有）。`BuildHABackendSpec`（`master_service_supervisor.cpp:41-44`）会在到达工厂前就拒绝 k8s 配置，导致启动直接失败。 | 改成与 etcd/redis 一致：`#ifdef STORE_USE_K8S_LEASE` ⇒ `OK`，否则 `UNAVAILABLE_IN_CURRENT_MODE`。 |
| 2 | `standby_controller.cpp:41` | `has_oplog_following = (type == ETCD)` ⇒ K8s 后端的 standby 无连续增量复制，只能 snapshot bootstrap。 | 想要近零 RPO，需实现一个不依赖 etcd 的 OpLog store（如基于 K8s ConfigMap/CRD 或共享 PVC），否则接受 snapshot 粒度的 RPO 并把 `--snapshot_interval_seconds` 调小。 |
| 3 | 客户端 | 只有 etcd 的 `etcd://IP;IP` 发现 scheme，**没有 `k8s://` scheme**。 | 本设计用"readiness 门控的 ClusterIP Service"绕开（推荐）；若要客户端直读 lease，需给 `MasterClient` 加 `k8s://namespace/lease-name` 解析。 |
| 4 | `CMakeLists.txt:51-55` | `STORE_USE_K8S_LEASE` 与 `STORE_USE_ETCD`/`USE_ETCD` 互斥（都构建 Go c-shared 库，同进程冲突）。 | CCE 方案下选 K8s 后端就不开 etcd；构建镜像时 `-DSTORE_USE_K8S_LEASE=ON -DSTORE_USE_ETCD=OFF`。 |

### 阻断性修复 patch（Gap 1）

```diff
--- a/mooncake-store/include/ha/ha_types.h
+++ b/mooncake-store/include/ha/ha_types.h
@@ -69,7 +69,11 @@ inline ErrorCode ValidateHABackendAvailability(HABackendType type) {
             return ErrorCode::UNAVAILABLE_IN_CURRENT_MODE;
 #endif
         case HABackendType::K8S:
+#ifdef STORE_USE_K8S_LEASE
+            return ErrorCode::OK;
+#else
             return ErrorCode::UNAVAILABLE_IN_CURRENT_MODE;
+#endif
     }
```

---

## 六、总结

1. Mooncake Store 的 HA **已经内置了完整的 K8s Lease 选主能力**（`STORE_USE_K8S_LEASE`，client-go leaderelection + cgo shim + C++ `K8sLeaderCoordinator`），只是默认关闭、且有一处 `ha_types.h:71-72` 的编译开关 bug 需要先修。
2. CCE 是标准 CNCF Kubernetes，`coordination.k8s.io/v1` Lease、ServiceAccount、RBAC 全部原生可用，**无需改选主代码**。
3. 推荐部署形态：**StatefulSet（3 副本）+ headless Service（给 Pod 稳定 FQDN 作 lease 身份）+ ClusterIP Service（readiness 门控 `/role`==leader，客户端只命中 leader）+ 命名空间级 RBAC（leases 的 get/list/watch/create/update/patch/delete）**。客户端零改动。
4. 两个工程要点先确认：①修 `ha_types.h` 的 `#ifdef`；②`--rpc_address` 必须用 Pod FQDN（保证 lease `holderIdentity` 稳定、唯一、可被 standby 解析）。

---

## 参考资料

- [华为云 CCE 产品页（Cloud Container Engine）](https://www.huaweicloud.com/intl/en-us/product/cce.html)
- [CCE 产品文档首页](https://support.huaweicloud.com/intl/zh-cn/cce/index.html)
- [CCE 命名空间与 coordination.k8s.io Lease（节点心跳）说明](https://support.huawei.com/enterprise/en/doc/EDOC1100296475/b44c62db/namespaces)
- [Mooncake Store 部署指南（HA 章节）](https://github.com/kvcache-ai/Mooncake/blob/main/docs/source/deployment/mooncake-store-deployment-guide.md)
- [Mooncake GitHub 仓库](https://github.com/kvcache-ai/Mooncake)
- 关键源码文件：
  - `mooncake-store/include/ha/ha_types.h`
  - `mooncake-store/src/ha/leadership/master_service_supervisor.cpp`
  - `mooncake-store/src/ha/leadership/backends/k8s/k8s_leader_coordinator.cpp`
  - `mooncake-common/k8s-lease/k8s_lease_wrapper.go`
  - `mooncake-store/src/ha/standby_controller.cpp`
  - `mooncake-store/src/rpc_service.cpp`（`/role`、`/health`、`/leader` 端点）
