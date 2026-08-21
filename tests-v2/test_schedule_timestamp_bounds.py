"""预约时间戳必须校验,否则一次畸形请求会永久毁掉用户的会议列表。

float 允许 inf 和 nan,Pydantic 照收,而 inf 会被原样写进数据库 —— 之后序列化
抛「Out of range float values are not JSON compliant」,该用户的 /api/meetings
永久返回 500,自己再也打不开会议页。实测确认:脏数据入库后列表持续崩溃。

同时挡住荒谬年份:1e300 是合法 JSON、不会崩,但会让会议永远停在「已预约」,
空房回收永不触发,房间名被永久占用。
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.meetings as M


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_timestamps_are_rejected(bad):
    with pytest.raises(HTTPException) as excinfo:
        M._validated_timestamp(bad, "开始时间")
    assert excinfo.value.status_code == 400


def test_absurd_future_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        M._validated_timestamp(1e300, "开始时间")
    assert excinfo.value.status_code == 400


def test_timestamp_before_the_allowed_range_is_rejected():
    with pytest.raises(HTTPException):
        M._validated_timestamp(-8.64e10, "开始时间")
    with pytest.raises(HTTPException):
        M._validated_timestamp(0, "开始时间")


def test_normal_timestamps_pass_through():
    now = time.time()
    assert M._validated_timestamp(None, "开始时间") is None
    assert M._validated_timestamp(now + 3600, "开始时间") == now + 3600
    # 一年后仍在允许范围内
    assert M._validated_timestamp(now + 365 * 86400, "开始时间") is not None


def test_dirty_timestamp_never_reaches_the_database(tmp_path, monkeypatch):
    """端到端:畸形时间戳被拒后,数据库里不能留下任何痕迹。"""
    monkeypatch.setattr(M, "DB_PATH", tmp_path / "t.db")
    M.init_meeting_db()
    for bad in (float("inf"), 1e300):
        with pytest.raises(HTTPException):
            M._validated_timestamp(bad, "开始时间")
    with M.db() as connection:
        count = connection.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    assert count == 0
