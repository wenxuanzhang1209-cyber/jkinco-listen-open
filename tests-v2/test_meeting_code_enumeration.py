"""会议号不得被无限制枚举。

会议号只有 9 亿种组合(900×1000×1000),而它是加入会议的唯一门槛 ——
所有会议接口原先零限流(限流只加在 assistant/export/dingtalk 上),
持续撞号即可找到并进入他人的会议,平台上会议越多越容易。
另外 leave 对非成员返回 200,等于向任何人确认「这个会议号存在」,
与 _require_member「对非成员不确认会议是否存在」的口径相悖。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "devsecret1234567890abcdef")
os.environ.setdefault("JKINCO_GUEST_ACCESS", "1")

import backend.meetings as M
from backend.main import app


def _login(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "123456"}).status_code == 200


def _reset_throttle():
    with M.MEETING_LOOKUP_LOCK:
        M.MEETING_LOOKUP_MISSES.clear()


def test_repeated_wrong_codes_get_throttled():
    _reset_throttle()
    with TestClient(app) as client:
        _login(client)
        codes = [
            client.post(f"/api/meetings/{100 + i:03d}-000-000/join", json={}).status_code
            for i in range(M.MEETING_LOOKUP_MAX_MISSES + 6)
        ]
    assert 429 in codes, f"撞号未被限流:{set(codes)}"
    assert codes.count(404) <= M.MEETING_LOOKUP_MAX_MISSES


def test_normal_join_is_not_throttled():
    """正常用户不该被误伤:没有失败记录时,加入必须畅通。"""
    _reset_throttle()
    with TestClient(app) as client:
        _login(client)
        meeting = client.post("/api/meetings", json={"title": "正常会议"}).json()
        for _ in range(10):
            response = client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={})
            assert response.status_code == 200, response.text[:120]


def test_leave_does_not_confirm_existence_to_non_members():
    _reset_throttle()
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "别人的会议"}).json()
        with TestClient(app) as outsider:
            assert outsider.post("/api/auth/guest", json={}).status_code == 200
            response = outsider.post(f"/api/meetings/{meeting['meeting_code']}/leave", json={})
        assert response.status_code == 404, f"向非成员确认了会议存在:{response.status_code}"


def test_member_can_still_leave():
    """不能矫枉过正:真正的成员必须能正常离开。"""
    _reset_throttle()
    with TestClient(app) as client:
        _login(client)
        meeting = client.post("/api/meetings", json={"title": "我的会议"}).json()
        client.post(f"/api/meetings/{meeting['id']}/join", json={})
        assert client.post(f"/api/meetings/{meeting['id']}/leave", json={}).status_code == 200


def test_throttle_only_counts_failures():
    """只统计失败:成功的查询不能把额度耗光。"""
    _reset_throttle()
    with TestClient(app) as client:
        _login(client)
        meeting = client.post("/api/meetings", json={"title": "会议"}).json()
        for _ in range(M.MEETING_LOOKUP_MAX_MISSES + 10):
            assert client.post(f"/api/meetings/{meeting['id']}/join", json={}).status_code == 200
