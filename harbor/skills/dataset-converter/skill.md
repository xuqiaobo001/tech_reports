# Harbor Dataset Converter Skill

## Description

This skill helps you quickly convert existing datasets from various formats into Harbor-compatible task format. It supports multiple source formats including JSON, CSV, JSONL, and common benchmark formats.

## Usage

When invoked, this skill will guide you through converting datasets with:
- Automatic format detection
- Batch task generation
- Field mapping configuration
- Validation and verification
- Progress tracking

## Parameters

- `source_path`: Path to source dataset (file or directory)
- `source_format`: Format of source data (json, csv, jsonl, swebench, etc.)
- `output_dir`: Directory where Harbor tasks will be created
- `mapping_config`: Optional JSON file with field mappings
- `batch_size`: Number of tasks to process at once (default: 100)

## Execution Steps

### 1. Analyze Source Dataset

Ask the user:
- Where is the source dataset located?
- What format is it in? (JSON, CSV, JSONL, custom)
- How many tasks/records are in the dataset?
- What fields contain the task information?
- Are there any special requirements or constraints?

### 2. Detect and Validate Format

Automatically detect the source format and validate structure:

```python
import json
import csv
from pathlib import Path

def detect_format(source_path: Path) -> str:
    """Detect dataset format from file extension and content."""
    if source_path.is_dir():
        # Check for common benchmark structures
        if (source_path / "tasks.json").exists():
            return "json"
        elif (source_path / "dataset.jsonl").exists():
            return "jsonl"
        else:
            return "directory"

    suffix = source_path.suffix.lower()
    if suffix == ".json":
        return "json"
    elif suffix == ".csv":
        return "csv"
    elif suffix == ".jsonl":
        return "jsonl"
    else:
        # Try to detect from content
        with open(source_path) as f:
            first_line = f.readline()
            try:
                json.loads(first_line)
                return "jsonl"
            except:
                return "unknown"

def validate_dataset(source_path: Path, format: str) -> dict:
    """Validate dataset and return statistics."""
    if format == "json":
        with open(source_path) as f:
            data = json.load(f)
            return {
                "format": "json",
                "count": len(data) if isinstance(data, list) else 1,
                "sample": data[0] if isinstance(data, list) else data,
                "fields": list(data[0].keys()) if isinstance(data, list) else list(data.keys())
            }
    elif format == "jsonl":
        count = 0
        sample = None
        with open(source_path) as f:
            for line in f:
                if count == 0:
                    sample = json.loads(line)
                count += 1
        return {
            "format": "jsonl",
            "count": count,
            "sample": sample,
            "fields": list(sample.keys()) if sample else []
        }
    elif format == "csv":
        with open(source_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            return {
                "format": "csv",
                "count": len(rows),
                "sample": rows[0] if rows else None,
                "fields": list(rows[0].keys()) if rows else []
            }
```

### 3. Configure Field Mapping

Create or load field mapping configuration:

```json
{
  "task_id_field": "id",
  "instruction_field": "description",
  "metadata_fields": ["source", "difficulty", "tags"],
  "timeout_field": "timeout_seconds",
  "default_timeout": 3600,
  "default_memory_gb": 8,
  "default_cpu_cores": 2,
  "environment": {
    "base_image": "ubuntu:22.04",
    "setup_commands": []
  },
  "test_template": "default"
}
```

**Common Field Mappings:**

| Source Field | Harbor Field | Notes |
|--------------|--------------|-------|
| `id`, `task_id`, `instance_id` | `task_id` | Unique identifier |
| `description`, `prompt`, `instruction` | `instruction` | Task description |
| `problem_statement`, `question` | `instruction` | Alternative names |
| `test_cases`, `tests` | `tests/` | Test data |
| `solution`, `reference` | `solution/` | Reference solution |
| `language`, `lang` | `metadata.language` | Programming language |
| `difficulty`, `level` | `metadata.difficulty` | Difficulty level |

### 4. Create Conversion Script

Generate a Python script to perform the conversion:

