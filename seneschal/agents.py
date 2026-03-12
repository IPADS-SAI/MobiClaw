# -*- coding: utf-8 -*-
"""Seneschal Agent 构建与能力注册模块。

核心功能：
- 统一创建 Router/Planner/SkillSelector/Worker/Steward/User 等 Agent；
- 注册各类工具能力并注入系统提示词；
- 提供路由层可消费的 Agent 能力描述。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import asyncio
import base64
from functools import lru_cache
import logging
import os
import json
import random
import re
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.session import JSONSession
from agentscope.tool import Toolkit, ToolResponse
from agentscope.plan import PlanNotebook, Plan, SubTask


from .config import CUSTOM_AGENT_CONFIG, MODEL_CONFIG, MEMORY_CONFIG, RAG_CONFIG, ROUTING_CONFIG
from .tools import (
    arxiv_search,
    brave_search,
    call_mobi_action,
    call_mobi_collect_verified,
    dblp_conference_search,
    download_file,
    edit_docx,
    extract_image_text_ocr,
    extract_pdf_text,
    fetch_url_links,
    fetch_url_readable_text,
    fetch_url_text,
    create_docx_from_text,
    create_pdf_from_text,
    read_docx_text,
    read_xlsx_summary,
    read_memory,
    run_skill_script,
    run_shell_command,
    search_task_history,
    search_steward_knowledge,
    store_steward_knowledge,
    fetch_feishu_chat_history,
    get_feishu_message,
    update_long_term_memory,
    write_xlsx_from_records,
    write_xlsx_from_rows,
    write_text_file,
    create_pptx_from_outline,
    edit_pptx,
    insert_pptx_image,
    read_pptx_summary,
    set_pptx_text_style,
)

logger = logging.getLogger(__name__)


def _trim_for_log(text: str, max_chars: int = 260) -> str:
    """裁剪日志显示长度，避免刷屏；不影响真实注入内容。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


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

    return {
        "task_description": str(execution.get("task_description", "") or metadata.get("final_task", "") or ""),
        "status_hint": str(summary.get("status_hint", "") or metadata.get("status_hint", "") or ""),
        "step_count": int(summary.get("step_count", metadata.get("step_count", 0)) or 0),
        "action_count": int(summary.get("action_count", metadata.get("action_count", 0)) or 0),
        "images_selected": selected,
        "image_data_urls": image_data_urls,
        "reasonings_text": reasonings_text,
        "reasonings_count": len(reasonings),
    }


