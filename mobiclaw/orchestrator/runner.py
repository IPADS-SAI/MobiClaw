# -*- coding: utf-8 -*-
"""orchestrator 的总调度流程。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import ROUTING_CONFIG
from ..session import GenericSessionManager
from .types import LEGACY_MODES, ROUTER_MODES, ProgressCallback, RouteDecision, ANSI_GREEN, ANSI_RED, _highlight_log

logger = logging.getLogger("mobiclaw.orchestrator")
_GENERIC_SESSION_MANAGER = GenericSessionManager()


def _orchestrator_override(name: str, default: Any) -> Any:
    from .. import orchestrator as orchestrator_module

    return getattr(orchestrator_module, name, default)


async def _emit_progress(
    callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        result = callback(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.warning("Failed to emit orchestrator progress", exc_info=True)


async def run_orchestrated_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
    external_context: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    task_start = time.perf_counter()
    normalized_mode = (mode or "").strip().lower() or ROUTING_CONFIG["default_mode"]
    session_handle = await _GENERIC_SESSION_MANAGER.resolve_session(context_id, mode=normalized_mode)
    _GENERIC_SESSION_MANAGER.append_history_message(
        handle=session_handle,
        role="user",
        name="user",
        text=task,
        mode=normalized_mode,
        command="orchestrated_task",
    )
    strategy = (routing_strategy or ROUTING_CONFIG["strategy"]).strip().lower()
    router_timeout_s = float(ROUTING_CONFIG["router_timeout_s"])
    planner_timeout_s = float(ROUTING_CONFIG["planner_timeout_s"])
    subtask_timeout_s = float(ROUTING_CONFIG["subtask_timeout_s"])

    create_job_output_paths = _orchestrator_override("_create_job_output_paths", None)
    resolved_output_path, job_output_dir, job_tmp_dir = create_job_output_paths(output_path)

    logger.info(
        "orchestrator.start mode=%s strategy=%s agent_hint=%s task_preview=%s",
        normalized_mode,
        strategy,
        agent_hint or "",
        (task or "")[:120].replace("\n", " "),
    )

    force_legacy_route = _orchestrator_override("_force_legacy_route", None)
    normalize_agent_name = _orchestrator_override("_normalize_agent_name", None)
    llm_route = _orchestrator_override("_llm_route", None)
    rule_route = _orchestrator_override("_rule_route", None)
    available_agent_names = _orchestrator_override("_available_agent_names", None)
    default_agent_name = _orchestrator_override("_default_agent_name", None)
    planner_allowed_agents_fn = _orchestrator_override("_planner_allowed_agents", None)
    llm_plan = _orchestrator_override("_llm_plan", None)
    fallback_plan = _orchestrator_override("_fallback_plan", None)
    select_skills = _orchestrator_override("_select_skills_for_subtask", None)
    build_upstream_context = _orchestrator_override("_build_upstream_context", None)
    run_one_agent = _orchestrator_override("_run_one_agent", None)
    aggregate_replies = _orchestrator_override("_aggregate_replies", None)
    ensure_output_file_written = _orchestrator_override("_ensure_output_file_written", None)
    collect_file_paths = _orchestrator_override("_collect_file_paths", None)
    merge_file_paths = _orchestrator_override("_merge_file_paths", None)
    collect_tmp_dir_file_paths = _orchestrator_override("_collect_tmp_dir_file_paths", None)
    build_file_entries = _orchestrator_override("_build_file_entries", None)

    forced = None
    route_control_path = "router"
    if ROUTING_CONFIG["allow_legacy_mode"] and normalized_mode in LEGACY_MODES:
        forced = force_legacy_route(normalized_mode)
        if forced:
            route_control_path = "legacy"

    if agent_hint:
        forced = RouteDecision(
            target_agents=[normalize_agent_name(agent_hint)],
            reason=f"forced by agent_hint={agent_hint}",
            confidence=1.0,
            plan_required=False,
            strategy="hint",
        )
        route_control_path = "hint"

    logger.info(
        "orchestrator.route.selecting_agent mode=%s strategy=%s forced=%s task_preview=%s",
        normalized_mode,
        strategy,
        bool(forced),
        (task or "")[:120].replace("\n", " "),
    )

    if forced:
        decision = forced
    elif normalized_mode in ROUTER_MODES or normalized_mode not in LEGACY_MODES:
        try:
            decision = await asyncio.wait_for(llm_route(task, strategy), timeout=router_timeout_s)
            route_control_path = "router_llm"
            logger.info(
                "orchestrator.route.ok timeout_s=%.2f targets=%s confidence=%.2f reason=%s",
                router_timeout_s,
                decision.target_agents,
                decision.confidence,
                decision.reason,
            )
        except asyncio.TimeoutError:
            timeout_default_agent = "worker" if "worker" in available_agent_names() else default_agent_name()
            decision = RouteDecision(
                target_agents=[timeout_default_agent],
                reason=f"router timeout -> default {timeout_default_agent}",
                confidence=0.0,
                plan_required=False,
                strategy="timeout_default_worker",
            )
            route_control_path = "router_timeout_worker"
            logger.warning("orchestrator.route.timeout timeout_s=%.2f; fallback=%s", router_timeout_s, timeout_default_agent)
        except Exception:
            decision = rule_route(task)
            route_control_path = "router_error_fallback"
            logger.exception("orchestrator.route.error fallback=rule")
    else:
        decision = rule_route(task)
        route_control_path = "rule"

    max_subtasks = int(ROUTING_CONFIG["max_subtasks"])
    planner_allowed_agents = planner_allowed_agents_fn(decision)
    plan_control_path = "direct"
    single_direct = len(decision.target_agents) == 1
    if decision.plan_required or len(decision.target_agents) > 1:
        if single_direct:
            stages = [[{"agent": decision.target_agents[0], "task": task.strip()}]]
            plan_source = "direct_single"
            plan_control_path = "direct_single"
            logger.info("orchestrator.plan.skip_single target=%s", decision.target_agents[0])
        else:
            try:
                stages = await asyncio.wait_for(llm_plan(task, decision, max_subtasks=max_subtasks), timeout=planner_timeout_s)
                plan_source = "llm"
                plan_control_path = "planner_llm"
                logger.info("orchestrator.plan.ok timeout_s=%.2f stages=%d", planner_timeout_s, len(stages))
            except asyncio.TimeoutError:
                timeout_default_agent = "worker" if "worker" in available_agent_names() else default_agent_name()
                stages = [[{"agent": timeout_default_agent, "task": task.strip()}]]
                plan_source = "timeout_worker"
                plan_control_path = "planner_timeout_worker"
                logger.warning("orchestrator.plan.timeout timeout_s=%.2f; fallback=%s", planner_timeout_s, timeout_default_agent)
            except Exception:
                stages = fallback_plan(task, decision, max_subtasks=max_subtasks)
                plan_source = "fallback"
                plan_control_path = "planner_error_fallback"
                logger.exception("orchestrator.plan.error fallback=rule")
    else:
        stages = [[{"agent": decision.target_agents[0], "task": task.strip()}]]
        plan_source = "direct"
        plan_control_path = "direct"

    executions: list[dict[str, Any]] = []
    shared_file_paths: list[Path] = []
    stage_traces: list[dict[str, Any]] = []
    skill_trace_records: list[dict[str, Any]] = []

    for stage_index, stage in enumerate(stages, start=1):
        stage_started_at = datetime.now(timezone.utc).isoformat()
        await _emit_progress(progress_callback, {
            "channel": "orchestrator_progress",
            "event_key": f"orchestrator:{stage_index}:0:started:{time.time_ns()}",
            "stage": stage_index,
            "subtask": 0,
            "agent": "",
            "state": "started",
            "task": "",
            "reply_preview": "",
            "error": "",
            "event_at": stage_started_at,
        })
        stage_start = time.perf_counter()
        logger.info("orchestrator.stage.start stage=%d subtasks=%d parallel=%s", stage_index, len(stage), len(stage) > 1)
        stage_execs: list[dict[str, Any]] = []
        for sub_index, item in enumerate(stage, start=1):
            subtask_started_at = datetime.now(timezone.utc).isoformat()
            await _emit_progress(progress_callback, {
                "channel": "orchestrator_progress",
                "event_key": f"orchestrator:{stage_index}:{sub_index}:started:{time.time_ns()}",
                "stage": stage_index,
                "subtask": sub_index,
                "agent": str(item.get("agent") or ""),
                "state": "started",
                "task": str(item.get("task") or ""),
                "reply_preview": "",
                "error": "",
                "event_at": subtask_started_at,
            })
            is_last = stage_index == len(stages) and sub_index == len(stage)
            hint_path = resolved_output_path if is_last else None
            skill_decision = await select_skills(
                task=task,
                subtask=item["task"],
                agent_name=item["agent"],
                strategy=strategy,
                skill_hint=skill_hint,
            )
            skill_trace_records.append({
                "stage": stage_index,
                "subtask": sub_index,
                "agent": item["agent"],
                "task_preview": item["task"][:120],
                "selected_skills": skill_decision.selected_skills,
                "source": skill_decision.source,
                "reason": skill_decision.reason,
                "candidates": skill_decision.candidates,
                "hint_used": skill_decision.hint_used,
                "hint_invalid": skill_decision.hint_invalid,
            })
            selected_skill_text = ", ".join(skill_decision.selected_skills) if skill_decision.selected_skills else "(none)"
            highlight_color = ANSI_GREEN if skill_decision.selected_skills else ANSI_RED
            logger.info(_highlight_log(
                "orchestrator.skill.final stage=" + str(stage_index)
                + " subtask=" + str(sub_index)
                + " agent=" + str(item["agent"])
                + " selected=[" + selected_skill_text + "] source="
                + str(skill_decision.source)
                + " reason=" + str(skill_decision.reason),
                highlight_color,
            ))
            prior_context = build_upstream_context(
                executions=executions,
                file_paths=shared_file_paths,
                max_chars=int(ROUTING_CONFIG.get("upstream_context_max_chars", 4000)),
                max_steps=int(ROUTING_CONFIG.get("upstream_context_max_steps", 20)),
            )

            try:
                # shield prevents wait_for from cancelling the inner task
                # directly, so wait_for properly raises TimeoutError.
                # Without shield, AgentBase.__call__ catches CancelledError
                # internally (for its realtime-steering feature) and returns
                # a normal Msg, causing wait_for to return instead of raising.
                agent_task = asyncio.ensure_future(run_one_agent(
                    item["agent"],
                    item["task"],
                    output_path=hint_path,
                    output_dir=job_output_dir,
                    temp_dir=job_tmp_dir,
                    selected_skills=skill_decision.selected_skills,
                    prior_context=prior_context,
                    session_manager=_GENERIC_SESSION_MANAGER,
                    session_handle=session_handle,
                    session_mode=normalized_mode,
                    external_context=external_context,
                ))
                result = await asyncio.wait_for(asyncio.shield(agent_task), timeout=subtask_timeout_s)
            except asyncio.TimeoutError:
                agent_task.cancel()
                try:
                    await agent_task
                except (asyncio.CancelledError, Exception):
                    pass
                result = {
                    "agent": item["agent"],
                    "task": item["task"],
                    "skills": skill_decision.selected_skills,
                    "reply": f"subtask \"{item['task'][:80]}\" timeout after {subtask_timeout_s}s",
                    "elapsed_ms": int(subtask_timeout_s * 1000),
                    "error": f"subtask \"{item['task'][:80]}\" timeout after {subtask_timeout_s}s",
                }
                logger.warning("orchestrator.executor.timeout agent=%s timeout_s=%.1f", item["agent"], subtask_timeout_s)
            except Exception as exc:
                result = {
                    "agent": item["agent"],
                    "task": item["task"],
                    "skills": skill_decision.selected_skills,
                    "reply": f"subtask failed: {exc}",
                    "elapsed_ms": 0,
                    "error": str(exc),
                }
            _GENERIC_SESSION_MANAGER.append_history_message(
                handle=session_handle,
                role="assistant",
                name=item["agent"],
                text=str(result.get("reply") or ""),
                mode=normalized_mode,
                command="orchestrated_subtask",
                agent=item["agent"],
                extra_meta={
                    "task": item["task"],
                    "stage": stage_index,
                    "subtask": sub_index,
                    "elapsed_ms": int(result.get("elapsed_ms", 0) or 0),
                    "error": str(result.get("error") or ""),
                },
            )

            stage_execs.append(result)
            executions.append(result)
            reply_preview = str(result.get("reply") or "").strip()
            await _emit_progress(progress_callback, {
                "channel": "orchestrator_progress",
                "event_key": f"orchestrator:{stage_index}:{sub_index}:{'failed' if result.get('error') else 'done'}:{time.time_ns()}",
                "stage": stage_index,
                "subtask": sub_index,
                "agent": str(item.get("agent") or ""),
                "state": "failed" if result.get("error") else "done",
                "task": str(item.get("task") or ""),
                "reply_preview": reply_preview[:320],
                "error": str(result.get("error") or ""),
                "event_at": datetime.now(timezone.utc).isoformat(),
            })
            shared_file_paths = merge_file_paths(shared_file_paths, collect_file_paths(str(result.get("reply") or ""), output_path=hint_path))
            shared_file_paths = merge_file_paths(
                shared_file_paths,
                collect_tmp_dir_file_paths(job_tmp_dir, max_files=int(ROUTING_CONFIG.get("tmp_scan_max_files", 200))),
            )

        stage_elapsed_ms = int((time.perf_counter() - stage_start) * 1000)
        stage_traces.append({
            "stage": stage_index,
            "parallel": False,
            "execution_mode": "sequential_with_context",
            "elapsed_ms": stage_elapsed_ms,
            "subtasks": stage_execs,
        })
        logger.info("orchestrator.stage.done stage=%d elapsed_ms=%d errors=%d", stage_index, stage_elapsed_ms, sum(1 for item in stage_execs if item.get("error")))

    reply = aggregate_replies(executions)
    ensured_output = ensure_output_file_written(resolved_output_path, reply)
    file_paths = merge_file_paths(shared_file_paths, collect_file_paths(reply, output_path=resolved_output_path))
    if ensured_output is not None:
        file_paths = merge_file_paths(file_paths, [ensured_output])
    file_paths = merge_file_paths(
        file_paths,
        collect_tmp_dir_file_paths(job_tmp_dir, max_files=int(ROUTING_CONFIG.get("tmp_scan_max_files", 200))),
    )
    files = build_file_entries(file_paths)
    total_elapsed_ms = int((time.perf_counter() - task_start) * 1000)

    logger.info(
        "orchestrator.done elapsed_ms=%d mode=%s plan_source=%s route_path=%s plan_path=%s",
        total_elapsed_ms,
        normalized_mode,
        plan_source,
        route_control_path,
        plan_control_path,
    )

    return {
        "reply": reply,
        "mode": normalized_mode,
        "context_id": session_handle.session_id,
        "session_id": session_handle.session_id,
        "session": {
            "session_id": session_handle.session_id,
            "session_dir": str(session_handle.session_dir.resolve()),
            "is_new_session": session_handle.is_new_session,
            "resumed_from_latest": session_handle.resumed_from_latest,
            "mode": normalized_mode,
            "storage_session_ids": session_handle.meta.get("storage_session_ids", {}),
            "agent_state_keys": session_handle.meta.get("agent_state_keys", {}),
        },
        "files": files,
        "routing_trace": {
            "control_path": {"route": route_control_path, "plan": plan_control_path},
            "timeouts": {
                "router_timeout_s": router_timeout_s,
                "planner_timeout_s": planner_timeout_s,
            },
            "timing": {"total_elapsed_ms": total_elapsed_ms},
            "external_context": external_context or {},
            "decision": {
                "target_agents": decision.target_agents,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "plan_required": decision.plan_required,
                "strategy": decision.strategy,
            },
            "planner_allowed_agents": planner_allowed_agents,
            "plan_source": plan_source,
            "skills": {
                "enabled": bool(ROUTING_CONFIG.get("skill_enabled", True)),
                "max_per_subtask": int(ROUTING_CONFIG.get("skill_max_per_subtask", 2)),
                "records": skill_trace_records,
            },
            "stages": stage_traces,
        },
    }
