# -*- coding: utf-8 -*-
"""飞书消息查询工具。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
import requests


def _timeout_s() -> float:
    raw = (os.environ.get("MOBICLAW_GATEWAY_CALLBACK_TIMEOUT") or "10").strip()
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


def _build_feishu_history_error_response(
    *,
    error_kind: str,
    http_status: int,
    payload: dict[str, Any],
    chat_id: str,
    container_id_type: str,
    app_id_hint: str,
    history_range_requested: str,
    history_range_applied: str,
    diagnostic_hint: str = "",
) -> ToolResponse:
    """统一构造历史消息查询错误响应。"""
    text = f"[Feishu] 拉取历史消息失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}"
    if diagnostic_hint:
        text += f"\n[Feishu] 诊断提示: {diagnostic_hint}"
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        metadata={
            "error": error_kind,
            "http_status": http_status,
            "payload": payload,
            "chat_id": chat_id,
            "container_id_type": container_id_type,
            "app_id_hint": app_id_hint,
            "history_range_requested": history_range_requested,
            "history_range_applied": history_range_applied,
        },
    )


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


def _read_int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    """读取整数环境变量并约束范围。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))


def _validate_fetch_feishu_history_args(
    chat_id: str,
    container_id_type: str,
    history_range: str,
) -> tuple[dict[str, Any] | None, ToolResponse | None]:
    """统一校验历史消息查询入参，并返回规范化结果。"""
    cid = (chat_id or "").strip()
    normalized_container_id_type = (container_id_type or "").strip()

    errors: list[dict[str, str]] = []
    if not cid:
        errors.append({"field": "chat_id", "code": "empty", "message": "chat_id 不能为空"})
    elif _is_placeholder_chat_id(cid):
        errors.append(
            {
                "field": "chat_id",
                "code": "placeholder",
                "message": "chat_id 不能是占位符(auto/chat/user)",
            }
        )
    elif not _is_valid_container_id(cid):
        errors.append(
            {
                "field": "chat_id",
                "code": "invalid_format",
                "message": "chat_id 格式无效或疑似被截断（期望 oc_/ou_ 开头且完整）",
            }
        )

    if not _is_supported_container_id_type(container_id_type):
        errors.append(
            {
                "field": "container_id_type",
                "code": "unsupported",
                "message": "container_id_type 仅支持 auto/chat/user",
            }
        )

    normalized_range: str | None = None
    try:
        normalized_range = _normalize_history_range(history_range)
    except ValueError:
        errors.append(
            {
                "field": "history_range",
                "code": "unsupported",
                "message": "history_range 仅支持 today/yesterday/7d/all",
            }
        )

    if errors:
        detail = "；".join(f"{item['field']}: {item['message']}" for item in errors)
        return None, ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 参数校验失败: {detail}")],
            metadata={
                "error": "invalid_arguments",
                "errors": errors,
                "chat_id": cid,
                "container_id_type": normalized_container_id_type,
                "history_range": history_range,
            },
        )

    if normalized_range is None:
        return None, ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 参数校验失败: history_range 无效")],
            metadata={"error": "invalid_arguments", "chat_id": cid},
        )

    return {
        "chat_id": cid,
        "container_id_type": normalized_container_id_type,
        "history_range": normalized_range,
    }, None


