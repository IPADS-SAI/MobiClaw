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
- 多智能体编排
  - `seneschal/orchestrator.py`
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

5. 为每个子任务选择可选技能（Skill），并按需注入到目标 Agent 的执行上下文
6. 输出带高亮前缀的阶段日志，并在结果中保留更完整的 routing trace 以便复盘

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
- `--agent-task "..." [--output path]`：`run_agent_task()`，默认走 orchestrator 多智能体编排

> 关键认知：当前 `--agent-task` 默认走 `router` 模式，由 `seneschal/orchestrator.py` 负责 Router / Planner / Executor / Skill Selector 协同；仅在显式传入 `--mode worker|steward|auto` 时才走 legacy 兼容路径。

---

## 4. Agent 构建设计

## 4.0 Skill 选择与注入

当前 orchestrator 在执行每个子任务前会执行 Skill Selector 子流程：

1. 发现：扫描 `seneschal/skills/*/SKILL.md`，提取 skill 名称与描述。
2. 召回：根据子任务文本与目标 agent，做规则召回并打分。
3. 重排：若开启配置，使用 LLM 对候选 skill 重排。
4. 注入：将选中的 skill 摘要注入 `create_worker_agent` / `create_steward_agent` 的 prompt。
5. 回退：若无命中、超时或解析失败，自动回退为“无 skill 执行”。

设计要点：
- Skill 是可选增强层，不改变既有 Router/Planner/Executor 主流程。
- 支持 `skill_hint` 人工覆盖（优先级高于自动选择）。
- 每个子任务最多挂载 N 个 skill（默认 2）。
- routing trace 中会记录 skill 选择来源、候选与最终结果，便于复盘。
- Planner 当前会受 `planner_allowed_agents` 约束，只在 Router 已允许的 Agent 集合中规划子任务。

## 4.1 `create_openai_model`

统一构建 OpenAI 兼容模型实例，来源于 `MODEL_CONFIG`：

- `model_name`（默认回退为 `google/gemini-3-flash-preview`）
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
- 典型工具：`brave_search`, `arxiv_search`, `dblp_conference_search`, `fetch_url_*`, `download_file`, `extract_pdf_text`, `write_text_file`, `run_shell_command`, `weknora_knowledge_search`
- 注意：仓库中虽已新增 `seneschal/tools/office.py`，但这些 Office 工具尚未接入当前 Worker 默认工具注册

Prompt 约束强调：

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

- 自动构造一条预置消息：`开始今日的数据整理和分析，给出最近活动的总结和待办事项。`
- 创建 `Steward` 与 `User`
- 由 `Steward` 执行一次单轮回复并直接打印结果
- 适合验证主链路、模型配置和基础工具链是否正常

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

- 默认走 orchestrator（Router + Planner + Executor + Skill Selector）
- 如果给 `--output`，会把“输出路径提示”附加到最后一个子任务
- 可选 `--mode` 控制执行模式：`router/intelligent` 或 legacy `worker/steward/auto`
- 可选 `--agent-hint` 控制目标 agent
- 可选 `--skill-hint` 控制 skill 选择（支持逗号分隔）
- 结果中可返回 `routing_trace`，便于复盘路由、规划、skill 选择与子任务执行明细
- `--context-id` 已预留到 CLI 与 orchestrator 入口，当前版本主要用于未来多轮编排扩展，暂不改变当前单次任务执行逻辑

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

### 6.5 Skill Selector

- `SENESCHAL_SKILL_ENABLED`
- `SENESCHAL_SKILL_ROOT_DIR`
- `SENESCHAL_SKILL_MAX_PER_SUBTASK`
- `SENESCHAL_SKILL_SELECTOR_TIMEOUT_S`
- `SENESCHAL_SKILL_LLM_RERANK`
- `SENESCHAL_SKILL_RULE_MAX_CANDIDATES`
- `SENESCHAL_SKILL_HINT_OVERRIDE`

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
