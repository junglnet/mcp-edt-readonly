# edt-readonly-mcp

Read-only stdio MCP server for 1C:EDT projects. It does not start EDT, execute database queries, or modify project files.

## Install and run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -e .
python -m edt_readonly_mcp --project /path/to/edt/project
```

The server is stdio-based: the MCP client starts it and communicates through stdin/stdout.

## Cursor

```json
{
  "mcpServers": {
    "edt-readonly": {
      "command": "/absolute/path/to/repo/.venv/bin/python",
      "args": [
        "-m",
        "edt_readonly_mcp",
        "--project",
        "/absolute/path/to/edt/project"
      ]
    }
  }
}
```

On Windows use the Python path inside `.venv\\Scripts\\python.exe` and Windows paths in `--project`.

## Build the knowledge index

Run this once, and repeat after source changes:

```bash
python scripts/analyze_project.py --project /path/to/edt/project
```

It creates `.edt-knowledge/domain-index.json` in the project. Generated facts are merged with manually curated `concepts`; the file is not required for the basic reader tools.

The domain tools are:

- `find_data_sources` — resolve a business question to likely metadata sources;
- `get_object_purpose` — read curated purpose and field mappings;
- `get_query_patterns` — show generated usage facts from code and queries.

Before calling the separate database-query MCP, the agent should call `find_data_sources`, inspect the selected metadata object, and only then compose a query.

## Current tools

Metadata, BSL, forms, DCS/СКД, tabular document templates, document movements, and domain-source discovery are supported. All operations are read-only.
