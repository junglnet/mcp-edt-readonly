# edt-readonly-mcp

Read-only MCP server for inspecting exported 1C:EDT projects. It reads project files directly and does not start EDT, connect to an infobase, execute database queries, or modify project files.

Supported transports:

- **stdio** for Cursor and MCP clients that start the server as a process;
- **HTTP** for an external 1C agent or another HTTP MCP client.

## Requirements

- Python 3.10 or newer;
- an exported 1C:EDT project directory on the local disk;
- read access to that directory;
- EDT does not need to be running.

The project path must be the directory containing the source tree with files such as `.mdo`, `.bsl`, `.form`, `.dcs`, `.dcss`, `.mxl`, or `.mxlx`. Do not pass the repository root unless it is also the EDT project root.

Database queries are outside the scope of this server and require a separate database MCP server.

## Installation

### Windows PowerShell

```powershell
git clone https://github.com/junglnet/mcp-edt-readonly.git
cd mcp-edt-readonly

python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install fastapi uvicorn  # required for HTTP mode
```

If PowerShell blocks activation, use `.venv\Scripts\python.exe` directly in every command.

### Linux/macOS

```bash
git clone https://github.com/junglnet/mcp-edt-readonly.git
cd mcp-edt-readonly

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install fastapi uvicorn  # required for HTTP mode
```

The package has no runtime dependency on FastAPI/Uvicorn for stdio mode. Install them when HTTP mode is needed.

## Project path

Pass the EDT project root with `--project` or set `EDT_PROJECT_PATH`.

```text
Windows: D:\1C\Projects\MyConfiguration
Linux:   /home/user/1c/projects/MyConfiguration
```

PowerShell example:

```powershell
$env:EDT_PROJECT_PATH = "D:\1C\Projects\MyConfiguration"
```

The path is validated when the server starts. A missing path causes startup to fail; an existing but wrong directory usually produces empty lists.

## stdio mode

Start the server manually:

```powershell
.venv\Scripts\python.exe -m edt_readonly_mcp --project "D:\1C\Projects\MyConfiguration"
```

```bash
.venv/bin/python -m edt_readonly_mcp --project /home/user/1c/projects/MyConfiguration
```

Normally the MCP client starts this process automatically. The process reads JSON-RPC messages from stdin and writes responses to stdout; do not write diagnostic output to stdout in a client wrapper.

## Cursor configuration

### Windows

```json
{
  "mcpServers": {
    "edt-readonly": {
      "command": "D:\\path\\to\\mcp-edt-readonly\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "edt_readonly_mcp",
        "--project",
        "D:\\1C\\Projects\\MyConfiguration"
      ]
    }
  }
}
```

### Linux/macOS

```json
{
  "mcpServers": {
    "edt-readonly": {
      "command": "/path/to/mcp-edt-readonly/.venv/bin/python",
      "args": [
        "-m",
        "edt_readonly_mcp",
        "--project",
        "/home/user/1c/projects/MyConfiguration"
      ]
    }
  }
}
```

Use an absolute interpreter path and an absolute project path. Reload the MCP server after changing the configuration.

## HTTP mode

Start the local HTTP server:

```powershell
.venv\Scripts\python.exe -m edt_readonly_mcp.http_server `
  --project "D:\1C\Projects\MyConfiguration" `
  --host 127.0.0.1 `
  --port 8767
```

```bash
.venv/bin/python -m edt_readonly_mcp.http_server \
  --project /home/user/1c/projects/MyConfiguration \
  --host 127.0.0.1 \
  --port 8767
```

Endpoints:

- MCP: `http://127.0.0.1:8767/mcp`
- health check: `http://127.0.0.1:8767/health`

Check that the server is alive:

```bash
curl http://127.0.0.1:8767/health
```

Expected response:

```json
{"status":"ok"}
```

The HTTP server binds to `127.0.0.1` and has no authentication. Keep it local. Do not expose it on `0.0.0.0` or the public network without authentication, firewall rules, and a trusted network boundary.

### HTTP MCP smoke test

Initialize the protocol, list tools, then call a tool:

```bash
curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_project_summary","arguments":{}}}'
```

For an external 1C agent on the same computer, configure `http://127.0.0.1:8767/mcp`. For a different computer, use a private address and add authentication and firewall protection before connecting.

## Build the domain index

The structural tools work without an index. Run the analyzer to enable useful generated facts for `find_data_sources` and `get_query_patterns`:

```powershell
.venv\Scripts\python.exe scripts\analyze_project.py `
  --project "D:\1C\Projects\MyConfiguration"
```

```bash
.venv/bin/python scripts/analyze_project.py \
  --project /home/user/1c/projects/MyConfiguration
```

The default output is:

```text
<project>/.edt-knowledge/domain-index.json
```

Use `--output` to choose another output path:

```bash
python scripts/analyze_project.py \
  --project /path/to/project \
  --output /path/to/project/.edt-knowledge/domain-index.json
