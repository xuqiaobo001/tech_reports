# Harbor Test Case Set Generator Skill

## Description

This skill helps you quickly generate test case sets that can run in Harbor framework. It creates complete, runnable task directories with proper structure, instructions, environments, and verification tests.

## Usage

When invoked, this skill will guide you through creating test cases with:
- Task configuration (task.toml)
- Natural language instructions (instruction.md)
- Docker environment setup
- Verification tests (test.sh)
- Optional reference solutions

## Parameters

- `task_name`: Name/ID of the test case
- `task_type`: Type of task (coding, debugging, system, qa, etc.)
- `difficulty`: Difficulty level (easy, medium, hard)
- `language`: Primary programming language (python, javascript, go, etc.)
- `output_dir`: Directory where task will be created (default: `tasks/`)

## Execution Steps

### 1. Gather Task Requirements

Ask the user:
- What is the task about? (Brief description)
- What type of task is this? (coding, debugging, system administration, QA, etc.)
- What programming language(s) are involved?
- What is the difficulty level?
- What should the agent accomplish?
- How should success be verified?
- What dependencies are needed?

### 2. Create Task Directory Structure

Create the following structure in `tasks/{task_name}/`:

```
{task_name}/
├── task.toml           # Task configuration
├── instruction.md      # Task description for agent
├── environment/        # Environment setup
│   ├── Dockerfile
│   └── setup.sh (optional)
├── tests/              # Verification tests
│   ├── test.sh
│   └── verify.py (optional)
└── solution/           # Reference solution (optional)
    └── solution.md
```

### 3. Generate task.toml

Create task configuration with appropriate resources:

```toml
# Task: {task_name}

[metadata]
task_id = "{task_name}"
title = "{task_title}"
description = "{brief_description}"
difficulty = "{difficulty}"
tags = ["{language}", "{task_type}"]

[resources]
timeout_sec = {timeout}
memory_gb = {memory}
cpu_cores = {cores}

[environment]
base_image = "ubuntu:22.04"
```

**Resource Guidelines:**
- Easy tasks: 600s timeout, 4GB RAM, 2 cores
- Medium tasks: 1800s timeout, 8GB RAM, 2 cores
- Hard tasks: 3600s timeout, 16GB RAM, 4 cores

### 4. Generate instruction.md

Create clear, detailed instructions for the agent:

```markdown
# {task_title}

## Objective

{Clear statement of what needs to be accomplished}

## Background

{Context and background information}

## Requirements

1. {Requirement 1}
2. {Requirement 2}
3. {Requirement 3}

## Constraints

- {Constraint 1}
- {Constraint 2}

## Success Criteria

The task is considered complete when:
- {Criterion 1}
- {Criterion 2}
- All tests pass

## Environment

- Operating System: Ubuntu 22.04
- Available tools: {list of tools}
- Working directory: /workspace

## Hints

{Optional hints for the agent}
```

**Instruction Best Practices:**
- Be specific and unambiguous
- Include all necessary context
- Specify exact success criteria
- Mention any edge cases
- Provide examples if helpful

### 5. Generate Dockerfile

Create appropriate environment based on task type:

**For Python tasks:**
```dockerfile
FROM ubuntu:22.04

# Install Python and common tools
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip3 install --no-cache-dir \
    pytest \
    requests \
    numpy

WORKDIR /workspace
```

**For Node.js tasks:**
```dockerfile
FROM ubuntu:22.04

# Install Node.js
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
```

**For Go tasks:**
```dockerfile
FROM ubuntu:22.04

# Install Go
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && curl -OL https://go.dev/dl/go1.21.0.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz \
    && rm go1.21.0.linux-amd64.tar.gz

ENV PATH="/usr/local/go/bin:${PATH}"

WORKDIR /workspace
```

**For System tasks:**
```dockerfile
FROM ubuntu:22.04

# Install system tools
RUN apt-get update && apt-get install -y \
    bash \
    coreutils \
    grep \
    sed \
    awk \
    curl \
    wget \
    git \
    vim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
```

### 6. Generate test.sh

Create verification script that checks task completion:

