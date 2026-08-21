"""预约会议与重复会议的链接可用性契约。

用户反馈:预约成功后把链接发给别人,对方在会议开始前进不去。原因是 join 里有
一道「非主持人须在开始前 10 分钟之内」的闸门 —— 主持人自己能进,所以问题不易
自查,而收到链接的人只看到一句「会议尚未开始」。

改后的口径:
  - 链接从会议建好那刻起一直有效,任何成员随时可进;
  - 到点之前进来的是「预览」:照发 LiveKit 令牌(调试摄像头麦克风需要真的进房),
    但会议状态仍是「预约」——试完设备走开不消耗这一次,会议照常等着按时开始;
  - 想不等了就开,走 /start:状态转 active 之后才能结束、才会生成纪要;
  - 到点之后留 30 分钟宽限期,期间即使房里没人也不回收 —— 只填了开始时间的
    会议原先一到点就按空房 10 分钟回收,晚来几分钟的人会发现会议没了。

重复会议在此之上还要求「长期常驻」:同一条链接反复使用,每次结束后自动生成
本次纪要并回到预约状态,直到取消。
"""
from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _login(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})


def _schedule(client: TestClient, title: str, *, start_in: float, recurrence: str = "none") -> dict:
    payload = {"title": title, "scheduled_start_at": time.time() + start_in}
    if recurrence != "none":
        payload["recurrence"] = recurrence
    response = client.post("/api/meetings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_status(client: TestClient, meeting_id: str, wanted: set[str], timeout: float = 10.0) -> str:
    """纪要生成跑在 MEETING_EXECUTOR 上,结束请求返回时它往往还没跑完。

    不能只等「不是 processing」:重复会议要先落成 completed、再由续排改回
    scheduled,中间那一瞬会被误判成「没有回到预约态」。必须等目标状态本身,
    否则测的是竞态而不是行为。
    """
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = client.get(f"/api/meetings/{meeting_id}").json()["status"]
        if status in wanted:
            return status
        time.sleep(0.05)
    return status


def _set_status(meeting_id: str, **columns) -> None:
    assignments = ",".join(f"{name}=?" for name in columns)
    with meeting_service.db() as connection:
        connection.execute(f"UPDATE meetings SET {assignments} WHERE id=?", (*columns.values(), meeting_id))


def test_link_works_long_before_the_scheduled_time():
    """核心回归:开始前几小时,拿到链接的人就该进得去。"""
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "下午的项目会", start_in=6 * 3600)
        joined = client.post(
            f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"}
        )
        assert joined.status_code == 200, joined.text
        body = joined.json()
        assert body["preview_mode"] is True, "到点之前应当是预览"
        # 设备调试必须能真的进房,所以令牌照发。
        assert body["token"], "预览模式也要发 LiveKit 令牌,否则调不了摄像头麦克风"
        assert body["meeting"]["status"] == "scheduled", "预览不该把会议开起来"


def test_previewing_does_not_consume_the_meeting():
    """试完设备就走,会议必须仍在那儿等着按时开始。"""
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "明早的周会", start_in=12 * 3600)
        client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"})
        client.post(f"/api/meetings/{meeting['id']}/leave", json={})
        # 把心跳推老,让空闲清扫认定房里没人
        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meeting_participants SET last_heartbeat_at=?, connection_status='left' WHERE meeting_id=?",
                (time.time() - meeting_service.EMPTY_ROOM_TIMEOUT_SECONDS * 3, meeting["id"]),
            )
        meeting_service._sweep_idle_meetings_once()
        assert client.get(f"/api/meetings/{meeting['id']}").json()["status"] == "scheduled"


def test_start_lets_the_meeting_begin_ahead_of_schedule():
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "临时提前开", start_in=4 * 3600)
        client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})
        started = client.post(f"/api/meetings/{meeting['id']}/start")
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "active"
        # 只有开起来之后才结束得了 —— 结束正是生成纪要的入口。
        assert client.post(f"/api/meetings/{meeting['id']}/end").status_code == 200
        assert _wait_for_status(client, meeting["id"], {"completed", "scheduled"}) in {"completed", "scheduled"}


def test_start_is_idempotent_and_rejects_finished_meetings():
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "重复点击", start_in=3600)
        assert client.post(f"/api/meetings/{meeting['id']}/start").json()["status"] == "active"
        # 连点两下不该报错
        assert client.post(f"/api/meetings/{meeting['id']}/start").status_code == 200
        _set_status(meeting["id"], status="cancelled")
        assert client.post(f"/api/meetings/{meeting['id']}/start").status_code == 409


