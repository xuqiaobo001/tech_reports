---
name: harbor-test-writer
description: This skill should be used when users need to create new test cases, verifier scripts, or complete task definitions for evaluating AI agents in Harbor. It provides guidance on writing high-quality test suites that conform to Harbor's containerized verification system, produce correct reward output, and are robust enough for automated execution. Trigger scenarios include requests like "write Harbor tests", "create a new evaluation task", "write a verifier for Harbor", or "create test cases for agent benchmarking".
license: Complete terms in LICENSE.txt
---

# Harbor Test Writer

## Overview

This skill enables writing high-quality Harbor evaluation task test suites that run automatically in Harbor's containerized verification system. It covers task.toml configuration, instruction.md authoring, verifier script patterns, pytest test case conventions, Dockerfile best practices, and a comprehensive quality checklist.

## Harbor Test Architecture

Harbor tasks are evaluated inside Docker containers. The verification flow is:

1. Agent completes the task in the container's `/workdir`
2. Harbor copies `tests/` directory to `/tests/` in the container
3. Harbor executes `/tests/test.sh` as the verifier entry point
4. `test.sh` writes a reward value to `/logs/verifier/reward.txt` (float 0.0–1.0) or `/logs/verifier/reward.json` (dict)
5. Harbor collects the reward and reports results

### Task Directory Structure

```
my-task/
├── task.toml              # Task configuration
├── instruction.md         # Natural language instruction for the agent
├── environment/
│   └── Dockerfile         # Container environment definition
├── tests/
│   ├── test.sh            # Verifier entry point (REQUIRED)
│   ├── test_*.py          # Python test files (recommended)
│   └── data/              # Optional test fixtures
└── solution/              # Optional reference solution
    └── solve.sh
```

## Workflow: Write a Harbor Test Suite

### Step 1 — Define task.toml

```toml
version = "1.0"

[metadata]
author_name = "Your Name"
author_email = "you@example.com"
difficulty = "easy"          # easy | medium | hard
category = "programming"     # programming | debugging | devops | data-science | etc.
tags = ["python", "testing"]

[verifier]
timeout_sec = 120.0          # Max time for test.sh to run

[agent]
timeout_sec = 300.0          # Max time for agent to work on the task

[environment]
build_timeout_sec = 600.0
cpus = 1
memory = "2G"
storage = "10G"
```

Configuration rules:
- `verifier.timeout_sec`: 60–300s for most tasks. Increase for compilation or heavy setup.
- `agent.timeout_sec`: Typically 2–10x the verifier timeout.
- Resource limits: Default `cpus=1, memory=2G, storage=10G` works for most tasks.

### Step 2 — Write instruction.md

```markdown
# Task Title

Clear, unambiguous description of what the agent should accomplish.

## Requirements
- Specific requirement 1
- Specific requirement 2

## Constraints
- Any restrictions on approach
```

Rules:
- Be precise about expected output (file paths, formats, function signatures).
- Avoid ambiguity — the agent cannot ask clarifying questions.
- State the working directory context (files are in `/workdir`).
- Do NOT reveal test implementation details.

### Step 3 — Write the Verifier Script (test.sh)

#### Pattern A: pytest-based Verification (Recommended)

```bash
#!/bin/bash

apt-get update
apt-get install -y curl

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_solution.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

#### Pattern B: Partial Credit Reward

```bash
#!/bin/bash

apt-get update && apt-get install -y curl jq
curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_solution.py -rA || true

TOTAL=$(jq '.results.summary.tests' /logs/verifier/ctrf.json)
PASSED=$(jq '.results.summary.passed' /logs/verifier/ctrf.json)

if [ "$TOTAL" -gt 0 ] 2>/dev/null; then
  REWARD=$(echo "scale=4; $PASSED / $TOTAL" | bc)
  echo "$REWARD" > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

#### Pattern C: Custom Shell Verification

