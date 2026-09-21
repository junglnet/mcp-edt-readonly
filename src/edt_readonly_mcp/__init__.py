from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET


@dataclass
class MetadataObject:
    name: str
    type_name: str
    path: str
    file_name: str
    attributes: list[str] = field(default_factory=list)
    tabular_sections: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)


class ProjectIndex:
    def __init__(self, project_path: str):
        self.root = Path(project_path).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Project path does not exist: {project_path}")

    def summary(self) -> dict[str, Any]:
        metadata_objects = self.list_metadata_objects()
        modules = self.list_modules()
        forms = self.list_forms()
        dcs = self.list_dcs_schemas()
        templates = self.list_tabular_document_templates()
        return {
            "project_path": str(self.root),
            "metadata_objects": len(metadata_objects),
            "modules": len(modules),
            "forms": len(forms),
            "dcs_schemas": len(dcs),
            "tabular_document_templates": len(templates),
        }

    def list_metadata_objects(self, limit: int = 200) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in sorted(self.root.rglob("*.mdo")):
            if not self._is_metadata_file(path):
                continue
            info = self._parse_mdo(path)
            if not info:
                continue
            key = f"{info['type']}::{info['name']}"
            if key in seen:
                continue
            seen.add(key)
            objects.append(info)
            if len(objects) >= limit:
                break
        return objects

    def get_metadata_details(self, object_name: str) -> dict[str, Any]:
        needle = object_name.strip()
        if not needle:
            return {"error": "Metadata object name is empty"}
        needle_l = needle.lower()
        type_prefix, _, name_part = needle_l.partition(".")
        type_aliases = {
            "документ": "document",
            "справочник": "catalog",
            "регистрсведений": "informationregister",
            "регистрнакопления": "accumulationregister",
            "отчет": "report",
            "обработка": "dataprocessor",
        }
        requested_type = type_aliases.get(type_prefix, type_prefix) if name_part else ""
        requested_name = name_part if name_part else needle_l
        candidates = []
        for path in sorted(self.root.rglob("*.mdo")):
            info = self._parse_mdo(path)
            if not info:
                continue
            name_l = info["name"].lower()
            path_l = info["path"].lower()
            if requested_type and info["type"].lower() != requested_type:
                continue
            if requested_name == name_l or path_l.endswith(f"/{requested_name}.mdo"):
                return info
            if name_l == needle_l or path_l.endswith(f"/{needle_l}.mdo"):
                return info
            if requested_name in name_l or requested_name in path_l:
                candidates.append(info)
        if candidates:
            return candidates[0]
        return {"error": f"Metadata object not found: {object_name}"}

    def list_modules(self, limit: int = 200) -> list[str]:
        modules: list[str] = []
        for path in sorted(self.root.rglob("*.bsl")):
            rel = path.relative_to(self.root).as_posix()
            if any(part in {".git", "bin", "obj", "target", "node_modules"} for part in path.parts):
                continue
            modules.append(rel)
            if len(modules) >= limit:
                break
        return modules

    def read_method_source(self, module_path: str, method_name: str | None = None) -> dict[str, Any]:
        path = self.root / module_path
        if not path.exists():
            return {"error": f"Module not found: {module_path}"}
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if method_name:
            pattern = re.compile(rf"(?is)(?:procedure|function|Процедура|Функция)\s+{re.escape(method_name)}\s*\(.*?\)\s*(?:Экспорт|Export)?\s*(?:;|\n|$)")
            match = pattern.search(text)
            if not match:
                return {"error": f"Method not found: {method_name} in {module_path}", "module": module_path}
            start = match.start()
            end = self._find_method_end(text, start)
            return {"module": module_path, "method": method_name, "source": text[start:end]}
        return {"module": module_path, "source": text}

    def get_module_structure(self, module_path: str) -> dict[str, Any]:
        path = self.root / module_path
        if not path.exists():
            return {"error": f"Module not found: {module_path}"}
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        methods: list[dict[str, Any]] = []
        for match in re.finditer(r"(?is)(?:procedure|function|Процедура|Функция)\s+(\w+)\s*\((.*?)\)", text):
            methods.append({"name": match.group(1), "signature": match.group(0).strip()})
        return {"module": module_path, "method_count": len(methods), "methods": methods}

    def search_in_code(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        query_l = query.lower()
        matches: list[dict[str, Any]] = []
        allowed_suffixes = {".bsl", ".xml", ".mdo", ".txt", ".md", ".form", ".dcs", ".dcss", ".mxl", ".mxlx"}
        for path in sorted(self.root.rglob("*")):
            if path.is_dir():
                continue
            name_l = path.name.lower()
            if path.suffix.lower() not in allowed_suffixes and not any(name_l.endswith(ext) for ext in sorted(allowed_suffixes, key=len, reverse=True)):
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if query_l in text.lower():
                line_no = self._find_line_number(text, query_l)
                matches.append({"path": path.relative_to(self.root).as_posix(), "line": line_no, "preview": self._short_preview(text, query_l)})
                if len(matches) >= limit:
                    break
        return matches

    def list_forms(self) -> list[str]:
        result: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            lower = rel.lower()
            if lower.endswith(".form") or lower.endswith(".form.xml"):
                result.append(rel)
                continue
            if lower.endswith(".xml") and ("/forms/" in lower or "/form/" in lower):
                result.append(rel)
        return result

    def get_form_structure(self, form_path: str) -> dict[str, Any]:
        path = self.root / form_path
        if not path.exists():
            return {"error": f"Form not found: {form_path}"}
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            return {"error": f"Could not parse form XML: {form_path} ({exc})"}

        items: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []
        handlers: list[dict[str, Any]] = []

        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            xsi_type = elem.attrib.get(f"{{{self._xsi_namespace(elem)}}}type") if self._xsi_namespace(elem) else None
            type_name = xsi_type.rsplit(":", 1)[-1] if xsi_type else ""
            local_names = {tag, type_name}

            if any(name in {"Item", "FormGroup", "FormField", "FormTable", "Decoration", "AutoCommandBar", "CommandBar", "InputField", "TextEdit", "Button", "CheckBoxField", "DateField", "ComboBox", "Label"} for name in local_names if name):
                name = elem.attrib.get("name") or elem.attrib.get("id") or "unknown"
                items.append({"name": name, "tag": tag, "type": type_name or None, "attrs": dict(elem.attrib)})

            if any(name.lower() in {"command", "commands"} for name in local_names if name):
                cmd_name = elem.attrib.get("name") or elem.attrib.get("id") or "unknown"
                commands.append({"name": cmd_name, "tag": tag, "type": type_name or None, "attrs": dict(elem.attrib)})

            if any((name.lower() == "handler" or name.lower().endswith("handler") or name.lower().startswith("oncreate") or name.lower().endswith("oncreate")) for name in local_names if name):
                handlers.append({"tag": tag, "type": type_name or None, "attrs": dict(elem.attrib), "text": (elem.text or '').strip()})

        return {
            "form": form_path,
            "items": items[:200],
            "commands": commands[:200],
            "handlers": handlers[:200],
        }

    def get_form_command_handler(self, form_path: str, command_name: str) -> dict[str, Any]:
        form = self.get_form_structure(form_path)
        if "error" in form:
            return form
        commands = form.get("commands", [])
        available = [cmd.get("name") for cmd in commands if isinstance(cmd, dict)]
        for cmd in commands:
            if cmd["name"].lower() == command_name.lower():
                return {"form": form_path, "command": cmd, "handlers": form.get("handlers", [])}
        return {"error": f"Command not found: {command_name} in {form_path}", "available_commands": available[:20]}

    def list_dcs_schemas(self) -> list[str]:
        result: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            lower = rel.lower()
            if lower.endswith(".dcs") or lower.endswith(".dcss"):
                result.append(rel)
                continue
            if lower.endswith(".xml") and (
                any(token in lower for token in ["datasource", "dcs", "data composition", "datacomposition", "schemes", "report", "query"]) or lower.endswith(".schema.xml")
            ):
                result.append(rel)
        return result

    def get_dcs_schema(self, schema_path: str) -> dict[str, Any]:
        path = self.root / schema_path
        if not path.exists():
            return {"error": f"DCS schema not found: {schema_path}"}
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            return {"error": f"Could not parse DCS XML: {schema_path} ({exc})"}
        root = tree.getroot()
        datasets = []
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in {"DataSet", "DataSetObject", "DataSource"}:
                datasets.append({"tag": tag, "attrs": dict(elem.attrib)})
        return {"path": schema_path, "datasets": datasets[:200]}

    def list_tabular_document_templates(self) -> list[str]:
        result: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            lower = rel.lower()
            if not ("templates" in lower or "template" in lower):
                continue
            if (
                lower.endswith((".dcs", ".dcss", ".mxl", ".mxlx", ".bin", ".xml"))
                or "tabulardocument" in lower
                or "spreadsheetdocument" in lower
            ):
                result.append(rel)
        return result

    def get_tabular_document_template(self, template_path: str) -> dict[str, Any]:
        path = self.root / template_path
        if not path.exists():
            return {"error": f"Template not found: {template_path}"}
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return {
            "path": template_path,
            "length": len(text),
            "preview": text[:4000],
            "has_tabular_document_markers": "TabularDocument" in text or "SpreadsheetDocument" in text,
        }

    def find_document_movements(self, document_name: str, limit: int = 20) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        for module in self.list_modules():
            text = (self.root / module).read_text(encoding="utf-8-sig", errors="replace")
            if document_name.lower() not in text.lower() and "Движения." not in text and "Движения," not in text:
                continue
            if "Движения." in text or "Движения," in text or "Movements." in text:
                line_no = self._find_line_number(text, "Движения.")
                findings.append({"module": module, "line": line_no, "preview": self._short_preview(text, "Движения.")})
                if len(findings) >= limit:
                    break
        return {"document": document_name, "findings": findings[:limit]}

    def _parse_mdo(self, path: Path) -> dict[str, Any] | None:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return None
        type_name = self._guess_type_name(path)
        name = path.stem
        if not type_name:
            type_name = root.tag.rsplit("}", 1)[-1]
        attributes = []
        tabular_sections = []
        forms = []
        commands = []
        modules = []

        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in {"Attribute", "Property"}:
                attributes.append(elem.attrib.get("name") or elem.attrib.get("id") or "unknown")
            elif tag == "TabularSection":
                tabular_sections.append(elem.attrib.get("name") or elem.attrib.get("id") or "unknown")
            elif tag == "Form":
                forms.append(elem.attrib.get("name") or elem.attrib.get("id") or "unknown")
            elif tag == "Command":
                commands.append(elem.attrib.get("name") or elem.attrib.get("id") or "unknown")

        for mod in self._iter_module_files_for_metadata(path):
            modules.append(mod)

        rel = path.relative_to(self.root).as_posix()
        return {
            "name": name,
            "type": type_name,
            "path": rel,
            "file_name": path.name,
            "attributes": attributes[:200],
            "tabular_sections": tabular_sections[:200],
            "forms": forms[:50],
            "commands": commands[:50],
            "modules": modules[:50],
        }

    def _iter_module_files_for_metadata(self, mdo_path: Path) -> list[str]:
        base = mdo_path.parent
        found: list[str] = []
        for file in sorted(base.rglob("*.bsl")):
            rel = file.relative_to(self.root).as_posix()
            found.append(rel)
        return found

    def _is_metadata_file(self, path: Path) -> bool:
        rel = path.relative_to(self.root).as_posix().lower()
        if not rel.endswith(".mdo"):
            return False
        return any(token in rel for token in ["catalogs", "documents", "accumulationregisters", "informationregisters", "commonmodules", "constants", "reports", "dataprocessors", "tasks", "businessprocesses", "exchangeplans"])

    def _guess_type_name(self, path: Path) -> str:
        parts = path.relative_to(self.root).parts
        for idx, part in enumerate(parts):
            if part.endswith("s") or part in {"Catalogs", "Documents", "Reports", "DataProcessors", "CommonModules"}:
                return part.rstrip("s")
        return path.parent.name

    @staticmethod
    def _xsi_namespace(elem: ET.Element) -> str | None:
        for key in elem.attrib:
            if key.startswith("{http://www.w3.org/2001/XMLSchema-instance}"):
                return "http://www.w3.org/2001/XMLSchema-instance"
        return None

    def _find_method_end(self, text: str, start: int) -> int:
        # Best effort: find the next procedure/function at the same or lower nesting level.
        next_matches = list(re.finditer(r"(?is)\b(?:procedure|function|Процедура|Функция)\b", text[start + 1:]))
        if not next_matches:
            return len(text)
        next_pos = start + 1 + next_matches[0].start()
        return next_pos

    def _find_line_number(self, text: str, needle: str) -> int:
        idx = text.lower().find(needle.lower())
        if idx < 0:
            return 0
        return text[:idx].count("\n") + 1

    def _short_preview(self, text: str, needle: str) -> str:
        idx = text.lower().find(needle.lower())
        if idx < 0:
            return text[:200]
        start = max(0, idx - 120)
        end = min(len(text), idx + 220)
        snippet = text[start:end].replace("\n", " ")
        return snippet.strip()


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_project_summary",
            "description": "Return a brief summary of project size and discovered artifacts.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "list_metadata_objects",
            "description": "List metadata objects in the project.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 200}}, "additionalProperties": False},
        },
        {
            "name": "get_metadata_details",
            "description": "Return metadata details for an object by name.",
            "inputSchema": {"type": "object", "properties": {"object_name": {"type": "string"}}, "required": ["object_name"], "additionalProperties": False},
        },
        {
            "name": "list_modules",
            "description": "List BSL modules in the project.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 200}}, "additionalProperties": False},
        },
        {
            "name": "get_module_structure",
            "description": "Return procedures and functions declared inside a BSL module.",
            "inputSchema": {"type": "object", "properties": {"module_path": {"type": "string"}}, "required": ["module_path"], "additionalProperties": False},
        },
        {
            "name": "read_method_source",
            "description": "Read the entire source or a particular method from a BSL module.",
            "inputSchema": {"type": "object", "properties": {"module_path": {"type": "string"}, "method_name": {"type": "string"}}, "required": ["module_path"], "additionalProperties": False},
        },
        {
            "name": "search_in_code",
            "description": "Search project files for a text query.",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 50}}, "required": ["query"], "additionalProperties": False},
        },
        {
            "name": "get_form_structure",
            "description": "Read the structure of a form XML file and list items, commands, and handlers.",
            "inputSchema": {"type": "object", "properties": {"form_path": {"type": "string"}}, "required": ["form_path"], "additionalProperties": False},
        },
        {
            "name": "get_form_command_handler",
            "description": "Find the command and associated handlers for a form command.",
            "inputSchema": {"type": "object", "properties": {"form_path": {"type": "string"}, "command_name": {"type": "string"}}, "required": ["form_path", "command_name"], "additionalProperties": False},
        },
        {
            "name": "list_dcs_schemas",
            "description": "List DCS / СКД schema files in the project.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_dcs_schema",
            "description": "Read basic structure of a DCS schema XML file.",
            "inputSchema": {"type": "object", "properties": {"schema_path": {"type": "string"}}, "required": ["schema_path"], "additionalProperties": False},
        },
        {
            "name": "list_tabular_document_templates",
            "description": "List tabular document and template XML files.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_tabular_document_template",
            "description": "Read summary information for a tabular document template file.",
            "inputSchema": {"type": "object", "properties": {"template_path": {"type": "string"}}, "required": ["template_path"], "additionalProperties": False},
        },
        {
            "name": "find_document_movements",
            "description": "Find BSL locations where movement registration code is present for a document.",
            "inputSchema": {"type": "object", "properties": {"document_name": {"type": "string"}, "limit": {"type": "integer", "default": 20}}, "required": ["document_name"], "additionalProperties": False},
        },
    ]


