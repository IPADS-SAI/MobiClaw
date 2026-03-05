# Seneschal 项目架构说明（与当前代码一致）

## 1. 项目定位

Seneschal 是一个“多 Agent + 多网关 + 知识库”的编排系统，核心职责是把不同能力串起来：

1. 通过手机端执行网关采集/执行（MobiAgent Gateway）
2. 把信息写入知识库并检索分析（WeKnora）
3. 用 Agent 工作流组织完整闭环

代码中主闭环是：`Collect -> Store -> Analyze -> Execute`。

---

## 2. 仓库结构

```text
Seneschal/
├── app.py                          # 程序入口，加载 .env 并运行 workflows.main
├── seneschal/
│   ├── workflows.py                # Demo/Interactive/Daily/AgentTask 入口
│   ├── agents.py                   # Steward / Worker / User Agent 构建
│   ├── config.py                   # LLM / WeKnora / Mobi / Brave 配置
│   ├── run_context.py              # run_id 与 JSONL 事件日志
│   ├── gateway_server.py           # Seneschal 对外任务网关
│   ├── dailytasks/
│   │   ├── runner.py               # 日常任务执行器
│   │   └── tasks/tasks.json        # 任务定义
│   └── tools/
│       ├── __init__.py             # 工具聚合、WeKnora 高阶封装、缓存
│       ├── mobi.py                 # 调用 mobiagent_server 的 collect/action
│       ├── weknora*.py             # WeKnora API 客户端封装
│       ├── web.py                  # brave + 网页抓取
│       ├── papers.py               # arXiv / DBLP / PDF 处理
│       ├── shell.py                # 命令白名单工具
│       └── file.py                 # 本地文本写入
├── mobiagent_server/server.py      # 手机执行网关（FastAPI）
├── scripts/                        # 一键启动/停止/导入导出脚本
├── configs/                        # WeKnora 导入配置样例
└── docs/                           # 项目文档
```

---

## 3. 运行模式与入口

`python app.py` 会进入 `seneschal/workflows.py`，支持 4 种模式：

- 默认：Demo 对话（Steward）
- `--interactive`：交互会话（Steward）
- `--daily --daily-trigger xxx`：任务清单执行（Daily Runner）
- `--agent-task "..." [--output path]`：通用子任务执行（Worker）

注意：

- `--agent-task` 直接走 Worker，不经过 Steward。
- Daily Runner 直接调用工具链，不通过 Steward 的 ReAct 推理循环。

---

## 4. Agent 模块

### 4.1 Steward Agent

`create_steward_agent()` 注册的关键工具：

- `call_mobi_collect_verified`
- `call_mobi_action`
- `weknora_add_knowledge`
- `weknora_rag_chat`
- `weknora_knowledge_search`
- `weknora_list_knowledge_bases`
- `fetch_url_text`
- `run_shell_command`
- `call_mobi_collect_with_retry_report`（封装重试证据包）
- `delegate_to_worker`（子任务委派）

设计重点：

- 显式重试上限由 `STEWARD_MOBI_MAX_RETRIES` 控制（默认 2）。
- 最终“任务是否完成”由 Agent 基于证据判断，不直接信任工具状态字段。

### 4.2 Worker Agent

`create_worker_agent()` 偏向“检索/处理/落盘”：

- Brave 搜索
- arXiv / DBLP 学术检索
- URL 抓取与可读化
- 下载文件、提取 PDF 文本
- shell 白名单命令
- 写文件
- WeKnora 检索

---

## 5. 工具层

`seneschal/tools/__init__.py` 统一导出工具，并做 WeKnora 缓存管理：

- 缓存文件：`seneschal/tools/weknora_cache.json`
- 缓存内容：KB、Agent、Session 映射
- 能力：按名称解析 KB/Agent，必要时自动建会话、补标签

`mobi.py` 与 `mobiagent_server` 通信；失败时可回退到 mock 数据。

`shell.py` 默认禁止管道与重定向，只允许白名单命令（环境变量可改）。

---

## 6. DailyTasks

任务定义在 `seneschal/dailytasks/tasks/tasks.json`，核心字段：

- `task_id`, `triggers`, `description/steps`
- 可选 `task_type=agent_task`（走 Worker）
- 可选 `output_path`（给 Worker 的落盘提示）

执行流程（`run_daily_tasks`）：

1. 按 trigger 过滤任务
2. collect 类任务调用 `call_mobi_collect`
3. 写入 WeKnora（附 run_id/task_id 元数据）
4. 所有 collect 完成后做一次统一 RAG 总结
5. 事件写入 `seneschal/logs/{run_id}.jsonl`

---

## 7. 网关模块

### 7.1 `mobiagent_server/server.py`

API：

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`
- `GET /`

模式：

- `mock`
- `proxy`
- `task_queue`
- `cli`

CLI 模式会：

- 生成任务文件与 data 目录
- 执行外部 CLI 命令
- 扫描图片/XML/action/react 构建 `execution_result.json`
- 可对 `output_schema` 做 VLM 抽取

### 7.2 `seneschal/gateway_server.py`

API：

- `POST /api/v1/task`（`async_mode` 支持异步）
- `GET /api/v1/jobs/{job_id}`
- `GET /health`

内部逻辑：创建 Steward 并执行任务；异步任务结果存内存 `_JOB_STORE`。

---

## 8. 配置要点

`seneschal/config.py` 约定：

- LLM：`OPENROUTER_*` 优先，回退 `OPENAI_*`
- WeKnora：`WEKNORA_BASE_URL/WEKNORA_API_KEY/WEKNORA_KB_NAME/WEKNORA_AGENT_NAME/WEKNORA_SESSION_ID`
- Mobi：`MOBI_AGENT_BASE_URL/MOBI_AGENT_API_KEY`
- Brave：`BRAVE_API_KEY` 等

`mobiagent_server` 约定：

- `MOBIAGENT_SERVER_MODE`
- `MOBIAGENT_CLI_CMD`
- `MOBIAGENT_TASK_DIR/MOBIAGENT_DATA_DIR`
- `MOBIAGENT_QUEUE_DIR/MOBIAGENT_RESULT_DIR`
- `MOBIAGENT_GATEWAY_PORT`

---

## 9. 扩展建议

1. 增加新任务：修改 `tasks/tasks.json`
2. 增加新工具：在 `seneschal/tools/` 新增并在 Agent 注册
3. 增强执行器：替换 `MOBIAGENT_CLI_CMD` 或改 `proxy/task_queue` 后端
4. 增强分析：通过 WeKnora 自定义 Agent、模型与检索策略配置
