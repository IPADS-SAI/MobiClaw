from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .session import _chat_upload_root_dir

if TYPE_CHECKING:
    from fastapi import Request

    from .models import GatewayConfig


def _gateway_override(name: str, default: Any) -> Any:
    try:
        from .. import gateway_server
    except Exception:
        return default
    return getattr(gateway_server, name, default)


def _feishu_media_download_dir() -> Path:
    """返回飞书媒体缓存目录。"""
    configured = (os.environ.get("FEISHU_MEDIA_DOWNLOAD_DIR") or "").strip()
    path = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "seneschal_feishu_media"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _resolve_file_root(cfg: GatewayConfig) -> Path | None:
    """解析允许暴露下载文件的根目录。"""
    if not cfg.file_root:
        return None
    return Path(cfg.file_root).expanduser().resolve()


def _default_exposed_roots() -> list[Path]:
    """返回网关内置允许暴露下载的安全目录。"""
    project_root = Path(__file__).resolve().parents[2]
    chat_upload_root = _gateway_override("_chat_upload_root_dir", _chat_upload_root_dir)
    feishu_media_root = _gateway_override("_feishu_media_download_dir", _feishu_media_download_dir)
    roots = [
        (project_root / "outputs").resolve(),
        chat_upload_root().resolve(),
        feishu_media_root().resolve(),
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _can_expose_file(path: str, cfg: GatewayConfig) -> bool:
    """判断文件是否允许通过下载接口暴露。"""
    root = _resolve_file_root(cfg)
    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except FileNotFoundError:
        return False
    if not resolved.exists() or not resolved.is_file():
        return False
    default_roots = _gateway_override("_default_exposed_roots", _default_exposed_roots)()
    if any(resolved == item or item in resolved.parents for item in default_roots):
        return True
    if root is None:
        return True
    return resolved == root or root in resolved.parents


def _build_download_url(job_id: str, file_name: str, request: Request | None, cfg: GatewayConfig) -> str:
    """构建文件下载 URL。"""
    base = cfg.public_base_url
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    if not base:
        return f"/api/v1/files/{job_id}/{file_name}"
    return f"{base}/api/v1/files/{job_id}/{file_name}"


def _decorate_result_with_files(job_id: str, result: dict[str, Any], request: Request | None, cfg: GatewayConfig) -> dict[str, Any]:
    """为结果中的文件条目补充安全过滤后的下载链接。"""
    files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(files, list):
        return result
    enriched: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or "").strip()
        if not path or not name:
            continue
        if not _can_expose_file(path, cfg):
            continue
        enriched_item = dict(item)
        enriched_item["download_url"] = _build_download_url(job_id, name, request, cfg)
        enriched.append(enriched_item)
    result = dict(result)
    result["files"] = enriched
    return result
