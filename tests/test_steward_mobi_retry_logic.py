from __future__ import annotations

import asyncio

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from mobiclaw.agents.factories_steward_chat_user import create_steward_agent


def _build_collect_response(task_desc: str) -> ToolResponse:
    metadata = {
        "success": True,
        "run_dir": "tmp/run",
        "index_file": "tmp/run/execution_result.json",
        "final_image_path": "tmp/run/2.jpg",
        "status_hint": "completed",
        "step_count": 2,
        "action_count": 2,
        "last_reasoning": "已进入目标页面",
        "execution": {
            "summary": {"status_hint": "completed", "step_count": 2, "action_count": 2},
            "artifacts": {"images": ["tmp/run/2.jpg"], "hierarchies": [], "overlays": [], "logs": []},
            "history": {"actions": [], "reacts": [], "reasonings": ["r1", "r2"]},
            "task_description": task_desc,
        },
    }
    return ToolResponse(content=[TextBlock(type="text", text="ok")], metadata=metadata)


def test_steward_collect_report_runs_single_attempt_and_returns_image(monkeypatch):
    from mobiclaw.agents import factories_steward_chat_user as steward_mod

    monkeypatch.setenv("STEWARD_MOBI_VLM_ENABLED", "1")
    collect_calls: list[str] = []
    summarize_calls: list[dict] = []

    async def _fake_collect(task_desc: str, max_retries: int = 0):
        _ = max_retries
        collect_calls.append(task_desc)
        return _build_collect_response(task_desc)

    async def _fake_summarize_execution_with_vlm(**kwargs):
        summarize_calls.append(kwargs)
        return {
            "summary": {
                "screen_state": "当前在活动详情页",
                "trajectory_last_steps": ["点击活动入口"],
                "relevant_information": ["有活动标题"],
                "extracted_text": ["最近活动", "活动详情"],
            }
        }

    monkeypatch.setattr(steward_mod, "call_mobi_collect_verified", _fake_collect)
    monkeypatch.setattr(steward_mod, "_summarize_execution_with_vlm", _fake_summarize_execution_with_vlm)
    monkeypatch.setattr(steward_mod, "create_openai_model", lambda **kwargs: object())

    agent = create_steward_agent()
    report_func = agent.toolkit.tools["call_mobi_collect_with_report"].original_func
    resp = asyncio.run(report_func("查看最近活动"))
    md = resp.metadata

    assert collect_calls == ["查看最近活动"]
    assert md["task"] == "查看最近活动"
    assert md["success"] is True
    assert md["requires_agent_validation"] is True
    assert md["attempt"] == 1
    assert md["attempt_total"] == 1
    assert "attempt_item" not in md
    assert "failure_report" not in md
    assert "validation_mode" not in md
    assert "needs_agent_judgement" not in md
    assert summarize_calls
    assert md["vlm_summary_screen_state"] == "当前在活动详情页"
    assert len(resp.content) == 2
    assert "完成状态: success" in resp.content[0]["text"]
    assert "证据可用性:" in resp.content[0]["text"]
    assert resp.content[1]["type"] == "image"
    assert resp.content[1]["source"]["url"] == "tmp/run/2.jpg"


def test_steward_collect_report_vlm_disabled_still_returns_pack(monkeypatch):
    from mobiclaw.agents import factories_steward_chat_user as steward_mod

    monkeypatch.setenv("STEWARD_MOBI_VLM_ENABLED", "0")
    collect_calls: list[str] = []

    async def _fake_collect(task_desc: str, max_retries: int = 0):
        _ = max_retries
        collect_calls.append(task_desc)
        return _build_collect_response(task_desc)

    monkeypatch.setattr(steward_mod, "call_mobi_collect_verified", _fake_collect)
    agent = create_steward_agent()
    report_func = agent.toolkit.tools["call_mobi_collect_with_report"].original_func
    resp = asyncio.run(report_func("查看最近活动"))
    md = resp.metadata

    assert collect_calls == ["查看最近活动"]
    assert md["task"] == "查看最近活动"
    assert md["success"] is True
    assert md["requires_agent_validation"] is True
    assert "validation_mode" not in md
    assert md["vlm_summary_screen_state"] == ""
