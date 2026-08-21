from __future__ import annotations

import base64
import io
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend import core
from backend.history import history_editable_by, history_visible_to, serialize_history
from jkinco_pipeline import DEFAULT_PROCESS_MODE, PROCESS_MODES, SUMMARY_AND_PUSH
from backend.auth import (
    GUEST_ACCESS_ENABLED,
    GUEST_MAX_PER_WINDOW,
    GUEST_SESSION_TTL,
    MAX_AVATAR_BYTES,
    PROFILE_DB,
    PROFILE_DB_LOCK,
    SESSION_COOKIE,
    attach_session_cookie,
    authenticate_user,
    clear_login_failures,
    configured_users,
    hash_password,
    create_guest_account,
    init_profile_db,
    is_admin,
    is_guest,
    purge_expired_guests,
    LOGIN_IP_MAX_ATTEMPTS,
    login_blocked,
    login_throttle_key,
    make_captcha,
    read_profile,
    record_login_failure,
    request_is_https,
    set_password,
    require_user,
    user_exists,
    verify_captcha,
    verify_session,
    write_profile,
)
from backend.meetings import register_meeting_routes
from backend.custom_templates import (
    MAX_TEMPLATE_BYTES,
    create_template,
    delete_template,
    generate_minutes_with_template,
    get_template,
    init_custom_template_db,
    purge_expired_deleted_templates,
    purge_templates_for_owners,
    list_templates,
    render_custom_docx,
    template_metadata,
    update_template,
)
from jkinco_asr import audio_duration_seconds, has_audio_stream
from jkinco_export import export_filename
from jkinco_logging import get_logger
from jkinco_text import clean_speaker_name, has_meaningful_speech, user_facing_error


LOGGER = get_logger("api")

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
UPLOAD_DIR = Path(os.getenv("JKINCO_UPLOAD_DIR", "/tmp/jkinco-v2-uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = int(os.getenv("JKINCO_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
MAX_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024
MAX_DEFAULT_API_BODY_BYTES = int(os.getenv("JKINCO_MAX_API_BODY_BYTES", str(2 * 1024 * 1024)))
# 实时转写文本的字符上限。Starlette 对单个表单字段本就有 1MB 的硬上限,所以这里
# 不是「从无限收到有限」,而是把限制提前到业务层:1MB 中文仍可拆成数十次按块计费的
# 大模型调用,且框架层报错("Field exceeded maximum size")对用户毫无意义。
# 10 万字约为两小时中文会议转写(3-6 万字)的两倍余量,且远低于框架上限,确保本检查生效。
MAX_LIVE_TEXT_CHARS = int(os.getenv("JKINCO_MAX_LIVE_TEXT_CHARS", "100000"))
ALLOWED_AUDIO_SUFFIXES = {
    ".aac", ".amr", ".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".opus",
    ".wav", ".webm", ".wma",
}
# 外接录音设备读取:单机版遗留能力,公网部署下无任何消费方,默认关闭。见 device_recordings。
DEVICE_READER_ENABLED = os.getenv("JKINCO_DEVICE_READER_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}
EXECUTOR = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("JKINCO_JOB_WORKERS", "2"))))
MAX_PENDING_JOBS = max(1, int(os.getenv("JKINCO_MAX_PENDING_JOBS", "12")))
JOB_CAPACITY = threading.BoundedSemaphore(MAX_PENDING_JOBS)
# 单个账号(含访客)最多同时占用的槽位。只有全局上限时,一个人连点十几次
# 就能把 MAX_PENDING_JOBS 全部占满,其余所有用户——包括管理员——一律 429,
# 等于一个访客就能让整个平台停止处理录音。
MAX_PENDING_JOBS_PER_USER = max(1, int(os.getenv("JKINCO_MAX_PENDING_JOBS_PER_USER", "3")))
# 访客配额更紧:访客免注册,单 IP 能连开数个账号,按账号计的配额对它形同虚设。
# 压到 1 之后,即使把 IP 的开号额度用满也占不掉全局的一半,正式账号始终有余量。
MAX_PENDING_JOBS_PER_GUEST = max(1, int(os.getenv("JKINCO_MAX_PENDING_JOBS_PER_GUEST", "1")))
JOB_SLOTS_BY_USER: dict[str, int] = {}
JOB_SLOTS_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_TTL_SECONDS = int(os.getenv("JKINCO_JOB_TTL_SECONDS", str(6 * 60 * 60)))
JOB_RUNNING_TIMEOUT_SECONDS = int(os.getenv("JKINCO_JOB_RUNNING_TIMEOUT_SECONDS", str(2 * 60 * 60)))
EXPENSIVE_REQUESTS_PER_MINUTE = max(1, int(os.getenv("JKINCO_EXPENSIVE_REQUESTS_PER_MINUTE", "20")))
# 按操作区分额度。默认的 20 次/分钟是按「等大模型返回」的操作定的:分类、助手、
# 钉钉推送大部分时间花在网络等待上,占的是线程不是核。导出不一样,它是这批操作里
# 唯一 CPU 密集的 —— 实测 20 万字的纪要生成 PDF 要 5.1 秒、Word 要 2.1 秒。
# 按 20 次/分钟算,一个账号(免注册访客同样适用)光靠导出就能吃掉约 100 CPU 秒
# /分钟,而这台机器只有 2 核。8 次/分钟远超正常使用(导出是为了拿去看,不会连点),
# 最坏也就占到一个核的三分之一。
EXPENSIVE_OPERATION_LIMITS = {
    "export": max(1, int(os.getenv("JKINCO_EXPORT_REQUESTS_PER_MINUTE", "8"))),
    # 头像重编码同样是 CPU 密集的(解码 + 缩放 + WebP method=6),而这条路原先
    # 完全没有限流 —— 一个账号(含免注册访客)连续上传就能持续占着核与内存。
    # 换头像是低频操作,6 次/分钟远超正常使用。
    "avatar": max(1, int(os.getenv("JKINCO_AVATAR_REQUESTS_PER_MINUTE", "6"))),
    # 模板解析同样是 CPU 密集的:约 0.9ms/段,而 MAX_XML_BYTES 允许约五万段,
    # 最坏一次上传要跑半分钟。这条路原先没有任何限流 —— 上传模板是低频操作,
    # 6 次/分钟远超正常使用。
    "template": max(1, int(os.getenv("JKINCO_TEMPLATE_REQUESTS_PER_MINUTE", "6"))),
}
EXPENSIVE_REQUESTS: dict[str, deque[float]] = defaultdict(deque)
# 键数超过这个量才触发一次清扫,和 LOGIN_FAILURES_MAX_KEYS 同一思路
EXPENSIVE_REQUESTS_MAX_KEYS = 5000
EXPENSIVE_REQUESTS_LOCK = threading.Lock()
# 孤儿文件清理的间隔。1 小时足够及时,又不会造成可观的额外磁盘扫描。
STALE_FILE_SWEEP_INTERVAL_SECONDS = int(os.getenv("JKINCO_STALE_SWEEP_INTERVAL", "3600"))


