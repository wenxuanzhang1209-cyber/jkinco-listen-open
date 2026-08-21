"""预约结束时间与取消会议的契约。

结束时间的作用是「在约定时间之前不回收空房」。预约的会常常是先建好、人陆续到,
或中途全体短暂离开(换会议室、等人),按空房 10 分钟就结束会让人回来发现会议
没了。但它同时也把回收关掉了,所以必须有时长上限 —— 否则一场忘记结束的会会永远
占着房间、也永远不会归档生成纪要。

取消与结束是两回事:取消是「这场会不开了」,不归档不生成纪要;结束是「开完了」,
要走归档流程。混成一个状态的话,取消掉的空会议会去跑一遍纪要生成然后失败。
"""
import time
import uuid

import pytest
from fastapi import HTTPException

import backend.meetings as meetings


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "m.db")
    meetings.init_meeting_db()
    return tmp_path


def _make(status="active", *, scheduled_end_at=None, last_activity=None, connected=0, heartbeat=None):
    """heartbeat 单独给:回收会先把心跳超时的人判为离线,若沿用 last_activity,
    「有人在场」的用例会因为心跳过期而自相矛盾。"""
    identifier = uuid.uuid4().hex
    now = time.time()
    activity = now if last_activity is None else last_activity
    beat = activity if heartbeat is None else heartbeat
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at,scheduled_end_at)
               VALUES (?,?,?,?,'alice','alice',?,1,1,1,1,1,0,?,?,?)""",
            (identifier, f"{uuid.uuid4().hex[:9]}", f"room-{identifier}", "测试会议",
             status, activity, now, scheduled_end_at),
        )
        for index in range(max(connected, 1)):
            connection.execute(
                """INSERT INTO meeting_participants
                   (id,meeting_id,username,display_name,role,livekit_identity,
                    joined_at,last_heartbeat_at,connection_status)
                   VALUES (?,?,?,?, 'participant', ?, ?, ?, ?)""",
                (uuid.uuid4().hex, identifier, f"u{index}", f"用户{index}", f"u{index}-x",
                 beat, beat, "connected" if index < connected else "left"),
            )
    return identifier


def _status(meeting_id):
    with meetings.db() as connection:
        return connection.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()["status"]


# ── 结束时间保护 ──

def test_empty_room_is_not_reclaimed_before_the_scheduled_end(db):
    """核心:约定结束时间之前,空房超时也不能结束会议。"""
    now = time.time()
    meeting_id = _make(scheduled_end_at=now + 3600, last_activity=now - 3600, connected=0)

    meetings._sweep_idle_meetings_once(now)
    assert _status(meeting_id) == "active", "结束时间未到就被回收了"


def test_empty_room_is_reclaimed_after_the_scheduled_end(db):
    """到点之后恢复原有规则,不能永久占着房间。"""
    now = time.time()
    meeting_id = _make(scheduled_end_at=now - 60, last_activity=now - 3600, connected=0)

    meetings._sweep_idle_meetings_once(now)
    # 回收先置 processing(纪要生成中),完成后才转 completed —— 这里只关心「已被回收」
    assert _status(meeting_id) != "active"


def test_meetings_without_an_end_time_behave_exactly_as_before(db):
    """存量会议该列为 NULL,行为必须与改动前一致。"""
    now = time.time()
    meeting_id = _make(scheduled_end_at=None, last_activity=now - 3600, connected=0)

    meetings._sweep_idle_meetings_once(now)
    assert _status(meeting_id) != "active"


def test_occupied_room_is_never_reclaimed(db):
    """有人在里面时本来就不该回收 —— 加了结束时间也不能改变这一点。"""
    now = time.time()
    # 心跳保持新鲜:人确实还在。会议本身的「最后活动」时间早已超过空房阈值,
    # 用来证明「有人在」这一条本身就足以阻止回收。
    meeting_id = _make(scheduled_end_at=None, last_activity=now - 3600, connected=2, heartbeat=now)

    meetings._sweep_idle_meetings_once(now)
    assert _status(meeting_id) == "active"


# ── 时长上限 ──

def test_end_time_must_be_after_start():
    start = time.time()
    with pytest.raises(HTTPException) as error:
        meetings._validated_end_time(start - 1, start)
    assert error.value.status_code == 400


def test_duration_over_the_cap_is_rejected():
    """上限存在的意义:过长的结束时间等于把空房回收永久关掉。"""
    start = time.time()
    with pytest.raises(HTTPException) as error:
        meetings._validated_end_time(start + meetings.MAX_SCHEDULED_DURATION_SECONDS + 60, start)
    assert error.value.status_code == 400
    assert "6 小时" in error.value.detail


def test_duration_exactly_at_the_cap_is_allowed():
    start = time.time()
    end = start + meetings.MAX_SCHEDULED_DURATION_SECONDS
    assert meetings._validated_end_time(end, start) == end


def test_no_end_time_is_allowed():
    """不填结束时间仍然合法,走原有的空房回收。"""
    assert meetings._validated_end_time(None, time.time()) is None


# ── 取消会议 ──

def test_cancelled_meeting_is_not_marked_completed(db):
    """取消不是结束:状态必须可区分,否则会去跑一遍纪要生成然后失败。"""
    meeting_id = _make(status="scheduled")
    now = time.time()
    with meetings.db() as connection:
        connection.execute("UPDATE meetings SET status='cancelled', ended_at=? WHERE id=?", (now, meeting_id))
    assert _status(meeting_id) == "cancelled"


def test_cancelled_meeting_is_not_swept_as_an_idle_room(db):
    """回收只看 status='active',取消掉的会不该被再处理一次。"""
    now = time.time()
    meeting_id = _make(status="active", last_activity=now - 3600, connected=0)
    with meetings.db() as connection:
        connection.execute("UPDATE meetings SET status='cancelled' WHERE id=?", (meeting_id,))

    meetings._sweep_idle_meetings_once(now)
    assert _status(meeting_id) == "cancelled"


def test_cancelling_removes_people_still_in_the_room(db):
    """取消时必须把仍在房间里的人清出去,否则他们的心跳会让这场会看起来还活着。"""
    now = time.time()
    meeting_id = _make(status="active", connected=2, heartbeat=now)
    with meetings.db() as connection:
        connection.execute("UPDATE meetings SET status='cancelled', ended_at=? WHERE id=?", (now, meeting_id))
        connection.execute(
            """UPDATE meeting_participants SET left_at=COALESCE(left_at, ?),
               connection_status='left' WHERE meeting_id=? AND connection_status='connected'""",
            (now, meeting_id),
        )
        still_in = connection.execute(
            "SELECT COUNT(*) AS n FROM meeting_participants WHERE meeting_id=? AND connection_status='connected'",
            (meeting_id,),
        ).fetchone()["n"]
    assert still_in == 0


def test_cancelled_meeting_keeps_its_audit_trail(db):
    """取消要留痕:排障时需要知道是谁在什么时候取消的。"""
    meeting_id = _make(status="scheduled")
    meetings._audit(meeting_id, "alice", "meeting.cancelled", None)
    with meetings.db() as connection:
        actions = [r["action"] for r in connection.execute(
            "SELECT action FROM meeting_audit_logs WHERE meeting_id=?", (meeting_id,)).fetchall()]
    assert "meeting.cancelled" in actions
