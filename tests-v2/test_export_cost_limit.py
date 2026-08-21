"""导出的额度必须比其它「昂贵操作」更紧。

默认的 20 次/分钟是按「等大模型返回」的操作定的:分类、助手、钉钉推送大部分时间
花在网络等待上,占的是线程不是核。导出不一样 —— 它是这批操作里唯一 CPU 密集的:
实测 20 万字的纪要生成 PDF 要 5.1 秒、Word 要 2.1 秒(ReviewPayload.summary 的
上限正是 20 万字)。按 20 次/分钟算,一个账号光靠导出就能吃掉约 100 CPU 秒/分钟,
而生产机器只有 2 核。免注册访客同样能调这个接口。

8 次/分钟远超正常使用 —— 导出是为了拿去看,不会连点 —— 最坏也就占到一个核的
三分之一。
"""
from __future__ import annotations

import tempfile
from collections import Counter
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.main import app


@pytest.fixture(autouse=True)
def _clean_counters():
    main.EXPENSIVE_REQUESTS.clear()
    yield
    main.EXPENSIVE_REQUESTS.clear()


def _fake_export(*args, **kwargs) -> str:
    return tempfile.mkstemp(suffix=".docx")[1]


def test_export_is_capped_below_the_generic_limit():
    assert main.EXPENSIVE_OPERATION_LIMITS["export"] < main.EXPENSIVE_REQUESTS_PER_MINUTE, (
        "导出与网络等待型操作用同一个额度,等于按最便宜的那个来限最贵的那个"
    )


def test_export_stops_at_its_own_limit():
    limit = main.EXPENSIVE_OPERATION_LIMITS["export"]
    with TestClient(app) as client, patch.object(main.core, "export_summary_docx", _fake_export):
        client.post("/api/auth/guest", json={})
        codes = [
            client.post("/api/export/docx", json={"summary": "纪要正文", "mode": "general"}).status_code
            for _ in range(limit + 5)
        ]
    counts = Counter(codes)
    assert counts[200] == limit, f"导出放行了 {counts[200]} 次,期望 {limit}: {counts}"
    assert counts[429] == 5


def test_other_expensive_operations_keep_the_generic_limit():
    """不能矫枉过正:等大模型的那些操作不该被导出的额度牵连。"""
    with TestClient(app) as client, patch.object(
        main.core, "infer_app_mode_best_effort", lambda *a, **k: ("general", "测试")
    ):
        client.post("/api/auth/guest", json={})
        allowed = main.EXPENSIVE_OPERATION_LIMITS["export"] + 3
        codes = [
            client.post("/api/classify", json={"transcript": "内容", "mode": "auto"}).status_code
            for _ in range(allowed)
        ]
    assert all(code == 200 for code in codes), f"分类被导出的额度牵连:{Counter(codes)}"


def test_limits_are_per_operation_not_shared():
    """两种操作各记各的 —— 用完导出额度不该连带封掉分类。"""
    with TestClient(app) as client, patch.object(
        main.core, "export_summary_docx", _fake_export
    ), patch.object(main.core, "infer_app_mode_best_effort", lambda *a, **k: ("general", "测试")):
        client.post("/api/auth/guest", json={})
        for _ in range(main.EXPENSIVE_OPERATION_LIMITS["export"] + 2):
            client.post("/api/export/docx", json={"summary": "正文", "mode": "general"})
        assert client.post("/api/classify", json={"transcript": "内容", "mode": "auto"}).status_code == 200
