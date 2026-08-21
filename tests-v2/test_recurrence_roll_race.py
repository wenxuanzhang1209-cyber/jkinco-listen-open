"""并发续排不能让周会跳周。

_roll_recurring_meeting 被五条路径触发(纪要生成成功、生成失败、判为无内容、
空房清扫、缺席兜底),它们各自在不同线程里跑,完全可能同时命中同一场会议。

代码注释自己写明了危害:「重复调用会一路往后加,直接跳过一整周,参会者按原时间
来会发现会议不在了」。防重入靠的是「已经排到将来的不再滚」,而那是一次
读-判-写 —— db() 用的是 SQLite 默认延迟事务,没有 BEGIN IMMEDIATE。

推理上它应该是幂等的:两个线程读到同一个旧锚点,算出的 next_start 也相同。
但这种事推理不算数 —— 本文件用真并发把它压出来。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meetings
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = meetings.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")
WEEK = 7 * 86400


@pytest.fixture
def weekly_meeting():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        created = client.post("/api/meetings", json={
            "title": "并发续排的周会",
            "scheduled_start_at": time.time() + 3600,
            "recurrence": "weekly",
        }).json()
        if created.get("recurrence") != "weekly":
            pytest.skip("此部署未启用重复会议")
        yield client, created


def _force_finished(meeting_id: str, start_at: float) -> None:
    with meetings.db() as connection:
        connection.execute(
            "UPDATE meetings SET status='completed', scheduled_start_at=?, scheduled_end_at=? WHERE id=?",
            (start_at, start_at + 3600, meeting_id),
        )


def _start_of(client, meeting_id: str) -> float:
    return float(client.get(f"/api/meetings/{meeting_id}").json()["scheduled_start_at"])


def test_concurrent_rolls_advance_exactly_one_occurrence(weekly_meeting):
    """核心:十个线程同时续排,只能前进一场,不能跳周。"""
    client, meeting = weekly_meeting
    meeting_id = meeting["id"]
    original = _start_of(client, meeting_id)
    _force_finished(meeting_id, original)

    barrier = threading.Barrier(10)

    def roll() -> bool:
        barrier.wait()                      # 尽量让它们真的同时进入
        return meetings._roll_recurring_meeting(meeting_id)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: roll(), range(10)))

    rolled = _start_of(client, meeting_id)
    advanced = (rolled - original) / WEEK
    assert abs(advanced - round(advanced)) < 0.01, f"落在了非整周的位置(前进 {advanced:.2f} 周)"
    assert round(advanced) == 1, f"跳周了:前进了 {round(advanced)} 周,应为 1 周"
    assert any(results), "十次调用没有一次真的滚动"


def test_repeated_sequential_rolls_do_not_stack(weekly_meeting):
    """顺序重复调用同样只能前进一场 —— 那是防重入判据的本职。"""
    client, meeting = weekly_meeting
    meeting_id = meeting["id"]
    original = _start_of(client, meeting_id)
    _force_finished(meeting_id, original)

    first = meetings._roll_recurring_meeting(meeting_id)
    later = [meetings._roll_recurring_meeting(meeting_id) for _ in range(5)]

    assert first is True
    assert not any(later), "已排到将来的会议又被滚了一次"
    assert round((_start_of(client, meeting_id) - original) / WEEK) == 1


def test_the_link_survives_concurrent_rolls(weekly_meeting):
    """重复会议的价值就在于链接不变 —— 并发下也不能变。"""
    client, meeting = weekly_meeting
    meeting_id, code = meeting["id"], meeting["meeting_code"]
    _force_finished(meeting_id, _start_of(client, meeting_id))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: meetings._roll_recurring_meeting(meeting_id), range(8)))

    assert client.get(f"/api/meetings/{meeting_id}").json()["meeting_code"] == code


def test_weekday_and_time_of_day_survive_concurrent_rolls(weekly_meeting):
    """跳周之外的另一种坏法:相位漂移,星期几或时刻变了。"""
    client, meeting = weekly_meeting
    meeting_id = meeting["id"]
    original = _start_of(client, meeting_id)

    for _ in range(4):
        _force_finished(meeting_id, _start_of(client, meeting_id))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: meetings._roll_recurring_meeting(meeting_id), range(6)))

    drift = (_start_of(client, meeting_id) - original) % WEEK
    assert min(drift, WEEK - drift) < 1.0, f"连滚四次后相位漂移了 {min(drift, WEEK - drift):.0f} 秒"


# --- 多人同时说话 ---
# 每个参会者各有一路 ASR WebSocket,它们在不同线程里各自落库。转写既不能丢
# (丢了纪要就缺内容),也不能重(重了纪要里同一句话说两遍)。
# 去重靠 deduplication_key 上的 UNIQUE 约束 + INSERT OR IGNORE —— 那是构造上
# 正确,不依赖时序;这条用例守的是「别有人把它改成先查后插」。

def _make_meeting() -> str:
    import uuid

    meeting_id = uuid.uuid4().hex
    with meetings.db() as connection:
        connection.execute(
            "INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,"
            "status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,"
            "auto_minutes_enabled,auto_record,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,1,1,1,1,0,?,?)",
            (meeting_id, f"{abs(hash(meeting_id)) % 900:03d}-000-000", f"room-{meeting_id}",
             "并发转写", "admin", "admin", "active", time.time(), time.time()),
        )
    return meeting_id


def _segment_count(meeting_id: str) -> int:
    with meetings.db() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM meeting_transcript_segments WHERE meeting_id=?", (meeting_id,)
        ).fetchone()[0]


def test_concurrent_speakers_lose_nothing():
    meeting_id = _make_meeting()
    speakers, per_speaker = 6, 25

    def speak(index: int) -> None:
        for order in range(per_speaker):
            meetings._store_transcript(meeting_id, f"user-{index}", {
                "text": f"发言人{index}的第{order}句：监理单位检查了检验批",
                "sentence_end": True, "sentence_id": order,
                "begin_time": order * 1000, "end_time": order * 1000 + 900,
            })

    with ThreadPoolExecutor(max_workers=speakers) as pool:
        list(pool.map(speak, range(speakers)))

    assert _segment_count(meeting_id) == speakers * per_speaker, "并发落库丢了转写"


def test_the_same_sentence_twice_is_stored_once():
    """重连后 ASR 会重发已经识别过的句子 —— 不能在纪要里说两遍。"""
    meeting_id = _make_meeting()
    sentence = {"text": "监理单位要求下周完成整改", "sentence_end": True,
                "sentence_id": 7, "begin_time": 0, "end_time": 900}

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: meetings._store_transcript(meeting_id, "user-a", dict(sentence)), range(8)))

    assert _segment_count(meeting_id) == 1, "同一句话被重复落库"


def test_dedup_is_enforced_by_the_schema_not_by_a_prior_read():
    """去重必须由 UNIQUE 约束兜底 —— 「先查后插」在并发下必然漏。"""
    import inspect

    source = inspect.getsource(meetings.init_meeting_db)
    assert "deduplication_key TEXT NOT NULL UNIQUE" in source
    store = inspect.getsource(meetings._store_transcript)
    assert "INSERT OR IGNORE" in store, "改成普通 INSERT 的话并发会撞约束报错"
