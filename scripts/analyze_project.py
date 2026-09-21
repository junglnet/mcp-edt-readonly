from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import time
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


class TruncatedResponseError(RuntimeError):
    """The model stopped before finishing the JSON (finish_reason='length').

    Such a response cannot be repaired by a plain retry — the same request
    would be truncated again. The batch must be split into smaller pieces.
    """


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


def _call_chat(
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
    system_prompt: str,
    user_content: str,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> dict[str, Any]:
    """Run a single chat-completions request with retries for transient failures."""
    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    def _single_attempt() -> dict[str, Any]:
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, http.client.HTTPException) as exc:
            raise RuntimeError(f"AI request failed: {exc}") from exc
        if not isinstance(response_data, dict):
            raise RuntimeError(
                f"AI returned an unexpected response (not a JSON object): {str(response_data)[:300]}"
            )
        if "error" in response_data:
            raise RuntimeError(f"AI provider returned an error: {response_data['error']}")
        reason = ""
        if response_data.get("choices"):
            choice = response_data["choices"][0]
            if not isinstance(choice, dict):
                raise RuntimeError(
                    f"AI returned an invalid response: 'choice' is not an object. "
                    f"Raw response (truncated): {str(response_data)[:500]}"
                )
            if choice.get("finish_reason") == "error" or choice.get("error"):
                err = choice.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else None
                message = err.get("message") if isinstance(err, dict) else str(err)
                return {"_transient_error": {"code": code, "message": message}}
            reason = choice.get("finish_reason") or choice.get("native_finish_reason") or ""
        try:
            message_obj = response_data["choices"][0]["message"]
            content = message_obj.get("content") or ""
            if not content:
                content = message_obj.get("reasoning") or message_obj.get("reasoning_content") or ""
            enrichment = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            if reason == "length":
                raise TruncatedResponseError(
                    "AI response truncated: finish_reason='length' and the content is "
                    "not valid JSON. The batch will be split and re-sent."
                ) from exc
            raise RuntimeError(
                "AI returned an invalid response: message content/reasoning is not valid JSON. "
                f"Raw response (truncated): {str(response_data)[:500]}"
            ) from exc
        if not isinstance(enrichment, dict):
            raise RuntimeError("AI response must be a JSON object")
        if "_transient_error" in enrichment:
            raise RuntimeError(
                "AI provider returned an embedded error: "
                f"{enrichment['_transient_error'].get('message')} "
                f"(code {enrichment['_transient_error'].get('code')})"
            )
        return enrichment

    attempt = 0
    while True:
        attempt += 1
        try:
            result = _single_attempt()
            if "_transient_error" in result:
                err = result["_transient_error"]
                message = f"AI provider error: {err.get('message')} (code {err.get('code')})"
                if attempt < max_retries:
                    print(f"    retry {attempt}/{max_retries} after transient error: {message}")
                    time.sleep(retry_delay * attempt)
                    continue
                raise RuntimeError(message)
            return result
        except RuntimeError as exc:
            if attempt < max_retries and _is_transient(str(exc)):
                print(f"    retry {attempt}/{max_retries}: {exc}")
                time.sleep(retry_delay * attempt)
                continue
            raise


def _is_transient(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "connection lost",
        "connection error",
        "please retry",
        "retry later",
        "try again",
        "incompleteread",
        "incomplete read",
        "chunked",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "remote disconnected",
        "network",
        "timed out",
        "timeout",
        "unavailable",
        "temporarily",
        "temporary failure",
        "504",
        "502",
        "503",
        "500",
        "429",
        "overloaded",
        "internal server error",
        "read timed out",
    )
    return any(marker in lowered for marker in markers)