def sweep_stale_files(max_age_seconds: int = 24 * 60 * 60) -> None:
    """进程崩溃、任务丢弃或下载中断会留下孤儿音频/导出文件,启动时清理防止磁盘被占满。"""
    cutoff = time.time() - max_age_seconds
    for directory in (UPLOAD_DIR, core.EXPORT_DIR):
        try:
            entries = list(directory.iterdir())
        except OSError as error:
            # 目录不存在或整体不可读:跳过这个目录,但不能影响另一个。
            LOGGER.warning("孤儿文件清理跳过目录 %s:%s", directory, error)
            continue
        for entry in entries:
            # try 必须在循环**内**。原先它包住整个内层循环,任何一个文件
            # stat 失败(并发删除、权限)都会让整轮清理中断,它后面的文件
            # 再也不会被检查 —— 而这是磁盘被占满的唯一防线(单个上传上限 500MB,
            # 磁盘写满会直接拖垮 SQLite 写入)。而且原先是 pass 掉不留痕,
            # 磁盘慢慢被吃满时看不到任何迹象。
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError as error:
                LOGGER.warning("清理孤儿文件失败(%s):%s", entry.name, error)


def _purge_expired_guests_and_their_templates() -> None:
    """清掉过期访客账号,连同其名下的模板。

    访客名下的模板必须一并清理:访客免注册、单个模板上限 10MB,只删账号会把
    内容永久留在库里(生产已出现过账号不存在的孤儿模板)。放在 main 这一层
    编排,是因为 custom_templates 已依赖 auth,反向导入会成环。
    """
    expired_guests = purge_expired_guests()
    if not expired_guests:
        return
    LOGGER.info("已清理过期访客账号 %d 个", len(expired_guests))
    try:
        removed = purge_templates_for_owners(set(expired_guests))
        if removed:
            LOGGER.info("已清理过期访客的自定义模板 %d 个", removed)
    except Exception as error:
        LOGGER.warning("清理访客模板失败:%s", error)


def _stale_file_sweeper() -> None:
    """周期性清理孤儿文件。

    原先只在模块导入时扫一次 —— 生产容器一跑就是数周,期间进程被杀、下载中断、
    任务异常留下的音频与导出文件再也不会被清理。单个上传上限 500MB,攒几个就能
    把磁盘吃掉一大块,而磁盘写满会直接拖垮 SQLite 写入。
    """
    while True:
        time.sleep(STALE_FILE_SWEEP_INTERVAL_SECONDS)
        try:
            sweep_stale_files()
        except Exception as error:  # 清理失败不能让线程退出,否则后面永远不再清理
            LOGGER.warning("孤儿文件清理失败:%s", error)
        try:
            # 软删除的模板过了保留期就释放内容:delete_template 只置 deleted_at,
            # 内容 BLOB 会一直占着空间(单个上限 10MB)。
            released = purge_expired_deleted_templates()
            if released:
                LOGGER.info("已释放过期软删除模板的内容 %d 个", released)
        except Exception as error:
            LOGGER.warning("软删除模板清理失败:%s", error)
        try:
            # 访客清理原先只挂在访客登录接口上,名义上「定期」实则依赖有人再来
            # 登录一次:访客通道一旦没人用,过期账号与其历史文件就永远留在库里。
            # 挂到这条线程上才真的是周期性的,顺便也把这段清理从登录路径挪走。
            _purge_expired_guests_and_their_templates()
        except Exception as error:
            LOGGER.warning("过期访客清理失败:%s", error)


init_profile_db()
init_custom_template_db()
sweep_stale_files()
threading.Thread(target=_stale_file_sweeper, name="stale-file-sweeper", daemon=True).start()


def set_job(job_id: str, **updates: Any) -> None:
    now = time.time()
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {"created_at": now})
        job.update(updates)
        job["updated_at"] = now


def job_slot_precheck(username: str) -> str:
    """不预留槽位的廉价预检,失败返回拒绝原因。

    用来在昂贵步骤之前拒掉注定失败的提交:acquire_job_slot 排在「把上传写进磁盘
    (最多 500MB)+ 跑一次 ffprobe」之后,槽位已满的账号每次被拒都要白付那些代价。

    刻意不预留:预留了再在后续校验失败时忘记归还,就会漏掉一个槽位 —— 那比多写
    一次磁盘糟得多。所以这里只读不占,权威判定仍是 acquire_job_slot。
    读到的计数可能略旧,但只会往「放行」方向偏,那一侧由权威判定兜住。
    """
    limit = MAX_PENDING_JOBS_PER_GUEST if is_guest(username) else MAX_PENDING_JOBS_PER_USER
    with JOB_SLOTS_LOCK:
        if JOB_SLOTS_BY_USER.get(username, 0) >= limit:
            return "您有录音正在处理，请等待完成后再提交"
    return ""


def acquire_job_slot(username: str) -> str:
    """占一个处理槽位,成功返回空串,失败返回给用户看的拒绝原因。

    两级配额都要拿到:全局一级保护整机不过载,按用户一级保证任何单个账号都
    挤不掉别人。顺序上先查用户配额再拿全局信号量,这样被用户配额挡下时不会
    白占一个全局槽位。释放走 release_job_slot,两级必须一起还。
    """
    limit = MAX_PENDING_JOBS_PER_GUEST if is_guest(username) else MAX_PENDING_JOBS_PER_USER
    with JOB_SLOTS_LOCK:
        if JOB_SLOTS_BY_USER.get(username, 0) >= limit:
            return "您有录音正在处理，请等待完成后再提交"
        if not JOB_CAPACITY.acquire(blocking=False):
            return "处理任务较多，请稍后重试"
        JOB_SLOTS_BY_USER[username] = JOB_SLOTS_BY_USER.get(username, 0) + 1
    return ""


def release_job_slot(username: str) -> None:
    with JOB_SLOTS_LOCK:
        remaining = JOB_SLOTS_BY_USER.get(username, 0) - 1
        if remaining > 0:
            JOB_SLOTS_BY_USER[username] = remaining
        else:
            # 归零就删键:访客用户名各不相同,留着会随进程运行时间无限增长。
            JOB_SLOTS_BY_USER.pop(username, None)
        try:
            JOB_CAPACITY.release()
        except ValueError:
            # BoundedSemaphore 对多还的一次会抛错。宁可少还一次也不能让异常
            # 冒出 finally 块,盖掉任务本身真正的失败原因。
            pass


def cleanup_jobs(now: float | None = None) -> None:
    current = now or time.time()
    with JOBS_LOCK:
        for job in JOBS.values():
            if job.get("status") in {"queued", "running"} and current - job.get("updated_at", current) > JOB_RUNNING_TIMEOUT_SECONDS:
                job.update(status="failed", message="任务处理超时，请重新提交", error="任务处理超时", updated_at=current)
        expired = [job_id for job_id, job in JOBS.items() if current - job.get("updated_at", current) > JOB_TTL_SECONDS]
        for job_id in expired:
            JOBS.pop(job_id, None)


