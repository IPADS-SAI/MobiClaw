from __future__ import annotations

import functools
from pathlib import Path

from mobiclaw.agents import create_steward_agent


def _find_partial_in_wrapped_closures(func):
    """Find first functools.partial captured in function closure chain.

    Tools may be wrapped (for timeout/error handling). This helper traverses
    ``__wrapped__`` links and inspects each closure layer.
    """
    seen: set[int] = set()
    current = func
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for cell in list(current.__closure__ or []):
            value = cell.cell_contents
            if isinstance(value, functools.partial):
                return value
        current = getattr(current, "__wrapped__", None)
    return None


def test_steward_mobi_tools_bind_job_mobile_exec(tmp_path):
    job_output_dir = tmp_path / "job_x"
    expected_mobile_dir = str((job_output_dir / "mobile_exec").resolve())

    agent = create_steward_agent(job_context={"job_output_dir": str(job_output_dir)})
    tools = agent.toolkit.tools

    action_tool = tools["call_mobi_action"]

    assert "call_mobi_collect_verified" not in tools
    assert "call_mobi_collect_with_report" in tools
    assert action_tool.preset_kwargs.get("output_dir") == expected_mobile_dir
    assert Path(expected_mobile_dir).exists()


def test_steward_collect_report_uses_bound_collect_output_dir(tmp_path):
    job_output_dir = tmp_path / "job_y"
    expected_mobile_dir = str((job_output_dir / "mobile_exec").resolve())

    agent = create_steward_agent(job_context={"job_output_dir": str(job_output_dir)})
    report_func = agent.toolkit.tools["call_mobi_collect_with_report"].original_func

    bound_collect = _find_partial_in_wrapped_closures(report_func)
    assert bound_collect is not None, "collect report wrapper should capture bound collect function"
    assert bound_collect.keywords.get("output_dir") == expected_mobile_dir


def test_steward_prompt_only_allows_collect_report_tool():
    agent = create_steward_agent()

    assert "call_mobi_collect_with_report" in agent.sys_prompt
    assert "call_mobi_collect_verified" not in agent.toolkit.tools
    assert "call_mobi_collect_with_retry_report" not in agent.toolkit.tools


def test_steward_formatter_promotes_tool_result_images():
    agent = create_steward_agent()
    assert getattr(agent.formatter, "promote_tool_result_images", False) is True