def _object_catalog(analysis: dict[str, Any], limit: int = 300) -> list[dict[str, Any]]:
    """Build a compact, deduplicated catalog of objects {id, name, type} for the AI.

    Only names that look like real business objects (directories, documents,
    registers — Cyrillic identifiers and/or PascalCase) are sent to the model.
    The raw fact stream stays entirely local and is never sent to the AI.

    Entries are sorted by popularity (usage_count descending) and capped at
    ``limit`` so the model reliably returns descriptions for all of them in a
    single response (otherwise the largest projects hit output limits and many
    objects are left without an AI description).
    """
    seen: dict[str, str] = {}
    usage: dict[str, int] = {}
    kind_names = {
        "document_movement": "Документ",
        "query_source": None,
        "register_reference": "Регистр",
    }
    for fact in analysis.get("facts", []):
        if not isinstance(fact, dict):
            continue
        kind = fact.get("kind")
        name = fact.get("object") or (fact.get("register") if kind == "document_movement" else None)
        if not isinstance(name, str) or not name:
            continue
        obj_type = kind_names.get(kind)
        if obj_type is None:
            # for query_source we can't reliably tell the type from an identifier alone
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) and not re.fullmatch(r"[А-ЯЁ][\wА-Яа-яЁё]*", name):
                continue
            obj_type = _classify_name(name)
        if name not in seen:
            seen[name] = obj_type
        usage[name] = usage.get(name, 0) + 1
    for obj in analysis.get("objects", []):
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        if not isinstance(name, str) or not name:
            continue
        seen[name] = str(obj.get("type") or _classify_name(name))
        usage[name] = max(usage.get(name, 0), int(obj.get("usage_count") or 0))

    object_ids = {obj.get("name"): obj.get("id") for obj in analysis.get("objects", []) if isinstance(obj, dict) and obj.get("id")}
    usage_counts = {obj.get("name"): int(obj.get("usage_count") or 0) for obj in analysis.get("objects", []) if isinstance(obj, dict)}
    entries = []
    for name, obj_type in seen.items():
        obj_id = object_ids.get(name)
        if not obj_id:
            obj_id = _default_object_id(name, obj_type)
        entries.append({
            "id": obj_id,
            "name": name,
            "type": obj_type,
            "usage_count": max(usage.get(name, 0), usage_counts.get(name, 0)),
        })
    entries.sort(key=lambda item: (-int(item.get("usage_count") or 0), str(item.get("name", ""))))
    return entries[:limit] if limit else entries


def _default_object_id(name: str, obj_type: str) -> str:
    """Generate a stable id like ``register:Name`` when no id exists yet."""
    lowered = obj_type.casefold()
    if any(key in lowered for key in ("регистр", "register")):
        prefix = "register"
    elif any(key in lowered for key in ("документ", "document")):
        prefix = "document"
    elif any(key in lowered for key in ("справочник", "справочн", "каталог", "directory", "catalog")):
        prefix = "catalog"
    else:
        prefix = "object"
    return f"{prefix}:{name}"


def _classify_name(name: str) -> str:
    """Best-effort type label based on the identifier shape."""
    lowered = name.casefold()
    if any(key in lowered for key in ("справочник", "справочн", "каталог", "directory", "catalog")):
        return "Справочник"
    if any(key in lowered for key in ("документ", "document")):
        return "Документ"
    if any(key in lowered for key in ("регистр", "register")):
        return "Регистр"
    return "Объект"


def enrich_with_ai(
    analysis: dict[str, Any],
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout: int,
    system_prompt: str,
    max_retries: int = 3,
    retry_delay_seconds: float = 5.0,
    catalog_limit: int = 0,
    catalog_batch_size: int = 60,
) -> dict[str, Any]:
    """Ask an OpenAI-compatible model to describe only the business objects.

    The AI receives a compact catalog of object names (directories, documents,
    registers) — not the raw fact stream — and returns purposes/descriptions
    for them. The catalog is sent in small batches (``catalog_batch_size``)
    because a single request can only return a limited number of descriptions;
    batching ensures every sent object gets one. If the model hits its output
    limit and truncates a response (finish_reason='length'), the chunk is
    automatically split in half and re-sent, so no object is lost. Facts
    remain local and are merged back unchanged.
    """
    catalog = _object_catalog(analysis, limit=catalog_limit)
    if not catalog:
        raise RuntimeError("AI enrichment: catalog is empty (no objects found)")
    full_enrichment: dict[str, Any] = {"objects": [], "concepts": []}
    pending: list[list[dict[str, Any]]] = [
        catalog[start:start + catalog_batch_size]
        for start in range(0, len(catalog), catalog_batch_size)
    ]
    total_batches = len(pending)
    attempts = 0
    while pending:
        chunk = pending.pop(0)
        attempts += 1
        print(f"  AI enrichment batch {attempts}/{total_batches}: {len(chunk)} objects")
        user_content = json.dumps({"objects": chunk}, ensure_ascii=False)
        try:
            enrichment = _call_chat(
                base_url,
                api_key,
                model,
                temperature,
                timeout,
                system_prompt,
                user_content,
                max_retries,
                retry_delay_seconds,
            )
        except TruncatedResponseError:
            if len(chunk) <= 1:
                raise
            half = (len(chunk) + 1) // 2
            print(
                f"    truncated for {len(chunk)} objects; "
                f"splitting into {half} + {len(chunk) - half}"
            )
            pending.insert(0, chunk[half:])
            pending.insert(0, chunk[:half])
            total_batches += 1
            continue
        full_enrichment["objects"].extend(enrichment.get("objects", []))
        full_enrichment["concepts"].extend(enrichment.get("concepts", []))
    enriched = _apply_enrichment(analysis, full_enrichment, catalog)
    enriched["concepts"] = [item for item in enriched.get("concepts", []) if isinstance(item, dict)]
    return enriched


