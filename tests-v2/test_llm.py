"""大模型调用模块的行为契约测试。

不触达真实模型:测试环境 LLM_BASE_URL 指向 example.invalid,请求必失败,
据此验证「所有模型都不可用时抛 RuntimeError 且汇总错误」的契约,以及
JKincoListen 仍以同名 re-export。
"""
import os

# 强制覆盖:若 shell 载入了真实 .env,setdefault 会保留真实端点,
# 导致测试真实调用大模型(产生费用且结果不可控)。
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
os.environ.setdefault("DINGTALK_WEBHOOK", "http://127.0.0.1:1/webhook")
os.environ.setdefault("DINGTALK_SECRET", "test-secret")

import pytest

import jkinco_llm as llm


def test_all_models_unreachable_raises_runtimeerror():
    with pytest.raises(RuntimeError) as exc:
        llm.call_llm("你好", timeout=2, model_name="test-model", fallback_model="")
    # 错误信息应包含尝试过的模型名,便于诊断
    assert "test-model" in str(exc.value)


def test_empty_model_config_raises():
    with pytest.raises(RuntimeError):
        llm.call_llm("你好", timeout=2, model_name="", fallback_model="")


def test_reexport_from_monolith_is_identical():
    import jkinco_llm as llm_module

    assert llm_module.call_llm is llm.call_llm
