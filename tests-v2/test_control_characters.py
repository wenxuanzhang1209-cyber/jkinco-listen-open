"""控制字符必须在清洗层就去掉。

XML 1.0 只接受制表、换行、回车三个控制字符。其余的(空字节、响铃、终端转义
序列)会让 python-docx 在写文档时直接抛
「All strings must be XML compatible: no NULL bytes」—— 一场会的纪要里只要混进
一个,Word 导出就永久失败,而 PDF 那条路照常能导,用户只会觉得 Word 坏了。

来源不止一处:语音识别的结果、用户粘贴的文本,以及 /api/process 直接提交的
live_text —— 实测含空字节的提交返回 200,一路畅通到落库。

所以在清洗层去掉,而不是只在导出处兜底:纪要还要进钉钉、进模板渲染,
每处各修一遍迟早会漏。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import report_templates
from jkinco_export import export_summary_docx, export_summary_pdf
from jkinco_text import clean_markdown_text, strip_control_characters

CONTROL_SAMPLE = "正常\x00文本\x07带控制符\x1b[31m"


@pytest.mark.parametrize("bad", ["\x00", "\x07", "\x1b", "\x0b", "\x0c", "\ud800"])
def test_control_characters_are_removed(bad):
    assert bad not in strip_control_characters(f"前{bad}后")


@pytest.mark.parametrize("keep", ["\t", "\n", "\r"])
def test_whitespace_control_characters_survive(keep):
    """制表/换行/回车是 XML 允许的,去掉它们会把纪要排版毁掉。"""
    assert strip_control_characters(f"前{keep}后") == f"前{keep}后"


@pytest.mark.parametrize("text", ["会议纪要", "🎉", "𠮷𩸽", "①②③", "café"])
def test_normal_content_is_untouched(text):
    """emoji 与生僻汉字在基本平面之外 —— 正则上界写成 \\uFFFD 会把它们一并删掉。"""
    assert strip_control_characters(text) == text


def test_clean_markdown_text_strips_them_too():
    cleaned = clean_markdown_text(CONTROL_SAMPLE)
    assert not any(ch in cleaned for ch in ("\x00", "\x07", "\x1b"))
    assert "正常" in cleaned and "文本" in cleaned


@pytest.mark.parametrize("exporter", [export_summary_docx, export_summary_pdf])
def test_export_survives_control_characters(exporter):
    """这条是本文件的由来:修复前 DOCX 会抛 ValueError,PDF 却正常。"""
    path = exporter(CONTROL_SAMPLE, "general")
    assert path and Path(path).exists() and Path(path).stat().st_size > 0
    Path(path).unlink(missing_ok=True)


MALFORMED = [
    "", "   \n\t ", "## 只有标题",
    "工程" * 3000,                                    # 超长无空格单行
    "| a | b | c |\n|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |",  # 表格列数不齐
    "|这不是表格因为没有分隔行|\n普通一行",
    "---\n---\n---",
    "<script>alert(1)</script><b>粗</b>",
    "**##--||__",
    "第一行\r\n第二行\r第三行\n",
]


@pytest.mark.parametrize("text", MALFORMED)
def test_docx_export_survives_malformed_content(text):
    """纪要正文由大模型生成、也可能被人工校核改过,格式不可能总是规范。"""
    path = export_summary_docx(text, "general")
    assert path and Path(path).exists() and Path(path).stat().st_size > 0
    Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize("text", MALFORMED)
def test_fallback_pdf_survives_malformed_content(monkeypatch, text):
    """强制走 reportlab 兜底 —— 那条路自己解析 Markdown,是畸形内容真正的风险所在。

    完整路径是「先建 DOCX,再交给 LibreOffice 渲染」:畸形内容的处理全在 DOCX
    那一步,PDF 只是换个渲染器。所以拿十种畸形内容各跑一遍完整路径,测的是同一件
    事十次,每次要 6.3 秒。

    反过来,兜底路径在装了 LibreOffice 的机器上**一次都跑不到** —— 而它才是自己
    解析 Markdown 的那条。改成在这里强制走它:覆盖比原先更全,单次 110ms。
    """
    monkeypatch.setattr(report_templates, "convert_docx_to_pdf", lambda *args, **kwargs: False)
    path = export_summary_pdf(text, "general")
    assert path and Path(path).exists() and Path(path).stat().st_size > 0
    Path(path).unlink(missing_ok=True)


def test_real_pdf_pipeline_still_works():
    """完整路径(LibreOffice)保留一条:证明这条路本身是通的。

    只留一条是因为 test_export_survives_control_characters[export_summary_pdf]
    已经跑过一次完整路径 —— 那条走的是干净内容,这条补一个畸形表格。
    每次完整路径要 6 秒,多跑一次不会多守住什么。
    """
    text = "| a | b |\n|---|\n| 1 |"
    path = export_summary_pdf(text, "general")
    assert path and Path(path).exists() and Path(path).stat().st_size > 0
    Path(path).unlink(missing_ok=True)


def test_dingtalk_push_strips_them():
    """钉钉那条路不走 clean_markdown_text,单独确认 —— 实测修复前推送体里带着 \\u0000。"""
    import jkinco_dingtalk as dingtalk

    captured = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"errcode": 0}

    def fake_post(url, data=None, **kwargs):
        captured["body"] = data
        return _Response()

    with patch.object(dingtalk.requests, "post", fake_post):
        dingtalk.send_to_dingtalk(f"{CONTROL_SAMPLE} 与 emoji🎉", "general")

    body = captured["body"].decode("utf-8")
    assert "u0000" not in body and "u0007" not in body, "控制字符被原样推进群里"
    assert "🎉" in body or "ud83c" in body.lower(), "emoji 被误删"
