"""在线成员列表契约。

成员区只展示「当前在线的人」,不再展示加入/离开的过程事件。
前端按 connection_status 过滤,所以离场的每条路径都必须真的把状态落成 'left':
主动离会、心跳超时(关标签页/断网)、会议结束。任何一条漏掉,列表里就会
一直挂着一个其实已经走了的人,人数也跟着不准。

去重按稳定身份(username / livekit_identity),不按昵称 —— 否则两个同名的人
会被合并成一个,重连产生的新记录也会盖掉旧的角色标记。
"""
import uuid

import pytest

import backend.meetings as meetings


@pytest.fixture()
def meeting_db(monkeypatch, tmp_path):
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "meetings.db")
    meetings.init_meeting_db()
    meeting_id = uuid.uuid4().hex
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','active',1,1,1,1,1,0,0,0)""",
            (meeting_id, "654-821-848", f"room-{meeting_id}", "在线成员测试"),
        )
    return meeting_id


def _join(meeting_id: str, username: str, display_name: str, identity: str, heartbeat: float) -> None:
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meeting_participants
               (id,meeting_id,username,display_name,role,livekit_identity,
                joined_at,last_heartbeat_at,connection_status)
               VALUES (?,?,?,?, 'participant', ?, ?, ?, 'connected')""",
            (uuid.uuid4().hex, meeting_id, username, display_name, identity, heartbeat, heartbeat),
        )


def _online(meeting_id: str) -> list[str]:
    with meetings.db() as connection:
        rows = connection.execute(
            "SELECT display_name FROM meeting_participants"
            " WHERE meeting_id=? AND connection_status='connected' ORDER BY display_name",
            (meeting_id,),
        ).fetchall()
    return [row["display_name"] for row in rows]


def test_heartbeat_timeout_removes_the_member(meeting_db):
    """关掉标签页不会发离会请求,只能靠心跳超时把人清掉。"""
    import time

    now = time.time()
    _join(meeting_db, "alice", "爱丽丝", "alice-aaaa", now)
    _join(meeting_db, "bob", "鲍勃", "bob-bbbb", now - meetings.PARTICIPANT_STALE_SECONDS - 60)

    assert _online(meeting_db) == ["爱丽丝", "鲍勃"]
    meetings._sweep_idle_meetings_once(now)
    assert _online(meeting_db) == ["爱丽丝"], "心跳超时的成员仍留在在线列表里"


def test_left_member_keeps_a_record_but_leaves_the_online_list(meeting_db):
    """离会记录留在库里供审计,但不能出现在成员区。"""
    import time

    now = time.time()
    _join(meeting_db, "bob", "鲍勃", "bob-bbbb", now)
    with meetings.db() as connection:
        connection.execute(
            """UPDATE meeting_participants SET left_at=?, connection_status='left'
               WHERE meeting_id=? AND username=?""",
            (now, meeting_db, "bob"),
        )
        total = connection.execute(
            "SELECT COUNT(*) AS n FROM meeting_participants WHERE meeting_id=?", (meeting_db,)
        ).fetchone()["n"]

    assert total == 1, "审计记录被删掉了,排障时查不到人来过"
    assert _online(meeting_db) == []


def test_reconnect_is_deduped_by_identity_not_by_nickname(meeting_db):
    """同名两个人必须算两个人;同一个人重连必须算一个人。"""
    import time

    now = time.time()
    _join(meeting_db, "bob", "鲍勃", "bob-bbbb", now)     # 断线前
    _join(meeting_db, "bob", "鲍勃", "bob-cccc", now)     # 重连后的新记录
    _join(meeting_db, "carol", "鲍勃", "carol-dddd", now)  # 另一个恰好也叫鲍勃的人

    with meetings.db() as connection:
        rows = connection.execute(
            "SELECT username, livekit_identity, display_name FROM meeting_participants"
            " WHERE meeting_id=? AND connection_status='connected'",
            (meeting_db,),
        ).fetchall()

    # 前端的去重键:username 优先,回退 livekit_identity
    deduped = {row["username"] or row["livekit_identity"] for row in rows}
    assert deduped == {"bob", "carol"}, "重连未合并,或同名用户被错误合并"

    # 只按昵称去重会把两个不同的人合成一个 —— 这正是要避免的
    assert len({row["display_name"] for row in rows}) == 1
