from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import requests

from .files import _feishu_media_download_dir
from .models import GatewayConfig, TaskResult

logger = logging.getLogger(__name__)


def _parse_feishu_text_from_content(content: str) -> str:
    """解析飞书消息内容并提取文本字段。"""
    parsed = (content or "").strip()
    if not parsed:
        return ""
    try:
        payload = json.loads(parsed)
    except json.JSONDecodeError:
        return parsed
    return str(payload.get("text") or "").strip()


def _parse_feishu_content(content: str) -> dict[str, Any]:
    """解析飞书消息内容，支持 text/image/file。"""
    parsed = (content or "").strip()
    if not parsed:
        return {"type": "text", "text": "", "raw": ""}
    try:
        payload = json.loads(parsed)
    except json.JSONDecodeError:
        return {"type": "text", "text": parsed, "raw": parsed}

    text = str(payload.get("text") or "").strip()
    image_key = str(payload.get("image_key") or "").strip()
    file_key = str(payload.get("file_key") or "").strip()
    if text:
        return {"type": "text", "text": text, "raw": parsed}
    if image_key:
        return {"type": "image", "image_key": image_key, "raw": parsed}
    if file_key:
        return {"type": "file", "file_key": file_key, "raw": parsed}
    return {"type": "unknown", "raw": parsed}


def _is_image_file(path: str) -> bool:
    """判断本地文件是否为图片。"""
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("image/")


def _is_text_like_file(path: str, mime_type: str | None = None) -> bool:
    """判断文件是否更适合文本回传而非原生媒体上传。"""
    mime = (mime_type or mimetypes.guess_type(path)[0] or "").strip().lower()
    if mime.startswith("text/"):
        return True
    if mime in {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
    }:
        return True

    suffix = Path(path).suffix.lower()
    return suffix in {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".csv",
        ".tsv",
        ".log",
        ".xml",
    }


def _extract_open_id_from_mention(mention: Any) -> str:
    """从飞书 mention 结构中提取 open_id（兼容 dict/SDK 对象）。"""
    if isinstance(mention, dict):
        ident = mention.get("id") if isinstance(mention.get("id"), dict) else {}
        return str(ident.get("open_id") or "").strip()

    ident = getattr(mention, "id", None)
    if ident is None:
        return ""
    return str(getattr(ident, "open_id", "") or "").strip()


def _extract_mentioned_open_ids(mentions: Any, content: str) -> set[str]:
    """抽取消息中被 @ 的 open_id，缺失时尝试从内容判定是否存在 at 标签。"""
    result: set[str] = set()
    if isinstance(mentions, list):
        for mention in mentions:
            oid = _extract_open_id_from_mention(mention)
            if oid:
                result.add(oid)

    if result:
        return result

    raw = (content or "").strip()
    if "<at " in raw or "@" in raw:
        return {"__any_mention__"}
    return set()


def _should_accept_feishu_message(
    cfg: GatewayConfig,
    *,
    chat_type: str | None,
    content: str,
    mentions: Any,
) -> tuple[bool, str | None]:
    """决定是否处理飞书消息：群聊默认要求 @ 机器人。"""
    if not cfg.feishu_group_require_mention:
        return True, None

    normalized_chat_type = (chat_type or "").strip().lower()
    if normalized_chat_type and normalized_chat_type != "group":
        return True, None

    mentioned_ids = _extract_mentioned_open_ids(mentions, content)
    if not mentioned_ids:
        return False, "group_message_without_mention"

    bot_open_id = (cfg.feishu_bot_open_id or "").strip()
    if not bot_open_id:
        return True, None

    if bot_open_id in mentioned_ids:
        return True, None
    return False, "mentioned_other_user_not_bot"


