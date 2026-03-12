# Seneschal Memory 模块详解

本文面向当前仓库代码，详细说明 Seneschal 中“Memory”相关实现的真实结构、各 Agent 的使用方式、不同运行模式下的行为，以及当前实现的边界和注意事项。

需要先说明一点：当前项目里的 “Memory” 不是单一模块，而是四类能力的组合：

1. Agent 运行时短期记忆：`InMemoryMemory`
2. 会话状态持久化：`GenericSessionManager + JSONSession`
3. 长期记忆文件：`MEMORY.md`
4. 本地 RAG 记忆：任务历史库 + Steward 知识库

这四类能力职责不同，不能混为一谈。

---

## 1. 总览

### 1.1 Memory 分层

| 层级 | 主要实现 | 是否持久化 | 典型用途 |
| --- | --- | --- | --- |
| 运行时短期记忆 | `agentscope.memory.InMemoryMemory` | 仅在会话状态被保存时可间接持久化 | 让 Agent 在一次会话/任务执行中记住上下文 |
| 会话状态记忆 | `seneschal/session/manager.py` + `agentscope.session.JSONSession` | 是 | 跨请求恢复 Agent state、memory、plan_notebook |
| 长期记忆文件 | `seneschal/tools/memory/long_term_memory.py` | 是 | 保存用户偏好、事实信息、长期提示 |
| 本地 RAG 记忆 | `seneschal/tools/memory/rag.py` | 是 | 检索历史任务、检索 Steward 存储的知识片段 |

### 1.2 相关代码位置

- `seneschal/config.py`
- `seneschal/agents.py`
- `seneschal/workflows.py`
- `seneschal/orchestrator.py`
- `seneschal/session/manager.py`
- `seneschal/tools/memory/long_term_memory.py`
- `seneschal/tools/memory/rag.py`
- `seneschal/run_context.py`

### 1.3 相关配置项

#### 长期记忆

- `SENESCHAL_MEMORY_ENABLED`
- `SENESCHAL_MEMORY_FILE`

#### 本地 RAG

- `SENESCHAL_RAG_ENABLED`
- `SENESCHAL_RAG_STORE_PATH`
- `SENESCHAL_RAG_COLLECTION`
- `SENESCHAL_RAG_EMBEDDING_MODEL`
- `SENESCHAL_RAG_EMBEDDING_DIMENSIONS`
- `SENESCHAL_RAG_CHUNK_SIZE`
- `SENESCHAL_RAG_INDEX_FILE_CONTENT`

#### 会话目录

- 环境变量中`SENESCHAL_SESSION_ROOT`指定，默认为项目目录下的 `.mobiclaw/session` 目录。

---

## 2. 运行时短期记忆：`InMemoryMemory`

### 2.1 当前哪些 Agent 使用了它

当前代码里，以下 Agent 在创建时显式挂载了 `InMemoryMemory()`：

- `create_worker_agent()`
- `create_steward_agent()`
- `create_chat_agent()`

以下 Agent 没有启用运行时 memory：

- `create_router_agent()`
- `create_planner_agent()`
- `create_skill_selector_agent()`

### 2.2 作用

`InMemoryMemory` 是 AgentScope 里的运行时记忆容器，主要作用是：

- 保存当前 Agent 在一轮或多轮交互中的消息上下文
- 让 Agent 在同一会话内继续引用前文
- 在调用 `agent.state_dict()` / `agent.load_state_dict()` 时，作为 Agent state 的一部分被序列化和恢复

### 2.3 它本身不是长期存储

单独看 `InMemoryMemory`，它只是进程内对象：

- 进程退出会消失
- 重新创建 Agent 会丢失

它只有在“会话状态持久化”链路生效时，才会被保存下来并在下次恢复。

---

## 3. 会话状态持久化：`GenericSessionManager + JSONSession`

这是当前项目里最容易和“知识记忆”混淆的一层，但实际上它更接近“Agent 状态恢复”。

### 3.1 主要实现

核心实现位于：

- [`seneschal/session/manager.py`](../seneschal/session/manager.py)

当前实际职责包括：

