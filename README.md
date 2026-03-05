# Seneschal

Seneschal 是一个“**编排层**”项目：用 Agent 协调手机端执行（MobiAgent）与知识库分析（WeKnora），形成 `Collect -> Store -> Analyze -> Execute` 的自动化闭环。

- **编排入口**：`app.py` + `seneschal/workflows.py`
- **智能体**：`seneschal/agents.py`（Steward / Worker）
- **工具层**：`seneschal/tools/`（mobi/weknora/web/shell/file/papers）
- **手机网关**：`mobiagent_server/server.py`（collect/action/jobs）
- **任务网关**：`seneschal/gateway_server.py`（统一任务入口）
- **定时任务**：`seneschal/dailytasks/runner.py`

---

## 文档导航

- 架构总览：`docs/Seneschal-简化架构图.md`
- 架构详解：`docs/Seneschal-项目架构说明.md`
- 详细分层图：`docs/Seneschal-详细架构图.md`
- 子模块文档：
  - `docs/模块-seneschal-core.md`
  - `docs/模块-tools.md`
  - `docs/模块-dailytasks.md`
  - `docs/模块-gateway.md`

---

## 从 0 到运行（最短路径）

### 1) 拉取代码与子模块

```bash
git clone <repo-url>
cd Seneschal
git submodule update --init --recursive
```

### 2) 安装 Python 依赖

```bash
uv sync
```

### 3) 配置环境变量

```bash
cp .env-example .env
```

然后在 shell 中预先导出关键密钥（不要硬编码到仓库）：

```bash
export OPENROUTER_API_KEY='...'
export WEKNORA_API_KEY='...'
# 可选：联网搜索
export BRAVE_API_KEY='...'
```

### 4) 启动依赖服务

#### 4.1 启动 WeKnora（在 `WeKnora` 子模块）

推荐开发流程（按 WeKnora README）：

```bash
cd WeKnora
make dev-start
make dev-app
make dev-frontend
```

#### 4.2 启动 Rerank（按需）

```bash
cd WeKnora
modelscope download --model BAAI/bge-reranker-v2-m3 --local_dir bge-reranker-v2-m3
python rerank_server_bge-reranker-v2-m3.py
```

#### 4.3 导入 WeKnora 配置

```bash
cd /workspace/Seneschal
ENV_FILE=./.env CONFIG_DIR=./configs bash ./scripts/weknora_import.sh
```

> 首次部署建议先确认 `configs/*.json` 中 tenant、用户、知识库与模型配置是否与你的 WeKnora 环境一致。

### 5) 启动 MobiAgent 网关

```bash
python -m mobiagent_server.server
```

默认端口：`8081`（可通过 `MOBIAGENT_GATEWAY_PORT` 修改）。

### 6) 运行 Seneschal

#### Demo 模式

```bash
python app.py
```

#### 交互模式

```bash
python app.py --interactive
```

#### Worker 单任务模式

```bash
python app.py --agent-task "从 arXiv 搜索今天的 Agent 论文并总结" --output "outputs/papers.md"
```

#### Daily 任务模式

```bash
python app.py --daily --daily-trigger daily
```

---

## 一键脚本（可选）

### 一键启动

```bash
bash ./scripts/bootstrap_one_click.sh
```

### 一键停止

```bash
bash ./scripts/stop_all.sh
```

---

## 服务接口速查

### MobiAgent Gateway

- `POST /api/v1/collect`
- `POST /api/v1/action`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/result`

### Seneschal Gateway

- `POST /api/v1/task`（同步或异步）
- `GET /api/v1/jobs/{job_id}`
- `GET /health`

---

## 常见问题

### Q1：为什么调用手机工具时返回 mock？
通常是 `MOBIAGENT_SERVER_MODE` 不是 `cli/proxy/task_queue`，或网关不可达。先检查 `python -m mobiagent_server.server` 是否启动、`MOBI_AGENT_BASE_URL` 是否正确。

### Q2：为什么 WeKnora 写入/查询失败？
请检查 `WEKNORA_BASE_URL`、`WEKNORA_API_KEY`、`WEKNORA_KB_NAME`、`WEKNORA_AGENT_NAME` 是否与 WeKnora 环境一致，并确认目标知识库已存在。

### Q3：Daily 模式没有执行任务？
`tasks.json` 中任务按 `triggers` 过滤，确保 `--daily-trigger` 值在任务 `triggers` 中。
