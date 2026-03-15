# 模块文档：DailyTasks

DailyTasks 用于把周期任务配置化执行，并为每次运行生成可追踪记录。

需要先明确一点：

> Daily 模块当前仍然可用，但内部仍是“现行逻辑 + legacy 路径”的混合状态。

---

## 1. 模块范围

- 执行器：`mobiclaw/dailytasks/runner.py`
- 任务清单：`mobiclaw/dailytasks/tasks/tasks.json`

---

## 2. 当前设计目标

Daily 现在实际承担的目标是：

1. 把可重复周期任务从代码中抽离成 JSON
2. 按 trigger 执行批处理
3. 为整轮运行生成 `run_id` 和事件日志
4. 对 collect 或 agent_task 做统一调度

---

## 3. 当前真实执行流程

`run_daily_tasks()` 的主流程是：

1. 创建或接收 `RunContext`
2. 读取 `tasks.json`
3. 按 trigger 过滤任务
4. 遍历任务
5. 记录结构化事件
6. 返回 `run_id / task_count / collected`

注意：当前返回值里已经没有旧文档中的 `analysis` 字段。

---

## 4. 两类任务

### 4.1 `collect`

适合手机端采集型任务：

- 消息读取
- 日历/提醒检查
- App 状态采集

当前执行方式：

- 调 `call_mobi_collect_verified(prompt, max_retries=0)`
- 记录返回内容和 metadata
- 写入 `RunContext`

### 4.2 `agent_task`

适合通用信息处理任务：

- 新闻总结
- 论文检索
- 生成日报
- 文件落盘

当前执行方式：

- 直接创建 Worker Agent
- 不经过 Orchestrator 的 Router / Planner / Skill Selector 主干
- 可附带 `output_path` 提示

这说明 Daily 当前并没有完全对齐现行 orchestrator 主架构。

---

## 5. 日志与可观测性

每次运行会写：

- `mobiclaw/logs/{run_id}.jsonl`

典型事件：

- `task_selection`
- `collect_start` / `collect_done`
- `agent_task_start` / `agent_task_done`

从可观测性角度看，Daily 当前最稳定的价值是：

- 任务选择
- 运行追踪
- 事件回放

---

## 6. 与 Scheduler 的关系

需要区分两套能力：

### 6.1 DailyTasks

- 基于 `tasks.json`
- 由 CLI 主动触发：`python app.py --daily --daily-trigger ...`
- 更像“预定义批处理清单”

### 6.2 Scheduler

- 基于 `mobiclaw/scheduler/`
- 由 APScheduler + JSON store 驱动
- 可通过 Gateway API 和 Worker 工具创建、查看、取消
- 更像“动态定时任务系统”

因此，Daily 与 Scheduler 是互补关系，不应再混为一个模块。

---

## 7. 当前应采用的文档口径

关于 Daily，建议统一这样描述：

- Daily 是一个批量任务执行器
- 它当前负责 trigger 过滤、任务遍历和运行日志记录
- 它内部仍保留部分 legacy 路径，尚未完全收敛到现行主架构
- 动态定时任务能力应归入 `scheduler/`，不是 Daily 自身的一部分

---

## 8. 后续收敛方向

当前最合理的收敛方向包括：

1. 把 `agent_task` 切换到 orchestrator 主链路，而不是直接 Worker
2. 保留 `RunContext` 与事件日志设计
3. 继续把 Daily 定位为批处理入口，而不是通用调度器
