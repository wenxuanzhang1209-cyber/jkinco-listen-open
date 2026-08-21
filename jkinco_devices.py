"""录音设备发现与扫描。

从 JKincoListen.py 单体抽出。负责跨平台(macOS /Volumes、Linux /media|/run/media|/mnt、
Windows 可移动盘符)扫描外接录音设备,识别支持的音频格式并生成可选列表。
JKincoListen.py 通过 re-import 保持向后兼容。
"""
from __future__ import annotations

import os
import string
import time
from pathlib import Path

from jkinco_asr import normalize_audio_path
from jkinco_text import human_file_size


RECORDER_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".amr", ".aiff", ".aif"
}


RECORDER_MAX_FILES = int(os.getenv("JKINCO_RECORDER_MAX_FILES", "300"))


RECORDER_SCAN_DEPTH = int(os.getenv("JKINCO_RECORDER_SCAN_DEPTH", "5"))


RECORDER_SKIP_DIRS = {
    ".Spotlight-V100", ".Trashes", ".fseventsd", "System Volume Information", "$RECYCLE.BIN"
}


def recorder_scan_roots():
    env_roots = os.getenv("JKINCO_RECORDER_ROOTS")
    if env_roots:
        candidates = [Path(item).expanduser() for item in env_roots.split(os.pathsep) if item.strip()]
    else:
        candidates = [Path("/Volumes"), Path("/media"), Path("/run/media"), Path("/mnt")]
    return [path for path in candidates if path.exists()]


def windows_recorder_roots():
    if os.name != "nt":
        return []

    try:
        import ctypes
    except ImportError:
        return []

    drive_removable = 2
    drive_fixed = 3
    include_fixed = os.getenv("JKINCO_INCLUDE_FIXED_DRIVES", "").strip().lower() in {"1", "true", "yes", "on"}
    system_drive = os.getenv("SystemDrive", "C:").upper()
    roots = []

    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        bitmask = 0

    for index, letter in enumerate(string.ascii_uppercase):
        if not bitmask & (1 << index):
            continue
        root_text = f"{letter}:/"
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root_text)
        except Exception:
            continue
        if drive_type == drive_removable or (include_fixed and drive_type == drive_fixed and f"{letter}:" != system_drive):
            root = Path(root_text)
            if root.exists():
                roots.append(root)
    return roots


def mounted_child_dirs(root, max_levels=2):
    dirs = []

    def walk(current, depth):
        if depth > max_levels:
            return
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or child.name.startswith(".") or child.name in RECORDER_SKIP_DIRS:
                continue
            try:
                if child.resolve() == Path("/").resolve():
                    continue
            except OSError:
                continue
            if child.name.lower().startswith("macintosh hd"):
                continue
            dirs.append(child)
            walk(child, depth + 1)

    walk(root, 1)
    return dirs


def recorder_volume_dirs():
    env_roots = os.getenv("JKINCO_RECORDER_ROOTS")
    if env_roots:
        return [path for path in recorder_scan_roots() if path.is_dir()]

    if os.name == "nt":
        return windows_recorder_roots()

    dirs = []
    for root in recorder_scan_roots():
        if root.name == "Volumes" and root.is_dir():
            dirs.extend(mounted_child_dirs(root, max_levels=1))
        elif root.name in {"media", "mnt"} or str(root).endswith("/run/media"):
            dirs.extend(mounted_child_dirs(root, max_levels=2))
        elif root.is_dir():
            dirs.append(root)
    return dirs


def is_recorder_audio_file(path):
    return path.is_file() and path.suffix.lower() in RECORDER_AUDIO_EXTENSIONS


