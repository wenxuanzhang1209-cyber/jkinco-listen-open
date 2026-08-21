"""纪要版本号的分配必须原子,否则并发保存会 500 且丢内容。

取 MAX(version)+1 与 INSERT 之间原先不是原子的:两人同时保存(或一个人连点
两下)会算出相同版本号,撞 UNIQUE(meeting_id, version) 直接 500,刚写的纪要
就此丢失。实测 12 次并发只有 1 次成功、11 次报 IntegrityError。

自动归档路径同理:生成期间若有成员手工保存,整个事务回滚 —— 会议被误标为
failed、history_record_id 归空,而纪要已经计过费。
"""
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "devsecret1234567890abcdef")

import backend.meetings as M
from backend.main import app


def _login(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "123456"}).status_code == 200


def test_concurrent_minutes_saves_all_succeed():
    with TestClient(app) as client:
        _login(client)
        meeting_id = client.post("/api/meetings", json={"title": "纪要并发"}).json()["id"]
        client.post(f"/api/meetings/{meeting_id}/join", json={})

        codes = []

        def save(index):
            codes.append(
                client.patch(f"/api/meetings/{meeting_id}/minutes",
                             json={"content_markdown": f"第 {index} 版"}).status_code
            )

        threads = [threading.Thread(target=save, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert set(codes) == {200}, f"并发保存出现失败:{dict(Counter(codes))}"

        with M.db() as connection:
            versions = [r[0] for r in connection.execute(
                "SELECT version FROM meeting_minutes_versions WHERE meeting_id=? ORDER BY version",
                (meeting_id,))]
        assert len(versions) == 12, f"有保存被丢弃:只落库 {len(versions)} 版"
        assert len(set(versions)) == len(versions), f"版本号重复:{versions}"


def test_auto_archive_survives_a_concurrent_manual_save():
    """自动归档期间有人手工保存,归档不能被打断。"""
    from unittest.mock import patch

    with TestClient(app) as client:
        _login(client)
        meeting_id = client.post("/api/meetings",
                                 json={"title": "交叉", "auto_minutes_enabled": True}).json()["id"]
        client.post(f"/api/meetings/{meeting_id}/join", json={})
        with M.db() as connection:
            connection.execute(
                """INSERT INTO meeting_transcript_segments(id,meeting_id,participant_identity,sentence_id,
                   start_time_ms,end_time_ms,text,is_final,provider,deduplication_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("seg-race", meeting_id, "i1", 1, 0, 1000, "转写内容", 1, "local", "dedup-race", time.time()),
            )

        def manual():
            for i in range(6):
                client.patch(f"/api/meetings/{meeting_id}/minutes",
                             json={"content_markdown": f"手工第 {i} 版"})

        def auto():
            with patch.object(M.core, "generate_minutes", lambda *a, **k: "自动纪要"), \
                 patch.object(M.core, "generate_meeting_overview", lambda *a, **k: "概览"), \
                 patch.object(M.core, "infer_app_mode_best_effort", lambda *a, **k: ("talk", "理由")):
                M._finalize_minutes(meeting_id)

        threads = [threading.Thread(target=manual), threading.Thread(target=auto)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with M.db() as connection:
            row = dict(connection.execute(
                "SELECT minutes_status, history_record_id FROM meetings WHERE id=?", (meeting_id,)).fetchone())
            versions = [r[0] for r in connection.execute(
                "SELECT version FROM meeting_minutes_versions WHERE meeting_id=? ORDER BY version",
                (meeting_id,))]

    assert row["minutes_status"] != "failed", "归档被并发保存打断,会议被误标为失败"
    assert row["history_record_id"], "归档回滚导致 history_record_id 丢失"
    assert len(set(versions)) == len(versions), f"版本号重复:{versions}"
