# Harbor CLI Adapter Generator Skill

## Description

This skill helps you quickly generate new CLI adapter scaffolding for Harbor framework. It creates the complete directory structure and boilerplate code needed to integrate external benchmarks into Harbor.

## Usage

When invoked, this skill will guide you through creating a new adapter with:
- Adapter class implementation
- CLI entry point
- Task conversion logic
- Documentation
- Test templates

## Parameters

- `adapter_name`: Name of the adapter (e.g., "my-benchmark")
- `source_format`: Format of source data (json, csv, api, etc.)
- `output_dir`: Directory where adapter will be created (default: `adapters/`)

## Execution Steps

### 1. Gather Requirements

Ask the user:
- What is the name of the benchmark/tool you want to adapt?
- What format is the source data in? (JSON, CSV, API, database, etc.)
- What is the typical structure of a task in this benchmark?
- Are there any special dependencies or environment requirements?

### 2. Create Directory Structure

Create the following structure in `adapters/{adapter_name}/`:

```
{adapter_name}/
├── adapter.py          # Main conversion logic
├── run_adapter.py      # CLI entry point
├── README.md           # Documentation
├── requirements.txt    # Python dependencies (if needed)
└── template/           # Task template files (optional)
    ├── Dockerfile
    ├── test.sh
    └── instruction.md.j2
```

### 3. Generate adapter.py

Create the main adapter class with the following structure:

```python
"""
{AdapterName} Adapter for Harbor

Converts {adapter_name} benchmark tasks to Harbor format.
"""

import json
from pathlib import Path
from typing import Any


class {AdapterName}Adapter:
    """Adapter for {adapter_name} benchmark."""

    def __init__(self, source_dir: Path, output_dir: Path):
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)

    def load_source_data(self) -> list[dict[str, Any]]:
        """Load source benchmark data."""
        # TODO: Implement based on source format
        pass

    def convert_task(self, source_task: dict[str, Any]) -> dict[str, Any]:
        """Convert a single source task to Harbor format."""
        return {
            "task_id": source_task.get("id"),
            "instruction": source_task.get("description"),
            "timeout_sec": 3600,
            "memory_gb": 8,
            "cpu_cores": 2,
        }

    def write_task(self, task: dict[str, Any], task_dir: Path):
        """Write a Harbor task to disk."""
        task_dir.mkdir(parents=True, exist_ok=True)

        # Write task.toml
        self._write_task_toml(task, task_dir)

        # Write instruction.md
        self._write_instruction(task, task_dir)

        # Create environment/
        self._create_environment(task, task_dir)

        # Create tests/
        self._create_tests(task, task_dir)

    def _write_task_toml(self, task: dict, task_dir: Path):
        """Write task.toml configuration."""
        with open(task_dir / "task.toml", "w") as f:
            f.write(f'''# {task["task_id"]}

[metadata]
task_id = "{task["task_id"]}"
source = "{adapter_name}"

[resources]
timeout_sec = {task["timeout_sec"]}
memory_gb = {task["memory_gb"]}
cpu_cores = {task["cpu_cores"]}
''')

    def _write_instruction(self, task: dict, task_dir: Path):
        """Write instruction.md."""
        with open(task_dir / "instruction.md", "w") as f:
            f.write(f"# Task: {task['task_id']}\\n\\n")
            f.write(task["instruction"])

    def _create_environment(self, task: dict, task_dir: Path):
        """Create environment/ directory with Dockerfile."""
        env_dir = task_dir / "environment"
        env_dir.mkdir(exist_ok=True)

        with open(env_dir / "Dockerfile", "w") as f:
            f.write('''FROM ubuntu:22.04

# Install basic dependencies
RUN apt-get update && apt-get install -y \\
    curl \\
    git \\
    python3 \\
    python3-pip \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
''')

    def _create_tests(self, task: dict, task_dir: Path):
        """Create tests/ directory with test.sh."""
        tests_dir = task_dir / "tests"
        tests_dir.mkdir(exist_ok=True)

        test_sh = tests_dir / "test.sh"
        with open(test_sh, "w") as f:
            f.write('''#!/bin/bash
set -euo pipefail

# TODO: Implement test logic
# Write reward (0.0 to 1.0) to /logs/verifier/reward.txt

mkdir -p /logs/verifier
echo "0.0" > /logs/verifier/reward.txt
''')
        test_sh.chmod(0o755)

    def run(self):
        """Run the adapter conversion."""
        print(f"Loading data from {self.source_dir}")
        source_data = self.load_source_data()

        print(f"Converting {len(source_data)} tasks...")
        for source_task in source_data:
            task = self.convert_task(source_task)
            task_dir = self.output_dir / task["task_id"]
            self.write_task(task, task_dir)

        print(f"✓ Converted {len(source_data)} tasks to {self.output_dir}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert {adapter_name} benchmark to Harbor format"
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)

    args = parser.parse_args()

    adapter = {AdapterName}Adapter(args.source_dir, args.output_dir)
    adapter.run()


if __name__ == "__main__":
    main()
```