```bash
#!/bin/bash
set -euo pipefail

# Test script for {task_name}

# Initialize reward
REWARD=0.0

# Create logs directory
mkdir -p /logs/verifier

# Run tests
echo "Running verification tests..."

# Test 1: Check if required files exist
if [ -f "/workspace/{expected_file}" ]; then
    echo "✓ Required file exists"
    REWARD=$(echo "$REWARD + 0.2" | bc)
else
    echo "✗ Required file missing"
fi

# Test 2: Run functional tests
if {test_command}; then
    echo "✓ Functional tests passed"
    REWARD=$(echo "$REWARD + 0.5" | bc)
else
    echo "✗ Functional tests failed"
fi

# Test 3: Verify output correctness
if {verification_command}; then
    echo "✓ Output is correct"
    REWARD=$(echo "$REWARD + 0.3" | bc)
else
    echo "✗ Output is incorrect"
fi

# Write final reward
echo "$REWARD" > /logs/verifier/reward.txt
echo "Final reward: $REWARD"
```

**Test Script Best Practices:**
- Test incrementally (partial credit)
- Provide clear pass/fail messages
- Write reward as float 0.0-1.0
- Handle errors gracefully
- Test edge cases

### 7. Generate Verification Logic

Based on task type, create appropriate verification:

**For Coding Tasks:**
```bash
# Run unit tests
python3 -m pytest tests/ -v
TEST_EXIT=$?

if [ $TEST_EXIT -eq 0 ]; then
    REWARD=1.0
else
    REWARD=0.0
fi
```

**For Output Verification:**
```bash
# Compare output with expected
EXPECTED="expected_output.txt"
ACTUAL="/workspace/output.txt"

if diff -q "$EXPECTED" "$ACTUAL" > /dev/null; then
    REWARD=1.0
else
    # Partial credit for partial match
    SIMILARITY=$(diff "$EXPECTED" "$ACTUAL" | wc -l)
    REWARD=$(echo "scale=2; 1.0 - ($SIMILARITY * 0.1)" | bc)
fi
```

**For System State Verification:**
```bash
# Check if service is running
if systemctl is-active --quiet myservice; then
    REWARD=$(echo "$REWARD + 0.5" | bc)
fi

# Check if configuration is correct
if grep -q "expected_config" /etc/myapp/config; then
    REWARD=$(echo "$REWARD + 0.5" | bc)
fi
```

**For Code Quality:**
```bash
# Run linter
pylint /workspace/*.py > /tmp/lint.txt 2>&1
LINT_SCORE=$(grep "Your code has been rated" /tmp/lint.txt | awk '{print $7}' | cut -d'/' -f1)

# Convert score to reward (8.0+ = full credit)
REWARD=$(echo "scale=2; $LINT_SCORE / 10.0" | bc)
```

### 8. Add Optional Components

**Reference Solution (solution/solution.md):**
```markdown
# Solution for {task_name}

## Approach

{Explanation of the solution approach}

## Implementation

```{language}
{reference implementation code}
```

## Explanation

{Step-by-step explanation}

## Time Complexity

O({complexity})

## Space Complexity

O({complexity})
```

**Setup Script (environment/setup.sh):**
```bash
#!/bin/bash
# Additional environment setup

# Clone repository
git clone https://github.com/example/repo.git /workspace/repo

# Install dependencies
pip3 install -r /workspace/requirements.txt

# Create necessary directories
mkdir -p /workspace/data /workspace/output
```

### 9. Validate Task Structure

Before finalizing, verify:
- [ ] task.toml has all required fields
- [ ] instruction.md is clear and complete
- [ ] Dockerfile installs all dependencies
- [ ] test.sh is executable (chmod +x)
- [ ] test.sh writes to /logs/verifier/reward.txt
- [ ] Reward is between 0.0 and 1.0
- [ ] Task can be run with `harbor run`

### 10. Test the Task

Run a quick test to ensure everything works:

```bash
# Test with oracle agent (should get 1.0 reward)
harbor run \
    --dataset tasks/{task_name} \
    --agent oracle \
    --environment docker

# Test with actual agent
harbor run \
    --dataset tasks/{task_name} \
    --agent claude-code \
    --model anthropic/claude-opus-4-1
```

## Task Type Templates

### Coding Task Template

**Objective:** Implement a function/class/module

**Structure:**
- instruction.md: Detailed specification with examples
- tests/: Unit tests using pytest/jest/go test
- Verification: Run test suite, check coverage

**Example:**
```markdown
# Implement Binary Search

Implement a binary search function that finds an element in a sorted array.

## Function Signature

```python
def binary_search(arr: list[int], target: int) -> int:
    """
    Returns the index of target in arr, or -1 if not found.
    """
    pass
```

## Requirements
- Time complexity: O(log n)
- Handle empty arrays
- Handle duplicates (return first occurrence)
```

### Debugging Task Template

**Objective:** Find and fix bugs in existing code

**Structure:**
- instruction.md: Description of buggy behavior
- environment/: Pre-populate with buggy code
- tests/: Tests that currently fail
- Verification: Tests pass after fix

