"""示例数据模式（JKINCO_DEMO_DATA=1）的契约测试。"""

from __future__ import annotations

import pytest

import backend.main as main


@pytest.fixture(autouse=True)
def _isolate_history():
    """每个用例从空历史开始，结束后恢复套件原状。"""
    existing = main.core.load_meeting_history()
    main.core.write_meeting_history([])
    yield
    main.core.write_meeting_history(existing)


def test_demo_seed_is_idempotent_when_enabled(monkeypatch):
    monkeypatch.setenv("JKINCO_DEMO_DATA", "1")
    main._demo_seeded = False
    main.maybe_seed_demo_history()

    items = main.core.load_meeting_history()
    assert items, "示例数据未写入历史"
    assert all(item.get("source") == "示例数据" for item in items)
    assert len(items) == 2
    modes = {item.get("mode") for item in items}
    assert {"talk", "customer_visit"} <= modes

    # 第二次调用不得重复写入
    main.maybe_seed_demo_history()
    assert len(main.core.load_meeting_history()) == 2


def test_demo_seed_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("JKINCO_DEMO_DATA", raising=False)
    main._demo_seeded = False
    main.maybe_seed_demo_history()
    assert not main.core.load_meeting_history(), "关闭示例模式时不应写入示例数据"


def test_demo_seed_respects_existing_history(monkeypatch):
    monkeypatch.setenv("JKINCO_DEMO_DATA", "1")
    main._demo_seeded = False
    main.core.save_meeting_history_record(
        transcript="真实转写内容示例",
        summary="真实纪要示例",
        dingtalk_status="未推送",
        app_mode="general",
        source="测试",
        owner_username="admin",
    )
    main.maybe_seed_demo_history()
    items = main.core.load_meeting_history()
    assert len(items) == 1, "已有历史时不应写入示例数据"
