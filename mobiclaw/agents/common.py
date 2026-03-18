# -*- coding: utf-8 -*-
"""mobiclaw.agents 公共私有函数模块。"""

from __future__ import annotations

import asyncio
import base64
import functools
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

from agentscope.model import OpenAIChatModel

from agentscope.tool import Toolkit

from ..config import MEMORY_CONFIG, MODEL_CONFIG, TOOL_CONFIG
from ..tools import read_memory
from ..tools.decorators import tool_timeout

logger = logging.getLogger("mobiclaw.agents")


def _trim_for_log(text: str, max_chars: int = 260) -> str:
    """裁剪日志显示长度，避免刷屏；不影响真实注入内容。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _trim_block(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "..."


def _normalize_str_list(value: Any, *, max_items: int = 8) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:max(1, max_items)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _format_recent_actions(actions: list[Any], *, limit: int) -> str:
    lines: list[str] = []
    for idx, item in enumerate(actions[-max(1, limit):], start=1):
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type", "") or item.get("action_type", "")).strip() or "unknown"
        step_index = item.get("action_index", item.get("step_index", ""))
        extras = {
            key: value
            for key, value in item.items()
            if key not in {"type", "action_type", "action_index", "step_index"}
        }
        step_label = f"step={step_index}" if str(step_index).strip() else "step=?"
        extras_json = json.dumps(extras, ensure_ascii=False) if extras else "{}"
        lines.append(f"{idx}. {step_label} action={action_type} extras={extras_json}")
    return "\n".join(lines)


def _format_recent_reacts(reacts: list[Any], *, limit: int) -> str:
    lines: list[str] = []
    for idx, item in enumerate(reacts[-max(1, limit):], start=1):
        if not isinstance(item, dict):
            continue
        step_index = item.get("action_index", item.get("step_index", ""))
        reasoning = str(item.get("reasoning", "")).strip()
        function = item.get("function", {}) if isinstance(item.get("function"), dict) else {}
        func_name = str(function.get("name", "")).strip() or "unknown"
        params = function.get("parameters", {})
        params_json = json.dumps(params, ensure_ascii=False) if params else "{}"
        step_label = f"step={step_index}" if str(step_index).strip() else "step=?"
        parts = [f"{idx}. {step_label} function={func_name}"]
        if reasoning:
            parts.append(f"reasoning={reasoning}")
        parts.append(f"params={params_json}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def create_openai_model(
    *,
    stream: bool = True,
    temperature: float | None = None,
    model_name: str | None = None,
) -> OpenAIChatModel:
    """创建 OpenAI 兼容的聊天模型实例。"""
    api_base = MODEL_CONFIG["api_base"]
    if not api_base.startswith("http://") and not api_base.startswith("https://"):
        api_base = "http://" + api_base

    temp = MODEL_CONFIG["temperature"] if temperature is None else temperature
    selected_model_name = (model_name or "").strip() or MODEL_CONFIG["model_name"]
    return OpenAIChatModel(
        model_name=selected_model_name,
        api_key=MODEL_CONFIG["api_key"],
        stream=stream,
        client_kwargs={"base_url": api_base},
        generate_kwargs={"temperature": temp},
    )


def _env_bool(name: str, default: bool) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "y"}


def _extract_text_from_model_response(resp: Any) -> str:
    if resp is None:
        return ""
    getter = None
    try:
        getter = getattr(resp, "get_text_content", None)
    except Exception:  # noqa: BLE001
        getter = None
    if callable(getter):
        try:
            text = getter()
        except Exception:  # noqa: BLE001
            text = ""
        return text if isinstance(text, str) else ""
    try:
        content = getattr(resp, "content", None)
    except Exception:  # noqa: BLE001
        content = None
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            continue
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _parse_vlm_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if "```" in raw:
        chunks = [chunk.strip() for chunk in raw.split("```") if chunk.strip()]
        for chunk in chunks:
            candidate = chunk
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    left = raw.find("{")
    right = raw.rfind("}")
    if left >= 0 and right > left:
        snippet = raw[left : right + 1]
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_vlm_evidence(
    metadata: dict[str, Any],
    *,
    last_n_images: int,
    last_n_steps: int,
    max_reasonings_chars: int,
) -> dict[str, Any]:
    execution = metadata.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    artifacts = execution.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    history = execution.get("history", {})
    if not isinstance(history, dict):
        history = {}
    summary = execution.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}

    images_raw = artifacts.get("images", [])
    images = [str(p) for p in images_raw if isinstance(p, str) and p.strip()]
    selected = images[-max(1, last_n_images):] if images else []
    image_data_urls: list[str] = []
    for image_path in selected:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except Exception:
            continue
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(data).decode("ascii")
        image_data_urls.append(f"data:{mime};base64,{b64}")

    reasonings_raw = history.get("reasonings", [])
    reasonings = [str(item).strip() for item in reasonings_raw if isinstance(item, str) and item.strip()]
    reasonings_text = "\n".join(f"{idx + 1}. {line}" for idx, line in enumerate(reasonings))
    if len(reasonings_text) > max_reasonings_chars:
        reasonings_text = "[truncated earlier reasonings]\n" + reasonings_text[-max_reasonings_chars:]

    actions_raw = history.get("actions", [])
    actions = actions_raw if isinstance(actions_raw, list) else []
    reacts_raw = history.get("reacts", [])
    reacts = reacts_raw if isinstance(reacts_raw, list) else []
    recent_actions_text = _trim_block(
        _format_recent_actions(actions, limit=last_n_steps),
        max_chars=max_reasonings_chars,
    )
    recent_reacts_text = _trim_block(
        _format_recent_reacts(reacts, limit=last_n_steps),
        max_chars=max_reasonings_chars,
    )

    return {
        "task_description": str(
            execution.get("task_description", "")
            or metadata.get("task", "")
            or ""
        ),
        "status_hint": str(summary.get("status_hint", "") or metadata.get("status_hint", "") or ""),
        "step_count": int(summary.get("step_count", metadata.get("step_count", 0)) or 0),
        "action_count": int(summary.get("action_count", metadata.get("action_count", 0)) or 0),
        "images_selected": selected,
        "image_data_urls": image_data_urls,
        "reasonings_text": reasonings_text,
        "reasonings_count": len(reasonings),
        "recent_actions_text": recent_actions_text,
        "recent_reacts_text": recent_reacts_text,
        "last_n_steps": max(1, last_n_steps),
    }


async def _summarize_execution_with_vlm(
    *,
    model: OpenAIChatModel,
    task_desc: str,
    status_hint: str,
    step_count: int,
    action_count: int,
    reasonings_text: str,
    recent_actions_text: str,
    recent_reacts_text: str,
    last_n_steps: int,
    image_data_urls: list[str],
    timeout_s: float,
) -> dict[str, Any]:
    async def _call_vlm_json(prompt_text: str, images: list[str]) -> tuple[dict[str, Any], str]:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
        for image_url in images:
            user_content.append({"type": "image_url", "image_url": {"url": image_url}})

        messages = [{"role": "user", "content": user_content}]
        response = await asyncio.wait_for(model(messages), timeout=timeout_s)
        raw = _extract_text_from_model_response(response)
        return _parse_vlm_json(raw), raw

    empty_summary: dict[str, Any] = {
        "screen_state": "",
        "trajectory_last_steps": [],
        "relevant_information": [],
        "extracted_text": [],
    }
    summary_prompt = (
        f"你是手机自动化任务执行摘要器。请根据任务描述、执行摘要、完整 reasonings 历史、最后{last_n_steps}步轨迹信息以及最终{len(image_data_urls)}张截图，"
        "总结当前页面状态和关键轨迹，提取所有可见事实内容。\n"
        f"task_desc: {task_desc}\n"
        f"status_hint: {status_hint}\n"
        f"step_count: {step_count}, action_count: {action_count}\n"
        "history_reasonings:\n"
        f"{reasonings_text or '[empty]'}\n"
        "recent_actions:\n"
        f"{recent_actions_text or '[empty]'}\n"
        "recent_reacts:\n"
        f"{recent_reacts_text or '[empty]'}\n"
        "请严格基于截图和轨迹，不要臆测。\n"
        "请只输出 JSON，不要输出其他文本。\n"
        "JSON schema:\n"
        '{"summary": {"screen_state": str, "trajectory_last_steps": list[str], "relevant_information": list[str], "extracted_text": list[str]}}\n'
    )

    try:
        summary_parsed, summary_raw_text = await _call_vlm_json(summary_prompt, image_data_urls)
    except Exception as exc:  # noqa: BLE001
        return {
            "summary": empty_summary,
            "error": str(exc),
            "fallback_used": True,
        }

    if not summary_parsed:
        return {
            "summary": empty_summary,
            "error": "vlm_non_json_output",
            "raw_text": summary_raw_text[:800],
            "fallback_used": True,
        }

    logger.debug("vlm.summary_json_output")
    logger.debug(summary_parsed)
    summary_raw = summary_parsed.get("summary", {})
    if not isinstance(summary_raw, dict):
        summary_raw = {}
    summary = {
        "screen_state": str(summary_raw.get("screen_state", "") or ""),
        "trajectory_last_steps": _normalize_str_list(
            summary_raw.get("trajectory_last_steps", []),
            max_items=last_n_steps,
        ),
        "relevant_information": _normalize_str_list(
            summary_raw.get("relevant_information", []),
            max_items=10,
        ),
        "extracted_text": _normalize_str_list(
            summary_raw.get("extracted_text", []),
            max_items=10,
        ),
    }

    result: dict[str, Any] = {"summary": summary}

    extraction_images = image_data_urls[-3:] if image_data_urls else []
    extract_prompt = (
        "你是手机自动化任务结果提取器。\n"
        f"请仅根据最后{len(extraction_images)}张截图中可见内容，提取与任务目标直接相关的所有信息；"
        "不要重复输出无关页面元素，不要推断截图中没有展示的内容。\n"
        f"task_desc: {task_desc}\n"
        "输出应聚焦最终结果、关键字段、页面上能直接读到的目标相关文本。\n"
        "请只输出 JSON，不要输出其他文本。\n"
        "JSON schema:\n"
        '{"relevant_information": list[str], "extracted_text": list[str]}\n'
    )

    try:
        extract_parsed, extract_raw_text = await _call_vlm_json(extract_prompt, extraction_images)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"extraction_failed: {exc}"
        result["extraction_error"] = str(exc)
        return result

    if not extract_parsed:
        result["error"] = "extraction_non_json_output"
        result["extraction_error"] = "vlm_non_json_output"
        result["extraction_raw_text"] = extract_raw_text[:800]
        return result

    logger.debug("vlm.extract_json_output")
    logger.debug(extract_parsed)
    relevant_information = _normalize_str_list(extract_parsed.get("relevant_information", []), max_items=10)
    extracted_text = _normalize_str_list(extract_parsed.get("extracted_text", []), max_items=10)
    result["summary"] = {
        "screen_state": summary["screen_state"],
        "trajectory_last_steps": summary["trajectory_last_steps"],
        "relevant_information": relevant_information or summary["relevant_information"],
        "extracted_text": extracted_text or summary["extracted_text"],
    }
    return result


def register_tool_with_timeout(
    toolkit: Toolkit,
    timeout_s: float,
    func,
    *,
    func_description: str,
    group_name: str | None = None,
) -> None:
    """Register a tool function with automatic timeout wrapping.

    The function is wrapped by :func:`tool_timeout` so that execution
    exceeding *timeout_s* returns a ``[Tool Timeout]`` ToolResponse
    instead of blocking indefinitely.

    Special handling for ``functools.partial``: after wrapping, the result
    is no longer a ``partial`` instance, so agentscope would not remove
    bound kwargs from the schema.  We extract them manually and pass as
    ``preset_kwargs`` to keep the schema clean.

    Args:
        toolkit: The Toolkit instance to register the tool on.
        timeout_s: Timeout in seconds for this tool call.
        func: The tool function (sync or async).
        func_description: Description passed to ``register_tool_function``.
        group_name: Optional tool group name.
    """
    wrapped = tool_timeout(timeout_s)(func)
    reg_kwargs: dict[str, Any] = {"func_description": func_description}
    if group_name is not None:
        reg_kwargs["group_name"] = group_name

    # If the original func is a functools.partial, extract its bound kwargs
    # so they don't leak into the parameter schema.
    if isinstance(func, functools.partial):
        preset: dict[str, Any] = dict(func.keywords)
        if func.args:
            param_names = list(inspect.signature(func.func).parameters.keys())
            for i, arg in enumerate(func.args):
                if i < len(param_names):
                    preset[param_names[i]] = arg
        if preset:
            reg_kwargs["preset_kwargs"] = preset

    toolkit.register_tool_function(wrapped, **reg_kwargs)


def _build_skill_prompt_suffix(skill_context: str | None) -> str:
    """构建技能上下文补充提示。

    参数说明：
        skill_context: 技能选择阶段输出的技能说明文本。
    返回值说明：
        str: 可直接拼接到系统提示词末尾的约束文本；无输入时返回空字符串。
    """
    text = (skill_context or "").strip()
    logger.debug("Skill context length(chars): %d", len(text))
    logger.debug("Skill context: %s", _trim_for_log(text, max_chars=260))
    if not text:
        return ""
    return (
        "\n\n[Activated Skills]\n"
        f"{text}\n"
        "使用方式：仅在与当前任务直接相关时参考这些技能约束；"
        "若不相关则忽略，不要为了使用技能而使用技能。"
    )


def _build_memory_prompt() -> str:
    """构建长期记忆 prompt 片段（复用于所有 agent）。"""
    if not MEMORY_CONFIG["enabled"]:
        return ""
    mem = read_memory()
    return f"\n\n[长期记忆]\n{mem}\n" if mem else ""
