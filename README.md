# Seneschal

Seneschal 是一个“**编排层**”项目：用 Agent 协调手机端执行（MobiAgent）与知识库分析（WeKnora），形成 `Collect -> Store -> Analyze -> Execute` 的自动化闭环。

- **编排入口**：`app.py` + `seneschal/workflows.py`
- **智能体**：`seneschal/agents.py`（Steward / Worker）
- **工具层**：`seneschal/tools/`（mobi/weknora/web/shell/file/papers）
- **手机网关**：`mobiagent_server/server.py`（collect/action/jobs）
- **任务网关**：`seneschal/gateway_server.py`（统一任务入口）
- **定时任务**：`seneschal/dailytasks/runner.py`

---

## 文档导航

- 架构总览：`docs/Seneschal-简化架构图.md`
- 架构详解：`docs/Seneschal-项目架构说明.md`
- 详细分层图：`docs/Seneschal-详细架构图.md`
- 子模块文档：
  - `docs/模块-seneschal-core.md`
  - `docs/模块-tools.md`
  - `docs/模块-dailytasks.md`
  - `docs/模块-gateway.md`

---

## 从 0 到运行（最短路径）

### 1) 拉取代码与子模块

```bash
git clone <repo-url>
cd Seneschal
git submodule update --init --recursive
```

### 2) 安装 Python 依赖

```bash
uv sync
```

### 3) 配置环境变量

```bash
cp .env-example .env
```

然后在 shell 中预先导出关键密钥（不要硬编码到仓库）：

```bash
export OPENROUTER_API_KEY='...'
export WEKNORA_API_KEY='...'
# 可选：联网搜索
export BRAVE_API_KEY='...'
```

### 4) 启动依赖服务

#### 4.1 启动 WeKnora（在 `WeKnora` 子模块）

推荐开发流程（按 WeKnora README）：

```bash
cd WeKnora
make dev-start
make dev-app
make dev-frontend
```

#### 4.2 启动 Rerank（按需）

```bash
cd WeKnora
modelscope download --model BAAI/bge-reranker-v2-m3 --local_dir bge-reranker-v2-m3
python rerank_server_bge-reranker-v2-m3.py
```

#### 4.3 导入 WeKnora 配置

```bash
cd /workspace/Seneschal
ENV_FILE=./.env CONFIG_DIR=./configs bash ./scripts/weknora_import.sh
```

> 首次部署建议先确认 `configs/*.json` 中 tenant、用户、知识库与模型配置是否与你的 WeKnora 环境一致。

### 5) 启动 MobiAgent 网关

```bash
python -m mobiagent_server.server
```

默认端口：`8081`（可通过 `MOBIAGENT_GATEWAY_PORT` 修改）。


### 一键脚本（可选）

#### 一键启动

```bash
bash ./scripts/bootstrap_one_click.sh
```

#### 一键停止

```bash
bash ./scripts/stop_all.sh
```


### 6) 运行 Seneschal

#### Demo 模式

```bash
python app.py
```

#### 交互模式

```bash
python app.py --interactive
```

#### Daily 任务模式

```bash
python app.py --daily --daily-trigger daily
```

#### Worker 单任务模式

```bash
python app.py --agent-task "从 arXiv 搜索今天的 Agent 论文并总结" --output "outputs/papers.md"
```

### 7) 智能路由多智能体模式（Router + Steward + Worker）

当前 `app.py --agent-task` 与 `gateway /api/v1/task` 默认都走统一编排：
- Router Agent：根据任务语义选择目标 Agent（LLM 语义路由 + 规则兜底）
- Planner Agent：复合任务自动拆分为阶段子任务（串并行混合）
- Executor：将子任务分发给 `Steward` / `Worker` 执行并聚合结果

联网搜索默认采用 Brave Search：先检索候选来源链接与摘要，再按需抓取网页正文。
实现见 [seneschal/workflows.py](seneschal/workflows.py) 与 `seneschal/orchestrator.py`。

```bash
python app.py --agent-task "帮我查看今天美伊战争的情况总结，并且生成对应的md总结"
```

如果需要指定输出路径，可提供 `--output`（Agent 会优先遵循）：

```bash
python app.py --agent-task "帮我查看今天美伊战争的情况总结，并且生成对应的md总结" --output "outputs/summart.md"
```

示例：每天从 arXiv 搜索最新的 Agent 相关论文并生成 Markdown 总结（可由 cron 定时调用）：

```bash
python app.py --agent-task "从 arXiv 搜索最新的 Agent 相关论文，下载并阅读 PDF，生成并保存论文摘要与要点，以markdown的格式，" --output "outputs/papers/agent_arxiv_daily.md"
```

