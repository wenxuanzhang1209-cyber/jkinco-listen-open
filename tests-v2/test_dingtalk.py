"""钉钉推送模块的行为契约测试。

不触达真实钉钉:加签为纯函数可直接验证;推送在测试环境指向 example.invalid,
连接失败会走异常兜底返回可读错误串,据此验证「异常不外抛」的契约。
并验证 JKincoListen 仍以同名 re-export。
"""
import os

# 必须强制覆盖而非 setdefault:若开发者 shell 里载入了真实 .env,
# setdefault 会保留真实 webhook,导致测试向生产钉钉群真实推送消息。
os.environ["DINGTALK_SECRET"] = "test-secret"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
# 本文件的 re-export 测试要导入单体,单体启动会校验必填变量。
# 补齐后本文件可单独运行,不再依赖其它测试文件先设置环境。
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/chat/completions")
os.environ.setdefault("LLM_MODEL_NAME", "test-model")

import jkinco_dingtalk as dingtalk


def test_get_dingtalk_sign_is_deterministic_shape():
    ts, sign = dingtalk.get_dingtalk_sign()
    assert ts.isdigit() and len(ts) >= 13  # 毫秒时间戳
    assert isinstance(sign, str) and sign  # URL 编码后的签名非空
    # 同一时刻两次调用签名格式一致(均为合法 URL 编码串)
    assert "%" in sign or sign.isalnum()


def test_send_to_dingtalk_never_raises_and_returns_status_string():
    # 指向 example.invalid,网络必失败;必须兜底为字符串而非抛异常
    result = dingtalk.send_to_dingtalk("# 测试纪要\n内容", "general")
    assert isinstance(result, str)
    assert result.startswith("❌")  # 异常/失败兜底


def test_send_to_dingtalk_handles_non_string_input():
    result = dingtalk.send_to_dingtalk(None, "talk")
    assert isinstance(result, str)


def test_reexport_from_monolith_is_identical():
    from backend import core

    assert core.send_to_dingtalk is dingtalk.send_to_dingtalk


def test_push_failure_never_leaks_webhook_token(monkeypatch):
    """推送失败的返回值会原样显示给前端,绝不能带出 webhook 令牌。

    requests 的连接异常通常把完整 URL 写进消息,而 access_token 就在 URL 里。
    """
    monkeypatch.setenv("DINGTALK_WEBHOOK", "https://oapi.example.invalid/robot/send?access_token=TOPSECRET123")
    monkeypatch.setenv("DINGTALK_SECRET", "SECRETSIGNKEY456")

    def boom(*args, **kwargs):
        # 模拟 requests 把完整 URL 带进异常文本
        raise RuntimeError(
            "HTTPSConnectionPool: Max retries exceeded with url: "
            "/robot/send?access_token=TOPSECRET123&timestamp=1&sign=abc"
        )

    monkeypatch.setattr(dingtalk.requests, "post", boom)
    result = dingtalk.send_to_dingtalk("内容", "talk")

    assert isinstance(result, str) and result.startswith("❌")
    assert "TOPSECRET123" not in result, f"响应泄露了 access_token: {result}"
    assert "SECRETSIGNKEY456" not in result
    assert "<REDACTED>" in result, "应保留脱敏标记以便诊断"


def test_redact_covers_common_credential_params():
    raw = "url=https://x/y?token=aaaa1111&sign=bbbb2222&key=cccc3333"
    out = dingtalk._redact_secrets(raw)
    for leaked in ("aaaa1111", "bbbb2222", "cccc3333"):
        assert leaked not in out
