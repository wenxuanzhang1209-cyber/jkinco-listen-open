"""语音转写：100% 本地识别（开源版）。

使用 FunASR 的 paraformer-zh（中文普通话识别）+ fsmn-vad（语音活动检测）
+ ct-punc（标点恢复），全部在本地运行，录音不上传任何服务器。
首次运行会自动从 ModelScope 下载模型（约 1-2GB），之后完全离线可用。
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

from jkinco_lexicon import correct_domain_terms
from jkinco_logging import get_logger
from jkinco_text import user_facing_error

LOGGER = get_logger("asr")

try:
    from funasr import AutoModel
except ImportError:
    AutoModel = None


ASR_MODEL = None
ASR_MODEL_LOCK = threading.Lock()

# 可选：指定本地模型目录，避免每次重建容器后重新下载。
ASR_MODEL_DIR = os.getenv("JKINCO_ASR_MODEL_DIR", "").strip() or None

# 实时流式识别模型（paraformer-zh-streaming）。与整段识别模型分开懒加载，
# 只有启用 JKINCO_REALTIME_LOCAL_ASR 时才会真正下载/占用内存。
STREAMING_MODEL = None
STREAMING_MODEL_LOCK = threading.Lock()


def get_asr_model():
    """懒加载本地语音识别模型（线程安全）。"""
    global ASR_MODEL
    if ASR_MODEL is not None:
        return ASR_MODEL

    with ASR_MODEL_LOCK:
        if ASR_MODEL is None:
            if AutoModel is None:
                raise RuntimeError(
                    "本地 FunASR 未安装。请先安装依赖：pip install -r requirements.txt"
                )
            LOGGER.info("正在加载本地语音识别模型 paraformer-zh（首次运行需下载模型）")
            kwargs = {}
            if ASR_MODEL_DIR:
                kwargs["model_dir"] = ASR_MODEL_DIR
            ASR_MODEL = AutoModel(
                model="paraformer-zh",
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                disable_update=True,
                **kwargs,
            )
            LOGGER.info("本地语音识别模型已就绪")
    return ASR_MODEL


def get_streaming_asr_model():
    """懒加载本地流式识别模型（实验性，线程安全）。"""
    global STREAMING_MODEL
    if STREAMING_MODEL is not None:
        return STREAMING_MODEL

    with STREAMING_MODEL_LOCK:
        if STREAMING_MODEL is None:
            if AutoModel is None:
                raise RuntimeError("本地 FunASR 未安装。请先安装依赖：pip install -r requirements.txt")
            LOGGER.info("正在加载本地流式识别模型 paraformer-zh-streaming（首次运行需下载模型）")
            kwargs = {}
            if ASR_MODEL_DIR:
                kwargs["model_dir"] = ASR_MODEL_DIR
            STREAMING_MODEL = AutoModel(
                model="paraformer-zh-streaming",
                vad_model="fsmn-vad",
                punc_model="ct-punc",
                disable_update=True,
                **kwargs,
            )
            LOGGER.info("本地流式识别模型已就绪")
    return STREAMING_MODEL


STREAMING_CHUNK_SIZE = [0, 10, 5]
STREAMING_ENCODER_LOOK_BACK = 4
STREAMING_DECODER_LOOK_BACK = 1


def streaming_transcribe(
    pcm_bytes: bytes,
    cache: dict | None = None,
    is_final: bool = False,
    sample_rate: int = 16000,
) -> tuple[str, dict]:
    """流式识别一段 16kHz 单声道 PCM（s16le）音频。

    返回 (识别文本, 更新后的 cache)。cache 必须在同一路会话内持续透传，
    is_final=True 时刷新最终句。实验性能力：默认关闭，由
    JKINCO_REALTIME_LOCAL_ASR=1 开启。
    """
    if not pcm_bytes:
        return "", dict(cache or {})
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return "", dict(cache or {})
    model = get_streaming_asr_model()
    state = dict(cache or {})
    result = model.generate(
        input=samples,
        cache=state,
        is_final=is_final,
        chunk_size=STREAMING_CHUNK_SIZE,
        encoder_chunk_look_back=STREAMING_ENCODER_LOOK_BACK,
        decoder_chunk_look_back=STREAMING_DECODER_LOOK_BACK,
    )
    text = "".join(item.get("text", "") for item in result if item.get("text"))
    return correct_domain_terms(text), state


# ================= 核心处理函数 =================
ASR_BATCH_SECONDS = 300

FFMPEG_TIMEOUT_SECONDS = int(os.getenv("JKINCO_FFMPEG_TIMEOUT_SECONDS", "900"))
FFPROBE_TIMEOUT_SECONDS = int(os.getenv("JKINCO_FFPROBE_TIMEOUT_SECONDS", "60"))
# 单个音频的最长可处理时长。解码后是 16kHz 单声道 pcm_s16le（32KB/秒），按时长设闸
# 才能挡住解压炸弹——文件大小挡不住，实测低码率 opus 膨胀比可达 84.9 倍。
MAX_AUDIO_DURATION_SECONDS = int(os.getenv("JKINCO_MAX_AUDIO_SECONDS", "21600"))


def normalize_audio_path(audio_file):
    """Return a filepath from Gradio's filepath or FileData dict formats."""
    if isinstance(audio_file, dict):
        return audio_file.get("path")
    return audio_file


