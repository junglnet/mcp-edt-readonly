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

    @staticmethod
    def _name_candidates(name: str) -> list[str]:
        """Return lookup variants for a metadata object name.

        Handles the 1C syntax ``Документ.ЗаказПокупателя`` (or
        ``document:ЗаказПокупателя``) by also trying the bare tail part,
        since entries in the index are stored as ``document:ЗаказПокупателя``
        / ``ЗаказПокупателя``.
        """
        candidates = [name]
        for sep in (".", ":"):
            if sep in name:
                tail = name.rsplit(sep, 1)[-1].strip()
                if tail and tail not in candidates:
                    candidates.append(tail)
        return candidates

    @staticmethod
    def _richness(item: dict[str, Any]) -> int:
        """Score how informative an entry is (purpose/description/content)."""
        score = 0
        for key in ("purpose", "description", "terms", "fields", "aliases"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                score += 1 + min(len(value.strip()), 100)
            elif isinstance(value, list) and value:
                score += 1
        return score

    @staticmethod
    def _match_score(candidate: str, item: dict[str, Any]) -> int:
        """Score how exact a match is: exact id/name wins over substring.

        Returns a tuple-rank via two integers: (exact, rich) — but since callers
        need a single comparison, we encode exactness as +1_000_000 dominance.
        """
        needle = candidate.casefold()
        item_id = str(item.get("id", "")).casefold()
        item_name = str(item.get("name", "")).casefold()
        if needle == item_id or needle == item_name:
            return 1_000_000
        aliases = item.get("aliases", [])
        if isinstance(aliases, list) and any(isinstance(a, str) and a.casefold() == needle for a in aliases):
            return 1_000_000
        return 0

    def object_purpose(self, name: str) -> dict[str, Any]:
        best: dict[str, Any] | None = None
        best_key = (-1, -1)
        for candidate in self._name_candidates(name):
            needle = candidate.casefold()
            for item in self.data.get("concepts", []) + self.data.get("objects", []):
                if not isinstance(item, dict):
                    continue
                haystack = json.dumps(item, ensure_ascii=False).casefold()
                if needle not in haystack:
                    continue
                key = (self._match_score(candidate, item), self._richness(item))
                if key > best_key:
                    best = item
                    best_key = key
        if best is not None:
            return best
        return {"error": f"No domain knowledge found for: {name}"}

    def query_patterns(self, name: str, limit: int = 20) -> dict[str, Any]:
        facts: list[dict[str, Any]] = []
        for candidate in self._name_candidates(name):
            needle = candidate.casefold()
            for fact in self.data.get("facts", []):
                if needle in json.dumps(fact, ensure_ascii=False).casefold() and fact not in facts:
                    facts.append(fact)
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
