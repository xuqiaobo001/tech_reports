---
name: harbor-test-converter
description: This skill should be used when users have pre-existing test suites (pytest, unittest, Jest, Mocha, Go test, shell scripts, etc.) and want to convert them into Harbor-compatible evaluation tasks. It provides structural mapping, reward wiring, Dockerfile generation, path adaptation, and validation workflows. Trigger scenarios include requests like "convert my tests to Harbor format", "migrate existing test suite to Harbor", "make my pytest tests work in Harbor", or "transform test cases into Harbor tasks".
license: Complete terms in LICENSE.txt
---

# Harbor Test Converter

## Overview

This skill enables converting existing test suites into Harbor-compatible evaluation tasks. It covers the structural mapping from various test frameworks to Harbor's task directory layout, reward wiring, Dockerfile generation, path adaptation, and a validation workflow to ensure correctness.

## Conversion Overview

Harbor tasks require a specific directory layout and a verifier script (`test.sh`) that writes a reward to `/logs/verifier/reward.txt`. Converting existing tests means:

1. Wrapping them in Harbor's task directory structure
2. Creating a `test.sh` entry point that runs the original tests
3. Wiring test results to Harbor's reward output
4. Creating a Dockerfile that provides the right runtime
5. Writing an `instruction.md` that describes the task without leaking test details
6. Configuring `task.toml` with appropriate timeouts and resources

### Target Directory Structure

```
converted-task/
├── task.toml              # Task configuration
├── instruction.md         # Agent instruction (no test details!)
├── environment/
│   ├── Dockerfile         # Runtime environment
│   └── (source files)     # Pre-existing code the agent works on
├── tests/
│   ├── test.sh            # Verifier entry point (REQUIRED)
│   └── (original tests)   # Converted test files
└── solution/              # Optional reference solution
    └── solve.sh
```

## Workflow: Convert Existing Tests

### Step 1 — Analyze the Existing Test Suite

Before converting, identify:

| Question | Why It Matters |
|---|---|
| What test framework is used? | Determines test.sh runner commands |
| What language/runtime is needed? | Determines Dockerfile base image |
| Are there test fixtures or setup? | May need to be included in Dockerfile |
| Do tests depend on external services? | Affects `allow_internet` and Dockerfile |
| Are tests independent or ordered? | Harbor runs tests once; ordering must be preserved |
| What do the tests verify? | Drives instruction.md content |
| Are there test data files? | Must be copied into the task structure |

### Step 2 — Create task.toml

```toml
version = "1.0"

[metadata]
author_name = "Converter"
difficulty = "medium"
category = "programming"
tags = ["converted"]

[verifier]
timeout_sec = 180.0          # Based on original test suite runtime + buffer

[agent]
timeout_sec = 600.0

[environment]
build_timeout_sec = 600.0
cpus = 1
memory = "2G"
storage = "10G"
```

### Step 3 — Convert Tests by Framework

#### Converting pytest Tests

Original structure:
```
project/
├── src/mylib/
├── tests/
│   ├── conftest.py
│   ├── test_core.py
│   └── test_utils.py
└── pyproject.toml
```

Converted `tests/test.sh`:
```bash
#!/bin/bash

apt-get update
apt-get install -y curl

curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

cd /workdir
if [ -f pyproject.toml ]; then
  uv sync 2>/dev/null || true
fi

uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_core.py /tests/test_utils.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

Key adaptations:
- Copy `test_*.py` and `conftest.py` to `tests/` directory
- Replace relative imports with absolute paths (`/workdir/`, `/tests/`)
- Pin pytest version in `uvx --with`
- Ensure `/workdir` is on `PYTHONPATH` if tests import the project

Fixture path adaptation:
```python
# BEFORE (relative)
@pytest.fixture
def sample_data():
    return Path("tests/data/sample.json")

# AFTER (Harbor absolute)
@pytest.fixture
def sample_data():
    return Path("/tests/data/sample.json")
```

#### Converting unittest Tests

```bash
#!/bin/bash

cd /workdir
export PYTHONPATH="/workdir:$PYTHONPATH"

python -m pytest /tests/test_calculator.py -v 2>&1 | tee /logs/verifier/output.txt

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

Note: unittest tests run fine under pytest — no need to rewrite.

#### Converting Jest / Mocha (JavaScript) Tests

```bash
#!/bin/bash

apt-get update && apt-get install -y curl
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

cd /workdir
npm install 2>/dev/null || true
npm install --save-dev jest 2>/dev/null || true

cp /tests/*.test.js /workdir/tests/ 2>/dev/null || true
cp /tests/*.spec.js /workdir/tests/ 2>/dev/null || true

npx jest --json --outputFile=/logs/verifier/jest-results.json 2>&1 || true

if [ -f /logs/verifier/jest-results.json ]; then
  PASSED=$(node -e "
    const r = require('/logs/verifier/jest-results.json');
    const total = r.numTotalTests || 1;
    const passed = r.numPassedTests || 0;
    console.log(passed / total);
  " 2>/dev/null)
  echo "${PASSED:-0}" > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

#### Converting Go Tests

```bash
#!/bin/bash

