# Orchestrator 模块说明

本文档解释 `mobiclaw/orchestrator` 目录下各文件职责、核心调用链与维护要点。

## 目录与职责

### `__init__.py`

- 作用：编排模块的统一导出层（facade）。
- 做了什么：
  - 聚合并导出 routing、planning、execution、skills、utils、types 中的函数与数据结构。
  - 供外部通过 `mobiclaw.orchestrator` 统一访问内部能力。
- 维护建议：
  - 新增 orchestrator 能力时，需要同步在这里导出，保证 override 机制可见。

### `types.py`

- 作用：编排系统的公共类型与常量定义。
- 核心内容：
  - 数据结构：`RouteDecision`、`SkillProfile`、`SkillDecision`。
  - 进度回调类型：`ProgressCallback`。
  - 模式集合：`LEGACY_MODES`、`ROUTER_MODES`。
  - 日志高亮 ANSI 常量与 `_highlight_log`。
- 维护建议：
  - 只放“纯类型/常量”，避免引入业务逻辑依赖，保持跨模块可复用。

### `routing.py`

- 作用：任务路由与任务拆分规划。
- 核心能力：
  - 路由：规则路由 `_rule_route`、LLM 路由 `_llm_route`。
  - 规划：LLM 规划 `_llm_plan`、规则回退 `_fallback_plan`。
  - Agent 名规范化与可用 Agent 管理：`_available_agent_names`、`_normalize_agent_name`、`_planner_allowed_agents`。
- 典型行为：
  - 优先 LLM 路由，超时/异常回退规则路由。
  - 任务复合时生成 stages（串行阶段 + 阶段内子任务）。

### `skills.py`

- 作用：子任务技能选择与技能提示上下文构建。
- 核心能力：
  - 扫描技能目录并构建缓存画像：`_available_skill_profiles`。
  - 规则筛选 + LLM rerank：`_rule_select_skills`、`_llm_rerank_skills`。
  - 处理 `skill_hint` 覆盖策略。
  - 为 Worker 生成技能提示词块：`_skill_prompt_context`，并注入 skill 的 `execution_dir`。
- 维护建议：
  - 只在这里处理“选技能”逻辑，不要把执行逻辑耦合进来。

### `execution.py`

- 作用：单个子任务执行（按 agent）与 Agent 构建。
- 核心能力：
  - `_build_agent`：根据名称构建 agent，支持 custom factory 与 fallback。
  - `_run_one_agent`：
    - 组装子任务 prompt（含前序上下文、外部上下文、输出路径提示、临时目录提示）。
    - 注入 `job_context`（如 `job_output_dir`、`job_tmp_dir`、`mobi_output_dir`、feishu IDs）。
    - 执行 agent 并提取文本输出。
  - `_aggregate_replies`：汇总多子任务最终回复。
- 维护建议：
  - 该文件聚焦“单子任务执行语义”，不要放路由或全局调度策略。

### `runner.py`

- 作用：编排总控器（端到端 orchestrated run）。
- 主流程（`run_orchestrated_task`）：

  1. 解析 mode/strategy，建立会话。
  2. 创建 job 输出目录与 tmp 目录。
  3. 路由决策（legacy/hint/llm/rule）。
  4. 规划 stages（llm/fallback/direct）。
  5. 逐 stage 执行子任务（当前是串行 with context），每个子任务先选技能再执行。
  6. 汇总 reply，兜底写 output 文件。
  7. 汇总 files 与 routing_trace，返回结构化结果。
- 额外职责：
  - 进度事件 `_emit_progress`，向上层持续推送 orchestrator 进度。

### `utils.py`

- 作用：编排辅助工具集合（文本、路径、JSON、文件收集）。
- 核心能力：
  - 响应文本抽取：`_extract_response_text`。
  - 回复/显式参数中的文件路径提取：`_collect_file_paths`。
  - 输出文件兜底写入：`_ensure_output_file_written`。
  - 文件条目序列化：`_build_file_entries`。
  - 路径去重合并：`_merge_file_paths`。
  - tmp 目录文件扫描：`_collect_tmp_dir_file_paths`。
    - 当前已做白名单过滤，仅收集文档/图片相关文件（如 md/pdf/docx/pptx/xlsx/csv/png/jpg 等）。
  - 外部上下文渲染（当前主要用于 Feishu）：`_build_external_context_text`。
  - JSON 解析容错：`_parse_json_object`。
  - job 输出路径创建：`_create_job_output_paths`。

## 核心调用关系

外部入口通常调用：

- `run_orchestrated_task` in `runner.py`

内部调用链：

1. `runner.py` -> `routing.py` 决策路由与规划。
2. `runner.py` -> `skills.py` 为每个子任务选择技能。
3. `runner.py` -> `execution.py` 执行每个子任务。
4. `execution.py` -> `utils.py`（文本提取 / 外部上下文等）。
5. `runner.py` -> `utils.py`（路径、输出文件、tmp 文件、最终 files）。

## 返回结果结构（高层）

`run_orchestrated_task` 最终返回字典包含：

- `reply`: 最终文本回复
- `mode`, `context_id`, `session_id`, `session`
- `files`: 产物文件元数据列表（由 `utils._build_file_entries` 生成）
- `routing_trace`: 路由/规划/技能/执行时序追踪

## Override 机制

目录内大量函数通过 `_orchestrator_override(name, default)` 获取实际实现。这意味着：

- 可在 `mobiclaw.orchestrator` 模块级替换同名函数，实现注入或灰度行为。
- 也是测试替身（monkeypatch）的主要入口。

## 常见维护场景

### 1) 调整路由策略

- 优先改 `routing.py`：`_rule_route`、`_llm_route`、`_fallback_plan`。

### 2) 调整技能选择策略

- 改 `skills.py`：规则打分、hint 覆盖、llm rerank、prompt context。

### 3) 调整最终 files 记录策略

- 改 `utils.py`：`_collect_tmp_dir_file_paths` 和 `_build_file_entries`。

### 4) 调整单子任务提示词与上下文

- 改 `execution.py`：`_run_one_agent` 内的 `msg_content` 组装。

## 维护注意事项

- `runner.py` 目前 stage 内是串行执行（`execution_mode: sequential_with_context`）。如果改并行，需同步处理：
  - 前序上下文一致性
  - shared_file_paths 合并冲突
  - session state 的读写时序
- `utils._parse_json_object` 是关键容错点，关系到路由/规划/技能选择稳定性。
- `utils._create_job_output_paths` 决定输出与 tmp 目录布局，改动会影响 gateway / file exposure / tests。
