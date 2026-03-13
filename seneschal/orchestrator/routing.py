# -*- coding: utf-8 -*-
"""orchestrator 的路由与规划逻辑。"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from agentscope.message import Msg

from ..config import ROUTING_CONFIG
from .types import ANSI_CYAN, ANSI_GREEN, ANSI_YELLOW, RouteDecision, _highlight_log

logger = logging.getLogger("seneschal.orchestrator")


def _orchestrator_override(name: str, default: Any) -> Any:
    from .. import orchestrator as orchestrator_module

    return getattr(orchestrator_module, name, default)


@lru_cache(maxsize=1)
def _available_agent_names() -> tuple[str, ...]:
    profiles_fn = _orchestrator_override("get_agent_capability_descriptions", None)
    profiles = profiles_fn() if callable(profiles_fn) else {}
    names: list[str] = []
    if isinstance(profiles, dict):
        for key in profiles.keys():
            name = str(key or "").strip().lower()
            if name and name not in names:
                names.append(name)
    if not names:
        names = ["steward", "worker"]
    return tuple(names)


def _default_agent_name() -> str:
    names = list(_available_agent_names())
    if "worker" in names:
        return "worker"
    return names[0]


def _normalize_agent_name(
    raw: str,
    allowed_agents: set[str] | None = None,
    default_agent: str | None = None,
) -> str:
    allowed = allowed_agents or set(_available_agent_names())
    fallback = default_agent or _default_agent_name()
    value = (raw or "").strip().lower()
    alias_map = {"research": "worker", "researcher": "worker"}
    value = alias_map.get(value, value)
    return value if value in allowed else fallback


def _planner_allowed_agents(decision: RouteDecision) -> list[str]:
    if decision.target_agents:
        return list(dict.fromkeys([name for name in decision.target_agents if name]))
    return list(_available_agent_names())


def _normalize_planner_agent(
    raw: str,
    allowed_agents: list[str],
    default_agent: str,
) -> str:
    allowed_set = set(allowed_agents)
    return _normalize_agent_name(
        raw,
        allowed_agents=allowed_set,
        default_agent=default_agent if default_agent in allowed_set else allowed_agents[0],
    )


def _rule_route(task: str) -> RouteDecision:
    text = (task or "").lower()
    split_signals = ["并且", "同时", "然后", "再", ";", "；"]
    plan_required = any(token in task for token in split_signals)

    worker_keys = {
        "arxiv", "dblp", "论文", "搜索", "检索", "网页", "网站", "新闻", "总结", "下载", "pdf",
        "历史", "之前", "以前", "过去", "任务记录", "做过", "取消定时", "取消任务", "定时任务", "定时",
        "定期", "每天", "每日", "每周", "每月", "cancel", "schedule", "every day", "every week",
        "daily", "weekly",
    }
    steward_keys = {"微信", "手机", "日历", "提醒", "微博", "携程", "淘宝", "饿了么"}

    hit_worker = any(k in text for k in worker_keys)
    hit_steward = any(k in text for k in steward_keys)

    if hit_worker and hit_steward:
        return RouteDecision(["steward", "worker"], "规则路由判断任务同时涉及端侧/知识流程与通用检索流程", 0.68, True, "rule_fallback")
    if hit_worker:
        return RouteDecision(["worker"], "规则路由命中通用检索/论文/网页任务", 0.7, plan_required, "rule_fallback")
    return RouteDecision([_default_agent_name()], f"规则路由默认回退到 {_default_agent_name()}", 0.55, plan_required, "rule_fallback")


def _compact_agent_profiles_for_route(
    profiles: Any,
    max_desc_chars: int,
) -> list[dict[str, str]]:
    if not isinstance(profiles, dict):
        return []
    compact: list[dict[str, str]] = []
    for name, info in profiles.items():
        agent_name = str(name or "").strip().lower()
        if not agent_name:
            continue
        desc = str(info or "").replace("\n", " ").strip()
        desc = re.sub(r"\s+", " ", desc)
        if max_desc_chars > 0 and len(desc) > max_desc_chars:
            desc = desc[:max_desc_chars].rstrip() + "..."
        compact.append({"agent": agent_name, "desc": desc})
    return compact


def _compact_task_for_route(task: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (task or "").strip())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


async def _llm_route(task: str, strategy: str) -> RouteDecision:
    profiles_fn = _orchestrator_override("get_agent_capability_descriptions", None)
    profiles = profiles_fn() if callable(profiles_fn) else {}
    available_agents = list(_available_agent_names())
    route_default_agent = _default_agent_name()
    task_for_prompt = _compact_task_for_route(task, int(ROUTING_CONFIG.get("route_task_max_chars", 320)))
    profile_brief = _compact_agent_profiles_for_route(
        profiles,
        int(ROUTING_CONFIG.get("route_profile_desc_max_chars", 100)),
    )
    prompt = (
        "你是任务路由器，请快速选择最合适的 agent。\n"
        "候选 Agent(精简版):\n"
        f"{json.dumps(profile_brief, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "仅输出 JSON，不要输出其他文本，格式为:\n"
        '{"target_agents":["agent_name"],"reason":"...","confidence":0.0,"plan_required":true|false}\n\n'
        "要求:\n"
        f"1) target_agents 里的每个值必须来自: {available_agents}。\n"
        "2) 可选一个或多个 agent。\n"
        "3) 任务明显复合时 plan_required=true。\n"
        f"4) 不确定时优先 {route_default_agent}。\n\n"
        f"用户任务(精简): {task_for_prompt}"
    )
    logger.info(
        "orchestrator.route.prompt strategy=%s prompt_chars=%d task_chars=%d task_compact_chars=%d prompt=\n%s",
        strategy,
        len(prompt),
        len(task or ""),
        len(task_for_prompt),
        prompt,
    )
    create_router_agent = _orchestrator_override("create_router_agent", None)
    agent = create_router_agent()
    logger.info(_highlight_log("orchestrator.route.request start=1 strategy=" + strategy + " prompt=\n" + prompt, ANSI_CYAN))

    response = await agent(Msg(name="User", content=prompt, role="user"))
    logger.info(_highlight_log("orchestrator.route.response.received strategy=" + strategy, ANSI_GREEN))

    extract_text = _orchestrator_override("_extract_response_text", None)
    parse_json = _orchestrator_override("_parse_json_object", None)
    text = extract_text(response)
    logger.info("orchestrator.route.response strategy=%s response=\n%s", strategy, text)
    logger.info(_highlight_log("orchestrator.route.response.full strategy=" + strategy + " response=\n" + text, ANSI_GREEN))
    parsed = parse_json(text)
    if not parsed:
        return _rule_route(task)

    targets = parsed.get("target_agents")
    if not isinstance(targets, list) or not targets:
        return _rule_route(task)

    allowed = set(available_agents)
    normalized = [
        _normalize_agent_name(str(item), allowed_agents=allowed, default_agent=route_default_agent)
        for item in targets
    ]
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
    return decision.target_agents[0] if decision.target_agents else _default_agent_name()


async def _llm_plan(task: str, decision: RouteDecision, max_subtasks: int) -> list[list[dict[str, str]]]:
    planner_allowed = _planner_allowed_agents(decision)
    default_plan_agent = planner_allowed[0] if planner_allowed else _default_agent_name()
    prompt = (
        "## Role\n"
        "你是一个高效的任务规划专家。你的职责是将复杂的用户请求拆解为最小可行化的执行计划。\n\n"
        "## Core Principles\n"
        "1. **粗粒度拆分**：仅在任务逻辑复杂或需要切换 Agent 时才进行拆分。简单任务应保持原子性。\n"
        "2. **Agent 隔离**：严格遵循 Agent 职责边界，严禁跨领域指派。若相邻步骤使用同一 Agent，必须合并。\n"
        "3. **拓扑结构**：识别任务的依赖关系。无依赖的任务应放在同一 `stage` 中并行执行。\n"
        "4. **极致响应**：直接输出 JSON，不进行任何解释或闲聊。\n\n"
        "## Constraints\n"
        f"- **可选 Agent 列表**: {planner_allowed}\n"
        f"- **最大子任务数**: {max_subtasks}\n"
        "- **输出格式**: 严格 JSON 格式，严禁包含 Markdown 代码块标记（如 ```json）。\n\n"
        "## Output Format\n"
        '{"stages": [[{"agent": "string", "task": "string"}]]}\n'
        "- `stages` (Array<Array>): 外层数组代表串行阶段（按顺序执行），内层数组代表该阶段内可并行的任务。\n\n"
        "## Context\n"
        f"- **Router Decision**: {json.dumps(decision.__dict__, ensure_ascii=False)}\n"
        f"- **User Task**: {task}\n\n"
        "## Planning Start"
    )
    create_planner_agent = _orchestrator_override("create_planner_agent", None)
    planner = create_planner_agent()
    logger.info(_highlight_log("orchestrator.planner.request start=1 max_subtasks=" + str(max_subtasks) + " prompt=\n" + prompt, ANSI_YELLOW))
    response = await planner(Msg(name="User", content=prompt, role="user"))
    extract_text = _orchestrator_override("_extract_response_text", None)
    parse_json = _orchestrator_override("_parse_json_object", None)
    response_text = extract_text(response)
    logger.info(_highlight_log("orchestrator.planner.response done=1 response=\n" + response_text, ANSI_GREEN))
    parsed = parse_json(response_text)
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
            agent_name = _normalize_planner_agent(
                str(item.get("agent") or ""),
                allowed_agents=planner_allowed,
                default_agent=default_plan_agent,
            )
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
    planner_allowed = _planner_allowed_agents(decision)
    default_plan_agent = planner_allowed[0] if planner_allowed else _default_agent_name()
    if not decision.plan_required and len(decision.target_agents) == 1:
        return [[{"agent": decision.target_agents[0], "task": task.strip()}]]

    parts = _split_task_by_connectors(task)
    stages: list[list[dict[str, str]]] = []
    if len(parts) <= 1 and len(decision.target_agents) > 1:
        stages.append([{"agent": decision.target_agents[0], "task": task.strip()}])
        second_agent = decision.target_agents[1] if len(decision.target_agents) > 1 else default_plan_agent
        stages.append([{"agent": second_agent, "task": f"基于用户任务补充执行并总结：{task.strip()}"}])
        return stages

    for part in parts[:max_subtasks]:
        agent_name = _normalize_planner_agent(
            _subtask_agent_by_rule(part),
            allowed_agents=planner_allowed,
            default_agent=default_plan_agent,
        )
        stages.append([{"agent": agent_name, "task": part}])
    return stages or [[{"agent": _default_agent_name(), "task": task.strip()}]]
