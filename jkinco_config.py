"""环境配置的唯一加载入口（开源本地版）。

本版本为 100% 本地运行设计：不依赖任何云端模型服务或 API Key。
可选集成（钉钉机器人推送、LiveKit 实时会议）未配置时自动关闭，
不会阻止应用启动。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent
ENV_PATH = APP_ROOT / ".env"

# 本地版没有任何必填的云端凭据。下列项缺失时使用安全的本地默认值：
# - LLM_BASE_URL      默认 Ollama 的 OpenAI 兼容端点
# - LLM_MODEL_NAME    默认 qwen2.5:7b-instruct（Ollama 一键拉取）
# - JKINCO_SESSION_SECRET 未配置时每次进程启动随机生成（登录态会随重启失效）
# - DINGTALK_*        未配置则钉钉推送不可用，界面会明确提示
# - LIVEKIT_*         未配置则实时会议媒体服务不可用，其余功能不受影响
_loaded = False


def load_config(*, require: bool = True) -> None:
    """加载 .env（不覆盖已有环境变量）。重复调用是安全的。

    本地版不要求任何外部凭据；require 参数保留是为了兼容旧调用点，
    未来如需强制校验某配置项可在此扩展。
    """
    global _loaded
    if not _loaded:
        load_dotenv(ENV_PATH)
        _loaded = True
