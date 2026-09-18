from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


class DomainKnowledge:
    """Read-only generated and curated knowledge for one EDT project."""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        self.knowledge_dir = self.root / ".edt-knowledge"
        self.index_path = self.knowledge_dir / "domain-index.json"
        self.data: dict[str, Any] = {"version": 1, "concepts": [], "objects": [], "facts": []}
        self.load()

    def load(self) -> None:
        if self.index_path.exists():
            try:
                loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass

    def save(self) -> None:
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def search_sources(self, question: str, limit: int = 10) -> dict[str, Any]:
        tokens = self._tokens(question)
        candidates: list[dict[str, Any]] = []
        for item in self.data.get("concepts", []) + self.data.get("objects", []):
            text = " ".join(str(item.get(key, "")) for key in ("id", "name", "type", "description", "purpose", "terms", "aliases", "fields"))
            score = sum(2 if token in text.lower() else 0 for token in tokens)
            if score:
                candidates.append({"score": score, **item})
        candidates.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("name", item.get("id", "")))))
        return {"question": question, "sources": candidates[:limit], "generated": True}

    def object_purpose(self, name: str) -> dict[str, Any]:
        needle = name.casefold()
        for item in self.data.get("concepts", []) + self.data.get("objects", []):
            haystack = json.dumps(item, ensure_ascii=False).casefold()
            if needle in haystack:
                return item
        return {"error": f"No domain knowledge found for: {name}"}

    def query_patterns(self, name: str, limit: int = 20) -> dict[str, Any]:
        needle = name.casefold()
        facts = [fact for fact in self.data.get("facts", []) if needle in json.dumps(fact, ensure_ascii=False).casefold()]
        return {"object": name, "patterns": facts[:limit]}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[\wА-Яа-яЁё]+", text.casefold()) if len(token) > 2}


def merge_generated_knowledge(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Merge analyzer output while preserving manually curated concepts."""
    result = dict(existing)
    result.setdefault("version", 1)
    result["objects"] = generated.get("objects", [])
    result["facts"] = generated.get("facts", [])
    manual_concepts = existing.get("concepts", [])
    manual_ids = {item.get("id") for item in manual_concepts if isinstance(item, dict)}
    result["concepts"] = list(manual_concepts) + [
        concept for concept in generated.get("concepts", [])
        if isinstance(concept, dict) and concept.get("id") not in manual_ids
    ]
    if "metadata" in generated:
        result["metadata"] = generated["metadata"]
    if generated.get("ai_enriched"):
        result["ai_enriched"] = True
    return result
