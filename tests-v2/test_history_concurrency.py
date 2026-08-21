"""历史记录并发写入的回归测试。

背景:历史是单个 JSON 文件,写入时按 id 去重(同 id 视为更新同一条)。
新记录的 id 曾用纯毫秒时间戳生成,并发下会碰撞,后写的静默覆盖先写的整条记录。
上传转写(backend/main.py)与会议归档(backend/meetings.py)都跑在线程池里,
同毫秒完成完全可能,因此这里用真实线程压测锁定「不丢记录」这条契约。
"""
import json
import os
import tempfile
import threading

# 强制覆盖:若开发者 shell 载入了真实 .env,会真实调用大模型生成标题(产生费用)。
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ.setdefault("DINGTALK_WEBHOOK", "http://127.0.0.1:1/webhook")
os.environ.setdefault("DINGTALK_SECRET", "test-secret")
os.environ["JKINCO_HISTORY_DIR"] = tempfile.mkdtemp(prefix="jkinco-hist-test-")

import jkinco_history as history


def _fast_title(*args, **kwargs):
    """跳过标题生成:它会走网络重试,拖慢并发压测且与本测试无关。"""
    return "测试标题"


def test_concurrent_saves_do_not_lose_records(monkeypatch):
    monkeypatch.setattr(history, "generate_meeting_title", _fast_title)
    history.write_meeting_history([])

    total = 40
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            history.save_meeting_history_record(
                f"转写内容{index}", f"纪要内容{index}", "ok",
                "general", "测试", owner_username="tester",
            )
        except Exception as error:  # noqa: BLE001 - 测试需捕获任意异常上报
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"并发写入抛异常:{errors}"

    items = history.load_meeting_history()
    assert len(items) == total, f"并发写 {total} 条,只剩 {len(items)} 条(id 碰撞导致覆盖)"

    ids = [item["id"] for item in items]
    assert len(set(ids)) == total, "生成的记录 id 存在重复"

    # 每条转写都必须原样存在,不能有内容被别的线程串写
    saved = {item["transcript"] for item in items}
    assert saved == {f"转写内容{i}" for i in range(total)}


def test_concurrent_saves_keep_history_file_valid_json(monkeypatch):
    """写入必须是原子替换,读者不能看到半截文件。"""
    monkeypatch.setattr(history, "generate_meeting_title", _fast_title)
    history.write_meeting_history([])

    corrupt: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                raw = history.HISTORY_FILE.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            if not raw:
                continue
            try:
                json.loads(raw)
            except json.JSONDecodeError as error:
                corrupt.append(str(error))

    watcher = threading.Thread(target=reader, daemon=True)
    watcher.start()

    threads = [
        threading.Thread(
            target=history.save_meeting_history_record,
            args=(f"并发{i}", f"纪要{i}", "ok", "general", "测试"),
        )
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    watcher.join(timeout=2)

    assert not corrupt, f"并发写入期间读到损坏的 JSON:{corrupt[:3]}"


def test_explicit_record_id_still_updates_in_place(monkeypatch):
    """传入 record_id 时仍是「更新同一条」,不能因为改了 id 生成方式而变成新增。"""
    monkeypatch.setattr(history, "generate_meeting_title", _fast_title)
    history.write_meeting_history([])

    record_id = history.save_meeting_history_record(
        "初始转写", "", "处理中", "general", "测试",
    )
    assert record_id

    history.save_meeting_history_record(
        "初始转写", "最终纪要", "完成", "general", "测试", record_id=record_id,
    )

    items = history.load_meeting_history()
    assert len(items) == 1, "同一 record_id 应更新原记录而非新增"
    assert items[0]["id"] == record_id
    assert items[0]["summary"] == "最终纪要"


def test_owner_is_preserved_when_updating_without_owner(monkeypatch):
    """分阶段更新不传 owner_username 时,必须沿用原归属,否则记录会变成"无主"。"""
    monkeypatch.setattr(history, "generate_meeting_title", _fast_title)
    history.write_meeting_history([])

    record_id = history.save_meeting_history_record(
        "转写", "", "处理中", "general", "测试", owner_username="alice",
    )
    history.save_meeting_history_record(
        "转写", "纪要", "完成", "general", "测试", record_id=record_id,
    )

    items = history.load_meeting_history()
    assert items[0]["owner_username"] == "alice"
