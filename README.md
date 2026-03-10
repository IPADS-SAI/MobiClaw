# Seneschal

Seneschal 是一个“**编排层**”项目：用 Agent 协调手机端执行（MobiAgent）与知识库分析（WeKnora），形成 `Collect -> Store -> Analyze -> Execute` 的自动化闭环。

当前主链路已经扩展为“**Router + Planner + Executor + Skill Selector**”的多智能体编排模型：

- **编排入口**：`app.py` + `seneschal/workflows.py`
- **编排核心**：`seneschal/orchestrator.py`
- **智能体**：`seneschal/agents.py`（Steward / Worker / Router / Planner / Skill Selector / User）
- **工具层**：`seneschal/tools/`（mobi / weknora / web / shell / file / papers / office）
- **手机网关**：`mobiagent_server/server.py`（collect / action / jobs）
- **任务网关**：`seneschal/gateway_server.py`（统一任务入口、异步任务、文件下载、飞书接入）
- **定时任务**：`seneschal/dailytasks/runner.py`
- **Skill 库**：`seneschal/skills/*/SKILL.md`

---

## 文档导航

- 架构总览：`docs/Seneschal-简化架构图.md`
- 架构详解：`docs/Seneschal-项目架构说明.md`
- 详细分层图：`docs/Seneschal-详细架构图.md`
- 详细项目拆解：`docs/Seneschal-详细项目分析与拆解.md`
- 改进路线图：`docs/Seneschal-改进路线图.md`
- 子模块文档：
  - `docs/模块-seneschal-core.md`
  - `docs/模块-tools.md`
  - `docs/模块-dailytasks.md`
  - `docs/模块-gateway.md`

---

## 当前运行模式概览

`python app.py` 统一从 `seneschal/workflows.py:231` 进入，按参数分为 4 种模式：

- **无参数**：Demo 模式，执行预置对话 `run_demo_conversation()`
- **`--interactive`**：交互模式，终端多轮对话
- **`--daily --daily-trigger <trigger>`**：Daily 任务模式
- **`--agent-task "..."`**：多智能体任务模式，默认走 orchestrator

> 注意：当前 `--agent-task` 默认不是 legacy 单 Agent，而是 `router` 模式。

---

## 从 0 到运行（推荐路径）

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

项目要求 Python 3.12+。

### 3) 配置环境变量

```bash
cp .env-example .env
```

然后至少补齐以下变量：

```bash
# LLM（必需）
export OPENROUTER_API_KEY='...'
# 或者：export OPENAI_API_KEY='...'

# WeKnora（推荐，涉及知识库存储 / RAG 时需要）
export WEKNORA_API_KEY='...'

# Brave Search（可选，联网搜索时建议）
export BRAVE_API_KEY='...'
```

常用补充项：

```bash
export OPENROUTER_MODEL='google/gemini-3-flash-preview'
export OPENROUTER_BASE_URL='https://openrouter.ai/api/v1'
export WEKNORA_BASE_URL='http://localhost:8080'
export MOBI_AGENT_BASE_URL='http://localhost:8081'
```

`app.py` 与 `seneschal/gateway_server.py` 启动时都会自动读取根目录 `.env`，仅在环境变量尚未存在时补齐。

### 4) 启动依赖服务

#### 4.1 启动 WeKnora（在 `WeKnora/` 子模块）

推荐开发流程（按 WeKnora README）：
按需配置`文件存储类型`、
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

#### 4.3 导入其余 WeKnora 配置
导入WeKnora的其余配置，包括知识库、模型、Agent等配置
```bash
cd /workspace/Seneschal
ENV_FILE=./.env CONFIG_DIR=./configs bash ./scripts/weknora_import.sh
```

> 若非首次部署/本地已有WeKnora服务，建议先检查 `configs/*.json` 中 tenant、用户、知识库、模型等配置是否与已有的 WeKnora 环境一致，避免覆盖导致冲突。

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

当前 Demo 会直接调用 `seneschal/workflows.py:92` 的 `run_demo_conversation()`，并使用这条预置任务：

```text
开始今日的数据整理和分析，给出最近活动的总结和待办事项。
```

它会创建 `Steward` 和 `User`，实际由 `Steward` 对这条预置消息执行一次完整回复，适合快速验证主链路是否跑通。

#### 交互模式

```bash
python app.py --interactive
```

输入 `exit` / `quit` / `退出` 可结束。

#### Daily 任务模式

```bash
python app.py --daily --daily-trigger daily
```

> 注意：Daily 模式中的 `agent_task` 任务当前仍是直接创建 Worker 执行，不经过 orchestrator 的 Router / Planner / Skill Selector 主链路。

#### Agent Task 模式

```bash
python app.py --agent-task "从 arXiv 搜索今天的 Agent 论文并总结" --output "outputs/papers.md"
```

---

