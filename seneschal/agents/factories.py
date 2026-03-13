# -*- coding: utf-8 -*-
"""seneschal.agents 的各类 factory。"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
from typing import Any

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock
from agentscope.plan import PlanNotebook
from agentscope.tool import Toolkit, ToolResponse

from ..config import MEMORY_CONFIG, MODEL_CONFIG, RAG_CONFIG, ROUTING_CONFIG, SCHEDULE_CONFIG
from .common import (
    _build_memory_prompt,
    _build_skill_prompt_suffix,
    _env_bool,
    _extract_vlm_evidence,
    _judge_completion_with_vlm,
    _trim_for_log,
    create_openai_model,
)
from ..tools import (
    arxiv_search,
    brave_search,
    call_mobi_action,
    call_mobi_collect_verified,
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
    search_steward_knowledge,
    search_task_history,
    set_pptx_text_style,
    store_steward_knowledge,
    update_long_term_memory,
    write_text_file,
    write_xlsx_from_records,
    write_xlsx_from_rows,
)

logger = logging.getLogger("seneschal.agents")


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


def create_worker_agent(
    skill_context: str | None = None,
    job_context: dict[str, Any] | None = None,
) -> ReActAgent:
    """创建 Worker Agent，用于子任务委派。"""
    toolkit = Toolkit()

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
    )

    toolkit.register_tool_function(
        run_skill_script,
        func_description=(
            "在指定 execution_dir 中执行skill中定义的命令。"
            "调用时请传入完整可执行命令字符串和执行目录。"
        ),
    )

    toolkit.register_tool_function(
        brave_search,
        func_description="通过 Brave Search API 联网检索新闻与网页来源链接。",
    )

    toolkit.register_tool_function(
        arxiv_search,
        func_description="查询 arXiv API 获取论文元数据、摘要与 PDF 链接。",
    )

    toolkit.register_tool_function(
        dblp_conference_search,
        func_description="检索会议论文清单与链接（DBLP），用于按年份与关键词筛选。",
    )

    toolkit.register_tool_function(
        fetch_url_text,
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )

    toolkit.register_tool_function(
        fetch_url_readable_text,
        func_description="抓取并提取网页可读文本，用于快速理解页面内容。",
    )

    toolkit.register_tool_function(
        fetch_url_links,
        func_description="抓取网页并提取链接，用于发现相关来源并继续检索。",
    )

    toolkit.register_tool_function(
        download_file,
        func_description="下载 URL 文件到本地路径（支持二进制，例如 PDF）。",
    )

    toolkit.register_tool_function(
        extract_pdf_text,
        func_description="从本地 PDF 文件中提取文本内容。",
    )

    toolkit.register_tool_function(
        extract_image_text_ocr,
        func_description="从本地图片文件中执行 OCR 识别，提取文字内容。",
    )

    toolkit.register_tool_function(
        read_docx_text,
        func_description="读取 DOCX 文档文本内容。",
    )

    toolkit.register_tool_function(
        create_docx_from_text,
        func_description="从纯文本生成 DOCX 文档。",
    )

    toolkit.register_tool_function(
        edit_docx,
        func_description="对 DOCX 文档进行查找替换、追加段落或插入表格。",
    )

    toolkit.register_tool_function(
        create_pdf_from_text,
        func_description="从纯文本生成 PDF 文档。",
    )

    toolkit.register_tool_function(
        read_xlsx_summary,
        func_description="读取 XLSX 工作簿摘要与预览。",
    )

    toolkit.register_tool_function(
        write_xlsx_from_records,
        func_description="从记录列表生成 XLSX 文件。",
    )

    toolkit.register_tool_function(
        write_xlsx_from_rows,
        func_description="从行数据生成 XLSX 文件。",
    )

    toolkit.register_tool_function(
        write_text_file,
        func_description="写入本地文本文件，用于保存结果或日志。",
    )

    toolkit.register_tool_function(
        search_steward_knowledge,
        func_description="检索本地知识库中已存储的信息（由智能管家从手机中提取并存储）。",
    )

    toolkit.register_tool_function(
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

    toolkit.register_tool_function(
        get_feishu_message,
        func_description="按消息 ID 获取飞书消息详情，用于排查和精确分析。",
    )

    toolkit.register_tool_function(
        read_pptx_summary,
        func_description="读取 PPTX/PPT 文件，返回每张幻灯片的标题、正文文本、备注、形状数量和图片数量的结构化摘要。",
    )

    toolkit.register_tool_function(
        create_pptx_from_outline,
        func_description=(
            "从幻灯片大纲列表创建新 PPTX 文件。"
            "每张幻灯片支持：标题、正文（字符串或列表）、演讲者备注、布局索引、"
            "嵌入图片（路径+位置+尺寸）、字号、字体颜色（#RRGGBB）、粗体、斜体。"
            "支持可选模板文件与全局默认字体大小/颜色。"
        ),
    )

    toolkit.register_tool_function(
        edit_pptx,
        func_description=(
            "综合编辑已有 PPTX：跨所有幻灯片全局文本替换、追加新幻灯片、"
            "按 1-based 索引删除幻灯片。三种操作可在一次调用中组合使用。"
        ),
    )

    toolkit.register_tool_function(
        insert_pptx_image,
        func_description=(
            "向指定幻灯片（1-based 索引）插入本地图片。"
            "支持英寸单位的定位（left/top）和尺寸（width/height），省略宽高时保持原始比例。"
        ),
    )

    toolkit.register_tool_function(
        set_pptx_text_style,
        func_description=(
            "在指定幻灯片中搜索文本子串，对所有匹配的 run 应用字体样式："
            "字号（pt）、颜色（#RRGGBB）、粗体、斜体、下划线。省略的属性保持原样。"
        ),
    )

    sys_prompt = """你是 Seneschal 的 Worker Agent，负责处理通用问题与单一子任务。

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
- 如果消息中包含 [Feishu Context]，调用飞书历史工具时必须优先使用其中的 chat_id/open_id/message_id，不得猜测或改写。
- 若 [Feishu Context] 缺少必需 ID，应先明确指出缺失项并向用户索取，不要编造参数。
- 输出格式遵循用户要求；未指定时默认使用 Markdown。
- 必须输出最终文本结论或可执行结果；不要输出空的工具调用。
- 即使已经把结果写入文件，也必须在当前回复中给出完整结论（至少包含关键结论与主要依据）；禁止只回复“已落盘+文件路径”。
- 不做多步长对话，输出最终结论或可执行结果。
"""
    if RAG_CONFIG["task_history_enabled"]:
        toolkit.register_tool_function(
            search_task_history,
            func_description="检索历史任务执行记录和相关文件，用于回答关于之前做过的任务的问题。",
        )
        sys_prompt += "- 如果用户询问之前做过的任务，使用 \"search_task_history\" 检索历史记录。\n"

    if MEMORY_CONFIG["enabled"]:
        toolkit.register_tool_function(
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
        toolkit.register_tool_function(
            bound_create,
            func_description='创建定时任务。传入 task（核心任务描述，去除时间信息）和 time_description（自然语言时间描述，如"每天早上8点"（周期任务）、"下午2点10分"（单次任务））。系统会自动解析时间并创建定时调度。',
        )
        toolkit.register_tool_function(
            list_scheduled_tasks,
            func_description="列出所有定时任务及其状态信息（schedule_id、任务内容、状态、调度类型、描述、cron 表达式等）。",
        )
        toolkit.register_tool_function(
            cancel_scheduled_task,
            func_description="取消指定的定时任务。需要提供 schedule_id，可先通过 list_scheduled_tasks 查询。",
        )
        sys_prompt += (
            '- 如果用户想创建定时任务（如"每天帮我搜新闻"），使用 "create_scheduled_task" 创建，传入核心任务和时间描述。\n'
            "- 如果用户想查看或取消定时任务，使用 \"list_scheduled_tasks\" 查看列表，使用 \"cancel_scheduled_task\" 取消指定任务。\n"
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


def create_steward_agent(
    skill_context: str | None = None,
    job_context: dict[str, Any] | None = None,
) -> ReActAgent:
    """创建智能管家 Agent (StewardAgent)。"""
    toolkit = Toolkit()
    retry_cap = max(0, min(int(os.environ.get("STEWARD_MOBI_MAX_RETRIES", "2")), 5))
    ctx = job_context if isinstance(job_context, dict) else {}
    mobi_output_dir = str(ctx.get("mobi_output_dir") or "").strip()
    if not mobi_output_dir:
        job_output_dir = str(ctx.get("job_output_dir") or "").strip()
        if job_output_dir:
            mobi_output_dir = str((Path(job_output_dir) / "mobile_exec").resolve())

    if mobi_output_dir:
        try:
            Path(mobi_output_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("steward.mobi_output_dir.create_failed path=%s", mobi_output_dir, exc_info=True)
            mobi_output_dir = ""

    if mobi_output_dir:
        collect_func = functools.partial(call_mobi_collect_verified, output_dir=mobi_output_dir)
        collect_func.__name__ = "call_mobi_collect_verified"
        collect_func.__doc__ = call_mobi_collect_verified.__doc__

        action_func = functools.partial(call_mobi_action, output_dir=mobi_output_dir)
        action_func.__name__ = "call_mobi_action"
        action_func.__doc__ = call_mobi_action.__doc__
    else:
        collect_func = call_mobi_collect_verified
        action_func = call_mobi_action

    toolkit.register_tool_function(
        collect_func,
        func_description=(
            "优先使用：调用 MobiAgent 收集手机任务结果（单次执行）。"
            "该工具不保证任务正确完成，也不会自动重试。"
            "返回统一结构化证据：截图路径、OCR文本、动作历史和推理历史，供 Agent 自主判断。"
        ),
    )

    toolkit.register_tool_function(
        action_func,
        func_description=(
            "指挥 MobiAgent 在手机端执行 GUI 操作。"
            "支持的操作例如: 'add_calendar_event'(添加日历事件), "
            "'send_message'(发送消息), 'set_reminder'(设置提醒), go_shop(下单购物)等。"
            "payload 参数为 JSON 格式字符串，如: "
            "'{\"title\": \"Meeting\", \"time\": \"15:00\", \"date\": \"2024-01-20\"}'。"
            "通常是数据整理流程的最后一步，根据分析结果执行具体操作。"
        ),
    )

    toolkit.register_tool_function(
        store_steward_knowledge,
        func_description=(
            "将收集到的信息存入本地知识库。"
            "用于持久化保存 OCR 识别的文字、对话记录、账单信息等。"
            "输入要存储的文本内容，系统会将其加入知识库供后续检索分析。"
            "通常应在收集数据后调用。"
        ),
    )

    toolkit.register_tool_function(
        search_steward_knowledge,
        func_description=(
            "检索本地知识库中已存储的信息。"
            "用于查找之前通过 store_steward_knowledge 存入的数据（OCR 文字、对话记录等）。"
            "检索后请根据返回的原始片段自行分析总结。"
        ),
    )

    toolkit.register_tool_function(
        fetch_url_text,
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
    )
    toolkit.register_tool_function(
        extract_image_text_ocr,
        func_description="从指定图片中提取文字。",
    )

    async def call_mobi_collect_with_retry_report(task_desc: str, success_criteria: str = "") -> ToolResponse:
        """执行带重试上限的 mobi 采集，并返回结构化证据包。

        Args:
            task_desc: 手机任务描述。
            success_criteria: 可选成功判定条件文本。
        """
        attempts: list[dict[str, object]] = []
        current_task = task_desc
        criteria_matched = False
        no_criteria_mode = not bool((success_criteria or "").strip())
        vlm_enabled = _env_bool("STEWARD_MOBI_VLM_ENABLED", True)
        vlm_last_n = max(1, int(os.environ.get("STEWARD_MOBI_VLM_LAST_N", "5")))
        vlm_last_n_steps = max(1, int(os.environ.get("STEWARD_MOBI_VLM_LAST_N_STEPS", str(vlm_last_n))))
        vlm_timeout_s = max(5.0, float(os.environ.get("STEWARD_MOBI_VLM_TIMEOUT_S", "25")))
        vlm_max_reasonings_chars = max(1000, int(os.environ.get("STEWARD_MOBI_VLM_MAX_REASONINGS_CHARS", "12000")))
        vlm_model = create_openai_model(stream=False, temperature=0.0) if vlm_enabled else None

        for idx in range(1, retry_cap + 2):
            resp = await collect_func(current_task, max_retries=0)
            md = (resp.metadata or {}) if resp else {}
            ocr_text = str(md.get("ocr_text", "") or "")
            last_reasoning = str(md.get("last_reasoning", "") or "")
            extracted_info = md.get("extracted_info", {}) if isinstance(md.get("extracted_info"), dict) else {}
            tool_success = bool(md.get("success", False))
            has_evidence = bool(ocr_text.strip() or last_reasoning.strip() or extracted_info or md.get("raw_data"))
            criteria_matched_text = False
            criteria_matched_vlm = False
            vlm_verdict: dict[str, Any] = {
                "completed": False,
                "confidence": 0.0,
                "reason": "",
                "evidence": [],
                "missing_requirements": [],
                "summary": {
                    "screen_state": "",
                    "trajectory_last_steps": [],
                    "relevant_information": [],
                    "extracted_text": [],
                },
            }
            vlm_images_used: list[str] = []
            reasonings_count = 0

            if success_criteria:
                haystack = ocr_text + "\n" + last_reasoning + "\n" + json.dumps(extracted_info, ensure_ascii=False)
                criteria_matched_text = tool_success and (success_criteria in haystack)
                if (not criteria_matched_text) and tool_success and has_evidence:
                    generic_tokens = ("成功获取", "获取到", "收集到", "活动信息", "最近活动")
                    if any(token in success_criteria for token in generic_tokens):
                        criteria_matched_text = True
            else:
                criteria_matched_text = tool_success and has_evidence

            if vlm_enabled and vlm_model is not None and tool_success and has_evidence:
                vlm_evidence = _extract_vlm_evidence(
                    md,
                    last_n_images=vlm_last_n,
                    last_n_steps=vlm_last_n_steps,
                    max_reasonings_chars=vlm_max_reasonings_chars,
                )
                reasonings_count = int(vlm_evidence.get("reasonings_count", 0) or 0)
                vlm_images_used = [str(p) for p in vlm_evidence.get("images_selected", []) if isinstance(p, str)]
                vlm_verdict = await _judge_completion_with_vlm(
                    model=vlm_model,
                    task_desc=str(vlm_evidence.get("task_description", "") or current_task),
                    success_criteria=success_criteria,
                    status_hint=str(vlm_evidence.get("status_hint", "")),
                    step_count=int(vlm_evidence.get("step_count", 0) or 0),
                    action_count=int(vlm_evidence.get("action_count", 0) or 0),
                    reasonings_text=str(vlm_evidence.get("reasonings_text", "")),
                    recent_actions_text=str(vlm_evidence.get("recent_actions_text", "")),
                    recent_reacts_text=str(vlm_evidence.get("recent_reacts_text", "")),
                    recent_ocr_text=str(vlm_evidence.get("recent_ocr_text", "")),
                    ocr_full_text=str(vlm_evidence.get("ocr_full_text", "")),
                    last_n_steps=int(vlm_evidence.get("last_n_steps", vlm_last_n_steps) or vlm_last_n_steps),
                    image_data_urls=[str(u) for u in vlm_evidence.get("image_data_urls", []) if isinstance(u, str)],
                    timeout_s=vlm_timeout_s,
                )
                criteria_matched_vlm = bool(vlm_verdict.get("completed", False))

            criteria_matched = criteria_matched_text or criteria_matched_vlm
            vlm_summary = vlm_verdict.get("summary", {}) if isinstance(vlm_verdict.get("summary"), dict) else {}

            attempt_item = {
                "attempt": idx,
                "task_desc": current_task,
                "run_dir": md.get("run_dir", ""),
                "index_file": md.get("index_file", ""),
                "status_hint": md.get("status_hint", ""),
                "step_count": md.get("step_count", 0),
                "action_count": md.get("action_count", 0),
                "screenshot_path": md.get("screenshot_path", ""),
                "last_reasoning": last_reasoning,
                "ocr_preview": ocr_text[:300],
                "extracted_info": extracted_info,
                "tool_success": tool_success,
                "criteria_matched_text": criteria_matched_text,
                "criteria_matched_vlm": criteria_matched_vlm,
                "vlm_completed": bool(vlm_verdict.get("completed", False)),
                "vlm_confidence": float(vlm_verdict.get("confidence", 0.0) or 0.0),
                "vlm_reason": str(vlm_verdict.get("reason", "") or ""),
                "vlm_error": str(vlm_verdict.get("error", "") or ""),
                "vlm_images_used": vlm_images_used,
                "reasonings_count": reasonings_count,
                "vlm_missing_requirements": vlm_verdict.get("missing_requirements", []),
                "vlm_summary_screen_state": str(vlm_summary.get("screen_state", "") or ""),
                "vlm_summary_last_steps": vlm_summary.get("trajectory_last_steps", []),
                "vlm_summary_relevant_information": vlm_summary.get("relevant_information", []),
                "vlm_summary_extracted_text": vlm_summary.get("extracted_text", []),
            }
            attempts.append(attempt_item)

            if criteria_matched:
                break

            if idx <= retry_cap:
                failure_reason = "criteria_not_matched" if success_criteria else "no_evidence_collected"
                current_task = f"{task_desc}\n"
                logger.info(f"重试要求(第{idx}次失败，原因:{failure_reason})：" "请严格按目标完成后立即停止；避免重复无效操作；保留可验证证据。")

        final_attempt = attempts[-1] if attempts else {}
        pack: dict[str, object] = {
            "report_type": "mobi_retry_evidence_pack_v1",
            "original_task": task_desc,
            "success_criteria": success_criteria,
            "no_criteria_mode": no_criteria_mode,
            "retry_limit": retry_cap,
            "attempt_count": len(attempts),
            "criteria_matched": criteria_matched,
            "needs_agent_judgement": True,
            "validation_mode": "text_or_vlm",
            "vlm_enabled": vlm_enabled,
            "vlm_last_n": vlm_last_n,
            "vlm_last_n_steps": vlm_last_n_steps,
            "attempts": attempts,
        }

        if not criteria_matched:
            pack["failure_report"] = {
                "status": "failed_after_retry_limit",
                "latest_run_dir": final_attempt.get("run_dir", ""),
                "latest_index_file": final_attempt.get("index_file", ""),
                "latest_screenshot_path": final_attempt.get("screenshot_path", ""),
                "latest_reasoning": final_attempt.get("last_reasoning", ""),
                "latest_ocr_preview": final_attempt.get("ocr_preview", ""),
                "latest_vlm_reason": final_attempt.get("vlm_reason", ""),
                "vlm_missing_requirements": final_attempt.get("vlm_missing_requirements", []),
                "latest_vlm_summary_screen_state": final_attempt.get("vlm_summary_screen_state", ""),
                "latest_vlm_summary_last_steps": final_attempt.get("vlm_summary_last_steps", []),
                "latest_vlm_summary_relevant_information": final_attempt.get("vlm_summary_relevant_information", []),
                "latest_vlm_summary_extracted_text": final_attempt.get("vlm_summary_extracted_text", []),
                "next_action_recommendation": "agent_decide_retry_or_handoff",
            }

        logger.info(f"[MobiAgent] 收集证据包：{_trim_for_log(json.dumps(pack, ensure_ascii=False))}")

        relevant_info_lines = [
            f"- {str(item).strip()}"
            for item in final_attempt.get("vlm_summary_relevant_information", [])
            if str(item).strip()
        ]
        extracted_text_lines = [
            f"- {str(item).strip()}"
            for item in final_attempt.get("vlm_summary_extracted_text", [])
            if str(item).strip()
        ]
        relevant_info_block = "\n".join(relevant_info_lines) if relevant_info_lines else "[empty]"
        extracted_text_block = "\n".join(extracted_text_lines) if extracted_text_lines else "[empty]"

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[MobiAgent 重试证据包]\n"
                        f"任务: {task_desc}\n"
                        f"重试上限: {retry_cap}\n"
                        f"尝试次数: {len(attempts)}\n"
                        f"criteria_matched: {criteria_matched}\n"
                        f"最后状态提示: {final_attempt.get('status_hint', '')}\n"
                        f"OCR摘要: {str(final_attempt.get('ocr_preview', '') or '')[:500]}\n"
                        f"VLM页面摘要: {str(final_attempt.get('vlm_summary_screen_state', '') or '')[:300]}\n"
                        f"VLM目标相关信息:\n{relevant_info_block}\n"
                        f"VLM截图提取文本:\n{extracted_text_block}\n"
                        "注意：该结果仅为证据汇总，最终完成判定必须由 Agent 自主做出。"
                    ),
                ),
            ],
            metadata=pack,
        )

    toolkit.register_tool_function(
        call_mobi_collect_with_retry_report,
        func_description=(
            "执行手机任务并应用显式重试上限（默认最多2次重试，总共3次尝试），"
            "返回结构化证据包与失败报告模板。"
            "该工具不做最终完成保证，最终判定由 Agent 根据证据自主决定。"
        ),
    )

    async def delegate_to_worker(task: str, delegation_depth: int = 0) -> ToolResponse:
        """将子任务委派给 Worker Agent 并返回结果。

        Args:
            task: 要委派给 Worker 的子任务描述。
            delegation_depth: 当前委派深度计数。
        """
        max_depth = int(ROUTING_CONFIG.get("max_routing_depth", 2))
        if delegation_depth >= max_depth:
            return ToolResponse(
                content=[TextBlock(type="text", text="[Worker 结果]\n已达到委派深度上限，停止继续委派。")],
                metadata={"task": task, "delegation_depth": delegation_depth, "stopped": True},
            )
        worker = create_worker_agent()
        msg = Msg(name="User", content=task, role="user")
        response = await worker(msg)
        text = response.get_text_content() if response else ""
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Worker 结果]\n{text}")],
            metadata={"task": task, "delegation_depth": delegation_depth + 1},
        )

    toolkit.register_tool_function(
        delegate_to_worker,
        func_description="将子任务委派给 Worker Agent 并汇总返回结果。",
    )

    sys_prompt = """你是 Seneschal 智能管家系统的核心 Agent，负责帮助用户管理个人数据和日常事务。

