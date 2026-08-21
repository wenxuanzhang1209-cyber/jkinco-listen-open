"""上传处理接口的入参校验契约。

两个原先缺失的约束:

1. process_mode 未校验。判定逻辑是「!= 只转写」即生成纪要,因此任何未知取值
   (前端改文案、调用方拼错、旧客户端)都会静默落到「生成但不推送」分支,
   行为悄悄变样且不报错。

2. live_text 缺业务层长度上限。Starlette 对单个表单字段本就有 1MB 硬上限,
   所以不是完全无界,但 1MB 中文仍能拆成数十次按块计费的大模型调用,而框架层
   的报错("Field exceeded maximum size")对用户没有任何指导意义。
   注意:超长用例必须走 multipart 提交 —— httpx 的 data= 会做 urlencode,
   一个中文字符膨胀成 9 字节,请求会先被框架的 1MB 上限拦下,测不到本层校验。
"""
import base64

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from jkinco_pipeline import PROCESS_MODES, SUMMARY_ONLY, TRANSCRIBE_ONLY
from helpers import solve_captcha


@pytest.fixture
def client(monkeypatch):
    """已登录客户端;拦截线程池提交,避免真的跑转写与大模型。"""
    monkeypatch.setattr(main.EXECUTOR, "submit", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": "procvalid", "display_name": "procvalid", "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        login = client.post("/api/auth/login", json={"username": "procvalid", "password": "StrongPass123"})
        assert login.status_code == 200, login.text
    return client


@pytest.mark.parametrize("mode", PROCESS_MODES)
def test_all_known_process_modes_are_accepted(client, mode):
    """三种正式模式必须全部放行 —— 校验不能误伤正常用法。"""
    response = client.post("/api/process", data={"live_text": "会议内容", "process_mode": mode})
    assert response.status_code == 200, response.text


def test_unknown_process_mode_is_rejected(client):
    """核心回归:未知模式必须报错,而不是静默按默认行为处理。"""
    response = client.post("/api/process", data={
        "live_text": "会议内容", "process_mode": "生成纪要,暂不推送",  # 半角逗号,与正式取值差一个字符
    })
    assert response.status_code == 400
    assert "处理模式" in response.json()["detail"]


def test_empty_process_mode_falls_back_to_default(client):
    """空字段被 FastAPI 视为「未提供」并套用默认值,这是可接受的行为,在此固定住。"""
    response = client.post("/api/process", data={"live_text": "会议内容", "process_mode": ""})
    assert response.status_code == 200


def test_oversized_live_text_is_rejected(client):
    """超长文本必须在入口拒绝,不能进入按块计费的大模型链路。"""
    oversized = "会" * (main.MAX_LIVE_TEXT_CHARS + 1)
    response = client.post(
        "/api/process",
        data={"process_mode": TRANSCRIBE_ONLY},
        files={"live_text": (None, oversized)},  # multipart:避免 urlencode 撑爆框架上限
    )
    assert response.status_code == 413
    assert "过长" in response.json()["detail"]


def test_live_text_at_the_limit_is_accepted(client):
    """边界值必须放行,否则正常的超长会议会被误拒。"""
    at_limit = "会" * main.MAX_LIVE_TEXT_CHARS
    response = client.post(
        "/api/process",
        data={"process_mode": TRANSCRIBE_ONLY},
        files={"live_text": (None, at_limit)},
    )
    assert response.status_code == 200, response.text


def test_request_without_audio_or_text_still_rejected(client):
    """原有校验不能因为新增检查而失效。"""
    response = client.post("/api/process", data={"live_text": "   ", "process_mode": SUMMARY_ONLY})
    assert response.status_code == 400


def test_rejects_disguised_or_unsupported_audio(client, monkeypatch):
    response = client.post(
        "/api/process",
        data={"process_mode": SUMMARY_ONLY},
        files={"audio": ("recording.exe", b"MZ-not-audio", "audio/mpeg")},
    )
    assert response.status_code == 400

    monkeypatch.setattr(main, "audio_duration_seconds", lambda _path: None)
    response = client.post(
        "/api/process",
        data={"process_mode": SUMMARY_ONLY},
        files={"audio": ("recording.mp3", b"not-really-an-mp3", "audio/mpeg")},
    )
    assert response.status_code == 400
    assert "有效媒体" in response.text


def test_full_processing_queue_returns_429_and_does_not_submit(client, monkeypatch):
    capacity = main.threading.BoundedSemaphore(1)
    assert capacity.acquire(blocking=False)
    monkeypatch.setattr(main, "JOB_CAPACITY", capacity)
    submitted = []
    monkeypatch.setattr(main.EXECUTOR, "submit", lambda *args: submitted.append(args))

    response = client.post(
        "/api/process",
        data={"live_text": "队列容量测试", "process_mode": SUMMARY_ONLY},
    )
    assert response.status_code == 429
    assert not submitted


def test_anonymous_cannot_submit(client):
    """校验顺序不能把鉴权挤掉:未登录一律 401。"""
    anonymous = TestClient(main.app)
    response = anonymous.post("/api/process", data={"live_text": "内容", "process_mode": SUMMARY_ONLY})
    assert response.status_code == 401
