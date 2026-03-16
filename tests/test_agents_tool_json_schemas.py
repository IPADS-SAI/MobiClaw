from __future__ import annotations

from jsonschema import Draft202012Validator

from mobiclaw.agents import create_steward_agent, create_worker_agent


_FUNCTION_TOOL_SCHEMA = {
    "type": "object",
    "required": ["type", "function"],
    "properties": {
        "type": {"const": "function"},
        "function": {
            "type": "object",
            "required": ["name", "parameters"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "required": ["type", "properties"],
                    "properties": {
                        "type": {"const": "object"},
                        "properties": {"type": "object"},
                        "required": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    },
}


_WORKER_CORE_TOOLS = {
    "run_shell_command",
    "run_skill_script",
    "brave_search",
    "arxiv_search",
    "dblp_conference_search",
    "fetch_url_text",
    "fetch_url_readable_text",
    "fetch_url_links",
    "download_file",
    "extract_pdf_text",
    "extract_image_text_ocr",
    "read_docx_text",
    "create_docx_from_text",
    "edit_docx",
    "create_pdf_from_text",
    "read_xlsx_summary",
    "write_xlsx_from_records",
    "write_xlsx_from_rows",
    "write_text_file",
    "search_steward_knowledge",
    "fetch_feishu_chat_history",
    "get_feishu_message",
    "schedule_feishu_meeting",
    "send_feishu_meeting_card",
    "read_pptx_summary",
    "create_pptx_from_outline",
    "edit_pptx",
    "insert_pptx_image",
    "set_pptx_text_style",
}

_STEWARD_CORE_TOOLS = {
    "call_mobi_collect_with_report",
    "delegate_to_worker",
    "call_mobi_action",
    "store_steward_knowledge",
    "search_steward_knowledge",
    "fetch_url_text",
    "run_shell_command",
    "extract_image_text_ocr",
}


def _schema_by_name(agent) -> dict[str, dict]:
    return {item["function"]["name"]: item for item in agent.toolkit.get_json_schemas()}


def _assert_param_descriptions(schema_map: dict[str, dict], tool_names: set[str]) -> None:
    for name in sorted(tool_names):
        schema = schema_map[name]
        properties = schema["function"]["parameters"].get("properties", {})
        for param_name, details in properties.items():
            desc = (details or {}).get("description")
            assert isinstance(desc, str) and desc.strip(), f"{name}.{param_name} missing JSON schema description"


def test_worker_and_steward_tool_json_schema_shape_is_valid() -> None:
    worker = create_worker_agent()
    steward = create_steward_agent()

    validator = Draft202012Validator(_FUNCTION_TOOL_SCHEMA)
    for schema in worker.toolkit.get_json_schemas() + steward.toolkit.get_json_schemas():
        validator.validate(schema)


def test_worker_and_steward_tools_have_parameter_descriptions() -> None:
    worker_schema_map = _schema_by_name(create_worker_agent())
    steward_schema_map = _schema_by_name(create_steward_agent())

    assert _WORKER_CORE_TOOLS.issubset(worker_schema_map.keys())
    assert _STEWARD_CORE_TOOLS.issubset(steward_schema_map.keys())

    _assert_param_descriptions(worker_schema_map, _WORKER_CORE_TOOLS)
    _assert_param_descriptions(steward_schema_map, _STEWARD_CORE_TOOLS)