### 4. Generate run_adapter.py

Create a simple CLI entry point:

```python
#!/usr/bin/env python3
"""Run {adapter_name} adapter."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from adapter import {AdapterName}Adapter


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()

    adapter = {AdapterName}Adapter(args.source_dir, args.output_dir)
    adapter.run()


if __name__ == "__main__":
    main()
```

### 5. Generate README.md

Create comprehensive documentation:

```markdown
# {AdapterName} Adapter for Harbor

Converts {adapter_name} benchmark tasks to Harbor format.

## Installation

No additional dependencies required beyond Harbor's base requirements.

## Usage

```bash
python run_adapter.py \\
    --source-dir /path/to/{adapter_name}/data \\
    --output-dir /path/to/harbor/tasks
```

## Source Data Format

Describe the expected format of source data here.

## Task Conversion

Explain how source tasks are mapped to Harbor format:
- Task ID mapping
- Instruction extraction
- Environment setup
- Test verification

## Testing

```bash
# Test the adapter
python run_adapter.py --source-dir ./test_data --output-dir ./test_output

# Verify generated tasks
harbor run --dataset ./test_output --agent oracle
```

## License

Apache License 2.0
```

### 6. Customize Based on Source Format

Based on the user's source format, customize the `load_source_data()` method:

**For JSON:**
```python
def load_source_data(self) -> list[dict[str, Any]]:
    with open(self.source_dir / "data.json") as f:
        return json.load(f)
```

**For CSV:**
```python
import csv

def load_source_data(self) -> list[dict[str, Any]]:
    with open(self.source_dir / "data.csv") as f:
        return list(csv.DictReader(f))
```

**For API:**
```python
import requests

def load_source_data(self) -> list[dict[str, Any]]:
    response = requests.get(f"{self.api_url}/tasks")
    return response.json()
```

### 7. Add Dependencies

If needed, create `requirements.txt`:

```
requests>=2.31.0  # For API adapters
pandas>=2.0.0     # For CSV/Excel adapters
```

## Examples

### Example 1: JSON-based Benchmark

```bash
# Source data: benchmark.json
# [
#   {"id": "task1", "description": "Do X", "tests": [...]}
# ]

# Generate adapter
# Follow prompts to create adapters/my-benchmark/

# Run adapter
cd adapters/my-benchmark
python run_adapter.py --source-dir ./data --output-dir ../../tasks/my-benchmark
```

### Example 2: API-based Benchmark

```bash
# Source: REST API at https://api.benchmark.com

# Customize load_source_data() to fetch from API
# Run adapter with API credentials
export BENCHMARK_API_KEY=xxx
python run_adapter.py --source-dir . --output-dir ../../tasks/api-benchmark
```

## Best Practices

1. **Validate source data** before conversion
2. **Preserve original metadata** in task.toml
3. **Test on small subset** before full conversion
4. **Document task ID mapping** for traceability
5. **Include example tasks** in README
6. **Handle errors gracefully** with clear messages

## Troubleshooting

- **Missing source data**: Check file paths and permissions
- **Invalid task format**: Validate against Harbor task schema
- **Environment issues**: Ensure Dockerfile has all dependencies
- **Test failures**: Verify test.sh writes to correct path

## Related

- Harbor Adapter Documentation: `docs/adapters/`
- Existing Adapters: `adapters/*/`
- Task Format: `docs/rfcs/task-format.md`
