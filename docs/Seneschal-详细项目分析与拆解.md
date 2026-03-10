# Seneschal 详细项目分析与拆解

## 1. 项目整体定位

Seneschal 本质上是一个 **Agent 编排层**，目标是把：

- **MobiAgent**：负责手机端 GUI 执行与信息采集
- **WeKnora**：负责知识库存储、检索、RAG 分析
- **Seneschal**：负责统一调度、任务编排、自动化闭环

串成一个完整流程：

**Collect -> Store -> Analyze -> Execute**

这一定位在 `README.md:3` 有清晰说明；代码入口也与此一致：

- `app.py:34` 导入 `seneschal.workflows.main`
- `seneschal/workflows.py:151` 统一处理 demo / interactive / daily / agent-task 四种模式

---

## 2. 仓库结构拆解

### 2.1 顶层结构

从仓库根目录看，当前项目包含以下关键部分：

- `app.py`：CLI 启动入口
- `README.md`：项目总览、运行步骤、接口说明
- `pyproject.toml`：依赖与 workspace 配置
- `seneschal/`：主编排逻辑
- `mobiagent_server/`：手机执行网关
- `MobiAgent/`：手机执行相关子模块
- `WeKnora/`：知识库系统子模块
- `scripts/`、`configs/`、`docs/`：部署、配置、文档层

### 2.2 `seneschal/` 核心目录

`seneschal/` 是主系统核心，关键文件如下：

- `seneschal/workflows.py`：统一运行入口与模式分发
- `seneschal/agents.py`：Steward / Worker Agent 构建
- `seneschal/config.py`：环境变量配置读取
- `seneschal/tools.py`：工具统一导出层
- `seneschal/tools/`：Mobi / WeKnora / Web / Shell / File / Papers 工具实现
- `seneschal/gateway_server.py`：FastAPI 任务网关
- `seneschal/dailytasks/runner.py`：Daily 任务执行器
- `seneschal/run_context.py`：运行上下文与 JSONL 事件日志

---

## 3. 技术栈分析

从 `pyproject.toml:1-290` 看，项目是一个 **Python 3.12+ 的多 Agent / 多工具编排系统**。

### 3.1 Agent / 模型层

当前主路径使用：

- `agentscope`
- `OpenAIChatModel`
- OpenAI 兼容推理接口

关键位置：

- `seneschal/agents.py:13`
- `seneschal/agents.py:37-49`

虽然依赖里还包含：

- `anthropic`
- `openai`
- `litellm`
- `google-genai`

但从主路径看，**当前实际编排逻辑是基于 AgentScope + OpenAI compatible API**。

### 3.2 服务层

- `FastAPI`
- `uvicorn`

对应：

- `seneschal/gateway_server.py:13`
- `mobiagent_server/server.py:25`

### 3.3 外部能力接入

- `requests`：用于 MobiAgent / WeKnora / Web 抓取调用
- Brave Search API
- arXiv / DBLP
- 本地 shell
- 文件系统读写

---

## 4. 程序入口与运行模式

### 4.1 `app.py`

`app.py` 很薄，职责非常集中：

1. `app.py:13-32` 手动加载 `.env`
2. `app.py:34` 导入 `seneschal.workflows.main`
3. `app.py:47` 执行 `asyncio.run(main())`

这说明入口层被刻意保持轻量，主要逻辑都下沉到 `workflows.py`。

### 4.2 `workflows.py`

`seneschal/workflows.py:151-195` 统一定义四种运行模式：

#### A. Demo 模式
默认运行 `run_demo_conversation()`：
- 构造一条预设用户消息
- 创建 Steward Agent
- 执行一次演示对话

#### B. 交互模式
通过 `--interactive` 进入 `run_interactive_mode()`：
- 持续接收用户输入
- 交给 Steward 处理
- 支持 `exit/quit/退出`

#### C. Worker 单任务模式
通过 `--agent-task` 进入 `run_agent_task()`：
- 直接调用 Worker Agent
- 支持可选 `--output` 输出路径提示

#### D. Daily 模式
通过 `--daily --daily-trigger xxx` 调用 `run_daily_tasks()`：
- 加载任务清单
- 按 trigger 执行批量流程

这个设计表明项目既支持：

- 本地交互式使用
- 单次自动任务执行
- 批处理式定时任务
- 对外服务接入

---

## 5. Agent 架构拆解

当前项目最核心的设计，是把 Agent 分成 **Steward** 和 **Worker** 两层。

### 5.1 Worker Agent：通用单任务执行器

位置：`seneschal/agents.py:52-136`

Worker 的角色是：

- 处理通用问题
- 处理单一子任务
- 使用工具做检索、下载、抓取、提取、落盘

它注册的工具包括：

- `run_shell_command`
- `brave_search`
- `arxiv_search`
- `dblp_conference_search`
- `fetch_url_text`
- `fetch_url_readable_text`
- `fetch_url_links`
- `download_file`
- `extract_pdf_text`
- `write_text_file`
- `weknora_knowledge_search`

从它的 system prompt（`seneschal/agents.py:110-126`）可以看出，Worker 被设计成一个：

**偏研究 / 抓取 / 处理 / 生成结果的 utility agent**。

特点：

- 更强调“给最终结果”
- 不做长轮对话
- 适合论文检索、网页理解、文本输出

---

### 5.2 Steward Agent：主控编排器

位置：`seneschal/agents.py:139-398`

Steward 是系统主控 Agent，职责包括：

1. 理解用户需求
2. 规划完整流程
3. 协调 MobiAgent 和 WeKnora
4. 必要时把子任务委派给 Worker

它注册的工具更偏主流程闭环：

- `call_mobi_collect_verified`
- `call_mobi_action`
- `weknora_add_knowledge`
- `weknora_rag_chat`
- `weknora_knowledge_search`
- `weknora_list_knowledge_bases`
- `fetch_url_text`
- `run_shell_command`
- `call_mobi_collect_with_retry_report`
- `delegate_to_worker`

