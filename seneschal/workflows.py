# -*- coding: utf-8 -*-
"""Seneschal 工作流模块。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentscope.message import Msg

from .agents import create_steward_agent, create_user_agent, create_worker_agent
from .dailytasks.runner import run_daily_tasks


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


async def run_gateway_task(
    task: str,
    output_path: str | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    """Run a gateway task through workflow-level control flow.

    mode:
    - auto/steward: use Steward for general orchestration
    - worker: use Worker for direct tool-driven task execution
    """
    normalized_mode = (mode or "auto").strip().lower()
    if normalized_mode in {"worker"}:
        agent = create_worker_agent()
    else:
        agent = create_steward_agent()

    msg_content = (task or "").strip()
    if output_path:
        msg_content += (
            "\n\n输出文件路径: "
            + output_path
            + "\n如需落盘，请自行选择合适工具完成。"
        )

    msg = Msg(name="User", content=msg_content, role="user")
    response = await agent(msg)
    text = _extract_response_text(response)
    file_paths = _collect_file_paths(text, output_path)
    files = _build_file_entries(file_paths)

    return {
        "reply": text,
        "mode": normalized_mode,
        "files": files,
    }


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

    preset_message = "开始今日的数据整理和分析，给出最近活动的总结和待办事项。"
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


async def run_agent_task(task: str, output_path: str | None = None) -> None:
    """运行通用 Agent 任务，具体策略由 Agent 决策。"""
    print("=" * 70)
    print("Seneschal Agent Task")
    print("=" * 70)
    print()

    worker = create_worker_agent()
    msg_content = task.strip() if task else ""
    if output_path:
        msg_content += (
            "\n\n输出文件路径: "
            + output_path
            + "\n如需落盘，请自行选择合适工具完成。"
        )

    msg = Msg(
        name="User",
        content=msg_content,
        role="user",
    )

    print("[Worker 正在思考和执行...]")
    print("-" * 70)

    try:
        response = await worker(msg)
        print("-" * 70)
        print("[Worker 回复]:")
        text = response.get_text_content() if response else ""
        if not text and response is not None:
            parts = []
            for block in response.content or []:
                block_text = getattr(block, "text", "")
                if block_text:
                    parts.append(block_text)
            text = "\n".join(parts).strip()
        if not text:
            text = "（无文本回复，可能是空工具调用）"
        print(text)
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
        await run_agent_task(args.agent_task, args.output)
    else:
        await run_demo_conversation()