## 你的职责
1. 理解用户的需求和指令
2. 规划并执行数据收集、存储、分析和操作的完整流程
3. 通过调用工具与手机操作Agent如MobiAgent（手机端）协作

## 工作流程规范
当用户要求进行数据整理或分析时，请严格按照以下步骤执行：

### 收集与验证 (Collect + Verify)
- 优先使用 `call_mobi_collect_with_retry_report` 执行手机任务并获取证据包
- 显式重试上限：最多 {retry_cap} 次重试
- 单一App原则：严禁将跨 App 的任务混在一个指令中。每个子任务必须仅对应 1 个 App 的 1 种场景
- 禁止过度拆分：不要使用 plan 工具去拆分单个 App 内的微操，交给 MobiAgent 自行推理
- 必须基于返回证据（截图路径、OCR文本、动作/推理历史）自行判断任务是否完成，不要把工具返回中的状态提示当作最终真值；它只能作为参考
- 对于购买、下单、发送类任务请勿重试，执行结束返回后，判断任务结果即可
- 避免使用plan工具拆分单个APP内部的任务，每个任务只能对应一个APP中的1种任务场景
- 若任务未完成，你必须在上限内改写任务并重试；超限后停止继续操作
- 每次重试在回复中说明失败依据与改写思路
- 例如：获取微信聊天截图、日历事件、通知消息等

