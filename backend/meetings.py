from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from livekit.api import AccessToken, DeleteRoomRequest, LiveKitAPI, VideoGrants
from pydantic import BaseModel, Field

from backend import core
from jkinco_lexicon import correct_domain_terms
from jkinco_text import clean_display_title, clean_message_text, clean_speaker_name, has_meaningful_speech
from backend.auth import is_admin, is_guest, read_profile
from backend.history import serialize_history

from jkinco_logging import get_logger

LOGGER = get_logger("meetings")


DB_PATH = Path(os.getenv("JKINCO_PROFILE_DB", str(core.HISTORY_DIR / "platform.db")))
# 会后纪要生成。会议常在整点集中结束,单线程会让后面的会排长队
# (每份纪要要等 LLM 数分钟)。任务是 I/O 等待型,适度并发不占 CPU。
MEETING_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("JKINCO_MINUTES_WORKERS", "2"))),
    thread_name_prefix="meeting-minutes",
)
EMPTY_ROOM_TIMEOUT_SECONDS = int(os.getenv("JKINCO_EMPTY_ROOM_TIMEOUT", "600"))
# 预约时刻之后的宽限期:这段时间内即使房里没人也不结束会议。会议链接从建好起
# 就一直有效,到点后再留出半小时,晚到的人仍然进得来 —— 只填了开始时间(没填
# 结束时间)的会议原先一到点就按空房 10 分钟回收,晚来几分钟就会发现会没了。
SCHEDULED_START_GRACE_SECONDS = int(os.getenv("JKINCO_SCHEDULED_START_GRACE", str(30 * 60)))
# minutes_status 到用户可见文案的映射。放在模块级而不是埋在处理函数里:
# 它是数据不是逻辑,新增状态时必须同时补文案,否则用户看到的是兜底的「会议记录」,
# 完全不知道自己那场会到底出了什么事。
# 会议聊天单次返回的最大条数。取 400 与前端的保留条数一致 —— 两处不一致时,
# 要么白传一批立刻被丢掉的消息,要么用户滚不到自己刚看过的那条。
CHAT_HISTORY_LIMIT = int(os.getenv("JKINCO_CHAT_HISTORY_LIMIT", "400"))
# 单次转写拉取的最大条数。取 200 略高于前端保留的 120 条,给「刚进会时想往回翻
# 一点」留余量,同时把百人同时进会的首屏开销压下来。增量轮询每次只有几条,
# 这个上限对它不生效。
TRANSCRIPT_FETCH_LIMIT = int(os.getenv("JKINCO_TRANSCRIPT_FETCH_LIMIT", "200"))


# 会议列表返回的最大条数。原先写死 100,而 admin 已经有 102 场 —— 最早的两场
# 在列表里直接消失了,没有任何提示,用户只会以为会议丢了。会议行本身很小
# (百余场约 130KB),500 覆盖数年使用。真正的长期解法是分页,那要改前端,
# 不在本次交付范围内。
MEETING_LIST_LIMIT = int(os.getenv("JKINCO_MEETING_LIST_LIMIT", "500"))
# 预约会议时长上限。设上限是为了防止一场会把空房回收永久关掉 —— 那样忘记结束的
# 会议会一直占着资源、也永远不会归档生成纪要。
MAX_SCHEDULED_DURATION_SECONDS = int(os.getenv("JKINCO_MAX_SCHEDULED_DURATION", str(6 * 60 * 60)))


# 重复频率 -> 间隔天数。按天数而非固定秒数推进,跨夏令时/时区调整时仍然落在同一时刻。
RECURRENCE_INTERVAL_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}
RECURRENCE_LABELS = {"none": "不重复", "daily": "每天", "weekly": "每周", "biweekly": "每两周"}
# WebSocket 连接 scheme 与其对应的页面来源 scheme。浏览器发出的 Origin 用后者。
WEBSOCKET_SCHEME_ORIGINS = {"ws": "http", "wss": "https"}
MINUTES_STATUS_LABELS = {
    "processing": "会议已结束，纪要正在生成",
    "failed": "纪要生成失败，可查看现有转写",
    "empty": "本次会议没有检测到有效发言，未生成纪要",
    "pending": "会议已结束，暂无自动纪要",
    "completed": "会议记录已生成",
}
PARTICIPANT_STALE_SECONDS = int(os.getenv("JKINCO_PARTICIPANT_STALE_SECONDS", "90"))
# 心跳落盘的最小间隔。必须远小于 PARTICIPANT_STALE_SECONDS,否则活跃成员会被误判离线;
# 取 20 秒对 90 秒的判活阈值留了 4 倍余量,同时把大会写压力降到原来的约 1/8。
HEARTBEAT_WRITE_INTERVAL_SECONDS = int(os.getenv("JKINCO_HEARTBEAT_WRITE_INTERVAL", "20"))


def _websocket_origin_matches_host(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin", "").strip()
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    forwarded_proto = websocket.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    # Origin 头永远是 http/https,而 websocket.url.scheme 是 ws/wss —— 直接相等比较
    # 永远不成立。原实现因此完全依赖 nginx 注入 x-forwarded-proto 才能放行任何连接;
    # 这个头一旦缺失(直连、本地起服务、换网关),所有实时转写会被静默 403 拒掉,
    # 而日志里只有一个 403,指不向真正的原因。这里把 ws/wss 映射回等价的 http/https。
    #
    # 注意这只是补上"没有代理头时也能正确判断",并没有放松校验:跨站、降级、
    # 换端口、换子域仍然一律拒绝(见 tests-v2/test_websocket_origin.py)。
    scheme = forwarded_proto or WEBSOCKET_SCHEME_ORIGINS.get(websocket.url.scheme, websocket.url.scheme)
    return parsed.scheme == scheme and parsed.netloc.lower() == websocket.headers.get("host", "").lower()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    # journal_mode 是写在库文件头里的持久属性,建库时设一次即可 —— 每次连接再设一遍
    # 要走一次日志切换检查,实测占建连总开销的 87%(0.456ms → 0.062ms)。
    # foreign_keys 相反,它是连接级开关,必须每次都设。
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_meeting_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as connection:
        # 持久属性,建库时设一次;已是 WAL 的库再设也不会有副作用,可重复执行。
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                meeting_code TEXT NOT NULL UNIQUE,
                room_name TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                creator_username TEXT NOT NULL,
                host_username TEXT NOT NULL,
                status TEXT NOT NULL,
                is_locked INTEGER NOT NULL DEFAULT 0,
                allow_guest INTEGER NOT NULL DEFAULT 1,
                allow_chat INTEGER NOT NULL DEFAULT 1,
                allow_screen_share INTEGER NOT NULL DEFAULT 1,
                realtime_transcription_enabled INTEGER NOT NULL DEFAULT 0,
                auto_minutes_enabled INTEGER NOT NULL DEFAULT 1,
                auto_record INTEGER NOT NULL DEFAULT 0,
                actual_start_at REAL,
                ended_at REAL,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                minutes_status TEXT NOT NULL DEFAULT 'pending',
                history_record_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meeting_participants (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                username TEXT,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                livekit_identity TEXT NOT NULL,
                joined_at REAL NOT NULL,
                left_at REAL,
                last_heartbeat_at REAL NOT NULL,
                connection_status TEXT NOT NULL,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id)
            );
            CREATE TABLE IF NOT EXISTS meeting_transcript_segments (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                participant_identity TEXT NOT NULL,
                sentence_id INTEGER NOT NULL,
                start_time_ms INTEGER NOT NULL,
                end_time_ms INTEGER NOT NULL,
                text TEXT NOT NULL,
                is_final INTEGER NOT NULL,
                provider TEXT NOT NULL,
                deduplication_key TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id)
            );
            CREATE TABLE IF NOT EXISTS meeting_chat_messages (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                sender_identity TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id)
            );
            CREATE TABLE IF NOT EXISTS meeting_minutes_versions (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content_markdown TEXT NOT NULL,
                editor_username TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(meeting_id, version),
                FOREIGN KEY(meeting_id) REFERENCES meetings(id)
            );
            CREATE TABLE IF NOT EXISTS meeting_audit_logs (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                operator_username TEXT NOT NULL,
                action TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_meetings_created_at ON meetings(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_participants_meeting ON meeting_participants(meeting_id, joined_at);
            -- 心跳更新与离场更新都按 (meeting_id, username) 定位,是会议室最高频的写路径
            CREATE INDEX IF NOT EXISTS idx_participants_lookup ON meeting_participants(meeting_id, username);
            -- 转写要按 livekit_identity 关联参会人取显示名。没有这条索引时,
            -- 拼接每一句转写都要把该会议的全部参会记录扫一遍 —— 而参会记录是
            -- 每次加入新增一条(重连也算),重复会议长期复用同一行记录,两边都在
            -- 累积:代价是「本次句数 × 历史参会记录数」,用得越久越慢。
            CREATE INDEX IF NOT EXISTS idx_participants_identity
                ON meeting_participants(meeting_id, livekit_identity);
            CREATE INDEX IF NOT EXISTS idx_transcript_meeting ON meeting_transcript_segments(meeting_id, start_time_ms);
            CREATE INDEX IF NOT EXISTS idx_transcript_incremental ON meeting_transcript_segments(meeting_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_chat_meeting ON meeting_chat_messages(meeting_id, created_at);
            -- 空房清扫每 30 秒跑一次,原先这两条都是全表扫描。meetings 与
            -- meeting_participants 只增不减,扫描成本会随使用时间线性上涨。
            CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
            CREATE INDEX IF NOT EXISTS idx_participants_stale
                ON meeting_participants(connection_status, last_heartbeat_at);
            -- 注:归档时按 history_record_id 反查也是全表扫描,但那条查询写的是
            -- TRIM(history_record_id) <> '',函数包住列之后索引用不上,加索引只会
            -- 徒增写入开销。该查询只在归档路径上跑,暂不为它改写语义。
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(meetings)").fetchall()}
        if "scheduled_start_at" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN scheduled_start_at REAL")
        # 预约会议的计划结束时间。在此之前空房不回收 —— 预约的会常常是「先建好、
        # 人陆续到」,按空房 10 分钟就结束会让先建会的人回来发现会议没了。
        # 存量会议该列为 NULL,行为与改动前完全一致。
        if "scheduled_end_at" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN scheduled_end_at REAL")
        # 重复频率。周会这类固定会议每次都新建一场,意味着链接每周都变,参会人要重新
        # 分发。这里改为同一场会议复用同一个 meeting_code / room_name,结束后把预约
        # 时间滚到下一次 —— 链接与时间都保持不变。
        # 存量会议该列为 'none',行为与改动前完全一致。
        if "recurrence" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'")
        # 本次会议的数据分界线。重复会议滚动到下一次时置为滚动时刻 —— 光靠
        # actual_start_at 不够:滚动后它是空的,从滚动到下次真正开会之间,谁打开
        # 会议都会看到上一次的转写和聊天。
        if "occurrence_floor_at" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN occurrence_floor_at REAL")
        # 周期基准与页面展示的「下一场时间」必须分开保存。用户可能只把下一场
        # 临时改期,但之后仍应回到原来的每周/每两周节奏；若只依赖
        # scheduled_start_at,临时改期会永久移动整个系列。
        if "recurrence_anchor_at" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN recurrence_anchor_at REAL")
        if "recurrence_duration_seconds" not in columns:
            connection.execute("ALTER TABLE meetings ADD COLUMN recurrence_duration_seconds INTEGER")
        # 存量周期会议没有上述字段。这里幂等回填当前预约时刻和标准时长，发布后
        # 第一次启动即可继续滚动，不要求用户重新创建会议。
        connection.execute(
            """UPDATE meetings SET recurrence_anchor_at=scheduled_start_at
               WHERE recurrence <> 'none' AND recurrence_anchor_at IS NULL
                 AND scheduled_start_at IS NOT NULL"""
        )
        connection.execute(
            """UPDATE meetings
               SET recurrence_duration_seconds=CAST(scheduled_end_at-scheduled_start_at AS INTEGER)
               WHERE recurrence <> 'none' AND recurrence_duration_seconds IS NULL
                 AND scheduled_start_at IS NOT NULL AND scheduled_end_at > scheduled_start_at"""
        )
        # 这里曾想为「常驻的重复会议」加一条部分索引,实测撤掉了:续排检查的
        # 到期条件是 MAX(COALESCE(end,0), start+宽限) <= now —— 列被表达式包住,
        # 索引做不了范围查找,只能整段扫;而已有的 idx_meetings_status 已经把
        # 扫描范围限定在「预约中」的会议上,二者代价相同(EXPLAIN QUERY PLAN 也
        # 确实选了后者)。加了只是徒增写入开销。


class MeetingCreate(BaseModel):
    title: str = Field(default="即时会议", min_length=1, max_length=80)
    realtime_transcription_enabled: bool = False
    auto_minutes_enabled: bool = True
    auto_record: bool = False
    allow_guest: bool = True
    allow_chat: bool = True
    allow_screen_share: bool = True
    scheduled_start_at: float | None = None
    scheduled_end_at: float | None = None
    # 重复频率:none / daily / weekly / biweekly。取值在创建时校验,
    # 不合法直接 400,而不是静默落到「不重复」——那样用户会以为已经设好了。
    recurrence: str = Field(default="none", max_length=16)


class MeetingReschedule(BaseModel):
    scheduled_start_at: float
    scheduled_end_at: float | None = None
    # occurrence:只改下一场；series:本场及以后都从新时刻继续重复。
    scope: str = Field(default="occurrence", max_length=16)


class JoinPayload(BaseModel):
    display_name: str = Field(default="", max_length=30)


class ChatPayload(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class MinutesPayload(BaseModel):
    content_markdown: str = Field(min_length=1, max_length=200000)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    for key in (
        "is_locked", "allow_guest", "allow_chat", "allow_screen_share",
        "realtime_transcription_enabled", "auto_minutes_enabled", "auto_record",
    ):
        if key in item:
            item[key] = bool(item[key])
    return item


def normalize_meeting_code(raw: str) -> str:
    """把用户输入的会议号规整成库内格式 NNN-NNN-NNN。

    会议号在库里存成带横杠的形式(见 create_meeting),但用户拿到的号可能来自
    微信、邮件或口头转述,常见形态有纯数字、带空格、全角横杠、复制时混入的
    不可见字符。只按原样精确匹配会让「654821848」这种完全合法的输入进不去会议。
    这里只抽数字重排,不做模糊匹配 —— 位数不符时原样返回,让上层按查不到处理。
    """
    digits = "".join(character for character in str(raw or "") if character.isdigit())
    if len(digits) != 9:
        return str(raw or "").strip()
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:9]}"


def _meeting(meeting_id_or_code: str) -> dict[str, Any]:
    identifier = str(meeting_id_or_code or "").strip()
    normalized = normalize_meeting_code(identifier)
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM meetings WHERE id = ? OR meeting_code = ? OR meeting_code = ?",
            (identifier, identifier, normalized),
        ).fetchone()
    meeting = _row(row)
    if not meeting:
        raise HTTPException(status_code=404, detail="会议不存在")
    return meeting


def _audit(meeting_id: str, username: str, action: str, detail: dict[str, Any] | None = None) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO meeting_audit_logs VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, meeting_id, username, action, json.dumps(detail or {}, ensure_ascii=False), time.time()),
        )