def _verify_feishu_signature(
    raw_body: bytes,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    cfg: GatewayConfig,
) -> bool:
    """校验飞书请求签名。"""
    if not cfg.feishu_encrypt_key:
        return True
    if not timestamp or not nonce or not signature:
        return False
    string_to_sign = f"{timestamp}{nonce}{cfg.feishu_encrypt_key}{raw_body.decode('utf-8', errors='replace')}"
    digest = hmac.new(
        cfg.feishu_encrypt_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def _should_start_feishu_long_conn(cfg: GatewayConfig) -> bool:
    """根据配置判断是否启用飞书长连接监听。"""
    mode = (cfg.feishu_event_transport or "both").strip().lower()
    if mode in {"off", "disabled", "none", "webhook"}:
        return False
    if mode in {"long_conn", "long-connection", "ws", "both"}:
        return True
    if mode == "auto":
        return True
    logger.warning("Unknown FEISHU_EVENT_TRANSPORT=%s, fallback to both", mode)
    return True


def _build_feishu_text(result: TaskResult) -> str:
    """将任务结果转换为适合飞书发送的文本。"""
    if result.status == "failed":
        return f"任务执行失败: {result.error or 'unknown error'}"
    output = result.result or {}
    text = str(output.get("reply") or "").strip() or "任务执行完成。"
    files = output.get("files") if isinstance(output, dict) else []
    if isinstance(files, list) and files:
        lines = [text, "", "文件结果:"]
        for item in files:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or "file"
            url = item.get("download_url") or ""
            lines.append(f"- {name}: {url}")
        return "\n".join(lines)
    return text


def _get_feishu_tenant_token(cfg: GatewayConfig) -> str | None:
    """获取飞书租户访问令牌。"""
    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        return None
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": cfg.feishu_app_id, "app_secret": cfg.feishu_app_secret},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        return None
    return payload.get("tenant_access_token")


def _send_feishu_message(
    cfg: GatewayConfig,
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content_payload: dict[str, Any],
) -> None:
    """发送飞书通用消息。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return
    content = json.dumps(content_payload, ensure_ascii=False)
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": receive_id, "msg_type": msg_type, "content": content},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()


def _send_feishu_text(cfg: GatewayConfig, receive_id: str, receive_id_type: str, text: str) -> None:
    """发送飞书文本消息。"""
    _send_feishu_message(cfg, receive_id, receive_id_type, "text", {"text": text})


def _feishu_response_debug_body(resp: requests.Response, max_len: int = 1200) -> str:
    """提取飞书响应体调试文本（优先 JSON，降级 text）。"""
    body = ""
    try:
        body = json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        body = (resp.text or "").strip()
    if len(body) > max_len:
        return body[:max_len] + "..."
    return body


def _upload_feishu_file(cfg: GatewayConfig, file_path: str) -> str | None:
    """上传本地文件到飞书，返回 file_key。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return None
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None
    with path.open("rb") as fp:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data={"file_type": "stream", "file_name": path.name},
            files={"file": (path.name, fp, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
            timeout=cfg.callback_timeout_s,
        )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("file_key") or "").strip() or None


def _upload_feishu_image(cfg: GatewayConfig, image_path: str) -> str | None:
    """上传本地图片到飞书，返回 image_key。"""
    token = _get_feishu_tenant_token(cfg)
    if not token:
        return None
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return None
    with path.open("rb") as fp:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (path.name, fp, mimetypes.guess_type(path.name)[0] or "image/png")},
            timeout=cfg.callback_timeout_s,
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        logger.warning(
            "Feishu image upload http_error status=%s path=%s response=%s",
            resp.status_code,
            image_path,
            _feishu_response_debug_body(resp),
        )
        raise
    payload = resp.json() if resp.content else {}
    if payload.get("code") != 0:
        logger.warning(
            "Feishu image upload api_error code=%s msg=%s path=%s response=%s",
            payload.get("code"),
            payload.get("msg"),
            image_path,
            _feishu_response_debug_body(resp),
        )
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("image_key") or "").strip() or None


def _send_feishu_file(cfg: GatewayConfig, receive_id: str, receive_id_type: str, file_path: str) -> bool:
    """发送飞书原生文件消息。"""
    file_key = _upload_feishu_file(cfg, file_path)
    if not file_key:
        return False
    _send_feishu_message(cfg, receive_id, receive_id_type, "file", {"file_key": file_key})
    return True


def _send_feishu_image(cfg: GatewayConfig, receive_id: str, receive_id_type: str, image_path: str) -> bool:
    """发送飞书原生图片消息。"""
    image_key = _upload_feishu_image(cfg, image_path)
    if not image_key:
        return False
    _send_feishu_message(cfg, receive_id, receive_id_type, "image", {"image_key": image_key})
    return True


def _send_feishu_ack(cfg: GatewayConfig, receive_id: str, receive_id_type: str) -> None:
    """发送受理反馈。"""
    _send_feishu_text(cfg, receive_id, receive_id_type, "已收到消息，正在处理中，请稍候。")


def _download_feishu_message_resource(
    cfg: GatewayConfig,
    *,
    message_id: str,
    resource_key: str,
    resource_type: str,
    download_dir: str | None = None,
) -> str | None:
    """按消息资源接口下载飞书图片/文件。"""
    token = _get_feishu_tenant_token(cfg)
    if not token or not message_id or not resource_key:
        return None

    kind = resource_type if resource_type in {"image", "file"} else "file"
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{resource_key}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"type": kind},
        timeout=cfg.callback_timeout_s,
    )
    resp.raise_for_status()

    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(content_type) or (".png" if kind == "image" else ".bin")
    if not ext.startswith("."):
        ext = ".bin"
    if download_dir:
        media_root = Path(download_dir).expanduser()
        media_root.mkdir(parents=True, exist_ok=True)
    else:
        media_root = _feishu_media_download_dir()
    save_path = media_root / f"{kind}_{message_id}_{resource_key}{ext}"
    save_path.write_bytes(resp.content)
    return str(save_path)


def _build_task_from_feishu_event(
    content: str,
    message_id: str | None,
    cfg: GatewayConfig,
    *,
    download_dir: str | None = None,
) -> str:
    """将飞书消息转成可执行任务文本。"""
    parsed = _parse_feishu_content(content)
    msg_type = str(parsed.get("type") or "text")
    if msg_type == "text":
        return str(parsed.get("text") or "").strip()

    if msg_type in {"image", "file"} and message_id:
        key_name = "image_key" if msg_type == "image" else "file_key"
        resource_key = str(parsed.get(key_name) or "").strip()
        if not resource_key:
            return ""
        try:
            local_path = _download_feishu_message_resource(
                cfg,
                message_id=message_id,
                resource_key=resource_key,
                resource_type=msg_type,
                download_dir=download_dir,
            )
        except Exception as exc:
            logger.warning("Failed to download Feishu %s resource: %s", msg_type, exc)
            local_path = None

        if local_path:
            if msg_type == "image":
                return f"请分析这张图片内容并给出结论。图片本地路径: {local_path}"
            return f"请读取并总结这个文件内容。文件本地路径: {local_path}"
        return f"收到飞书{msg_type}消息，资源键: {resource_key}。请提示用户重试或改为文字描述。"

    return ""
