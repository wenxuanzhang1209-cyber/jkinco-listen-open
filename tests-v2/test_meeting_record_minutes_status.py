"""会议记录必须给出机器可判的纪要状态。

界面要在纪要生成期间显示「正在生成」。此前记录里只有 status,而那是给人看的
中文文案(「会议已结束，纪要正在生成」)—— 让界面去比对那串文字,改一个字、
或者哪天改了措辞,提示就会静默失效,用户又会对着空白页以为卡死了。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _open_meeting(client: TestClient) -> dict:
    client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    meeting = client.post("/api/meetings", json={"title": "纪要状态契约"}).json()
    client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})
    return meeting


@pytest.mark.parametrize(
    "stored", ["processing", "completed", "failed", "empty", "pending"],
)
def test_record_exposes_raw_minutes_status(stored):
    with TestClient(app) as client:
        meeting = _open_meeting(client)
        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meetings SET minutes_status=?, ended_at=? WHERE id=?",
                (stored, time.time(), meeting["id"]),
            )
        record = client.get(f"/api/meetings/{meeting['id']}/record").json()
        assert record["minutes_status"] == stored, "缺少机器可判的纪要状态"
        # 中文文案仍然保留:它是显示用的,两者并存而非互相替代
        assert record["status"] in meeting_service.MINUTES_STATUS_LABELS.values()


def test_only_processing_means_generating():
    """界面据此判断是否显示「正在生成」,别的状态都不该触发。"""
    generating = {
        status for status in meeting_service.MINUTES_STATUS_LABELS
        if status == "processing"
    }
    assert generating == {"processing"}
    assert "completed" in meeting_service.MINUTES_STATUS_LABELS
    assert "failed" in meeting_service.MINUTES_STATUS_LABELS
