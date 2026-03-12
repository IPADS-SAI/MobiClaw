# Seneschal 详细架构图（按当前实际代码口径）

## 1. 分层组件图

```mermaid
flowchart TB
    subgraph Entry[入口层]
      U[用户 / 外部调用方 / Cron]
      CLI[app.py]
      GW[seneschal.gateway_server]
      WEB[Gateway Web UI]
      U --> CLI
      U --> GW
      U --> WEB
      WEB --> GW
    end

    subgraph Workflow[工作流层]
      WF[workflows.py\nmode dispatch]
      CSM[ChatSessionManager\nsession restore/save]
      PM[planner monitor events]
      CLI --> WF
      GW --> WF
      WF --> CSM
      WF --> PM
    end

    subgraph Orchestrator[编排层]
      ORCH[orchestrator.py\nroute -> plan -> execute]
      ROUTER[Router Agent]
      PLAN[Planner Agent]
      SKILL[Skill Selector]
      CHAT[Chat Agent]
      WORKER[Worker Agent]
      STEWARD[Steward Agent]
      WF --> ORCH
      WF --> CHAT
      ORCH --> ROUTER
      ORCH --> PLAN
      ORCH --> SKILL
      ORCH --> WORKER
      ORCH --> STEWARD
    end

    subgraph Daily[Daily 层]
      DR[dailytasks/runner.py\nlegacy mixed path]
      TJ[tasks/tasks.json]
      RC[run_context.py\nrun_id + JSONL]
      WF --> DR
      DR --> TJ
      DR --> RC
    end

    subgraph Tools[工具层]
      MOBI[mobi.py]
      WEBT[web.py / papers.py]
      LOCAL[shell.py / file.py / ocr.py]
      OFFICE[office.py / ppt.py]
      MEM[memory.py]
      SKRUN[skill_runner.py]
      LEGACY[weknora/*\nlegacy compatibility]
      WORKER --> WEBT
      WORKER --> LOCAL
      WORKER --> OFFICE
      WORKER --> MEM
      WORKER --> SKRUN
      STEWARD --> MOBI
      DR --> MOBI
    end

    subgraph DeviceBoundary[MobiAgent 边界]
      MG[mobiagent_server/server.py\ncollect / action / jobs]
      MODE[mode: mock / proxy / task_queue / cli]
      ART[artifact indexing\nexecution_result + OCR + actions + reasoning]
      VLM[optional output_schema extract]
      MOBI --> MG
      MG --> MODE
      MODE --> ART
      ART --> VLM
    end

    subgraph RuntimeState[本地状态层]
      SESS[chat sessions]
      OUT[outputs/job_xxx]
      LOGS[seneschal/logs/*.jsonl]
      KNOW[local memory / steward knowledge / task history]
      CSM --> SESS
      CHAT --> SESS
      ORCH --> OUT
      RC --> LOGS
      MEM --> KNOW
    end

    subgraph External[外部信息源]
      SEARCH[Brave / Web]
      PAPER[arXiv / DBLP / PDF]
      PHONE[Mobile GUI Runtime]
      WEBT --> SEARCH
      WEBT --> PAPER
      MG --> PHONE
    end

    LEGACY -.not in current main path.- ORCH
```

## 2. Gateway / Chat 时序

```mermaid
sequenceDiagram
    participant User as User
    participant GW as gateway_server
    participant WF as workflows.py
    participant CSM as ChatSessionManager
    participant CA as Chat Agent
    participant PM as planner monitor
    participant FS as Local Session Store

    User->>GW: POST /api/v1/task (mode=chat)
    GW->>WF: run_gateway_task(...)
    WF->>CSM: resolve_session(context_id)
    CSM-->>WF: session handle
    WF->>CA: create_chat_agent()
    WF->>CSM: load_agent_state(...)
    WF->>PM: bootstrap planner state
    WF->>CA: agent(user message)
    CA-->>WF: reply / plan events
    WF->>PM: emit planner_monitor
    WF->>CSM: append_turn_history(...)
    WF->>CSM: save_agent_state(...)
    CSM->>FS: persist session files
    WF-->>GW: reply + session + planner_monitor
    GW-->>User: TaskResult
```

## 3. Orchestrator 主时序

```mermaid
sequenceDiagram
    participant Caller as Caller
    participant Orch as orchestrator.py
    participant Router as Router Agent
    participant Planner as Planner Agent
    participant Skill as Skill Selector
    participant Worker as Worker Agent
    participant Steward as Steward Agent
    participant Out as outputs + routing_trace

    Caller->>Orch: run_orchestrated_task(task, mode)
    Orch->>Router: route(task)
    Router-->>Orch: target_agents + plan_required

    alt 需要规划
        Orch->>Planner: plan(task)
        Planner-->>Orch: stages[][]
    else 直接执行
        Orch-->>Orch: single stage
    end

    loop each subtask
        Orch->>Skill: select_skills(subtask)
        Skill-->>Orch: selected_skills
        alt worker
            Orch->>Worker: run subtask
            Worker-->>Orch: reply/files
        else steward
            Orch->>Steward: run subtask
            Steward-->>Orch: reply/evidence
        end
    end

    Orch->>Out: collect files + trace
    Orch-->>Caller: reply + files + routing_trace
```

## 4. Steward / Mobi 时序

```mermaid
sequenceDiagram
    participant User as User Task
    participant Steward as Steward Agent
    participant MobiTool as mobi.py
    participant MG as mobiagent_server
    participant Device as Mobile Runtime
    participant Judge as Agent/VLM Judge

    User->>Steward: 手机任务
    Steward->>MobiTool: collect or action
    MobiTool->>MG: POST /api/v1/collect or /action
    MG->>Device: natural-language mobile task
    Device-->>MG: screenshots + OCR + XML + actions + reasoning
    MG-->>MobiTool: execution evidence
    MobiTool-->>Steward: ToolResponse + metadata
    Steward->>Judge: inspect evidence / verify completion
    Judge-->>Steward: completed? confidence reason
    Steward-->>User: final answer
```

## 5. 当前设计结论

- 当前主架构核心是 `Gateway / Chat / Workflows / Orchestrator / Agents / MobiAgent / Local State`。
- `--agent-task` 当前默认已经走 Orchestrator，而不是旧口径里的“直接 Worker”。
- Daily 模块仍存在，但内部仍混有 legacy 路径。
- WeKnora 相关内容仍在仓库中，但不应再被画成当前主链路核心组件。
