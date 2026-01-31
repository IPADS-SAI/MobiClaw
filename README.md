# Seneschal 启动指南

本项目将 **AgentScope / WeKnora / MobiAgent** 组合为个人数据管家系统。下面是本地启动与联调步骤。

> 你已说明 WeKnora 已运行在 `http://localhost:8080`，并可使用 API Key 访问。

---

## 1. 环境准备

进入项目根目录：

```bash
cd /home/zhaoxi/ipads/llm-agent/Seneschal
```

确保 `.env` 已填写（本项目已支持自动加载）：

```bash
# LLM (用于分析与VL抽取)
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="<your-key>"
export OPENAI_MODEL="google/gemini-2.5-flash"

# WeKnora (已运行在本机 8080)
export WEKNORA_BASE_URL="http://localhost:8080"
export WEKNORA_API_KEY="sk-Q-ziU0BDP99DYjRluKGovL6XRFSEOH7YQALvVzPUpSru1QLJ"
export WEKNORA_SESSION_ID="seneschal-session"

# MobiAgent Gateway
export MOBIAGENT_GATEWAY_PORT="8081"
export MOBIAGENT_SERVER_MODE="cli"
export MOBIAGENT_CLI_CMD="python -m runner.mobiagent.mobiagent --service_ip 166.111.53.96 --decider_port 7003 --grounder_port 7003 --planner_port 7002 --use_qwen3 on --use_experience off --e2e --task_file {task_file} --data_dir {data_dir}"
export MOBIAGENT_TASK_DIR="mobiagent_server/tasks"
export MOBIAGENT_DATA_DIR="mobiagent_server/data"
```

说明：
- `OPENAI_*` 用于 `mobiagent_server` 解析 output_schema（VL 抽取）。
- `WEKNORA_*` 用于知识库写入与 RAG 分析。
- `MOBIAGENT_*` 用于网关联通端侧 MobiAgent CLI。

---

## 2. 启动 MobiAgent 网关（必须）

网关负责接收统一 API（`/api/v1/collect` / `/api/v1/action`），并调用 MobiAgent CLI 执行任务。

```bash
python -m mobiagent_server.server
```

默认监听：`http://localhost:8081`

---

## 3. 启动 Seneschal（AgentScope 核心）

### 3.1 运行 Demo 对话

```bash
python app.py
```

### 3.2 交互模式

```bash
python app.py --interactive
```

### 3.3 运行 Daily Loop

```bash
python app.py --daily --daily-trigger daily
```

---

## 4. 请求示例（网关）

### 4.1 Collect

```bash
curl -X POST http://localhost:8081/api/v1/collect \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"task":"获取微信聊天列表前5条摘要"}'
```

### 4.2 Action + output_schema

```bash
curl -X POST http://localhost:8081/api/v1/action \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "action_type": "add_calendar_event",
    "params": {
      "title": "产品评审",
      "date": "2025-02-01",
      "time": "15:00",
      "output_schema": {
        "title": "string",
        "date": "string",
        "time": "string",
        "success": "boolean"
      }
    },
    "options": {"wait_for_completion": true, "timeout": 60}
  }'
```

---

## 5. 常见问题

**Q1: WeKnora 已启动，还需要做什么？**  
A: 只要 `WEKNORA_BASE_URL` / `WEKNORA_API_KEY` / `WEKNORA_KB_NAME` 有效即可。Seneschal 会直接写入与查询。

**Q2: MobiAgent CLI 运行很慢怎么办？**  
A: 可先把 `MOBIAGENT_SERVER_MODE=mock`，等模型与设备就绪后再切回 `cli`。

**Q3: 解析 output_schema 为什么失败？**  
A: 检查 `OPENAI_*` 是否可用；同时确认任务最终截图存在于 `data_dir` 中。

---

如需扩展：
- 将 `mobiagent_server` 换成 `task_queue` 异步模式
- 把 `parsed_output` 写入 WeKnora 做长期记忆
- 增加更严格的 output_schema 校验
