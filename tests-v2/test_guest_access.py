"""访客(免注册临时进入)的行为与安全契约。

设计取舍:访客是库内 role='guest' 的**真实账号**,而不是绕开鉴权的旁路。
理由是归属隔离已经统一按 owner_username 判定(会议/历史/任务),让访客成为一个
普通身份,隔离就自动生效;若做成「无账号通道」,每个接口都得再加一套访客分支,
迟早漏掉一个。

本文件锁住四件事:
  1. 访客能进来、能用核心功能;
  2. 访客之间、访客与正式用户之间互相看不见;
  3. 访客不是管理员、不能推钉钉(公司群是对外通道)、凭据短命;
  4. 免注册入口有限流,且过期账号会被清理,不会把用户表撑大。
"""
import base64
import time

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth
import backend.main as main
from jkinco_pipeline import SUMMARY_AND_PUSH, TRANSCRIBE_ONLY
from helpers import solve_captcha


@pytest.fixture(autouse=True)
def _enable_guest(monkeypatch):
    monkeypatch.setattr(main, "GUEST_ACCESS_ENABLED", True)


def _guest() -> tuple[TestClient, dict]:
    client = TestClient(main.app)
    response = client.post("/api/auth/guest")
    assert response.status_code == 200, response.text
    return client, response.json()["user"]


def _registered(username: str) -> TestClient:
    client = TestClient(main.app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        assert client.post("/api/auth/login", json={
            "username": username, "password": "StrongPass123",
        }).status_code == 200
    return client


# ---------- 基本可用性 ----------

def test_guest_can_enter_without_registration():
    client, user = _guest()
    assert user["username"].startswith(auth.GUEST_USERNAME_PREFIX)
    assert user["role"] == "访客"
    assert client.get("/api/auth/me").status_code == 200


def test_guest_can_use_core_features():
    """临时进入要真的能用,否则这个入口没有意义。"""
    client, _ = _guest()
    meeting = client.post("/api/meetings", json={"title": "访客的会议"})
    assert meeting.status_code == 200
    meeting_id = meeting.json()["id"]
    assert client.post(f"/api/meetings/{meeting_id}/join", json={"display_name": "访客"}).status_code == 200
    assert client.get(f"/api/meetings/{meeting_id}/transcript?after=0").status_code == 200
    assert client.get("/api/history").status_code == 200


def test_each_guest_gets_a_distinct_identity():
    _, first = _guest()
    _, second = _guest()
    assert first["username"] != second["username"]


# ---------- 隔离 ----------

def test_guests_cannot_see_each_other():
    """核心安全回归:两个访客之间必须互相不可见。"""
    alice, alice_user = _guest()
    secret = "访客甲的并购底价四千三百万"
    meeting = alice.post("/api/meetings", json={"title": "访客甲的会议"}).json()
    alice.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "甲"})
    alice.post(f"/api/meetings/{meeting['id']}/chat", json={"message": secret})

    bob, _ = _guest()
    for path in (
        f"/api/meetings/{meeting['id']}",
        f"/api/meetings/{meeting['id']}/chat",
        f"/api/meetings/{meeting['id']}/transcript?after=0",
        "/api/history",
        "/api/meetings",
    ):
        response = bob.get(path)
        assert secret not in response.text, f"{path} 泄露了另一位访客的内容"


def test_guest_cannot_see_registered_user_data():
    owner = _registered("guestisolationowner")
    secret = "正式用户的投标策略"
    record_id = main.core.save_meeting_history_record(
        secret, secret, "ok", "general", "测试", owner_username="guestisolationowner",
    )
    guest, _ = _guest()
    assert secret not in guest.get("/api/history").text
    assert guest.get(f"/api/history/{record_id}").status_code == 404
    assert owner.get(f"/api/history/{record_id}").status_code == 200


def test_registered_user_cannot_see_guest_data():
    """反向:正式用户也不应看到访客的数据(除管理员)。"""
    guest, guest_user = _guest()
    secret = "访客留下的内部报价"
    record_id = main.core.save_meeting_history_record(
        secret, secret, "ok", "general", "测试", owner_username=guest_user["username"],
    )
    other = _registered("guestisolationother")
    assert secret not in other.get("/api/history").text
    assert other.get(f"/api/history/{record_id}").status_code == 404


# ---------- 权限边界 ----------

