"""会议数据隔离契约。

发现:会议详情/转写/纪要/聊天原先只校验登录、不校验成员归属,
任何登录用户凭会议号即可读取他人会议全文并篡改纪要(实测 7/7 接口失守)。
本测试锁死修复,同时确保邀请加入流程不被误伤。
"""
import base64
import os
import tempfile

os.environ.setdefault("JKINCO_AUTH", "admin:123456")
os.environ.setdefault("JKINCO_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-iso-"))
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret-at-least-thirty-two-characters")
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://meet.example.com/livekit")

from fastapi.testclient import TestClient

from backend.main import app
from helpers import solve_captcha


def _register(client, username):
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    return client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })


def _make_meeting(client, title="隔离验证会议"):
    meeting = client.post("/api/meetings", json={"title": title}).json()
    client.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "主持人"})
    client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "内部报价信息"})
    client.patch(f"/api/meetings/{meeting['id']}/minutes", json={"content_markdown": "机密纪要"})
    return meeting


def test_non_member_cannot_read_or_modify_meeting():
    with TestClient(app) as owner:
        owner.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        meeting = _make_meeting(owner)
        mid = meeting["id"]

    with TestClient(app) as intruder:
        assert _register(intruder, "iso_intruder").status_code == 201
        # 读:一律 404(不确认会议是否存在,避免按会议号枚举)
        for path in ("", "/transcript", "/record", "/minutes", "/chat"):
            r = intruder.get(f"/api/meetings/{mid}{path}")
            assert r.status_code == 404, f"GET {path} 未拦截:{r.status_code}"
            assert "机密" not in r.text and "报价" not in r.text
        # 写:发聊天与改纪要都必须拒绝
        assert intruder.post(f"/api/meetings/{mid}/chat", json={"message": "越权"}).status_code == 404
        assert intruder.patch(f"/api/meetings/{mid}/minutes",
                              json={"content_markdown": "篡改"}).status_code == 404


def test_invited_member_keeps_full_access():
    """凭会议号加入是邀请流程本身,加入后必须能正常使用全部功能。"""
    with TestClient(app) as owner:
        owner.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        meeting = _make_meeting(owner, "邀请流程验证")

    with TestClient(app) as guest:
        assert _register(guest, "iso_invited").status_code == 201
        # 加入前不可见
        assert guest.get(f"/api/meetings/{meeting['id']}").status_code == 404
        # 凭会议号加入(邀请流程)
        joined = guest.post(f"/api/meetings/{meeting['meeting_code']}/join",
                            json={"display_name": "受邀者"})
        assert joined.status_code == 200, "邀请加入被误伤"
        # 加入后全部可用
        for path in ("", "/transcript", "/record", "/minutes", "/chat"):
            assert guest.get(f"/api/meetings/{meeting['id']}{path}").status_code == 200, f"加入后 {path} 仍不可用"
        assert guest.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "大家好"}).status_code == 200


def test_admin_can_access_any_meeting():
    """管理员保留全平台可见性,与历史记录的口径一致。"""
    with TestClient(app) as guest:
        assert _register(guest, "iso_owner").status_code == 201
        meeting = guest.post("/api/meetings", json={"title": "普通用户的会议"}).json()

    with TestClient(app) as admin:
        admin.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert admin.get(f"/api/meetings/{meeting['id']}").status_code == 200
