import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_project  # noqa: E402
from edt_readonly_mcp.domain_knowledge import merge_generated_knowledge  # noqa: E402

CATALOG_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaObject-Catalog xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">
\t<uuid>00000000-0000-0000-0000-000000000001</uuid>
\t<Properties>
\t\t<Name>Номенклатура</Name>
\t\t<Synonym>
\t\t\t<v8:item>
\t\t\t\t<v8:lang>ru</v8:lang>
\t\t\t\t<v8:content>Товары</v8:content>
\t\t\t</v8:item>
\t\t</Synonym>
\t</Properties>
\t<ChildObjects>
\t\t<Attribute uuid="00000000-0000-0000-0000-000000000011">
\t\t\t<Properties>
\t\t\t\t<Name>Артикул</Name>
\t\t\t\t<Type>
\t\t\t\t\t<v8:TypeSet>xs:string</v8:TypeSet>
\t\t\t\t</Type>
\t\t\t</Properties>
\t\t</Attribute>
\t</ChildObjects>
</MetaObject-Catalog>
"""

DOCUMENT_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaObject-Document xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">
\t<uuid>00000000-0000-0000-0000-000000000002</uuid>
\t<Properties>
\t\t<Name>ЗаказПокупателя</Name>
\t</Properties>
</MetaObject-Document>
"""

REGISTER_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaObject-InformationRegister xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core">
\t<uuid>00000000-0000-0000-0000-000000000003</uuid>
\t<Properties>
\t\t<Name>КурсыВалют</Name>
\t</Properties>
\t<ChildObjects>
\t\t<Resource uuid="00000000-0000-0000-0000-000000000012">
\t\t\t<Properties>
\t\t\t\t<Name>Курс</Name>
\t\t\t\t<Type>
\t\t\t\t\t<v8:Type>xs:decimal</v8:Type>
\t\t\t\t</Type>
\t\t\t</Properties>
\t\t</Resource>
\t</ChildObjects>
</MetaObject-InformationRegister>
"""

EDT_CATALOG_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Catalog xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:core="http://g5.1c.ru/v8/dt/mcore" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="bea7f781-5f18-4219-997c-9a767fb284be">
\t<name>Товары</name>
\t<synonym>
\t\t<key>ru</key>
\t\t<value>Товарный справочник</value>
\t</synonym>
\t<attributes uuid="afac85be-333c-43af-b698-a26b03cb038b">
\t\t<name>Артикул</name>
\t\t<type>
\t\t\t<types>String</types>
\t\t</type>
\t</attributes>
</mdclass:Catalog>
"""

