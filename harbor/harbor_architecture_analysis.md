# Harbor Framework 源码架构深度分析

> 分析日期: 2026-03-18
> 分析范围: Harbor Framework 核心源码

---

## 目录

1. [整体运行逻辑架构](#1-harbor整体运行起来的逻辑架构)
2. [并行执行原理与机制](#2-harbor如何实现测试用例并行执行的原理和机制)
3. [评测结果获取机制](#3-harbor如何获取测试用例执行的评测结果)
4. [Agent日志收集能力](#4-harbor本身运行的时候可以获取到的日志类型)
5. [架构扩展性分析](#5-harbor的架构的扩展性分析结论)

---

## 1. Harbor整体运行起来的逻辑架构

### 1.1 架构概览图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI Layer (Typer)                               │
│                     src/harbor/cli/main.py, jobs.py                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Job Layer                                       │
│                     src/harbor/job.py                                        │
│   - 解析JobConfig，生成TrialConfig列表                                        │
│   - 选择并初始化Orchestrator                                                  │
│   - 聚合Trial结果生成JobResult                                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Orchestrator Layer                                  │
│                   src/harbor/orchestrators/                                  │
│  ┌─────────────────────┐      ┌────────────────────────┐                    │
│  │  LocalOrchestrator  │      │   QueueOrchestrator    │                    │
│  │  (asyncio.Semaphore)│      │  (Producer-Consumer)   │                    │
│  └─────────────────────┘      └────────────────────────┘                    │
│                     BaseOrchestrator (抽象基类)                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Trial Layer                                       │
│                      src/harbor/trial/trial.py                               │
│                                                                             │
│    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐            │
│    │Environment│ → │  Agent   │ → │ Verifier │ → │  Result  │            │
│    │  Start   │    │  Setup   │    │  Run     │    │  Save    │            │
│    └──────────┘    │   + Run  │    └──────────┘    └──────────┘            │
│                    └──────────┘                                              │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │                  │
         ▼                    ▼                  ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   Environment   │  │      Agent      │  │    Verifier     │
│     Factory     │  │     Factory     │  │                 │
│                 │  │                 │  │                 │
│ - Docker        │  │ - claude-code   │  │ 上传tests目录   │
│ - Daytona       │  │ - openhands     │  │ 执行test.sh    │
│ - Modal         │  │ - aider         │  │ 解析reward文件  │
│ - E2B           │  │ - codex         │  │                 │
│ - GKE           │  │ - 20+ agents    │  │                 │
│ - Runloop       │  │                 │  │                 │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 1.2 核心数据流

| 阶段 | 输入 | 输出 | 关键文件 |
|------|------|------|----------|
| 配置解析 | CLI参数 | `JobConfig` | `src/harbor/cli/jobs.py` |
| Trial生成 | `JobConfig` | `List[TrialConfig]` | `src/harbor/job.py` |
| 并行执行 | `TrialConfig` | `TrialResult` | `src/harbor/orchestrators/` |
| 结果聚合 | `List[TrialResult]` | `JobResult` | `src/harbor/job.py` |

### 1.3 关键类与职责

#### BaseOrchestrator (`src/harbor/orchestrators/base.py`)
```python
class BaseOrchestrator(ABC):
    def __init__(
        self,
        trial_configs: list[TrialConfig],
        n_concurrent_trials: int,
        metrics: dict[str, list[BaseMetric]],
        quiet: bool = False,
        plain_output: bool = False,
        retry_config: RetryConfig | None = None,
    ): ...

    @staticmethod
    @abstractmethod
    def type() -> OrchestratorType: ...

    @abstractmethod
    async def run(self) -> list[TrialResult]: ...
```

#### BaseAgent (`src/harbor/agents/base.py`)
```python
class BaseAgent(ABC):
    SUPPORTS_ATIF: bool = False  # 是否支持ATIF轨迹格式

    @staticmethod
    @abstractmethod
    def name() -> str: ...

    @abstractmethod
    def version(self) -> str | None: ...

    @abstractmethod
    async def setup(self, environment: BaseEnvironment) -> None: ...

    @abstractmethod
    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None: ...
```

#### BaseEnvironment (`src/harbor/environments/base.py`)
```python
class BaseEnvironment(ABC):
    @staticmethod
    @abstractmethod
    def type() -> EnvironmentType: ...

    @property
    @abstractmethod
    def is_mounted(self) -> bool: ...

    @abstractmethod
    async def start(self, force_build: bool) -> None: ...

    @abstractmethod
    async def stop(self, delete: bool) -> None: ...

    @abstractmethod
    async def exec(self, command: str, cwd: str | None = None,
                   env: dict[str, str] | None = None,
                   timeout_sec: int | None = None) -> ExecResult: ...

    @abstractmethod
    async def upload_file/dir(...) -> None: ...

    @abstractmethod
    async def download_file/dir(...) -> None: ...
```

---

## 2. Harbor如何实现测试用例并行执行的原理和机制

### 2.1 两种Orchestrator实现对比

| 特性 | LocalOrchestrator | QueueOrchestrator |
|------|-------------------|-------------------|
| 并发模式 | Semaphore信号量 | Producer-Consumer队列 |
| 任务创建 | 一次性创建所有任务 | Worker按需消费 |
| 适用场景 | 固定任务集 | 动态任务提交 |
| 代码位置 | `orchestrators/local.py` | `orchestrators/queue.py` |

### 2.2 LocalOrchestrator (信号量模式)

```python
# src/harbor/orchestrators/local.py
class LocalOrchestrator(BaseOrchestrator):
    async def run(self) -> list[TrialResult]:
        semaphore = asyncio.Semaphore(self._n_concurrent_trials)

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    self._run_trial(semaphore, trial_config, ...)
                )
                for trial_config in self._trial_configs
            ]

        return [task.result() for task in tasks]

    async def _run_trial(self, semaphore, trial_config, ...):
        async with semaphore:
            # 并发控制 - 最多n_concurrent_trials个同时运行
            result = await trial.run()
        return result
```

**特点**:
- 使用 `asyncio.Semaphore` 限制并发数
- `TaskGroup` 管理任务生命周期和异常传播
- 所有任务在启动时创建，但执行受信号量限制

### 2.3 QueueOrchestrator (生产者-消费者模式)

```python
# src/harbor/orchestrators/queue.py
class QueueOrchestrator(BaseOrchestrator):
    CONTAINER_LAUNCH_GRACE_PERIOD_SEC = 2  # 容器启动间隔

    async def run(self) -> list[TrialResult]:
        # 启动固定数量的worker
        for _ in range(self._n_concurrent_trials):
            worker = asyncio.create_task(self._worker())
            self._workers.append(worker)

        # 提交所有trial到队列
        for trial_config in self._trial_configs:
            await self._queue.put(trial_config)

        # 等待所有worker完成
        await self._queue.join()

    async def _worker(self):
        while True:
            trial_config = await self._queue.get()
            try:
                # 2秒grace period防止系统过载
                async with self._container_launch_lock:
                    await asyncio.sleep(self.CONTAINER_LAUNCH_GRACE_PERIOD_SEC)
                result = await self._run_trial(trial_config)
            finally:
                self._queue.task_done()
```

**特点**:
- 生产者-消费者模式，动态任务分发
- Worker池可复用，适合长时间运行
- Grace Period防止容器启动风暴

### 2.4 并发控制机制汇总

| 机制 | 用途 | 位置 | 实现方式 |
|------|------|------|----------|
| `asyncio.Semaphore` | 限制并发Trial数量 | LocalOrchestrator | `async with semaphore:` |
| `asyncio.Queue` | 动态任务分发 | QueueOrchestrator | `await queue.get()` |
| `asyncio.Lock` | 防止同时构建相同镜像 | DockerEnvironment | 类级别锁字典 |
| Grace Period (2s) | 防止容器启动风暴 | QueueOrchestrator | `asyncio.sleep(2)` |
| TaskGroup | 任务管理和取消传播 | 两种Orchestrator | `async with TaskGroup()` |

### 2.5 重试机制

```python
# src/harbor/orchestrators/local.py
class RetryConfig(BaseModel):
    max_retries: int = 0
    retry_exceptions: list[str] = Field(default_factory=list)
    no_retry_exceptions: list[str] = Field(default_factory=list)
    base_delay_sec: float = 1.0
    max_delay_sec: float = 60.0
    exponential_base: float = 2.0

    def _calculate_backoff_delay(self, attempt: int) -> float:
        delay = self.base_delay_sec * (self.exponential_base ** attempt)
        return min(delay, self.max_delay_sec)
```

### 2.6 CLI配置并发数

```bash
# 通过CLI设置并发数
harbor run --n-concurrent 8 --dataset terminal-bench@2.0 --agent claude-code

# 通过配置文件
# config.yaml
orchestrator:
  type: QUEUE
  n_concurrent_trials: 8
```

---

## 3. Harbor如何获取测试用例执行的评测结果

### 3.1 Verifier工作流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        Verifier.verify()                        │
│                    src/harbor/verifier/verifier.py              │
└─────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐      ┌───────────────┐      ┌───────────────┐
│ 1. Upload     │      │ 2. Execute    │      │ 3. Download   │
│ tests/ dir    │ ───► │ test.sh       │ ───► │ verifier dir  │
│ to env        │      │ in env        │      │ (if not mount)│
└───────────────┘      └───────────────┘      └───────────────┘
                                                      │
                                                      ▼
                                              ┌───────────────┐
                                              │ 4. Parse      │
                                              │ reward file   │
                                              └───────────────┘
                                                      │
                                                      ▼
                                              ┌───────────────┐
                                              │ VerifierResult│
                                              └───────────────┘
```

### 3.2 Verifier核心代码

```python
# src/harbor/verifier/verifier.py
class Verifier:
    async def verify(self) -> VerifierResult:
        # 1. 上传测试目录到环境
        await self._environment.upload_dir(
            source_dir=self._task.paths.tests_dir,
            target_dir="/tests",
        )

        # 2. 执行测试脚本
        test_script_path = shlex.quote(str(EnvironmentPaths.tests_dir / ...))
        test_stdout_path = shlex.quote(str(EnvironmentPaths.verifier_dir / ...))

        await self._environment.exec(
            command=f"{test_script_path} > {test_stdout_path} 2>&1",
            env=env,
        )

        # 3. 下载verifier目录(非挂载环境)
        if not self._environment.is_mounted:
            await self._environment.download_dir(
                source_dir=str(EnvironmentPaths.verifier_dir),
                target_dir=self._trial_paths.verifier_dir,
            )

        # 4. 解析reward文件
        if self._trial_paths.reward_text_path.exists():
            rewards = self._parse_reward_text()
        elif self._trial_paths.reward_json_path.exists():
            rewards = self._parse_reward_json()
        else:
            raise RewardFileNotFoundError(...)

        return VerifierResult(rewards=rewards)
```

### 3.3 Reward文件格式

#### 简单文本格式 (`/logs/verifier/reward.txt`)
```
0.85
```

解析代码:
```python
def _parse_reward_text(self) -> dict[str, float | int]:
    return {"reward": float(self._trial_paths.reward_text_path.read_text())}
```

#### JSON格式 (`/logs/verifier/reward.json`)
```json
{
  "accuracy": 0.85,
  "coverage": 0.92,
  "pass_rate": 1.0,
  "custom_metric": 100
}
```

解析代码:
```python
def _parse_reward_json(self) -> dict[str, float | int]:
    return json.loads(self._trial_paths.reward_json_path.read_text())
```

### 3.4 结果流转路径

```
Task Environment                    Harbor Host
┌──────────────────┐               ┌──────────────────┐
│ /tests/test.sh   │               │ TrialPaths       │
│       ↓          │               │                  │
│ 执行测试脚本      │               │ verifier_dir/    │
│       ↓          │    download   │ ├── test-stdout  │
│ /logs/verifier/  │ ────────────► │ ├── reward.txt   │
│ ├── reward.txt   │               │ └── reward.json  │
│ └── test-stdout  │               │        ↓         │
└──────────────────┘               │ VerifierResult   │
                                   │        ↓         │
                                   │ TrialResult      │
                                   │        ↓         │
                                   │ JobResult        │
                                   └──────────────────┘
```

### 3.5 数据模型

```python
# src/harbor/models/verifier/result.py
class VerifierResult(BaseModel):
    rewards: dict[str, float | int] | None

# src/harbor/models/trial/result.py
class TrialResult(BaseModel):
    task_name: str
    trial_name: str
    trial_uri: str

    # 时间信息
    environment_setup: float | None
    agent_setup: float | None
    agent_execution: float | None
    verifier: float | None

    # 结果
    agent_result: AgentContext | None
    verifier_result: VerifierResult | None

    # 异常信息
    exception: str | None
    exception_tb: str | None

# src/harbor/models/job/result.py
class JobResult(BaseModel):
    job_name: str
    job_uri: str
    stats: JobStats
    trial_results: dict[str, TrialResult]
    metrics: dict[str, dict[str, float | int]]
```

### 3.6 Metrics聚合

```python
# src/harbor/metrics/
class BaseMetric(ABC, Generic[T]):
    @abstractmethod
    def add(self, value: float) -> None: ...

    @abstractmethod
    def result(self) -> T: ...

# 内置实现
class Mean(BaseMetric[float]): ...
class Sum(BaseMetric[float]): ...
class Min(BaseMetric[float]): ...
class Max(BaseMetric[float]): ...
```

---

## 4. Harbor本身运行的时候可以获取到的日志类型

### 4.1 ATIF轨迹格式 (Agent Trajectory Interchange Format)

Harbor使用 **ATIF-v1.6** 作为标准轨迹格式，定义在 `src/harbor/models/trajectories/`。

```python
class Trajectory(BaseModel):
    """标准化的Agent交互轨迹"""
    trajectory_id: str
    agent_info: AgentInfo
    context: list[ContextStep]        # 完整交互历史
    available_tools: list[ToolInfo]   # 可用工具定义
    metrics: TrajectoryMetrics        # Token使用、成本
    extra: dict[str, Any]             # 自定义元数据

class ContextStep(BaseModel):
    """单步交互"""
    step_id: str
    step_type: StepType  # SYSTEM, USER, AGENT
    content: list[ContentPart]  # 支持多模态
    tool_calls: list[ToolCall] | None
    observations: list[Observation] | None
    timestamp: str  # ISO 8601

class TrajectoryMetrics(BaseModel):
    """Token和成本追踪"""
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_cost_usd: float
```

### 4.2 各Agent日志收集详情

| Agent | 日志位置 | 日志格式 | 收集内容 |
|-------|----------|----------|----------|
| **Claude Code** | `logs/sessions/projects/*/` | JSONL | 消息事件、工具调用、Token使用、reasoning effort |
| **OpenCode** | `opencode.txt` | JSON Lines | text/tool_use/step_start/step_finish/error事件 |
| **OpenHands** | `logs/events/*.json`, `logs/completions/*.json` | JSON | 结构化事件、原始LLM响应 |
| **Codex** | `codex.txt` | 文本/JSON | stdout捕获、会话目录 |
| **Aider** | `aider.txt` | 文本 | 交互历史 |
| **Gemini CLI** | `gemini.txt` | 文本 | 输出日志 |

### 4.3 日志目录结构

```
logs/
├── agent/
│   ├── trajectory.json        # ATIF标准格式轨迹
│   ├── agent.txt              # 原始agent输出
│   └── sessions/              # Claude Code会话日志
│       └── projects/*/
│           └── *.jsonl
├── environment/               # 环境执行日志
│   ├── setup.log
│   └── exec.log
└── verifier/                  # 验证日志
    ├── test-stdout.txt        # 测试标准输出
    ├── test-stderr.txt        # 测试错误输出
    ├── reward.txt             # 简单分值
    └── reward.json            # 结构化分值
```

### 4.4 收集的数据类型

#### 内容日志
- Agent消息和推理过程
- 用户指令和系统提示
- 工具调用参数和结果
- 观察输出 (stdout/stderr, 文件内容)

#### 指标与性能
- Token使用量 (prompt, completion, cached)
- 成本追踪 (USD)
- 模型特定指标
- 执行状态和错误码
- Reasoning effort级别

#### 上下文信息
- Agent名称、版本、模型
- Session和Run ID
- 时间戳 (ISO 8601格式)
- 可用工具定义

#### 多模态支持 (v1.6+)
- 消息和观察中的图像内容
- ContentPart schema支持混合媒体
- 图像文件存储在单独目录

### 4.5 Agent的ATIF支持标志

```python
# src/harbor/agents/base.py
class BaseAgent(ABC):
    # 子类应覆盖此标志表明支持ATIF格式
    SUPPORTS_ATIF: bool = False

# 示例: Claude Code支持ATIF
class ClaudeCodeAgent(BaseInstalledAgent):
    SUPPORTS_ATIF = True
```

### 4.6 CLI日志查看命令

```bash
# 导出轨迹到数据集格式
harbor traces export --path /path/to/trials --recursive

# 在Web查看器中查看
harbor view /path/to/jobs

# 导出为ShareGPT格式
harbor traces export --to-sharegpt --push

# 按成功/失败过滤
harbor traces export --filter-success-only
```

---

## 5. Harbor的架构的扩展性分析结论

### 5.1 扩展性设计模式

```
┌─────────────────────────────────────────────────────────────────┐
│                    Factory Pattern                               │
│  ┌───────────────┐ ┌────────────────┐ ┌──────────────────┐     │
│  │ AgentFactory  │ │ EnvFactory     │ │OrchestratorFactory│     │
│  └───────────────┘ └────────────────┘ └──────────────────┘     │
│           │                 │                  │                │
│           ▼                 ▼                  ▼                │
│  支持import_path    资源覆盖配置       类型选择实例化            │
│  动态加载                                                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   Abstract Base Classes                          │
│  ┌───────────────┐ ┌────────────────┐ ┌──────────────────┐     │
│  │  BaseAgent    │ │BaseEnvironment │ │BaseOrchestrator  │     │
│  └───────────────┘ └────────────────┘ └──────────────────┘     │
│           │                 │                  │                │
│           └─────────────────┴──────────────────┘                │
│                             │                                    │
│                             ▼                                    │
│                     清晰的接口契约                               │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 添加新组件的步骤

#### 新增Agent

```python
# 步骤1: 实现BaseAgent接口
# src/harbor/agents/installed/my_agent.py
class MyAgent(BaseAgent):
    SUPPORTS_ATIF = True  # 如果支持ATIF格式

    @staticmethod
    def name() -> str:
        return "my-agent"

    def version(self) -> str | None:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        # 安装依赖、配置MCP服务器等
        await environment.exec("pip install my-agent-tool")

    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        # 执行agent逻辑
        result = await environment.exec(f"my-agent run {instruction}")
        context.output = result.stdout

# 步骤2: 注册到factory
# src/harbor/agents/factory.py
_AGENTS = [
    ...,
    MyAgent,
]

# 步骤3: 添加到枚举
# src/harbor/models/agent/name.py
class AgentName(str, Enum):
    ...
    MY_AGENT = "my-agent"

# 步骤4: (可选) 创建安装模板
# src/harbor/agents/installed/install-my-agent.sh.j2
#!/bin/bash
pip install my-agent=={{ version }}
```

#### 新增Environment

```python
# 步骤1: 实现BaseEnvironment接口
# src/harbor/environments/my_env.py
class MyEnvironment(BaseEnvironment):
    @staticmethod
    def type() -> EnvironmentType:
        return EnvironmentType.MY_ENV

    @property
    def is_mounted(self) -> bool:
        return False

    @property
    def supports_gpus(self) -> bool:
        return True

    @property
    def can_disable_internet(self) -> bool:
        return True

    def _validate_definition(self) -> None:
        # 验证环境定义文件
        pass

    async def start(self, force_build: bool) -> None:
        # 启动环境
        pass

    async def stop(self, delete: bool) -> None:
        # 停止环境
        pass

    async def exec(self, command: str, cwd: str | None = None,
                   env: dict[str, str] | None = None,
                   timeout_sec: int | None = None) -> ExecResult:
        # 执行命令
        pass

    async def upload_file(self, source_path: Path | str,
                          target_path: str) -> None:
        pass

    async def upload_dir(self, source_dir: Path | str,
                         target_dir: str) -> None:
        pass

    async def download_file(self, source_path: str,
                            target_path: Path | str) -> None:
        pass

    async def download_dir(self, source_dir: str,
                           target_dir: Path | str) -> None:
        pass

# 步骤2: 添加到_ENVIRONMENTS列表
# src/harbor/environments/factory.py
_ENVIRONMENTS = [
    ...,
    MyEnvironment,
]

# 步骤3: 添加到枚举
# src/harbor/models/environment_type.py
class EnvironmentType(str, Enum):
    ...
    MY_ENV = "my-env"
```

#### 新增Benchmark适配器

```
adapters/my_benchmark/
├── adapter.py           # 主转换逻辑
├── run_adapter.py       # CLI入口
├── README.md            # 使用文档
└── template/            # 任务模板
    ├── task.toml
    ├── instruction.md.j2
    ├── environment/
    │   └── Dockerfile.j2
    └── tests/
        └── test.sh
```

### 5.3 扩展性评分 (1-5分)

| 维度 | 评分 | 说明 |
|------|------|------|
| **Agent扩展** | ⭐⭐⭐⭐⭐ 5/5 | 工厂模式 + 模板系统，支持import_path动态加载 |
| **Environment扩展** | ⭐⭐⭐⭐⭐ 5/5 | 清晰的抽象接口，7种内置实现作为参考 |
| **Orchestrator扩展** | ⭐⭐⭐⭐ 4/5 | 简单的注册模式，需要理解async调度 |
| **Benchmark适配** | ⭐⭐⭐⭐⭐ 5/5 | 20+适配器示例，标准化模板结构 |
| **配置灵活性** | ⭐⭐⭐⭐⭐ 5/5 | Pydantic验证 + 多源配置 + 环境变量 |
| **Hook/事件系统** | ⭐⭐⭐⭐ 4/5 | TrialEvent钩子，支持自定义回调 |
| **Metrics扩展** | ⭐⭐⭐⭐ 4/5 | BaseMetric抽象，内置常用指标 |

### 5.4 扩展性优点

| 优点 | 说明 |
|------|------|
| **清晰的抽象边界** | Agent/Environment/Orchestrator各自独立，职责明确 |
| **工厂模式** | 统一的组件创建入口，支持动态加载 |
| **模板系统** | Jinja2模板简化安装脚本配置，支持变量替换 |
| **Pydantic验证** | 类型安全的配置管理，自动校验和序列化 |
| **Hook系统** | 事件驱动的扩展点，可在关键节点注入自定义逻辑 |
| **ATIF标准** | 统一的轨迹格式，便于跨Agent分析 |

### 5.5 潜在改进空间

| 改进方向 | 当前状态 | 建议改进 |
|----------|----------|----------|
| **插件注册表** | 需修改factory文件 | 装饰器自动注册 `@register_agent` |
| **配置热加载** | 启动时固定 | 支持运行时配置更新 |
| **分布式Orchestrator** | 仅支持单机 | 支持分布式调度 (Ray, Dask) |
| **插件发现** | 手动添加到列表 | 自动扫描指定目录 |
| **依赖注入** | 构造函数传参 | 使用DI框架简化 |

### 5.6 内置组件统计

| 组件类型 | 数量 | 示例 |
|----------|------|------|
| **Agents** | 20+ | claude-code, openhands, aider, codex, goose, gemini-cli, cursor-cli, cline, mini-swe-agent, terminus, oracle, nop |
| **Environments** | 7 | docker, daytona, modal, e2b, gke, runloop |
| **Orchestrators** | 2 | local, queue |
| **Adapters** | 20+ | swebench, aider_polyglot, terminal-bench, aime, gpqa-diamond, usaco, mmau |
| **Metrics** | 5+ | Mean, Sum, Min, Max, UvScript |

### 5.7 总体评价

Harbor的架构设计具有良好的扩展性，采用经典的**工厂模式**和**抽象基类**设计，使得添加新的Agent、Environment或Benchmark适配器非常直接。

**核心优势**:
1. **ATIF标准化轨迹格式**保证了数据一致性
2. **Pydantic数据模型**提供了类型安全和自动验证
3. **Jinja2模板系统**简化了配置管理
4. **Hook系统**提供了灵活的扩展点

**整体架构**在保持简洁的同时提供了足够的灵活性，适合作为AI Agent评估的通用框架。扩展新组件主要遵循"实现接口 → 注册到工厂"的模式，学习曲线平缓。

---

## 附录

### A. 关键文件路径速查

```
src/harbor/
├── agents/
│   ├── base.py              # BaseAgent抽象类
│   ├── factory.py           # Agent工厂
│   └── installed/           # 内置Agent实现
├── environments/
│   ├── base.py              # BaseEnvironment抽象类
│   ├── factory.py           # Environment工厂
│   └── docker/              # Docker环境实现
├── orchestrators/
│   ├── base.py              # BaseOrchestrator抽象类
│   ├── local.py             # 本地并行执行
│   └── queue.py             # 队列式执行
├── verifier/
│   └── verifier.py          # 验证器
├── models/
│   ├── agent/               # Agent相关模型
│   ├── job/                 # Job相关模型
│   ├── trial/               # Trial相关模型
│   ├── trajectories/        # ATIF轨迹模型
│   └── verifier/            # 验证结果模型
├── metrics/
│   └── base.py              # 指标基类
└── cli/
    ├── main.py              # CLI入口
    └── jobs.py              # Job命令
```

### B. 常用CLI命令

```bash
# 运行评估
harbor run --dataset terminal-bench@2.0 --agent claude-code --model anthropic/claude-opus-4-1 --n-concurrent 4

# 列出数据集
harbor datasets list

# 查看结果
harbor view /path/to/jobs

# 导出轨迹
harbor traces export --path /path/to/trials --recursive
```

### C. 参考资源

- 项目仓库: https://github.com/laude-institute/harbor
- ATIF规范: `docs/rfcs/`
- Adapter文档: `adapters/*/README.md`