async def _judge_completion_with_vlm(
    *,
    model: OpenAIChatModel,
    task_desc: str,
    success_criteria: str,
    status_hint: str,
    step_count: int,
    action_count: int,
    reasonings_text: str,
    image_data_urls: list[str],
    timeout_s: float,
) -> dict[str, Any]:
    prompt = (
        f"你是手机自动化任务验证器。请根据任务描述、执行摘要、完整 reasonings 历史以及最终{len(image_data_urls)}张截图，"
        f"task_desc: {task_desc}\n"
        f"success_criteria: {success_criteria}\n"
        f"status_hint: {status_hint}\n"
        f"step_count: {step_count}, action_count: {action_count}\n"
        "history_reasonings:\n"
        f"{reasonings_text or '[empty]'}\n"
        "请根据截图和操作历史，判断任务是否已经完成。\n"
        "请只输出 JSON，不要输出其他文本。\n"
        "JSON schema:\n"
        '{"completed": bool, "confidence": float, "reason": str, "evidence": list[str], "missing_requirements": list[str]}\n'
    )

    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_url in image_data_urls:
        user_content.append({"type": "image_url", "image_url": {"url": image_url}})

    messages = [{"role": "user", "content": user_content}]
    try:
        response = await asyncio.wait_for(model(messages), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return {
            "completed": False,
            "confidence": 0.0,
            "reason": "",
            "evidence": [],
            "missing_requirements": [],
            "error": str(exc),
            "fallback_used": True,
        }

    raw_text = _extract_text_from_model_response(response)
    parsed = _parse_vlm_json(raw_text)
    if not parsed:
        return {
            "completed": False,
            "confidence": 0.0,
            "reason": "",
            "evidence": [],
            "missing_requirements": [],
            "error": "vlm_non_json_output",
            "raw_text": raw_text[:800],
            "fallback_used": True,
        }
    logger.debug("vlm.parse_json_output")
    logger.debug(parsed)
    evidence = parsed.get("evidence", [])
    missing = parsed.get("missing_requirements", [])
    return {
        "completed": bool(parsed.get("completed", False)),
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "reason": str(parsed.get("reason", "") or ""),
        "evidence": evidence if isinstance(evidence, list) else [],
        "missing_requirements": missing if isinstance(missing, list) else [],
    }

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


@dataclass
class AgentCapability:
    """描述单个 Agent 能力边界的结构化模型。"""

    name: str
    role: str
    strengths: list[str]
    typical_tasks: list[str]
    boundaries: list[str]


@dataclass
class CustomAgentDefinition:
    """配置文件驱动的自定义 Agent 定义。"""

    name: str
    display_name: str
    role: str
    system_prompt: str
    tools: list[str]
    strengths: list[str]
    typical_tasks: list[str]
    boundaries: list[str]
    model_name: str | None
    temperature: float | None
    max_iters: int


def _tool_catalog() -> dict[str, tuple[Any, str]]:
    """返回可供自定义 Agent 复用的工具目录。"""
    return {
        "run_shell_command": (run_shell_command, "运行受限的本地命令行工具（白名单约束）。"),
        "run_skill_script": (run_skill_script, "在指定 execution_dir 中执行 skill 脚本。"),
        "brave_search": (brave_search, "通过 Brave Search API 联网检索新闻与网页来源链接。"),
        "arxiv_search": (arxiv_search, "查询 arXiv API 获取论文元数据、摘要与 PDF 链接。"),
        "dblp_conference_search": (dblp_conference_search, "检索会议论文清单与链接（DBLP）。"),
        "fetch_url_text": (fetch_url_text, "抓取指定 URL 的文本内容用于快速检索。"),
        "fetch_url_readable_text": (fetch_url_readable_text, "抓取并提取网页可读文本。"),
        "fetch_url_links": (fetch_url_links, "抓取网页并提取链接。"),
        "download_file": (download_file, "下载 URL 文件到本地路径（支持二进制，例如 PDF）。"),
        "extract_pdf_text": (extract_pdf_text, "从本地 PDF 文件中提取文本内容。"),
        "extract_image_text_ocr": (extract_image_text_ocr, "从本地图片文件中执行 OCR 识别。"),
        "read_docx_text": (read_docx_text, "读取 DOCX 文档文本内容。"),
        "create_docx_from_text": (create_docx_from_text, "从纯文本生成 DOCX 文档。"),
        "edit_docx": (edit_docx, "对 DOCX 文档进行查找替换、追加段落或插入表格。"),
        "create_pdf_from_text": (create_pdf_from_text, "从纯文本生成 PDF 文档。"),
        "read_xlsx_summary": (read_xlsx_summary, "读取 XLSX 工作簿摘要与预览。"),
        "write_xlsx_from_records": (write_xlsx_from_records, "从记录列表生成 XLSX 文件。"),
        "write_xlsx_from_rows": (write_xlsx_from_rows, "从行数据生成 XLSX 文件。"),
        "write_text_file": (write_text_file, "写入本地文本文件。"),
        "search_task_history": (search_task_history, "检索历史任务执行记录和相关文档。"),
        "search_steward_knowledge": (search_steward_knowledge, "检索本地知识库中已存储的信息。"),
        "store_steward_knowledge": (store_steward_knowledge, "将收集到的信息存入本地知识库。"),
        "fetch_feishu_chat_history": (fetch_feishu_chat_history, "读取飞书会话历史消息列表。"),
        "get_feishu_message": (get_feishu_message, "按消息 ID 获取飞书消息详情。"),
        "update_long_term_memory": (update_long_term_memory, "更新长期记忆文件（MEMORY.md）。"),
        "read_pptx_summary": (read_pptx_summary, "读取 PPTX/PPT 文件并返回结构化摘要。"),
        "create_pptx_from_outline": (create_pptx_from_outline, "从幻灯片大纲列表创建新 PPTX 文件。"),
        "edit_pptx": (edit_pptx, "综合编辑已有 PPTX。"),
        "insert_pptx_image": (insert_pptx_image, "向指定幻灯片插入本地图片。"),
        "set_pptx_text_style": (set_pptx_text_style, "对匹配文本应用 PPTX 字体样式。"),
    }


def _normalize_agent_name(raw: str) -> str:
    value = re.sub(r"[^0-9a-zA-Z_\-]+", "_", str(raw or "").strip().lower())
    return value.strip("_")


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _builtin_agent_capabilities() -> list[AgentCapability]:
    return [
        AgentCapability(
            name="steward",
            role="负责手机端数据收集-存储-分析这一类特殊任务（Collect/Store/Analyze/Execute）",
            strengths=[
                "手机端数据采集与执行动作",
            ],
            typical_tasks=[
                "整理今日待办并决定是否执行手机操作",
                "采集微信信息后入库并生成建议",
            ],
            boundaries=[
                "不擅长大规模网页/论文检索,不擅长直接生成总结内容",
                "通用检索类子任务建议委派给 worker",
            ],
        ),
        AgentCapability(
            name="worker",
            role="负责通用检索、网页阅读、学术资料收集、生成和阅读各类文档、历史任务检索、知识库检索、长期记忆管理，本地工具执行，飞书相关的聊天历史检索（使用飞书连接时）",
            strengths=[
                "Brave/网页/arXiv/DBLP 检索",
                "下载文件与 PDF 文本提取",
                "Word/Excel/PDF 文档读写与编辑",
                "Shell 与本地文件写入",
                "历史任务记录检索与知识库检索",
                "长期记忆读写（记录用户偏好、事实信息等跨会话信息）",
            ],
            typical_tasks=[
                "检索最新论文并总结",
                "整理或生成 Word/Excel/PDF 文档",
                "抓取网页并提炼可执行结论",
                "查询之前做过的任务或历史记录",
                "检索智能管家存储的知识（如手机采集的 OCR 文字、对话记录等）",
                "记住用户偏好或更新长期记忆",
            ],
            boundaries=[
                "不直接执行手机 GUI 操作",
            ],
        ),
    ]


@lru_cache(maxsize=1)
def _load_custom_agent_definitions() -> tuple[CustomAgentDefinition, ...]:
    raw_items = CUSTOM_AGENT_CONFIG.get("agents", []) if isinstance(CUSTOM_AGENT_CONFIG, dict) else []
    if not isinstance(raw_items, list) or not raw_items:
        return ()

    catalog = _tool_catalog()
    reserved = {"worker", "steward", "router", "planner", "skillselector", "user", "mobichatbot"}
    seen: set[str] = set()
    defs: list[CustomAgentDefinition] = []

    for idx, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            logger.warning("custom agent ignored: index=%d reason=not_object", idx)
            continue

        display_name = str(raw.get("agent_name") or "").strip()
        role = str(raw.get("role") or "").strip()
        system_prompt = str(raw.get("system_prompt") or "").strip()
        if not display_name or not role or not system_prompt:
            logger.warning("custom agent ignored: index=%d reason=missing_required_fields", idx)
            continue

        name = _normalize_agent_name(display_name)
        if not name:
            logger.warning("custom agent ignored: index=%d reason=invalid_name", idx)
            continue
        if name in reserved:
            logger.warning("custom agent ignored: name=%s reason=reserved_name", name)
            continue
        if name in seen:
            logger.warning("custom agent ignored: name=%s reason=duplicate", name)
            continue

        raw_tools = _as_str_list(raw.get("tools"))
        tools = list(dict.fromkeys([_normalize_agent_name(tool) for tool in raw_tools if _normalize_agent_name(tool)]))
        unknown_tools = [tool for tool in tools if tool not in catalog]
        if unknown_tools:
            logger.warning(
                "custom agent ignored: name=%s reason=unknown_tools unknown=%s",
                name,
                unknown_tools,
            )
            continue

        temperature: float | None = None
        if raw.get("temperature") is not None:
            try:
                temperature = float(raw.get("temperature"))
            except (TypeError, ValueError):
                logger.warning("custom agent ignored: name=%s reason=invalid_temperature", name)
                continue

        max_iters = 12
        if raw.get("max_iters") is not None:
            try:
                max_iters = max(1, min(50, int(raw.get("max_iters"))))
            except (TypeError, ValueError):
                logger.warning("custom agent ignored: name=%s reason=invalid_max_iters", name)
                continue

        model_name = str(raw.get("model_name") or "").strip() or None
        defs.append(
            CustomAgentDefinition(
                name=name,
                display_name=display_name,
                role=role,
                system_prompt=system_prompt,
                tools=tools,
                strengths=_as_str_list(raw.get("strengths")),
                typical_tasks=_as_str_list(raw.get("typical_tasks")),
                boundaries=_as_str_list(raw.get("boundaries")),
                model_name=model_name,
                temperature=temperature,
                max_iters=max_iters,
            )
        )
        seen.add(name)

    return tuple(defs)


def get_agent_capability_descriptions() -> dict[str, dict[str, object]]:
    """返回路由可用的 Agent 能力描述字典。

    返回值说明：
        dict[str, dict[str, object]]: 以 agent 名称为键、能力画像为值的映射。
    """
    registry = _builtin_agent_capabilities()
    for item in _load_custom_agent_definitions():
        registry.append(
            AgentCapability(
                name=item.name,
                role=item.role,
                strengths=item.strengths,
                typical_tasks=item.typical_tasks,
                boundaries=item.boundaries,
            )
        )
    return {item.name: asdict(item) for item in registry}


def create_configured_agent_by_name(agent_name: str, *, skill_context: str | None = None) -> ReActAgent | None:
    """根据 custom_agent.json 定义按名称构建自定义 Agent。"""
    normalized = _normalize_agent_name(agent_name)
    target = None
    for item in _load_custom_agent_definitions():
        if item.name == normalized:
            target = item
            break
    if target is None:
        return None

    toolkit = Toolkit()
    catalog = _tool_catalog()
    for tool_name in target.tools:
        func, desc = catalog[tool_name]
        if tool_name == "search_task_history" and not RAG_CONFIG["task_history_enabled"]:
            continue
        if tool_name == "update_long_term_memory" and not MEMORY_CONFIG["enabled"]:
            continue
        toolkit.register_tool_function(func, func_description=desc)

    sys_prompt = target.system_prompt
    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name=target.display_name,
        sys_prompt=sys_prompt,
        model=create_openai_model(
            temperature=target.temperature,
            model_name=target.model_name,
        ),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=target.max_iters,
    )


