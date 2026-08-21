"""大模型错误文本必须脱敏后才回显给用户。

大模型不可达时,requests 的异常会把完整请求 URL(凭证常在 query 里)和内部
主机名原样带出。这段文本被上游拼进「场景识别理由」「概览生成失败」等文案,
落库并展示给用户。钉钉链路早就为此做了脱敏,大模型链路却没有 —— 同一类问题
只堵了一半。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jkinco_text import redact_secrets

SAMPLE = (
    "test-model: HTTPSConnectionPool(host='internal-llm.corp.invalid', port=443): "
    "Max retries exceeded with url: /v1/chat/completions?token=SECRET123&api_key=ABCDEF "
    "(Caused by SSLError(...))"
)


def test_query_credentials_are_removed():
    out = redact_secrets(SAMPLE)
    assert "SECRET123" not in out
    assert "ABCDEF" not in out


def test_internal_hostname_is_removed():
    out = redact_secrets(SAMPLE)
    assert "internal-llm.corp.invalid" not in out


def test_configured_secret_values_are_removed(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-a-very-real-looking-key")
    monkeypatch.setenv("DINGTALK_SECRET", "SECdingtalk12345")
    text = "调用失败 sk-a-very-real-looking-key 以及 SECdingtalk12345"
    out = redact_secrets(text)
    assert "sk-a-very-real-looking-key" not in out
    assert "SECdingtalk12345" not in out


def test_llm_failure_surface_is_clean(monkeypatch):
    """端到端:大模型不可达时,用户可见文案里不能出现凭证或内部地址。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-REAL-KEY-1234567890")
    monkeypatch.setenv("LLM_BASE_URL", "https://internal-llm.corp.invalid/v1/chat?token=SECRET123")
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("JKINCO_LLM_ATTEMPT_TIMEOUT", "1")

    from jkinco_classifier import infer_app_mode_best_effort
    from jkinco_reports import generate_meeting_overview

    _, reason = infer_app_mode_best_effort("今天开会讨论项目进度。", "auto")
    overview = generate_meeting_overview("纪要正文", "转写", "general")
    for surface in (reason, overview):
        assert "SECRET123" not in surface
        assert "sk-REAL-KEY-1234567890" not in surface
        assert "internal-llm.corp.invalid" not in surface


def test_normal_text_is_not_mangled():
    """脱敏不能把正常内容也改掉。"""
    text = "自动识别为通用会议纪要（工程特征 0，其他会议特征 0）。"
    assert redact_secrets(text) == text
