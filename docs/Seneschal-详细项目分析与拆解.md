# Seneschal 详细项目分析与拆解（按当前实际代码口径）

## 1. 先说结论

当前项目的真实运行核心是：

- `app.py` / `gateway_server.py` 作为入口
- `workflows.py` 作为统一模式分发层
- `orchestrator.py` 作为 `route -> plan -> execute` 主编排层
- `agents.py` 中的 Chat / Worker / Steward / Router / Planner / Skill Selector
- `mobiagent_server/server.py` 作为手机执行侧稳定边界
- 本地 session、outputs、RunContext、memory 作为主要持久化层

因此当前最准确的架构描述是：

> **Seneschal 是一套以 Gateway/Chat 为入口、以 Orchestrator + Agents 为核心、以 MobiAgent 和本地工具/本地状态为基础的多 Agent 编排系统。**


---


## 3. 顶层结构重新理解

当前顶层应按下面这 5 层来理解，而不是旧的“手机 + 知识库 + 编排”三角结构。

### 3.1 入口层

文件：

- `app.py`
- `seneschal/gateway_server.py`
- `seneschal/webui/*`

职责：

- 接收任务
- 加载环境变量
- 暴露 FastAPI 与 Web UI
- 进入工作流层

### 3.2 工作流层

文件：

- `seneschal/workflows.py`

职责：

- 管理 CLI 各模式分发
- 管理 gateway chat 模式
- 管理 chat command
- 管理会话恢复/保存
- 管理 planner_monitor 事件输出

### 3.3 编排层

文件：

- `seneschal/orchestrator.py`
- `seneschal/agents.py`

职责：

- 路由选 Agent
- 规划子任务
- 选择 skill
- 执行子任务
- 汇总 reply / file / routing_trace

### 3.4 工具与边界层

文件：

- `seneschal/tools/*`
- `mobiagent_server/server.py`

职责：

- 调用手机执行侧
- 联网检索
- 文档处理
- OCR / shell / 文件写入
- 执行 skill
- 访问本地 memory / task history / steward knowledge

### 3.5 状态与持久化层

主要内容：

- chat session 目录
- outputs 目录
- RunContext JSONL
- 本地 memory
- task history / steward knowledge

---

## 4. 入口与运行模式

### 4.1 `app.py`

`app.py` 很薄，实际只做两件事：

1. 调 `load_project_env()` 加载根目录 `.env`
2. 进入 `seneschal.workflows.main()`

这说明：

- 真正的入口控制中心不在 `app.py`
- 而在 `workflows.py`

### 4.2 `workflows.py`

当前统一承接这些入口模式：

- Demo
- Interactive
- Agent Task
- Daily
- Gateway Chat

其中最重要的变化是：

- `--agent-task` 不再是“默认只走 Worker”
- 现在默认会进入 `run_orchestrated_task()`
- 即统一走多 Agent 编排主链路

### 4.3 Chat 模式是完整产品链路

当前 chat 模式已经具备：

- `context_id` 绑定
- 最近会话恢复
- `/new`
- `/interrupt`
- `/exit`
- 首轮欢迎语
- planner monitor 回传
- session 持久化

所以 chat 模式已经不是 demo 辅助功能，而是主入口之一。

---

## 5. 编排主干：Orchestrator

### 5.1 核心函数

主入口是：

- `seneschal/orchestrator.py: run_orchestrated_task()`

它的标准流程是：

1. 接收任务与 mode / hint / routing strategy
2. 进行 route
3. 需要时进行 plan
4. 为每个子任务选 skill
5. 顺序执行每个子任务
6. 聚合 reply / files / routing_trace

### 5.2 Route

优先级大致是：

1. `agent_hint` 强制指定
2. legacy mode 强制指定
3. LLM route
4. route 超时回退
5. route 出错时规则回退

Router 输出的是：

- `target_agents`
- `reason`
- `confidence`
- `plan_required`
- `strategy`

### 5.3 Plan

当任务复杂或涉及多个 Agent 时：

- Planner 会生成 `stages[][]`
- 外层是串行阶段
- 内层是同阶段子任务

如果 planner 超时或报错：

- 会走 fallback plan

### 5.4 Execute

当前执行模型是：

- 按 stage 顺序执行
- 每个 subtask 在执行前先做 skill 选择
- 执行时会把上游执行结果压成 prior context 传给下游
- 执行结束后回收文件路径与 reply

最终返回：

- `reply`
- `files`
- `routing_trace`

这说明当前 Orchestrator 已经是项目真正的“主干”。

---

## 6. Agent 体系重解

### 6.1 Chat Agent

作用：

- 默认 chat 入口
- 处理多轮会话
- 配合 `ChatSessionManager` 做持久化

### 6.2 Worker Agent

作用：

- 通用任务执行器
- 更像 research / processing / output agent

其主能力集中在：

- Brave / 网页阅读 / 链接抓取
- arXiv / DBLP
- 下载与 PDF 提取
- OCR
- shell
- 文本与 Office 文件处理
- skill 脚本执行
- 本地 memory / task history / steward knowledge

当前 Worker 的现实定位已经明显偏向：

> **通用工具执行器**

而不是旧文档里“知识库问答代理”。