def create_router_agent() -> ReActAgent:
    """创建 Router Agent，用于任务路由决策。"""
    sys_prompt = """你是多智能体任务路由器。你的目标是根据任务文本选择最合适的 Agent。

输出要求：
- 只输出 JSON，不要包含额外解释。
- JSON 字段：target_agents(list)、reason(str)、confidence(float 0-1)、plan_required(bool)。
- 如果任务涉及多种能力，可返回多个 agent。
- 不确定时优先选择 worker。
"""
    return ReActAgent(
        name="Router",
        sys_prompt=sys_prompt,
        model=create_openai_model(
            stream=True,
            temperature=0.1,
            model_name=MODEL_CONFIG.get("orchestrator_model_name"),
        ),
        formatter=OpenAIChatFormatter(),
        # toolkit=Toolkit(),
        # memory=InMemoryMemory(),
        max_iters=1,
    )


def create_planner_agent() -> ReActAgent:
    """创建 Planner Agent，用于复合任务拆分。"""
    sys_prompt = """你是多智能体任务规划器。请把复杂任务拆成阶段化子任务。

输出要求：
- 只输出 JSON，不要包含额外解释。
- 格式：{"stages":[[{"agent":"steward|worker","task":"..."}]]}
- 外层 stages 表示串行阶段，内层列表表示可并行任务。
- 子任务必须简洁可执行，避免重复。
"""
    return ReActAgent(
        name="Planner",
        sys_prompt=sys_prompt,
        model=create_openai_model(
            stream=True,
            temperature=0.1,
            model_name=MODEL_CONFIG.get("orchestrator_model_name"),
        ),
        formatter=OpenAIChatFormatter(),
        # toolkit=Toolkit(),
        # memory=InMemoryMemory(),
        max_iters=1,
    )


def create_skill_selector_agent() -> ReActAgent:
    """创建 Skill Selector Agent，用于技能候选重排。"""
    sys_prompt = """你是 Skill Selector。你的目标是在给定候选技能集合中选择最合适的技能。

输出要求：
- 只输出 JSON，不要包含额外解释。
- JSON 字段：skills(list)、reason(str)。
- skills 中的每个值必须来自输入给出的候选集合。
- 若没有合适技能，可以返回空数组。
"""
    return ReActAgent(
        name="SkillSelector",
        sys_prompt=sys_prompt,
        model=create_openai_model(
            stream=True,
            temperature=0.1,
            model_name=MODEL_CONFIG.get("orchestrator_model_name"),
        ),
        formatter=OpenAIChatFormatter(),
        max_iters=1,
    )