**Example:**
```markdown
# Fix Memory Leak

The application in /workspace/app.py has a memory leak. Find and fix it.

## Symptoms
- Memory usage grows over time
- Eventually crashes with OOM

## Requirements
- Fix the leak without changing functionality
- All existing tests must pass
```

### System Administration Task Template

**Objective:** Configure system or service

**Structure:**
- instruction.md: System configuration requirements
- environment/: Base system with services
- tests/: Verify configuration and service state
- Verification: Check files, processes, network

**Example:**
```markdown
# Configure Nginx Reverse Proxy

Set up Nginx as a reverse proxy for a backend service.

## Requirements
- Listen on port 80
- Proxy to localhost:8000
- Enable gzip compression
- Add security headers
```

### QA/Testing Task Template

**Objective:** Write tests for existing code

**Structure:**
- instruction.md: Code to test and coverage requirements
- environment/: Pre-populate with code to test
- tests/: Starter test file
- Verification: Check test coverage and quality

**Example:**
```markdown
# Write Tests for Calculator

Write comprehensive tests for the calculator module in /workspace/calc.py.

## Requirements
- Achieve 90%+ code coverage
- Test edge cases (division by zero, etc.)
- Use pytest framework
```

## Examples

### Example 1: Simple Python Coding Task

```bash
# Create task
Task name: fibonacci
Task type: coding
Language: python
Difficulty: easy

# Generated structure:
tasks/fibonacci/
├── task.toml (timeout: 600s, memory: 4GB)
├── instruction.md (implement fibonacci function)
├── environment/Dockerfile (Python 3.11 + pytest)
└── tests/test.sh (run pytest, check correctness)
```

### Example 2: Complex System Task

```bash
# Create task
Task name: docker-compose-setup
Task type: system
Language: yaml
Difficulty: hard

# Generated structure:
tasks/docker-compose-setup/
├── task.toml (timeout: 3600s, memory: 16GB)
├── instruction.md (setup multi-container app)
├── environment/Dockerfile (Docker-in-Docker)
└── tests/test.sh (verify containers, networking, volumes)
```

### Example 3: Debugging Task

```bash
# Create task
Task name: fix-race-condition
Task type: debugging
Language: go
Difficulty: medium

# Generated structure:
tasks/fix-race-condition/
├── task.toml (timeout: 1800s, memory: 8GB)
├── instruction.md (describe race condition symptoms)
├── environment/
│   ├── Dockerfile (Go 1.21)
│   └── setup.sh (copy buggy code)
└── tests/test.sh (run with race detector)
```

## Best Practices

### Instruction Writing
1. **Be specific**: Avoid ambiguous requirements
2. **Provide context**: Explain why the task matters
3. **Include examples**: Show expected input/output
4. **Specify constraints**: Time/space complexity, style requirements
5. **Define success clearly**: Exact criteria for completion

### Test Design
1. **Test incrementally**: Award partial credit
2. **Cover edge cases**: Empty input, large input, invalid input
3. **Be deterministic**: Tests should always produce same result
4. **Provide feedback**: Clear pass/fail messages
5. **Handle errors**: Don't crash on unexpected output

### Environment Setup
1. **Minimize image size**: Only install necessary packages
2. **Pin versions**: Ensure reproducibility
3. **Clean up**: Remove apt cache, temp files
4. **Use layers wisely**: Optimize Docker layer caching
5. **Document dependencies**: Comment why each package is needed

### Resource Allocation
1. **Start conservative**: Can always increase if needed
2. **Monitor usage**: Check actual resource consumption
3. **Consider task type**: System tasks need more resources
4. **Set reasonable timeouts**: Allow for agent thinking time
5. **Test with limits**: Ensure task completes within constraints

## Troubleshooting

### Task Fails Immediately
- Check Dockerfile syntax
- Verify all dependencies are installed
- Ensure test.sh is executable
- Check file paths in test.sh

### Tests Don't Run
- Verify test.sh has correct shebang
- Check permissions (chmod +x)
- Ensure test commands are available in environment
- Check for syntax errors in test.sh

### Reward Not Written
- Ensure /logs/verifier directory is created
- Check write permissions
- Verify reward calculation logic
- Ensure script doesn't exit early

### Agent Can't Complete Task
- Instruction may be unclear or ambiguous
- Task may be too difficult
- Missing necessary tools in environment
- Timeout may be too short

## Related

- Harbor Task Format: `docs/rfcs/task-format.md`
- Example Tasks: `examples/tasks/`
- Verification Guide: `docs/verification.md`
- ATIF Trajectory Format: `docs/rfcs/atif.md`