def enforce_expensive_rate_limit(username: str, operation: str) -> None:
    """Bound synchronous model/export calls per user so one account cannot exhaust workers or API quota."""
    now = time.monotonic()
    key = f"{username}:{operation}"
    with EXPENSIVE_REQUESTS_LOCK:
        timestamps = EXPENSIVE_REQUESTS[key]
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) >= EXPENSIVE_OPERATION_LIMITS.get(operation, EXPENSIVE_REQUESTS_PER_MINUTE):
            raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
        timestamps.append(now)
        # 顺手清掉其它已经空掉的键。过期时间戳会被弹出,但键本身原先永不删除 ——
        # 访客用户名各不相同,每来一个访客就多留一个键,进程跑得越久占用越大。
        # 只在键数超过阈值时才做一次全量清扫,平时不付出这个开销。
        if len(EXPENSIVE_REQUESTS) > EXPENSIVE_REQUESTS_MAX_KEYS:
            for stale in [k for k, v in EXPENSIVE_REQUESTS.items() if not v and k != key]:
                EXPENSIVE_REQUESTS.pop(stale, None)


def request_origin_matches_host(request: Request) -> bool:
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme
    return parsed.scheme == scheme and parsed.netloc.lower() == request.headers.get("host", "").lower()


def request_body_limit(path: str) -> int:
    if path == "/api/process":
        return MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    if path == "/api/custom-templates":
        return MAX_TEMPLATE_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    if path == "/api/profile":
        return MAX_AVATAR_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    return MAX_DEFAULT_API_BODY_BYTES


# 前端传来的输入方式 -> 历史记录里展示的来源
INPUT_MODE_SOURCES = {"live": "实时录音", "upload": "上传音频", "device": "筑听读取"}


def template_supports_scene(template: dict[str, Any], scene: str) -> bool:
    """自定义模板只能用于声明的场景；auto 模板是跨场景模板。

    智能识别时尚未知道最终场景，因此只允许显式 auto 模板。否则一个工程模板
    会把已正确识别为 general 的结果重新排成工程纪要，界面上看起来仍像误判。
    """
    template_scene = str(template.get("scenario") or "general").strip()
    return template_scene == "auto" or (scene != "auto" and template_scene == scene)


def run_processing_job(job_id: str, audio_path: str | None, live_text: str, process_mode: str, app_mode: str, username: str, custom_template_id: str = "", input_mode: str = "") -> None:
    try:
        set_job(job_id, status="running", stage="asr", progress=10, message="正在进行高精度语音转写")

        def asr_progress(done: int, total: int) -> None:
            ratio = done / max(total, 1)
            set_job(job_id, progress=10 + int(38 * ratio), message=f"语音转写中（{done}/{total} 段完成，并行识别）")

        # 把用户选的场景传给识别:手动场景使用专用词表；auto 必须使用跨场景
        # 中性词表。分类前单边灌入工程热词会形成「偏出工程词 -> 判成工程」闭环。
        transcript = (
            core.transcribe_audio(audio_path, progress_callback=asr_progress, app_mode=app_mode)
            if audio_path else live_text.strip()
        )
        # 归一后再判:原先第一个条件是 `not transcript`,顺带挡住了 None;
        # 拆开之后 .startswith 会先执行,不归一就会在 None 上抛 AttributeError。
        transcript = str(transcript or "")
        if transcript.startswith("❌") or transcript.startswith("请先"):
            raise RuntimeError(transcript)
        if not has_meaningful_speech(transcript):
            # 判据原为 `not transcript`,只挡完全空串。没人说话时 ASR 往往不返回空,
            # 而是给出一两个语气词或一个句号 —— 那种转写会照常走完生成流程,模型
            # 拿着近乎空的素材按模板硬造出一整套章节标题,看起来像一份真纪要。
            raise RuntimeError("未检测到有效语音内容，请确认录音中有说话声后重试")
        set_job(job_id, stage="model", progress=52, message="正在识别会议场景")
        effective_mode, reason = core.infer_app_mode_best_effort(transcript, app_mode)
        set_job(job_id, progress=58, message=f"已识别为「{core.mode_label(effective_mode)}」，正在生成结构化报告")
        # include_deleted:提交时(/api/process)已经校验过模板存在,所以到这里还查不到
        # 只有一种可能 —— 用户在**转写进行当中**把它删了(软删)。而转写此刻已经跑完,
        # 因为这个抛异常会连同转写一起丢掉,那是不可挽回的:实时录音无法重录。
        # 导出那条路径早就是 include_deleted=True,同一个判断这里漏了。
        #
        # 仍然会对伪造的 id 报错 —— 查不到就静默忽略的话,用户会拿到一份没套模板的
        # 纪要,而界面上显示的是他选的那个模板。
        custom_template = get_template(username, custom_template_id, include_deleted=True) if custom_template_id else None
        if custom_template_id and not custom_template:
            raise RuntimeError("所选自定义模板不存在或无权使用")
        if custom_template and not template_supports_scene(custom_template, effective_mode):
            # 提交接口已经拦截新任务；这里是并发删除/旧队列任务的纵深防御。
            # 不因模板错配丢掉已经完成的长录音转写，安全降级为系统场景模板。
            LOGGER.warning(
                "忽略场景不匹配的自定义模板:template=%s template_scene=%s effective_scene=%s",
                custom_template_id,
                custom_template.get("scenario"),
                effective_mode,
            )
            custom_template = None
            custom_template_id = ""
        # 转写到这里已经完成 —— 一小时录音要跑几十秒、六小时要几分钟,而音频文件
        # 在 finally 里就会被删除。所以后续任何一步失败,都不能让转写结果一起丢掉:
        # 实时录音根本无法重录,让用户「再传一次」是不成立的。
        # 失败时保留转写落库,并在状态里说清楚,用户可以从历史会议重新生成纪要。
        minutes_error = ""
        summary = ""
        overview = ""
        try:
            summary = core.generate_minutes(transcript, effective_mode) if core.should_generate_and_push(process_mode) else ""
            if custom_template and summary:
                set_job(job_id, progress=72, message=f"正在按「{custom_template['name']}」重排纪要")
                summary = generate_minutes_with_template(summary, custom_template)
            set_job(job_id, stage="review", progress=84, message="正在生成会议概览")
            overview = core.generate_meeting_overview(summary, transcript, effective_mode) if summary else ""
        except Exception as error:
            minutes_error = str(error)
            LOGGER.warning("纪要生成失败,已保留转写:%s", minutes_error)

        # 分类依据(reason)只随任务结果返回,不写入对用户展示的状态文案
        if minutes_error:
            status = f"转写已完成并保存；纪要生成失败（{minutes_error[:120]}），可在历史会议中重新生成。"
        elif core.should_push_to_dingtalk(process_mode) and summary:
            status = core.send_to_dingtalk(summary, effective_mode)
        elif summary:
            status = "报告已生成，等待人工校核；当前未推送钉钉。"
        else:
            status = "已完成转写；当前处理模式为“只转写，不推送”。"
        record_id = core.save_meeting_history_record(
            transcript,
            summary,
            status or reason,
            effective_mode,
            # 优先用前端明确告知的来源。回退到旧的启发式只为兼容不带该字段的老客户端 ——
            # 那个启发式两个方向都会出错(见 input_mode 的前端注释)。
            INPUT_MODE_SOURCES.get(input_mode) or ("实时录音" if live_text else "上传音频"),
            overview,
            owner_username=username,
            classification={
                "requested_mode": app_mode,
                "predicted_mode": effective_mode,
                "final_mode": effective_mode,
                "source": "auto" if app_mode == "auto" else "manual",
                "reason": reason,
                "version": "engineering-evidence-v2",
                "created_at": time.time(),
            },
            custom_template_id=custom_template_id,
            custom_template_name=custom_template["name"] if custom_template else "",
        )
        result = {
            "record_id": record_id,
            "mode": effective_mode,
            "mode_label": core.mode_label(effective_mode),
            "reason": reason,
            "transcript": transcript,
            "summary": summary,
            "overview": overview,
            "status": status,
            "custom_template_id": custom_template_id,
            "custom_template_name": custom_template["name"] if custom_template else "",
        }
        set_job(job_id, status="completed", stage="push", progress=100, message="报告已生成", result=result)
    except Exception as error:
        # 这条兜底原先既不记日志、也不处理异常文本。转写阶段失败的任务因此在日志里
        # 没有任何痕迹(内层的纪要失败记了,外层没有),而主功能恰恰失败在这一层;
        # 同时 message 会被前端轮询后直接显示,未脱敏的 OSError 会带出服务器绝对路径。
        LOGGER.warning("录音处理任务失败:%s", error)
        detail = user_facing_error(error) or "处理失败，请稍后重试"
        set_job(job_id, status="failed", message=detail, error=detail)
    finally:
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)
        release_job_slot(username)