## Orchestrator：默认任务执行链路

最近的更新已经把 `--agent-task` 与 Gateway `POST /api/v1/task` 统一切到 `seneschal/orchestrator.py:816` 的编排入口。

### 执行阶段

默认控制流如下：

1. **Router**：选择目标 Agent（优先 LLM 路由，失败时规则回退）
2. **Planner**：对复合任务进行阶段拆分（串行 / 并行子任务）
3. **Skill Selector**：为每个子任务自动选择最合适的 skill
4. **Executor**：把子任务交给 `Steward` / `Worker` 执行
5. **Aggregator**：聚合文本回复、输出文件与 routing trace

### 路由与回退机制

当前实现支持：

- `mode=router` / `mode=intelligent`：默认多智能体编排
- `mode=worker|steward|auto`：legacy 兼容模式
- `--agent-hint steward|worker`：强制指定目标 agent
- Planner 只会在 Router 选出的允许 Agent 集合内拆分子任务，避免规划结果偏离目标执行者
- Router 超时：默认回退到 `worker`
- Planner 超时：默认退化为单子任务 `worker`

### Skill 自动选择

当前版本新增 Skill 选择机制：

- 发现方式：扫描 `seneschal/skills/*/SKILL.md`
- 召回方式：规则召回 + 可选 LLM 重排
- 注入方式：把 skill 摘要注入目标 Agent 的 prompt 上下文
- 手动覆盖：支持 `--skill-hint`
- 可观测性：结果中的 `routing_trace.skills.records` 会记录候选、来源、原因和最终选择
- 当前 `routing_trace` 还会记录 `planner_allowed_agents` 等规划约束信息，方便定位路由与规划偏差

### `--agent-task` 常见示例

`--context-id` 参数已经接入 CLI 与 Gateway 请求模型，当前主要作为后续多轮上下文能力的预留字段，现阶段不会改变单次执行结果。

```bash
# 默认多智能体路由
python app.py --agent-task "帮我查看今天美伊战争的情况总结，并且生成对应的 md 总结"

# 指定输出路径
python app.py --agent-task "帮我查看今天美伊战争的情况总结，并且生成对应的 md 总结" --output "outputs/summary.md"

# 强制走 legacy 单 Agent 模式
python app.py --agent-task "帮我总结今天待办" --mode steward
python app.py --agent-task "帮我检索最新 Agent 论文" --mode worker

# 给 Router 提示偏好 Agent
python app.py --agent-task "整理并补充今日行动建议" --agent-hint steward

# 手动指定 skill（优先级高于自动选择，支持逗号分隔）
python app.py --agent-task "做一个内部周报草稿" --skill-hint internal-comms
python app.py --agent-task "生成一个前端页面原型" --skill-hint web-artifacts-builder,frontend-design
```

### 输出文件提示机制

如果提供 `--output`，当前实现只会把该路径提示追加到**最后一个子任务**，由实际执行 Agent 自行决定是否落盘。若 Agent 在回复中输出 `[File] Wrote: ...`，orchestrator 会自动收集并返回文件列表。

---

## Gateway 模式

启动命令：

```bash
python -m seneschal.gateway_server
```

默认监听：`http://0.0.0.0:8090`

### 当前 Gateway 能力

相比旧版本只做“提交任务 -> 返回文本”，当前 Gateway 已支持：

- `POST /api/v1/task`：同步 / 异步提交任务
- `GET /api/v1/jobs/{job_id}`：查询异步任务状态
- `GET /api/v1/files/{job_id}/{file_name}`：下载任务产出文件
- `POST /api/v1/feishu/events`：飞书 webhook 事件入口
- 启动时可按配置自动建立飞书长连接
- 异步完成后可回调 `webhook_url`

### 同步调用示例

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task":"整理今日待办并给出简要总结","async_mode":false}'
```

### 异步调用示例

```bash
curl -X POST http://localhost:8090/api/v1/task \
  -H "Content-Type: application/json" \
  -d '{"task":"检索近期会议安排并总结","async_mode":true}'

curl http://localhost:8090/api/v1/jobs/<job_id>
```

### 异步 + 回调示例

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

### 文件下载

当任务结果中包含 `result.files` 时，且文件位于允许暴露的目录内，可通过：

```bash
curl -L "http://localhost:8090/api/v1/files/<job_id>/<file_name>" -o ./downloaded_file
```

### 鉴权

若设置了 `SENESCHAL_GATEWAY_API_KEY`，则：

- `POST /api/v1/task`
- `GET /api/v1/files/{job_id}/{file_name}`

都需要 `Authorization: Bearer <SENESCHAL_GATEWAY_API_KEY>`。

### 飞书接入

支持两种方式：

- webhook：`/api/v1/feishu/events`
- long connection：本地开发推荐，无需公网 IP

长连接启动示例：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_EVENT_TRANSPORT="long_conn"
python -m seneschal.gateway_server
```

