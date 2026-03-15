# 模块文档：mobiclaw 核心编排（按当前实际代码口径）

本文档面向二次开发者，说明 `mobiclaw/` 核心模块当前真实职责、运行主干与关键边界。

---

## 1. 模块范围

当前核心编排层涉及：

- `app.py`
- `mobiclaw/workflows.py`
- `mobiclaw/agents/`
- `mobiclaw/orchestrator/`
- `mobiclaw/config.py`
- `mobiclaw/run_context.py`
- `mobiclaw/session/`

这意味着旧文档中提到的：

- `mobiclaw/agents.py`
- `mobiclaw/orchestrator.py`

已经不再是当前实现的准确描述。现在它们分别对应为 package 与兼容导出层。

---

## 2. 当前职责边界

`mobiclaw` 核心编排层当前主要负责：

1. 统一入口与模式分发
2. 创建 Chat / Worker / Steward / Router / Planner / Skill Selector
3. 执行 `route -> plan -> select skills -> execute`
4. 管理 chat session 恢复与持久化
5. 管理 planner monitor 事件
6. 聚合 `reply / files / routing_trace`
7. 管理长期记忆、调度器与本地状态相关配置入口

当前它 **不直接负责**：

- 设备底层操作：由 `mobiclaw/mobile/` 中的 provider 与 device adapter 负责
- 具体联网抓取与文件处理：由 `mobiclaw/tools/*` 负责
- Daily 任务定义：由 `mobiclaw/dailytasks/tasks/tasks.json` 负责
- 外部知识库主链路：当前主链路依赖本地工具和状态，而非外部知识库

---

## 3. 启动链路

### 3.1 `app.py`

职责很薄：

- 加载根目录 `.env`
- 初始化日志
- 进入 `workflows.main()`

### 3.2 `workflows.main()`

统一处理：

- Demo
- Interactive
- Daily
- Agent Task

当前关键事实：

- `--agent-task` 默认走 orchestrator
- 只有显式传入 legacy mode 时才强制 worker / steward / auto

---

## 4. `workflows.py` 的现实地位

`workflows.py` 当前不只是 CLI 分发器，而是：

- CLI 入口分发层
- Gateway chat 流程层
- Chat session 编排层
- planner monitor 事件桥接层
- Gateway task 到 orchestrator 的转发层

### 4.1 Chat 相关职责

- 解析 `/new` / `/interrupt` / `/exit`
- 恢复已有 session
- 创建 chat agent
- 读取/写回 agent state
- 持久化历史消息
- 输出 planner_monitor 事件

### 4.2 Gateway 非 chat 路径

非 chat 请求会直接进入：

- `run_orchestrated_task()`

因此当前真正的任务主干已经集中到 orchestrator，而不是早期的单个 Steward 直接处理一切。

---

## 5. `orchestrator/` 是当前主干

### 5.1 模块组成

当前 orchestrator 是 package，而不是单文件：

- `mobiclaw/orchestrator/__init__.py`
- `mobiclaw/orchestrator/runner.py`
- `mobiclaw/orchestrator/routing.py`
- `mobiclaw/orchestrator/execution.py`
- `mobiclaw/orchestrator/skills.py`
- `mobiclaw/orchestrator/utils.py`
- `mobiclaw/orchestrator/types.py`

### 5.2 主职责

`run_orchestrated_task()` 负责：

1. route
2. plan
3. per-subtask skill select
4. execute
5. collect files
6. aggregate final reply
7. 写入 session history

### 5.3 当前 route 逻辑

优先级大致是：

1. `agent_hint`
2. legacy mode force
3. LLM route
4. timeout fallback
5. error fallback

输出通常包括：

- `target_agents`
- `reason`
- `confidence`
- `plan_required`
- `strategy`

### 5.4 当前 plan 逻辑

当任务复杂时：

- Planner 返回 `stages[][]`
- 外层表示串行阶段
- 内层表示同阶段子任务

如果 planner 失败：

- 自动 fallback plan

### 5.5 当前 execute 逻辑

