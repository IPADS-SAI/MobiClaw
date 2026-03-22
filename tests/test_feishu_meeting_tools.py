from __future__ import annotations

from mobiclaw.tools.feishu import schedule_feishu_meeting, send_feishu_meeting_card


class _DummyResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"
        self.text = ""

    def json(self):  # noqa: ANN201
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error status={self.status_code}")


def test_schedule_feishu_meeting_success(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    calls: list[tuple[str, dict]] = []

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        calls.append((url, kwargs))
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        if "vc/v1/reserves/apply" in url:
            return _DummyResp(
                200,
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "reserve": {
                            "id": "resv_1",
                            "url": "https://vc.feishu.cn/j/123456",
                            "meeting_no": "123456789",
                            "password": "1122",
                            "app_link": "https://applink.feishu.cn/xx",
                        }
                    },
                },
            )
        if "im/v1/messages" in url:
            return _DummyResp(200, {"code": 0, "msg": "success", "data": {"message_id": "om_xxx"}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)

    result = schedule_feishu_meeting(
        topic="项目评审会",
        start_time="2026-03-20 15:30",
        duration_minutes=45,
    )

    assert result.metadata.get("meeting", {}).get("reserve_id") == "resv_1"
    assert result.metadata.get("meeting", {}).get("topic") == "项目评审会"
    assert result.metadata.get("meeting", {}).get("end_time", "").endswith("16:15")
    assert "会议预约成功" in str(result.content[0].get("text") or "")

    assert any("vc/v1/reserves/apply" in url for url, _ in calls)


def test_schedule_feishu_meeting_invalid_start_time() -> None:
    result = schedule_feishu_meeting(
        topic="项目评审会",
        start_time="明天下午三点",
    )

    assert result.metadata.get("error") == "invalid_start_time"


def test_send_feishu_meeting_card_success(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_xxx")

    calls: list[tuple[str, dict]] = []

    def _fake_post(url: str, **kwargs):  # noqa: ANN003, ANN201
        calls.append((url, kwargs))
        if "tenant_access_token" in url:
            return _DummyResp(200, {"code": 0, "tenant_access_token": "t-1"})
        if "im/v1/messages" in url:
            return _DummyResp(200, {"code": 0, "msg": "success", "data": {"message_id": "om_xxx"}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("mobiclaw.tools.feishu.requests.post", _fake_post)

    result = send_feishu_meeting_card(
        receive_id="oc_1234567890abcdef1234567890abcdef",
        receive_id_type="chat_id",
        topic="项目评审会",
        start_time="2026-03-20 15:30",
        end_time="2026-03-20 16:15",
        meeting_url="https://vc.feishu.cn/j/123456",
        meeting_no="123456789",
        password="1122",
    )

    assert result.metadata.get("ok") is True
    assert any("im/v1/messages" in url for url, _ in calls)


def test_send_feishu_meeting_card_invalid_time_range() -> None:
    result = send_feishu_meeting_card(
        receive_id="oc_1234567890abcdef1234567890abcdef",
        receive_id_type="chat_id",
        topic="项目评审会",
        start_time="2026-03-20 16:15",
        end_time="2026-03-20 15:30",
        meeting_url="https://vc.feishu.cn/j/123456",
    )

    assert result.metadata.get("error") == "invalid_time_range"
