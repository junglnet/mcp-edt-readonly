from __future__ import annotations

import argparse
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from edt_readonly_mcp.server import execute, tool_definitions
from edt_readonly_mcp import ProjectIndex
from edt_readonly_mcp.domain_knowledge import DomainKnowledge

app = FastAPI(title="edt-readonly-mcp-http")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/mcp")
async def mcp(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}
    project_path = os.environ.get("EDT_PROJECT_PATH") or params.get("project")

    if not project_path:
        raise HTTPException(status_code=400, detail="EDT_PROJECT_PATH or project param is required")

    index = ProjectIndex(project_path)
    knowledge = DomainKnowledge(project_path)

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "edt-readonly-mcp", "version": "0.2.0"}}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_definitions()}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = execute(index, knowledge, tool_name, args)
            payload_result = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result}
            return {"jsonrpc": "2.0", "id": request_id, "result": payload_result}
        except Exception as exc:  # pragma: no cover
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}

    if method in {"ping", "notifications/initialized"}:
        return {"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not supported: {method}"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTP MCP server for read-only EDT project analysis")
    parser.add_argument("--project", default=os.environ.get("EDT_PROJECT_PATH"), help="Path to the EDT project root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    if not args.project:
        parser.error("--project or EDT_PROJECT_PATH is required")

    os.environ["EDT_PROJECT_PATH"] = args.project

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
