import os
import tempfile
import base64
import io
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("JKINCO_AUTH", "admin:123456")
os.environ.setdefault("JKINCO_LOGIN_MAX_FAILURES", "50")
os.environ.setdefault("JKINCO_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-api-tests-"))
# 外部服务端点强制覆盖(而非 setdefault):开发者 shell 载入真实 .env 时,
# setdefault 会保留真实地址,导致测试真实调用大模型或向生产钉钉群推送。
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret-at-least-thirty-two-characters")
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://meet.example.com/livekit")

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app, template_supports_scene
from backend import meetings as meeting_service
from backend import core
from helpers import solve_captcha


def register_test_user(client, username: str, display_name: str):
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    return client.post("/api/auth/register", json={
        "username": username,
        "display_name": display_name,
        "password": "StrongPass123",
        "captcha_token": challenge["token"],
        "captcha_answer": answer,
    })


def test_auth_and_history_contract():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        response = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert response.status_code == 200
        assert client.get("/api/auth/me").json()["username"] == "admin"
        assert client.get("/api/history").status_code == 200


def test_scene_contract():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        response = client.post("/api/classify", json={
            "transcript": (
                "工程例会：施工单位汇报上周混凝土浇筑进度，监理单位要求本周整改临边防护，"
                "建设单位确认下周节点并报送专项施工方案。"
            ),
        })
        assert response.status_code == 200
        assert response.json()["mode"] == "talk"


def test_scene_api_canonicalizes_aliases_and_rejects_unknown_modes_early():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        alias = client.post("/api/classify", json={"transcript": "普通内容", "mode": "工程例会"})
        assert alias.status_code == 200
        assert alias.json()["mode"] == "talk"

        invalid = client.post("/api/classify", json={"transcript": "普通内容", "mode": "engineering-ish"})
        assert invalid.status_code == 400
        assert "场景" in invalid.json()["detail"]

        process = client.post("/api/process", data={"app_mode": "engineering-ish"})
        assert process.status_code == 400
        assert "场景" in process.json()["detail"]

        exported = client.post("/api/export/docx", json={"summary": "测试纪要", "mode": "engineering-ish"})
        assert exported.status_code == 400
        pushed = client.post("/api/dingtalk/push", json={"summary": "测试纪要", "mode": "engineering-ish"})
        assert pushed.status_code == 400


def test_custom_template_scene_matrix_is_closed_by_default():
    auto_template = {"scenario": "auto"}
    talk_template = {"scenario": "talk"}
    general_template = {"scenario": "general"}
    assert template_supports_scene(auto_template, "auto")
    assert template_supports_scene(auto_template, "talk")
    assert template_supports_scene(auto_template, "general")
    assert template_supports_scene(talk_template, "talk")
    assert not template_supports_scene(talk_template, "auto")
    assert not template_supports_scene(talk_template, "general")
    assert template_supports_scene(general_template, "general")
    assert not template_supports_scene(general_template, "talk")


def test_process_and_export_reject_cross_scene_custom_templates():
    fake_talk_template = {"id": "talk-template", "scenario": "talk", "name": "工程模板"}
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        with patch("backend.main.get_template", return_value=fake_talk_template):
            auto_process = client.post("/api/process", data={
                "live_text": "这是普通会议文本。",
                "app_mode": "auto",
                "custom_template_id": "talk-template",
            })
            assert auto_process.status_code == 400
            assert "跨场景" in auto_process.json()["detail"]

            wrong_export = client.post("/api/export/docx", json={
                "summary": "通用会议纪要",
                "mode": "general",
                "custom_template_id": "talk-template",
            })
            assert wrong_export.status_code == 400
            assert "不匹配" in wrong_export.json()["detail"]


