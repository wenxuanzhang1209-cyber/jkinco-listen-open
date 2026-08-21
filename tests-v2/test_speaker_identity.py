"""发言人身份 ↔ 姓名映射契约。

画面放大后左下角显示错误姓名的根因在服务端:LiveKit 磁贴上的名字来自 token 的
`name` 字段,字幕行的名字来自 meeting_participants 的 display_name 关联。
只要这两处按 identity 关联,前端就没有任何理由回退到「当前登录用户」。

这里锁住三条不变量:
1. 每次入会签发的 identity 唯一 —— 否则重连/多端会互相覆盖轨道归属;
2. token 携带本人 display_name —— 磁贴放大后才显示得出真名;
3. 字幕按 participant_identity 关联到本人 display_name,而不是任何默认值。
"""
import os
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
            (meeting_id, "654-821-848", f"room-{meeting_id}", "身份映射测试"),
        )
    return meetings._meeting(meeting_id)


def test_each_join_gets_a_unique_identity(monkeypatch, meeting_db):
    """同一账号两次入会必须拿到不同 identity,否则重连后新旧轨道会归到同一人。"""
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-value-long-enough")

    _, first, _ = meetings._issue_token(meeting_db, "bob", "鲍勃")
    _, second, _ = meetings._issue_token(meeting_db, "bob", "鲍勃")
    assert first != second
    assert first.startswith("bob-") and second.startswith("bob-")


def test_token_carries_the_participants_own_display_name(monkeypatch, meeting_db):
    """磁贴/放大画面上的名字取自 token 的 name —— 必须是本人的,不能是主持人的。"""
    import jwt

    secret = "test-secret-value-long-enough"
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", secret)

    for username, display_name in [("alice", "爱丽丝"), ("bob", "鲍勃"), ("carol", "鲍勃")]:
        token, identity, _ = meetings._issue_token(meeting_db, username, display_name)
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
        assert claims["name"] == display_name, f"{username} 的 token 携带了别人的名字"
        assert claims["sub"] == identity


def test_transcript_resolves_to_the_speakers_own_name(meeting_db):
    """两个人各说一句,字幕必须各自归到本人 —— 重名也要按 identity 区分。"""
    meeting_id = meeting_db["id"]
    people = [("alice", "alice-aaaa", "爱丽丝"), ("bob", "bob-bbbb", "鲍勃"), ("carol", "carol-cccc", "鲍勃")]
    with meetings.db() as connection:
        for username, identity, display_name in people:
            connection.execute(
                """INSERT INTO meeting_participants
                   (id,meeting_id,username,display_name,role,livekit_identity,
                    joined_at,last_heartbeat_at,connection_status)
                   VALUES (?,?,?,?, 'participant', ?, 0, 0, 'connected')""",
                (uuid.uuid4().hex, meeting_id, username, display_name, identity),
            )

    for index, (_, identity, _) in enumerate(people):
        meetings._store_transcript(meeting_id, identity, {
            "sentence_id": index, "begin_time": 0, "end_time": 1000, "text": f"第{index}句", "sentence_end": True,
        })

    with meetings.db() as connection:
        rows = connection.execute(
            "SELECT s.participant_identity, s.text,"
            " COALESCE(p.display_name, s.participant_identity) AS speaker_name"
            " FROM meeting_transcript_segments s"
            " LEFT JOIN meeting_participants p"
            " ON p.meeting_id=s.meeting_id AND p.livekit_identity=s.participant_identity"
            " WHERE s.meeting_id=? ORDER BY s.text",
            (meeting_id,),
        ).fetchall()

    resolved = {row["participant_identity"]: row["speaker_name"] for row in rows}
    assert resolved == {"alice-aaaa": "爱丽丝", "bob-bbbb": "鲍勃", "carol-cccc": "鲍勃"}


def test_unknown_identity_falls_back_to_identity_not_to_a_person(meeting_db):
    """查不到参与者时回落到 identity —— 绝不能落到某个具体的人身上。"""
    meeting_id = meeting_db["id"]
    meetings._store_transcript(meeting_id, "ghost-9999", {
        "sentence_id": 1, "begin_time": 0, "end_time": 1000, "text": "孤儿句子", "sentence_end": True,
    })
    with meetings.db() as connection:
        row = connection.execute(
            "SELECT COALESCE(p.display_name, s.participant_identity) AS speaker_name"
            " FROM meeting_transcript_segments s"
            " LEFT JOIN meeting_participants p"
            " ON p.meeting_id=s.meeting_id AND p.livekit_identity=s.participant_identity"
            " WHERE s.meeting_id=?",
            (meeting_id,),
        ).fetchone()
    assert row["speaker_name"] == "ghost-9999"
