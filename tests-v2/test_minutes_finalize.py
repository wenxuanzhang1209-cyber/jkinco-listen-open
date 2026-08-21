"""会议结束后自动生成纪要的回归测试。

发现:_finalize_minutes 把纪要版本号硬编码为 1,而 save_minutes 用 MAX(version)+1。
只要成员在自动生成期间(LLM 通常耗时数十秒)手工保存过一版,自动写入就会撞
UNIQUE(meeting_id, version)。因为整段写入在同一个事务里,异常会连带回滚前一条
UPDATE:已计费生成的纪要被丢弃、history_record_id 归空、会议被误标为 failed。

本测试锁死两条契约:
1. 常规路径(无人工版本)行为不变 —— 仍是 version 1 的自动纪要;
2. 竞态路径不再报错,且后台任务不覆盖人写的纪要。
"""
import os
import tempfile
import time
import uuid

os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "s" * 34)
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://example.invalid/livekit")
_WORK_DIR = tempfile.mkdtemp(prefix="jkinco-minutes-")
os.environ["JKINCO_HISTORY_DIR"] = _WORK_DIR
os.environ.setdefault("JKINCO_MEETING_DB", os.path.join(_WORK_DIR, "meetings.db"))

import pytest

import backend.meetings as meetings


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """打桩模型调用:本测试验证的是持久化路径,不应触达网络。"""
    monkeypatch.setattr(meetings.core, "infer_app_mode_best_effort", lambda text, mode: ("general", "理由"))
    monkeypatch.setattr(meetings.core, "generate_minutes", lambda text, mode: "# 自动生成的纪要正文")
    monkeypatch.setattr(meetings.core, "generate_meeting_overview", lambda s, t, m: "概览")
    monkeypatch.setattr(meetings.core, "save_meeting_history_record", lambda *a, **k: "history-record-1")


def _make_ended_meeting_with_transcript() -> str:
    meetings.init_meeting_db()
    meeting_id = uuid.uuid4().hex
    now = time.time()
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','ended',1,1,1,1,1,0,?,?)""",
            (meeting_id, uuid.uuid4().hex[:11], f"room-{meeting_id}", "纪要测试会议", now, now),
        )
        connection.execute(
            """INSERT INTO meeting_transcript_segments (id,meeting_id,participant_identity,sentence_id,
               start_time_ms,end_time_ms,text,is_final,provider,deduplication_key,created_at)
               VALUES (?,?,'alice',1,0,900,'今天讨论现场进度问题',1,'test',?,?)""",
            (uuid.uuid4().hex, meeting_id, uuid.uuid4().hex, now),
        )
    return meeting_id


def _read(meeting_id: str):
    with meetings.db() as connection:
        meeting = connection.execute(
            "SELECT status, minutes_status, history_record_id FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()
        versions = connection.execute(
            "SELECT version, editor_username, content_markdown FROM meeting_minutes_versions"
            " WHERE meeting_id=? ORDER BY version",
            (meeting_id,),
        ).fetchall()
    return meeting, versions


def test_normal_path_writes_version_one():
    """常规路径行为不变:自动纪要落为 version 1,会议标记完成。"""
    meeting_id = _make_ended_meeting_with_transcript()
    meetings._finalize_minutes(meeting_id)

    meeting, versions = _read(meeting_id)
    assert meeting["minutes_status"] == "completed"
    assert meeting["history_record_id"] == "history-record-1"
    assert [row["version"] for row in versions] == [1]
    assert versions[0]["content_markdown"] == "# 自动生成的纪要正文"


def test_manual_save_during_generation_does_not_break_finalize():
    """核心回归:生成期间已有人工版本时,不得报错、不得丢结果。"""
    meeting_id = _make_ended_meeting_with_transcript()
    now = time.time()
    with meetings.db() as connection:
        connection.execute(
            "INSERT INTO meeting_minutes_versions VALUES (?,?,1,?,'alice',?)",
            (uuid.uuid4().hex, meeting_id, "# 我手工写的纪要", now),
        )

    meetings._finalize_minutes(meeting_id)

    meeting, versions = _read(meeting_id)
    assert meeting["minutes_status"] == "completed", "撞版本号导致会议被误标为 failed"
    assert meeting["history_record_id"] == "history-record-1", "事务回滚导致历史关联丢失"
    # 后台任务不得覆盖人写的纪要:get_minutes 只取最新版
    assert versions[-1]["content_markdown"] == "# 我手工写的纪要"


def test_skipped_auto_write_is_recorded_in_audit_log():
    """跳过写入必须留痕,否则运维无法解释纪要为何不是自动生成的那版。"""
    meeting_id = _make_ended_meeting_with_transcript()
    with meetings.db() as connection:
        connection.execute(
            "INSERT INTO meeting_minutes_versions VALUES (?,?,1,?,'alice',?)",
            (uuid.uuid4().hex, meeting_id, "# 我手工写的纪要", time.time()),
        )

    meetings._finalize_minutes(meeting_id)

    with meetings.db() as connection:
        actions = [
            row["action"] for row in connection.execute(
                "SELECT action FROM meeting_audit_logs WHERE meeting_id=?", (meeting_id,)
            ).fetchall()
        ]
    assert "minutes.auto_skipped_manual_exists" in actions
    assert "minutes.failed" not in actions


def test_empty_transcript_marks_empty_without_crashing():
    """没有最终转写时标记 empty 而非 failed,留下审计,且不向上抛异常。

    原先这里断言的是 failed。改判的理由见 tests-v2/test_minutes_outcome.py:
    空会议在正常使用中很常见,和真故障混记会让告警失效。本用例保留原有的两条
    关键保证 —— 不抛异常、不写出纪要版本。
    """
    meetings.init_meeting_db()
    meeting_id = uuid.uuid4().hex
    now = time.time()
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','ended',1,1,1,1,1,0,?,?)""",
            (meeting_id, uuid.uuid4().hex[:11], f"room-{meeting_id}", "空会议", now, now),
        )

    meetings._finalize_minutes(meeting_id)

    meeting, versions = _read(meeting_id)
    assert meeting["minutes_status"] == "empty"
    assert versions == []
