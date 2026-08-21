"""传输层与浏览器侧安全约束。

覆盖:安全响应头、CSP、会话 Cookie 属性、CSRF 前提(SameSite)、会话不可伪造、
登录后会话轮换。这些一旦被误改不会有任何功能报错,只会静默降低防护。
"""
import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.auth as auth
import backend.main as main
from helpers import solve_captcha


@pytest.fixture
def client():
    return TestClient(main.app)


def _login(client: TestClient) -> str:
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": "hdrsuser", "display_name": "hdrsuser", "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        client.post("/api/auth/login", json={"username": "hdrsuser", "password": "StrongPass123"})
    return client.cookies.get(auth.SESSION_COOKIE)


def test_baseline_security_headers_present(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in response.headers["Permissions-Policy"]


def test_csp_is_present_and_restrictive(client):
    """CSP 必须存在,且不得放开脚本执行 —— 会议转写与纪要都是用户/模型产出的文本。"""
    policy = client.get("/api/health").headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    # script-src 绝不能出现 unsafe-inline / unsafe-eval / 通配符
    script_directive = next(part for part in policy.split(";") if part.strip().startswith("script-src"))
    for forbidden in ("'unsafe-inline'", "'unsafe-eval'", "*"):
        assert forbidden not in script_directive, f"script-src 放开了 {forbidden}:{script_directive}"


def test_csp_still_allows_what_the_app_actually_needs(client):
    """CSP 不能把应用自己打死:头像是 data URI,LiveKit 走 blob 与 WebSocket。"""
    policy = client.get("/api/health").headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob:" in policy
    assert "blob:" in next(p for p in policy.split(";") if "media-src" in p)
    assert "wss:" in next(p for p in policy.split(";") if "connect-src" in p)


def test_session_cookie_flags(client):
    _login(client)
    cookie_header = "; ".join(
        value for key, value in client.cookies.items() if key == auth.SESSION_COOKIE
    )
    assert cookie_header, "登录未下发会话 Cookie"
    # httponly / samesite 需从 Set-Cookie 原始头断言
    response = client.post("/api/auth/login", json={"username": "hdrsuser", "password": "StrongPass123"})
    raw = response.headers.get("set-cookie", "")
    assert "httponly" in raw.lower(), f"会话 Cookie 缺少 HttpOnly:{raw}"
    assert "samesite=lax" in raw.lower(), f"会话 Cookie 缺少 SameSite=Lax(CSRF 前提):{raw}"


def test_session_token_cannot_be_forged(client):
    """会话是 HMAC 签名串,改动其中任何一段都必须失效。

    令牌为 用户名:过期时间:口令指纹:签名 四段。口令指纹是后加的(改密码要能踢掉
    其他设备),因此老的三段式令牌也必须被拒 —— 它们无法证明口令未被改过。
    """
    token = _login(client)
    username, expires, fingerprint, signature = token.rsplit(":", 3)

    forged = [
        f"admin:{expires}:{fingerprint}:{signature}",                   # 冒充管理员
        f"{username}:{int(expires) + 86400 * 365}:{fingerprint}:{signature}",  # 延长有效期
        f"{username}:{expires}:{fingerprint}:{'0' * len(signature)}",   # 伪造签名
        f"{username}:{expires}:{'0' * len(fingerprint)}:{signature}",   # 伪造口令指纹
        f"{username}:{expires}:{signature}",                            # 老三段式令牌
        f"{username}:{expires}",                                        # 缺签名
    ]
    for token_value in forged:
        assert auth.verify_session(token_value) is None, f"伪造会话被接受:{token_value[:40]}"


def test_login_issues_a_fresh_session(client):
    """登录必须签发新会话,不能沿用请求里带来的旧值(会话固定)。"""
    first = _login(client)
    response = client.post(
        "/api/auth/login",
        json={"username": "hdrsuser", "password": "StrongPass123"},
        headers={"Cookie": f"{auth.SESSION_COOKIE}=attacker-chosen-value"},
    )
    assert response.status_code == 200
    raw_cookie = response.headers["set-cookie"]
    issued = raw_cookie.split(f"{auth.SESSION_COOKIE}=", 1)[1].split(";", 1)[0]
    assert issued != "attacker-chosen-value", "登录后仍沿用了请求带来的会话值"
    assert auth.verify_session(issued) == "hdrsuser"
    assert first != "attacker-chosen-value"


def test_no_cors_allows_credentialed_cross_origin(client):
    """不得存在放行跨源携带凭据的 CORS 头,否则 SameSite 的 CSRF 防护形同虚设。"""
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    assert "access-control-allow-credentials" not in {k.lower() for k in response.headers}


def test_cross_site_state_changing_request_is_rejected(client):
    _login(client)
    response = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["detail"] == "拒绝跨站请求"


def test_same_origin_state_changing_request_remains_compatible(client):
    _login(client)
    response = client.post(
        "/api/auth/logout",
        headers={"Origin": str(client.base_url).rstrip("/")},
    )
    assert response.status_code == 200


def test_oversized_body_is_rejected_before_parsing(client):
    response = client.post(
        "/api/profile",
        content=b"x",
        headers={"Content-Length": str(auth.MAX_AVATAR_BYTES + 2 * 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "请求内容过大"


def test_realtime_websocket_rejects_cross_site_origin(client):
    _login(client)
    meeting = client.post("/api/meetings", json={"title": "origin-test"}).json()
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(
            f"/api/realtime/asr/{meeting['id']}",
            headers={"Origin": "https://evil.example"},
        ):
            pass
    assert error.value.code == 4403
