# Seneschal

Seneschal 是一个“**编排层**”项目：用 Agent 协调手机端执行（MobiAgent）与本地工具，形成 `Route -> Plan -> Execute -> Persist` 的自动化闭环。

当前主链路已经扩展为“**Router + Planner + Executor + Skill Selector**”的多智能体编排模型：

- **编排入口**：`app.py` + `seneschal/workflows.py`
- **编排核心**：`seneschal/orchestrator.py`
- **智能体**：`seneschal/agents.py`（Steward / Worker / Router / Planner / Skill Selector / User）
- **工具层**：`seneschal/tools/`（mobi / web / shell / file / papers / office / memory）
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

# 安装 tesseract 中文包，用于后续可能的 OCR 需求（可选）
sudo apt-get update
sudo apt-get install -y tesseract-ocr-chi-sim
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

# Brave Search（可选，联网搜索时建议）
export BRAVE_API_KEY='...'
```

常用补充项：

```bash
export OPENROUTER_MODEL='google/gemini-3-flash-preview'
export OPENROUTER_BASE_URL='https://openrouter.ai/api/v1'
export MOBI_AGENT_BASE_URL='http://localhost:8081'
```

`app.py` 与 `seneschal/gateway_server.py` 启动时都会自动读取根目录 `.env`，仅在环境变量尚未存在时补齐。

### 4) 启动依赖服务


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

当前 Demo 会直接调用 `run_demo_conversation()`，并使用这条预置任务：

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

通过`--mode`指定具体的Agen：
```bash
python app.py --agent-task "从 arXiv 搜索今天的 Agent 论文并总结" --mode worker
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

### 配置驱动自定义 Agent

当前支持通过配置文件自动注册自定义 Agent，并默认参与 Router/Planner 的候选集合。

- 配置文件路径：`seneschal/configs/custom_agent.json`
- 可选覆盖：环境变量 `SENESCHAL_CUSTOM_AGENT_CONFIG_PATH`
- 加载时机：进程启动加载一次
- 校验策略：`tools` 严格校验；若包含未知工具名，该 Agent 会被跳过并记录 warning

配置项说明：

- 必填字段：
- `agent_name`：Agent 名称（路由标识，内部会标准化为小写）
- `role`：该 Agent 的职责描述（供 Router 能力画像使用）
- `system_prompt`：该 Agent 的系统提示词
- 可选字段：
- `tools`：工具名列表（必须来自系统已注册工具名）
- `strengths`：能力优势列表
- `typical_tasks`：典型任务列表
- `boundaries`：能力边界列表
- `model_name`：该 Agent 专用模型（不填则沿用默认模型）
- `temperature`：该 Agent 温度参数
- `max_iters`：该 Agent 最大迭代轮数（1-50）

示例：

```json
{
  "agents": [
    {
      "agent_name": "research_assistant",
      "role": "负责论文与网页信息的深度检索和结构化总结",
      "system_prompt": "你是 Seneschal 的 Research Assistant。你只处理检索、阅读、对比与总结类任务。",
      "tools": [
        "brave_search",
        "fetch_url_readable_text",
        "arxiv_search",
        "download_file",
        "extract_pdf_text"
      ],
      "strengths": ["跨来源信息检索与交叉验证"],
      "typical_tasks": ["检索并总结某个主题的最新论文"],
      "boundaries": ["不执行手机 GUI 操作"],
      "temperature": 0.2,
      "max_iters": 12
    }
  ]
}
```

使用方式：

- 自动路由：提交普通任务即可，Router 会把它作为候选 Agent
- 显式指定：可通过 `agent_hint` / `--agent-hint` 直接指定自定义 Agent

### Skill 自动选择

当前版本新增 Skill 选择机制：

- 发现方式：扫描 `seneschal/skills/*/SKILL.md`
- 召回方式：规则召回 + 可选 LLM 重排
- 注入方式：把 skill 摘要注入目标 Agent 的 prompt 上下文
- 手动覆盖：支持 `--skill-hint`
- 可观测性：结果中的 `routing_trace.skills.records` 会记录候选、来源、原因和最终选择
- 当前 `routing_trace` 还会记录 `planner_allowed_agents` 等规划约束信息，方便定位路由与规划偏差

#### Skill 脚本执行（运行时）

除了固定工具外，Worker 还支持通过 `run_skill_script` 在运行时调用 skill 目录中的脚本。

脚本发现规则：
- `run_skill_script` 接口参数：
  - `command`：完整可执行命令字符串
  - `execution_dir`：命令执行目录
  - `timeout_s`：超时时间（可选）
