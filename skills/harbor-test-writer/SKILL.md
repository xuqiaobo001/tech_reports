# harbor-test-writer

Guide for writing high-quality Harbor evaluation task test suites that can run automatically in Harbor's containerized verification system. Use this skill when users need to create new test cases, verifier scripts, or complete task definitions for evaluating AI agents in Harbor. This skill ensures tests follow Harbor conventions, produce correct reward output, and are robust enough for automated execution.

---

## Harbor Test Architecture

Harbor tasks are evaluated inside Docker containers. The verification flow is:

1. Agent completes the task in the container's `/workdir`
2. Harbor copies `tests/` directory to `/tests/` in the container
3. Harbor executes `/tests/test.sh` as the verifier entry point
4. `test.sh` writes a reward value to `/logs/verifier/reward.txt` (float 0.0-1.0) or `/logs/verifier/reward.json` (dict)
5. Harbor collects the reward and reports results

### Directory Structure of a Complete Task

```
my-task/
├── task.toml              # Task configuration
├── instruction.md         # Natural language instruction for the agent
├── environment/
│   └── Dockerfile         # Container environment definition
├── tests/
│   ├── test.sh            # Verifier entry point (REQUIRED)
│   ├── test_*.py          # Python test files (recommended)
│   └── helpers/           # Optional test utilities
└── solution/              # Optional reference solution
    └── solve.sh
```

---

## Step-by-Step: Write a Harbor Test Suite

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
build_timeout_sec = 600.0    # Max time to build Docker image
cpus = 1
memory = "2G"
storage = "10G"
```

Configuration rules:
- `verifier.timeout_sec`: Keep reasonable (60-300s for most tasks). Tests that need compilation or heavy setup may need more.
- `agent.timeout_sec`: Should be generous enough for the agent to complete the task. Typically 2-10x the verifier timeout.
- Resource limits: Use minimal resources needed. Default `cpus=1, memory=2G, storage=10G` works for most tasks.

### Step 2 — Write instruction.md

```markdown
# Task Title

Clear, unambiguous description of what the agent should accomplish.

## Requirements

- Specific requirement 1
- Specific requirement 2
- Expected output format or file locations

## Constraints

- Any restrictions on approach
- Language or framework requirements
```

Instruction quality rules:
- Be precise about expected output (file paths, formats, function signatures)
- Avoid ambiguity — the agent has no way to ask clarifying questions
- State the working directory context (files are in `/workdir`)
- Do not reveal test implementation details

### Step 3 — Write the Verifier Script (test.sh)

This is the most critical file. It must be robust, deterministic, and produce a reward.

#### Pattern A: pytest-based Verification (Recommended)

```bash
#!/bin/bash

# Install test dependencies
apt-get update
apt-get install -y curl

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

# Run pytest with CTRF report
uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_solution.py -rA

# Write binary reward (1 = all pass, 0 = any fail)
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

# Run pytest with JSON output
uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_solution.py -rA || true

# Calculate partial reward from CTRF report
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

# Check file exists
if [ -f /workdir/output.txt ]; then
  # Check content matches expected
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

Create `tests/test_solution.py` (or any `test_*.py` file):

```python
"""Verification tests for the task."""

import os
import subprocess


WORKDIR = "/workdir"


class TestFileExists:
    """Verify the agent created the required files."""

    def test_main_file_exists(self):
        assert os.path.isfile(f"{WORKDIR}/solution.py"), \
            "solution.py was not created"

    def test_config_file_exists(self):
        assert os.path.isfile(f"{WORKDIR}/config.json"), \
            "config.json was not created"


class TestFunctionality:
    """Verify the solution works correctly."""

    def test_output_correct(self):
        result = subprocess.run(
            ["python", f"{WORKDIR}/solution.py"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "expected output" in result.stdout

    def test_edge_case(self):
        result = subprocess.run(
            ["python", f"{WORKDIR}/solution.py", "--input", "edge"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
```

---

## Test Quality Checklist

Every test suite MUST satisfy these criteria before being considered complete:

