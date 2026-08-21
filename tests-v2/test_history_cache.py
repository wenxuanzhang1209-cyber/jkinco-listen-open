"""历史记录解析缓存的正确性契约。

历史是单个 JSON 文件,生产上已近 700 KB,而一次请求里会被读多次。缓存能省下
大部分解析开销,但引入了三类新风险,这里逐条钉住:

1. 调用方拿到的必须是深拷贝 —— 校核稿保存等路径是「读出来、就地改、再写回」,
   直接返回共享对象会让一次未落盘的改动污染所有后续读取;
2. 写入后必须立刻失效 —— 否则保存完看到的还是旧内容;
3. 记录与搜索文本必须同批返回 —— 错位会导致「搜到 A 的词、返回 B 那条」。
"""
import json

import pytest

import jkinco_history


@pytest.fixture()
def history_file(monkeypatch, tmp_path):
    path = tmp_path / "meetings.json"
    monkeypatch.setattr(jkinco_history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jkinco_history, "HISTORY_FILE", path)
    # 故意不重置缓存:缓存键里带了文件路径,换文件就必须自然失效。
    # 若哪天把路径从键里去掉,这里的用例会开始互相串数据而暴露问题。

    def write(items):
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    write([
        {"id": "a", "title": "季度复盘", "summary": "讨论了预算", "owner_username": "alice"},
        {"id": "b", "title": "技术评审", "summary": "确认了架构", "owner_username": "bob"},
    ])
    return write


def test_repeated_reads_return_equal_content(history_file):
    first = jkinco_history.load_meeting_history()
    second = jkinco_history.load_meeting_history()
    assert first == second
    assert [item["id"] for item in first] == ["a", "b"]


def test_caller_mutation_does_not_leak_into_the_cache(history_file):
    """核心风险:改了没写回,不能影响下一次读取。"""
    items = jkinco_history.load_meeting_history()
    items[0]["title"] = "被就地改坏的标题"
    items[0]["summary"] = ""

    fresh = jkinco_history.load_meeting_history()
    assert fresh[0]["title"] == "季度复盘"
    assert fresh[0]["summary"] == "讨论了预算"


def test_write_is_visible_immediately(history_file):
    jkinco_history.load_meeting_history()  # 先把缓存填上
    items = jkinco_history.load_meeting_history()
    items[0]["title"] = "改完并写回"
    jkinco_history.write_meeting_history(items)

    assert jkinco_history.load_meeting_history()[0]["title"] == "改完并写回"


def test_external_rewrite_is_picked_up(history_file):
    """别的进程改写了文件也要能发现,不能只认自己的写入。"""
    jkinco_history.load_meeting_history()
    history_file([{"id": "c", "title": "外部写入的记录", "owner_username": "carol"}])

    items = jkinco_history.load_meeting_history()
    assert [item["id"] for item in items] == ["c"]


def test_search_blobs_align_with_records(history_file):
    items, blobs = jkinco_history.load_history_with_search()
    assert len(items) == len(blobs)
    for item, blob in zip(items, blobs):
        assert item["title"].lower() in blob
        assert item["id"] in blob


def test_search_blob_covers_body_not_just_title(history_file):
    """搜索要能命中正文,而不只是标题 —— 这是原实现 json.dumps 整条记录的用意。"""
    items, blobs = jkinco_history.load_history_with_search()
    matched = [item for item, blob in zip(items, blobs) if "预算" in blob]
    assert [item["id"] for item in matched] == ["a"]


def test_search_blobs_refresh_after_write(history_file):
    items, _ = jkinco_history.load_history_with_search()
    items[1]["summary"] = "新增了一个只在正文里出现的词：碳中和"
    jkinco_history.write_meeting_history(items)

    refreshed, blobs = jkinco_history.load_history_with_search()
    matched = [item for item, blob in zip(refreshed, blobs) if "碳中和" in blob]
    assert [item["id"] for item in matched] == ["b"]


def test_missing_and_broken_files_degrade_to_empty(history_file, tmp_path):
    (tmp_path / "meetings.json").unlink()
    assert jkinco_history.load_meeting_history() == []
    assert jkinco_history.load_history_with_search() == ([], [])

    (tmp_path / "meetings.json").write_text("{ 不是合法 JSON", encoding="utf-8")
    assert jkinco_history.load_meeting_history() == []


def test_placeholder_rows_stay_filtered(history_file):
    """占位/错误行不进列表 —— 缓存不能把这条既有规则绕过去。"""
    history_file([
        {"id": "x", "title": "❌ 处理失败"},
        {"id": "y", "title": "请先选择录音文件"},
        {"id": "z", "title": "正常会议"},
    ])
    items, blobs = jkinco_history.load_history_with_search()
    assert [item["id"] for item in items] == ["z"]
    assert len(blobs) == 1


def test_search_text_is_only_built_when_asked_for(history_file, monkeypatch):
    """按需构建:普通读取不该付构建全文的代价。

    它的开销与解析整个历史文件相当(4.32ms vs 4.93ms),而十四个调用点里只有
    关键词搜索用得上。每次写入都会让缓存失效,之后第一个读到的调用方若无条件
    构建,就是白付这笔钱,还平白多占一份常驻内存。
    """
    built = []
    original = jkinco_history.json.dumps

    def counting_dumps(obj, **kwargs):
        built.append(1)
        return original(obj, **kwargs)

    monkeypatch.setattr(jkinco_history.json, "dumps", counting_dumps)

    jkinco_history.load_meeting_history()
    assert built == [], "普通读取不该构建搜索全文"

    jkinco_history.load_history_with_search()
    assert built, "搜索路径必须构建全文"


def test_search_text_is_reused_across_calls(history_file, monkeypatch):
    """构建一次即可复用,不能每次搜索都重来。"""
    jkinco_history.load_history_with_search()
    built = []
    original = jkinco_history.json.dumps
    monkeypatch.setattr(jkinco_history.json, "dumps",
                        lambda obj, **kw: (built.append(1), original(obj, **kw))[1])

    jkinco_history.load_history_with_search()
    assert built == [], "缓存命中时不该重建搜索全文"


def test_search_text_matches_records_after_lazy_build(history_file):
    """惰性构建仍必须与记录一一对应 —— 错位会让搜到 A 的词却返回 B 那条。"""
    jkinco_history.load_meeting_history()          # 先只填记录,不建全文
    history_file([{"id": f"r{n}", "title": f"会议{n}", "summary": f"正文关键词{n}"} for n in range(6)])
    jkinco_history.load_meeting_history()          # 换文件后再只读记录
    items, blobs = jkinco_history.load_history_with_search()   # 此时才惰性构建

    assert len(items) == len(blobs) == 6
    for index, (item, blob) in enumerate(zip(items, blobs)):
        assert f"正文关键词{index}" in blob
        assert item["id"] == f"r{index}"


def test_caller_cannot_corrupt_the_cached_search_list(history_file):
    """返回的全文列表被就地清空/排序时,不能影响缓存。"""
    _, blobs = jkinco_history.load_history_with_search()
    blobs.clear()
    _, again = jkinco_history.load_history_with_search()
    assert len(again) == 2