def test_review_persists_auditable_scene_correction_and_drops_old_scene_template():
    record_id = core.save_meeting_history_record(
        "施工单位汇报钢筋绑扎。",
        "原工程纪要",
        "已生成",
        "talk",
        "测试",
        owner_username="admin",
        classification={
            "requested_mode": "auto",
            "predicted_mode": "talk",
            "final_mode": "talk",
            "source": "auto",
            "reason": "测试原判定",
            "version": "engineering-evidence-v2",
        },
        custom_template_id="talk-template",
        custom_template_name="工程模板",
    )
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        with (
            patch("backend.main.get_template", return_value={"scenario": "talk"}),
            patch("backend.main.core.generate_meeting_overview", return_value="校核概览"),
        ):
            response = client.put(
                f"/api/history/{record_id}/review",
                json={"summary": "人工确认这是通用会议。", "mode": "general"},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "general"
    assert body["custom_template_id"] == ""
    assert body["classification"]["predicted_mode"] == "talk"
    assert body["classification"]["final_mode"] == "general"
    assert body["classification"]["corrected"] is True
    assert body["classification"]["correction_events"][-1]["to_mode"] == "general"

    stored = next(item for item in core.load_meeting_history() if item.get("id") == record_id)
    assert "custom_template_id" not in stored
    assert stored["classification"]["original_mode"] == "talk"


def test_generic_meeting_does_not_use_engineering_scene():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        for transcript in (
            "产品项目周会讨论研发进度、数据质量、账号安全和功能验收，决定周五完成联调。",
            "各部门月度汇报，讨论经营风险、预算、人员安排和下月重点工作。",
        ):
            response = client.post("/api/classify", json={"transcript": transcript})
            assert response.status_code == 200
            assert response.json()["mode"] == "general"


def test_registration_persists_and_can_login_again():
    with TestClient(app) as client:
        registered = register_test_user(client, "meeting_user", "会议用户")
        assert registered.status_code == 201
        assert client.get("/api/auth/me").json()["display_name"] == "会议用户"
        assert client.post("/api/auth/logout").status_code == 200
        assert client.post("/api/auth/login", json={"username": "meeting_user", "password": "StrongPass123"}).status_code == 200


def test_meeting_and_history_are_isolated_between_accounts():
    with TestClient(app) as admin:
        admin.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        meeting = admin.post("/api/meetings", json={"title": "管理员私有会议"}).json()
        record_id = core.save_meeting_history_record(
            "管理员讨论交付计划。", "管理员私有纪要", "已生成",
            "general", "测试", owner_username="admin",
        )
        assert any(item["id"] == record_id for item in admin.get("/api/history").json()["items"])

    with TestClient(app) as other:
        assert register_test_user(other, "privacy_user", "隐私验收用户").status_code == 201
        assert other.get("/api/auth/me").json()["role"] == "普通用户"
        assert all(item["id"] != meeting["id"] for item in other.get("/api/meetings").json()["items"])
        assert all(item["id"] != record_id for item in other.get("/api/history").json()["items"])
        assert other.get(f"/api/history/{record_id}").status_code == 404

        other_record = core.save_meeting_history_record(
            "普通用户的个人备忘。", "普通用户纪要", "已生成",
            "personal", "测试", owner_username="privacy_user",
        )
        assert any(item["id"] == other_record for item in other.get("/api/history").json()["items"])

        joined = other.post(
            f"/api/meetings/{meeting['meeting_code']}/join",
            json={"display_name": "隐私验收用户"},
        )
        assert joined.status_code == 200
        assert any(item["id"] == meeting["id"] for item in other.get("/api/meetings").json()["items"])

    with TestClient(app) as admin:
        admin.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert admin.get("/api/auth/me").json()["role"] == "平台管理员"
        ids = {item["id"] for item in admin.get("/api/history").json()["items"]}
        assert record_id in ids and other_record in ids


def test_profile_persists_across_sessions():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    avatar = buffer.getvalue()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        response = client.put(
            "/api/profile",
            data={"display_name": "交付管理员", "remove_avatar": "false"},
            files={"avatar": ("avatar.png", avatar, "image/png")},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "交付管理员"
        # 落库前统一缩放重编码成 WebP:原先存的是上传的原图,/api/auth/me 每次
        # 打开页面都要把它整个返回,实测单个响应 1.6MB。
        assert response.json()["avatar_data"].startswith("data:image/webp;base64,")

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        profile = client.get("/api/profile").json()
        assert profile["display_name"] == "交付管理员"
        assert profile["avatar_data"].startswith("data:image/webp;base64,")


def test_large_avatar_is_downscaled_before_storage():
    """大图必须被压小后才落库。

    这条守的是一个真实故障:头像原样存库,导致 /api/auth/me 单次响应 1.6MB,
    成为全平台流量第一名。只断言格式是 WebP 不够 —— 不缩放也能是 WebP,
    所以这里同时断言尺寸和体积。
    """
    # 用真随机像素:规则花纹会被 PNG 压到很小,样本不够大这条断言就失去意义。
    # 尺寸取 800x600,原图约 1.4MB —— 既足够大,又不触发 2MB 的上传上限。
    width, height = 800, 600
    large = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buffer = io.BytesIO()
    large.save(buffer, format="PNG")
    raw = buffer.getvalue()
    assert 500_000 < len(raw) < 2 * 1024 * 1024, f"测试样本大小不合适:{len(raw)} 字节"

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        response = client.put(
            "/api/profile",
            data={"display_name": "交付管理员", "remove_avatar": "false"},
            files={"avatar": ("avatar.png", raw, "image/png")},
        )
        assert response.status_code == 200
        stored = response.json()["avatar_data"]
        assert stored.startswith("data:image/webp;base64,")
        decoded = base64.b64decode(stored.split(",", 1)[1])
        with Image.open(io.BytesIO(decoded)) as shrunk:
            assert max(shrunk.size) <= 256, f"未缩放,实际尺寸 {shrunk.size}"
        assert len(decoded) < 200_000, f"存储体积仍然过大:{len(decoded)} 字节"
        assert len(decoded) < len(raw) / 5, (
            f"压缩效果不足:原图 {len(raw)} 字节,存储 {len(decoded)} 字节"
        )


def test_username_preserves_case_and_uniqueness_is_case_insensitive():
    with TestClient(app) as client:
        registered = register_test_user(client, "MixedCase_01", "大小写用户")
        assert registered.status_code == 201
        assert registered.json()["user"]["username"] == "MixedCase_01"
        assert client.post("/api/auth/logout").status_code == 200
        # 登录大小写不敏感,会话使用注册时的规范用户名
        login = client.post("/api/auth/login", json={"username": "mixedcase_01", "password": "StrongPass123"})
        assert login.status_code == 200
        assert client.get("/api/auth/me").json()["username"] == "MixedCase_01"

    with TestClient(app) as duplicate:
        assert register_test_user(duplicate, "MIXEDCASE_01", "重复用户").status_code == 409


def test_export_rejects_empty_and_cleans_temp_file():
    import glob

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert client.post("/api/export/docx", json={"summary": "   ", "mode": "general"}).status_code == 400
        assert client.post("/api/export/rtf", json={"summary": "内容", "mode": "general"}).status_code == 404

        before = set(glob.glob(str(core.EXPORT_DIR / "*")))
        response = client.post("/api/export/docx", json={"summary": "# 会议纪要\n讨论了交付计划。", "mode": "general"})
        assert response.status_code == 200
        assert len(response.content) > 0
        # 响应结束后后台任务应已删除临时文件,导出目录不残留新文件
        after = set(glob.glob(str(core.EXPORT_DIR / "*")))
        assert after <= before


def test_process_live_text_records_owner():
    import time as _time

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        response = client.post(
            "/api/process",
            data={
                "live_text": "同步项目预算与人员安排，下周提交月度计划。",
                "process_mode": "只转写，不推送",
                "app_mode": "general",
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = _time.time() + 60
        job = {}
        while _time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job.get("status") in {"completed", "failed"}:
                break
            _time.sleep(0.3)
        assert job.get("status") == "completed", job
        record_id = job["result"]["record_id"]
        record = next(item for item in core.load_meeting_history() if item.get("id") == record_id)
        assert record.get("owner_username") == "admin"


def test_realtime_meeting_vertical_contract():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        created = client.post(
            "/api/meetings",
            json={"title": "实时工程例会", "realtime_transcription_enabled": True, "auto_minutes_enabled": True},
        )
        assert created.status_code == 200
        meeting = created.json()
        assert meeting["status"] == "active"
        assert len(meeting["meeting_code"].split("-")) == 3

        joined = client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "测试主持人"})
        assert joined.status_code == 200
        session = joined.json()
        assert session["role"] == "host"
        assert session["token"].count(".") == 2
        # 对照配置值而非字面量:环境由 conftest.py 统一设定,
        # 此处要断言的契约是「返回配置的信令地址」,不是某个具体 URL。
        assert session["livekit_url"] == os.environ["LIVEKIT_PUBLIC_URL"]

        message = client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "会议开始"})
        assert message.status_code == 200
        assert client.get(f"/api/meetings/{meeting['id']}/chat").json()["items"][0]["message"] == "会议开始"

        assert client.post(f"/api/meetings/{meeting['id']}/lock").json()["is_locked"] is True
        assert client.post(f"/api/meetings/{meeting['id']}/unlock").json()["is_locked"] is False
        assert client.post(f"/api/meetings/{meeting['id']}/leave", json={}).status_code == 200


