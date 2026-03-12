# -*- coding: utf-8 -*-
"""Seneschal 工作流编排入口。

适用场景：
- CLI 演示模式与交互模式；
- 网关任务转发到编排层；
- 日常任务（daily）触发执行。

依赖模块：
- `seneschal.agents`：创建用户/执行 Agent；
- `seneschal.orchestrator`：多 Agent 智能路由与执行；
- `seneschal.dailytasks.runner`：定时/触发型每日任务流程。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from .agents import create_steward_agent, create_user_agent, create_worker_agent
from .dailytasks.runner import run_daily_tasks
from .orchestrator import run_orchestrated_task

logger = logging.getLogger(__name__)


def _extract_response_text(response: Any) -> str:
    """提取 Agent 返回对象中的纯文本内容。

    参数说明：
        response: Agent 返回对象，兼容 `get_text_content` 或 `content` 块结构。
    返回值说明：
        str: 提取后的文本，若无可用文本则返回空字符串。
    """
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
    """从回复文本与输出路径提示中收集文件路径并去重。"""
    paths: list[Path] = []
    if output_path:
        paths.append(Path(output_path).expanduser())

    for raw in re.findall(r"\[(?:File|Download)\]\s+Wrote:\s*(.+)", text or ""):
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
    """将文件路径列表转换为可序列化的文件元数据结构。"""
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


async def run_gateway_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
    external_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """通过编排器执行网关任务。

    功能描述：
        将网关层请求透传至 `run_orchestrated_task`，统一走多 Agent 路由与执行流程。
    参数说明：
        task: 用户任务文本。
        output_path: 可选输出路径提示。
        mode: 执行模式（默认 router）。
        agent_hint: 可选 Agent 选择提示。
        skill_hint: 可选技能提示。
        routing_strategy: 可选路由策略覆盖值。
        context_id: 可选上下文 ID（用于多轮扩展）。
        external_context: 可选外部上下文（如飞书事件 ID）。
    返回值说明：
        dict[str, Any]: 编排执行结果。
    """
    logger.info("workflows.run_gateway_task mode=%s agent_hint=%s task_preview=%s",
                mode, agent_hint or "", (task or "")[:120].replace("\n", " "))
    return await run_orchestrated_task(
        task=task,
        output_path=output_path,
        mode=mode,
        agent_hint=agent_hint,
        skill_hint=skill_hint,
        routing_strategy=routing_strategy,
        context_id=context_id,
        external_context=external_context,
    )


async def run_demo_conversation() -> None:
    """运行演示对话流程。"""
    print("=" * 70)
    print("Seneschal 个人数据管家智能体系统")
    print("=" * 70)
    print()

    steward = create_steward_agent()
    user = create_user_agent()

    print("✓ 智能管家 Agent (Steward) 已创建")
    print("✓ 用户代理 Agent (User) 已创建")
    print()
    print("-" * 70)
    print("开始演示对话...")
    print("-" * 70)
    print()

    preset_message = "开始今日的数据整理和分析，给出最近的待办事项。"
    print(f"[User]: {preset_message}")
    print()

    msg = Msg(
        name="User",
        content=preset_message,
        role="user",
    )

    print("[Steward 正在思考和执行...]")
    print("-" * 70)

    try:
        response = await steward(msg)

        print("-" * 70)
        print("[Steward 回复]:")
        print(response.get_text_content() if response else "（无回复）")
        print("-" * 70)

    except Exception as e:
        print(f"[错误] Agent 执行出错: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 70)
    print("演示结束")
    print("=" * 70)


async def run_interactive_mode() -> None:
    """运行交互式对话模式。"""
    print("=" * 70)
    print("Seneschal 个人数据管家智能体系统 - 交互模式")
    print("=" * 70)
    print()
    print("提示: 输入 'exit' 或 'quit' 退出对话")
    print()

    steward = create_steward_agent()
    user = create_user_agent()

    print("✓ 系统初始化完成")
    print("-" * 70)
    print()

    while True:
        try:
            msg = await user(None)

            user_text = msg.get_text_content() if msg else ""
            if user_text.lower() in ["exit", "quit", "退出"]:
                print("再见！")
                break

            print()
            await steward(msg)

            print()
            print("-" * 70)
            print()

        except KeyboardInterrupt:
            print("\n用户中断，退出程序。")
            break
        except Exception as e:
            print(f"[错误] {e}")
            continue


async def run_agent_task(
    task: str,
    output_path: str | None = None,
    mode: str = "router",
    agent_hint: str | None = None,
    skill_hint: str | None = None,
    routing_strategy: str | None = None,
    context_id: str | None = None,
) -> None:
    """运行通用 Agent 任务，默认使用智能路由多智能体编排。"""
    print("=" * 70)
    print("Seneschal Agent Task")
    print("=" * 70)
    print()

    print("[Orchestrator 正在路由与执行...]")
    print("-" * 70)
    logger.info("workflows.run_agent_task mode=%s agent_hint=%s task_preview=%s",
                mode, agent_hint or "", (task or "")[:120].replace("\n", " "))

    try:
        result = await run_orchestrated_task(
            task=task,
            output_path=output_path,
            mode=mode,
            agent_hint=agent_hint,
            skill_hint=skill_hint,
            routing_strategy=routing_strategy,
            context_id=context_id,
        )
        print("-" * 70)
        print("[Orchestrator 回复]:")
        text = str(result.get("reply") or "")
        if not text:
            text = "（无文本回复，可能是空工具调用）"
        print(text)

        trace = result.get("routing_trace") if isinstance(result, dict) else None
        if isinstance(trace, dict):
            print("-" * 70)
            print("[Routing Trace]:")
            print(str(trace))
        print("-" * 70)
    except Exception as e:
        print(f"[错误] Agent 执行出错: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 70)


async def main() -> None:
    """主入口函数。"""
    import argparse

    parser = argparse.ArgumentParser(description="Seneschal workflow entrypoint")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run interactive chat mode",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run daily collection workflow",
    )
    parser.add_argument(
        "--daily-trigger",
        default="daily",
        help="Trigger name used to filter daily tasks",
    )
    parser.add_argument(
        "--agent-task",
        help="Run an agent task with tool usage",
    )
    parser.add_argument(
        "--output",
        help="Optional output path hint for agent tasks",
    )
    parser.add_argument(
        "--mode",
        default="router",
        help="Task execution mode: router/intelligent or legacy worker/steward/auto",
    )
    parser.add_argument(
        "--agent-hint",
        help="Optional forced agent hint (worker/steward)",
    )
    parser.add_argument(
        "--skill-hint",
        help="Optional skill hint (single skill or comma separated skill names)",
    )
    parser.add_argument(
        "--routing-strategy",
        help="Optional routing strategy override",
    )
    parser.add_argument(
        "--context-id",
        help="Optional context id for future multi-turn orchestration",
    )
    args = parser.parse_args()

    if args.daily:
        print("=" * 70)
        print("Seneschal Daily Loop")
        print("=" * 70)
        result = await run_daily_tasks(args.daily_trigger)
        print("-" * 70)
        print(f"Run ID: {result['run_id']}")
        print(f"Tasks executed: {result['task_count']}")
        print("-" * 70)
    elif args.interactive:
        await run_interactive_mode()
    elif args.agent_task:
        await run_agent_task(
            args.agent_task,
            args.output,
            mode=args.mode,
            agent_hint=args.agent_hint,
            skill_hint=args.skill_hint,
            routing_strategy=args.routing_strategy,
            context_id=args.context_id,
        )
    else:
        await run_demo_conversation()
