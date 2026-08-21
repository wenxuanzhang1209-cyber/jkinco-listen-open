"""历史记录不得因为条数上限而被静默删除。

守的是一个真实存在过的数据丢失路径:write_meeting_history 原先执行
items[:80],而新记录插在列表头部 —— 于是第 81 场会议开始,每保存一场就
永久删掉一场最老的,没有任何提示也无法找回。发现时生产已有 67 条,
距离开始丢数据只剩 13 场;其中 15 条只存在于这个文件里。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jkinco_history


def _record(index: int) -> dict:
    return {"id": f"rec-{index:04d}", "title": f"第 {index} 场", "created_at": index,
            "transcript": "内容", "summary": "纪要", "owner_username": "tester"}


def test_overflow_is_archived_not_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(jkinco_history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jkinco_history, "HISTORY_FILE", tmp_path / "meetings.json")
    monkeypatch.setattr(jkinco_history, "HISTORY_ARCHIVE_FILE", tmp_path / "meetings-archive.jsonl")
    monkeypatch.setattr(jkinco_history, "HISTORY_MAX_ITEMS", 10)

    # 新记录在前,和真实写入顺序一致
    items = [_record(i) for i in range(25, 0, -1)]
    jkinco_history.write_meeting_history(items)

    hot = json.loads((tmp_path / "meetings.json").read_text(encoding="utf-8"))
    assert len(hot) == 10, "热表应被限制在上限内"

    archive_path = tmp_path / "meetings-archive.jsonl"
    assert archive_path.exists(), "溢出的记录必须落到归档,不能直接丢弃"
    archived = [json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines() if line]

    # 一条都不能少
    all_ids = {item["id"] for item in hot} | {item["id"] for item in archived}
    assert all_ids == {r["id"] for r in items}, (
        f"有记录凭空消失:缺失 {({r['id'] for r in items} - all_ids)}"
    )


def test_archive_failure_keeps_records_in_hot_table(tmp_path, monkeypatch):
    """归档写不进去时,宁可热表超长也不能删记录。"""
    monkeypatch.setattr(jkinco_history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jkinco_history, "HISTORY_FILE", tmp_path / "meetings.json")
    monkeypatch.setattr(jkinco_history, "HISTORY_MAX_ITEMS", 5)
    monkeypatch.setattr(jkinco_history, "_archive_overflow", lambda overflow: False)

    items = [_record(i) for i in range(12, 0, -1)]
    jkinco_history.write_meeting_history(items)

    hot = json.loads((tmp_path / "meetings.json").read_text(encoding="utf-8"))
    assert len(hot) == 12, "归档失败时不能丢弃任何记录"


def test_no_truncation_when_within_limit(tmp_path, monkeypatch):
    """未超上限时不应产生归档文件。"""
    monkeypatch.setattr(jkinco_history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jkinco_history, "HISTORY_FILE", tmp_path / "meetings.json")
    monkeypatch.setattr(jkinco_history, "HISTORY_ARCHIVE_FILE", tmp_path / "meetings-archive.jsonl")
    monkeypatch.setattr(jkinco_history, "HISTORY_MAX_ITEMS", 50)

    items = [_record(i) for i in range(5, 0, -1)]
    jkinco_history.write_meeting_history(items)

    assert not (tmp_path / "meetings-archive.jsonl").exists()
    hot = json.loads((tmp_path / "meetings.json").read_text(encoding="utf-8"))
    assert len(hot) == 5