- 解析/创建 session 目录
- 维护 `latest_session.json`
- 保存 `meta.json`
- 追加 `history.jsonl`
- 通过 `JSONSession` 保存/恢复 Agent state
- 支持中断活跃回复

### 3.2 默认目录

如果未设置 `SENESCHAL_SESSION_ROOT`，默认目录为：

```text
<repo-root>/.mobiclaw/session
```

### 3.3 每个 session 目录内的主要文件

当前目录结构大致如下：

```text
.mobiclaw/session/
├── latest_session.json
└── 20260312_101500_123456-chat_20260312101500123_ctx-a/
    ├── meta.json
    ├── history.jsonl
    ├── chat_20260312101500123_ctx-a.json
    ├── router_20260312101600123_ctx-a__worker.json
    └── router_20260312101600123_ctx-a__steward.json
```

说明：

- `meta.json`：会话元信息、storage_session_ids、agent_state_keys 等
- `history.jsonl`：追加写入的消息历史
- `*.json`：由 `JSONSession.save_session_state()` 保存的 Agent 状态文件

### 3.4 会保存什么

在 `load_agent_state()` / `save_agent_state()` 的注释和实现里，当前项目明确依赖 `agent.load_state_dict(..., strict=False)` 做统一恢复。

也就是说，当前会被尝试持久化和恢复的内容包括：

- `memory`
- `toolkit`
- `plan_notebook`
- 其他 Agent state 中可序列化的字段

### 3.5 哪些模式会用到这层

#### Gateway Chat

`workflows._run_gateway_chat_task()` 会：

1. `resolve_session()`
2. `load_agent_state()`
3. 执行当前一轮 `agent(msg)`
4. `append_turn_history()`
5. `save_agent_state()`

因此 chat 模式是当前会话记忆最完整的一条链路。

#### Orchestrated Task / Agent Task

`orchestrator.run_orchestrated_task()` 也会：

1. 通过 `_GENERIC_SESSION_MANAGER.resolve_session()` 获取/创建上下文
2. 记录用户输入到 `history.jsonl`
3. 对每个 subtask 执行 `_run_one_agent()`
4. 在 `_run_one_agent()` 内对具体 Agent 做 `load_agent_state()` / `save_agent_state()`
5. 把每个 subtask 的回复写入 `history.jsonl`

因此：

- 同一个 `context_id` 下，Worker / Steward 的状态是可以跨请求恢复的
- 且是“按 mode + agent_key”分别存储

#### Interactive / Demo

这两种模式当前不会经过 `GenericSessionManager`。

意味着：

- `InMemoryMemory` 只在当前进程里有效
- 程序退出后不会自动恢复

### 3.6 当前实现的几个关键特点

#### 1) `history.jsonl` 记录的是消息，不是完整状态

`history.jsonl` 更适合做审计和排查，不能替代 `JSONSession` 状态文件。

#### 2) Orchestrator 记录的是子任务回复，不是最终聚合回复

当前 `orchestrator.run_orchestrated_task()` 会把每个 subtask 的 reply 追加到 history，但不会额外把最终聚合后的总回复再写一条 assistant message。

这意味着：

- `history.jsonl` 更接近“执行轨迹”
- 不是完整的最终答复视图

#### 3) `latest_session.json` 是共享的

当前 `resolve_session(context_id=None, mode=...)` 会优先读 `latest_session.json`，而不是先按 mode 隔离。

这意味着在没有显式传入 `context_id` 时：

- chat 模式可能续到最近一次 orchestrated task 的 session
- orchestrated task 也可能续到上一次 chat session

这是当前实现里需要特别注意的行为。

#### 4) 配置名带有 `CHAT`，但并非只给 chat 用

`SENESCHAL_SESSION_ROOT` 实际上被通用会话管理器用于：

- chat
- router
- worker
- steward
- 其他 orchestrated mode

命名和真实职责并不完全一致。

---

## 4. 长期记忆文件：`MEMORY.md`

### 4.1 主要实现

文件：

- `seneschal/tools/memory/long_term_memory.py`

接口：

- `read_memory()`
- `update_long_term_memory(content: str)`

### 4.2 存储位置

由 `MEMORY_CONFIG["file_path"]` 决定，对应环境变量：

- `SENESCHAL_MEMORY_FILE`

默认值：

