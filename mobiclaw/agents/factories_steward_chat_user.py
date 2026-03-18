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
from agentscope.message import Msg, TextBlock, ImageBlock
from agentscope.plan import PlanNotebook
from agentscope.tool import Toolkit, ToolResponse

from ..config import ROUTING_CONFIG
from .common import (
    _build_memory_prompt,
    _build_skill_prompt_suffix,
    _env_bool,
    _extract_vlm_evidence,
    _summarize_execution_with_vlm,
    _trim_for_log,
    create_openai_model,
    register_tool_with_timeout,
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
    write_text_file,
)

logger = logging.getLogger("mobiclaw.agents")


def create_steward_agent(
    skill_context: str | None = None,
    job_context: dict[str, Any] | None = None,
) -> ReActAgent:
    """创建智能管家 Agent (StewardAgent)。"""
    toolkit = Toolkit()
    ctx = job_context if isinstance(job_context, dict) else {}
    mobi_output_dir = str(ctx.get("mobi_output_dir") or "").strip()

    # 优先使用 job_context 中的 mobi_output_dir 配置, 以保存手机任务结果到对应job目录下；如果没有，再尝试使用环境变量指定的路径。
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

    _reg = functools.partial(register_tool_with_timeout, toolkit)

    _reg(
        action_func,
        func_description=(
            "指挥 MobiAgent 在手机端执行 GUI 操作，执行任务或设置事件状态。"
            "支持的操作例如: 添加日历事件), 发送消息, 设置提醒下单购物)等。"
        ),
    )

    _reg(write_text_file, func_description="写入本地文本文件，用于保存结果或日志。")

    _reg(
        store_steward_knowledge,
        func_description=(
            "将收集到的信息存入本地知识库。"
            "用于持久化保存 OCR 识别的文字、对话记录、账单信息等。"
            "输入要存储的文本内容，系统会将其加入知识库供后续检索分析。"
            "通常应在收集数据后调用。"
        ),
    )

    _reg(
        search_steward_knowledge,
        func_description=(
            "检索本地知识库中已存储的信息。"
            "用于查找之前通过 store_steward_knowledge 存入的数据（图片内容、对话记录等）。"
            "检索后请根据返回的原始片段自行分析总结。"
        ),
    )

    _reg(fetch_url_text, func_description="抓取指定 URL 的文本内容用于快速检索。")
    _reg(run_shell_command, func_description="运行受限的本地命令行工具（白名单约束）。")
    _reg(extract_image_text_ocr, func_description="从指定图片中提取文字。")

    async def call_mobi_collect_with_report(task_desc: str) -> ToolResponse:
        """执行一次手机采集任务，返回 VLM 摘要/提取结果与最后截图。

        Args:
            task_desc: 手机任务描述。
        Returns:
            ToolResponse: 含 VLM 摘要、原始内容、原始元数据 和最后结果截图。
        """
        vlm_enabled = _env_bool("STEWARD_MOBI_VLM_ENABLED", True)
        vlm_last_n = max(1, int(os.environ.get("STEWARD_MOBI_VLM_LAST_N", "5")))
        vlm_timeout_s = max(5.0, float(os.environ.get("STEWARD_MOBI_VLM_TIMEOUT_S", "25")))
        vlm_max_reasonings_chars = max(1000, int(os.environ.get("STEWARD_MOBI_VLM_MAX_REASONINGS_CHARS", "12000")))
        vlm_model = create_openai_model(stream=False, temperature=0.0) if vlm_enabled else None

        def _lines_to_block(values: object) -> str:
            # 将列表字段统一渲染为 markdown 列表块，
            # 提升返回文本对模型和人工阅读的可读性。
            if not isinstance(values, list):
                return "[empty]"
            lines = [f"- {str(item).strip()}" for item in values if str(item).strip()]
            return "\n".join(lines) if lines else "[empty]"

        # 单次执行：工具层不再管理重试与完成判定。
        resp = await collect_func(task_desc, max_retries=0)
        metadata = (resp.metadata or {}) if resp else {}
        # 最后一张截图由 collect 工具写入 metadata.final_image_path；
        # steward 侧直接复用，不再重复解析上游 content。
        final_image_url = str(metadata.get("final_image_path", "") or "").strip()

        empty_summary: dict[str, Any] = {
            "screen_state": "",
            "trajectory_last_steps": [],
            "relevant_information": [],
            "extracted_text": [],
        }
        # 默认空摘要；当 VLM 关闭或不可用时，仍返回稳定字段结构。
        vlm_summary = empty_summary
        vlm_error = ""
        if vlm_model is not None:
            # 将执行元数据压缩成 VLM prompt：
            # 最近截图 + 轨迹历史 + 推理文本。
            vlm_evidence = _extract_vlm_evidence(
                metadata,
                last_n_images=vlm_last_n,
                last_n_steps=vlm_last_n,
                max_reasonings_chars=vlm_max_reasonings_chars,
            )
            # 本工具路径下，VLM 仅做“摘要 + 可见信息提取”
            vlm_result = await _summarize_execution_with_vlm(
                model=vlm_model,
                task_desc=str(vlm_evidence.get("task_description", "") or task_desc),
                status_hint=str(vlm_evidence.get("status_hint", "")),
                step_count=int(vlm_evidence.get("step_count", 0) or 0),
                action_count=int(vlm_evidence.get("action_count", 0) or 0),
                reasonings_text=str(vlm_evidence.get("reasonings_text", "")),
                recent_actions_text=str(vlm_evidence.get("recent_actions_text", "")),
                recent_reacts_text=str(vlm_evidence.get("recent_reacts_text", "")),
                last_n_steps=int(vlm_evidence.get("last_n_steps", vlm_last_n) or vlm_last_n),
                image_data_urls=[str(u) for u in vlm_evidence.get("image_data_urls", []) if isinstance(u, str)],
                timeout_s=vlm_timeout_s,
            )
            vlm_summary = vlm_result.get("summary", {}) if isinstance(vlm_result.get("summary"), dict) else empty_summary
            vlm_error = str(vlm_result.get("error", "") or "")

        # metadata 是给 steward 消费的结构化结果包。
        pack: dict[str, object] = {
            "task": task_desc,
            "success": bool(metadata.get("success", False)),
            "requires_agent_validation": bool(metadata.get("requires_agent_validation", True)),
            "attempt": int(metadata.get("attempt", 1) or 1),
            "attempt_total": int(metadata.get("attempt_total", 1) or 1),
            "run_dir": metadata.get("run_dir", ""),
            "status_hint": metadata.get("status_hint", ""),
            "last_reasoning": str(metadata.get("last_reasoning", "") or ""),
            "vlm_summary_screen_state": str(vlm_summary.get("screen_state", "") or ""),
            "vlm_summary_last_steps": vlm_summary.get("trajectory_last_steps", []),
            "vlm_summary_relevant_information": vlm_summary.get("relevant_information", []),
            "vlm_summary_extracted_text": vlm_summary.get("extracted_text", []),
            "vlm_error": vlm_error,
        }
        logger.info(f"[MobiAgent] 收集结果包：{_trim_for_log(json.dumps(pack, ensure_ascii=False))}")

        # content 是给人/模型直接阅读的摘要文本 + 最后截图
        relevant_info_block = _lines_to_block(pack.get("vlm_summary_relevant_information", []))
        extracted_text_block = _lines_to_block(pack.get("vlm_summary_extracted_text", []))
        has_vlm_summary = bool(str(pack.get("vlm_summary_screen_state", "") or "").strip())
        has_relevant_info = bool(
            isinstance(pack.get("vlm_summary_relevant_information"), list)
            and len(pack.get("vlm_summary_relevant_information", [])) > 0
        )
        has_extracted_text = bool(
            isinstance(pack.get("vlm_summary_extracted_text"), list)
            and len(pack.get("vlm_summary_extracted_text", [])) > 0
        )
        has_image = bool(final_image_url)
        content: list[TextBlock | ImageBlock] = [
            TextBlock(
                type="text",
                text=(
                    "[MobiAgent 执行结果包]\n"
                    f"完成状态: {'success' if pack.get('success') else 'failed'}\n"
                    f"证据可用性: vlm_summary={True if has_vlm_summary else False}, "
                    f"relevant_info={True if has_relevant_info else False}, "
                    f"extracted_text={True if has_extracted_text else False}, "
                    f"image={True if has_image else False}\n"
                    f"任务: {task_desc}\n"
                    f"最后状态提示: {pack.get('status_hint', '')}\n"
                    f"最后推理: {str(pack.get('last_reasoning', '') or '')[:500]}\n"
                    f"VLM页面摘要: {str(pack.get('vlm_summary_screen_state', '') or '')}\n"
                    f"VLM最后步骤摘要: {_lines_to_block(pack.get('vlm_summary_last_steps', []))}\n"
                    f"VLM目标相关信息:\n{relevant_info_block}\n"
                    f"VLM截图提取文本:\n{extracted_text_block}\n"
                    "说明：该结果已包含执行后的 VLM 摘要、任务相关信息提取和截图文本提取。"
                ),
            )
        ]

        image_block: ImageBlock | None = None
        if final_image_url:
            logger.info(f"[MobiAgent] 最后截图路径: {final_image_url}")
            image_block = {"type": "image", "source": {"type": "url", "url": final_image_url}}
            # 读取路径中的图片内容，转换为 base64 内嵌格式，避免后续使用时的文件访问问题。
            # try:
            #     with open(final_image_url, "rb") as f:
            #         image_data = f.read()
            #         image_block = ImageBlock(type="image", source={"type": "base64", "data": image_data})
            # except Exception:
            #     logger.warning("[MobiAgent] 读取最后截图失败", exc_info=True)
            #     # fallback: 如果读取失败，仍然返回路径信息供后续排查；但不提供图片内容。
            #     image_block = None
        else:
            logger.warning("[MobiAgent] 未获取到最后截图")
        if image_block is not None:
            content.append(image_block)

        return ToolResponse(content=content, metadata=pack)

    _reg(
        call_mobi_collect_with_report,
        func_description=(
            "执行手机任务，并返回 VLM 摘要/提取结果与最后结束时的手机截图。"
            "该工具不做重试与完成判定，Steward 基于结果继续后续流程。"
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

    _reg(delegate_to_worker, func_description="将子任务委派给 Worker Agent 并汇总返回结果。")

    sys_prompt = """你是 MobiClaw 手机操控 Agent，负责帮助用户操控手机，管理个人数据和日常事务。

## 你的职责
1. 理解用户的需求和指令
2. 规划并执行数据收集、存储、分析和操作的完整流程
3. 通过调用工具与手机操作Agent如MobiAgent（手机端）协作

## 工作流程规范
当用户要求进行数据整理或分析时，请严格按照以下步骤执行：

### 收集与验证 (Collect + Verify)
- 执行手机任务收集只允许使用 `call_mobi_collect_with_report` 工具
- 单一App原则：严禁将跨 App 的任务混在一个指令中。每个手机任务必须仅对应 1 个 App 的 1 种任务场景
- 禁止过度拆分：不要使用 plan 工具去拆分单个 App 内的微操，交给 MobiAgent 自行推理
- 工具层只做单次执行，不做重试管理；你需要基于返回结果继续后续流程
- 工具返回包含 VLM 页面摘要、任务相关信息提取和最后截图，请综合使用这些结果
- 避免使用plan工具拆分单个APP内部的任务，每个任务只能对应一个APP中的1种任务场景
- 若任务返回的文本内容和图片中获取收集结果；若未获取到结果，请在最终返回时明确说明未完成原因

### 存储 (Store)
- 使用 `store_steward_knowledge` 工具将收集到的信息存入管家知识库
- 确保所有有价值的信息都被持久化保存
- 需要输出日志等文件时，可用 "write_text_file" 落盘。

### 检索 (Retrieve)
- 查找之前存储的管家知识库（个人活动信息、对话记录等），使用 `search_steward_knowledge`
- 对外部页面查询可用 `fetch_url_text` 获取原始文本

### 分析 (Analyze)
- 根据管家知识库检索到的原始片段，自行分析总结待办事项、账单、重要提醒等

### 执行 (Execute)
- 如果分析发现需要执行的操作（如添加日程、设置提醒）
- 使用 `call_mobi_action` 工具在手机端执行相应操作

## 注意事项
- 每一任务情况都要向用户汇报进展
- 如果某一步失败，要尝试其他方法或向用户说明
- 在执行敏感操作前，需要用户确认（除非用户明确授权自动执行）
- 保持回复简洁专业，优先使用中文交流
- 即使过程产出了落盘文件，也必须在当前回复正文给出明确结论与关键内容；不能只回复文件路径。
- 如果任务主要是通用网页/论文检索，优先委派给 Worker，避免重复调用端侧工具
- 若路由层已明确指定本 Agent，仅处理职责范围内任务，不要无限自委派
- 如果工具调用返回 "[Tool Timeout]" 或 "[Tool Error]"，说明该工具执行超时或出错。此时你可以：
  (1) 尝试换一个替代方案或工具重试；
  (2) 如果没有替代方案或多次失败，应立即结束任务，向用户清楚说明失败原因（哪个工具、什么错误、影响了什么），不要无限重试。

## 示例对话
用户：开始今日的数据整理和分析
你应该：
1. 思考并调用 call_mobi_collect_with_report 获取单次执行结果（含 VLM 摘要、提取文本、最后截图）
2. 直接消费返回中的 VLM 页面摘要、目标相关信息和截图提取文本；不要声称工具“没有返回这些内容”
3. 基于结果继续完成后续分析与执行
4. 调用 store_steward_knowledge 存储收集到的信息
5. 调用 search_steward_knowledge 检索已存数据，自行分析待办和账单
6. 如发现待办事项，询问是否需要添加到日历，然后调用 call_mobi_action

现在，请准备好为用户服务！"""
    sys_prompt += _build_memory_prompt()
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Steward",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(promote_tool_result_images=True),
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
    _chat_reg = functools.partial(register_tool_with_timeout, toolkit)

    toolkit.create_tool_group(
        group_name="web_search",
        description="用于网页搜索的工具函数。",
        active=web_search_enabled,
        notes="""优先使用 brave_search 直接获取结果，若获取结果失败，再使用 fetch_* 工具尝试获取。""",
    )
    _chat_reg(
        brave_search,
        group_name="web_search",
        func_description="通过 Brave Search API 联网检索新闻与网页来源链接。",
    )

    _chat_reg(fetch_url_text, group_name="web_search", func_description="抓取指定 URL 的文本内容用于快速检索。")
    _chat_reg(fetch_url_readable_text, group_name="web_search", func_description="抓取并提取网页可读文本，用于快速理解页面内容。")
    _chat_reg(fetch_url_links, group_name="web_search", func_description="抓取网页并提取链接，用于发现相关来源并继续检索。")

    sys_prompt = """你是 MobiClaw 的基础对话助手,名字是 MobiChatBot。

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
