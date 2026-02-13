# Harbor Test Case Generator

Generate complete, runnable test cases for the Harbor framework with proper structure, instructions, environments, and verification tests.

## Overview

This skill helps you create individual Harbor test cases by:
- Generating task configuration (task.toml)
- Writing clear instructions (instruction.md)
- Setting up Docker environments
- Creating verification tests (test.sh)
- Providing reference solutions (optional)

## When to Use

Use this skill when you need to:
- Create a new test case from scratch
- Design custom evaluation tasks for agents
- Build specific scenarios for testing agent capabilities
- Create tasks for coding, debugging, system administration, or QA

## Usage

When invoked, I will:
1. Ask about the task requirements (type, language, difficulty, objective)
2. Create the task directory structure in `tasks/{task_name}/`
3. Generate task.toml with appropriate resource allocation
4. Write clear, detailed instructions for the agent
5. Create a Dockerfile with necessary dependencies
6. Generate a verification script (test.sh) with reward calculation
7. Optionally create a reference solution

## Parameters

- **task_name**: Name/ID of the test case (e.g., "binary-search", "fix-memory-leak")
- **task_type**: Type of task (coding, debugging, system, qa, etc.)
- **difficulty**: Difficulty level (easy, medium, hard)
- **language**: Primary programming language (python, javascript, go, etc.)
- **output_dir**: Directory where task will be created (default: `tasks/`)

## Generated Structure

```
tasks/{task_name}/
├── task.toml           # Task configuration and metadata
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

## Resource Guidelines

I will automatically set appropriate resources based on difficulty:

| Difficulty | Timeout | Memory | CPU Cores |
|------------|---------|--------|-----------|
| Easy       | 600s    | 4GB    | 2         |
| Medium     | 1800s   | 8GB    | 2         |
| Hard       | 3600s   | 16GB   | 4         |

## Example

**User request:** "Create a test case for implementing binary search in Python"

**I will:**
1. Gather requirements:
   - Task type: coding
   - Language: Python
   - Difficulty: easy
   - Objective: Implement binary search with O(log n) complexity

2. Create `tasks/binary-search/` with:
   - **task.toml**: 600s timeout, 4GB memory, Python tags
   - **instruction.md**: Clear specification with function signature, requirements, and success criteria
   - **environment/Dockerfile**: Python 3.11 + pytest
   - **tests/test.sh**: Runs pytest, calculates reward based on test results

3. Generate instruction.md:
   ```markdown
   # Implement Binary Search

   ## Objective
   Implement a binary search function that finds an element in a sorted array.

   ## Requirements
   1. Function signature: `def binary_search(arr: list[int], target: int) -> int`
   2. Return index of target, or -1 if not found
   3. Time complexity: O(log n)
   4. Handle empty arrays and duplicates

   ## Success Criteria
   - All unit tests pass
   - Correct time complexity
   - Handles edge cases
   ```

4. Generate test.sh:
   ```bash
   #!/bin/bash
   set -euo pipefail

   mkdir -p /logs/verifier
   REWARD=0.0

   # Run pytest
   if python3 -m pytest /workspace/tests/ -v; then
       REWARD=1.0
   fi

   echo "$REWARD" > /logs/verifier/reward.txt
   ```

**Result:** A complete, runnable task that can be tested with:
```bash
harbor run --dataset tasks/binary-search --agent claude-code
```

## Task Types

### Coding Tasks
- Implement functions, classes, or modules
- Focus on correctness and efficiency
- Verification: Unit tests, code quality checks

### Debugging Tasks
- Find and fix bugs in existing code
- Pre-populate environment with buggy code
- Verification: Tests pass after fix

### System Administration Tasks
- Configure services or system settings
- Verification: Check system state, files, processes

### QA/Testing Tasks
- Write tests for existing code
- Verification: Check test coverage and quality

## Key Features

- **Automatic resource allocation**: Sets appropriate timeout/memory based on difficulty
- **Language-specific environments**: Generates Dockerfiles for Python, Node.js, Go, etc.
- **Incremental testing**: Test scripts support partial credit
- **Clear instructions**: Follows best practices for unambiguous task descriptions
- **Validation**: Ensures all required files are created and properly configured

## Verification Best Practices

The generated test.sh will:
- Award partial credit for incremental progress
- Write reward as float 0.0-1.0 to `/logs/verifier/reward.txt`
- Provide clear pass/fail messages
- Handle errors gracefully
- Test edge cases

## Notes

- Instructions should be specific and unambiguous
- Include all necessary context and examples
- Specify exact success criteria
- Test with oracle agent first to verify task is solvable
- Adjust resource limits based on actual usage

## Related

- Dataset Converter Skill: For batch converting existing datasets
- Adapter Generator Skill: For creating reusable adapters
- Harbor Task Format: `docs/rfcs/task-format.md`
- Example Tasks: `examples/tasks/`
