"""卡在「纪要生成中」的会议必须能恢复。

minutes_status='processing' 的含义是「有个线程正在生成」。这个断言在进程被杀时
就不成立了 —— 生成跑在内存里的线程池上,每次发布都会重启进程,在途的任务随之
消失,而库里还写着「正在生成」。此前没有任何机制把它改回来:那场会议就永久停在
「会议已结束，纪要正在生成」,界面据此持续轮询,用户看到一个永远转下去的圈。

两道恢复:
  - 启动时:那一刻必然没有在跑的生成线程,还停在 processing 的一定是孤儿,
    不看时长直接复位(用户不必等三刻钟);
  - 运行中:按超时兜底,应对进程还活着但任务异常消失的情形。

复位成 failed 而不是重新生成:转写还在,用户可以自己重新处理;自动重跑会在每次
发布后对所有在途会议重新计费,代价与收益不成比例。
"""
from __future__ import annotations

import time
import uuid

import pytest

import backend.meetings as meeting_service


def _meeting(minutes_status: str, ended_ago: float) -> str:
    meeting_id = uuid.uuid4().hex
    now = time.time()
    with meeting_service.db() as connection:
        connection.execute(
            """INSERT INTO meetings(id, meeting_code, room_name, title, creator_username, host_username,
                                    status, created_at, updated_at, recurrence, minutes_status, ended_at)
               VALUES(?,?,?,?,?,?,'processing',?,?,'none',?,?)""",
            (meeting_id, uuid.uuid4().hex[:11], f"room-{meeting_id[:8]}", "卡住的会议",
             "u", "u", now - 7200, now - 7200, minutes_status, now - ended_ago),
        )
    return meeting_id


def _status(meeting_id: str) -> str:
    with meeting_service.db() as connection:
        return connection.execute(
            "SELECT minutes_status FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()[0]


def test_long_stuck_minutes_are_reset():
    stuck = _meeting("processing", meeting_service.MINUTES_PROCESSING_TIMEOUT_SECONDS + 600)
    assert meeting_service.recover_stuck_minutes() >= 1
    assert _status(stuck) == "failed"


def test_recently_ended_meeting_is_left_alone():
    """刚结束的会议正在正常生成,不能被误判成卡住。"""
    fresh = _meeting("processing", 60)
    meeting_service.recover_stuck_minutes()
    assert _status(fresh) == "processing", "把正在生成的会议误复位了"


@pytest.mark.parametrize("status", ["completed", "failed", "empty", "pending"])
def test_other_statuses_are_untouched(status):
    other = _meeting(status, meeting_service.MINUTES_PROCESSING_TIMEOUT_SECONDS + 600)
    meeting_service.recover_stuck_minutes()
    assert _status(other) == status


def test_startup_reset_ignores_age():
    """启动那一刻没有任何生成线程,所有 processing 都是孤儿。"""
    fresh = _meeting("processing", 5)
    meeting_service.recover_stuck_minutes(
        now=time.time() + meeting_service.MINUTES_PROCESSING_TIMEOUT_SECONDS + 1
    )
    assert _status(fresh) == "failed"


def test_sweeper_runs_the_recovery():
    """光有函数不够,必须真的挂在周期清扫上。"""
    import inspect

    assert "recover_stuck_minutes" in inspect.getsource(meeting_service._sweep_idle_meetings_once)


def test_transcript_survives_the_reset():
    """复位的是状态,不是内容 —— 转写还在,用户才能自己重新处理。"""
    stuck = _meeting("processing", meeting_service.MINUTES_PROCESSING_TIMEOUT_SECONDS + 600)
    with meeting_service.db() as connection:
        connection.execute(
            """INSERT INTO meeting_transcript_segments
               VALUES (?, ?, 'u', 1, 0, 900, '这段话必须留着', 1, 'test', ?, ?)""",
            (uuid.uuid4().hex, stuck, uuid.uuid4().hex, time.time()),
        )
    meeting_service.recover_stuck_minutes()
    with meeting_service.db() as connection:
        text = connection.execute(
            "SELECT text FROM meeting_transcript_segments WHERE meeting_id=?", (stuck,)
        ).fetchone()[0]
    assert text == "这段话必须留着"
