# edt-readonly-mcp

Read-only MCP server for 1C:EDT projects. It reads project files directly and does not start EDT, execute database queries, or modify project files.

The server supports two transports:

- **stdio** — for Cursor and MCP clients that start the server as a process;
- **HTTP** — for an external agent in 1C or another HTTP MCP client.

## Requirements

- Python 3.10 or newer;
- an exported 1C:EDT project directory on the local disk;
- EDT itself does not need to be running.

The server reads the project directory. It does not read live data from an infobase. Database queries are handled by a separate MCP server.

## Installation

Clone the repository and create a virtual environment:

### Windows PowerShell

```powershell
git clone https://github.com/junglnet/mcp-edt-readonly.git
cd mcp-edt-readonly

python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

If PowerShell blocks activation, run the server with the virtual-environment interpreter directly:

```powershell
.venv\Scripts\python.exe --version
```

### Linux/macOS

```bash
git clone https://github.com/junglnet/mcp-edt-readonly.git
cd mcp-edt-readonly

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Project path

Use the path to the actual EDT project directory — the directory containing the project's source files (`.mdo`, `.bsl`, forms, DCS, and templates).

Examples:

```text
Windows: D:\1C\Projects\MyConfiguration
Linux:   /home/user/1c/projects/MyConfiguration
```

The path may also be supplied through the environment variable `EDT_PROJECT_PATH`.

## HTTP mode

Start the HTTP MCP server on the local machine:

### Windows PowerShell

```powershell
.venv\Scripts\python.exe -m edt_readonly_mcp.http_server `
  --project "D:\1C\Projects\MyConfiguration" `
  --host 127.0.0.1 `
  --port 8767
```

### Linux/macOS

```bash
.venv/bin/python -m edt_readonly_mcp.http_server \
  --project "/home/user/1c/projects/MyConfiguration" \
  --host 127.0.0.1 \
  --port 8767
```

The MCP endpoint is:

```text
http://127.0.0.1:8767/mcp
```

Health check:

```text
http://127.0.0.1:8767/health
```

The HTTP implementation requires FastAPI and Uvicorn. Install them with:

```bash
python -m pip install fastapi uvicorn
```

Or install them on Windows with:

```powershell
.venv\Scripts\python.exe -m pip install fastapi uvicorn
```

### HTTP test with curl

Initialize MCP:

```bash
curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'
```

List tools:

```bash
curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

Call a tool:

```bash
curl -X POST http://127.0.0.1:8767/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_project_summary","arguments":{}}}'
```

The current HTTP endpoint is intended for local trusted use. It binds to `127.0.0.1` by default and currently has no authentication. Do not expose it on `0.0.0.0` or the public network without adding authentication and a network boundary.

## stdio mode

Start the stdio server manually:

```bash
python -m edt_readonly_mcp --project /path/to/edt/project
```

Normally an MCP client starts this process automatically.

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

Restart Cursor or reload its MCP servers after changing the configuration.

## Connecting an external 1C agent over HTTP

Configure the external agent with this MCP URL:

```text
http://127.0.0.1:8767/mcp
```

The agent and the Python server must run on the same computer for `127.0.0.1` to work. If they run on different computers, bind the server to a specific private network address and protect it with a firewall and authentication before use. The current implementation is designed for local testing.

## Build the project knowledge index

Run this once and repeat after source changes:

```bash
python scripts/analyze_project.py --project /path/to/edt/project
```

Windows example:

```powershell
.venv\Scripts\python.exe scripts\analyze_project.py `
  --project "D:\1C\Projects\MyConfiguration"
```

The script creates:

```text
<project>/.edt-knowledge/domain-index.json
```

It extracts generated facts about register references, query sources, and document movements. Manually curated business concepts can be stored in the same file and are preserved on subsequent analyzer runs.

## Domain-source workflow

For a question about data, the agent should use this sequence before calling the separate database-query MCP:

```text
find_data_sources
get_metadata_details
get_query_patterns
compose query
call the database-query MCP
```

For example, the domain index can describe that “основная спецификация” is stored in `InformationRegister.СпецификацииПоУмолчанию`, rather than in the `Спецификации` catalog.

## Available tools

- `get_project_summary`
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
- `find_data_sources`
- `get_object_purpose`
- `get_query_patterns`

All tools are read-only. This server does not modify project files or execute database queries.
