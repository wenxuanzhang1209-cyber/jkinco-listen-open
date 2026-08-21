"""会议号撞了要换一个,而不是抛一个没人看得懂的 500。

meeting_code 是 900×1000×1000 ≈ 9 亿种组合的随机三段号,表上有 UNIQUE 约束。
几百场会时碰撞概率可以忽略 —— 但会议号**永不释放**:重复会议长期持有自己的号,
已结束、已取消的也都保留。数量只增不减,碰撞概率随时间单调上升。

原先撞上时直接抛出未捕获的 sqlite3.IntegrityError,用户拿到一个没有任何信息的
500。这是典型的长期问题:现在看不见,将来偶发一次,还查不出原因 —— 而修法只是
换个号重试。

连续 3 次都撞意味着号段确实快满了(或随机源坏了),此时报 503 比无限重试好:
前者能被监控看见,后者只会把请求线程耗在里面。
"""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meetings
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = meetings.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


@pytest.fixture
def client():
    with TestClient(app) as session:
        session.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        yield session


def test_a_collision_is_retried_with_a_new_code(client):
    """核心回归:第二场撞号后自动换号,用户无感。"""
    codes = iter(["101-202-303", "101-202-303", "404-505-606", "707-808-909"])
    with patch.object(meetings, "_new_meeting_code", lambda: next(codes)):
        first = client.post("/api/meetings", json={"title": "第一场"})
        second = client.post("/api/meetings", json={"title": "撞号的第二场"})

    assert first.status_code == 200 and first.json()["meeting_code"] == "101-202-303"
    assert second.status_code == 200, f"撞号后没能换号:{second.text[:120]}"
    assert second.json()["meeting_code"] == "404-505-606"


def test_exhausting_the_retries_reports_a_readable_error(client):
    """一直撞不出新号时,要给一个能看懂、能被监控识别的错误。"""
    with patch.object(meetings, "_new_meeting_code", lambda: "111-222-333"):
        client.post("/api/meetings", json={"title": "占位"})
        blocked = client.post("/api/meetings", json={"title": "必然撞满"})
    assert blocked.status_code == 503, f"应为 503,实际 {blocked.status_code}"
    assert "会议号" in blocked.json()["detail"]


def test_the_raw_integrity_error_never_reaches_the_client(client):
    """底线:任何情况下都不该把 sqlite 的约束错误直接抛给用户。"""
    with patch.object(meetings, "_new_meeting_code", lambda: "121-222-323"):
        client.post("/api/meetings", json={"title": "占位"})
        response = client.post("/api/meetings", json={"title": "撞号"})
    assert "IntegrityError" not in response.text
    assert "UNIQUE constraint" not in response.text


def test_generated_codes_look_right():
    for _ in range(200):
        code = meetings._new_meeting_code()
        assert re.fullmatch(r"\d{3}-\d{3}-\d{3}", code), code
        assert int(code[:3]) < 900


def test_codes_are_actually_random():
    """自检:生成函数若退化成常量,上面所有用例都会变成恒真。"""
    assert len({meetings._new_meeting_code() for _ in range(200)}) > 150


def test_retry_bound_is_small_but_not_one():
    """1 次等于没有重试;过大则把请求线程耗在里面。"""
    assert 2 <= meetings.MEETING_CODE_MAX_ATTEMPTS <= 5
