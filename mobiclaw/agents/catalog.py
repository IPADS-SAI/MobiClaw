# -*- coding: utf-8 -*-
"""mobiclaw.agents 的工具目录与内建能力画像。"""

from __future__ import annotations

from typing import Any

from ..config import MEMORY_CONFIG, RAG_CONFIG, SCHEDULE_CONFIG
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
    read_feishu_docx_link,
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
    store_steward_knowledge,
    update_long_term_memory,
    write_text_file,
    write_xlsx_from_records,
    write_xlsx_from_rows,
)
from .types import AgentCapability


def _tool_catalog() -> dict[str, tuple[Any, str]]:
    """返回可供自定义 Agent 复用的工具目录。"""
    return {
        "run_shell_command": (run_shell_command, "运行受限的本地命令行工具（白名单约束）。"),
        "run_skill_script": (run_skill_script, "在指定 execution_dir 中执行 skill 脚本。"),
        "brave_search": (brave_search, "通过 Brave Search API 联网检索新闻与网页来源链接。"),
        "arxiv_search": (arxiv_search, "查询 arXiv API 获取论文元数据、摘要与 PDF 链接。"),
        "dblp_conference_search": (dblp_conference_search, "检索会议论文清单与链接（DBLP）。"),
        "fetch_url_text": (fetch_url_text, "抓取指定 URL 的文本内容用于快速检索。"),
        "fetch_url_readable_text": (fetch_url_readable_text, "抓取并提取网页可读文本。"),
        "fetch_url_links": (fetch_url_links, "抓取网页并提取链接。"),
        "download_file": (download_file, "下载 URL 文件到本地路径（支持二进制，例如 PDF）。"),
        "extract_pdf_text": (extract_pdf_text, "从本地 PDF 文件中提取文本内容。"),
        "extract_image_text_ocr": (extract_image_text_ocr, "从本地图片文件中执行 OCR 识别。"),
        "read_docx_text": (read_docx_text, "读取 DOCX 文档文本内容。"),
        "create_docx_from_text": (create_docx_from_text, "从纯文本生成 DOCX 文档。"),
        "edit_docx": (edit_docx, "对 DOCX 文档进行查找替换、追加段落或插入表格。"),
        "create_pdf_from_text": (create_pdf_from_text, "从纯文本生成 PDF 文档。"),
        "read_xlsx_summary": (read_xlsx_summary, "读取 XLSX 工作簿摘要与预览。"),
        "write_xlsx_from_records": (write_xlsx_from_records, "从记录列表生成 XLSX 文件。"),
        "write_xlsx_from_rows": (write_xlsx_from_rows, "从行数据生成 XLSX 文件。"),
        "write_text_file": (write_text_file, "写入本地文本文件。"),
        "search_task_history": (search_task_history, "检索历史任务执行记录和相关文档。"),
        "search_steward_knowledge": (search_steward_knowledge, "检索本地知识库中已存储的信息。"),
        "store_steward_knowledge": (store_steward_knowledge, "将收集到的信息存入本地知识库。"),
        "fetch_feishu_chat_history": (fetch_feishu_chat_history, "读取飞书会话历史消息列表。"),
        "get_feishu_message": (get_feishu_message, "按消息 ID 获取飞书消息详情。"),
        "read_feishu_docx_link": (read_feishu_docx_link, "读取飞书 Docx/Wiki 云文档链接并返回正文文本。"),
        "schedule_feishu_meeting": (schedule_feishu_meeting, "按显式时间参数预约飞书会议并返回会议链接信息。"),
        "send_feishu_meeting_card": (send_feishu_meeting_card, "将会议信息以卡片形式发送到飞书会话。"),
        "update_long_term_memory": (update_long_term_memory, "更新长期记忆文件（MEMORY.md）。"),
        "read_pptx_summary": (read_pptx_summary, "读取 PPTX/PPT 文件并返回结构化摘要。"),
        "create_pptx_from_outline": (create_pptx_from_outline, "从幻灯片大纲列表创建新 PPTX 文件。"),
        "edit_pptx": (edit_pptx, "综合编辑已有 PPTX。"),
        "insert_pptx_image": (insert_pptx_image, "向指定幻灯片插入本地图片。"),
        "set_pptx_text_style": (set_pptx_text_style, "对匹配文本应用 PPTX 字体样式。"),
    }


def _builtin_agent_capabilities() -> list[AgentCapability]:
    """返回路由可用的 Agent 能力描述字典。"""
    worker_role = "负责通用检索、网页阅读、学术资料收集、生成和阅读各类文档、智能管家知识库检索、本地工具执行"
    if RAG_CONFIG["task_history_enabled"]:
        worker_role += "、历史任务检索"
    if MEMORY_CONFIG["enabled"]:
        worker_role += "、长期记忆管理"
    if SCHEDULE_CONFIG["enabled"]:
        worker_role += "、定时任务管理"
    worker_role += "，飞书相关的聊天历史检索与会议预约卡片发送（使用飞书连接时）"

    worker_strengths = [
        "Brave/网页/arXiv/DBLP 检索",
        "下载文件与 PDF 文本提取",
        "Word/Excel/PDF 文档读写与编辑",
        "Shell 与本地文件写入",
        "智能管家知识库检索",
    ]
    if RAG_CONFIG["task_history_enabled"]:
        worker_strengths.append("历史任务记录检索")
    if MEMORY_CONFIG["enabled"]:
        worker_strengths.append("长期记忆读写（记录用户偏好、事实信息等跨会话信息）")
    if SCHEDULE_CONFIG["enabled"]:
        worker_strengths.append("定时任务管理（创建、查看、取消）")

    worker_typical_tasks = [
        "检索最新论文并总结",
        "整理或生成 Word/Excel/PDF 文档",
        "抓取网页并提炼可执行结论",
        "检索智能管家存储的知识（如手机采集的 OCR 文字、对话记录等）",
    ]
    if RAG_CONFIG["task_history_enabled"]:
        worker_typical_tasks.append("查询之前做过的任务或历史记录")
    if MEMORY_CONFIG["enabled"]:
        worker_typical_tasks.append("记住用户偏好或更新长期记忆")
    if SCHEDULE_CONFIG["enabled"]:
        worker_typical_tasks.append("查看、创建或取消定时任务")

    return [
        AgentCapability(
            name="steward",
            role="完成手机端APP任务，负责手机端APP控制，手机端数据收集-存储-分析等任务（Collect/Store/Analyze/Execute）",
            strengths=["手机端数据采集与执行动作，手机端APP控制，手机端APP任务执行"],
            typical_tasks=["整理今日待办并决定是否执行手机操作", "采集微信信息后入库并生成建议", "帮我用淘宝购买某个商品", "帮我饿了么点一杯蜜雪冰城的奶茶"],
            boundaries=["不擅长大规模网页/论文检索,不擅长直接生成总结内容", "通用检索类子任务建议委派给 worker"],
        ),
        AgentCapability(
            name="worker",
            role=worker_role,
            strengths=worker_strengths,
            typical_tasks=worker_typical_tasks,
            boundaries=["不直接执行手机 GUI 操作"],
        ),
    ]
