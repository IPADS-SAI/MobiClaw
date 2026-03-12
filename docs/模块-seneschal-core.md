# 模块文档：seneschal 核心编排（按当前实际代码口径）

本文档面向二次开发者，说明 `seneschal/` 核心模块当前真实职责、运行主干与历史残留边界。

---

## 1. 模块范围

- `app.py`
- `seneschal/workflows.py`
- `seneschal/agents.py`
- `seneschal/orchestrator.py`
- `seneschal/config.py`
- `seneschal/run_context.py`

---

## 2. 当前职责边界

`seneschal` 核心编排层当前主要负责：

1. 统一入口与模式分发
2. 组装 Chat / Worker / Steward / Router / Planner / Skill Selector
3. 执行 `route -> plan -> execute`
4. 管理 chat session 恢复与持久化
5. 管理 planner monitor 事件
6. 聚合 reply / files / routing_trace

当前它 **不直接负责**：

- 手机端 GUI 执行细节：由 `mobiagent_server` 负责
- 具体联网抓取与文件处理：由 `seneschal/tools/*` 负责
- 外部知识库主链路：当前代码里已无 WeKnora 主依赖

注意：

- 仓库里仍存在 WeKnora 相关封装与配置
- 但它们应视为 legacy / 兼容残留，而不是当前核心编排依赖

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

当前关键变化：

- `--agent-task` 默认走 orchestrator
- 只有显式传入 legacy mode 时才强制 worker / steward / auto

---

## 4. `workflows.py` 的现实地位

`workflows.py` 当前不是简单 CLI 分发器，而是：

- CLI 入口分发层
- Gateway chat 流程层
- Chat session 编排层
- planner monitor 事件桥接层

### 4.1 Chat 相关职责

- 解析 `/new` / `/interrupt` / `/exit`
- 恢复已有 session
- 创建 chat agent
- 读取/写回 agent state
- 持久化历史消息

### 4.2 Gateway 非 chat 路径

非 chat 请求会直接进入：

- `run_orchestrated_task()`

因此当前真正的任务主干已经集中到 orchestrator，而不是早期的单个 Steward 直接处理一切。

---

## 5. `orchestrator.py` 是当前主干

### 5.1 主职责

`run_orchestrated_task()` 负责：

1. route
2. plan
3. per-subtask skill select
4. execute
5. collect files
6. aggregate final reply

### 5.2 当前 route 逻辑

优先级大致是：

1. `agent_hint`
2. legacy mode force
3. LLM route
4. timeout fallback
5. error fallback

输出：

- `target_agents`
- `reason`
- `confidence`
- `plan_required`
- `strategy`

### 5.3 当前 plan 逻辑

当任务复杂时：

- Planner 返回 `stages[][]`
- 外层表示串行阶段
- 内层表示同阶段子任务

如果 planner 失败：

- 自动 fallback plan

### 5.4 当前 execute 逻辑

- 按 stage 顺序执行
- 每个 subtask 先做 skill 选择
- 下游可获得上游摘要上下文
- 最终回收 `reply / files / routing_trace`

这意味着当前最核心的编排观念已经变成：

> 任务编排中心是 Orchestrator，而不是单个 Steward Agent。

---

## 6. `agents.py` 的当前结构

### 6.1 Chat Agent

职责：

- 默认聊天入口
- 处理多轮对话
- 配合 `ChatSessionManager` 做状态保存

### 6.2 Worker Agent

职责：

- 通用子任务执行
- 研究、检索、抓取、处理、输出

当前主要依赖：

- web / papers / shell / office / ppt / ocr / file / skill / memory

### 6.3 Steward Agent

职责：

- 手机任务闭环
- collect / action
- 证据判断
- 必要时委派 Worker

当前核心判断依据不是知识库，而是：

- screenshot
- OCR
- XML / hierarchy
- actions
- reasonings
- VLM completion judgment

### 6.4 Router / Planner / Skill Selector

这是当前系统从“单 Agent 工具箱”转向“多角色编排”的关键标志。

---

## 7. `run_context.py`

当前主要作用：

- 生成 `run_id`
- 把执行过程事件写入 JSONL
- 为 Daily 和其它批处理提供轻量追踪能力

日志落点：

- `seneschal/logs/{run_id}.jsonl`

这部分仍是当前系统可观测性的基础组件。

---

## 8. `config.py` 的当前口径

### 8.1 当前主链路相关配置

- LLM：`OPENROUTER_*` / `OPENAI_*`
- Mobi：`MOBI_AGENT_*`
- Routing：`SENESCHAL_ROUTING_*`
- Skill：`SENESCHAL_SKILL_*`
- Memory：`SENESCHAL_MEMORY_*`

### 8.2 当前仍保留但属于 legacy 的配置

- `WEKNORA_*`

这些字段仍在配置中，但不应再被当作当前运行主链路的必需条件。

---

## 9. 当前最重要的开发认知

1. `workflows.py` 是统一入口控制层
2. `orchestrator.py` 是当前主干
3. Worker / Steward 是执行角色，不是顶层控制中心
4. MobiAgent 是当前唯一明确的外部执行边界
5. Store / Analyze 当前更多依赖本地状态、本地工具结果和 Agent 推理
6. WeKnora 是 legacy，不应继续作为核心编排边界来写

---

## 10. 后续清理建议

1. 把旧文档中的 WeKnora 主链路表述全部替换
2. 继续收敛 Daily 中的 legacy 路径
3. 考虑把 WeKnora 相关封装移动到 `legacy/` 或单独目录
4. 增加 `Gateway -> Orchestrator -> Mobi` 的集成测试
