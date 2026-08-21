"""统一日志出口。

此前全项目用 `print()` 当日志:输出没有时间戳、没有级别、没有来源模块,在容器日志里
与 uvicorn 的访问日志混成一片,既无法按严重程度过滤,也无法判断某条消息来自哪里。
排障时只能靠关键词硬搜。

这里建立独立于 uvicorn 的 `jkinco` 日志命名空间(propagate=False,不受 uvicorn
日志配置影响,也不会污染它),统一格式为:

    2026-07-28 14:05:01 WARNING  jkinco.asr  云端语音识别暂不可用，自动切换本地模型

级别用法约定:
  - warning:功能降级但已自动兜底(切换模型、跳过推送),运维需要知道但无需立刻处理
  - error  :本次操作确实失败,用户会看到失败结果
  - info   :关键状态变更(纪要生成完成等)
"""
from __future__ import annotations

import logging
import os
import sys

_ROOT_NAME = "jkinco"
_configured = False


def configure_logging() -> None:
    """初始化 jkinco 日志命名空间。重复调用安全。"""
    global _configured
    if _configured:
        return
    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(os.getenv("JKINCO_LOG_LEVEL", "INFO").upper())
    # 不向上传播:uvicorn 会给 root 装自己的 handler,传播会导致每条日志打印两次,
    # 且格式被 uvicorn 接管。这里自带 handler,输出格式完全自主。
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    _configured = True


def get_logger(module_name: str) -> logging.Logger:
    """取得某个模块的 logger,例如 get_logger("asr") -> jkinco.asr。"""
    configure_logging()
    return logging.getLogger(f"{_ROOT_NAME}.{module_name}")