def _is_platform_admin(username: str) -> bool:
    return is_admin(username)


# 会议号冲突时的重试次数。每次重试都换一个全新的号,3 次全撞上意味着号段确实
# 快满了(或随机源坏了),此时报 503 比无限重试好 —— 前者能被监控看见。
MEETING_CODE_MAX_ATTEMPTS = 3


def _new_meeting_code() -> str:
    """随机会议号。首段避开 0 开头以外的语义,格式与用户习惯的「三段式」一致。"""
    return f"{secrets.randbelow(900):03d}-{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"


def _require_host(meeting: dict[str, Any], username: str) -> None:
    if username in {meeting["host_username"], meeting["creator_username"]} or _is_platform_admin(username):
        return
    raise HTTPException(status_code=403, detail="仅主持人可以执行此操作")


def _is_meeting_member(meeting: dict[str, Any], username: str) -> bool:
    """成员判定与会议列表(list_meetings)保持同一口径:
    创建者、主持人、曾加入过的参与者,或平台管理员。"""
    if username in {meeting["creator_username"], meeting["host_username"]}:
        return True
    with db() as connection:
        joined = connection.execute(
            "SELECT 1 FROM meeting_participants WHERE meeting_id=? AND username=? LIMIT 1",
            (meeting["id"], username),
        ).fetchone()
    return bool(joined) or _is_platform_admin(username)


def _require_member(meeting: dict[str, Any], username: str) -> None:
    """会议内容(转写/纪要/聊天/详情)只对本会议成员开放。

    返回 404 而非 403:对非成员不确认会议是否存在,避免按会议号枚举探测。
    与历史记录详情对非所有者返回 404 的口径一致。
    注意:加入接口(join)不受此限制 —— 凭会议号加入正是邀请流程本身。
    """
    if not _is_meeting_member(meeting, username):
        raise HTTPException(status_code=404, detail="会议不存在")


def _end_meeting_record(meeting: dict[str, Any], operator: str, action: str) -> bool:
    now = time.time()
    duration = max(0, int(now - float(meeting.get("actual_start_at") or now)))
    minutes_status = "processing" if meeting["auto_minutes_enabled"] else "pending"
    with db() as connection:
        cursor = connection.execute(
            """UPDATE meetings SET status='processing', ended_at=?, duration_seconds=?,
               minutes_status=?, updated_at=? WHERE id=? AND status='active'""",
            (now, duration, minutes_status, now, meeting["id"]),
        )
        connection.execute(
            """UPDATE meeting_participants SET left_at=COALESCE(left_at, ?),
               last_heartbeat_at=?, connection_status='left'
               WHERE meeting_id=? AND connection_status='connected'""",
            (now, now, meeting["id"]),
        )
    if cursor.rowcount != 1:
        return False
    _audit(meeting["id"], operator, action, {"empty_timeout_seconds": EMPTY_ROOM_TIMEOUT_SECONDS} if operator == "system" else None)
    _disconnect_livekit_room(meeting["room_name"])
    if meeting["auto_minutes_enabled"]:
        MEETING_EXECUTOR.submit(_finalize_minutes, meeting["id"])
    else:
        # 关闭了自动纪要的会议不会走 _finalize_minutes,滚动要在这里做
        _roll_recurring_meeting(meeting["id"])
    return True


LIVEKIT_ADMIN_TIMEOUT_SECONDS = float(os.getenv("JKINCO_LIVEKIT_ADMIN_TIMEOUT", "5"))


def _disconnect_livekit_room(room_name: str) -> None:
    """结束会议时把媒体房间里的人真的断开。

    原先「结束会议」只改数据库:应用停止转写、纪要照常生成,而参会人的浏览器
    还连在 LiveKit 房间里,可以继续通话 —— 主持人以为会已经散了,实际没有,
    而且这段对话不会进入转写,也不留任何记录。令牌的有效期是 12 小时,单靠它
    过期兜不住。

    房间的 auto_create 是开的,所以删房间挡不住持有令牌的人重新建;但它能确定性
    地把当前在场的所有人清出去,这正是「结束」这个动作应有的语义。

    LIVEKIT_API_URL 未配置时直接跳过:LiveKit 跑在 host 网络、应用在 bridge 网络,
    两者之间的地址随部署而变,写死网关 IP 迟早失配。宁可不做这一步,也不能让
    结束会议因为猜错地址而变慢或报错。

    失败只记警告:会议结束已经落库,不能因为媒体服务不可达就把它回滚 —— 那会
    让用户点了「结束」却发现会议还在。
    """
    api_url = os.getenv("LIVEKIT_API_URL", "").strip()
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not api_url or not api_key or not api_secret:
        return

    async def _delete() -> None:
        livekit = LiveKitAPI(api_url, api_key, api_secret)
        try:
            await asyncio.wait_for(
                livekit.room.delete_room(DeleteRoomRequest(room=room_name)),
                timeout=LIVEKIT_ADMIN_TIMEOUT_SECONDS,
            )
        finally:
            await livekit.aclose()

    try:
        # 两个调用方(结束会议的路由、空闲会议清扫线程)都在同步上下文里,
        # 各自所在线程没有事件循环,asyncio.run 可以安全使用。
        asyncio.run(_delete())
    except Exception as error:
        # 房间不存在是常态而非故障:建了会没人进、或者人都已经自行退出,
        # LiveKit 侧根本没有这个房间。按警告记会让日志天天报无意义的错。
        if "does not exist" in str(error) or getattr(error, "code", "") == "not_found":
            return
        LOGGER.warning("结束会议时断开媒体房间失败(%s):%s", room_name, error)


