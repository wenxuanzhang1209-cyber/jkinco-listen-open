"""配额已满的上传不该先付解析代价。

analyze_docx 的开销随段落数线性上涨:实测约 0.9ms/段,8000 段要 6 秒;而
MAX_XML_BYTES(3MB)允许约五万段 —— 最坏一次上传要跑半分钟。

原先它排在配额检查之前,于是配额已满的账号每次被拒的上传仍要付全额解析代价:
同样是一句「模板数量已达上限」,小文件 30ms、大文件 5936ms。这条路又没有限流,
等于一个免费的 CPU 消耗入口 —— 而生产那台机器只有 2 核。

修法是在解析前加一道廉价预检(只查计数,不加锁)。权威判定仍在写入锁内:并发
上传必须由同一把锁串行化,否则两个请求会各自读到超限前的计数、双双放行。
预检可能读到略旧的数字,但那只会往「放行」的方向偏 —— 少拒不要紧,
重点是让注定失败的请求早点失败。
"""
from __future__ import annotations

import inspect
import io
import time

import pytest
from docx import Document

import backend.custom_templates as templates


def _docx(paragraphs: int) -> bytes:
    document = Document()
    for index in range(paragraphs):
        document.add_paragraph(f"第{index}段正文内容用于填充")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def sized_templates() -> tuple[bytes, bytes]:
    return _docx(10), _docx(4000)


def _fill_quota(owner: str, small: bytes) -> None:
    for index in range(templates.MAX_TEMPLATES_PER_USER):
        templates.create_template(owner, f"模板{index}", "t.docx", small, scenario="general")


def test_rejection_does_not_pay_the_parse_cost(sized_templates):
    """核心回归:配额已满时,大文件被拒的耗时应与小文件同量级。"""
    small, big = sized_templates
    owner = "cost_probe_user"
    _fill_quota(owner, small)

    def rejected(content: bytes) -> float:
        start = time.perf_counter()
        with pytest.raises(ValueError, match="模板数量已达上限"):
            templates.create_template(owner, "超额", "t.docx", content, scenario="general")
        return (time.perf_counter() - start) * 1000

    小 = min(rejected(small) for _ in range(3))
    大 = min(rejected(big) for _ in range(3))
    assert 大 < 小 + 200, f"大文件被拒仍付了解析代价:小 {小:.0f}ms vs 大 {大:.0f}ms"


def test_precheck_runs_before_parsing():
    """结构上钉住顺序 —— 这是很容易在重构时被挪回去的一行。"""
    source = inspect.getsource(templates.create_template)
    assert source.index("_precheck_quota(") < source.index("analyze_docx("), "预检又排到解析后面了"


def test_authoritative_check_stays_inside_the_lock():
    """预检不能取代锁内判定:并发上传靠那把锁串行化,少了它会双双放行。"""
    source = inspect.getsource(templates.create_template)
    lock_at = source.index("with PROFILE_DB_LOCK")
    assert source.index("active >= count_limit", lock_at) > lock_at, "锁内的权威判定没了"


def test_quota_is_still_enforced(sized_templates):
    """省代价不能省掉判定本身。"""
    small, _ = sized_templates
    owner = "cost_probe_user2"
    _fill_quota(owner, small)
    with pytest.raises(ValueError, match="模板数量已达上限"):
        templates.create_template(owner, "再来一个", "t.docx", small, scenario="general")


def test_storage_quota_is_prechecked_too(monkeypatch, sized_templates):
    """总量超限同样该早拒 —— 它和数量上限是同一类判定。"""
    small, _ = sized_templates
    owner = "cost_probe_user3"
    monkeypatch.setattr(templates, "MAX_TEMPLATE_STORAGE_PER_USER", len(small) * 2)
    templates.create_template(owner, "第一个", "t.docx", small, scenario="general")
    templates.create_template(owner, "第二个", "t.docx", small, scenario="general")
    with pytest.raises(ValueError, match="占用空间已达上限"):
        templates.create_template(owner, "第三个", "t.docx", small, scenario="general")


def test_upload_is_rate_limited():
    """这条路原先没有任何限流,而单次最坏要跑半分钟。"""
    import backend.main as main

    assert "template" in main.EXPENSIVE_OPERATION_LIMITS
    source = inspect.getsource(main.custom_template_upload)
    assert 'enforce_expensive_rate_limit(username, "template")' in source