def _execute_tool(index: ProjectIndex, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "get_project_summary":
        return index.summary()
    if tool_name == "list_metadata_objects":
        return {"objects": index.list_metadata_objects(limit=int(arguments.get("limit", 200)))}
    if tool_name == "get_metadata_details":
        return index.get_metadata_details(str(arguments["object_name"]))
    if tool_name == "list_modules":
        return {"modules": index.list_modules(limit=int(arguments.get("limit", 200)))}
    if tool_name == "get_module_structure":
        return index.get_module_structure(str(arguments["module_path"]))
    if tool_name == "read_method_source":
        return index.read_method_source(str(arguments["module_path"]), arguments.get("method_name"))
    if tool_name == "search_in_code":
        return {"matches": index.search_in_code(str(arguments["query"]), limit=int(arguments.get("limit", 50)))}
    if tool_name == "get_form_structure":
        return index.get_form_structure(str(arguments["form_path"]))
    if tool_name == "get_form_command_handler":
        return index.get_form_command_handler(str(arguments["form_path"]), str(arguments["command_name"]))
    if tool_name == "list_dcs_schemas":
        return {"schemas": index.list_dcs_schemas()}
    if tool_name == "get_dcs_schema":
        return index.get_dcs_schema(str(arguments["schema_path"]))
    if tool_name == "list_tabular_document_templates":
        return {"templates": index.list_tabular_document_templates()}
    if tool_name == "get_tabular_document_template":
        return index.get_tabular_document_template(str(arguments["template_path"]))
    if tool_name == "find_document_movements":
        return index.find_document_movements(str(arguments["document_name"]), limit=int(arguments.get("limit", 20)))
    raise ValueError(f"Unknown tool: {tool_name}")


def _jsonrpc_response(request_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def _read_requests(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return [obj]
    except json.JSONDecodeError:
        pass
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MCP server for 1C:EDT projects")
    parser.add_argument("--project", default=os.environ.get("EDT_PROJECT_PATH"), help="Path to the 1C:EDT project directory")
    args = parser.parse_args()

    if not args.project:
        print("Project path is required. Pass --project or set EDT_PROJECT_PATH.", file=sys.stderr)
        sys.exit(2)

    index = ProjectIndex(args.project)

    raw = sys.stdin.read()
    for request in _read_requests(raw):
        msg_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if request.get("jsonrpc") == "2.0" and method == "initialize":
            response = _jsonrpc_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "edt-readonly-mcp", "version": "0.1.0"},
            })
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if method == "tools/list":
            response = _jsonrpc_response(msg_id, {"tools": _tool_definitions()})
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = _execute_tool(index, name, args)
                response = _jsonrpc_response(msg_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "structuredContent": result})
            except Exception as exc:  # pragma: no cover - runtime guard
                response = _jsonrpc_response(msg_id, error={"code": -32603, "message": str(exc)})
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if method in {"ping", "notifications/initialized"}:
            response = _jsonrpc_response(msg_id, {"ok": True})
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if method == "resources/list":
            response = _jsonrpc_response(msg_id, {"resources": []})
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        response = _jsonrpc_response(msg_id, error={"code": -32601, "message": f"Method not supported: {method}"})
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
