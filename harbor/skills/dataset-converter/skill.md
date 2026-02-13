# Harbor Dataset Converter

Convert existing datasets from various formats (JSON, CSV, JSONL, benchmark formats) into Harbor-compatible task format.

## Overview

This skill helps you batch-convert external datasets into Harbor tasks by:
- Automatically detecting source format
- Mapping fields from source to Harbor format
- Generating complete task structures (task.toml, instruction.md, Dockerfile, test.sh)
- Validating converted tasks
- Providing conversion reports

## When to Use

Use this skill when you need to:
- Convert an existing benchmark dataset to Harbor format
- Migrate tasks from another framework
- Batch-process multiple tasks from structured data files
- Transform data from JSON, CSV, JSONL, or custom formats

## Usage

When invoked, I will:
1. Ask about your source dataset location and format
2. Analyze the dataset structure and show sample records
3. Help you configure field mappings (which source fields map to Harbor fields)
4. Generate a conversion script customized for your data
5. Run the conversion and validate results
6. Provide a summary report

## Parameters

- **source_path**: Path to source dataset (file or directory)
- **source_format**: Format of source data (json, csv, jsonl, swebench, humaneval, etc.)
- **output_dir**: Directory where Harbor tasks will be created
- **mapping_config**: Optional JSON file with field mappings
- **batch_size**: Number of tasks to process at once (default: 100)

## Field Mapping

Common field mappings from source to Harbor format:

| Source Field | Harbor Field | Purpose |
|--------------|--------------|---------|
| `id`, `task_id`, `instance_id` | `task_id` | Unique identifier |
| `description`, `prompt`, `instruction` | `instruction.md` | Task description |
| `test_cases`, `tests` | `tests/` | Test data |
| `solution`, `reference` | `solution/` | Reference solution |
| `language`, `lang` | `metadata.language` | Programming language |
| `difficulty`, `level` | `metadata.difficulty` | Difficulty level |

## Example

**User request:** "Convert my HumanEval dataset to Harbor format"

**I will:**
1. Ask for the dataset path (e.g., `/data/humaneval.jsonl`)
2. Detect format (JSONL) and show sample record
3. Create mapping configuration:
   ```json
   {
     "task_id_field": "task_id",
     "instruction_field": "prompt",
     "metadata_fields": ["entry_point", "canonical_solution"]
   }
   ```
4. Generate and run conversion script
5. Create tasks in `tasks/humaneval/` with structure:
   ```
   tasks/humaneval/
   ├── HumanEval_0/
   │   ├── task.toml
   │   ├── instruction.md
   │   ├── environment/Dockerfile
   │   └── tests/test.sh
   ├── HumanEval_1/
   │   └── ...
   ```
6. Validate all tasks and provide report

**Result:** 164 tasks converted, ready to run with `harbor run --dataset tasks/humaneval`

## Generated Files

For each task, the converter creates:
- **task.toml**: Task metadata and resource configuration
- **instruction.md**: Task description for the agent
- **environment/Dockerfile**: Docker environment setup
- **tests/test.sh**: Verification script (placeholder, needs customization)
- **raw_record.json**: Original source record for reference

## Key Features

- **Auto-detection**: Automatically detects JSON, CSV, JSONL formats
- **Flexible mapping**: Configure how source fields map to Harbor format
- **Batch processing**: Efficiently handles large datasets
- **Validation**: Checks generated tasks for completeness
- **Progress tracking**: Shows conversion progress for large datasets
- **Format-specific handlers**: Built-in support for common benchmarks (SWE-Bench, HumanEval, MMLU)

## Supported Formats

- **JSON**: Single file with array of tasks or nested structure
- **CSV**: Tabular data with headers
- **JSONL**: One JSON object per line
- **SWE-Bench**: GitHub issue-based software engineering tasks
- **HumanEval**: Python code generation tasks
- **MMLU**: Multiple-choice question format
- **Custom**: Configurable for any structured format

## Notes

- Generated test.sh files are placeholders and need customization based on your verification requirements
- The converter preserves original data in raw_record.json for reference
- For large datasets (1000+ tasks), use batch processing to manage memory
- After conversion, test with oracle agent first: `harbor run --dataset output_dir --agent oracle`

## Related

- Adapter Generator Skill: For creating reusable adapters
- Testcase Generator Skill: For creating individual test cases
- Harbor Task Format: `docs/rfcs/task-format.md`
