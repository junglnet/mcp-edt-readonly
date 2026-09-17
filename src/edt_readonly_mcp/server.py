from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import ProjectIndex
from .domain_knowledge import DomainKnowledge

VERSION = "0.2.0"


def tool_definitions() -> list[dict[str, Any]]:
    string = lambda: {"type": "string"}
    integer = lambda default: {"type": "integer", "default": default, "minimum": 1}
    return [
        {"name": "get_project_summary", "description": "Return a summary of the EDT project.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_metadata_objects", "description": "List metadata objects discovered in the project.", "inputSchema": {"type": "object", "properties": {"limit": integer(200)}}},
        {"name": "get_metadata_details", "description": "Read metadata details by object name.", "inputSchema": {"type": "object", "properties": {"object_name": string()}, "required": ["object_name"]}},
        {"name": "list_modules", "description": "List BSL modules.", "inputSchema": {"type": "object", "properties": {"limit": integer(200)}}},
        {"name": "get_module_structure", "description": "List procedures and functions in a BSL module.", "inputSchema": {"type": "object", "properties": {"module_path": string()}, "required": ["module_path"]}},
        {"name": "read_method_source", "description": "Read a BSL module or one method from it.", "inputSchema": {"type": "object", "properties": {"module_path": string(), "method_name": string()}, "required": ["module_path"]}},
        {"name": "search_in_code", "description": "Search text in BSL and project XML files.", "inputSchema": {"type": "object", "properties": {"query": string(), "limit": integer(50)}, "required": ["query"]}},
        {"name": "get_form_structure", "description": "Read form items, commands and handlers from an XML form.", "inputSchema": {"type": "object", "properties": {"form_path": string()}, "required": ["form_path"]}},
        {"name": "get_form_command_handler", "description": "Find a command and handlers in a form.", "inputSchema": {"type": "object", "properties": {"form_path": string(), "command_name": string()}, "required": ["form_path", "command_name"]}},
        {"name": "list_dcs_schemas", "description": "List possible DCS/СКД XML files.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_dcs_schema", "description": "Read a DCS/СКД XML file summary.", "inputSchema": {"type": "object", "properties": {"schema_path": string()}, "required": ["schema_path"]}},
        {"name": "list_tabular_document_templates", "description": "List tabular document template files.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_tabular_document_template", "description": "Read a tabular document template summary.", "inputSchema": {"type": "object", "properties": {"template_path": string()}, "required": ["template_path"]}},
        {"name": "find_document_movements", "description": "Find likely document movement registrations in BSL.", "inputSchema": {"type": "object", "properties": {"document_name": string(), "limit": integer(20)}, "required": ["document_name"]}},
        {"name": "find_data_sources", "description": "Find likely metadata data sources for a business question using curated concepts and generated code facts. Call this before composing a database query.", "inputSchema": {"type": "object", "properties": {"question": string(), "limit": integer(10)}, "required": ["question"]}},
        {"name": "get_object_purpose", "description": "Return curated purpose and field mapping for a metadata object or business concept.", "inputSchema": {"type": "object", "properties": {"name": string()}, "required": ["name"]}},
        {"name": "get_query_patterns", "description": "Return generated code/query usage facts for an object.", "inputSchema": {"type": "object", "properties": {"name": string(), "limit": integer(20)}, "required": ["name"]}},
    ]


def execute(index: ProjectIndex, knowledge: DomainKnowledge, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "get_project_summary": return index.summary()
    if name == "list_metadata_objects": return {"objects": index.list_metadata_objects(int(args.get("limit", 200)))}
    if name == "get_metadata_details": return index.get_metadata_details(str(args["object_name"]))
    if name == "list_modules": return {"modules": index.list_modules(int(args.get("limit", 200)))}
    if name == "get_module_structure": return index.get_module_structure(str(args["module_path"]))
    if name == "read_method_source": return index.read_method_source(str(args["module_path"]), args.get("method_name"))
    if name == "search_in_code": return {"matches": index.search_in_code(str(args["query"]), int(args.get("limit", 50)))}
    if name == "get_form_structure": return index.get_form_structure(str(args["form_path"]))
    if name == "get_form_command_handler": return index.get_form_command_handler(str(args["form_path"]), str(args["command_name"]))
    if name == "list_dcs_schemas": return {"schemas": index.list_dcs_schemas()}
    if name == "get_dcs_schema": return index.get_dcs_schema(str(args["schema_path"]))
    if name == "list_tabular_document_templates": return {"templates": index.list_tabular_document_templates()}
    if name == "get_tabular_document_template": return index.get_tabular_document_template(str(args["template_path"]))
    if name == "find_document_movements": return index.find_document_movements(str(args["document_name"]), int(args.get("limit", 20)))
    if name == "find_data_sources": return knowledge.search_sources(str(args["question"]), int(args.get("limit", 10)))
    if name == "get_object_purpose": return knowledge.object_purpose(str(args["name"]))
    if name == "get_query_patterns": return knowledge.query_patterns(str(args["name"]), int(args.get("limit", 20)))
    raise ValueError(f"Unknown tool: {name}")


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error else "result"] = error or result
    return payload


def run(project: str) -> None:
    index = ProjectIndex(project)
    knowledge = DomainKnowledge(project)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params") or {}
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "edt-readonly-mcp", "version": VERSION}}
                print(json.dumps(response(request_id, result), ensure_ascii=False), flush=True)
            elif method == "tools/list":
                print(json.dumps(response(request_id, {"tools": tool_definitions()}), ensure_ascii=False), flush=True)
            elif method == "tools/call":
                try:
                    result = execute(index, knowledge, params["name"], params.get("arguments") or {})
                    payload = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
                    print(json.dumps(response(request_id, payload), ensure_ascii=False), flush=True)
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    print(json.dumps(response(request_id, error={"code": -32602, "message": str(exc)}), ensure_ascii=False), flush=True)
            elif method in {"ping", "notifications/initialized"}:
                print(json.dumps(response(request_id, {"ok": True}), ensure_ascii=False), flush=True)
            elif method == "resources/list":
                print(json.dumps(response(request_id, {"resources": []}), ensure_ascii=False), flush=True)
            elif request_id is not None:
                print(json.dumps(response(request_id, error={"code": -32601, "message": f"Method not supported: {method}"}), ensure_ascii=False), flush=True)
        except json.JSONDecodeError as exc:
            print(json.dumps(response(None, error={"code": -32700, "message": str(exc)}), ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MCP server for 1C:EDT projects")
    parser.add_argument("--project", default=os.environ.get("EDT_PROJECT_PATH"), required=False)
    args = parser.parse_args()
    if not args.project:
        parser.error("project path is required; use --project or EDT_PROJECT_PATH")
    run(args.project)


if __name__ == "__main__":
    main()
