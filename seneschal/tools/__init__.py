# -*- coding: utf-8 -*-
"""Seneschal 工具包。"""

from __future__ import annotations

import logging
from typing import Any

from .mobi import call_mobi_action, call_mobi_collect, call_mobi_collect_verified
from .file import write_text_file
from .papers import arxiv_search, dblp_conference_search, download_file, extract_pdf_text
from .office import (
    create_docx_from_text,
    create_pdf_from_text,
    edit_docx,
    read_docx_text,
    read_xlsx_summary,
    write_xlsx_from_records,
    write_xlsx_from_rows,
)
from .ppt import (
    create_pptx_from_outline,
    edit_pptx,
    insert_pptx_image,
    read_pptx_summary,
    set_pptx_text_style,
)
from .skill_runner import run_skill_script
from .ocr import extract_image_text_ocr
from .shell import run_shell_command
from .web import brave_search, fetch_url_links, fetch_url_readable_text, fetch_url_text
from .feishu import fetch_feishu_chat_history, get_feishu_message
from .memory import (
    read_memory,
    update_long_term_memory,
    store_task_result,
    search_task_history,
    store_steward_knowledge,
    search_steward_knowledge,
)

logger = logging.getLogger(__name__)


__all__ = [
    "call_mobi_action",
    "call_mobi_collect",
    "call_mobi_collect_verified",
    "run_shell_command",
    "fetch_url_text",
    "fetch_url_readable_text",
    "fetch_url_links",
    "brave_search",
    "write_text_file",
    "arxiv_search",
    "dblp_conference_search",
    "download_file",
    "extract_pdf_text",
    "read_docx_text",
    "create_docx_from_text",
    "edit_docx",
    "create_pdf_from_text",
    "read_xlsx_summary",
    "write_xlsx_from_records",
    "write_xlsx_from_rows",
    "read_memory",
    "update_long_term_memory",
    "store_task_result",
    "search_task_history",
    "store_steward_knowledge",
    "search_steward_knowledge",
    "fetch_feishu_chat_history",
    "get_feishu_message",
]
