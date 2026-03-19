# MobiClaw CLI Design Spec

## Overview

A command-line tool that connects directly to the MobiClaw gateway server, exposing all gateway functionality (chat, task submission, scheduling, MCP management, environment configuration, device management, etc.) via a structured CLI built with Click.

**Motivation**: The existing `app.py` calls low-level interfaces directly without connecting to a long-running service, making features like scheduled tasks impossible. The CLI solves this by acting as a client to the gateway server.

**Requirements (from brainstorming)**:
- Support both interactive (REPL chat, config management) and script-friendly (single-shot commands, `--output json`, piping) usage
- Config file (`~/.mobiclaw/cli.yaml`) with `config show/set/reset` commands for convenient management

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Framework | Click + prompt_toolkit + rich + httpx | Mature CLI ecosystem, nested subcommands, REPL support, async HTTP |
| CLI name | `mobiclaw` | Project name, registered via pyproject.toml entry point |
| Chat mode | REPL loop | Persistent input loop with prompt_toolkit, multi-turn conversation |
| Connection config | `~/.mobiclaw/cli.yaml` + CLI commands to manage it | Persistent config file, `config set/show/reset` commands |
| Dependencies | Optional group `cli` in pyproject.toml | `pip install mobiclaw[cli]`, keeps gateway server lean |
| Output | rich tables/JSON/text, selectable via `--output` | Flexible for both human and script consumption |

## Architecture

### Project Structure

```
mobiclaw/cli/
├── __init__.py
├── __main__.py          # python -m mobiclaw.cli entry point
├── main.py              # Click root group + global options
├── config.py            # config subcommand group (config management)
├── chat.py              # chat command (REPL)
├── task.py              # task subcommand group (submit/status/upload)
├── schedule.py          # schedule subcommand group (list/cancel)
├── session.py           # session subcommand group (list/show/delete)
├── mcp.py               # mcp subcommand group (list/add/remove)
├── env.py               # env subcommand group (show/set/edit)
├── device.py            # device subcommand group (list/show/heartbeat/remove)
├── file.py              # file subcommand group (download)
├── feishu.py            # feishu subcommand group (send-event)
├── http_client.py       # GatewayClient wrapping httpx
└── output.py            # rich output formatting utilities
```

### Entry Point

```toml
# pyproject.toml
[project.scripts]
mobiclaw = "mobiclaw.cli.main:cli"
```

## Command Tree

```
mobiclaw
├── config                          # Configuration management
│   ├── show                        # Display current config
│   ├── set <key> <value>           # Set config item (server-url, api-key, default-output, default-mode)
│   └── reset                       # Reset to defaults
│
├── chat                            # REPL chat mode
│   (options: --context-id, --mode, --agent-hint, --skill-hint, --web-search/--no-web-search)
│
├── task                            # Task management
│   ├── submit <task>               # Submit task (--async, --mode, --agent-hint, --schedule-type, etc.)
│   ├── status <job_id>             # Query task status/result (--wait for polling)
│   └── upload <files...>           # Upload files for task use
│
├── schedule                        # Scheduled tasks
│   ├── list                        # List all scheduled tasks
│   └── cancel <schedule_id>        # Cancel scheduled task (--yes to skip confirmation)
│
├── session                         # Session management
│   ├── list                        # List all sessions
│   ├── show <context_id>           # View session details + message history (--limit N)
│   └── delete <context_id>         # Delete session (--yes to skip confirmation)
│
├── mcp                             # MCP server management
│   ├── list                        # List MCP servers
│   ├── add <name> <command>        # Register MCP server (--args, --env KEY=VAL)
│   └── remove <name>               # Remove MCP server (--yes)
│
├── env                             # Gateway environment variable management
│   ├── show                        # Display variables (--schema for grouped view)
│   ├── set <KEY> <VALUE>           # Set single variable
│   └── edit                        # Open $EDITOR to edit raw .env content
│
├── device                          # Device management
│   ├── list                        # List all devices
│   ├── show <device_id>            # View device details
│   ├── heartbeat                   # Send heartbeat (--device-id, --ip, --port, --name)
│   └── remove <device_id>         # Remove device (--yes)
│
├── file                            # File operations
│   └── download <job_id> <name>    # Download task output file (--output PATH)
│
├── health                          # Health check (connectivity test)
│
└── feishu                          # Feishu integration
    └── send-event <payload_file>   # POST JSON payload to feishu/events (debug)
```