def test_login_and_register_issue_identical_cookie_attributes():
    """登录与注册下发的是同一套会话 cookie,属性必须一致。

    两处曾各写一遍 set_cookie,导致登录读 JKINCO_COOKIE_SECURE 而注册不读。
    现已收敛到 attach_session_cookie,这里锁死两者行为一致。
    """
    def attrs(headers):
        raw = headers.get("set-cookie", "")
        return {
            part.split("=", 1)[0].strip().lower()
            for part in raw.split(";")[1:]
        }

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        assert login.status_code == 200
        login_attrs = attrs(login.headers)

    with TestClient(app) as client:
        registered = register_test_user(client, "cookie_parity", "同款 cookie")
        assert registered.status_code == 201
        register_attrs = attrs(registered.headers)

    assert login_attrs == register_attrs, f"登录 {login_attrs} 与注册 {register_attrs} 的 cookie 属性不一致"
    for required in ("httponly", "samesite", "max-age", "path"):
        assert required in login_attrs, f"会话 cookie 缺少 {required}"


def test_heartbeat_throttle_keeps_participant_alive():
    """心跳落盘做了节流,但绝不能让在线成员被判成离线。

    节流窗口内重复轮询不再写库(降低大会写压力),
    超过窗口后必须立刻续上心跳,且始终远早于 PARTICIPANT_STALE_SECONDS。
    """
    import sqlite3

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        meeting = client.post("/api/meetings", json={"title": "心跳节流验证"}).json()
        client.post(f"/api/meetings/{meeting['id']}/join", json={"display_name": "心跳用户"})

        def heartbeat_at():
            with sqlite3.connect(meeting_service.DB_PATH) as connection:
                return connection.execute(
                    "SELECT last_heartbeat_at FROM meeting_participants"
                    " WHERE meeting_id=? AND username='admin' AND connection_status='connected'",
                    (meeting["id"],),
                ).fetchone()[0]

        first = heartbeat_at()
        client.get(f"/api/meetings/{meeting['id']}")
        assert heartbeat_at() == first, "节流窗口内不应重复写库"

        # 把心跳人为拨回到窗口之外,下一次轮询必须续上
        stale = first - meeting_service.HEARTBEAT_WRITE_INTERVAL_SECONDS - 5
        with sqlite3.connect(meeting_service.DB_PATH) as connection:
            connection.execute(
                "UPDATE meeting_participants SET last_heartbeat_at=? WHERE meeting_id=? AND username='admin'",
                (stale, meeting["id"]),
            )
        client.get(f"/api/meetings/{meeting['id']}")
        refreshed = heartbeat_at()
        assert refreshed > stale, "超过节流窗口后必须续上心跳"

        # 节流窗口必须远小于判离线阈值,否则活跃成员会被扫描线程误清
        assert meeting_service.HEARTBEAT_WRITE_INTERVAL_SECONDS * 3 < meeting_service.PARTICIPANT_STALE_SECONDS

        # 续过心跳的成员不会被空闲扫描判离线
        meeting_service._sweep_idle_meetings_once()
        with sqlite3.connect(meeting_service.DB_PATH) as connection:
            status = connection.execute(
                "SELECT connection_status FROM meeting_participants WHERE meeting_id=? AND username='admin'",
                (meeting["id"],),
            ).fetchone()[0]
        assert status == "connected", "刚续过心跳的成员不应被判离线"


