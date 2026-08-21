"""会议历史存储与标题生成。

从 JKincoListen.py 单体抽出的持久化层。历史以单个 JSON 文件保存,采用
「临时文件 + 原子替换」写入并由 HISTORY_LOCK 串行化,保证后台任务、人工校核与
实时会议纪要并发落盘时不互相覆盖、进程崩溃不留半截文件。
标题优先走规则抽取,失败时才回退小模型命名。
JKincoListen.py 通过 re-import 保持向后兼容(backend 直接引用 HISTORY_DIR/HISTORY_LOCK 等)。
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from jkinco_llm import call_llm
from jkinco_prompts import SOURCE_GUARD, fence_source
from jkinco_scenes import history_mode_label, mode_label
from jkinco_text import compact_text

from jkinco_logging import get_logger

LOGGER = get_logger("history")


HISTORY_DIR = Path(os.getenv("JKINCO_HISTORY_DIR", str(Path.home() / ".jkinco_history")))


HISTORY_FILE = HISTORY_DIR / "meetings.json"


# 溢出归档。只追加、不解析,所以文件再大也不影响正常读取。
HISTORY_ARCHIVE_FILE = HISTORY_DIR / "meetings-archive.jsonl"


# 热表(meetings.json)保留的条数。上限存在的理由是这个文件被整体读入内存,
# 且 load_meeting_history() 每次返回都要 deepcopy —— 无限增长会让每一次读取
# 都变慢、变重。
#
# 但超出的部分绝不能直接丢弃:原先是 items[:80] 一刀切,新记录插在头部,
# 于是第 81 场会议开始,每存一场就永久删掉一场最老的,没有任何提示,也无法找回。
# 发现时生产已有 67 条,距离开始丢数据只剩 13 场会议;其中 15 条只存在于这个
# 文件里,删掉就是彻底没了。而且这是全局共用的一份列表,一个人开会多了会挤掉
# 别人的历史。
#
# 现在改为:超出的部分追加到归档文件(每行一条 JSON,只追加不解析),热表只保留
# 最近的若干条。数据一条都不少,读取代价也仍然有界。
HISTORY_MAX_ITEMS = int(os.getenv("JKINCO_HISTORY_MAX_ITEMS", "300"))


# 历史文件为整体读-改-写:后台任务、人工校核、实时会议纪要会并发落盘,必须串行化
HISTORY_LOCK = threading.RLock()


# 解析缓存。历史文件是整体读-改-写的单个 JSON,生产上已近 700 KB,而
# load_meeting_history 在一次请求里会被调用多次(问筑听一次提问就要 3~4 次)。
# 缓存键取文件的 (mtime_ns, size):外部进程改写文件也能被发现,不只认自己的写入。
_CACHE_LOCK = threading.RLock()
_cache_key = None
_cache_items: list | None = None
_cache_search: list[str] | None = None


def _read_history_file():
    data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [
        item for item in data
        if not str(item.get("title", "")).startswith(("请先选择", "❌", "实时转写暂不可用"))
    ]


class HistoryUnavailable(RuntimeError):
    """历史文件存在但读不出来。

    专门给「读出来、改一改、再写回」的路径用:那种路径一旦把读失败当成「空历史」,
    紧接着的写回就会把整个历史文件覆盖掉。宁可让这次写入失败,也不能销毁数据。
    """


def _load_cached(with_search: bool = False, strict: bool = False):
    """返回 (记录列表, 搜索文本列表或 None);两者索引一一对应。调用方不得改动返回值。

    搜索文本按需构建。它的代价和解析文件本身相当(4.32ms vs 4.93ms),但十四个
    调用点里只有关键词搜索用得上 —— 每次写入都会让缓存失效,之后第一个读到的
    调用方若无条件构建,就是白付这笔钱,还平白多占 691 KB 常驻内存。

    strict=True 时读不出来抛 HistoryUnavailable,而不是退化成空列表。展示类路径
    退化成空只是少显示几条,写回类路径退化成空则会抹掉全部历史 —— 两者不能共用
    同一套容错。文件不存在不算失败:那是首次写入前的正常状态。
    """
    global _cache_key, _cache_items, _cache_search
    try:
        stat = HISTORY_FILE.stat()
    except FileNotFoundError:
        return [], []
    except OSError as error:
        if strict:
            raise HistoryUnavailable(f"历史文件无法访问:{error}") from error
        return [], []
    # 键里带上路径:否则换一个历史文件(测试夹具、或改了 JKINCO_HISTORY_DIR)时,
    # 只要 mtime 和大小恰好相同,就会读到上一个文件的内容。
    key = (str(HISTORY_FILE), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        if key != _cache_key or _cache_items is None:
            try:
                items = _read_history_file()
            except (OSError, json.JSONDecodeError) as error:
                if strict:
                    raise HistoryUnavailable(f"历史文件无法解析:{error}") from error
                return [], []
            _cache_key, _cache_items, _cache_search = key, items, None
        # 必须在锁内、且基于同一份 _cache_items 构建,否则搜索文本会与记录错位
        if with_search and _cache_search is None:
            _cache_search = [json.dumps(item, ensure_ascii=False).lower() for item in _cache_items]
        return _cache_items, _cache_search


def load_meeting_history():
    """历史记录列表,用于展示。读不出来时退化为空列表。

    要改完写回的路径请用 load_meeting_history_for_update() —— 用这个函数会把
    「读失败」当成「历史是空的」,写回时就把所有记录抹掉了。
    """
    items, _ = _load_cached()
    return copy.deepcopy(items)


def iter_meeting_history():
    """只读遍历历史记录 —— 不拷贝,调用方绝不能改动返回的字典。

    load_meeting_history() 每次都要 deepcopy 整份历史,那是为了防止调用方改到
    缓存。但读取路径里的绝大多数调用只是「过一遍、挑出可见的、交给
    serialize_history 生成新字典」,一次也不写 —— 却要为 300 条带全文转写的
    记录付 0.91ms 的拷贝(实测 2.6MB 热表),而这正是打开工作台时最常走的那条路。

    要改完写回请用 load_meeting_history_for_update():那条路径不但会拷贝,
    读失败时还会抛异常而不是返回空列表 —— 用错函数会把整份历史覆盖掉。
    """
    items, _ = _load_cached()
    return items


def load_meeting_history_for_update():
    """供读-改-写路径使用:读不出来时抛 HistoryUnavailable,绝不返回空列表。

    调用方应当在 HISTORY_LOCK 内使用它,并让异常向上冒泡 —— 让这一次保存失败
    (用户会看到报错、可以重试),远好过静默地把全部历史覆盖成一条或零条。

    直接解析文件,而不是拷贝缓存:两者都能给出一份「改了也不会污染别人」的独立
    副本,但解析 2.6MB 只要 0.8ms,深拷贝要 4.4ms —— 保存一条纪要总共才 6.9ms,
    这一项就占了六成。顺带还更稳:读-改-写本就该以磁盘上的当前内容为准,不经过
    那层按 (mtime, size) 判新旧的缓存。
    """
    try:
        return _read_history_file()
    except FileNotFoundError:
        # 首次写入前没有文件,这不是失败 —— 与 _load_cached 的口径保持一致
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise HistoryUnavailable(f"历史文件无法读取:{error}") from error


def find_archived_record(record_id: str):
    """在归档文件里按 id 找一条记录,找不到返回 None。

    归档文件此前是只写的:热表满 HISTORY_MAX_ITEMS 条之后,更早的记录被追加到
    meetings-archive.jsonl 就再也没有任何代码读它 —— 数据还在盘上,但用户那边
    是「不见了」。而会议表里存着 history_record_id,一旦对应记录被挤出热表,
    那场会议的「查看纪要」就此打不开,且看起来像数据丢了。

    只在热表未命中时才调用:归档只增不减,不该进常规读取路径。

    必须逐行流式扫描,不能 readlines()。归档里每条都带全文转写(单条约 2KB),
    一次性读进内存意味着「查一条老纪要」的开销跟着归档总量线性上涨 —— 实测 2 万条
    (120MB)时单次查询峰值多占 103MB,而这台机器只有 2 核、内存本就紧张。
    流式扫描的峰值只取决于最长的那一行。

    这里不做「从后往前」:那需要先把全文读进来才知道行边界,正是要避免的事。
    顺序扫描会多读一些行,但每次查询是常数内存,而且这条路径本就少见
    (热表能覆盖最近 HISTORY_MAX_ITEMS 条)。
    """
    record_id = str(record_id or "").strip()
    if not record_id or not HISTORY_ARCHIVE_FILE.exists():
        return None
    found = None
    try:
        with open(HISTORY_ARCHIVE_FILE, encoding="utf-8") as handle:
            for line in handle:
                # 先做子串预筛:逐行 json.loads 太贵,而记录 id 是十六进制串,
                # 出现在无关行里的概率可以忽略。
                if record_id not in line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 追加写 + 断电可能留下半行,跳过即可
                if isinstance(item, dict) and item.get("id") == record_id:
                    found = item  # 不提前返回:同一 id 若被归档过多次,以最后一条为准
    except OSError as error:
        LOGGER.warning("读取历史归档失败:%s", error)
        return None
    return found


def load_history_with_search():
    """一次取回 (深拷贝的记录, 对应的小写全文)。仅关键词搜索需要,别处请用
    load_meeting_history() —— 全文的构建代价和解析整个文件相当。

    必须一次取回:分两次调用的话,中间若有写入落盘,两个列表会来自不同版本,
    过滤时就会张冠李戴 —— 搜到 A 的关键词却返回 B 那条记录。
    """
    items, search = _load_cached(with_search=True)
    # 全文列表按引用返回但拷一层外壳:字符串本身不可变,拷贝整份没有意义,
    # 而共享同一个 list 对象会让调用方的排序/删改直接改到缓存上。
    return copy.deepcopy(items), list(search or [])


def _archive_overflow(overflow):
    """把溢出热表的记录追加到归档文件。

    归档失败绝不能中断写入 —— 那会让整个「保存纪要」失败;但也不能因此把记录
    丢掉,所以失败时保留在热表里,下次写入再试。
    """
    if not overflow:
        return True
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_ARCHIVE_FILE, "a", encoding="utf-8") as handle:
            for item in overflow:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError as error:
        LOGGER.warning("历史归档写入失败,记录暂留在热表中:%s", error)
        return False


def write_meeting_history(items):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # 超出热表容量的部分先落归档,归档成功才允许从热表移除。归档失败就整份保留,
    # 宁可热表暂时超长,也不能让记录凭空消失。
    kept = items
    if len(items) > HISTORY_MAX_ITEMS:
        if _archive_overflow(items[HISTORY_MAX_ITEMS:]):
            kept = items[:HISTORY_MAX_ITEMS]
    payload = json.dumps(kept, ensure_ascii=False, indent=2)
    # 先写临时文件再原子替换,进程中途崩溃不会留下半截 JSON 拖垮整个历史库
    with HISTORY_LOCK:
        descriptor, temp_path = tempfile.mkstemp(dir=HISTORY_DIR, prefix=".meetings-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_path, HISTORY_FILE)
            # 显式失效,不单靠 mtime 变化:某些文件系统的时间戳精度较粗,
            # 同一秒内的连续写入可能拿到相同的 (mtime, size),缓存就会读到旧数据。
            with _CACHE_LOCK:
                global _cache_key
                _cache_key = None
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
            raise


TITLE_INVALID_MARKERS = ("未提及", "待确认", "未命名", "待补充", "暂无", "未知")


TITLE_TEMPLATE_LINES = {
    "会议纪要", "会 议 纪 要", "管理简报", "灵犀管理简报", "个人备忘录",
    "个人备忘录总结", "面试记录与候选人反馈表", "客户拜访会议纪要",
    "智能识别场景路由", "结构化纪要", "会议内容",
}


def _title_candidate(value, min_len=2):
    value = re.sub(r"[#*`>【】\[\]]+", "", str(value or "")).strip().strip(" -：:|·，,。")
    compact = value.replace(" ", "")
    if not compact or len(compact) < min_len:
        return None
    if compact in TITLE_TEMPLATE_LINES or compact in {"无", "待定", "N/A", "n/a"}:
        return None
    if any(marker in compact[:6] for marker in TITLE_INVALID_MARKERS):
        return None
    return value[:28]


def extract_meeting_title(summary_text, transcript_text="", overview_text=""):
    patterns = [
        r"会议名称[：:]\s*([^\n]+)",
        r"会议主题[：:]\s*([^\n]+)",
        r"记录主题[：:]\s*([^\n]+)",
        r"候选人[：:]\s*([^\n]+)",
        r"拜访客户[：:]\s*([^\n]+)",
        r"项目名称[：:]\s*([^\n]+)",
        r"灵犀管理简报\s*([^\n]*)",
    ]
    sources = [str(source or "").replace("#", "").replace("*", "").strip() for source in (summary_text, overview_text)]
    for text in sources:
        if not text:
            continue
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                title = _title_candidate(match.group(1))
                if title:
                    return title
    for text in sources:
        for line in text.splitlines():
            line = line.strip()
            # 键值字段行（如“项目名称：未提及”）已在上面处理，不作为标题回退。
            if re.match(r"^[^\n：:]{1,10}[：:]", line):
                continue
            if re.match(r"^[一二三四五六七八九十]+、", line) or "等待处理" in line:
                continue
            title = _title_candidate(line, min_len=6)
            if title:
                return title
    transcript = re.sub(r"\s+", " ", str(transcript_text or "")).strip()
    if transcript:
        title = _title_candidate(transcript[:28], min_len=6)
        if title:
            return title
    return "未命名会议"


def generate_meeting_title(transcript, summary="", overview="", app_mode="auto"):
    """History titles: rule extraction first, tiny-model naming as fallback."""
    title = extract_meeting_title(summary, transcript, overview)
    if title != "未命名会议":
        return title
    snippet = compact_text(transcript, 900) or compact_text(summary, 900)
    if snippet:
        try:
            raw = call_llm(
                "为下面的会议内容起一个客观、具体的中文标题，不超过14个字。只输出标题本身，不要引号或句号。\n"
                + SOURCE_GUARD + "\n" + fence_source(snippet),
                timeout=int(os.getenv("JKINCO_TITLE_TIMEOUT", "15")),
                model_name=os.getenv("JKINCO_TITLE_MODEL", ""),
                fallback_model="",
                thinking=False,
                temperature=0.2,
            )
            raw = str(raw or "").strip().strip("《》\"'“”。").splitlines()[0].strip()[:20]
            candidate = _title_candidate(raw)
            if candidate:
                return candidate
        except Exception as error:
            LOGGER.warning("智能标题生成不可用,使用场景兜底:%s", error)
    return f"{mode_label(app_mode)}·{time.strftime('%m月%d日')}"


def history_time_label(timestamp):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return "刚刚"


def history_choices(query=""):
    query = str(query or "").strip().lower()
    choices = []
    for item in load_meeting_history():
        haystack = "\n".join([
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("overview", "")),
            str(item.get("transcript", "")),
            str(item.get("mode_label", "")),
        ]).lower()
        if query and query not in haystack:
            continue
        title = item.get("title") or "未命名会议"
        label = f"{title}｜{item.get('mode_label', '智能')}｜{history_time_label(item.get('created_at'))}"
        choices.append((label, item.get("id")))
    return choices


def normalize_usernames(values, exclude=""):
    """去重、去空并剔除 owner 自己,保持传入顺序。

    shared_usernames 只做「可见性名单」用,owner 已单独存字段,重复存会让
    两处口径漂移;顺序保留是为了让回填与新写入产生一致的 JSON,便于 diff。
    """
    exclude = str(exclude or "").strip()
    result = []
    for value in values or []:
        name = str(value or "").strip()
        if not name or name == exclude or name in result:
            continue
        result.append(name)
    return result


def save_meeting_history_record(
    transcript,
    summary,
    dingtalk_status,
    app_mode="auto",
    source="录音输入",
    overview="",
    record_id=None,
    owner_username="",
    shared_usernames=None,
    classification=None,
    custom_template_id="",
    custom_template_name="",
):
    transcript = str(transcript or "").strip()
    summary = str(summary or "").strip()
    if transcript.startswith(("请先选择", "❌", "实时转写暂不可用")):
        return None
    if not transcript and not summary:
        return None
    if summary in {"*等待处理...*", "等待处理..."}:
        return None

    now = time.time()
    # 新记录的 id 必须全局唯一:写入时按 id 去重(见下方 items 组装),
    # 纯毫秒时间戳在并发下会碰撞,后写的会静默覆盖先写的整条记录。
    # 上传转写与会议归档都跑在线程池里,同毫秒完成完全可能,故补随机后缀。
    # 该 id 全程作为不透明主键使用,没有任何代码解析其格式。
    record_id = record_id or f"meeting-{int(now * 1000)}-{uuid.uuid4().hex[:8]}"
    title = generate_meeting_title(transcript, summary, overview, app_mode)
    with HISTORY_LOCK:
        # 读-改-写:读不出来必须让本次保存失败,而不是把历史覆盖成只剩这一条
        existing_items = load_meeting_history_for_update()
        existing = next((item for item in existing_items if item.get("id") == record_id), {})
        owner_username = str(owner_username or existing.get("owner_username") or "").strip()
        # None 表示「本次调用不涉及名单」(上传转写、人工校核回写),沿用已有名单;
        # 传空列表才是显式清空。
        shared = normalize_usernames(
            existing.get("shared_usernames") if shared_usernames is None else shared_usernames,
            owner_username,
        )
        classification_record = dict(existing.get("classification") or {})
        if classification is not None:
            classification_record.update(dict(classification))
        stored_template_id = str(custom_template_id or existing.get("custom_template_id") or "")
        stored_template_name = str(custom_template_name or existing.get("custom_template_name") or "")
        record = {
            "id": record_id,
            "title": title,
            "created_at": now,
            "mode": app_mode or "auto",
            "mode_label": history_mode_label(app_mode, dingtalk_status),
            "source": source,
            "transcript": transcript,
            "summary": summary,
            "overview": str(overview or "").strip(),
            "dingtalk_status": str(dingtalk_status or "").strip(),
            "owner_username": owner_username,
            # 实时会议的参会成员:他们在「最近会议」里本就能读到同一份纪要,
            # 历史会议列表若只认 owner,同一场会在两个入口下的可见性就自相矛盾。
            "shared_usernames": shared,
        }
        if classification_record:
            record["classification"] = classification_record
        if stored_template_id:
            record["custom_template_id"] = stored_template_id
            record["custom_template_name"] = stored_template_name
        items = [record] + [item for item in existing_items if item.get("id") != record_id]
        write_meeting_history(items)
    return record_id


def _history_visible_to(item, owner_username=""):
    if not owner_username:
        return True
    owner = str(item.get("owner_username") or "").strip()
    if owner == owner_username or (owner_username == "admin" and not owner):
        return True
    return owner_username in normalize_usernames(item.get("shared_usernames"))
