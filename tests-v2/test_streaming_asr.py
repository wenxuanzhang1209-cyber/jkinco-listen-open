"""实验性本地流式识别的契约测试（JKINCO_REALTIME_LOCAL_ASR）。"""

from __future__ import annotations

import numpy as np
import pytest

import jkinco_asr


class FakeStreamingModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        text = "监理单位要求旁站" if kwargs.get("is_final") else "监理单位要求"
        return [{"text": text}]


def test_streaming_transcribe_calls_model_and_corrects_terms(monkeypatch):
    fake = FakeStreamingModel()
    monkeypatch.setattr(jkinco_asr, "get_streaming_asr_model", lambda: fake)
    pcm = np.zeros(16000 * 2, dtype=np.int16).tobytes()

    text, cache = jkinco_asr.streaming_transcribe(pcm, {"k": 1}, is_final=True)

    assert text == "监理单位要求旁站"
    assert fake.calls[0]["cache"] == {"k": 1}
    assert fake.calls[0]["is_final"] is True
    assert fake.calls[0]["chunk_size"] == jkinco_asr.STREAMING_CHUNK_SIZE
    assert cache["k"] == 1


def test_streaming_transcribe_empty_input_skips_model(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("空输入不应调用模型")

    monkeypatch.setattr(jkinco_asr, "get_streaming_asr_model", boom)
    text, cache = jkinco_asr.streaming_transcribe(b"", {"k": 1}, is_final=True)
    assert text == ""
    assert cache == {"k": 1}


def test_get_streaming_model_requires_funasr(monkeypatch):
    monkeypatch.setattr(jkinco_asr, "AutoModel", None)
    monkeypatch.setattr(jkinco_asr, "STREAMING_MODEL", None)
    with pytest.raises(RuntimeError):
        jkinco_asr.get_streaming_asr_model()
