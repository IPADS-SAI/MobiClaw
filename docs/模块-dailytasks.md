# 模块文档：DailyTasks（日常任务，含 legacy 说明）

DailyTasks 用于把周期任务配置化执行，并为每次运行生成可追踪记录。

需要先明确一点：

> Daily 模块当前仍然可用，但内部仍是“现行逻辑 + legacy 路径”的混合状态。

---

## 1. 模块范围

- 执行器：`seneschal/dailytasks/runner.py`
- 任务清单：`seneschal/dailytasks/tasks/tasks.json`

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

---

## 4. 两类任务

### 4.1 `collect`

适合手机端采集型任务：

- 消息读取
- 日历/提醒检查
- App 状态采集

当前执行方式：

- 调 `call_mobi_collect(prompt)`
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

## 5. 当前执行状态

Daily 当前的现实状态：

- 可运行
- 有 RunContext 和任务选择逻辑

---

## 6. 日志与可观测性

每次运行会写：

- `seneschal/logs/{run_id}.jsonl`

典型事件：

- `task_selection`
- `collect_start` / `collect_done`
- `agent_task_start` / `agent_task_done`
- `analyze_start` / `analyze_done`

从可观测性角度看，Daily 当前最稳定的价值其实是：

- 任务选择
- 运行追踪
- 事件回放

---

## 7. 返回结构

`run_daily_tasks()` 返回：

- `run_id`
- `task_count`
- `collected`
- `analysis`

注意：

- `analysis` 当前仍可能来自 legacy 路径
- `task_count` 是命中数量，不等于成功数量

---

## 8. 当前应采用的文档口径

关于 Daily，建议统一这样描述：

### 推荐表述

- Daily 是一个批量任务执行器
- 它当前负责 trigger 过滤、任务遍历和运行日志记录


### 不再推荐表述

- Daily 已经完全对齐当前 Orchestrator 主链路

---

## 9. 后续收敛建议

1. 把 `collect` 结果统一落到本地 memory / local knowledge 路径
2. 把 `agent_task` 切换到 orchestrator 主链路，而不是直接 Worker
3. 保留 RunContext 和事件日志设计

---

## 10. 最终判断

Daily 的定位是：

> **一个批量任务执行器，核心价值在任务调度与运行追踪。**
