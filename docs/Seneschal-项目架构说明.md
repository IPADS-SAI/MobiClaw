# Seneschal 项目架构说明（按当前实际代码口径）

## 1. 当前定位

Seneschal 当前实际是一个以 **多 Agent 编排** 为核心的执行层，负责把：

- Chat / Gateway / CLI / Daily 等入口
- Orchestrator 的路由、规划、执行
- Worker / Steward 的能力分工
- MobiAgent 手机执行能力
- 本地工具、本地状态、本地输出

串成一条可运行的任务闭环。

当前真实主链路的准确描述是：

1. 输入从 `CLI / Gateway / Chat / Daily` 进入
2. `workflows.py` 进行模式分发
3. `orchestrator.py` 执行 `route -> plan -> execute`
4. Worker / Steward 分别调用本地工具或 MobiAgent
5. 结果写入本地 session / outputs / run logs / local memory

一句话总结：

> Seneschal 当前是一套以 `Gateway / Chat + Orchestrator + Agents + MobiAgent + 本地状态` 为核心的多 Agent 编排系统。


```text
Seneschal/
├── app.py                          # 程序入口，加载 .env 并进入 workflows.main
├── seneschal/
│   ├── workflows.py                # 统一模式分发与 chat 会话流程
│   ├── orchestrator.py             # route -> plan -> execute 主编排
│   ├── agents.py                   # Chat / Worker / Steward / Router / Planner / Skill Selector
│   ├── gateway_server.py           # Seneschal 对外 HTTP Gateway
│   ├── run_context.py              # run_id 与 JSONL 事件日志
│   ├── config.py                   # LLM / Mobi / Routing / legacy 配置
│   ├── dailytasks/
│   │   ├── runner.py               # Daily 任务执行器（仍含 legacy 路径）
│   │   └── tasks/tasks.json        # 日常任务定义
│   ├── skills/                     # skill 目录
│   └── tools/
│       ├── mobi.py                 # MobiAgent collect/action 调用
│       ├── web.py                  # Brave 与网页抓取
│       ├── papers.py               # arXiv / DBLP / PDF
│       ├── office.py               # DOCX / XLSX / PDF 工具
│       ├── ppt.py                  # PPTX 读写与样式处理
│       ├── ocr.py                  # OCR
│       ├── shell.py                # 命令执行
│       ├── file.py                 # 文本写入
│       ├── memory.py               # 本地 memory / task history / steward knowledge
│       ├── skill_runner.py         # 执行 skill 脚本
├── mobiagent_server/server.py      # 手机执行网关
├── docs/                           # 项目文档
└── outputs/                        # 任务输出目录
```

---

## 2. 顶层分层

### 2.1 入口层

- `app.py`
- `seneschal.gateway_server`
- Gateway Web UI

职责：

- 接用户输入
- 加载环境变量
- 决定进入哪种运行模式

### 2.2 工作流层

核心文件：`seneschal/workflows.py`

职责：

- 分发 demo / interactive / agent-task / daily / gateway chat
- 管理 chat 指令：`/new`、`/interrupt`、`/exit`
- 恢复/保存 chat session
- 对外输出 planner monitor 事件

### 2.3 编排层

核心文件：`seneschal/orchestrator.py` + `seneschal/agents.py`

职责：

- 路由：选哪个 Agent
- 规划：拆多少阶段、多少子任务
- 选技：每个子任务是否带 skill
- 执行：顺序执行子任务并保留上下文
- 聚合：回收 reply / files / trace

### 2.4 工具与集成层

主要模块：

- `mobi.py`
- `web.py`
- `papers.py`
- `shell.py`
- `ocr.py`
- `office.py`
- `ppt.py`
- `skill_runner.py`
- `memory.py`

职责：

- 调用外部能力
- 执行本地命令与文件处理
- 保存和检索本地知识/记忆

### 2.5 状态与持久化层

主要内容：

- Chat sessions
- `RunContext` JSONL
- `outputs/`
- 本地长期记忆
- steward knowledge / task history

---

## 2. 运行模式

### 2.1 Demo 模式

`python app.py`

- 加载 `.env`
- 进入 `workflows.main()`
- 默认执行一条预设演示消息

### 2.2 Interactive 模式

`python app.py --interactive`

- 在终端中持续收发消息
- 默认由 Steward 处理

### 2.3 Agent Task 模式

`python app.py --agent-task "..."`