```text
~/.seneschal/MEMORY.md
```

### 4.3 读取与写入方式

#### `read_memory()`

- 直接读取整个文件全文
- 文件不存在返回空字符串

#### `update_long_term_memory(content)`

- 直接覆盖整个文件
- 不做 append
- 不做 merge
- 自动创建父目录

这说明长期记忆工具本质上是“整文件覆盖写入”。

### 4.4 当前在哪些 Agent 中生效

#### Worker

当 `SENESCHAL_MEMORY_ENABLED=1` 时：

- `create_worker_agent()` 会注册 `update_long_term_memory`
- 同时 `_build_memory_prompt()` 会把当前 `MEMORY.md` 内容拼进系统提示词

因此 Worker 具备：

- 读取长期记忆快照（通过 prompt 注入）
- 写回长期记忆文件（通过 tool）

#### Steward

Steward 会调用 `_build_memory_prompt()`，因此也会把长期记忆文本注入到 sys_prompt。

但当前 Steward 没有注册 `update_long_term_memory` 工具。

也就是说：

- Steward 能“看到”长期记忆
- 但不能直接写长期记忆文件

#### Chat Agent

当前 `create_chat_agent()` 没有调用 `_build_memory_prompt()`，也没有注册 `update_long_term_memory`。

因此：

- chat 模式默认不使用 `MEMORY.md`
- chat 的上下文延续主要靠 session state，而不是长期记忆文件

### 4.5 一个非常重要的行为细节

`_build_memory_prompt()` 是在 Agent 创建时读取 `MEMORY.md` 并拼入 sys_prompt 的。

这意味着：

- 长期记忆是“创建时快照”
- 不是运行时实时读取

因此如果当前 Worker 在一个任务中调用 `update_long_term_memory()`：

- 文件会被更新
- 但当前这个 Agent 的 sys_prompt 不会自动刷新
- 新内容通常要等下一次重新创建 Agent 时才会自然进入提示词

这点在 Interactive 模式中尤其需要注意，因为 Interactive 模式会复用同一个 `Steward` 实例。

---

## 5. 本地 RAG 记忆：任务历史库 + Steward 知识库

### 5.1 主要实现

文件：

- `seneschal/tools/memory/rag.py`

当前内部维护了两个 singleton：

- `_task_history_kb`
- `_knowledge_kb`

底层使用：

- `QdrantStore`
- `SimpleKnowledge`
- `OpenAITextEmbedding`

### 5.2 默认存储路径和集合名

路径由 `SENESCHAL_RAG_STORE_PATH` 决定，默认：

```text
~/.seneschal/rag_store
```

集合名基于 `SENESCHAL_RAG_COLLECTION` 派生：

- `<collection>_history`
- `<collection>_knowledge`

### 5.3 两类 RAG 记忆的职责

#### 任务历史库：`task_history`

系统写入，Agent 只读。

接口：

- `store_task_result(...)`
- `search_task_history(query, limit=5)`

用途：

- 保存已完成任务的摘要
- 允许 Worker 回答“之前做过什么任务”

#### Steward 知识库：`knowledge`

Agent 可写可读。

接口：

- `store_steward_knowledge(content, title="")`
- `search_steward_knowledge(query, limit=5)`

用途：

- 保存手机收集得到的 OCR 文本、对话记录、账单信息、活动信息等
- 供 Steward / Worker 后续再检索分析

### 5.4 任务历史是如何写入的

`gateway_server._run_job()` 在异步任务成功完成后，会检查：

```python
if RAG_CONFIG["task_history_enabled"]:
    await store_task_result(...)
```

因此当前“自动写任务历史”的触发条件是：

- 走 Gateway 异步任务
- 任务成功完成
- `SENESCHAL_RAG_ENABLED=1`

这意味着以下路径不会自动写入 task history：

- `python app.py` demo
- `python app.py --interactive`
- CLI 直跑的本地 demo / interactive
- Daily 的 collect 路径

### 5.5 `store_task_result()` 存了什么

它会写入一段摘要文本，包含：

- `job_id`
- `timestamp`
- `task`
- `reply`
- `files`

如果开启：

- `SENESCHAL_RAG_INDEX_FILE_CONTENT=1`

