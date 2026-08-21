"""Web 层依赖的引擎能力,集中在此声明。

背景:后端此前用 `import JKincoListen as core` 取用这些能力。但 JKincoListen.py
是 Gradio 单体,模块级会加载 Gradio 全家桶、构建整个 UI、读盘解析全部历史记录、
扫描录音设备目录、读取并 base64 编码 logo —— 这一切都发生在 FastAPI 生产进程里。
实测代价:多 110MB 常驻内存、多 1.3 秒启动、多加载 1239 个模块,而后端真正需要的
18 个名字里有 16 个只是单体从 jkinco_* 子模块转发的。

因此改为直接从各子模块引入。本模块只做聚合,不含逻辑 —— 它同时也是一份清单:
Web 层对引擎的依赖面就是下面这些,任何新增都应当是有意识的决定。

单体自身保持不变,仍可独立运行(legacy Gradio 容器),两侧共用同一批子模块。
"""
from __future__ import annotations

# .env 加载与必填项校验。必须在其它子模块之前执行:它们在模块级读取环境变量。
from jkinco_config import load_config

load_config()

from jkinco_asr import transcribe_audio  # noqa: E402
from jkinco_assistant import ask_xiaozhi  # noqa: E402
from jkinco_classifier import infer_app_mode_best_effort  # noqa: E402
from jkinco_devices import recorder_dropdown_data  # noqa: E402
from jkinco_dingtalk import send_to_dingtalk  # noqa: E402
from jkinco_export import EXPORT_DIR, export_summary_docx, export_summary_pdf  # noqa: E402
from jkinco_history import (  # noqa: E402
    HISTORY_DIR,
    HISTORY_LOCK,
    HistoryUnavailable,
    load_history_with_search,
    load_meeting_history_for_update,
    find_archived_record,
    iter_meeting_history,
    load_meeting_history,
    normalize_usernames,
    save_meeting_history_record,
    write_meeting_history,
)
from jkinco_pipeline import should_generate_and_push, should_push_to_dingtalk  # noqa: E402
from jkinco_reports import generate_meeting_overview, generate_minutes  # noqa: E402
from jkinco_scenes import canonical_mode, mode_label  # noqa: E402

__all__ = [
    "EXPORT_DIR",
    "HISTORY_DIR",
    "HISTORY_LOCK",
    "ask_xiaozhi",
    "canonical_mode",
    "export_summary_docx",
    "export_summary_pdf",
    "generate_meeting_overview",
    "generate_minutes",
    "infer_app_mode_best_effort",
    "HistoryUnavailable",
    "load_history_with_search",
    "load_meeting_history_for_update",
    "find_archived_record",
    "iter_meeting_history",
    "load_meeting_history",
    "mode_label",
    "normalize_usernames",
    "recorder_dropdown_data",
    "save_meeting_history_record",
    "send_to_dingtalk",
    "should_generate_and_push",
    "should_push_to_dingtalk",
    "transcribe_audio",
    "write_meeting_history",
]