def test_grace_period_keeps_the_room_open_after_the_start_time():
    """只填了开始时间的会议,到点后 30 分钟内不回收。"""
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "晚到几分钟", start_in=60)
        # 必须先 join:空房判据取的是参会记录里最后一次心跳,没有参会记录时会
        # 回落到会议创建时刻(刚刚),永远凑不满「空置 10 分钟」——那样测的就不是
        # 宽限期,而是「刚建的会不会被回收」。
        client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})
        client.post(f"/api/meetings/{meeting['id']}/start")
        started_at = time.time() - meeting_service.EMPTY_ROOM_TIMEOUT_SECONDS * 2
        _set_status(meeting["id"], scheduled_start_at=started_at, actual_start_at=started_at)
        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meeting_participants SET last_heartbeat_at=?, connection_status='left' WHERE meeting_id=?",
                (started_at, meeting["id"]),
            )
        # 宽限期内:房里没人也不结束
        meeting_service._sweep_idle_meetings_once()
        assert client.get(f"/api/meetings/{meeting['id']}").json()["status"] == "active"
        # 过了宽限期:恢复原有的空房回收。约定的结束时刻要一起推过去 ——
        # 回收下限取「结束时刻」与「开始时刻+宽限」中较晚的那个,只推开始时刻的话
        # 仍会被尚未到来的结束时刻挡住(这正是该逻辑的本意)。
        expired = time.time() - meeting_service.SCHEDULED_START_GRACE_SECONDS - 60
        _set_status(meeting["id"], scheduled_start_at=expired, scheduled_end_at=expired)
        meeting_service._sweep_idle_meetings_once()
        assert client.get(f"/api/meetings/{meeting['id']}").json()["status"] != "active"


def test_recurring_link_survives_each_occurrence():
    """重复会议:同一条链接反复使用,结束后回到预约态等下一次。"""
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "每周例会", start_in=3600, recurrence="weekly")
        if meeting.get("recurrence") != "weekly":
            pytest.skip("此部署未启用重复会议")
        code, meeting_id = meeting["meeting_code"], meeting["id"]

        for _ in range(3):
            joined = client.post(f"/api/meetings/{code}/join", json={"display_name": "参会人"})
            assert joined.status_code == 200, "常驻链接每一次都该进得去"
            assert client.post(f"/api/meetings/{meeting_id}/start").json()["status"] == "active"
            assert client.post(f"/api/meetings/{meeting_id}/end").status_code == 200
            # 结束后回到预约,且下一次排在将来 —— 链接不变
            _wait_for_status(client, meeting_id, {"scheduled"})
            current = client.get(f"/api/meetings/{meeting_id}").json()
            assert current["status"] == "scheduled", f"重复会议结束后应回到预约态,实际 {current['status']}"
            assert current["meeting_code"] == code, "重复会议的链接必须保持不变"
            assert float(current["scheduled_start_at"]) > time.time(), "应已排到下一次"


def test_rolling_and_reaping_use_the_same_deadline():
    """两处判据必须一致,否则会议还能进人时就被滚到下一周。"""
    now = time.time()
    meeting = {"scheduled_start_at": now, "scheduled_end_at": None}
    assert meeting_service._reap_floor(meeting) == now + meeting_service.SCHEDULED_START_GRACE_SECONDS
    with_end = {"scheduled_start_at": now, "scheduled_end_at": now + 10 * 3600}
    assert meeting_service._reap_floor(with_end) == now + 10 * 3600


def test_stuck_recurring_meeting_is_revived():
    """续排若失败,这场周会会永久停在「已结束」—— 必须有兜底把它捞回来。

    续排挂在纪要生成之后,那一步失败(数据库锁、进程被杀在两条语句之间)时,
    此前没有任何机制能救它:下一周谁也进不去,而用户要的是「长期常驻,直到取消」。
    """
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "卡住的周会", start_in=3600, recurrence="weekly")
        if meeting.get("recurrence") != "weekly":
            pytest.skip("此部署未启用重复会议")
        expired = time.time() - meeting_service.SCHEDULED_START_GRACE_SECONDS - 60
        # 模拟「纪要生成完了,续排没跑成」的现场
        _set_status(meeting["id"], status="completed", scheduled_start_at=expired, scheduled_end_at=expired)

        meeting_service._sweep_idle_meetings_once()
        revived = client.get(f"/api/meetings/{meeting['id']}").json()
        assert revived["status"] == "scheduled", "卡住的重复会议应被兜底捞回"
        assert float(revived["scheduled_start_at"]) > time.time()
        assert revived["meeting_code"] == meeting["meeting_code"], "链接必须保持不变"