则它还会继续读取产出文件内容并索引，目前支持：

- `.txt`
- `.md`
- `.csv`
- `.json`
- `.pdf`
- `.docx`
- `.xlsx`

### 5.6 `store_steward_knowledge()` 存了什么

它会把文本包装为：

```text
[Knowledge Record]
title: ...
timestamp: ...

<content>
```

然后按 `chunk_size` 切分后写入本地知识库。

### 5.7 当前哪些 Agent 能用这些工具

#### Worker

- 始终可用：`search_steward_knowledge`
- 条件可用：`search_task_history`，前提是 `SENESCHAL_RAG_ENABLED=1`

Worker 当前不能调用 `store_steward_knowledge`。

#### Steward

- 可用：`store_steward_knowledge`
- 可用：`search_steward_knowledge`

Steward 当前不能调用 `search_task_history`。

#### Chat Agent

当前 chat agent 不接这组工具。

### 5.8 当前 RAG 结果的表现形式

`search_task_history()` / `search_steward_knowledge()` 当前都会返回：

- 一段拼接好的纯文本结果
- 每条结果附带 score

但不会返回高度结构化的业务对象。

这意味着：

- 去重、总结、归因仍然交给上层 Agent 自己完成
- 检索命中质量高度依赖 embedding、chunk size 和 query 写法

---

## 6. 各 Agent 当前对 Memory 的使用情况

## 6.1 Worker

Worker 是当前 Memory 能力最完整的 Agent。

### 具备的 Memory 能力

- `InMemoryMemory`
- 可选的长期记忆 prompt 注入
- 可选的 `update_long_term_memory`
- 可选的 `search_task_history`
- 固定可用的 `search_steward_knowledge`
- 在 orchestrator 中可被 session manager 持久化

### 典型用途

- 跨请求延续工具调用上下文
- 查询之前做过的任务
- 查询 Steward 存入的知识
- 记住用户偏好、输出风格、长期事实

### 注意

- 长期记忆写入是整文件覆盖
- 任务历史默认不是所有模式都自动写

## 6.2 Steward

### 具备的 Memory 能力

- `InMemoryMemory`
- 可选的长期记忆 prompt 注入
- `store_steward_knowledge`
- `search_steward_knowledge`
- 在 orchestrator 中可被 session manager 持久化

### 典型用途

- 采集手机内容后存入知识库
- 结合历史知识做总结、提醒、待办提取

### 注意

- Steward 当前不能直接更新 `MEMORY.md`
- 它存的是本地知识库，不是 task history

## 6.3 Chat Agent

### 具备的 Memory 能力

- `InMemoryMemory`
- `PlanNotebook`
- 通过 session manager 做状态保存/恢复

### 不具备的能力

- 不读长期记忆文件
- 不写长期记忆文件
- 不连本地 RAG 记忆工具

### 典型用途

- 多轮问答
- 会话内延续上下文
- 结合 planner_monitor 输出当前计划状态

---

## 7. 不同运行模式中的 Memory 行为

## 7.1 Demo

- 会创建一个新的 `Steward`
- 使用 `InMemoryMemory`
- 不走 session manager
- 退出即丢失

## 7.2 Interactive

- 整个进程中复用同一个 `Steward`
- memory 仅存在于当前进程
- 没有自动 state 持久化

## 7.3 Agent Task / Orchestrator

- 走 `GenericSessionManager`
- 若给定 `context_id`，可跨请求恢复对应 agent state
- subtask 的执行轨迹会进入 `history.jsonl`
- 同一 context 下，Worker / Steward 各自有独立 state key

## 7.4 Gateway Chat

- 是当前 Memory 使用最完整的模式
- 每轮都会保存和恢复 Agent state
- 支持 `/new`、`/interrupt`、`/exit`
- `history.jsonl` 保存用户与 assistant 的轮次消息

## 7.5 Daily

Daily 需要单独理解：

- `collect` 路径走的是 `call_mobi_collect()` + `weknora_add_knowledge()`
- 不是本地 RAG `store_steward_knowledge()`
- 当本轮存在 collect 结果时，会调用 `weknora_rag_chat()`

因此当前 Daily 的“长期数据沉淀”主路径更偏向 WeKnora，不是本地 memory 模块。