cd /workdir
cp /tests/*_test.go /workdir/ 2>/dev/null || true

go test -v -json ./... > /logs/verifier/go-test.json 2>&1

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

#### Converting Shell Script Tests

```bash
#!/bin/bash

TOTAL=0
PASSED=0

run_check() {
  TOTAL=$((TOTAL + 1))
  if eval "$1"; then
    PASSED=$((PASSED + 1))
    echo "PASS: $2"
  else
    echo "FAIL: $2"
  fi
}

run_check '[ -f /workdir/deploy.sh ]' "deploy script exists"
run_check 'bash /workdir/deploy.sh --dry-run 2>&1 | grep -q "success"' "dry run succeeds"

if [ "$TOTAL" -gt 0 ]; then
  REWARD=$(echo "scale=4; $PASSED / $TOTAL" | bc)
  echo "$REWARD" > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

### Step 4 — Create the Dockerfile

Choose a base image matching the original test runtime:

| Original Runtime | Recommended Base Image |
|---|---|
| Python 3.x | `python:3.12-slim` |
| Node.js | `node:20-slim` |
| Go | `golang:1.22` |
| Java | `eclipse-temurin:21-jdk` |
| Rust | `rust:1.78-slim` |
| Multi-language | `ubuntu:22.04` |

```dockerfile
FROM python:3.12-slim
WORKDIR /workdir
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY src/ /workdir/src/
COPY pyproject.toml /workdir/
RUN pip install -e . 2>/dev/null || true
```

Key rules:
- Copy project source into `/workdir/`
- Do NOT copy test files — Harbor copies `tests/` to `/tests/` automatically
- Pre-install heavy dependencies to reduce agent setup time

### Step 5 — Write instruction.md

Convert the test intent into a natural language task description. Do NOT reveal test details.

```markdown
# Fix the Calculator Module

The calculator module in `src/calculator.py` has bugs in the `add()` and `subtract()` functions.

## Requirements
- Fix `add(a, b)` to correctly return the sum of two numbers
- Fix `subtract(a, b)` to correctly return the difference
- Do not change function signatures
```

Rules:
- Describe WHAT needs to be done, not HOW to verify it
- Never mention specific test names, assertions, or expected values
- Be specific about file locations and function signatures

### Step 6 — Handle Test Dependencies and Data

Test data files — reference with absolute paths:
```python
DATA_DIR = Path("/tests/data")

def test_process():
    input_data = json.loads((DATA_DIR / "input.json").read_text())
```

External dependencies — install in `test.sh`:
```bash
uvx --with pytest==8.4.1 \
    --with requests==2.32.0 \
    pytest /tests/ -rA
```

## Conversion Checklist

### Structure
- [ ] `task.toml` created with appropriate timeouts and resources
- [ ] `instruction.md` describes the task without leaking test details
- [ ] `environment/Dockerfile` provides the correct runtime
- [ ] `tests/test.sh` is the entry point and is executable
- [ ] Original test files are in `tests/` directory
- [ ] Test data files included with absolute path references

### Path Fixes
- [ ] All relative paths converted to absolute (`/workdir/`, `/tests/`)
- [ ] `conftest.py` / shared fixtures updated with absolute paths
- [ ] Import paths updated (`PYTHONPATH`, `NODE_PATH`, etc.)
- [ ] Test data file references use `/tests/data/` prefix

### Reward Wiring
- [ ] `test.sh` writes reward to `/logs/verifier/reward.txt` or `/logs/verifier/reward.json`
- [ ] Reward is always written, even on test infrastructure failure
- [ ] Reward value between 0.0 and 1.0
- [ ] Partial credit calculated correctly (if applicable)

### Robustness
- [ ] `test.sh` uses non-interactive installs (`-y`, `DEBIAN_FRONTEND=noninteractive`)
- [ ] All subprocess calls have timeouts
- [ ] Tests don't depend on network unless `allow_internet = true`
- [ ] Tests are deterministic (no random seeds, no timing dependencies)
- [ ] Tests don't assume a specific agent implementation

### Validation
- [ ] Run `harbor tasks validate <task-path>` to check structure
- [ ] Run with `oracle` agent to verify tests pass with reference solution
- [ ] Run with `nop` agent to verify tests fail without a solution (reward = 0)

## Common Conversion Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Relative imports in tests | `ModuleNotFoundError` | Add `PYTHONPATH=/workdir` or use absolute imports |
| Missing test dependencies | `ImportError` in verifier | Install in `test.sh` with pinned versions |
| Tests modify source files | Flaky results across runs | Use temp copies or reset state in setup |
| Hardcoded localhost ports | Port conflicts in container | Use dynamic or fixed non-conflicting ports |
| Tests assume specific OS | Fails in Docker container | Use compatible base image, install missing tools |
| No reward on crash | `NaN` reward in Harbor | Wrap test runner in `|| true`, always write reward |
| conftest.py not copied | Fixtures not found | Include conftest.py in `tests/` directory |
| Test ordering dependency | Random failures | Use `pytest-ordering` or restructure tests |

## Batch Conversion

To convert multiple test files into separate Harbor tasks, refer to `references/batch_converter.md` for a Python script template that automates the directory scaffolding, test.sh generation, task.toml creation, and Dockerfile generation.

## Validation Workflow

After conversion, validate with Harbor's built-in tools:

```bash
# 1. Validate task structure
harbor tasks validate ./converted-task/

# 2. Run with oracle agent (should pass)
harbor run --dataset ./converted-task/ --agent oracle

# 3. Run with nop agent (should fail — reward = 0)
harbor run --dataset ./converted-task/ --agent nop

# 4. Run with a real agent to verify end-to-end
harbor run --dataset ./converted-task/ --agent claude-code --model anthropic/claude-opus-4-1
```

If the oracle agent fails, the test conversion has a bug. If the nop agent passes, the tests are not actually verifying agent work.

## Resources

### references/
- `references/batch_converter.md` — Python script template for batch-converting multiple test files into Harbor tasks.
