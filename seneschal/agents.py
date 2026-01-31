# -*- coding: utf-8 -*-
"""Seneschal Agent 构建模块。"""

from __future__ import annotations

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit

from .config import MODEL_CONFIG
from .tools import (
    call_mobi_action,
    call_mobi_collect,
    weknora_add_knowledge,
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

    sys_prompt = """你是 Seneschal 智能管家系统的核心 Agent，负责帮助用户管理个人数据和日常事务。

## 你的职责
1. 理解用户的需求和指令
2. 规划并执行数据收集、存储、分析和操作的完整流程
3. 通过调用工具与 MobiAgent（手机端）和 WeKnora（知识库）协作

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
