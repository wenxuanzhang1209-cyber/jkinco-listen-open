"""槽位已满时,别先把 500MB 写进磁盘再拒。

/api/process 的顺序原先是:校验后缀 -> 把上传写进磁盘(最多 500MB)-> 跑 ffprobe
-> 查模板 -> **最后**才 acquire_job_slot。于是槽位已满的账号,每次注定被拒的提交
都要白付那些磁盘与 CPU 代价 —— 而被拒的请求不占槽位,可以一直重试。

预检刻意**不预留**槽位:预留了再在后续校验失败时忘记归还就会漏槽位,那比多写
一次磁盘糟得多。权威判定仍是 acquire_job_slot。
"""
from __future__ import annotations

import inspect
import io

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = main.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


@pytest.fixture
def client():
    with TestClient(app) as session:
        session.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        yield session


@pytest.fixture(autouse=True)
def _clean_slots():
    main.JOB_SLOTS_BY_USER.clear()
    yield
    main.JOB_SLOTS_BY_USER.clear()


def test_precheck_rejects_before_touching_the_upload(client, monkeypatch):
    """核心回归:槽位满时,请求不该走到写盘与 ffprobe。"""
    main.JOB_SLOTS_BY_USER[ADMIN_USERNAME] = main.MAX_PENDING_JOBS_PER_USER
    monkeypatch.setattr(main, "has_audio_stream", lambda *a, **k: pytest.fail("不该跑到 ffprobe"))

    response = client.post(
        "/api/process",
        files={"audio": ("m.wav", io.BytesIO(b"RIFF" + b"\x00" * 4096), "audio/wav")},
        data={"process_mode": main.DEFAULT_PROCESS_MODE, "app_mode": "auto"},
    )
    assert response.status_code == 429
    assert "正在处理" in response.json()["detail"]


def test_precheck_does_not_reserve_a_slot(client):
    """预检占了槽位又不归还的话,用户会被自己的失败请求永久挡住。"""
    before = dict(main.JOB_SLOTS_BY_USER)
    assert main.job_slot_precheck(ADMIN_USERNAME) == ""
    assert dict(main.JOB_SLOTS_BY_USER) == before, "预检预留了槽位"


def test_precheck_runs_before_the_upload_is_written():
    source = inspect.getsource(main.process_audio)
    assert source.index("job_slot_precheck(") < source.index("await audio.read("), "预检排到写盘后面了"
    assert source.index("job_slot_precheck(") < source.index("has_audio_stream"), "预检排到 ffprobe 后面了"


def test_authoritative_acquire_is_still_there():
    """预检不能取代真正的占位 —— 少了它并发提交会超配额。"""
    source = inspect.getsource(main.process_audio)
    assert "acquire_job_slot(username)" in source
    assert source.index("job_slot_precheck(") < source.index("acquire_job_slot(username)")


def test_a_free_slot_still_lets_the_upload_through(client, monkeypatch):
    """省代价不能把正常提交也挡了。"""
    monkeypatch.setattr(main.core, "transcribe_audio", lambda *a, **k: "监理单位检查了检验批")
    monkeypatch.setattr(main.core, "infer_app_mode_best_effort", lambda *a, **k: ("talk", "工程例会"))
    monkeypatch.setattr(main.core, "generate_minutes", lambda *a, **k: "## 结论\n- 通过")
    monkeypatch.setattr(main.core, "generate_meeting_overview", lambda *a, **k: "## 概览")
    monkeypatch.setattr(main.core, "save_meeting_history_record", lambda *a, **k: "rec-x")
    monkeypatch.setattr(main.core, "should_push_to_dingtalk", lambda *a, **k: False)

    response = client.post(
        "/api/process",
        data={"live_text": "监理单位检查了检验批", "process_mode": main.DEFAULT_PROCESS_MODE, "app_mode": "auto"},
    )
    assert response.status_code == 200, response.text[:200]
    assert response.json().get("job_id")
