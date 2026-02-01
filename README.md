# Seneschal 启动指南

本项目将 **AgentScope / WeKnora / MobiAgent** 组合为个人数据管家系统。下面是推荐的启动顺序与配置流程。

---

## 1. 下载仓库并拉取子仓库

```bash
git clone <repo-url>
cd /home/zhaoxi/ipads/llm-agent/Seneschal
git submodule update --init --recursive
```

---

## 2. 启动并部署 WeKnora（快速开发模式）

方式 1：使用 Make 命令（[推荐](https://github.com/Tencent/WeKnora/blob/main/README_CN.md)）

```bash
make dev-start      # 启动基础设施
make dev-app        # 启动后端（新终端）
make dev-frontend   # 启动前端（新终端）
```

方式 2：一键启动

```bash
./scripts/quick-dev.sh
```

方式 3：使用脚本

```bash
./scripts/dev.sh start     # 启动基础设施
./scripts/dev.sh app       # 启动后端（新终端）
./scripts/dev.sh frontend  # 启动前端（新终端）
```

---

## 3. 修改并导入 WeKnora 配置

根据注册用户名（或租户）修改 `backup/` 目录下的配置文件（租户信息默认 `tenant_id=10000`）：

- `backup/models_export.json`
- `backup/custom_agents_export.json`
- `backup/knowledge_bases_export.json`

修改完成后执行一键导入：

```bash
MODELS_JSON=./backup/models_export.json \
KB_JSON=./backup/knowledge_bases_export.json \
AGENTS_JSON=./backup/custom_agents_export.json \
./scripts/weknora_import.sh
```

导入完成会输出校验结果：
```
Import completed. models=... knowledge_bases=... custom_agents=...
```

记录WeKnora的 **API Key**，后续配置需要使用。

---

## 4. 配置环境变量并同步依赖

激活并同步 uv 环境：

```bash
uv sync
```

确保 `.env` 已填写（支持自动加载）：

```bash
# LLM (用于分析与VL抽取)
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="<your-key>"
export OPENAI_MODEL="google/gemini-2.5-flash"

# WeKnora (已运行在本机 8080)
export WEKNORA_BASE_URL="http://localhost:8080"
# 结合 WeKnora 配置查看填写
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

## 5. 运行 MobiAgent Server

网关负责接收统一 API（`/api/v1/collect` / `/api/v1/action`），并调用 MobiAgent CLI 或者其他GUI-Agent 执行手机侧任务，以实现解耦替换和便于调试测试。

```bash
python -m mobiagent_server.server
```

默认监听：`http://localhost:8081`

---

## 6. 运行示例程序

### 6.1 运行 Demo 对话

```bash
python app.py
```

### 6.2 交互模式

```bash
python app.py --interactive
```

### 6.3 运行 Daily Loop

```bash
python app.py --daily --daily-trigger daily
```

---

## 7. 请求示例（网关）

### 7.1 Collect

为支持完整任务执行的场景。

```bash
curl -X POST http://localhost:8081/api/v1/collect \
  -H "Authorization: Bearer <MOBI_AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"task":"获取微信聊天列表前5条摘要"}'
```

### 7.2 Action + output_schema

为支持特定操作、单步操作场景预留接口和请求格式。
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

## 8. 常见问题

**Q1: WeKnora 已启动，还需要做什么？**  
A: 修改填写对应的模型、API配置后，一键导入[修改并导入 WeKnora 配置](#3-修改并导入-weknora-配置)；确保 `WEKNORA_BASE_URL` / `WEKNORA_API_KEY` / `WEKNORA_KB_NAME` 有效，后续 Seneschal 会通过API接口直接写入知识库与查询。

**Q2: MobiAgent CLI 运行很慢、指令执行错误怎么办？**  
A: 可先把 `MOBIAGENT_SERVER_MODE=mock`，等模型与设备调试就绪后再切回 `cli`。


---

## WeKnora 配置参考

### 一键导出（模型 / 知识库 / 智能体）

使用脚本统一导出三类配置（JSON）：

```bash
./scripts/weknora_export.sh
```

默认会生成：
- `models_export.json`
- `knowledge_bases_export.json`
- `custom_agents_export.json`

说明：
- 智能体导出优先走 API（包含内置智能体），因此需确保已加载 `.env` 中的 `WEKNORA_BASE_URL` / `WEKNORA_API_KEY`，若没有也会会退到数据库读取。
- 也可使用环境变量覆盖容器名或输出目录：
  - `CONTAINER_NAME`（默认 `WeKnora-postgres-dev`）
  - `DB_NAME`（默认 `WeKnora`）
  - `DB_USER`（默认 `postgres`）
  - `OUT_DIR`（默认当前目录）

示例：
```bash
OUT_DIR=./backup ./scripts/weknora_export.sh
```

### 一键导入（模型 / 知识库 / 智能体）

使用脚本一键导入（自动压缩 JSON、拷贝到容器、导入并校验数量）：

```bash
MODELS_JSON=./backup/models_export.json \
KB_JSON=./backup/knowledge_bases_export.json \
AGENTS_JSON=./backup/custom_agents_export.json \
./scripts/weknora_import.sh
```

脚本导入完成后会输出校验结果：
```
Import completed. models=... knowledge_bases=... custom_agents=...
```

如需仅导入某一类配置，可保留对应文件路径，其它参数使用默认路径或修改为空路径即可。