```python
#!/usr/bin/env python3
"""
Dataset Converter for Harbor

Converts {source_format} dataset to Harbor task format.
"""

import json
import csv
from pathlib import Path
from typing import Any, Iterator


class DatasetConverter:
    """Convert external dataset to Harbor format."""

    def __init__(
        self,
        source_path: Path,
        output_dir: Path,
        mapping_config: dict[str, Any]
    ):
        self.source_path = Path(source_path)
        self.output_dir = Path(output_dir)
        self.mapping = mapping_config

    def load_records(self) -> Iterator[dict[str, Any]]:
        """Load records from source dataset."""
        format = self.detect_format()

        if format == "json":
            with open(self.source_path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    yield from data
                else:
                    yield data

        elif format == "jsonl":
            with open(self.source_path) as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)

        elif format == "csv":
            with open(self.source_path) as f:
                yield from csv.DictReader(f)

    def detect_format(self) -> str:
        """Detect source format."""
        suffix = self.source_path.suffix.lower()
        if suffix == ".json":
            return "json"
        elif suffix == ".jsonl":
            return "jsonl"
        elif suffix == ".csv":
            return "csv"
        else:
            raise ValueError(f"Unsupported format: {suffix}")

    def map_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Map source record to Harbor task format."""
        # Extract task ID
        task_id = self._get_field(record, self.mapping["task_id_field"])

        # Extract instruction
        instruction = self._get_field(record, self.mapping["instruction_field"])

        # Extract metadata
        metadata = {}
        for field in self.mapping.get("metadata_fields", []):
            if field in record:
                metadata[field] = record[field]

        # Get resource configuration
        timeout = self._get_field(
            record,
            self.mapping.get("timeout_field"),
            default=self.mapping["default_timeout"]
        )
        memory_gb = self.mapping.get("default_memory_gb", 8)
        cpu_cores = self.mapping.get("default_cpu_cores", 2)

        return {
            "task_id": task_id,
            "instruction": instruction,
            "metadata": metadata,
            "timeout_sec": timeout,
            "memory_gb": memory_gb,
            "cpu_cores": cpu_cores,
            "raw_record": record  # Keep original for reference
        }

    def _get_field(self, record: dict, field: str | list[str], default=None):
        """Get field value, trying multiple field names."""
        if isinstance(field, str):
            return record.get(field, default)

        # Try multiple field names
        for f in field:
            if f in record:
                return record[f]
        return default

    def write_task(self, task: dict[str, Any]):
        """Write Harbor task to disk."""
        task_id = task["task_id"]
        task_dir = self.output_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Write task.toml
        self._write_task_toml(task, task_dir)

        # Write instruction.md
        self._write_instruction(task, task_dir)

        # Create environment/
        self._create_environment(task, task_dir)

        # Create tests/
        self._create_tests(task, task_dir)

        # Write raw record for reference
        with open(task_dir / "raw_record.json", "w") as f:
            json.dump(task["raw_record"], f, indent=2)

    def _write_task_toml(self, task: dict, task_dir: Path):
        """Write task.toml configuration."""
        with open(task_dir / "task.toml", "w") as f:
            f.write(f'''# Task: {task["task_id"]}

[metadata]
task_id = "{task["task_id"]}"
''')
            # Add metadata fields
            for key, value in task["metadata"].items():
                if isinstance(value, str):
                    f.write(f'{key} = "{value}"\n')
                elif isinstance(value, list):
                    f.write(f'{key} = {json.dumps(value)}\n')
                else:
                    f.write(f'{key} = {value}\n')

            f.write(f'''
[resources]
timeout_sec = {task["timeout_sec"]}
memory_gb = {task["memory_gb"]}
cpu_cores = {task["cpu_cores"]}
''')

    def _write_instruction(self, task: dict, task_dir: Path):
        """Write instruction.md."""
        with open(task_dir / "instruction.md", "w") as f:
            f.write(f"# Task: {task['task_id']}\n\n")
            f.write(task["instruction"])

    def _create_environment(self, task: dict, task_dir: Path):
        """Create environment/ directory."""
        env_dir = task_dir / "environment"
        env_dir.mkdir(exist_ok=True)

        base_image = self.mapping["environment"].get("base_image", "ubuntu:22.04")
        setup_commands = self.mapping["environment"].get("setup_commands", [])

        with open(env_dir / "Dockerfile", "w") as f:
            f.write(f"FROM {base_image}\n\n")
            f.write("# Install basic dependencies\n")
            f.write("RUN apt-get update && apt-get install -y \\\n")
            f.write("    curl \\\n")
            f.write("    git \\\n")
            f.write("    python3 \\\n")
            f.write("    python3-pip \\\n")
            f.write("    && rm -rf /var/lib/apt/lists/*\n\n")

            if setup_commands:
                f.write("# Custom setup commands\n")
                for cmd in setup_commands:
                    f.write(f"RUN {cmd}\n")

            f.write("\nWORKDIR /workspace\n")

    def _create_tests(self, task: dict, task_dir: Path):
        """Create tests/ directory."""
        tests_dir = task_dir / "tests"
        tests_dir.mkdir(exist_ok=True)

        test_sh = tests_dir / "test.sh"
        with open(test_sh, "w") as f:
            f.write('''#!/bin/bash
set -euo pipefail

# TODO: Implement test logic based on task requirements
# This is a placeholder - customize based on your dataset

mkdir -p /logs/verifier

# Default: assume task is incomplete
REWARD=0.0

# Add your verification logic here
# Example:
# if [ -f "/workspace/solution.py" ]; then
#     python3 /workspace/solution.py > /tmp/output.txt
#     if diff -q /tmp/output.txt /workspace/expected_output.txt; then
#         REWARD=1.0
#     fi
# fi

echo "$REWARD" > /logs/verifier/reward.txt
''')
        test_sh.chmod(0o755)

    def convert(self, batch_size: int = 100):
        """Convert entire dataset."""
        print(f"Converting dataset from {self.source_path}")
        print(f"Output directory: {self.output_dir}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for record in self.load_records():
            task = self.map_record(record)
            self.write_task(task)
            count += 1

            if count % batch_size == 0:
                print(f"  Converted {count} tasks...")

        print(f"✓ Successfully converted {count} tasks")
        return count


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert dataset to Harbor format"
    )
    parser.add_argument(
        "source_path",
        type=Path,
        help="Path to source dataset"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory where Harbor tasks will be written"
    )
    parser.add_argument(
        "--mapping-config",
        type=Path,
        help="JSON file with field mapping configuration"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of tasks to process at once"
    )

    args = parser.parse_args()

    # Load mapping config
    if args.mapping_config:
        with open(args.mapping_config) as f:
            mapping = json.load(f)
    else:
        # Use default mapping
        mapping = {
            "task_id_field": "id",
            "instruction_field": "description",
            "metadata_fields": ["source", "difficulty"],
            "default_timeout": 3600,
            "default_memory_gb": 8,
            "default_cpu_cores": 2,
            "environment": {
                "base_image": "ubuntu:22.04",
                "setup_commands": []
            }
        }

    converter = DatasetConverter(args.source_path, args.output_dir, mapping)
    converter.convert(batch_size=args.batch_size)


if __name__ == "__main__":
    main()
```

