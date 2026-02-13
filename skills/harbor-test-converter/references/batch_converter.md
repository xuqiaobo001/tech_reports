# Batch Converter for Harbor Tasks

Python script template for batch-converting multiple test files from an existing test suite into separate Harbor evaluation tasks.

## Usage

```python
from pathlib import Path
from batch_convert import convert_test_to_task

# Convert a single test file
convert_test_to_task(
    test_file=Path("tests/test_core.py"),
    source_dir=Path("src/"),
    output_dir=Path("harbor-tasks/"),
    base_image="python:3.12-slim",
)

# Batch convert all test files
for test_file in Path("tests/").glob("test_*.py"):
    convert_test_to_task(test_file, Path("src/"), Path("harbor-tasks/"))
```

## Script Template

```python
"""Batch-convert a test suite into Harbor tasks."""

import shutil
from pathlib import Path


def convert_test_to_task(
    test_file: Path,
    source_dir: Path,
    output_dir: Path,
    base_image: str = "python:3.12-slim",
    difficulty: str = "medium",
    verifier_timeout: float = 180.0,
    agent_timeout: float = 600.0,
):
    """Convert a single test file into a Harbor task directory.

    Args:
        test_file: Path to the original test file (e.g. tests/test_core.py)
        source_dir: Path to the project source code to copy into the task
        output_dir: Parent directory where the task folder will be created
        base_image: Docker base image for the task environment
        difficulty: Task difficulty (easy, medium, hard)
        verifier_timeout: Timeout in seconds for the verifier
        agent_timeout: Timeout in seconds for the agent
    """
    task_name = test_file.stem.replace("test_", "")
    task_dir = output_dir / task_name

    # Create task structure
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "solution").mkdir(parents=True, exist_ok=True)

    # Copy test file
    shutil.copy2(test_file, task_dir / "tests" / test_file.name)

    # Copy conftest.py if it exists alongside the test
    conftest = test_file.parent / "conftest.py"
    if conftest.exists():
        shutil.copy2(conftest, task_dir / "tests" / "conftest.py")

    # Copy test data directory if it exists
    data_dir = test_file.parent / "data"
    if data_dir.is_dir():
        shutil.copytree(data_dir, task_dir / "tests" / "data", dirs_exist_ok=True)

    # Generate test.sh
    (task_dir / "tests" / "test.sh").write_text(
        f"""#!/bin/bash

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
"""
    )

    # Generate task.toml
    (task_dir / "task.toml").write_text(
        f"""version = "1.0"

[metadata]
author_name = "Converted"
difficulty = "{difficulty}"
category = "programming"
tags = ["converted"]

[verifier]
timeout_sec = {verifier_timeout}

[agent]
timeout_sec = {agent_timeout}

[environment]
build_timeout_sec = 600.0
cpus = 1
memory = "2G"
storage = "10G"
"""
    )

    # Generate Dockerfile
    (task_dir / "environment" / "Dockerfile").write_text(
        f"""FROM {base_image}
WORKDIR /workdir
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl build-essential \\
    && rm -rf /var/lib/apt/lists/*
COPY . /workdir/
"""
    )

    # Copy source code into environment directory
    if source_dir.is_dir():
        shutil.copytree(
            source_dir,
            task_dir / "environment" / source_dir.name,
            dirs_exist_ok=True,
        )

    # Generate placeholder instruction.md
    title = task_name.replace("_", " ").title()
    (task_dir / "instruction.md").write_text(
        f"# {title}\\n\\n"
        "TODO: Write task instruction based on what the tests verify.\\n"
        "Do NOT reveal test implementation details.\\n"
    )

    print(f"Created task: {task_dir}")
    return task_dir


def batch_convert(
    test_dir: Path,
    source_dir: Path,
    output_dir: Path,
    pattern: str = "test_*.py",
    **kwargs,
):
    """Convert all matching test files in a directory.

    Args:
        test_dir: Directory containing test files
        source_dir: Project source code directory
        output_dir: Output directory for Harbor tasks
        pattern: Glob pattern for test files
        **kwargs: Additional arguments passed to convert_test_to_task
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    test_files = sorted(test_dir.glob(pattern))

    print(f"Found {len(test_files)} test files to convert")

    for test_file in test_files:
        convert_test_to_task(test_file, source_dir, output_dir, **kwargs)

    print(f"\\nDone. Created {len(test_files)} tasks in {output_dir}")
    print("\\nNext steps:")
    print("1. Edit each instruction.md with proper task descriptions")
    print("2. Validate: harbor tasks validate <task-path>")
    print("3. Test with oracle agent: harbor run --dataset <path> --agent oracle")
    print("4. Test with nop agent: harbor run --dataset <path> --agent nop")
```

## Validation After Batch Conversion

```bash
# Validate all converted tasks
for task in harbor-tasks/*/; do
  echo "Validating $task..."
  harbor tasks validate "$task"
done

# Run oracle agent on all tasks (should all pass)
harbor run --dataset harbor-tasks/ --agent oracle

# Run nop agent on all tasks (should all fail with reward=0)
harbor run --dataset harbor-tasks/ --agent nop
```
