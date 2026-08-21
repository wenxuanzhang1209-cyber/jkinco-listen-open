"""被挤出热表的历史记录仍然要能打开。

热表只保留最近 HISTORY_MAX_ITEMS(默认 300)条,更早的追加进
meetings-archive.jsonl。而那个文件此前是只写的 —— 全代码库没有任何读取路径。
后果不是「数据丢了」(它还在盘上),而是用户那边看起来就是丢了:

会议表里存着 history_record_id。一旦对应的历史记录被挤出热表,那场会议的
「查看纪要」就此变成 404 —— 会议还在列表里,点进去纪要没了。平台跑得越久,
越多的老会议会这样,而且是静默发生的。

归档记录按只读返回:它已经不在热表里,任何写回都会把它再弄丢一次。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import jkinco_history as history
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = history.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


@pytest.fixture
def archived_record():
    """直接往归档文件里放一条记录,模拟它早已被挤出热表。"""
    record = {
        "id": "archived-record-under-test",
        "title": "去年的项目复盘会",
        "transcript": "张经理：这是很久以前的一场会。",
        "summary": "## 会议纪要\n- 归档记录的正文",
        "overview": "概览",
        "mode": "general",
        "mode_label": "会议纪要",
        "created_at": time.time() - 400 * 86400,
        "owner_username": ADMIN_USERNAME,
        "source": "录音输入",
    }
    history.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(history.HISTORY_ARCHIVE_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    yield record
    try:
        history.HISTORY_ARCHIVE_FILE.unlink()
    except OSError:
        pass


def test_archived_record_is_still_reachable_by_id(archived_record):
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        response = client.get(f"/api/history/{archived_record['id']}")
        assert response.status_code == 200, "归档记录打不开 —— 老会议的「查看纪要」会变成 404"
        body = response.json()
        assert archived_record["title"] in body.get("title", "")
        assert "归档记录的正文" in body.get("summary", "")


def test_archived_record_is_read_only(archived_record):
    """它已不在热表,任何写回都会把它再弄丢一次。"""
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        body = client.get(f"/api/history/{archived_record['id']}").json()
        assert body.get("read_only") is True
        assert body.get("archived") is True


def test_archived_lookup_respects_ownership(archived_record):
    """归档回落不能绕开归属校验 —— 那会把它变成一条读取他人纪要的旁路。"""
    with TestClient(app) as outsider:
        outsider.post("/api/auth/guest", json={})
        assert outsider.get(f"/api/history/{archived_record['id']}").status_code == 404


def test_unknown_id_still_404s():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        assert client.get("/api/history/no-such-record-anywhere").status_code == 404


def test_archive_lookup_tolerates_corrupt_lines(archived_record):
    """归档是追加写的 jsonl,断电可能留下半行 —— 不能因此让整条查询失败。"""
    with open(history.HISTORY_ARCHIVE_FILE, "a", encoding="utf-8") as handle:
        handle.write('{"id": "half-written-record", "titl\n')
    assert history.find_archived_record(archived_record["id"]) is not None


def test_missing_archive_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_ARCHIVE_FILE", tmp_path / "does-not-exist.jsonl")
    assert history.find_archived_record("anything") is None


def test_overflow_actually_lands_in_the_archive(monkeypatch, tmp_path):
    """自检:若热表根本不会溢出,上面几条测的就是个不存在的场景。"""
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "meetings.json")
    monkeypatch.setattr(history, "HISTORY_ARCHIVE_FILE", tmp_path / "archive.jsonl")
    monkeypatch.setattr(history, "HISTORY_MAX_ITEMS", 3)
    items = [{"id": f"r{i}", "title": f"会议{i}", "created_at": time.time() - i} for i in range(6)]
    history.write_meeting_history(items)

    kept = json.loads((tmp_path / "meetings.json").read_text(encoding="utf-8"))
    assert len(kept) == 3, "热表未按上限裁剪"
    assert history.find_archived_record("r5") is not None, "溢出的记录没进归档"
    assert history.find_archived_record("r0") is None, "留在热表的记录不该在归档里"