def create_worker_agent(skill_context: str | None = None) -> ReActAgent:
    """创建 Worker Agent，用于子任务委派。"""
    toolkit = Toolkit()

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
    )

    toolkit.register_tool_function(
        run_skill_script,
        func_description=(
            "在指定 execution_dir 中执行skill中定义的命令。"
            "调用时请传入完整可执行命令字符串和执行目录。"
        ),
    )

    toolkit.register_tool_function(
        brave_search,
        func_description="通过 Brave Search API 联网检索新闻与网页来源链接。",
    )

    toolkit.register_tool_function(
        arxiv_search,
        func_description="查询 arXiv API 获取论文元数据、摘要与 PDF 链接。",
    )

    toolkit.register_tool_function(
        dblp_conference_search,
        func_description="检索会议论文清单与链接（DBLP），用于按年份与关键词筛选。",
    )

    toolkit.register_tool_function(
        fetch_url_text,
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )

    toolkit.register_tool_function(
        fetch_url_readable_text,
        func_description="抓取并提取网页可读文本，用于快速理解页面内容。",
    )

    toolkit.register_tool_function(
        fetch_url_links,
        func_description="抓取网页并提取链接，用于发现相关来源并继续检索。",
    )

    toolkit.register_tool_function(
        download_file,
        func_description="下载 URL 文件到本地路径（支持二进制，例如 PDF）。",
    )

    toolkit.register_tool_function(
        extract_pdf_text,
        func_description="从本地 PDF 文件中提取文本内容。",
    )

    toolkit.register_tool_function(
        extract_image_text_ocr,
        func_description="从本地图片文件中执行 OCR 识别，提取文字内容。",
    )

    toolkit.register_tool_function(
        read_docx_text,
        func_description="读取 DOCX 文档文本内容。",
    )

    toolkit.register_tool_function(
        create_docx_from_text,
        func_description="从纯文本生成 DOCX 文档。",
    )

    toolkit.register_tool_function(
        edit_docx,
        func_description="对 DOCX 文档进行查找替换、追加段落或插入表格。",
    )

    toolkit.register_tool_function(
        create_pdf_from_text,
        func_description="从纯文本生成 PDF 文档。",
    )

    toolkit.register_tool_function(
        read_xlsx_summary,
        func_description="读取 XLSX 工作簿摘要与预览。",
    )

    toolkit.register_tool_function(
        write_xlsx_from_records,
        func_description="从记录列表生成 XLSX 文件。",
    )

    toolkit.register_tool_function(
        write_xlsx_from_rows,
        func_description="从行数据生成 XLSX 文件。",
    )

    toolkit.register_tool_function(
        write_text_file,
        func_description="写入本地文本文件，用于保存结果或日志。",
    )
    if RAG_CONFIG["task_history_enabled"]:
        toolkit.register_tool_function(
            search_task_history,
            func_description="检索历史任务执行记录和相关文档，用于回答关于之前做过的任务的问题。",
        )
    toolkit.register_tool_function(
        search_steward_knowledge,
        func_description="检索本地知识库中已存储的信息（由智能管家从手机中提取并存储）。",
    )

    toolkit.register_tool_function(
        fetch_feishu_chat_history,
        func_description=(
            "读取飞书会话历史消息列表。"
            "chat_id 必须传真实会话/用户 ID（如 oc_... 或 ou_...），不能传 auto；"
            "container_id_type 可选 auto/chat/user，默认 auto；"
            "history_range 可选 today/yesterday/7d/all，默认 today；"
            "当 history_range=today 且消息少于 10 条时，会自动向更早消息补齐到最多 10 条；"
            "page_token 可用于非 today 查询的续页。"
        ),
    )

    toolkit.register_tool_function(
        get_feishu_message,
        func_description="按消息 ID 获取飞书消息详情，用于排查和精确分析。",
    )

    toolkit.register_tool_function(
        read_pptx_summary,
        func_description="读取 PPTX/PPT 文件，返回每张幻灯片的标题、正文文本、备注、形状数量和图片数量的结构化摘要。",
    )

    toolkit.register_tool_function(
        create_pptx_from_outline,
        func_description=(
            "从幻灯片大纲列表创建新 PPTX 文件。"
            "每张幻灯片支持：标题、正文（字符串或列表）、演讲者备注、布局索引、"
            "嵌入图片（路径+位置+尺寸）、字号、字体颜色（#RRGGBB）、粗体、斜体。"
            "支持可选模板文件与全局默认字体大小/颜色。"
        ),
    )

    toolkit.register_tool_function(
        edit_pptx,
        func_description=(
            "综合编辑已有 PPTX：跨所有幻灯片全局文本替换、追加新幻灯片、"
            "按 1-based 索引删除幻灯片。三种操作可在一次调用中组合使用。"
        ),
    )

    toolkit.register_tool_function(
        insert_pptx_image,
        func_description=(
            "向指定幻灯片（1-based 索引）插入本地图片。"
            "支持英寸单位的定位（left/top）和尺寸（width/height），省略宽高时保持原始比例。"
        ),
    )

    toolkit.register_tool_function(
        set_pptx_text_style,
        func_description=(
            "在指定幻灯片中搜索文本子串，对所有匹配的 run 应用字体样式："
            "字号（pt）、颜色（#RRGGBB）、粗体、斜体、下划线。省略的属性保持原样。"
        ),
    )

    sys_prompt = """你是 Seneschal 的 Worker Agent，负责处理通用问题与单一子任务。

工作准则：
- 只聚焦当前任务（如果当前是一个子任务，只聚焦于子任务），给出简明直接的结果。
- 必要时使用工具检索或执行本地命令。
- 如果提供了相应的skill，请优先使用skill中指定的工具和方法，运行skill中的脚本，请务必使用 "run_skill_script"，而非"run_shell_command"。
- 使用 "run_skill_script" 时，必须提供 command 和 execution_dir；优先使用 Activated Skills 中给出的 execution_dir。
- 如果需要联网搜索新闻或网页来源，优先使用 "brave_search" 获取新闻/web内容。
- 如果检索学术论文，优先使用 "arxiv_search" 获取元数据与 PDF 链接。
- 如果检索会议论文，优先使用 "dblp_conference_search" 获取论文清单与链接，然后去arxiv上搜索对应的论文。
- 如果已经下载过论文/文件了，优先通过临时文件目录，查找之前下载的文件，避免重复上网搜索和下载。
- 如果已经搜索过论文/文件了，优先通过之前的历史对话获取信息，避免重复联网检索。
- 如果任务中有今天，明天等相对日期的描述，你可以通过shell中的date命令，获取具体的日期。
- 拿到候选链接后，优先使用 "fetch_url_readable_text" 抓取正文；需要原始 HTML 时再使用 "fetch_url_text"。
- 需要从网页中发现相关链接时使用 "fetch_url_links"，再逐条抓取与筛选。
- 需要下载论文或附件时使用 "download_file"；阅读 PDF 用 "extract_pdf_text"。
- 需要识别图片中的文字时使用 "extract_image_text_ocr"。
- 处理 Word/Excel/PDF 文档时，使用 docx/xlsx/pdf 相关工具完成读取或生成。
- 需要输出文件时，可用 "write_text_file" 落盘。
- 如果用户询问之前智能管家从手机中提取并存储的知识，使用 "search_steward_knowledge" 检索。
- 如果用户要求总结飞书群聊历史或按消息 ID 查询，请使用飞书历史消息工具。
- 如果消息中包含 [Feishu Context]，调用飞书历史工具时必须优先使用其中的 chat_id/open_id/message_id，不得猜测或改写。
- 若 [Feishu Context] 缺少必需 ID，应先明确指出缺失项并向用户索取，不要编造参数。
- 输出格式遵循用户要求；未指定时默认使用 Markdown。
- 必须输出最终文本结论或可执行结果；不要输出空的工具调用。
- 即使已经把结果写入文件，也必须在当前回复中给出完整结论（至少包含关键结论与主要依据）；禁止只回复“已落盘+文件路径”。
- 不做多步长对话，输出最终结论或可执行结果。
"""
    if RAG_CONFIG["task_history_enabled"]:
        sys_prompt += "- 如果用户询问之前做过的任务，使用 \"search_task_history\" 检索历史记录。\n"

    if MEMORY_CONFIG["enabled"]:
        toolkit.register_tool_function(
            update_long_term_memory,
            func_description=(
                "更新长期记忆文件（MEMORY.md）。传入完整新内容，覆盖写入。"
                "用于记录用户偏好、事实信息、回答风格等需要跨会话保留的凝练信息。"
            ),
        )
        sys_prompt += (
            "- 你拥有 \"update_long_term_memory\" 工具，可更新长期记忆。"
            "当用户明确要求记住某些偏好或信息时，先读取现有记忆内容，合并后写回。\n"
        )

    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Worker",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=20,
    )