### Global Options

```
mobiclaw --server-url <url>          # Override config server URL
mobiclaw --api-key <key>             # Override config API key
mobiclaw --output json|table|text    # Output format (default: table)
mobiclaw --verbose                   # Verbose output
```

## Component Details

### Configuration (`~/.mobiclaw/cli.yaml`)

```yaml
server_url: "http://localhost:8090"
api_key: ""
default_output: "table"
default_mode: "chat"
```

Priority: CLI flags > config file > defaults.

### HTTP Client (`http_client.py`)

`GatewayClient` class wrapping `httpx.AsyncClient`:

- Auto-injects `Authorization: Bearer` header and base URL
- One typed method per gateway endpoint:
  - `submit_task()`, `get_job()`, `list_sessions()`, `get_session()`, `delete_session()`
  - `upload_files()`, `list_schedules()`, `cancel_schedule()`
  - `list_mcp_servers()`, `add_mcp_server()`, `remove_mcp_server()`
  - `get_env()`, `get_env_schema()`, `set_env_content()`, `set_env_structured()`
  - `list_devices()`, `get_device()`, `device_heartbeat()`, `remove_device()`
  - `download_file()`, `health()`, `send_feishu_event()`
- HTTP errors → `click.ClickException` with status code and message
- Connection failures → clear message ("Cannot connect to gateway server, check server_url")

### Output Formatting (`output.py`)

Uses `rich`:
- **table**: `rich.table.Table` for list data (sessions, devices, schedules)
- **json**: `rich.syntax.Syntax` with JSON highlighting
- **text**: plain text for piping/scripts
- Auto-selects: list → table, single object → key-value panel, long text → direct output

### Chat REPL (`chat.py`)

Entry: `mobiclaw chat [--context-id ID] [--mode MODE] [--agent-hint HINT] [--skill-hint HINT] [--web-search/--no-web-search]`

**Implementation**:
1. `prompt_toolkit` for input: history (`~/.mobiclaw/chat_history`), multi-line (`Alt+Enter`), Emacs keys
2. First message sent without `context_id`; response returns `context_id` for subsequent messages
3. Prompt shows short context ID: `[abc123] >`
4. Each message → `POST /api/v1/task` synchronous mode (`async_mode=false`)
5. Response text printed, output files listed with paths

**Built-in REPL commands** (`/` prefix, not sent to server):
- `/help` — show help
- `/attach <file>` — upload attachment
- `/mode <mode>` — switch mode
- `/context` — show current context_id
- `/new` — start new session
- `/quit` — exit

### Task Submit (`task.py`)

```
mobiclaw task submit <task> [options]
```

| Option | Description |
|--------|-------------|
| `--async` | Async mode, return job_id immediately |
| `--mode MODE` | chat/router/worker/steward/auto |
| `--agent-hint HINT` | Preferred agent |
| `--skill-hint HINT` | Preferred skill |
| `--context-id ID` | Bind to session |
| `--no-web-search` | Disable web search |
| `--output-path PATH` | Output path |
| `--input-file FILE` | Attachment (repeatable) |
| `--webhook-url URL` | Completion callback |
| `--schedule-type TYPE` | once/cron |
| `--cron EXPR` | Cron expression |
| `--run-at DATETIME` | One-time execution datetime |
| `--schedule-desc TEXT` | Schedule description |