### 失败报告模板 (Failure Pack)
- 达到重试上限仍未完成时，必须输出结构化失败证据包，字段至少包括：
- `report_type`, `original_task`, `retry_limit`, `attempt_count`, `attempts`, `failure_report`
- `failure_report` 内至少包含：
- `status`, `latest_run_dir`, `latest_index_file`, `latest_screenshot_path`, `latest_reasoning`, `latest_ocr_preview`, `next_action_recommendation`

### 存储 (Store)
- 使用 `store_steward_knowledge` 工具将收集到的信息存入管家知识库
- 确保所有有价值的信息都被持久化保存

### 检索 (Retrieve)
- 查找之前存储的管家知识库（OCR、对话记录等），使用 `search_steward_knowledge`
- 对外部页面查询可用 `fetch_url_text` 获取原始文本

### 分析 (Analyze)
- 根据管家知识库检索到的原始片段，自行分析总结待办事项、账单、重要提醒等

### 执行 (Execute)
- 如果分析发现需要执行的操作（如添加日程、设置提醒）
- 使用 `call_mobi_action` 工具在手机端执行相应操作

## 注意事项
- 每一步都要向用户汇报进展
- 如果某一步失败，要尝试其他方法或向用户说明
- 在执行敏感操作前，需要用户确认（除非用户明确授权自动执行）
- 保持回复简洁专业，优先使用中文交流
- 即使过程产出了落盘文件，也必须在当前回复正文给出明确结论与关键内容；不能只回复文件路径。
- 如果任务主要是通用网页/论文检索，优先委派给 Worker，避免重复调用端侧工具
- 若路由层已明确指定本 Agent，仅处理职责范围内任务，不要无限自委派