### 5. Create Mapping Configuration Template

Generate a template mapping configuration file:

```json
{
  "task_id_field": "id",
  "instruction_field": "description",
  "metadata_fields": [
    "source",
    "difficulty",
    "language",
    "tags"
  ],
  "timeout_field": null,
  "default_timeout": 3600,
  "default_memory_gb": 8,
  "default_cpu_cores": 2,
  "environment": {
    "base_image": "ubuntu:22.04",
    "setup_commands": [
      "pip3 install pytest requests"
    ]
  },
  "test_template": "default",
  "field_transformations": {
    "instruction": {
      "type": "template",
      "template": "# Task\n\n{description}\n\n## Requirements\n\n{requirements}"
    },
    "task_id": {
      "type": "prefix",
      "prefix": "task-"
    }
  }
}
```

### 6. Handle Common Dataset Formats

Provide format-specific handlers:

**SWE-Bench Format:**
```python
def convert_swebench(record: dict) -> dict:
    return {
        "task_id": record["instance_id"],
        "instruction": f"{record['problem_statement']}\n\n{record.get('hints_text', '')}",
        "metadata": {
            "repo": record["repo"],
            "base_commit": record["base_commit"],
            "version": record.get("version", ""),
        },
        "timeout_sec": 3600,
        "memory_gb": 16,
        "cpu_cores": 4,
    }
```

**HumanEval Format:**
```python
def convert_humaneval(record: dict) -> dict:
    return {
        "task_id": record["task_id"],
        "instruction": record["prompt"],
        "metadata": {
            "entry_point": record["entry_point"],
            "canonical_solution": record["canonical_solution"],
        },
        "timeout_sec": 600,
        "memory_gb": 4,
        "cpu_cores": 2,
    }
```

**MMLU Format:**
```python
def convert_mmlu(record: dict) -> dict:
    choices = "\n".join([
        f"{chr(65+i)}. {choice}"
        for i, choice in enumerate(record["choices"])
    ])

    return {
        "task_id": f"mmlu-{record['subject']}-{record['id']}",
        "instruction": f"{record['question']}\n\n{choices}\n\nProvide your answer (A, B, C, or D).",
        "metadata": {
            "subject": record["subject"],
            "correct_answer": record["answer"],
        },
        "timeout_sec": 300,
        "memory_gb": 4,
        "cpu_cores": 1,
    }
```

