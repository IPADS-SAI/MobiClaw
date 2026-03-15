from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


def _configure_logging() -> None:
    """Ensure gateway and orchestrator logs are visible under module startup."""
    level_name = (os.environ.get("MOBICLAW_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s : %(message)s",
        )
    else:
        # Keep existing handlers from uvicorn, only raise/lower threshold.
        root_logger.setLevel(level)

    logging.getLogger("mobiclaw").setLevel(level)


@dataclass
class GatewayConfig:
    """网关运行配置。"""

    api_key: str
    callback_timeout_s: float
    callback_retry: int
    callback_retry_backoff_s: float
    public_base_url: str | None
    file_root: str | None
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verification_token: str
    feishu_encrypt_key: str
    feishu_event_transport: str
    feishu_native_file_enabled: bool
    feishu_native_image_enabled: bool
    feishu_ack_enabled: bool
    feishu_group_require_mention: bool
    feishu_bot_open_id: str


def load_config() -> GatewayConfig:
    """从环境变量读取网关配置并构建配置对象。"""
    return GatewayConfig(
        api_key=os.environ.get("MOBICLAW_GATEWAY_API_KEY", ""),
        callback_timeout_s=float(os.environ.get("MOBICLAW_GATEWAY_CALLBACK_TIMEOUT", "10")),
        callback_retry=max(1, int(os.environ.get("MOBICLAW_GATEWAY_CALLBACK_RETRY", "3"))),
        callback_retry_backoff_s=float(os.environ.get("MOBICLAW_GATEWAY_CALLBACK_BACKOFF", "1.0")),
        public_base_url=(os.environ.get("MOBICLAW_GATEWAY_PUBLIC_BASE_URL") or "").strip() or None,
        file_root=(os.environ.get("MOBICLAW_GATEWAY_FILE_ROOT") or "").strip() or None,
        feishu_app_id=os.environ.get("FEISHU_APP_ID", "").strip(),
        feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", "").strip(),
        feishu_verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip(),
        feishu_encrypt_key=os.environ.get("FEISHU_ENCRYPT_KEY", "").strip(),
        feishu_event_transport=os.environ.get("FEISHU_EVENT_TRANSPORT", "both").strip().lower() or "both",
        feishu_native_file_enabled=os.environ.get("FEISHU_NATIVE_FILE_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_native_image_enabled=os.environ.get("FEISHU_NATIVE_IMAGE_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_ack_enabled=os.environ.get("FEISHU_ACK_ENABLED", "1").strip() not in {"0", "false", "False"},
        feishu_group_require_mention=os.environ.get("FEISHU_GROUP_REQUIRE_MENTION", "1").strip() not in {"0", "false", "False"},
        feishu_bot_open_id=os.environ.get("FEISHU_BOT_OPEN_ID", "").strip(),
    )


class ScheduleParam(BaseModel):
    """显式指定的定时任务参数（供 API 调用方使用）。"""

    schedule_type: str = Field(description="once（单次）或 cron（周期）")
    cron_expr: str | None = Field(default=None, description="5 字段 cron 表达式，周几用 mon-sun")
    run_at: str | None = Field(default=None, description="ISO 8601 datetime，仅 once 类型")
    description: str | None = Field(default=None, description="人类可读的时间描述")


class TaskRequest(BaseModel):
    """任务提交请求体。"""

    task: str
    async_mode: bool = Field(default=False)
    output_path: str | None = None
    mode: str = Field(default="chat")
    agent_hint: str | None = None
    skill_hint: str | None = None
    routing_strategy: str | None = None
    context_id: str | None = None
    web_search_enabled: bool = Field(default=True)
    webhook_url: str | None = None
    webhook_token: str | None = None
    callback_headers: dict[str, str] = Field(default_factory=dict)
    schedule: ScheduleParam | None = Field(default=None, description="显式定时参数，为空则自动检测")
    input_files: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    """任务状态与结果响应体。"""

    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class EnvContentRequest(BaseModel):
    """`.env` 文件更新请求体。"""

    content: str = Field(default="")


class EnvStructuredRequest(BaseModel):
    """结构化 `.env` 配置更新请求体。"""

    values: dict[str, str] = Field(default_factory=dict)
    unmanaged: dict[str, str] | None = None
    preserve_unmanaged: bool = Field(default=True)


@dataclass
class JobContext:
    """异步任务上下文（回调地址与飞书投递信息）。"""

    webhook_url: str | None = None
    webhook_token: str | None = None
    callback_headers: dict[str, str] | None = None
    feishu_chat_id: str | None = None
    feishu_user_open_id: str | None = None
    feishu_message_id: str | None = None
    feishu_receive_id_type: str = "chat_id"