## 示例对话
用户：开始今日的数据整理和分析
你应该：
1. 思考并调用 call_mobi_collect_with_retry_report 获取证据（含显式重试上限）
2. 基于证据自主判断是否完成；若未完成且已达上限，输出结构化失败证据包
3. 调用 store_steward_knowledge 存储收集到的信息
4. 调用 search_steward_knowledge 检索已存数据，自行分析待办和账单
5. 如发现待办事项，询问是否需要添加到日历，然后调用 call_mobi_action

现在，请准备好为用户服务！"""
    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Steward",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=10,
        plan_notebook=PlanNotebook(),
    )


def create_user_agent() -> UserAgent:
    """创建用户代理 Agent。"""
    return UserAgent(name="User")


def create_chat_agent(*, web_search_enabled: bool = True) -> ReActAgent:
    """创建网关 chat 模式使用的基础对话 Agent。"""
    toolkit = Toolkit()

    toolkit.create_tool_group(
        group_name="web_search",
        description="用于网页搜索的工具函数。",
        active=web_search_enabled,
        notes="""优先使用 brave_search 直接获取结果，若获取结果失败，再使用 fetch_* 工具尝试获取。""",
    )
    toolkit.register_tool_function(
        brave_search,
        group_name="web_search",
        func_description="通过 Brave Search API 联网检索新闻与网页来源链接。",
    )

    toolkit.register_tool_function(
        fetch_url_text,
        group_name="web_search",
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )
    toolkit.register_tool_function(
        fetch_url_readable_text,
        group_name="web_search",
        func_description="抓取并提取网页可读文本，用于快速理解页面内容。",
    )

    toolkit.register_tool_function(
        fetch_url_links,
        group_name="web_search",
        func_description="抓取网页并提取链接，用于发现相关来源并继续检索。",
    )

    sys_prompt = """你是 Seneschal 的基础对话助手,名字是 MobiChatBot。

    职责：
    - 与用户进行连续、多轮的自然语言对话；
    - 直接回答用户问题，必要时说明不确定性；
    - 语气简洁、专业、清晰。
    - 若有文件落盘，也必须在当前回复中直接给出答案要点，不能只给文件路径。
"""
    return ReActAgent(
        name="MobiChatBot",
        sys_prompt=sys_prompt,
        model=create_openai_model(stream=False, temperature=0.3),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        plan_notebook=PlanNotebook(),
        max_iters=8,
    )
