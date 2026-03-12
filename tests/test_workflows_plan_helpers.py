from __future__ import annotations

from types import SimpleNamespace

from seneschal import workflows


def test_serialize_plan_for_monitor_with_subtasks() -> None:
    plan = SimpleNamespace(
        id="plan-1",
        name="主计划",
        description="测试",
        expected_outcome="完成",
        outcome="ok",
        state="done",
        created_at="t1",
        finished_at="t2",
        subtasks=[
            SimpleNamespace(
                id="s1",
                name="子任务1",
                description="desc1",
                state="done",
                expected_outcome="eo1",
                outcome="o1",
                created_at="st1",
                finished_at="st2",
            )
        ],
    )

    serialized = workflows._serialize_plan_for_monitor(plan)
    assert serialized is not None
    assert serialized["id"] == "plan-1"
    assert serialized["state"] == "done"
    assert serialized["subtasks"][0]["index"] == 0
    assert serialized["subtasks"][0]["name"] == "子任务1"


def test_build_plan_event_delta_created_done_and_abandoned() -> None:
    event_type, delta = workflows._build_plan_event_delta(None, {"id": "p1", "name": "n1", "state": "todo"})
    assert event_type == "plan_created"
    assert delta["plan_id"] == "p1"

    event_type, delta = workflows._build_plan_event_delta({"id": "p2", "name": "n2", "state": "done"}, None)
    assert event_type == "plan_done"
    assert delta["state"] == "done"

    prev = {"id": "p3", "name": "n3", "state": "in_progress", "subtasks": []}
    curr = {"id": "p3", "name": "n3", "state": "abandoned", "subtasks": []}
    event_type, delta = workflows._build_plan_event_delta(prev, curr)
    assert event_type == "plan_abandoned"
    assert delta["previous_state"] == "in_progress"


def test_build_plan_event_delta_subtask_transitions_and_revised() -> None:
    prev = {
        "id": "p4",
        "name": "n4",
        "state": "in_progress",
        "subtasks": [{"id": "s1", "name": "a", "state": "todo"}],
    }
    curr = {
        "id": "p4",
        "name": "n4",
        "state": "in_progress",
        "subtasks": [{"id": "s1", "name": "a", "state": "in_progress"}],
    }
    event_type, delta = workflows._build_plan_event_delta(prev, curr)
    assert event_type == "subtask_activated"
    assert delta["subtask_id"] == "s1"

    prev_done = curr
    curr_done = {
        "id": "p4",
        "name": "n4",
        "state": "in_progress",
        "subtasks": [{"id": "s1", "name": "a", "state": "done", "outcome": "完成"}],
    }
    event_type, delta = workflows._build_plan_event_delta(prev_done, curr_done)
    assert event_type == "subtask_done"
    assert delta["outcome"] == "完成"

    prev_count = {"id": "p4", "name": "n4", "state": "todo", "subtasks": []}
    curr_count = {"id": "p4", "name": "n4", "state": "todo", "subtasks": [{"id": "s2", "name": "b", "state": "todo"}]}
    event_type, delta = workflows._build_plan_event_delta(prev_count, curr_count)
    assert event_type == "plan_revised"
    assert delta["subtasks_count_after"] == 1


def test_build_plan_reply_fallback_uses_latest_plan_and_done_subtasks() -> None:
    events = [
        {"plan": "invalid"},
        {
            "plan": {
                "name": "计划A",
                "state": "done",
                "outcome": "全部完成",
                "subtasks": [
                    {"name": "步骤1", "state": "done", "outcome": "已完成"},
                    {"name": "步骤2", "state": "in_progress", "outcome": ""},
                ],
            }
        },
    ]

    text = workflows._build_plan_reply_fallback(events)
    assert "计划：计划A" in text
    assert "状态：done" in text
    assert "计划结果：全部完成" in text
    assert "- 步骤1: 已完成" in text
    assert "步骤2" not in text


def test_build_plan_reply_fallback_empty_cases() -> None:
    assert workflows._build_plan_reply_fallback([]) == ""
    assert workflows._build_plan_reply_fallback([{"plan": "not-dict"}]) == ""