def create_steward_agent(skill_context: str | None = None) -> ReActAgent:
    """创建智能管家 Agent (StewardAgent)。"""
    toolkit = Toolkit()
    retry_cap = max(0, min(int(os.environ.get("STEWARD_MOBI_MAX_RETRIES", "2")), 5))

    toolkit.register_tool_function(
        call_mobi_collect_verified,
        func_description=(
            "优先使用：调用 MobiAgent 收集手机任务结果（单次执行）。"
            "该工具不保证任务正确完成，也不会自动重试。"
            "返回统一结构化证据：截图路径、OCR文本、动作历史和推理历史，供 Agent 自主判断。"
        ),
    )

    toolkit.register_tool_function(
        call_mobi_action,
        func_description=(
            "指挥 MobiAgent 在手机端执行 GUI 操作。"
            "支持的操作例如: 'add_calendar_event'(添加日历事件), "
            "'send_message'(发送消息), 'set_reminder'(设置提醒), go_shop(下单购物)等。"
            "payload 参数为 JSON 格式字符串，如: "
            "'{\"title\": \"Meeting\", \"time\": \"15:00\", \"date\": \"2024-01-20\"}'。"
            "通常是数据整理流程的最后一步，根据分析结果执行具体操作。"
        ),
    )

    toolkit.register_tool_function(
        store_steward_knowledge,
        func_description=(
            "将收集到的信息存入本地知识库。"
            "用于持久化保存 OCR 识别的文字、对话记录、账单信息等。"
            "输入要存储的文本内容，系统会将其加入知识库供后续检索分析。"
            "通常应在收集数据后调用。"
        ),
    )

    toolkit.register_tool_function(
        search_steward_knowledge,
        func_description=(
            "检索本地知识库中已存储的信息。"
            "用于查找之前通过 store_steward_knowledge 存入的数据（OCR 文字、对话记录等）。"
            "检索后请根据返回的原始片段自行分析总结。"
        ),
    )

    toolkit.register_tool_function(
        fetch_url_text,
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
    )
    toolkit.register_tool_function(
        extract_image_text_ocr,
        func_description="从图片中提取文字。",
    )
    
    async def call_mobi_collect_with_retry_report(task_desc: str, success_criteria: str = "") -> ToolResponse:
        """执行带重试上限的 mobi 采集，并返回结构化证据包。"""
        attempts: list[dict[str, object]] = []
        current_task = task_desc
        criteria_matched = False
        no_criteria_mode = not bool((success_criteria or "").strip())
        vlm_enabled = _env_bool("STEWARD_MOBI_VLM_ENABLED", True)
        vlm_last_n = max(1, int(os.environ.get("STEWARD_MOBI_VLM_LAST_N", "5")))
        vlm_timeout_s = max(5.0, float(os.environ.get("STEWARD_MOBI_VLM_TIMEOUT_S", "25")))
        vlm_max_reasonings_chars = max(1000, int(os.environ.get("STEWARD_MOBI_VLM_MAX_REASONINGS_CHARS", "12000")))
        vlm_model = create_openai_model(stream=False, temperature=0.0) if vlm_enabled else None

        for idx in range(1, retry_cap + 2):
            resp = await call_mobi_collect_verified(current_task, max_retries=0)
            md = (resp.metadata or {}) if resp else {}
            ocr_text = str(md.get("ocr_text", "") or "")
            last_reasoning = str(md.get("last_reasoning", "") or "")
            extracted_info = md.get("extracted_info", {}) if isinstance(md.get("extracted_info"), dict) else {}
            tool_success = bool(md.get("success", False))
            has_evidence = bool(
                ocr_text.strip()
                or last_reasoning.strip()
                or extracted_info
                or md.get("raw_data")
            )
            criteria_matched_text = False
            criteria_matched_vlm = False
            vlm_verdict: dict[str, Any] = {
                "completed": False,
                "confidence": 0.0,
                "reason": "",
                "evidence": [],
                "missing_requirements": [],
            }
            vlm_images_used: list[str] = []
            reasonings_count = 0

            if success_criteria:
                haystack = (
                    ocr_text
                    + "\n"
                    + last_reasoning
                    + "\n"
                    + json.dumps(extracted_info, ensure_ascii=False)
                )
                criteria_matched_text = tool_success and (success_criteria in haystack)
                if (not criteria_matched_text) and tool_success and has_evidence:
                    generic_tokens = ("成功获取", "获取到", "收集到", "活动信息", "最近活动")
                    if any(token in success_criteria for token in generic_tokens):
                        criteria_matched_text = True
            else:
                # 无显式标准时，只要拿到可用证据即可继续由 Agent 判定。
                criteria_matched_text = tool_success and has_evidence

            if vlm_enabled and vlm_model is not None and tool_success and has_evidence:
                vlm_evidence = _extract_vlm_evidence(
                    md,
                    last_n_images=vlm_last_n,
                    max_reasonings_chars=vlm_max_reasonings_chars,
                )
                reasonings_count = int(vlm_evidence.get("reasonings_count", 0) or 0)
                vlm_images_used = [
                    str(p) for p in vlm_evidence.get("images_selected", []) if isinstance(p, str)
                ]
                vlm_verdict = await _judge_completion_with_vlm(
                    model=vlm_model,
                    task_desc=str(vlm_evidence.get("task_description", "") or current_task),
                    success_criteria=success_criteria,
                    status_hint=str(vlm_evidence.get("status_hint", "")),
                    step_count=int(vlm_evidence.get("step_count", 0) or 0),
                    action_count=int(vlm_evidence.get("action_count", 0) or 0),
                    reasonings_text=str(vlm_evidence.get("reasonings_text", "")),
                    image_data_urls=[
                        str(u) for u in vlm_evidence.get("image_data_urls", []) if isinstance(u, str)
                    ],
                    timeout_s=vlm_timeout_s,
                )
                criteria_matched_vlm = bool(vlm_verdict.get("completed", False))

            criteria_matched = criteria_matched_text or criteria_matched_vlm

            attempt_item = {
                "attempt": idx,
                "task_desc": current_task,
                "run_dir": md.get("run_dir", ""),
                "index_file": md.get("index_file", ""),
                "status_hint": md.get("status_hint", ""),
                "step_count": md.get("step_count", 0),
                "action_count": md.get("action_count", 0),
                "screenshot_path": md.get("screenshot_path", ""),
                "last_reasoning": last_reasoning,
                "ocr_preview": ocr_text[:300],
                "extracted_info": extracted_info,
                "tool_success": tool_success,
                "criteria_matched_text": criteria_matched_text,
                "criteria_matched_vlm": criteria_matched_vlm,
                "vlm_completed": bool(vlm_verdict.get("completed", False)),
                "vlm_confidence": float(vlm_verdict.get("confidence", 0.0) or 0.0),
                "vlm_reason": str(vlm_verdict.get("reason", "") or ""),
                "vlm_error": str(vlm_verdict.get("error", "") or ""),
                "vlm_images_used": vlm_images_used,
                "reasonings_count": reasonings_count,
                "vlm_missing_requirements": vlm_verdict.get("missing_requirements", []),
            }
            attempts.append(attempt_item)

            if criteria_matched:
                break

            if idx <= retry_cap:
                failure_reason = "criteria_not_matched" if success_criteria else "no_evidence_collected"
                current_task = (
                    f"{task_desc}\n"
                    f"重试要求(第{idx}次失败，原因:{failure_reason})："
                    "请严格按目标完成后立即停止；避免重复无效操作；保留可验证证据。"
                )

        final_attempt = attempts[-1] if attempts else {}
        pack: dict[str, object] = {
            "report_type": "mobi_retry_evidence_pack_v1",
            "original_task": task_desc,
            "success_criteria": success_criteria,
            "no_criteria_mode": no_criteria_mode,
            "retry_limit": retry_cap,
            "attempt_count": len(attempts),
            "criteria_matched": criteria_matched,
            "needs_agent_judgement": True,
            "validation_mode": "text_or_vlm",
            "vlm_enabled": vlm_enabled,
            "vlm_last_n": vlm_last_n,
            "attempts": attempts,
        }

        if not criteria_matched:
            pack["failure_report"] = {
                "status": "failed_after_retry_limit",
                "latest_run_dir": final_attempt.get("run_dir", ""),
                "latest_index_file": final_attempt.get("index_file", ""),
                "latest_screenshot_path": final_attempt.get("screenshot_path", ""),
                "latest_reasoning": final_attempt.get("last_reasoning", ""),
                "latest_ocr_preview": final_attempt.get("ocr_preview", ""),
                "latest_vlm_reason": final_attempt.get("vlm_reason", ""),
                "vlm_missing_requirements": final_attempt.get("vlm_missing_requirements", []),
                "next_action_recommendation": "agent_decide_retry_or_handoff",
            }

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 重试证据包]\n"
                        f"任务: {task_desc}\n"
                        f"重试上限: {retry_cap}\n"
                        f"尝试次数: {len(attempts)}\n"
                        f"criteria_matched: {criteria_matched}\n"
                        f"最后状态提示: {final_attempt.get('status_hint', '')}\n"
                        f"OCR摘要: {str(final_attempt.get('ocr_preview', '') or '')[:500]}\n"
                        "注意：该结果仅为证据汇总，最终完成判定必须由 Agent 自主做出。"
                    ),
                ),
            ],
            metadata=pack,
        )

    toolkit.register_tool_function(
        call_mobi_collect_with_retry_report,
        func_description=(
            "执行手机任务并应用显式重试上限（默认最多2次重试，总共3次尝试），"
            "返回结构化证据包与失败报告模板。"
            "该工具不做最终完成保证，最终判定由 Agent 根据证据自主决定。"
        ),
    )

    async def delegate_to_worker(task: str, delegation_depth: int = 0) -> ToolResponse:
        """将子任务委派给 Worker Agent 并返回结果。"""
        max_depth = int(ROUTING_CONFIG.get("max_routing_depth", 2))
        if delegation_depth >= max_depth:
            return ToolResponse(
                content=[TextBlock(type="text", text="[Worker 结果]\n已达到委派深度上限，停止继续委派。")],
                metadata={"task": task, "delegation_depth": delegation_depth, "stopped": True},
            )
        worker = create_worker_agent()
        msg = Msg(name="User", content=task, role="user")
        response = await worker(msg)
        text = response.get_text_content() if response else ""
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Worker 结果]\n{text}")],
            metadata={"task": task, "delegation_depth": delegation_depth + 1},
        )

    toolkit.register_tool_function(
        delegate_to_worker,
        func_description="将子任务委派给 Worker Agent 并汇总返回结果。",
    )

    sys_prompt = """你是 Seneschal 智能管家系统的核心 Agent，负责帮助用户管理个人数据和日常事务。

## 你的职责
1. 理解用户的需求和指令
2. 规划并执行数据收集、存储、分析和操作的完整流程
3. 通过调用工具与手机操作Agent如MobiAgent（手机端）协作
4. 必要时委派子任务给 Worker Agent（例如快速检索或命令行检查）

## 工作流程规范
当用户要求进行数据整理或分析时，请严格按照以下步骤执行：

### 收集与验证 (Collect + Verify)
- 优先使用 `call_mobi_collect_with_retry_report` 执行手机任务并获取证据包
- 显式重试上限：最多 {retry_cap} 次重试（总尝试次数 {retry_cap + 1}）
- 必须基于返回证据（截图路径、OCR文本、动作/推理历史）自行判断任务是否完成
- 不要把工具返回中的状态提示当作最终真值；它只能作为参考
- 若任务未完成，你必须在上限内改写任务并重试；超限后停止继续操作
- 每次重试要在回复中说明失败依据与改写思路
- 例如：获取微信聊天截图、日历事件、通知消息等

### 失败报告模板 (Failure Pack)
- 达到重试上限仍未完成时，必须输出结构化失败证据包，字段至少包括：
- `report_type`, `original_task`, `retry_limit`, `attempt_count`, `attempts`, `failure_report`
- `failure_report` 内至少包含：
- `status`, `latest_run_dir`, `latest_index_file`, `latest_screenshot_path`, `latest_reasoning`, `latest_ocr_preview`, `next_action_recommendation`

### 存储 (Store)
- 使用 `store_steward_knowledge` 工具将收集到的信息存入知识库
- 确保所有有价值的信息都被持久化保存

### 分析 (Analyze)
- 使用 `search_steward_knowledge` 检索知识库中已存储的数据
- 根据检索到的原始片段，自行分析总结待办事项、账单、重要提醒等

### 检索 (Retrieve)
- 查找之前存储的数据（OCR、对话记录等），使用 `search_steward_knowledge`
- 对外部页面查询可用 `fetch_url_text` 获取原始文本

### 委派 (Delegate)
- 可将通用检索、浏览器查询或本地命令任务交给 `delegate_to_worker`
- 可将小任务交给 `delegate_to_worker`，减少主流程干扰
- 涉及联网新闻/网页检索时，优先委派 Worker 使用 `brave_search` 再抓取正文

### 执行 (Execute)
- 如果分析发现需要执行的操作（如添加日程、设置提醒）
- 使用 `call_mobi_action` 工具在手机端执行相应操作

## 注意事项
- 每一步都要向用户汇报进展
- 如果某一步失败，要尝试其他方法或向用户说明
- 在执行敏感操作前，需要用户确认（除非用户明确授权自动执行）
- 保持回复简洁专业，优先使用中文交流
- 即使过程产出了落盘文件，也必须在当前回复正文给出明确结论与关键内容；不能只回复文件路径。
- 如果任务主要是通用网页/论文检索，优先委派给 Worker，避免重复调用端侧工具
- 若路由层已明确指定本 Agent，仅处理职责范围内任务，不要无限自委派

## 示例对话
用户：开始今日的数据整理和分析
你应该：
1. 思考并调用 call_mobi_collect_with_retry_report 获取证据（含显式重试上限）
2. 基于证据自主判断是否完成；若未完成且已达上限，输出结构化失败证据包
3. 调用 store_steward_knowledge 存储收集到的信息
4. 调用 search_steward_knowledge 检索已存数据，自行分析待办和账单
5. 如发现待办事项，询问是否需要添加到日历，然后调用 call_mobi_action

现在，请准备好为用户服务！"""
    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Steward",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=10,
        plan_notebook=PlanNotebook(),
    )