def test_scheduled_meeting_preview_does_not_end_before_start():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        scheduled_at = meeting_service.time.time() + 3600
        created = client.post(
            "/api/meetings",
            json={"title": "预约项目协调会", "scheduled_start_at": scheduled_at},
        )
        assert created.status_code == 200
        meeting = created.json()
        assert meeting["status"] == "scheduled"
        assert meeting["actual_start_at"] is None
        assert abs(meeting["scheduled_start_at"] - scheduled_at) < 1

        listed = client.get("/api/meetings").json()["items"]
        assert any(item["id"] == meeting["id"] and item["status"] == "scheduled" for item in listed)

        joined = client.post(
            f"/api/meetings/{meeting['meeting_code']}/join",
            json={"display_name": "预约主持人"},
        )
        assert joined.status_code == 200
        assert joined.json()["meeting"]["status"] == "scheduled"
        assert joined.json()["preview_mode"] is True
        assert client.post(f"/api/meetings/{meeting['id']}/end").status_code == 409
        assert client.post(f"/api/meetings/{meeting['id']}/leave", json={}).status_code == 200
        assert client.get(f"/api/meetings/{meeting['id']}").json()["status"] == "scheduled"

        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meetings SET scheduled_start_at=? WHERE id=?",
                (meeting_service.time.time() - 1, meeting["id"]),
            )
        started = client.post(
            f"/api/meetings/{meeting['meeting_code']}/join",
            json={"display_name": "预约主持人"},
        )
        assert started.status_code == 200
        assert started.json()["meeting"]["status"] == "active"
        assert started.json()["preview_mode"] is False


