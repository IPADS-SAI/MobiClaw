# 模块文档：seneschal 核心编排

本文档面向**二次开发者**，帮助你快速理解 Seneschal 的入口、编排、Agent 构建与运行态日志机制。

---

## 1. 模块范围

- 入口与模式分发
  - `app.py`
  - `seneschal/workflows.py`
- Agent 构建
  - `seneschal/agents.py`
- 运行配置
  - `seneschal/config.py`
- 运行上下文与日志
  - `seneschal/run_context.py`

---

## 2. 职责边界

`seneschal` 核心编排层只做“任务组织与工具编排”，不直接负责：

- 手机端 GUI 执行细节（由 `mobiagent_server` 处理）
- 知识库底层检索/会话实现（由 WeKnora 处理）

编排层主要负责：

1. 组装 Agent（Steward / Worker / User）
2. 管理不同运行模式（demo / interactive / daily / agent-task）
3. 将用户任务转成消息与工具调用
4. 输出运行日志（run_id + jsonl）用于复盘

---

## 3. 启动链路（从 `python app.py` 开始）

### 3.1 `app.py`

- 启动时读取根目录 `.env`（仅补齐当前进程中不存在的变量）
- 初始化日志格式
- `asyncio.run(main())` 进入 `workflows.main`

### 3.2 `workflows.main`

根据 CLI 参数分支：

- 无参数：`run_demo_conversation()`
- `--interactive`：`run_interactive_mode()`
- `--daily --daily-trigger xxx`：`run_daily_tasks(trigger)`
- `--agent-task "..." [--output path]`：`run_agent_task()`

> 关键认知：`--agent-task` 走 Worker，不走 Steward。

---

## 4. Agent 构建设计

## 4.1 `create_openai_model`

统一构建 OpenAI 兼容模型实例，来源于 `MODEL_CONFIG`：

- `model_name`
- `api_key`
- `api_base`（自动补 `http://` 前缀）
- `temperature`

## 4.2 `create_worker_agent`

Worker 目标：处理“通用子任务 + 外部检索 + 本地落盘”。

典型工具：

- 检索：`brave_search`, `arxiv_search`, `dblp_conference_search`
- 抓取：`fetch_url_text`, `fetch_url_readable_text`, `fetch_url_links`
- 文件：`download_file`, `extract_pdf_text`, `write_text_file`
- 本地命令：`run_shell_command`
- 知识库检索：`weknora_knowledge_search`

Prompt 约束强调：

- 优先获取候选来源，再抓正文
- 任务结束必须给出最终文本结果
- 默认输出 Markdown（除非用户另有要求）

## 4.3 `create_steward_agent`

Steward 目标：执行核心闭环 `Collect -> Store -> Analyze -> Execute`。

关键工具：

- 采集：`call_mobi_collect_verified`
- 采集重试封装：`call_mobi_collect_with_retry_report`
- 存储分析：`weknora_add_knowledge`, `weknora_rag_chat`, `weknora_knowledge_search`
- 执行：`call_mobi_action`
- 委派：`delegate_to_worker`

关键机制：

- `STEWARD_MOBI_MAX_RETRIES` 控制重试上限（默认 2，范围 0~5）
- 重试工具返回结构化证据包（attempts/failure_report）
- 最终是否“任务完成”由 Agent 判断，不依赖单个状态字段

## 4.4 `create_user_agent`

用于 interactive 模式从终端读取用户输入。

---

## 5. 运行模式实战说明

## 5.1 Demo 模式

命令：

```bash
python app.py
```

行为：

- 自动构造一条预置消息
- 由 Steward 执行并输出最终回复

## 5.2 Interactive 模式

命令：

```bash
python app.py --interactive
```

行为：

- 循环读取用户输入
- 每轮调用 Steward
- `exit/quit/退出` 结束

## 5.3 Daily 模式

命令：

```bash
python app.py --daily --daily-trigger daily
```

行为：

- 调用 `run_daily_tasks`
- 返回 run_id、执行任务数与总结

## 5.4 Agent Task 模式

命令：

```bash
python app.py --agent-task "检索今天的 AI 新闻并生成摘要" --output "outputs/news.md"
```

行为：

- 构造 Worker 输入消息
- 如果给 `--output`，会附加“输出路径提示”到任务文本

---

## 6. 配置项说明（`config.py`）

### 6.1 LLM

- `OPENROUTER_MODEL` / `OPENAI_MODEL`
- `OPENROUTER_API_KEY` / `OPENAI_API_KEY`
- `OPENROUTER_BASE_URL` / `OPENAI_BASE_URL`

优先顺序：`OPENROUTER_*` > `OPENAI_*`。

### 6.2 WeKnora

- `WEKNORA_BASE_URL`
- `WEKNORA_API_KEY`
- `WEKNORA_KB_NAME`
- `WEKNORA_AGENT_NAME`
- `WEKNORA_SESSION_ID`

### 6.3 Mobi

- `MOBI_AGENT_BASE_URL`
- `MOBI_AGENT_API_KEY`

### 6.4 Brave Search

- `BRAVE_API_KEY`
- `BRAVE_SEARCH_BASE_URL`
- `BRAVE_SEARCH_MAX_RESULTS`

---

## 7. 运行日志与可观测性

`run_context.py` 提供：

- `create_run_context()`：生成 `run_id` 与日志文件路径
- `RunContext.log_event()`：追加事件到内存和 jsonl

日志落点：

- `seneschal/logs/{run_id}.jsonl`

日志事件字段：

- `run_id`
- `timestamp`
- `type`
- `level`
- `payload`

---

## 8. 常见扩展场景

## 8.1 新增运行模式

建议步骤：

1. 在 `workflows.main()` 增加 CLI 参数
2. 新建 `run_xxx_mode()`
3. 复用 Agent 或 runner
4. 在 README 与 docs 补充使用方式

## 8.2 调整 Steward 策略

建议优先调整：

- system prompt 的流程约束
- 工具描述（func_description）
- 重试上限环境变量

尽量避免直接在工具层硬编码业务判断。

## 8.3 增加跨模块可观测性

可在关键路径打点：

- task_received
- tool_invocation
- tool_result
- final_answer

保持 jsonl 字段稳定，便于后续接入日志平台。

---

## 9. 排障建议

- 现象：模型无响应/报鉴权错误
  - 检查 `OPENROUTER_API_KEY` 或 `OPENAI_API_KEY`
- 现象：WeKnora 调用失败
  - 检查 `WEKNORA_BASE_URL` 与 API Key
- 现象：Mobi 工具无结果
  - 检查 `MOBI_AGENT_BASE_URL`、网关服务是否启动
- 现象：interactive 意外中断
  - 查看终端 traceback 与上游工具报错信息