```

Run the analyzer once after checkout and again after source changes. It scans BSL, MDO, and XML files for register references, query sources, and document movements. Existing manually curated `concepts` are preserved when the file is regenerated. The generated index is project-local and should not be treated as live database schema.

## Tool usage

Call `tools/list` first when integrating a new client. For path-based tools, call the corresponding list tool first and pass the returned project-relative path exactly as returned. Paths use `/` separators even on Windows.

| Tool | Arguments | Purpose |
| --- | --- | --- |
| `get_project_summary` | none | Counts metadata objects, BSL modules, forms, DCS schemas, and templates. |
| `list_metadata_objects` | `limit` | Lists discovered `.mdo` metadata objects. |
| `get_metadata_details` | `object_name` | Reads attributes, tabular sections, forms, commands, and modules. Exact, filename, typed (`Документ.ЗаказПокупателя`), and partial lookup are supported. |
| `list_modules` | `limit` | Lists BSL module paths. |
| `get_module_structure` | `module_path` | Lists procedures and functions in one BSL module. |
| `read_method_source` | `module_path`, optional `method_name` | Reads the whole module or one method. |
| `search_in_code` | `query`, `limit` | Searches text in BSL, XML, MDO, TXT, MD, FORM, DCS, DCSS, MXL, and MXLX files. |
| `get_form_structure` | `form_path` | Reads form items, commands, and handlers from XML form files, including namespaced EDT forms. |
| `get_form_command_handler` | `form_path`, `command_name` | Finds a command case-insensitively and returns handlers; a missing command includes available command names. |
| `list_dcs_schemas` | none | Lists `.dcs`, `.dcss`, and likely DCS XML files. |
| `get_dcs_schema` | `schema_path` | Returns a summary of datasets in a DCS XML file. |
| `list_tabular_document_templates` | none | Lists likely tabular document template files. |
| `get_tabular_document_template` | `template_path` | Returns a text preview and template markers. |
| `find_document_movements` | `document_name`, `limit` | Finds likely `Движения`/`Movements` registrations in BSL. |
| `find_data_sources` | `question`, `limit` | Matches a business question against curated concepts and generated objects. |
| `get_object_purpose` | `name` | Returns matching curated or generated purpose information. |
| `get_query_patterns` | `name`, `limit` | Returns generated code/query facts mentioning an object. |

Limits are positive integers. A list result can be incomplete when its limit is too small; increase the limit for large projects.

## Recommended workflows

### Inspect an unfamiliar project

```text
get_project_summary
list_metadata_objects
list_modules
list_forms
list_dcs_schemas
list_tabular_document_templates
```

`list_forms` is an internal index operation used by the summary; form paths can also be discovered with `search_in_code` or by inspecting the project tree.

### Trace a form command

```text
get_form_structure(form_path)
get_form_command_handler(form_path, command_name)
read_method_source(module_path, method_name)
```

If the command is not found, use the returned `available_commands` instead of guessing a different file format or command spelling.

### Find the source for a data question

Run these before composing a database query in another MCP server:

```text
find_data_sources(question)
get_metadata_details(object_name)
get_query_patterns(object_name)
compose the query using the confirmed fields and sources
call the separate database-query MCP
```

Without a domain index, domain tools may return empty results or `No domain knowledge found`; regenerate the index rather than assuming that the data is absent.

## Troubleshooting

### `Project path does not exist`

Check the path, quoting, and that `--project` points to the EDT source root rather than its parent directory.

### Empty metadata, modules, forms, or DCS lists

Confirm that the directory is an exported EDT project and contains `.mdo`, `.bsl`, `.form`, `.dcs`, `.dcss`, `.mxl`, or `.mxlx` files. A binary or infobase directory is not a supported project input.

### `Module not found`, `Form not found`, or `Template not found`

Pass the project-relative path returned by a list/search operation. Do not pass an absolute path or only the basename when the project contains duplicate names.

### `Metadata object not found`

Call `list_metadata_objects` and use the returned `name` or `path`. Typed names such as `Документ.ЗаказПокупателя` and `Справочник.Номенклатура` are supported. Partial names are supported, but an ambiguous partial name returns the first matching object.

### `Could not parse ... XML`

The file is not valid XML or is not the expected EDT artifact. Use `search_in_code` to inspect it as text and verify the path.

### Domain tools return no matches

Run `scripts/analyze_project.py` for the same project path. Then restart the MCP server if it was already running, because the domain index is loaded when a server instance starts.

## Safety and scope

All tools are read-only. The server only reads project files and writes the optional generated `.edt-knowledge/domain-index.json` when the analyzer is run. It never edits source files, starts EDT, reads live infobase data, or executes SQL.

For development, run the focused regression tests with:

```bash
PYTHONPATH=src python -m pytest tests/test_project_index_real_project.py -q
```
