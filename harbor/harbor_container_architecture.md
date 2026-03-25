# Harbor 容器架构原理分析

> 分析日期: 2026-03-18
> 分析范围: Harbor Framework 容器管理与嵌套架构

---

## 1. 核心概念澄清

**Harbor默认并不运行在容器内部**。根据源码分析，Harbor采用的是 **Docker-outside-of-Docker (DooD)** 模式。

---

## 2. 两种容器架构模式

### 2.1 Docker-outside-of-Docker (DooD) - 默认模式

```
┌─────────────────────────────────────────────────────────────────┐
│                        Host Machine (宿主机)                     │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Harbor CLI / Python 进程                    │    │
│  │                                                         │    │
│  │   harbor run --agent claude-code --dataset ...          │    │
│  │                                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                            │                                     │
│                            │ docker compose ...                  │
│                            ▼                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                   Docker Daemon                          │    │
│  │                  (宿主机上的Docker守护进程)               │    │
│  └─────────────────────────────────────────────────────────┘    │
│                            │                                     │
│              ┌─────────────┼─────────────┐                      │
│              ▼             ▼             ▼                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │
│  │ Task Container│ │ Task Container│ │ Task Container│           │
│  │  (Trial 1)    │ │  (Trial 2)    │ │  (Trial N)    │           │
│  │              │ │              │ │              │            │
│  │  Agent执行   │ │  Agent执行   │ │  Agent执行   │            │
│  └──────────────┘ └──────────────┘ └──────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

**特点**:
- Harbor CLI 直接运行在宿主机上
- 通过 `docker compose` 命令调用宿主机 Docker Daemon
- Task容器与Harbor进程是**兄弟关系**
- 不需要挂载 `docker.sock`

### 2.2 Docker-in-Docker (DinD) - 云环境模式

```
┌─────────────────────────────────────────────────────────────┐
│                    Local Machine                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Harbor CLI                              │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ API调用
                          ▼
┌─────────────────────────────────────────────────────────────┐
│               Daytona Cloud (DinD模式)                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │          Sandbox VM (docker:28-dind)                │    │
│  │  ┌─────────────────────────────────────────────┐    │    │
│  │  │           Docker Daemon (dockerd)            │    │    │
│  │  └─────────────────────────────────────────────┘    │    │
│  │                      │                               │    │
│  │          ┌───────────┼───────────┐                  │    │
│  │          ▼           ▼           ▼                  │    │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐      │    │
│  │  │Task Container│ │Task Container│ │Task Container│    │    │
│  │  │ (Trial 1)  │ │ (Trial 2)  │ │ (Trial N)  │      │    │
│  │  └────────────┘ └────────────┘ └────────────┘      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**特点**:
- 云端创建独立的VM沙箱
- VM内部运行完整的Docker环境
- 额外的隔离层，适合多租户场景
- Harbor通过API远程控制

---

## 3. 源码分析

### 3.1 Docker环境实现 (DooD)

位置: `src/harbor/environments/docker/docker.py`

```python
async def _run_docker_compose_command(
    self, command: list[str], check: bool = True, timeout_sec: int | None = None
) -> ExecResult:
    """Run a docker compose command and return the result."""
    full_command = [
        "docker",
        "compose",
        "-p",
        self.session_id.lower().replace(".", "-"),
        "--project-directory",
        str(self.environment_dir.resolve().absolute()),
    ]
    for path in self._docker_compose_paths:
        full_command.extend(["-f", str(path.resolve().absolute())])
    full_command.extend(command)

    process = await asyncio.create_subprocess_exec(
        *full_command,
        env=self._env_vars.to_env_dict(include_os_env=True),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    # ...
```

**关键点**:
- 直接使用 `asyncio.create_subprocess_exec` 执行 `docker compose` 命令
- 没有socket挂载，直接调用宿主机Docker
- 使用环境变量传递配置

### 3.2 环境变量配置

```python
class DockerEnvironmentEnvVars(BaseModel):
    main_image_name: str
    context_dir: str
    host_verifier_logs_path: str      # 宿主机日志路径
    host_agent_logs_path: str         # 宿主机Agent日志路径
    host_artifacts_path: str          # 宿主机artifacts路径
    env_verifier_logs_path: str       # 容器内日志路径
    env_agent_logs_path: str          # 容器内Agent日志路径
    env_artifacts_path: str           # 容器内artifacts路径
    prebuilt_image_name: str | None = None
    cpus: int = 1
    memory: str = "1G"
```