CONFIGURATION_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaObjectConfiguration xmlns="http://v8.1c.ru/8.3/MDClasses">
\t<Properties>
\t\t<Name>Конфигурация</Name>
\t</Properties>
\t<ChildObjects>
\t\t<informationRegisters>InformationRegister.ФейковыйРегистр</informationRegisters>
\t</ChildObjects>
</MetaObjectConfiguration>
"""

DOCUMENT_MODULE_BSL = """Процедура ОбработкаПроведения(Отказ)
\tДвижения.Номенклатура.Записывать = Истина;
\tЗапрос = Новый Запрос("ВЫБРАТЬ * ИЗ РегистрСведений.КурсыВалют");
\tТовар = Справочники.Номенклатура.НайтиПоКоду("001");
КонецПроцедуры
"""

SERVICE_MODULE_BSL = """Функция Служебная()
\tЗапрос = Новый Запрос("ВЫБРАТЬ 1 ИЗ xml КАК xml");
\tВозврат Неопределено;
КонецФункции
"""


def _write(root: Path, rel: str, content: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


@pytest.fixture()
def edt_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    _write(root, "Catalogs/Номенклатура/Номенклатура.mdo", CATALOG_MDO)
    _write(root, "Documents/ЗаказПокупателя/ЗаказПокупателя.mdo", DOCUMENT_MDO)
    _write(root, "InformationRegisters/КурсыВалют/КурсыВалют.mdo", REGISTER_MDO)
    _write(root, "Configuration/Configuration.mdo", CONFIGURATION_MDO)
    _write(root, "Documents/ЗаказПокупателя/ObjectModule.bsl", DOCUMENT_MODULE_BSL)
    _write(root, "CommonModules/Служебный.bsl", SERVICE_MODULE_BSL)
    return root


def test_objects_come_from_project_structure(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    ids = {obj["id"] for obj in result["objects"]}
    assert {"catalog:Номенклатура", "document:ЗаказПокупателя", "information_register:КурсыВалют"} <= ids
    assert result["version"] == 2

    nom = next(obj for obj in result["objects"] if obj["id"] == "catalog:Номенклатура")
    assert nom["type"] == "Справочник"
    assert nom["qualified_name"] == "Catalog.Номенклатура"
    assert nom["path"] == "Catalogs/Номенклатура/Номенклатура.mdo"
    assert nom["fields"]["Артикул"] == "xs:string"
    assert nom["synonym"] == "Товары"

    kursy = next(obj for obj in result["objects"] if obj["id"] == "information_register:КурсыВалют")
    assert kursy["type"] == "РегистрСведений"
    assert kursy["qualified_name"] == "InformationRegister.КурсыВалют"
    assert kursy["fields"]["Курс"] == "xs:decimal"


def test_movement_fact_does_not_create_document_object(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    ids = {obj["id"] for obj in result["objects"]}
    assert "document:Номенклатура" not in ids
    assert not any(obj_id.startswith("object:") for obj_id in ids)
    movement = [f for f in result["facts"] if f["kind"] == "document_movement" and f["object"] == "Номенклатура"]
    assert movement, result["facts"][:5]
    assert "unmatched" not in movement[0]


def test_configuration_mdo_produces_no_objects_or_facts(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    ids = {obj["id"] for obj in result["objects"]}
    assert "information_register:ФейковыйРегистр" not in ids
    fact_objects = {fact["object"] for fact in result["facts"]}
    assert "ФейковыйРегистр" not in fact_objects


def test_query_source_fact_matches_structure_object(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    fact = next(f for f in result["facts"] if f["kind"] == "query_source" and f["object"] == "КурсыВалют")
    assert fact["count"] == 1
    assert fact["paths"] == ["Documents/ЗаказПокупателя/ObjectModule.bsl"]
    assert "unmatched" not in fact


def test_unmatched_names_stay_only_in_facts(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    assert all(obj["name"] != "xml" for obj in result["objects"])
    unmatched = [fact for fact in result["facts"] if fact.get("unmatched")]
    assert any(fact["object"] == "xml" for fact in unmatched)


def test_usage_count_counts_bsl_occurrences(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    nom = next(obj for obj in result["objects"] if obj["id"] == "catalog:Номенклатура")
    assert nom["usage_count"] == 2  # Движения.Номенклатура + Справочники.Номенклатура


def test_edt_mdclass_mdo_is_parsed(tmp_path: Path):
    root = tmp_path / "edt"
    _write(root, "Catalogs/Товары/Товары.mdo", EDT_CATALOG_MDO)
    result = analyze_project.analyze(root)
    goods = next(obj for obj in result["objects"] if obj["id"] == "catalog:Товары")
    assert goods["type"] == "Справочник"
    assert goods["qualified_name"] == "Catalog.Товары"
    assert goods["fields"]["Артикул"] == "String"
    assert goods["synonym"] == "Товарный справочник"


def test_category_detection_falls_back_to_mdo_root_tag(tmp_path: Path):
    root = tmp_path / "p"
    _write(root, "Weird/Проба/Проба.mdo", CATALOG_MDO)
    result = analyze_project.analyze(root)
    ids = {obj["id"] for obj in result["objects"]}
    assert "catalog:Проба" in ids


def test_apply_enrichment_ignores_unknown_objects_and_protects_structure(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    enrichment = {
        "objects": [
            {"id": "document:Номенклатура", "name": "Номенклатура", "type": "Документ", "purpose": "фантом"},
            {
                "id": "catalog:Номенклатура",
                "type": "Документ",
                "path": "hacked",
                "qualified_name": "Document.Номенклатура",
                "usage_count": 999,
                "purpose": "Хранение товаров",
                "description": "Справочник товаров и услуг",
                "aliases": ["Товары"],
                "terms": ["товары", "номенклатура"],
                "confidence": 0.9,
            },
        ],
        "concepts": [
            {"id": "concept:Товары", "name": "Товары", "objects": ["catalog:Номенклатура", "document:Номенклатура"]},
        ],
    }
    enriched = analyze_project._apply_enrichment(result, enrichment)
    ids = {obj["id"] for obj in enriched["objects"]}
    assert "document:Номенклатура" not in ids

    nom = next(obj for obj in enriched["objects"] if obj["id"] == "catalog:Номенклатура")
    assert nom["type"] == "Справочник"
    assert nom["path"] == "Catalogs/Номенклатура/Номенклатура.mdo"
    assert nom["qualified_name"] == "Catalog.Номенклатура"
    assert nom["usage_count"] == 2
    assert nom["purpose"] == "Хранение товаров"
    assert nom["description"] == "Справочник товаров и услуг"
    assert nom["aliases"] == ["Товары"]
    assert nom["confidence"] == 0.9

    concept = next(item for item in enriched["concepts"] if item["id"] == "concept:Товары")
    assert concept["objects"] == ["catalog:Номенклатура"]
    assert enriched["ai_enriched"] is True


def test_apply_enrichment_model_fields_only_when_structure_empty(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    enrichment = {"objects": [{"id": "catalog:Номенклатура", "fields": {"товар": "Номенклатура"}}]}
    enriched = analyze_project._apply_enrichment(result, enrichment)
    nom = next(obj for obj in enriched["objects"] if obj["id"] == "catalog:Номенклатура")
    assert nom["fields"]["Артикул"] == "xs:string"  # structural fields win


def test_object_catalog_uses_structure_types(edt_project: Path):
    result = analyze_project.analyze(edt_project)
    catalog = analyze_project._object_catalog(result, limit=0)
    entry = next(item for item in catalog if item["id"] == "catalog:Номенклатура")
    assert entry["type"] == "Справочник"
    assert entry["usage_count"] == 2


def test_merge_generated_knowledge_drops_metadata_and_sets_version2():
    existing = {
        "version": 1,
        "objects": [{"id": "old"}],
        "facts": [],
        "concepts": [{"id": "keep", "name": "Ручное понятие"}],
        "metadata": [{"name": "stale"}],
        "ai_enriched": True,
    }
    generated = {
        "version": 2,
        "objects": [{"id": "catalog:X", "name": "X", "type": "Справочник"}],
        "facts": [{"kind": "query_source", "object": "X", "count": 1, "paths": [], "preview": ""}],
    }
    merged = merge_generated_knowledge(existing, generated)
    assert merged["version"] == 2
    assert merged["objects"] == generated["objects"]
    assert merged["facts"] == generated["facts"]
    assert "metadata" not in merged
    assert merged["concepts"][0]["id"] == "keep"
    assert merged["concepts"][0]["name"] == "Ручное понятие"


def test_merge_generated_knowledge_remaps_legacy_concept_refs():
    existing = {
        "version": 1,
        "concepts": [
            {
                "id": "concept:СтароеПонятие",
                "name": "Старое понятие",
                "objects": ["register:КурсыВалют", "object:SMS", "document:Номенклатура"],
            },
        ],
    }
    generated = {
        "version": 2,
        "objects": [
            {"id": "information_register:КурсыВалют", "name": "КурсыВалют", "type": "РегистрСведений"},
            {"id": "catalog:Номенклатура", "name": "Номенклатура", "type": "Справочник"},
        ],
        "facts": [],
    }
    merged = merge_generated_knowledge(existing, generated)
    concept = next(item for item in merged["concepts"] if item["id"] == "concept:СтароеПонятие")
    assert concept["objects"] == [
        "information_register:КурсыВалют",
        "catalog:Номенклатура",
    ]  # object:SMS dropped (no structure match)
