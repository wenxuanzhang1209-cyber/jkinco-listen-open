"""长转写分块生成纪要:单段失败不得拖垮整份纪要。

超过 12000 字的转写会被切成 6000 字一段并行提取,再把提取结果合成纪要。原先
用的是 pool.map —— 任何一段抛异常都会在收集结果时重新抛出。实测一场三小时的会
切成 7 段,第 3 段抖动一次,前面 6 次调用的钱照付、结果全部作废,用户只看到
「纪要生成失败」;而 call_llm 只做主备模型切换、不重试,重试整个任务也要从头
再跑一遍全部片段。

少一段的细节远好过整份纪要没有,所以改为逐段容错。但「全部失败」必须照旧报错:
那说明不是抖动而是模型或网络真的不可用,此时硬编一份「全是待确认」的纪要只会
掩盖故障,让用户以为会议内容就这么点。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import jkinco_reports as reports
from jkinco_prompts import LLM_DIRECT_MAX_CHARS, split_text

# 约三小时的中文会议转写(口语约每分钟 200 字)
LONG_TRANSCRIPT = "关于下一阶段的排期安排，我们需要确认交付节点。" * 1600


@pytest.fixture(autouse=True)
def _skip_quality_review(monkeypatch):
    """质检复核会再打一次模型,与本文件要测的分块逻辑无关。"""
    monkeypatch.setenv("JKINCO_QUALITY_REVIEW", "0")


def test_the_fixture_transcript_really_triggers_chunking():
    """自检:转写若没超过直连阈值,下面几条测的就不是分块路径。"""
    assert len(LONG_TRANSCRIPT) > LLM_DIRECT_MAX_CHARS
    assert len(split_text(LONG_TRANSCRIPT)) >= 5


def test_one_failed_chunk_does_not_lose_the_whole_minutes():
    calls = {"count": 0}

    def flaky(prompt, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("模型返回空内容")
        return f"片段要点 {calls['count']}"

    with patch.object(reports, "call_llm", flaky):
        minutes = reports.generate_minutes(LONG_TRANSCRIPT, "talk")
    assert minutes, "单段失败不该让整份纪要作废"


def test_failed_chunk_is_marked_rather_than_silently_dropped():
    """缺的那段要在正文里留痕,否则读者会以为那段时间没人说话。"""
    captured: list[str] = []

    def collect(prompt, **kwargs):
        captured.append(prompt)
        if len(captured) == 2:
            raise RuntimeError("模型返回空内容")
        return f"片段要点 {len(captured)}"

    with patch.object(reports, "call_llm", collect):
        reports.generate_minutes(LONG_TRANSCRIPT, "talk")
    # 最后一次调用是合成纪要,其提示词里应当带着缺失说明
    assert "本段提取失败" in captured[-1]


def test_all_chunks_failing_still_raises():
    def always_fail(prompt, **kwargs):
        raise RuntimeError("模型不可用")

    with patch.object(reports, "call_llm", always_fail):
        with pytest.raises(Exception) as caught:
            reports.generate_minutes(LONG_TRANSCRIPT, "talk")
    assert "片段提取均失败" in str(caught.value)


def test_short_transcript_still_goes_straight_through():
    """短转写不走分块,行为必须原样不变。"""
    calls: list[str] = []

    def record(prompt, **kwargs):
        calls.append(prompt)
        return "纪要正文"

    with patch.object(reports, "call_llm", record):
        assert reports.generate_minutes("今天开了个短会，确认了排期。", "talk") == "纪要正文"
    assert len(calls) == 1, "短转写应当只打一次模型"