class LoginPayload(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class RegisterPayload(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=8, max_length=128)
    captcha_token: str = Field(max_length=512)
    captcha_answer: str = Field(max_length=16)


class PasswordChangePayload(BaseModel):
    # 长度与注册保持一致:两处规则若不同,用户会遇到「能注册的密码改不了」
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AssistantPayload(BaseModel):
    question: str = Field(max_length=4000)
    record_id: str | None = Field(default=None, max_length=128)
    overview: str = Field(default="", max_length=200000)
    summary: str = Field(default="", max_length=200000)
    transcript: str = Field(default="", max_length=500000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=40)


class ReviewPayload(BaseModel):
    summary: str = Field(max_length=200000)
    overview: str = Field(default="", max_length=200000)
    mode: str = Field(default="auto", max_length=32)
    custom_template_id: str = Field(default="", max_length=64)
    title: str = Field(default="", max_length=200)
    mode_label: str = Field(default="", max_length=80)
    transcript: str = Field(default="", max_length=500000)
    date: str = Field(default="", max_length=40)
    start_time: str = Field(default="", max_length=40)
    end_time: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=200)
    participants: str = Field(default="", max_length=4000)
    host: str = Field(default="", max_length=200)
    recorder: str = Field(default="", max_length=200)
    client: str = Field(default="", max_length=200)
    project: str = Field(default="", max_length=200)


class TemplateUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    scenario: str | None = Field(default=None, max_length=32)
    is_default: bool | None = None
    insertion_strategy: str | None = Field(default=None, max_length=16)
    insertion_target: str | None = Field(default=None, max_length=240)


class ClassifyPayload(BaseModel):
    transcript: str = Field(max_length=500000)
    mode: str = Field(default="auto", max_length=32)


# 内容安全策略。前端无 dangerouslySetInnerHTML,React 默认转义,但会议转写与纪要
# 全是用户/模型产出的文本,CSP 作为纵深防御:即使将来某处误用 HTML 渲染,注入的
# 脚本也无法执行或外传数据。
# 各项依据(依实际构建产物与运行时确定,不照抄模板):
#   script-src 'self' —— Vite 产物是外链 js,index.html 无内联脚本;
#   style-src 允许 'unsafe-inline' —— 组件用了内联 style 属性,去掉会破坏布局;
#   img-src/media-src 允许 data: 与 blob: —— 头像是 data URI,LiveKit 轨道是 blob;
#   connect-src 允许 wss: —— 实时会议信令与 ASR 都走 WebSocket,地址随部署域名变化;
#   frame-ancestors 'none' —— 与 X-Frame-Options 一致,禁止被嵌套点击劫持。
CONTENT_SECURITY_POLICY = os.getenv("JKINCO_CSP", "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' ws: wss:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
]))