```bash
#!/bin/bash

REWARD=0

if [ -f /workdir/output.txt ]; then
  EXPECTED="hello world"
  ACTUAL=$(cat /workdir/output.txt | tr -d '[:space:]')
  EXPECTED_CLEAN=$(echo "$EXPECTED" | tr -d '[:space:]')
  if [ "$ACTUAL" = "$EXPECTED_CLEAN" ]; then
    REWARD=1
  fi
fi

echo $REWARD > /logs/verifier/reward.txt
```

### Step 4 — Write Python Test Cases

```python
"""Verification tests for the task."""

import os
import subprocess

WORKDIR = "/workdir"


class TestFileExists:
    def test_main_file_exists(self):
        assert os.path.isfile(f"{WORKDIR}/solution.py"), \
            "solution.py was not created"


class TestFunctionality:
    def test_output_correct(self):
        result = subprocess.run(
            ["python", f"{WORKDIR}/solution.py"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "expected output" in result.stdout
```

## Test Quality Checklist

### Correctness
- [ ] Tests verify actual requirements, not implementation details
- [ ] Tests are deterministic — no random behavior, no timing dependencies
- [ ] Tests handle missing files gracefully (assert with clear message)
- [ ] Reward output is always written, even on test infrastructure failure

### Robustness
- [ ] `test.sh` uses `apt-get install -y` (non-interactive)
- [ ] All subprocess calls have `timeout` parameter
- [ ] Tests don't depend on network unless `allow_internet = true`
- [ ] Tests handle trailing whitespace/newlines in output comparison

### Reward Output
- [ ] Reward written to `/logs/verifier/reward.txt` or `/logs/verifier/reward.json`
- [ ] Reward value between 0.0 and 1.0 inclusive
- [ ] Reward written even if tests crash (use `|| true` and fallback)

### Independence
- [ ] Tests don't assume any specific agent was used
- [ ] Tests don't inspect agent logs or trajectory
- [ ] Tests only verify observable output in `/workdir`
- [ ] Each test function is independent (no ordering dependency)

## Writing Effective Test Cases

### DO: Test Observable Outcomes

```python
def test_api_endpoint_works(self):
    proc = subprocess.Popen(
        ["python", f"{WORKDIR}/server.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        import time, requests
        time.sleep(2)
        resp = requests.get("http://localhost:8000/health")
        assert resp.status_code == 200
    finally:
        proc.terminate()
        proc.wait()
```

### DON'T: Test Implementation Details

```python
# BAD — prescribes implementation
def test_uses_correct_library(self):
    with open(f"{WORKDIR}/solution.py") as f:
        code = f.read()
    assert "import pandas" in code
```

### DO: Use Clear Assertion Messages

```python
def test_output_format(self):
    with open(f"{WORKDIR}/output.csv") as f:
        lines = f.readlines()
    assert len(lines) > 1, f"Expected CSV with header + data, got {len(lines)} lines"
```

## Dockerfile Best Practices

```dockerfile
FROM python:3.12-slim
WORKDIR /workdir
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY . /workdir/
```

Rules:
- Use slim base images (`python:3.12-slim`, `node:20-slim`, `ubuntu:22.04`)
- Install shared dependencies in Dockerfile, not in test.sh
- Do NOT copy test files into Dockerfile — Harbor copies `tests/` to `/tests/` automatically
- Set `WORKDIR /workdir`

## Anti-Patterns to Avoid

| Anti-Pattern | Fix |
|---|---|
| No reward output on crash | Wrap in `|| true`, always write reward |
| Hardcoded paths outside /workdir | Use `/workdir/` prefix |
| `pip install` without pinning | Pin versions or use `uvx --with pkg==x.y.z` |
| Testing agent internals | Test observable output only |
| No timeout on subprocess | Always set `timeout=` parameter |
| Interactive prompts in test.sh | Use `-y` flags, `DEBIAN_FRONTEND=noninteractive` |

## Resources

### references/
- `references/harbor_task_config.md` — Detailed task.toml schema reference with all supported fields, environment options, and MCP server configuration.
