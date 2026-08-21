"""水平越权总表:用户 A 是否能触及用户 B 的任何资源。

这是一张覆盖表,而不是针对某个已知缺陷的回归。它遍历所有会返回用户数据的接口,
用两个真实账号做实际请求,断言 B 的机密串一个字都不会出现在 A 的响应里。
新增此类接口时,应在 CASES 里补一行。
"""
import base64
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main as main
import backend.meetings as meetings
from helpers import solve_captcha

# B 的机密内容。任何接口只要在 A 的响应里带出其中任一串,即为越权。
SECRETS = {
    "chat": "投标底价七千二百万元",
    "minutes": "内部授标策略不得外泄",
    "history": "并购对价谈判纪要",
    "job": "候选人薪资期望四十五万",
}

ADMIN_USERNAME, _, ADMIN_PASSWORD = __import__("os").environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _client(username: str) -> TestClient:
    client = TestClient(main.app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        login = client.post("/api/auth/login", json={"username": username, "password": "StrongPass123"})
        assert login.status_code == 200, login.text
    return client


@pytest.fixture(scope="module")
def victim():
    """用户 B 建一场会、发言、存纪要、留历史与任务,全部带机密串。"""
    suffix = uuid.uuid4().hex[:6]
    bob = _client(f"victim{suffix}")

    meeting = bob.post("/api/meetings", json={"title": "B 的保密会议"}).json()
    bob.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "B"})
    bob.post(f"/api/meetings/{meeting['id']}/chat", json={"message": SECRETS["chat"]})
    bob.patch(f"/api/meetings/{meeting['id']}/minutes", json={"content_markdown": SECRETS["minutes"]})

    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meeting_transcript_segments (id,meeting_id,participant_identity,sentence_id,
               start_time_ms,end_time_ms,text,is_final,provider,deduplication_key,created_at)
               VALUES (?,?,'B',1,0,900,?,1,'test',?,?)""",
            (uuid.uuid4().hex, meeting["id"], SECRETS["chat"], uuid.uuid4().hex, 0),
        )

    record_id = main.core.save_meeting_history_record(
        SECRETS["history"], SECRETS["history"], "ok", "general", "测试",
        owner_username=f"victim{suffix}",
    )
    job_id = f"job-{suffix}"
    main.set_job(job_id, owner_username=f"victim{suffix}", status="completed",
                 result={"transcript": SECRETS["job"], "summary": SECRETS["job"]})

    return {"meeting": meeting, "record_id": record_id, "job_id": job_id, "username": f"victim{suffix}"}


@pytest.fixture(scope="module")
def attacker():
    return _client(f"attacker{uuid.uuid4().hex[:6]}")


def _paths(victim):
    meeting_id = victim["meeting"]["id"]
    code = victim["meeting"]["meeting_code"]
    return [
        ("GET", f"/api/meetings/{meeting_id}", None),
        ("GET", f"/api/meetings/{code}", None),           # 凭会议号直接访问
        ("GET", f"/api/meetings/{meeting_id}/transcript?after=0", None),
        ("GET", f"/api/meetings/{meeting_id}/chat", None),
        ("GET", f"/api/meetings/{meeting_id}/minutes", None),
        ("GET", f"/api/meetings/{meeting_id}/record", None),
        ("GET", f"/api/history/{victim['record_id']}", None),
        ("GET", "/api/history", None),
        ("GET", "/api/meetings", None),
        ("GET", f"/api/jobs/{victim['job_id']}", None),
        ("POST", f"/api/meetings/{meeting_id}/chat", {"message": "入侵消息"}),
        ("PATCH", f"/api/meetings/{meeting_id}/minutes", {"content_markdown": "篡改纪要"}),
        ("PATCH", f"/api/meetings/{meeting_id}/schedule", {
            "scheduled_start_at": 4_000_000_000,
            "scheduled_end_at": 4_000_003_600,
            "scope": "series",
        }),
        ("POST", f"/api/meetings/{meeting_id}/end", None),
        ("POST", f"/api/meetings/{meeting_id}/lock", None),
    ]


def test_attacker_never_sees_victim_secrets(attacker, victim):
    """总表:遍历所有接口,B 的机密一个字都不能出现在 A 的响应里。"""
    leaks = []
    for method, path, body in _paths(victim):
        response = attacker.request(method, path, json=body) if body else attacker.request(method, path)
        for name, secret in SECRETS.items():
            if secret in response.text:
                leaks.append(f"{method} {path} 泄露了 {name}(HTTP {response.status_code})")
    assert not leaks, "存在水平越权:\n" + "\n".join(leaks)


def test_attacker_cannot_mutate_victim_meeting(attacker, victim):
    """写操作必须被拒,且不能真的改到 B 的数据。"""
    meeting_id = victim["meeting"]["id"]
    for method, path, body in _paths(victim):
        if method == "GET":
            continue
        response = attacker.request(method, path, json=body) if body else attacker.request(method, path)
        assert response.status_code in (403, 404), f"{method} {path} 返回 {response.status_code},未拒绝写操作"

    with meetings.db() as connection:
        chats = connection.execute(
            "SELECT message FROM meeting_chat_messages WHERE meeting_id=?", (meeting_id,)
        ).fetchall()
        latest = connection.execute(
            "SELECT content_markdown FROM meeting_minutes_versions WHERE meeting_id=? ORDER BY version DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
        status = connection.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()[0]

    assert all("入侵消息" not in row[0] for row in chats), "攻击者的消息被写入了 B 的会议"
    assert latest[0] == SECRETS["minutes"], "B 的纪要被篡改"
    assert status != "ended", "B 的会议被他人结束"


def test_anonymous_is_rejected_everywhere(victim):
    """未登录访问同一批接口,一律 401,且不泄露任何内容。"""
    anonymous = TestClient(main.app)
    for method, path, body in _paths(victim):
        response = anonymous.request(method, path, json=body) if body else anonymous.request(method, path)
        assert response.status_code == 401, f"{method} {path} 未登录返回 {response.status_code}"
        assert not any(secret in response.text for secret in SECRETS.values())


def test_owner_still_has_full_access(victim):
    """反向验证:隔离不能把资源所有者一起挡住。"""
    owner = _client(victim["username"])
    meeting_id = victim["meeting"]["id"]
    assert SECRETS["chat"] in owner.get(f"/api/meetings/{meeting_id}/chat").text
    assert SECRETS["minutes"] in owner.get(f"/api/meetings/{meeting_id}/minutes").text
    assert SECRETS["history"] in owner.get(f"/api/history/{victim['record_id']}").text
    assert SECRETS["job"] in owner.get(f"/api/jobs/{victim['job_id']}").text


def test_admin_retains_oversight(victim):
    """管理员的可见性是既定业务规则,一并固定住,避免被误改。"""
    admin = TestClient(main.app)
    assert admin.post("/api/auth/login", json={
        "username": ADMIN_USERNAME, "password": ADMIN_PASSWORD,
    }).status_code == 200
    assert SECRETS["history"] in admin.get(f"/api/history/{victim['record_id']}").text
    assert SECRETS["job"] in admin.get(f"/api/jobs/{victim['job_id']}").text