def _roll_missed_recurrences(current_time: float) -> int:
    """把「时间已过却没人参加」的重复会议滚动到下一次。

    滚动原本只发生在会议结束时,而没人来就不会进入 active、也就不会结束 ——
    周会跳过一次(放假、临时取消),界面上就会一直停在几周前的时间,
    列表里的「预约 X 月 X 日」永远是过去,用户会以为这场会已经失效了。

    只处理已经过了计划结束时刻的:正在等人到场的会议不能被提前滚走。
    """
    rolled = 0
    with db() as connection:
        # 到期判断下推到 SQL,配合 idx_meetings_recurring_scheduled 这个部分索引。
        # 重复会议是「长期常驻」的 —— 记录永远停在 scheduled,数量只增不减,而这个
        # 查询每 30 秒跑一次。原先把所有常驻会议全查出来再在 Python 里逐个比时间,
        # 开销随常驻会议数线性增长;下推之后绝大多数轮次一行都不返回。
        # 判据与 _reap_floor 一致:两处若不一致,重复会议会在还能进人的时候就
        # 滚到下一周,参会者按原时间来会发现会议不在了。
        # 也捞 completed:重复会议的续排挂在纪要生成之后,那一步若失败(数据库锁、
        # 进程被杀在两条语句之间),这场周会就永久停在「已结束」,下一周谁也进不去,
        # 而此前没有任何机制能救它 —— 用户要的是「长期常驻,直到取消」。
        # 不含 cancelled:取消就是取消,兜底不能把用户关掉的系列又拉起来。
        # 正常路径下这条兜底不会命中:会议一结束就已被滚到下一周,那时
        # MAX(...) 已在将来,查询自然过滤掉。
        rows = connection.execute(
            """SELECT id FROM meetings
               WHERE status IN ('scheduled', 'completed')
                 AND recurrence<>'none' AND scheduled_start_at IS NOT NULL
                 AND MAX(COALESCE(scheduled_end_at, 0), scheduled_start_at + ?) <= ?""",
            (SCHEDULED_START_GRACE_SECONDS, current_time),
        ).fetchall()
    for row in rows:
        if _roll_recurring_meeting(row["id"]):
            rolled += 1
    return rolled


def _sweep_idle_meetings_once(now: float | None = None) -> int:
    current_time = now or time.time()
    recover_stuck_minutes(current_time)
    _roll_missed_recurrences(current_time)
    with db() as connection:
        connection.execute(
            """UPDATE meeting_participants SET left_at=COALESCE(left_at, last_heartbeat_at),
               connection_status='left' WHERE connection_status='connected' AND last_heartbeat_at < ?""",
            (current_time - PARTICIPANT_STALE_SECONDS,),
        )
        rows = connection.execute(
            """SELECT m.*, COALESCE(MAX(p.last_heartbeat_at), m.created_at) AS last_activity,
                      SUM(CASE WHEN p.connection_status='connected' THEN 1 ELSE 0 END) AS connected_count
               FROM meetings m LEFT JOIN meeting_participants p ON p.meeting_id=m.id
               WHERE m.status='active' GROUP BY m.id"""
        ).fetchall()

    ended_count = 0
    for row in rows:
        meeting = _row(row)
        if not meeting:
            continue
        is_empty = (
            int(meeting.get("connected_count") or 0) == 0
            and current_time - float(meeting["last_activity"]) >= EMPTY_ROOM_TIMEOUT_SECONDS
        )
        if not is_empty:
            continue
        # 约定的结束时间(以及开始后的宽限期)之前不回收空房。预约的会常常是
        # 「先建好、人陆续到」,或中途全体短暂离开(换会议室、等人),按空房
        # 10 分钟就结束会让人回来发现会议已经没了。
        if current_time < _reap_floor(meeting):
            continue
        if _end_meeting_record(meeting, "system", "meeting.auto_ended_empty"):
            ended_count += 1
    return ended_count


def _reap_floor(meeting: dict[str, Any]) -> float:
    """空房回收的最早时刻。

    取「约定结束时刻」与「约定开始时刻 + 宽限期」两者中较晚的。只看结束时刻
    是不够的:很多会只填了开始时间,那样一到点就按空房 10 分钟回收,晚来几分钟
    的人就会发现会议没了。
    """
    floor = 0.0
    scheduled_end_at = meeting.get("scheduled_end_at")
    if scheduled_end_at:
        floor = float(scheduled_end_at)
    scheduled_start_at = meeting.get("scheduled_start_at")
    if scheduled_start_at:
        floor = max(floor, float(scheduled_start_at) + SCHEDULED_START_GRACE_SECONDS)
    return floor


def _start_scheduled_meeting(meeting_id: str) -> bool:
    """把预约会议正式开起来,不管有没有到点。

    与 join 的区别:join 在到点之前只发预览令牌、不改状态(试完设备走开,会议
    仍在那儿等着按时开始);这里是明确的「现在就开」,状态转 active 之后才能
    结束、也才会生成纪要 —— 结束的前置条件正是 status='active'。
    """
    now = time.time()
    with db() as connection:
        cursor = connection.execute(
            "UPDATE meetings SET status='active', actual_start_at=?, updated_at=? WHERE id=? AND status='scheduled'",
            (now, now, meeting_id),
        )
    return cursor.rowcount == 1


# 预约时刻的合理区间。上界按创建时刻往后推,不用绝对年份,免得到期后要改代码。
SCHEDULE_MAX_AHEAD_SECONDS = int(os.getenv("JKINCO_SCHEDULE_MAX_AHEAD", str(2 * 365 * 24 * 3600)))
SCHEDULE_MIN_TIMESTAMP = 1_000_000_000  # 2001-09-09,早于此的取值一定是传错了


def _validated_timestamp(raw: float | None, field: str) -> float | None:
    """校验前端传来的时间戳。

    这里必须挡住非有限值:float 允许 inf 和 nan,Pydantic 也照收,而 inf 会被
    原样写进数据库 —— 之后序列化会抛
    「Out of range float values are not JSON compliant」,该用户的会议列表就
    永久 500,自己再也打不开会议页。一次畸形请求造成永久性故障。

    同时挡住荒谬的年份:1e300 这类取值是合法 JSON、不会崩,但会让会议永远停在
    「已预约」,空房回收永不触发,房间名被永久占用。
    """
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail=f"{field}不是有效时间")
    if value < SCHEDULE_MIN_TIMESTAMP:
        raise HTTPException(status_code=400, detail=f"{field}早于允许范围")
    if value > time.time() + SCHEDULE_MAX_AHEAD_SECONDS:
        years = SCHEDULE_MAX_AHEAD_SECONDS // (365 * 24 * 3600)
        raise HTTPException(status_code=400, detail=f"{field}不能超过 {years} 年之后")
    return value


def _validated_end_time(raw: float | None, start_at: float) -> float | None:
    """校验预约结束时间,返回落库值;不合法直接 400。

    上限存在的意义:在结束时间之前空房不回收,所以一个过长的结束时间等于把回收
    永久关掉 —— 忘记结束的会议会一直占着房间、也永远不会归档生成纪要。
    """
    if raw is None:
        return None
    end_at = float(raw)
    if end_at <= start_at:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
    if end_at - start_at > MAX_SCHEDULED_DURATION_SECONDS:
        hours = MAX_SCHEDULED_DURATION_SECONDS // 3600
        raise HTTPException(status_code=400, detail=f"单场会议时长不能超过 {hours} 小时")
    return end_at


def _meeting_idle_sweeper() -> None:
    """后台回收空房。异常必须可见:静默失败会让空会议永远挂着且无人察觉。"""
    consecutive_failures = 0
    while True:
        time.sleep(30)
        try:
            _sweep_idle_meetings_once()
            consecutive_failures = 0
        except Exception as error:
            consecutive_failures += 1
            # 连续失败只在前几次和每 20 次打印,避免刷屏但保证可诊断
            if consecutive_failures <= 3 or consecutive_failures % 20 == 0:
                LOGGER.warning("空闲会议清理失败(连续第 %d 次):%s", consecutive_failures, error)


def _livekit_url(request: Request) -> str:
    configured = os.getenv("LIVEKIT_PUBLIC_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    ws_proto = "wss" if proto == "https" else "ws"
    host = request.headers.get("host", request.url.netloc)
    forwarded = request.headers.get("x-forwarded-proto", "")
    if not forwarded and ":" in host:
        # 直连容器端口(如 IP:7860)时,/livekit 反代只在 nginx 80/443 上,
        # 去掉端口让信令走 nginx,而不是打到没有该路由的应用容器
        host = host.rsplit(":", 1)[0]
    return f"{ws_proto}://{host}/livekit"


def _issue_token(meeting: dict[str, Any], username: str, display_name: str) -> tuple[str, str, str]:
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not api_key or not api_secret:
        raise HTTPException(status_code=503, detail="会议媒体服务尚未配置")
    identity = f"{username}-{secrets.token_hex(4)}"
    is_host = username in {meeting["creator_username"], meeting["host_username"]}
    # 关闭共享屏幕时,必须在令牌里限制可发布的源 —— 光靠前端隐藏按钮拦不住:
    # LiveKit 的发布权限由令牌决定,谁都可以直接调 SDK 发起共享。
    # 不能简单把 can_publish 置为 False,那会连麦克风和摄像头一起禁掉。
    # 主持人不受此限,否则关掉共享后连自己也没法演示。
    allow_share = bool(meeting["allow_screen_share"]) or is_host
    publish_sources = None if allow_share else ["camera", "microphone"]
    # 同理,关闭聊天时也要收回数据通道的发布权。allow_chat 原先只在 /chat 接口
    # 上强制,而界面用的 LiveKit VideoConference 自带一个走数据通道的聊天:
    # 主持人为敏感会议关掉聊天后,那个入口照样能用,消息既不入库也不进审计。
    # 与共享屏幕一样,这种事只能由令牌决定,前端藏按钮拦不住。
    # 主持人不受此限,口径与共享屏幕保持一致。
    allow_chat = bool(meeting["allow_chat"]) or is_host
    grants = VideoGrants(
        room_join=True,
        room=meeting["room_name"],
        room_admin=is_host,
        can_publish=True,
        can_publish_sources=publish_sources,
        can_subscribe=True,
        can_publish_data=allow_chat,
    )
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        # TTL 给足长会余量:默认 6 小时正好等于会议时长,临界易触发重连;12 小时留出缓冲
        .with_ttl(timedelta(seconds=int(os.getenv("JKINCO_LIVEKIT_TOKEN_TTL", str(12 * 60 * 60)))))
        .with_name(display_name)
        .with_grants(grants)
        .to_jwt()
    )
    return token, identity, "host" if is_host else "participant"


# 转写段与参与者的关联查询在多处复用:说话人显示名优先取参与者档案,
# 参与者记录缺失时回落到 LiveKit identity,避免历史数据出现空说话人。
_TRANSCRIPT_JOIN = """
    FROM meeting_transcript_segments s
    LEFT JOIN meeting_participants p
      ON p.meeting_id=s.meeting_id AND p.livekit_identity=s.participant_identity
"""


