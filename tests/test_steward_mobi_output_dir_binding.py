from __future__ import annotations

import functools
from pathlib import Path

from seneschal.agents import create_steward_agent


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

    closures = list(report_func.__closure__ or [])
    partial_cells = [cell.cell_contents for cell in closures if isinstance(cell.cell_contents, functools.partial)]

    assert partial_cells, "collect report wrapper should capture bound collect function"
    bound_collect = partial_cells[0]
    assert bound_collect.keywords.get("output_dir") == expected_mobile_dir


def test_steward_prompt_only_allows_collect_report_tool():
    agent = create_steward_agent()

    assert "call_mobi_collect_with_report" in agent.sys_prompt
    assert "call_mobi_collect_verified" not in agent.toolkit.tools
    assert "call_mobi_collect_with_retry_report" not in agent.toolkit.tools


def test_steward_formatter_promotes_tool_result_images():
    agent = create_steward_agent()
    assert getattr(agent.formatter, "promote_tool_result_images", False) is True
