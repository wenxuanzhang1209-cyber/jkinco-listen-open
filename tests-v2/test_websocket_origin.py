"""实时转写 WebSocket 的同源校验契约。

浏览器不对 WebSocket 施加同源限制,任何网站都能向我们的 ws 端点发起连接并带上
用户的 cookie。所以这道 Origin 校验是实打实的 CSRF 防线,不能松。

但它也不能过紧到把正常连接一起拒掉:Origin 头永远是 http/https,而连接自身的
scheme 是 ws/wss,直接相等比较永远不成立。生产上能工作完全依赖 nginx 注入的
x-forwarded-proto —— 这个头一旦缺失(直连、本地起服务、换网关),所有实时转写
会被静默 403 拒绝,且没有任何日志能指向真正的原因。

下面同时钉住两侧:该拒的必须拒,该放的必须放。
"""
import pytest

from backend.meetings import _websocket_origin_matches_host


class _FakeURL:
    def __init__(self, scheme):
        self.scheme = scheme


class _FakeWebSocket:
    """只提供被校验函数用到的两样东西:请求头与连接 scheme。"""

    def __init__(self, scheme="ws", **headers):
        self.url = _FakeURL(scheme)
        self.headers = {key.replace("_", "-").lower(): value for key, value in headers.items()}


def test_same_origin_over_plain_ws_is_allowed_without_any_proxy_header():
    """核心修复:没有 x-forwarded-proto 时,ws 也要能识别出与 http 同源。"""
    ws = _FakeWebSocket("ws", origin="http://127.0.0.1:8090", host="127.0.0.1:8090")
    assert _websocket_origin_matches_host(ws) is True


def test_same_origin_over_wss_is_allowed_without_any_proxy_header():
    ws = _FakeWebSocket("wss", origin="https://local.test", host="local.test")
    assert _websocket_origin_matches_host(ws) is True


def test_proxy_terminated_tls_still_works():
    """生产形态:nginx 终止 TLS,到应用这一跳是明文 ws,靠 x-forwarded-proto 还原。

    这是改动前唯一能工作的路径,必须保持不变。
    """
    ws = _FakeWebSocket(
        "ws",
        origin="https://local.test",
        host="local.test",
        x_forwarded_proto="https",
    )
    assert _websocket_origin_matches_host(ws) is True


def test_forwarded_proto_with_multiple_hops_takes_the_first():
    """多层代理时该头是逗号分隔的链,取最靠近客户端的那一跳。"""
    ws = _FakeWebSocket(
        "ws",
        origin="https://local.test",
        host="local.test",
        x_forwarded_proto="https, http",
    )
    assert _websocket_origin_matches_host(ws) is True


def test_missing_origin_is_allowed():
    """非浏览器客户端不发 Origin。放行是刻意的 —— 这道防线针对的是浏览器
    跨站发起的连接,而攻击者能构造的恰恰只有带 Origin 的那种。"""
    assert _websocket_origin_matches_host(_FakeWebSocket("ws", host="127.0.0.1:8090")) is True


# ── 以下是必须继续拒绝的 ──

def test_cross_site_origin_is_rejected():
    ws = _FakeWebSocket("ws", origin="http://evil.example", host="127.0.0.1:8090")
    assert _websocket_origin_matches_host(ws) is False


def test_cross_site_origin_is_rejected_even_over_wss():
    ws = _FakeWebSocket("wss", origin="https://evil.example", host="local.test")
    assert _websocket_origin_matches_host(ws) is False


def test_scheme_downgrade_is_rejected():
    """https 页面不该通过明文连接进来 —— 放行等于接受降级。"""
    ws = _FakeWebSocket("ws", origin="https://local.test", host="local.test")
    assert _websocket_origin_matches_host(ws) is False


def test_scheme_upgrade_mismatch_is_rejected():
    ws = _FakeWebSocket("wss", origin="http://local.test", host="local.test")
    assert _websocket_origin_matches_host(ws) is False


def test_host_with_different_port_is_rejected():
    """同域不同端口仍是跨源。"""
    ws = _FakeWebSocket("ws", origin="http://127.0.0.1:9999", host="127.0.0.1:8090")
    assert _websocket_origin_matches_host(ws) is False


def test_subdomain_is_not_the_same_origin():
    ws = _FakeWebSocket("wss", origin="https://evil.local.test", host="local.test")
    assert _websocket_origin_matches_host(ws) is False


@pytest.mark.parametrize("origin", ["file://", "javascript:alert(1)", "null", "", "   ", "chrome-extension://abc"])
def test_non_http_origins_are_rejected(origin):
    """只认 http/https。空串走「未提供 Origin」分支,其余一律拒绝。"""
    ws = _FakeWebSocket("ws", origin=origin, host="127.0.0.1:8090")
    expected = True if not origin.strip() else False
    assert _websocket_origin_matches_host(ws) is expected


def test_host_comparison_is_case_insensitive():
    ws = _FakeWebSocket("wss", origin="https://Local.Test", host="local.test")
    assert _websocket_origin_matches_host(ws) is True
