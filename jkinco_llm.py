"""大模型调用客户端（开源本地版）。

封装对 OpenAI 兼容 Chat Completions 接口的调用，包含主模型 → 备用模型自动降级。
默认指向本机 Ollama（http://127.0.0.1:11434），也兼容任意 OpenAI 兼容端点
（vLLM、LM Studio、llama.cpp server、云厂商兼容接口等）。
只依赖标准库与 requests，配置全部来自环境变量，无共享状态。
"""
from __future__ import annotations

import json
import os
import time

import requests

from jkinco_logging import get_logger
from jkinco_text import redact_secrets

LOGGER = get_logger("llm")

# 每个模型最多尝试几次。此前是「一个模型只打一枪,失败立刻换备用」,而且两枪之间
# 没有任何间隔 —— 实测一次瞬时 503 或连接重置就能同时打掉主备两次调用,因为两者
# 打的是同一个 endpoint。代价不对等:分块生成会先花几分钟、按量计费跑完全部分片
# 提取,最后的合稿只有一次调用,它被一次抖动打掉就等于把前面的钱和时间全部作废。
MAX_ATTEMPTS_PER_MODEL = max(1, int(os.getenv("JKINCO_LLM_MAX_ATTEMPTS", "2") or 2))
RETRY_BACKOFF_SECONDS = max(0.0, float(os.getenv("JKINCO_LLM_RETRY_BACKOFF", "2") or 2))

# 本地默认值：未配置时直接使用 Ollama。
LOCAL_LLM_BASE_URL = "http://127.0.0.1:11434/v1/chat/completions"
LOCAL_LLM_MODEL = "qwen2.5:7b-instruct"


def _is_retryable(error: Exception) -> bool:
    """能靠重试恢复的才重试。

    判据按「失败成本」分,而不是只看是不是临时故障:

    - 连接中断 / 429 / 5xx:秒级就失败,重试一次几乎不花时间,收益明确;
    - 4xx(请求体不合法、鉴权失败、模型名不存在):重试多少次都是同样的结果,
      直接换备用模型更有意义;
    - 超时:**不重试**。一次读超时意味着这个模型已经烧掉整整 attempt_timeout 才
      失败,同一模型跑同一段提示词大概率再烧一遍 —— 那会把最坏耗时从 240 秒推到
      480 秒,而作业槽位在这期间一直被占着,反而拖垮排队。这种情况换备用模型才对。
      注意 ConnectTimeout 同时是 ConnectionError 的子类,必须先于它判掉。
    """
    if isinstance(error, requests.Timeout):
        return False
    if isinstance(error, requests.ConnectionError):
        return True
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    # 「模型返回空内容」多半是对端瞬时抽风,值得再要一次
    return isinstance(error, RuntimeError)


def call_llm(
    prompt: str,
    timeout: int = 180,
    model_name: str | None = None,
    fallback_model: str | None = None,
    thinking: bool | None = None,
    temperature: float = 0.3,
) -> str:
    headers = {
        "Authorization": f"Bearer {os.getenv('LLM_API_KEY')}",
        "Content-Type": "application/json; charset=utf-8",
    }
    primary_model = (model_name or os.getenv("LLM_MODEL_NAME") or LOCAL_LLM_MODEL).strip()
    fallback_model = (fallback_model if fallback_model is not None else os.getenv("JKINCO_LLM_FALLBACK_MODEL", "")).strip()
    models = [primary_model] + ([fallback_model] if fallback_model and fallback_model != primary_model else [])
    thinking_enabled = (
        thinking
        if thinking is not None
        else os.getenv("JKINCO_LLM_THINKING", "1").strip().lower() not in {"0", "false", "no", "off"}
    )
    errors = []

    base_url = (os.getenv("LLM_BASE_URL") or LOCAL_LLM_BASE_URL).strip()
    if not base_url:
        raise RuntimeError("未配置 LLM_BASE_URL")
    try:
        attempt_timeout = int(os.getenv("JKINCO_LLM_ATTEMPT_TIMEOUT", "120"))
    except ValueError:
        # env 写错不该表现成「两个模型都不可用」—— 那会让人去查模型服务
        LOGGER.warning("JKINCO_LLM_ATTEMPT_TIMEOUT 不是整数,按 120 秒处理")
        attempt_timeout = 120
    attempt_timeout = min(timeout, max(1, attempt_timeout))

    # 循环变量另起名字:原来复用了入参 model_name,读代码时分不清此刻用的是调用方
    # 指定的模型还是降级链里的当前项。
    for index, current_model in enumerate(models):
        if not current_model:
            continue
        payload = {
            "model": current_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        # 本地版不注入任何云端专属参数；如需启用 Qwen3 等模型的思考模式，
        # 可在兼容端点侧按模型默认行为开启。
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                response = requests.post(
                    base_url,
                    headers=headers,
                    data=body,
                    timeout=attempt_timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not str(content or "").strip():
                    raise RuntimeError("模型返回空内容")
                if index > 0 or attempt > 1:
                    LOGGER.info("模型调用成功:%s(第 %d 次尝试)", current_model, attempt)
                return content
            except Exception as error:
                last_attempt = attempt >= MAX_ATTEMPTS_PER_MODEL
                if last_attempt or not _is_retryable(error):
                    errors.append(f"{current_model}: {error}")
                    if index + 1 < len(models):
                        LOGGER.warning("模型 %s 不可用,切换 %s", current_model, models[index + 1])
                    break
                delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                LOGGER.warning(
                    "模型 %s 调用失败(第 %d/%d 次),%.0f 秒后重试:%s",
                    current_model, attempt, MAX_ATTEMPTS_PER_MODEL, delay,
                    redact_secrets(str(error))[:200],
                )
                time.sleep(delay)

    # 在这里统一脱敏:错误文本会被上游拼进「场景识别理由」「概览生成失败」等文案,
    # 落库并展示给用户,而 requests 的异常会把完整请求 URL(可能含 query 里的凭证)
    # 和内部主机名原样带出。钉钉链路早有同样的处理,这里补齐另一半。
    raise RuntimeError(redact_secrets("；".join(errors))[:400])
