## Environment Variables

Set these before running:

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `WEKNORA_BASE_URL`
- `WEKNORA_API_KEY`
- `WEKNORA_KB_NAME` (知识库名称，例如 test)
- `WEKNORA_KB_ID` (可选，已知 ID 时直接填)
- `WEKNORA_AGENT_NAME` (自定义智能体名称，例如 个人助理)
- `WEKNORA_SESSION_ID`
- `MOBIAGENT_SERVER_MODE` (mock 或 proxy)
- `MOBIAGENT_COLLECT_URL` (proxy 模式下的采集端点)
- `MOBIAGENT_ACTION_URL` (proxy 模式下的执行端点)
- `MOBIAGENT_V1_URL` (MobiAgent /v1 接口地址，可选)
- `MOBIAGENT_QUEUE_DIR` (task_queue 模式任务目录)
- `MOBIAGENT_RESULT_DIR` (task_queue 模式结果目录)
- `MOBIAGENT_CLI_CMD` (cli 模式命令模板，包含 {task_file} 和 {data_dir})
- `MOBIAGENT_TASK_DIR` (cli 模式任务文件目录)
- `MOBIAGENT_DATA_DIR` (cli 模式 data_dir 根目录)
- `MOBIAGENT_GATEWAY_PORT` (mobiagent_server 监听端口)
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` (用于解析 output_schema 的 VL 模型)

Example:

```bash
export OPENAI_BASE_URL=http://123.60.91.241:9003/v1
export OPENAI_API_KEY=sk-xxx
export OPENAI_MODEL=Qwen3-VL-30B-A3B-Instruct
export WEKNORA_BASE_URL=http://localhost:8080
export WEKNORA_API_KEY=sk-xxx
export WEKNORA_KB_NAME=test
export WEKNORA_AGENT_NAME=个人助理
export WEKNORA_SESSION_ID=seneschal-session
export MOBIAGENT_SERVER_MODE=cli
export SERVICE_IP=166.111.53.96
export DECIDER_PORT=7003
export GROUNDER_PORT=7003
export PLANNER_PORT=7002
export MOBIAGENT_CLI_CMD="python -m runner.mobiagent.mobiagent --service_ip $SERVICE_IP --decider_port $DECIDER_PORT --grounder_port $GROUNDER_PORT --planner_port $PLANNER_PORT --e2e --task_file {task_file} --data_dir {data_dir}"
export MOBIAGENT_TASK_DIR=mobiagent_server/tasks
export MOBIAGENT_DATA_DIR=mobiagent_server/data
export MOBIAGENT_QUEUE_DIR=mobiagent_server/queue
export MOBIAGENT_RESULT_DIR=mobiagent_server/results
export MOBIAGENT_GATEWAY_PORT=8081
```
备用地址：

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1/
export OPENAI_API_KEY=sk-xxxxxxxxxxxxz
export OPENAI_MODEL=google/gemini-2.5-flash
export WEKNORA_BASE_URL=http://localhost:8080
export WEKNORA_API_KEY=sk-xxx
export WEKNORA_KB_NAME=test
export WEKNORA_AGENT_NAME=个人助理
export WEKNORA_SESSION_ID=seneschal-session
export MOBIAGENT_SERVER_MODE=cli
export SERVICE_IP=123.60.91.241
export DECIDER_PORT=8000
export GROUNDER_PORT=8000
export PLANNER_PORT=9003
export MOBIAGENT_CLI_CMD="python -m runner.mobiagent.mobiagent --service_ip $SERVICE_IP --decider_port $DECIDER_PORT --grounder_port $GROUNDER_PORT --planner_port $PLANNER_PORT --e2e --task_file {task_file} --data_dir {data_dir}"
export MOBIAGENT_TASK_DIR=mobiagent_server/tasks
export MOBIAGENT_DATA_DIR=mobiagent_server/data
export MOBIAGENT_QUEUE_DIR=mobiagent_server/queue
export MOBIAGENT_RESULT_DIR=mobiagent_server/results
export MOBIAGENT_GATEWAY_PORT=8081
```