- 执行逻辑：
  - 先进入 `execution_dir`
  - 执行 `command`
  - 执行完成后返回之前目录

运行时示例（由 Agent 内部调用）：
- `run_skill_script(command="python scripts/thumbnail.py input.pptx outputs/thumbs", execution_dir="/workspace/Seneschal/seneschal/skills/pptx")`
- `run_skill_script(command="python scripts/office/unpack.py input.docx tmp/unpacked", execution_dir="/workspace/Seneschal/seneschal/skills/docx")`

相关环境变量：
- `SENESCHAL_SKILL_SCRIPT_TIMEOUT_S`：脚本超时秒数（默认 `120`）
- `SENESCHAL_SKILL_SCRIPT_PYTHON`：Python 脚本运行时（默认当前解释器）
- `SENESCHAL_SKILL_SCRIPT_NODE`：Node 脚本运行时（默认 `node`）
- `SENESCHAL_SKILL_SCRIPT_BASH`：Shell 脚本运行时（默认 `bash`）

说明：
- `run_skill_script` 与 `run_shell_command` 的白名单机制独立。
- `run_skill_script` 在运行时会读取 `execution_dir` 下的 `SKILL.md`，仅允许执行其中提到的命令。
- `execution_dir` 必须位于 `seneschal/skills` 目录下。
- 若 `SKILL.md` 缺失、无法提取命令，或命令不在白名单中，调用会被拒绝。

### `--agent-task` 常见示例

`--context-id` 参数已经接入 CLI 与 Gateway 请求模型，当前主要作为后续多轮上下文能力的预留字段，现阶段不会改变单次执行结果。

```bash
# 默认多智能体路由
python app.py --agent-task "帮我查看今天美伊战争的情况总结，并且生成对应的 md 总结"

#### 智能路由多智能体模式（Router + Steward + Worker）

当前 `app.py --agent-task` 与 `gateway /api/v1/task` 默认都走统一编排：
- Router Agent：根据任务语义选择目标 Agent（LLM 语义路由 + 规则兜底）
- Planner Agent：复合任务自动拆分为阶段子任务（串并行混合）
- Executor：将子任务分发给 `Steward` / `Worker` / 其他Agent 执行并聚合结果
- Skill Selector：为每个子任务自动选择最合适的 Skill（规则召回 + LLM 重排，可为空）

联网搜索默认采用 Brave Search：先检索候选来源链接与摘要，再按需抓取网页正文。
实现见 [seneschal/workflows.py](seneschal/workflows.py) 与 [seneschal/orchestrator.py](seneschal/orchestrator.py)。

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

当前实现会为每次任务创建独立目录：`<项目根>/outputs/job_<时间戳>/`，并在其下创建临时目录：`tmp/`。

`--output` 的实际落盘规则如下：

- 始终只把“最终输出文件路径”提示给**最后一个子任务**（前面的子任务不会拿到该提示）。
- 若未提供 `--output`：默认最终输出路径为
  ` <项目根>/outputs/job_<时间戳>/final_output.md `。
- 若提供了相对路径（例如 `--output outputs/paper.md`）：会被拼到该 job 目录下，最终路径为
  ` <项目根>/outputs/job_<时间戳>/outputs/paper.md `。
- 若提供了绝对路径：出于目录隔离，当前实现会仅保留文件名并落到 job 目录下（不会写到外部绝对路径）。


## Gateway模式（类似OpenClaw Core 入口）



启动命令：

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
- `SENESCHAL_SKILL_ENABLED`：是否启用 skill 自动选择（默认 `1`）
- `SENESCHAL_SKILL_ROOT_DIR`：skill 根目录（默认 `seneschal/skills`）
- `SENESCHAL_SKILL_MAX_PER_SUBTASK`：每个子任务最多挂载的 skill 数（默认 `2`）
- `SENESCHAL_SKILL_SELECTOR_TIMEOUT_S`：skill LLM 重排超时秒数（默认 `20`）
- `SENESCHAL_SKILL_LLM_RERANK`：是否启用 LLM 重排（默认 `1`）
- `SENESCHAL_SKILL_RULE_MAX_CANDIDATES`：规则召回候选上限（默认 `8`）
- `SENESCHAL_SKILL_HINT_OVERRIDE`：是否允许 `skill_hint` 覆盖自动选择（默认 `1`）
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

#### 8.4 飞书机器人事件订阅接入

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

## 项目结构提示

这是一个包含多个子模块的大仓库，修改时请先确认你操作的是：

- 根项目 `Seneschal`
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
