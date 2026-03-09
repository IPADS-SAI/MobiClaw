import asyncio
import re
import time
import os

import pytest

from seneschal import orchestrator


class _TimedRouterProxy:
    def __init__(self, inner_agent):
        self._inner_agent = inner_agent
        self.last_prompt = ""
        self.llm_call_ms = 0.0
        self.last_response = None

    async def __call__(self, msg):
        self.last_prompt = str(getattr(msg, "content", ""))
        started = time.perf_counter()
        result = await self._inner_agent(msg)
        self.llm_call_ms = (time.perf_counter() - started) * 1000
        self.last_response = result
        return result

def _is_placeholder_api_key(raw: str) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return True
    return value in {"sk-or-v1-xxx", "sk-xxx", "xxx", "none", "null"} or "xxx" in value


def test_llm_route_real_llm_call_timing_and_prompt(monkeypatch):
    if os.environ.get("RUN_REAL_LLM_ROUTE_TEST", "0") != "1":
        pytest.skip("Set RUN_REAL_LLM_ROUTE_TEST=1 to run real LLM timing test")

    if _is_placeholder_api_key(os.environ.get("OPENAI_API_KEY", "")):
        pytest.skip("OPENAI_API_KEY is missing or placeholder; skip real LLM timing test")

    orchestrator._available_agent_names.cache_clear()

    monkeypatch.setattr(
        orchestrator,
        "get_agent_capability_descriptions",
        lambda: {
            "worker": {"role": "web and paper retrieval"},
            "steward": {"role": "mobile and data workflow"},
        },
    )
    monkeypatch.setitem(orchestrator.ROUTING_CONFIG, "route_task_max_chars", 60)
    monkeypatch.setitem(orchestrator.ROUTING_CONFIG, "route_profile_desc_max_chars", 40)

    task = (
        "Search latest OSDI and SOSP papers about storage disaggregation and summarize the key points "
        "with links and short comments."
    )
    strategy = "unit-test-strategy"

    real_router = orchestrator.create_router_agent()
    timed_router = _TimedRouterProxy(real_router)
    monkeypatch.setattr(orchestrator, "create_router_agent", lambda: timed_router)

    total_started = time.perf_counter()
    decision = asyncio.run(orchestrator._llm_route(task, strategy))
    total_ms = (time.perf_counter() - total_started) * 1000
    llm_call_ms = timed_router.llm_call_ms
    prompt = timed_router.last_prompt
    response_text = orchestrator._extract_response_text(timed_router.last_response)

    assert decision.strategy == strategy
    assert decision.target_agents
    assert 0.0 <= decision.confidence <= 1.0

    assert prompt, "router prompt should be captured from real _llm_route call"
    assert "你是任务路由器" in prompt
    assert "仅输出 JSON" in prompt
    assert "target_agents" in prompt
    assert "候选 Agent(精简版)" in prompt

    compact_task = orchestrator._compact_task_for_route(
        task,
        int(orchestrator.ROUTING_CONFIG.get("route_task_max_chars", 320)),
    )
    assert f"用户任务(精简): {compact_task}" in prompt

    print("\n=== llm_route real call timing (ms) ===")
    print(f"llm_call_ms={llm_call_ms:.3f}")
    print(f"total_ms={total_ms:.3f}")
    print(f"non_llm_overhead_ms={max(total_ms - llm_call_ms, 0.0):.3f}")

    assert llm_call_ms > 0.0
    assert total_ms >= llm_call_ms

    print("\n=== full prompt fed to llm_route router ===")
    print(prompt)

    print("\n=== full raw response returned by router LLM ===")
    print(response_text)

    compact_len = len(compact_task)
    assert compact_len <= int(orchestrator.ROUTING_CONFIG["route_task_max_chars"]) + 3
    assert re.search(r'\"agent\":\"worker\"|\"agent\":\"steward\"', prompt)
