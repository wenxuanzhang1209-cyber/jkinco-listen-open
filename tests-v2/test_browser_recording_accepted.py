"""浏览器录音必须被接受。

MediaRecorder 产出的是流式 webm:头部不写时长,ffprobe 的 format=duration 返回
N/A。原先的校验拿「读不出时长」当「文件已损坏」,于是每一段浏览器录音提交后都会
被拒,提示「录音文件已损坏或不是有效媒体文件」—— 而文件其实完全可以正常解码。

判据必须换成「有没有可解析的音频流」。同时时长本身也要能兜底取到:调用方拿它做
时长闸门(低码率 opus 解码后可膨胀 80 余倍),取不到就等于闸门被跳过。
"""
import shutil
import subprocess

import pytest

import jkinco_asr as asr


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="需要 ffmpeg/ffprobe",
)


def _make(path, *, live: bool, seconds: int = 3):
    """live=True 模拟 MediaRecorder:流式封装,头部不写时长。"""
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi",
        "-i", f"anullsrc=r=48000:cl=mono", "-t", str(seconds),
        "-c:a", "libopus", "-f", "webm",
    ]
    if live:
        command += ["-live", "1"]
    command += [str(path), "-y"]
    subprocess.run(command, check=True, timeout=60)
    return path


def test_streaming_webm_has_no_duration_in_the_header(tmp_path):
    """先确认前提成立,否则下面的用例是在验证一个不存在的场景。"""
    path = _make(tmp_path / "live.webm", live=True)
    raw = asr._ffprobe(
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    )
    assert raw in {"N/A", ""}, f"前提不成立:头部竟然有时长 {raw}"


def test_streaming_webm_is_recognised_as_valid(tmp_path):
    """核心:浏览器录音不能被判成损坏。"""
    path = _make(tmp_path / "live.webm", live=True)
    assert asr.has_audio_stream(path) is True


def test_normal_file_is_recognised_as_valid(tmp_path):
    path = _make(tmp_path / "normal.webm", live=False)
    assert asr.has_audio_stream(path) is True


def test_random_bytes_are_rejected(tmp_path):
    """放宽判据不能把真正的坏文件一起放进来。"""
    path = tmp_path / "garbage.webm"
    path.write_bytes(b"\x1f\x43\xb6\x75" + bytes(range(256)) * 8)
    assert asr.has_audio_stream(path) is False


def test_truncated_file_is_rejected(tmp_path):
    """只剩文件头的截断文件同样要挡住。"""
    source = _make(tmp_path / "full.webm", live=True)
    truncated = tmp_path / "truncated.webm"
    truncated.write_bytes(source.read_bytes()[:200])
    assert asr.has_audio_stream(truncated) is False


def test_missing_file_is_rejected(tmp_path):
    assert asr.has_audio_stream(tmp_path / "nope.webm") is False


def test_duration_falls_back_to_packet_timestamps(tmp_path):
    """时长要能兜底取到 —— 拿不到就等于时长闸门被整个跳过。

    闸门防的是解码炸弹:低码率 opus 解码后可膨胀 80 余倍,一个 500MB 的合法音频
    解码后约 41GB,而生产磁盘余量只有 16GB。
    """
    path = _make(tmp_path / "live.webm", live=True, seconds=3)
    duration = asr.audio_duration_seconds(path)

    assert duration is not None, "流式 webm 仍然取不到时长,闸门会被跳过"
    assert 2.0 <= duration <= 4.5, f"时长偏差过大:{duration}"


def test_duration_still_works_for_normal_files(tmp_path):
    path = _make(tmp_path / "normal.webm", live=False, seconds=3)
    duration = asr.audio_duration_seconds(path)
    assert duration is not None and 2.5 <= duration <= 3.6


def test_duration_is_none_for_a_broken_file(tmp_path):
    """坏文件仍应返回 None,不能凭空编一个时长出来。"""
    path = tmp_path / "garbage.webm"
    path.write_bytes(b"not media at all" * 50)
    assert asr.audio_duration_seconds(path) is None
