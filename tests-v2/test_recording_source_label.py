"""录音来源标签必须准确。

原先服务端靠「有没有实时字幕」反推来源,而字幕依赖 Web Speech API:
- iOS Safari 基本不支持 -> 实时录音被记成「上传音频」
- 先录音再改上传文件,残留字幕 -> 上传文件被记成「实时录音」
两个方向都会错,而这个标签会永久写进历史记录。
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.main as main


def _run(input_mode: str, live_text: str) -> str:
    """跑一遍处理任务,返回落库时使用的来源标签。"""
    captured = {}

    def fake_save(transcript, summary, status, mode, source, overview, owner_username="", **_metadata):
        captured["source"] = source
        return "rec-1"

    with patch.object(main.core, "transcribe_audio", return_value="转写内容"), \
         patch.object(main.core, "infer_app_mode_best_effort", return_value=("talk", "理由")), \
         patch.object(main.core, "mode_label", return_value="会议纪要"), \
         patch.object(main.core, "should_generate_and_push", return_value=False), \
         patch.object(main.core, "should_push_to_dingtalk", return_value=False), \
         patch.object(main.core, "save_meeting_history_record", side_effect=fake_save), \
         patch.object(main, "set_job"):
        main.JOB_CAPACITY.acquire()
        main.run_processing_job("job-1", "/tmp/a.webm", live_text, "只转写，不推送",
                                "auto", "tester", "", input_mode)
    return captured.get("source", "")


def test_live_recording_without_speech_api_is_still_labelled_live():
    """iOS Safari 没有实时字幕,但录的确实是实时录音。"""
    assert _run("live", "") == "实时录音"


def test_uploaded_file_with_stale_captions_is_not_labelled_live():
    """先录音再改上传文件时,残留字幕不该把它变成「实时录音」。"""
    assert _run("upload", "上一轮残留的字幕文本") == "上传音频"


def test_falls_back_to_heuristic_for_old_clients():
    """不带 input_mode 的老客户端仍走原有判断,不至于全部标错。"""
    assert _run("", "有字幕") == "实时录音"
    assert _run("", "") == "上传音频"


def test_transcript_is_preserved_when_minutes_generation_fails():
    """纪要生成失败时,转写结果必须已经落库。

    转写是整条链路里最贵的一步(一小时录音几十秒、六小时几分钟),而音频文件
    在任务收尾时就会被删除。原先大模型一次抖动就让整个任务失败、转写一并丢弃 ——
    实时录音根本无法重录,让用户「再传一次」是不成立的。
    """
    captured = {}

    def fake_save(transcript, summary, status, mode, source, overview, owner_username="", **_metadata):
        captured.update(transcript=transcript, summary=summary, status=status)
        return "rec-2"

    with patch.object(main.core, "transcribe_audio", return_value="这是一小时会议的完整转写"), \
         patch.object(main.core, "infer_app_mode_best_effort", return_value=("talk", "理由")), \
         patch.object(main.core, "mode_label", return_value="会议纪要"), \
         patch.object(main.core, "should_generate_and_push", return_value=True), \
         patch.object(main.core, "should_push_to_dingtalk", return_value=False), \
         patch.object(main.core, "generate_minutes", side_effect=RuntimeError("大模型超时")), \
         patch.object(main.core, "save_meeting_history_record", side_effect=fake_save), \
         patch.object(main, "set_job") as set_job:
        main.JOB_CAPACITY.acquire()
        main.run_processing_job("job-2", "/tmp/a.webm", "", "生成纪要，暂不推送",
                                "auto", "tester", "", "live")

    assert captured.get("transcript") == "这是一小时会议的完整转写", "转写没有被保存,用户白录了"
    assert "纪要生成失败" in captured.get("status", ""), "状态里必须说明纪要失败,否则用户不知道发生了什么"
    # 任务本身仍应视为完成:转写已交付,用户可从历史会议重新生成纪要
    final = [c for c in set_job.call_args_list if c.kwargs.get("status") == "completed"]
    assert final, "转写已保存,任务不该被标记为彻底失败"
