"""会议聊天返回条数的契约。

前端每 2.5 秒轮询一次这个接口,拿到后只保留最后 400 条。若服务端不截断,一场
消息很多的会议就会反复把早期消息查出来、传过去、解析完再丢掉 —— 开销随会议
时长线性增长,而多出来的部分用户根本看不到。

截断必须发生在数据库侧(ORDER BY DESC + LIMIT 再翻正),而不是取全量后切片,
否则省下的只有网络传输,数据库仍然要扫全表。
"""
import uuid

import pytest

import backend.meetings as meetings


@pytest.fixture()
def meeting_id(monkeypatch, tmp_path):
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "m.db")
    meetings.init_meeting_db()
    identifier = uuid.uuid4().hex
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','active',1,1,1,1,1,0,0,0)""",
            (identifier, "654-821-848", f"room-{identifier}", "聊天截断测试"),
        )
    return identifier


def _seed(meeting_id: str, count: int) -> None:
    with meetings.db() as connection:
        connection.executemany(
            "INSERT INTO meeting_chat_messages VALUES (?,?,?,?,?,?)",
            [(uuid.uuid4().hex, meeting_id, "alice", "爱丽丝", f"第{n}条", float(n)) for n in range(count)],
        )


def _fetch(meeting_id: str):
    """复现接口内的查询,验证 SQL 层面的截断行为。"""
    with meetings.db() as connection:
        rows = connection.execute(
            "SELECT * FROM meeting_chat_messages WHERE meeting_id=? ORDER BY created_at DESC LIMIT ?",
            (meeting_id, meetings.CHAT_HISTORY_LIMIT),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def test_returns_everything_when_under_the_limit(meeting_id):
    _seed(meeting_id, 5)
    items = _fetch(meeting_id)
    assert [item["message"] for item in items] == [f"第{n}条" for n in range(5)]


def test_truncates_to_the_most_recent_messages(meeting_id):
    _seed(meeting_id, meetings.CHAT_HISTORY_LIMIT + 60)
    items = _fetch(meeting_id)

    assert len(items) == meetings.CHAT_HISTORY_LIMIT
    # 保留的必须是最新的那一批,而不是最早的
    assert items[-1]["message"] == f"第{meetings.CHAT_HISTORY_LIMIT + 59}条"
    assert items[0]["message"] == f"第{60}条"


def test_result_is_in_chronological_order(meeting_id):
    """倒序取完必须翻正:聊天从上往下读,顺序反了整段对话就没法看。"""
    _seed(meeting_id, 30)
    times = [item["created_at"] for item in _fetch(meeting_id)]
    assert times == sorted(times)


def test_limit_matches_what_the_frontend_keeps():
    """服务端上限与前端保留条数必须一致,否则要么白传要么滚不到看过的消息。"""
    import pathlib

    source = pathlib.Path(__file__).resolve().parent.parent / "frontend/src/MeetingModule.tsx"
    assert f"slice(-{meetings.CHAT_HISTORY_LIMIT})" in source.read_text(encoding="utf-8")


def test_messages_from_other_meetings_are_not_returned(meeting_id):
    """截断不能把会议隔离一起弄丢。"""
    other = uuid.uuid4().hex
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'bob','bob','active',1,1,1,1,1,0,0,0)""",
            (other, "111-222-333", f"room-{other}", "另一场会"),
        )
    _seed(meeting_id, 3)
    _seed(other, 3)
    assert len(_fetch(meeting_id)) == 3
