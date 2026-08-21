"""到点了、人在房里,会议要真的开始 —— 而且不能依赖「又有人加入」这个事件。

预约链接自建好起就常驻有效,提前进来调设备是常态。于是有个看起来很危险的场景:
会议约在 14:00,所有人 13:50 就都进来了(预览态,状态仍是 scheduled);人齐了
直接开始说;14:00 之后没有任何新人加入。

如果「预约 → 进行中」只挂在 join 上,这场会就永远停在 scheduled:实时转写在
预览态下照常工作(ASR 路由不检查会议状态),字幕照常显示、转写照常落库,所有人
都以为在正常记录,但会议永远不会结束,也就永远不会生成纪要 —— 一整场会消失。

实际实现没有这个洞:转换同时挂在会议详情接口上,而那是会议室每 2.5–8 秒一次的
高频轮询点 —— 只要房里还有人开着页面,它必然被触发,不需要任何人再次加入。

本文件钉住的就是这一点,以及它的几条边界(空房不开、未到点不开、已取消不开)。
这些条件散落在一个高频接口里,很容易在优化轮询开销时被顺手改掉。
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _login(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})


def _set(meeting_id: str, **columns) -> None:
    assignments = ",".join(f"{name}=?" for name in columns)
    with meeting_service.db() as connection:
        connection.execute(f"UPDATE meetings SET {assignments} WHERE id=?", (*columns.values(), meeting_id))


def _schedule(client: TestClient, title: str, start_in: float) -> dict:
    response = client.post("/api/meetings", json={"title": title, "scheduled_start_at": time.time() + start_in})
    assert response.status_code == 200, response.text
    return response.json()


def _poll_detail(client: TestClient, meeting_id: str) -> dict:
    """会议室的高频轮询点 —— 自动开始就挂在这里。"""
    return client.get(f"/api/meetings/{meeting_id}").json()


def test_meeting_starts_on_the_room_poll_without_anyone_joining_again():
    """核心回归:所有人提前进场,到点后无人再加入,靠房间轮询把会议开起来。"""
    with TestClient(app) as client:
        _login(client)
        created = _schedule(client, "人都到齐的预约会", 600)
        meeting_id = created["id"]

        joined = client.post(f"/api/meetings/{created['meeting_code']}/join", json={"display_name": "参会人"})
        assert joined.status_code == 200
        assert joined.json().get("preview_mode") is True, "开始前进入应为预览态"
        assert _poll_detail(client, meeting_id)["status"] == "scheduled"

        # 时间到了,但没有任何人再调 join —— 只有房间还在轮询
        _set(meeting_id, scheduled_start_at=time.time() - 60)
        assert _poll_detail(client, meeting_id)["status"] == "active", (
            "到点且房里有人,会议却没开始 —— 这场会将永远不会结束,也就永远不会生成纪要"
        )


def test_an_empty_room_is_not_started():
    """房里没人就不能开:试完设备走开,会议要留在那儿等着按时开始。"""
    with TestClient(app) as client:
        _login(client)
        created = _schedule(client, "没人来的预约会", 600)
        _set(created["id"], scheduled_start_at=time.time() - 60)
        assert _poll_detail(client, created["id"])["status"] == "scheduled", "房里没人却把会议开起来了"


def test_future_meetings_are_left_alone():
    """还没到点的会议不能被提前开起来 —— 提前开会要走 /start,是个显式动作。"""
    with TestClient(app) as client:
        _login(client)
        created = _schedule(client, "还没到点", 3600)
        client.post(f"/api/meetings/{created['meeting_code']}/join", json={"display_name": "早到的人"})
        assert _poll_detail(client, created["id"])["status"] == "scheduled"


def test_cancelled_meetings_are_never_started():
    with TestClient(app) as client:
        _login(client)
        created = _schedule(client, "已取消", 600)
        client.post(f"/api/meetings/{created['meeting_code']}/join", json={"display_name": "参会人"})
        _set(created["id"], status="cancelled", scheduled_start_at=time.time() - 60)
        assert _poll_detail(client, created["id"])["status"] == "cancelled"


def test_the_room_shows_preview_state_so_nobody_talks_into_the_void():
    """预览态必须能被界面识别 —— 否则提前进来的人会以为已经在正式记录了。"""
    with TestClient(app) as client:
        _login(client)
        created = _schedule(client, "提前进场", 600)
        joined = client.post(f"/api/meetings/{created['meeting_code']}/join", json={"display_name": "参会人"}).json()
        assert joined["preview_mode"] is True
        assert _poll_detail(client, created["id"])["status"] == "scheduled"


# --- 提前开会:后端有、前端也必须真的接上 ---
# /start 在后端写好很久了,注释也写明了用途(「不等了,现在就开」),而且是产品
# 明确要的两条路之一(「开始前可进入调试设备,或直接开会」)。但前端一次都没调过 ——
# 接口存在不等于能力存在。人提前到齐时没有任何办法开始:只能干等到点,或者照常
# 说话,而到点之前说的话在数据起点之下,不会进入纪要。

def _meeting_module_source() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "frontend" / "src" / "MeetingModule.tsx"
    return path.read_text(encoding="utf-8")


def test_frontend_actually_calls_start():
    source = _meeting_module_source()
    assert "/start" in source, "前端没有任何地方调用 /start —— 「提前开会」是不可达的"


def test_start_control_only_shows_in_preview_state():
    """会议已经在进行中还显示「现在就开始」会让人以为没开成。"""
    source = _meeting_module_source()
    assert 'meetingStatus === "scheduled"' in source
    assert "现在就开始" in source


def test_start_is_host_only_in_the_ui():
    """后端限主持人;界面上给所有人显示按钮,普通参会者只会点出一个 403。"""
    source = _meeting_module_source()
    block = source.split("现在就开始", 1)[0][-400:]
    assert 'session.role === "host"' in block, "「现在就开始」没有限定主持人可见"