def _resolve_participant_identity(meeting_id: str, username: str, requested: str) -> str:
    """核定这一路转写该署名给谁 —— 不能听客户端的。

    identity 原先直接取自查询参数,而它决定转写落在谁名下:_TRANSCRIPT_JOIN 会
    拿它去 meeting_participants 换显示名。于是任何参会人只要把别人的 identity
    填进去,自己说的话就以对方的名义进入会议转写,并照原样写进纪要 —— 纪要是
    会议的正式记录,这等于可以给任何参会人栽赃发言。

    校验方式是查参与者表:该 identity 必须确实属于本人在这场会里的参与记录。
    不匹配时回落到本人最近一次的 identity,而不是照单全收 —— 客户端拿着上一轮
    的 identity 重连是正常现象,直接拒连会让字幕平白断掉。
    """
    with db() as connection:
        rows = connection.execute(
            """SELECT livekit_identity FROM meeting_participants
               WHERE meeting_id=? AND username=? ORDER BY joined_at DESC""",
            (meeting_id, username),
        ).fetchall()
    mine = [row[0] for row in rows]
    if requested and requested in mine:
        return requested
    # 没有参与者记录时回落到用户名本身:此处已通过成员校验,只是记录尚未落库。
    return mine[0] if mine else username


def _final_transcript_text(connection: sqlite3.Connection, meeting_id: str, since: float = 0) -> str:
    """取会议的最终转写,拼成「说话人：内容」的多行文本。

    since 是本次会议的开始时刻:重复会议复用同一行记录,上一次的转写仍留在库里,
    不按此过滤的话,这周的纪要会把上周的内容一起写进去。
    """
    rows = connection.execute(
        "SELECT COALESCE(p.display_name, s.participant_identity) AS speaker_name, s.text"
        + _TRANSCRIPT_JOIN
        + "WHERE s.meeting_id=? AND s.is_final=1 AND s.created_at>=?"
          " ORDER BY s.created_at, s.start_time_ms",
        (meeting_id, since),
    ).fetchall()
    return "\n".join(
        f"{row['speaker_name']}：{row['text'].strip()}" for row in rows if row["text"].strip()
    )


def _final_speech_only(connection: sqlite3.Connection, meeting_id: str, since: float = 0) -> str:
    """同样的范围,但只取说话内容,不带署名。

    判断「这场会有没有人真的说话」必须用这一份。用带署名的那份会被姓名前缀
    架空:has_meaningful_speech("主持人：嗯。") 里「主持人」三个字本身就是有意义
    的文字,于是整场只有语气词的会议照样通过闸门 —— 上传那条路是裸转写所以有效,
    实时会议这条路因此一直没生效。是端到端用例把它照出来的。
    """
    rows = connection.execute(
        "SELECT s.text FROM meeting_transcript_segments s"
        " WHERE s.meeting_id=? AND s.is_final=1 AND s.created_at>=?"
        " ORDER BY s.created_at, s.start_time_ms",
        (meeting_id, since),
    ).fetchall()
    return "\n".join(str(row["text"]).strip() for row in rows if str(row["text"]).strip())


def _meeting_member_usernames(connection: sqlite3.Connection, meeting: dict[str, Any]) -> list[str]:
    """会议的实名成员清单,口径与 _is_meeting_member / list_meetings 一致:
    创建者、主持人、以及曾加入过的注册参与者。

    访客(meeting_participants.username 为空)没有账号,也就没有历史会议列表,
    这里必须过滤掉,否则空串会被当成用户名写进共享名单。
    """
    rows = connection.execute(
        """SELECT DISTINCT username FROM meeting_participants
           WHERE meeting_id=? AND username IS NOT NULL AND TRIM(username) <> ''
           ORDER BY joined_at""",
        (meeting["id"],),
    ).fetchall()
    return [meeting["creator_username"], meeting["host_username"]] + [row["username"] for row in rows]


# 纪要生成的最长容忍时间。超过这个时长仍停在 processing 的,只可能是执行它的
# 进程已经不在了 —— 生成跑在内存里的线程池上,进程一重启(每次发布都会)在途的
# 任务就随之消失,而库里的状态还写着「正在生成」。分块的长会议要打十几次模型,
# 留足余量。
MINUTES_PROCESSING_TIMEOUT_SECONDS = int(os.getenv("JKINCO_MINUTES_PROCESSING_TIMEOUT", str(45 * 60)))


def recover_stuck_minutes(now: float | None = None) -> int:
    """把「永远生成不完」的纪要复位成失败。

    minutes_status='processing' 表示「有个线程正在生成」。这个断言在进程被杀时
    就不再成立:重启后没有任何东西会继续那件事,也没有任何机制把状态改回来 ——
    会议就永久停在「会议已结束，纪要正在生成」。界面据此显示「正在生成」并持续
    轮询,用户看到的是一个永远转下去的圈。

    标成 failed 而不是重新生成:转写还在,用户可以自己重新处理;而自动重跑会在
    每次发布后对所有在途会议重新计费,代价与收益不成比例。
    """
    current = now or time.time()
    with db() as connection:
        cursor = connection.execute(
            """UPDATE meetings SET minutes_status='failed', status='completed', updated_at=?
               WHERE minutes_status='processing'
                 AND COALESCE(ended_at, updated_at, created_at) < ?""",
            (current, current - MINUTES_PROCESSING_TIMEOUT_SECONDS),
        )
    if cursor.rowcount:
        LOGGER.warning("已复位 %d 场卡在「纪要生成中」的会议(执行进程已不存在)", cursor.rowcount)
    return cursor.rowcount


def _mark_minutes_empty(meeting_id: str) -> None:
    """标记「这场会没有可转写的内容」。

    与 failed 分开是为了让告警可用:failed 应当稀有到每出现一条都值得看一眼,
    而空会议在正常使用中很常见,混在一起会让这个信号彻底失效。
    """
    with db() as connection:
        connection.execute(
            "UPDATE meetings SET status='completed', minutes_status='empty', updated_at=? WHERE id=?",
            (time.time(), meeting_id),
        )
    _audit(meeting_id, "system", "minutes.skipped_empty", None)


