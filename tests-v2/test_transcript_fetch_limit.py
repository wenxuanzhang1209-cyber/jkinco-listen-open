"""转写拉取的条数上限。

前端每 5 秒轮询一次这个接口,并且只保留最后 120 条。首次进会时 after=0,若不
截断就要把整场会议的转写全部取出:一小时会议约 2000 条 / 904 KB / 12.4ms ——
百人同时进会(会议开始、或网络抖动后集体重连)就是 88 MB 传输和 1.24 秒 CPU,
而多出来的部分前端拿到手立刻丢掉。

截断必须发生在数据库侧(ORDER BY DESC + LIMIT 再翻正),否则省下的只有网络传输,
数据库仍要扫全表。同时必须保证取到的是**最新**那批,而不是最早的 —— 取错方向
的话用户进会看到的是一小时前的对话。
"""
import time
import uuid

import pytest

import backend.meetings as meetings


@pytest.fixture()
def meeting_id(monkeypatch, tmp_path):
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "m.db")
    meetings.init_meeting_db()
    identifier = uuid.uuid4().hex
    now = time.time()
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','active',1,1,1,1,1,0,?,?)""",
            (identifier, "654-821-848", f"room-{identifier}", "转写截断测试", now, now),
        )
    return identifier


# created_at 从 1 起算:接口用的是 created_at > after,而 after 默认 0,
# 时间戳为 0 的那条会被排除。真实时间戳不可能是 0,这里避开这个人为的边界。
def _seed(meeting_id: str, count: int) -> None:
    with meetings.db() as connection:
        connection.executemany(
            "INSERT INTO meeting_transcript_segments VALUES (?,?,?,?,?,?,?,1,'test',?,?)",
            [(uuid.uuid4().hex, meeting_id, "alice-aaaa", n, n * 1000, n * 1000 + 900,
              f"第{n}句", uuid.uuid4().hex, float(n + 1)) for n in range(count)],
        )


def _fetch(meeting_id: str, after: float = 0):
    """复现接口内的查询。"""
    with meetings.db() as connection:
        rows = connection.execute(
            "SELECT * FROM meeting_transcript_segments WHERE meeting_id=? AND created_at>?"
            " ORDER BY created_at DESC, start_time_ms DESC LIMIT ?",
            (meeting_id, after, meetings.TRANSCRIPT_FETCH_LIMIT),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def test_returns_everything_when_under_the_limit(meeting_id):
    _seed(meeting_id, 5)
    assert [item["text"] for item in _fetch(meeting_id)] == [f"第{n}句" for n in range(5)]


def test_first_fetch_is_capped(meeting_id):
    _seed(meeting_id, meetings.TRANSCRIPT_FETCH_LIMIT + 300)
    items = _fetch(meeting_id)
    assert len(items) == meetings.TRANSCRIPT_FETCH_LIMIT


def test_capped_result_keeps_the_newest_not_the_oldest(meeting_id):
    """取错方向的话,用户一进会看到的是一小时前的对话。"""
    total = meetings.TRANSCRIPT_FETCH_LIMIT + 300
    _seed(meeting_id, total)
    items = _fetch(meeting_id)

    assert items[-1]["text"] == f"第{total - 1}句", "最后一条不是最新的"
    assert items[0]["text"] == f"第{total - meetings.TRANSCRIPT_FETCH_LIMIT}句"


def test_result_is_in_chronological_order(meeting_id):
    """倒序取完必须翻正,否则整段对话是反着读的。"""
    _seed(meeting_id, 50)
    times = [item["created_at"] for item in _fetch(meeting_id)]
    assert times == sorted(times)


def test_incremental_polling_is_unaffected(meeting_id):
    """增量拉取每次只有几条,上限对它不该生效。"""
    _seed(meeting_id, 500)
    items = _fetch(meeting_id, after=496)   # created_at = 序号 + 1
    assert [item["text"] for item in items] == [f"第{n}句" for n in (496, 497, 498, 499)]


def test_limit_leaves_headroom_over_what_the_frontend_keeps():
    """上限要略高于前端保留的条数,否则刚进会就没法往回翻。"""
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent
              / "frontend/src/MeetingModule.tsx").read_text(encoding="utf-8")
    assert "slice(-120)" in source, "前端保留条数变了,请同步复核这个上限"
    assert meetings.TRANSCRIPT_FETCH_LIMIT > 120


def test_other_meetings_are_not_returned(meeting_id):
    """截断不能把会议隔离一起弄丢。"""
    other = uuid.uuid4().hex
    now = time.time()
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'bob','bob','active',1,1,1,1,1,0,?,?)""",
            (other, "111-222-333", f"room-{other}", "另一场会", now, now),
        )
    _seed(meeting_id, 3)
    _seed(other, 3)
    assert len(_fetch(meeting_id)) == 3
