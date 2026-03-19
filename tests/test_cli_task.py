"""Tests for mobiclaw.cli.task."""
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mobiclaw.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_task_submit_sync(runner):
    """Sync submit prints result text and files."""
    result_data = {
        "job_id": "job-1",
        "status": "completed",
        "result": {
            "reply": "Task completed successfully",
            "files": [{"name": "out.txt", "path": "/tmp/out.txt", "download_url": "http://x/files/job-1/out.txt"}],
        },
    }

    async def mock_submit(**kwargs):
        return result_data

    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.submit_task = AsyncMock(side_effect=mock_submit)
        r = runner.invoke(cli, ["task", "submit", "hello world"])
    assert r.exit_code == 0
    assert "Task completed successfully" in r.output
    assert "out.txt" in r.output


def test_task_submit_async(runner):
    """Async submit prints job_id and suggests status command."""
    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.submit_task = AsyncMock(
            return_value={"job_id": "abc123", "status": "queued"}
        )
        r = runner.invoke(cli, ["task", "submit", "hello", "--async"])
    assert r.exit_code == 0
    assert "abc123" in r.output
    assert "task status" in r.output


def test_task_submit_scheduled(runner):
    """Scheduled submit prints schedule_id."""
    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.submit_task = AsyncMock(
            return_value={
                "job_id": "sched-1",
                "status": "scheduled",
                "result": {
                    "schedule_id": "sched-1",
                    "message": "已创建定时任务",
                },
            }
        )
        r = runner.invoke(
            cli,
            ["task", "submit", "hello", "--schedule-type", "once", "--run-at", "2026-01-01T00:00:00Z"],
        )
    assert r.exit_code == 0
    assert "sched-1" in r.output


def test_task_status(runner):
    """Status fetches job and prints result."""
    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.get_job = AsyncMock(
            return_value={
                "job_id": "job-1",
                "status": "completed",
                "result": {"reply": "Done"},
            }
        )
        r = runner.invoke(cli, ["task", "status", "job-1"])
    assert r.exit_code == 0
    assert "Done" in r.output


def test_task_status_wait(runner):
    """Status --wait polls until completed."""
    call_count = 0

    async def mock_get(job_id):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return {"job_id": job_id, "status": "running"}
        return {"job_id": job_id, "status": "completed", "result": {"reply": "Finished"}}

    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.get_job = AsyncMock(side_effect=mock_get)
        r = runner.invoke(cli, ["task", "status", "job-1", "--wait"])
    assert r.exit_code == 0
    assert "Finished" in r.output
    assert call_count >= 2


def test_task_upload(runner, tmp_path):
    """Upload sends files and prints returned paths."""
    f1 = tmp_path / "a.txt"
    f1.write_text("content")
    with patch("mobiclaw.cli.task.GatewayClient") as mock_cls:
        mock_cls.return_value.upload_files = AsyncMock(
            return_value={
                "files": [
                    {"name": "a.txt", "path": "/uploads/20260101/a.txt", "size": 7},
                ]
            }
        )
        r = runner.invoke(cli, ["task", "upload", str(f1)])
    assert r.exit_code == 0
    assert "/uploads/20260101/a.txt" in r.output


def test_task_upload_no_files(runner):
    """Upload without files raises usage error."""
    r = runner.invoke(cli, ["task", "upload"])
    assert r.exit_code != 0
    assert "At least one file" in r.output


def test_task_help(runner):
    """Task group and subcommands have help."""
    r = runner.invoke(cli, ["task", "--help"])
    assert r.exit_code == 0
    assert "submit" in r.output
    assert "status" in r.output
    assert "upload" in r.output