def test_guest_is_never_admin():
    _, user = _guest()
    assert auth.is_admin(user["username"]) is False
    assert auth.is_guest(user["username"]) is True


def test_guest_cannot_push_to_dingtalk():
    """公司钉钉群是对外通道,免注册用户不该能触达。"""
    client, _ = _guest()
    response = client.post("/api/dingtalk/push", json={"summary": "# 内容", "mode": "talk"})
    assert response.status_code == 403
    assert "访客" in response.json()["detail"]


def test_guest_cannot_push_via_the_processing_pipeline(monkeypatch):
    """处理任务内部也会推钉钉 —— 只拦推送接口会被这条链路绕过。"""
    monkeypatch.setattr(main.EXECUTOR, "submit", lambda *args, **kwargs: None)
    client, _ = _guest()
    response = client.post("/api/process", data={
        "live_text": "会议内容", "process_mode": SUMMARY_AND_PUSH,
    })
    assert response.status_code == 403
    # 不推送的模式仍应放行,限制不能误伤正常使用
    assert client.post("/api/process", data={
        "live_text": "会议内容", "process_mode": TRANSCRIBE_ONLY,
    }).status_code == 200


def test_guest_session_is_short_lived():
    """访客凭据有效期必须显著短于正式账号。"""
    assert auth.GUEST_SESSION_TTL < auth.SESSION_TTL
    client = TestClient(main.app)
    response = client.post("/api/auth/guest")
    raw = response.headers["set-cookie"]
    max_age = int(raw.split("Max-Age=", 1)[1].split(";", 1)[0])
    assert max_age == auth.GUEST_SESSION_TTL


def test_guest_password_cannot_be_used_to_log_in():
    """访客口令是不可复现的随机值,只能靠本次 Cookie 进入。"""
    _, user = _guest()
    for guess in ("", "guest", user["username"], "StrongPass123"):
        assert auth.authenticate_user(user["username"], guess) is None


# ---------- 滥用防护 ----------

def test_guest_creation_is_rate_limited(monkeypatch):
    """免注册入口天然是刷号入口,必须限流。

    这里调的是访客自己的旋钮。原先调的是 LOGIN_MAX_FAILURES —— 那时访客接口走的
    是登录失败计数的默认上限,两个旋钮是耦合的(GUEST_MAX_PER_WINDOW 定义了却从未
    被使用)。解耦之后这条用例必须跟着改,否则它测的是一个已经不存在的联动。
    main 是按值导入这个常量的,所以要打在 main 上而不是 auth 上。
    """
    monkeypatch.setattr(main, "GUEST_MAX_PER_WINDOW", 3)
    auth.LOGIN_FAILURES.clear()
    client = TestClient(main.app)
    statuses = [client.post("/api/auth/guest").status_code for _ in range(5)]
    assert 429 in statuses, f"未触发限流:{statuses}"


def test_expired_guests_and_their_data_are_purged():
    """过期访客账号与其历史记录都要清理,否则用户表与历史库无限膨胀。"""
    _, user = _guest()
    username = user["username"]
    main.core.save_meeting_history_record(
        "访客的会议内容", "纪要", "ok", "general", "测试", owner_username=username,
    )

    # 账号仍在有效期内 -> 不该被清理
    # 返回值是被清理的用户名清单(上层据此继续清理其名下的自定义模板)
    assert auth.purge_expired_guests() == []
    assert auth.user_exists(username) is True

    # 把时间推到有效期两倍之后
    removed = auth.purge_expired_guests(now=time.time() + auth.GUEST_SESSION_TTL * 2 + 10)
    assert username in removed
    assert auth.user_exists(username) is False
    remaining = [item.get("owner_username") for item in main.core.load_meeting_history()]
    assert username not in remaining, "访客账号已删除但历史记录成了无主数据"


def test_purge_does_not_touch_registered_users():
    """清理只针对访客,绝不能误删正式账号。"""
    _registered("guestpurgesurvivor")
    auth.purge_expired_guests(now=time.time() + auth.GUEST_SESSION_TTL * 10)
    assert auth.user_exists("guestpurgesurvivor") is True


def test_guest_entry_can_be_disabled(monkeypatch):
    monkeypatch.setattr(main, "GUEST_ACCESS_ENABLED", False)
    response = TestClient(main.app).post("/api/auth/guest")
    assert response.status_code == 403
    assert "未开放" in response.json()["detail"]
