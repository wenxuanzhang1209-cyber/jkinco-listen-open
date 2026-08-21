"""钉钉机器人推送。

从 JKincoListen.py 单体抽出的第二个独立模块。封装钉钉加签与 Markdown 消息推送,
只依赖标准库、requests 和场景模块的 output_title。JKincoListen.py 通过 re-import
保持向后兼容,所有历史调用点(core.send_to_dingtalk 等)行为完全不变。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse

import requests

from jkinco_scenes import output_title
from jkinco_text import strip_control_characters

from jkinco_logging import get_logger

LOGGER = get_logger("dingtalk")


def _redact_secrets(text: str) -> str:
    """抹掉异常文本里的凭证。

    第三方库的异常常把请求 URL 原样带出,而 webhook 的 access_token 就在 URL 里。
    这些字符串会回显给前端,必须先脱敏。同时覆盖 query 中的常见凭证参数名,
    以及环境变量里的真实密钥值(防止其它形式的意外拼接)。
    """
    redacted = re.sub(
        r"((?:access_token|token|sign|key|secret|password)=)[^&\s'\"]+",
        r"\1<REDACTED>",
        str(text),
        flags=re.IGNORECASE,
    )
    for name in ("DINGTALK_WEBHOOK", "DINGTALK_SECRET", "LLM_API_KEY",
                 "LIVEKIT_API_SECRET", "JKINCO_SESSION_SECRET"):
        value = os.getenv(name, "")
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "<REDACTED>")
    return redacted


def get_dingtalk_sign() -> tuple[str, str]:
    """生成钉钉机器人加签参数(timestamp, sign)。"""
    secret = os.getenv("DINGTALK_SECRET")
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_to_dingtalk(summary_text: str, app_mode: str = "talk") -> str:
    """将纪要推送到钉钉并返回状态。异常一律兜底为可读错误字符串,不向上抛。"""
    try:
        # 防御性处理:确保文本是安全的 UTF-8 字符串,过滤无法编码的异常字符。
        # 同时去掉控制字符 —— encode/decode 这一步只处理编码问题,空字节与终端
        # 转义序列是合法 UTF-8,会原样发进群里(实测推送体里带着 \u0000)。
        safe_text = strip_control_characters(summary_text).encode("utf-8", errors="replace").decode("utf-8")

        msg = {
            "msgtype": "markdown",
            "markdown": {"title": output_title(app_mode), "text": safe_text},
        }
        webhook = os.getenv("DINGTALK_WEBHOOK")
        ts, sign = get_dingtalk_sign()
        url = f"{webhook}&timestamp={ts}&sign={sign}"

        # 手动序列化 + 显式 UTF-8 字节流,杜绝特定 locale 下隐式触发 latin-1 编码的问题
        json_data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        resp = requests.post(
            url,
            data=json_data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )

        result = resp.json()
        if result.get("errcode") == 0:
            return "✅ 钉钉推送成功！请查看群消息。"
        return f"❌ 钉钉推送失败: {result.get('errmsg')}"
    except Exception as e:
        # 异常文本常包含完整 webhook(内含 access_token),requests 的连接错误尤其如此。
        # 该字符串会原样返回给前端,直接回显等于把群机器人令牌发给任何登录用户。
        # 因此:完整原因只进服务端日志,返回给客户端的一律脱敏。
        detail = _redact_secrets(str(e))
        LOGGER.error("钉钉推送异常:%s", detail)
        return f"❌ 钉钉推送异常: {detail}"
