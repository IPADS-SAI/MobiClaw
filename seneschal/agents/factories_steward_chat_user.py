# -*- coding: utf-8 -*-
"""Steward / Chat / User 工厂。"""

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

from ..config import ROUTING_CONFIG
from .common import (
    _build_memory_prompt,
    _build_skill_prompt_suffix,
    _env_bool,
    _extract_vlm_evidence,
    _judge_completion_with_vlm,
    _trim_for_log,
    create_openai_model,
)
from .factories_worker import create_worker_agent
from ..tools import (
    brave_search,
    call_mobi_action,
    call_mobi_collect_verified,
    extract_image_text_ocr,
    fetch_url_links,
    fetch_url_readable_text,
    fetch_url_text,
    run_shell_command,
    search_steward_knowledge,
    store_steward_knowledge,
)

logger = logging.getLogger("seneschal.agents")


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
- 单一App原则：严禁将跨 App 的任务混在一个指令中。每个手机任务必须仅对应 1 个 App 的 1 种任务场景
- 禁止过度拆分：不要使用 plan 工具去拆分单个 App 内的微操，交给 MobiAgent 自行推理
- 必须基于返回证据（截图、OCR文本、动作/推理历史）自行判断任务是否完成，不要把工具返回中的状态提示当作最终真值；它只能作为参考
- 对于购买、下单、发送类任务请勿重试，执行结束返回后，判断任务结果即可
- 避免使用plan工具拆分单个APP内部的任务，每个任务只能对应一个APP中的1种任务场景
- 若任务未完成，你必须在上限内改写任务并重试；超限后停止继续操作
- 每次重试在回复中说明失败依据与改写思路

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