### 7. Validate Converted Tasks

After conversion, validate the generated tasks:

```python
def validate_converted_tasks(output_dir: Path) -> dict:
    """Validate all converted tasks."""
    issues = []
    valid_count = 0

    for task_dir in output_dir.iterdir():
        if not task_dir.is_dir():
            continue

        # Check required files
        required_files = [
            "task.toml",
            "instruction.md",
            "environment/Dockerfile",
            "tests/test.sh"
        ]

        for file in required_files:
            file_path = task_dir / file
            if not file_path.exists():
                issues.append(f"{task_dir.name}: Missing {file}")

        # Check test.sh is executable
        test_sh = task_dir / "tests/test.sh"
        if test_sh.exists() and not os.access(test_sh, os.X_OK):
            issues.append(f"{task_dir.name}: test.sh not executable")

        if not issues:
            valid_count += 1

    return {
        "total": len(list(output_dir.iterdir())),
        "valid": valid_count,
        "issues": issues
    }
```

### 8. Generate Conversion Report

Create a summary report of the conversion:

```markdown
# Dataset Conversion Report

## Summary

- **Source**: {source_path}
- **Format**: {source_format}
- **Total Records**: {total_records}
- **Successfully Converted**: {converted_count}
- **Failed**: {failed_count}
- **Output Directory**: {output_dir}

## Field Mapping

| Source Field | Harbor Field | Transformation |
|--------------|--------------|----------------|
| {source_field} | {harbor_field} | {transformation} |

## Statistics

- **Average Timeout**: {avg_timeout}s
- **Memory Range**: {min_memory}GB - {max_memory}GB
- **Languages**: {languages}
- **Difficulty Distribution**: {difficulty_dist}

## Validation Results

- **Valid Tasks**: {valid_count}
- **Issues Found**: {issue_count}

### Issues

{list_of_issues}

## Next Steps

1. Review generated tasks in `{output_dir}`
2. Customize test.sh for each task type
3. Test with oracle agent: `harbor run --dataset {output_dir} --agent oracle`
4. Run with actual agent: `harbor run --dataset {output_dir} --agent claude-code`

## Sample Tasks

- {task_1}
- {task_2}
- {task_3}
```

### 9. Batch Processing

For large datasets, implement batch processing:

```python
def convert_in_batches(
    source_path: Path,
    output_dir: Path,
    mapping: dict,
    batch_size: int = 100
):
    """Convert dataset in batches to manage memory."""
    converter = DatasetConverter(source_path, output_dir, mapping)

    batch = []
    total_count = 0

    for record in converter.load_records():
        batch.append(record)

        if len(batch) >= batch_size:
            # Process batch
            for rec in batch:
                task = converter.map_record(rec)
                converter.write_task(task)
                total_count += 1

            print(f"Processed {total_count} tasks...")
            batch = []

    # Process remaining
    for rec in batch:
        task = converter.map_record(rec)
        converter.write_task(task)
        total_count += 1

    print(f"✓ Total: {total_count} tasks converted")
```

### 10. Test Converted Dataset

Run validation tests on converted dataset:

```bash
#!/bin/bash
# Test converted dataset

OUTPUT_DIR=$1

echo "Testing converted dataset in $OUTPUT_DIR"

# Count tasks
TASK_COUNT=$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
echo "Found $TASK_COUNT tasks"

# Validate structure
echo "Validating task structure..."
python3 validate_tasks.py "$OUTPUT_DIR"

# Test with oracle agent (should get 1.0 on solvable tasks)
echo "Testing with oracle agent..."
harbor run \
    --dataset "$OUTPUT_DIR" \
    --agent oracle \
    --n-concurrent 4 \
    --max-trials 10

# Check results
echo "Conversion complete!"
```

## Format-Specific Guides

### JSON Dataset

**Source Format:**
```json
[
  {
    "id": "task-001",
    "description": "Implement function X",
    "difficulty": "medium",
    "language": "python"
  }
]
```

**Mapping:**
```json
{
  "task_id_field": "id",
  "instruction_field": "description",
  "metadata_fields": ["difficulty", "language"]
}
```

### CSV Dataset

**Source Format:**
```csv
id,description,difficulty,language
task-001,"Implement function X",medium,python
task-002,"Fix bug Y",easy,javascript
```

