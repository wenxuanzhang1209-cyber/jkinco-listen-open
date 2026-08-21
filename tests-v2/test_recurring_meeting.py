"""重复会议:同一个链接、同一时间,下一次自动出现。

关键约束:
- 复用同一行记录,所以 meeting_code / room_name 不变,链接不用重新分发
- 上一次的转写与聊天不能串到下一次
- 会议隔了很久才结束时,下一次必须落在将来,不能落在已经过去的时刻
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.meetings as M


def test_next_occurrence_is_always_in_the_future():
    """会议拖了三周才结束时,下一次不能滚到一个过去的时刻。"""
    now = time.time()
    three_weeks_ago = now - 21 * 86400
    nxt = M._next_occurrence(three_weeks_ago, 7, now)
    assert nxt > now, "下一次落在了过去,会议会立刻又变成可加入"
    # 必须仍落在同一「周几 + 同一时刻」上
    assert abs(((nxt - three_weeks_ago) % (7 * 86400))) < 1e-6


def test_next_occurrence_keeps_the_same_time_of_day():
    now = time.time()
    start = now - 60          # 刚结束的一场
    nxt = M._next_occurrence(start, 7, now)
    assert abs(nxt - (start + 7 * 86400)) < 1e-6


def test_recurrence_requires_a_scheduled_start():
    """没有预约时间就无从推算下一次,必须明确报错而不是静默忽略。"""
    with pytest.raises(Exception) as excinfo:
        M._validated_recurrence("weekly", is_scheduled=False)
    assert "开始时间" in str(excinfo.value)


def test_invalid_recurrence_is_rejected_not_silently_ignored():
    with pytest.raises(Exception):
        M._validated_recurrence("每周", is_scheduled=True)
    assert M._validated_recurrence("weekly", is_scheduled=True) == "weekly"
    assert M._validated_recurrence("", is_scheduled=False) == "none"


def test_occurrence_floor_isolates_previous_round():
    """本次数据的起点取本次真正开始的时刻。"""
    assert M._occurrence_floor({"actual_start_at": 1000.0}) == 1000.0
    # 尚未开始的会议:起点为 0,不过滤任何内容
    assert M._occurrence_floor({"actual_start_at": None}) == 0


def test_all_supported_frequencies_have_labels_and_intervals():
    """界面上能选到的每个频率都必须能真正推算出下一次。"""
    for key in M.RECURRENCE_LABELS:
        if key == "none":
            continue
        assert key in M.RECURRENCE_INTERVAL_DAYS, f"{key} 有标签但无法推算下一次"


def test_full_cycle_keeps_the_same_link_and_isolates_content(tmp_path, monkeypatch):
    """完整走一遍:开会 -> 结束 -> 滚动到下周,链接不变、上周内容不串到下周。"""
    import uuid

    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()

    now = time.time()
    # 会议一小时前开始、刚刚结束 —— 转写与聊天都发生在滚动之前,这才是真实时序
    start = now - 3600
    mid, code, room = uuid.uuid4().hex, "111-222-333", "room-weekly"
    with M.db() as c:
        c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,host_username,
                     status,auto_minutes_enabled,created_at,updated_at,scheduled_start_at,scheduled_end_at,recurrence)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, code, room, "周例会", "admin", "admin", "scheduled", 0, now, now,
                   start, start + 3600, "weekly"))
        # 本次开始,并产生一条转写与一条聊天
        c.execute("UPDATE meetings SET status='active', actual_start_at=? WHERE id=?", (start, mid))
        c.execute("""INSERT INTO meeting_transcript_segments(id,meeting_id,participant_identity,sentence_id,
                     start_time_ms,end_time_ms,text,is_final,provider,deduplication_key,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (uuid.uuid4().hex, mid, "u1", 1, 0, 1000, "上周说的内容", 1, "local", uuid.uuid4().hex, start + 10))
        c.execute("INSERT INTO meeting_chat_messages VALUES(?,?,?,?,?,?)",
                  (uuid.uuid4().hex, mid, "u1", "张三", "上周的聊天", start + 20))

    with M.db() as c:
        before = dict(c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone())
        text_before = M._final_transcript_text(c, mid, M._occurrence_floor(before))
    assert "上周说的内容" in text_before

    # 结束并滚动
    assert M._roll_recurring_meeting(mid) is True

    with M.db() as c:
        after = dict(c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone())

    # 链接的两个来源都必须不变 —— 这正是这个功能的意义
    assert after["meeting_code"] == code, "会议号变了,链接需要重新分发"
    assert after["room_name"] == room, "房间名变了,链接会失效"
    assert after["id"] == mid

    # 时间滚到下周同一时刻,时长保持
    assert abs(after["scheduled_start_at"] - (start + 7 * 86400)) < 1e-6
    assert abs((after["scheduled_end_at"] - after["scheduled_start_at"]) - 3600) < 1e-6

    # 状态回到可预约,本次的痕迹清空
    assert after["status"] == "scheduled"
    assert after["actual_start_at"] is None
    assert after["ended_at"] is None
    assert after["minutes_status"] == "pending"

    # 上周的转写不会进入下周的纪要
    with M.db() as c:
        text_after = M._final_transcript_text(c, mid, M._occurrence_floor(after))
    assert text_after == "", f"上周的转写串到了下周:{text_after!r}"


