"""改纪要的权限,两条路径必须是同一档。

backend/history.py 的 history_editable_by 把策略写得很明确:

    写权限比读权限窄一档:只有所有者和管理员能改。参会成员能看、能导出,
    但不能覆盖主持人定稿 —— 一场会有多个参会者,谁都能改就没有「定稿」可言了。

但 PATCH /api/meetings/{id}/minutes 原先只要 _require_member,等于给同一份记录
开了第二道更宽的门。

这不是纸面问题。纪要生成失败(minutes_status='failed')或判为无内容('empty')
的会议永远拿不到 history_record_id,GET /record 就会一直回落到
meeting_minutes_versions —— 那种会议里,任何参会者都能把伪造的「正式纪要」
塞进去,而全体成员看到的就是它。

顺带记一笔:这两个 /minutes 接口前端一次都没调用过。没有消费方不等于没有攻击面,
接口存在就能被直接调用。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.history import history_editable_by
from backend.main import app
from helpers import solve_captcha

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")
INTRUDER = "minutes_intruder"


def _register(client: TestClient, username: str) -> None:
    challenge = client.get("/api/auth/captcha").json()
    client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": solve_captcha(challenge["token"]),
    })


@pytest.fixture
def meeting_with_two_people():
    """主持人开会,另一个人加入 —— 后者是成员,但不是主持人。"""
    with TestClient(app) as host, TestClient(app) as member:
        host.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        meeting = host.post("/api/meetings", json={"title": "定稿归属"}).json()
        host.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "主持人"})

        _register(member, INTRUDER)
        member.post("/api/auth/login", json={"username": INTRUDER, "password": "StrongPass123"})
        joined = member.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "参会人"})
        assert joined.status_code == 200, joined.text
        yield host, member, meeting


def test_a_participant_cannot_rewrite_the_minutes(meeting_with_two_people):
    """核心回归。"""
    _host, member, meeting = meeting_with_two_people
    response = member.patch(f"/api/meetings/{meeting['id']}/minutes",
                            json={"content_markdown": "## 伪造的会议结论\n- 全体同意"})
    assert response.status_code == 403, f"参会者改写了正式纪要:{response.status_code}"


def test_the_host_can_still_save(meeting_with_two_people):
    """收紧不能把功能一起收没了。"""
    host, _member, meeting = meeting_with_two_people
    response = host.patch(f"/api/meetings/{meeting['id']}/minutes",
                          json={"content_markdown": "## 结论\n- 方案通过"})
    assert response.status_code == 200, response.text
    assert response.json()["version"] >= 1


def test_a_non_member_is_still_rejected(meeting_with_two_people):
    _host, _member, meeting = meeting_with_two_people
    with TestClient(app) as stranger:
        _register(stranger, "minutes_stranger")
        stranger.post("/api/auth/login", json={"username": "minutes_stranger", "password": "StrongPass123"})
        response = stranger.patch(f"/api/meetings/{meeting['id']}/minutes",
                                  json={"content_markdown": "外人写的"})
        # 必须是 404 而不是 403:403 会确认「这个会议存在」,给按会议号枚举留口子。
        # 本模块其余接口都是这个口径,收紧权限时差点把它破坏掉。
        assert response.status_code == 404, f"非成员拿到了 {response.status_code},会泄露会议是否存在"


def test_the_forged_minutes_would_have_been_shown_to_everyone(meeting_with_two_people):
    """说明危害:没有历史记录的会议,/record 展示的就是 minutes_versions 的最新一版。"""
    host, _member, meeting = meeting_with_two_people
    host.patch(f"/api/meetings/{meeting['id']}/minutes", json={"content_markdown": "## 结论\n- 方案通过"})
    record = host.get(f"/api/meetings/{meeting['id']}/record").json()
    assert "方案通过" in record["summary"], "这条路径若不成立,上面的用例就失去了意义"


def test_both_edit_paths_share_one_policy():
    """守住「不会有第三条更宽的门」。"""
    import inspect

    source = inspect.getsource(meeting_service._register_meeting_content_routes)
    block = source.split("def save_minutes", 1)[1][:900]
    assert "_require_host" in block, "改纪要的权限又被放宽到成员了"
    # history 那条路的口径不能反过来被放宽
    assert history_editable_by({"owner_username": "someone"}, "other", admin=False) is False