def create_user_agent() -> UserAgent:
    """创建用户代理 Agent。"""
    return UserAgent(name="User")


def create_chat_agent(*, web_search_enabled: bool = True) -> ReActAgent:
    """创建网关 chat 模式使用的基础对话 Agent。"""
    toolkit = Toolkit()

    if web_search_enabled:
        toolkit.register_tool_function(
            brave_search,
            func_description="通过 Brave Search API 联网检索新闻与网页来源链接。",
        )

        toolkit.register_tool_function(
            fetch_url_text,
            func_description="抓取指定 URL 的文本内容用于快速检索。",
        )

        toolkit.register_tool_function(
            fetch_url_readable_text,
            func_description="抓取并提取网页可读文本，用于快速理解页面内容。",
        )

        toolkit.register_tool_function(
            fetch_url_links,
            func_description="抓取网页并提取链接，用于发现相关来源并继续检索。",
        )

    sys_prompt = """你是 Seneschal 的基础对话助手,名字是 MobiChatBot。

    职责：
    - 与用户进行连续、多轮的自然语言对话；
    - 直接回答用户问题，必要时说明不确定性；
    - 语气简洁、专业、清晰。
    - 若有文件落盘，也必须在当前回复中直接给出答案要点，不能只给文件路径。
"""
    return ReActAgent(
        name="MobiChatBot",
        sys_prompt=sys_prompt,
        model=create_openai_model(stream=False, temperature=0.3),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        plan_notebook=PlanNotebook(),
        max_iters=8,
    )


