# -*- coding: utf-8 -*-
"""Seneschal 工作流模块。"""

from __future__ import annotations

from agentscope.message import Msg

from .agents import create_steward_agent, create_user_agent
from .dailytasks.runner import run_daily_tasks


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
    else:
        await run_demo_conversation()
