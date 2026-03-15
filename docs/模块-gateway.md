# 模块文档：Gateway（任务接入与执行适配）

本项目包含两层 Gateway：

1. `mobiagent_server/server.py`：手机执行适配网关
2. `mobiclaw/gateway_server.py`：MobiClaw 对外任务接入网关

两者职责不同，建议独立部署与监控。

---

## 1. `mobiagent_server/server.py`

## 1.1 模块职责

将“collect/action 请求”适配到不同执行后端（mock/proxy/task_queue/cli），并输出统一回执结构。

## 1.2 API 列表

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`
- `GET /`（健康检查）

## 1.3 鉴权

- 使用 `Authorization: Bearer <MOBI_AGENT_API_KEY>`
- 若配置中 api_key 为空，可不校验

## 1.4 四种模式说明

### A) mock

- 不访问真实执行器
- 返回模拟数据
- 适合本地开发、链路联调

### B) proxy

- 将请求转发到上游 HTTP 服务
- 依赖：`MOBIAGENT_COLLECT_URL` / `MOBIAGENT_ACTION_URL`

### C) task_queue

- 把任务写入队列目录（json 文件）
- 等待结果目录出现对应结果
- 适合与异构执行器解耦

### D) cli

- 生成 task_file + data_dir
- 执行 `MOBIAGENT_CLI_CMD`
- 收集执行产物并构建标准化 result

## 1.5 CLI 模式结构化产物

聚合字段包括：

- 执行摘要：`step_count/action_count/last_action/status_hint/final_screenshot`
- 产物索引：steps/images/hierarchies/overlays
- 历史数据：actions/reacts/reasonings
- OCR 汇总：按步骤与 full_text
- 索引文件：`execution_result.json`

## 1.6 output_schema 抽取能力

在 `action` 场景，若请求携带 `params.output_schema`：

- 服务会尝试取最后截图
- 调用 VLM（OpenRouter/OpenAI 兼容接口）
- 输出 `parsed_output` JSON（或为空）

## 1.7 关键环境变量

- 模式与端口：
  - `MOBIAGENT_SERVER_MODE`
  - `MOBIAGENT_GATEWAY_PORT`
- CLI：
  - `MOBIAGENT_CLI_CMD`
  - `MOBIAGENT_CLI_WORKDIR`
  - `MOBIAGENT_TASK_DIR`
  - `MOBIAGENT_DATA_DIR`
- 队列：
  - `MOBIAGENT_QUEUE_DIR`
  - `MOBIAGENT_RESULT_DIR`
- VLM：
  - `OPENROUTER_BASE_URL`/`OPENAI_BASE_URL`
  - `OPENROUTER_API_KEY`/`OPENAI_API_KEY`
  - `OPENROUTER_MODEL`/`OPENAI_MODEL`

## 1.8 运维建议

- 对 `collect/action` 分开统计成功率
- 监控 data_dir 增长，定期归档
- 记录 CLI stderr 以便排障
- 对 queue/result 目录设置权限与清理策略

---

## 2. `mobiclaw/gateway_server.py`

## 2.1 模块职责

对外提供统一任务入口，将请求交给 workflow / orchestrator 层执行，支持同步与异步任务、文件下载、回调和飞书接入。

## 2.2 API 列表

- `POST /api/v1/task`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/files/{job_id}/{file_name}`
- `POST /api/v1/feishu/events`
- `GET /health`

## 2.3 请求模型

`TaskRequest`：

- `task: str`
- `async_mode: bool = false`
- `output_path: str | None`
- `mode: str = "router"`
- `agent_hint: str | None`
- `skill_hint: str | None`
- `routing_strategy: str | None`
- `context_id: str | None`（已透传到 workflow/orchestrator；当前主要作为后续多轮上下文能力的预留字段）
- `webhook_url: str | None`
- `webhook_token: str | None`
- `callback_headers: dict[str, str]`

返回 `TaskResult`：

- `job_id`
- `status`（queued/running/completed/failed）
- `result`（回复、routing trace、文件列表或错误信息）
- `error`（可选）

## 2.4 异步机制

- 异步任务通过 `asyncio.create_task` 后台运行
- 结果存入进程内 `_JOB_STORE`
- 进程重启后历史任务不可恢复

## 2.5 鉴权

- `MOBICLAW_GATEWAY_API_KEY` 设置后启用 Bearer 校验
- 未设置时可匿名访问（仅建议内网开发环境）

## 2.6 附加能力

- 任务结果中的 `files` 会按 `MOBICLAW_GATEWAY_FILE_ROOT` 与 `MOBICLAW_GATEWAY_PUBLIC_BASE_URL` 生成可下载链接
- 异步任务完成后支持回调 `webhook_url`
- 支持飞书 webhook 事件入口
- 支持按配置自动启动飞书长连接（`FEISHU_EVENT_TRANSPORT=long_conn/both/auto`）

## 2.7 部署建议

- 生产环境建议置于 API 网关后
- 建议加超时、限流与审计日志
- 若需要持久任务状态，建议把 `_JOB_STORE` 替换为 Redis/DB

---

## 3. 两层网关协作关系

典型链路：

1. 外部系统调用 `mobiclaw/gateway_server` 提交任务
2. gateway 调用 `run_gateway_task()`
3. orchestrator 完成 Router / Planner / Executor / Skill Selector 编排
4. 如需端侧执行，工具再调用 `mobiagent_server`
5. `mobiagent_server` 再调用真实执行器（cli/proxy/task_queue）
6. 执行结果回流到 orchestrator，最终返回给外部系统或异步回调/飞书消息

---

## 4. 常见故障与排查

- `401 Unauthorized`
  - 检查 Bearer Token 和对应 API Key
- `502`（proxy 场景）
  - 检查上游 URL 可达性与协议
- `pending` 长时间不结束（task_queue）
  - 检查执行器是否写回 result 文件
- CLI 无产物
  - 检查 `MOBIAGENT_CLI_CMD` 模板、工作目录、设备连通性
- MobiClaw 异步任务丢失
  - 说明网关进程重启，需持久化任务存储

---

## 5. 扩展路线

- `mobiagent_server`：新增 mode（如 grpc、mq）
- `mobiclaw/gateway_server`：支持批任务、回调、取消任务
- 双网关统一：标准化 trace_id，贯穿全链路观测
- 进一步增强文件暴露策略、异步任务持久化与飞书双向交互能力
