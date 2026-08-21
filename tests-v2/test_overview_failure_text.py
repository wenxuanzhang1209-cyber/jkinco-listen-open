"""概览生成失败时留下的那段文字,本身也是产品的一部分。

generate_meeting_overview 失败时不抛错,而是返回一段带「概览生成失败：…」的
Markdown —— 这段文字会落库,并作为会议概览展示给全体成员。它有两个问题:

1. 整个分支不记日志。上方的 quality_refine_minutes 失败会记一条 warning,这里
   原先什么都不留:概览若对所有用户都开始失败,运维侧看不到任何迹象,只能等
   有人打开某场会才发现。

2. 异常文本直接拼进 Markdown。异常里带一个换行加「## 」,概览里就会多出一个
   假章节 —— 前端按章节渲染,用户看到的会议记录里会凭空长出一段。

第 2 点由服务端自己产生的异常触发,参会者控制不了,属纵深防御;第 1 点才是
实打实的运维盲区。
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import jkinco_reports as reports

EXPECTED_SECTIONS = ("## 一、会议概述", "## 二、会议流程", "## 三、会议结论", "## 四、待办事项")


def _overview_with_error(error: Exception) -> str:
    with patch.object(reports, "call_llm", side_effect=error):
        return reports.generate_meeting_overview("会议摘要正文", "转写", "auto")


def test_failure_text_cannot_inject_a_fake_section():
    """异常里的换行 + 「## 」原先会在概览里插出第五个章节。"""
    out = _overview_with_error(RuntimeError("连接失败\n## 九、伪造章节\n- 这是注入的内容"))
    headings = [line for line in out.splitlines() if line.startswith("## ")]
    assert headings == list(EXPECTED_SECTIONS), f"章节被改动了:{headings}"
    assert "伪造章节" in out, "内容不该被丢掉,只该降级成正文"


def test_failure_text_is_bounded():
    """异常可以很长;概览是要展示的,不能被一段报错撑爆。"""
    out = _overview_with_error(RuntimeError("x" * 5000))
    detail = out.split("概览生成失败：", 1)[1].split("\n", 1)[0]
    assert len(detail) <= 200, f"未截断,长 {len(detail)}"


def test_failure_is_logged():
    """这是主要收益:失败必须在日志里留痕。"""
    with patch.object(reports.LOGGER, "warning") as logged:
        _overview_with_error(RuntimeError("模型不可用"))
    assert logged.called, "概览生成失败没有记录任何日志"


def test_failure_text_is_redacted():
    """异常文本会落库并展示,凭证不能随它一起进去。"""
    out = _overview_with_error(RuntimeError("请求 https://user:s3cret@host/v1 失败"))
    assert "s3cret" not in out


def test_all_four_sections_survive_a_failure():
    """兜底结构不能因为收敛处理而缺项 —— 前端按这四段渲染。"""
    out = _overview_with_error(RuntimeError("任意错误"))
    for section in EXPECTED_SECTIONS:
        assert section in out, f"缺少 {section}"


def test_successful_overview_is_untouched():
    """收敛只作用于失败路径,正常概览不得被改写。"""
    with patch.object(reports, "call_llm", return_value="## 一、会议概述\n- 正常内容"):
        out = reports.generate_meeting_overview("会议摘要正文", "转写", "auto")
    assert out == "## 一、会议概述\n- 正常内容"


def test_placeholder_summary_short_circuits_before_calling_the_model():
    """还没出纪要时不该白调一次模型。"""
    with patch.object(reports, "call_llm", side_effect=AssertionError("不该被调用")) as spy:
        assert reports.generate_meeting_overview("*等待处理...*") == "*等待生成会议概览...*"
        assert reports.generate_meeting_overview("") == "*等待生成会议概览...*"
    assert not spy.called
