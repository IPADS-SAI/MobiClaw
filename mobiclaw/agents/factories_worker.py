# -*- coding: utf-8 -*-
"""Worker 工厂。"""

from __future__ import annotations

import functools
from typing import Any

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

from ..config import MEMORY_CONFIG, RAG_CONFIG, SCHEDULE_CONFIG, TOOL_CONFIG
from ..mcp import get_mcp_manager
from .common import _build_memory_prompt, _build_skill_prompt_suffix, create_openai_model, register_tool_with_timeout
from ..tools import (
    arxiv_search,
    brave_search,
    create_docx_from_text,
    create_pdf_from_text,
    create_pptx_from_outline,
    dblp_conference_search,
    download_file,
    edit_docx,
    edit_pptx,
    extract_image_text_ocr,
    extract_pdf_text,
    fetch_feishu_chat_history,
    fetch_url_links,
    fetch_url_readable_text,
    fetch_url_text,
    get_feishu_message,
    insert_pptx_image,
    read_docx_text,
    read_pptx_summary,
    read_xlsx_summary,
    run_shell_command,
    run_skill_script,
    schedule_feishu_meeting,
    search_steward_knowledge,
    search_task_history,
    send_feishu_meeting_card,
    set_pptx_text_style,
    update_long_term_memory,
    write_text_file,
    write_xlsx_from_records,
    write_xlsx_from_rows,
)


