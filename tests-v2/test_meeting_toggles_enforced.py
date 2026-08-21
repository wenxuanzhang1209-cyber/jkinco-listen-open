"""会议开关必须在服务端真正生效,不能只落库。

allow_guest 与 allow_screen_share 原先只在建会时写进数据库,加入与令牌签发
都不看它们:
  - 主持人为敏感会议关掉「允许访客」,访客照样能进(实测 200);
  - 关掉「共享屏幕」只是前端隐藏按钮,LiveKit 的发布权限由令牌决定,
    谁都可以直接调 SDK 发起共享。
allow_chat 本来就是校验的,这组测试把三者拉齐。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt as _jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "devsecret1234567890abcdef")
os.environ.setdefault("JKINCO_GUEST_ACCESS", "1")

from backend.main import app


def _login(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "123456"}).status_code == 200


def _video_claims(token):
    return _jwt.decode(token, options={"verify_signature": False}).get("video", {})


def test_guest_cannot_join_when_guests_are_disabled():
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "机密会议", "allow_guest": False}).json()
        with TestClient(app) as guest:
            assert guest.post("/api/auth/guest", json={}).status_code == 200
            response = guest.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "访客甲"})
        assert response.status_code == 403, f"访客进入了不允许访客的会议:{response.status_code}"


def test_guest_can_join_when_guests_are_allowed():
    """不能矫枉过正:允许访客时必须照常放行。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "开放会议", "allow_guest": True}).json()
        with TestClient(app) as guest:
            guest.post("/api/auth/guest", json={})
            response = guest.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "访客甲"})
        assert response.status_code == 200, response.text[:160]


def test_screen_share_is_blocked_in_the_token_for_participants():
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "禁共享", "allow_screen_share": False,
                                                   "allow_guest": True}).json()
        with TestClient(app) as other:
            other.post("/api/auth/guest", json={})
            token = other.post(f"/api/meetings/{meeting['id']}/join", json={}).json()["token"]
        sources = _video_claims(token).get("canPublishSources")
        assert sources, "令牌未限制可发布的源,前端隐藏按钮拦不住直接调 SDK"
        assert "screen_share" not in sources
        # 麦克风与摄像头不能被误伤
        assert "microphone" in sources and "camera" in sources


def test_host_can_still_share_when_disabled():
    """关掉共享后主持人仍要能演示,否则等于把自己也锁住了。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "禁共享", "allow_screen_share": False}).json()
        token = host.post(f"/api/meetings/{meeting['id']}/join", json={}).json()["token"]
        assert not _video_claims(token).get("canPublishSources")


def test_screen_share_unrestricted_when_allowed():
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "可共享", "allow_screen_share": True,
                                                   "allow_guest": True}).json()
        with TestClient(app) as other:
            other.post("/api/auth/guest", json={})
            token = other.post(f"/api/meetings/{meeting['id']}/join", json={}).json()["token"]
        assert not _video_claims(token).get("canPublishSources")


def test_chat_toggle_still_enforced():
    """allow_chat 本来就生效,这条防止将来被改坏。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "禁聊天", "allow_chat": False}).json()
        host.post(f"/api/meetings/{meeting['id']}/join", json={})
        response = host.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "应被拒"})
        assert response.status_code == 403


def test_asr_websocket_rejects_when_transcription_disabled():
    """关闭实时字幕后,服务端必须真的拒绝推流。

    原先这个开关只随加入响应回传(asr_enabled),由前端决定连不连 —— 改过的
    客户端或直接调接口都能绕过,既违背主持人的设置(保密会议不留字幕),
    也会白白产生语音识别的调用费用。
    """
    with TestClient(app) as client:
        _login(client)
        meeting = client.post("/api/meetings", json={"title": "不留字幕",
                                                     "realtime_transcription_enabled": False}).json()
        client.post(f"/api/meetings/{meeting['id']}/join", json={})
        with pytest.raises(Exception) as excinfo:
            with client.websocket_connect(f"/api/realtime/asr/{meeting['id']}?identity=admin") as ws:
                ws.receive_json()
        assert "4403" in str(excinfo.value) or getattr(excinfo.value, "code", None) == 4403