def _finalize_minutes(meeting_id: str) -> None:
    try:
        with db() as connection:
            meeting = _row(connection.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone())
            floor = _occurrence_floor(meeting)
            transcript = _final_transcript_text(connection, meeting_id, floor)
            # 判「有没有人真的说话」要用不带署名的那份,见 _final_speech_only
            speech_only = _final_speech_only(connection, meeting_id, floor)
            members = _meeting_member_usernames(connection, meeting)
        if not has_meaningful_speech(speech_only):
            # 没人说话不是故障,是这场会本来就没内容 —— 建了会没进去、进去了没开麦
            # 都会走到这里。原先把它当异常记成 failed,后果有两个:用户在界面上看到
            # 「纪要生成失败」,而真正的故障淹没在噪音里(生产上 61 条 failed 里
            # 只有 1 条是真的,真出事故根本发现不了)。
            #
            # 判据原为 `not transcript`,只挡完全空串。但没人说话时 ASR 常常不返回
            # 空,而是给出一两个语气词或一个句号(呼吸声、键盘声都会诱发) ——
            # 那种转写会照常进入生成流程,模型拿着近乎空的素材硬造出一整套章节标题。
            _mark_minutes_empty(meeting_id)
            # 空会议同样是一次已经结束的周期实例。之前这里直接 return，导致每周
            # 会议只要某次没有最终转写，就永久停在「已结束」，下一周不再出现。
            _roll_recurring_meeting(meeting_id)
            return
        # 场景判定同样只看说了什么,不看谁在房间里。显示名由参会者自己填,而它
        # 会出现在带署名转写的每一行 —— 实测把显示名改成「客户拜访」或「候选人」
        # 就能把整场会的场景翻掉,于是一场普通项目会被套上面试记录的模板出纪要。
        # 分类器的关键词表本就是描述「会上谈了什么」的,用说话内容判更贴合原意。
        mode, reason = core.infer_app_mode_best_effort(speech_only, "auto")
        # 生成纪要用带署名的那份:模型需要知道谁说了什么
        summary = core.generate_minutes(transcript, mode)
        overview = core.generate_meeting_overview(summary, transcript, mode)
        record_id = core.save_meeting_history_record(
            transcript, summary, "实时会议自动纪要已生成", mode, "实时会议", overview,
            owner_username=meeting["creator_username"],
            shared_usernames=members,
            classification={
                "requested_mode": "auto",
                "predicted_mode": mode,
                "final_mode": mode,
                "source": "auto_realtime",
                "reason": reason,
                "version": "engineering-evidence-v2",
                "created_at": time.time(),
            },
        )
        now = time.time()
        with db() as connection:
            # 与 save_minutes 同理:这里也要先拿写锁。自动纪要生成期间若有成员
            # 手工保存,两边会算出同一个版本号,撞 UNIQUE 后整个事务回滚 ——
            # 会议被误标为 failed、history_record_id 归空,而纪要已经计过费。
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE meetings SET status='completed', minutes_status='completed',
                   history_record_id=?, updated_at=? WHERE id=?""",
                (record_id, now, meeting_id),
            )
            # 版本号硬编码为 1 时,只要成员在自动纪要生成期间手工保存过一版,
            # 这里就会撞 UNIQUE(meeting_id, version),整个事务回滚:已计费生成的
            # 纪要被丢弃、history_record_id 归空、会议被误标为 failed。
            # 常规路径(无人工版本)下 MAX+1 仍为 1,行为与原来完全一致。
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM meeting_minutes_versions WHERE meeting_id=?",
                (meeting_id,),
            ).fetchone()[0]
            # 判据必须是「本次会议期间有没有人手写过纪要」,不能用 version == 1。
            # 重复会议复用同一行记录,版本号跨次累加:到了第二次,MAX+1 就已经是 2,
            # 用版本号判断会把上一次留下的自动纪要误当成人工版本,于是从第二次起
            # 自动纪要根本不入库,而 get_minutes 只取最新版 —— 用户看到的永远是
            # 第一次那份内容。
            occurrence_floor = _occurrence_floor(meeting)
            manual_versions = connection.execute(
                "SELECT COUNT(*) FROM meeting_minutes_versions WHERE meeting_id=? AND created_at>=?",
                (meeting_id, occurrence_floor),
            ).fetchone()[0]
            if manual_versions == 0:
                connection.execute(
                    "INSERT INTO meeting_minutes_versions VALUES (?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, meeting_id, version, summary, meeting["host_username"], now),
                )
        if manual_versions:
            # 本次已有人工版本:后台任务不覆盖人写的纪要(get_minutes 只取最新版)。
            # 自动生成的正文已随 save_meeting_history_record 落到历史记录,不会丢失。
            _audit(meeting_id, "system", "minutes.auto_skipped_manual_exists", {"manual_versions": manual_versions})
        _roll_recurring_meeting(meeting_id)
    except Exception as error:
        with db() as connection:
            connection.execute(
                "UPDATE meetings SET status='completed', minutes_status='failed', updated_at=? WHERE id=?",
                (time.time(), meeting_id),
            )
        _audit(meeting_id, "system", "minutes.failed", {"error": str(error)[:300]})
        # 纪要失败也要滚动:否则一次生成失败就会让这场周会永久停在已结束状态,
        # 下周谁也进不去,而失败原因往往只是一次大模型抖动。
        _roll_recurring_meeting(meeting_id)


# 会议号的枚举防护。会议号只有 9 亿种组合(900×1000×1000),而它是加入会议的
# 唯一门槛 —— 所有会议接口原先零限流,持续撞号即可找到并进入他人的会议,
# 平台上会议越多越容易。这里对「查不到会议」的尝试计数:正常用户偶尔输错几次,
# 撞号者则需要百万量级的尝试。
MEETING_LOOKUP_MAX_MISSES = int(os.getenv("JKINCO_MEETING_LOOKUP_MAX_MISSES", "20"))
MEETING_LOOKUP_WINDOW_SECONDS = int(os.getenv("JKINCO_MEETING_LOOKUP_WINDOW", "300"))
MEETING_LOOKUP_MISSES: dict[str, list[float]] = {}
MEETING_LOOKUP_LOCK = threading.Lock()


def _guard_meeting_lookup(username: str) -> None:
    """按用户限制「查不到会议」的尝试频率,防止枚举会议号。

    只统计失败:正常加入、重连、离开都能查到会议,不受影响。
    """
    now = time.time()
    with MEETING_LOOKUP_LOCK:
        misses = [ts for ts in MEETING_LOOKUP_MISSES.get(username, []) if now - ts < MEETING_LOOKUP_WINDOW_SECONDS]
        MEETING_LOOKUP_MISSES[username] = misses
        # 顺手清掉空条目,避免字典随用户数无限增长
        if not misses:
            MEETING_LOOKUP_MISSES.pop(username, None)
        if len(misses) >= MEETING_LOOKUP_MAX_MISSES:
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")


def _record_meeting_lookup_miss(username: str) -> None:
    now = time.time()
    with MEETING_LOOKUP_LOCK:
        MEETING_LOOKUP_MISSES.setdefault(username, []).append(now)


def _meeting_for(username: str, meeting_id_or_code: str) -> dict[str, Any]:
    """带枚举防护的会议查询,用于接受用户输入的会议号/ID 的入口。"""
    _guard_meeting_lookup(username)
    try:
        return _meeting(meeting_id_or_code)
    except HTTPException as error:
        if error.status_code == 404:
            _record_meeting_lookup_miss(username)
        raise


def _validated_recurrence(raw: str, is_scheduled: bool) -> str:
    """校验重复频率,不合法直接 400。

    静默落回「不重复」是不可接受的:用户在界面上选了每周、也看到设置成功,
    结果下周会议没出现,而且没有任何地方能看出问题。
    """
    value = (raw or "none").strip().lower()
    if value not in RECURRENCE_LABELS:
        raise HTTPException(status_code=400, detail="不支持的重复频率")
    if value != "none" and not is_scheduled:
        # 重复靠「上次的预约时刻 + 间隔」推算下一次,没有预约时间就无从推算
        raise HTTPException(status_code=400, detail="设置重复频率前需要先选择会议开始时间")
    return value


def _next_occurrence(start_at: float, interval_days: int, now: float) -> float:
    """把预约时刻推进到 now 之后的下一次。

    必须循环推进而不是简单加一个周期:会议可能隔了几周才被结束(忘记关、长假),
    只加一次会滚到一个已经过去的时刻,那样会议立刻又被判定为可加入,等于失效。
    """
    step = interval_days * 86400
    next_at = start_at + step
    if next_at <= now:
        # 直接算出需要跨过几个周期,避免长时间未结束时在这里空转
        missed = int((now - next_at) // step) + 1
        next_at += missed * step
    return next_at


def _roll_recurring_meeting(meeting_id: str) -> bool:
    """重复会议结束后滚动到下一次。

    复用同一行记录(也就是同一个 meeting_code / room_name),所以链接不变。
    本次的转写、纪要、历史记录都已经落库,这里只把「下一次什么时候开」重置掉。

    不删除上一次的转写与聊天:它们是历史记录的原始素材。改为用 actual_start_at
    作为「本次」的分界线,由查询侧过滤 —— 见 _occurrence_floor。
    """
    with db() as connection:
        row = connection.execute(
            """SELECT recurrence, scheduled_start_at, scheduled_end_at,
                      recurrence_anchor_at, recurrence_duration_seconds, status
               FROM meetings WHERE id=?""",
            (meeting_id,),
        ).fetchone()
        if not row:
            return False
        # 已经滚到将来的不再滚。滚动会被多条路径触发(纪要生成成功、纪要生成失败、
        # 空房清扫),而每次滚动都会把锚点推到下一次 —— 重复调用会一路往后加,
        # 直接跳过一整周,参会者按原时间来会发现会议不在了。
        #
        # 判据不能只看 status:清扫缺席会议时,目标恰恰就是 status='scheduled'
        # 但时间已经过去的那些。只有「已排到将来」才说明这次滚动是重复的。
        if (
            str(row["status"]) == "scheduled"
            and row["scheduled_start_at"] is not None
            and float(row["scheduled_start_at"]) > time.time()
        ):
            return False
        interval_days = RECURRENCE_INTERVAL_DAYS.get(str(row["recurrence"] or "none"))
        anchor_at = row["recurrence_anchor_at"] or row["scheduled_start_at"]
        if not interval_days or anchor_at is None:
            return False
        now = time.time()
        canonical_duration = row["recurrence_duration_seconds"]
        if (
            canonical_duration is None
            and row["scheduled_start_at"] is not None
            and row["scheduled_end_at"] is not None
            and float(row["scheduled_end_at"]) > float(row["scheduled_start_at"])
        ):
            # 兼容迁移前数据以及直接写库的集成脚本。第一次续排时自动补齐，
            # 后续实例即可始终保留原会议时长。
            canonical_duration = max(
                0,
                int(float(row["scheduled_end_at"]) - float(row["scheduled_start_at"])),
            )
        next_start = _next_occurrence(float(anchor_at), interval_days, now)
        next_end = (
            next_start + int(canonical_duration)
            if canonical_duration is not None
            else None
        )
        connection.execute(
            """UPDATE meetings SET status='scheduled', scheduled_start_at=?, scheduled_end_at=?,
               recurrence_anchor_at=?, recurrence_duration_seconds=?,
               actual_start_at=NULL, ended_at=NULL, duration_seconds=0,
               minutes_status='pending', history_record_id=NULL,
               occurrence_floor_at=?, updated_at=?
               WHERE id=?""",
            (next_start, next_end, next_start, canonical_duration, now, now, meeting_id),
        )
    _audit(meeting_id, "system", "meeting.recurrence_rolled", {"next_start_at": next_start})
    return True


def _reschedule_scheduled_meeting(
    meeting: dict[str, Any], start_at: float, end_at: float | None, scope: str
) -> dict[str, Any]:
    """修改尚未开始的预约会议。

    occurrence 只改变下一场的可见时间，周期基准保持不动；series 同时移动周期
    基准。会议行、会议号与 LiveKit 房间均不重建，因此原邀请链接继续有效。
    """
    if meeting["status"] != "scheduled":
        raise HTTPException(status_code=409, detail="只有尚未开始的预约会议可以改期")
    if not math.isfinite(start_at):
        raise HTTPException(status_code=400, detail="开始时间无效")
    if end_at is not None and not math.isfinite(float(end_at)):
        raise HTTPException(status_code=400, detail="结束时间无效")
    if start_at <= time.time() + 60:
        raise HTTPException(status_code=400, detail="新的开始时间需至少晚于当前时间 1 分钟")
    normalized_scope = (scope or "occurrence").strip().lower()
    if normalized_scope not in {"occurrence", "series"}:
        raise HTTPException(status_code=400, detail="不支持的改期范围")

    validated_end = _validated_end_time(end_at, start_at)
    recurrence = str(meeting.get("recurrence") or "none")
    if recurrence == "none":
        normalized_scope = "occurrence"
    duration = int(validated_end - start_at) if validated_end is not None else None
    now = time.time()
    with db() as connection:
        if normalized_scope == "series":
            # 没传结束时间时保留原有的时长基准,不能置空 —— 置空之后每一次排期都
            # 没有结束时刻,而「结束时刻之前不回收空房」的逻辑随之失效:周会开场前
            # 十分钟没人进来就会被自动关掉。occurrence 分支用 COALESCE 保留了,
            # 这里原先直接覆盖,两个分支行为不一致。
            cursor = connection.execute(
                """UPDATE meetings SET scheduled_start_at=?, scheduled_end_at=?,
                   recurrence_anchor_at=?,
                   recurrence_duration_seconds=COALESCE(?, recurrence_duration_seconds),
                   updated_at=?
                   WHERE id=? AND status='scheduled'""",
                (start_at, validated_end, start_at, duration, now, meeting["id"]),
            )
        else:
            anchor_at = meeting.get("recurrence_anchor_at") or meeting.get("scheduled_start_at")
            canonical_duration = meeting.get("recurrence_duration_seconds")
            if (
                canonical_duration is None
                and meeting.get("scheduled_start_at") is not None
                and meeting.get("scheduled_end_at") is not None
                and float(meeting["scheduled_end_at"]) > float(meeting["scheduled_start_at"])
            ):
                canonical_duration = int(
                    float(meeting["scheduled_end_at"]) - float(meeting["scheduled_start_at"])
                )
            cursor = connection.execute(
                """UPDATE meetings SET scheduled_start_at=?, scheduled_end_at=?,
                   recurrence_anchor_at=COALESCE(recurrence_anchor_at, ?),
                   recurrence_duration_seconds=COALESCE(recurrence_duration_seconds, ?), updated_at=?
                   WHERE id=? AND status='scheduled'""",
                (start_at, validated_end, anchor_at, canonical_duration, now, meeting["id"]),
            )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="会议状态已变化，请刷新后重试")
    return _meeting(meeting["id"])


def _occurrence_floor(meeting: dict[str, Any]) -> float:
    """本次会议的数据起点。

    重复会议复用同一行记录,上一次的转写与聊天仍留在库里(它们是历史记录的素材)。
    以本次真正开始的时刻为界,查询只取这之后的内容,上周的内容不会串到这周。
    非重复会议的数据本来就都在 actual_start_at 之后,该过滤是无操作。

    取两者的较大值:会议进行中以本次开始时刻为准;滚动之后、下次开会之前
    actual_start_at 是空的,此时以滚动时刻为准,否则会露出上一次的内容。
    """
    return max(
        float(meeting.get("actual_start_at") or 0),
        float(meeting.get("occurrence_floor_at") or 0),
    )


def _store_transcript(meeting_id: str, identity: str, sentence: dict[str, Any],
                      text: str | None = None) -> None:
    """落库一句最终转写。

    text 由调用方传入已纠错的文本 —— 同一句话要同时落库和下发给界面,两处各自
    纠错一次的话,它们「一致」就只靠约定,而不是结构上不可能不一致。纠错本身很
    便宜(实测 0.3 微秒),所以这不是为了省时间,是为了去掉那个口子。
    不传时自己纠错,单元测试与其它调用方的行为不变。
    """
    raw = str(sentence.get("text") or "") if text is None else text
    text = (correct_domain_terms(raw) if text is None else raw).strip()
    if not text:
        return
    # 中间态识别结果只经 WebSocket 实时下发,不落库;持久化仅保留最终句,
    # 否则每句话会以十余个逐字增长的副本占满 meeting_transcript_segments。
    if not sentence.get("sentence_end"):
        return
    sentence_id = int(sentence.get("sentence_id") or 0)
    begin = int(sentence.get("begin_time") or 0)
    end = int(sentence.get("end_time") or begin)
    key = hashlib.sha256(f"{meeting_id}:{identity}:{sentence_id}:True:{text}".encode()).hexdigest()
    with db() as connection:
        if connection.execute(
            """SELECT 1 FROM meeting_transcript_segments
               WHERE meeting_id=? AND participant_identity=? AND is_final=1 AND text=? AND created_at>?
               LIMIT 1""",
            (meeting_id, identity, text, time.time() - 45),
        ).fetchone():
            return
        connection.execute(
            """INSERT OR IGNORE INTO meeting_transcript_segments
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'local', ?, ?)""",
            (uuid.uuid4().hex, meeting_id, identity, sentence_id, begin, end, text, key, time.time()),
        )


def backfill_history_shared_members() -> int:
    """给存量历史记录补写参会成员名单。

    修复前 _finalize_minutes 只写 creator_username,已归档的会议里参会者看不到
    自己参加过的那场会。启动时跑一次把缺口补上,只做并集、不删已有名单,所以
    可重复执行;失败只告警不阻断启动 —— 这是数据修补,不是服务的必要条件。
    """
    try:
        with db() as connection:
            rows = connection.execute(
                """SELECT id, creator_username, host_username, history_record_id FROM meetings
                   WHERE history_record_id IS NOT NULL AND TRIM(history_record_id) <> ''"""
            ).fetchall()
            members_by_record = {
                row["history_record_id"]: _meeting_member_usernames(connection, dict(row))
                for row in rows
            }
        if not members_by_record:
            return 0
        with core.HISTORY_LOCK:
            # 读-改-写:读不出来就别回填,总比把历史覆盖掉好
            items = core.load_meeting_history_for_update()
            changed = 0
            for item in items:
                members = members_by_record.get(item.get("id"))
                if not members:
                    continue
                existing = item.get("shared_usernames")
                merged = core.normalize_usernames(
                    list(existing or []) + members,
                    str(item.get("owner_username") or "").strip(),
                )
                if isinstance(existing, list) and merged == existing:
                    continue
                item["shared_usernames"] = merged
                changed += 1
            if changed:
                core.write_meeting_history(items)
        if changed:
            LOGGER.info("历史记录参会成员回填完成,补齐 %d 条", changed)
        return changed
    except Exception as error:
        LOGGER.warning("历史记录参会成员回填失败,存量记录仍只对发起人可见:%s", error)
        return 0



def _register_meeting_lifecycle_routes(router: APIRouter, require_user: Callable[[Request], str]) -> None:
    """会议本身的生命周期:创建、查询、进出、结束、锁定。

    这些接口都在改变「这场会存不存在、谁在里面」,与下面只读写会中产物的一组
    是两件事,放在一起时整个注册函数长达 413 行,新增接口只能靠往中间插。
    """
    @router.post("")
    def create_meeting(payload: MeetingCreate, request: Request):
        username = require_user(request)
        now = time.time()
        meeting_id = uuid.uuid4().hex
        meeting_code = _new_meeting_code()
        room_name = f"jkinco-{meeting_id}"
        scheduled_start_at = _validated_timestamp(payload.scheduled_start_at, "开始时间")
        is_scheduled = scheduled_start_at is not None and scheduled_start_at > now + 60
        status = "scheduled" if is_scheduled else "active"
        actual_start_at = None if is_scheduled else now
        # 结束时间的基准是「开始时间」而不是「此刻」:即时会议从现在起算,
        # 预约会议从预约时刻起算,否则预约到明天的会必然一创建就超上限。
        scheduled_end_at = _validated_end_time(
            _validated_timestamp(payload.scheduled_end_at, "结束时间"),
            scheduled_start_at if is_scheduled else now,
        )
        if payload.auto_record:
            # auto_record 是个只存不做的字段:它进数据库、进接口返回、也在前端的类型
            # 定义里,但全平台没有任何录制实现(没有一处调用 LiveKit Egress)。
            # 默默收下等于向用户承诺了一个不存在的功能 —— 开完会去找录像才会发现
            # 没有,而那时会议已经结束、无法补救。生产至今 auto_record=1 的会议为 0,
            # 现有界面也没有这个开关,所以明确拒绝不影响任何人,只是让下一个想把它
            # 接到界面上的人立刻知道后端并不支持。
            raise HTTPException(status_code=400, detail="平台暂不支持会议录制，请使用实时转写与自动纪要")
        recurrence = _validated_recurrence(payload.recurrence, is_scheduled)
        recurrence_anchor_at = scheduled_start_at if recurrence != "none" else None
        recurrence_duration_seconds = (
            int(scheduled_end_at - scheduled_start_at)
            if recurrence != "none" and scheduled_end_at is not None and scheduled_start_at is not None
            else None
        )
        # 会议号撞了就换一个再试。9 亿种组合,几百场会时碰撞概率可以忽略 —— 但会议号
        # 永不释放(重复会议长期持有,已结束/已取消的也保留),数量只增不减,概率随
        # 时间单调上升。而 meeting_code 上有 UNIQUE 约束:撞上时原先直接抛出未捕获的
        # sqlite3.IntegrityError,用户拿到的是一个没有任何信息的 500。
        # 这是典型的长期问题:现在看不见,将来偶发一次,还查不出原因。
        for attempt in range(MEETING_CODE_MAX_ATTEMPTS):
            try:
                with db() as connection:
                    connection.execute(
                        """INSERT INTO meetings
                           (id, meeting_code, room_name, title, creator_username, host_username, status,
                            allow_guest, allow_chat, allow_screen_share, realtime_transcription_enabled,
                            auto_minutes_enabled, auto_record, actual_start_at, created_at, updated_at,
                            scheduled_start_at, scheduled_end_at, recurrence,
                            recurrence_anchor_at, recurrence_duration_seconds)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            meeting_id, meeting_code, room_name, clean_display_title(payload.title), username, username, status,
                            int(payload.allow_guest), int(payload.allow_chat), int(payload.allow_screen_share),
                            int(payload.realtime_transcription_enabled), int(payload.auto_minutes_enabled),
                            int(payload.auto_record), actual_start_at, now, now,
                            scheduled_start_at if is_scheduled else None, scheduled_end_at, recurrence,
                            recurrence_anchor_at, recurrence_duration_seconds,
                        ),
                    )
                break
            except sqlite3.IntegrityError:
                # room_name 里含 meeting_id(uuid4),不会重复;能撞的只有 meeting_code。
                if attempt + 1 >= MEETING_CODE_MAX_ATTEMPTS:
                    LOGGER.error("连续 %d 次生成的会议号都已被占用", MEETING_CODE_MAX_ATTEMPTS)
                    raise HTTPException(status_code=503, detail="会议号分配失败，请重试")
                meeting_code = _new_meeting_code()
                LOGGER.warning("会议号冲突,重新分配(第 %d 次)", attempt + 1)
        _audit(meeting_id, username, "meeting.scheduled" if is_scheduled else "meeting.created", {
            "scheduled_start_at": scheduled_start_at if is_scheduled else None,
            "scheduled_end_at": scheduled_end_at,
            "recurrence": recurrence,
        })
        return _meeting(meeting_id)

    @router.get("")
    def list_meetings(request: Request, status: str = ""):
        username = require_user(request)
        # 可见性三个分支必须整体括起来:AND 的优先级高于 OR,不加括号时
        # 追加的 " AND m.status=?" 只作用在最后一个 OR 分支上,status 过滤形同虚设。
        query = """SELECT DISTINCT m.* FROM meetings m
                   WHERE (
                       m.creator_username=? OR m.host_username=? OR EXISTS (
                           SELECT 1 FROM meeting_participants p
                           WHERE p.meeting_id=m.id AND p.username=?
                       )
                   )"""
        params: list[Any] = [username, username, username]
        if status:
            query += " AND m.status=?"
            params.append(status)
        query += (
            " ORDER BY CASE WHEN m.status='scheduled' THEN 0 WHEN m.status='active' THEN 1 ELSE 2 END,"
            " COALESCE(m.scheduled_start_at, m.created_at) DESC LIMIT ?"
        )
        params.append(MEETING_LIST_LIMIT)
        with db() as connection:
            rows = connection.execute(query, params).fetchall()
        return {"items": [_row(row) for row in rows]}

    @router.get("/{meeting_id}")
    def get_meeting(meeting_id: str, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        now = time.time()
        if (
            meeting["status"] == "scheduled"
            and now >= float(meeting.get("scheduled_start_at") or now)
        ):
            with db() as connection:
                connected = connection.execute(
                    """SELECT 1 FROM meeting_participants
                       WHERE meeting_id=? AND connection_status='connected' LIMIT 1""",
                    (meeting["id"],),
                ).fetchone()
                if connected:
                    connection.execute(
                        """UPDATE meetings SET status='active', actual_start_at=?, updated_at=?
                           WHERE id=? AND status='scheduled'""",
                        (now, now, meeting["id"]),
                    )
            if connected:
                meeting = _meeting(meeting["id"])
                _audit(meeting["id"], username, "meeting.started_from_schedule")
        with db() as connection:
            # 心跳写入节流:此接口是会议室的高频轮询点(每人 2.5–8 秒一次),
            # 每次都写库会在大会场景放大成持续写压力。判活只需精度到十几秒,
            # 因此只在距上次心跳超过阈值时才落盘,其余请求走纯读。
            connection.execute(
                """UPDATE meeting_participants SET last_heartbeat_at=?
                   WHERE meeting_id=? AND username=? AND connection_status='connected'
                     AND last_heartbeat_at < ?""",
                (now, meeting["id"], username, now - HEARTBEAT_WRITE_INTERVAL_SECONDS),
            )
            # 只取本次会议的参会记录,口径与转写一致(见 _occurrence_floor)。
            # 每次加入都新增一条(重连也算),而重复会议长期复用同一行记录 ——
            # 不过滤的话,一年后 8 个人会变成 416 条,响应从几 KB 涨到 128KB,
            # 名单里全是重复项。而这个接口是会议室每 2.5-8 秒轮询一次的。
            participants = [dict(row) for row in connection.execute(
                """SELECT * FROM meeting_participants
                   WHERE meeting_id=? AND COALESCE(left_at, joined_at) >= ?
                   ORDER BY joined_at""",
                (meeting["id"], _occurrence_floor(meeting)),
            ).fetchall()]
        meeting["participants"] = participants
        return meeting

    @router.patch("/{meeting_id}/schedule")
    def reschedule_meeting(meeting_id: str, payload: MeetingReschedule, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_host(meeting, username)
        previous = {
            "scheduled_start_at": meeting.get("scheduled_start_at"),
            "scheduled_end_at": meeting.get("scheduled_end_at"),
        }
        updated = _reschedule_scheduled_meeting(
            meeting,
            float(_validated_timestamp(payload.scheduled_start_at, "开始时间")),
            _validated_timestamp(payload.scheduled_end_at, "结束时间"),
            payload.scope,
        )
        _audit(meeting["id"], username, "meeting.rescheduled", {
            "scope": payload.scope,
            "previous": previous,
            "scheduled_start_at": updated.get("scheduled_start_at"),
            "scheduled_end_at": updated.get("scheduled_end_at"),
        })
        return updated

    @router.post("/{meeting_id}/join")
    def join_meeting(meeting_id: str, payload: JoinPayload, request: Request):
        username = require_user(request)
        meeting = _meeting_for(username, meeting_id)
        now = time.time()
        if meeting["status"] == "scheduled":
            # 预约会议的链接自建好起就一直有效。原先非主持人在开始前 10 分钟之外
            # 一律 409「会议尚未开始」—— 主持人把链接发出去,别人点开只看到一句
            # 拒绝,既没法提前调试设备,也没法临时提早开会。而这两件事恰恰最常发生。
            #
            # 到点之前进来的是「预览」:照发 LiveKit 令牌(设备调试需要真的进房),
            # 但会议状态仍是「预约」,不消耗这一次 —— 试完设备走开,会议依然在那儿
            # 等着按时开始。要提前正式开会,走 /start(见 start_meeting)。
            scheduled_at = float(meeting.get("scheduled_start_at") or now)
            if now >= scheduled_at:
                with db() as connection:
                    connection.execute(
                        "UPDATE meetings SET status='active', actual_start_at=?, updated_at=? WHERE id=? AND status='scheduled'",
                        (now, now, meeting["id"]),
                    )
                meeting = _meeting(meeting["id"])
                _audit(meeting["id"], username, "meeting.started_from_schedule")
        if meeting["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="会议已被取消")
        if meeting["status"] not in {"active", "scheduled"}:
            raise HTTPException(status_code=409, detail="会议已结束")
        if meeting["is_locked"] and username != meeting["host_username"]:
            raise HTTPException(status_code=423, detail="会议已锁定")
        # 「不允许访客」原先只落库、不校验:主持人为敏感会议关掉访客,访客照样能进。
        # 放在锁定检查之后、签发令牌之前 —— 一旦签出令牌,人就已经能进房间了。
        if not meeting["allow_guest"] and is_guest(username):
            raise HTTPException(status_code=403, detail="本场会议不允许访客加入，请使用正式账号")
        # 显示名会作为转写的署名原样拼进纪要(「说话人：内容」按行拼接)。只 strip
        # 的话,名字里带一个换行就能凭空造出一整行别人的发言 —— 实测
        # 「张三\n李四：我同意这个方案」会在正式转写里生成一行归到李四名下的发言,
        # 而纪要是共享给全体成员并归档的正式记录。identity 那道门已经关了
        # (见 test_transcript_attribution),这是通往同一后果的第二道门。
        display_name = clean_speaker_name(payload.display_name) or read_profile(username)["display_name"]
        token, identity, role = _issue_token(meeting, username, display_name)
        participant_id = uuid.uuid4().hex
        now = time.time()
        with db() as connection:
            connection.execute(
                """UPDATE meeting_participants SET left_at=?, last_heartbeat_at=?, connection_status='left'
                   WHERE meeting_id=? AND username=? AND left_at IS NULL""",
                (now, now, meeting["id"], username),
            )
            connection.execute(
                """INSERT INTO meeting_participants
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'connected')""",
                (participant_id, meeting["id"], username, display_name, role, identity, now, now),
            )
        is_preview = meeting["status"] == "scheduled"
        _audit(meeting["id"], username, "participant.preview_joined" if is_preview else "participant.joined", {"identity": identity})
        return {
            "meeting": meeting, "token": token, "identity": identity, "role": role,
            "livekit_url": _livekit_url(request),
            "asr_enabled": meeting["realtime_transcription_enabled"],
            "preview_mode": is_preview,
        }

    @router.post("/{meeting_id}/leave")
    def leave_meeting(meeting_id: str, payload: JoinPayload, request: Request):
        username = require_user(request)
        meeting = _meeting_for(username, meeting_id)
        # 非成员调用 leave 原先返回 200,等于向任何人确认「这个会议号存在」——
        # 与 _require_member「对非成员不确认会议是否存在」的口径相悖,
        # 而会议号正是加入会议的唯一门槛。
        _require_member(meeting, username)
        now = time.time()
        with db() as connection:
            connection.execute(
                """UPDATE meeting_participants SET left_at=?, last_heartbeat_at=?, connection_status='left'
                   WHERE meeting_id=? AND username=? AND left_at IS NULL""",
                (now, now, meeting["id"], username),
            )
        _audit(meeting["id"], username, "participant.left")
        return {"ok": True}

    @router.post("/{meeting_id}/start")
    def start_meeting(meeting_id: str, request: Request):
        """把预约会议提前开起来。

        到点之前 join 只给预览令牌(设备调试用),会议状态不动 —— 因为试完设备
        就走的人不该消耗掉这场会。想「不等了,现在就开」时调这里:状态转 active
        之后才能结束,也才会在结束时生成纪要(结束的前置条件正是 status='active')。

        对重复会议尤其重要:它的链接长期常驻,随时可以进,但只有正式开起来的
        这一次才会在结束后归档、生成纪要并滚到下一次。

        权限口径同「结束会议」:发起人、主持人与平台管理员。
        """
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_host(meeting, username)
        if meeting["status"] == "active":
            return _meeting(meeting["id"])  # 幂等:重复点击不该报错
        if meeting["status"] != "scheduled":
            raise HTTPException(status_code=409, detail="会议已结束或已取消")
        if not _start_scheduled_meeting(meeting["id"]):
            # 并发:另一个请求刚把它开起来或取消掉了。以库里的实际状态为准。
            return _meeting(meeting["id"])
        _audit(meeting["id"], username, "meeting.started_manually", {
            "ahead_of_schedule": time.time() < float(meeting.get("scheduled_start_at") or 0),
        })
        return _meeting(meeting["id"])

    @router.post("/{meeting_id}/end")
    def end_meeting(meeting_id: str, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_host(meeting, username)
        if meeting["status"] == "scheduled":
            raise HTTPException(status_code=409, detail="预约时间前为设备测试，退出不会结束预约会议")
        _end_meeting_record(meeting, username, "meeting.ended")
        return _meeting(meeting["id"])

    @router.post("/{meeting_id}/cancel")
    def cancel_meeting(meeting_id: str, request: Request):
        """取消会议。发起人、主持人与平台管理员可用(口径同 _require_host)。

        与「结束会议」的区别在于会议有没有真正开过:取消是「这场会不开了」,
        不生成纪要、不写归档;结束是「开完了」,要走归档与纪要流程。所以两者
        必须是不同的状态,否则取消掉的空会议会去跑一遍纪要生成然后失败。
        """
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_host(meeting, username)
        if meeting["status"] in {"completed", "cancelled"}:
            raise HTTPException(status_code=409, detail="会议已结束或已取消")
        # 仍在房间里的人要被清出去,否则他们的心跳会让这场会看起来还活着
        now = time.time()
        with db() as connection:
            connection.execute(
                """UPDATE meetings SET status='cancelled', ended_at=?, updated_at=?
                   WHERE id=? AND status IN ('scheduled','active')""",
                (now, now, meeting["id"]),
            )
            connection.execute(
                """UPDATE meeting_participants SET left_at=COALESCE(left_at, ?),
                   connection_status='left' WHERE meeting_id=? AND connection_status='connected'""",
                (now, meeting["id"]),
            )
        _audit(meeting["id"], username, "meeting.cancelled", None)
        return _meeting(meeting["id"])

    @router.post("/{meeting_id}/lock")
    @router.post("/{meeting_id}/unlock")
    def meeting_action(meeting_id: str, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_host(meeting, username)
        action = request.url.path.rsplit("/", 1)[-1]
        locked = action == "lock"
        with db() as connection:
            connection.execute(
                "UPDATE meetings SET is_locked=?, updated_at=? WHERE id=?",
                (int(locked), time.time(), meeting["id"]),
            )
        _audit(meeting["id"], username, f"meeting.{action}ed")
        return _meeting(meeting["id"])

def _register_meeting_content_routes(router: APIRouter, require_user: Callable[[Request], str]) -> None:
    """会中产物:转写、聊天、纪要、归档记录。

    共同前提是会议已经存在且调用者是成员,因此每个接口开头都是
    _meeting() + _require_member() 这对固定动作。
    """
    @router.get("/{meeting_id}/transcript")
    def transcript(meeting_id: str, request: Request, after: float = 0):
        """after 为上次拉取的最大 created_at,增量返回;长会全量转写会大到拖垮轮询。"""
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        with db() as connection:
            # 倒序取最近 N 条再翻正。首次进会(after=0)时若不截断,一小时会议的
            # 2000 条转写要 853 KB / 12.3ms —— 百人同时进会就是 83 MB 和 1.2 秒 CPU,
            # 而前端本来只保留最后 120 条,多出来的全部立刻丢掉。
            # 增量拉取(after>0)通常只有几条,截断不会生效,行为不变。
            rows = connection.execute(
                "SELECT s.*, COALESCE(p.display_name, s.participant_identity) AS speaker_name,"
                " p.username AS speaker_username"
                + _TRANSCRIPT_JOIN
                + "WHERE s.meeting_id=? AND s.created_at>?"
                + " ORDER BY s.created_at DESC, s.start_time_ms DESC LIMIT ?",
                # 游标不得早于本次开始时刻,否则重复会议进场时会先刷出上一次的转写
                (meeting["id"], max(after, _occurrence_floor(meeting)), TRANSCRIPT_FETCH_LIMIT),
            ).fetchall()
        return {"items": [dict(row) for row in reversed(rows)]}

    @router.get("/{meeting_id}/chat")
    def chat(meeting_id: str, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        with db() as connection:
            # 只取最近 CHAT_HISTORY_LIMIT 条。前端每 2.5 秒轮询一次,且拿到后本来就
            # 只留最后 400 条 —— 早于此的消息查了、传了、解析了,然后被丢掉。
            # 先按时间倒序取 N 条再翻正,避免为了拿末尾 N 条而扫描整场会议的消息。
            rows = connection.execute(
                "SELECT * FROM meeting_chat_messages WHERE meeting_id=? AND created_at>=?"
                " ORDER BY created_at DESC LIMIT ?",
                # 重复会议复用同一行记录,不按本次起点过滤会看到上一次的聊天
                (meeting["id"], _occurrence_floor(meeting), CHAT_HISTORY_LIMIT),
            ).fetchall()
        return {"items": [dict(row) for row in reversed(rows)]}

    @router.post("/{meeting_id}/chat")
    def send_chat(meeting_id: str, payload: ChatPayload, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        if not meeting["allow_chat"]:
            raise HTTPException(status_code=403, detail="主持人已关闭聊天")
        # ChatPayload 的 min_length=1 挡的是原始长度。清洗之后可能什么都不剩
        # (纯空白、纯控制字符),那样会在所有人的聊天里留下一个空气泡。
        message = clean_message_text(payload.message)
        if not message:
            raise HTTPException(status_code=400, detail="消息内容不能为空")
        item = {
            "id": uuid.uuid4().hex, "meeting_id": meeting["id"], "sender_identity": username,
            "sender_name": read_profile(username)["display_name"], "message": message,
            "created_at": time.time(),
        }
        with db() as connection:
            connection.execute(
                "INSERT INTO meeting_chat_messages VALUES (:id,:meeting_id,:sender_identity,:sender_name,:message,:created_at)", item,
            )
        return item

    @router.get("/{meeting_id}/minutes")
    def get_minutes(meeting_id: str, request: Request):
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        with db() as connection:
            row = connection.execute(
                "SELECT * FROM meeting_minutes_versions WHERE meeting_id=? ORDER BY version DESC LIMIT 1",
                (meeting["id"],),
            ).fetchone()
        return {"status": meeting["minutes_status"], "item": dict(row) if row else None, "history_record_id": meeting["history_record_id"]}

    @router.get("/{meeting_id}/record")
    def get_meeting_record(meeting_id: str, request: Request):
        """Return a viewable record even when automatic minutes are unavailable."""
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)

        if meeting.get("history_record_id"):
            # 只读:serialize_history 生成的是新字典,下面改的是它、不是缓存里的记录
            for history_item in core.iter_meeting_history():
                if history_item.get("id") == meeting["history_record_id"]:
                    # read_only 由 serialize_history 按 viewer 判定:参会者能看能导出,
                    # 但「保存校核稿」只留给发起人。此前这里恒为 False,参会者点保存
                    # 会撞历史接口的 404。
                    item = serialize_history(history_item, viewer=username)
                    item["realtime_meeting_id"] = meeting["id"]
                    return item

        with db() as connection:
            transcript_text = _final_transcript_text(connection, meeting["id"])
            minutes_row = connection.execute(
                """SELECT content_markdown FROM meeting_minutes_versions
                   WHERE meeting_id=? ORDER BY version DESC LIMIT 1""",
                (meeting["id"],),
            ).fetchone()

        summary_text = str(minutes_row[0]).strip() if minutes_row else ""
        record_status = MINUTES_STATUS_LABELS.get(str(meeting.get("minutes_status")), "会议记录")
        overview_text = summary_text or transcript_text or "本次会议暂无可用的最终转写内容。"
        return {
            "id": f"realtime-{meeting['id']}",
            "realtime_meeting_id": meeting["id"],
            "read_only": True,
            "title": meeting["title"],
            "created_at": meeting["created_at"],
            "mode": "talk",
            "mode_label": "会议纪要",
            "source": "实时会议",
            "transcript": transcript_text,
            "summary": summary_text,
            "overview": overview_text,
            "status": record_status,
            # 显式给出机器可判的状态。前端要据此显示「纪要生成中」,而 status 是
            # 给人看的中文文案 —— 让界面去比对那串文字,改一个字就会失效。
            "minutes_status": str(meeting.get("minutes_status") or ""),
        }

    @router.patch("/{meeting_id}/minutes")
    def save_minutes(meeting_id: str, payload: MinutesPayload, request: Request):
        """写入一版纪要。

        权限口径必须与 history_editable_by 一致 —— 那里写得很明确:「写权限比读
        权限窄一档,参会成员能看、能导出,但不能覆盖主持人定稿:一场会有多个
        参会者,谁都能改就没有「定稿」可言了。」这里原先只要 _require_member,
        等于给同一份记录开了第二道更宽的门。

        不是纸面问题:纪要生成失败或判为无内容的会议永远拿不到
        history_record_id,/record 就一直回落到 meeting_minutes_versions ——
        那种会议里,任何参会者都能把伪造的「正式纪要」塞进去给全体成员看。

        两道检查的顺序有意义:非成员必须拿到 404,与本模块其余接口一致 ——
        403 会确认「这个会议存在」,给按会议号枚举留了口子。成员但非主持人
        才给 403,那是一句对本人有意义的说明。
        """
        username = require_user(request)
        meeting = _meeting(meeting_id)
        _require_member(meeting, username)
        _require_host(meeting, username)
        with db() as connection:
            # 取版本号与写入之间必须原子:两人同时保存(或一个人连点两下)会算出
            # 相同的版本号,撞上 UNIQUE(meeting_id, version) 直接 500,用户刚写的
            # 纪要就此丢失。BEGIN IMMEDIATE 先拿写锁再读,把并发保存串行化;
            # 连接本身有 15 秒 busy 超时,等待期间不会立即失败。
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM meeting_minutes_versions WHERE meeting_id=?",
                (meeting["id"],),
            ).fetchone()[0]
            item_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO meeting_minutes_versions VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, meeting["id"], version, payload.content_markdown, username, time.time()),
            )
        _audit(meeting["id"], username, "minutes.updated", {"version": version})
        return {"id": item_id, "version": version}

def _register_realtime_asr_route(app: FastAPI, verify_session: Callable[[str | None], str | None]) -> None:
    """实时转写 WebSocket（开源版）。

    开源版暂不提供实时流式字幕（录音上传后的整段本地转写为旗舰能力）。
    路由保留并返回明确错误，避免前端无限重连；鉴权与会议开关校验照旧。
    """
    @app.websocket("/api/realtime/asr/{meeting_id}")
    async def realtime_asr(websocket: WebSocket, meeting_id: str):
        if not _websocket_origin_matches_host(websocket):
            await websocket.close(code=4403)
            return
        username = verify_session(websocket.cookies.get("jkinco_session"))
        if not username:
            await websocket.close(code=4401)
            return
        try:
            meeting = _meeting(meeting_id)
        except HTTPException:
            await websocket.close(code=4404)
            return
        # 非成员不得推流:否则可向他人会议注入伪造转写内容
        if not _is_meeting_member(meeting, username):
            await websocket.close(code=4403)
            return
        # 主持人关掉实时字幕后,服务端必须真的拒绝推流。原先这个开关只随加入响应
        # 回传给前端(asr_enabled),由前端决定要不要连 —— 改过的客户端或直接调
        # 接口都能绕过,既违背主持人的设置(保密会议不留字幕),也会白白产生
        # 语音识别的调用费用。
        if not meeting["realtime_transcription_enabled"]:
            await websocket.close(code=4403, reason="本场会议未开启实时转写")
            return
        await websocket.accept()
        await websocket.send_json({
            "type": "asr.error",
            "message": "开源版暂未提供实时流式转写，请使用录音上传后自动转写",
        })
        await websocket.close(code=4503)

    @app.websocket("/api/realtime/asr")
    async def realtime_asr_recorder(websocket: WebSocket):
        """录音面板的实时转写：开源版暂不支持，返回明确错误。"""
        if not _websocket_origin_matches_host(websocket):
            await websocket.close(code=4403)
            return
        username = verify_session(websocket.cookies.get("jkinco_session"))
        if not username:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        await websocket.send_json({
            "type": "asr.error",
            "message": "开源版暂未提供实时流式转写，请使用录音上传后自动转写",
        })
        await websocket.close(code=4503)

def register_meeting_routes(app: FastAPI, require_user: Callable[[Request], str], verify_session: Callable[[str | None], str | None]) -> None:
    """注册全部会议接口,并拉起后台的空闲会议回收线程。"""
    init_meeting_db()
    # 启动这一刻必然没有正在跑的生成线程,库里还写着 processing 的一定是孤儿
    # (上一个进程被杀在半途)。这里不看时长直接复位 —— 比清扫线程那条按 45 分钟
    # 超时的规则更准,用户不必再等三刻钟才看到结果。
    try:
        orphaned = recover_stuck_minutes(now=time.time() + MINUTES_PROCESSING_TIMEOUT_SECONDS + 1)
        if orphaned:
            LOGGER.warning("启动时复位了 %d 场上一进程遗留的「纪要生成中」会议", orphaned)
    except Exception as error:  # 复位失败不该挡住服务启动
        LOGGER.warning("启动复位纪要状态失败:%s", error)
    backfill_history_shared_members()
    threading.Thread(target=_meeting_idle_sweeper, name="meeting-idle-sweeper", daemon=True).start()
    router = APIRouter(prefix="/api/meetings", tags=["meetings"])
    _register_meeting_lifecycle_routes(router, require_user)
    _register_meeting_content_routes(router, require_user)
    app.include_router(router)
    _register_realtime_asr_route(app, verify_session)
