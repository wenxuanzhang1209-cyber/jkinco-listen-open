"""处理模式:决定一次录音走完哪几步流水线。

「处理模式」是前端下拉框与后端之间的字符串契约,此前这三个中文串散落在
前端 <option>、后端默认值、单体的两个判断函数里,改任何一处的措辞都会静默
改变行为(判断退化为恒假,纪要照生成或干脆不生成,且不会报错)。
这里给出唯一定义,并提供判定函数。
"""
from __future__ import annotations

# 三种处理模式。值即前端下拉框展示的文案,前后端必须逐字一致。
SUMMARY_ONLY = "生成纪要，暂不推送"
SUMMARY_AND_PUSH = "生成并推送钉钉"
TRANSCRIBE_ONLY = "只转写，不推送"

PROCESS_MODES = (SUMMARY_ONLY, SUMMARY_AND_PUSH, TRANSCRIBE_ONLY)
DEFAULT_PROCESS_MODE = SUMMARY_ONLY


def should_generate_and_push(process_mode) -> bool:
    """是否需要调用大模型生成纪要。

    兼容布尔入参:单体早期用勾选框表达该开关,历史调用点仍可能传 True/False。
    """
    if isinstance(process_mode, bool):
        return process_mode
    return process_mode != TRANSCRIBE_ONLY


def should_push_to_dingtalk(process_mode) -> bool:
    """是否需要把纪要推送到钉钉群。"""
    return process_mode == SUMMARY_AND_PUSH