def test_non_recurring_meeting_is_not_rolled():
    """普通会议结束就是结束,不能被误滚成重复会议。"""
    import uuid, tempfile
    with tempfile.TemporaryDirectory() as d:
        original = M.DB_PATH
        M.DB_PATH = Path(d) / "t.db"
        try:
            M.init_meeting_db()
            now = time.time()
            mid = uuid.uuid4().hex
            with M.db() as c:
                c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,
                             host_username,status,created_at,updated_at,scheduled_start_at,recurrence)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (mid, "c", "r", "普通会", "admin", "admin", "completed", now, now, now, "none"))
            assert M._roll_recurring_meeting(mid) is False
            with M.db() as c:
                row = dict(c.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone())
            assert row["status"] == "completed"
        finally:
            M.DB_PATH = original


def _insert_future_recurring_meeting(
    start_at: float, *, recurrence: str = "weekly"
) -> tuple[str, str, str]:
    """写入一场预约会议，供改期规则的数据库级回归使用。"""
    import uuid

    meeting_id = uuid.uuid4().hex
    meeting_code = f"{uuid.uuid4().int % 1000:03d}-222-333"
    room_name = f"room-{meeting_id}"
    with M.db() as connection:
        connection.execute(
            """INSERT INTO meetings(
                   id,meeting_code,room_name,title,creator_username,host_username,
                   status,auto_minutes_enabled,created_at,updated_at,
                   scheduled_start_at,scheduled_end_at,recurrence
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meeting_id,
                meeting_code,
                room_name,
                "周期改期测试",
                "admin",
                "admin",
                "scheduled",
                1,
                start_at - 60,
                start_at - 60,
                start_at,
                start_at + 3600,
                recurrence,
            ),
        )
    return meeting_id, meeting_code, room_name


def test_reschedule_one_occurrence_returns_to_original_weekly_cadence(tmp_path, monkeypatch):
    """下一场临时改期后，再下一场仍回到原来的周几和时间。"""
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "occurrence.db")
    M.init_meeting_db()
    now = time.time()
    original_start = now + 2 * 86400
    meeting_id, meeting_code, room_name = _insert_future_recurring_meeting(original_start)

    # 模拟迁移前数据：新字段为空。单次改期必须先把原周期基准自愈保存下来。
    monkeypatch.setattr(M.time, "time", lambda: now)
    one_off_start = original_start + 86400
    updated = M._reschedule_scheduled_meeting(
        M._meeting(meeting_id), one_off_start, one_off_start + 5400, "occurrence"
    )
    assert updated["scheduled_start_at"] == one_off_start
    assert updated["scheduled_end_at"] == one_off_start + 5400
    assert updated["recurrence_anchor_at"] == original_start
    assert updated["recurrence_duration_seconds"] == 3600

    # 临时改期这一场结束后，续排应回到原始节奏，标准时长也回到 1 小时。
    monkeypatch.setattr(M.time, "time", lambda: one_off_start + 7200)
    assert M._roll_recurring_meeting(meeting_id) is True
    rolled = M._meeting(meeting_id)
    assert rolled["scheduled_start_at"] == original_start + 7 * 86400
    assert rolled["scheduled_end_at"] == original_start + 7 * 86400 + 3600
    assert rolled["meeting_code"] == meeting_code
    assert rolled["room_name"] == room_name


def test_reschedule_series_moves_future_weekly_cadence(tmp_path, monkeypatch):
    """修改本场及以后时，新时刻成为后续周会的周期基准。"""
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "series.db")
    M.init_meeting_db()
    now = time.time()
    original_start = now + 2 * 86400
    meeting_id, meeting_code, _ = _insert_future_recurring_meeting(original_start)

    shifted_start = original_start + 2 * 86400
    monkeypatch.setattr(M.time, "time", lambda: now)
    updated = M._reschedule_scheduled_meeting(
        M._meeting(meeting_id), shifted_start, shifted_start + 5400, "series"
    )
    assert updated["recurrence_anchor_at"] == shifted_start
    assert updated["recurrence_duration_seconds"] == 5400

    monkeypatch.setattr(M.time, "time", lambda: shifted_start + 7200)
    assert M._roll_recurring_meeting(meeting_id) is True
    rolled = M._meeting(meeting_id)
    assert rolled["scheduled_start_at"] == shifted_start + 7 * 86400
    assert rolled["scheduled_end_at"] == shifted_start + 7 * 86400 + 5400
    assert rolled["meeting_code"] == meeting_code


def test_empty_recurring_meeting_still_rolls_to_next_occurrence(tmp_path, monkeypatch):
    """没有最终转写也算完成一场，不能让周期会议永久停在已结束。"""
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "empty-recurring.db")
    M.init_meeting_db()
    now = time.time()
    start_at = now - 3600
    meeting_id, meeting_code, _ = _insert_future_recurring_meeting(start_at)
    with M.db() as connection:
        connection.execute(
            """UPDATE meetings SET status='ended', actual_start_at=?, ended_at=?
               WHERE id=?""",
            (start_at, now, meeting_id),
        )

    monkeypatch.setattr(M.time, "time", lambda: now)
    M._finalize_minutes(meeting_id)

    rolled = M._meeting(meeting_id)
    assert rolled["status"] == "scheduled"
    assert rolled["minutes_status"] == "pending"
    assert rolled["scheduled_start_at"] == start_at + 7 * 86400
    assert rolled["meeting_code"] == meeting_code
    with M.db() as connection:
        actions = {
            row["action"]
            for row in connection.execute(
                "SELECT action FROM meeting_audit_logs WHERE meeting_id=?", (meeting_id,)
            ).fetchall()
        }
    assert "minutes.skipped_empty" in actions
    assert "meeting.recurrence_rolled" in actions


def _mk_meeting(tmp_path, monkeypatch, **overrides):
    import uuid
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()
    now = time.time()
    mid = uuid.uuid4().hex
    values = {"status": "scheduled", "scheduled_start_at": now - 21 * 86400,
              "scheduled_end_at": now - 21 * 86400 + 3600, "recurrence": "weekly"}
    values.update(overrides)
    with M.db() as c:
        c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,host_username,
                     status,created_at,updated_at,scheduled_start_at,scheduled_end_at,recurrence)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, "888-888-888", "room-x", "周例会", "admin", "admin", values["status"],
                   now, now, values["scheduled_start_at"], values["scheduled_end_at"], values["recurrence"]))
    return mid, now


def test_unattended_recurrence_is_rolled_forward(tmp_path, monkeypatch):
    """没人参加的重复会议也要滚动。

    滚动原本只发生在会议结束时,而没人来就不会进入 active、也就不会结束 ——
    周会跳过一次(放假、临时取消),列表里的预约时间会永远停在过去。
    """
    mid, now = _mk_meeting(tmp_path, monkeypatch)
    M._sweep_idle_meetings_once()
    with M.db() as c:
        row = dict(c.execute("SELECT status, scheduled_start_at, meeting_code FROM meetings WHERE id=?", (mid,)).fetchone())
    assert row["scheduled_start_at"] > now, "预约时间仍停在过去"
    assert row["status"] == "scheduled"
    assert row["meeting_code"] == "888-888-888", "滚动不该改变会议号"


def test_upcoming_recurrence_is_not_rolled_early(tmp_path, monkeypatch):
    """还没到点的会议不能被提前滚走,否则等人到场的会议会凭空消失。"""
    now = time.time()
    mid, _ = _mk_meeting(tmp_path, monkeypatch,
                         scheduled_start_at=now + 3600, scheduled_end_at=now + 7200)
    M._sweep_idle_meetings_once()
    with M.db() as c:
        row = dict(c.execute("SELECT scheduled_start_at FROM meetings WHERE id=?", (mid,)).fetchone())
    assert abs(row["scheduled_start_at"] - (now + 3600)) < 2, "未到点的会议被提前滚走了"


def test_non_recurring_missed_meeting_is_left_alone(tmp_path, monkeypatch):
    """普通预约会议错过了就是错过了,不能被滚到下一周。"""
    mid, now = _mk_meeting(tmp_path, monkeypatch, recurrence="none")
    M._sweep_idle_meetings_once()
    with M.db() as c:
        row = dict(c.execute("SELECT scheduled_start_at FROM meetings WHERE id=?", (mid,)).fetchone())
    assert row["scheduled_start_at"] < now, "普通会议被误滚了"


def test_auto_minutes_are_saved_on_every_occurrence(tmp_path, monkeypatch):
    """第二次及以后的自动纪要必须照常入库。

    判据原先是 version == 1,而重复会议的版本号跨次累加:到第二次 MAX+1 已经是 2,
    会被误判成「已有人工版本」而跳过写入,于是 get_minutes 永远返回第一次的内容。
    """
    import uuid
    mid, now = _mk_meeting(tmp_path, monkeypatch, status="active")
    # 第一次的自动纪要
    with M.db() as c:
        c.execute("INSERT INTO meeting_minutes_versions VALUES (?,?,?,?,?,?)",
                  (uuid.uuid4().hex, mid, 1, "第一周的纪要", "admin", now - 21 * 86400 + 100))
        # 本次(第二周)刚刚开始
        c.execute("UPDATE meetings SET actual_start_at=?, occurrence_floor_at=? WHERE id=?",
                  (now - 3600, now - 7200, mid))
        meeting = dict(c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone())
        floor = M._occurrence_floor(meeting)
        manual = c.execute(
            "SELECT COUNT(*) FROM meeting_minutes_versions WHERE meeting_id=? AND created_at>=?",
            (mid, floor)).fetchone()[0]
    assert manual == 0, "上一次的自动纪要被误判成了本次的人工版本,本次纪要将不会入库"


def test_manual_version_within_the_same_occurrence_still_blocks_overwrite(tmp_path, monkeypatch):
    """本次会议期间有人手写过纪要时,后台任务仍不得覆盖。"""
    import uuid
    mid, now = _mk_meeting(tmp_path, monkeypatch, status="active")
    with M.db() as c:
        c.execute("UPDATE meetings SET actual_start_at=?, occurrence_floor_at=? WHERE id=?",
                  (now - 3600, now - 7200, mid))
        # 本次期间的人工版本
        c.execute("INSERT INTO meeting_minutes_versions VALUES (?,?,?,?,?,?)",
                  (uuid.uuid4().hex, mid, 1, "主持人手写的纪要", "admin", now - 1800))
        meeting = dict(c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone())
        manual = c.execute(
            "SELECT COUNT(*) FROM meeting_minutes_versions WHERE meeting_id=? AND created_at>=?",
            (mid, M._occurrence_floor(meeting))).fetchone()[0]
    assert manual == 1, "本次的人工纪要没被识别到,会被自动纪要覆盖"


def test_rolling_is_idempotent(tmp_path, monkeypatch):
    """重复滚动不得连续推进。

    滚动会被多条路径触发(纪要生成成功、纪要生成失败、空房清扫),而每次滚动
    都会把锚点推到下一次 —— 若不幂等,重复调用会一路往后加,直接跳过一整周,
    参会者按原时间来会发现会议不在了。
    """
    import uuid
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()
    now = time.time()
    start = now - 3600
    mid = uuid.uuid4().hex
    with M.db() as c:
        c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,host_username,
                     status,created_at,updated_at,scheduled_start_at,scheduled_end_at,recurrence,
                     recurrence_anchor_at,recurrence_duration_seconds)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, "c", "r", "周会", "admin", "admin", "completed", now, now,
                   start, start + 3600, "weekly", start, 3600))

    assert M._roll_recurring_meeting(mid) is True
    with M.db() as c:
        first = c.execute("SELECT scheduled_start_at FROM meetings WHERE id=?", (mid,)).fetchone()[0]

    # 再触发两次:必须原地不动
    assert M._roll_recurring_meeting(mid) is False
    assert M._roll_recurring_meeting(mid) is False
    with M.db() as c:
        after = c.execute("SELECT scheduled_start_at FROM meetings WHERE id=?", (mid,)).fetchone()[0]
    assert abs(after - first) < 1, f"重复滚动跳过了 {(after - first) / 86400:.0f} 天"


def test_rolling_preserves_the_meeting_duration(tmp_path, monkeypatch):
    """滚动后会议时长必须与原来一致,不能被拉长或压缩。"""
    import uuid
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()
    now = time.time()
    start = now - 7200
    mid = uuid.uuid4().hex
    with M.db() as c:
        c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,host_username,
                     status,created_at,updated_at,scheduled_start_at,scheduled_end_at,recurrence,
                     recurrence_anchor_at,recurrence_duration_seconds)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, "c", "r", "周会", "admin", "admin", "completed", now, now,
                   start, start + 5400, "weekly", start, 5400))
    M._roll_recurring_meeting(mid)
    with M.db() as c:
        row = dict(c.execute("SELECT scheduled_start_at, scheduled_end_at FROM meetings WHERE id=?", (mid,)).fetchone())
    assert abs((row["scheduled_end_at"] - row["scheduled_start_at"]) - 5400) < 1


def _seed_recurring(tmp_path, monkeypatch, duration_seconds=5400):
    import uuid
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()
    now = time.time()
    start = now + 86400
    mid = uuid.uuid4().hex
    with M.db() as c:
        c.execute("""INSERT INTO meetings(id,meeting_code,room_name,title,creator_username,host_username,
                     status,created_at,updated_at,scheduled_start_at,scheduled_end_at,recurrence,
                     recurrence_anchor_at,recurrence_duration_seconds)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, "c", "r", "周会", "admin", "admin", "scheduled", now, now,
                   start, start + duration_seconds, "weekly", start, duration_seconds))
    return mid


