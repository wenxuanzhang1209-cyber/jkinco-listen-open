"""登录失败节流的回归测试。

原实现在失败记录字典超过容量上限时整体 clear(),把生效中的锁定一并抹掉。
攻击者只要用大量不同的「IP:用户名」组合刷满上限,就能重置目标账号的锁定,
从而绕过暴力破解防护。同时只读探测会留下空条目,正常登录也会撑大字典。
这里锁定两条契约:回收绝不清除生效中的锁定;不产生永久滞留的空条目。
"""
import os
import tempfile

os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-livekit-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "s" * 34)
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://example.invalid/livekit")
os.environ.setdefault("JKINCO_SESSION_SECRET", "t" * 32)
os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-throttle-"))

import pytest

# 直接面向拥有该逻辑的模块,而不是借道 backend.main 的转发。
import backend.auth as auth


@pytest.fixture(autouse=True)
def _clean_state():
    auth.LOGIN_FAILURES.clear()
    yield
    auth.LOGIN_FAILURES.clear()


def test_lockout_engages_after_max_failures():
    key = "1.2.3.4:victim"
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        auth.record_login_failure(key)
    assert auth.login_blocked(key) is False, "未达阈值不应锁定"
    auth.record_login_failure(key)
    assert auth.login_blocked(key) is True


def test_flooding_distinct_keys_cannot_reset_an_active_lockout():
    """核心回归:洪泛不同 key 撑爆容量上限,不得解除已生效的锁定。"""
    victim = "1.2.3.4:victim"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(victim)
    assert auth.login_blocked(victim) is True

    for index in range(auth.LOGIN_FAILURES_MAX_KEYS + 1):
        auth.login_blocked(f"9.9.9.{index % 255}:flood{index}")

    assert auth.login_blocked(victim) is True, "锁定被洪泛重置,暴力破解防护可绕过"


def test_flooding_with_real_failures_cannot_reset_an_active_lockout():
    """更强的变体:洪泛的 key 自身也带失败记录,同样不得解除受害者锁定。"""
    victim = "1.2.3.4:victim"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(victim)

    for index in range(auth.LOGIN_FAILURES_MAX_KEYS + 1):
        auth.record_login_failure(f"7.7.7.{index % 255}:noise{index}")

    assert auth.login_blocked(victim) is True


def test_probing_unknown_keys_leaves_no_residue():
    """从未失败过的 key 不应在字典里留下空条目,否则正常登录也会撑大内存。"""
    for index in range(500):
        assert auth.login_blocked(f"8.8.8.8:probe{index}") is False
    assert len(auth.LOGIN_FAILURES) == 0


def test_expired_failures_are_reclaimed(monkeypatch):
    """超出锁定窗口的记录应随回收清理,并且不再算作锁定。"""
    key = "5.6.7.8:user"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(key)
    assert auth.login_blocked(key) is True

    real_time = auth.time.time
    monkeypatch.setattr(
        auth.time, "time", lambda: real_time() + auth.LOGIN_LOCKOUT_SECONDS + 1
    )
    assert auth.login_blocked(key) is False, "过了锁定窗口应自动解锁"
    assert key not in auth.LOGIN_FAILURES


def test_successful_login_clears_failures():
    key = "1.1.1.1:user"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(key)
    assert auth.login_blocked(key) is True
    auth.clear_login_failures(key)
    assert auth.login_blocked(key) is False


def test_username_rotation_is_capped_by_an_ip_level_gate():
    """限流键含用户名,换用户名就换一个计数器 —— 必须另有一道按 IP 的闸门。

    每次登录尝试无论账号是否存在都要跑满一次 PBKDF2(为消除时序差异,见
    authenticate_user)。生产实测单次 115ms、约 9 请求/秒即可吃满一个核,而
    机器只有两核:没有这道闸门,不带认证的攻击者用一条连接轮换用户名就能把
    CPU 占满,会议与实时转写一起被拖垮。
    """
    ip_key = "203.0.113.7:__login_ip__"
    for _ in range(auth.LOGIN_IP_MAX_ATTEMPTS - 1):
        auth.record_login_failure(ip_key)
    assert auth.login_blocked(ip_key, auth.LOGIN_IP_MAX_ATTEMPTS) is False
    auth.record_login_failure(ip_key)
    assert auth.login_blocked(ip_key, auth.LOGIN_IP_MAX_ATTEMPTS) is True


def test_ip_gate_does_not_affect_other_addresses():
    """闸门是防 CPU 耗尽的,不是第二道密码锁,不能牵连别的地址。"""
    busy = "203.0.113.7:__login_ip__"
    for _ in range(auth.LOGIN_IP_MAX_ATTEMPTS):
        auth.record_login_failure(busy)
    assert auth.login_blocked(busy, auth.LOGIN_IP_MAX_ATTEMPTS) is True
    assert auth.login_blocked("203.0.113.99:__login_ip__", auth.LOGIN_IP_MAX_ATTEMPTS) is False


def test_default_limit_is_unchanged_when_not_specified():
    """login_blocked 新增了 limit 参数,不传时行为必须与原来完全一致。"""
    key = "9.9.9.9:someone"
    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth.record_login_failure(key)
    assert auth.login_blocked(key) is True
    assert auth.login_blocked(key, auth.LOGIN_MAX_FAILURES) is True