app = FastAPI(title="筑听平台 API", version="3.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    content_length = request.headers.get("content-length", "").strip()
    if request.method not in {"GET", "HEAD", "OPTIONS"} and content_length:
        try:
            if int(content_length) > request_body_limit(request.url.path):
                return JSONResponse({"detail": "请求内容过大"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.url.path.startswith("/api/")
        and not request_origin_matches_host(request)
    ):
        return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        # Vite 产物文件名带内容哈希,可长期缓存
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(self), display-capture=(self)"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if request_is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "jkinco-listen-open",
        "version": "3.0.0-open.1",
        "realtime_asr": "disabled",
        "meeting_media": "livekit" if os.getenv("LIVEKIT_API_KEY") else "disabled",
    }


@app.post("/api/auth/login")
def login(payload: LoginPayload, request: Request):
    throttle_key = login_throttle_key(request, payload.username.lower())
    # 两道闸门都必须在 authenticate_user 之前:那一步无论账号是否存在都要跑满
    # 一次 PBKDF2,正是要限制的开销本身,放在后面等于白付。
    ip_key = login_throttle_key(request, "__login_ip__")
    if login_blocked(throttle_key) or login_blocked(ip_key, LOGIN_IP_MAX_ATTEMPTS):
        raise HTTPException(status_code=429, detail="尝试次数过多，请一分钟后再试")
    canonical_username = authenticate_user(payload.username.strip(), payload.password)
    if not canonical_username:
        record_login_failure(throttle_key)
        # 按 IP 的计数与用户名无关,轮换用户名也躲不开。
        record_login_failure(ip_key)
        raise HTTPException(status_code=401, detail="账号或密码错误")
    clear_login_failures(throttle_key)
    response = JSONResponse({"user": read_profile(canonical_username)})
    attach_session_cookie(response, request, canonical_username)
    return response


@app.get("/api/auth/captcha")
def captcha():
    return make_captcha()


@app.post("/api/auth/register")
def register(payload: RegisterPayload, request: Request):
    username = payload.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise HTTPException(status_code=400, detail="用户名仅支持3-32位大小写字母、数字和下划线")
    register_key = login_throttle_key(request, "__register__")
    if login_blocked(register_key):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")
    record_login_failure(register_key)
    if not verify_captcha(payload.captcha_token, payload.captcha_answer):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    if user_exists(username):
        raise HTTPException(status_code=409, detail="该用户名已存在")
    salt = secrets.token_bytes(16)
    try:
        with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
            connection.execute(
                "INSERT INTO platform_users(username,password_hash,password_salt,created_at) VALUES (?,?,?,?)",
                (username, hash_password(payload.password, salt), salt.hex(), time.time()),
            )
            connection.execute(
                "INSERT OR REPLACE INTO user_profiles(username,display_name,avatar_data,updated_at) VALUES (?,?,\'\',?)",
                (username, clean_speaker_name(payload.display_name, fallback=username), time.time()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="该用户名已存在")
    response = JSONResponse({"user": read_profile(username)}, status_code=201)
    attach_session_cookie(response, request, username)
    return response


@app.post("/api/auth/guest")
def guest_access(request: Request):
    """免注册临时进入。

    访客是库内 role='guest' 的真实账号,不是绕开鉴权的旁路 —— 会议、历史、任务
    的归属隔离全部按 owner_username 判定,因此访客自动只能看见自己的数据,
    不需要在每个接口另加访客分支(那样迟早会漏掉一个)。

    与正式账号的差别:
      - 会话有效期显著更短(默认 4 小时 vs 7 天),且口令是不可复现的随机值,
        Cookie 一过期账号即无法再进入;
      - 不能推送钉钉(见 push_dingtalk):公司群不该被免注册用户触达;
      - 永远不是管理员(role != 'admin');
      - 过期账号与其历史记录由 purge_expired_guests 定期清理,避免用户表无限膨胀。

    限流复用登录失败计数器:免注册入口天然是刷号入口。
    """
    if not GUEST_ACCESS_ENABLED:
        raise HTTPException(status_code=403, detail="访客访问未开放")
    guest_key = login_throttle_key(request, "__guest__")
    # 显式传访客自己的上限:不传的话走的是登录失败计数的默认值,两个旋钮会耦合
    if login_blocked(guest_key, GUEST_MAX_PER_WINDOW):
        raise HTTPException(status_code=429, detail="访客进入过于频繁，请稍后再试")
    record_login_failure(guest_key)

    # 清理也留在这里:新访客进来正是账号表增长的时刻,顺手回收一次最及时;
    # 真正的周期性由 _stale_file_sweeper 保证,不再依赖有人来登录。
    try:
        _purge_expired_guests_and_their_templates()
    except Exception as error:
        LOGGER.warning("过期访客清理失败:%s", error)
    username = create_guest_account()
    response = JSONResponse({"user": read_profile(username)})
    attach_session_cookie(response, request, username, ttl_seconds=GUEST_SESSION_TTL)
    return response


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/me")
def me(request: Request):
    username = require_user(request)
    return read_profile(username)


@app.post("/api/auth/password")
def change_password(payload: PasswordChangePayload, request: Request):
    """修改本人口令。

    必须校验旧口令,即使调用方已经登录:会话可能是从别人的电脑上遗留下来的,
    只凭登录态就允许改密,等于把「拿到一次会话」升级成「永久接管账号」。

    改密成功后所有既有会话失效(见 password_fingerprint),包括发起本次请求的
    这台设备 —— 所以这里立刻补发一张新令牌,当前设备不必重新登录,其余设备必须。
    """
    username = require_user(request)

    # 访客口令是注册时生成的不可复现随机值,本人并不知道,改密对他们没有意义
    if is_guest(username):
        raise HTTPException(status_code=403, detail="访客账号无法修改密码，请注册正式账号")
    # JKINCO_AUTH 配置的运营账号不在库里,口令由环境变量决定,改库无效
    if username in configured_users():
        raise HTTPException(status_code=403, detail="该账号的密码由服务端配置管理，请联系管理员")

    # 与登录共用限流:改密要验旧口令,不限流就是一个可以爆破口令的旁路入口
    throttle_key = login_throttle_key(request, f"__password__{username}")
    if login_blocked(throttle_key):
        raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")

    if authenticate_user(username, payload.current_password) is None:
        record_login_failure(throttle_key)
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    if not set_password(username, payload.new_password):
        raise HTTPException(status_code=404, detail="账号不存在")

    clear_login_failures(throttle_key)
    response = JSONResponse({"ok": True, "message": "密码已修改，其他设备需要重新登录"})
    attach_session_cookie(response, request, username)
    return response


@app.get("/api/profile")
def profile(request: Request):
    return read_profile(require_user(request))


# 头像最终存成不超过这个边长的 WebP。界面上头像最大显示 96px,存原图没有意义。
AVATAR_MAX_EDGE = 256
AVATAR_QUALITY = 82
# 允许解码的最大像素数。取 5000 万:覆盖了当下手机与单反的最高分辨率(48MP 的
# 手机直出照片也在其内),同时把最坏内存代价从约 650MB 压到约三分之一。
MAX_AVATAR_PIXELS = max(1_000_000, int(os.getenv("JKINCO_MAX_AVATAR_PIXELS", str(50_000_000))))
AVATAR_MEDIA_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _build_avatar_data_uri(content: bytes, media_type: str) -> str:
    """校验并把头像压成小尺寸 WebP 的 data URI。

    原先是「校验完就把上传的原始字节直接 base64 存库」,而 /api/auth/me 每次
    打开页面都会把它原样返回 —— 实测单个响应 1.6MB,是全平台流量第一名,
    手机上就是打开变慢的直接原因。这里改成落库前先缩放重编码,通常能压到
    十几 KB。

    整个函数都是 CPU 密集的同步调用(解码 + 缩放 + 编码),必须放在线程池里跑,
    否则会卡住事件循环,期间所有并发请求(会议心跳、转写轮询)全部停摆。
    """
    try:
        with Image.open(io.BytesIO(content)) as probe:
            # 尺寸在解码之前判。Image.open 只读文件头,此时还没有位图 —— 这是唯一
            # 能在付出内存代价之前拦住的位置。
            #
            # 此前完全依赖 PIL 自带的 89 兆像素阈值,而那道线远高于头像的需要:
            # 实测一张 75KB、80 兆像素的 PNG(刚好压在阈值下方)会让进程多吃约
            # 650MB、耗时约 0.5 秒,而这个接口没有任何限流。上传上限 2MB 挡不住 ——
            # 纯色 PNG 的压缩比足够高。最终存的是 256×256,任何超过几百万像素的
            # 输入都是纯浪费。
            width, height = probe.size
            if width * height > MAX_AVATAR_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail=f"头像分辨率过高（{width}×{height}），请压缩后再上传",
                )
            probe.verify()
            image_format = (probe.format or "").upper()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="头像文件已损坏或不是有效图片")
    # 声明的类型必须和实际内容一致,否则等于允许上传任意伪装成图片的文件
    if image_format != AVATAR_MEDIA_FORMATS[media_type]:
        raise HTTPException(status_code=400, detail="头像文件内容与格式不一致")
    # verify() 会让原对象失效,重编码必须重新打开
    try:
        with Image.open(io.BytesIO(content)) as image:
            # 统一转 RGBA 再存 WebP:PNG 的透明通道得以保留,JPEG 也不会因缺少
            # 通道信息而报错
            resized = image.convert("RGBA")
            resized.thumbnail((AVATAR_MAX_EDGE, AVATAR_MAX_EDGE), Image.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="WEBP", quality=AVATAR_QUALITY, method=6)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="头像文件无法处理，请更换图片重试")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


