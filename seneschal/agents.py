# -*- coding: utf-8 -*-
"""Seneschal Agent 构建模块。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import json

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

from .config import MODEL_CONFIG, ROUTING_CONFIG
from .tools import (
    arxiv_search,
    brave_search,
    call_mobi_action,
    call_mobi_collect_verified,
    dblp_conference_search,
    download_file,
    extract_pdf_text,
    fetch_url_links,
    fetch_url_readable_text,
    fetch_url_text,
    run_shell_command,
    write_text_file,
    weknora_add_knowledge,
    weknora_knowledge_search,
    weknora_list_knowledge_bases,
    weknora_rag_chat,
)


def create_openai_model(*, stream: bool = True, temperature: float | None = None) -> OpenAIChatModel:
    """创建 OpenAI 兼容的聊天模型实例。"""
    api_base = MODEL_CONFIG["api_base"]
    if not api_base.startswith("http://") and not api_base.startswith("https://"):
        api_base = "http://" + api_base

    temp = MODEL_CONFIG["temperature"] if temperature is None else temperature
    return OpenAIChatModel(
        model_name=MODEL_CONFIG["model_name"],
        api_key=MODEL_CONFIG["api_key"],
        stream=stream,
        client_kwargs={"base_url": api_base},
        generate_kwargs={"temperature": temp},
    )


def _build_skill_prompt_suffix(skill_context: str | None) -> str:
    text = (skill_context or "").strip()
    if not text:
        return ""
    return (
        "\n\n[Activated Skills]\n"
        f"{text}\n"
        "使用方式：仅在与当前任务直接相关时参考这些技能约束；"
        "若不相关则忽略，不要为了使用技能而使用技能。"
    )


@dataclass
class AgentCapability:
    name: str
    role: str
    strengths: list[str]
    typical_tasks: list[str]
    boundaries: list[str]


def get_agent_capability_descriptions() -> dict[str, dict[str, object]]:
    registry = [
        AgentCapability(
            name="steward",
            role="负责手机端数据收集-存储-分析这一类特殊任务（Collect/Store/Analyze/Execute）",
            strengths=[
                "手机端数据采集与执行动作",
            ],
            typical_tasks=[
                "整理今日待办并决定是否执行手机操作",
                "采集微信信息后入库并生成建议",
            ],
            boundaries=[
                "不擅长大规模网页/论文检索",
                "通用检索类子任务建议委派给 worker",
            ],
        ),
        AgentCapability(
            name="worker",
            role="负责通用检索、网页阅读、学术资料收集和本地工具执行",
            strengths=[
                "Brave/网页/arXiv/DBLP 检索",
                "下载文件与 PDF 文本提取",
                "Shell 与本地文件写入",
            ],
            typical_tasks=[
                "检索最新论文并总结",
                "抓取网页并提炼可执行结论",
            ],
            boundaries=[
                "不直接执行手机 GUI 操作",
                "不负责 WeKnora 主流程编排",
            ],
        ),
    ]
    return {item.name: asdict(item) for item in registry}


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
        model=create_openai_model(stream=True, temperature=0.1),
        formatter=OpenAIChatFormatter(),
        # toolkit=Toolkit(),
        # memory=InMemoryMemory(),
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
        model=create_openai_model(stream=True, temperature=0.1),
        formatter=OpenAIChatFormatter(),
        # toolkit=Toolkit(),
        # memory=InMemoryMemory(),
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
        model=create_openai_model(stream=True, temperature=0.1),
        formatter=OpenAIChatFormatter(),
        max_iters=1,
    )


def create_worker_agent(skill_context: str | None = None) -> ReActAgent:
    """创建 Worker Agent，用于子任务委派。"""
    toolkit = Toolkit()

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
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
        write_text_file,
        func_description="写入本地文本文件，用于保存结果或日志。",
    )
    toolkit.register_tool_function(
        weknora_knowledge_search,
        func_description="在 WeKnora 知识库中检索已有信息（不做 LLM 总结）。",
    )

    sys_prompt = """你是 Seneschal 的 Worker Agent，负责处理通用问题与单一子任务。

