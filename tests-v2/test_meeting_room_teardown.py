"""结束会议必须真的把媒体房间断开。

发现:「结束会议」原先只改数据库 —— 应用停止转写、纪要照常生成,而参会人的
浏览器还连在 LiveKit 房间里,可以继续通话。主持人以为会已经散了,实际没有,
而且这段对话不进转写、不留任何记录。令牌有效期是 12 小时,单靠它过期兜不住。

真机验证过:建房 -> 调用结束逻辑 -> 房间从 list_rooms 中消失。

这里锁住三条,都是「不能因为这个新增动作反过来把结束会议弄坏」:
  - 未配置 LIVEKIT_API_URL 时完全跳过(LiveKit 在 host 网络、应用在 bridge 网络,
    地址随部署而变,写死网关 IP 迟早失配);
  - 媒体服务不可达时不抛异常、不无界等待 —— 会议结束已经落库,不能因为删房间
    失败就让用户点了「结束」却发现会议还在;
  - 房间不存在按正常处理:建了会没人进是常态,按警告记会让日志天天报无意义的错。
"""
from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

import backend.meetings as meetings


@pytest.fixture(autouse=True)
def _clear_api_url(monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_URL", raising=False)


def test_skipped_entirely_when_no_admin_url_is_configured():
    """没配地址就什么都不做 —— 尤其不能去猜一个地址再等它超时。"""
    with patch.object(meetings, "LiveKitAPI") as fake:
        started = time.monotonic()
        meetings._disconnect_livekit_room("jkinco-any")
        assert time.monotonic() - started < 0.5
        fake.assert_not_called()


def test_unreachable_media_service_does_not_break_ending_a_meeting(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_URL", "http://127.0.0.1:59999")
    started = time.monotonic()
    meetings._disconnect_livekit_room("jkinco-any")  # 不得抛出
    assert time.monotonic() - started < meetings.LIVEKIT_ADMIN_TIMEOUT_SECONDS + 3


def test_missing_room_is_not_reported_as_a_failure(monkeypatch):
    """建了会没人进就没有房间,这是常态不是故障。"""
    monkeypatch.setenv("LIVEKIT_API_URL", "http://127.0.0.1:7880")

    class _Rooms:
        async def delete_room(self, request):
            raise RuntimeError("twirp error unknown: requested room does not exist")

    class _FakeApi:
        def __init__(self, *args, **kwargs):
            self.room = _Rooms()

        async def aclose(self):
            pass

    with patch.object(meetings, "LiveKitAPI", _FakeApi), \
         patch.object(meetings.LOGGER, "warning") as warned:
        meetings._disconnect_livekit_room("jkinco-empty")
        warned.assert_not_called()


def test_real_failures_are_logged_but_swallowed(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_URL", "http://127.0.0.1:7880")

    class _Rooms:
        async def delete_room(self, request):
            raise RuntimeError("permission denied")

    class _FakeApi:
        def __init__(self, *args, **kwargs):
            self.room = _Rooms()

        async def aclose(self):
            pass

    with patch.object(meetings, "LiveKitAPI", _FakeApi), \
         patch.object(meetings.LOGGER, "warning") as warned:
        meetings._disconnect_livekit_room("jkinco-broken")
        assert warned.called, "真正的故障必须留下痕迹"


def test_ending_a_meeting_asks_for_the_room_to_be_torn_down():
    """接线本身也要锁住:函数写好了但没被调用等于没做。"""
    import inspect

    assert "_disconnect_livekit_room(meeting[\"room_name\"])" in inspect.getsource(
        meetings._end_meeting_record
    )
