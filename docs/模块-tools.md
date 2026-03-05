# 模块文档：seneschal/tools 工具层

本文档说明工具层的能力分类、返回契约、容错策略和扩展实践。

---

## 1. 模块范围

- 聚合与高阶封装
  - `seneschal/tools/__init__.py`
- Mobi 工具
  - `seneschal/tools/mobi.py`
- WeKnora API 客户端
  - `seneschal/tools/weknora.py`
  - `seneschal/tools/weknora/*`
- 联网与抓取
  - `seneschal/tools/web.py`
- 论文工具
  - `seneschal/tools/papers.py`
- 本地工具
  - `seneschal/tools/shell.py`
  - `seneschal/tools/file.py`

---

## 2. 统一返回契约

大部分工具函数返回 `ToolResponse`：

- `content`: 文本块列表（可直接给 Agent 作为上下文）
- `metadata`: 结构化字段（建议用于程序判断/复盘）

开发建议：

- `content` 面向“可读性”
- `metadata` 面向“可判断性”
- 错误要同时放入可读信息与结构化标记

---

## 3. Mobi 工具族（`mobi.py`）

## 3.1 核心能力

- `call_mobi_collect(task_desc, timeout=...)`
- `call_mobi_collect_verified(...)`
- `call_mobi_action(action_type, payload, ...)`

## 3.2 行为特点

- 通过 HTTP 请求 `mobiagent_server`
- 对 collect/action 提供统一封装
- 网关失败时可退回 mock（用于开发联调）

## 3.3 返回值关注字段

实践中可重点看：

- `success`
- `run_dir` / `index_file`
- `screenshot_path`
- `ocr_text`
- `status_hint`
- `action_count` / `step_count`

---

## 4. WeKnora 工具族（`__init__.py` + `weknora.py`）

## 4.1 常用高阶工具

- `weknora_add_knowledge(content, title, metadata)`
- `weknora_rag_chat(query, ...)`
- `weknora_knowledge_search(query, ...)`
- `weknora_list_knowledge_bases()`

## 4.2 缓存设计

缓存文件：`seneschal/tools/weknora_cache.json`

缓存对象：

- knowledge base name -> id/info
- agent name -> id
- session_id

目的：

- 减少 list API 次数
- 稳定名称解析
- 避免重复创建 session

## 4.3 自动解析与回退

高阶封装会自动做：

- `kb_name` -> `kb_id` 解析
- `agent_name` -> `agent_id` 解析
- 会话不存在时创建 session 并重试

当远端异常时会保留错误信息，方便 Agent 自主恢复策略（重试、降级、提示用户）。

---

## 5. Web 工具族（`web.py`）

## 5.1 能力

- `brave_search`：获取候选来源
- `fetch_url_text`：抓原始文本
- `fetch_url_readable_text`：抓可读正文
- `fetch_url_links`：提取页面链接

## 5.2 推荐使用顺序

1. `brave_search` 初筛来源
2. `fetch_url_readable_text` 抓正文
3. `fetch_url_links` 扩展来源链
4. 需要原始结构时再用 `fetch_url_text`

---

## 6. Papers 工具族（`papers.py`）

## 6.1 能力

- `arxiv_search`
- `dblp_conference_search`
- `download_file`
- `extract_pdf_text`

## 6.2 典型工作流

1. 先 `dblp_conference_search` 找会议论文列表
2. 再 `arxiv_search` 找对应 preprint/PDF
3. `download_file` 下载 PDF
4. `extract_pdf_text` 抽取内容并总结

---

## 7. 本地工具（安全约束）

## 7.1 shell 工具

`run_shell_command` 具备安全限制：

- 白名单命令（`SENESCHAL_SHELL_ALLOWLIST`）
- 禁止危险 token（如 `|`, `;`, `&&`, 重定向等）

适合：

- 快速读取文件、查看时间、目录检查

不适合：

- 多命令串联、复杂脚本执行

## 7.2 file 工具

`write_text_file` 用于将 Agent 结果落盘。建议：

- 明确输出路径
- 明确覆盖/追加策略
- 结果中带上写入路径回执

---

## 8. 工具扩展指南

## 8.1 新增工具的最小步骤

1. 在对应文件实现 async 函数，返回 `ToolResponse`
2. 在 `seneschal/tools/__init__.py` 导出
3. 在 `agents.py` 注册到 Steward 或 Worker
4. 为工具写 `func_description`（给 LLM 的“使用说明”）
5. 在 docs 补充工具用途与示例

## 8.2 `func_description` 编写建议

描述里至少包含：

- 输入格式
- 输出关键字段
- 失败时含义
- 适用场景与禁用场景

这样可显著降低 Agent 误用概率。

## 8.3 错误处理建议

- HTTP 错误：保留状态码与错误体摘要
- 解析错误：返回原始片段 + parse_error 字段
- 外部依赖不可用：明确“可重试/不可重试”

---

## 9. 调试与排障

- 工具返回为空：检查 `ToolResponse.content` 是否构造
- metadata 缺字段：检查下游 API 原始返回
- WeKnora 解析失败：删除本地 cache 后重试
- Shell 被拒绝：确认命令是否在 allowlist 且不含危险 token

---

## 10. 与 Agent 的协作约定

- Steward 关注“任务闭环”和“证据充分性”
- Worker 关注“检索质量”和“结果交付”
- 工具层应尽量无业务判断，让 Agent 决策层保留灵活性
