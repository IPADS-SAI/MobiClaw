# -*- coding: utf-8 -*-
"""飞书消息查询工具。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
import requests


def _timeout_s() -> float:
    raw = (os.environ.get("SENESCHAL_GATEWAY_CALLBACK_TIMEOUT") or "10").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 10.0


def _get_tenant_token() -> str:
    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET is required")

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=_timeout_s(),
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        raise RuntimeError(f"Failed to get tenant token: {payload}")
    token = str(payload.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError("Empty tenant_access_token")
    return token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _parse_response_payload(resp: requests.Response) -> dict[str, Any]:
    """尽量解析响应体为 JSON；失败时返回文本包装。"""
    if not resp.content:
        return {}
    try:
        payload = resp.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:
        return {"raw_text": (resp.text or "").strip()[:2000]}


def _app_id_hint() -> str:
    """返回脱敏 app_id 片段，便于排查环境串用。"""
    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    if not app_id:
        return ""
    if len(app_id) <= 6:
        return app_id
    return f"{app_id[:3]}***{app_id[-3:]}"


def _resolve_container_id_type(container_id: str, container_id_type: str) -> str:
    """按前缀自动推断 container_id_type，或使用显式传参。"""
    explicit = (container_id_type or "").strip().lower()
    if explicit and explicit != "auto":
        return explicit

    cid = (container_id or "").strip().lower()
    if cid.startswith("oc_"):
        return "chat"
    if cid.startswith("ou_"):
        return "user"
    return "chat"


def _is_placeholder_chat_id(chat_id: str) -> bool:
    """识别常见占位输入，避免把类型值误当会话 ID。"""
    return (chat_id or "").strip().lower() in {"auto", "chat", "user"}


def _is_supported_container_id_type(container_id_type: str) -> bool:
    """校验 container_id_type 是否为支持值。"""
    return (container_id_type or "").strip().lower() in {"", "auto", "chat", "user"}


def _is_valid_container_id(container_id: str) -> bool:
    """校验 container_id 格式，避免截断 ID 直接请求飞书接口。"""
    cid = (container_id or "").strip().lower()
    if cid.startswith("oc_") or cid.startswith("ou_"):
        suffix = cid[3:]
        if len(suffix) < 16:
            return False
        return re.fullmatch(r"[0-9a-f]+", suffix) is not None
    return False


def _container_id_recovery_hint() -> str:
    """提供获取有效 container_id 的建议。"""
    return (
        "请使用真实且完整的会话 ID。可从飞书事件 message.chat_id 获取，"
        "或通过飞书群列表接口获取可访问群的 chat_id；"
        "避免手工复制被截断的 oc_/ou_ 值。"
    )


def _invalid_container_hint(container_id: str, container_id_type: str, app_id_hint: str) -> str:
    """构造 container_id 无效时的定向排障提示。"""
    pieces = [
        f"当前请求使用 container_id_type={container_id_type}",
        f"container_id={container_id}",
    ]
    if app_id_hint:
        pieces.append(f"当前 FEISHU_APP_ID(脱敏)={app_id_hint}")
    pieces.append("请确认 container_id 来自当前应用收到的飞书事件 message.chat_id（或对应 user id）")
    pieces.append("若 container_id 以 oc_ 开头，应优先使用 container_id_type=chat")
    pieces.append("若 container_id 以 ou_ 开头，应优先使用 container_id_type=user")
    pieces.append("若仍失败，通常是应用与群会话不匹配（应用不在群内、环境串用、跨租户）")
    return "；".join(pieces)


def _normalize_history_range(history_range: str) -> str:
    """规范化历史范围参数，仅支持固定枚举。"""
    normalized = (history_range or "today").strip().lower()
    if normalized in {"today", "yesterday", "7d", "all"}:
        return normalized
    raise ValueError("history_range 仅支持 today/yesterday/7d/all")


def _to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _parse_message_create_time_ms(raw: Any) -> int | None:
    """将飞书消息时间解析为 epoch 毫秒；无法解析时返回 None。"""
    if raw is None:
        return None

    text = str(raw).strip()
    if not text:
        return None

    if re.fullmatch(r"\d+", text):
        value = int(text)
        # 10 位通常是秒级时间戳，13 位是毫秒级。
        if value < 10_000_000_000:
            return value * 1000
        return value

    iso_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return _to_epoch_ms(parsed)


def _to_epoch_s(dt: datetime) -> int:
    return int(dt.timestamp())


def _history_range_bounds_s(normalized_range: str) -> tuple[int | None, int | None]:
    """返回 [start_s, end_s) 范围；all 返回 (None, None)。"""
    if normalized_range == "all":
        return None, None

    now_local = datetime.now().astimezone()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)
    seven_days_start = today_start - timedelta(days=6)

    if normalized_range == "today":
        return _to_epoch_s(today_start), _to_epoch_s(tomorrow_start)
    if normalized_range == "yesterday":
        return _to_epoch_s(yesterday_start), _to_epoch_s(today_start)
    if normalized_range == "7d":
        return _to_epoch_s(seven_days_start), _to_epoch_s(tomorrow_start)

    return None, None


def fetch_feishu_chat_history(
    chat_id: str,
    page_size: int = 20,
    container_id_type: str = "auto",
    history_range: str = "today",
    page_token: str = "",
) -> ToolResponse:
    """读取飞书会话历史消息，支持 today/yesterday/7d/all 范围查询。"""
    cid = (chat_id or "").strip()
    if not cid:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] chat_id 不能为空")],
            metadata={"error": "chat_id_empty"},
        )
    if _is_placeholder_chat_id(cid):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "[Feishu] chat_id 不能是占位符(auto/chat/user)。"
                        "请传真实会话 ID（如 oc_...）或用户 ID（如 ou_...）；"
                        "container_id_type 才能使用 auto/chat/user。"
                    ),
                )
            ],
            metadata={"error": "chat_id_placeholder", "chat_id": cid},
        )

    if not _is_supported_container_id_type(container_id_type):
        normalized = (container_id_type or "").strip()
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Feishu] container_id_type 不支持: {normalized}。"
                        "仅支持 auto/chat/user。"
                    ),
                )
            ],
            metadata={"error": "invalid_container_id_type", "container_id_type": normalized, "chat_id": cid},
        )

    if not _is_valid_container_id(cid):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Feishu] container_id 格式无效或疑似被截断: {cid}。"
                        "期望格式示例：oc_a0553eda9014c201e6969b478895c230。\n"
                        + _container_id_recovery_hint()
                    ),
                )
            ],
            metadata={"error": "invalid_container_id_format", "chat_id": cid},
        )

    try:
        size = max(1, min(int(page_size), 50))
    except (TypeError, ValueError):
        size = 20

    try:
        normalized_range = _normalize_history_range(history_range)
    except ValueError as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] history_range 无效: {history_range}。仅支持 today/yesterday/7d/all")],
            metadata={
                "error": "invalid_history_range",
                "history_range": history_range,
                "detail": str(exc),
                "chat_id": cid,
            },
        )

    range_start_s, range_end_s = _history_range_bounds_s(normalized_range)
    minimum_today_count = 10
    max_pages = 10
    max_raw_items = 500
    requested_page_token = (page_token or "").strip()
    target_count = max(size, minimum_today_count) if normalized_range == "today" else size

    resolved_container_id_type = _resolve_container_id_type(cid, container_id_type)
    app_hint = _app_id_hint()

    try:
        token = _get_tenant_token()
        def _fetch_segment(
            *,
            start_time_s: int | None,
            end_time_s: int | None,
            initial_page_token: str,
            need_count: int,
        ) -> tuple[dict[str, Any] | None, ToolResponse | None]:
            messages: list[dict[str, Any]] = []
            current_token = (initial_page_token or "").strip()
            segment_has_more = False
            pages = 0
            raw_count = 0
            unparseable_count = 0
            segment_http_status = 200
            segment_payload: dict[str, Any] = {}

            for _ in range(max_pages):
                params: dict[str, Any] = {
                    "container_id_type": resolved_container_id_type,
                    "container_id": cid,
                    "page_size": size,
                    "sort_type": "ByCreateTimeDesc",
                }
                if start_time_s is not None:
                    params["start_time"] = str(start_time_s)
                if end_time_s is not None:
                    params["end_time"] = str(end_time_s)
                if current_token:
                    params["page_token"] = current_token

                resp = requests.get(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    headers=_headers(token),
                    params=params,
                    timeout=_timeout_s(),
                )
                segment_payload = _parse_response_payload(resp)
                segment_http_status = int(resp.status_code)
                api_code = segment_payload.get("code") if isinstance(segment_payload, dict) else None
                api_msg = str(segment_payload.get("msg") or "") if isinstance(segment_payload, dict) else ""

                is_invalid_container = bool(api_code == 230001 and "invalid container_id" in api_msg.lower())
                diagnostic_hint = (
                    _invalid_container_hint(cid, resolved_container_id_type, app_hint) if is_invalid_container else ""
                )

                if segment_http_status >= 400:
                    text = (
                        f"[Feishu] 拉取历史消息失败: http_status={segment_http_status}, "
                        f"payload={json.dumps(segment_payload, ensure_ascii=False)}"
                    )
                    if diagnostic_hint:
                        text += f"\n[Feishu] 诊断提示: {diagnostic_hint}"
                    return None, ToolResponse(
                        content=[TextBlock(type="text", text=text)],
                        metadata={
                            "error": "invalid_container_id" if is_invalid_container else "http_error",
                            "http_status": segment_http_status,
                            "payload": segment_payload,
                            "chat_id": cid,
                            "container_id_type": resolved_container_id_type,
                            "app_id_hint": app_hint,
                            "history_range_requested": history_range,
                            "history_range_applied": normalized_range,
                        },
                    )

                if api_code != 0:
                    text = (
                        f"[Feishu] 拉取历史消息失败: http_status={segment_http_status}, "
                        f"payload={json.dumps(segment_payload, ensure_ascii=False)}"
                    )
                    if diagnostic_hint:
                        text += f"\n[Feishu] 诊断提示: {diagnostic_hint}"
                    return None, ToolResponse(
                        content=[TextBlock(type="text", text=text)],
                        metadata={
                            "error": "invalid_container_id" if is_invalid_container else "api_error",
                            "http_status": segment_http_status,
                            "payload": segment_payload,
                            "chat_id": cid,
                            "container_id_type": resolved_container_id_type,
                            "app_id_hint": app_hint,
                            "history_range_requested": history_range,
                            "history_range_applied": normalized_range,
                        },
                    )

                data = segment_payload.get("data") if isinstance(segment_payload.get("data"), dict) else {}
                items = data.get("items") if isinstance(data.get("items"), list) else []
                segment_has_more = bool(data.get("has_more"))
                current_token = str(data.get("page_token") or "")
                pages += 1

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    body = item.get("body") if isinstance(item.get("body"), dict) else {}
                    create_time = item.get("create_time")
                    create_time_ms = _parse_message_create_time_ms(create_time)
                    if create_time_ms is None:
                        unparseable_count += 1
                    messages.append(
                        {
                            "message_id": item.get("message_id"),
                            "create_time": create_time,
                            "sender": item.get("sender"),
                            "message_type": item.get("msg_type") or item.get("message_type"),
                            "content": body.get("content") or item.get("content"),
                        }
                    )

                raw_count += len(items)
                if len(messages) >= need_count:
                    break
                if raw_count >= max_raw_items:
                    break
                if not segment_has_more:
                    break
                if segment_has_more and not current_token:
                    break

            return (
                {
                    "messages": messages,
                    "has_more": segment_has_more,
                    "page_token": current_token,
                    "pages": pages,
                    "raw_count": raw_count,
                    "unparseable_count": unparseable_count,
                    "http_status": segment_http_status,
                    "payload": segment_payload,
                },
                None,
            )

        phase_one_token = "" if normalized_range == "today" else requested_page_token
        primary, err = _fetch_segment(
            start_time_s=range_start_s,
            end_time_s=range_end_s,
            initial_page_token=phase_one_token,
            need_count=target_count,
        )
        if err is not None:
            return err
        if primary is None:
            raise RuntimeError("unexpected empty fetch result")

        messages = list(primary.get("messages") or [])
        has_more = bool(primary.get("has_more"))
        current_page_token = str(primary.get("page_token") or "")
        pages_consumed = int(primary.get("pages") or 0)
        raw_items_count = int(primary.get("raw_count") or 0)
        unparseable_create_time_count = int(primary.get("unparseable_count") or 0)
        http_status = int(primary.get("http_status") or 200)
        payload = primary.get("payload") if isinstance(primary.get("payload"), dict) else {}

        today_count = len(messages) if normalized_range == "today" else 0
        extended_to_minimum = False

        if normalized_range == "today" and today_count < minimum_today_count:
            need_more = minimum_today_count - today_count
            backfill, err = _fetch_segment(
                start_time_s=None,
                end_time_s=range_start_s,
                initial_page_token="",
                need_count=need_more,
            )
            if err is not None:
                return err
            if backfill is not None:
                existing_ids = {str(item.get("message_id") or "") for item in messages}
                for item in list(backfill.get("messages") or []):
                    mid = str(item.get("message_id") or "")
                    if mid and mid in existing_ids:
                        continue
                    messages.append(item)
                    if mid:
                        existing_ids.add(mid)
                    if len(messages) >= minimum_today_count:
                        break

                if len(messages) > today_count:
                    extended_to_minimum = True
                has_more = bool(backfill.get("has_more"))
                current_page_token = str(backfill.get("page_token") or "")
                pages_consumed += int(backfill.get("pages") or 0)
                raw_items_count += int(backfill.get("raw_count") or 0)
                unparseable_create_time_count += int(backfill.get("unparseable_count") or 0)
                http_status = int(backfill.get("http_status") or http_status)
                payload = backfill.get("payload") if isinstance(backfill.get("payload"), dict) else payload

        simplified = messages[:target_count]

        preview = json.dumps(simplified[:5], ensure_ascii=False)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Feishu] 已获取 {len(simplified)} 条历史消息"
                        f"(range={normalized_range}, pages={pages_consumed}, sort=ByCreateTimeDesc)"
                        f"，预览: {preview}"
                    ),
                )
            ],
            metadata={
                "chat_id": cid,
                "container_id_type": resolved_container_id_type,
                "app_id_hint": app_hint,
                "count": len(simplified),
                "messages": simplified,
                "http_status": http_status,
                "payload": payload,
                "history_range_requested": history_range,
                "history_range_applied": normalized_range,
                "minimum_today_count": minimum_today_count,
                "today_count": today_count,
                "extended_to_minimum": extended_to_minimum,
                "requested_page_token": requested_page_token,
                "has_more": has_more,
                "page_token": current_page_token,
                "pages_consumed": pages_consumed,
                "raw_items_count": raw_items_count,
                "unparseable_create_time_count": unparseable_create_time_count,
            },
        )
    except requests.RequestException as exc:
        resp = getattr(exc, "response", None)
        payload = _parse_response_payload(resp) if isinstance(resp, requests.Response) else {}
        http_status = int(resp.status_code) if isinstance(resp, requests.Response) else None
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[Feishu] 拉取历史消息异常: http_status={http_status}, error={exc}, payload={json.dumps(payload, ensure_ascii=False)}",
                )
            ],
            metadata={
                "error": str(exc),
                "http_status": http_status,
                "payload": payload,
                "chat_id": cid,
                "container_id_type": resolved_container_id_type,
                "app_id_hint": app_hint,
            },
        )
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 拉取历史消息异常: {exc}")],
            metadata={"error": str(exc)},
        )


def get_feishu_message(message_id: str) -> ToolResponse:
    """按消息 ID 读取飞书消息详情。"""
    mid = (message_id or "").strip()
    if not mid:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] message_id 不能为空")],
            metadata={"error": "message_id_empty"},
        )

    try:
        token = _get_tenant_token()
        resp = requests.get(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{mid}",
            headers=_headers(token),
            timeout=_timeout_s(),
        )
        payload = _parse_response_payload(resp)
        http_status = int(resp.status_code)

        if http_status >= 400:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 读取消息失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={"error": "http_error", "http_status": http_status, "payload": payload, "message_id": mid},
            )

        if payload.get("code") != 0:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 读取消息失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={"error": "api_error", "http_status": http_status, "payload": payload, "message_id": mid},
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        item = data.get("items") if isinstance(data.get("items"), dict) else data
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 消息读取成功: {json.dumps(item, ensure_ascii=False)[:1200]}")],
            metadata={"message": item, "http_status": http_status, "payload": payload},
        )
    except requests.RequestException as exc:
        resp = getattr(exc, "response", None)
        payload = _parse_response_payload(resp) if isinstance(resp, requests.Response) else {}
        http_status = int(resp.status_code) if isinstance(resp, requests.Response) else None
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[Feishu] 读取消息异常: http_status={http_status}, error={exc}, payload={json.dumps(payload, ensure_ascii=False)}",
                )
            ],
            metadata={"error": str(exc), "http_status": http_status, "payload": payload, "message_id": mid},
        )
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 读取消息异常: {exc}")],
            metadata={"error": str(exc)},
        )
