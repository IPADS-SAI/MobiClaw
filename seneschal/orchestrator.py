# -*- coding: utf-8 -*-
"""Seneschal 多智能体编排模块（Router + Planner + Executor）。

核心流程：
1. 路由：按规则与 LLM 混合决策选择执行 Agent；
2. 规划：对复合任务拆分串并行阶段；
3. 执行：按阶段调度 Agent，并汇总回复与文件产出。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from . import agents as agents_module
from .agents import (
    create_planner_agent,
    create_router_agent,
    create_skill_selector_agent,
    create_steward_agent,
    create_worker_agent,
    get_agent_capability_descriptions,
)
from .config import ROUTING_CONFIG

logger = logging.getLogger(__name__)


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[96m"
ANSI_YELLOW = "\033[93m"
ANSI_GREEN = "\033[92m"
ANSI_RED = "\033[91m"


def _highlight_log(message: str, color: str = ANSI_CYAN) -> str:
    """格式化彩色高亮日志文本，便于终端观察关键编排节点。"""
    return f"{ANSI_BOLD}{color}{message}{ANSI_RESET}"


LEGACY_MODES = {"worker", "steward", "auto"}
ROUTER_MODES = {"router", "intelligent"}


@dataclass
class RouteDecision:
    """路由阶段产出的标准决策结构。"""
    target_agents: list[str]
    reason: str
    confidence: float
    plan_required: bool
    strategy: str


@dataclass
class SkillProfile:
    """技能元数据抽象，用于候选筛选与提示词构建。"""
    name: str
    description: str
    content_hint: str
    full_content: str
    skill_dir: str


@dataclass
class SkillDecision:
    """单个子任务的技能选择结果与依据。"""
    selected_skills: list[str]
    source: str
    reason: str
    candidates: list[dict[str, Any]]
    hint_used: list[str]
    hint_invalid: list[str]


def _extract_response_text(response: Any) -> str:
    """从 Agent 响应对象中提取可读文本。"""
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
    """从模型回复与显式输出参数中收集文件路径。"""
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
    """将文件路径转换为可序列化的文件信息。"""
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


def _merge_file_paths(existing: list[Path], incoming: list[Path]) -> list[Path]:
    """合并文件路径并保持顺序去重。"""
    merged: list[Path] = []
    seen: set[str] = set()
    for path in [*(existing or []), *(incoming or [])]:
        key = str(path)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(path)
    return merged


def _trim_for_prompt(text: str, max_chars: int) -> str:
    """压缩空白并按长度截断，避免提示词过长。"""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _build_upstream_context(
    executions: list[dict[str, Any]],
    file_paths: list[Path],
    *,
    max_chars: int = 4000,
    max_steps: int = 20,
) -> str:
    """构建上游子任务摘要，供后续子任务引用。"""
    if not executions and not file_paths:
        return ""

    recent = executions[-max_steps:] if max_steps > 0 else executions
    sections: list[str] = ["上游子任务上下文（按时间顺序）:"]

    if recent:
        sections.append("前序子任务结果:")
        start_index = len(executions) - len(recent) + 1
        for offset, item in enumerate(recent):
            idx = start_index + offset
            agent = str(item.get("agent") or "unknown")
            subtask = _trim_for_prompt(str(item.get("task") or ""), 240)
            reply = _trim_for_prompt(str(item.get("reply") or ""), 600)
            if not reply:
                reply = "(无文本输出)"
            sections.append(f"- [{idx}] agent={agent}; task={subtask}; reply={reply}")

    if file_paths:
        sections.append("前序产出文件:")
        for path in file_paths:
            sections.append(f"- {path}")

    return _trim_for_prompt("\n".join(sections), max_chars)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """从文本中解析首个可用 JSON 对象。"""
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


def _skills_root() -> Path:
    """确定技能目录根路径（优先使用环境配置）。"""
    configured = str(ROUTING_CONFIG.get("skill_root_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent / "skills"


def _parse_skill_frontmatter(text: str) -> dict[str, str]:
    """解析技能 Markdown 的 frontmatter 元数据。"""
    if not text:
        return {}
    match = re.match(r"\s*---\s*\n([\s\S]*?)\n---\s*(?:\n|$)", text)
    if not match:
        return {}
    body = match.group(1)
    meta: dict[str, str] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta


def _strip_frontmatter(text: str) -> str:
    """移除技能文档中的 frontmatter 区块。"""
    return re.sub(r"\A\s*---\s*\n[\s\S]*?\n---\s*(?:\n|$)", "", text, count=1)


def _skill_content_hint(markdown: str) -> str:
    """提取技能正文中的精简提示片段。"""
    content = _strip_frontmatter(markdown)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    chunks: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        chunks.append(line)
        if len(" ".join(chunks)) >= 220:
            break
    return " ".join(chunks)[:280].strip()


def _tokenize_query(text: str) -> list[str]:
    """对任务文本做中英文关键词切分并去重。"""
    if not text:
        return []
    lowered = text.lower()
    raw_tokens = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", lowered)
    return list(dict.fromkeys(raw_tokens))


@lru_cache(maxsize=1)
def _available_skill_profiles() -> tuple[SkillProfile, ...]:
    """扫描技能目录并缓存可用技能画像。"""
    root = _skills_root()
    if not root.exists() or not root.is_dir():
        return tuple()

    profiles: list[SkillProfile] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.exists() or not skill_file.is_file():
            continue
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_skill_frontmatter(raw)
        name = str(meta.get("name") or child.name).strip().lower()
        description = str(meta.get("description") or "").strip()
        if not description:
            hint = _skill_content_hint(raw)
            description = hint or f"Skill {name}"
        profiles.append(
            SkillProfile(
                name=name,
                description=description,
                content_hint=_skill_content_hint(raw),
                full_content=raw.strip(),
                skill_dir=str(child.resolve()),
            )
        )
    return tuple(profiles)


def _rule_select_skills(task: str, agent_name: str, max_candidates: int) -> list[dict[str, Any]]:
    """按规则从技能画像中筛选候选技能。"""
    task_tokens = _tokenize_query(task)
    agent_token = (agent_name or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for profile in _available_skill_profiles():
        haystack = f"{profile.name}\n{profile.description}\n{profile.content_hint}".lower()
        score = 0
        matched: list[str] = []
        if profile.name in (task or "").lower():
            score += 6
            matched.append(profile.name)
        if agent_token and agent_token in haystack:
            score += 2
            matched.append(f"agent:{agent_token}")
        for token in task_tokens:
            if token in haystack:
                score += 1
                matched.append(token)
        if score <= 0:
            continue
        candidates.append(
            {
                "name": profile.name,
                "score": score,
                "description": profile.description,
                "matched": list(dict.fromkeys(matched))[:8],
            }
        )

    candidates.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("name", ""))))
    return candidates[:max_candidates]


def _all_skill_candidates(max_candidates: int) -> list[dict[str, Any]]:
    """返回有限数量的全量技能候选。"""
    candidates: list[dict[str, Any]] = []
    for profile in _available_skill_profiles()[:max_candidates]:
        candidates.append(
            {
                "name": profile.name,
                "score": 0,
                "description": profile.description,
                "matched": [],
            }
        )
    return candidates


async def _llm_rerank_skills(
    task: str,
    subtask: str,
    agent_name: str,
    candidates: list[dict[str, Any]],
    max_skills: int,
) -> tuple[list[str], str]:
    """调用 Skill Selector 对规则候选进行重排。"""
    if not candidates:
        return [], "empty_candidates"

    names = [str(item.get("name") or "").strip().lower() for item in candidates]
    names = [name for name in names if name]
    if not names:
        return [], "empty_names"

    prompt = (
        "你是 Skill Selector。请从候选技能中为子任务选择最合适的技能。\n"
        "仅输出 JSON，不要输出其他文本。格式:\n"
        '{"skills":["skill_name"],"reason":"..."}\n\n'
        "要求:\n"
        f"1) skills 中每个值必须来自候选集合: {names}\n"
        f"2) 最多选择 {max_skills} 个；若没有合适技能可返回空数组。\n"
        "3) 优先与子任务直接相关的技能。\n\n"
        f"总任务: {task}\n"
        f"子任务: {subtask}\n"
        f"目标 agent: {agent_name}\n"
        f"候选详情: {json.dumps(candidates, ensure_ascii=False)}"
    )
    agent = create_skill_selector_agent()
    logger.info(
        _highlight_log(
            "orchestrator.skill_selector.request start=1 prompt=\n" + prompt,
            ANSI_YELLOW,
        )
    )
    response = await agent(Msg(name="User", content=prompt, role="user"))
    response_text = _extract_response_text(response)
    logger.info(
        _highlight_log(
            "orchestrator.skill_selector.response done=1 response=\n" + response_text,
            ANSI_GREEN,
        )
    )
    parsed = _parse_json_object(response_text)
    if not parsed:
        return [], "parse_failed"

    raw_skills = parsed.get("skills")
    if not isinstance(raw_skills, list):
        return [], "invalid_skills_field"

    allowed = set(names)
    selected: list[str] = []
    for item in raw_skills:
        name = str(item or "").strip().lower()
        if name in allowed:
            selected.append(name)
    selected = list(dict.fromkeys(selected))[:max_skills]
    return selected, str(parsed.get("reason") or "llm_rerank")


def _skill_hint_items(skill_hint: str | None) -> list[str]:
    """解析并标准化用户传入的技能提示。"""
    if not skill_hint:
        return []
    parts = [item.strip().lower() for item in re.split(r"[,;，；\s]+", skill_hint) if item and item.strip()]
    return list(dict.fromkeys(parts))


def _skill_prompt_context(selected_skills: list[str]) -> str:
    """根据选中技能拼接提示词上下文。"""
    if not selected_skills:
        return ""
    profile_map = {profile.name: profile for profile in _available_skill_profiles()}
    blocks: list[str] = []
    for name in selected_skills:
        profile = profile_map.get(name)
        if not profile:
            continue
        content = (profile.full_content or "").strip()
        if not content:
            content = (profile.description or profile.content_hint or "").strip()
        if not content:
            continue
        blocks.append(
            "\n".join(
                [
                    f"[Skill: {profile.name}]",
                    f"execution_dir: {profile.skill_dir}",
                    content,
                ]
            )
        )
    return "\n\n".join(blocks)


async def _select_skills_for_subtask(
    task: str,
    subtask: str,
    agent_name: str,
    strategy: str,
    skill_hint: str | None,
) -> SkillDecision:
    """为子任务执行“提示覆盖 + 规则筛选 + LLM重排”的组合选技。"""
    if not bool(ROUTING_CONFIG.get("skill_enabled", True)):
        return SkillDecision([], "disabled", "skill selection disabled", [], [], [])

    profiles = _available_skill_profiles()
    if not profiles:
        return SkillDecision([], "no_skills", "no skill profiles discovered", [], [], [])

    max_skills = int(ROUTING_CONFIG.get("skill_max_per_subtask", 2))
    if max_skills <= 0:
        return SkillDecision([], "disabled", "skill max is 0", [], [], [])

    all_names = {profile.name for profile in profiles}
    hint_items = _skill_hint_items(skill_hint)
    hint_valid = [name for name in hint_items if name in all_names]
    hint_invalid = [name for name in hint_items if name not in all_names]

    if hint_valid and bool(ROUTING_CONFIG.get("skill_hint_override", True)):
        selected = hint_valid[:max_skills]
        return SkillDecision(
            selected_skills=selected,
            source="hint",
            reason="manual skill_hint override",
            candidates=[{"name": name, "score": 999, "description": "hint"} for name in selected],
            hint_used=selected,
            hint_invalid=hint_invalid,
        )

    rule_candidates = _rule_select_skills(
        subtask or task,
        agent_name,
        max_candidates=int(ROUTING_CONFIG.get("skill_rule_max_candidates", 8)),
    )
    if not rule_candidates and bool(ROUTING_CONFIG.get("skill_llm_rerank", True)):
        semantic_pool = _all_skill_candidates(int(ROUTING_CONFIG.get("skill_rule_max_candidates", 8)))
        timeout_s = float(ROUTING_CONFIG.get("skill_selector_timeout_s", 20.0))
        try:
            llm_selected, llm_reason = await asyncio.wait_for(
                _llm_rerank_skills(
                    task=task,
                    subtask=subtask,
                    agent_name=agent_name,
                    candidates=semantic_pool,
                    max_skills=max_skills,
                ),
                timeout=timeout_s,
            )
            if llm_selected:
                return SkillDecision(
                    selected_skills=llm_selected,
                    source="llm_global",
                    reason=llm_reason or "llm selected from global pool",
                    candidates=semantic_pool,
                    hint_used=hint_valid[:max_skills],
                    hint_invalid=hint_invalid,
                )
        except asyncio.TimeoutError:
            return SkillDecision([], "no_match", "rule empty and llm global timeout", semantic_pool, hint_valid[:max_skills], hint_invalid)
        except Exception:
            logger.exception("orchestrator.skill.global_rerank.error strategy=%s", strategy)
            return SkillDecision([], "no_match", "rule empty and llm global failed", semantic_pool, hint_valid[:max_skills], hint_invalid)

    if not rule_candidates:
        return SkillDecision([], "no_match", "rule recall returned empty", [], hint_valid[:max_skills], hint_invalid)

    selected = [str(item.get("name") or "").strip().lower() for item in rule_candidates[:max_skills]]
    selected = [name for name in selected if name]
    source = "rule"
    reason = "rule ranked"

    if bool(ROUTING_CONFIG.get("skill_llm_rerank", True)) and len(rule_candidates) > 1:
        timeout_s = float(ROUTING_CONFIG.get("skill_selector_timeout_s", 20.0))
        try:
            llm_selected, llm_reason = await asyncio.wait_for(
                _llm_rerank_skills(
                    task=task,
                    subtask=subtask,
                    agent_name=agent_name,
                    candidates=rule_candidates,
                    max_skills=max_skills,
                ),
                timeout=timeout_s,
            )
            if llm_selected:
                selected = llm_selected
                source = "llm_rerank"
                reason = llm_reason or "llm reranked"
            else:
                reason = f"llm fallback: {llm_reason or 'empty'}"
        except asyncio.TimeoutError:
            reason = "llm rerank timeout -> rule fallback"
        except Exception:
            logger.exception("orchestrator.skill.rerank.error strategy=%s", strategy)
            reason = "llm rerank error -> rule fallback"

    return SkillDecision(
        selected_skills=selected[:max_skills],
        source=source,
        reason=reason,
        candidates=rule_candidates,
        hint_used=hint_valid[:max_skills],
        hint_invalid=hint_invalid,
    )


@lru_cache(maxsize=1)
def _available_agent_names() -> tuple[str, ...]:
    """读取当前可用 Agent 名称集合。"""
    profiles = get_agent_capability_descriptions() or {}
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
    """返回默认 Agent 名称。"""
    names = list(_available_agent_names())
    if "worker" in names:
        return "worker"
    return names[0]


def _normalize_agent_name(
    raw: str,
    allowed_agents: set[str] | None = None,
    default_agent: str | None = None,
) -> str:
    """标准化并校验 Agent 名称。"""
    allowed = allowed_agents or set(_available_agent_names())
    fallback = default_agent or _default_agent_name()
    value = (raw or "").strip().lower()
    alias_map = {
        "research": "worker",
        "researcher": "worker",
    }
    value = alias_map.get(value, value)
    return value if value in allowed else fallback


def _planner_allowed_agents(decision: RouteDecision) -> list[str]:
    """根据路由结果限制规划器可选 Agent。"""
    if decision.target_agents:
        return list(dict.fromkeys([name for name in decision.target_agents if name]))
    return list(_available_agent_names())


def _normalize_planner_agent(
    raw: str,
    allowed_agents: list[str],
    default_agent: str,
) -> str:
    """标准化规划器输出的 Agent 字段。"""
    allowed_set = set(allowed_agents)
    return _normalize_agent_name(
        raw,
        allowed_agents=allowed_set,
        default_agent=default_agent if default_agent in allowed_set else allowed_agents[0],
    )


def _rule_route(task: str) -> RouteDecision:
    """基于启发式规则做快速路由决策。"""
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
    }
    steward_keys = {
        "微信",
        "手机",
        "日历",
        "提醒",
        "微博",
        "携程",
        "淘宝",
        "饿了么",
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
        target_agents=[_default_agent_name()],
        reason=f"规则路由默认回退到 {_default_agent_name()}",
        confidence=0.55,
        plan_required=plan_required,
        strategy="rule_fallback",
    )


def _compact_agent_profiles_for_route(
    profiles: Any,
    max_desc_chars: int,
) -> list[dict[str, str]]:
    """压缩 Agent 能力画像，减少路由提示长度。"""
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
    """压缩任务文本并控制长度。"""
    text = re.sub(r"\s+", " ", (task or "").strip())
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


async def _llm_route(task: str, strategy: str) -> RouteDecision:
    """调用 Router Agent 生成路由决策。"""
    profiles = get_agent_capability_descriptions()
    available_agents = list(_available_agent_names())
    route_default_agent = _default_agent_name()
    task_for_prompt = _compact_task_for_route(
        task,
        int(ROUTING_CONFIG.get("route_task_max_chars", 320)),
    )
    profile_brief = _compact_agent_profiles_for_route(
        profiles,
        int(ROUTING_CONFIG.get("route_profile_desc_max_chars", 100)),
    )
    prompt = (
        "你是任务路由器，请快速选择最合适的 agent。\n"
        "候选 Agent(精简版):\n"
        f"{json.dumps(profile_brief, ensure_ascii=False, separators=(",", ":"))}\n\n"
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
    agent = create_router_agent()
    logger.info(
        _highlight_log(
            "orchestrator.route.request start=1 strategy="
            + strategy
            + " prompt=\n"
            + prompt,
            ANSI_CYAN,
        )
    )

    response = await agent(Msg(name="User", content=prompt, role="user"))
    logger.info(_highlight_log("orchestrator.route.response.received strategy=" + strategy, ANSI_GREEN))

    text = _extract_response_text(response)
    logger.info("orchestrator.route.response strategy=%s response=\n%s", strategy, text)
    logger.info(
        _highlight_log(
            "orchestrator.route.response.full strategy=" + strategy + " response=\n" + text,
            ANSI_GREEN,
        )
    )
    parsed = _parse_json_object(text)
    if not parsed:
        return _rule_route(task)

    targets = parsed.get("target_agents")
    if not isinstance(targets, list) or not targets:
        return _rule_route(task)

    allowed = set(available_agents)
    normalized: list[str] = []
    for item in targets:
        normalized.append(
            _normalize_agent_name(
                str(item),
                allowed_agents=allowed,
                default_agent=route_default_agent,
            )
        )
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
    """当模式显式指定 legacy 时返回固定路由。"""
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode == "worker":
        return RouteDecision(["worker"], "legacy mode=worker", 1.0, False, "legacy")
    if normalized_mode in {"steward", "auto"}:
        return RouteDecision(["steward"], f"legacy mode={normalized_mode}", 1.0, False, "legacy")
    return None


def _split_task_by_connectors(task: str) -> list[str]:
    """按连接词粗粒度拆分子任务片段。"""
    raw_parts = re.split(r"(?:并且|然后|再|同时|;|；|\n)", task)
    parts = [part.strip() for part in raw_parts if part and part.strip()]
    return parts or [task.strip()]


def _subtask_agent_by_rule(subtask: str) -> str:
    """按规则为子任务选择默认执行 Agent。"""
    decision = _rule_route(subtask)
    return decision.target_agents[0] if decision.target_agents else _default_agent_name()


async def _llm_plan(task: str, decision: RouteDecision, max_subtasks: int) -> list[list[dict[str, str]]]:
    """调用 Planner Agent 将任务拆解为阶段化计划。"""
    planner_allowed = _planner_allowed_agents(decision)
    default_plan_agent = planner_allowed[0] if planner_allowed else _default_agent_name()
    prompt = (
        "你是任务规划器。请把用户任务拆成可执行阶段，并且**快速**做出相应。\n"
        "如果是涉及手机类应用（例如微博，微信，饿了么等），请按照不同的应用拆分任务。如果任务只涉及一个应用，则无需拆分。\n"
        "输出严格 JSON，格式为:\n"
        '{"stages":[[{"agent":"agent_name","task":"..."}]]}\n\n'
        "规则:\n"
        "1) stages 是二维数组，外层表示阶段（串行），内层表示同阶段并行子任务。\n"
        f"2) agent 只能从以下集合中选择: {planner_allowed}。\n"
        "3) 子任务总数不超过 max_subtasks。\n"
        "4) 若任务简单，可只给一个子任务。\n\n"
        f"max_subtasks={max_subtasks}\n"
        f"router_decision={json.dumps(decision.__dict__, ensure_ascii=False)}\n"
        f"task={task}"
    )
    planner = create_planner_agent()
    logger.info(
        _highlight_log(
            "orchestrator.planner.request start=1 max_subtasks="
            + str(max_subtasks)
            + " prompt=\n"
            + prompt,
            ANSI_YELLOW,
        )
    )
    response = await planner(Msg(name="User", content=prompt, role="user"))
    response_text = _extract_response_text(response)
    logger.info(
        _highlight_log(
            "orchestrator.planner.response done=1 response=\n" + response_text,
            ANSI_GREEN,
        )
    )
    parsed = _parse_json_object(response_text)
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
    """当规划失败时生成规则回退计划。"""
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


def _build_agent(agent_name: str, skill_context: str | None = None):
    """按名称构建对应执行 Agent。"""
    normalized = (agent_name or "").strip().lower()
    factory = getattr(agents_module, f"create_{normalized}_agent", None)
    if callable(factory):
        try:
            return factory(skill_context=skill_context)
        except TypeError:
            return factory()

    fallback = _default_agent_name()
    if normalized != fallback:
        logger.warning(
            "orchestrator.agent.unknown agent=%s; fallback=%s",
            normalized,
            fallback,
        )
    fallback_factory = getattr(agents_module, f"create_{fallback}_agent", None)
    if callable(fallback_factory):
        return fallback_factory()

    # Defensive fallback to keep runtime compatible if registry and factories drift.
    if fallback == "worker":
        return create_worker_agent(skill_context=skill_context)
    return create_steward_agent(skill_context=skill_context)


async def _run_one_agent(
    agent_name: str,
    task: str,
    output_path: str | None = None,
    selected_skills: list[str] | None = None,
    prior_context: str | None = None,
) -> dict[str, Any]:
    """执行单个子任务并返回结构化结果。"""
    skill_list = selected_skills or []
    skill_context = _skill_prompt_context(skill_list)
    agent = _build_agent(agent_name, skill_context=skill_context)
    msg_content = task.strip()
    if prior_context:
        msg_content = (
            "请在执行当前子任务时参考以下前序上下文（结果与文件），并据此衔接后续工作。\n\n"
            + prior_context
            + "\n\n"
            + msg_content
        )
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
        "skills": skill_list,
        "reply": _extract_response_text(response),
        "elapsed_ms": elapsed_ms,
    }


def _aggregate_replies(executions: list[dict[str, Any]]) -> str:
    """聚合多子任务回复文本为最终回复。"""
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
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    """执行完整的多智能体编排任务。

    功能描述：
        对输入任务执行“路由决策 -> 任务规划 -> 子任务执行 -> 结果聚合”，并返回统一结构化结果。
    参数说明：
        task: 用户原始任务文本。
        output_path: 可选输出文件路径提示。
        mode: 执行模式（router/intelligent 或 legacy 模式）。
        agent_hint: 可选强制 Agent 提示。
        skill_hint: 可选技能提示（支持逗号分隔）。
        routing_strategy: 可选路由策略覆盖值。
        context_id: 预留的多轮上下文标识。
    返回值说明：
        dict[str, Any]: 含最终回复、路由轨迹、执行明细与文件信息。
    """
    del context_id  # Reserved for future multi-turn persistence identifier.

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
            timeout_default_agent = "worker" if "worker" in _available_agent_names() else _default_agent_name()
            decision = RouteDecision(
                target_agents=[timeout_default_agent],
                reason=f"router timeout -> default {timeout_default_agent}",
                confidence=0.0,
                plan_required=False,
                strategy="timeout_default_worker",
            )
            route_control_path = "router_timeout_worker"
            logger.warning(
                "orchestrator.route.timeout timeout_s=%.2f; fallback=%s",
                router_timeout_s,
                timeout_default_agent,
            )
        except Exception:
            decision = _rule_route(task)
            route_control_path = "router_error_fallback"
            logger.exception("orchestrator.route.error fallback=rule")
    else:
        decision = _rule_route(task)
        route_control_path = "rule"

    max_subtasks = int(ROUTING_CONFIG["max_subtasks"])
    planner_allowed_agents = _planner_allowed_agents(decision)
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
            timeout_default_agent = "worker" if "worker" in _available_agent_names() else _default_agent_name()
            stages = [[{"agent": timeout_default_agent, "task": task.strip()}]]
            plan_source = "timeout_worker"
            plan_control_path = "planner_timeout_worker"
            logger.warning(
                "orchestrator.plan.timeout timeout_s=%.2f; fallback=%s",
                planner_timeout_s,
                timeout_default_agent,
            )
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
    shared_file_paths: list[Path] = []
    stage_traces: list[dict[str, Any]] = []
    skill_trace_records: list[dict[str, Any]] = []

    for stage_index, stage in enumerate(stages, start=1):
        stage_start = time.perf_counter()
        logger.info(
            "orchestrator.stage.start stage=%d subtasks=%d parallel=%s",
            stage_index,
            len(stage),
            len(stage) > 1,
        )
        stage_execs: list[dict[str, Any]] = []
        for sub_index, item in enumerate(stage, start=1):
            is_last = stage_index == len(stages) and sub_index == len(stage)
            hint_path = output_path if is_last else None
            skill_decision = await _select_skills_for_subtask(
                task=task,
                subtask=item["task"],
                agent_name=item["agent"],
                strategy=strategy,
                skill_hint=skill_hint,
            )
            skill_trace_records.append(
                {
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
                }
            )
            selected_skill_text = ", ".join(skill_decision.selected_skills) if skill_decision.selected_skills else "(none)"
            highlight_color = ANSI_GREEN if skill_decision.selected_skills else ANSI_RED
            logger.info(
                _highlight_log(
                    "orchestrator.skill.final stage="
                    + str(stage_index)
                    + " subtask="
                    + str(sub_index)
                    + " agent="
                    + str(item["agent"])
                    + " selected=["
                    + selected_skill_text
                    + "] source="
                    + str(skill_decision.source)
                    + " reason="
                    + str(skill_decision.reason),
                    highlight_color,
                )
            )
            prior_context = _build_upstream_context(
                executions=executions,
                file_paths=shared_file_paths,
                max_chars=int(ROUTING_CONFIG.get("upstream_context_max_chars", 4000)),
                max_steps=int(ROUTING_CONFIG.get("upstream_context_max_steps", 20)),
            )

            try:
                result = await asyncio.wait_for(
                    _run_one_agent(
                        item["agent"],
                        item["task"],
                        output_path=hint_path,
                        selected_skills=skill_decision.selected_skills,
                        prior_context=prior_context,
                    ),
                    timeout=subtask_timeout_s,
                )
            except Exception as exc:
                if isinstance(exc, asyncio.TimeoutError):
                    error_text = f"subtask timeout>{subtask_timeout_s:.2f}s"
                else:
                    error_text = str(exc)
                result = {
                    "agent": item["agent"],
                    "task": item["task"],
                    "skills": skill_decision.selected_skills,
                    "reply": f"subtask failed: {error_text}",
                    "elapsed_ms": 0,
                    "error": error_text,
                }

            stage_execs.append(result)
            executions.append(result)
            shared_file_paths = _merge_file_paths(
                shared_file_paths,
                _collect_file_paths(str(result.get("reply") or ""), output_path=hint_path),
            )

        stage_elapsed_ms = int((time.perf_counter() - stage_start) * 1000)
        stage_traces.append(
            {
                "stage": stage_index,
                "parallel": False,
                "execution_mode": "sequential_with_context",
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
    file_paths = _merge_file_paths(
        shared_file_paths,
        _collect_file_paths(reply, output_path=output_path),
    )
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
