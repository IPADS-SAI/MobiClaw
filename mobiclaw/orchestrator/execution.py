# -*- coding: utf-8 -*-
"""orchestrator 的执行逻辑。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from .. import agents as agents_module
from ..agents import create_steward_agent, create_worker_agent
from ..session import GenericSessionHandle, GenericSessionManager
from .types import ANSI_CYAN, _highlight_log

logger = logging.getLogger("mobiclaw.orchestrator")


def _orchestrator_override(name: str, default: Any) -> Any:
    from .. import orchestrator as orchestrator_module

    return getattr(orchestrator_module, name, default)


def _build_agent(
    agent_name: str,
    skill_context: str | None = None,
    job_context: dict[str, Any] | None = None,
):
    normalized = (agent_name or "").strip().lower()

    custom_factory = getattr(agents_module, "create_configured_agent_by_name", None)
    if callable(custom_factory):
        custom_agent = custom_factory(normalized, skill_context=skill_context)
        if custom_agent is not None:
            return custom_agent

    factory = getattr(agents_module, f"create_{normalized}_agent", None)
    if callable(factory):
        try:
            return factory(skill_context=skill_context, job_context=job_context)
        except TypeError:
            try:
                return factory(skill_context=skill_context)
            except TypeError:
                return factory()

    default_agent_name = _orchestrator_override("_default_agent_name", None)
    fallback = default_agent_name()
    if normalized != fallback:
        logger.warning("orchestrator.agent.unknown agent=%s; fallback=%s", normalized, fallback)
    fallback_factory = getattr(agents_module, f"create_{fallback}_agent", None)
    if callable(fallback_factory):
        try:
            return fallback_factory(job_context=job_context)
        except TypeError:
            return fallback_factory()

    if fallback == "worker":
        return create_worker_agent(skill_context=skill_context, job_context=job_context)
    return create_steward_agent(skill_context=skill_context)


async def _run_one_agent(
    agent_name: str,
    task: str,
    output_path: str | None = None,
    output_dir: str | None = None,
    temp_dir: str | None = None,
    selected_skills: list[str] | None = None,
    prior_context: str | None = None,
    session_manager: GenericSessionManager | None = None,
    session_handle: GenericSessionHandle | None = None,
    session_mode: str = "router",
    external_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill_list = selected_skills or []
    skill_prompt_context = _orchestrator_override("_skill_prompt_context", None)
    extract_text = _orchestrator_override("_extract_response_text", None)
    build_external_context = _orchestrator_override("_build_external_context_text", None)
    build_agent = _orchestrator_override("_build_agent", _build_agent)
    skill_context = skill_prompt_context(skill_list)

    ctx = external_context if isinstance(external_context, dict) else {}
    feishu_ctx = ctx.get("feishu")
    feishu_ctx = feishu_ctx if isinstance(feishu_ctx, dict) else {}
    job_ctx_dict = {
        "feishu_chat_id": feishu_ctx.get("chat_id", None),
        "feishu_user_open_id": feishu_ctx.get("open_id", None),
        "feishu_message_id": feishu_ctx.get("message_id", None),
    }
    job_ctx_dict["feishu_receive_id_type"] = "chat_id" if job_ctx_dict["feishu_chat_id"] else "open_id"
    job_ctx_dict["job_output_dir"] = str(output_dir or "")
    job_ctx_dict["job_tmp_dir"] = str(temp_dir or "")
    if output_dir:
        job_ctx_dict["mobi_output_dir"] = str((Path(output_dir) / "mobile_exec").resolve())

    agent = build_agent(agent_name, skill_context=skill_context, job_context=job_ctx_dict)
    if session_manager is not None and session_handle is not None:
        await session_manager.load_agent_state(session_handle, agent, mode=session_mode, agent_key=agent_name)
    msg_content = task.strip()
    if prior_context:
        msg_content = (
            "请在执行当前子任务时参考以下前序上下文（结果与文件），并据此衔接后续工作。\n\n"
            + prior_context
            + "\n\n"
            + msg_content
        )
    if external_context:
        msg_content = build_external_context(external_context) + "\n\n" + msg_content

    if output_path:
        msg_content += (
            "\n\n重要回复要求：必须在最终回复正文中直接给出完整答案或完整总结；"
            "禁止只回复‘已落盘/见文件路径’。若同时生成了文件，可在正文后附文件路径。"
            "\n\n全部任务完成后，最终输出文件路径或文件名(绝对路径): "
            + str(output_path or "")
            + "\n任务执行过程的临时目录，例如下载或者生成文件的目录(绝对路径): "
            + str(temp_dir or "")
            + "\n如需落盘，请自行选择合适工具完成。"
        )
    else:
        msg_content += (
            "\n\n重要回复要求：必须在最终回复正文中直接给出完整答案或完整总结；"
            "禁止只回复‘已落盘/见文件路径’。"
            "\n\n任务执行过程的临时目录，例如下载或者生成文件的目录(绝对路径): "
            + str(temp_dir or "")
            + "\n如需落盘，请自行选择合适工具完成。"
        )

    logger.info(_highlight_log("orchestrator.executor.paths agent=" + str(agent_name) + " output_path=" + str(output_path or "") + " temp_dir=" + str(temp_dir or ""), ANSI_CYAN))

    start = time.perf_counter()
    try:
        response = await agent(Msg(name="User", content=msg_content, role="user"))
    finally:
        if session_manager is not None and session_handle is not None:
            await session_manager.save_agent_state(
                session_handle,
                agent,
                command="orchestrated_subtask",
                mode=session_mode,
                agent_key=agent_name,
            )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return {
        "agent": agent_name,
        "task": task,
        "skills": skill_list,
        "reply": extract_text(response),
        "elapsed_ms": elapsed_ms,
    }


def _aggregate_replies(executions: list[dict[str, Any]]) -> str:
    if not executions:
        return ""
    last_reply = str(executions[-1].get("reply") or "").strip()
    if last_reply:
        return last_reply
    for item in reversed(executions[:-1]):
        text = str(item.get("reply") or "").strip()
        if text:
            return text
    return ""
