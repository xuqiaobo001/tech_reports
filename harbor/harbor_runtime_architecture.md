# Harbor 运行态部署架构图

> 基于Harbor源码分析的运行时架构可视化

---

## 1. 整体运行态架构

```mermaid
graph TB
    subgraph "User Layer"
        CLI[CLI Client<br/>harbor run]
    end

    subgraph "Harbor Host Machine"
        subgraph "Orchestration Layer"
            JOB[Job<br/>job.py]
            ORCH[Orchestrator<br/>LocalOrchestrator/QueueOrchestrator]
        end

        subgraph "Trial Execution"
            TRIAL[Trial Runner<br/>trial.py]
            AGENT[Agent<br/>claude-code/openhands/etc.]
            VERIFIER[Verifier<br/>verifier.py]
        end

        subgraph "Data Layer"
            CONFIG[(JobConfig<br/>TrialConfig)]
            RESULT[(TrialResult<br/>JobResult)]
            LOGS[(Logs & Traces<br/>ATIF)]
        end
    end

    subgraph "Environment Providers"
        DOCKER[Docker<br/>Local Containers]
        MODAL[Modal<br/>Cloud Sandbox]
        DAYTONA[Daytona<br/>Cloud Dev Env]
        E2B[E2B<br/>Sandbox]
    end

    CLI --> JOB
    JOB --> CONFIG
    JOB --> ORCH
    ORCH --> TRIAL
    TRIAL --> AGENT
    TRIAL --> VERIFIER
    AGENT --> RESULT
    VERIFIER --> RESULT
    TRIAL --> LOGS

    AGENT -.->|Provision| DOCKER
    AGENT -.->|Provision| MODAL
    AGENT -.->|Provision| DAYTONA
    AGENT -.->|Provision| E2B

    VERIFIER -.->|Execute Tests| DOCKER
    VERIFIER -.->|Execute Tests| MODAL
```

---

## 2. 单个Trial执行流程

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant T as Trial
    participant E as Environment
    participant A as Agent
    participant V as Verifier
    participant FS as FileSystem

    O->>T: start_trial(config)

    rect rgb(200, 220, 240)
        Note over T,E: Phase 1: Environment Setup
        T->>E: start(force_build=False)
        E->>E: _validate_definition()
        E->>E: _validate_gpu_support()
        E-->>T: environment_ready
    end

    rect rgb(220, 240, 200)
        Note over T,A: Phase 2: Agent Setup
        T->>A: setup(environment)
        A->>E: exec(install commands)
        A->>E: upload_file(mcp_config)
        A-->>T: agent_ready
    end

    rect rgb(240, 220, 200)
        Note over T,A: Phase 3: Agent Execution
        T->>A: run(instruction, environment, context)
        A->>E: exec(agent commands)
        E-->>A: output
        A->>FS: write trajectory.json
        A-->>T: execution_complete
    end

    rect rgb(240, 200, 220)
        Note over T,V: Phase 4: Verification
        T->>V: verify()
        V->>E: upload_dir(tests/)
        V->>E: exec(test.sh)
        V->>E: download_dir(/logs/verifier/)
        V->>FS: read reward.txt/json
        V-->>T: VerifierResult
    end

    T->>FS: save TrialResult
    T-->>O: TrialResult
```

---

## 3. 并行执行架构 (LocalOrchestrator)

```mermaid
graph TB
    subgraph "LocalOrchestrator"
        SEM[Semaphore<br/>n_concurrent_trials]
        TG[TaskGroup]

        subgraph "Concurrent Trials"
            T1[Trial 1]
            T2[Trial 2]
            T3[Trial 3]
            TN[Trial N...]
        end
    end

    subgraph "Environments"
        E1[Env 1<br/>Docker Container]
        E2[Env 2<br/>Docker Container]
        E3[Env 3<br/>Docker Container]
        EN[Env N<br/>Docker Container]
    end

    SEM -->|controls concurrency| TG
    TG --> T1
    TG --> T2
    TG --> T3
    TG --> TN

    T1 --> E1
    T2 --> E2
    T3 --> E3
    TN --> EN

    style SEM fill:#ff9999
    style TG fill:#99ff99
```

---

## 4. 并行执行架构 (QueueOrchestrator)

```mermaid
graph TB
    subgraph "QueueOrchestrator"
        Q[AsyncIO Queue<br/>Trial Configs]
        LOCK[Container Launch Lock<br/>+ 2s Grace Period]

        subgraph "Worker Pool"
            W1[Worker 1]
            W2[Worker 2]
            W3[Worker 3]
            WN[Worker N]
        end
    end

    subgraph "Trials"
        T1[Trial A]
        T2[Trial B]
        T3[Trial C]
    end

    Q -->|dequeue| W1
    Q -->|dequeue| W2
    Q -->|dequeue| W3
    Q -->|dequeue| WN

    LOCK -->|serialize launches| W1
    LOCK -->|serialize launches| W2
    LOCK -->|serialize launches| W3
    LOCK -->|serialize launches| WN

    W1 --> T1
    W2 --> T2
    W3 --> T3

    style Q fill:#ffcc99
    style LOCK fill:#ff9999
