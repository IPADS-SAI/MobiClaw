---
name: setup
description: Run initial MobiClaw setup. Use when user wants to install dependencies, configure environment variables, or start the gateway server. Triggers on "setup", "install", "configure", "start gateway", or first-time setup requests.
---

# MobiClaw Setup

Run setup steps automatically. Only pause when user action is required (providing API keys, choosing configuration options). Verbose logs go to `logs/`.

**Principle:** When something is broken or missing, fix it. Don't tell the user to go fix it themselves unless it genuinely requires their manual action (e.g. obtaining an API key, connecting a real device). If a dependency is missing, install it. If a service won't start, diagnose and repair. Ask the user for permission when needed, then do the work.

**UX Note:** Use `AskUserQuestion` for all user-facing questions. Do NOT collect secrets (API keys, tokens, app IDs/secrets) in chat. Instead, tell the user to add them to `.env` manually.

## 0. Prerequisites Check

Verify the essential tools are available.

Run:
- `git --version`
- `python3 --version`
- `uv --version`
- `curl --version`

**If python3 is missing or < 3.12:** AskUserQuestion: "Python 3.12+ is required but not found. Would you like me to install it?"
- Yes (recommended) — install via system package manager or pyenv
- No — abort setup, tell user to install Python 3.12+ manually

**If uv is missing:** Install it automatically:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then verify with `uv --version`. If still not found, add `~/.local/bin` to PATH and retry.

**If git is missing:** Tell user to install git (`sudo apt-get install -y git` on Debian/Ubuntu, `sudo dnf install -y git` on Fedora).

## 1. Submodule Sync

Run:
```bash
git submodule update --init --recursive
```

If it fails, warn the user and continue — submodule issues are non-fatal for basic gateway operation.

## 2. Install Python Dependencies

Run:
```bash
uv sync
```

**If uv sync fails:**
- Read the error output. Common causes:
  - Missing system libraries (e.g. `libffi-dev`, `libssl-dev`) — install them and retry
  - Python version mismatch — ensure Python 3.12+ is active
  - Lock file conflict — try `uv lock --upgrade` then `uv sync`
- If build of native extensions fails (e.g. `pillow`, `qdrant-client`), install build tools (`sudo apt-get install -y build-essential python3-dev`) and retry.

**Optional: Tesseract OCR (for Chinese document OCR)**

AskUserQuestion: "Install Tesseract OCR with Chinese language support? (Needed only if you plan to use OCR features)"
- Yes — run `sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim`
- Skip — continue without OCR support

## 3. Configure Environment

Check if `.env` already exists at the project root.

**If `.env` exists:** Read it and parse existing variables. AskUserQuestion: "An existing .env file was found. How would you like to proceed?"
- Keep and review — show current LLM provider settings, offer to update individual values
- Overwrite from template — copy `.env-example` to `.env`, then configure
- Keep as-is — skip environment configuration entirely

**If `.env` does not exist:** Copy the template:
```bash
cp .env-example .env
```

### 3a. LLM Provider (Required)

This is the only **required** configuration. AskUserQuestion: "Which LLM provider do you want to use?"
- OpenRouter (recommended) — needs `OPENROUTER_API_KEY`
- OpenAI-compatible — needs `OPENAI_API_KEY` and `OPENAI_BASE_URL`

