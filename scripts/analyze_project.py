from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from edt_readonly_mcp.domain_knowledge import merge_generated_knowledge

EXCLUDED = {".git", ".edt-knowledge", "target", "bin", "obj", ".venv"}
REGISTER_RE = re.compile(r"(?:Регистр(?:Сведений|Накопления|Бухгалтерии)\s*\.\s*([\wА-Яа-яЁё]+)|Registers?\s*\.\s*([\w]+))")
QUERY_RE = re.compile(r"(?is)(?:ИЗ|FROM)\s+(?:Регистр(?:Сведений|Накопления|Бухгалтерии)\s*\.\s*([\wА-Яа-яЁё]+)|([A-Za-z_]\w*))")
MOVEMENT_RE = re.compile(r"(?i)(?:Движения|Movements)\s*[.,]\s*([\wА-Яа-яЁё]+)")
DEFAULT_SYSTEM_PROMPT = (
    "Ты анализируешь экспортированный проект 1С:EDT. Верни только JSON-объект с ключами "
    "objects и concepts. Описывай только то, что подтверждается данными; не придумывай "
    "бизнес-правила. Для object используй id существующего объекта, name, purpose, "
    "description, aliases, terms, fields и confidence. Добавляй concepts только для явно "
    "видимых бизнес-сущностей. Не включай исходный код и длинные цитаты."
)


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
    return {"version": 1, "generated_by": "analyze_project", "objects": objects, "facts": facts, "metadata": _metadata_snapshot(root)}


def _metadata_snapshot(root: Path, limit: int = 500) -> list[dict[str, Any]]:
    """Collect compact EDT metadata context without sending source files wholesale."""
    snapshot: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.mdo")):
        if any(part in EXCLUDED for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        names = re.findall(r"(?:name|Name|title|Title|synonym|Synonym)\s*[=:]\s*[\"']([^\"']+)", text)
        snapshot.append({
            "name": path.stem,
            "type": path.parent.name,
            "path": path.relative_to(root).as_posix(),
            "declared_names": list(dict.fromkeys(names))[:20],
        })
        if len(snapshot) >= limit:
            break
    return snapshot


def enrich_with_ai(
    analysis: dict[str, Any],
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout: int,
    system_prompt: str,
) -> dict[str, Any]:
    """Ask an OpenAI-compatible model to add only evidence-based domain metadata."""
    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(analysis, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"AI request failed: {exc}") from exc
    try:
        content = response_data["choices"][0]["message"]["content"]
        enrichment = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("AI returned an invalid JSON response") from exc
    if not isinstance(enrichment, dict):
        raise RuntimeError("AI response must be a JSON object")
    return _apply_enrichment(analysis, enrichment)


def _load_ai_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read AI config: {path} ({exc})") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"AI config must contain a JSON object: {path}")
    return config


def _apply_enrichment(analysis: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    result = dict(analysis)
    generated_objects = {item.get("id"): item for item in result.get("objects", []) if item.get("id")}
    for item in enrichment.get("objects", []):
        if not isinstance(item, dict) or item.get("id") not in generated_objects:
            continue
        generated_objects[item["id"]].update({
            key: value for key, value in item.items()
            if key != "id" and value not in (None, "", [], {})
        })
    result["objects"] = list(generated_objects.values())
    result["concepts"] = [item for item in enrichment.get("concepts", []) if isinstance(item, dict)]
    result["ai_enriched"] = True
    return result


def _preview(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    return text[line_start: line_end if line_end >= 0 else len(text)].strip()[:500]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate read-only domain facts from a 1C:EDT project")
    parser.add_argument("--project", required=True, help="Path to an EDT project")
    parser.add_argument("--output", help="Output path; default: PROJECT/.edt-knowledge/domain-index.json")
    parser.add_argument("--ai", action="store_true", help="Enrich generated knowledge with an OpenAI-compatible model")
    parser.add_argument("--config", help="AI config path; default: PROJECT/.edt-knowledge/ai-agent.json")
    parser.add_argument("--model", help="Override model from AI config")
    parser.add_argument("--base-url", help="Override API base URL from AI config")
    parser.add_argument("--temperature", type=float, help="Override temperature from AI config")
    args = parser.parse_args()
    root = Path(args.project).resolve()
    output = Path(args.output) if args.output else root / ".edt-knowledge" / "domain-index.json"
    existing: dict[str, Any] = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    analysis = analyze(root)
    if args.ai:
        config_path = Path(args.config) if args.config else root / ".edt-knowledge" / "ai-agent.json"
        if not config_path.exists():
            parser.error(f"AI config not found: {config_path}. Copy .edt-knowledge/ai-agent.json.example first.")
        try:
            config = _load_ai_config(config_path)
        except RuntimeError as exc:
            parser.error(str(exc))
        model = args.model or config.get("model") or os.getenv("EDT_AI_MODEL") or "gpt-4o-mini"
        base_url = args.base_url or config.get("base_url") or os.getenv("EDT_AI_BASE_URL") or "https://api.openai.com/v1"
        api_key_env = str(config.get("api_key_env", "EDT_AI_API_KEY"))
        api_key = os.getenv(api_key_env) or os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error(f"--ai requires the {api_key_env} or OPENAI_API_KEY environment variable")
        temperature = args.temperature if args.temperature is not None else float(config.get("temperature", 0))
        timeout = int(config.get("timeout_seconds", 180))
        system_prompt = str(config.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        analysis = enrich_with_ai(analysis, str(model), str(base_url), api_key, temperature, timeout, system_prompt)
    result = merge_generated_knowledge(existing, analysis)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(result['objects'])} objects and {len(result['facts'])} facts: {output}")


if __name__ == "__main__":
    main()
