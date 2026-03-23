# MobiClaw Gateway Server 模块解读

本文档面向维护者，解释 `mobiclaw/gateway_server` 目录下各模块职责、主调用链和扩展点。

## 1. 目录总览

- `__main__.py`
  - 网关启动入口。
  - 从环境变量读取 `MOBICLAW_GATEWAY_HOST` 和 `MOBICLAW_GATEWAY_PORT`，调用 uvicorn 启动 `mobiclaw.gateway_server:app`。

- `__init__.py`
  - 网关装配层（模块聚合器）。
  - 负责：加载环境变量、配置日志、创建 FastAPI app、定义生命周期（scheduler、Feishu 长连接、MCP 服务器加载与关闭）、注册 API 路由。
  - 通过 `globals().update(register_routes(app))` 将路由函数暴露到模块级，配合 `_gateway_override` 提供可替换实现。

- `models.py`
  - 配置和请求/响应模型定义。
  - 包含：`GatewayConfig`、`TaskRequest`、`TaskResult`、`JobContext`、`ScheduleParam`、环境设置请求模型、设备心跳模型。
  - `load_config()` 统一读取环境变量。

- `api.py`
  - HTTP 路由定义与编排。
  - 主要覆盖：健康检查、console 页面、任务提交与查询、会话管理、调度管理、文件下载、Feishu webhook 入口、MCP 管理、设备管理。
  - 通过 `register_routes(app)` 把路由注册到 FastAPI。

- `api_env.py`
  - `.env` 读写相关 API。
  - 包含原文读写与结构化 schema 读写两套接口。

- `runtime.py`
  - 任务执行运行时核心。
  - 负责异步作业执行、进度上报、结果回调（webhook/Feishu）、调度任务触发执行。

- `events.py`
  - Feishu 事件入口处理（webhook 与长连接共用核心逻辑）。
  - 包含消息去重、受理判定、转任务入队、回执发送、长连接监听线程启动。

- `feishu.py`
  - Feishu 协议与消息发送/下载适配层。
  - 负责：签名校验、提及过滤、tenant token 获取、文本/卡片/文件/图片消息发送、消息资源下载、Feishu 事件内容转任务文本。

- `session.py`
  - Chat 会话与历史管理。
  - 负责：context_id 规范化、session 目录命名与解析、history.jsonl 读写、上传文件路径注入任务。

- `files.py`
  - 结果文件可见性和下载链接生成。
  - 负责：暴露根目录安全校验、结果中 `files` 的下载地址填充。

- `env.py`
  - `.env` 文件处理与设置 schema。
  - 包含 schema 定义、解析、清洗、渲染、分组逻辑。

- `devices.py`
  - 设备注册与 ADB 连接管理。
  - 包含：设备持久化、心跳更新、启动时重连、退出时断连、adbutils 依赖兼容处理。

## 2. 核心调用链

### 2.1 普通任务调用链（/api/v1/task）

1. `api.py` 接收 `TaskRequest`。
2. 若是同步请求：直接调用 `run_gateway_task`，得到结果后做文件装饰并返回。
3. 若是异步请求：创建 `job_id` 写入 `_JOB_STORE`，并通过 `runtime._run_job` 在后台执行。
4. `runtime._run_job` 调用 `run_gateway_task`，更新 job 状态，必要时落 RAG 历史，再调用 `_deliver_result` 做 webhook/Feishu 投递。

### 2.2 Feishu 事件调用链

1. `api.py` 的 `/api/v1/feishu/events` 负责 webhook 接收与基础校验。
2. 调用 `events._accept_feishu_message`：
   - 提及过滤与机器人判定。
   - message_id 去重 claim。
   - 创建 reserved job 输出目录和临时目录。
   - 通过 `feishu._build_task_from_feishu_event` 将文本/图片/文件事件转成任务文本。
3. 调用 `events._enqueue_feishu_job` 入队后台执行。
4. `runtime._run_job` 执行完成后，`runtime._deliver_result` 推送结果。

## 3. 路由职责清单

### 3.1 UI 与基础

- `GET /health`
- `GET /`（重定向 `/console/chat`）
- `GET /console`
- `GET /console/chat`
- `GET /console/settings`

### 3.2 任务与作业

- `POST /api/v1/task`
  - 支持同步/异步、路由模式、agent/skill hint、webhook、显式 schedule、input_files。
- `GET /api/v1/jobs/{job_id}`

### 3.3 Chat 会话