### 3.3 文件挂载机制

```
宿主机路径                              容器内路径
/home/user/trials/xxx/verifier/  ←→  /logs/verifier/
/home/user/trials/xxx/agent/     ←→  /logs/agent/
/home/user/trials/xxx/artifacts/ ←→  /logs/artifacts/
```

通过 **bind mount (绑定挂载)** 实现日志和数据的双向共享。

---

## 4. 为什么看起来像"容器套容器"？

### 4.1 可能的误解来源

| 场景 | 说明 |
|------|------|
| **云环境执行** | Daytona/Modal等使用DinD模式，确实有嵌套 |
| **CI/CD环境** | Jenkins/GitLab CI可能将Harbor运行在容器内 |
| **开发环境** | VSCode DevContainer等开发容器 |

### 4.2 CI/CD中的特殊情况

在CI/CD环境中，Harbor可能运行在容器内，通过挂载 `docker.sock` 实现容器管理:

```yaml
# CI/CD 配置示例
jobs:
  harbor-test:
    container:
      image: harbor:latest
      volumes:
        - /var/run/docker.sock:/var/run/docker.sock  # 关键：共享Docker socket
```

这种情况下:
```
┌─────────────────────────────────────────────────────────────┐
│                      Host Machine                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 Docker Daemon                        │    │
│  │              /var/run/docker.sock                    │    │
│  └─────────────────────────────────────────────────────┘    │
│              ▲                           ▲                   │
│              │ mount                     │                   │
│  ┌───────────┴───────────┐   ┌───────────┴───────────┐      │
│  │  Harbor Container     │   │   Task Containers     │      │
│  │  (CI Runner)          │   │   (兄弟关系)          │      │
│  └───────────────────────┘   └───────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

**关键**: 即使Harbor在容器内，Task容器仍然是"兄弟"而非"子"容器，因为它们共享同一个Docker Daemon。

---

## 5. 两种模式对比

| 特性 | DooD (默认) | DinD (云环境) |
|------|-------------|---------------|
| Harbor运行位置 | 宿主机进程 | 云端VM |
| Docker访问方式 | 直接调用docker命令 | 通过API或嵌套Docker |
| 容器关系 | 兄弟关系 | 嵌套关系 |
| 隔离性 | 共享宿主机内核 | 完全隔离的VM |
| 性能 | 高 (无额外开销) | 中 (有虚拟化层) |
| 适用场景 | 本地开发 | 云端大规模执行 |
| 复杂度 | 低 | 高 |

---

## 6. Harbor启动Task容器的完整流程

```
┌────────────────────────────────────────────────────────────────────┐
│                    Harbor Trial Execution Flow                      │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Trial.start()                                                    │
│    - 创建 TrialPaths (日志目录)                                      │
│    - 初始化 DockerEnvironment                                       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. Environment.start(force_build=False)                            │
│    a) 检查镜像是否存在                                               │
│    b) 执行: docker compose build (如果需要)                         │
│    c) 执行: docker compose down (清理旧容器)                        │
│    d) 执行: docker compose up --detach --wait                      │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. Task Container 运行中                                            │
│    - 主容器执行: sleep infinity (保持运行)                          │
│    - 挂载卷: logs/, artifacts/                                      │
│    - 网络模式: 可选 none (禁用网络)                                  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. Agent 执行                                                       │
│    - docker compose exec main <agent-command>                      │
│    - Agent在Task容器内执行任务                                       │
│    - 输出写入挂载的日志目录                                          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 5. Verifier 验证                                                    │
│    - docker compose cp tests/ main:/tests/                         │
│    - docker compose exec main /tests/test.sh                       │
│    - docker compose cp main:/logs/verifier/ ./                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 6. Environment.stop(delete=True)                                   │
│    - chown 修复文件权限                                             │
│    - docker compose down --rmi all --volumes                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Docker Compose 文件组合

Harbor使用多个compose文件的组合策略:

