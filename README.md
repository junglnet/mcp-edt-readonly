# edt-readonly-mcp

Read-only MCP server for 1C:EDT projects. It indexes a project on disk and exposes a limited set of tools for metadata, forms, BSL modules, СКД, and tabular document templates without requiring a running EDT instance.

This project is intended for AI agents that need structural understanding of a 1C configuration, not for modifying data or changing the project.

## Features

- List metadata objects in a project
- Read metadata details (attributes, tabular sections, forms, commands)
- List and read BSL modules
- Search code across the project
- Read form structure and command handlers
- List and inspect DCS (СКД) schemas
- List and inspect tabular document templates
- Find document movement patterns from BSL code
- Operates in read-only mode only

## Quick start

Install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Run against a project:

```bash
python -m edt_readonly_mcp.server --project /path/to/your/edt/project
```

Or with environment variable:

```bash
export EDT_PROJECT_PATH=/path/to/your/edt/project
python -m edt_readonly_mcp.server
```

## Cursor / MCP configuration

Example for Cursor or similar MCP-compatible clients:

```json
{
  "mcpServers": {
    "edt-readonly": {
      "command": "python",
      "args": [
        "-m",
        "edt_readonly_mcp.server",
        "--project",
        "/path/to/your/edt/project"
      ]
    }
  }
}
```

## Included tools

- `list_metadata_objects`
- `get_metadata_details`
- `list_modules`
- `get_module_structure`
- `read_method_source`
- `search_in_code`
- `get_form_structure`
- `get_form_command_handler`
- `list_dcs_schemas`
- `get_dcs_schema`
- `list_tabular_document_templates`
- `get_tabular_document_template`
- `find_document_movements`
- `get_project_summary`

## Notes

This is intentionally read-only. It does not modify the project or execute requests against a live database. It is optimized for knowledge extraction and AI context preparation.

The parser intentionally works on the project files directly and is therefore resilient to a missing EDT runtime.
