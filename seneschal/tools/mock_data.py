# -*- coding: utf-8 -*-
"""Mock 数据生成工具。"""

from __future__ import annotations

from typing import Any


def get_mock_collect_result(task_desc: str) -> str:
    """生成模拟的数据收集结果。"""
    mock_responses = {
        "wechat": (
            "OCR识别结果:\n"
            "【工作群】明天下午3点有产品评审会议，请准备好材料\n"
            "【家庭群】周末聚餐定在周六晚上6点，地址：朝阳区xxx餐厅\n"
            "【账单通知】信用卡本月账单2580元，还款日1月25日\n"
            "【日程提醒】周日晚6点，请准时参加聚餐，避免迟到"
        ),
        "calendar": (
            "今日日程:\n"
            "- 10:00 晨会\n"
            "- 14:00 客户拜访\n"
            "- 16:30 周报整理"
        ),
        "notification": (
            "未读通知:\n"
            "- 快递：您的包裹已到达菜鸟驿站，待取件\n"
            "- 银行：您的账户入账5000元"
        ),
    }

    task_lower = task_desc.lower()
    if "wechat" in task_lower or "微信" in task_lower:
        return mock_responses["wechat"]
    if "calendar" in task_lower or "日历" in task_lower:
        return mock_responses["calendar"]
    if "notification" in task_lower or "通知" in task_lower:
        return mock_responses["notification"]

    return (
        f"模拟收集结果 (任务: {task_desc}):\n"
        "发现待办事项: 明天下午3点产品评审会议\n"
        "发现账单: 信用卡2580元，还款日1月25日\n"
        "发现日程提醒: 周六晚餐聚会\n"
        "发现通知: 一个包裹在驿站，待取件"
    )


def get_mock_rag_answer(query: str) -> str:
    """生成模拟的 RAG 分析结果。"""
    query_lower = query.lower()

    if "待办" in query_lower or "todo" in query_lower:
        return (
            "根据今日收集的信息，发现以下待办事项:\n"
            "1. 【紧急】明天下午3点产品评审会议 - 需准备材料\n"
            "2. 【重要】信用卡账单2580元，还款日1月25日\n"
            "3. 【一般】周六晚6点家庭聚餐，需确认出席"
        )
    if "账单" in query_lower or "消费" in query_lower:
        return (
            "账单分析结果:\n"
            "- 信用卡本月账单: 2580元\n"
            "- 还款截止日: 1月25日\n"
            "- 建议: 设置还款提醒，避免逾期"
        )
    if "总结" in query_lower or "分析" in query_lower:
        return (
            "今日数据整理总结:\n"
            "1. 共收集到3条重要信息\n"
            "2. 发现2个待办事项需要处理\n"
            "3. 有1笔账单需要关注\n"
            "4. 建议优先处理明天的会议准备"
        )

    return f"已基于知识库分析您的查询: {query}\n分析结果: 系统正常运行，数据收集完整。"


def get_mock_action_result(action_type: str, payload: Any) -> str:
    """生成模拟的操作执行结果。"""
    if action_type == "add_calendar_event":
        return "日历事件已成功添加！系统将在事件前15分钟提醒您。"
    if action_type == "send_message":
        return "消息发送成功！"
    if action_type == "set_reminder":
        return "提醒已设置成功！"
    if action_type == "open_app":
        return "应用已打开！"
    return f"操作 '{action_type}' 已模拟执行成功。"