**OpenRouter path:**
- Tell user to add `export OPENROUTER_API_KEY=<key>` to `.env` (get one at https://openrouter.ai/keys). Do NOT collect the key in chat.
- AskUserQuestion: "Which model to use as default?" with options:
  - moonshotai/kimi-k2.5 (recommended, default in template)
  - google/gemini-3-flash-preview
  - Other — let user type a model ID
- Write `OPENROUTER_MODEL` to `.env`
- AskUserQuestion: "Use a different (stronger) model for Router/Planner orchestration?"
  - Yes — ask for model ID, write `OPENROUTER_MODEL_FOR_ORCHESTRATOR`
  - No — leave empty, will fall back to the default model

**OpenAI-compatible path:**
- Tell user to add `export OPENAI_API_KEY=<key>` and `export OPENAI_BASE_URL=<url>` to `.env`. Do NOT collect the key in chat.
- AskUserQuestion: "Enter the model name:" with options:
  - "gpt-4o" — default
  - User types model via "Other" → write `OPENAI_MODEL` to `.env`

### 3b. Web Search (Optional)

AskUserQuestion: "Configure Brave Search for web search capabilities?"
- Yes — tell user to add `export BRAVE_API_KEY=<key>` to `.env` (get from https://brave.com/search/api/). Do NOT collect the key in chat.
- Skip — Worker agent will not have web search ability

### 3c. Mobile Executor (Optional)

AskUserQuestion: "Configure mobile device executor?"
- Yes — proceed to mobile configuration
- Skip — mobile features will use mock mode (default)

**If yes:**

AskUserQuestion: "Which mobile execution provider?"
- MobiAgent (recommended, default)
- UITARS
- Qwen VL
- AutoGLM

Write `MOBILE_PROVIDER` to `.env`.

AskUserQuestion: "What device type are you connecting to?"
- Android — set `MOBILE_DEVICE_TYPE=Android`, ask for `MOBILE_DEVICE_ID` (e.g. `127.0.0.1:5555`)
- HarmonyOS — set `MOBILE_DEVICE_TYPE=Harmony`, ask for `MOBILE_DEVICE_ID`
- Mock (no real device) — set `MOBILE_DEVICE_TYPE=mock`

For MobiAgent provider, configure the server endpoints:
- Ask for MobiAgent server IP/port or accept defaults (`166.111.53.96:7003`)
- Write `MOBILE_MOBIAGENT_SERVER_IP`, `MOBILE_MOBIAGENT_DECIDER_PORT`, etc. to `.env`

For other providers, tell user to add `export MOBILE_<PROVIDER>_API_BASE=<api-base>` and `export MOBILE_<PROVIDER>_MODEL=<model>` in `.env`.

### 3d. Feishu Integration (Optional)

AskUserQuestion: "Configure Feishu (Lark) bot integration?"
- Yes — proceed to Feishu configuration
- Skip — gateway will start without Feishu bot

**If yes:**

Tell user: "You need a Feishu app with bot capability enabled. Create one at https://open.feishu.cn/app"

Tell user to add `export FEISHU_APP_ID=<app-id>` and `export FEISHU_APP_SECRET=<app-secret>` to `.env`. Do NOT collect these in chat.

AskUserQuestion: "Which Feishu event transport mode?"
- Long connection (recommended for local dev, no public IP needed)
- Webhook (needs public URL)
- Both (enable both modes)

Write `FEISHU_EVENT_TRANSPORT` to `.env`.

Optionally tell user to add `export FEISHU_VERIFICATION_TOKEN=<verification-token>` and `export FEISHU_ENCRYPT_KEY=<encrypt-key>` to `.env`.

### 3e. Advanced Options (Optional)

AskUserQuestion: "Configure advanced options? (routing, scheduling, memory, RAG)"
- Yes — show sub-options
- Skip (use defaults) — continue to next step

**If yes, present each category:**

**Routing:**
- `SENESCHAL_ROUTING_DEFAULT_MODE` — default `router` (options: router, intelligent, worker, steward, auto)
- `SENESCHAL_ROUTING_STRATEGY` — default `llm_rule_hybrid`
- `SENESCHAL_ROUTING_MAX_SUBTASKS` — default `4`

**Scheduling:**
- `SENESCHAL_SCHEDULE_ENABLED` — default `1` (enabled)
- `SENESCHAL_SCHEDULE_STORE_PATH` — default `~/.seneschal/schedules.json`

**Memory:**
- `SENESCHAL_MEMORY_ENABLED` — default `1` (enabled)
- `SENESCHAL_MEMORY_FILE` — default `~/.seneschal/MEMORY.md`

**RAG:**
- `SENESCHAL_RAG_STORE_HISTORY` — default `1` (enabled)
- `SENESCHAL_RAG_STORE_PATH` — default `~/.seneschal/rag_store`

Write any user-modified values to `.env`.

## 4. Create Required Directories

Ensure working directories exist:
```bash
mkdir -p logs tmp outputs
mkdir -p ~/.seneschal
```

## 5. Start Gateway Server

Check if the gateway port is already in use:
```bash
ss -lnt "( sport = :8090 )" 2>/dev/null || lsof -iTCP:8090 -sTCP:LISTEN 2>/dev/null
```

Read `SENESCHAL_GATEWAY_PORT` from `.env` (default `8090`).

**If port is occupied:** AskUserQuestion: "Port <port> is already in use. What should we do?"
- Kill existing process and restart — identify and stop the process, then start
- Use a different port — ask for port number, update `SENESCHAL_GATEWAY_PORT` in `.env`
- Skip — don't start the gateway server

**Start the gateway in background:**
```bash
nohup uv run python -m seneschal.gateway_server > logs/gateway-server.log 2>&1 &
echo $! > tmp/gateway-server.pid
```

Wait for health check — poll the gateway until it responds:
```bash
curl -sf http://127.0.0.1:<port>/docs > /dev/null
```

Retry up to 30 times with 2-second intervals. If it doesn't come up:
- Read `logs/gateway-server.log` tail for errors
- Common issues:
  - Missing `.env` or invalid API key — re-run step 3
  - Port conflict — check for stale processes
  - Import error — re-run `uv sync` (step 2)
  - Feishu connection failure (non-fatal) — gateway still starts, just without Feishu

## 6. Verify

After the gateway is running, verify the setup:

**Check gateway health:**
```bash
curl -sf http://127.0.0.1:<port>/docs | head -c 100
```

**Check .env completeness:** Verify at minimum `OPENROUTER_API_KEY` or `OPENAI_API_KEY` is set and non-empty.

**Print summary:**
- Gateway URL: `http://127.0.0.1:<port>`
- API docs: `http://127.0.0.1:<port>/docs`
- Web console: `http://127.0.0.1:<port>/console` (if available)
- PID file: `tmp/gateway-server.pid`
- Log file: `logs/gateway-server.log`
- Configured features: list which optional features are enabled (Feishu, Brave Search, Mobile, etc.)

## Stopping the Gateway

To stop the gateway server later:
```bash
kill $(cat tmp/gateway-server.pid 2>/dev/null) 2>/dev/null; rm -f tmp/gateway-server.pid
```

Or use the project's stop script:
```bash
bash scripts/stop_all.sh
```

## Troubleshooting

**Gateway not starting:** Check `logs/gateway-server.log`. Common causes:
- Missing LLM API key — ensure `OPENROUTER_API_KEY` or `OPENAI_API_KEY` is set in `.env`
- Port already in use — check with `lsof -iTCP:<port> -sTCP:LISTEN` or `ss -lnt`
- Python import error — run `uv sync` to ensure all dependencies are installed
- Feishu credentials missing (warning only) — gateway will skip Feishu long connection but still start

**uv sync fails:** Ensure Python 3.12+ is the active interpreter. Check `python3 --version`. Install missing system dev packages if native builds fail.

**Mobile executor errors:** If `MOBILE_DEVICE_TYPE` is not `mock`, ensure the target device is connected and reachable. For Android: `adb devices` should list the device. For MobiAgent: verify the server is running at the configured IP/port.

**Feishu bot not responding:** Verify `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are correct. Check `logs/gateway-server.log` for connection errors. For long connection mode, no public IP is needed. For webhook mode, ensure the public URL is reachable.

**Task execution fails with timeout:** Increase `SENESCHAL_SUBTASK_TIMEOUT_S` (default 300s) or `SENESCHAL_ROUTER_TIMEOUT_S` (default 120s) in `.env`. Restart the gateway after changes.

**Out of memory or slow responses:** Try a lighter model (e.g. `google/gemini-3-flash-preview`). Reduce `SENESCHAL_ROUTING_MAX_SUBTASKS` to limit parallel work.