---

## 6. Mobi 采集重试证据包机制

这是当前实现中比较关键且成熟的一部分。

位置：`seneschal/agents.py:208-299`

### 6.1 设计目的

手机 GUI Agent 的执行结果往往不能只看一个 success flag，需要综合：

- OCR 文本
- 截图
- 推理记录
- action 历史
- step 数量

因此项目引入了 `call_mobi_collect_with_retry_report()`：

- 基于 `call_mobi_collect_verified()` 做封装
- 每次采集都形成 attempt 记录
- 支持有限重试
- 重试后仍失败则输出 failure_report

### 6.2 返回证据内容

每次 attempt 中会记录：

- `run_dir`
- `index_file`
- `status_hint`
- `step_count`
- `action_count`
- `screenshot_path`
- `last_reasoning`
- `ocr_preview`
- `extracted_info`
- `tool_success`

最终还会生成一个结构化 pack：

- `report_type`
- `original_task`
- `success_criteria`
- `retry_limit`
- `attempt_count`
- `criteria_matched`
- `attempts`
- `failure_report`

### 6.3 架构意义

这说明作者已经意识到：

**手机执行器的“执行完成”不能被直接信任，必须由上层 Agent 根据证据自主判断。**

这是 GUI Agent 场景下很重要的设计点。

---

## 7. 配置体系分析

位置：`seneschal/config.py:8-39`

当前配置分为四组。

### 7.1 模型配置

- `OPENROUTER_MODEL`
- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- 回退支持 `OPENAI_*`

默认值：

- model：`qwen/qwen3.5-397b-a17b`
- base_url：`https://openrouter.ai/api/v1`

### 7.2 MobiAgent 配置

- `MOBI_AGENT_BASE_URL`
- `MOBI_AGENT_API_KEY`

### 7.3 WeKnora 配置

- `WEKNORA_BASE_URL`
- `WEKNORA_API_KEY`
- `WEKNORA_KB_NAME`
- `WEKNORA_AGENT_NAME`
- `WEKNORA_SESSION_ID`

### 7.4 Brave Search 配置

- `BRAVE_API_KEY`
- `BRAVE_SEARCH_BASE_URL`
- `BRAVE_SEARCH_MAX_RESULTS`

整体是典型的 **env-driven configuration**。

---

## 8. 工具层设计分析

### 8.1 `seneschal/tools.py`：统一导出层

位置：`seneschal/tools.py:1-56`

该文件并不实现具体逻辑，而是把下列子模块统一导出：

- `seneschal.tools.mobi`
- `seneschal.tools.weknora`
- `seneschal.tools.shell`
- `seneschal.tools.file`
- `seneschal.tools.web`
- `seneschal.tools.papers`

作用：

- 降低 `agents.py` 对子模块的直接依赖
- 保持工具注册层的简洁
- 形成一个 facade / compatibility layer

---

### 8.2 `mobi.py`：手机执行工具封装

位置：`seneschal/tools/mobi.py:19-225`

#### 收集链路

- `_collect_request()`：调用 `/api/v1/collect`
- `_normalize_collect_result()`：抽取 success/message/data
- `_extract_summary_from_execution()`：从 `execution_result.json` 或返回体中提取摘要
- `call_mobi_collect_verified()`：单次执行，返回结构化证据

抽取的重点信息有：

- `ocr_text`
- `screenshot_path`
- `last_reasoning`
- `action_count`
- `step_count`
- `status_hint`
- `run_dir`
- `index_file`

#### 操作链路

- `call_mobi_action()`：调用 `/api/v1/action`

如果请求失败，会 fallback 到 mock：

- `seneschal/tools/mobi.py:199-215`

这说明当前系统支持开发态降级运行。

---

### 8.3 WeKnora 工具层

WeKnora 部分的封装明显比其他工具更“SDK 化”。

关键文件：

- `seneschal/tools/weknora.py`
- `seneschal/tools/weknora/__init__.py`
- `seneschal/tools/weknora/base.py`
- `seneschal/tools/weknora/knowledge.py`
- 以及 `knowledge_base / chat / session / tag / tenant / agent / model` 等模块

#### `base.py`
位置：`seneschal/tools/weknora/base.py:15-142`

提供统一基础设施：

- `_base_url()`
- `build_headers()`
- `request_json()`
- `parse_json_response()`
- `parse_sse_response()`

#### `knowledge.py`
位置：`seneschal/tools/weknora/knowledge.py:14-272`

提供知识对象管理：

- 文件导入
- URL 导入
- 手工内容导入
- 列表
- 详情
- 删除
- 下载
- 更新
- 标签管理

#### `__init__.py`
位置：`seneschal/tools/weknora/__init__.py:8-168`

统一导出全套 API，形成 SDK 风格的使用方式。

### 8.4 结论

WeKnora 封装层比主流程里实际使用的能力更广，说明：

- 这个 SDK 层是为未来扩展准备的
- 当前 Seneschal 主流程只接入了其中一部分核心能力

---

### 8.4 `web.py`：网页抓取工具

位置：`seneschal/tools/web.py:19-289`

主要能力：

- `fetch_url_text()`：抓原始文本
- `fetch_url_readable_text()`：去 HTML 后的可读文本
- `fetch_url_links()`：提取页面链接
- `brave_search()`：通过 Brave Search API 做联网搜索

实现特点：

- 使用 `requests`
- 用正则做简单 HTML 清洗
- 用简单规则猜主内容区域
- 用关键词过滤噪声链接

这是一套 **轻量网页理解工具**，适合 Agent 快速阅读页面，不是重型 crawler。

---

### 8.5 `shell.py`：受控 shell 工具