def test_abandoned_active_recurring_meeting_still_rolls():
    """会议开起来了却没人正常结束,这场周会也必须回到预约态。

    _roll_missed_recurrences 的兜底查询只捞 scheduled 和 completed,**不含
    active** —— 会议一旦转 active 又没走 /end(全员断网、浏览器直接关掉、进程被
    杀),它就落在兜底网之外,只能靠空房回收间接救。这条链路比 completed 那条
    长一截:先要把心跳过期的参会者标成 left,凑够空房时长,过了回收下限才结束,
    结束后才轮到续排。中间任何一环断掉,这场周会都会永久停在 active,下一周
    谁也进不去 —— 表现和「续排失败」一模一样,但成因完全不同。
    """
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "开着没关的周会", start_in=3600, recurrence="weekly")
        if meeting.get("recurrence") != "weekly":
            pytest.skip("此部署未启用重复会议")
        meeting_id, code = meeting["id"], meeting["meeting_code"]
        original_start = float(client.get(f"/api/meetings/{meeting_id}").json()["scheduled_start_at"])

        client.post(f"/api/meetings/{code}/join", json={"display_name": "参会人"})
        assert client.post(f"/api/meetings/{meeting_id}/start").json()["status"] == "active"

        # 现场:开始时刻与约定结束都已过去很久,参会者的心跳停在那时(异常掉线,
        # 没有发过 leave)。connection_status 仍是 connected —— 这正是「异常」的含义。
        abandoned_at = time.time() - meeting_service.SCHEDULED_START_GRACE_SECONDS - 86400
        _set_status(
            meeting_id,
            scheduled_start_at=abandoned_at,
            scheduled_end_at=abandoned_at,
            actual_start_at=abandoned_at,
        )
        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meeting_participants SET last_heartbeat_at=? WHERE meeting_id=?",
                (abandoned_at, meeting_id),
            )

        meeting_service._sweep_idle_meetings_once()
        assert _wait_for_status(client, meeting_id, {"scheduled"}) == "scheduled", (
            "转过 active 又被遗弃的重复会议没有回到预约态 —— 它落在续排兜底之外"
        )
        revived = client.get(f"/api/meetings/{meeting_id}").json()
        assert revived["meeting_code"] == code, "链接必须保持不变"
        next_start = float(revived["scheduled_start_at"])
        assert next_start > time.time(), "应已排到将来"
        # 相位不能因为「跳过了一次」而漂移:仍是同一个星期几、同一个时刻。
        drift = (next_start - original_start) % (7 * 86400)
        assert min(drift, 7 * 86400 - drift) < 1.0, (
            f"跳过一次之后星期几/时刻发生漂移,偏了 {min(drift, 7 * 86400 - drift):.0f} 秒"
        )


def test_cancelled_recurring_series_is_not_revived():
    """兜底不能把用户主动取消的系列又拉起来 —— 「直到取消重复会议」。"""
    with TestClient(app) as client:
        _login(client)
        meeting = _schedule(client, "已取消的周会", start_in=3600, recurrence="weekly")
        if meeting.get("recurrence") != "weekly":
            pytest.skip("此部署未启用重复会议")
        assert client.post(f"/api/meetings/{meeting['id']}/cancel").status_code == 200
        expired = time.time() - meeting_service.SCHEDULED_START_GRACE_SECONDS - 60
        _set_status(meeting["id"], scheduled_start_at=expired, scheduled_end_at=expired)

        meeting_service._sweep_idle_meetings_once()
        assert client.get(f"/api/meetings/{meeting['id']}").json()["status"] == "cancelled"


# --- 放开「提前进入」之后,其余门禁必须原样保留 ---
# 这道闸门原先顺带挡住了很多人,拆掉它等于把预约会议的准入面放大到「拿到会议号
# 即可随时进」。下面几条把其余门禁钉死:任何一条失守,都是拿着旧链接的人可以
# 长期蹲在会议室里。

def _guest_client() -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/api/auth/guest", json={})
    return client


@pytest.mark.parametrize(
    "setup, expected, label",
    [
        (lambda host, meeting: host.post(f"/api/meetings/{meeting['id']}/lock"), 423, "已锁定"),
        (lambda host, meeting: host.post(f"/api/meetings/{meeting['id']}/cancel"), 409, "已取消"),
    ],
)
def test_other_gates_still_apply_to_early_join(setup, expected, label):
    with TestClient(app) as host:
        _login(host)
        meeting = _schedule(host, f"{label}的会", start_in=6 * 3600)
        setup(host, meeting)
        guest = _guest_client()
        try:
            response = guest.post(
                f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "闯入者"}
            )
            assert response.status_code == expected, f"{label}的会议仍可提前进入"
        finally:
            guest.__exit__(None, None, None)


def test_guest_ban_still_applies_to_early_join():
    with TestClient(app) as host:
        _login(host)
        response = host.post(
            "/api/meetings",
            json={"title": "不许访客", "scheduled_start_at": time.time() + 6 * 3600, "allow_guest": False},
        )
        meeting = response.json()
        if meeting.get("allow_guest", True):
            pytest.skip("此部署未启用「不允许访客」开关")
        guest = _guest_client()
        try:
            joined = guest.post(
                f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "访客"}
            )
            assert joined.status_code == 403
        finally:
            guest.__exit__(None, None, None)


def test_only_the_host_can_start_early():
    """任何拿到链接的人都能提前进入,但「现在就开」只能是主持人。"""
    with TestClient(app) as host:
        _login(host)
        meeting = _schedule(host, "抢开", start_in=6 * 3600)
        guest = _guest_client()
        try:
            guest.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"})
            assert guest.post(f"/api/meetings/{meeting['id']}/start").status_code in {403, 404}
            assert host.get(f"/api/meetings/{meeting['id']}").json()["status"] == "scheduled"
        finally:
            guest.__exit__(None, None, None)
