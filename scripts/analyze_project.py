"""Generate read-only domain knowledge from a 1C:EDT project.

Responsibilities are strictly separated:

* Structure (``objects``) is derived **only** from project metadata files
  (``.mdo``) by a deterministic algorithm. ``id``, ``name``, ``type``,
  ``qualified_name``, ``path`` and ``fields`` are facts about the
  configuration and are never invented or modified by the AI model.
* Code usage (``facts`` and ``usage_count``) is derived only from ``.bsl``
  modules and is aggregated. Names that do not exist in the metadata
  structure never become objects (they stay in ``facts`` with
  ``"unmatched": true``).
* The AI model (optional ``--ai``) adds only descriptive fields
  (``purpose``, ``description``, ``aliases``, ``terms``, ``confidence``).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

# Allow running the script directly (``python scripts/analyze_project.py``)
# without installing the package: resolve the project's ``src`` directory
# relative to this file and make it importable.
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from edt_readonly_mcp.domain_knowledge import merge_generated_knowledge  # noqa: E402

EXCLUDED = {".git", ".edt-knowledge", "target", "bin", "obj", ".venv"}

REGISTER_RE = re.compile(
    r"(?:Регистры?(?:Сведений|Накопления|Бухгалтерии|Расчета)?\s*\.\s*([\wА-Яа-яЁё]+)"
    r"|Registers?\s*\.\s*([\w]+))"
)
QUERY_RE = re.compile(
    r"(?is)(?:\bИЗ\b|\bFROM\b)\s+"
    r"(?:"
    r"(?:Регистры?(?:Сведений|Накопления|Бухгалтерии|Расчета)?"
    r"|Документы?|Справочники?|Перечисления?|Константы?"
    r"|Планы?(?:ВидовХарактеристик|Счетов|ВидовРасчета|Обмена)?"
    r"|Задачи?|БизнесПроцессы?)\s*\.\s*([\wА-Яа-яЁё]+)"
    r"|(?:InformationRegister|AccumulationRegister|AccountingRegister|CalculationRegister"
    r"|Document|Catalog|Enum|Constant|ExchangePlan|Task|BusinessProcess"
    r"|ChartOfCharacteristicTypes|ChartOfAccounts|ChartOfCalculationTypes)\s*\.\s*([A-Za-z_]\w*)"
    r"|([A-Za-z_]\w*)"
    r")"
)
MOVEMENT_RE = re.compile(r"(?i)(?:Движения|Movements)\s*[.,]\s*([\wА-Яа-яЁё]+)")

# EDT metadata category folders: folder name (lowercase) ->
# (Russian type, id prefix, qualified-name prefix).
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "catalogs": ("Справочник", "catalog", "Catalog"),
    "documents": ("Документ", "document", "Document"),
    "informationregisters": ("РегистрСведений", "information_register", "InformationRegister"),
    "accumulationregisters": ("РегистрНакопления", "accumulation_register", "AccumulationRegister"),
    "accountingregisters": ("РегистрБухгалтерии", "accounting_register", "AccountingRegister"),
    "calculationregisters": ("РегистрРасчета", "calculation_register", "CalculationRegister"),
    "enums": ("Перечисление", "enum", "Enum"),
    "constants": ("Константа", "constant", "Constant"),
    "reports": ("Отчет", "report", "Report"),
    "dataprocessors": ("Обработка", "data_processor", "DataProcessor"),
    "exchangeplans": ("ПланОбмена", "exchange_plan", "ExchangePlan"),
    "tasks": ("Задача", "task", "Task"),
    "businessprocesses": ("БизнесПроцесс", "business_process", "BusinessProcess"),
    "sequences": ("Последовательность", "sequence", "Sequence"),
    "commonmodules": ("ОбщийМодуль", "common_module", "CommonModule"),
    "chartsofcharacteristictypes": ("ПланВидовХарактеристик", "characteristic_plan", "ChartOfCharacteristicTypes"),
    "chartsofaccounts": ("ПланСчетов", "chart_of_accounts", "ChartOfAccounts"),
    "chartsofcalculationtypes": ("ПланВидовРасчета", "calculation_type_plan", "ChartOfCalculationTypes"),
}

# Fallback when the folder layout is non-standard: EDT .mdo root tag ->
# category folder name.
TAG_TO_CATEGORY: dict[str, str] = {
    "Catalog": "catalogs",
    "Document": "documents",
    "InformationRegister": "informationregisters",
    "AccumulationRegister": "accumulationregisters",
    "AccountingRegister": "accountingregisters",
    "CalculationRegister": "calculationregisters",
    "Enum": "enums",
    "Constant": "constants",
    "Report": "reports",
    "DataProcessor": "dataprocessors",
    "ExchangePlan": "exchangeplans",
    "Task": "tasks",
    "BusinessProcess": "businessprocesses",
    "Sequence": "sequences",
    "CommonModule": "commonmodules",
    "ChartOfCharacteristicTypes": "chartsofcharacteristictypes",
    "ChartOfAccounts": "chartsofaccounts",
    "ChartOfCalculationTypes": "chartsofcalculationtypes",
}

# Fields the AI model is allowed to set on an object.
DESCRIPTIVE_FIELDS = frozenset({"purpose", "description", "aliases", "terms", "confidence"})
# Fields the AI model must never touch: they describe the real configuration.
STRUCTURAL_FIELDS = frozenset({"id", "name", "type", "qualified_name", "path", "usage_count", "fields", "synonym"})

DEFAULT_SYSTEM_PROMPT = (
    "Ты анализируешь экспортированный проект 1С:EDT. Тебе передан каталог объектов метаданных "
    "(id, name, type). Эта структура получена алгоритмом из файлов метаданных проекта и является "
    "истиной: запрещено создавать новые объекты, запрещено менять id, name и type. Верни только "
    "JSON-объект с ключами objects и concepts, где для каждого объекта заполнены только "
    "описательные поля: purpose, description, aliases, terms, confidence. В concepts ссылайся "
    "только на существующие id объектов. Описывай только то, что подтверждается данными; "
    "не придумывай бизнес-правила. Не включай исходный код и длинные цитаты."
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
    """Build the v2 index: structure objects + aggregated code facts."""
    objects = _scan_structure(root)

    token_to_ids: dict[str, list[str]] = {}
    for obj in objects:
        token_to_ids.setdefault(obj["name"], []).append(obj["id"])
        synonym = obj.get("synonym")
        if synonym:
            token_to_ids.setdefault(synonym, []).append(obj["id"])
    usage = _count_name_usage(root, token_to_ids)
    for obj in objects:
        obj["usage_count"] = usage.get(obj["id"], 0)

    facts = _scan_code_facts(root, objects)
    return {
        "version": 2,
        "generated_by": "analyze_project",
        "objects": objects,
        "facts": facts,
    }


def _scan_structure(root: Path) -> list[dict[str, Any]]:
    """Scan .mdo files and build the strict metadata object list."""
    objects: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.mdo")):
        if any(part in EXCLUDED for part in path.parts):
            continue
        category = _detect_category(path)
        if category is None:
            continue
        mdo_type, prefix, qualified_prefix = CATEGORIES[category]
        name = path.stem
        obj_id = f"{prefix}:{name}"
        if obj_id in objects:
            continue
        fields, synonym = _parse_mdo_details(path)
        obj: dict[str, Any] = {
            "id": obj_id,
            "name": name,
            "type": mdo_type,
            "qualified_name": f"{qualified_prefix}.{name}",
            "path": path.relative_to(root).as_posix(),
            "usage_count": 0,
            "fields": fields,
        }
        if synonym and synonym != name:
            obj["synonym"] = synonym
        objects[obj_id] = obj
    return sorted(objects.values(), key=lambda item: (item["type"], item["name"]))


def _detect_category(path: Path) -> str | None:
    """Determine the metadata category for a .mdo file.

    Primary source is the project folder layout (``Catalogs/X/X.mdo``);
    fallback is the .mdo root XML tag. Files whose category cannot be
    determined are skipped — nothing is ever guessed from a name.
    """
    for parent in (path.parent.name, path.parent.parent.name):
        folder = parent.casefold()
        if folder in CATEGORIES:
            return folder
    try:
        root_tag = ET.parse(path).getroot().tag.rsplit("}", 1)[-1]
    except (ET.ParseError, OSError):
        return None
    local_tag = root_tag
    if local_tag.startswith("MetaObject"):
        local_tag = local_tag.rsplit("-", 1)[-1]
    local_tag = local_tag.rsplit(":", 1)[-1]
    return TAG_TO_CATEGORY.get(local_tag)


# Direct children of an EDT (mdclass) .mdo root that declare object fields.
EDT_FIELD_SECTIONS = frozenset({
    "attributes",
    "dimensions",
    "resources",
    "tabularSections",
    "addressingAttributes",
    "commonAttributes",
    "accountingFlags",
    "extDimensionAccountingFlags",
})


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_mdo_details(mdo_path: Path) -> tuple[dict[str, str], str | None]:
    """Extract structural fields and the Russian synonym from a .mdo file.

    Supports both EDT ``mdclass:`` files (flat layout: direct ``<attributes>``
    children with ``<name>`` / ``<type><types>...`` and ``key``/``value``
    synonyms) and configurator ``MetaObject-*`` files (``Properties`` /
    ``ChildObjects`` layout).
    """
    try:
        root = ET.parse(mdo_path).getroot()
    except (ET.ParseError, OSError):
        return {}, None
    if _local(root.tag).casefold().startswith("metaobject"):
        return _parse_mdo_details_configurator(root)
    return _parse_mdo_details_edt(root)


def _parse_mdo_details_edt(root: ET.Element) -> tuple[dict[str, str], str | None]:
    fields: dict[str, str] = {}
    synonym: str | None = None
    for section in root:
        tag = _local(section.tag)
        if tag == "synonym" and synonym is None:
            synonym = _edt_synonym(section)
        elif tag in EDT_FIELD_SECTIONS:
            name = None
            vtype = ""
            for prop in section:
                prop_tag = _local(prop.tag)
                if prop_tag == "name" and prop.text:
                    name = prop.text.strip()
                elif prop_tag == "type":
                    vtype = _edt_type(prop)
            if name and name not in fields:
                fields[name] = vtype or "Произвольный"
                if len(fields) >= 100:
                    break
    return fields, synonym


def _edt_synonym(synonym_elem: ET.Element) -> str | None:
    """Parse ``<synonym><key>ru</key><value>...</value></synonym>``."""
    key = value = None
    for item in synonym_elem:
        tag = _local(item.tag)
        if tag == "key":
            key = (item.text or "").strip()
        elif tag == "value":
            value = (item.text or "").strip()
    return value or None


def _edt_type(type_elem: ET.Element) -> str:
    for node in type_elem.iter():
        if _local(node.tag) == "types" and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _parse_mdo_details_configurator(root: ET.Element) -> tuple[dict[str, str], str | None]:
    field_tags = {"Attribute", "Dimension", "Resource", "TabularSection", "CommonAttribute", "AddressingAttribute"}
    fields: dict[str, str] = {}
    synonym: str | None = None
    synonym_checked = False
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag == "Synonym" and not synonym_checked:
            synonym_checked = True
            synonym = _configurator_synonym(elem)
        elif tag in field_tags:
            name, vtype = "", ""
            for child in elem:
                if _local(child.tag) != "Properties":
                    continue
                for prop in child:
                    prop_tag = _local(prop.tag)
                    if prop_tag == "Name" and prop.text:
                        name = prop.text.strip()
                    elif prop_tag == "Type" and not vtype:
                        vtype = _first_type_text(prop)
            if name:
                fields[name] = vtype or "Произвольный"
                if len(fields) >= 100:
                    break
    return fields, synonym


def _first_type_text(container: ET.Element) -> str:
    for node in container.iter():
        tag = _local(node.tag)
        if tag in {"Type", "TypeSet"} and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _configurator_synonym(synonym_elem: ET.Element) -> str | None:
    first: str | None = None
    russian: str | None = None
    for item in synonym_elem:
        if _local(item.tag) != "item":
            continue
        lang, content = None, None
        for node in item:
            node_tag = _local(node.tag)
            if node_tag == "lang":
                lang = (node.text or "").strip()
            elif node_tag == "content":
                content = (node.text or "").strip()
        if content and first is None:
            first = content
        if lang == "ru" and content:
            russian = content
            break
    return russian or first


def _count_name_usage(root: Path, token_to_ids: dict[str, list[str]]) -> Counter[str]:
    """Count whole-identifier occurrences of object names in .bsl modules."""
    usage: Counter[str] = Counter()
    for path in files(root, {".bsl"}):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for token in re.findall(r"[\wА-Яа-яЁё]+", text):
            ids = token_to_ids.get(token)
            if ids:
                for obj_id in ids:
                    usage[obj_id] += 1
    return usage


def _scan_code_facts(root: Path, objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect aggregated usage facts from .bsl modules only.

    Each fact is aggregated by (kind, object) with a usage count, up to ten
    example paths and one short preview. Facts referencing names absent from
    the metadata structure are kept for evidence but flagged ``unmatched``;
    they never create objects.
    """
    structure_names = {obj["name"] for obj in objects}
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, name: str | None, path: Path, text: str, position: int) -> None:
        if not name:
            return
        entry = aggregated.setdefault((kind, name), {"count": 0, "paths": set(), "preview": ""})
        entry["count"] += 1
        entry["paths"].add(path.relative_to(root).as_posix())
        if not entry["preview"]:
            entry["preview"] = _preview(text, position)[:200]

    for path in files(root, {".bsl"}):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for match in REGISTER_RE.finditer(text):
            add("register_reference", match.group(1) or match.group(2), path, text, match.start())
        for match in QUERY_RE.finditer(text):
            name = match.group(1) or match.group(2) or match.group(3)
            if name:
                add("query_source", name, path, text, match.start())
        for match in MOVEMENT_RE.finditer(text):
            add("document_movement", match.group(1), path, text, match.start())

    facts: list[dict[str, Any]] = []
    for (kind, name), data in sorted(aggregated.items(), key=lambda item: (item[0][1], item[0][0])):
        fact: dict[str, Any] = {
            "kind": kind,
            "object": name,
            "count": data["count"],
            "paths": sorted(data["paths"])[:10],
            "preview": data["preview"],
        }
        if name not in structure_names:
            fact["unmatched"] = True
        facts.append(fact)
    return facts