```

---

## 5. 环境提供者架构

```mermaid
graph TB
    subgraph "BaseEnvironment Interface"
        BASE[BaseEnvironment<br/>Abstract Class]
        BASE --> |start/stop| OPS[Container Operations]
        BASE --> |exec| CMD[Command Execution]
        BASE --> |upload/download| FILES[File Transfer]
    end

    subgraph "Local"
        DOCKER[DockerEnvironment]
        DOCKER --> DOCKERD[dockerd<br/>Docker Daemon]
        DOCKERD --> CONTAINERS[Containers]
    end

    subgraph "Cloud Providers"
        MODAL[ModalEnvironment]
        DAYTONA[DaytonaEnvironment]
        E2B[E2BEnvironment]
        GKE[GKEEnvironment]
        RUNLOOP[RunloopEnvironment]
    end

    MODAL --> MODAL_API[Modal API]
    DAYTONA --> DAYTONA_API[Daytona API]
    E2B --> E2B_API[E2B API]
    GKE --> GKE_API[GKE/K8s API]
    RUNLOOP --> RUNLOOP_API[Runloop API]

    BASE -.->|implements| DOCKER
    BASE -.->|implements| MODAL
    BASE -.->|implements| DAYTONA
    BASE -.->|implements| E2B
    BASE -.->|implements| GKE
    BASE -.->|implements| RUNLOOP

    style BASE fill:#e1f5fe
```

---

## 6. Agent执行架构

```mermaid
graph TB
    subgraph "BaseAgent Interface"
        BASE[BaseAgent]
        BASE --> SETUP[setup env]
        BASE --> RUN[run instruction]
        BASE --> ATIF[SUPPORTS_ATIF]
    end

    subgraph "Installed Agents"
        CC[Claude Code]
        OH[OpenHands]
        AIDER[Aider]
        CODEX[Codex]
        GOOSE[Goose]
        GEMINI[Gemini CLI]
    end

    subgraph "Internal Agents"
        TERM[Terminus]
        ORACLE[Oracle]
        NOP[Nop]
    end

    subgraph "Agent Runtime"
        INSTALL[Install Script<br/>.sh.j2 Template]
        MCP[MCP Server Config]
        SKILLS[Skills Directory]
        EXEC[CLI Execution]
    end

    BASE -.->|extends| CC
    BASE -.->|extends| OH
    BASE -.->|extends| AIDER
    BASE -.->|extends| CODEX
    BASE -.->|extends| GOOSE
    BASE -.->|extends| GEMINI
    BASE -.->|extends| TERM

    CC --> INSTALL
    CC --> MCP
    CC --> SKILLS
    CC --> EXEC

    style BASE fill:#fff3e0
```

---

## 7. 验证器架构

```mermaid
graph TB
    subgraph "Verifier Flow"
        V[Verifier]
        UPLOAD[Upload tests/]
        EXEC[Execute test.sh]
        DOWNLOAD[Download /logs/verifier/]
        PARSE[Parse Reward]
    end

    subgraph "Environment"
        ENV[Container/Sandbox]
        TESTDIR[/tests/]
        SCRIPT[test.sh]
        REWARD[/logs/verifier/]
    end

    subgraph "Output"
        TXT[reward.txt<br/>0.85]
        JSON[reward.json<br/>{accuracy: 0.85}]
        STDOUT[test-stdout.txt]
    end

    V --> UPLOAD
    UPLOAD --> TESTDIR
    TESTDIR --> SCRIPT
    SCRIPT --> EXEC
    EXEC --> REWARD
    REWARD --> DOWNLOAD
    DOWNLOAD --> PARSE
    PARSE --> TXT
    PARSE --> JSON
    PARSE --> STDOUT

    style V fill:#e8f5e9
    style REWARD fill:#fff8e1
```

---

## 8. 数据流架构

```mermaid
graph LR
    subgraph "Input"
        CLI[CLI Args]
        TASK[Task Definition<br/>task.toml]
        DS[Dataset Registry]
    end

    subgraph "Configuration"
        JC[JobConfig]
        TC[TrialConfig]
    end

    subgraph "Execution"
        CTX[AgentContext]
        TRAJ[Trajectory<br/>ATIF Format]
    end

    subgraph "Output"
        VR[VerifierResult]
        TR[TrialResult]
        JR[JobResult]
        METRICS[Aggregated Metrics]
    end

    CLI --> JC
    TASK --> TC
    DS --> JC
    JC --> TC
    TC --> CTX
    CTX --> TRAJ
    CTX --> VR
    VR --> TR
    TR --> JR
    JR --> METRICS

    style JC fill:#e3f2fd
    style TC fill:#e3f2fd
    style JR fill:#e8f5e9
    style METRICS fill:#e8f5e9
