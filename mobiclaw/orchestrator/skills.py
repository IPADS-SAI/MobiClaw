# -*- coding: utf-8 -*-
"""orchestrator 的技能选择逻辑。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from .types import ANSI_GREEN, ANSI_YELLOW, SkillDecision, SkillProfile, _highlight_log
from .utils import _extract_response_text, _parse_json_object
from ..agents import create_skill_selector_agent
from ..config import ROUTING_CONFIG

logger = logging.getLogger("mobiclaw.orchestrator")


def _skills_root() -> Path:
    """确定技能目录根路径（优先使用环境配置）。"""
    configured = str(ROUTING_CONFIG.get("skill_root_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "skills"


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
            raw = skill_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning(
                "orchestrator.skill.read_failed path=%s error=%s",
                str(skill_file),
                str(exc),
            )
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


def _load_skill_content_direct(name: str) -> tuple[str, str]:
    """从技能目录直接读取技能文档，绕过画像缓存做兜底。"""
    skill_name = (name or "").strip().lower()
    if not skill_name:
        return "", ""
    skill_file = _skills_root() / skill_name / "SKILL.md"
    if not skill_file.exists() or not skill_file.is_file():
        return "", ""
    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning(
            "orchestrator.skill.direct_read_failed name=%s path=%s error=%s",
            skill_name,
            str(skill_file),
            str(exc),
        )
        return "", ""
    meta = _parse_skill_frontmatter(raw)
    description = str(meta.get("description") or "").strip() or _skill_content_hint(raw)
    return raw, description


def _collect_skill_markdown_pairs(skill_dir: str, primary_content: str) -> list[tuple[str, str]]:
    """Collect markdown files under skill directory as (filename, content) pairs."""
    base = (skill_dir or "").strip()
    if not base:
        fallback = (primary_content or "").strip()
        return [("SKILL.md", fallback)] if fallback else []

    root = Path(base)
    if not root.exists() or not root.is_dir():
        fallback = (primary_content or "").strip()
        return [("SKILL.md", fallback)] if fallback else []

    md_files = [p for p in root.rglob("*.md") if p.is_file()]

    # Keep SKILL.md first, then sort the rest for deterministic prompts.
    def _sort_key(path: Path) -> tuple[int, str]:
        rel = str(path.relative_to(root)).replace("\\", "/")
        return (0 if rel.lower() == "skill.md" else 1, rel.lower())

    pairs: list[tuple[str, str]] = []
    for path in sorted(md_files, key=_sort_key):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception as exc:
            logger.warning(
                "orchestrator.skill.md_read_failed path=%s error=%s",
                str(path),
                str(exc),
            )
            continue
        if text:
            pairs.append((rel, text))

    if not pairs:
        fallback = (primary_content or "").strip()
        if fallback:
            pairs.append(("SKILL.md", fallback))
    return pairs


def _format_skill_markdown_pairs(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    blocks: list[str] = []
    for filename, text in pairs:
        blocks.append(f"[Skill File: {filename}]\n{text}")
    return "\n\n".join(blocks)


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
        content = ""
        skill_dir = ""
        skill_name = str(name or "").strip().lower()

        if profile:
            content = (profile.full_content or "").strip()
            if not content:
                content = (profile.description or profile.content_hint or "").strip()
            skill_dir = profile.skill_dir

        # 兜底：缓存缺失或内容为空时，直接按名字读取技能文件。
        if not content:
            raw, fallback_desc = _load_skill_content_direct(skill_name)
            content = raw or fallback_desc
            if not skill_dir:
                skill_dir = str((_skills_root() / skill_name).resolve())

        if not content:
            logger.warning(
                "orchestrator.skill.context_missing name=%s selected=%s",
                skill_name,
                selected_skills,
            )
            continue
        markdown_pairs = _collect_skill_markdown_pairs(skill_dir, content)
        merged_content = _format_skill_markdown_pairs(markdown_pairs) or content

        blocks.append(
            "\n".join(
                [
                    f"[Skill: {skill_name}]",
                    f"execution_dir (just used in run_skill_script function): {skill_dir}",
                    merged_content,
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
