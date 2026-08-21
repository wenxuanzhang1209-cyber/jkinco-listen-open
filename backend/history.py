"""历史会议记录的可见性与序列化。

从 backend/main.py 抽出。这两个函数被 backend/meetings.py 用到(会议结束后要把
归档的历史记录回显给前端),而 main.py 又 import meetings 注册路由 —— 原先只能
靠函数体内延迟导入绕开循环。它们只依赖引擎与身份层,独立后依赖恢复单向。
"""
from __future__ import annotations

from typing import Any

from backend import core
from backend.auth import is_admin


def _display_mode_label(item: dict[str, Any]) -> str:
    """展示用的场景名:场景能判出来就按场景,判不出来才用落库的那份。

    auto/空场景没有规范名字,此时存下的标签(当时按状态文本猜的)仍比
    「智能识别」有信息量,予以保留。
    """
    derived = core.mode_label(item.get("mode"))
    if derived != "智能识别":
        return derived
    return item.get("mode_label") or derived


def serialize_history(
    item: dict[str, Any],
    compact: bool = False,
    viewer: str | None = None,
    admin: bool | None = None,
) -> dict[str, Any]:
    """compact 用于列表:卡片只展示标题和摘要片段,完整转写由详情接口按需返回,
    否则 80 条 × 全文转写的列表响应会到 MB 级。

    viewer 给出时附带 read_only,前端据此决定是否显示「保存校核稿 / 推送钉钉」。
    不给 viewer 时不带该字段,保持老调用方的响应结构不变。

    admin 给列表接口用:它在最开始就已经判定过一次管理员身份,不传的话这里每条
    记录都会再查一次账号库(每次新建 sqlite 连接)—— 59 条记录实测 29.6ms,
    占整个 /api/history 的八成。不传时行为不变,仍自行判定。
    """
    transcript = item.get("transcript") or ""
    summary = item.get("summary") or ""
    overview = item.get("overview") or ""
    if compact:
        transcript = ""
        summary = summary[:400]
        overview = overview[:400]
    payload = {
        "id": item.get("id"),
        "title": item.get("title") or "未命名会议",
        "created_at": item.get("created_at"),
        "mode": item.get("mode") or "auto",
        # 场景已知时按场景推导,不用落库时存下的那份标签。存量数据里有 26/86 条
        # 两者对不上(改名前留下的「会议纪要」「灵犀」,以及按状态文本猜错的
        # 「客户拜访」)—— 同一场会在场景页签和历史列表里叫两个名字。
        # 按场景推导对所有存量取值都成立(含遗留的 lingxi -> 通用会议纪要),
        # 而且不改动落库内容:存下的原值仍在文件里,只是不再用于展示。
        "mode_label": _display_mode_label(item),
        "source": item.get("source") or "录音输入",
        "transcript": transcript,
        "summary": summary,
        "overview": overview,
        "status": item.get("dingtalk_status") or "",
        "custom_template_id": item.get("custom_template_id") or "",
        "custom_template_name": item.get("custom_template_name") or "",
    }
    if not compact and isinstance(item.get("classification"), dict):
        # 详情页保留自动判断、模型理由与人工纠正事件，列表页不携带这批审计数据。
        # 这让后续能按 auto-only 样本计算混淆矩阵，而不是从最终 mode 猜原判定。
        payload["classification"] = dict(item["classification"])
    if viewer is not None:
        payload["read_only"] = not history_editable_by(item, viewer, admin)
    return payload


def history_visible_to(item: dict[str, Any], username: str, admin: bool | None = None) -> bool:
    """读权限:所有者、被共享的参会成员、管理员。

    实时会议归档时会把参会成员写进 shared_usernames。这些人在「最近会议」里
    (list_meetings / _require_member 的口径)本来就能读到同一份纪要,历史列表
    若只认 owner,同一场会在两个入口下就一个看得到、一个看不到。
    """
    if admin is None:
        admin = is_admin(username)
    if admin:
        return True
    username = str(username or "").strip()
    if not username:
        return False
    owner = str(item.get("owner_username") or "").strip()
    if owner and owner == username:
        return True
    return username in history_shared_usernames(item)


def history_shared_usernames(item: dict[str, Any]) -> list[str]:
    values = item.get("shared_usernames")
    if not isinstance(values, list):
        return []
    return [name for name in (str(value or "").strip() for value in values) if name]


def history_editable_by(item: dict[str, Any], username: str, admin: bool | None = None) -> bool:
    """写权限比读权限窄一档:只有所有者和管理员能改。

    参会成员能看、能导出,但不能覆盖主持人定稿 —— 一场会有多个参会者,
    谁都能改就没有「定稿」可言了。
    """
    if admin is None:
        admin = is_admin(username)
    if admin:
        return True
    username = str(username or "").strip()
    owner = str(item.get("owner_username") or "").strip()
    return bool(username) and owner == username
