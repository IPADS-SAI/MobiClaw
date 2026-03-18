# 模块文档：mobiclaw/tools 工具层（按当前实际代码口径）

本文档说明当前工具层的能力分类、主链路接入情况，以及与 scheduler / memory / mobile executor 的真实关系。

---

## 1. 模块范围

当前工具层可按 5 类理解：

### 1.1 移动执行工具

- `mobiclaw/tools/mobi.py`

### 1.2 联网与资料工具

- `mobiclaw/tools/web.py`
- `mobiclaw/tools/papers.py`

### 1.3 本地处理工具

- `mobiclaw/tools/shell.py`
- `mobiclaw/tools/file.py`
- `mobiclaw/tools/ocr.py`
- `mobiclaw/tools/office.py`
- `mobiclaw/tools/ppt.py`
- `mobiclaw/tools/skill_runner.py`

### 1.4 状态与集成工具

- `mobiclaw/tools/feishu.py`
- `mobiclaw/tools/schedule.py`
- `mobiclaw/tools/memory/long_term_memory.py`
- `mobiclaw/tools/memory/rag.py`

### 1.5 聚合导出层

- `mobiclaw/tools/__init__.py`

---

## 2. 当前工具层的真实定位

更准确的现实是：

1. **Mobi 工具**：把手机任务转给 `mobiclaw.mobile.MobileExecutor`
2. **Web / Papers 工具**：负责联网检索与学术资料处理
3. **Local 工具**：负责 shell、OCR、文件、Office、PPT 与 skill 脚本执行
4. **Stateful 工具**：负责 memory、schedule、feishu 等带状态或外部集成的能力
5. **聚合导出层**：为 agent factory 提供统一工具注册入口

也就是说，当前工具层的主轴是：

> **Mobile + Local Tools + Memory + Scheduler + Integrations**

---

## 3. 聚合导出层实际导出的能力

`mobiclaw/tools/__init__.py` 当前实际导出：

- `call_mobi_action`
- `call_mobi_collect_verified`
- `write_text_file`
- `arxiv_search`
- `dblp_conference_search`
- `download_file`
- `extract_pdf_text`
- `read_docx_text`
- `create_docx_from_text`
- `edit_docx`
- `create_pdf_from_text`
- `read_xlsx_summary`
- `write_xlsx_from_records`
- `write_xlsx_from_rows`
- `run_skill_script`
- `extract_image_text_ocr`
- `run_shell_command`
- `brave_search`
- `fetch_url_text`
- `fetch_url_readable_text`
- `fetch_url_links`
- `fetch_feishu_chat_history`
- `get_feishu_message`
- `read_memory`
- `update_long_term_memory`
- `store_task_result`
- `search_task_history`
- `store_steward_knowledge`
- `search_steward_knowledge`

注意：`schedule.py` 中的定时任务工具并不在 `tools/__init__.py` 统一导出，而是由 `Worker` factory 单独引入并注册。

---

## 4. Mobi 工具族

文件：

- `mobiclaw/tools/mobi.py`

核心能力：

- `call_mobi_collect_verified`
- `call_mobi_action`

当前真实作用：

- 是 Steward 主链路最关键的工具边界之一
- 不再直接依赖旧文档里的外部单独网关口径，而是调用 `mobiclaw.mobile.MobileExecutor`
- 统一返回 `ToolResponse + metadata`

重点 metadata 包括：

- `success`
- `requires_agent_validation`
- `execution`
- `final_image_path`
- `last_reasoning`
- `action_count`
- `step_count`
- `status_hint`
- `run_dir`
- `index_file`

---

## 5. Web / Papers 工具族

### 5.1 Web 工具

文件：

- `mobiclaw/tools/web.py`

能力：

- `brave_search`
- `fetch_url_text`
- `fetch_url_readable_text`
- `fetch_url_links`

### 5.2 Papers 工具

文件：

- `mobiclaw/tools/papers.py`

能力：

- `arxiv_search`
- `dblp_conference_search`
- `download_file`
- `extract_pdf_text`

这些能力当前仍是 Worker 主链路的重要组成部分。

---

## 6. Local 工具族

### 6.1 shell

文件：

- `mobiclaw/tools/shell.py`

特性：

- 白名单约束
- 禁止危险 token

### 6.2 file

文件：

- `mobiclaw/tools/file.py`

能力：

- 文本落盘

### 6.3 ocr

文件：

- `mobiclaw/tools/ocr.py`

能力：

- 本地图片 OCR

### 6.4 office / ppt

文件：

- `mobiclaw/tools/office.py`
- `mobiclaw/tools/ppt.py`

能力：

- DOCX / XLSX / PDF 读写
- PPTX 创建、编辑、插图、样式处理

这些工具已经进入 Worker 主链路能力集，不应再被视为“未接入实验工具”。

### 6.5 skill_runner

文件：

- `mobiclaw/tools/skill_runner.py`

作用：

- 执行 skill 中声明的脚本
- 配合 Skill Selector 成为 Worker 的增强层
- 运行时会读取 `SKILL.md` 并约束白名单命令

---

## 7. Memory / Feishu / Schedule 工具族

### 7.1 Memory

文件：

- `mobiclaw/tools/memory/long_term_memory.py`
- `mobiclaw/tools/memory/rag.py`

负责：

- 长期记忆读写
- 历史任务检索
- Steward 知识存储与检索

### 7.2 Feishu

文件：

- `mobiclaw/tools/feishu.py`

能力：

- `fetch_feishu_chat_history`
- `get_feishu_message`

### 7.3 Schedule

文件：

- `mobiclaw/tools/schedule.py`

能力：

- `list_scheduled_tasks`
- `create_scheduled_task`
- `cancel_scheduled_task`

这组工具通过 `mobiclaw.scheduler.ScheduleManager` 接入 APScheduler + JSON store。

---

## 8. Worker 实际工具能力补充

除了旧文档常见的 web / papers / office / shell / memory 之外，当前 Worker 还已接入：

- 飞书相关工具
- 定时任务工具
- skill 运行时脚本执行

因此若文档仍只描述“研究、网页、文件”能力，已经低估了当前 Worker 的实际职责边界。

---

## 9. 调试与排障

- Mobi 无结果：检查 `MOBILE_PROVIDER`、device 配置、provider 参数与输出目录
- Web 无结果：检查 Brave Key 与网络访问
- Shell 被拒：检查 allowlist 与危险 token
- Skill 脚本被拒：检查 `execution_dir`、`SKILL.md` 白名单与超时限制
- 定时任务不可用：检查 `SENESCHAL_SCHEDULE_ENABLED` 与 scheduler store path
- 文件未落盘：检查写入根目录限制
- OCR 异常：检查依赖和图片路径

---

## 8. 当前结论

而是：

> **Mobi + Local Tools + Local Memory**
