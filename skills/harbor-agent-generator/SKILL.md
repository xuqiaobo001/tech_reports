# harbor-agent-generator

Guide for quickly generating Harbor-compatible Agent implementations for CLI tools. Use this skill when users want to integrate a new CLI-based AI agent (coding assistant, code generation tool, etc.) into the Harbor evaluation framework, or when they need to scaffold agent code that conforms to Harbor's BaseAgent / BaseInstalledAgent interfaces.

---

## Harbor Agent Architecture Overview

Harbor is a Python framework (3.12+, Pydantic v2, async/await) for evaluating AI agents in containerized environments. Agents are executed inside Docker (or cloud) containers and graded by verifier scripts.

### Core Interfaces

There are two base classes an agent can extend:

1. **`BaseAgent`** (`harbor.agents.base`) — low-level interface, full control.
2. **`BaseInstalledAgent`** (`harbor.agents.installed.base`) — higher-level, for agents installed via shell script and run via CLI commands. This is the recommended path for most CLI tools.

### BaseAgent Required Methods

```python
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

class MyAgent(BaseAgent):
    SUPPORTS_ATIF: bool = False  # Set True if you emit ATIF trajectory

    @staticmethod
    def name() -> str:
        """Unique agent identifier, e.g. 'my-agent'."""

    def version(self) -> str | None:
        """Semantic version or None."""

    async def setup(self, environment: BaseEnvironment) -> None:
        """Install agent and dependencies in the container.
        Register MCP servers from self.mcp_servers here if needed."""

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Execute the agent with the given instruction.
        Populate context with trajectory/results as the agent runs."""
```

### BaseInstalledAgent Required Methods (Recommended for CLI tools)

```python
from pathlib import Path
from harbor.agents.installed.base import BaseInstalledAgent, ExecInput

class MyCLIAgent(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = False

    @staticmethod
    def name() -> str:
        return "my-cli-agent"

    @property
    def _install_agent_template_path(self) -> Path:
        """Path to the Jinja2 install script template (.sh.j2)."""
        return Path(__file__).parent / "install-my-cli-agent.sh.j2"

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        """Return shell commands to execute the agent headlessly."""
        return [
            ExecInput(
                command=f'my-cli-agent run --headless --prompt "{instruction}"',
                cwd="/workdir",
            )
        ]

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Parse trajectory/log files and populate context after execution."""
        pass
```

---

## Step-by-Step: Generate a New Harbor Agent

When the user asks to create a Harbor agent for a CLI tool, follow these steps in order.

### Step 1 — Gather Agent Information

Ask the user for:
- **Agent name**: kebab-case identifier (e.g. `my-agent`)
- **Python class name**: PascalCase (e.g. `MyAgent`)
- **Installation method**: How to install the CLI tool (npm, pip, curl, apt, etc.)
- **Run command**: The headless/non-interactive command to execute the agent with a prompt
- **Version pinning**: Whether to pin a specific version (use Jinja2 `{{ version }}` variable)
- **ATIF support**: Whether the agent produces trajectory logs that can be parsed into ATIF format
- **MCP support**: Whether the agent supports MCP servers

### Step 2 — Create the Install Template

Create a Jinja2 shell script at `src/harbor/agents/installed/install-{agent-name}.sh.j2`:

```bash
#!/bin/bash
set -euo pipefail

# Install system dependencies
apt-get update
apt-get install -y curl git

# Install the agent CLI
# Example for npm-based tool:
npm install -g {agent-package}@{{ version }}

# Example for pip-based tool:
# pip install {agent-package}=={{ version }}

# Example for curl-based installer:
# curl -fsSL https://example.com/install.sh | bash

# Ensure agent binary is on PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

echo "INSTALL SUCCESS!"
```

Key rules for install templates:
- Always use `set -euo pipefail`
- Use `{{ version }}` Jinja2 variable for version pinning
- End with `echo "INSTALL SUCCESS!"` for log clarity
- Install all system dependencies (curl, git, node, etc.)
- Export PATH additions to `~/.bashrc`

### Step 3 — Create the Agent Python Module

Create `src/harbor/agents/installed/{agent_name}.py`:

```python
"""Harbor agent implementation for {AgentDisplayName}."""

from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext


class {ClassName}(BaseInstalledAgent):
    """Harbor agent wrapper for {agent-display-name} CLI."""

    SUPPORTS_ATIF: bool = False  # Set True if trajectory parsing is implemented

    @staticmethod
    def name() -> str:
        return "{agent-name}"

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).parent / "install-{agent-name}.sh.j2"

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        commands: list[ExecInput] = []

        # Optional: register MCP servers before running
        mcp_cmd = self._build_mcp_setup_command()
        if mcp_cmd:
            commands.append(ExecInput(command=mcp_cmd))

        # Main agent execution command
        commands.append(
            ExecInput(
                command=(
                    "{agent-cli-binary} run "
                    "--headless "
                    f'--prompt "{instruction}" '
                    "--output /logs/agent/"
                ),
                cwd="/workdir",
                env={
                    "MODEL_NAME": self._parsed_model_name or "",
                    "MODEL_PROVIDER": self._parsed_model_provider or "",
                },
            )
        )

        return commands

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Parse agent output logs into context. Implement ATIF parsing here."""
        pass
```

### Step 4 — Register the Agent

1. Add the agent name to the `AgentName` enum in `src/harbor/models/agent/name.py`:

```python
class AgentName(str, Enum):
    # ... existing agents ...
    MY_AGENT = "my-agent"
```

2. Import and register in `src/harbor/agents/factory.py`:

```python
from harbor.agents.installed.my_agent import MyAgent

class AgentFactory:
    _AGENTS: list[type[BaseAgent]] = [
        # ... existing agents ...
        MyAgent,
    ]
```

### Step 5 — Add MCP Server Support (if applicable)

If the agent supports MCP, implement a method to write the MCP configuration file the agent expects. Example pattern from Claude Code agent:

```python
def _build_mcp_setup_command(self) -> str | None:
    if not self.mcp_servers:
        return None

    mcp_config = {}
    for server in self.mcp_servers:
        if server.transport in ("sse", "streamable-http"):
            mcp_config[server.name] = {
                "type": "http" if server.transport == "streamable-http" else "sse",
                "url": server.url,
            }
        elif server.transport == "stdio":
            entry = {"type": "stdio", "command": server.command}
            if server.args:
                entry["args"] = server.args
            mcp_config[server.name] = entry

    import json
    config_json = json.dumps({"mcpServers": mcp_config})
    return f"echo '{config_json}' > ~/.agent-mcp-config.json"
```

### Step 6 — Write Unit Tests

Create `tests/unit/agents/installed/test_{agent_name}.py`:

```python
"""Unit tests for {AgentDisplayName} agent."""

import pytest
from harbor.agents.installed.{agent_module} import {ClassName}


@pytest.mark.unit
class TestAgentName:
    def test_name(self):
        assert {ClassName}.name() == "{agent-name}"


@pytest.mark.unit
class TestCreateRunAgentCommands:
    def test_basic_command(self, mock_logs_dir):
        agent = {ClassName}(logs_dir=mock_logs_dir)
        commands = agent.create_run_agent_commands("do something")
        assert len(commands) >= 1
        assert "do something" in commands[-1].command

    def test_install_template_exists(self, mock_logs_dir):
        agent = {ClassName}(logs_dir=mock_logs_dir)
        assert agent._install_agent_template_path.exists()
```

### Step 7 — Validate

Run the following checks:
```bash
# Lint
uvx ruff check src/harbor/agents/installed/{agent_name}.py

# Format
uvx ruff format src/harbor/agents/installed/{agent_name}.py

# Unit tests
uv run pytest tests/unit/agents/installed/test_{agent_name}.py -v
```

---

## File Checklist

When generating a new Harbor agent, ensure these files are created/modified:

| File | Action |
|------|--------|
| `src/harbor/agents/installed/{agent_name}.py` | Create — agent implementation |
| `src/harbor/agents/installed/install-{agent-name}.sh.j2` | Create — install template |
| `src/harbor/models/agent/name.py` | Modify — add to AgentName enum |
| `src/harbor/agents/factory.py` | Modify — import and register agent |
| `tests/unit/agents/installed/test_{agent_name}.py` | Create — unit tests |

---

## Common Patterns Reference

### Model Name Handling
Harbor passes model names as `provider/model` (e.g. `anthropic/claude-opus-4-1`). The base class parses this into `self._parsed_model_provider` and `self._parsed_model_name`. Use these in your `create_run_agent_commands()` to pass the correct model config to the CLI tool.

### Timeout Handling
Agent timeouts are managed by the orchestrator. Your `run()` / `create_run_agent_commands()` does not need to implement timeout logic — the environment's `exec()` call handles it.

### Logging
Use `self.logger` (inherited from BaseAgent) for structured logging:
```python
self.logger.info("Starting agent execution")
self.logger.debug(f"Running command: {cmd}")
```

### ExecInput Fields
```python
ExecInput(
    command="...",        # Shell command to execute
    cwd="/workdir",       # Working directory (default: container root)
    env={"KEY": "val"},   # Additional environment variables
    timeout_sec=300,      # Per-command timeout override (optional)
)
```