def scan_audio_files(base_dir, max_depth=RECORDER_SCAN_DEPTH):
    found = []

    def walk(current_dir, depth):
        if len(found) >= RECORDER_MAX_FILES or depth > max_depth:
            return
        try:
            entries = sorted(current_dir.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return

        for entry in entries:
            if len(found) >= RECORDER_MAX_FILES:
                return
            if entry.name.startswith(".") or entry.name in RECORDER_SKIP_DIRS:
                continue
            if entry.is_dir():
                walk(entry, depth + 1)
            elif is_recorder_audio_file(entry):
                found.append(entry)

    walk(base_dir, 0)
    return found


def recorder_volume_name_for_path(path):
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    for volume_dir in recorder_volume_dirs():
        try:
            resolved.relative_to(volume_dir.resolve())
            return volume_dir.name
        except (OSError, ValueError):
            continue
    if len(path.parts) > 2 and path.parts[1] == "Volumes":
        return path.parts[2]
    return path.parent.name


def recorder_file_label(path):
    try:
        modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    except OSError:
        modified = "未知时间"
    volume = recorder_volume_name_for_path(path)
    return f"{volume} / {path.name} · {modified} · {human_file_size(path)}"


def recorder_dropdown_data():
    files = []
    volumes = recorder_volume_dirs()
    for volume_dir in volumes:
        files.extend(scan_audio_files(volume_dir))
        if len(files) >= RECORDER_MAX_FILES:
            break

    unique_files = {}
    for path in files:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        unique_files[key] = path

    files = sorted(unique_files.values(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    choices = [(recorder_file_label(path), str(path)) for path in files[:RECORDER_MAX_FILES]]
    if choices:
        volume_names = "、".join(dict.fromkeys(recorder_file_label(path).split(" / ", 1)[0] for path in files[:3]))
        status = f"筑听/筑言设备已接入：{volume_names}，已发现 {len(choices)} 个录音文件。"
    elif volumes:
        volume_names = "、".join(path.name for path in volumes[:3])
        status = f"筑听/筑言设备已接入：{volume_names}，但未发现支持的录音文件。"
    else:
        status = "等待筑听/筑言设备接入。"
    return choices, status


RECORDER_UI_CACHE = {"signature": None}


RECORDER_STABILITY_CACHE = {}


AUTO_PROCESS_STABLE_SECONDS = float(os.getenv("JKINCO_AUTO_PROCESS_STABLE_SECONDS", "3"))


def audio_mtime(audio_file):
    if isinstance(audio_file, tuple):
        return time.time()
    audio_path = normalize_audio_path(audio_file)
    if not audio_path:
        return 0
    try:
        return Path(audio_path).stat().st_mtime
    except OSError:
        return 0


def choose_audio_file(upload_audio, recorded_audio):
    upload_path = normalize_audio_path(upload_audio)
    recorded_path = normalize_audio_path(recorded_audio)
    if upload_path and recorded_path:
        return recorded_audio if audio_mtime(recorded_audio) >= audio_mtime(upload_audio) else upload_audio
    return recorded_audio or upload_audio


def is_allowed_recorder_relative_path(relative_path):
    return all(not part.startswith(".") and part not in RECORDER_SKIP_DIRS for part in relative_path.parts)


def recorder_ui_signature(choices, status):
    return status, tuple((label, value) for label, value in choices)


def validate_recorder_audio_path(audio_path):
    if not audio_path:
        return None
    path = Path(audio_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not is_recorder_audio_file(resolved):
        return None

    for volume_dir in recorder_volume_dirs():
        try:
            relative_path = resolved.relative_to(volume_dir.resolve())
            if not is_allowed_recorder_relative_path(relative_path):
                continue
            return str(resolved)
        except (OSError, ValueError):
            continue
    return None


def stable_threshold_seconds() -> float:
    """录音文件判定为“稳定”所需的静默秒数。

    优先读取本模块的 AUTO_PROCESS_STABLE_SECONDS,便于运行时调参与测试覆盖;
    该常量由 JKINCO_AUTO_PROCESS_STABLE_SECONDS 环境变量初始化。
    """
    return AUTO_PROCESS_STABLE_SECONDS


def recorder_processing_key(audio_path):
    validated = validate_recorder_audio_path(audio_path)
    if not validated:
        return None, False
    path = Path(validated)
    try:
        stat = path.stat()
    except OSError:
        return None, False
    key = f"{path.resolve()}::{stat.st_size}::{stat.st_mtime_ns}"
    signature = (stat.st_size, stat.st_mtime_ns)
    now = time.monotonic()
    cached = RECORDER_STABILITY_CACHE.get(str(path))
    if not cached or cached["signature"] != signature:
        RECORDER_STABILITY_CACHE[str(path)] = {"signature": signature, "stable_since": now}
        return key, False
    # 阈值在调用时读取:运行时调参(含测试 monkeypatch)立即生效,
    # 而不是被固化成导入时的快照
    stable_seconds = stable_threshold_seconds()
    stable = (
        stat.st_size > 0
        and now - cached["stable_since"] >= stable_seconds
        and time.time() - stat.st_mtime >= stable_seconds
    )
    return key, stable
