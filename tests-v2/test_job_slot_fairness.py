"""处理队列的公平性契约:任何单个账号都挤不掉别人的处理名额。

发现:槽位原先只有 JOB_CAPACITY 一道全局上限(默认 12)。实测一个免注册访客
连续提交 12 次就能把它占满,此后所有其他用户——包括管理员——提交录音一律
拿到 429「处理任务较多」,等于一个人让整个平台停止处理录音。这不需要恶意,
一个人手里有十几段录音、或者页面卡住连点几次就会发生。

修法是两级配额:全局一级仍然保护整机不过载,新增按账号一级保证余量。访客的
配额比正式账号更紧,因为访客免注册、单 IP 能连开数个,按账号计对它约束有限。

本文件锁住三件事:单账号占不满全局、正式账号在访客打满后仍能提交、槽位在
成功/异常/入池失败三条路径上都如实归还(泄漏一次就永久少一个名额)。
"""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")

# 线程池只有两个 worker。若用 sleep 占位,占住的 worker 会拖到下一个用例,
# 后续任务在池里排队、永远不执行,于是槽位「看起来」泄漏 —— 那是用例之间的
# 污染,不是产品缺陷。改用可唤醒的事件,收尾时一次放行。
RELEASE_HELD_JOBS = threading.Event()


def _submit(client: TestClient, tag: str):
    return client.post("/api/process", data={
        "live_text": f"占位文本 {tag}", "process_mode": "只转写，不推送", "app_mode": "auto",
    })


def _hold(job_id, audio_path, live_text, process_mode, app_mode, username, *args, **kwargs):
    """占住槽位直到收尾放行,并按真实代码的写法在 finally 里归还。"""
    try:
        RELEASE_HELD_JOBS.wait(30)
    finally:
        main.release_job_slot(username)


def _free_global_slots() -> int:
    """当前还能取到几个全局槽位(取完立刻还回去,不改变状态)。"""
    taken = 0
    while main.JOB_CAPACITY.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        main.JOB_CAPACITY.release()
    return taken


def _wait_for_idle(timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not main.JOB_SLOTS_BY_USER and _free_global_slots() == main.MAX_PENDING_JOBS:
            return
        time.sleep(0.05)


@pytest.fixture
def idle_queue():
    """用例前后队列都必须是空的,否则用例之间会互相污染。"""
    RELEASE_HELD_JOBS.clear()
    _wait_for_idle()
    assert _free_global_slots() == main.MAX_PENDING_JOBS, "用例开始时队列就不干净"
    assert not main.JOB_SLOTS_BY_USER
    yield
    RELEASE_HELD_JOBS.set()
    _wait_for_idle()
    assert not main.JOB_SLOTS_BY_USER, f"用例结束仍有槽位未归还: {main.JOB_SLOTS_BY_USER}"
    assert _free_global_slots() == main.MAX_PENDING_JOBS


def test_single_account_cannot_exhaust_the_queue(idle_queue):
    with TestClient(app) as hog, patch.object(main, "run_processing_job", _hold):
        hog.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        codes = [_submit(hog, str(i)).status_code for i in range(main.MAX_PENDING_JOBS + 2)]
        accepted = codes.count(200)
        assert accepted == main.MAX_PENDING_JOBS_PER_USER, codes
        assert accepted < main.MAX_PENDING_JOBS, "单账号不该能占满全局队列"
        # 关键:别人此刻还有名额。修复前这里是 0,所有人一律 429。
        assert _free_global_slots() == main.MAX_PENDING_JOBS - accepted


def test_guests_cannot_crowd_out_registered_users(idle_queue):
    """访客把单 IP 的开号额度用满,也吃不掉正式账号的名额。"""
    if not main.GUEST_ACCESS_ENABLED:
        pytest.skip("访客通道未开放")
    with patch.object(main, "run_processing_job", _hold):
        for _ in range(8):
            client = TestClient(app)
            client.__enter__()
            try:
                if client.post("/api/auth/guest", json={}).status_code != 200:
                    break  # 开号已被限流,这正是设计意图
                for i in range(4):
                    _submit(client, str(i))
            finally:
                client.__exit__(None, None, None)

        assert sum(main.JOB_SLOTS_BY_USER.values()) < main.MAX_PENDING_JOBS, "访客小号仍能占满全局队列"
        with TestClient(app) as staff:
            staff.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
            assert _submit(staff, "正式账号").status_code == 200


@pytest.mark.parametrize("outcome", ["completed", "raised"])
def test_slots_are_returned_on_every_path(idle_queue, outcome):
    """槽位泄漏是不可逆的:漏一次就永久少一个名额,直到进程重启。"""
    def worker(job_id, audio_path, live_text, process_mode, app_mode, username, *args, **kwargs):
        try:
            if outcome == "raised":
                raise RuntimeError("模拟处理失败")
        except Exception:
            pass
        finally:
            main.release_job_slot(username)

    with TestClient(app) as client, patch.object(main, "run_processing_job", worker):
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        for i in range(main.MAX_PENDING_JOBS_PER_USER * 8):
            _submit(client, str(i))
            time.sleep(0.02)

    _wait_for_idle()
    assert _free_global_slots() == main.MAX_PENDING_JOBS
    # 归零即删键:访客用户名各不相同,留着会随进程运行时间无限增长。
    assert not main.JOB_SLOTS_BY_USER


def test_slot_returned_when_submission_to_pool_fails(idle_queue):
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        with patch.object(main.EXECUTOR, "submit", side_effect=RuntimeError("池已关闭")):
            assert _submit(client, "入池失败").status_code == 503
    assert _free_global_slots() == main.MAX_PENDING_JOBS
    assert not main.JOB_SLOTS_BY_USER