def _preview(text: str, position: int) -> str:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    return text[line_start: line_end if line_end >= 0 else len(text)].strip()[:500]


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
    """Build the compact catalog of real metadata objects for the AI.

    Entries come strictly from the project structure: ``objects`` already
    carry exact ``id`` and ``type`` derived from .mdo files, so the model can
    never invent or reclassify anything. The raw fact stream stays local and
    is never sent to the AI.

    Entries are sorted by popularity (usage_count descending) and capped at
    ``limit`` so the model reliably returns descriptions for all of them.
    """
    entries = [
        {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "type": obj.get("type"),
            "usage_count": int(obj.get("usage_count") or 0),
        }
        for obj in analysis.get("objects", [])
        if isinstance(obj, dict) and obj.get("id")
    ]
    entries.sort(key=lambda item: (-int(item.get("usage_count") or 0), str(item.get("name", ""))))
    return entries[:limit] if limit else entries


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

    The AI receives a compact catalog of real metadata objects (id, name,
    type taken from the project structure) — not the raw fact stream — and
    returns descriptive fields for them. The catalog is sent in small batches
    (``catalog_batch_size``) because a single request can only return a
    limited number of descriptions; batching ensures every sent object gets
    one. If the model hits its output limit and truncates a response
    (finish_reason='length'), the chunk is automatically split in half and
    re-sent, so no object is lost. Facts remain local and are merged back
    unchanged. Structural fields (id/name/type/path) cannot be changed by the
    model, and model-provided objects absent from the structure are ignored.
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
    enriched = _apply_enrichment(analysis, full_enrichment)
    enriched["concepts"] = [item for item in enriched.get("concepts", []) if isinstance(item, dict)]
    return enriched