def test_scheduled_meeting_reschedule_requires_owner_and_preserves_link():
    suffix = uuid.uuid4().hex[:8]
    with TestClient(app) as owner:
        assert register_test_user(owner, f"schedule_owner_{suffix}", "预约创建者").status_code == 201
        original_start = meeting_service.time.time() + 7_200
        original_end = original_start + 3_600
        created = owner.post("/api/meetings", json={
            "title": "每周项目协调会",
            "scheduled_start_at": original_start,
            "scheduled_end_at": original_end,
            "recurrence": "weekly",
        })
        assert created.status_code == 200, created.text
        meeting = created.json()

        with TestClient(app) as other:
            assert register_test_user(other, f"schedule_other_{suffix}", "其他用户").status_code == 201
            denied = other.patch(f"/api/meetings/{meeting['id']}/schedule", json={
                "scheduled_start_at": original_start + 86_400,
                "scheduled_end_at": original_end + 86_400,
                "scope": "series",
            })
            assert denied.status_code == 403

        moved_start = original_start + 86_400
        moved = owner.patch(f"/api/meetings/{meeting['id']}/schedule", json={
            "scheduled_start_at": moved_start,
            "scheduled_end_at": moved_start + 5_400,
            "scope": "occurrence",
        })
        assert moved.status_code == 200, moved.text
        updated = moved.json()
        assert updated["meeting_code"] == meeting["meeting_code"]
        assert updated["room_name"] == meeting["room_name"]
        assert updated["scheduled_start_at"] == moved_start
        assert updated["recurrence_anchor_at"] == meeting["recurrence_anchor_at"]
        assert updated["recurrence_duration_seconds"] == 3_600


def test_meeting_host_end_and_empty_room_timeout():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})

        explicit = client.post(
            "/api/meetings",
            json={"title": "主持人结束验收", "auto_minutes_enabled": False},
        ).json()
        ended = client.post(f"/api/meetings/{explicit['id']}/end")
        assert ended.status_code == 200
        assert ended.json()["status"] != "active"
        assert ended.json()["ended_at"] is not None

        idle = client.post(
            "/api/meetings",
            json={"title": "空房自动结束验收", "auto_minutes_enabled": False},
        ).json()
        assert client.post(
            f"/api/meetings/{idle['id']}/join",
            json={"display_name": "测试主持人"},
        ).status_code == 200
        assert client.post(f"/api/meetings/{idle['id']}/leave", json={}).status_code == 200

        now = meeting_service.time.time()
        expired_at = now - meeting_service.EMPTY_ROOM_TIMEOUT_SECONDS - 1
        with meeting_service.db() as connection:
            connection.execute(
                "UPDATE meetings SET created_at=?, updated_at=? WHERE id=?",
                (expired_at, expired_at, idle["id"]),
            )
            connection.execute(
                "UPDATE meeting_participants SET last_heartbeat_at=? WHERE meeting_id=?",
                (expired_at, idle["id"]),
            )

        assert meeting_service._sweep_idle_meetings_once(now) == 1
        auto_ended = client.get(f"/api/meetings/{idle['id']}").json()
        assert auto_ended["status"] != "active"
        assert auto_ended["ended_at"] is not None


def test_ended_meeting_record_is_always_viewable():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
        meeting = client.post(
            "/api/meetings",
            json={"title": "无转写会议记录", "auto_minutes_enabled": False},
        ).json()
        assert client.post(f"/api/meetings/{meeting['id']}/end").status_code == 200

        record = client.get(f"/api/meetings/{meeting['id']}/record")
        assert record.status_code == 200
        payload = record.json()
        assert payload["title"] == "无转写会议记录"
        assert payload["source"] == "实时会议"
        assert payload["read_only"] is True
        assert "暂无可用" in payload["overview"]


def test_static_assets_carry_cache_headers_but_html_does_not():
    """静态图片必须可缓存,HTML 外壳必须不可缓存。

    修复前根目录下的图片(logo、favicon,合计约 470KB)不带任何缓存头,每次打开
    页面都要重新下载 —— 手机上这部分开销和首屏一样大。
    同时 HTML 外壳必须保持 no-cache:它内嵌了带哈希的资源文件名,一旦被缓存住,
    发布新版也刷不掉。
    """
    if not (Path(__file__).resolve().parent.parent / "frontend" / "dist").exists():
        pytest.skip("前端未构建（frontend/dist 不存在）")
    with TestClient(app) as client:
        for name in ("jkinco-listen-logo.png", "favicon.png"):
            response = client.get(f"/{name}")
            if response.status_code != 200:
                continue
            assert "max-age" in response.headers.get("cache-control", ""), f"{name} 缺少缓存头"

        for path in ("/", "/index.html"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers.get("cache-control") == "no-cache", f"{path} 不该被缓存"
