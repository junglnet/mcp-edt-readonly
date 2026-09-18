from pathlib import Path
import json
import os
import subprocess
import sys

from edt_readonly_mcp import ProjectIndex

PROJECT = Path(r"C:\Users\aderk\projects\unf\unf-bochky\src\unf")


def test_list_forms_finds_edt_form_files():
    index = ProjectIndex(str(PROJECT))
    forms = index.list_forms()
    assert forms, "expected at least one Form.form file in sample EDT project"
    assert any(path.endswith("Form.form") for path in forms), forms[:10]


def test_list_dcs_schemas_finds_template_dcs_files():
    index = ProjectIndex(str(PROJECT))
    schemas = index.list_dcs_schemas()
    assert schemas, "expected at least one .dcs template in sample EDT project"
    assert any(path.endswith(".dcs") for path in schemas), schemas[:10]


def test_get_form_structure_reads_form_xml():
    index = ProjectIndex(str(PROJECT))
    form_path = "src/CommonForms/АварийныйРежимИСМП/Form.form"
    result = index.get_form_structure(form_path)
    assert "error" not in result, result
    assert result["items"], result


def test_get_metadata_details_allows_partial_name_lookup():
    index = ProjectIndex(str(PROJECT))
    objects = index.list_metadata_objects(20)
    name = objects[0]["name"]
    partial = name[: max(3, len(name) // 2)]
    result = index.get_metadata_details(partial)
    assert "error" not in result, result
    assert result["name"].lower().startswith(partial.lower()) or partial.lower() in result["name"].lower(), result


def test_get_metadata_details_accepts_russian_type_prefix():
    index = ProjectIndex(str(PROJECT))
    result = index.get_metadata_details("Документ.ЗаказПокупателя")
    assert "error" not in result, result
    assert result["name"] == "ЗаказПокупателя", result
    assert result["type"] == "Document", result


def test_module_entrypoint_handles_metadata_lookup():
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_metadata_details",
            "arguments": {"object_name": "Документ.ЗаказПокупателя"},
        },
    }
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    environment["EDT_PROJECT_PATH"] = str(PROJECT)
    completed = subprocess.run(
        [sys.executable, "-m", "edt_readonly_mcp"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )
    response = json.loads(completed.stdout)
    result = json.loads(response["result"]["content"][0]["text"])
    assert result["name"] == "ЗаказПокупателя", result


def test_search_in_code_finds_text_inside_form_files():
    index = ProjectIndex(str(PROJECT))
    matches = index.search_in_code("Аварийный режим", limit=10)
    assert matches, "expected to find the form title text in Form.form"
    assert any(item["path"].endswith("Form.form") for item in matches), matches[:5]


def test_get_form_command_handler_reports_available_commands():
    index = ProjectIndex(str(PROJECT))
    form_path = "src/CommonForms/АварийныйРежимИСМП/Form.form"
    result = index.get_form_command_handler(form_path, "НесуществующаяКоманда")
    assert "error" in result, result
    assert "available_commands" in result, result