def fetch_feishu_chat_history(
    chat_id: str,
    output_file_dir: str,
    download_files: bool = False,
    download_images: bool = False,
    page_size: int = 40,
    container_id_type: str = "auto",
    history_range: str = "today",
    page_token: str = "",
) -> ToolResponse:
    """读取飞书会话历史消息，支持 today/yesterday/7d/all 范围查询。

    Args:
        chat_id: 会话 ID（chat_id/open_chat_id/oc_xxx）。
        output_file_dir: 附件下载目录（建议传本次任务的 outputs/job_xxx/tmp）。
        download_files: 是否下载历史文件消息附件，默认 False。
        download_images: 是否下载历史图片消息附件，默认 False。
        page_size: 单次请求条数，内部会约束到允许范围。
        container_id_type: 容器类型，支持 auto/chat/open_chat/user/open_id/union_id。
        history_range: 历史时间范围，仅支持 today/yesterday/7d/all。
        page_token: 分页游标，用于续页拉取。
    """
    validated, validation_error = _validate_fetch_feishu_history_args(
        chat_id=chat_id,
        container_id_type=container_id_type,
        history_range=history_range,
    )
    if validation_error is not None:
        return validation_error
    if validated is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 参数校验失败")],
            metadata={"error": "invalid_arguments"},
        )

    cid = str(validated.get("chat_id") or "").strip()
    container_id_type = str(validated.get("container_id_type") or "").strip()
    normalized_range = str(validated.get("history_range") or "today").strip().lower()

    try:
        size = max(1, min(int(page_size), 50))
    except (TypeError, ValueError):
        size = 40

    range_start_s, range_end_s = _history_range_bounds_s(normalized_range)
    minimum_today_messages = 10
    fetch_page_budget = _read_int_env("FEISHU_HISTORY_FETCH_PAGE_BUDGET", 50, 1, 100)
    fetch_item_budget = _read_int_env("FEISHU_HISTORY_FETCH_ITEM_BUDGET", 500, 50, 10000)
    requested_page_token = (page_token or "").strip()
    desired_return_count = max(size, minimum_today_messages) if normalized_range == "today" else size
    should_download_files = bool(download_files)
    should_download_images = bool(download_images)

    resolved_container_id_type = _resolve_container_id_type(cid, container_id_type)
    app_hint = _app_id_hint()
    download_limit_env = os.environ.get("MOBICLAW_DOWNLOAD_MAX_BYTES", "50000000")
    try:
        download_max_bytes = max(1, int(download_limit_env))
    except ValueError:
        download_max_bytes = 50_000_000

    output_dir_raw = (output_file_dir or "").strip()
    if not output_dir_raw:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 参数校验失败: output_file_dir 不能为空")],
            metadata={
                "error": "invalid_arguments",
                "errors": [
                    {
                        "field": "output_file_dir",
                        "code": "empty",
                        "message": "output_file_dir 不能为空",
                    }
                ],
                "chat_id": cid,
            },
        )

    download_root_error = ""
    try:
        requested_output_dir = Path(output_dir_raw).expanduser()
        transformed_parts = list(requested_output_dir.parts)
        for idx in range(len(transformed_parts) - 1, -1, -1):
            if transformed_parts[idx] == "tmp":
                transformed_parts[idx] = "feishu_media"
                break
        transformed_output_dir = Path(*transformed_parts)
        download_tmp_dir = transformed_output_dir.resolve()
        download_tmp_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        download_tmp_dir = None
        download_root_error = str(exc)

    try:
        token = _get_tenant_token()

        def _safe_file_name(name: str, fallback: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()).strip("._")
            if not cleaned:
                cleaned = fallback
            return cleaned[:160]

        def _download_resource(
            *,
            message_id: str,
            resource_key: str,
            resource_type: str,
            display_name: str,
        ) -> tuple[str, str]:
            if download_tmp_dir is None:
                return "", f"download_dir_unavailable: {download_root_error or 'unknown'}"

            safe_mid = _safe_file_name(message_id, "om_unknown")
            fallback_name = f"{safe_mid}_{resource_type}"
            safe_name = _safe_file_name(display_name, fallback_name)
            if "." not in safe_name:
                safe_name = f"{safe_name}.{ 'bin' if resource_type == 'file' else 'img' }"

            local_path = download_tmp_dir / safe_name
            dedupe_index = 1
            while local_path.exists():
                stem = local_path.stem
                suffix = local_path.suffix
                local_path = download_tmp_dir / f"{stem}_{dedupe_index}{suffix}"
                dedupe_index += 1

            encoded_key = quote(resource_key, safe="")
            url = (
                "https://open.feishu.cn/open-apis/im/v1/messages/"
                f"{message_id}/resources/{encoded_key}?type={resource_type}"
            )

            try:
                resp = requests.get(url, headers=_headers(token), timeout=_timeout_s(), stream=True)
                payload = _parse_response_payload(resp)
                if resp.status_code >= 400:
                    return "", f"http_status={resp.status_code}, payload={json.dumps(payload, ensure_ascii=False)}"

                bytes_written = 0
                with local_path.open("wb") as handle:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if bytes_written > download_max_bytes:
                            handle.close()
                            try:
                                local_path.unlink()
                            except FileNotFoundError:
                                pass
                            return "", f"size_limit_exceeded: {download_max_bytes}"
                        handle.write(chunk)
                return str(local_path), ""
            except Exception as exc:  # noqa: BLE001
                try:
                    if local_path.exists():
                        local_path.unlink()
                except Exception:  # noqa: BLE001
                    pass
                return "", str(exc)

        def _fetch_segment(
            *,
            start_time_s: int | None,
            end_time_s: int | None,
            initial_page_token: str,
            stop_after_count: int | None,
        ) -> tuple[dict[str, Any] | None, ToolResponse | None]:
            messages: list[dict[str, Any]] = []
            current_token = (initial_page_token or "").strip()
            segment_has_more = False
            pages = 0
            raw_count = 0
            unparseable_count = 0
            segment_http_status = 200
            segment_payload: dict[str, Any] = {}

            for _ in range(fetch_page_budget):
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

                if segment_http_status >= 400 or api_code != 0:
                    if is_invalid_container:
                        error_kind = "invalid_container_id"
                    elif segment_http_status >= 400:
                        error_kind = "http_error"
                    else:
                        error_kind = "api_error"

                    return None, _build_feishu_history_error_response(
                        error_kind=error_kind,
                        http_status=segment_http_status,
                        payload=segment_payload,
                        chat_id=cid,
                        container_id_type=resolved_container_id_type,
                        app_id_hint=app_hint,
                        history_range_requested=history_range,
                        history_range_applied=normalized_range,
                        diagnostic_hint=diagnostic_hint,
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
                    message_id = item.get("message_id")
                    create_time_ms = _parse_message_create_time_ms(create_time)
                    if create_time_ms is None:
                        unparseable_count += 1

                    raw_content = body.get("content") or item.get("content")
                    if isinstance(raw_content, str):
                        raw_content_text = raw_content
                    elif raw_content is None:
                        raw_content_text = ""
                    else:
                        raw_content_text = json.dumps(raw_content, ensure_ascii=False)

                    content_type = "empty"
                    parsed_fields: dict[str, Any] = {}
                    if raw_content_text:
                        try:
                            parsed_content = json.loads(raw_content_text)
                        except json.JSONDecodeError:
                            parsed_content = None

                        if isinstance(parsed_content, dict):
                            text_content = str(parsed_content.get("text") or "").strip()
                            image_key = str(parsed_content.get("image_key") or "").strip()
                            file_key = str(parsed_content.get("file_key") or "").strip()
                            file_name = str(parsed_content.get("file_name") or parsed_content.get("name") or "").strip()

                            if text_content:
                                content_type = "text"
                                parsed_fields["text"] = text_content
                            elif image_key:
                                content_type = "image"
                                parsed_fields["image_key"] = image_key
                                if should_download_images and message_id:
                                    local_path, download_error = _download_resource(
                                        message_id=str(message_id),
                                        resource_key=image_key,
                                        resource_type="image",
                                        display_name=f"{message_id}_image",
                                    )
                                    if local_path:
                                        parsed_fields["local_path"] = local_path
                                    elif download_error:
                                        parsed_fields["download_error"] = download_error
                            elif file_key:
                                content_type = "file"
                                parsed_fields["file_key"] = file_key
                                if file_name:
                                    parsed_fields["file_name"] = file_name
                                if should_download_files and message_id:
                                    local_path, download_error = _download_resource(
                                        message_id=str(message_id),
                                        resource_key=file_key,
                                        resource_type="file",
                                        display_name=file_name or f"{message_id}_file",
                                    )
                                    if local_path:
                                        parsed_fields["local_path"] = local_path
                                    elif download_error:
                                        parsed_fields["download_error"] = download_error
                            else:
                                content_type = "json"
                                parsed_fields["content_json"] = parsed_content
                        else:
                            content_type = "plain"
                            parsed_fields["text"] = raw_content_text

                    messages.append(
                        {
                            "message_id": message_id,
                            "create_time": create_time,
                            "sender": item.get("sender"),
                            "message_type": item.get("msg_type") or item.get("message_type"),
                            "content": raw_content_text,
                            "content_type": content_type,
                            **parsed_fields,
                        }
                    )

                raw_count += len(items)
                if stop_after_count is not None and len(messages) >= stop_after_count:
                    break
                if raw_count >= fetch_item_budget:
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
            stop_after_count=desired_return_count,
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

        if normalized_range == "today" and today_count < desired_return_count:
            missing_today_messages = desired_return_count - today_count
            backfill, err = _fetch_segment(
                start_time_s=None,
                end_time_s=range_start_s,
                initial_page_token="",
                stop_after_count=missing_today_messages,
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
                    if len(messages) >= desired_return_count:
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

        simplified = messages[:desired_return_count]
        attachments: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        images: list[dict[str, Any]] = []
        for msg in simplified:
            if not isinstance(msg, dict):
                continue
            message_id = str(msg.get("message_id") or "").strip()
            create_time = msg.get("create_time")
            sender = msg.get("sender")

            image_key = str(msg.get("image_key") or "").strip()
            if image_key:
                image_item = {
                    "type": "image",
                    "message_id": message_id,
                    "create_time": create_time,
                    "sender": sender,
                    "image_key": image_key,
                    "local_path": str(msg.get("local_path") or "").strip(),
                    "download_error": str(msg.get("download_error") or "").strip(),
                }
                attachments.append(image_item)
                images.append(image_item)

            file_key = str(msg.get("file_key") or "").strip()
            if file_key:
                file_item = {
                    "type": "file",
                    "message_id": message_id,
                    "create_time": create_time,
                    "sender": sender,
                    "file_key": file_key,
                    "file_name": str(msg.get("file_name") or "").strip(),
                    "local_path": str(msg.get("local_path") or "").strip(),
                    "download_error": str(msg.get("download_error") or "").strip(),
                }
                attachments.append(file_item)
                files.append(file_item)

        preview = json.dumps(simplified[:5], ensure_ascii=False)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Feishu] 已获取 {len(simplified)} 条历史消息"
                        f"(range={normalized_range}, pages={pages_consumed}, sort=ByCreateTimeDesc)"
                        f"，附件 {len(attachments)} 个（文件 {len(files)}，图片 {len(images)}）"
                        f"，下载目录: {str(download_tmp_dir) if download_tmp_dir else '(unavailable)'}"
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
                "attachments": attachments,
                "files": files,
                "images": images,
                "download_dir": str(download_tmp_dir) if download_tmp_dir else "",
                "download_root_error": download_root_error,
                "download_max_bytes": download_max_bytes,
                "output_file_dir": output_dir_raw,
                "download_files": should_download_files,
                "download_images": should_download_images,
                "http_status": http_status,
                "payload": payload,
                "history_range_requested": history_range,
                "history_range_applied": normalized_range,
                "minimum_today_count": minimum_today_messages,
                "desired_return_count": desired_return_count,
                "fetch_page_budget": fetch_page_budget,
                "fetch_item_budget": fetch_item_budget,
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
    """按消息 ID 读取飞书消息详情。

    Args:
        message_id: 飞书消息 ID。
    """
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


def _format_local_time(dt: datetime) -> str:
    """格式化本地时间为可读文本。"""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _build_feishu_meeting_card_payload(
    *,
    topic: str,
    start_at: datetime,
    end_at: datetime,
    join_url: str,
    meeting_no: str,
    password: str,
) -> dict[str, Any]:
    """构造飞书简版会议卡片。"""
    fields = [
        {
            "is_short": False,
            "text": {"tag": "lark_md", "content": f"**开始时间**\n{_format_local_time(start_at)}"},
        },
        {
            "is_short": False,
            "text": {"tag": "lark_md", "content": f"**结束时间**\n{_format_local_time(end_at)}"},
        },
    ]
    if meeting_no:
        fields.append(
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**会议号**\n{meeting_no}"},
            }
        )
    if password:
        fields.append(
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**密码**\n{password}"},
            }
        )

    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"已预约会议：{topic}"},
        },
        "elements": [
            {"tag": "div", "fields": fields},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "加入会议"},
                        "type": "primary",
                        "url": join_url,
                    }
                ],
            },
        ],
    }