如果未设置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，网关会记录 warning 并跳过长连接启动，不会直接退出。

---

## 关键环境变量

### 编排与路由

- `SENESCHAL_ROUTING_DEFAULT_MODE`：默认 `router`
- `SENESCHAL_ROUTING_STRATEGY`：默认 `llm_rule_hybrid`
- `SENESCHAL_ALLOW_LEGACY_MODE`
- `SENESCHAL_ROUTING_MAX_SUBTASKS`
- `SENESCHAL_ROUTING_MAX_DEPTH`
- `SENESCHAL_ROUTER_TIMEOUT_S`
- `SENESCHAL_PLANNER_TIMEOUT_S`
- `SENESCHAL_SUBTASK_TIMEOUT_S`

### Skill Selector

- `SENESCHAL_SKILL_ENABLED`
- `SENESCHAL_SKILL_ROOT_DIR`
- `SENESCHAL_SKILL_MAX_PER_SUBTASK`
- `SENESCHAL_SKILL_SELECTOR_TIMEOUT_S`
- `SENESCHAL_SKILL_LLM_RERANK`
- `SENESCHAL_SKILL_RULE_MAX_CANDIDATES`
- `SENESCHAL_SKILL_HINT_OVERRIDE`

### Gateway

- `SENESCHAL_GATEWAY_PORT`
- `SENESCHAL_GATEWAY_API_KEY`
- `SENESCHAL_GATEWAY_PUBLIC_BASE_URL`
- `SENESCHAL_GATEWAY_FILE_ROOT`
- `SENESCHAL_GATEWAY_CALLBACK_TIMEOUT`
- `SENESCHAL_GATEWAY_CALLBACK_RETRY`
- `SENESCHAL_GATEWAY_CALLBACK_BACKOFF`

### 飞书

- `FEISHU_EVENT_TRANSPORT`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_VERIFICATION_TOKEN`
- `FEISHU_ENCRYPT_KEY`

### 其他执行限制

- `SENESCHAL_SHELL_ALLOWLIST`：Shell 工具白名单
- `SENESCHAL_FILE_WRITE_ROOT`：文件写入根目录限制
- `SENESCHAL_DOCX_MAX_CHARS`：DOCX 文本读取最大字符数上限

### 文档与 Office 处理能力

代码库中已增加 `seneschal/tools/office.py`，提供以下本地文档处理能力：

- DOCX：读取文本、根据纯文本创建文档、基于替换/追加/表格写入编辑文档
- PDF：基于纯文本生成 PDF
- XLSX：读取工作表摘要与预览、按 records/rows 写入 Excel

当前这些能力已经进入工具层代码，但尚未默认注册到 Worker / Steward 的工具清单；如果需要在 Agent 主链路中启用，还需要在 `seneschal/tools.py` 与 `seneschal/agents.py` 中继续接入。

---

## 一键脚本（可选）

### 一键启动

```bash
bash ./scripts/bootstrap_one_click.sh
```

### 一键停止

```bash
bash ./scripts/stop_all.sh
```

### Gateway 示例脚本

```bash
bash ./scripts/run_gateway_demo.sh
bash ./scripts/run_gateway_feishu_demo.sh
```

其中：

- `run_gateway_demo.sh` 用于本地快速验证 Gateway 提交链路
- `run_gateway_feishu_demo.sh` 用于模拟验证飞书 webhook 入口

---

## MobiAgent / Gateway 请求示例

### MobiAgent Server：Collect

```bash
curl -X POST http://localhost:8081/api/v1/collect \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"task":"获取微信聊天列表前5条摘要"}'
```

### MobiAgent Server：Action + output_schema

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

---

## 测试

当前仓库没有在根 `pyproject.toml` 中定义统一的 pytest 配置。

推荐优先运行根项目自身测试：

```bash
python -m pytest tests
```

补充说明：

- 根仓库直接执行 `python -m pytest --collect-only` 目前会被 `MobiAgent/MobiFlow/test_model_connectivity.py` 阻塞
- 跑单文件：`python -m pytest tests/<file>.py`
- 跑单用例：`python -m pytest tests/<file>.py -k <pattern>`

---

## 项目结构提示

这是一个包含多个子模块的大仓库，修改时请先确认你操作的是：

- 根项目 `Seneschal`
- 子模块 `WeKnora/`
- 子模块 `MobiAgent/`
- 其他子模块或辅助目录

根项目的核心实现主要集中在：

- `app.py`
- `seneschal/`
- `mobiagent_server/`
- `configs/`
- `scripts/`
- `docs/`

如果要进一步理解当前架构，建议先读：

- `docs/Seneschal-详细项目分析与拆解.md`
- `docs/模块-seneschal-core.md`
- `docs/模块-gateway.md`
