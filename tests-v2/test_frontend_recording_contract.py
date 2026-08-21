"""实时录音「停止即生成」的前端契约。

这几条都是实际踩过的坑,靠读代码或类型检查发现不了,所以在这里钉死:

1. 自动提交只能发生在用户主动点「停止录音」时。切场景、开新录音、组件卸载
   同样会走收尾流程 —— 那些时候用户并没有要出纪要,自动跑起来会白占任务配额、
   白花模型调用,而面板已被重置、他还看不见。

2. 恢复草稿之后必须能处理。实时录音下已经没有「开始处理」按钮,恢复出来的录音
   如果不自动接上处理,就会卡在面板里永远提交不了。

3. 轮询用的 useEffect 必须排在所有提前返回之前。hooks 调用数量随渲染分支变化
   会让 React 抛 #310 整页白屏 —— 这条上过一次线。

4. 「是否生成中」必须由处理方上报,不能拿进度反推:任务失败时进度停在中途,
   反推出来的「生成中」永远不消失。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "frontend" / "src" / "App.tsx"


@pytest.fixture(scope="module")
def source() -> str:
    return APP.read_text(encoding="utf-8")


def test_auto_submit_requires_an_explicit_user_stop(source):
    match = re.search(r"if \(result && ([^)]*?) && modeRef\.current === \"live\"\) void submit\(result\);", source)
    assert match, "自动提交的条件被改动了,请同步更新本测试"
    assert "stoppedByUser" in match.group(1), "自动提交没有要求「用户主动停止」"
    assert "capture.stoppedByUser = true;" in source, "停止录音时没有打上标记"


def test_session_reset_does_not_set_the_user_stop_flag(source):
    """切场景/开新录音那条收尾路径不能打这个标记,否则又会自动提交。"""
    reset_block = source[source.index("}, [sessionKey, initialMode]);") - 700: source.index("}, [sessionKey, initialMode]);")]
    assert "stoppedByUser" not in reset_block, "会话重置路径打上了「用户主动停止」标记"


def test_recovered_draft_is_submitted(source):
    assert "void submit(recoveredFile);" in source, "恢复出来的草稿没有接上处理,会卡死在面板里"


def test_live_mode_has_no_manual_process_button(source):
    assert 'mode !== "live" && <button className="primary process-button"' in source


def test_polling_effect_precedes_every_early_return(source):
    poll = source.index("const realtimeId = meeting?.realtime_meeting_id;")
    for guard in ("if (loading) return", "if (!user) return <Login", "if (meetingSession) return"):
        assert poll < source.index(guard), f"轮询 effect 排在了「{guard}」之后,会触发 React #310 白屏"


def test_generating_flag_is_reported_not_inferred(source):
    assert "onProcessingChange" in source, "缺少处理状态的上报通道"
    assert not re.search(r"jobRunning\s*=\s*progress\s*>\s*0", source), (
        "「生成中」又改回从进度反推了 —— 任务失败时进度停在中途,提示会永远不消失"
    )


def test_settings_lock_covers_recording_and_processing(source):
    match = re.search(r"const settingsLocked = ([^;]+);", source)
    assert match, "settingsLocked 被改名了,请同步更新本测试"
    for flag in ("recording", "finalizing", "processing"):
        assert flag in match.group(1), f"设置锁定没有覆盖 {flag}"


def test_archived_view_hides_the_recording_inputs(source):
    """看一场已完成的会议时,整套录音输入都不该出现。

    这不只是观感:打开历史记录并不会重置录音面板(selectMeeting 不动 sessionKey),
    上一轮残留的文件或实时字幕还在,这时点「开始处理」提交的是那份残留素材,
    而用户正看着另一场会。
    """
    assert "if (viewingArchived) {" in source, "缺少「查看已完成会议」的分支"
    assert "recorder-panel--archived" in source
    assert "onStartNew" in source, "归档态必须给出回到录音的出口"


def test_archived_flag_excludes_active_sessions(source):
    """录音中、处理中都不算「在看历史」—— 那时面板正在被使用,不能把它换掉。"""
    match = re.search(r"const viewingArchived = ([^;]+);", source)
    assert match, "viewingArchived 被改名了,请同步更新本测试"
    expression = match.group(1)
    assert "!meeting.draft" in expression, "判据必须是 draft(草稿=正准备录新的)"
    assert "!liveRecording" in expression, "录音中不该切成归档态"
    assert "!jobRunning" in expression, "处理中不该切成归档态"


def test_minutes_nav_lights_only_while_composing(source):
    """「录音纪要」只代表「新建一场录音纪要」。

    打开一场历史会议同样停在 workspace,若照旧按 view 点亮,「录音纪要」会和下面
    选中的那条最近会议同时高亮 —— 两处指向不同的东西,反而看不出自己在哪儿。
    """
    assert 'className={composing ? "active" : ""}' in source, "录音纪要仍按 view 点亮"
    assert 'composing={view === "workspace" && !viewingArchived}' in source, (
        "composing 的算法变了,请确认它仍排除「正在看已完成的会议」"
    )