@app.put("/api/profile")
async def update_profile(
    request: Request,
    display_name: str = Form(default=""),
    avatar: UploadFile | None = File(default=None),
    remove_avatar: str = Form(default="0"),
):
    username = require_user(request)
    # 原先只压空白。空白压平已经挡住了换行注入,但 \x00 与双向覆写符不是 \s ——
    # 它们会一路进到参会名单、聊天署名和转写署名里。三个写入点(注册、改资料、
    # 加入会议)统一走同一个收敛函数。
    name = clean_speaker_name(display_name)
    if not 1 <= len(name) <= 30:
        raise HTTPException(status_code=400, detail="显示名称需为1-30个字符")
    avatar_data: str | None = "" if remove_avatar == "1" else None
    if avatar:
        # 只在真的要处理图片时计入额度 —— 单改显示名不该被这条限制挡住
        enforce_expensive_rate_limit(username, "avatar")
        media_type = (avatar.content_type or "").lower()
        if media_type not in AVATAR_MEDIA_FORMATS:
            raise HTTPException(status_code=400, detail="头像仅支持 JPG、PNG 或 WebP")
        content = await avatar.read(MAX_AVATAR_BYTES + 1)
        if len(content) > MAX_AVATAR_BYTES:
            raise HTTPException(status_code=413, detail="头像文件不能超过2MB")
        avatar_data = await run_in_threadpool(_build_avatar_data_uri, content, media_type)
    return write_profile(username, name, avatar_data)


@app.get("/api/history")
def history(request: Request, q: str = ""):
    username = require_user(request)
    admin = is_admin(username)
    query = q.strip().lower()
    if not query:
        # 没有关键词就不碰搜索全文:构建它的代价与解析整个历史文件相当,而打开
        # 工作台时的这次不带 q 的请求才是最常见的调用。
        visible = [item for item in core.iter_meeting_history() if history_visible_to(item, username, admin)]
    else:
        # 记录与全文必须成对取出,否则中途落盘会让两者来自不同版本而错位。
        # 在截断前的完整记录上搜索,保证正文/待办也能命中。
        items, blobs = core.load_history_with_search()
        visible = [
            item for item, blob in zip(items, blobs)
            if history_visible_to(item, username, admin) and query in blob
        ]
    return {"items": [serialize_history(item, compact=True, viewer=username, admin=admin) for item in visible[:100]]}


@app.get("/api/history/{record_id}")
def history_detail(record_id: str, request: Request):
    username = require_user(request)
    admin = is_admin(username)
    for item in core.iter_meeting_history():
        if item.get("id") == record_id and history_visible_to(item, username, admin):
            return serialize_history(item, viewer=username, admin=admin)
    # 热表只保留最近 HISTORY_MAX_ITEMS 条,更早的被移进归档文件。会议表里存着
    # history_record_id,不回落的话,老会议的「查看纪要」会在某天突然变成 404 ——
    # 数据其实还在盘上。归档只读:那条记录已不在热表,任何写回都会把它再弄丢一次。
    archived = core.find_archived_record(record_id)
    if archived and history_visible_to(archived, username, admin):
        payload = serialize_history(archived, viewer=username, admin=admin)
        payload["read_only"] = True
        payload["archived"] = True
        return payload
    raise HTTPException(status_code=404, detail="会议不存在")


