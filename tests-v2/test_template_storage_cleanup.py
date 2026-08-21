"""模板存储必须能被回收,否则是一条无人看管的增长路径。

两处实测存在的泄漏:
  - 访客账号过期时只清账号、资料和历史记录,模板留了下来 —— 生产已出现
    账号早已不存在的孤儿模板。访客免注册、单个模板上限 10MB。
  - delete_template 只置 deleted_at,内容 BLOB 一直占着空间。
"""
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.custom_templates as CT
from backend.custom_templates import (
    DELETED_TEMPLATE_RETENTION_SECONDS,
    init_custom_template_db,
    purge_expired_deleted_templates,
    purge_templates_for_owners,
)


def _seed(owner: str, *, deleted_at: float | None = None, size: int = 4096) -> str:
    init_custom_template_db()
    tid = uuid.uuid4().hex
    now = time.time()
    with CT.PROFILE_DB_LOCK, sqlite3.connect(CT.PROFILE_DB) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(custom_templates)")}
        values = {
            "id": tid, "owner_username": owner, "name": "模板", "scenario": "general",
            "original_filename": "t.docx", "filename": "t.docx", "content": b"x" * size,
            "created_at": now, "updated_at": now, "is_default": 0, "parse_status": "ready",
            "insertion_strategy": "append", "insertion_target": "append:new-page",
            "analysis_json": "{}", "sha256": "x", "content_size": size, "version": 1,
            "deleted_at": deleted_at,
        }
        used = {k: v for k, v in values.items() if k in columns}
        connection.execute(
            f"INSERT INTO custom_templates({','.join(used)}) VALUES({','.join('?' * len(used))})",
            tuple(used.values()),
        )
    return tid


def _row(tid):
    with CT.PROFILE_DB_LOCK, sqlite3.connect(CT.PROFILE_DB) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM custom_templates WHERE id=?", (tid,)).fetchone()
    return dict(row) if row else None


def test_guest_templates_are_purged_with_the_account():
    owner = f"guest_{uuid.uuid4().hex[:8]}"
    tid = _seed(owner)
    assert _row(tid) is not None
    assert purge_templates_for_owners({owner}) == 1
    assert _row(tid) is None, "访客账号过期后模板仍留在库里"


def test_purge_does_not_touch_other_owners():
    keep = _seed("admin")
    gone = _seed("guest_temp")
    purge_templates_for_owners({"guest_temp"})
    assert _row(keep) is not None, "误删了其他用户的模板"
    assert _row(gone) is None


def test_expired_soft_deleted_content_is_released():
    old = time.time() - DELETED_TEMPLATE_RETENTION_SECONDS - 60
    tid = _seed("admin", deleted_at=old, size=8192)
    assert _row(tid)["content_size"] == 8192
    assert purge_expired_deleted_templates() >= 1
    row = _row(tid)
    assert row is not None, "行本身应保留 —— 历史记录还要用模板名等元信息"
    assert row["content_size"] == 0
    assert not row["content"]


def test_recently_deleted_content_is_kept():
    """保留期内必须留着:导出接口用 include_deleted=True 读内容,
    历史记录引用的模板即使已删除也要能重新导出。"""
    tid = _seed("admin", deleted_at=time.time() - 60, size=8192)
    purge_expired_deleted_templates()
    assert _row(tid)["content_size"] == 8192


def test_live_templates_are_never_touched():
    tid = _seed("admin", deleted_at=None, size=8192)
    purge_expired_deleted_templates()
    assert _row(tid)["content_size"] == 8192