def _load_ai_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read AI config: {path} ({exc})") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"AI config must contain a JSON object: {path}")
    return config


def _apply_enrichment(
    analysis: dict[str, Any],
    enrichment: dict[str, Any],
    catalog: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    result = dict(analysis)
    generated_objects = {item.get("id"): item for item in result.get("objects", []) if item.get("id")}
    by_name = {item.get("name"): item for item in result.get("objects", []) if item.get("name")}
    # Ensure every catalog entry is present so that name-only matches can land even
    # for objects that only ever appeared in facts (not in analysis["objects"]).
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        obj_id = entry.get("id")
        name = entry.get("name")
        if obj_id and obj_id not in generated_objects:
            new_obj = {
                "id": obj_id,
                "name": name,
                "type": entry.get("type") or "data_source_candidate",
                "purpose": "Register referenced by project code; inspect facts and schema before querying.",
                "aliases": [name] if name else [],
            }
            generated_objects[obj_id] = new_obj
            if name:
                by_name[name] = new_obj
    for item in enrichment.get("objects", []):
        if not isinstance(item, dict):
            continue
        target = generated_objects.get(item.get("id"))
        if target is None and item.get("name"):
            target = by_name.get(item["name"])
        if target is None:
            continue
        target.update({
            key: value for key, value in item.items()
            if key not in ("id",) and value not in (None, "", [], {})
        })
    result["objects"] = list(generated_objects.values())
    result["concepts"] = [item for item in enrichment.get("concepts", []) if isinstance(item, dict)]
    result["ai_enriched"] = True
    return result


def _preview(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    return text[line_start: line_end if line_end >= 0 else len(text)].strip()[:500]


def _find_ai_config(start: Path) -> Path | None:
    """Search for .edt-knowledge/ai-agent.json from `start` up to the filesystem root."""
    current = start
    while True:
        candidate = current / ".edt-knowledge" / "ai-agent.json"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate read-only domain facts from a 1C:EDT project")
    parser.add_argument("--project", required=True, help="Path to an EDT project")
    parser.add_argument("--output", help="Output path; default: PROJECT/.edt-knowledge/domain-index.json")
    parser.add_argument("--ai", action="store_true", help="Enrich generated knowledge with an OpenAI-compatible model")
    parser.add_argument("--config", help="AI config path; default: PROJECT/.edt-knowledge/ai-agent.json")
    parser.add_argument("--model", help="Override model from AI config")
    parser.add_argument("--base-url", help="Override API base URL from AI config")
    parser.add_argument("--temperature", type=float, help="Override temperature from AI config")
    parser.add_argument("--max-retries", type=int, help="Max retries for transient AI errors (default: from config or 3)")
    parser.add_argument("--retry-delay", type=float, help="Base delay in seconds between retries (default: from config or 5)")
    parser.add_argument("--catalog-limit", type=int, help="Max objects sent to the AI overall (0 = all; default: from config or 0)")
    parser.add_argument("--catalog-batch-size", type=int, help="Objects per AI request; auto-batched to respect output limits (default: from config or 60)")
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
        if args.config:
            config_path = Path(args.config)
        else:
            config_path = _find_ai_config(root)
        if config_path is None:
            parser.error(
                f"AI config not found under '{root}' or its parent directories. "
                "Copy .edt-knowledge/ai-agent.json.example first or pass --config."
            )
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
        max_retries = args.max_retries if args.max_retries is not None else int(config.get("max_retries", 3))
        retry_delay = args.retry_delay if args.retry_delay is not None else float(config.get("retry_delay_seconds", 5))
        catalog_limit = args.catalog_limit if args.catalog_limit is not None else int(config.get("catalog_limit", 0))
        catalog_batch_size = args.catalog_batch_size if args.catalog_batch_size is not None else int(config.get("catalog_batch_size", 60))
        analysis = enrich_with_ai(
            analysis,
            str(model),
            str(base_url),
            api_key,
            temperature,
            timeout,
            system_prompt,
            max_retries,
            retry_delay,
            catalog_limit,
            catalog_batch_size,
        )
    result = merge_generated_knowledge(existing, analysis)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(result['objects'])} objects and {len(result['facts'])} facts: {output}")


if __name__ == "__main__":
    main()