def numpy_audio_to_wav(audio_data):
    sample_rate, data = audio_data
    if data is None:
        return None

    audio = np.asarray(data)
    if audio.size == 0:
        return None

    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    if np.issubdtype(audio.dtype, np.floating):
        audio = np.clip(audio, -1.0, 1.0)
        audio = (audio * 32767).astype(np.int16)
    elif audio.dtype != np.int16:
        if np.issubdtype(audio.dtype, np.integer):
            max_value = max(abs(np.iinfo(audio.dtype).min), np.iinfo(audio.dtype).max)
            audio = (audio.astype(np.float32) / max_value * 32767).astype(np.int16)
        else:
            audio = audio.astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.close()
    with wave.open(tmp.name, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(audio.tobytes())
    return tmp.name


def prepare_audio_path(audio_file):
    if isinstance(audio_file, tuple):
        return numpy_audio_to_wav(audio_file), True
    return normalize_audio_path(audio_file), False


def _ffprobe(*arguments) -> str:
    """跑一次 ffprobe 并返回标准输出；失败返回空串。"""
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", *arguments],
            capture_output=True, text=True, check=True, timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        return completed.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def has_audio_stream(audio_path) -> bool:
    """文件里是否存在可解析的音频流——用于「这是不是一个有效媒体文件」的判定。

    不能用「读不到时长」来判定损坏：浏览器 MediaRecorder 产出的是流式 webm，
    头部根本不写时长，ffprobe 返回 N/A，但文件完全可以正常解码。
    随机字节和只剩文件头的截断文件都探不出音频流，因此这个判据仍能挡住真正的坏文件。
    """
    return _ffprobe(
        "-select_streams", "a:0", "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
    ) == "audio"


def audio_duration_seconds(audio_path):
    """用 ffprobe 读取时长；读不到返回 None（ffprobe 缺失、格式无法解析等）。

    依次尝试三种来源，因为容器头里的时长对流式录音常常是缺失的：
      1. format=duration —— 最快，普通文件都有；
      2. stream=duration —— 部分容器只在流上写；
      3. 末个音频包的时间戳 —— 只读包不解码，专门覆盖 MediaRecorder 这类
         「头部无时长」的流式 webm。
    """
    for arguments in (
        ("-show_entries", "format=duration"),
        ("-select_streams", "a:0", "-show_entries", "stream=duration"),
    ):
        raw = _ffprobe(*arguments, "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path))
        try:
            return float(raw)
        except ValueError:
            continue

    packets = _ffprobe(
        "-select_streams", "a:0", "-show_entries", "packet=pts_time",
        "-of", "csv=p=0", str(audio_path),
    )
    for line in reversed(packets.splitlines()):
        try:
            return float(line.strip().rstrip(","))
        except ValueError:
            continue
    return None


def transcribe_audio(audio_file, batch_size_s=ASR_BATCH_SECONDS, progress_callback=None, app_mode: str = "auto"):
    """本地转写整段录音。

    全程在本机完成：ffmpeg 探测/解码 + FunASR 识别 + 领域术语纠错。
    app_mode 参数保留用于兼容旧调用点（本地词表偏置仍由 jkinco_lexicon 生效）。
    """
    audio_path, should_cleanup = prepare_audio_path(audio_file)
    if not audio_path:
        return ""

    # 时长闸门：按时长而非文件大小设闸，因为压缩率不可控。
    duration = audio_duration_seconds(audio_path)
    if duration is not None and duration > MAX_AUDIO_DURATION_SECONDS:
        if should_cleanup:
            with contextlib.suppress(OSError):
                Path(audio_path).unlink(missing_ok=True)
        raise RuntimeError(
            f"录音时长约 {duration / 3600:.1f} 小时，超过 "
            f"{MAX_AUDIO_DURATION_SECONDS / 3600:.0f} 小时上限，请分段后再上传"
        )

    try:
        model = get_asr_model()
        res = model.generate(input=audio_path, batch_size_s=batch_size_s)
        return correct_domain_terms(
            "\n".join(item.get("text", "") for item in res if item.get("text"))
        )
    finally:
        if should_cleanup and audio_path:
            try:
                Path(audio_path).unlink(missing_ok=True)
            except OSError:
                pass


LIVE_MIN_CHUNK_SECONDS = 1.4
LIVE_MAX_CHUNK_SECONDS = 8


def extract_live_audio_chunk(audio_file, live_state):
    """Extract only new microphone samples so live ASR stays responsive."""
    if not isinstance(audio_file, tuple):
        return audio_file, live_state or {}

    live_state = dict(live_state or {})
    sample_rate, data = audio_file
    if data is None:
        return None, live_state

    audio = np.asarray(data)
    if audio.size == 0:
        return None, live_state
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    total_samples = int(audio.shape[0])
    last_total = int(live_state.get("last_total", 0) or 0)
    if total_samples > last_total:
        new_audio = audio[last_total:]
    else:
        new_audio = audio
    live_state["last_total"] = total_samples

    pending_audio = live_state.get("pending_audio")
    if pending_audio is not None:
        pending_audio = np.asarray(pending_audio)
        if pending_audio.size:
            new_audio = np.concatenate([pending_audio, new_audio])

    if new_audio.size == 0:
        return None, live_state

    duration = new_audio.size / float(sample_rate)
    if duration < LIVE_MIN_CHUNK_SECONDS:
        live_state["pending_audio"] = new_audio
        return None, live_state

    max_samples = int(sample_rate * LIVE_MAX_CHUNK_SECONDS)
    if new_audio.size > max_samples:
        new_audio = new_audio[-max_samples:]

    live_state["pending_audio"] = None
    return (sample_rate, new_audio), live_state


def live_transcribe(audio_file, current_text, live_state):
    """轻量麦克风分块转写（旧版录音面板用，仍为本地识别）。"""
    if audio_file is None:
        return current_text or "", live_state or {}

    try:
        live_chunk, live_state = extract_live_audio_chunk(audio_file, live_state)
        if live_chunk is None:
            return current_text or "", live_state

        transcript = transcribe_audio(live_chunk, batch_size_s=15).strip()
        if not transcript:
            return current_text or "", live_state

        current_text = (current_text or "").strip()
        if not current_text:
            return transcript, live_state
        if transcript in current_text[-300:]:
            return current_text, live_state
        return f"{current_text}\n{transcript}", live_state
    except Exception as e:
        if current_text:
            return current_text, live_state or {}
        return f"实时转写暂不可用：{user_facing_error(e)}", live_state or {}
