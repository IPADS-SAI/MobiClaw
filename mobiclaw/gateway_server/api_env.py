# -*- coding: utf-8 -*-
"""gateway_server 的 env 相关 API。"""

from __future__ import annotations

from typing import Any

from fastapi import Header

from .models import EnvContentRequest, EnvStructuredRequest


def _gateway_override(name: str, default: Any) -> Any:
    from .. import gateway_server as gateway_module

    return getattr(gateway_module, name, default)


def register_env_routes(app, exported: dict[str, Any]) -> None:
    @app.get("/api/v1/env")
    async def get_env(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        read_env_content = _gateway_override("_read_env_content", None)
        env_file_path = _gateway_override("_env_file_path", None)
        parse_env_variables = _gateway_override("_parse_env_variables", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        content = read_env_content()
        return {"path": str(env_file_path()), "content": content, "variables": parse_env_variables(content)}

    exported["get_env"] = get_env

    @app.put("/api/v1/env")
    async def put_env(body: EnvContentRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        write_env_content = _gateway_override("_write_env_content", None)
        read_env_content = _gateway_override("_read_env_content", None)
        env_file_path = _gateway_override("_env_file_path", None)
        parse_env_variables = _gateway_override("_parse_env_variables", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        write_env_content(body.content)
        content = read_env_content()
        return {"ok": True, "path": str(env_file_path()), "content": content, "variables": parse_env_variables(content)}

    exported["put_env"] = put_env

    @app.get("/api/v1/env/schema")
    async def get_env_schema(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        read_env_content = _gateway_override("_read_env_content", None)
        parse_env_variables = _gateway_override("_parse_env_variables", None)
        split_env_variables = _gateway_override("_split_env_variables", None)
        env_file_path = _gateway_override("_env_file_path", None)
        env_settings_schema = _gateway_override("_ENV_SETTINGS_SCHEMA", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        content = read_env_content()
        variables = parse_env_variables(content)
        managed, unmanaged = split_env_variables(variables)
        return {
            "path": str(env_file_path()),
            "schema": env_settings_schema,
            "values": managed,
            "unmanaged": unmanaged,
            "variables": variables,
            "content": content,
        }

    exported["get_env_schema"] = get_env_schema

    @app.put("/api/v1/env/schema")
    async def put_env_schema(body: EnvStructuredRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        load_config = _gateway_override("load_config", None)
        ensure_auth = _gateway_override("_ensure_auth", None)
        sanitize_structured_values = _gateway_override("_sanitize_structured_values", None)
        managed_env_keys = _gateway_override("_managed_env_keys", None)
        parse_env_variables = _gateway_override("_parse_env_variables", None)
        read_env_content = _gateway_override("_read_env_content", None)
        split_env_variables = _gateway_override("_split_env_variables", None)
        render_structured_env_content = _gateway_override("_render_structured_env_content", None)
        write_env_content = _gateway_override("_write_env_content", None)
        env_file_path = _gateway_override("_env_file_path", None)
        env_settings_schema = _gateway_override("_ENV_SETTINGS_SCHEMA", None)
        cfg = load_config()
        ensure_auth(authorization, cfg)
        incoming_values = sanitize_structured_values(body.values)
        merged_values: dict[str, str] = {}
        for key in managed_env_keys():
            merged_values[key] = incoming_values.get(key, "")
        if body.unmanaged is not None:
            unmanaged = sanitize_structured_values(body.unmanaged)
        elif body.preserve_unmanaged:
            current_variables = parse_env_variables(read_env_content())
            _, unmanaged = split_env_variables(current_variables)
        else:
            unmanaged = {}
        new_content = render_structured_env_content(merged_values, unmanaged)
        write_env_content(new_content)
        content = read_env_content()
        variables = parse_env_variables(content)
        managed, unmanaged_saved = split_env_variables(variables)
        return {
            "ok": True,
            "path": str(env_file_path()),
            "schema": env_settings_schema,
            "values": managed,
            "unmanaged": unmanaged_saved,
            "variables": variables,
            "content": content,
        }

    exported["put_env_schema"] = put_env_schema