示例：查看近三年 OSDI 会议上关于 Agent 的论文并作总结：

```bash
python app.py --agent-task "帮我查看近三年 OSDI 会议上关于 端侧大模型推理 的相关论文，总结论文的设计与实现，并以markdown格式保存" --output "outputs/papers/osdi_agent_last3years.md"

# 强制走 legacy 单 Agent 模式（兼容）
python app.py --agent-task "帮我总结今天待办" --mode steward
python app.py --agent-task "帮我检索最新 Agent 论文" --mode worker

# 可选：给路由器提示偏好 Agent
python app.py --agent-task "整理并补充今日行动建议" --agent-hint steward
```

Shell 工具默认受白名单限制，若你设置了 `SENESCHAL_SHELL_ALLOWLIST`，请按需加入允许的命令。
如需限制写文件路径，可设置 `SENESCHAL_FILE_WRITE_ROOT`。

### 8) Gateway模式（类似OpenClaw Core 入口）

网关用于接收任务并交给 workflow 层决策执行，支持同步和异步任务查询。

当前实现已改为将控制流交给 `seneschal/workflows.py`，不在网关层写死具体 Agent 调用逻辑。
接口语义参考 OpenClaw 的异步任务风格：`submit -> accepted/running -> query result`。

```bash
python -m seneschal.gateway_server
```

默认监听：`http://0.0.0.0:8090`

可选环境变量：
- `SENESCHAL_GATEWAY_PORT`：自定义端口（默认 `8090`）
- `SENESCHAL_GATEWAY_API_KEY`：网关鉴权（Bearer token）
- `SENESCHAL_ROUTING_DEFAULT_MODE`：默认路由模式（默认 `router`）
- `SENESCHAL_ROUTING_STRATEGY`：路由策略（默认 `llm_rule_hybrid`）
- `SENESCHAL_ALLOW_LEGACY_MODE`：是否允许 legacy `worker/steward/auto`（默认 `1`）
- `SENESCHAL_ROUTING_MAX_SUBTASKS`：Planner 最大子任务数（默认 `4`）
- `SENESCHAL_ROUTING_MAX_DEPTH`：委派/路由最大深度（默认 `2`）
- `SENESCHAL_ROUTER_TIMEOUT_S`：Router 决策超时秒数（默认 `60`，超时默认回退到 `worker`）
- `SENESCHAL_PLANNER_TIMEOUT_S`：Planner 拆分超时秒数（默认 `60`，超时默认回退到 `worker`）
- `SENESCHAL_SUBTASK_TIMEOUT_S`：单子任务执行超时秒数（默认 `300`）
- `SENESCHAL_GATEWAY_PUBLIC_BASE_URL`：生成文件下载链接时使用的公网前缀
- `SENESCHAL_GATEWAY_FILE_ROOT`：允许下载文件的根目录（建议设置）
- `SENESCHAL_GATEWAY_CALLBACK_TIMEOUT`：异步回调超时秒数
- `SENESCHAL_GATEWAY_CALLBACK_RETRY`：异步回调重试次数
- `SENESCHAL_GATEWAY_CALLBACK_BACKOFF`：回调重试退避基数秒数
- `FEISHU_EVENT_TRANSPORT`：飞书事件接入模式，支持 `webhook` / `long_conn` / `both` / `auto`（默认 `both`）
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：飞书应用机器人凭据（长连接与主动回发结果都会使用）
- `FEISHU_VERIFICATION_TOKEN`：飞书事件订阅 token（可选）
- `FEISHU_ENCRYPT_KEY`：飞书签名校验 key（可选）

飞书长连接模式（推荐本地开发，无需公网 IP）：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_EVENT_TRANSPORT="long_conn"
python -m seneschal.gateway_server
```

说明：
- 若 `FEISHU_APP_ID` 或 `FEISHU_APP_SECRET` 未定义，网关会输出 warning 并跳过长连接启动（不会导致服务退出）。
- `both` 模式下，`/api/v1/feishu/events` webhook 和长连接可同时使用。

也可以直接运行示例脚本（无需手动启动gateway_server服务）：

```bash
bash ./scripts/run_gateway_demo.sh

飞书事件订阅本地模拟验证脚本：

```bash
bash ./scripts/run_gateway_feishu_demo.sh
```

该脚本仅用于验证 webhook 入口，不会验证飞书长连接链路。

---


### 9) 其他请求示例（网关）

#### 9.1 MobiAgent Server：Collect

为支持完整任务执行的场景。

```bash
curl -X POST http://localhost:8081/api/v1/collect \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"task":"获取微信聊天列表前5条摘要"}'
```

#### 9.2 MobiAgent Server：Action + output_schema

