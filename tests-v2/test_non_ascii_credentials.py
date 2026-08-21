"""非 ASCII 口令与验证码答案不得让接口崩成 500。

hmac.compare_digest 不接受含非 ASCII 的 str,直接传原始口令会抛
TypeError -> 500。而口令只校验长度(8-128),中文口令完全合法:
用户能注册成功,却会在登录时撞上 500 而不是干净的 401。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import authenticate_user, make_captcha, verify_captcha
from helpers import solve_captcha


@pytest.mark.parametrize("password", ["错误的中文口令", "🔑emoji口令", "混合Ab中文123", "Ünïcödé"])
def test_non_ascii_password_returns_clean_rejection(password):
    """不能抛异常 —— 抛出去就是 500,用户完全不知道发生了什么。"""
    assert authenticate_user("admin", password) is None


def test_correct_password_still_works():
    """不能矫枉过正:正确口令必须照常通过。"""
    assert authenticate_user("admin", "123456") == "admin"


@pytest.mark.parametrize("answer", ["１２", "中文答案", "🙂", "Ünï"])
def test_non_ascii_captcha_answer_is_rejected_not_crashed(answer):
    token = make_captcha()["token"]
    assert verify_captcha(token, answer) is False


def test_captcha_still_accepts_the_right_answer():
    """正确答案仍要通过 —— 收紧不能把功能一起收没了。

    原先这条是从 SVG 的 <text> 里正则出「3 + 5 = ?」再相加。那正是验证码当时
    形同虚设的原因之一(取文字就能算),现在题目改用 7 段数码管的 <path> 画,
    图里没有任何文本节点。改走和真实客户端一样的校验入口求解。
    """
    payload = make_captcha()
    assert verify_captcha(payload["token"], solve_captcha(payload["token"])) is True


def test_login_timing_does_not_reveal_account_existence(monkeypatch):
    """登录耗时不得泄露账号是否存在。

    存在的账号要跑 21 万轮 PBKDF2(实测 31.7ms),不存在的原先直接返回(0.06ms)——
    500 倍的时间差让攻击者能靠计时远程枚举账号,即便两种情况的错误提示完全一致。
    """
    import secrets
    import sqlite3
    import statistics
    import time as _time

    import backend.auth as A

    A.init_profile_db()
    salt = secrets.token_bytes(16)
    with A.PROFILE_DB_LOCK, sqlite3.connect(A.PROFILE_DB) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO platform_users(username,password_hash,password_salt,created_at,role)"
            " VALUES(?,?,?,?,?)",
            ("timinguser", A.hash_password("correctpassword", salt), salt.hex(), _time.time(), "user"),
        )

    def median_ms(username, rounds=9):
        samples = []
        for _ in range(rounds):
            start = _time.perf_counter()
            authenticate_user(username, "someWrongPassword123")
            samples.append((_time.perf_counter() - start) * 1000)
        return statistics.median(samples)

    existing = median_ms("timinguser")
    missing = median_ms("这个账号肯定不存在xyz")
    # 存在的那次本就要几十毫秒,差值控制在其一半以内即无法据此区分
    assert abs(existing - missing) < existing * 0.5, (
        f"耗时差异过大,可据此枚举账号:存在 {existing:.2f}ms / 不存在 {missing:.2f}ms"
    )
