"""什么都没说的录音,不该生成一份看起来像模像样的纪要。

用户反馈:实时纪要和录音,「可能什么话都没说,就会有类似于一段标题」。

成因:两处闸门都写作 `if not transcript`,只挡完全空串。但没人说话时 ASR 往往
不返回空 —— 呼吸声、键盘声、空调声会诱发一两个语气词或一个句号。只要有一个
字符,闸门就放行,后面照常走完整条生成流程:提示词里 <会议素材> 近乎为空,而
指令仍是「请把以下转写整理为《会议纪要》」,模型只能照章节要求硬造出标题。
严格模板的几档更明显 —— 整套章节骨架配上「待确认」,看起来像一份真纪要。

判据不能用长度阈值:个人备忘的「明天下午三点开会」只有 8 个字,是完全有效的
内容。改为「剥掉空白与标点后,剩下的是不是只有语气词」。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jkinco_text import has_meaningful_speech


SILENT = [
    ("", "完全空"),
    ("   \n\t  ", "只有空白"),
    ("。", "只有一个句号"),
    ("嗯。", "一个语气词"),
    ("嗯嗯，啊……", "多个语气词加标点"),
    ("呃，，，哦。", "语气词混标点"),
    ("Um... uh,", "英文语气词"),
    ("​​", "零宽字符"),
    ("哦。嗯。啊。", "分句的语气词"),
    # 整段只有一句「嗯？」的会议没有任何可整理的内容 —— 要守的是「语气词开头的
    # 真话」不被误伤(见下方 REAL),而不是把孤零零的语气词也算成内容。
    ("嗯？", "只有一个疑问语气词"),
]

REAL = [
    ("明天下午三点开会", "个人备忘,只有 8 个字"),
    ("开会", "极短但有效"),
    ("嗯，那就这么定了。", "以语气词开头的真话"),
    ("OK，收到。", "英文短句"),
    ("这个方案我们下周再评审一次。", "正常句子"),
    ("1号楼验收", "含数字"),
]


@pytest.mark.parametrize("text, label", SILENT)
def test_silence_is_recognised(text, label):
    assert has_meaningful_speech(text) is False, f"「{label}」应判为无内容"


@pytest.mark.parametrize("text, label", REAL)
def test_real_speech_is_never_suppressed(text, label):
    """误伤比漏判严重得多:把真话判成噪音,用户会直接丢掉一次会议记录。"""
    assert has_meaningful_speech(text) is True, f"「{label}」被误判为无内容"


def test_none_and_non_string_do_not_crash():
    for value in (None, 0, [], {}):
        assert has_meaningful_speech(value) is False


# --- 两条产品路径都要接上这个判据 ---

@pytest.mark.parametrize("noise", ["嗯。", "。", "   ", "呃，，，哦。"])
def test_recording_path_rejects_a_silent_transcript(noise):
    """上传/录音这条路:噪音转写必须在进入模型之前就被拦下。

    直接跑真实的 run_processing_job —— 在测试里重写一遍判断逻辑是没有意义的,
    那样实现改坏了用例照样过。
    """
    import backend.main as main

    job_id = f"test-silent-{abs(hash(noise))}"
    with patch.object(main.core, "generate_minutes", side_effect=AssertionError("不该进入生成流程")), \
         patch.object(main.core, "infer_app_mode_best_effort", side_effect=AssertionError("不该识别场景")):
        main.run_processing_job(job_id, None, noise, main.DEFAULT_PROCESS_MODE, "auto", "tester")

    job = main.JOBS[job_id]
    assert job["status"] == "failed", f"噪音「{noise}」仍被当作有效录音处理"
    assert "未检测到有效语音内容" in job["message"], job["message"]


def test_recording_path_still_accepts_a_short_real_note():
    """反向:极短但有效的内容必须照常走完流程,不能被这道闸门误伤。"""
    import backend.main as main

    job_id = "test-short-real-note"
    with patch.object(main.core, "infer_app_mode_best_effort", return_value=("personal", "个人备忘")), \
         patch.object(main.core, "generate_minutes", return_value="## 待办\n- 明天下午三点开会"), \
         patch.object(main.core, "generate_meeting_overview", return_value="## 一、会议概述\n- 备忘"), \
         patch.object(main.core, "save_meeting_history_record", return_value="rec-1"), \
         patch.object(main.core, "should_push_to_dingtalk", return_value=False):
        main.run_processing_job(job_id, None, "明天下午三点开会", main.DEFAULT_PROCESS_MODE, "auto", "tester")

    job = main.JOBS[job_id]
    assert job["status"] == "completed", f"8 个字的有效备忘被误拦:{job.get('message')}"


def test_meeting_path_marks_empty_instead_of_generating():
    """实时会议这条路:标记为「没有转写内容」,而不是造一份纪要。

    这个分支本来就存在(minutes_status='empty'),问题只在判据太松。
    """
    import backend.meetings as meetings

    assert meetings.has_meaningful_speech("嗯。") is False
    assert meetings.MINUTES_STATUS_LABELS["empty"] == "本次会议没有检测到有效发言，未生成纪要"


def test_both_entry_points_use_the_shared_predicate():
    """守住「不会有第三处再写回 `if not transcript`」。"""
    import inspect

    import backend.main as main
    import backend.meetings as meetings

    finalize = inspect.getsource(meetings._finalize_minutes)
    # 实时那条判的是不带署名的 speech_only —— 原因见本文件末尾那组用例
    assert "has_meaningful_speech(speech_only)" in finalize, "实时会议这条路没用共享判据"
    job = inspect.getsource(main.run_processing_job)
    assert "has_meaningful_speech(transcript)" in job, "录音这条路没用共享判据"


# --- 署名前缀会把闸门整个架空 ---
# 上面的修复对上传那条路有效(那是裸转写),但实时会议这条路一直没生效:
# _final_transcript_text 返回的是「说话人：内容」,而「主持人」三个字本身就是
# 有意义的文字 —— has_meaningful_speech("主持人：嗯。") 恒为 True。
# 单元用例看不见这一层,是端到端用例把它照出来的。

def test_the_speaker_prefix_defeats_the_check():
    """先证明这个坑是真的:带署名的文本一律判为「有内容」。"""
    assert has_meaningful_speech("嗯。") is False
    assert has_meaningful_speech("主持人：嗯。") is True, "前提变了,下面的用例失去意义"
    assert has_meaningful_speech("张三：嗯。\n李四：啊。") is True


def test_finalize_checks_the_speech_not_the_formatted_transcript():
    """所以 _finalize_minutes 必须判不带署名的那一份。"""
    import inspect

    import backend.meetings as meetings

    source = inspect.getsource(meetings._finalize_minutes)
    assert "has_meaningful_speech(speech_only)" in source, "又拿带署名的文本去判了"
    assert "_final_speech_only" in source


def test_speech_only_really_drops_the_names():
    import inspect

    import backend.meetings as meetings

    source = inspect.getsource(meetings._final_speech_only)
    assert "display_name" not in source, "_final_speech_only 里不该再拼署名"
    assert "SELECT s.text" in source