为支持特定操作、单步操作场景预留接口和请求格式。
```bash
curl -X POST http://localhost:8081/api/v1/action \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "add_calendar_event",
    "params": {
      "title": "产品评审",
      "date": "2025-02-01",
      "time": "15:00",
      "output_schema": {
        "title": "string",
        "date": "string",
        "time": "string",
        "success": "boolean"
      }
    },
    "options": {"wait_for_completion": true, "timeout": 60}
  }'
```

#### 9.3 Seneschal Gateway Task

用于触发 OpenClaw Core（Steward Agent）任务。

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task":"整理今日待办并给出简要总结","async_mode":false}'
```

支持可选参数：
- `output_path`：输出文件提示路径（由 workflow 决策是否落盘）
- `mode`：`router`（默认）/ `intelligent` / `worker` / `steward` / `auto`
- `agent_hint`：可选路由提示（`worker` / `steward`）
- `routing_strategy`：可选路由策略覆盖
- `context_id`：可选上下文标识（便于多轮编排追踪）
- `webhook_url`：异步完成后回调地址
- `webhook_token`：回调 Bearer token
- `callback_headers`：回调附加 headers

异步 + 回调示例：

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{
    "task":"先整理今天的待办，再补充联网信息并输出 markdown",
    "async_mode":true,
    "mode":"router",
    "routing_strategy":"llm_rule_hybrid",
    "output_path":"outputs/tasks/today.md",
    "webhook_url":"http://127.0.0.1:9000/callback",
    "webhook_token":"demo-token"
  }'
```

异步任务：

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task":"检索近期会议安排并总结","async_mode":true}'

curl http://localhost:8090/api/v1/jobs/<job_id>
```

如果任务产出了文件，结果中会包含 `result.files`，每个文件包含：
- `path`：本地绝对路径
- `name`：文件名
- `size`：文件大小
- `download_url`：可下载地址

智能路由执行结果还会包含 `result.routing_trace`，用于查看：
- 路由决策（目标 Agent、置信度、理由）
- 规划来源（`direct` / `llm` / `fallback`）
- 分阶段子任务执行明细（含并行阶段）

下载文件示例：

```bash
curl -L "http://localhost:8090/api/v1/files/<job_id>/<file_name>" -o ./downloaded_file
```

如果启用鉴权：

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Authorization: Bearer <SENESCHAL_GATEWAY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"task":"给出今天的提醒事项","async_mode":false}'
```

#### 9.4 飞书机器人事件订阅接入

网关提供飞书事件入口：`POST /api/v1/feishu/events`。

同时支持飞书长连接模式（SDK websocket/event stream），可用于本地开发和内网部署（无需公网 IP）。

配置步骤：
- 在飞书开放平台创建应用并开启机器人能力。
- 二选一配置事件接入：
- webhook 模式：在事件订阅中填入网关地址 `https://<your-domain>/api/v1/feishu/events`。
- 长连接模式：无需配置公网回调地址，网关会主动连接飞书并接收事件。
- 配置环境变量：
- `FEISHU_EVENT_TRANSPORT`（推荐本地开发设为 `long_conn`，混合模式用 `both`）
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`（若在飞书侧配置了 token）
- `FEISHU_ENCRYPT_KEY`（若启用了签名校验）
- 建议配置 `SENESCHAL_GATEWAY_PUBLIC_BASE_URL`，让回传的文件链接可被飞书用户访问。

启动示例（长连接）：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_EVENT_TRANSPORT="long_conn"
python -m seneschal.gateway_server
```

事件处理说明：
- 网关收到飞书消息后会创建异步任务（返回 accepted 或写入日志）。
- 任务完成后，网关会主动调用飞书消息接口回发结果文本。
- 若包含文件，默认以下载链接形式附在结果文本中。

---

## 服务接口速查

### MobiAgent Gateway

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`

### Seneschal Gateway

- `POST /api/v1/task`（同步或异步）
- `GET /api/v1/jobs/{job_id}`
- `GET /health`

---

## 常见问题

### Q1：为什么调用手机工具时返回 mock？
通常是 `MOBIAGENT_SERVER_MODE` 不是 `cli/proxy/task_queue`，或网关不可达。先检查 `python -m mobiagent_server.server` 是否启动、`MOBI_AGENT_BASE_URL` 是否正确。

### Q2：为什么 WeKnora 写入/查询失败？
请检查 `WEKNORA_BASE_URL`、`WEKNORA_API_KEY`、`WEKNORA_KB_NAME`、`WEKNORA_AGENT_NAME` 是否与 WeKnora 环境一致，并确认目标知识库已存在。

### Q3：Daily 模式没有执行任务？
`tasks.json` 中任务按 `triggers` 过滤，确保 `--daily-trigger` 值在任务 `triggers` 中。