@app.put("/api/history/{record_id}/review")
def save_review(record_id: str, payload: ReviewPayload, request: Request):
    username = require_user(request)
    # 锁外这次只做权限预检,不改任何东西 —— 真正的读-改-写在下面的
    # HISTORY_LOCK 里用 load_meeting_history_for_update() 另取一份。
    items = core.iter_meeting_history()
    # 校核是写操作,口径比列表可见性窄:参会成员能看这条记录,但不能覆盖定稿。
    # 对只读成员返回 403 而非 404 —— 他们已经通过列表确认过这条记录存在,
    # 再假装不存在只会让人反复重试。
    target = next((item for item in items if item.get("id") == record_id), None)
    if not target or not history_visible_to(target, username):
        raise HTTPException(status_code=404, detail="会议不存在")
    if not history_editable_by(target, username):
        raise HTTPException(status_code=403, detail="只有会议发起人可以保存校核稿")
    try:
        reviewed_mode = core.canonical_mode(payload.mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # 已完成记录应有确定场景。旧客户端仍可能回传 auto，此时保留原判定，
    # 避免一次只改纪要文字的保存把场景反而抹回“智能识别”。
    if reviewed_mode == "auto":
        try:
            reviewed_mode = core.canonical_mode(target.get("mode"))
        except ValueError:
            reviewed_mode = "general"
        if reviewed_mode == "auto":
            reviewed_mode = "general"
    reviewed_template_id = str(target.get("custom_template_id") or "")
    reviewed_template = (
        get_template(username, reviewed_template_id, include_deleted=True)
        if reviewed_template_id else None
    )
    summary = payload.summary.strip()
    overview = core.generate_meeting_overview(summary, target.get("transcript", ""), reviewed_mode)
    with core.HISTORY_LOCK:
        items = core.load_meeting_history_for_update()
        target = next((item for item in items if item.get("id") == record_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="会议不存在")
        previous_mode = str(target.get("mode") or "auto")
        target["summary"] = summary
        target["overview"] = overview
        target["mode"] = reviewed_mode
        target["mode_label"] = core.mode_label(reviewed_mode)
        classification = dict(target.get("classification") or {})
        original_mode = str(classification.setdefault(
            "original_mode",
            classification.get("predicted_mode") or previous_mode,
        ))
        reviewed_at = time.time()
        correction_events = list(classification.get("correction_events") or [])[-99:]
        correction_events.append({
            "from_mode": previous_mode,
            "to_mode": reviewed_mode,
            "reviewed_by": username,
            "reviewed_at": reviewed_at,
        })
        classification.update({
            "final_mode": reviewed_mode,
            "reviewed": True,
            "reviewed_mode": reviewed_mode,
            "corrected": original_mode != reviewed_mode,
            "reviewed_by": username,
            "reviewed_at": reviewed_at,
            "correction_events": correction_events,
        })
        target["classification"] = classification
        if (
            previous_mode != reviewed_mode
            and reviewed_template_id
            and (not reviewed_template or not template_supports_scene(reviewed_template, reviewed_mode))
        ):
            # 人工纠正场景后不能继续拿旧场景模板导出，否则标签虽然改对了，
            # 成品仍长得像原来的误判。跨场景(auto)模板可以保留。
            target.pop("custom_template_id", None)
            target.pop("custom_template_name", None)
        target["dingtalk_status"] = "人工校核稿已保存"
        core.write_meeting_history(items)
    return serialize_history(target, viewer=username)


@app.get("/api/device/recordings")
def device_recordings(request: Request):
    """外接录音设备的文件清单。默认关闭 —— 这是单机版遗留能力。

    实测该接口原先把服务器绝对路径、目录结构与文件名回显给任意登录用户,而文件名
    常带业务信息(如「张总_并购谈判」);录音属于谁、谁能看,没有任何归属校验。

    确认过的调用情况(据此决定默认关闭,而非仅仅脱敏):
      - Web 前端不调用它(「筑听读取」页只展示静态说明);
      - 桌面端 Gradio 单体在进程内直调 recorder_dropdown_data(),不走 HTTP;
      - 仓库内无任何脚本调用。
    即公网部署下它没有消费方,只是攻击面。单机自用场景可置
    JKINCO_DEVICE_READER_ENABLED=1 开启。

    开启时也不再返回绝对路径:没有任何接口接受客户端传入的路径,回显它毫无用处。
    """
    require_user(request)
    if not DEVICE_READER_ENABLED:
        return {"items": [], "status": "设备读取未启用"}
    choices, _status = core.recorder_dropdown_data()
    return {
        "items": [{"label": label} for label, _path in choices],
        "status": _status,
    }


@app.post("/api/process")
async def process_audio(
    request: Request,
    audio: UploadFile | None = File(default=None),
    live_text: str = Form(default=""),
    input_mode: str = Form(default=""),
    process_mode: str = Form(default=DEFAULT_PROCESS_MODE),
    app_mode: str = Form(default="auto"),
    custom_template_id: str = Form(default=""),
):
    username = require_user(request)
    # 槽位预检放在最前面:下面要把上传写进磁盘(最多 500MB)再跑一次 ffprobe,
    # 而权威的 acquire_job_slot 排在那之后 —— 槽位已满的账号每次被拒都要白付
    # 那些代价,且被拒的请求不占槽位,可以一直重试。预检不预留槽位,见其说明。
    rejection = job_slot_precheck(username)
    if rejection:
        raise HTTPException(status_code=429, detail=rejection)
    # 处理模式必须是已知取值。未知值原先会静默落到「生成纪要但不推送」分支
    # (判定是 != TRANSCRIBE_ONLY),前端改文案或调用方拼错都不会报错,只会行为悄悄变样。
    if process_mode not in PROCESS_MODES:
        raise HTTPException(status_code=400, detail="不支持的处理模式")
    try:
        app_mode = core.canonical_mode(app_mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    selected_template = get_template(username, custom_template_id) if custom_template_id else None
    if custom_template_id and not selected_template:
        raise HTTPException(status_code=404, detail="自定义模板不存在")
    if selected_template and not template_supports_scene(selected_template, app_mode):
        if app_mode == "auto":
            detail = "智能识别仅可使用跨场景模板；请改用系统自动匹配，或先手动选择会议场景"
        else:
            detail = "所选自定义模板与当前会议场景不匹配"
        raise HTTPException(status_code=400, detail=detail)
    # 处理任务内部也会推钉钉(见 run_processing_job),所以对访客的限制必须在这里
    # 再拦一道 —— 只在 /api/dingtalk/push 上拦会被这条链路绕过。
    if process_mode == SUMMARY_AND_PUSH and is_guest(username):
        raise HTTPException(status_code=403, detail="访客无法推送钉钉，请注册正式账号")
    # 文本按 6000 字符分块、每块一次大模型调用。框架层的 1MB 字段上限太宽松,
    # 且它的报错("Field exceeded maximum size")对用户没有指导意义。
    #
    # 只在「没有录音、文本就是唯一转写来源」时才按长度拒绝。带录音提交时,
    # live_text 只是浏览器侧实时字幕的副本,下面取 transcript 时会被录音覆盖 ——
    # 原先无条件拒绝,会让一场长会议因为字幕太长而整个提交失败,而里面的录音
    # 本来可以正常转写。密集发言约 52000 字符/小时,不到两小时就会踩到这条线。
    if audio is None and len(live_text) > MAX_LIVE_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="转写文本过长，请分段处理")
    audio_path: str | None = None
    if audio:
        suffix = Path(audio.filename or "recording.webm").suffix.lower() or ".webm"
        if suffix not in ALLOWED_AUDIO_SUFFIXES:
            raise HTTPException(status_code=400, detail="不支持该录音格式")
        target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        size = 0
        with target.open("wb") as output:
            while chunk := await audio.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="录音文件过大")
                output.write(chunk)
        if size == 0:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="录音文件为空")
        # 判据是「有没有可解析的音频流」,不是「能不能读出时长」:浏览器
        # MediaRecorder 产出的是流式 webm,头部不写时长,用时长判定会把每一段
        # 浏览器录音都误判成「文件已损坏」。
        #
        # ffprobe 是同步子进程调用:一小时的音频实测 114ms,超时上限更是 60 秒。
        # 直接在 async 端点里调用会卡住整个事件循环 —— 这期间所有并发请求(会议
        # 心跳、转写轮询)全部停摆。丢到线程池执行,事件循环继续服务其他请求。
        if not await run_in_threadpool(has_audio_stream, target):
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="录音文件已损坏或不是有效媒体文件")
        audio_path = str(target)
    if not audio_path and not live_text.strip():
        raise HTTPException(status_code=400, detail="请上传录音或提供实时转写文本")
    cleanup_jobs()
    rejection = acquire_job_slot(username)
    if rejection:
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)
        raise HTTPException(status_code=429, detail=rejection)
    job_id = uuid.uuid4().hex
    # 记归属:任务结果里带完整转写与纪要,拉取时必须校验是本人的任务
    set_job(job_id, owner_username=username, status="queued", stage="input", progress=5,
            message="录音已进入处理队列")
    try:
        EXECUTOR.submit(run_processing_job, job_id, audio_path, live_text.strip(), process_mode, app_mode, username, custom_template_id, input_mode)
    except Exception:
        release_job_slot(username)
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        raise HTTPException(status_code=503, detail="处理服务暂时不可用，请稍后重试")
    return {"job_id": job_id}


@app.get("/api/custom-templates")
def custom_template_list(request: Request):
    return {"items": list_templates(require_user(request))}


@app.post("/api/custom-templates", status_code=201)
async def custom_template_upload(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(default=""),
    scenario: str = Form(default="general"),
):
    username = require_user(request)
    # 模板解析是 CPU 密集的(约 0.9ms/段,最坏一次半分钟),和导出、头像同一档
    enforce_expensive_rate_limit(username, "template")
    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    try:
        # create_template 会解压 docx、解析 XML 并落库,全是同步阻塞操作
        # (实测 16 KB 的模板就要 27.7ms)。放到线程池,避免卡住事件循环。
        return await run_in_threadpool(
            create_template,
            username,
            name,
            file.filename or "template.docx",
            content,
            content_type=file.content_type or "",
            scenario=scenario,
        )
    except ValueError as error:
        raise HTTPException(status_code=400 if len(content) <= MAX_TEMPLATE_BYTES else 413, detail=str(error))


@app.get("/api/custom-templates/{template_id}")
def custom_template_detail(template_id: str, request: Request):
    template = get_template(require_user(request), template_id)
    if not template:
        raise HTTPException(status_code=404, detail="自定义模板不存在")
    return template_metadata(template)