---

## 8. 当前实现的边界与注意事项

## 8.1 `update_long_term_memory()` 是覆盖写，不是增量写

这点非常关键。

正确使用方式应当是：

1. 先读当前记忆内容
2. 合并新信息
3. 再整体写回

否则非常容易把旧记忆覆盖掉。

## 8.2 长期记忆注入是创建时快照

`MEMORY.md` 的内容是在 Agent 创建时读入 sys_prompt 的，不是实时动态读取。

因此：

- 新写入的长期记忆不会自动影响当前 Agent 的已有 sys_prompt
- 更适合下一次新建 Agent 时生效

## 8.3 会话状态和业务知识不是一回事

需要明确区分：

- session state：恢复的是 Agent 运行状态
- RAG knowledge：恢复的是知识片段
- MEMORY.md：恢复的是长期偏好/事实文本

三者作用完全不同。

## 8.4 `SENESCHAL_SESSION_ROOT` 会被多模式共用

名称容易让人误以为只用于 chat。

实际上当前 orchestrator 也使用同一套目录。

## 8.5 `latest_session.json` 可能跨模式串上下文

如果调用时不显式传 `context_id`，当前实现可能续上一次其他模式留下的 latest session。

对于生产级接入，建议：

- 显式传 `context_id`
- 或在不同业务模式下使用不同 session root

## 8.6 task history 只覆盖部分路径

当前 task history 的自动写入主要挂在 Gateway 异步任务成功回调后。

这意味着它不是“全项目统一任务历史”。

如果要把它当成可靠历史来源，需要补齐：

- CLI 路径
- Daily 路径
- 同步 Gateway 路径

## 8.7 当前 Memory 没有多租户隔离

无论是：

- `MEMORY.md`
- 本地 Qdrant store
- session 目录

当前都默认是单机单空间共享。

因此：

- 不适合直接用于严格多用户隔离场景
- 需要自行做用户维度 namespace 或目录隔离

## 8.8 当前没有复杂冲突治理

例如：

- 同一偏好的多次写入冲突
- 同一知识的重复入库
- 过期知识清理

当前都没有系统级治理机制，主要依赖 Agent 提示词和调用约束。

---

## 9. 推荐理解方式

如果只想快速把当前仓库中的 Memory 看清楚，建议按下面的方式理解：

### 第一层：会话记忆

由 `InMemoryMemory + JSONSession + GenericSessionManager` 组成。

关注点是：

- 这轮任务之前发生过什么
- 当前 Agent state 能否恢复

### 第二层：长期偏好记忆

由 `MEMORY.md` 组成。

关注点是：

- 用户偏好
- 固定事实
- 回答风格

### 第三层：检索型知识记忆

由本地 RAG 组成。

关注点是：

- 之前做过哪些任务
- 之前从手机里收集过哪些知识

### 第四层：运行日志

由 `RunContext` 与 `history.jsonl` 组成。

关注点是：

- 发生了哪些步骤
- 方便追踪和排查

---

## 10. 结论

当前 Seneschal 的 Memory 体系已经不是单一“聊天记忆”，而是一个组合方案：

- Agent 运行态上下文由 `InMemoryMemory` 承担
- 跨请求恢复由 `GenericSessionManager + JSONSession` 承担
- 跨会话偏好保存由 `MEMORY.md` 承担
- 检索式历史与知识复用由本地 RAG 承担

它已经足够支撑：

- chat 多轮续接
- orchestrator 子任务跨请求续跑
- 用户偏好长期保存
- 历史任务和已采集知识检索

但当前仍然存在几个工程边界：

- 长期记忆是整文件覆盖
- 自动任务历史写入覆盖面不完整
- latest session 存在跨模式续接风险
- chat / worker / steward 对 Memory 的能力不对称
- Daily 主链路仍偏 WeKnora，不完全走本地 memory

如果后续要把它演进成稳定的个人助理 Memory 体系，建议优先补齐：

1. 统一各模式的 task history 落库
2. 给长期记忆增加 append/merge/schema 机制
3. 为 session 和 memory 增加用户级隔离
4. 明确区分会话状态、长期偏好、知识库和审计日志四类数据