**Behavior**:
- Sync (default): wait for completion, print result, list output files
- Async (`--async`): print job_id, suggest `mobiclaw task status <job_id>`
- Scheduled (`--schedule-type`): create scheduled task, print schedule_id

### Task Status

```
mobiclaw task status <job_id> [--wait]
```

- Default: query once, print status and result
- `--wait`: poll every 2s until completed/failed, show progress

### Env Set Implementation

`env set KEY VALUE`: GET `/api/v1/env/schema` → modify target key in values → PUT `/api/v1/env/schema` (preserves all other variables).

### Env Edit Implementation

`env edit`: GET `/api/v1/env` → write content to temp file → open `$EDITOR` → read back → PUT `/api/v1/env`.

### File Download

`file download <job_id> <name> [--output PATH]`: downloads to current directory by default, shows `rich.progress` bar.

### Async Handling

Click commands are synchronous. Each command entry uses `asyncio.run()` to call async `GatewayClient` methods. Chat REPL uses `prompt_toolkit` async API with asyncio event loop.

## Dependencies

```toml
[project.optional-dependencies]
cli = [
    "click>=8.1",
    "httpx>=0.27",
    "rich>=13.0",
    "prompt-toolkit>=3.0",
    "pyyaml>=6.0",
]
```

Optional dependency group: `pip install mobiclaw[cli]`. Gateway server does not require these.

## Endpoint Coverage

All 27 gateway endpoints are covered:

| Gateway Endpoint | CLI Command |
|-----------------|-------------|
| `GET /health` | `mobiclaw health` |
| `POST /api/v1/task` | `mobiclaw task submit` / `mobiclaw chat` (REPL) |
| `GET /api/v1/jobs/{job_id}` | `mobiclaw task status` |
| `POST /api/v1/chat/files` | `mobiclaw task upload` / `chat /attach` |
| `GET /api/v1/chat/sessions` | `mobiclaw session list` |
| `GET /api/v1/chat/sessions/{id}` | `mobiclaw session show` |
| `DELETE /api/v1/chat/sessions/{id}` | `mobiclaw session delete` |
| `GET /api/v1/schedules` | `mobiclaw schedule list` |
| `DELETE /api/v1/schedules/{id}` | `mobiclaw schedule cancel` |
| `GET /api/v1/files/{job_id}/{name}` | `mobiclaw file download` |
| `POST /api/v1/feishu/events` | `mobiclaw feishu send-event` |
| `GET /api/v1/env` | `mobiclaw env show` / `mobiclaw env edit` |
| `PUT /api/v1/env` | `mobiclaw env edit` |
| `GET /api/v1/env/schema` | `mobiclaw env show --schema` / `mobiclaw env set` |
| `PUT /api/v1/env/schema` | `mobiclaw env set` |
| `GET /api/v1/mcp/servers` | `mobiclaw mcp list` |
| `POST /api/v1/mcp/servers` | `mobiclaw mcp add` |
| `DELETE /api/v1/mcp/servers/{name}` | `mobiclaw mcp remove` |
| `POST /api/v1/devices/heartbeat` | `mobiclaw device heartbeat` |
| `GET /api/v1/devices` | `mobiclaw device list` |
| `GET /api/v1/devices/{id}` | `mobiclaw device show` |
| `DELETE /api/v1/devices/{id}` | `mobiclaw device remove` |
| `GET /`, `GET /console`, `GET /console/chat`, `GET /console/settings`, `GET /favicon.ico` | N/A (web UI only) |

### MCP Add Usage

`mobiclaw mcp add` supports two patterns:

1. **stdio**: `mobiclaw mcp add <name> <command> [--args A B] [--env KEY=VAL ...]`
2. **sse/streamable_http**: `mobiclaw mcp add <name> --url <url> [--transport sse]`

## Testing Strategy

- Unit tests for `GatewayClient` with httpx mock transport
- Unit tests for `output.py` formatting functions
- Integration tests for each CLI command using Click's `CliRunner`
- Mock gateway responses via `httpx.MockTransport` or `respx`
