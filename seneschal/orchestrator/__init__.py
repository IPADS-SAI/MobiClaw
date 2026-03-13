# -*- coding: utf-8 -*-
"""Seneschal 多智能体编排模块（Router + Planner + Executor）。"""

from __future__ import annotations

from ..agents import (
    create_planner_agent,
    create_router_agent,
    create_skill_selector_agent,
    create_steward_agent,
    create_worker_agent,
    get_agent_capability_descriptions,
)
from ..config import ROUTING_CONFIG
from .execution import _aggregate_replies, _build_agent, _run_one_agent
from .routing import (
    _available_agent_names,
    _compact_agent_profiles_for_route,
    _compact_task_for_route,
    _default_agent_name,
    _fallback_plan,
    _force_legacy_route,
    _llm_plan,
    _llm_route,
    _normalize_agent_name,
    _normalize_planner_agent,
    _planner_allowed_agents,
    _rule_route,
    _split_task_by_connectors,
    _subtask_agent_by_rule,
)
from .runner import _emit_progress, run_orchestrated_task
from .skills import (
    _all_skill_candidates,
    _available_skill_profiles,
    _llm_rerank_skills,
    _load_skill_content_direct,
    _parse_skill_frontmatter,
    _rule_select_skills,
    _select_skills_for_subtask,
    _skill_content_hint,
    _skill_hint_items,
    _skill_prompt_context,
    _skills_root,
    _strip_frontmatter,
    _tokenize_query,
)
from .types import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
    LEGACY_MODES,
    ROUTER_MODES,
    ProgressCallback,
    RouteDecision,
    SkillDecision,
    SkillProfile,
    _highlight_log,
)
from .utils import (
    _build_external_context_text,
    _build_file_entries,
    _build_upstream_context,
    _collect_file_paths,
    _collect_tmp_dir_file_paths,
    _create_job_output_paths,
    _ensure_output_file_written,
    _extract_response_text,
    _merge_file_paths,
    _parse_json_object,
    _project_root,
    _trim_for_prompt,
)
