# harbor-test-converter

Guide for converting existing test suites (pytest, unittest, Jest, Mocha, Go test, shell scripts, etc.) into Harbor-compatible evaluation tasks. Use this skill when users have pre-existing test cases and want to transform them into Harbor task format so they can be used to evaluate AI agents automatically. This skill covers the structural mapping, reward wiring, Dockerfile generation, and common pitfalls of conversion.

---

## Conversion Overview

Harbor tasks require a specific directory layout and a verifier script (`test.sh`) that writes a reward to `/logs/verifier/reward.txt`. Converting existing tests means:

1. Wrapping them in Harbor's task directory structure
2. Creating a `test.sh` entry point that runs the original tests
3. Wiring test results to Harbor's reward output
4. Creating a Dockerfile that provides the right runtime
5. Writing an `instruction.md` that describes the task without leaking test details
6. Configuring `task.toml` with appropriate timeouts and resources

### Harbor Task Directory Structure (Target)

```
converted-task/
├── task.toml              # Task configuration
├── instruction.md         # Agent instruction (no test details!)
├── environment/
│   ├── Dockerfile         # Runtime environment
│   └── (source files)     # Pre-existing code the agent works on
├── tests/
│   ├── test.sh            # Verifier entry point (REQUIRED)
│   └── (original tests)   # Your converted test files
└── solution/              # Optional reference solution
    └── solve.sh
```

---

## Step-by-Step Conversion Process

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
difficulty = "medium"        # Estimate based on test complexity
category = "programming"
tags = ["converted", "python"]  # Add relevant tags

[verifier]
timeout_sec = 180.0          # Based on original test suite runtime + buffer

[agent]
timeout_sec = 600.0          # Give agent enough time to work

[environment]
build_timeout_sec = 600.0
cpus = 1
memory = "2G"
storage = "10G"
# allow_internet = true      # Only if tests need network access
```

### Step 3 — Convert Tests by Framework

#### Converting pytest Tests

Original structure:
```
project/
├── src/
│   └── mylib/
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

# Install project dependencies from the agent's work
cd /workdir
if [ -f pyproject.toml ]; then
  uv sync 2>/dev/null || true
fi

# Run the converted test suite
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

Conversion notes for pytest:
- Copy `test_*.py` files to `tests/` directory
- Copy `conftest.py` if tests depend on shared fixtures
- Replace relative imports with absolute paths (`/workdir/`, `/tests/`)
- Pin pytest version in `uvx --with`
- If tests import the project, ensure `/workdir` is on `PYTHONPATH`

Fixture adaptation pattern:
```python
# BEFORE (original conftest.py — relative paths)
@pytest.fixture
def sample_data():
    return Path("tests/data/sample.json")

# AFTER (Harbor — absolute paths)
@pytest.fixture
def sample_data():
    return Path("/tests/data/sample.json")
```

#### Converting unittest Tests

Original:
```python
import unittest

class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)

    def test_subtract(self):
        self.assertEqual(subtract(5, 3), 2)
```

Converted `tests/test.sh`:
```bash
#!/bin/bash

cd /workdir

# Add workdir to Python path so tests can import the module
export PYTHONPATH="/workdir:$PYTHONPATH"

python -m pytest /tests/test_calculator.py -v 2>&1 | tee /logs/verifier/output.txt

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

Conversion notes for unittest:
- unittest tests run fine under pytest — no need to rewrite
- Replace `self.assert*` calls only if they reference relative paths
- Move test data files and update paths

#### Converting Jest / Mocha (JavaScript) Tests

Original:
```javascript
describe('API', () => {
  test('returns 200 on health check', async () => {
    const res = await request(app).get('/health');
    expect(res.status).toBe(200);
  });
});
```

Converted `tests/test.sh`:
```bash
#!/bin/bash

# Install Node.js if not in base image
apt-get update
apt-get install -y curl
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

cd /workdir

# Install project dependencies
npm install 2>/dev/null || true

# Install test runner
npm install --save-dev jest 2>/dev/null || true