### 6.3 Steward Agent

作用：

- 处理手机相关闭环任务
- 把手机执行证据转成最终结论

当前最重要的设计点是：

- 不直接信任工具的 success flag
- 结合截图、OCR、actions、reasoning、step_count 等证据做判断
- 支持重试报告
- 支持 VLM 完成度判断

Steward 当前更像：

> **面向手机场景的执行审查型 Agent**

### 6.4 Router / Planner / Skill Selector

这三者的存在说明：

- 系统已经不是“一个大 Agent 装很多工具”
- 而是逐步转向明确的多角色编排系统

这也是当前代码相比早期结构最实质性的进步。

---

## 7. MobiAgent 执行边界

### 7.1 为什么它是当前真实外部核心

`mobiagent_server/server.py` 提供的是当前最关键的外部执行边界：

- collect
- action
- job 状态
- 结果上传

它支持：

- `mock`
- `proxy`
- `task_queue`
- `cli`

这说明 MobiGateway 已经从“临时桥接脚本”进化成“稳定边界层”。

### 7.2 它真正返回的是什么

MobiGateway 返回的不是“任务已完成”的业务真值，而是：

- 截图
- XML / hierarchy
- OCR
- actions
- reasonings
- summary / status_hint

也就是：

> **execution evidence package**

因此系统的关键判断逻辑在 Steward，而不是在 MobiGateway 本身。

---

## 8. Gateway 的实际地位

### 8.1 当前已经是完整服务层

`seneschal/gateway_server.py` 当前已经不仅仅是转发器，而是：

- 任务 API
- 异步 job 管理
- 文件下载网关
- chat session 查询入口
- env 配置管理入口
- 飞书接入层
- 内置 Web UI 容器

### 8.2 它与主链路的关系

Gateway 的关系可以理解为：

- 接住外部请求
- 调 `run_gateway_task()`
- 对结果进行补充装饰
- 管理上下文、异步任务和文件暴露

这使得 Gateway 成为当前“产品化入口”的关键一环。

---

## 9. 当前真正的 Store / Analyze 在哪里

当前 Store / Analyze 的实际情况：

### 9.1 Store

当前主要落在：

- chat session 持久化
- `outputs/`
- `RunContext`
- 本地 memory / task history / steward knowledge

### 9.2 Analyze

当前主要落在：

- Agent 自己基于上下文推理
- Worker 使用联网工具分析
- Steward 基于执行证据判断
- VLM 对截图和 reasonings 做完成度校验

因此当前的 Store / Analyze 已经明显偏向：

> **本地状态 + Agent 推理 + 工具结果聚合**

而不是：

> **外部知识库中心化处理**

---

## 10. Daily 任务的真实状态

`seneschal/dailytasks/runner.py` 当前仍然是一个混合态模块。

一方面它仍可运行：

- 读取 `tasks.json`
- 选 `collect` / `agent_task`
- 记录 `RunContext`

> Daily 当前仍然存在，正在向本地状态 + 多 Agent 主链路对齐。

---

## 11. 当前文档与代码之间的主要错位

当前最大的错位点主要有 4 个：

### 11.1 还把 `--agent-task` 说成直接 Worker

当前默认已经是 orchestrator 主链路。

### 11.2 低估了 Gateway 和 Chat 的产品化程度

它们现在已经不是辅助模块，而是核心入口。

### 11.3 低估了 Orchestrator 的中心地位

当前系统的主干已经明显从“Steward 单 Agent 统领一切”转为：

- Workflows
- Orchestrator
- 多 Agent 分工

---

## 12. 现状优点

### 12.1 主干已经成型

当前至少这条线已经完整：

- 输入
- 路由
- 规划
- 执行
- 输出

### 12.2 手机能力很有辨识度

Steward + MobiAgent + evidence judging 这条线是项目最独特的地方。

### 12.3 本地状态层比旧设计更实用

相较于把一切都压到外部知识库，当前本地 session / outputs / memory 的做法更贴近真实工程落地。

### 12.4 Gateway 已经具备产品形态

FastAPI + Web UI + 文件下载 + session 查询 + env 管理，这已经是相对完整的一层。

---

## 13. 当前需要继续处理的问题

### 13.1 清理 legacy 口径

尤其是：

- 文档
- Daily
- Gateway 配置项
- tools 导出层

### 13.2 补充跨模块测试

当前已有部分测试，但系统最关键的跨模块链路仍值得补：

- Gateway -> workflows -> orchestrator
- orchestrator -> worker/steward
- steward -> mobi gateway

### 13.3 收敛配置

当前配置中混有：

- 现行配置
- 实验配置
- legacy 配置

后续适合做一次瘦身。

---

## 14. 最终判断

如果只看当前代码真实状态，而不看历史设计愿景，那么项目最准确的架构判断是：

> Seneschal 当前已经从“以知识库为中心的编排设想”，演化成了“以 Gateway/Chat + Orchestrator + MobiAgent + 本地状态为中心的多 Agent 执行系统”。

所以后续所有文档都应统一使用这个口径：

- 当前主架构：Gateway / Chat / Orchestrator / Agents / MobiAgent / Local State
- 当前主链路：route -> plan -> execute -> persist -> return
