# Harbor Agent API Reference

## BaseAgent (`harbor.agents.base`)

```python
class BaseAgent(ABC):
    logs_dir: Path
    model_name: str | None
    logger: logging.Logger
    mcp_servers: list[MCPServerConfig]
    SUPPORTS_ATIF: bool = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        *args, **kwargs,
    ): ...

    # Parsed from model_name (e.g. "anthropic/claude-opus-4-1")
    _parsed_model_provider: str | None
    _parsed_model_name: str | None

    def to_agent_info(self) -> AgentInfo: ...

    @staticmethod
    @abstractmethod
    def name() -> str: ...

    @abstractmethod
    def version(self) -> str | None: ...

    @classmethod
    def import_path(cls) -> str: ...

    @abstractmethod
    async def setup(self, environment: BaseEnvironment) -> None: ...

    @abstractmethod
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None: ...
```

## BaseInstalledAgent (`harbor.agents.installed.base`)

```python
class ExecInput(BaseModel):
    command: str
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_sec: int | None = None

class BaseInstalledAgent(BaseAgent, ABC):
    def __init__(
        self,
        logs_dir: Path,
        prompt_template_path: Path | str | None = None,
        version: str | None = None,
        *args, **kwargs,
    ): ...

    @property
    @abstractmethod
    def _install_agent_template_path(self) -> Path: ...

    @abstractmethod
    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]: ...

    @property
    def _template_variables(self) -> dict[str, str]: ...

    @abstractmethod
    def populate_context_post_run(self, context: AgentContext) -> None: ...

    def version(self) -> str | None: ...

    async def setup(self, environment: BaseEnvironment) -> None:
        """
        1. Creates ~/.bash_profile sourcing ~/.bashrc
        2. Creates /installed-agent/ directory
        3. Renders Jinja2 install template with _template_variables
        4. Uploads and executes install.sh in container
        5. Saves return code, stdout, stderr to logs_dir/setup/
        """

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        """
        1. Optionally renders prompt template
        2. Iterates over create_run_agent_commands() results
        3. Executes each command via environment.exec()
        4. Saves return code, stdout, stderr to logs_dir/command-{i}/
        5. Calls populate_context_post_run()
        """
```

## AgentFactory (`harbor.agents.factory`)

```python
class AgentFactory:
    _AGENTS: list[type[BaseAgent]] = [...]
    _AGENT_MAP: dict[AgentName, type[BaseAgent]] = {AgentName(a.name()): a for a in _AGENTS}

    @classmethod
    def create_agent_from_name(cls, name: AgentName, logs_dir: Path, model_name: str | None = None, **kwargs) -> BaseAgent: ...

    @classmethod
    def create_agent_from_import_path(cls, import_path: str, logs_dir: Path, model_name: str | None = None, **kwargs) -> BaseAgent:
        """import_path format: 'module.path:ClassName'"""

    @classmethod
    def create_agent_from_config(cls, config: AgentConfig, logs_dir: Path, **kwargs) -> BaseAgent: ...
```

## MCPServerConfig (`harbor.models.task.config`)

```python
class MCPServerConfig(BaseModel):
    name: str
    transport: str          # "sse" | "streamable-http" | "stdio"
    url: str | None = None  # For sse / streamable-http
    command: str | None = None  # For stdio
    args: list[str] | None = None  # For stdio
```

## AgentContext (`harbor.models.agent.context`)

Populated during and after agent execution. Contains trajectory data, token usage, and model information.

## AgentName Enum (`harbor.models.agent.name`)

Built-in agent names: `claude-code`, `openhands`, `aider`, `codex`, `goose`, `gemini-cli`, `qwen-coder`, `opencode`, `cursor-cli`, `cline-cli`, `mini-swe-agent`, `swe-agent`, `oracle`, `nop`, `terminus-2`.

## Test Fixtures (`tests/conftest.py`)

```python
@pytest.fixture
def temp_dir() -> Path: ...       # Temporary directory

@pytest.fixture
def mock_environment() -> AsyncMock: ...  # Mock environment (exec returns rc=0)

@pytest.fixture
def mock_logs_dir(temp_dir) -> Path: ...  # temp_dir / "logs"
```

## Test Markers

```python
@pytest.mark.unit         # Fast, no external dependencies
@pytest.mark.integration  # Requires external services (may be mocked)
@pytest.mark.runtime      # May need Docker
@pytest.mark.asyncio      # Async tests (auto mode enabled)
```
