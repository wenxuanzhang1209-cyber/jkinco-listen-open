"""历史记录不得因一次读取失败而被抹掉。

历史是单个 JSON 文件,多条路径是「读出来、改一改、再写回」。这类路径若把
「读失败」当成「历史是空的」,紧接着的写回就会把整个文件覆盖 —— 一次瞬时的
磁盘错误或文件损坏,就能让全部会议记录消失,而且此后每次保存都会重复这个覆盖。

这不是假想:load_meeting_history() 的容错设计就是读不出来返回 []。展示路径这样
退化是合理的(少显示几条),写回路径这样退化则是灾难。两者必须用不同的读取入口。
"""
import json
import uuid

import pytest

import jkinco_history


@pytest.fixture()
def history(monkeypatch, tmp_path):
    path = tmp_path / "meetings.json"
    monkeypatch.setattr(jkinco_history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jkinco_history, "HISTORY_FILE", path)
    records = [
        {"id": f"rec-{n}", "title": f"会议 {n}", "transcript": f"正文 {n}",
         "summary": f"纪要 {n}", "owner_username": "alice"}
        for n in range(20)
    ]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path


def _corrupt(path):
    path.write_text("{ 这不是合法的 JSON", encoding="utf-8")


def test_display_path_degrades_quietly(history):
    """展示路径读不出来就少显示几条,不该抛异常打断页面。"""
    _corrupt(history)
    assert jkinco_history.load_meeting_history() == []


def test_update_path_refuses_to_guess(history):
    """写回路径必须拒绝把「读不出来」当成「空」。"""
    _corrupt(history)
    with pytest.raises(jkinco_history.HistoryUnavailable):
        jkinco_history.load_meeting_history_for_update()


def test_saving_a_record_does_not_wipe_history_when_the_file_is_unreadable(history):
    """核心:文件损坏时保存新记录,原有 20 条不能消失。"""
    before = history.read_text(encoding="utf-8")
    _corrupt(history)

    with pytest.raises(jkinco_history.HistoryUnavailable):
        jkinco_history.save_meeting_history_record(
            "新的转写内容", "新的纪要", "已生成", owner_username="alice",
        )

    # 文件必须原封不动 —— 没有被覆盖成只剩新记录
    assert history.read_text(encoding="utf-8") != json.dumps([], ensure_ascii=False)
    assert history.read_text(encoding="utf-8") == "{ 这不是合法的 JSON"
    assert before  # 原始内容确实非空,用例有意义


def test_normal_save_still_keeps_every_existing_record(history):
    """反向保险:正常情况下保存必须保留全部旧记录,别把这次改动做成「谁都不写」。"""
    record_id = jkinco_history.save_meeting_history_record(
        "新的转写内容", "新的纪要", "已生成", owner_username="alice",
    )
    assert record_id

    items = jkinco_history.load_meeting_history()
    assert len(items) == 21
    assert items[0]["id"] == record_id
    assert {item["id"] for item in items[1:]} == {f"rec-{n}" for n in range(20)}


def test_missing_file_is_not_treated_as_failure(history):
    """文件还不存在是首次写入前的正常状态,不该抛异常。"""
    history.unlink()
    assert jkinco_history.load_meeting_history_for_update() == []
    assert jkinco_history.save_meeting_history_record(
        "首条记录", "首条纪要", "已生成", owner_username="alice",
    )
    assert len(jkinco_history.load_meeting_history()) == 1


def test_update_path_returns_a_deep_copy(history):
    """写回路径拿到的同样必须是深拷贝,改了没写回不能污染缓存。"""
    items = jkinco_history.load_meeting_history_for_update()
    items[0]["title"] = "被就地改坏"
    assert jkinco_history.load_meeting_history()[0]["title"] != "被就地改坏"