- 当前默认不是“直接 Worker”
- 而是走 `run_orchestrated_task()`
- 由 Router / Planner / Executor 决定具体执行路径

### 2.4 Chat / Gateway 模式

`python -m seneschal.gateway_server`

- 对外提供 FastAPI
- 支持同步和异步任务
- 支持 chat session 查询
- 支持文件下载
- 支持 webhook / 飞书

### 2.5 Daily 模式

`python app.py --daily --daily-trigger daily`

- 从 `tasks.json` 选择任务
- 批量执行 collect 或 agent_task
- 当前仍保留部分 legacy 路径

---

## 2. Agent 架构

### 2.1 Chat Agent

职责：

- 默认聊天入口
- 承载多轮对话
- 配合 `ChatSessionManager` 实现会话恢复与保存

### 2.2 Worker Agent

职责：

- 通用单任务执行
- 检索、抓取、处理、文档生成、文件输出
- 使用本地工具链与 skill

当前典型能力：

- Brave / Web / arXiv / DBLP
- OCR
- shell
- Word / Excel / PDF / PPT
- 本地 memory / task history / steward knowledge

### 2.3 Steward Agent

职责：

- 面向手机场景的闭环任务执行
- 调用 MobiAgent 执行 collect / action
- 基于执行证据自行判断任务是否完成
- 必要时委派 Worker 做通用子任务

### 2.4 Router Agent

职责：

- 判断任务更适合由 `worker` 还是 `steward` 执行
- 输出 `target_agents / confidence / plan_required`

### 2.5 Planner Agent

职责：

- 对复杂任务拆分阶段
- 返回串行/并行子任务结构

### 2.6 Skill Selector

职责：

- 扫描 `seneschal/skills/*/SKILL.md`
- 规则召回 skill
- 用 LLM 对候选做重排
- 把 skill 上下文注入目标 Agent

---

## 2. MobiAgent 边界

`mobiagent_server/server.py` 提供稳定边界：

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`

核心职责：

- 接受 collect / action 请求
- 把 action 翻译成自然语言任务
- 在 CLI / task_queue / proxy / mock 模式间切换
- 收集截图、XML、actions、reasoning、OCR 等执行产物
- 返回统一 execution evidence

这意味着 MobiAgent 返回的是：

- **执行证据**

而不是：

- **最终业务真值**

最终是否完成，由 Steward 自主判断。

---

## 2. Gateway 边界

`seneschal/gateway_server.py` 当前承担完整任务网关能力。

核心接口包括：

- `POST /api/v1/task`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/chat/sessions`
- `GET /api/v1/chat/sessions/{context_id}`
- `GET /api/v1/env`
- `GET /api/v1/env/schema`
- `GET /api/v1/files/{job_id}/{file_name}`
- `POST /api/v1/feishu/events`

职责：

- 接收任务
- 调 `run_gateway_task()`
- 维护异步 job store
- 追加 chat history
- 暴露输出文件下载地址
- 提供 console / chat / settings 页面

---

## 2. 当前真正的状态层

当前主链路依赖的持久化主要是本地状态，而不是外部知识库：

- Chat session 目录
- agent state
- 历史消息
- `outputs/job_xxx`
- `RunContext` JSONL 日志
- `memory.py` 中的长期记忆/本地知识

因此“Store / Analyze”在当前项目里更准确的理解应该是：

- 存到本地状态、本地知识或任务输出
- 再由 Agent 基于本地数据、联网结果和手机证据继续分析


---

## 2. 配置说明

### 2.1 当前核心配置

- LLM：`OPENROUTER_*` 或 `OPENAI_*`
- Mobi：`MOBI_AGENT_BASE_URL` / `MOBI_AGENT_API_KEY`
- Routing：`SENESCHAL_ROUTING_*`
- Skill：`SENESCHAL_SKILL_*`
- Memory：`SENESCHAL_MEMORY_*`


## 2. 扩展建议

1. 继续统一文档口径，避免出现两套架构叙述
2. 增强跨模块集成测试，优先覆盖 Gateway -> Orchestrator -> Mobi 的主链路

---

## 2. 最终结论

Seneschal 当前不是“多 Agent + 多网关 + 知识库”的三层架构，而是：

> **以 Chat/Gateway 为入口，以 Orchestrator + Agents 为核心，以 MobiAgent 和本地工具/本地状态为执行基础的多 Agent 编排系统。**

