from __future__ import annotations

import asyncio

from mobiclaw import orchestrator


class _DummyAgent:
    async def __call__(self, msg):
        _ = msg
        return None


def test_run_one_agent_allows_none_external_context(monkeypatch):
    monkeypatch.setattr(orchestrator, "_build_agent", lambda *args, **kwargs: _DummyAgent())

    result = asyncio.run(
        orchestrator._run_one_agent(
            agent_name="worker",
            task="hello",
            output_path=None,
            output_dir=None,
            temp_dir=None,
            selected_skills=None,
            prior_context=None,
            session_manager=None,
            session_handle=None,
            session_mode="router",
            external_context=None,
        )
    )

    assert result["agent"] == "worker"
    assert "reply" in result
