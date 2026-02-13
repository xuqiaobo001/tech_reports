# Harbor CLI Adapter Generator

Generate scaffolding for new Harbor CLI adapters that convert external benchmarks into Harbor-compatible task format.

## Overview

This skill automates the creation of adapter boilerplate code, including:
- Adapter class with conversion logic
- CLI entry point
- Task structure generation
- Documentation templates
- Test scaffolding

## When to Use

Use this skill when you need to:
- Integrate a new external benchmark into Harbor
- Convert tasks from custom formats (JSON, CSV, API, etc.)
- Create reusable conversion tools for specific data sources

## Usage

When invoked, I will:
1. Ask about the benchmark name and source data format
2. Create the adapter directory structure in `adapters/{adapter_name}/`
3. Generate boilerplate Python code for the adapter
4. Create documentation and test templates
5. Customize the code based on your source format

## Parameters

- **adapter_name**: Name of the adapter (e.g., "swebench", "humaneval")
- **source_format**: Format of source data (json, csv, jsonl, api, database)
- **output_dir**: Where to create the adapter (default: `adapters/`)

## Generated Structure

```
adapters/{adapter_name}/
├── adapter.py          # Main conversion logic
├── run_adapter.py      # CLI entry point
├── README.md           # Documentation
├── requirements.txt    # Dependencies (if needed)
└── template/           # Task templates (optional)
    ├── Dockerfile
    ├── test.sh
    └── instruction.md.j2
```

## Example

**User request:** "Create an adapter for the HumanEval benchmark"

**I will:**
1. Ask about HumanEval's data format (JSON/JSONL)
2. Create `adapters/humaneval/` directory
3. Generate `adapter.py` with:
   - `HumanevalAdapter` class
   - `load_source_data()` method for JSON loading
   - `convert_task()` method mapping HumanEval fields to Harbor format
   - `write_task()` method creating Harbor task structure
4. Generate `run_adapter.py` CLI script
5. Create README with usage instructions

**Result:** A working adapter that can be run with:
```bash
python adapters/humaneval/run_adapter.py \
    --source-dir /path/to/humaneval/data \
    --output-dir tasks/humaneval
```

## Key Features

- **Format-specific customization**: Automatically adapts `load_source_data()` based on source format (JSON, CSV, API, etc.)
- **Complete boilerplate**: Generates all necessary files including Dockerfile, test.sh, and task.toml templates
- **Best practices**: Includes error handling, validation, and documentation
- **Extensible**: Easy to customize generated code for specific requirements

## Related

- Dataset Converter Skill: For batch converting existing datasets
- Testcase Generator Skill: For creating individual test cases
- Harbor Adapter Documentation: `docs/adapters/`