```python
@property
def _docker_compose_paths(self) -> list[Path]:
    """
    Returns the docker-compose file(s) to use.

    Option 1: Simple task (just Dockerfile)
    - Uses: base + build/prebuilt

    Option 2: Task with extra services (docker-compose.yaml)
    - Uses: base + build/prebuilt + docker-compose.yaml
    """
    paths = [
        self._DOCKER_COMPOSE_BASE_PATH,      # 基础配置
        build_or_prebuilt,                    # 构建或预构建配置
    ]

    if self._environment_docker_compose_path.exists():
        paths.append(self._environment_docker_compose_path)  # 任务特定配置

    if not self.task_env_config.allow_internet:
        paths.append(self._DOCKER_COMPOSE_NO_NETWORK_PATH)   # 禁用网络

    return paths
```

**文件组合示例**:
```bash
docker compose \
  -f compose-base.yaml \
  -f compose-build.yaml \
  -f task-docker-compose.yaml \
  -f compose-no-network.yaml \
  up --detach
```

---

## 8. 并行执行时的容器管理

```
┌─────────────────────────────────────────────────────────────────┐
│                      Host Machine                               │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Harbor Orchestrator                         │   │
│  │                                                         │   │
│  │   Semaphore(n=4) - 最多4个并发Trial                      │   │
│  │                                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            │                                    │
│         ┌──────────────────┼──────────────────┐                │
│         ▼                  ▼                  ▼                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │Trial 1      │    │Trial 2      │    │Trial 3      │        │
│  │session-001  │    │session-002  │    │session-003  │        │
│  │             │    │             │    │             │        │
│  │ docker      │    │ docker      │    │ docker      │        │
│  │ compose -p  │    │ compose -p  │    │ compose -p  │        │
│  │ session-001 │    │ session-002 │    │ session-003 │        │
│  └─────────────┘    └─────────────┘    └─────────────┘        │
│         │                  │                  │                │
│         ▼                  ▼                  ▼                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
│  │ Container   │    │ Container   │    │ Container   │        │
│  │ trial-001   │    │ trial-002   │    │ trial-003   │        │
│  └─────────────┘    └─────────────┘    └─────────────┘        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   Docker Daemon                          │   │
│  │            (统一管理所有容器实例)                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键机制**:
- 使用 `-p <session-id>` 指定项目名，隔离不同Trial
- 类级别的锁防止相同镜像并行构建
- 独立的日志目录避免冲突

---

## 9. 网络隔离实现

当 `allow_internet=False` 时:

```yaml
# compose-no-network.yaml
services:
  main:
    network_mode: none  # 完全禁用网络
```

这确保Agent执行环境与外网隔离，适用于:
- 安全敏感的测试场景
- 防止Agent访问外部资源
- 确保测试的可重复性

---

## 10. 总结

### 10.1 核心问题解答

| 问题 | 答案 |
|------|------|
| Harbor运行在容器里吗？ | **默认不**，运行在宿主机进程 |
| Task容器是怎么启动的？ | 通过 `docker compose` 命令调用宿主机Docker Daemon |
| Harbor和Task容器是什么关系？ | **兄弟关系** (同一个Docker Daemon管理) |
| 为什么有时看起来像嵌套？ | 云环境或CI/CD使用DinD模式 |
| 日志怎么共享？ | 通过bind mount (绑定挂载) |

### 10.2 设计优势

| 优势 | 说明 |
|------|------|
| **简单** | 不需要复杂的DinD配置 |
| **高性能** | 没有额外的虚拟化层开销 |
| **易于调试** | 日志直接在宿主机可访问 |
| **资源效率** | 共享宿主机Docker缓存 |
| **灵活性** | 支持多种执行环境 |

### 10.3 架构选择建议

| 场景 | 推荐架构 | 原因 |
|------|----------|------|
| 本地开发测试 | DooD | 简单、高效 |
| CI/CD流水线 | DooD + socket mount | 与现有CI集成 |
| 云端大规模执行 | DinD (Daytona/Modal) | 隔离性、可扩展性 |
| 多租户SaaS | DinD + VM隔离 | 安全性要求 |

---

## 附录: 相关源码文件

| 文件 | 说明 |
|------|------|
| `src/harbor/environments/docker/docker.py` | Docker环境实现 |
| `src/harbor/environments/docker/compose-*.yaml` | Compose模板文件 |
| `src/harbor/environments/daytona.py` | Daytona云环境 (DinD) |
| `src/harbor/environments/modal.py` | Modal云环境 |
| `src/harbor/orchestrators/local.py` | 本地并行执行器 |
| `src/harbor/orchestrators/queue.py` | 队列式并行执行器 |