@app.patch("/api/custom-templates/{template_id}")
def custom_template_update(template_id: str, payload: TemplateUpdatePayload, request: Request):
    username = require_user(request)
    try:
        updated = update_template(
            username,
            template_id,
            name=payload.name,
            scenario=payload.scenario,
            is_default=payload.is_default,
            insertion_strategy=payload.insertion_strategy,
            insertion_target=payload.insertion_target,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not updated:
        raise HTTPException(status_code=404, detail="自定义模板不存在")
    return updated


@app.get("/api/custom-templates/{template_id}/download")
def custom_template_download(template_id: str, request: Request):
    template = get_template(require_user(request), template_id)
    if not template:
        raise HTTPException(status_code=404, detail="自定义模板不存在")
    safe_filename = quote(template["filename"])
    return Response(
        content=template["content"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
    )


@app.delete("/api/custom-templates/{template_id}", status_code=204)
def custom_template_delete(template_id: str, request: Request):
    username = require_user(request)
    if not delete_template(username, template_id):
        raise HTTPException(status_code=404, detail="自定义模板不存在")


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    username = require_user(request)
    cleanup_jobs()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        owner = str(job.get("owner_username") or "").strip()
        job_snapshot = dict(job)
    # 任务结果内含完整转写与纪要,归属校验与历史记录保持同一口径(管理员可见)。
    # 非本人返回 404 而非 403,不确认任务是否存在。
    if owner and owner != username and not is_admin(username):
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_snapshot


@app.post("/api/classify")
def classify(payload: ClassifyPayload, request: Request):
    username = require_user(request)
    enforce_expensive_rate_limit(username, "classify")
    try:
        requested_mode = core.canonical_mode(payload.mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    mode, reason = core.infer_app_mode_best_effort(payload.transcript, requested_mode)
    return {"mode": mode, "mode_label": core.mode_label(mode), "reason": reason}


@app.post("/api/assistant")
def assistant(payload: AssistantPayload, request: Request):
    username = require_user(request)
    enforce_expensive_rate_limit(username, "assistant")
    answer = core.ask_xiaozhi(
        payload.question,
        payload.overview,
        payload.summary,
        payload.transcript,
        payload.record_id,
        payload.history,
        username,
    )
    return {"answer": answer}


@app.post("/api/export/{kind}")
def export(kind: str, payload: ReviewPayload, request: Request):
    username = require_user(request)
    enforce_expensive_rate_limit(username, "export")
    if not payload.summary.strip():
        raise HTTPException(status_code=400, detail="没有可导出的内容")
    try:
        export_mode = core.canonical_mode(payload.mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    export_fields = payload.model_dump()
    export_fields["mode"] = export_mode
    export_fields["mode_label"] = core.mode_label(export_mode)
    if kind == "docx":
        template = get_template(username, payload.custom_template_id, include_deleted=True) if payload.custom_template_id else None
        if payload.custom_template_id and not template:
            raise HTTPException(status_code=404, detail="自定义模板不存在")
        if template and not template_supports_scene(template, export_mode):
            raise HTTPException(status_code=400, detail="所选自定义模板与导出场景不匹配")
        if template:
            path = export_filename("docx")
            render_custom_docx(
                template["content"],
                payload.summary,
                Path(path),
                fields=export_fields,
                analysis=template["analysis"],
                insertion_strategy=template["insertion_strategy"],
                insertion_target=template["insertion_target"],
            )
        else:
            path = core.export_summary_docx(payload.summary, export_mode)
    elif kind == "pdf":
        template = get_template(username, payload.custom_template_id, include_deleted=True) if payload.custom_template_id else None
        if payload.custom_template_id and not template:
            raise HTTPException(status_code=404, detail="自定义模板不存在")
        if template and not template_supports_scene(template, export_mode):
            raise HTTPException(status_code=400, detail="所选自定义模板与导出场景不匹配")
        if template:
            from report_templates import convert_docx_to_pdf
            docx_path = Path(export_filename("docx"))
            pdf_path = Path(export_filename("pdf"))
            render_custom_docx(
                template["content"],
                payload.summary,
                docx_path,
                fields=export_fields,
                analysis=template["analysis"],
                insertion_strategy=template["insertion_strategy"],
                insertion_target=template["insertion_target"],
            )
            try:
                if not convert_docx_to_pdf(docx_path, pdf_path):
                    raise HTTPException(status_code=503, detail="PDF 转换服务暂不可用，请先导出 Word")
            finally:
                docx_path.unlink(missing_ok=True)
            path = str(pdf_path)
        else:
            path = core.export_summary_pdf(payload.summary, export_mode)
    else:
        raise HTTPException(status_code=404, detail="不支持的导出格式")
    # 导出文件是一次性下载产物,发送完成后删除,避免临时目录无限增长
    return FileResponse(path, filename=Path(path).name, background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))


@app.post("/api/dingtalk/push")
def push_dingtalk(payload: ReviewPayload, request: Request):
    username = require_user(request)
    # 访客是免注册进入的,不应能把内容推到公司钉钉群 —— 那是对外发声的通道。
    if is_guest(username):
        raise HTTPException(status_code=403, detail="访客无法推送钉钉，请注册正式账号")
    enforce_expensive_rate_limit(username, "dingtalk")
    try:
        push_mode = core.canonical_mode(payload.mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": core.send_to_dingtalk(payload.summary, push_mode)}


register_meeting_routes(app, require_user, verify_session)


# 根目录下的图片、图标等静态文件。nginx 只给 /assets/ 配了缓存,这些文件穿透到
# 应用后原先不带任何缓存头,于是每次打开页面都要重新下载 —— logo 加图标合计约
# 470KB,手机上这部分开销和首屏一样大。文件名没有内容哈希,所以不能用 immutable:
# 改用一天的 max-age,配合 FileResponse 自带的 ETag / Last-Modified,过期后
# 也只是一次 304,不会重新传输。
STATIC_CACHE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf"}
STATIC_CACHE_CONTROL = "public, max-age=86400"


def _static_cache_headers(path: Path) -> dict[str, str]:
    suffix = path.suffix.lower()
    if suffix in STATIC_CACHE_SUFFIXES:
        return {"Cache-Control": STATIC_CACHE_CONTROL}
    # HTML 外壳绝对不能被缓存:里面写死了带哈希的资源文件名,缓存住等于把用户
    # 钉在旧版本上,发新版也刷不掉。根路径已经显式设了 no-cache,直接按文件名
    # 访问 /index.html 会走到这里,必须一并覆盖。
    if suffix in {".html", ".htm"}:
        return {"Cache-Control": "no-cache"}
    return {}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path:
            try:
                candidate = (FRONTEND_DIST / path).resolve()
                candidate.relative_to(FRONTEND_DIST.resolve())
            except (ValueError, OSError):
                raise HTTPException(status_code=404, detail="Not Found")
            if candidate.is_file():
                return FileResponse(candidate, headers=_static_cache_headers(candidate))
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
