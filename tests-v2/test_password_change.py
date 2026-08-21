"""修改密码的契约。

这个接口的每一条约束都是安全约束,而且都不会以功能故障的形式暴露 —— 漏掉任何
一条,系统照常工作,只是防护静默失效。所以逐条钉住:

1. 必须校验旧口令。会话可能是从别人的电脑上遗留下来的,只凭登录态就允许改密,
   等于把「拿到一次会话」升级成「永久接管账号」;
2. 必须限流。改密要验旧口令,不限流就是一个绕开登录限流的口令爆破入口;
3. 改完必须让其他设备的会话失效 —— 这正是用户改密码的目的;
4. 当前设备不该被自己踢下线,否则改完密码立刻跳登录页,体验上像是失败了。
"""
import base64
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth
import backend.main as main
from helpers import solve_captcha


@pytest.fixture()
def client():
    return TestClient(main.app)


def _register(client: TestClient, password: str = "StrongPass123") -> str:
    """注册一个全新账号并保持登录,返回用户名。"""
    username = "pw" + uuid.uuid4().hex[:10]
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    response = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": password,
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    assert response.status_code in (200, 201), response.text
    return username


def _change(client: TestClient, current: str, new: str):
    return client.post("/api/auth/password", json={"current_password": current, "new_password": new})


def test_password_can_be_changed_and_used_to_log_in(client):
    username = _register(client)
    assert _change(client, "StrongPass123", "BrandNewPass456").status_code == 200

    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": username, "password": "StrongPass123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": username, "password": "BrandNewPass456"}).status_code == 200


def test_wrong_current_password_is_rejected(client):
    """核心:光有会话不够,必须证明你知道当前口令。"""
    username = _register(client)
    response = _change(client, "WrongCurrent999", "BrandNewPass456")
    assert response.status_code == 400
    assert "当前密码" in response.json()["detail"]

    # 口令没有被改动
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": username, "password": "StrongPass123"}).status_code == 200


def test_anonymous_caller_is_rejected(client):
    client.cookies.clear()
    assert _change(client, "StrongPass123", "BrandNewPass456").status_code == 401


def test_new_password_must_differ_from_current(client):
    _register(client)
    response = _change(client, "StrongPass123", "StrongPass123")
    assert response.status_code == 400
    assert "不能与当前密码相同" in response.json()["detail"]


@pytest.mark.parametrize("weak", ["", "short", "1234567"])
def test_new_password_length_is_enforced(client, weak):
    """与注册同一套长度规则,否则会出现「能注册的密码改不了」。"""
    _register(client)
    assert _change(client, "StrongPass123", weak).status_code == 422


def test_other_devices_are_logged_out(client):
    """改密码的典型场景是怀疑账号被盗 —— 旧会话必须立刻失效,否则改了等于没改。"""
    _register(client)
    stolen = client.cookies.get(auth.SESSION_COOKIE)
    assert auth.verify_session(stolen) is not None

    assert _change(client, "StrongPass123", "BrandNewPass456").status_code == 200
    assert auth.verify_session(stolen) is None, "改密后旧会话仍然有效"


def test_current_device_stays_logged_in(client):
    """当前设备要拿到新令牌,不能改完密码就被自己踢到登录页。"""
    _register(client)
    response = _change(client, "StrongPass123", "BrandNewPass456")
    assert response.status_code == 200
    assert auth.SESSION_COOKIE in response.headers.get("set-cookie", "")
    assert client.get("/api/auth/me").status_code == 200


def test_guest_cannot_change_password(client):
    """访客口令是注册时生成的不可复现随机值,本人并不知道。"""
    client.cookies.clear()
    if client.post("/api/auth/guest", json={}).status_code != 200:
        pytest.skip("访客通道未开启")
    response = _change(client, "whatever1", "BrandNewPass456")
    assert response.status_code == 403
    assert "访客" in response.json()["detail"]


def test_repeated_wrong_attempts_are_throttled(client, monkeypatch):
    """不限流的话,这里就是一个绕开登录限流的口令爆破入口。"""
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILURES", 3)
    _register(client)

    seen = [_change(client, "WrongOne111", "BrandNewPass456").status_code for _ in range(6)]
    assert 429 in seen, f"连续猜错未触发限流:{seen}"


def test_successful_change_clears_the_throttle_counter(client, monkeypatch):
    """猜错几次后用正确口令改成功,不该给自己留下未清理的失败计数。"""
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILURES", 5)
    _register(client)

    _change(client, "WrongOne111", "BrandNewPass456")
    assert _change(client, "StrongPass123", "BrandNewPass456").status_code == 200
    # 换回来:若计数没清,这一次会被限流拦住
    assert _change(client, "BrandNewPass456", "ThirdPassword789").status_code == 200


def test_password_is_stored_hashed_with_a_fresh_salt(client):
    """改密必须换新盐,且库里不得出现明文。"""
    import sqlite3

    username = _register(client)
    with sqlite3.connect(auth.PROFILE_DB) as connection:
        before = connection.execute(
            "SELECT password_hash, password_salt FROM platform_users WHERE username=?", (username,)
        ).fetchone()

    assert _change(client, "StrongPass123", "BrandNewPass456").status_code == 200

    with sqlite3.connect(auth.PROFILE_DB) as connection:
        after = connection.execute(
            "SELECT password_hash, password_salt FROM platform_users WHERE username=?", (username,)
        ).fetchone()

    assert after[1] != before[1], "改密沿用了旧盐"
    assert after[0] != before[0]
    assert "BrandNewPass456" not in after[0], "库里出现了明文口令"


def test_session_token_never_carries_the_password_hash(client):
    """令牌发到浏览器,不能让它携带任何可用于离线爆破的原始哈希。"""
    import sqlite3

    username = _register(client)
    token = client.cookies.get(auth.SESSION_COOKIE)
    with sqlite3.connect(auth.PROFILE_DB) as connection:
        stored_hash = connection.execute(
            "SELECT password_hash FROM platform_users WHERE username=?", (username,)
        ).fetchone()[0]

    assert stored_hash not in token
    assert "StrongPass123" not in token


def test_no_builtin_admin_account_when_env_is_absent(monkeypatch):
    """删掉 JKINCO_AUTH 不能凭空长出管理员后门。

    这里的默认值曾经是 "admin:123456"。它走明文比对、完全绕过口令哈希、直接授予
    管理员,且缺省生效时不会有任何报错 —— 只要有人删掉环境变量(而不是置空),
    线上就多一个人尽皆知的后门账号。
    """
    monkeypatch.delenv("JKINCO_AUTH", raising=False)
    assert auth.configured_users() == {}
    assert auth.authenticate_user("admin", "123456") != "admin"


def test_configured_users_still_work_when_explicitly_set(monkeypatch):
    """去掉默认值不能把这个能力一起去掉:显式配置时仍要生效。"""
    monkeypatch.setenv("JKINCO_AUTH", "ops_user:S3cretOps!")
    assert auth.configured_users() == {"ops_user": "S3cretOps!"}
    assert auth.authenticate_user("ops_user", "S3cretOps!") == "ops_user"
    assert auth.authenticate_user("ops_user", "wrong") is None
