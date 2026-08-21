"""只读路径不得污染历史缓存。

load_meeting_history() 每次都 deepcopy 整份历史,那是为了防止调用方改到缓存。
但读取路径的绝大多数调用只是「过一遍、挑出可见的、交给 serialize_history 生成
新字典」,一次也不写 —— 却要为 300 条带全文转写的记录付 0.91ms 的拷贝(实测
2.6MB 热表),而这正是打开工作台时最常走的那条路。

改用不拷贝的 iter_meeting_history() 之后,「调用方不改返回值」从一句注释变成了
必须守住的契约:一旦有人改了,污染的是进程内所有人共享的缓存,而且不会报错 ——
下一个读到的人直接拿到脏数据。所以在这里钉死。
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

import backend.core as core
import jkinco_history as history
from backend.main import app

ADMIN, _, PASSWORD = history.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


@pytest.fixture
def seeded():
    ids = [
        core.save_meeting_history_record(
            f"第{i}场会议的转写内容。", f"## 纪要{i}\n**决议**：通过。", "", "general",
            "录音输入", f"概览{i}", owner_username=ADMIN,
        )
        for i in range(4)
    ]
    return ids


def _cache_snapshot():
    items, _ = history._load_cached()
    return copy.deepcopy(items)


def test_history_list_does_not_mutate_the_cache(seeded):
    before = _cache_snapshot()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        assert client.get("/api/history").status_code == 200
    assert _cache_snapshot() == before, "列表接口改动了共享缓存"


def test_history_detail_does_not_mutate_the_cache(seeded):
    before = _cache_snapshot()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        assert client.get(f"/api/history/{seeded[-1]}").status_code == 200
    assert _cache_snapshot() == before, "详情接口改动了共享缓存"


def test_list_still_truncates_the_transcript(seeded):
    """只读优化不能顺带改变响应内容:列表仍不返回全文转写。"""
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        items = client.get("/api/history").json()["items"]
    assert items, "没有取到记录,这条用例没测到东西"
    assert all(item["transcript"] == "" for item in items), "列表把全文转写也返回了"


def test_detail_still_returns_the_full_transcript(seeded):
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        detail = client.get(f"/api/history/{seeded[0]}").json()
    assert "第0场会议的转写内容" in detail["transcript"]


def test_readonly_accessor_returns_the_cache_itself():
    """这是它与 load_meeting_history 的唯一区别 —— 如果哪天又拷贝了,收益就没了。"""
    cached, _ = history._load_cached()
    assert history.iter_meeting_history() is cached


def test_update_path_still_copies():
    """读-改-写那条路必须继续拷贝,否则改到一半就污染了别人正在读的缓存。"""
    cached, _ = history._load_cached()
    assert history.load_meeting_history_for_update() is not cached
    assert history.load_meeting_history() is not cached


def test_saving_a_review_does_not_corrupt_other_records(seeded):
    """端到端:保存校核稿只应改动目标那一条。"""
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        target = seeded[1]
        others_before = {
            item["id"]: item.get("summary")
            for item in core.iter_meeting_history() if item.get("id") != target
        }
        response = client.put(
            f"/api/history/{target}/review",
            json={"summary": "校核后的纪要正文", "mode": "general"},
        )
        assert response.status_code == 200, response.text
        others_after = {
            item["id"]: item.get("summary")
            for item in core.iter_meeting_history() if item.get("id") != target
        }
    assert others_after == others_before, "保存校核稿改动了其它记录"
