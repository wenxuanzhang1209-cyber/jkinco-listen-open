"""配置加载的契约（开源本地版）。

本地版没有任何必填的云端凭据：未配置钉钉 / LiveKit / 会话密钥时，对应功能
自动关闭或使用安全默认值，进程必须照常启动。这里钉死「不要求外部凭据」这一
契约，防止未来误把可选配置提升为启动硬条件。
"""
from __future__ import annotations

import jkinco_config


def test_no_cloud_credentials_are_required():
    """本地版不得要求任何云端 API Key / 密钥才能启动。"""
    required = {
        name
        for name in dir(jkinco_config)
        if name.isupper()
        and (
            "API_KEY" in name
            or "SECRET" in name
            or "WEBHOOK" in name
            or "BASE_URL" in name
        )
    }
    assert not required, f"本地版不应存在必填的外部凭据配置：{required}"


def test_load_config_without_any_env(monkeypatch):
    """干净环境下加载配置不应抛错。"""
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL_NAME",
        "DINGTALK_WEBHOOK",
        "DINGTALK_SECRET",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "JKINCO_SESSION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    jkinco_config.load_config(require=True)  # 不应抛 ValueError


def test_require_false_is_still_supported(monkeypatch):
    """require=False 的兼容入口保持可用。"""
    jkinco_config.load_config(require=False)