### Correctness
- [ ] Tests verify actual requirements, not implementation details
- [ ] Tests are deterministic — no random behavior, no timing dependencies
- [ ] Tests handle missing files gracefully (assert with clear message, don't crash)
- [ ] Reward output is always written, even on test infrastructure failure

### Robustness
- [ ] `test.sh` uses `apt-get install -y` (non-interactive)
- [ ] All subprocess calls have `timeout` parameter
- [ ] Tests don't depend on network access unless `allow_internet = true` in task.toml
- [ ] Tests work regardless of file permissions (agent may create files with any mode)
- [ ] Tests handle trailing whitespace/newlines in output comparison

### Reward Output
- [ ] Reward is written to `/logs/verifier/reward.txt` (single float) or `/logs/verifier/reward.json`
- [ ] Reward value is between 0.0 and 1.0 inclusive
- [ ] Reward is written even if tests crash (use `|| true` and fallback)
- [ ] Binary reward (0 or 1) for pass/fail tasks; partial credit only when meaningful

### Independence
- [ ] Tests don't assume any specific agent was used
- [ ] Tests don't inspect agent logs or trajectory
- [ ] Tests only verify the observable output in `/workdir`
- [ ] Each test function is independent (no ordering dependency)

### Performance
- [ ] Tests complete well within `verifier.timeout_sec`
- [ ] No unnecessary package installations in test.sh
- [ ] Heavy dependencies are in the Dockerfile, not installed at test time

---

## Writing Effective Test Cases

### DO: Test Observable Outcomes

```python
# GOOD: Tests what the agent produced
def test_api_endpoint_works(self):
    """Start the server and verify the endpoint responds."""
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
# BAD: Tests how the agent solved it, not what it produced
def test_uses_correct_library(self):
    with open(f"{WORKDIR}/solution.py") as f:
        code = f.read()
    assert "import pandas" in code  # Don't prescribe implementation
```

### DO: Use Clear Assertion Messages

```python
# GOOD
def test_output_format(self):
    with open(f"{WORKDIR}/output.csv") as f:
        lines = f.readlines()
    assert len(lines) > 1, f"Expected CSV with header + data, got {len(lines)} lines"
    assert "," in lines[0], "First line should be comma-separated header"
```

### DO: Handle Edge Cases in Verification

```python
# GOOD: Robust file reading
def test_result_file(self):
    path = f"{WORKDIR}/result.txt"
    assert os.path.isfile(path), "result.txt not found"

    with open(path) as f:
        content = f.read().strip()

    assert content != "", "result.txt is empty"
    value = float(content)
    assert 0 <= value <= 100, f"Result {value} out of expected range [0, 100]"
```

---

## Dockerfile Best Practices

```dockerfile
FROM python:3.12-slim

WORKDIR /workdir

# Install system dependencies needed by both agent and tests
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pre-install heavy Python dependencies to speed up agent execution
# RUN pip install numpy pandas  # Only if the task requires them

# Copy task files
COPY . /workdir/
```

Rules:
- Use slim base images (`python:3.12-slim`, `node:20-slim`, `ubuntu:22.04`)
- Install shared dependencies in Dockerfile, not in test.sh
- Keep images small — agents download and install their own tools
- Set `WORKDIR /workdir`

---

## Reward Output Formats

### Simple (reward.txt)
Single float value, 0.0 to 1.0:
```
1
```
or
```
0.75
```

### Structured (reward.json)
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

---

## Common Test Patterns by Task Category

### Code Generation Tasks
- Verify file exists and is valid syntax (`python -c "compile(open('f.py').read(), 'f.py', 'exec')"`)
- Run the code and check output
- Test with multiple inputs if applicable

### Bug Fix Tasks
- Run the existing test suite that was failing
- Verify the fix doesn't break other tests
- Check the specific bug scenario is resolved

### Refactoring Tasks
- Run the original test suite (should still pass)
- Verify structural requirements (e.g., function count, file organization)
- Check that behavior is preserved

### DevOps / Configuration Tasks
- Validate config file syntax (YAML, JSON, TOML parsing)
- Check required keys/values exist
- Dry-run commands where possible

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why It's Bad | Fix |
|---|---|---|
| No reward output on crash | Harbor reports NaN, trial is wasted | Wrap in `\|\| true`, always write reward |
| Hardcoded paths outside /workdir | Agent output is always in /workdir | Use `/workdir/` prefix |
| `pip install` in test.sh without pinning | Non-deterministic, may break | Pin versions or use `uvx --with pkg==x.y.z` |
| Testing agent internals | Couples to specific agent | Test observable output only |
| No timeout on subprocess | Test hangs, wastes verifier timeout | Always set `timeout=` parameter |
| Interactive prompts in test.sh | Blocks forever in container | Use `-y` flags, `DEBIAN_FRONTEND=noninteractive` |
