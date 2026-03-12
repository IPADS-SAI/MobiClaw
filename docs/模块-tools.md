# 模块文档：seneschal/tools 工具层（按当前实际代码口径）

本文档说明当前工具层的能力分类与主链路接入情况。

---

## 1. 模块范围

当前工具层可按 4 类理解：

### 1.1 当前主链路工具

- `seneschal/tools/mobi.py`
- `seneschal/tools/web.py`
- `seneschal/tools/papers.py`
- `seneschal/tools/shell.py`
- `seneschal/tools/file.py`
- `seneschal/tools/ocr.py`
- `seneschal/tools/office.py`
- `seneschal/tools/ppt.py`
- `seneschal/tools/memory.py`
- `seneschal/tools/skill_runner.py`

### 1.2 聚合导出层

- `seneschal/tools/__init__.py`
- `seneschal/tools.py`


---

## 2. 当前工具层的真实定位

当前工具层的主轴：

1. **Mobi 工具**：手机执行边界
2. **Web / Papers 工具**：联网信息获取
3. **Local 工具**：shell / ocr / file / office / ppt / skill
4. **Memory 工具**：本地长期记忆、本地知识、任务历史

也就是说，当前工具层的主轴是：

> **Mobi + Local Tools + Local Memory**

---

## 3. 统一返回契约

大部分工具函数返回 `ToolResponse`：

- `content`：给 Agent 看的人类可读内容
- `metadata`：给程序判断的结构化字段

当前推荐约定：

- `content` 强调可读摘要
- `metadata` 强调可判断性和复盘能力
- 错误同时保存在文本与结构化字段里

---

## 4. Mobi 工具族

文件：

- `seneschal/tools/mobi.py`

核心能力：

- `call_mobi_collect`
- `call_mobi_collect_verified`
- `call_mobi_action`

当前现实作用：

- 是 Steward 主链路最关键的工具边界
- 负责把 collect/action 请求转给 `mobiagent_server`
- 负责把执行结果封成统一 `ToolResponse`

实践里重点关注的 metadata：

- `success`
- `run_dir`
- `index_file`
- `screenshot_path`
- `ocr_text`
- `status_hint`
- `step_count`
- `action_count`
- `last_reasoning`

---

## 5. Web / Papers 工具族

### 5.1 Web 工具

文件：

- `seneschal/tools/web.py`

能力：

- `brave_search`
- `fetch_url_text`
- `fetch_url_readable_text`
- `fetch_url_links`

推荐顺序：

1. `brave_search`
2. `fetch_url_readable_text`
3. `fetch_url_links`
4. 必要时再看 `fetch_url_text`

### 5.2 Papers 工具

文件：

- `seneschal/tools/papers.py`

能力：

- `arxiv_search`
- `dblp_conference_search`
- `download_file`
- `extract_pdf_text`

当前它们是 Worker 主链路的重要组成部分。

---

## 6. Local 工具族

### 6.1 shell

文件：

- `seneschal/tools/shell.py`

特性：

- 白名单约束
- 禁止危险 token

适合：

- 轻量读取
- 简单环境查询
- 快速辅助命令

### 6.2 file

文件：

- `seneschal/tools/file.py`

能力：

- 文本落盘

### 6.3 ocr

文件：

- `seneschal/tools/ocr.py`

能力：

- 本地图片 OCR

### 6.4 office / ppt

文件：

- `seneschal/tools/office.py`
- `seneschal/tools/ppt.py`

能力：

- DOCX / XLSX / PDF 读写
- PPTX 创建、编辑、插图、样式处理

这些工具当前已经进入 Worker 主链路能力集，不应再被视为“未接入实验工具”。

### 6.5 skill_runner

文件：

- `seneschal/tools/skill_runner.py`

作用：

- 执行 skill 中声明的脚本
- 配合 Skill Selector 成为 Worker/Steward 的增强层

---

## 7. Memory 工具族

文件：

- `seneschal/tools/memory.py`

当前这是工具层里非常关键但旧文档经常低估的一块。

它负责的不是外部知识库，而是本地状态能力：

- 长期记忆
- 本地任务历史
- steward knowledge

这也解释了为什么当前系统的 Store / Analyze 更偏向本地状态。

---


## 9. 工具扩展建议

### 9.1 新增主链路工具

步骤：

1. 在对应文件实现函数并返回 `ToolResponse`
2. 在聚合层导出
3. 在 `agents.py` 注册到 Worker 或 Steward
4. 补 `func_description`
5. 补文档和测试


---

## 10. 调试与排障

- Mobi 无结果：检查 `mobiagent_server` 状态、认证和 mode
- Web 无结果：检查搜索 Key 与网络访问
- Shell 被拒：检查 allowlist 与危险 token
- 文件未落盘：检查写入根目录限制
- OCR 异常：检查依赖和图片路径

---

## 11. 当前结论

当前工具层的正确理解不是：

> **Mobi + Local Tools + Local Memory**
