"""纪要生成的结果分类契约。

「这场会没人说话」和「纪要生成出错了」是两件事,必须分开记。

混在一起的代价是告警彻底失效:生产上 61 条 minutes.failed 里只有 1 条是真的
故障,其余全是建了会没进去、或进去了没开麦。淹没在这种噪音里,真出一次事故
没有任何人会发现。用户侧同样受影响 —— 一场空会议不该在界面上显示「生成失败」。
"""
import uuid

import pytest

import backend.meetings as meetings


@pytest.fixture()
def meeting_id(monkeypatch, tmp_path):
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "meetings.db")
    meetings.init_meeting_db()
    identifier = uuid.uuid4().hex
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','active',1,1,1,1,1,0,0,0)""",
            (identifier, "654-821-848", f"room-{identifier}", "纪要结果分类测试"),
        )
    return identifier


def _state(meeting_id: str):
    with meetings.db() as connection:
        row = connection.execute(
            "SELECT status, minutes_status FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()
        actions = [
            r["action"] for r in connection.execute(
                "SELECT action FROM meeting_audit_logs WHERE meeting_id=?", (meeting_id,)
            ).fetchall()
        ]
    return row["status"], row["minutes_status"], actions


def test_meeting_without_transcript_is_empty_not_failed(meeting_id):
    """核心:没人说话不算失败。"""
    meetings._finalize_minutes(meeting_id)

    status, minutes_status, actions = _state(meeting_id)
    assert status == "completed"
    assert minutes_status == "empty", "空会议被标成了 failed"
    assert "minutes.skipped_empty" in actions
    assert "minutes.failed" not in actions, "空会议不该产生故障告警"


def test_interim_only_transcript_also_counts_as_empty(meeting_id):
    """只有中间态识别结果、没有最终句,同样是没内容 —— 生产上这种占了 4 场。"""
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meeting_transcript_segments
               VALUES (?,?,?,?,?,?,?,0,'test',?,0)""",
            (uuid.uuid4().hex, meeting_id, "alice-aaaa", 1, 0, 1000, "识别中的半句", uuid.uuid4().hex),
        )
    meetings._finalize_minutes(meeting_id)

    _, minutes_status, actions = _state(meeting_id)
    assert minutes_status == "empty"
    assert "minutes.failed" not in actions


def test_real_generation_error_is_still_recorded_as_failed(meeting_id, monkeypatch):
    """反向保险:真出错时必须仍然记 failed,不能被这次改动一起吞掉。"""
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meeting_transcript_segments
               VALUES (?,?,?,?,?,?,?,1,'test',?,0)""",
            (uuid.uuid4().hex, meeting_id, "alice-aaaa", 1, 0, 1000, "这是一句真实的最终转写", uuid.uuid4().hex),
        )

    def boom(*args, **kwargs):
        raise RuntimeError("大模型调用失败")

    monkeypatch.setattr(meetings.core, "infer_app_mode_best_effort", boom)
    meetings._finalize_minutes(meeting_id)

    status, minutes_status, actions = _state(meeting_id)
    assert status == "completed"
    assert minutes_status == "failed"
    assert "minutes.failed" in actions
    assert "minutes.skipped_empty" not in actions


def test_every_minutes_status_has_a_user_facing_label():
    """每个会写进库的状态都必须有文案,否则用户看到的是兜底的「会议记录」,
    完全不知道自己那场会到底出了什么事。

    原先这条断言的是「register_meeting_routes 的源码里含某段文字」,拆分路由时
    文案挪进了子函数,测试就假性失败了 —— 断言实现位置而不是行为,本身就是错的。
    """
    written_by_code = {"processing", "failed", "empty", "pending", "completed"}
    missing = written_by_code - set(meetings.MINUTES_STATUS_LABELS)
    assert not missing, f"这些状态缺少界面文案:{sorted(missing)}"
    assert all(meetings.MINUTES_STATUS_LABELS.values()), "存在空文案"