def create_worker_agent(
    skill_context: str | None = None,
    job_context: dict[str, Any] | None = None,
) -> ReActAgent:
    """创建 Worker Agent，用于子任务委派。"""
    toolkit = Toolkit()
    tool_timeout_s = TOOL_CONFIG["timeout_s"]
    _reg = functools.partial(register_tool_with_timeout, toolkit, tool_timeout_s)

    _reg(run_shell_command, func_description="运行受限的本地命令行工具（白名单约束）。")

    _reg(
        run_skill_script,
        func_description=(
            "在指定 execution_dir 中执行skill中定义的命令。"
            "调用时请传入完整可执行命令字符串和执行目录。"
        ),
    )

    _reg(brave_search, func_description="通过 Brave Search API 联网检索新闻与网页来源链接。")
    _reg(arxiv_search, func_description="查询 arXiv API 获取论文元数据、摘要与 PDF 链接。")
    _reg(dblp_conference_search, func_description="检索会议论文清单与链接（DBLP），用于按年份与关键词筛选。")
    _reg(fetch_url_text, func_description="抓取指定 URL 的文本内容用于快速检索。")
    _reg(fetch_url_readable_text, func_description="抓取并提取网页可读文本，用于快速理解页面内容。")
    _reg(fetch_url_links, func_description="抓取网页并提取链接，用于发现相关来源并继续检索。")
    _reg(download_file, func_description="下载 URL 文件到本地路径（支持二进制，例如 PDF）。")
    _reg(extract_pdf_text, func_description="从本地 PDF 文件中提取文本内容。")
    _reg(extract_image_text_ocr, func_description="从本地图片文件中执行 OCR 识别，提取文字内容。")
    _reg(read_docx_text, func_description="读取 DOCX 文档文本内容。")
    _reg(create_docx_from_text, func_description="从纯文本生成 DOCX 文档。")
    _reg(edit_docx, func_description="对 DOCX 文档进行查找替换、追加段落或插入表格。")
    _reg(create_pdf_from_text, func_description="从纯文本生成 PDF 文档。")
    _reg(read_xlsx_summary, func_description="读取 XLSX 工作簿摘要与预览。")
    _reg(write_xlsx_from_records, func_description="从记录列表生成 XLSX 文件。")
    _reg(write_xlsx_from_rows, func_description="从行数据生成 XLSX 文件。")
    _reg(write_text_file, func_description="写入本地文本文件，用于保存结果或日志。")
    _reg(search_steward_knowledge, func_description="检索本地知识库中已存储的信息（由智能管家从手机中提取并存储）。")

    _reg(
        fetch_feishu_chat_history,
        func_description=(
            "读取飞书会话历史消息列表。"
            "chat_id 必须传真实会话/用户 ID（如 oc_... 或 ou_...），不能传 auto；"
            "container_id_type 可选 auto/chat/user，默认 auto；"
            "history_range 可选 today/yesterday/7d/all，默认 today；"
            "当 history_range=today 且消息少于 10 条时，会自动向更早消息补齐到最多 10 条；"
            "page_token 可用于非 today 查询的续页。"
        ),
    )

    _reg(get_feishu_message, func_description="按消息 ID 获取飞书消息详情，用于排查和精确分析。")

    _reg(
        schedule_feishu_meeting,
        func_description=(
            "按显式参数预约飞书会议，返回会议链接、会议号、密码等结构化信息。"
            "start_time 必须为 YYYY-MM-DD HH:MM。"
        ),
    )

    _reg(
        send_feishu_meeting_card,
        func_description=(
            "将已创建的会议信息以 interactive 卡片发送到飞书会话。"
            "群聊请传 receive_id_type=chat_id 且 receive_id=chat_id。"
        ),
    )

    _reg(
        read_pptx_summary,
        func_description="读取 PPTX/PPT 文件，返回每张幻灯片的标题、正文文本、备注、形状数量和图片数量的结构化摘要。",
    )

    _reg(
        create_pptx_from_outline,
        func_description=(
            "从幻灯片大纲列表创建新 PPTX 文件。"
            "每张幻灯片支持：标题、正文（字符串或列表）、演讲者备注、布局索引、"
            "嵌入图片（路径+位置+尺寸）、字号、字体颜色（#RRGGBB）、粗体、斜体。"
            "支持可选模板文件与全局默认字体大小/颜色。"
        ),
    )

    _reg(
        edit_pptx,
        func_description=(
            "综合编辑已有 PPTX：跨所有幻灯片全局文本替换、追加新幻灯片、"
            "按 1-based 索引删除幻灯片。三种操作可在一次调用中组合使用。"
        ),
    )

    _reg(
        insert_pptx_image,
        func_description=(
            "向指定幻灯片（1-based 索引）插入本地图片。"
            "支持英寸单位的定位（left/top）和尺寸（width/height），省略宽高时保持原始比例。"
        ),
    )

    _reg(
        set_pptx_text_style,
        func_description=(
            "在指定幻灯片中搜索文本子串，对所有匹配的 run 应用字体样式："
            "字号（pt）、颜色（#RRGGBB）、粗体、斜体、下划线。省略的属性保持原样。"
        ),
    )

    sys_prompt = """你是 MobiClaw 的 Worker Agent，负责处理通用问题与单一子任务。

工作准则：
- 只聚焦当前任务（如果当前是一个子任务，只聚焦于子任务），给出简明直接的结果。
- 必要时使用工具检索或执行本地命令。
- 如果提供了相应的skill，请优先使用skill中指定的工具和方法，运行skill中的脚本，请务必使用 "run_skill_script"，而非"run_shell_command"。
- 使用 "run_skill_script" 时，必须提供 command 和 execution_dir；优先使用 Activated Skills 中给出的 execution_dir。
- 如果需要联网搜索新闻或网页来源，优先使用 "brave_search" 获取新闻/web内容。
- 如果检索学术论文，优先使用 "arxiv_search" 获取元数据与 PDF 链接。
- 如果检索会议论文，优先使用 "dblp_conference_search" 获取论文清单与链接，然后去arxiv上搜索对应的论文。
- 如果已经下载过论文/文件了，优先通过临时文件目录，查找之前下载的文件，避免重复上网搜索和下载。
- 如果已经搜索过论文/文件了，优先通过之前的历史对话获取信息，避免重复联网检索。
- 如果任务中有今天，明天等相对日期的描述，你可以通过shell中的date命令，获取具体的日期。
- 拿到候选链接后，优先使用 "fetch_url_readable_text" 抓取正文；需要原始 HTML 时再使用 "fetch_url_text"。
- 需要从网页中发现相关链接时使用 "fetch_url_links"，再逐条抓取与筛选。
- 需要下载论文或附件时使用 "download_file"；阅读 PDF 用 "extract_pdf_text"。
- 需要识别图片中的文字时使用 "extract_image_text_ocr"。
- 处理 Word/Excel/PDF 文档时，使用 docx/xlsx/pdf 相关工具完成读取或生成。
- 需要输出文件时，可用 "write_text_file" 落盘。
- 如果用户询问之前智能管家从手机中提取并存储的知识，使用 "search_steward_knowledge" 检索。
- 如果用户要求总结飞书群聊历史或按消息 ID 查询，请使用飞书历史消息工具。
- 如果用户要求“预约飞书会议/创建会议链接并发群”，先调用 "schedule_feishu_meeting" 创建会议，再调用 "send_feishu_meeting_card" 发送卡片。
- 如果消息中包含 [Feishu Context]，调用飞书历史工具时必须优先使用其中的 chat_id/open_id/message_id，不得猜测或改写。
- 如果消息中包含 [Feishu Context] 且要发会议卡片，必须优先使用其中 chat_id 作为 receive_id。
- 若 [Feishu Context] 缺少必需 ID，应先明确指出缺失项并向用户索取，不要编造参数。
- 输出格式遵循用户要求；未指定时默认使用 Markdown。
- 必须输出最终文本结论或可执行结果；不要输出空的工具调用。
- 即使已经把结果写入文件，也必须在当前回复中给出完整结论（至少包含关键结论与主要依据）；禁止只回复“已落盘+文件路径”。
- 不做多步长对话，输出最终结论或可执行结果。
- 如果工具调用返回 "[Tool Timeout]" 或 "[Tool Error]"，说明该工具执行超时或出错。此时你可以：
  (1) 尝试换一个替代方案或工具重试；
  (2) 如果没有替代方案或多次失败，应立即结束任务，向用户清楚说明失败原因（哪个工具、什么错误、影响了什么），不要无限重试。
"""
    if RAG_CONFIG["task_history_enabled"]:
        _reg(search_task_history, func_description="检索历史任务执行记录和相关文件，用于回答关于之前做过的任务的问题。")
        sys_prompt += "- 如果用户询问之前做过的任务，使用 \"search_task_history\" 检索历史记录。\n"

    if MEMORY_CONFIG["enabled"]:
        _reg(
            update_long_term_memory,
            func_description=(
                "更新长期记忆文件（MEMORY.md）。传入完整新内容，覆盖写入。"
                "用于记录用户偏好、事实信息、回答风格等需要跨会话保留的凝练信息。"
            ),
        )
        sys_prompt += (
            "- 你拥有 \"update_long_term_memory\" 工具，可更新长期记忆。"
            "当用户明确要求记住某些偏好或信息时，先读取现有记忆内容，合并后写回。\n"
        )

    if SCHEDULE_CONFIG["enabled"]:
        from ..tools.schedule import list_scheduled_tasks, cancel_scheduled_task, create_scheduled_task

        bound_create = functools.partial(create_scheduled_task, bound_job_context=job_context or {})
        bound_create.__name__ = "create_scheduled_task"
        bound_create.__doc__ = create_scheduled_task.__doc__
        _reg(
            bound_create,
            func_description='创建定时任务。传入 task（核心任务描述，去除时间信息）和 time_description（自然语言时间描述，如"每天早上8点"（周期任务）、"下午2点10分"（单次任务））。系统会自动解析时间并创建定时调度。',
        )
        _reg(list_scheduled_tasks, func_description="列出所有定时任务及其状态信息（schedule_id、任务内容、状态、调度类型、描述、cron 表达式等）。")
        _reg(cancel_scheduled_task, func_description="取消指定的定时任务。需要提供 schedule_id，可先通过 list_scheduled_tasks 查询。")
        sys_prompt += (
            '- 如果用户想创建定时任务（如"每天帮我搜新闻"），使用 "create_scheduled_task" 创建，传入核心任务和时间描述。\n'
            "- 如果用户想查看或取消定时任务，使用 \"list_scheduled_tasks\" 查看列表，使用 \"cancel_scheduled_task\" 取消指定任务。\n"
        )

    manager = get_mcp_manager()
    if manager is not None:
        manager.register_tools_with_timeout(toolkit, tool_timeout_s)
        mcp_tool_names = manager.get_tool_names()
        if mcp_tool_names:
            sys_prompt += (
                "- 你还拥有以下通过 MCP 服务器注册的外部工具，可按需调用：" + ", ".join(mcp_tool_names) + "\n"
            )

    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Worker",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=20,
    )
