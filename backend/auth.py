"""身份与访问控制:账号、口令、会话、验证码、登录节流、用户档案与管理员判定。

从 backend/main.py 抽出。原因不只是 main.py 过长 —— 这里解开了一处真实的
循环依赖:backend/meetings.py 需要 is_admin / read_profile,但 main.py 又
import meetings 来注册路由,于是 meetings.py 只能在函数体内做延迟导入来绕开
(共 3 处)。身份层本就不该依赖任何路由模块,独立出来后依赖变成单向:

    meetings.py ─┐
                 ├─> auth.py ─> core.py ─> jkinco_* 引擎模块
    main.py ─────┘

安全关键代码集中在一个文件里,也便于审计与针对性测试。
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend import core
from jkinco_logging import get_logger

LOGGER = get_logger("auth")

PROFILE_DB = Path(os.getenv("JKINCO_PROFILE_DB", str(core.HISTORY_DIR / "platform.db")))
PROFILE_DB_LOCK = threading.Lock()
SESSION_SECRET = os.getenv("JKINCO_SESSION_SECRET", secrets.token_urlsafe(48)).encode()
SESSION_COOKIE = "jkinco_session"
SESSION_TTL = int(os.getenv("JKINCO_SESSION_TTL", str(60 * 60 * 24 * 7)))
MAX_AVATAR_BYTES = 2 * 1024 * 1024

# ---- 访客(免注册临时进入)----
GUEST_ROLE = "guest"
GUEST_USERNAME_PREFIX = "guest_"
# 是否开放免注册入口。关掉后 /api/auth/guest 直接 403,前端也不再显示入口。
GUEST_ACCESS_ENABLED = os.getenv("JKINCO_GUEST_ACCESS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
# 访客会话有效期,显著短于正式用户(默认 7 天):临时进入不该长期持有凭据。
GUEST_SESSION_TTL = int(os.getenv("JKINCO_GUEST_SESSION_TTL", str(4 * 60 * 60)))
# 单 IP 在锁定窗口内可创建的访客数上限。免注册入口天然是刷号入口,
# 不限流会让用户表和历史库被无限撑大。
#
# 此前这个常量定义了却从未被任何地方使用 —— 访客接口走的是
# login_blocked 的默认上限 LOGIN_MAX_FAILURES。两者
# 当前恰好都是 5,所以没有行为差异,但那是个陷阱:为宽容密码手误调大
# JKINCO_LOGIN_MAX_FAILURES 会同时放宽访客开号,而调 JKINCO_GUEST_MAX_PER_WINDOW
# 则毫无效果。两个旋钮不该耦合在一起。
GUEST_MAX_PER_WINDOW = int(os.getenv("JKINCO_GUEST_MAX_PER_WINDOW", "5"))


def init_profile_db() -> None:
    PROFILE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PROFILE_DB) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS user_profiles (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                avatar_data TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS platform_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(platform_users)").fetchall()}
        if "role" not in columns:
            connection.execute("ALTER TABLE platform_users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        # 用户名保留注册时的大小写,但唯一性大小写不敏感(Alice 与 alice 视为同一账号)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_users_username_nocase ON platform_users(username COLLATE NOCASE)"
        )


def is_admin(username: str) -> bool:
    """分级制度:JKINCO_AUTH 配置的运营账号与库内 role=admin 的账号为管理员,其余为普通用户。"""
    if username in configured_users():
        return True
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute("SELECT role FROM platform_users WHERE username=?", (username,)).fetchone()
    return bool(row) and row[0] == "admin"


def is_guest(username: str) -> bool:
    """访客身份判定。

    访客是库内 role='guest' 的真实账号,而不是特殊的「无账号」通道 —— 这样归属
    隔离(会议/历史/任务均按 owner_username 判定)自动生效,不必在每个接口另加
    一套访客分支,也就不会漏掉某个接口。
    """
    if username in configured_users():
        return False
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute("SELECT role FROM platform_users WHERE username=?", (username,)).fetchone()
    return bool(row) and row[0] == GUEST_ROLE


def create_guest_account() -> str:
    """建一个临时访客账号并返回用户名。

    口令置为不可复现的随机值:访客只能靠本次下发的会话 Cookie 进入,
    Cookie 过期即无法再登录,也无法用口令找回。
    """
    username = f"{GUEST_USERNAME_PREFIX}{secrets.token_hex(5)}"
    salt = secrets.token_bytes(16)
    unusable_password = secrets.token_urlsafe(32)
    now = time.time()
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        connection.execute(
            "INSERT INTO platform_users(username,password_hash,password_salt,created_at,role) VALUES (?,?,?,?,?)",
            (username, hash_password(unusable_password, salt), salt.hex(), now, GUEST_ROLE),
        )
        connection.execute(
            "INSERT OR REPLACE INTO user_profiles(username,display_name,avatar_data,updated_at) VALUES (?,?,'',?)",
            (username, f"访客{username[-4:]}", now),
        )
    return username


def purge_expired_guests(now: float | None = None) -> list[str]:
    """清理过期访客账号及其档案,返回清理数量。

    访客账号会随每次「免注册进入」增长,不清理的话用户表会无限膨胀。
    以会话有效期的两倍为界:会话早已失效,账号再无任何用处。
    历史记录按 owner_username 归属,这里一并删掉,避免留下无主数据。
    """
    cutoff = (now or time.time()) - GUEST_SESSION_TTL * 2
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        expired = [
            row[0] for row in connection.execute(
                "SELECT username FROM platform_users WHERE role=? AND created_at < ?",
                (GUEST_ROLE, cutoff),
            ).fetchall()
        ]
        if expired:
            marks = ",".join("?" * len(expired))
            connection.execute(f"DELETE FROM platform_users WHERE username IN ({marks})", expired)
            connection.execute(f"DELETE FROM user_profiles WHERE username IN ({marks})", expired)
    if expired:
        _purge_guest_history(set(expired))
    # 返回被清理的用户名,交由上层继续清理其名下资源(如自定义模板)。
    # 不在此处直接清模板:custom_templates 已依赖 auth,反向导入会形成循环依赖。
    return expired


def _purge_guest_history(usernames: set[str]) -> None:
    """删除这些访客留下的历史记录。account 没了,记录留着也没人能看见。"""
    with core.HISTORY_LOCK:
        # 读-改-写:读失败时若拿到空列表,下面的 len 比较会认为「没有变化」而跳过写入,
        # 属于安全的一侧;但仍用严格版,让问题以异常形式暴露而不是静默不清理。
        items = core.load_meeting_history_for_update()
        remaining = [item for item in items if str(item.get("owner_username") or "") not in usernames]
        if len(remaining) != len(items):
            core.write_meeting_history(remaining)


def read_profile(username: str) -> dict[str, str]:
    role = "平台管理员" if is_admin(username) else ("访客" if is_guest(username) else "普通用户")
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute(
            "SELECT display_name, avatar_data FROM user_profiles WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            display_name = "管理员" if username == "admin" else username
            connection.execute(
                "INSERT INTO user_profiles(username, display_name, avatar_data, updated_at) VALUES (?, ?, '', ?)",
                (username, display_name, time.time()),
            )
            return {"username": username, "display_name": display_name, "avatar_data": "", "role": role}
    return {"username": username, "display_name": row[0], "avatar_data": row[1], "role": role}


def write_profile(username: str, display_name: str, avatar_data: str | None = None) -> dict[str, str]:
    current = read_profile(username)
    next_avatar = current["avatar_data"] if avatar_data is None else avatar_data
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        connection.execute(
            """INSERT INTO user_profiles(username, display_name, avatar_data, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET
                 display_name = excluded.display_name,
                 avatar_data = excluded.avatar_data,
                 updated_at = excluded.updated_at""",
            (username, display_name, next_avatar, time.time()),
        )
    return read_profile(username)


@functools.lru_cache(maxsize=1)
def _parse_configured_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for item in raw.split(","):
        if ":" in item:
            username, password = item.split(":", 1)
            if username.strip() and password.strip():
                users[username.strip()] = password.strip()
    return users


def configured_users() -> dict[str, str]:
    # JKINCO_AUTH 在运行时固定,按原始字符串缓存解析结果;
    # 认证在每个请求的热路径上,避免重复分割字符串。返回副本防止缓存被外部改写。
    # 默认必须为空。曾经的默认值是 "admin:123456":只要有人删掉环境变量(而不是
    # 置空),就会凭空多出一个管理员后门 —— 它走明文比对、完全绕过口令哈希,且
    # 不会有任何报错提示。运营账号必须显式配置,缺省时一个都不存在。
    return dict(_parse_configured_users(os.getenv("JKINCO_AUTH", "")))


def user_exists(username: str) -> bool:
    if username in configured_users():
        return True
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        return connection.execute(
            "SELECT 1 FROM platform_users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone() is not None


# PBKDF2-HMAC-SHA256 的迭代次数,取 OWASP 密码存储规范给出的推荐值。
# 这个数字直接决定口令被离线暴破的成本,调低就是削弱防护,不能因为"登录慢了"
# 就随手改小。改动它会使库中已有的口令哈希全部失效,必须配套迁移方案。
PASSWORD_HASH_ITERATIONS = 210_000


def hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS).hex()


def set_password(username: str, new_password: str) -> bool:
    """重设口令,成功返回 True;账号不存在返回 False。

    每次都换新盐:沿用旧盐会让新旧哈希共享同一份彩虹表工作量,也让「口令是否
    被改过」从盐上就能看出来。调用方负责校验旧口令与新口令强度 —— 这里只落库。
    """
    salt = secrets.token_bytes(16)
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        cursor = connection.execute(
            "UPDATE platform_users SET password_hash=?, password_salt=? WHERE username=?",
            (hash_password(new_password, salt), salt.hex(), username),
        )
    return cursor.rowcount > 0


# 用于抹平「账号不存在」与「口令错误」之间的耗时差异。进程内固定即可 ——
# 它只用来消耗与真实校验相同的计算量,不参与任何安全判定。
TIMING_EQUALIZER_SALT = secrets.token_bytes(16)


def authenticate_user(username: str, password: str) -> str | None:
    """校验成功时返回库内的规范用户名(登录输入大小写不敏感),失败返回 None。"""
    configured = configured_users().get(username)
    if configured is not None:
        # 必须先编码成字节:hmac.compare_digest 不接受含非 ASCII 的 str,
        # 直接传原始口令会抛 TypeError 变成 500 —— 而口令只校验长度(8-128),
        # 中文口令完全合法。用户能注册,却会在登录时撞上 500 而不是干净的 401。
        return username if hmac.compare_digest(configured.encode("utf-8"), password.encode("utf-8")) else None
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute(
            "SELECT username, password_hash, password_salt FROM platform_users WHERE username=? COLLATE NOCASE",
            (username,),
        ).fetchone()
    if not row:
        # 账号不存在时也跑一次同等代价的哈希。否则「存在」要跑 21 万轮 PBKDF2
        # (实测 31.7ms),「不存在」立即返回(0.06ms)—— 500 倍的时间差让攻击者
        # 能靠计时远程判断某个账号是否存在,即便两种情况的错误提示完全一致。
        hash_password(password, TIMING_EQUALIZER_SALT)
        return None
    return row[0] if hmac.compare_digest(row[1], hash_password(password, bytes.fromhex(row[2]))) else None


LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_FAILURES_LOCK = threading.Lock()
LOGIN_MAX_FAILURES = int(os.getenv("JKINCO_LOGIN_MAX_FAILURES", "5"))
# 与用户名无关的按 IP 闸门。登录的限流键是「IP:用户名」,换个用户名就换一个
# 计数器,于是 5 次上限根本挡不住轮换用户名的请求;而每次尝试无论账号是否存在
# 都要跑满一次 PBKDF2(为消除时序差异,见 authenticate_user)。生产实测单次
# 115ms、约 9 请求/秒即可吃满一个核,机器只有两核 —— 不带认证的攻击者用一条
# 连接就能占满 CPU,会议与实时转写一起被拖垮。阈值取得宽松:办公室共用出口 IP
# 时 35 个人一分钟内也凑不出 30 次失败,而 30 次只合约 3.4 秒 CPU。
LOGIN_IP_MAX_ATTEMPTS = int(os.getenv("JKINCO_LOGIN_IP_MAX_ATTEMPTS", "30"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("JKINCO_LOGIN_LOCKOUT_SECONDS", "60"))


def client_ip_for_throttle(request: Request) -> str:
    """取限流用的客户端 IP —— 必须取 X-Forwarded-For 的最后一段,不能取第一段。

    nginx 用的是 $proxy_add_x_forwarded_for:它把请求方自带的 XFF 原样保留,再
    将真实对端追加在末尾。因此第一段完全由请求方伪造,取它等于让攻击者自己
    决定限流的键。实测(经 nginx 打到生产):固定伪造 IP 在第 6 次失败登录被锁,
    每次换一个伪造 IP 则 9 次全部放行 —— 登录爆破防护、访客开号限流、注册限流
    一并失效,只需加一个请求头。

    最后一段是 nginx 自己写的,伪造不了。nginx 是边缘(前面没有 CDN/WAF,
    access.log 里 $remote_addr 是各不相同的公网地址),所以它等于真实客户端。
    X-Real-IP 作次选:auth 相关路由都走 location /,那里 nginx 会覆写此头。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if hops:
        return hops[-1]
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def login_throttle_key(request: Request, username: str) -> str:
    return f"{client_ip_for_throttle(request)}:{username}"


LOGIN_FAILURES_MAX_KEYS = 10000
# 验证码有效期。见 make_captcha 中关于折中取值的说明。
CAPTCHA_TTL_SECONDS = 300


def _evict_expired_login_failures(now: float) -> None:
    """按容量回收失败记录,只淘汰「已过锁定窗口」的条目。

    调用方必须已持有 LOGIN_FAILURES_LOCK。

    原实现在超限时整体 clear(),会连同生效中的锁定一起抹掉:攻击者只要用上万个
    不同的 IP/用户名组合刷一遍,就能把目标账号的锁定重置,暴力破解防护形同虚设。
    这里改为只回收过期条目,生效中的锁定在任何情况下都不被清除。
    """
    if len(LOGIN_FAILURES) <= LOGIN_FAILURES_MAX_KEYS:
        return
    for stale_key in [
        candidate for candidate, timestamps in LOGIN_FAILURES.items()
        if not timestamps or now - max(timestamps) >= LOGIN_LOCKOUT_SECONDS
    ]:
        LOGIN_FAILURES.pop(stale_key, None)
    # 极端情况下全部条目都还在锁定窗口内(等同于正在被大规模攻击):
    # 此时宁可让字典短暂超限,也不能放行任何一个生效中的锁定。


def login_blocked(key: str, limit: int | None = None) -> bool:
    now = time.time()
    with LOGIN_FAILURES_LOCK:
        failures = [ts for ts in LOGIN_FAILURES.get(key, []) if now - ts < LOGIN_LOCKOUT_SECONDS]
        # 只读探测不得留下空条目:否则每个新 IP/用户名组合都会永久占一格,
        # 未失败过的正常登录也会把字典撑大。
        if failures:
            LOGIN_FAILURES[key] = failures
        else:
            LOGIN_FAILURES.pop(key, None)
        _evict_expired_login_failures(now)
        return len(failures) >= (LOGIN_MAX_FAILURES if limit is None else limit)


def record_login_failure(key: str) -> None:
    now = time.time()
    with LOGIN_FAILURES_LOCK:
        LOGIN_FAILURES.setdefault(key, []).append(now)
        _evict_expired_login_failures(now)


def clear_login_failures(key: str) -> None:
    with LOGIN_FAILURES_LOCK:
        LOGIN_FAILURES.pop(key, None)


# 7 段数码管:段名 -> 相对单元格的两个端点(比例坐标)
_SEGMENT_ENDS = {
    "a": ((0, 0), (1, 0)),
    "b": ((1, 0), (1, 0.5)),
    "c": ((1, 0.5), (1, 1)),
    "d": ((0, 1), (1, 1)),
    "e": ((0, 0.5), (0, 1)),
    "f": ((0, 0), (0, 0.5)),
    "g": ((0, 0.5), (1, 0.5)),
}
# 只需要 1-8:两个加数各取 1..8
_DIGIT_SEGMENTS = {
    1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
    5: "afgcd", 6: "afgecd", 7: "abc", 8: "abcdefg",
}


def _digit_path(digit: int, x: float, y: float, width: float, height: float) -> str:
    parts = []
    for segment in _DIGIT_SEGMENTS[digit]:
        (x1, y1), (x2, y2) = _SEGMENT_ENDS[segment]
        parts.append(f"M{x + x1 * width:.0f} {y + y1 * height:.0f}L{x + x2 * width:.0f} {y + y2 * height:.0f}")
    return "".join(parts)


def make_captcha() -> dict[str, str]:
    """注册用的算术验证码。

    它挡的是「脚本批量注册」。注册本身还有按 IP 的频次限制,而登录另有失败计数与
    锁定 —— 验证码不承担强防护,但也不该形同虚设。原实现有两处让它退化成纯装饰:

      1. token 是 base64(答案:过期:nonce:签名) —— 答案就明文写在里面,
         任何人 base64 解一下就读到了。HMAC 只防伪造,不防读取。
      2. SVG 里的题目是 <text> 纯文本(「3 + 5 = ?」),连 token 都不用解,
         直接取文字就能算。

    现在:token 里存的是答案的 HMAC(校验时用提交的答案重算再比对),题目用
    7 段数码管的 <path> 画出来,整张图没有任何文本节点。这不能挡住 OCR,
    也从没打算挡 —— 目标是让「解一下 base64」或「取 SVG 文字」这种一行脚本失效。

    5 分钟有效期是可用性与安全的折中:太短会让填表慢的用户白填一遍,
    太长则让一次答案可被反复复用。
    """
    left, right = secrets.randbelow(8) + 1, secrets.randbelow(8) + 1
    answer = str(left + right)
    expires, nonce = int(time.time()) + CAPTCHA_TTL_SECONDS, secrets.token_hex(8)
    # 答案不进 token,只进 token 的这一段摘要 —— nonce 让相同答案每次的摘要都不同
    answer_digest = hmac.new(SESSION_SECRET, f"{answer}:{nonce}".encode(), hashlib.sha256).hexdigest()
    body = f"{answer_digest}:{expires}:{nonce}"
    signature = hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{body}:{signature}".encode()).decode()
    strokes = (
        _digit_path(left, 18, 13, 12, 18)
        + "M40 22L52 22M46 16L46 28"                      # 加号
        + _digit_path(right, 62, 13, 12, 18)
        + "M84 19L96 19M84 25L96 25"                      # 等号
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="124" height="44" viewBox="0 0 124 44">
      <rect width="124" height="44" rx="7" fill="#eef4ff"/><path d="M3 34L121 10M14 8L110 38" stroke="#bad0f5"/>
      <path d="{strokes}" stroke="#195fca" stroke-width="3" stroke-linecap="round" fill="none"/>
      <rect x="104" y="13" width="13" height="18" rx="3" fill="none" stroke="#7ba3de" stroke-width="2" stroke-dasharray="3 2"/>
    </svg>'''
    return {"token": token, "image": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()}


def verify_captcha(token: str, answer: str) -> bool:
    try:
        answer_digest, expires, nonce, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit(":", 3)
        body = f"{answer_digest}:{expires}:{nonce}"
        expected = hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()
        # 答案不在 token 里(见 make_captcha),这里用提交的答案重算摘要再比对。
        # 验证码答案同样来自用户输入,可能含全角数字等非 ASCII 字符;
        # 外层虽然捕获了 TypeError,但那会让「格式不对」和「答案错误」不可区分。
        submitted = hmac.new(
            SESSION_SECRET, f"{answer.strip()}:{nonce}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            int(expires) >= time.time()
            and hmac.compare_digest(signature, expected)
            and hmac.compare_digest(answer_digest, submitted)
        )
    except (ValueError, TypeError):
        return False


def password_fingerprint(username: str) -> str:
    """当前口令的短指纹,写进会话令牌用于「改密码即踢下线其他设备」。

    会话是无状态签名令牌,服务端不存会话表,因此无法逐个吊销。把口令指纹绑进
    令牌后,改密码会让所有既有令牌对不上而自动失效 —— 这正是改密码的目的:
    怀疑账号被盗时,旧设备上的登录必须立刻失效,否则改了等于没改。

    指纹取自口令哈希再做一次 SHA-256 并截断:令牌是发给浏览器的,不能让它携带
    任何可用于离线爆破的原始哈希。环境变量账号不在库里,返回空串代表「无指纹」。
    """
    if username in configured_users():
        return ""
    with PROFILE_DB_LOCK, sqlite3.connect(PROFILE_DB) as connection:
        row = connection.execute(
            "SELECT password_hash FROM platform_users WHERE username=?", (username,)
        ).fetchone()
    if not row:
        return ""
    return hashlib.sha256(row[0].encode()).hexdigest()[:16]


def make_session(username: str, ttl_seconds: int | None = None) -> str:
    """签发会话令牌,格式为 用户名:过期时间:口令指纹:HMAC 签名。

    服务端不存会话表,令牌自身携带全部判定依据,因此每一段都必须纳入签名 ——
    任何一段可被单独篡改,都等于会话可被伪造。口令指纹的用途见
    password_fingerprint:它让改密码能够使其他设备的会话立即失效。
    """
    expires = int(time.time()) + (SESSION_TTL if ttl_seconds is None else ttl_seconds)
    # 用户名里已限制只含字母数字下划线,指纹是十六进制,冒号只会出现在分隔位置
    body = f"{username}:{expires}:{password_fingerprint(username)}"
    signature = hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}:{signature}"


def verify_session(token: str | None) -> str | None:
    """校验会话令牌,通过则返回用户名,否则返回 None。

    四道检查缺一不可:签名、未过期、账号仍存在、口令未被改过。任何一道失败都
    只返回 None,不区分原因 —— 对外区分「签名错」与「已过期」会泄露信息。

    段数不符(例如加入口令指纹之前签发的三段式旧令牌)会在解包时抛 ValueError
    并被下面捕获,即视为无效:旧令牌无法证明口令未被改过,不能继续放行。
    """
    if not token:
        return None
    try:
        username, expires, fingerprint, signature = token.rsplit(":", 3)
        body = f"{username}:{expires}:{fingerprint}"
        expected = hmac.new(SESSION_SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expires) < time.time():
            return None
        if not user_exists(username):
            return None
        # 指纹对不上说明口令在此令牌签发之后被改过,这台设备必须重新登录
        return username if hmac.compare_digest(fingerprint, password_fingerprint(username)) else None
    except (TypeError, ValueError):
        return None


def request_is_https(request: Request) -> bool:
    """判断用户侧是否真的走 HTTPS。反向代理后 request.url.scheme 恒为 http,
    必须同时看 X-Forwarded-Proto。"""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


def attach_session_cookie(
    response: JSONResponse, request: Request, username: str, ttl_seconds: int | None = None
) -> None:
    """登录、注册与访客进入共用的会话下发。集中一处,避免各入口的 cookie 属性走偏
    (曾出现登录读 JKINCO_COOKIE_SECURE、注册不读的不一致)。

    ttl_seconds 供访客使用:临时进入的凭据有效期应显著短于正式账号。
    """
    secure = os.getenv("JKINCO_COOKIE_SECURE", "0") == "1" or request_is_https(request)
    ttl = SESSION_TTL if ttl_seconds is None else ttl_seconds
    response.set_cookie(
        SESSION_COOKIE,
        make_session(username, ttl),
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def require_user(request: Request) -> str:
    username = verify_session(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期")
    return username


