"""模型调用要扛得住一次瞬时抖动。

call_llm 是所有模型调用的唯一出口。改之前它对每个模型只打一枪,主备两枪之间
零间隔,而且打的是同一个 endpoint —— 实测一次瞬时 503 或连接重置就能同时打掉
两次,整个纪要生成随之失败。

代价是不对等的:长转写会先花几分钟、按量计费跑完全部分片提取,最后的合稿只有
一次调用(jkinco_reports.py 第 69 行附近),它被一次抖动打掉就等于把前面的钱和
时间全部作废。分片那一层早有容错(全挂才报错),合稿这一层没有。

重试的判据按「失败成本」分,不是按「是不是临时故障」分 —— 超时故意不重试,
见 test_timeout_does_not_retry_the_same_model。
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
import requests

import jkinco_llm


class _Response:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._body


OK = {"choices": [{"message": {"content": "正常纪要"}}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL_NAME", "primary")
    monkeypatch.setenv("JKINCO_LLM_FALLBACK_MODEL", "backup")


@pytest.fixture
def no_sleep(monkeypatch):
    """退避是真的要 sleep 的,测试里只记录时长、不真等。

    退避秒数也显式打回生产默认值:conftest 为了让整套测试跑得快,把
    JKINCO_LLM_RETRY_BACKOFF 置成了 0(见那里的说明)。本文件要验证的恰恰是
    「两次尝试之间必须退避」,不能跟着那个加速设置走。
    """
    monkeypatch.setattr(jkinco_llm, "RETRY_BACKOFF_SECONDS", 2.0)
    slept = []
    monkeypatch.setattr(jkinco_llm.time, "sleep", lambda s: slept.append(s))
    return slept


def _driver(failures):
    """failures 是一串「这次调用要抛什么」,None 表示成功。"""
    calls = {"models": []}

    def fake_post(url, headers=None, data=None, timeout=None):
        import json as _json

        calls["models"].append(_json.loads(data.decode("utf-8"))["model"])
        index = len(calls["models"]) - 1
        outcome = failures[index] if index < len(failures) else None
        if outcome is None:
            return _Response(200, OK)
        if isinstance(outcome, int):
            return _Response(outcome)
        raise outcome

    return fake_post, calls


def test_one_transient_503_no_longer_kills_the_call(no_sleep):
    """改之前:第一枪 503、第二枪(备用模型)也 503 就整体失败。现在第一个模型自己会再试。"""
    fake, calls = _driver([503])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "primary"], "应当先重试同一个模型,而不是立刻降级"
    assert no_sleep == [2.0], "两次尝试之间必须退避,限流场景下立刻重试只会继续被拒"


def test_connection_reset_is_retried(no_sleep):
    fake, calls = _driver([requests.ConnectionError("连接被重置")])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "primary"]


def test_rate_limit_is_retried(no_sleep):
    fake, calls = _driver([429])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "primary"]


def test_empty_content_is_retried(no_sleep):
    """对端偶发返回空 content,再要一次通常就有了。"""
    fake, calls = _driver([200])  # 200 但 body 为空 → 走下面的自定义 driver
    responses = [_Response(200, {"choices": [{"message": {"content": "  "}}]}), _Response(200, OK)]
    with patch.object(jkinco_llm.requests, "post", lambda *a, **k: responses.pop(0)):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"


def test_persistent_failure_still_falls_back_to_the_second_model(no_sleep):
    """主模型真的挂了,重试用光后必须降级,不能死磕。"""
    fake, calls = _driver([503, 503])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "primary", "backup"]


def test_bad_request_skips_retry_and_goes_straight_to_fallback(no_sleep):
    """4xx 是请求本身的问题,重试是纯浪费 —— 直接换模型。"""
    fake, calls = _driver([400])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "backup"], "4xx 不该重试同一个模型"
    assert no_sleep == [], "4xx 不该退避等待"


def test_timeout_does_not_retry_the_same_model(no_sleep):
    """故意不重试超时 —— 这条很容易被后人「顺手补全」而改坏。

    一次读超时说明该模型已经烧掉整整 attempt_timeout(默认 120 秒)才失败,
    同一模型跑同一段提示词大概率再烧一遍。若重试,最坏耗时从 240 秒变成 480 秒,
    而作业槽位在这期间一直被占着 —— 排队会被拖垮。正确做法是立刻换备用模型。
    """
    fake, calls = _driver([requests.Timeout("读超时")])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary", "backup"], "超时应当直接降级,不得重试同一模型"
    assert no_sleep == []


def test_connect_timeout_counts_as_timeout_not_connection_error():
    """ConnectTimeout 同时继承 ConnectionError,判断顺序错了就会被当成可重试。"""
    assert issubclass(requests.ConnectTimeout, requests.ConnectionError)
    assert jkinco_llm._is_retryable(requests.ConnectTimeout("连接超时")) is False


def test_worst_case_attempt_count_is_bounded(no_sleep):
    """所有模型、所有重试都失败时,总调用次数必须有上限。"""
    fake, calls = _driver([503] * 20)
    with patch.object(jkinco_llm.requests, "post", fake):
        with pytest.raises(RuntimeError):
            jkinco_llm.call_llm("提示词")
    expected = jkinco_llm.MAX_ATTEMPTS_PER_MODEL * 2  # 主 + 备
    assert len(calls["models"]) == expected, f"实际打了 {len(calls['models'])} 次"


def test_success_on_first_try_costs_nothing_extra(no_sleep):
    """常态路径不能因为加了重试而变慢或多打请求。"""
    fake, calls = _driver([])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"
    assert calls["models"] == ["primary"]
    assert no_sleep == []


def test_missing_base_url_reports_the_real_reason():
    """配置缺失曾经表现成「两个模型都不可用」,让人去查模型服务。"""
    with patch.dict("os.environ", {"LLM_BASE_URL": ""}), \
            patch.object(jkinco_llm, "LOCAL_LLM_BASE_URL", ""):
        with pytest.raises(RuntimeError, match="未配置 LLM_BASE_URL"):
            jkinco_llm.call_llm("提示词")


def test_bad_attempt_timeout_env_does_not_look_like_a_model_outage(no_sleep, monkeypatch):
    monkeypatch.setenv("JKINCO_LLM_ATTEMPT_TIMEOUT", "两分钟")
    fake, calls = _driver([])
    with patch.object(jkinco_llm.requests, "post", fake):
        assert jkinco_llm.call_llm("提示词") == "正常纪要"


def test_caller_timeout_still_caps_the_attempt(no_sleep, monkeypatch):
    """调用方传的 timeout 是总预算,单次尝试不得超过它。"""
    monkeypatch.setenv("JKINCO_LLM_ATTEMPT_TIMEOUT", "120")
    seen = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        seen["timeout"] = timeout
        return _Response(200, OK)

    with patch.object(jkinco_llm.requests, "post", fake_post):
        jkinco_llm.call_llm("提示词", timeout=30)
    assert seen["timeout"] == 30


def test_error_message_is_still_redacted(no_sleep):
    """脱敏这道防线不能因为重构掉了:错误文本会落库并展示给用户。"""
    fake, _ = _driver([requests.ConnectionError("连接 https://user:secret@host/v1 失败")] * 8)
    with patch.object(jkinco_llm.requests, "post", fake):
        with pytest.raises(RuntimeError) as caught:
            jkinco_llm.call_llm("提示词")
    assert "secret" not in str(caught.value)


def test_retry_reuses_the_same_request_body(no_sleep):
    """重试必须发同一份请求体 —— 别在循环里重新构造出不一样的 payload。"""
    bodies = []

    def fake_post(url, headers=None, data=None, timeout=None):
        bodies.append(data)
        if len(bodies) == 1:
            return _Response(503)
        return _Response(200, OK)

    with patch.object(jkinco_llm.requests, "post", fake_post):
        jkinco_llm.call_llm("提示词")
    assert bodies[0] == bodies[1]
