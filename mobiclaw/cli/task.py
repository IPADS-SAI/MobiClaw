"""Task submit, status, and upload commands."""
from __future__ import annotations

import asyncio
import time

import click

from .http_client import GatewayClient
from .output import print_text, render


def register_task_commands(cli_group: click.Group) -> None:
    """Register task submit, status, upload subcommands."""

    @cli_group.group("task")
    def task():
        """Submit, check status, or upload files for tasks."""

    @task.command("submit")
    @click.argument("task_text", required=True)
    @click.option("--async", "async_mode", is_flag=True, help="Submit asynchronously, return job_id")
    @click.option("--mode", default="router", help="Execution mode (chat, router, etc.)")
    @click.option("--agent-hint", "agent_hint", default=None, help="Agent selection hint")
    @click.option("--skill-hint", "skill_hint", default=None, help="Skill selection hint")
    @click.option("--context-id", "context_id", default=None, help="Session/context ID")
    @click.option("--no-web-search", is_flag=True, help="Disable web search")
    @click.option("--output-path", "output_path", default=None, help="Output path hint")
    @click.option("--input-file", "input_files", multiple=True, help="Input file path (repeatable)")
    @click.option("--webhook-url", "webhook_url", default=None, help="Webhook URL for async callback")
    @click.option("--schedule-type", "schedule_type", default=None, help="Schedule type: once or cron")
    @click.option("--cron", "cron_expr", default=None, help="Cron expression (5 fields, mon-sun for weekday)")
    @click.option("--run-at", "run_at", default=None, help="ISO 8601 datetime for once schedule")
    @click.option("--schedule-desc", "schedule_desc", default=None, help="Human-readable schedule description")
    @click.pass_context
    def submit(
        ctx,
        task_text: str,
        async_mode: bool,
        mode: str,
        agent_hint: str | None,
        skill_hint: str | None,
        context_id: str | None,
        no_web_search: bool,
        output_path: str | None,
        input_files: tuple[str, ...],
        webhook_url: str | None,
        schedule_type: str | None,
        cron_expr: str | None,
        run_at: str | None,
        schedule_desc: str | None,
    ):
        """Submit a task to the gateway."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        payload: dict = {
            "task": task_text.strip(),
            "async_mode": async_mode,
            "mode": mode,
            "agent_hint": agent_hint or None,
            "skill_hint": skill_hint or None,
            "context_id": context_id or None,
            "web_search_enabled": not no_web_search,
            "output_path": output_path or None,
            "input_files": list(input_files) if input_files else [],
            "webhook_url": webhook_url or None,
        }

        if schedule_type:
            schedule: dict = {
                "schedule_type": schedule_type,
                "cron_expr": cron_expr,
                "run_at": run_at,
                "description": schedule_desc,
            }
            payload["schedule"] = {k: v for k, v in schedule.items() if v is not None}

        if not async_mode and not schedule_type:
            print_text("Sync mode: waiting for agent to complete...")

        result = asyncio.run(client.submit_task(**payload))

        if result.get("status") == "scheduled":
            schedule_id = (
                result.get("result", {}).get("schedule_id")
                or result.get("job_id")
            )
            print_text(f"Schedule created: {schedule_id}")
            if result.get("result", {}).get("message"):
                print_text(str(result["result"]["message"]))
            return

        if async_mode:
            job_id = result.get("job_id", "")
            print_text(f"Job submitted: {job_id}")
            print_text(f"Check status: mobiclaw task status {job_id}")
            return

        # Sync: print result text and files
        res = result.get("result") or {}
        reply = res.get("reply") or res.get("text") or res.get("text_content") or ""
        if reply:
            print_text(str(reply).strip())
        files = res.get("files") or []
        if files:
            print_text("\nFiles:")
            for f in files:
                name = f.get("name") or f.get("path") or "?"
                path = f.get("path", "")
                url = f.get("download_url", "")
                if url:
                    print_text(f"  - {name}: {url}")
                elif path:
                    print_text(f"  - {name}: {path}")
                else:
                    print_text(f"  - {name}")
        if not reply and not files:
            render(result, output_fmt)

    @task.command("status")
    @click.argument("job_id", required=True)
    @click.option("--wait", is_flag=True, help="Poll every 2s until completed/failed")
    @click.pass_context
    def status(ctx, job_id: str, wait: bool):
        """Get job status. Use --wait to poll until done."""
        from .config import resolve_config

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        output_fmt = cfg.get("output_fmt", "table")

        async def _get() -> dict:
            return await client.get_job(job_id)

        while True:
            result = asyncio.run(_get())
            status_val = result.get("status", "unknown")
            if not wait or status_val in ("completed", "failed"):
                break
            time.sleep(2)

        if result.get("status") == "completed" and result.get("result"):
            res = result["result"]
            reply = res.get("reply") or res.get("text") or res.get("text_content") or ""
            if reply:
                print_text(str(reply).strip())
            files = res.get("files") or []
            if files:
                print_text("\nFiles:")
                for f in files:
                    name = f.get("name") or f.get("path") or "?"
                    url = f.get("download_url", "")
                    path = f.get("path", "")
                    if url:
                        print_text(f"  - {name}: {url}")
                    elif path:
                        print_text(f"  - {name}: {path}")
                    else:
                        print_text(f"  - {name}")
        elif result.get("error"):
            print_text(f"Error: {result['error']}")
        else:
            render(result, output_fmt)

    @task.command("upload")
    @click.argument("files", nargs=-1, type=click.Path(exists=True))
    @click.pass_context
    def upload(ctx, files: tuple[str, ...]):
        """Upload files to the gateway. Returns server paths for use in task input_files."""
        from .config import resolve_config

        if not files:
            raise click.UsageError("At least one file path is required")

        cfg = resolve_config(ctx)
        client = GatewayClient(cfg["server_url"], cfg["api_key"])
        result = asyncio.run(client.upload_files(list(files)))
        paths = [f.get("path", "") for f in result.get("files", []) if f.get("path")]
        for p in paths:
            print_text(p)
