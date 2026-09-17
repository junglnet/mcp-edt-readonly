# Domain knowledge and automatic enrichment

The server uses two kinds of knowledge:

1. **Generated facts** from the project: register references, query sources, document movement patterns, and usage locations.
2. **Curated concepts** maintained by a developer: business terms, preferred data sources, field mappings, query hints, and objects to avoid.

Generate or refresh the generated part after changing the project:

```bash
python scripts/analyze_project.py --project /path/to/edt/project
```

The script writes:

```text
/path/to/edt/project/.edt-knowledge/domain-index.json
```

It preserves manually maintained `concepts`. Start with `.edt-knowledge/domain-index.example.json` and copy its `concepts` into the generated file, or add them directly to the project file.

## Recommended model workflow

For a question about data, the agent should first resolve the business concept, then inspect the source and schema, and only then call the separate query-execution MCP:

```text
find_data_sources(question)
get_metadata_details(source)
get_query_patterns(source)
execute_1c_query(...)
```

The analyzer does not execute queries and does not modify project files. Its output is evidence, not a final semantic decision: manually curated concepts have priority over inferred facts.
