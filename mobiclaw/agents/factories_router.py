# -*- coding: utf-8 -*-
"""Router / Planner / SkillSelector 工厂。"""

from __future__ import annotations

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter

from ..config import MODEL_CONFIG
from .common import create_openai_model


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