@pytest.mark.parametrize("scope", ["occurrence", "series"])
def test_reschedule_without_end_time_keeps_the_duration(tmp_path, monkeypatch, scope):
    """改期时不传结束时间,不能把时长基准抹掉。

    抹掉之后每一次排期都没有结束时刻,而「结束时刻之前不回收空房」的逻辑随之
    失效 —— 周会开场前十分钟没人进来就会被自动关掉。
    occurrence 分支原本用 COALESCE 保留了,series 分支直接覆盖,两者不一致。
    """
    mid = _seed_recurring(tmp_path, monkeypatch, duration_seconds=5400)
    M._reschedule_scheduled_meeting(M._meeting(mid), time.time() + 7200, None, scope)
    assert M._meeting(mid)["recurrence_duration_seconds"] == 5400, f"{scope} 分支抹掉了时长基准"

    # 滚动到下一次时,结束时间必须按原时长排出
    with M.db() as c:
        c.execute("UPDATE meetings SET status='completed' WHERE id=?", (mid,))
    M._roll_recurring_meeting(mid)
    rolled = M._meeting(mid)
    assert rolled["scheduled_end_at"] is not None, f"{scope} 改期后下一次排期没有结束时间"
    assert abs((rolled["scheduled_end_at"] - rolled["scheduled_start_at"]) - 5400) < 1


def test_reschedule_with_a_new_end_time_updates_the_duration(tmp_path, monkeypatch):
    """显式传了新的结束时间时,时长基准应当跟着更新,而不是被 COALESCE 挡住。"""
    mid = _seed_recurring(tmp_path, monkeypatch, duration_seconds=5400)
    new_start = time.time() + 7200
    M._reschedule_scheduled_meeting(M._meeting(mid), new_start, new_start + 1800, "series")
    assert M._meeting(mid)["recurrence_duration_seconds"] == 1800