**Mapping:**
```json
{
  "task_id_field": "id",
  "instruction_field": "description",
  "metadata_fields": ["difficulty", "language"]
}
```

### JSONL Dataset

**Source Format:**
```jsonl
{"id": "task-001", "prompt": "Do X", "tests": [...]}
{"id": "task-002", "prompt": "Do Y", "tests": [...]}
```

**Mapping:**
```json
{
  "task_id_field": "id",
  "instruction_field": "prompt",
  "metadata_fields": ["tests"]
}
```

### Custom Benchmark Format

For custom formats, implement a custom loader:

```python
class CustomBenchmarkConverter(DatasetConverter):
    def load_records(self) -> Iterator[dict]:
        # Custom loading logic
        data = load_custom_format(self.source_path)
        for item in data:
            yield self.transform_record(item)

    def transform_record(self, item) -> dict:
        # Custom transformation
        return {
            "id": item.custom_id,
            "description": item.custom_description,
            # ... more fields
        }
```

## Examples

### Example 1: Convert JSON Dataset

```bash
# Source: tasks.json
# [{"id": "t1", "desc": "Do X"}, ...]

# Create mapping config
cat > mapping.json <<EOF
{
  "task_id_field": "id",
  "instruction_field": "desc",
  "default_timeout": 1800
}
EOF

# Run conversion
python3 converter.py tasks.json ./harbor_tasks --mapping-config mapping.json

# Validate
harbor run --dataset ./harbor_tasks --agent oracle
```

### Example 2: Convert CSV Dataset

```bash
# Source: benchmark.csv
# id,problem,difficulty,lang
# 1,"Implement X",easy,python

# Run conversion (auto-detect CSV)
python3 converter.py benchmark.csv ./harbor_tasks

# Review first task
cat ./harbor_tasks/1/instruction.md
```

### Example 3: Convert Large JSONL Dataset

```bash
# Source: large_dataset.jsonl (10,000 tasks)

# Convert in batches
python3 converter.py \
    large_dataset.jsonl \
    ./harbor_tasks \
    --batch-size 500

# Validate sample
harbor run --dataset ./harbor_tasks --agent oracle --max-trials 100
```

## Best Practices

### Field Mapping
1. **Inspect source data first**: Understand structure before mapping
2. **Handle missing fields**: Provide sensible defaults
3. **Preserve original data**: Keep raw_record.json for reference
4. **Validate mappings**: Test on small sample first
5. **Document transformations**: Explain any data modifications

### Resource Allocation
1. **Analyze task complexity**: Set appropriate timeouts
2. **Consider language requirements**: Different languages need different resources
3. **Start conservative**: Can adjust after testing
4. **Monitor actual usage**: Check if limits are appropriate
5. **Batch similar tasks**: Group by resource requirements

### Quality Assurance
1. **Validate all tasks**: Check structure and required files
2. **Test sample tasks**: Run with oracle agent first
3. **Review instructions**: Ensure clarity and completeness
4. **Check test scripts**: Verify they work correctly
5. **Document issues**: Track problems for manual review

### Performance
1. **Use batch processing**: For large datasets
2. **Parallelize when possible**: Convert multiple tasks concurrently
3. **Stream large files**: Don't load entire dataset into memory
4. **Cache intermediate results**: Resume interrupted conversions
5. **Monitor progress**: Provide feedback during conversion

## Troubleshooting

### Conversion Fails

**Issue**: Script crashes during conversion

**Solutions:**
- Check source file format and encoding
- Validate JSON/CSV syntax
- Ensure sufficient disk space
- Check file permissions

### Missing Fields

**Issue**: Required fields not found in source data

**Solutions:**
- Review field mapping configuration
- Check for alternative field names
- Provide default values
- Transform existing fields

### Invalid Task Structure

**Issue**: Generated tasks don't validate

**Solutions:**
- Check task.toml syntax
- Ensure all required files are created
- Verify file permissions (test.sh executable)
- Review Dockerfile syntax

### Large Dataset Performance

**Issue**: Conversion is too slow

**Solutions:**
- Increase batch size
- Use streaming for JSONL
- Parallelize conversion
- Optimize I/O operations

## Related

- Harbor Adapter Guide: `skills/adapter-generator/`
- Task Format Specification: `docs/rfcs/task-format.md`
- Existing Adapters: `adapters/*/`
- Validation Tools: `src/harbor/cli/quality_checker/`
