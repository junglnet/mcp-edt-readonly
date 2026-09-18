from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from edt_readonly_mcp.domain_knowledge import merge_generated_knowledge

EXCLUDED = {".git", ".edt-knowledge", "target", "bin", "obj", ".venv"}
REGISTER_RE = re.compile(r"(?:Регистр(?:Сведений|Накопления|Бухгалтерии)\s*\.\s*([\wА-Яа-яЁё]+)|Registers?\s*\.\s*([\w]+))")
QUERY_RE = re.compile(r"(?is)(?:ИЗ|FROM)\s+(?:Регистр(?:Сведений|Накопления|Бухгалтерии)\s*\.\s*([\wА-Яа-яЁё]+)|([A-Za-z_]\w*))")
MOVEMENT_RE = re.compile(r"(?i)(?:Движения|Movements)\s*[.,]\s*([\wА-Яа-яЁё]+)")


def files(root: Path, suffixes: set[str]):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if set(path.parts) & EXCLUDED:
            continue
        suffix = path.suffix.casefold()
        if suffix in suffixes or path.name.lower().endswith(tuple(ext.casefold() for ext in suffixes)):
            yield path


def analyze(root: Path) -> dict[str, Any]:
    register_usage: Counter[str] = Counter()
    facts: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    for path in files(root, {".bsl", ".mdo", ".xml"}):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for match in REGISTER_RE.finditer(text):
            name = match.group(1) or match.group(2)
            register_usage[name] += 1
            facts.append({"kind": "register_reference", "object": name, "path": rel, "preview": _preview(text, match.start())})
        for match in QUERY_RE.finditer(text):
            name = match.group(1) or match.group(2)
            if name:
                facts.append({"kind": "query_source", "object": name, "path": rel, "preview": _preview(text, match.start())})
        for match in MOVEMENT_RE.finditer(text):
            facts.append({"kind": "document_movement", "register": match.group(1), "path": rel, "preview": _preview(text, match.start())})
    for name, count in register_usage.most_common():
        objects.append({
            "id": f"register:{name}",
            "name": name,
            "type": "data_source_candidate",
            "purpose": "Register referenced by project code; inspect facts and schema before querying.",
            "usage_count": count,
            "aliases": [name],
        })
    return {"version": 1, "generated_by": "analyze_project", "objects": objects, "facts": facts}


def _preview(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    return text[line_start: line_end if line_end >= 0 else len(text)].strip()[:500]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate read-only domain facts from a 1C:EDT project")
    parser.add_argument("--project", required=True, help="Path to an EDT project")
    parser.add_argument("--output", help="Output path; default: PROJECT/.edt-knowledge/domain-index.json")
    args = parser.parse_args()
    root = Path(args.project).resolve()
    output = Path(args.output) if args.output else root / ".edt-knowledge" / "domain-index.json"
    existing: dict[str, Any] = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    result = merge_generated_knowledge(existing, analyze(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(result['objects'])} objects and {len(result['facts'])} facts: {output}")


if __name__ == "__main__":
    main()
