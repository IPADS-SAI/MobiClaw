from __future__ import annotations

import asyncio

from seneschal.agents.common import _extract_vlm_evidence, _judge_completion_with_vlm


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text_content(self) -> str:
        return self._text


class _FakeModel:
    def __init__(self, text: str) -> None:
        self._text = text
        self.messages = None

    async def __call__(self, messages):
        self.messages = messages
        return _FakeResponse(self._text)


def test_extract_vlm_evidence_includes_recent_trajectory_and_ocr(tmp_path) -> None:
    image_path = tmp_path / "final.png"
    image_path.write_bytes(b"fake-image")

    metadata = {
        "final_task": "查看最近活动",
        "execution": {
            "summary": {
                "status_hint": "running",
                "step_count": 4,
                "action_count": 3,
            },
            "artifacts": {
                "images": [str(image_path)],
            },
            "history": {
                "reasonings": ["进入个人页", "打开活动页"],
                "actions": [
                    {"type": "tap", "action_index": 2, "x": 100, "y": 200},
                    {"type": "swipe", "action_index": 3, "direction": "up"},
                ],
                "reacts": [
                    {
                        "reasoning": "页面里有最近活动入口",
                        "function": {"name": "tap", "parameters": {"x": 100, "y": 200}},
                        "action_index": 2,
                    }
                ],
            },
            "ocr": {
                "full_text": "最近活动\n活动详情\n领奖记录",
                "by_step": [
                    {"step": 2, "text": "最近活动"},
                    {"step": 3, "text": "活动详情"},
                ],
            },
        },
    }

    evidence = _extract_vlm_evidence(
        metadata,
        last_n_images=1,
        last_n_steps=2,
        max_reasonings_chars=400,
    )

    assert evidence["task_description"] == "查看最近活动"
    assert evidence["images_selected"] == [str(image_path)]
    assert evidence["image_data_urls"][0].startswith("data:image/png;base64,")
    assert "action=tap" in evidence["recent_actions_text"]
    assert "function=tap" in evidence["recent_reacts_text"]
    assert "活动详情" in evidence["recent_ocr_text"]
    assert "领奖记录" in evidence["ocr_full_text"]
    assert evidence["last_n_steps"] == 2


def test_judge_completion_with_vlm_parses_summary_payload() -> None:
    model = _FakeModel(
        """
        {
          "completed": true,
          "confidence": 0.93,
          "reason": "页面已经显示任务结果",
          "evidence": ["截图显示最近活动列表", "最后一步打开了活动详情"],
          "missing_requirements": [],
          "summary": {
            "screen_state": "当前停留在最近活动详情页",
            "trajectory_last_steps": ["点击最近活动入口", "打开活动详情"],
            "relevant_information": ["存在活动标题", "可见领奖记录入口"],
            "extracted_text": ["最近活动", "活动详情", "领奖记录"]
          }
        }
        """
    )

    result = asyncio.run(
        _judge_completion_with_vlm(
            model=model,
            task_desc="查看最近活动",
            success_criteria="看到最近活动详情",
            status_hint="running",
            step_count=4,
            action_count=3,
            reasonings_text="1. 进入首页\n2. 打开活动页",
            recent_actions_text="1. step=2 action=tap extras={\"x\":100}",
            recent_reacts_text="1. step=2 function=tap reasoning=页面里有最近活动入口 params={\"x\":100}",
            recent_ocr_text="1. step=3 text=活动详情",
            ocr_full_text="最近活动\n活动详情\n领奖记录",
            last_n_steps=2,
            image_data_urls=["data:image/png;base64,ZmFrZQ=="],
            timeout_s=1.0,
        )
    )

    assert result["completed"] is True
    assert result["confidence"] == 0.93
    assert result["summary"]["screen_state"] == "当前停留在最近活动详情页"
    assert result["summary"]["trajectory_last_steps"] == ["点击最近活动入口", "打开活动详情"]
    assert result["summary"]["relevant_information"] == ["存在活动标题", "可见领奖记录入口"]
    assert result["summary"]["extracted_text"] == ["最近活动", "活动详情", "领奖记录"]
    assert model.messages is not None
