"""处理任务的归属隔离契约。

发现:/api/jobs/{job_id} 原先只校验登录,不校验任务归属。任务结果里带着
完整转写与纪要正文,任何登录用户只要拿到任务 id(日志、浏览器历史、截图、
转发的链接)就能读到他人的会议全文。归属口径与历史记录保持一致:本人可读、
管理员可读、其余按「不存在」处理(返回 404,不确认任务是否存在)。
"""
import base64
import os
import tempfile

# 注册接口按 IP 限流(默认每分钟 5 次),本文件要建多个用户,放宽阈值;
# 注册限流本身由 test_login_throttle.py 单独覆盖。
os.environ.setdefault("JKINCO_LOGIN_MAX_FAILURES", "50")
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "s" * 34)
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://example.invalid/livekit")
os.environ.setdefault("JKINCO_SESSION_SECRET", "t" * 32)
os.environ.setdefault("JKINCO_AUTH", "admin:AdminPass123")
os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-jobs-"))

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from helpers import solve_captcha

SECRET = "本次投标底价七千二百万元"

# JKINCO_AUTH 由最先导入的测试文件用 setdefault 抢占,这里不能硬编码口令,
# 必须从最终生效的环境变量里解析,否则全量运行时会因文件顺序而失败。
ADMIN_USERNAME, _, ADMIN_PASSWORD = os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _register(client: TestClient, username: str):
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    return client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })


@pytest.fixture
def owner_job():
    job_id = "job-under-test"
    main.set_job(
        job_id, owner_username="jobowner", status="completed", stage="push", progress=100,
        message="报告已生成", result={"transcript": SECRET, "summary": f"# 纪要\n{SECRET}"},
    )
    yield job_id
    with main.JOBS_LOCK:
        main.JOBS.pop(job_id, None)


def test_owner_can_read_own_job(owner_job):
    client = TestClient(main.app)
    _register(client, "jobowner")
    response = client.get(f"/api/jobs/{owner_job}")
    assert response.status_code == 200
    assert SECRET in response.text


def test_other_user_cannot_read_someone_elses_job(owner_job):
    """核心回归:他人任务按不存在处理,正文一个字都不能出去。"""
    client = TestClient(main.app)
    _register(client, "jobstranger")
    response = client.get(f"/api/jobs/{owner_job}")
    assert response.status_code == 404, "他人任务不应可读"
    assert SECRET not in response.text


def test_anonymous_is_rejected(owner_job):
    response = TestClient(main.app).get(f"/api/jobs/{owner_job}")
    assert response.status_code == 401
    assert SECRET not in response.text


def test_admin_can_read_any_job(owner_job):
    client = TestClient(main.app)
    login = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert login.status_code == 200, f"管理员登录失败:{login.text}"
    response = client.get(f"/api/jobs/{owner_job}")
    assert response.status_code == 200
    assert SECRET in response.text


def test_missing_job_returns_404_same_as_forbidden():
    """不存在与无权应返回同样的响应,避免用任务 id 做探测。"""
    client = TestClient(main.app)
    _register(client, "jobprobe")
    assert client.get("/api/jobs/definitely-not-a-real-job").status_code == 404


def test_process_endpoint_stamps_owner_on_new_jobs(monkeypatch):
    """新建任务必须带归属,否则隔离形同虚设。"""
    submitted: list[tuple] = []
    monkeypatch.setattr(main.EXECUTOR, "submit", lambda *args, **kwargs: submitted.append(args))

    client = TestClient(main.app)
    _register(client, "jobcreator")
    response = client.post("/api/process", data={
        "live_text": "这是一段实时转写文本", "process_mode": "只转写，不推送", "app_mode": "auto",
    })
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    with main.JOBS_LOCK:
        assert main.JOBS[job_id]["owner_username"] == "jobcreator"
    assert submitted, "任务未被提交到线程池"
