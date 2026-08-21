"""限流的客户端 IP 不能由请求方决定。

发现:login_throttle_key 取的是 X-Forwarded-For 的第一段,而 nginx 用的是
$proxy_add_x_forwarded_for —— 它保留请求方自带的 XFF、把真实对端追加在末尾。
于是第一段完全由请求方伪造,限流的键等于攻击者说了算。

经 nginx 打到生产实测:
  固定伪造 IP  9.9.9.9  -> 401 401 401 401 401 429 429 429 429   (第 6 次被锁)
  每次换伪造 IP 10.0.0.x -> 401 401 401 401 401 401 401 401 401   (从不被锁)

受影响的不止登录爆破:免注册访客的开号限流、注册限流复用同一个键,一并失效。

修法是取最后一段 —— 那一段是 nginx 自己写的。nginx 是边缘(前面没有 CDN/WAF),
所以它就是真实客户端地址。同时 nginx 侧改成覆写而非追加,两层都堵上。
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth
import backend.main as main

ADMIN_USERNAME, _, ADMIN_PASSWORD = os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


class _FakeRequest:
    def __init__(self, headers: dict[str, str], peer: str | None = "127.0.0.1"):
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.client = type("Peer", (), {"host": peer})() if peer else None


def test_forged_first_hop_is_ignored():
    """nginx 追加在末尾的那一段才算数。"""
    request = _FakeRequest({"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})
    assert auth.client_ip_for_throttle(request) == "203.0.113.7"


def test_many_forged_hops_still_resolve_to_the_real_peer():
    request = _FakeRequest({"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3, 203.0.113.7"})
    assert auth.client_ip_for_throttle(request) == "203.0.113.7"


def test_falls_back_to_real_ip_then_peer():
    assert auth.client_ip_for_throttle(_FakeRequest({"X-Real-IP": "203.0.113.9"})) == "203.0.113.9"
    assert auth.client_ip_for_throttle(_FakeRequest({}, peer="203.0.113.11")) == "203.0.113.11"
    assert auth.client_ip_for_throttle(_FakeRequest({}, peer=None)) == "unknown"


def test_rotating_forged_ips_cannot_reset_the_login_throttle(monkeypatch):
    """本文件的核心:轮换伪造 IP 必须仍然被同一个计数器拦住。

    请求头按 nginx 的真实行为构造 —— $proxy_add_x_forwarded_for 会把请求方
    自带的 XFF 原样保留、再把真实对端追加在末尾。直接拿 TestClient 发一个只含
    伪造值的 XFF 是不真实的:后端只听得到 nginx 转述的内容,而 uvicorn 只绑在
    回环地址上,不存在绕过 nginx 直连的路径。
    """
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILURES", 5)
    auth.LOGIN_FAILURES.clear()
    real_peer = "203.0.113.7"
    with TestClient(app=main.app) as client:
        statuses = []
        for attempt in range(auth.LOGIN_MAX_FAILURES + 4):
            response = client.post(
                "/api/auth/login",
                json={"username": ADMIN_USERNAME, "password": "definitely-wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{attempt}, {real_peer}"},
            )
            statuses.append(response.status_code)
        assert 429 in statuses, f"轮换伪造 X-Forwarded-For 即可无限次尝试密码: {statuses}"
        assert statuses[-1] == 429, statuses


def test_legitimate_distinct_clients_are_still_throttled_separately():
    """修复不能矫枉过正:真正来自不同客户端的请求仍要各算各的。"""
    auth.LOGIN_FAILURES.clear()
    first = _FakeRequest({"X-Forwarded-For": "9.9.9.9, 203.0.113.7"})
    second = _FakeRequest({"X-Forwarded-For": "9.9.9.9, 203.0.113.8"})
    assert auth.login_throttle_key(first, "u") != auth.login_throttle_key(second, "u")
