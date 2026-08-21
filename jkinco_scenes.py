"""筑听场景/模式分类的纯函数集合。

从 JKincoListen.py 单体中抽出的第一个独立模块。这里只做「模式标识 → 语义」的
判断与映射,不涉及任何 I/O、状态或对其它引擎函数的依赖,因此可以独立测试、
独立演进。JKincoListen.py 通过 re-import 保持向后兼容,所有历史调用点(core.xxx)
行为完全不变。
"""
from __future__ import annotations


def is_lingxi_mode(app_mode) -> bool:
    return app_mode in {"lingxi", "灵犀智能体", "管理简报"}


def is_talk_mode(app_mode) -> bool:
    return app_mode in {"talk", "筑言", "工程例会", "工程会议纪要"}


def is_general_mode(app_mode) -> bool:
    # 旧版“灵犀/管理简报”统一迁移为无固定模板的通用会议纪要。
    return app_mode in {"general", "通用会议纪要", "通用纪要", "会议纪要"} or is_lingxi_mode(app_mode)


def is_personal_mode(app_mode) -> bool:
    return app_mode == "personal" or app_mode == "个人助手"


def is_interview_mode(app_mode) -> bool:
    return app_mode == "interview" or app_mode == "面试记录"


def is_customer_visit_mode(app_mode) -> bool:
    return app_mode == "customer_visit" or app_mode == "客户拜访"


def is_auto_mode(app_mode) -> bool:
    return app_mode in (None, "", "auto", "智能识别")


def canonical_mode(app_mode) -> str:
    """把兼容别名收敛成 API 与持久化层使用的六个稳定场景值。

    场景值会同时影响 ASR 词表、分类旁路和纪要模板，不能让拼错或未知值
    静默落入任意分支。保留中文与旧版灵犀别名，只在确实无法识别时拒绝。
    """
    if is_auto_mode(app_mode):
        return "auto"
    if is_talk_mode(app_mode):
        return "talk"
    if is_general_mode(app_mode):
        return "general"
    if is_personal_mode(app_mode):
        return "personal"
    if is_interview_mode(app_mode):
        return "interview"
    if is_customer_visit_mode(app_mode):
        return "customer_visit"
    raise ValueError("不支持的会议场景")


def mode_label(app_mode) -> str:
    if is_talk_mode(app_mode):
        return "工程例会"
    if is_general_mode(app_mode):
        return "通用会议纪要"
    if is_personal_mode(app_mode):
        return "个人助手"
    if is_interview_mode(app_mode):
        return "面试记录"
    if is_customer_visit_mode(app_mode):
        return "客户拜访"
    return "智能识别"


def output_title(app_mode) -> str:
    if is_general_mode(app_mode):
        return "会议纪要"
    if is_personal_mode(app_mode):
        return "个人备忘录"
    if is_interview_mode(app_mode):
        return "面试记录与候选人反馈表"
    if is_customer_visit_mode(app_mode):
        return "客户拜访会议纪要"
    return "会 议 纪 要"


def history_mode_label(app_mode, status_text: str = "") -> str:
    """历史记录里显示的场景名。

    必须与 mode_label 同源 —— 两者是同一个东西的两个显示位置(场景页签 / 历史列表)。
    此前这里对工程例会返回「会议纪要」,而 mode_label 返回「工程例会」:用户按
    工程例会录的会,在历史列表里显示成另一个名字。生产 86 条记录里 26 条对不上,
    其中 11 条正是这条分支造成的,而且一直在产生新的(最近一条 8 月 6 日)。
    前端甚至为此打过一个专门绕开 lingxi 的补丁,说明这个漂移已经被感知到了。

    status_text 只在场景本身判不出来(auto/未知)时才用作线索:它是一段给人看的
    中文状态文案,拿它反推场景很脆弱 —— 生产里就有 mode=general 却因为状态里出现
    「客户拜访」四个字而被标成客户拜访的记录。场景已知时以场景为准。
    """
    label = mode_label(app_mode)
    if label != "智能识别":
        return label
    status_text = str(status_text or "")
    for keyword, guessed in (
        ("个人助手", "个人助手"),
        ("面试记录", "面试记录"),
        ("客户拜访", "客户拜访"),
        ("工程例会", "工程例会"),
        ("其他会议", "通用会议纪要"),
    ):
        if keyword in status_text:
            return guessed
    return "智能"
