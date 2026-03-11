# 模块文档：DailyTasks（日常任务）

DailyTasks 用于把“可重复的周期任务”配置化执行，并统一沉淀到知识库。

---

## 1. 模块范围

- 执行器：`seneschal/dailytasks/runner.py`
- 任务清单：`seneschal/dailytasks/tasks/tasks.json`

---

## 2. 设计目标

1. 把“每日/每晚等周期采集”从代码中抽离为 JSON 配置
2. 执行过程结构化记录（run_id + 事件日志）
3. 自动写入 WeKnora，便于后续统一检索与总结

---

## 3. 任务定义结构

任务由 `tasks.json` 管理，基础字段建议：

- `task_id`: string，唯一 ID
- `category`: string，业务分类（可选）
- `app`: string，来源应用（可选）
- `priority`: string，优先级（可选）
- `triggers`: list[string]，触发标签（必填）
- `description`: string，任务描述
- `steps`: string，更详细的执行提示（优先于 description）
- `task_type`: string，可选 `collect`（默认）/ `agent_task`
- `output_path`: string，仅 `agent_task` 典型使用
- `output_schema`: object，可选，用于定义结构化预期

> 目前 runner 核心依赖 `task_id/triggers/description|steps/task_type/output_path`，其余字段可作为元数据沉淀。

---

## 4. 执行流程（`run_daily_tasks`）

1. 创建/接收 `RunContext`
2. 读取 `tasks.json`
3. 按 trigger 过滤任务
4. 遍历任务：
   - `collect`：`call_mobi_collect` -> `weknora_add_knowledge`
   - `agent_task`：创建 Worker 执行任务
5. collect 任务执行完后，触发统一 `weknora_rag_chat` 总结
6. 返回汇总对象：`run_id/task_count/collected/analysis`

---

## 5. 两类任务语义

## 5.1 collect（默认）

适合手机端采集任务：

- 电商订单检查
- 日历/消息读取
- 通知状态采集

落库逻辑：

- 每个任务一条 knowledge
- metadata 附带 `run_id/task_id/category/app/trigger/timestamp`
- 若采集工具返回 metadata，也会透传到 `collect_metadata`

## 5.2 agent_task

适合“纯信息处理/检索/写文件”任务：

- 联网新闻总结
- 论文抓取与摘要
- 生成日报并落盘

执行方式：

- 由 Worker Agent 直接处理，不经过 orchestrator 的 Router / Planner / Skill Selector 链路
- 支持 `output_path` 提示输出路径

---

## 6. 日志与可观测性

每次 run 会记录事件到：

- `seneschal/logs/{run_id}.jsonl`

典型事件：

- `task_selection`
- `collect_start` / `collect_done`
- `agent_task_start` / `agent_task_done`
- `analyze_start` / `analyze_done`

建议在运维侧按 `run_id + task_id` 聚合观察成功率和耗时。

---

## 7. 返回结构（给调用方）

`run_daily_tasks` 返回 dict：

- `run_id`: 本次运行 ID
- `task_count`: 命中的任务数
- `collected`: 每个任务的结果列表
- `analysis`: 统一总结结果（ToolResponse 或 None）

上层 `workflows.py` 目前会打印 `run_id` 与 `task_count`。

---

## 8. 任务扩展实践

## 8.1 新增任务

1. 在 `tasks.json` 添加新对象
2. 确保 `triggers` 包含目标触发标签
3. 本地执行 `python app.py --daily --daily-trigger <trigger>` 验证

## 8.2 新增 trigger 体系

可约定：

- `daily`：每日
- `morning`：早间
- `evening`：晚间
- `weekly`：每周

调度侧（cron/外部系统）按 trigger 调不同命令。

## 8.3 新增 task_type

1. 在 runner 中新增分支（例如 `pipeline_task`）
2. 定义输入字段与输出结构
3. 复用现有工具，避免重复实现
4. 补充日志事件与失败语义

---

## 9. 风险与限制

- 当前 `task_count` 是“命中任务数”，不等于“成功任务数”
- `agent_task` 的落盘可靠性依赖 Worker 是否调用 file 工具
- 统一总结仅在 `collect_count > 0` 时执行
- 错误重试策略目前主要由工具层/上游服务决定

---

## 10. 排障手册

- 没有任务执行：确认 trigger 是否匹配 `triggers`
- 采集结果为空：检查 mobi 网关状态与认证
- 未写入 WeKnora：检查 base_url、api_key、kb 解析
- 无分析结果：确认本次是否有 collect 任务成功执行