- 按 stage 顺序执行
- 每个 subtask 先做 skill 选择
- 下游可获得上游摘要上下文
- 最终回收 `reply / files / routing_trace`

这意味着当前最核心的编排观念已经变成：

> 任务编排中心是 Orchestrator，而不是单个 Steward Agent。

---

## 6. `agents/` 的当前结构

### 6.1 包结构

当前 agent 构建逻辑已拆分为多个模块：

- `catalog.py`
- `common.py`
- `custom.py`
- `factories.py`
- `factories_router.py`
- `factories_steward_chat_user.py`
- `factories_worker.py`
- `types.py`
- `__init__.py`

`__init__.py` 主要承担兼容导出层职责。

### 6.2 当前实际 Agent

当前可创建的 agent 包括：

- Chat Agent
- Worker Agent
- Steward Agent
- Router Agent
- Planner Agent
- Skill Selector Agent
- User Agent

### 6.3 Worker Agent

职责：

- 通用子任务执行
- 检索、抓取、处理、输出
- 调用 web / papers / shell / office / ppt / ocr / file / skill / memory / feishu / schedule

### 6.4 Steward Agent

职责：

- 手机任务闭环
- collect / action
- 证据判断
- 必要时委派 Worker

核心判断依据通常包括：

- screenshot
- OCR
- XML / hierarchy
- actions
- reasonings
- execution summary

### 6.5 Router / Planner / Skill Selector

这是当前系统从“单 Agent 工具箱”转向“多角色编排”的关键标志。

---

## 7. `run_context.py`

当前主要作用：

- 生成 `run_id`
- 把执行过程事件写入 JSONL
- 为 Daily 和批处理提供轻量追踪能力

典型日志落点：

- `mobiclaw/logs/{run_id}.jsonl`

这部分仍是当前系统可观测性的基础组件。

---

## 8. `config.py` 的当前口径

`mobiclaw/config.py` 是当前统一配置聚合层，会在导入时加载 `.env`，并整合：

### 8.1 LLM 配置

- `MODEL_CONFIG`
- LLM：`OPENROUTER_*` / `OPENAI_*`
- Mobi：`MOBI_AGENT_*`
- Routing：`MOBICLAW_ROUTING_*`
- Skill：`MOBICLAW_SKILL_*`
- Memory：`MOBICLAW_MEMORY_*`

### 8.2 移动执行配置

- `MOBI_AGENT_CONFIG`
- `MOBILE_EXECUTOR_CONFIG`

### 8.3 RAG / 搜索 / 路由

- `RAG_CONFIG`
- `BRAVE_SEARCH_CONFIG`
- `ROUTING_CONFIG`

### 8.4 调度与记忆

- `SCHEDULE_CONFIG`
- `MEMORY_CONFIG`

### 8.5 自定义 Agent

- `CUSTOM_AGENT_CONFIG`

因此，修改运行行为时，应优先检查 `config.py`，而不是在 agent factory 中硬编码。

---

## 9. Session 层

当前会话相关能力主要位于：

- `mobiclaw/session/manager.py`

其职责包括：

- 解析/创建 session 目录
- 保存/恢复 agent state
- 维护 `history.jsonl`
- 中断活跃回复
- 为 Gateway chat 与 orchestrator 提供统一 session handle

---

## 10. 当前最重要的开发认知

1. `workflows.py` 是统一入口控制层
2. `orchestrator/` 是当前主干
3. Worker / Steward 是执行角色，不是顶层控制中心
4. `agents/`、`orchestrator/`、`session/` 都已经是 package 结构
5. 调度器、长期记忆、RAG、本地输出都属于核心运行时基础设施

---

## 11. 推荐阅读顺序

如果要理解当前 MobiClaw 的主架构，建议优先阅读：

1. `app.py`
2. `mobiclaw/workflows.py`
3. `mobiclaw/orchestrator/runner.py`
4. `mobiclaw/agents/__init__.py` + 各 factory 文件
5. `mobiclaw/config.py`
6. `mobiclaw/session/manager.py`