- `POST /api/v1/chat/files`
- `GET /api/v1/chat/sessions`
- `GET /api/v1/chat/sessions/{context_id}`
- `DELETE /api/v1/chat/sessions/{context_id}`

### 3.4 调度

- `GET /api/v1/schedules`
- `DELETE /api/v1/schedules/{schedule_id}`

### 3.5 文件下载

- `GET /api/v1/files/{job_id}/{file_name}`
  - 仅允许暴露在白名单目录中的文件。

### 3.6 Feishu

- `POST /api/v1/feishu/events`
  - webhook 回调入口。

### 3.7 环境配置

- `GET /api/v1/env`
- `PUT /api/v1/env`
- `GET /api/v1/env/schema`
- `PUT /api/v1/env/schema`

### 3.8 MCP 服务管理

- `GET /api/v1/mcp/servers`
- `POST /api/v1/mcp/servers`
- `DELETE /api/v1/mcp/servers/{name}`

### 3.9 设备管理

- `POST /api/v1/devices/heartbeat`
- `GET /api/v1/devices`
- `GET /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}`

## 4. Feishu 结果渲染与媒体策略

- 结果文本由 `feishu._build_feishu_text` 组装。
- 发送阶段在 `runtime._deliver_result` 中优先发送 Markdown 卡片（interactive），失败回退 text。
- `files` 列表会根据 MIME 与后缀做策略：
  - 文本类文件不走原生媒体上传，避免噪声。
  - 图片在开启 `feishu_native_image_enabled` 时优先原生图片。
  - 其他文件在开启 `feishu_native_file_enabled` 时走原生文件上传。

## 5. 会话与目录约定

- Chat session 根目录：
  - 环境变量 `MOBICLAW_CHAT_SESSION_ROOT`，默认 `<project>/.mobiclaw/session`。
- Chat 上传目录：`<project>/.mobiclaw/uploads`。
- Feishu 下载媒体目录：
  - 环境变量 `FEISHU_MEDIA_DOWNLOAD_DIR`，默认系统临时目录下 `seneschal_feishu_media`。
- 任务输出目录（Feishu 入口）：
  - 若设置 `MOBICLAW_FILE_WRITE_ROOT`，使用该根目录。
  - 否则默认 `<project>/outputs`。

## 6. 安全与稳定性机制

- API 鉴权：`_ensure_auth`（Bearer token）。
- Feishu webhook 签名校验：`_verify_feishu_signature`。
- Feishu 群聊提及过滤：`_should_accept_feishu_message`。
- Feishu 消息去重：`events.py` 中基于 message_id + TTL 的 claim 机制。
- 下载暴露控制：`files._can_expose_file` 限制路径范围。
- 异步回调重试：`runtime._post_callback` 指数退避。

## 7. 可扩展点（重要）

本目录广泛使用 `_gateway_override(name, default)`：

- 任何关键函数和状态（例如 `_deliver_result`、`_build_feishu_text`、`_run_job`、`_read_env_content`）都可通过 `mobiclaw.gateway_server` 模块级同名符号覆盖。
- 这让你可以在不改核心函数调用点的情况下替换行为，适合灰度升级与测试注入。

## 8. 常见维护场景

### 8.1 新增一个 API

1. 在 `api.py` 的 `register_routes` 内增加路由函数。
2. 放入 `exported` 字典（便于测试和 override）。
3. 若涉及可复用逻辑，优先下沉到 `runtime.py`、`session.py`、`files.py` 等模块。

### 8.2 调整 Feishu 文本格式

1. 优先修改 `feishu._build_feishu_text`。
2. 若涉及消息类型（text vs interactive），改 `runtime._deliver_result` 的发送策略。

### 8.3 调整会话落盘行为

1. 修改 `session.py` 中目录命名与 history 读写逻辑。
2. 注意兼容 `_parse_chat_session_dir_name` 的已有命名约定，避免历史会话不可见。

## 9. 快速排障建议

- 任务提交成功但回调无结果：检查 `runtime._deliver_result` 的异常日志与 webhook 地址可达性。
- Feishu 群消息不触发：检查提及过滤配置、机器人 open_id 自动解析、signature 校验。
- 文件下载 403：检查 `MOBICLAW_GATEWAY_FILE_ROOT` 与 `files._can_expose_file` 白名单路径。
- 会话列表为空：检查 `MOBICLAW_CHAT_SESSION_ROOT` 与 session 目录命名是否符合规则。

---

如果后续你希望，我可以再补一版“调用时序图版 README”（任务路径、Feishu 路径、调度路径各一张）。