@dataclass
class ChatSessionHandle:
    """Chat 会话句柄。"""

    session_id: str
    session_dir: Path
    is_new_session: bool
    resumed_from_latest: bool
    meta: dict[str, Any]


class ChatSessionManager:
    """管理 chat 模式会话状态、历史与中断。"""

    _SESSION_DIR_RE = re.compile(r"^(?P<prefix>\d{8}_\d{6}_\d{6})-(?P<storage_session_id>.+)$")
    _STORAGE_SESSION_ID_RE = re.compile(r"^(?P<mode>[0-9A-Za-z]+)_(?P<stamp>\d{14,20})_(?P<session_id>.+)$")

    def __init__(self, root_dir: str | Path | None = None) -> None:
        configured = str(root_dir or os.environ.get("SENESCHAL_CHAT_SESSION_ROOT", "")).strip()
        if configured:
            self.root_dir = Path(configured).expanduser()
        else:
            self.root_dir = Path(__file__).resolve().parents[1] / ".mobiclaw" / "session"
        self.latest_pointer = self.root_dir / "latest_session.json"
        self._active_replies: dict[str, tuple[ReActAgent, asyncio.Task[Any]]] = {}
        self._active_lock = asyncio.Lock()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_session_id(raw: str | None) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        text = re.sub(r"[^0-9A-Za-z._-]+", "-", text)
        return text.strip("-")

    @staticmethod
    def _generate_session_id() -> str:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"chat_{stamp}_{suffix}"

    def _ensure_root(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _session_meta_path(self, session_dir: Path) -> Path:
        return session_dir / "meta.json"

    def _history_path(self, session_dir: Path) -> Path:
        return session_dir / "history.jsonl"

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_history(self, session_dir: Path, record: dict[str, Any]) -> None:
        path = self._history_path(session_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _compact_stamp(stamp: str) -> str:
        return re.sub(r"[^0-9]+", "", str(stamp or "").strip())

    def _parse_session_dir_name(self, name: str) -> tuple[str, str, str] | None:
        candidate = str(name or "").strip()
        if not candidate:
            return None

        matched = self._SESSION_DIR_RE.match(candidate)
        if not matched:
            return None
        prefix = str(matched.group("prefix") or "").strip()
        storage_session_id = self._normalize_session_id(matched.group("storage_session_id"))
        if not prefix or not storage_session_id:
            return None
        storage_parts = self._STORAGE_SESSION_ID_RE.match(storage_session_id)
        if not storage_parts:
            return None
        session_id = self._normalize_session_id(storage_parts.group("session_id"))
        if not session_id:
            return None
        return prefix, storage_session_id, session_id

    def _list_session_dirs(self) -> list[dict[str, Any]]:
        self._ensure_root()
        records: list[dict[str, Any]] = []
        for child in self.root_dir.iterdir():
            if not child.is_dir():
                continue
            parsed = self._parse_session_dir_name(child.name)
            if parsed is None:
                continue
            _, storage_session_id, session_id = parsed
            try:
                stat = child.stat()
            except OSError:
                continue
            records.append(
                {
                    "session_id": session_id,
                    "storage_session_id": storage_session_id,
                    "path": child,
                    "updated_ts": float(stat.st_mtime),
                }
            )
        records.sort(key=lambda item: float(item.get("updated_ts", 0.0)), reverse=True)
        return records

    def _find_latest_dir_for_session(self, session_id: str) -> Path | None:
        normalized = self._normalize_session_id(session_id)
        if not normalized:
            return None
        for item in self._list_session_dirs():
            if item.get("session_id") == normalized or item.get("storage_session_id") == normalized:
                target = item.get("path")
                if isinstance(target, Path):
                    return target
        return None

    def _create_session_dir(self, session_id: str) -> Path:
        storage_session_id = self._build_storage_session_id(session_id)
        if not storage_session_id:
            raise ValueError("session_id is empty")
        self._ensure_root()
        dir_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}-{storage_session_id}"
        session_dir = self.root_dir / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _build_storage_session_id(self, session_id: str) -> str:
        normalized = self._normalize_session_id(session_id)
        if self._STORAGE_SESSION_ID_RE.match(normalized):
            return normalized
        if not normalized:
            return ""
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        return f"chat_{stamp}_{normalized}"

    @staticmethod
    def _session_state_path(session_dir: Path, session_id: str) -> Path:
        return session_dir / f"{session_id}.json"

    def _read_latest_pointer(self) -> dict[str, Any]:
        return self._read_json(self.latest_pointer)

    def _write_latest_pointer(self, *, session_id: str, session_dir: Path) -> None:
        parsed = self._parse_session_dir_name(session_dir.name)
        self._write_json(
            self.latest_pointer,
            {
                "session_id": session_id,
                "storage_session_id": parsed[1] if parsed is not None else self._build_storage_session_id(session_id),
                "session_dir": str(session_dir.resolve()),
                "updated_at": self._utc_now_iso(),
            },
        )

    async def resolve_session(self, context_id: str | None, *, force_new: bool = False) -> ChatSessionHandle:
        """根据优先级选择或创建会话。"""
        normalized_context = self._normalize_session_id(context_id)
        resumed_from_latest = False
        is_new_session = False

        if force_new:
            session_id = normalized_context or self._generate_session_id()
            session_dir = self._create_session_dir(session_id)
            is_new_session = True
        elif normalized_context:
            session_id = normalized_context
            existing = self._find_latest_dir_for_session(session_id)
            if existing is not None:
                session_dir = existing
                is_new_session = False
            else:
                session_dir = self._create_session_dir(session_id)
                is_new_session = True
        else:
            latest = self._read_latest_pointer()
            latest_id = self._normalize_session_id(latest.get("session_id"))
            latest_path = Path(str(latest.get("session_dir") or "")).expanduser() if latest.get("session_dir") else None
            if latest_id and latest_path and latest_path.exists() and latest_path.is_dir():
                session_id = latest_id
                session_dir = latest_path
                resumed_from_latest = True
            else:
                session_id = self._generate_session_id()
                session_dir = self._create_session_dir(session_id)
                is_new_session = True

        meta = self._read_json(self._session_meta_path(session_dir))
        self._write_latest_pointer(session_id=session_id, session_dir=session_dir)
        return ChatSessionHandle(
            session_id=session_id,
            session_dir=session_dir,
            is_new_session=is_new_session,
            resumed_from_latest=resumed_from_latest,
            meta=meta,
        )

    async def load_agent_state(self, handle: ChatSessionHandle, agent: ReActAgent) -> None:
        """加载会话中的 agent 状态。"""
        storage_session_id = str(handle.meta.get("storage_session_id") or "").strip()
        if not storage_session_id:
            parsed = self._parse_session_dir_name(handle.session_dir.name)
            if parsed is not None:
                storage_session_id = parsed[1]
        if not storage_session_id:
            storage_session_id = self._build_storage_session_id(handle.session_id)
        if not storage_session_id:
            logger.info("Skip loading chat state: empty storage_session_id")
            return

        state_path = self._session_state_path(handle.session_dir, storage_session_id)
        if not state_path.exists() or not state_path.is_file():
            logger.info("Session state file not found: %s", state_path)
            return
        data = self._read_json(state_path)
        agent_state = data.get("agent") if isinstance(data, dict) else None
        if not isinstance(agent_state, dict):
            logger.warning("Invalid agent state file: %s", state_path)
            return
        try:
            # Align with AgentScope task_state tutorial: delegate state
            # restore to agent.load_state_dict so nested StateModule
            # attributes (memory/toolkit/long_term_memory/plan_notebook)
            # are restored uniformly.
            agent.load_state_dict(agent_state, strict=False)
            logger.info("Loaded chat agent state from %s", state_path)
        except Exception:
            logger.warning("Failed to load chat agent state from %s", state_path, exc_info=True)

    async def save_agent_state(
        self,
        handle: ChatSessionHandle,
        agent: ReActAgent,
        *,
        command: str,
        introduced: bool,
    ) -> None:
        """保存会话中的 agent 状态与元数据。"""
        session = JSONSession(save_dir=str(handle.session_dir))
        storage_session_id = str(handle.meta.get("storage_session_id") or "").strip()
        if not storage_session_id:
            parsed = self._parse_session_dir_name(handle.session_dir.name)
            if parsed is not None:
                storage_session_id = parsed[1]
        if not storage_session_id:
            storage_session_id = self._build_storage_session_id(handle.session_id)
        await session.save_session_state(
            session_id=storage_session_id,
            agent=agent,
        )
        current_meta = self._read_json(self._session_meta_path(handle.session_dir))
        current_meta.update(
            {
                "session_id": handle.session_id,
                "storage_session_id": storage_session_id,
                "session_dir": str(handle.session_dir.resolve()),
                "updated_at": self._utc_now_iso(),
                "last_command": command,
                "introduced": bool(introduced),
            }
        )
        if "created_at" not in current_meta:
            current_meta["created_at"] = self._utc_now_iso()
        self._write_json(self._session_meta_path(handle.session_dir), current_meta)
        self._write_latest_pointer(session_id=handle.session_id, session_dir=handle.session_dir)
        handle.meta = current_meta

    def append_turn_history(
        self,
        *,
        handle: ChatSessionHandle,
        user_text: str,
        assistant_text: str,
        command: str,
    ) -> None:
        """记录对话轮次到 history.jsonl。"""
        ts = self._utc_now_iso()
        if str(user_text or "").strip():
            self._append_history(
                handle.session_dir,
                {
                    "ts": ts,
                    "role": "user",
                    "name": "user",
                    "text": str(user_text or "").strip(),
                    "meta": {
                        "session_id": handle.session_id,
                        "command": command,
                    },
                },
            )
        if str(assistant_text or "").strip():
            self._append_history(
                handle.session_dir,
                {
                    "ts": ts,
                    "role": "assistant",
                    "name": "assistant",
                    "text": str(assistant_text or "").strip(),
                    "meta": {
                        "session_id": handle.session_id,
                        "command": command,
                    },
                },
            )

    async def register_active_reply(
        self,
        session_id: str,
        agent: ReActAgent,
        task: asyncio.Task[Any],
    ) -> None:
        """注册当前会话活跃回复任务。"""
        async with self._active_lock:
            self._active_replies[session_id] = (agent, task)

    async def unregister_active_reply(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        """注销当前会话活跃回复任务。"""
        async with self._active_lock:
            current = self._active_replies.get(session_id)
            if current and current[1] is task:
                self._active_replies.pop(session_id, None)

    async def interrupt_session(self, session_id: str) -> bool:
        """尝试中断某会话当前活跃回复。"""
        async with self._active_lock:
            current = self._active_replies.get(session_id)
        if not current:
            return False
        agent, _ = current
        await agent.interrupt()
        return True
