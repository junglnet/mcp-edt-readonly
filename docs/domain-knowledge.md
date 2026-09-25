# Domain knowledge and automatic enrichment

The server uses two kinds of knowledge:

1. **Generated facts** from the project: metadata structure, register references, query sources, document movement patterns.
2. **Curated concepts** maintained by a developer: business terms, preferred data sources, field mappings, query hints, and objects to avoid.

## Index format (version 2)

The analyzer writes `PROJECT/.edt-knowledge/domain-index.json`:

```json
{
  "version": 2,
  "objects": [
    {
      "id": "catalog:Номенклатура",
      "name": "Номенклатура",
      "type": "Справочник",
      "qualified_name": "Catalog.Номенклатура",
      "path": "Catalogs/Номенклатура/Номенклатура.mdo",
      "synonym": "Товары",
      "usage_count": 12,
      "fields": {"Артикул": "xs:string"},
      "purpose": "…", "description": "…", "terms": ["…"], "aliases": ["…"],
      "confidence": 0.9
    }
  ],
  "facts": [
    {"kind": "query_source", "object": "КурсыВалют", "count": 4, "paths": ["…"], "preview": "…"}
  ],
  "concepts": [
    {"id": "concept:Товары", "name": "Товары", "objects": ["catalog:Номенклатура"], "description": "…"}
  ],
  "ai_enriched": true
}
```

### Strict separation of responsibilities

| Layer | Source | Fields | Mutable by AI |
|---|---|---|---|
| Structure | deterministic scanner of `.mdo` files and EDT category folders (`Catalogs`, `Documents`, `InformationRegisters`, …) | `id`, `name`, `type`, `qualified_name`, `path`, `synonym`, `fields` | **no** |
| Usage | deterministic scan of `.bsl` modules only | `usage_count`, `facts` (`kind`, `object`, `count`, `paths`, `preview`) | **no** |
| Description | optional AI enrichment (`--ai`) | `purpose`, `description`, `aliases`, `terms`, `confidence` | **yes — the only thing the model adds** |

Rules enforced by the code (not by prompt only):

- `objects` contains **only** real metadata objects found in the project. An id prefix is derived from the actual category (`catalog:`, `document:`, `information_register:`, `accumulation_register:`, `accounting_register:`, `report:`, `data_processor:`, `enum:`, `constant:`, `exchange_plan:`, `task:`, `business_process:`, `common_module:`, …). Nothing is guessed from identifiers found in code.
- Facts referencing a name that is absent from the structure are kept in `facts` with `"unmatched": true` but never create objects. This is what prevents entries like `document:Номенклатура` (born from `Движения.Номенклатура`, where `Номенклатура` is actually a catalog/register name).
- `_apply_enrichment()` ignores model objects whose id/name do not match the structure, rejects changes to structural fields, and drops concept references pointing to unknown ids.
- The v1 `metadata` section is removed: it duplicated `objects` in degraded form and was read by no tool.
- `usage_count` counts occurrences in `.bsl` code only; declarations in `Configuration/Configuration.mdo` and other `.mdo` files are not counted as usage.

### Field reference

**Structural (algorithm only):**

- `id` — stable key `prefix:Name`; used for references from `concepts` and deduplication.
- `name` — metadata name as in the configuration.
- `type` — exact 1C type (`Справочник`, `Документ`, `РегистрСведений`, …); determines query table syntax.
- `qualified_name` — full name for queries (`Catalog.Номенклатура`, `InformationRegister.КурсыВалют`); this is what a query tool substitutes into `SELECT ... FROM`.
- `path` — `.mdo` path for `get_metadata_details` to read the full schema.
- `synonym` — Russian synonym declared in `.mdo` (also indexed for search).
- `fields` — attributes/dimensions/resources with types parsed from `.mdo`.

**Computed (algorithm only):**

- `usage_count` — occurrences of the object name/synonym in `.bsl` code; ranks candidates in `find_data_sources` and orders the AI catalog.
- `facts.kind` — `query_source` / `register_reference` / `document_movement`; aggregated per object with `count`, example `paths` and one `preview`. Used by `get_query_patterns`.

**Descriptive (AI only):**

- `purpose`, `description` — human-readable meaning; the file exists so objects can be found by description.
- `terms` — keywords/synonyms used by semantic search (`search_sources`).
- `aliases` — alternative spellings of the name.
- `confidence` — model certainty (0..1) for ranking conflicting candidates.

## Generation

```bash
python scripts/analyze_project.py --project /path/to/edt/project
```

AI enrichment is optional and adds only descriptive fields:

```bash
python scripts/analyze_project.py --project /path/to/edt/project --ai
```

The script preserves manually maintained `concepts` (they survive regeneration; see `.edt-knowledge/domain-index.example.json`). Concept `objects` references are validated against the generated structure — links to removed objects are dropped.

## Recommended model workflow

For a question about data, the agent should first resolve the business concept, then inspect the source and schema, and only then call the separate query-execution MCP:

```text
find_data_sources(question)
get_metadata_details(source)
get_query_patterns(source)
execute_1c_query(...)
```

The analyzer does not execute queries and does not modify project files. Its output is evidence, not a final semantic decision: manually curated concepts have priority over inferred facts.