# Copy test files into project
cp /tests/*.test.js /workdir/tests/ 2>/dev/null || true
cp /tests/*.spec.js /workdir/tests/ 2>/dev/null || true

# Run tests with JSON output
npx jest --json --outputFile=/logs/verifier/jest-results.json 2>&1 || true

# Parse results for reward
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

Original:
```go
func TestAdd(t *testing.T) {
    result := Add(1, 2)
    if result != 3 {
        t.Errorf("expected 3, got %d", result)
    }
}
```

Converted `tests/test.sh`:
```bash
#!/bin/bash

cd /workdir

# Copy test files into the Go module
cp /tests/*_test.go /workdir/ 2>/dev/null || true

# Run Go tests with JSON output
go test -v -json ./... > /logs/verifier/go-test.json 2>&1

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

#### Converting Shell Script Tests

Original:
```bash
#!/bin/bash
# test_deploy.sh
if curl -s http://localhost:8080/health | grep -q "ok"; then
  echo "PASS: health check"
else
  echo "FAIL: health check"
  exit 1
fi
```

Converted `tests/test.sh`:
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

# Convert each original assertion to a run_check call
run_check '[ -f /workdir/deploy.sh ]' "deploy script exists"
run_check 'bash /workdir/deploy.sh --dry-run 2>&1 | grep -q "success"' "dry run succeeds"
run_check '[ -f /workdir/config.yaml ]' "config file created"

# Calculate reward
if [ "$TOTAL" -gt 0 ]; then
  REWARD=$(echo "scale=4; $PASSED / $TOTAL" | bc)
  echo "$REWARD" > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

---

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

Example Dockerfile:
```dockerfile
FROM python:3.12-slim

WORKDIR /workdir

# System dependencies needed by both agent and tests
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the project source code (the "broken" or "incomplete" version)
# that the agent needs to fix/complete
COPY src/ /workdir/src/
COPY pyproject.toml /workdir/

# Pre-install project dependencies to speed up agent execution
RUN pip install -e . 2>/dev/null || true
```

Key rules:
- Copy the project source into `/workdir/` — this is what the agent sees and modifies
- Do NOT copy test files into the Dockerfile — Harbor copies `tests/` to `/tests/` automatically
- Pre-install heavy dependencies to reduce agent setup time

### Step 5 — Write instruction.md

Convert the test intent into a natural language task description. Do NOT reveal test implementation details.

```markdown
# Fix the Calculator Module

The calculator module in `src/calculator.py` has bugs in the `add()` and `subtract()` functions.

## Requirements

- Fix `add(a, b)` to correctly return the sum of two numbers
- Fix `subtract(a, b)` to correctly return the difference
- Do not change function signatures
- All existing imports and module structure should be preserved

## Files

- `src/calculator.py` — the module to fix
```

Instruction rules:
- Describe WHAT needs to be done, not HOW to verify it
- Never mention specific test names, assertions, or expected values that would let the agent "cheat"
- Be specific about file locations and function signatures
- State constraints clearly

### Step 6 — Handle Test Dependencies and Data

#### Test Data Files
```
tests/
├── test.sh
├── test_solution.py
└── data/
    ├── input.json        # Test fixtures
    └── expected.csv      # Expected outputs
```

Reference in tests with absolute paths:
```python
DATA_DIR = Path("/tests/data")

def test_process():
    input_data = json.loads((DATA_DIR / "input.json").read_text())
    # ...
```

#### External Dependencies
If original tests use libraries not in the base image, install them in `test.sh`:
```bash
# In test.sh, before running tests
uvx --with pytest==8.4.1 \
    --with requests==2.32.0 \
    --with beautifulsoup4==4.12.0 \
    pytest /tests/ -rA
```

Or for non-Python:
```bash
npm install --save-dev @testing-library/jest-dom supertest
```

---

## Conversion Checklist

Use this checklist for every conversion:

### Structure
- [ ] `task.toml` created with appropriate timeouts and resources
- [ ] `instruction.md` describes the task without leaking test details
- [ ] `environment/Dockerfile` provides the correct runtime
- [ ] `tests/test.sh` is the entry point and is executable
- [ ] Original test files are in `tests/` directory
- [ ] Test data files are included in `tests/` with absolute path references

### Path Fixes
- [ ] All relative paths converted to absolute (`/workdir/`, `/tests/`)
- [ ] `conftest.py` / shared fixtures updated with absolute paths
- [ ] Import paths updated (`PYTHONPATH`, `NODE_PATH`, etc.)
- [ ] Test data file references use `/tests/data/` prefix

### Reward Wiring
- [ ] `test.sh` writes reward to `/logs/verifier/reward.txt` or `/logs/verifier/reward.json`
- [ ] Reward is always written, even on test infrastructure failure
- [ ] Reward value is between 0.0 and 1.0
- [ ] Partial credit is calculated correctly (if applicable)

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

---

## Common Conversion Pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Relative imports in tests | `ModuleNotFoundError` | Add `PYTHONPATH=/workdir` or use absolute imports |
| Missing test dependencies | `ImportError` in verifier | Install in `test.sh` with pinned versions |
| Tests modify source files | Flaky results across runs | Use temp copies or reset state in setup |
| Hardcoded localhost ports | Port conflicts in container | Use dynamic ports or fixed non-conflicting ports |
| Tests assume specific OS | Fails in Docker container | Use compatible base image, install missing tools |
| No reward on crash | `NaN` reward in Harbor | Wrap test runner in `\|\| true`, always write reward |
| Tests leak expected answers | Agent "cheats" by reading tests | Tests are in `/tests/`, agent works in `/workdir/` — but don't put answers in instruction.md |
| conftest.py not copied | Fixtures not found | Include conftest.py in `tests/` directory |
| Test ordering dependency | Random failures | Use `pytest-ordering` or restructure tests |
| Large test data in repo | Slow Docker build | Use `.dockerignore` or download at test time |

---

## Batch Conversion Pattern

For converting multiple test files from a test suite into separate Harbor tasks:

```python
"""Script to batch-convert a test suite into Harbor tasks."""

import os
import shutil
from pathlib import Path

def convert_test_to_task(
    test_file: Path,
    source_dir: Path,
    output_dir: Path,
    base_image: str = "python:3.12-slim",
):
    task_name = test_file.stem.replace("test_", "")
    task_dir = output_dir / task_name

    # Create task structure
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "solution").mkdir(parents=True, exist_ok=True)

    # Copy test file
    shutil.copy2(test_file, task_dir / "tests" / test_file.name)

    # Generate test.sh
    (task_dir / "tests" / "test.sh").write_text(f"""#!/bin/bash
apt-get update && apt-get install -y curl
curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

export PYTHONPATH="/workdir:$PYTHONPATH"

uvx \\
  --with pytest==8.4.1 \\
  --with pytest-json-ctrf==0.3.5 \\
  pytest --ctrf /logs/verifier/ctrf.json /tests/{test_file.name} -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
""")

    # Generate task.toml
    (task_dir / "task.toml").write_text("""version = "1.0"

[metadata]
author_name = "Converted"
difficulty = "medium"
category = "programming"
tags = ["converted"]

[verifier]
timeout_sec = 180.0

[agent]
timeout_sec = 600.0

[environment]
build_timeout_sec = 600.0
cpus = 1
memory = "2G"
storage = "10G"
""")

    # Generate Dockerfile
    (task_dir / "environment" / "Dockerfile").write_text(f"""FROM {base_image}
WORKDIR /workdir
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl build-essential \\
    && rm -rf /var/lib/apt/lists/*
COPY . /workdir/
""")

    # Generate placeholder instruction.md
    (task_dir / "instruction.md").write_text(
        f"# {task_name.replace('_', ' ').title()}\n\n"
        "TODO: Write task instruction based on what the tests verify.\n"
    )

    return task_dir
```

---

## Validation Workflow

After conversion, validate with Harbor's built-in tools:

```bash
# 1. Validate task structure
harbor tasks validate ./converted-task/

# 2. Run with oracle agent (should pass — uses reference solution)
harbor run --dataset ./converted-task/ --agent oracle

# 3. Run with nop agent (should fail — no solution applied)
harbor run --dataset ./converted-task/ --agent nop

# 4. Run with a real agent to verify end-to-end
harbor run --dataset ./converted-task/ --agent claude-code --model anthropic/claude-opus-4-1
```

If the oracle agent fails, the test conversion has a bug. If the nop agent passes, the tests are not actually verifying agent work.