def test_asr_websocket_accepted_when_transcription_enabled():
    """开启时不能误伤:连接必须能建立。"""
    with TestClient(app) as client:
        _login(client)
        meeting = client.post("/api/meetings", json={"title": "留字幕",
                                                     "realtime_transcription_enabled": True}).json()
        client.post(f"/api/meetings/{meeting['id']}/join", json={})
        # 连接建立后会收到开源版「暂未提供实时流式转写」的错误，这里只验证没有被 4403 挡下
        try:
            with client.websocket_connect(f"/api/realtime/asr/{meeting['id']}?identity=admin") as ws:
                ws.receive_json()
        except Exception as error:
            assert "4403" not in str(error), f"开启实时转写却被拒:{error}"


# --- allow_chat 同样必须落到令牌上 ---
# 界面用的 LiveKit VideoConference 自带一个走数据通道的聊天。allow_chat 原先
# 只在 /chat 接口上强制,于是主持人为敏感会议关掉聊天后,那个入口照样能用 ——
# 消息既不入库、也不进审计、更不会进纪要。与共享屏幕同理:发布权限由令牌决定,
# 前端藏按钮拦不住。

def test_chat_is_blocked_in_the_token_for_participants():
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "禁聊天", "allow_chat": False}).json()
        with TestClient(app) as member:
            member.post("/api/auth/guest", json={})
            joined = member.post(
                f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"}
            ).json()
        claims = _video_claims(joined["token"])
        assert claims.get("canPublishData") is False, "关掉聊天后,数据通道仍可发布"
        # 关掉的是聊天,不是音视频 —— 不能连带禁掉说话
        assert claims.get("canPublish") is True


def test_host_can_still_use_data_channel_when_chat_disabled():
    """口径与共享屏幕一致:主持人不受自己设的限制。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "禁聊天", "allow_chat": False}).json()
        joined = host.post(
            f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"}
        ).json()
        assert _video_claims(joined["token"]).get("canPublishData") is True


def test_data_channel_open_when_chat_allowed():
    """不能矫枉过正:允许聊天时数据通道必须照常可用。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "开放聊天", "allow_chat": True}).json()
        with TestClient(app) as member:
            member.post("/api/auth/guest", json={})
            joined = member.post(
                f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"}
            ).json()
        assert _video_claims(joined["token"]).get("canPublishData") is True


@pytest.mark.parametrize("allow_chat", [True, False])
def test_token_and_rest_chat_agree(allow_chat):
    """两条入口口径必须一致,否则用户会遇到「能打字但发不出去」之类的怪事。"""
    with TestClient(app) as host:
        _login(host)
        meeting = host.post("/api/meetings", json={"title": "口径一致", "allow_chat": allow_chat}).json()
        with TestClient(app) as member:
            member.post("/api/auth/guest", json={})
            joined = member.post(
                f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"}
            ).json()
            rest = member.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "测试"})
        token_allows = _video_claims(joined["token"]).get("canPublishData")
        assert token_allows is (rest.status_code == 200) is allow_chat


def test_auto_record_is_rejected_rather_than_silently_ignored():
    """auto_record 是只存不做的字段:全平台没有任何录制实现。

    默默收下等于向用户承诺一个不存在的功能 —— 开完会去找录像才发现没有,
    而那时会议已经结束、无法补救。生产至今该字段为 1 的会议是 0 场,现有界面
    也没有这个开关,所以明确拒绝不影响任何人。
    """
    with TestClient(app) as host:
        _login(host)
        response = host.post("/api/meetings", json={"title": "要录像", "auto_record": True})
        assert response.status_code == 400, "接受了平台并不支持的录制请求"
        assert "录制" in response.json()["detail"]


def test_meetings_without_recording_still_work():
    """不能矫枉过正:不要求录制的会议照常创建。"""
    with TestClient(app) as host:
        _login(host)
        assert host.post("/api/meetings", json={"title": "正常会议"}).status_code == 200
        assert host.post("/api/meetings", json={"title": "显式关闭", "auto_record": False}).status_code == 200
