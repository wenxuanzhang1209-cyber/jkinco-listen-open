"""会议列表的状态过滤与可见性契约。

发现:列表 SQL 的可见性三分支(创建者 / 主持人 / 曾参与)未加括号,而 SQL 里
AND 的优先级高于 OR。追加的 " AND m.status=?" 因此只作用在最后一个 OR 分支上,
导致 ?status=xxx 完全失效(实测三种取值都返回全部会议)。

本测试同时锁死:加括号后过滤生效,且可见性范围一点没放宽。
"""
import base64
import os
import tempfile

# 注册接口按 IP 限流(默认每分钟 5 次),本文件要建多个用户,放宽阈值;
# 注册限流本身由 test_login_throttle.py 单独覆盖。
os.environ.setdefault("JKINCO_LOGIN_MAX_FAILURES", "50")
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "s" * 34)
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://example.invalid/livekit")
os.environ.setdefault("JKINCO_SESSION_SECRET", "t" * 32)
_WORK_DIR = tempfile.mkdtemp(prefix="jkinco-listfilter-")
os.environ["JKINCO_HISTORY_DIR"] = _WORK_DIR
os.environ.setdefault("JKINCO_MEETING_DB", os.path.join(_WORK_DIR, "meetings.db"))

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meetings
from backend.main import app
from helpers import solve_captcha


PASSWORD = "StrongPass123"


def _client(username: str) -> TestClient:
    """返回已登录的客户端。用户库在同一次 pytest 运行内是共享的,
    同名用户第二次注册会失败,这里退回登录,保证每个用例都拿到会话。"""
    client = TestClient(app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": PASSWORD,
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        login = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        assert login.status_code == 200, f"{username} 既无法注册也无法登录:{registered.text} / {login.text}"
    return client


@pytest.fixture
def owner_with_three_states():
    client = _client("filterowner")
    ids = {}
    for status, title in (("active", "进行中"), ("ended", "已结束"), ("scheduled", "已预约")):
        meeting_id = client.post("/api/meetings", json={"title": title}).json()["id"]
        ids[status] = meeting_id
        with meetings.db() as connection:
            connection.execute("UPDATE meetings SET status=? WHERE id=?", (status, meeting_id))
    return client, ids


@pytest.mark.parametrize("wanted", ["active", "ended", "scheduled"])
def test_status_filter_returns_only_that_status(owner_with_three_states, wanted):
    client, _ = owner_with_three_states
    items = client.get(f"/api/meetings?status={wanted}").json()["items"]
    assert items, f"status={wanted} 应至少返回一场"
    assert {item["status"] for item in items} == {wanted}, "状态过滤未生效"


def test_no_status_returns_all_own_meetings(owner_with_three_states):
    """不传 status 时行为不变:返回自己全部会议。"""
    client, ids = owner_with_three_states
    returned = {item["id"] for item in client.get("/api/meetings").json()["items"]}
    assert set(ids.values()) <= returned


def test_filter_does_not_widen_visibility(owner_with_three_states):
    """加括号不能顺带放宽可见性:他人会议在任何 status 下都不出现。"""
    _, ids = owner_with_three_states
    stranger = _client("filterstranger")
    for status in ("active", "ended", "scheduled", ""):
        url = f"/api/meetings?status={status}" if status else "/api/meetings"
        returned = {item["id"] for item in stranger.get(url).json()["items"]}
        assert not (returned & set(ids.values())), f"status={status!r} 时泄露了他人会议"


def test_participant_sees_joined_meeting_under_matching_status():
    """曾参与者这一可见性分支在带 status 过滤时同样要保留。"""
    host = _client("filterhost")
    meeting = host.post("/api/meetings", json={"title": "邀请会议"}).json()
    guest = _client("filterguest")
    joined = guest.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "来宾"})
    assert joined.status_code == 200

    with meetings.db() as connection:
        connection.execute("UPDATE meetings SET status='active' WHERE id=?", (meeting["id"],))

    returned = {item["id"] for item in guest.get("/api/meetings?status=active").json()["items"]}
    assert meeting["id"] in returned, "参与者应能在过滤结果里看到自己加入过的会议"

    ended = {item["id"] for item in guest.get("/api/meetings?status=ended").json()["items"]}
    assert meeting["id"] not in ended
