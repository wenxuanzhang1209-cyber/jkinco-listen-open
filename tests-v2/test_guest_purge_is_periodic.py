"""过期访客的回收必须是真正周期性的。

发现:purge_expired_guests 只挂在「免注册进入」接口上,而 auth 的文档说的是
「定期清理」。两者并不一致 —— 访客通道一旦没人再来登录,过期账号连同其历史
文件就永远留在库里,而这正是访客用得少的部署最容易出现的情形(生产一个月内
只产生过 1 个访客)。清理挂到小时级清扫线程之后才与文档相符。

登录接口里的那次调用保留:新访客进来正是账号表增长的时刻,顺手回收最及时。
"""
from __future__ import annotations

import inspect
import sqlite3
import time

import backend.auth as auth
import backend.main as main


def _guest_count() -> int:
    with sqlite3.connect(auth.PROFILE_DB) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM platform_users WHERE role=?", (auth.GUEST_ROLE,)
        ).fetchone()[0]


def test_expired_guests_are_reclaimed_without_anyone_logging_in():
    for index in range(3):
        auth.create_guest_account()
    assert _guest_count() >= 3

    # 推到过期界限之外(cutoff 取会话有效期的两倍)
    with sqlite3.connect(auth.PROFILE_DB) as connection:
        connection.execute(
            "UPDATE platform_users SET created_at=? WHERE role=?",
            (time.time() - auth.GUEST_SESSION_TTL * 3, auth.GUEST_ROLE),
        )

    # 关键:不经过 /api/auth/guest,直接跑清扫线程做的那件事
    main._purge_expired_guests_and_their_templates()
    assert _guest_count() == 0


def test_sweeper_thread_actually_runs_the_guest_purge():
    """回收函数存在还不够,必须真的挂在周期线程上。"""
    source = inspect.getsource(main._stale_file_sweeper)
    assert "_purge_expired_guests_and_their_templates" in source


def test_guest_login_still_reclaims_immediately():
    auth.create_guest_account()
    with sqlite3.connect(auth.PROFILE_DB) as connection:
        connection.execute(
            "UPDATE platform_users SET created_at=? WHERE role=?",
            (time.time() - auth.GUEST_SESSION_TTL * 3, auth.GUEST_ROLE),
        )
    stale = _guest_count()
    assert stale >= 1

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        if not main.GUEST_ACCESS_ENABLED:
            return
        assert client.post("/api/auth/guest", json={}).status_code == 200
    # 过期的都被清掉,只剩刚建的这一个
    assert _guest_count() == 1
