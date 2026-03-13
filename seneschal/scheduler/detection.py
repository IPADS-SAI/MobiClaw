# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..agents import _extract_text_from_model_response, create_openai_model
from .models import ScheduleDetectionResult, logger

_SCHEDULE_HINT_PATTERNS = [
    r"每[天日周月年]",
    r"每隔",
    r"定[时期]",
    r"[早晚]上?\s*\d",
    r"\d+[点时]\d*[分]?.*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:周|星期)[一二三四五六日天].*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:上午|下午|凌晨|中午).*(?:执行|运行|提醒|创建|搜|整理|汇总|发送|生成)",
    r"(?:明天|后天|大后天|下周|下个?月)",
    r"every\s+(?:day|week|month|hour|minute)",
    r"(?:daily|weekly|monthly|hourly)",
    r"at\s+\d+:\d+",
    r"(?:tomorrow|next\s+\w+day)",
    r"cron",
]


def _has_schedule_hints(text: str) -> bool:
    """快速预检：文本中是否包含可能的定时关键词。"""
    for pattern in _SCHEDULE_HINT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


_DETECT_PROMPT_TEMPLATE = """\
你是一个定时任务意图解析器。分析用户消息是否包含"在特定时间"或"按周期"执行任务的意图。

当前时间: {now}

注意区分：
- "帮我搜集新闻" -> 立即执行，不是定时任务
- "每天帮我搜集新闻" -> 定时任务（周期）
- "明天早上8点帮我搜集新闻" -> 定时任务（单次）
- "每周一提醒我开会" -> 定时任务（周期）

如果不是定时任务，只输出:
{{"is_scheduled": false}}

如果是定时任务，输出:
{{
  "is_scheduled": true,
  "core_task": "核心任务描述，需要去除时间相关的描述",
  "schedule_type": "once 或 cron",
  "cron_expr": "分 时 日 月 周几（仅 cron 类型，周几用 mon/tue/wed/thu/fri/sat/sun）",
  "run_at": "ISO 8601 datetime（仅 once 类型，如 2025-03-15T08:00:00）",
  "human_description": "人类可读的时间描述"
}}

cron_expr 中如果用户没有指定具体时间，默认使用早上 8:00。

示例:
用户: "每天帮我搜集新闻"
{{"is_scheduled":true,"core_task":"搜集新闻","schedule_type":"cron","cron_expr":"0 8 * * *","run_at":null,"human_description":"每天早上8:00"}}

用户: "每周一8:00创建本周安排"
{{"is_scheduled":true,"core_task":"创建本周安排","schedule_type":"cron","cron_expr":"0 8 * * mon","run_at":null,"human_description":"每周一早上8:00"}}

用户: "明天下午3点提醒我开会"
{{"is_scheduled":true,"core_task":"提醒我开会","schedule_type":"once","cron_expr":null,"run_at":"{tomorrow_3pm}","human_description":"明天下午3:00"}}

用户: "工作日每天下午6点总结当天工作"
{{"is_scheduled":true,"core_task":"总结当天工作","schedule_type":"cron","cron_expr":"0 18 * * mon-fri","run_at":null,"human_description":"工作日每天下午6:00"}}

用户: "帮我搜一下最新的AI论文"
{{"is_scheduled":false}}

只输出 JSON，不要输出其他文本。"""


async def detect_schedule_intent(
    task_text: str
) -> ScheduleDetectionResult:
    """通过 LLM 检测用户消息中的定时任务意图。
    """
    # if not _has_schedule_hints(task_text):
    #     return ScheduleDetectionResult(is_scheduled=False)

    try:
        now = datetime.now()
        tomorrow_3pm = (now + timedelta(days=1)).replace(
            hour=15, minute=0, second=0, microsecond=0,
        )

        sys_prompt = _DETECT_PROMPT_TEMPLATE.format(
            now=now.strftime("%Y-%m-%d %H:%M:%S"),
            tomorrow_3pm=tomorrow_3pm.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        model = create_openai_model(stream=False, temperature=0)
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": task_text},
        ]
        response = await model(messages)

        raw_text = _extract_text_from_model_response(response)
        parsed = _parse_json_from_text(raw_text)
        if not isinstance(parsed, dict):
            logger.warning("Schedule detection: non-dict output: %s", raw_text[:200])
            return ScheduleDetectionResult(is_scheduled=False)

        if not parsed.get("is_scheduled"):
            return ScheduleDetectionResult(is_scheduled=False)

        return ScheduleDetectionResult(
            is_scheduled=True,
            core_task=str(parsed.get("core_task") or task_text).strip(),
            schedule_type=str(parsed.get("schedule_type") or "cron").strip().lower(),
            cron_expr=parsed.get("cron_expr"),
            run_at=parsed.get("run_at"),
            human_description=str(parsed.get("human_description") or "").strip(),
        )

    except Exception as exc:
        logger.warning(
            "Schedule intent detection failed, treating as non-scheduled: %s", exc,
        )
        return ScheduleDetectionResult(is_scheduled=False)


def _parse_json_from_text(text: str) -> dict[str, Any] | None:
    """从 LLM 输出中提取 JSON 对象（支持 markdown 代码块）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    if "```" in raw:
        for chunk in raw.split("```"):
            candidate = chunk.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    left = raw.find("{")
    right = raw.rfind("}")
    if left >= 0 and right > left:
        try:
            parsed = json.loads(raw[left : right + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None