def _send_feishu_message_by_receive_id(
    *,
    token: str,
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content_payload: dict[str, Any],
) -> requests.Response:
    """按 receive_id 发送飞书消息。"""
    content = json.dumps(content_payload, ensure_ascii=False)
    return requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=_headers(token),
        json={"receive_id": receive_id, "msg_type": msg_type, "content": content},
        timeout=_timeout_s(),
    )


def _is_supported_receive_id_type(receive_id_type: str) -> bool:
    return (receive_id_type or "").strip().lower() in {"chat_id", "open_id", "union_id", "user_id"}


def _parse_local_datetime(start_time: str) -> datetime | None:
    """Parse local datetime string in YYYY-MM-DD HH:MM format."""
    try:
        return datetime.strptime((start_time or "").strip(), "%Y-%m-%d %H:%M").astimezone()
    except ValueError:
        return None


def schedule_feishu_meeting(
    topic: str,
    start_time: str,
    duration_minutes: int = 60,
    owner_open_id: str = "",
    user_id_type: str = "open_id",
) -> ToolResponse:
    """按显式时间参数预约飞书会议。

    Args:
        topic: 会议主题。
        start_time: 开始时间（格式 YYYY-MM-DD HH:MM）。
        duration_minutes: 会议时长（分钟），默认 60。
        owner_open_id: 可选，指定会议 owner 的 open_id。
        user_id_type: user_id 类型，默认 open_id。
    """
    normalized_topic = (topic or "").strip() or "飞书会议"
    normalized_user_id_type = (user_id_type or "open_id").strip() or "open_id"

    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError):
        duration = 60
    duration = max(15, min(duration, 480))

    start_at = _parse_local_datetime(start_time)
    if start_at is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 预约会议失败: start_time 格式需为 YYYY-MM-DD HH:MM")],
            metadata={"error": "invalid_start_time", "start_time": start_time, "topic": normalized_topic},
        )
    end_at = start_at + timedelta(minutes=duration)

    body: dict[str, Any] = {
        "end_time": str(int(end_at.timestamp())),
        "meeting_settings": {"topic": normalized_topic},
    }
    owner = (owner_open_id or "").strip()
    if owner:
        body["owner_id"] = owner

    try:
        token = _get_tenant_token()
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/vc/v1/reserves/apply?user_id_type={normalized_user_id_type}",
            headers=_headers(token),
            json=body,
            timeout=_timeout_s(),
        )
        payload = _parse_response_payload(resp)
        http_status = int(resp.status_code)

        if http_status >= 400:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 预约会议失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={
                    "error": "http_error",
                    "http_status": http_status,
                    "payload": payload,
                    "topic": normalized_topic,
                    "start_time": start_time,
                },
            )

        if payload.get("code") != 0:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 预约会议失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={
                    "error": "api_error",
                    "http_status": http_status,
                    "payload": payload,
                    "topic": normalized_topic,
                    "start_time": start_time,
                },
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        reserve = data.get("reserve") if isinstance(data.get("reserve"), dict) else {}
        result = {
            "reserve_id": str(reserve.get("id") or "").strip(),
            "meeting_url": str(reserve.get("url") or "").strip(),
            "meeting_no": str(reserve.get("meeting_no") or "").strip(),
            "password": str(reserve.get("password") or "").strip(),
            "app_link": str(reserve.get("app_link") or "").strip(),
            "topic": normalized_topic,
            "start_time": _format_local_time(start_at),
            "end_time": _format_local_time(end_at),
            "start_time_epoch_s": int(start_at.timestamp()),
            "end_time_epoch_s": int(end_at.timestamp()),
            "duration_minutes": duration,
        }
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"[Feishu] 会议预约成功：{normalized_topic}"
                        f"\n时间：{result['start_time']} - {result['end_time']}"
                        f"\n入会链接：{result['meeting_url'] or '(empty)'}"
                    ),
                )
            ],
            metadata={"meeting": result, "http_status": http_status, "payload": payload},
        )
    except requests.RequestException as exc:
        resp = getattr(exc, "response", None)
        payload = _parse_response_payload(resp) if isinstance(resp, requests.Response) else {}
        http_status = int(resp.status_code) if isinstance(resp, requests.Response) else None
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"[Feishu] 预约会议异常: http_status={http_status}, error={exc}, payload={json.dumps(payload, ensure_ascii=False)}",
                )
            ],
            metadata={"error": str(exc), "http_status": http_status, "payload": payload},
        )
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 预约会议异常: {exc}")],
            metadata={"error": str(exc)},
        )


