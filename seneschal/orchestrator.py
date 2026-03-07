# -*- coding: utf-8 -*-
"""Multi-agent task orchestration: router + planner + executor."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from .agents import (
    create_planner_agent,
    create_router_agent,
    create_steward_agent,
    create_worker_agent,
    get_agent_capability_descriptions,
)
from .config import ROUTING_CONFIG

logger = logging.getLogger(__name__)


LEGACY_MODES = {"worker", "steward", "auto"}
ROUTER_MODES = {"router", "intelligent"}


@dataclass
class RouteDecision:
    target_agents: list[str]
    reason: str
    confidence: float
    plan_required: bool
    strategy: str


def _extract_response_text(response: Any) -> str:
    if response is None:
        return ""
    text = response.get_text_content() if hasattr(response, "get_text_content") else ""
    if text:
        return text
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_text = getattr(block, "text", "")
        if block_text:
            parts.append(block_text)
    return "\n".join(parts).strip()


def _collect_file_paths(text: str, output_path: str | None = None) -> list[Path]:
    paths: list[Path] = []
    if output_path:
        paths.append(Path(output_path).expanduser())

    for raw in re.findall(r"\[File\]\s+Wrote:\s*(.+)", text or ""):
        candidate = raw.strip()
        if candidate:
            paths.append(Path(candidate).expanduser())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _build_file_entries(paths: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            resolved = path.absolute()
        if not resolved.exists() or not resolved.is_file():
            continue
        stat = resolved.stat()
        entries.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "size": stat.st_size,
            }
        )
    return entries


def _parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_agent_name(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"worker", "research", "researcher"}:
        return "worker"
    return "steward"


def _rule_route(task: str) -> RouteDecision:
    text = (task or "").lower()
    split_signals = ["并且", "同时", "然后", "再", ";", "；"]
    plan_required = any(token in task for token in split_signals)

    worker_keys = {
        "arxiv",
        "dblp",
        "论文",
        "搜索",
        "检索",
        "网页",
        "网站",
        "新闻",
        "总结",
        "下载",
        "pdf",
        "osdi",
    }
    steward_keys = {
        "微信",
        "手机",
        "日历",
        "提醒",
        "mobi",
        "weknora",
        "知识库",
        "收集",
        "待办",
    }

    hit_worker = any(k in text for k in worker_keys)
    hit_steward = any(k in text for k in steward_keys)

    if hit_worker and hit_steward:
        return RouteDecision(
            target_agents=["steward", "worker"],
            reason="规则路由判断任务同时涉及端侧/知识流程与通用检索流程",
            confidence=0.68,
            plan_required=True,
            strategy="rule_fallback",
        )
    if hit_worker:
        return RouteDecision(
            target_agents=["worker"],
            reason="规则路由命中通用检索/论文/网页任务",
            confidence=0.7,
            plan_required=plan_required,
            strategy="rule_fallback",
        )
    return RouteDecision(
        target_agents=["steward"],
        reason="规则路由默认回退到 Steward",
        confidence=0.55,
        plan_required=plan_required,
        strategy="rule_fallback",
    )


async def _llm_route(task: str, strategy: str) -> RouteDecision:
    profiles = get_agent_capability_descriptions()
    prompt = (
        "你是任务路由器。请根据任务为多智能体系统做决策。请你快速做选择，不需要多想\n"
        "候选 Agent 及能力:\n"
        f"{json.dumps(profiles, ensure_ascii=False, indent=2)}\n\n"
        "输出严格 JSON，不要输出其他文本，格式为:\n"
        '{"target_agents":["steward"|"worker"],"reason":"...","confidence":0.0,"plan_required":true|false}\n\n'
        "要求:\n"
        "1) 可选一个或多个 agent。\n"
        "2) 任务明显复合时 plan_required=true。\n"
        "3) 不确定时优先 steward。\n\n"
        f"用户任务:\n{task}"
    )
    agent = create_router_agent()
    response = await agent(Msg(name="User", content=prompt, role="user"))
    text = _extract_response_text(response)
    parsed = _parse_json_object(text)
    if not parsed:
        return _rule_route(task)

    targets = parsed.get("target_agents")
    if not isinstance(targets, list) or not targets:
        return _rule_route(task)

    normalized: list[str] = []
    for item in targets:
        normalized.append(_normalize_agent_name(str(item)))
    normalized = list(dict.fromkeys(normalized))

    confidence = parsed.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.5
    confidence_value = max(0.0, min(1.0, confidence_value))

    return RouteDecision(
        target_agents=normalized,
        reason=str(parsed.get("reason") or "llm_route"),
        confidence=confidence_value,
        plan_required=bool(parsed.get("plan_required", len(normalized) > 1)),
        strategy=strategy,
    )


def _force_legacy_route(mode: str) -> RouteDecision | None:
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == "worker":
        return RouteDecision(["worker"], "legacy mode=worker", 1.0, False, "legacy")
    if normalized_mode in {"steward", "auto"}:
        return RouteDecision(["steward"], f"legacy mode={normalized_mode}", 1.0, False, "legacy")
    return None


def _split_task_by_connectors(task: str) -> list[str]:
    raw_parts = re.split(r"(?:并且|然后|再|同时|;|；|\n)", task)
    parts = [part.strip() for part in raw_parts if part and part.strip()]
    return parts or [task.strip()]


def _subtask_agent_by_rule(subtask: str) -> str:
    decision = _rule_route(subtask)
    return decision.target_agents[0] if decision.target_agents else "steward"


async def _llm_plan(task: str, decision: RouteDecision, max_subtasks: int) -> list[list[dict[str, str]]]:
    prompt = (
        "你是任务规划器。请把用户任务拆成可执行阶段。\n"
        "输出严格 JSON，格式为:\n"
        '{"stages":[[{"agent":"steward|worker","task":"..."}]]}\n\n'
        "规则:\n"
        "1) stages 是二维数组，外层表示阶段（串行），内层表示同阶段并行子任务。\n"
        "2) agent 只能是 steward 或 worker。\n"
        "3) 子任务总数不超过 max_subtasks。\n"
        "4) 若任务简单，可只给一个子任务。\n\n"
        f"max_subtasks={max_subtasks}\n"
        f"router_decision={json.dumps(decision.__dict__, ensure_ascii=False)}\n"
        f"task={task}"
    )
    planner = create_planner_agent()
    response = await planner(Msg(name="User", content=prompt, role="user"))
    parsed = _parse_json_object(_extract_response_text(response))
    stages = parsed.get("stages") if isinstance(parsed, dict) else None
    if not isinstance(stages, list):
        raise ValueError("invalid stages")

    planned: list[list[dict[str, str]]] = []
    subtask_count = 0
    for stage in stages:
        if not isinstance(stage, list):
            continue
        normalized_stage: list[dict[str, str]] = []
        for item in stage:
            if not isinstance(item, dict):
                continue
            agent_name = _normalize_agent_name(str(item.get("agent") or ""))
            task_text = str(item.get("task") or "").strip()
            if not task_text:
                continue
            normalized_stage.append({"agent": agent_name, "task": task_text})
            subtask_count += 1
            if subtask_count >= max_subtasks:
                break
        if normalized_stage:
            planned.append(normalized_stage)
        if subtask_count >= max_subtasks:
            break

    if not planned:
        raise ValueError("empty plan")
    return planned


def _fallback_plan(task: str, decision: RouteDecision, max_subtasks: int) -> list[list[dict[str, str]]]:
    if not decision.plan_required and len(decision.target_agents) == 1:
        return [[{"agent": decision.target_agents[0], "task": task.strip()}]]

    parts = _split_task_by_connectors(task)
    stages: list[list[dict[str, str]]] = []

    if len(parts) <= 1 and len(decision.target_agents) > 1:
        stages.append([{"agent": decision.target_agents[0], "task": task.strip()}])
        stages.append([{"agent": decision.target_agents[1], "task": f"基于用户任务补充执行并总结：{task.strip()}"}])
        return stages

    for part in parts[:max_subtasks]:
        stages.append([{"agent": _subtask_agent_by_rule(part), "task": part}])
    return stages or [[{"agent": "steward", "task": task.strip()}]]


def _build_agent(agent_name: str):
    if agent_name == "worker":
        return create_worker_agent()
    return create_steward_agent()


async def _run_one_agent(agent_name: str, task: str, output_path: str | None = None) -> dict[str, Any]:
    agent = _build_agent(agent_name)
    msg_content = task.strip()
    if output_path:
        msg_content += (
            "\n\n输出文件路径: "
            + output_path
            + "\n如需落盘，请自行选择合适工具完成。"
        )
    start = time.perf_counter()
    response = await agent(Msg(name="User", content=msg_content, role="user"))
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return {
        "agent": agent_name,
        "task": task,
        "reply": _extract_response_text(response),
        "elapsed_ms": elapsed_ms,
    }


def _aggregate_replies(executions: list[dict[str, Any]]) -> str:
    if not executions:
        return ""
    if len(executions) == 1:
        return str(executions[0].get("reply") or "")

    blocks: list[str] = []
    for idx, item in enumerate(executions, start=1):
        blocks.append(
            f"[{idx}] Agent={item.get('agent')}\n"
            f"Task: {item.get('task')}\n"
            f"Reply:\n{item.get('reply') or ''}"
        )
    return "\n\n".join(blocks).strip()


async def run_orchestrated_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    del context_id  # Reserved for future multi-turn context persistence.

    task_start = time.perf_counter()
    normalized_mode = (mode or "").strip().lower() or ROUTING_CONFIG["default_mode"]
    strategy = (routing_strategy or ROUTING_CONFIG["strategy"]).strip().lower()
    router_timeout_s = float(ROUTING_CONFIG["router_timeout_s"])
    planner_timeout_s = float(ROUTING_CONFIG["planner_timeout_s"])
    subtask_timeout_s = float(ROUTING_CONFIG["subtask_timeout_s"])

    logger.info(
        "orchestrator.start mode=%s strategy=%s agent_hint=%s task_preview=%s",
        normalized_mode,
        strategy,
        agent_hint or "",
        (task or "")[:120].replace("\n", " "),
    )

    forced = None
    route_control_path = "router"
    if ROUTING_CONFIG["allow_legacy_mode"] and normalized_mode in LEGACY_MODES:
        forced = _force_legacy_route(normalized_mode)
        if forced:
            route_control_path = "legacy"

    if agent_hint:
        forced = RouteDecision(
            target_agents=[_normalize_agent_name(agent_hint)],
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
            decision = await asyncio.wait_for(_llm_route(task, strategy), timeout=router_timeout_s)
            route_control_path = "router_llm"
            logger.info(
                "orchestrator.route.ok timeout_s=%.2f targets=%s confidence=%.2f reason=%s",
                router_timeout_s,
                decision.target_agents,
                decision.confidence,
                decision.reason,
            )
        except asyncio.TimeoutError:
            decision = RouteDecision(
                target_agents=["worker"],
                reason="router timeout -> default worker",
                confidence=0.0,
                plan_required=False,
                strategy="timeout_default_worker",
            )
            route_control_path = "router_timeout_worker"
            logger.warning("orchestrator.route.timeout timeout_s=%.2f; fallback=worker", router_timeout_s)
        except Exception:
            decision = _rule_route(task)
            route_control_path = "router_error_fallback"
            logger.exception("orchestrator.route.error fallback=rule")
    else:
        decision = _rule_route(task)
        route_control_path = "rule"

    max_subtasks = int(ROUTING_CONFIG["max_subtasks"])
    plan_control_path = "direct"
    if decision.plan_required or len(decision.target_agents) > 1:
        try:
            stages = await asyncio.wait_for(
                _llm_plan(task, decision, max_subtasks=max_subtasks),
                timeout=planner_timeout_s,
            )
            plan_source = "llm"
            plan_control_path = "planner_llm"
            logger.info(
                "orchestrator.plan.ok timeout_s=%.2f stages=%d",
                planner_timeout_s,
                len(stages),
            )
        except asyncio.TimeoutError:
            stages = [[{"agent": "worker", "task": task.strip()}]]
            plan_source = "timeout_worker"
            plan_control_path = "planner_timeout_worker"
            logger.warning("orchestrator.plan.timeout timeout_s=%.2f; fallback=worker", planner_timeout_s)
        except Exception:
            stages = _fallback_plan(task, decision, max_subtasks=max_subtasks)
            plan_source = "fallback"
            plan_control_path = "planner_error_fallback"
            logger.exception("orchestrator.plan.error fallback=rule")
    else:
        stages = [[{"agent": decision.target_agents[0], "task": task.strip()}]]
        plan_source = "direct"
        plan_control_path = "direct"

    executions: list[dict[str, Any]] = []
    stage_traces: list[dict[str, Any]] = []

    for stage_index, stage in enumerate(stages, start=1):
        stage_start = time.perf_counter()
        logger.info(
            "orchestrator.stage.start stage=%d subtasks=%d parallel=%s",
            stage_index,
            len(stage),
            len(stage) > 1,
        )
        run_items = []
        for sub_index, item in enumerate(stage, start=1):
            is_last = stage_index == len(stages) and sub_index == len(stage)
            hint_path = output_path if is_last else None
            run_items.append(
                asyncio.wait_for(
                    _run_one_agent(item["agent"], item["task"], output_path=hint_path),
                    timeout=subtask_timeout_s,
                )
            )

        stage_results = await asyncio.gather(*run_items, return_exceptions=True)
        stage_execs: list[dict[str, Any]] = []
        for result in stage_results:
            if isinstance(result, Exception):
                if isinstance(result, asyncio.TimeoutError):
                    error_text = f"subtask timeout>{subtask_timeout_s:.2f}s"
                else:
                    error_text = str(result)
                stage_execs.append(
                    {
                        "agent": "unknown",
                        "task": "",
                        "reply": f"subtask failed: {error_text}",
                        "elapsed_ms": 0,
                        "error": error_text,
                    }
                )
            else:
                stage_execs.append(result)
        executions.extend(stage_execs)
        stage_elapsed_ms = int((time.perf_counter() - stage_start) * 1000)
        stage_traces.append(
            {
                "stage": stage_index,
                "parallel": len(stage_execs) > 1,
                "elapsed_ms": stage_elapsed_ms,
                "subtasks": stage_execs,
            }
        )
        logger.info(
            "orchestrator.stage.done stage=%d elapsed_ms=%d errors=%d",
            stage_index,
            stage_elapsed_ms,
            sum(1 for item in stage_execs if item.get("error")),
        )

    reply = _aggregate_replies(executions)
    file_paths = _collect_file_paths(reply, output_path=output_path)
    files = _build_file_entries(file_paths)
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
        "files": files,
        "routing_trace": {
            "control_path": {
                "route": route_control_path,
                "plan": plan_control_path,
            },
            "timeouts": {
                "router_timeout_s": router_timeout_s,
                "planner_timeout_s": planner_timeout_s,
                "subtask_timeout_s": subtask_timeout_s,
            },
            "timing": {
                "total_elapsed_ms": total_elapsed_ms,
            },
            "decision": {
                "target_agents": decision.target_agents,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "plan_required": decision.plan_required,
                "strategy": decision.strategy,
            },
            "plan_source": plan_source,
            "stages": stage_traces,
        },
    }