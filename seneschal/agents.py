# -*- coding: utf-8 -*-
"""Seneschal Agent 构建模块。"""

from __future__ import annotations

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

from .config import MODEL_CONFIG
from .tools import (
    brave_search,
    call_mobi_action,
    call_mobi_collect,
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


def create_openai_model() -> OpenAIChatModel:
    """创建 OpenAI 兼容的聊天模型实例。"""
    api_base = MODEL_CONFIG["api_base"]
    if not api_base.startswith("http://") and not api_base.startswith("https://"):
        api_base = "http://" + api_base

    return OpenAIChatModel(
        model_name=MODEL_CONFIG["model_name"],
        api_key=MODEL_CONFIG["api_key"],
        stream=True,
        client_kwargs={"base_url": api_base},
        generate_kwargs={"temperature": MODEL_CONFIG["temperature"]},
    )


def create_worker_agent() -> ReActAgent:
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
- 如果任务中有今天，明天等相对日期的描述，你可以通过shell中的date命令，获取具体的日期。
- 拿到候选链接后，优先使用 fetch_url_readable_text 抓取正文；需要原始 HTML 时再使用 fetch_url_text。
- 需要从网页中发现相关链接时使用 fetch_url_links，再逐条抓取与筛选。
- 需要输出文件时，可用 write_text_file 落盘。
- 不做多步长对话，输出最终结论或可执行结果。
"""

    return ReActAgent(
        name="Worker",
        sys_prompt=sys_prompt,
        model=create_openai_model(),
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=6,
    )


def create_steward_agent() -> ReActAgent:
    """创建智能管家 Agent (StewardAgent)。"""
    toolkit = Toolkit()

    toolkit.register_tool_function(
        call_mobi_collect,
        func_description=(
            "调用 MobiAgent 从手机端收集数据。"
            "用于获取微信聊天截图、日历事件、通知消息等信息。"
            "输入任务描述如 'Screenshot WeChat top chat' 或 '获取今日微信置顶聊天截图'，"
            "返回收集到的数据和 OCR 识别结果。"
            "这是数据整理流程的第一步。"
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
        call_mobi_action,
        func_description=(
            "指挥 MobiAgent 在手机端执行 GUI 操作。"
            "支持的操作类型: 'add_calendar_event'(添加日历事件), "
            "'send_message'(发送消息), 'set_reminder'(设置提醒)。"
            "payload 参数为 JSON 格式字符串，如: "
            "'{\"title\": \"Meeting\", \"time\": \"15:00\", \"date\": \"2024-01-20\"}'。"
            "这是数据整理流程的最后一步，根据分析结果执行具体操作。"
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

    async def delegate_to_worker(task: str) -> ToolResponse:
        """将子任务委派给 Worker Agent 并返回结果。"""
        worker = create_worker_agent()
        msg = Msg(name="User", content=task, role="user")
        response = await worker(msg)
        text = response.get_text_content() if response else ""
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Worker 结果]\n{text}")],
            metadata={"task": task},
        )

    toolkit.register_tool_function(
        delegate_to_worker,
        func_description="将子任务委派给 Worker Agent 并汇总返回结果。",
    )

    sys_prompt = """你是 Seneschal 智能管家系统的核心 Agent，负责帮助用户管理个人数据和日常事务。

## 你的职责
1. 理解用户的需求和指令
2. 规划并执行数据收集、存储、分析和操作的完整流程
3. 通过调用工具与 MobiAgent（手机端）和 WeKnora（知识库）协作
4. 必要时委派子任务给 Worker Agent（例如快速检索或命令行检查）

## 工作流程规范
当用户要求进行数据整理或分析时，请严格按照以下步骤执行：

### 第一步：收集 (Collect)
- 使用 `call_mobi_collect` 工具从手机端获取原始数据
- 例如：获取微信聊天截图、日历事件、通知消息等

### 第二步：存储 (Store)  
- 使用 `weknora_add_knowledge` 工具将收集到的信息存入知识库
- 确保所有有价值的信息都被持久化保存

### 第三步：分析 (Analyze)
- 使用 `weknora_rag_chat` 工具基于知识库进行智能分析
- 识别待办事项、账单、重要提醒等

### 补充：检索 (Retrieve)
- 如果需要历史信息或材料，先用 `weknora_knowledge_search` 检索
- 对外部页面查询可用 `fetch_url_text` 获取原始文本

### 补充：委派 (Delegate)
- 可将通用检索、浏览器查询或本地命令任务交给 `delegate_to_worker`
- 可将小任务交给 `delegate_to_worker`，减少主流程干扰
- 涉及联网新闻/网页检索时，优先委派 Worker 使用 `brave_search` 再抓取正文

### 第四步：执行 (Execute)
- 如果分析发现需要执行的操作（如添加日程、设置提醒）
- 使用 `call_mobi_action` 工具在手机端执行相应操作

## 注意事项
- 每一步都要向用户汇报进展
- 如果某一步失败，要尝试其他方法或向用户说明
- 在执行操作前，需要用户确认（除非用户明确授权自动执行）
- 保持回复简洁专业，使用中文交流

## 示例对话
用户：开始今日的数据整理和分析
你应该：
1. 思考并调用 call_mobi_collect 获取今日数据
2. 调用 weknora_add_knowledge 存储收集到的信息
3. 调用 weknora_rag_chat 分析待办和账单
4. 如发现待办事项，询问是否需要添加到日历，然后调用 call_mobi_action

现在，请准备好为用户服务！"""

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