def _apply_enrichment(
    analysis: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """Merge AI descriptions into structure objects without touching structure.

    * Model items whose id and name do not match a real structure object are
      ignored — the model cannot create objects.
    * Only descriptive fields are applied; id, name, type, qualified_name,
      path, usage_count, synonym are protected, and model ``fields`` are
      accepted only when the .mdo file declares none.
    * Concept object references are validated against the structure.
    """
    result = dict(analysis)
    objects = {obj["id"]: dict(obj) for obj in result.get("objects", []) if isinstance(obj, dict) and obj.get("id")}
    by_name = {obj["name"]: obj for obj in objects.values() if obj.get("name")}
    known_ids = set(objects)

    for item in enrichment.get("objects", []):
        if not isinstance(item, dict):
            continue
        target = objects.get(item.get("id"))
        if target is None and item.get("name"):
            target = by_name.get(item["name"])
        if target is None:
            continue
        for key, value in item.items():
            if key in STRUCTURAL_FIELDS or key not in DESCRIPTIVE_FIELDS:
                continue
            if value not in (None, "", [], {}):
                target[key] = value
        if not target.get("fields"):
            model_fields = item.get("fields")
            if isinstance(model_fields, dict) and model_fields:
                target["fields"] = model_fields

    concepts = []
    for concept in enrichment.get("concepts", []):
        if not isinstance(concept, dict):
            continue
        refs = concept.get("objects")
        if isinstance(refs, list):
            concept = dict(concept)
            concept["objects"] = [ref for ref in refs if isinstance(ref, str) and ref in known_ids]
        concepts.append(concept)

    result["objects"] = list(objects.values())
    result["concepts"] = concepts
    result["ai_enriched"] = True
    return result


def _load_ai_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read AI config: {path} ({exc})") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"AI config must contain a JSON object: {path}")
    return config


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