```

---

## 9. 目录结构映射

```mermaid
graph TB
    subgraph "Harbor Workspace"
        ROOT[workspace/]

        subgraph "Jobs"
            JOBS[jobs/]
            JOB1[job_001/]
            JCONFIG[config.json]
            JRESULT[result.json]
        end

        subgraph "Trials"
            TRIALS[trials/]
            T1[task1__agent1__001/]
            T2[task2__agent1__001/]

            subgraph "Trial Contents"
                TCONFIG[trial_config.json]
                TRESULT[result.json]
                AGENT[agent/]
                TRAJ[trajectory.json]
                VER[verifier/]
                REWARD[reward.txt/json]
            end
        end

        subgraph "Tasks"
            TASKS[tasks/]
            TASK1[task1/]
            TASKCFG[task.toml]
            INST[instruction.md]
            ENV[environment/]
            TESTS[tests/]
        end
    end

    ROOT --> JOBS
    ROOT --> TRIALS
    ROOT --> TASKS

    JOBS --> JOB1
    JOB1 --> JCONFIG
    JOB1 --> JRESULT

    TRIALS --> T1
    TRIALS --> T2
    T1 --> TCONFIG
    T1 --> TRESULT
    T1 --> AGENT
    T1 --> VER
    AGENT --> TRAJ
    VER --> REWARD

    TASKS --> TASK1
    TASK1 --> TASKCFG
    TASK1 --> INST
    TASK1 --> ENV
    TASK1 --> TESTS
```

---

## 10. 典型部署场景

### 场景A: 本地开发测试

```mermaid
graph TB
    subgraph "Developer Machine"
        CLI[harbor CLI]
        ORCH[LocalOrchestrator]
        DOCKER[Docker Desktop]

        subgraph "Containers"
            C1[Task Container 1]
            C2[Task Container 2]
            C3[Task Container 3]
            C4[Task Container 4]
        end
    end

    CLI --> ORCH
    ORCH -->|Semaphore(4)| DOCKER
    DOCKER --> C1
    DOCKER --> C2
    DOCKER --> C3
    DOCKER --> C4

    style DOCKER fill:#2496ed,color:white
```

### 场景B: 大规模云端评估

```mermaid
graph TB
    subgraph "Control Plane"
        CLI[harbor CLI]
        ORCH[QueueOrchestrator]
    end

    subgraph "Modal Cloud"
        M1[Sandbox 1]
        M2[Sandbox 2]
        M3[Sandbox 3]
        MN[Sandbox N...]
    end

    subgraph "Daytona Cloud"
        D1[Workspace 1]
        D2[Workspace 2]
    end

    CLI --> ORCH
    ORCH -->|Queue| M1
    ORCH -->|Queue| M2
    ORCH -->|Queue| M3
    ORCH -->|Queue| MN
    ORCH -->|Queue| D1
    ORCH -->|Queue| D2

    style ORCH fill:#ff9800,color:white
    style M1 fill:#000,color:white
    style M2 fill:#000,color:white
    style M3 fill:#000,color:white
```

---

## 11. 关键组件交互图

```mermaid
graph TB
    subgraph "Harbor Framework"
        CLI[harbor run]

        subgraph "Core"
            JOB[Job]
            ORCH[Orchestrator]
            TRIAL[Trial]
        end

        subgraph "Interfaces"
            AGENT_I[BaseAgent]
            ENV_I[BaseEnvironment]
            VER_I[Verifier]
            METRIC_I[BaseMetric]
        end

        subgraph "Implementations"
            AGENTS[Claude Code<br/>OpenHands<br/>Aider...]
            ENVS[Docker<br/>Modal<br/>Daytona...]
            METRICS[Mean<br/>Sum<br/>Max...]
        end

        subgraph "Data"
            CONFIG[Config Models]
            RESULT[Result Models]
            TRAJ[Trajectory Models]
        end
    end

    CLI --> JOB
    JOB --> ORCH
    ORCH --> TRIAL
    TRIAL --> AGENT_I
    TRIAL --> ENV_I
    TRIAL --> VER_I

    AGENT_I -.->|implements| AGENTS
    ENV_I -.->|implements| ENVS

    JOB --> CONFIG
    TRIAL --> RESULT
    AGENT_I --> TRAJ
    JOB --> METRIC_I
    METRIC_I -.->|implements| METRICS

    style CLI fill:#7c4dff,color:white
    style JOB fill:#448aff,color:white
    style ORCH fill:#448aff,color:white
    style TRIAL fill:#448aff,color:white
```

---

## 总结

| 组件 | 职责 | 可扩展点 |
|------|------|----------|
| **CLI** | 命令解析、入口 | 自定义命令 |
| **Job** | 配置管理、结果聚合 | JobConfig扩展 |
| **Orchestrator** | 并行调度 | 新调度策略 |
| **Trial** | 单次执行编排 | Hook机制 |
| **Agent** | 任务执行 | 新Agent实现 |
| **Environment** | 容器/沙箱管理 | 新环境提供者 |
| **Verifier** | 结果验证 | 自定义测试脚本 |
| **Metrics** | 指标聚合 | 新Metric类型 |
