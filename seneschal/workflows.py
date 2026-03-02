# -*- coding: utf-8 -*-
"""Seneschal 工作流模块。"""

from __future__ import annotations

from agentscope.message import Msg
import html
import os
import re
from pathlib import Path

from .agents import create_steward_agent, create_user_agent
from .dailytasks.runner import run_daily_tasks
from .tools import fetch_url_text, run_shell_command


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


def _tool_response_to_text(response) -> str:
    if response is None:
        return ""
    parts: list[str] = []
    for block in response.content or []:
        text = getattr(block, "text", "")
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_weibo_top10(text: str) -> list[str]:
    if not text:
        return []
    pattern = re.compile(r'<td class="td-02">\s*<a href="[^"]+"[^>]*>([^<]+)</a>')
    items = [html.unescape(item).strip() for item in pattern.findall(text)]
    cleaned = []
    for item in items:
        if not item or item == "微博热搜":
            continue
        cleaned.append(item)
        if len(cleaned) >= 10:
            break
    return cleaned


async def run_weibo_browser_terminal_workflow() -> None:
    """验证浏览器与终端工具：抓取微博热搜并生成摘要文件。"""
    print("=" * 70)
    print("Seneschal Weibo Browser + Terminal 验证")
    print("=" * 70)
    print()

    url = "https://s.weibo.com/top/summary?cate=realtimehot"
    print(f"[Web] 抓取: {url}")
    response = await fetch_url_text(url)
    text_content = _tool_response_to_text(response)

    # Remove the tool prefix line if present.
    if text_content.startswith("[Web]"):
        split_idx = text_content.find("\n")
        if split_idx != -1:
            text_content = text_content[split_idx + 1 :]

    top10 = _extract_weibo_top10(text_content)
    if not top10:
        top10 = ["未能解析微博热搜内容，请检查网络或页面结构是否变化。"]

    output_path = Path("weibo_top10_summary.md")

    allowlist = os.environ.get("SENESCHAL_SHELL_ALLOWLIST", "")
    if "touch" not in allowlist.split(","):
        allowlist = ",".join(item for item in [allowlist.strip(","), "touch"] if item)
        os.environ["SENESCHAL_SHELL_ALLOWLIST"] = allowlist

    print("[Shell] 创建摘要文件...")
    shell_result = await run_shell_command(f"touch {output_path.as_posix()}")
    print(_tool_response_to_text(shell_result))

    lines = ["# 微博热搜 Top 10", "", "数据来源: https://s.weibo.com/top/summary?cate=realtimehot", ""]
    for idx, item in enumerate(top10, start=1):
        lines.append(f"{idx}. {item}")
    summary = "\n".join(lines) + "\n"
    output_path.write_text(summary, encoding="utf-8")

    print(f"已生成: {output_path.as_posix()}")
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
        "--weibo-hot",
        action="store_true",
        help="Verify browser/terminal tools by fetching Weibo top 10",
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
    elif args.weibo_hot:
        await run_weibo_browser_terminal_workflow()
    else:
        await run_demo_conversation()