工作准则：
- 只聚焦当前任务，给出简明直接的结果。
- 必要时使用工具检索或执行本地命令。
- 如果需要联网搜索新闻或网页来源，优先使用 brave_search 获取候选链接与摘要。
- 如果检索学术论文，优先使用 arxiv_search 获取元数据与 PDF 链接。
- 如果检索会议论文，优先使用 dblp_conference_search 获取论文清单与链接，然后去arxiv上搜索对应的论文。
- 如果任务中有今天，明天等相对日期的描述，你可以通过shell中的date命令，获取具体的日期。
- 拿到候选链接后，优先使用 fetch_url_readable_text 抓取正文；需要原始 HTML 时再使用 fetch_url_text。
- 需要从网页中发现相关链接时使用 fetch_url_links，再逐条抓取与筛选。
- 需要下载论文或附件时使用 download_file；阅读 PDF 用 extract_pdf_text。
- 需要输出文件时，可用 write_text_file 落盘。
- 输出格式遵循用户要求；未指定时默认使用 Markdown。
- 必须输出最终文本结论或可执行结果；不要输出空的工具调用。
- 不做多步长对话，输出最终结论或可执行结果。
- 若任务明显要求手机端采集/操作或完整 Collect-Store-Analyze-Execute 流程，应明确建议切换 steward 处理。
"""
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


def create_steward_agent(skill_context: str | None = None) -> ReActAgent:
    """创建智能管家 Agent (StewardAgent)。"""
    toolkit = Toolkit()
    retry_cap = max(0, min(int(os.environ.get("STEWARD_MOBI_MAX_RETRIES", "2")), 5))

    toolkit.register_tool_function(
        call_mobi_collect_verified,
        func_description=(
            "优先使用：调用 MobiAgent 收集手机任务结果（单次执行）。"
            "该工具不保证任务正确完成，也不会自动重试。"
            "返回统一结构化证据：截图路径、OCR文本、动作历史和推理历史，供 Agent 自主判断。"
        ),
    )

    toolkit.register_tool_function(
        call_mobi_action,
        func_description=(
            "指挥 MobiAgent 在手机端执行 GUI 操作。"
            "支持的操作例如: 'add_calendar_event'(添加日历事件), "
            "'send_message'(发送消息), 'set_reminder'(设置提醒), go_shop(下单购物)等。"
            "payload 参数为 JSON 格式字符串，如: "
            "'{\"title\": \"Meeting\", \"time\": \"15:00\", \"date\": \"2024-01-20\"}'。"
            "这是数据整理流程的最后一步，根据分析结果执行具体操作。"
        ),
    )

    toolkit.register_tool_function(
        weknora_add_knowledge,
        func_description=(
            "将收集到的信息存入 WeKnora 知识库。"
            "用于持久化保存 OCR 识别的文字、对话记录、账单信息等。"
            "输入要存储的文本内容，系统会将其加入知识库供后续检索分析。"
            "这是数据整理流程的第二步，应在收集数据后调用。"
        ),
    )

    toolkit.register_tool_function(
        weknora_rag_chat,
        func_description=(
            "基于 WeKnora 知识库进行 RAG 智能分析。"
            "用于分析待办事项、总结账单、回答基于历史记录的问题。"
            "输入分析查询如 '基于近日活动，有哪些待办事项？' 或 '分析本月消费账单'，"
            "返回基于知识库内容的智能分析结果。"
            "这是数据整理流程的第三步，应在存储数据后调用进行分析。"
        ),
    )

    

    toolkit.register_tool_function(
        weknora_knowledge_search,
        func_description="在 WeKnora 知识库中检索已有信息（不做 LLM 总结）。",
    )

    toolkit.register_tool_function(
        weknora_list_knowledge_bases,
        func_description="列出当前可用的 WeKnora 知识库。",
    )

    toolkit.register_tool_function(
        fetch_url_text,
        func_description="抓取指定 URL 的文本内容用于快速检索。",
    )

    toolkit.register_tool_function(
        run_shell_command,
        func_description="运行受限的本地命令行工具（白名单约束）。",
    )
    
    async def call_mobi_collect_with_retry_report(task_desc: str, success_criteria: str = "") -> ToolResponse:
        """执行带重试上限的 mobi 采集，并返回结构化证据包。"""
        attempts: list[dict[str, object]] = []
        current_task = task_desc
        criteria_matched = False

        for idx in range(1, retry_cap + 2):
            resp = await call_mobi_collect_verified(current_task, max_retries=0)
            md = (resp.metadata or {}) if resp else {}
            ocr_text = str(md.get("ocr_text", "") or "")
            last_reasoning = str(md.get("last_reasoning", "") or "")
            extracted_info = md.get("extracted_info", {}) if isinstance(md.get("extracted_info"), dict) else {}

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
                "tool_success": md.get("success", False),
            }
            attempts.append(attempt_item)

            if success_criteria:
                haystack = (
                    ocr_text
                    + "\n"
                    + last_reasoning
                    + "\n"
                    + json.dumps(extracted_info, ensure_ascii=False)
                )
                criteria_matched = success_criteria in haystack
            else:
                # 无显式标准时仅表示“已拿到证据”，不代表任务完成
                criteria_matched = False

            if criteria_matched:
                break

            if idx <= retry_cap:
                failure_reason = "criteria_not_matched"
                current_task = (
                    f"{task_desc}\n"
                    f"重试要求(第{idx}次失败，原因:{failure_reason})："
                    "请严格按目标完成后立即停止；避免重复无效操作；保留可验证证据。"
                )

        final_attempt = attempts[-1] if attempts else {}
        pack: dict[str, object] = {
            "report_type": "mobi_retry_evidence_pack_v1",
            "original_task": task_desc,
            "success_criteria": success_criteria,
            "retry_limit": retry_cap,
            "attempt_count": len(attempts),
            "criteria_matched": criteria_matched,
            "needs_agent_judgement": True,
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
                "next_action_recommendation": "agent_decide_retry_or_handoff",
            }

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
        """将子任务委派给 Worker Agent 并返回结果。"""
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
3. 通过调用工具与 手机操作Agent如MobiAgent（手机端）和 WeKnora（知识库）协作
4. 必要时委派子任务给 Worker Agent（例如快速检索或命令行检查）

## 工作流程规范
当用户要求进行数据整理或分析时，请严格按照以下步骤执行：

### 收集与验证 (Collect + Verify)
- 优先使用 `call_mobi_collect_with_retry_report` 执行手机任务并获取证据包
- 显式重试上限：最多 {retry_cap} 次重试（总尝试次数 {retry_cap + 1}）
- 必须基于返回证据（截图路径、OCR文本、动作/推理历史）自行判断任务是否完成
- 不要把工具返回中的状态提示当作最终真值；它只能作为参考
- 若任务未完成，你必须在上限内改写任务并重试；超限后停止继续操作
- 每次重试要在回复中说明失败依据与改写思路
- 例如：获取微信聊天截图、日历事件、通知消息等

### 失败报告模板 (Failure Pack)
- 达到重试上限仍未完成时，必须输出结构化失败证据包，字段至少包括：
- `report_type`, `original_task`, `retry_limit`, `attempt_count`, `attempts`, `failure_report`
- `failure_report` 内至少包含：
- `status`, `latest_run_dir`, `latest_index_file`, `latest_screenshot_path`, `latest_reasoning`, `latest_ocr_preview`, `next_action_recommendation`

### 存储 (Store)  
- 使用 `weknora_add_knowledge` 工具将收集到的信息存入知识库
- 确保所有有价值的信息都被持久化保存

### 分析 (Analyze)
- 使用 `weknora_rag_chat` 工具基于知识库进行智能分析
- 识别待办事项、账单、重要提醒等

### 检索 (Retrieve)
- 如果需要历史信息或材料，先用 `weknora_knowledge_search` 检索
- 对外部页面查询可用 `fetch_url_text` 获取原始文本

### 委派 (Delegate)
- 可将通用检索、浏览器查询或本地命令任务交给 `delegate_to_worker`
- 可将小任务交给 `delegate_to_worker`，减少主流程干扰
- 涉及联网新闻/网页检索时，优先委派 Worker 使用 `brave_search` 再抓取正文

### 执行 (Execute)
- 如果分析发现需要执行的操作（如添加日程、设置提醒）
- 使用 `call_mobi_action` 工具在手机端执行相应操作

## 注意事项
- 每一步都要向用户汇报进展
- 如果某一步失败，要尝试其他方法或向用户说明
- 在执行敏感操作前，需要用户确认（除非用户明确授权自动执行）
- 保持回复简洁专业，优先使用中文交流
- 如果任务主要是通用网页/论文检索，优先委派给 Worker，避免重复调用端侧工具
- 若路由层已明确指定本 Agent，仅处理职责范围内任务，不要无限自委派

## 示例对话
用户：开始今日的数据整理和分析
你应该：
1. 思考并调用 call_mobi_collect_with_retry_report 获取证据（含显式重试上限）
2. 基于证据自主判断是否完成；若未完成且已达上限，输出结构化失败证据包
3. 调用 weknora_add_knowledge 存储收集到的信息
4. 调用 weknora_rag_chat 分析待办和账单
5. 如发现待办事项，询问是否需要添加到日历，然后调用 call_mobi_action

现在，请准备好为用户服务！"""
    sys_prompt += _build_skill_prompt_suffix(skill_context)

    return ReActAgent(
        name="Steward",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=10,
    )


def create_user_agent() -> UserAgent:
    """创建用户代理 Agent。"""
    return UserAgent(name="User")