def send_feishu_meeting_card(
    receive_id: str,
    topic: str,
    start_time: str,
    end_time: str,
    meeting_url: str,
    meeting_no: str = "",
    password: str = "",
    receive_id_type: str = "chat_id",
) -> ToolResponse:
    """发送飞书会议卡片消息。

    Args:
        receive_id: 飞书接收方 ID（群聊常用 chat_id）。
        topic: 会议主题。
        start_time: 开始时间（格式 YYYY-MM-DD HH:MM）。
        end_time: 结束时间（格式 YYYY-MM-DD HH:MM）。
        meeting_url: 入会链接。
        meeting_no: 可选，会议号。
        password: 可选，会议密码。
        receive_id_type: 接收方类型，默认 chat_id。
    """
    rid = (receive_id or "").strip()
    if not rid:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 发送会议卡片失败: receive_id 不能为空")],
            metadata={"error": "receive_id_empty"},
        )
    if not _is_supported_receive_id_type(receive_id_type):
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 发送会议卡片失败: receive_id_type 无效")],
            metadata={"error": "invalid_receive_id_type", "receive_id_type": receive_id_type},
        )
    if not (meeting_url or "").strip():
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 发送会议卡片失败: meeting_url 不能为空")],
            metadata={"error": "meeting_url_empty"},
        )

    start_dt = _parse_local_datetime(start_time)
    end_dt = _parse_local_datetime(end_time)
    if start_dt is None or end_dt is None:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 发送会议卡片失败: start_time/end_time 格式需为 YYYY-MM-DD HH:MM")],
            metadata={"error": "invalid_time_format", "start_time": start_time, "end_time": end_time},
        )
    if end_dt <= start_dt:
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 发送会议卡片失败: end_time 必须晚于 start_time")],
            metadata={"error": "invalid_time_range", "start_time": start_time, "end_time": end_time},
        )

    try:
        token = _get_tenant_token()
        card_payload = _build_feishu_meeting_card_payload(
            topic=(topic or "飞书会议").strip() or "飞书会议",
            start_at=start_dt,
            end_at=end_dt,
            join_url=(meeting_url or "").strip(),
            meeting_no=(meeting_no or "").strip(),
            password=(password or "").strip(),
        )
        resp = _send_feishu_message_by_receive_id(
            token=token,
            receive_id=rid,
            receive_id_type=(receive_id_type or "chat_id").strip(),
            msg_type="interactive",
            content_payload=card_payload,
        )
        payload = _parse_response_payload(resp)
        http_status = int(resp.status_code)

        if http_status >= 400:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 发送会议卡片失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={"error": "http_error", "http_status": http_status, "payload": payload},
            )

        if payload.get("code") != 0:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"[Feishu] 发送会议卡片失败: http_status={http_status}, payload={json.dumps(payload, ensure_ascii=False)}",
                    )
                ],
                metadata={"error": "api_error", "http_status": http_status, "payload": payload},
            )

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return ToolResponse(
            content=[TextBlock(type="text", text="[Feishu] 会议卡片发送成功")],
            metadata={
                "ok": True,
                "http_status": http_status,
                "payload": payload,
                "message_id": data.get("message_id"),
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
                    text=f"[Feishu] 发送会议卡片异常: http_status={http_status}, error={exc}, payload={json.dumps(payload, ensure_ascii=False)}",
                )
            ],
            metadata={"error": str(exc), "http_status": http_status, "payload": payload},
        )
    except Exception as exc:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"[Feishu] 发送会议卡片异常: {exc}")],
            metadata={"error": str(exc)},
        )
