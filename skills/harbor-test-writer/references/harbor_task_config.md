# Harbor task.toml Configuration Reference

## Full Schema

```toml
version = "1.0"

[metadata]
author_name = "string"           # Task author name
author_email = "string"          # Task author email
difficulty = "easy"              # easy | medium | hard
category = "programming"         # programming | debugging | devops | data-science | etc.
tags = ["string"]                # Arbitrary tags for filtering

[verifier]
timeout_sec = 120.0              # Max seconds for test.sh to execute
env = {}                         # Environment variables passed to verifier

[agent]
timeout_sec = 300.0              # Max seconds for agent to work on the task

[environment]
build_timeout_sec = 600.0        # Max seconds to build Docker image
cpus = 1                         # Number of CPUs allocated
memory = "2G"                    # Memory limit (string format: "2G", "512M")
memory_mb = 2048                 # Memory limit (integer MB, alternative to memory)
storage = "10G"                  # Storage limit (string format)
storage_mb = 10240               # Storage limit (integer MB, alternative to storage)
gpus = 0                         # Number of GPUs (0 for CPU-only tasks)
allow_internet = true            # Whether container has network access

[solution]
env = {}                         # Environment variables for reference solution

[[environment.mcp_servers]]      # MCP server definitions (repeatable)
name = "mcp-server"              # Unique server name
transport = "streamable-http"    # "sse" | "streamable-http" | "stdio"
url = "http://mcp-server:8000/mcp"  # URL for sse/streamable-http
# command = "npx"                # Command for stdio transport
# args = ["-y", "my-mcp"]       # Args for stdio transport
```

## Reward Output

The verifier script must write reward to one of:

### `/logs/verifier/reward.txt`
Single float value, 0.0 to 1.0:
```
1
```
or partial credit:
```
0.75
```

### `/logs/verifier/reward.json`
Dict with reward and optional metadata:
```json
{
  "reward": 0.75,
  "details": {
    "tests_passed": 3,
    "tests_total": 4,
    "failed": ["test_edge_case"]
  }
}
```

## CTRF Test Report

Harbor's recommended test reporting format uses pytest-json-ctrf:
```bash
uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_solution.py -rA
```

CTRF JSON structure (for partial credit calculation):
```json
{
  "results": {
    "summary": {
      "tests": 4,
      "passed": 3,
      "failed": 1,
      "pending": 0,
      "skipped": 0,
      "other": 0
    }
  }
}
```

## Container Filesystem Layout

```
/workdir/          # Agent working directory (task source files)
/tests/            # Verifier test files (copied by Harbor from task tests/)
/logs/             # Log output directory
/logs/verifier/    # Verifier output (reward.txt, reward.json, ctrf.json)
/logs/agent/       # Agent output logs
/installed-agent/  # Agent installation directory (created by BaseInstalledAgent)
```