位置：`seneschal/tools/shell.py:14-105`

特点：

- 白名单命令
- 禁止 `| ; && || > < $ \`` 等 token
- 使用 `shlex.split`
- 使用 `subprocess.run`

默认允许的命令包含：

- `ls`
- `rg`
- `grep`
- `cat`
- `head`
- `tail`
- `sed`
- `awk`
- `find`
- `whoami`
- `uname`
- `date`
- `pwd`
- `mkdir`

定位是：

**给 Agent 一个受限的本地探测能力，而不是完整 shell。**

---

## 9. MobiAgent Gateway 深入拆解

位置：`mobiagent_server/server.py:1-686`

这是当前项目与手机执行器之间的关键边界层。

### 9.1 网关职责

对外暴露统一 API：

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`
- `GET /`

它屏蔽了底层执行方式差异，支持多种 mode。

### 9.2 支持的模式

由 `load_config()` 中的 `MOBIAGENT_SERVER_MODE` 控制：

- `cli`
- `proxy`
- `task_queue`
- 其他情况默认 mock

#### `cli` 模式

直接调用本地 CLI：

- 生成任务文件
- 创建 data 目录
- 执行外部命令
- 解析产物
- 构建 `execution_result.json`

#### `proxy` 模式

转发到上游 collect/action 服务。

#### `task_queue` 模式

将任务写入 queue 目录，等待异步结果文件。

#### mock 模式

返回伪造 collect / action 数据。

---

### 9.3 CLI 模式是当前最值得关注的执行链

#### 任务文件生成

`mobiagent_server/server.py:194-213`

- `_write_task_file()` 生成 task JSON
- 创建 `cli_task_dir` 和 `cli_data_dir`
- 兼容 legacy `runner.mobiagent.mobiagent` 的 JSON list 格式

#### 执行 CLI

`mobiagent_server/server.py:511-553`

- `_run_cli_job()` 读取 `MOBIAGENT_CLI_CMD`
- 用 `_render_cli_cmd()` 替换占位符
- `subprocess.run(..., shell=True)` 执行命令
- 成功时构建结构化 execution 结果

#### 执行结果解析

`mobiagent_server/server.py:351-416`

`_build_execution_result()` 会扫描：

- `actions.json`
- `react.json`
- 每一步截图 `*.jpg`
- 每一步 hierarchy `*.xml` / `*.json`
- overlay 图层

然后生成：

- `summary`
- `artifacts`
- `history`
- `ocr`
- `index_file`

这实际上是在把底层 CLI 执行结果，标准化成一个统一“证据包”。

---

### 9.4 OCR / 层级文本的提取方式

#### Step 文件扫描

`mobiagent_server/server.py:259-287`

- 扫描数字命名的截图文件
- 对应查找 `.xml` 或 `.json`
- 收集 overlay 文件

#### XML 文本抽取

`mobiagent_server/server.py:290-307`

- 从 XML 节点属性里抽取：
  - `text`
  - `content-desc`
  - `content_desc`
  - `label`
  - `name`

#### 汇总 OCR

`mobiagent_server/server.py:310-329`

- 每一步生成文本
- 再汇总成 `full_text`

这说明当前所谓 OCR，实际上并不只依赖视觉 OCR，更依赖 UI hierarchy 文本抽取。

---

### 9.5 action -> task 的桥接方式

位置：`mobiagent_server/server.py:126-145`

`_build_task_from_action()` 会把结构化 action 转成自然语言任务，例如：

- `add_calendar_event` -> “打开系统日历并创建日程...”
- `send_message` -> “通过微信给某人发送消息...”
- `set_reminder` -> “在系统提醒事项中创建提醒...”

这说明 action API 的底层并不是一个强约束动作执行器，而是：

**把结构化 action 再翻译成手机 Agent 可理解的任务描述。**

换句话说，执行仍然是语言驱动的，而非纯脚本驱动。

---

### 9.6 `output_schema` + VLM 抽取能力

位置：`mobiagent_server/server.py:465-509`

这是一个比较有价值的增强点。

流程是：

1. action 请求里传入 `output_schema`
2. CLI 执行完成后找到最终截图
3. 调 `_call_vl_model()` 把截图 + 最后 react + task 描述发给视觉模型
4. 要求模型严格按 schema 输出 JSON
5. 最终保存到 `parsed_output`

这意味着 MobiAgent Gateway 不只是“执行器网关”，也承担了部分**结果结构化抽取**职责。

---

## 10. Seneschal Gateway 深入拆解

位置：`seneschal/gateway_server.py:1-145`

这是把 Steward Agent 暴露成 HTTP 服务的入口。

### 10.1 提供的接口

- `GET /health`
- `POST /api/v1/task`
- `GET /api/v1/jobs/{job_id}`

### 10.2 执行模型

`seneschal/gateway_server.py:81-86`

内部执行时：

- `create_steward_agent()`
- 构造 `Msg(name="User", content=task, role="user")`
- 等待 Steward 返回
- 返回文本 reply

### 10.3 异步模式

`POST /api/v1/task` 支持 `async_mode=True`：

- 生成 `job_id`
- 在 `_JOB_STORE` 里存为 `running`
- 后台 `asyncio.create_task(_run_job(...))`
- 完成后写回结果

### 10.4 当前限制

`_JOB_STORE` 是内存字典：

- 进程重启即丢失
- 无法跨实例共享
- 更适合 demo / 轻量服务，不适合强任务可靠性场景

---

## 11. DailyTasks 机制拆解

位置：`seneschal/dailytasks/runner.py:57-151`

### 11.1 任务来源

任务定义来自：

- `seneschal/dailytasks/tasks/tasks.json`

当前 runner 会：

1. 读取 tasks
2. 按 trigger 过滤
3. 顺序执行

### 11.2 两类任务

#### A. `agent_task`

`seneschal/dailytasks/runner.py:73-101`

- 直接创建 Worker
- 把 prompt 发给 Worker
- 可附带 `output_path` 提示
- 收集 Worker 文本输出

#### B. collect 任务

`seneschal/dailytasks/runner.py:103-133`

- 调 `call_mobi_collect()`
- 把结果写入 WeKnora
- 附加 metadata：
  - `run_id`
  - `task_id`
  - `category`
  - `app`
  - `trigger`
  - `timestamp`

### 11.3 统一分析阶段

`seneschal/dailytasks/runner.py:135-145`

当 collect_count > 0 时，会做一次统一 RAG 总结：

- `weknora_rag_chat()`
- 输出摘要 / 待办 / 风险 / 建议行动

这说明 Daily 模式是：

**批量采集 -> 统一入库 -> 统一分析**

而不是每个任务采完就单独分析。

---

## 12. RunContext 日志机制

位置：`seneschal/run_context.py:18-50`

该模块实现了轻量级运行上下文：

- 每次运行生成 `run_id`
- 自动记录 `started_at`
- 事件写入 `seneschal/logs/{run_id}.jsonl`

记录方式：

- `task_selection`
- `collect_start`
- `collect_done`
- `agent_task_start`
- `agent_task_done`
- `analyze_start`
- `analyze_done`

这为后续审计、问题回放、结果追踪提供了基础。

---

## 13. 当前架构的优点

### 13.1 分层较清晰

- 入口层：`app.py`
- 流程层：`workflows.py`
- Agent 层：`agents.py`
- 工具层：`tools/`
- 服务层：`gateway_server.py` / `mobiagent_server/server.py`
- 批处理层：`dailytasks/runner.py`

### 13.2 Agent 分工明确

- Steward：管主流程与决策
- Worker：做局部执行与检索

这是比较标准且合理的 manager-worker 结构。

### 13.3 Mobi 与 WeKnora 的边界清晰

- Mobi：手机端执行与证据采集
- WeKnora：知识存储、检索、RAG
- Seneschal：统一协调与编排

### 13.4 Mobi 证据包机制设计成熟

相较于简单 success/fail，项目引入：

- screenshot
- OCR
- react/action history
- reasoning
- failure pack

这更贴合真实 GUI Agent 场景。

### 13.5 同时支持 CLI / HTTP / Daily 三类入口

有利于：

- 人工使用
- 系统集成
- 定时批处理

---

## 14. 当前实现中的主要问题与风险

### 14.1 Gateway 异步任务状态只存在内存

位置：`seneschal/gateway_server.py:64-66`

问题：

- 服务重启后任务状态会丢失
- 不支持多实例部署
- 缺少持久化和可靠队列能力

### 14.2 Shell 工具虽然有限制，但默认白名单仍偏宽

位置：`seneschal/tools/shell.py:15-18`

包含：

- `sed`
- `awk`
- `grep`
- `cat`
- `find`

虽然有 token 限制，但如果以后对外暴露为多租户服务，这部分仍值得进一步收紧。

### 14.3 Mobi action 失败 fallback mock 容易掩盖真实问题

位置：`seneschal/tools/mobi.py:199-215`

风险：

- 真失败时仍可能返回模拟结果
- 如果调用方只看文字不看 metadata，可能误判为成功

### 14.4 WeKnora SDK 封装很全，但主流程只用了少数能力

这不是 bug，但说明当前实际主流程还比较“轻”，很多潜力能力尚未真正接入。

### 14.5 Daily runner 当前是串行执行

位置：`seneschal/dailytasks/runner.py:68-145`

如果任务较多，整体执行时间会线性增加。

### 14.6 配置缺省值偏开发态

位置：`seneschal/config.py:10-31`

例如存在占位值：

- `sk-or-v1-xxx`
- `mobi-xxx`
- `sk-Q-xxx`

这便于开发，但在真实环境里，如果没有显式校验，容易出现“系统启动了，但关键能力不可用”的半坏状态。

### 14.7 `test_ipads.py` 存在硬编码密钥

位置：`test_ipads.py:5-12`

该文件里直接出现了：

- 明文 API Key
- 明文 base URL

这属于明显的敏感信息暴露风险。即便它只是本地测试脚本，也不应该以这种形式保存在仓库中。

---

## 15. 项目成熟度判断

综合来看，我会把当前 Seneschal 判断为：

**“已有较清晰架构、可运行主流程，但整体仍偏实验性 / 原型化的多 Agent 编排系统。”**

### 已经比较成熟的部分

- 分层清晰
- 入口清楚
- Agent 职责分工明确
- 外部系统边界清晰
- Mobi 证据包机制有实际经验沉淀
- CLI / Gateway / Daily 三条使用路径都已打通

### 仍偏原型的部分

- 异步任务无持久化
- mock 与真实执行边界仍有混用
- shell 与 CLI 执行边界仍偏开发态
- 大量模块可用但缺少更严格的生产级约束

---

## 16. 一句话总结

**Seneschal 是一个围绕“手机采集 + 知识库存储 + Agent 分析 + 手机执行”构建的个人自动化管家编排层。**

它把手机端执行器、知识库系统与多 Agent 推理组织在一起，用于形成个人数据整理和任务执行的自动化闭环。

---

## 17. 建议的后续深入分析方向

如果后续继续拆解，建议优先沿以下方向继续：

1. **MobiAgent 主链路继续深挖**
   - 从 `mobiagent_server` 继续下钻到 `MobiAgent/`
   - 理清 CLI 执行器、产物格式、失败模式

2. **WeKnora 主接入链继续深挖**
   - 继续查看 `weknora_add_knowledge / weknora_rag_chat` 的具体实现
   - 梳理知识入库、检索、会话、引用返回结构

3. **DailyTasks 任务模板与数据流整理**
   - 分析 `tasks.json`
   - 梳理每日自动化任务与 metadata 设计

4. **产出完整系统设计文档**
   - 按“模块职责 + 调用链 + 数据流 + 风险点 + 运行方式”组织为系统设计说明

---

## 19. 主链路深挖：用户请求 -> Steward -> Mobi / WeKnora / Worker -> 返回结果

这一节沿着系统最核心的一条路径，按实际代码调用关系，把用户请求如何进入系统、如何被 Steward 理解并分流到 Mobi / WeKnora / Worker，以及最终如何返回给用户完整拆开。

---

### 19.1 用户请求的两个正式入口

当前主链路实际上有两个主入口。

#### A. CLI 入口

位置：`app.py:47` + `seneschal/workflows.py:151-195`

调用关系是：

1. `app.py` 加载 `.env`
2. `app.py:34` 导入 `seneschal.workflows.main`
3. `app.py:47` 执行 `asyncio.run(main())`
4. `seneschal/workflows.py:155-179` 解析命令行参数
5. 根据模式分发到：
   - `run_demo_conversation()`
   - `run_interactive_mode()`
   - `run_agent_task()`
   - `run_daily_tasks()`

如果是“用户直接与 Steward 对话”的主路径，本质上主要落在：

- `seneschal/workflows.py:62-99` `run_interactive_mode()`
- `seneschal/workflows.py:12-59` `run_demo_conversation()`

这两条路径最终都会构造一个 `Msg(name="User", content=..., role="user")`，再把它送进 Steward。

#### B. HTTP Gateway 入口

位置：`seneschal/gateway_server.py:108-128`

这条链路更适合系统集成或外部服务调用：

1. 外部调用 `POST /api/v1/task`
2. `submit_task()` 校验鉴权与 task 非空
3. 同步模式下直接调用 `_run_task(task)`
4. `_run_task()` 内部：
   - `create_steward_agent()`
   - 构造 `Msg(name="User", content=task, role="user")`
   - `await steward(msg)`
5. 最终返回 `{"reply": text}`

关键位置：

- `seneschal/gateway_server.py:81-86`
- `seneschal/gateway_server.py:126-128`

也就是说，无论是 CLI 还是 HTTP，进入 Agent 层之前都会被统一归一化成一条 AgentScope 的 `Msg` 消息对象。

---

### 19.2 进入 Steward：真正的主控分发点

位置：`seneschal/agents.py:139-398`

`create_steward_agent()` 是整个主链路的中枢。

它做了三件关键事情：

1. 创建 `Toolkit`
2. 注册主流程工具
3. 构造带 system prompt 的 `ReActAgent`

#### 19.2.1 Steward 拿到的工具面

Steward 当前可直接调用的工具是：

- `call_mobi_collect_verified`
- `call_mobi_action`
- `weknora_add_knowledge`
- `weknora_rag_chat`
- `weknora_knowledge_search`
- `weknora_list_knowledge_bases`
- `fetch_url_text`
- `run_shell_command`
- `call_mobi_collect_with_retry_report`
- `delegate_to_worker`

对应代码：`seneschal/agents.py:144-323`

可以看出，Steward 并不是一个“什么都自己做”的 Agent，而是一个：

- 决策器
- 流程编排器
- 工具调度器
- 子任务分发器

#### 19.2.2 Steward 的 system prompt 如何约束主链路

最关键的约束在 `seneschal/agents.py:326-388`。

这个 prompt 直接规定了主链路顺序：

1. **Collect + Verify**：优先 `call_mobi_collect_with_retry_report`
2. **Store**：用 `weknora_add_knowledge` 持久化
3. **Analyze**：用 `weknora_rag_chat` 做知识分析
4. **Retrieve**：必要时先搜知识库或网页
5. **Delegate**：小任务或通用任务可下发给 Worker
6. **Execute**：最后如需落地动作，再 `call_mobi_action`

这意味着：

**系统的主链路并不是硬编码的 if/else 工作流，而是“由 prompt 规定顺序、由 ReActAgent 决策调用哪些工具”的半显式编排。**

因此真正的流程控制分成两层：

- 显式控制：工具注册与函数实现
- 隐式控制：Steward system prompt 对步骤顺序的约束

---

### 19.3 用户请求进入 Steward 后的数据形态

Steward 接收到的输入，本质是一个 AgentScope 的消息对象：

```python
Msg(name="User", content=task, role="user")
```

见：

- `seneschal/workflows.py:34-38`
- `seneschal/workflows.py:118-122`
- `seneschal/gateway_server.py:83`
- `seneschal/agents.py:313`

这一层没有额外 schema，也没有复杂 envelope。说明系统当前把“用户自然语言请求”作为核心任务表达。

好处是简单灵活。

代价是：

- 请求结构不强约束
- 是否触发 Collect / Search / Execute，全靠 Steward 理解语义后自主选工具

也就是说，这个系统当前主链路是 **language-first orchestration**，而不是 strongly typed workflow orchestration。

---

### 19.4 主分支一：Steward -> Mobi Collect

这是最符合项目定位的一条主路径。

#### 19.4.1 Steward 为什么优先走这个分支

根据 `seneschal/agents.py:337-345`，只要用户是在做“数据整理 / 信息获取 / 手机端状态查看”，Steward 应优先调用：

- `call_mobi_collect_with_retry_report()`

而不是直接相信单次 collect。

#### 19.4.2 重试证据包函数是怎么组织的

位置：`seneschal/agents.py:208-299`

这个函数本质上是对 `call_mobi_collect_verified()` 的上层编排封装。

内部流程：

1. 初始化 `attempts`
2. 循环执行单次 collect
3. 每次从 `resp.metadata` 里抽取关键证据
4. 形成 `attempt_item`
5. 如果提供了 `success_criteria`，就在 OCR / reasoning / extracted_info 中做字符串匹配
6. 若未满足标准且还可重试，则改写任务描述重试
7. 最终输出统一 `pack`

关键字段：

- `report_type`
- `original_task`
- `success_criteria`
- `retry_limit`
- `attempt_count`
- `criteria_matched`
- `attempts`
- `failure_report`

这里有一个非常重要的架构点：

**Steward 不把 Mobi 的 success 当作最终完成，而是把 Mobi 当成“证据采集器”。**

也就是说，Mobi 返回的不是业务真相，而是供 Steward 判断的证据材料。

#### 19.4.3 单次 collect 是怎么发出去的

位置：`seneschal/tools/mobi.py:70-146`

主链路如下：

1. `call_mobi_collect_verified()`
2. 内部调用 `_collect_request(task_desc, timeout=180)`
3. `_collect_request()` 向：
   - `POST {MOBI_AGENT_BASE_URL}/api/v1/collect`
4. 请求体：
   ```json
   {
     "task": "...",
     "options": {
       "ocr_enabled": true,
       "timeout": 180
     }
   }
   ```
5. 返回 JSON 后经 `_normalize_collect_result()` 归一化
6. 再经 `_extract_summary_from_execution()` 抽取摘要字段
7. 最后包装成 `ToolResponse`

关键位置：

- `seneschal/tools/mobi.py:70-82`
- `seneschal/tools/mobi.py:90-136`

#### 19.4.4 Mobi collect 返回给 Steward 的不是原始响应，而是二次抽象结果

`call_mobi_collect_verified()` 返回给 Steward 的 `metadata` 中，已经被整理成：

- `execution`
- `ocr_text`
- `screenshot_path`
- `last_reasoning`
- `action_count`
- `step_count`
- `status_hint`
- `run_dir`
- `index_file`
- `raw_data`

也就是：

**Seneschal 在 Mobi 之上又做了一层 semantic normalization。**

Steward 不需要直接理解底层 `actions.json / react.json / jpg / xml`，而是读提炼后的统一字段。

---

### 19.5 Mobi Gateway 内部：Collect 请求如何变成“证据包”

这一层位于：`mobiagent_server/server.py`

#### 19.5.1 `/api/v1/collect` 的行为分支

位置：`mobiagent_server/server.py:556-599`

Collect API 根据 `MOBIAGENT_SERVER_MODE` 走不同分支：

- `cli`
- `proxy`
- `task_queue`
- mock

其中对主链路最关键的是 `cli` 模式：

```python
result = _run_cli_job(cfg, request.task, None, request.options.timeout or cfg.timeout_s)
return GatewayResponse(success=result.get("status") == "ok", message=result["status"], data=result)
```

也就是说 collect 的核心，不是直接在 HTTP 层做识别，而是：

**HTTP Gateway -> CLI 执行器 -> 产物目录 -> execution_result.json -> HTTP 返回**

#### 19.5.2 CLI 模式如何把任务送到底层执行器

位置：`mobiagent_server/server.py:511-553`

`_run_cli_job()` 的主链路：

1. `_write_task_file()` 生成 task JSON
2. `_render_cli_cmd()` 用模板渲染命令
3. `subprocess.run(..., shell=True)` 执行外部 CLI
4. 判断返回码形成 `status`
5. 定位有效 `data_dir`
6. 如果执行成功，调用 `_build_execution_result()`

关键输入产物：

- `task_file`
- `data_dir`
- CLI stdout/stderr
- `execution_result.json`

#### 19.5.3 `_build_execution_result()` 如何生成统一证据

位置：`mobiagent_server/server.py:351-416`

这个函数是 Mobi Gateway 最核心的标准化层。

它会：

1. 读 `actions.json`
2. 读 `react.json`
3. 扫描各 step 的截图和 hierarchy
4. 用 `_collect_execution_ocr()` 汇总文本
5. 提取 reasonings
6. 统计 `step_count / action_count / last_action`
7. 用 `_status_hint_from_history()` 估计状态
8. 生成统一结构：
   - `summary`
   - `artifacts`
   - `history`
   - `ocr`
   - `run_dir`
   - `index_file`

这说明在系统主链路里，Mobi Gateway 的真实职责不是“单纯转发任务”，而是：

**把底层手机执行过程重新编码成一个可被上层 Agent 消费的标准执行证据模型。**

#### 19.5.4 OCR 实际并不完全是视觉 OCR

从：

- `mobiagent_server/server.py:259-329`
- `mobiagent_server/server.py:290-307`

可以看出，当前所谓 OCR 很大程度是：

- 扫描 step 对应 hierarchy XML / JSON
- 从节点属性里提取 `text / content-desc / label / name`
- 汇总成 `full_text`

所以这一层更准确地说是：

**UI hierarchy text extraction + step aggregation**

而不是纯截图 OCR。

这也是为什么 Steward 能比较稳定拿到 `ocr_text` 并做后续判断。

---

### 19.6 主分支二：Steward -> WeKnora Store

当 Steward 已经拿到可保留的信息后，会进入 Store 阶段。

位置：

- 调用方：`seneschal/agents.py:352-355`
- 实现：`seneschal/tools/__init__.py:260-345`

#### 19.6.1 `weknora_add_knowledge()` 做了什么

该函数不是直接把文本 POST 出去，而是做了几层处理：

1. 如果 `content` 不是字符串，先转 JSON 字符串
2. 解析 title
3. 通过 `_resolve_kb_id()` 找实际知识库 ID
4. 调 `create_knowledge_manual()`
5. 从响应中提取 `knowledge_id`
6. 如果没显式 tag，则自动按当天日期打 tag

真正落库调用在：

- `seneschal/tools/weknora/knowledge.py:75-106`

底层接口是：

- `POST /knowledge-bases/{kb_id}/knowledge/manual`

通过 `request_json()` 发出，见：

- `seneschal/tools/weknora/base.py:42-81`

#### 19.6.2 这一步在主链路中的意义

这里不是简单的日志记录，而是把 Mobi 收集结果转成 **可检索、可引用、可 RAG 分析的知识对象**。

换句话说，Collect 阶段拿到的还是“本次运行产物”，而 Store 阶段才把它升格为长期知识。

这是用户请求从“实时执行”进入“长期记忆”的关键边界。

---

### 19.7 主分支三：Steward -> WeKnora Analyze

位置：

- 调用入口：`seneschal/tools/__init__.py:348-457`
- 底层聊天：`seneschal/tools/weknora/chat.py:34-55`

#### 19.7.1 `weknora_rag_chat()` 的主链路

它的工作并不是简单把 query 发给 WeKnora，而是先补全上下文：

1. 解析 `session_id`
2. 必要时缓存 `_SESSION_ID`
3. 如果未显式传 `knowledge_base_ids`，则解析目标知识库
4. 自动构造 `mentioned_items`
5. 默认启用：
   - `agent_enabled = True`
   - `web_search_enabled = True`
6. 自动解析 `agent_id`
7. 最终调用 `agent_chat(session_id, query, **kwargs)`

关键位置：

- `seneschal/tools/__init__.py:359-393`

底层真正请求是：

- `POST /agent-chat/{session_id}`

见：`seneschal/tools/weknora/chat.py:34-55`

#### 19.7.2 WeKnora 返回如何被再次包装

`agent_chat()` 返回的是解析过的 SSE 聚合结果，结构来自：

- `seneschal/tools/weknora/base.py:97-141`

包含：

- `answer`
- `thinking`
- `references`
- `events`

随后 `weknora_rag_chat()` 再把其中 `answer` 取出，封装成：

```text
[WeKnora] 分析完成。
问题: ...
结果: ...
```

同时把完整结构保留在 `metadata["result"]` 里。

也就是说，主链路里 WeKnora 有两层抽象：

1. API 层：SSE -> 聚合结果
2. Tool 层：聚合结果 -> Agent 可消费的 ToolResponse

#### 19.7.3 会话不存在时的自动修复

`weknora_rag_chat()` 还有一个很关键的细节：

- 如果 `agent_chat()` 返回 404
- 它会尝试 `create_session()` 自动创建新会话
- 更新 `WEKNORA_CONFIG["session_id"]`
- 再重试一次 `agent_chat()`

对应：`seneschal/tools/__init__.py:394-418`

这意味着主链路里，WeKnora 分析阶段具备一定自愈能力。

---

### 19.8 主分支四：Steward -> Worker 委派

位置：`seneschal/agents.py:310-324`

#### 19.8.1 委派是怎么发生的

Steward 不会直接操作 Worker 内部工具，而是通过本地定义的 `delegate_to_worker(task)`：

1. `create_worker_agent()`
2. 构造 `Msg(name="User", content=task, role="user")`
3. `await worker(msg)`
4. 把 Worker 文本结果包成 `ToolResponse`

返回形式：

```text
[Worker 结果]
...
```

这说明 Worker 在架构里不是并列主控，而是：

**Steward 的一个可动态创建的子 Agent 工具。**

#### 19.8.2 Worker 拿到的能力面

见：`seneschal/agents.py:52-136`

Worker 注册的是偏通用处理工具：

- shell
- Brave Search
- arXiv
- DBLP
- 网页抓取
- 下载文件
- 提取 PDF
- 写文件
- 检索 WeKnora

可以看出 Worker 更适合：

- 外部资料检索
- 学术搜索
- 网页阅读
- 本地轻量处理
- 产出最终文本结果

而不负责手机主流程闭环。

#### 19.8.3 在主链路中的定位

因此用户请求一旦包含这些类型：

- “帮我查一下网页/新闻/资料”
- “调研某主题并总结”
- “把结果保存到文件”
- “查知识库里已有内容”

Steward 就更可能：

1. 不走 Mobi
2. 或只把 Mobi 当补充
3. 把子任务丢给 Worker
4. 再由自己汇总回答

这条路径体现的是：

**Steward 负责 route，Worker 负责 execute。**

---

### 19.9 主分支五：Steward -> Mobi Action

位置：`seneschal/tools/mobi.py:149-225` 与 `mobiagent_server/server.py:602-651`

当 Steward 经过分析后，认为需要落地执行操作，例如：

- 添加日历
- 发消息
- 建提醒
- 打开某个应用

才会进入 Execute 分支。

#### 19.9.1 Seneschal 侧如何发起 action

`call_mobi_action(action_type, payload)` 的流程是：

1. 解析 JSON payload
2. 向 Mobi Gateway 发送：
   - `POST /api/v1/action`
3. 请求体包含：
   - `action_type`
   - `params`
   - `options.wait_for_completion`
   - `options.timeout`
4. 将结果包装为 `ToolResponse`

关键位置：

- `seneschal/tools/mobi.py:149-197`

#### 19.9.2 Gateway 侧如何把 action 转成手机任务

位置：`mobiagent_server/server.py:126-145` 与 `602-616`

`/api/v1/action` 并不是执行强约束脚本，而是：

1. 用 `_build_task_from_action()` 把结构化 action 翻译成自然语言 task
2. 再调用 `_run_cli_job()`
3. 也就是重新走一次 Mobi CLI 执行链

例如：

- `add_calendar_event` -> “打开系统日历并创建日程...”
- `send_message` -> “通过微信给某人发送消息...”
- `set_reminder` -> “在系统提醒事项中创建提醒...”

这说明当前 action 本质上依旧是：

**structured intent -> natural language mobile task -> GUI agent execution**

而不是脚本级 determinisitc action engine。

#### 19.9.3 `output_schema` 的增强路径

如果 action 请求中带有 `output_schema`，Gateway 还会在执行完成后：

1. 找到最后截图
2. 调用视觉模型 `_call_vl_model()`
3. 按 schema 解析最终界面
4. 生成 `parsed_output`

对应：`mobiagent_server/server.py:465-509` 与 `611-615`

这相当于给 Execute 分支增加了一个“执行后结构化抽取”层。

---

### 19.10 返回结果：是如何从各工具重新回流给用户的

这一层要分成两段看：

#### 19.10.1 工具返回给 Steward 的统一形态

无论是：

- Mobi collect
- Mobi action
- WeKnora add knowledge
- WeKnora rag chat
- Worker delegate

返回给 Steward 的统一对象都是 `ToolResponse`。

`ToolResponse` 里主要包含：

- `content`：给模型可见的文本
- `metadata`：结构化附加信息

这意味着主链路中的上游工具分支，虽然底层调用完全不同，但在 Agent 层被统一成一个抽象协议。

这也是 ReActAgent 能统一调度这些异构能力的前提。

#### 19.10.2 Steward 最终如何回给用户

Steward 执行结束后，外层只取文本内容：

- CLI 演示模式：`seneschal/workflows.py:44-49`
- Agent task 模式：`seneschal/workflows.py:127-142`
- Gateway 模式：`seneschal/gateway_server.py:84-86`

典型代码：

```python
text = response.get_text_content() if response else ""
```

也就是说，最终直接返回给用户的通常是 **Steward 的自然语言总结文本**。

而：

- Mobi 的证据 metadata
- WeKnora 的 references / events
- Worker 的子步骤过程

默认并不会原样暴露给最终调用方，除非 Steward 在回复文本中主动引用它们。

所以从整体上看，系统的返回链路是：

**异构工具结果 -> ToolResponse -> Steward 综合理解 -> 最终文本答复**

而不是把底层结构化结果直接透传给用户。

---

### 19.11 把整条主链路压缩成一张逻辑图

#### 情况 A：典型“整理并分析”链路

1. 用户输入任务
2. CLI / HTTP Gateway 构造 `Msg`
3. `create_steward_agent()` 创建 Steward
4. `await steward(msg)`
5. Steward 调 `call_mobi_collect_with_retry_report()`
6. Mobi Gateway `/api/v1/collect`
7. CLI 执行手机 Agent
8. 产出 `execution_result.json`
9. Seneschal 提取证据字段
10. Steward 判断任务完成度
11. 调 `weknora_add_knowledge()` 入库
12. 调 `weknora_rag_chat()` 分析
13. Steward 汇总结论
14. 返回用户

#### 情况 B：典型“检索 / 调研”链路

1. 用户输入问题
2. 进入 Steward
3. Steward 判断不需要手机端 collect
4. 调 `delegate_to_worker()`
5. Worker 自主调用搜索 / 网页 / 文件工具
6. Worker 输出最终文本
7. Steward 汇总或直接转述
8. 返回用户

#### 情况 C：典型“分析后执行动作”链路

1. 用户请求整理并落地操作
2. Steward collect -> store -> analyze
3. Steward 判断需要执行操作
4. 征得确认后调 `call_mobi_action()`
5. Mobi Gateway `/api/v1/action`
6. action -> 自然语言 task
7. CLI 执行手机 GUI 流程
8. 返回执行结果
9. Steward 汇总回复用户

---

### 19.12 这一主链路最本质的架构特征

综合这次深挖，可以把当前主链路的本质概括成下面四点：

#### 1. 入口统一、内部靠 Agent 决策分流

无论 CLI 还是 HTTP，都会归一化为 `Msg -> Steward`。

#### 2. Mobi 是证据采集器和执行器，不是最终裁判

最终完成判定在 Steward，不在 Mobi。

#### 3. WeKnora 是长期记忆与分析层

Collect 结果只有入库后，才真正成为可复用知识。

#### 4. Worker 是被 Steward 工具化的子 Agent

Worker 不是独立主流程，而是可按需拉起的能力扩展器。

---

### 19.13 这条主链路里的关键风险点

再沿链路回看，当前还有几个值得注意的结构性风险：

#### A. 最终结果高度依赖 Steward 的 prompt 与模型判断

因为主流程不是硬编码状态机，而是 ReAct + prompt 约束，所以不同模型或不同提示稳定性会直接影响分流质量。

#### B. Mobi collect 的 `success` 不能代表业务完成

这一点设计上已经有意识规避，但依然依赖 Steward 是否真的严格审证。

#### C. Mobi action 失败会 fallback mock

见：`seneschal/tools/mobi.py:199-215`

这会让 Execute 分支存在“表面成功”的风险。

#### D. 对外返回以文本为主，底层结构化证据默认被隐藏

如果后续要做可靠自动化或二次系统集成，可能需要把 `metadata` 结果也显式暴露出来，而不只是文本 reply。

---

### 19.14 一句话总结主链路

**Seneschal 的主链路本质上是：把用户自然语言请求交给 Steward，由 Steward 以 ReAct 方式在 Mobi、WeKnora 和 Worker 之间选择与编排，再把异构工具返回压缩成最终面向用户的自然语言结果。**

---

## 20. 第二轮补充说明

本轮补充重点聚焦于“用户请求 -> Steward -> Mobi / WeKnora / Worker -> 返回结果”的运行主链路，额外深入阅读了以下实现：

- `seneschal/agents.py`
- `seneschal/tools/mobi.py`
- `seneschal/tools/__init__.py`
- `seneschal/tools/weknora/base.py`
- `seneschal/tools/weknora/chat.py`
- `seneschal/tools/weknora/knowledge.py`
- `seneschal/tools/weknora/knowledge_base.py`
- `seneschal/tools/weknora/knowledge_search.py`
- `seneschal/gateway_server.py`
- `mobiagent_server/server.py`
