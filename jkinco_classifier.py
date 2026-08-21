"""会议场景智能识别。

从 JKincoListen.py 单体抽出的核心业务逻辑:基于工程证据门控的关键词规则打分,
叠加小模型语义复核,在工程例会/通用会议/个人助手/面试记录/客户拜访之间路由。
设计要点:模型不能绕过工程证据门控强制套用工程模板,证据不足时保守归入通用会议纪要。
JKincoListen.py 通过 re-import 保持向后兼容。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from jkinco_llm import call_llm
from jkinco_prompts import SOURCE_GUARD, fence_source
from jkinco_scenes import (
    is_auto_mode,
    is_customer_visit_mode,
    is_general_mode,
    is_interview_mode,
    is_personal_mode,
    is_talk_mode,
    mode_label,
)


ENGINEERING_KEYWORDS = {
    "工程例会": 8, "工程会议": 8, "工程会议纪要": 8, "监理例会": 8,
    "工地例会": 8, "现场例会": 7, "项目例会": 6, "专题例会": 5,
    "施工单位": 8, "监理单位": 8, "建设单位": 8, "业主单位": 8,
    "业主": 5, "甲方": 4, "建设方": 5, "设计单位": 7, "勘察单位": 6,
    "代建单位": 6, "投资监理": 6, "总包": 6, "总包单位": 8,
    "分包": 5, "专业分包": 7, "专业分包单位": 8, "各专业单位": 6,
    "施工现场": 4, "施工进度": 4, "进度控制": 4, "质量控制": 4, "安全控制": 4,
    "安全巡视": 4, "桩基": 4, "围护": 3, "基坑": 3, "塔吊": 3, "脚手架": 3,
    "钢筋": 3, "混凝土": 3, "方案报审": 3, "图纸": 2, "签证": 2, "整改": 2,
    "闭环": 2, "扬尘": 2, "临电": 2, "班组": 2, "施工人员": 2, "项目名称": 2,
    "会议签到表": 2, "五方验收": 8, "竣工验收": 8, "工程验收": 8,
    "项目验收": 7, "竣工资料": 8, "资料归档": 7, "验收资料": 6,
    "资料提交": 4, "返工风险": 4, "监理通知单": 5, "联系单": 3,
    "工程量": 3, "工程款": 3, "设计变更": 4, "图纸会审": 5,
}


NON_ENGINEERING_KEYWORDS = {
    "销售": 3, "市场": 3, "产品": 3, "研发": 3, "财务": 3, "人事": 3,
    "行政": 3, "运营": 3, "客户": 3, "合同": 2, "预算": 2, "招聘": 2,
    "绩效": 2, "渠道": 2, "品牌": 2, "推广": 2, "部门": 2, "管理层": 2,
    "汇报": 1, "复盘": 1, "战略": 1,
}


PERSONAL_ASSISTANT_KEYWORDS = {
    "个人备忘": 10, "备忘录": 8, "工作复盘": 8, "个人复盘": 8,
    "今天": 1, "明天": 1, "待办": 3, "提醒": 5, "总结": 1,
    "记录要点": 6, "个人助手": 10, "事项跟进": 7, "优先事项": 7,
}


INTERVIEW_KEYWORDS = {
    "面试": 10, "候选人": 10, "HR": 8, "招聘": 7, "应聘": 8,
    "岗位": 4, "简历": 5, "录用": 8, "复面": 8, "薪资": 5,
    "到岗": 5, "稳定性": 5, "能力评价": 7, "用人部门": 6,
}


CUSTOMER_VISIT_KEYWORDS = {
    "客户拜访": 10, "拜访客户": 10, "客户方": 7, "客户诉求": 8,
    "客户反馈": 8, "合作意向": 7, "拜访背景": 8, "需求调研": 7,
    "后续跟进": 5, "第二轮沟通": 6, "试点": 4, "方案沟通": 6,
}


ENGINEERING_ROLE_KEYWORDS = [
    "施工单位", "监理单位", "建设单位", "业主单位", "业主", "甲方", "建设方",
    "设计单位", "勘察单位", "代建单位", "投资监理", "总包", "总包单位",
    "分包", "专业分包", "专业分包单位", "各专业单位", "项目经理",
    "项目负责人", "总监", "总监理工程师", "监理工程师", "业主代表",
]


ENGINEERING_TRADE_KEYWORDS = [
    "土建", "桩基", "围护", "基坑", "支护", "降水", "塔吊", "脚手架",
    "钢筋", "模板", "混凝土", "砌体", "抹灰", "防水", "幕墙", "精装",
    "装饰", "机电", "水电", "暖通", "消防", "弱电", "强电", "智能化",
    "给排水", "管线", "市政", "道路", "绿化", "外立面", "结构", "建筑",
]


ENGINEERING_MANAGEMENT_KEYWORDS = [
    "施工", "现场", "进度", "工期", "节点", "质量", "安全", "文明施工",
    "扬尘", "临电", "整改", "闭环", "旁站", "巡视", "验收", "报验",
    "检验批", "隐蔽验收", "材料进场", "材料报审", "方案报审", "施工方案",
    "专项方案", "周计划", "月计划", "劳动力", "班组", "作业面", "机械",
]


ENGINEERING_DOCUMENT_KEYWORDS = [
    "竣工资料", "资料归档", "验收资料", "资料提交", "五方验收", "竣工验收",
    "工程验收", "项目验收", "图纸", "蓝图", "图纸会审", "设计变更",
    "签证", "联系单", "监理通知单", "会议签到表", "施工组织设计",
]


# 「工程例会」不是「文本里出现了两个工程词」。用户提供的 56 场独立例会里，
# 核心三方、多个管理议程、周度复盘和责任指令是稳定结构；五方完整发言只占少数。
# 这里把证据拆成互相独立的族，避免旧逻辑从全文任意位置拼出
# 「一个角色 + 一个泛化动作」后直接锁死工程模板。
ENGINEERING_ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "contractor": (
        "施工单位", "总承包单位", "总包单位", "总包", "施工项目部",
        "专业分包单位", "专业分包", "分包单位", "施工方", "总包方",
        "承包方", "乙方",
    ),
    "supervision": (
        "监理单位", "监理项目部", "监理方", "总监理工程师", "专业监理工程师", "总监",
    ),
    "owner": (
        "建设单位", "代建单位", "业主单位", "建设方", "业主方", "代建方",
        "甲方", "业主代表",
    ),
    "design": ("设计单位", "设计院", "设计方"),
    "survey": ("勘察单位", "勘察方"),
    "investment_supervision": ("投资监理",),
    # “五方验收”本身代表多方责任主体，但仍必须再有动作或另一份交付物，不能像
    # 旧版那样同时算作角色和资料、靠一个短语完成两类独立证据。
    "five_party": ("五方责任主体", "五方验收"),
}

ENGINEERING_CONCRETE_ACTIONS: tuple[str, ...] = (
    "钢筋绑扎", "绑钢筋", "模板安装", "模板支撑", "支模", "拆模",
    "混凝土浇筑", "浇筑混凝土", "浇砼", "打砼", "排架搭设",
    "土方开挖", "基坑开挖", "基坑支护", "桩基施工", "打桩", "灌注桩",
    "塔吊安装", "塔吊拆除", "塔吊附着", "脚手架搭设", "脚手架拆除",
    "砌体施工", "幕墙安装", "吊顶施工", "管线安装", "桥架安装", "风管安装",
    "管道试压", "防水施工", "临边防护", "洞口防护", "隐蔽验收",
    "检验批", "见证取样", "送检", "复试", "报验", "旁站", "材料进场",
    "现场巡视", "现场检查", "现场整改", "整改回复", "复查", "复验",
    "动火作业", "临时用电", "临电检查", "临电整改", "隐验",
    "申请复工", "按图施工",
)

ENGINEERING_OBJECTS: tuple[str, ...] = (
    "钢筋", "模板", "混凝土", "砼", "排架", "基坑", "支护", "桩基", "灌注桩",
    "塔吊", "脚手架", "临边", "洞口", "砌体", "幕墙", "吊顶", "龙骨",
    "地下室", "楼层", "楼栋", "屋面", "外立面", "施工电梯", "人货梯",
    "风管", "桥架", "给排水", "消防管道", "喷淋", "消火栓", "配电箱",
    "施工图", "施工蓝图", "作业面",
)

ENGINEERING_DELIVERABLES: tuple[str, ...] = (
    "施工组织设计", "专项施工方案", "专项方案", "施工方案", "施工蓝图",
    "设计图纸", "施工图纸", "图纸会审", "监理通知单", "整改回复单",
    "竣工资料", "验收资料", "技术资料", "复试报告", "型式报告",
    "工程签证", "设计变更", "工程联系单", "工程款", "进度款",
    "五方验收", "竣工验收", "会签", "报审报验表",
)

ENGINEERING_AGENDA_GROUPS: dict[str, tuple[str, ...]] = {
    "progress": ("施工进度", "进度控制", "工期", "节点工期", "进度计划", "完成情况", "工作安排"),
    "quality": ("质量控制", "质量管理", "质量问题", "验收", "送检", "复试", "检验批"),
    "safety": ("安全控制", "安全管理", "安全检查", "安全隐患", "文明施工", "临时用电", "消防", "临边防护"),
    "resources": ("资源配置", "人员配置", "劳动力", "班组", "机械设备", "施工人员", "管理人员"),
    "technical": ("技术资料", "图纸", "施工方案", "专项方案", "报审", "设计变更", "图纸会审"),
    "coordination": ("协调问题", "需要协调", "工序衔接", "界面移交", "交叉施工"),
}

ENGINEERING_CYCLE_MARKERS: tuple[str, ...] = (
    "上周", "本周", "下周", "上期", "本期", "下期", "周计划", "月计划",
    "完成情况", "工作安排",
)

ENGINEERING_COORDINATION_MARKERS: tuple[str, ...] = (
    "汇报", "提出", "要求", "请", "责令", "督促", "负责", "必须", "应当",
    "需要", "整改", "回复", "报送", "提交", "报审", "完成", "计划", "安排",
    "补齐", "落实", "复查", "验收", "协调",
)

EXPLICIT_ENGINEERING_MEETINGS: tuple[str, ...] = (
    "工程例会", "监理例会", "工地例会", "施工例会", "现场工程例会",
)

TOPIC_MEETING_MARKERS: tuple[str, ...] = (
    "专题会议", "专题会", "专项会议", "安全专题", "质量专题", "技术专题",
    "专项培训会", "安全培训会", "质量培训会", "事故分析会", "事故处置会",
    "应急处置会", "图纸会审会", "图纸会审会议", "图纸会审协调会",
    "工程款支付协调会", "支付协调会", "设计协调会", "变更协调会",
)

GENERAL_CONTEXT_GROUPS: dict[str, tuple[str, ...]] = {
    "declared_general": (
        "通用例会", "通用周会", "公司经营周会", "经营周会", "经营例会",
        "经营分析会", "跨部门项目周会", "跨部门周会", "公司周会",
        "部门周会", "部门例会", "行政例会", "管理例会", "物业项目周会",
        "物业周会", "产品周会", "研发周会",
    ),
    "training": (
        "培训议程", "培训课程", "课程示例", "培训讲解", "授课内容",
        "课堂讲解", "教学案例", "考试题目",
    ),
    "product": (
        "产品项目", "产品研发", "研发周会", "软件项目", "软件研发",
        "平台功能", "系统功能", "版本上线", "算法模型", "大模型",
        "账号安全", "产品团队",
    ),
    "administration": (
        "行政部门", "人事部门", "财务部门", "财务预算",
        "绩效考核", "员工排班", "值班排班", "管理层例会", "总部例会",
        "公司内部例会",
    ),
    "property_operations": ("物业维修周会", "物业工程维修周会", "运维周会", "设施维修周会"),
    "quoted_or_historical": (
        "转述", "术语", "这些词", "ASR错误", "识别功能", "功能说明", "系统应输出",
        "新闻稿", "宣传稿", "报道稿", "示例文本", "案例复述",
    ),
}


@dataclass(frozen=True)
class EngineeringEvidence:
    """可解释的工程例会证据；score 是规则证据分，不是模型自报概率。"""

    role_groups: tuple[str, ...]
    role_terms: tuple[str, ...]
    attributed_roles: tuple[str, ...]
    actions: tuple[str, ...]
    objects: tuple[str, ...]
    deliverables: tuple[str, ...]
    agendas: tuple[str, ...]
    cycle_markers: tuple[str, ...]
    coordination_markers: tuple[str, ...]
    explicit_meetings: tuple[str, ...]
    negative_contexts: tuple[str, ...]
    evidence_windows: int
    quantitative_markers: int
    engineering_clause_ratio: float
    topic_meeting: bool
    blocked: bool
    candidate: bool
    strong: bool
    score: int


def _group_hits(text: str, groups: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(name for name, terms in groups.items() if has_any(text, terms))


def _longest_keyword_hits(text: str, keywords) -> tuple[str, ...]:
    """去掉同一长词造成的子串重复；证据数量不能靠“分包/专业分包”虚增。"""
    picked: list[str] = []
    for keyword in sorted(set(keywords), key=len, reverse=True):
        if keyword not in text:
            continue
        if any(keyword in existing for existing in picked):
            continue
        picked.append(keyword)
    return tuple(picked)


def _role_terms(text: str) -> tuple[str, ...]:
    terms = [
        term
        for variants in ENGINEERING_ROLE_GROUPS.values()
        for term in variants
        if has_any(text, (term,))
    ]
    return _longest_keyword_hits(text, terms)


def _attributed_role_groups(text: str) -> tuple[str, ...]:
    attributed: list[str] = []
    verbs = "汇报|提出|要求|强调|回复|表示|安排|负责|应|需|必须|报送|整改"
    for group, terms in ENGINEERING_ROLE_GROUPS.items():
        if any(
            re.search(
                rf"{('施工方(?!案)' if term == '施工方' else re.escape(term))}.{{0,8}}(?:[：:]|{verbs})",
                text,
            )
            or re.search(
                rf"(?:请|要求|责令|督促).{{0,12}}{('施工方(?!案)' if term == '施工方' else re.escape(term))}",
                text,
            )
            for term in terms
        ):
            attributed.append(group)
    return tuple(attributed)


def _has_weekly_cycle(markers: tuple[str, ...]) -> bool:
    marker_set = set(markers)
    return (
        {"上周", "本周", "下周"}.issubset(marker_set)
        or ({"本周", "下周"}.issubset(marker_set))
        or len(marker_set) >= 3
    )


def _classification_sample(transcript: str, limit: int = 18000) -> str:
    """长转写等距取首、中、尾，避免只看开场白或只看前 6,000 字。"""
    source = str(transcript or "")
    if len(source) <= limit:
        return source
    chunk = max(1, limit // 3)
    middle_start = max(chunk, (len(source) - chunk) // 2)
    return "\n".join((
        source[:chunk],
        source[middle_start:middle_start + chunk],
        source[-chunk:],
    ))


def analyze_engineering_evidence(transcript: str) -> EngineeringEvidence:
    """按角色、议程、工程实体、周期和责任链分析，缺证据时允许拒识。"""
    source = _classification_sample(transcript)
    normalized = re.sub(r"\s+", " ", source).strip()
    compact = re.sub(r"\s+", "", source)
    # 会议类型通常在标题/开场交代。只在开场识别“专题会/工程例会”，避免一场
    # 正常例会后半段提到“另开安全专题会”就被整体改类，也避免产品文稿在正文
    # 引用“工程例会”三个字获得显式会议加分。
    opening = compact[:1200]
    clauses = [
        clause.strip()
        for clause in re.split(r"[。！？!?；;\n]+", normalized)
        if len(clause.strip()) >= 4
    ]

    role_groups = _group_hits(compact, ENGINEERING_ROLE_GROUPS)
    role_terms = _role_terms(compact)
    attributed_roles = _attributed_role_groups(compact)
    actions = _longest_keyword_hits(compact, ENGINEERING_CONCRETE_ACTIONS)
    objects = _longest_keyword_hits(compact, ENGINEERING_OBJECTS)
    deliverables = _longest_keyword_hits(compact, ENGINEERING_DELIVERABLES)
    agendas = _group_hits(compact, ENGINEERING_AGENDA_GROUPS)
    cycle_markers = _longest_keyword_hits(compact, ENGINEERING_CYCLE_MARKERS)
    coordination_markers = _longest_keyword_hits(compact, ENGINEERING_COORDINATION_MARKERS)
    explicit_meetings = _longest_keyword_hits(opening, EXPLICIT_ENGINEERING_MEETINGS)
    opening_negative_contexts = _group_hits(opening, GENERAL_CONTEXT_GROUPS)
    quoted_contexts = (
        ("quoted_or_historical",)
        if has_any(compact, GENERAL_CONTEXT_GROUPS["quoted_or_historical"])
        else ()
    )
    negative_contexts = tuple(dict.fromkeys((*opening_negative_contexts, *quoted_contexts)))
    topic_meeting = has_any(opening, TOPIC_MEETING_MARKERS)

    evidence_windows = 0
    engineering_clauses = 0
    for clause in clauses:
        clause_compact = re.sub(r"\s+", "", clause)
        clause_roles = _group_hits(clause_compact, ENGINEERING_ROLE_GROUPS)
        clause_actions = _longest_keyword_hits(clause_compact, ENGINEERING_CONCRETE_ACTIONS)
        clause_objects = _longest_keyword_hits(clause_compact, ENGINEERING_OBJECTS)
        clause_deliverables = _longest_keyword_hits(clause_compact, ENGINEERING_DELIVERABLES)
        clause_coordination = _longest_keyword_hits(clause_compact, ENGINEERING_COORDINATION_MARKERS)
        if clause_roles or clause_actions or clause_objects or clause_deliverables:
            engineering_clauses += 1
        if clause_coordination and (
            (clause_roles and (clause_actions or clause_objects or clause_deliverables))
            or (clause_actions and clause_objects)
        ):
            evidence_windows += 1

    engineering_clause_ratio = engineering_clauses / max(1, len(clauses))
    quantitative_markers = min(
        6,
        len(re.findall(r"\d+(?:\.\d+)?\s*(?:%|％|人|台|根|层|栋|米|天|项)", compact)),
    )
    field_dimensions = sum(bool(items) for items in (actions, objects, deliverables))
    weekly_cycle = _has_weekly_cycle(cycle_markers)
    non_five_party_deliverables = tuple(item for item in deliverables if item != "五方验收")
    five_party_completion = (
        "five_party" in role_groups
        and bool(non_five_party_deliverables or len(actions) >= 2)
        and len(coordination_markers) >= 1
    )

    # 工程专题会、培训/产品/行政转述以及明确否定，是高频硬负例。它们即使完整
    # 复述了角色、五类议程和上本下周，仍然不是周期性工程例会，不能靠“结构很像”
    # 解除反证。开场同时明确自称工程例会时，允许后文包含一个专题议程。
    explicit_negation = bool(re.search(
        r"(?:不是|不属于).{0,10}(?:工程例会|监理例会|工地例会|工程会议|施工现场协调)"
        r"|(?:不涉及|未涉及|不讨论).{0,10}(?:施工现场|工程施工|监理通知单|工程项目)"
        r"|不是施工单位施工现场协调",
        compact,
    ))
    regular_structure = (
        len(role_groups) >= 2
        and len(agendas) >= 3
        and weekly_cycle
        and field_dimensions >= 2
        and len(coordination_markers) >= 2
    )
    narrow_topic = topic_meeting and not explicit_meetings
    low_engineering_share = len(clauses) >= 4 and engineering_clause_ratio < 0.34
    hard_negative_context = any(context != "administration" for context in negative_contexts)
    administration_block = "administration" in negative_contexts and not (
        bool(explicit_meetings) and regular_structure and engineering_clause_ratio >= 0.50
    )
    blocked = (
        narrow_topic
        or explicit_negation
        or hard_negative_context
        or administration_block
        or (low_engineering_share and not explicit_meetings)
    )

    candidate = not blocked and bool(
        (len(role_groups) >= 2 and field_dimensions >= 1 and (attributed_roles or coordination_markers))
        or (len(role_groups) >= 1 and field_dimensions >= 2 and len(coordination_markers) >= 1)
        or (len(actions) >= 2 and len(objects) >= 2 and deliverables and len(coordination_markers) >= 2)
        or (bool(explicit_meetings) and field_dimensions >= 1 and (role_groups or len(agendas) >= 2))
        or five_party_completion
    )

    compact_multi_role = (
        bool(explicit_meetings)
        and len(role_groups) >= 2
        and len(attributed_roles) >= 2
        and field_dimensions >= 2
        and evidence_windows >= 1
        and len(coordination_markers) >= 2
    )
    dense_field_control = (
        len(role_groups) >= 1
        and field_dimensions == 3
        and len(actions) >= 2
        and len(objects) >= 2
        and deliverables
        and len(agendas) >= 3
        and evidence_windows >= 2
        and len(coordination_markers) >= 2
        and (weekly_cycle or quantitative_markers >= 2)
    )
    speakerless_dense = (
        not role_groups
        and field_dimensions == 3
        and len(actions) >= 3
        and len(objects) >= 3
        and deliverables
        and len(agendas) >= 3
        and weekly_cycle
        and len(coordination_markers) >= 2
    )
    explicit_supported = (
        bool(explicit_meetings)
        and len(role_groups) >= 1
        and len(agendas) >= 2
        and field_dimensions >= 2
        and len(coordination_markers) >= 1
    )
    closeout_regular = (
        bool(explicit_meetings)
        and len(role_groups) >= 3
        and len(agendas) >= 3
        # 收尾阶段的周会常常不再谈具体工序，只剩本周销项、五方验收和竣工资料。
        # 有明确例会名称、多方责任链与多议程时，一个周期标记即可，不强求上本下周齐全。
        and bool(cycle_markers)
        and len(deliverables) >= 2
        and evidence_windows >= 2
    )
    strong = not blocked and (
        regular_structure
        or compact_multi_role
        or dense_field_control
        or speakerless_dense
        or explicit_supported
        or closeout_regular
    )

    score = 0
    score += min(24, len(agendas) * 5) + (6 if weekly_cycle else 0)
    score += min(12, len(role_groups) * 4) + min(8, len(attributed_roles) * 4)
    score += min(8, len(actions) * 2) + min(6, len(objects)) + min(6, len(deliverables) * 2)
    score += min(10, len(coordination_markers) * 2) + min(8, evidence_windows * 4)
    score += min(6, quantitative_markers) + (4 if explicit_meetings else 0)
    if negative_contexts:
        score -= min(18, len(negative_contexts) * 7)
    if narrow_topic:
        score -= 20
    if explicit_negation:
        score -= 25
    if low_engineering_share:
        score -= 12
    score = max(0, min(100, score))
    if strong:
        score = max(82, score)
    elif candidate:
        score = min(81, max(62, score))
    else:
        score = min(61, score)

    return EngineeringEvidence(
        role_groups=role_groups,
        role_terms=role_terms,
        attributed_roles=attributed_roles,
        actions=actions,
        objects=objects,
        deliverables=deliverables,
        agendas=agendas,
        cycle_markers=cycle_markers,
        coordination_markers=coordination_markers,
        explicit_meetings=explicit_meetings,
        negative_contexts=negative_contexts,
        evidence_windows=evidence_windows,
        quantitative_markers=quantitative_markers,
        engineering_clause_ratio=engineering_clause_ratio,
        topic_meeting=topic_meeting,
        blocked=blocked,
        candidate=candidate,
        strong=strong,
        score=score,
    )


def keyword_score(text, weights):
    return sum(weight for keyword, weight in weights.items() if keyword in text)


def keyword_hits(text, keywords):
    return [keyword for keyword in keywords if keyword in text]


def has_any(text, keywords):
    for keyword in keywords:
        # “施工方”是常用口语角色，但也是“专项施工方案/施工方案”的前缀。
        # 裸 contains 会在完全没有说话人的文本里凭空造出 contractor 角色。
        if keyword == "施工方":
            if re.search(r"施工方(?!案)", text):
                return True
            continue
        if keyword in text:
            return True
    return False


def _first_keyword_index(text: str, keywords) -> int:
    positions = [text.find(keyword) for keyword in keywords if keyword and keyword in text]
    return min(positions) if positions else len(text) + 1


def calibrated_scene_confidence(mode: str, reason: str, model_scene: str | None = None) -> int:
    """Return an explainable confidence score, not the model's self-reported probability."""
    score = 58
    agreement = model_scene == mode
    strong_markers = ["强证据", "自动识别为工程例会", "个人事项强证据"]
    if any(marker in reason for marker in strong_markers):
        score += 14
    if agreement:
        score += 8

    scene_scores = {
        name: int(value)
        for name, value in re.findall(r"(个人|面试|客户拜访|工程|通用)\s*(\d+)", reason)
    }
    if scene_scores:
        values = sorted(scene_scores.values(), reverse=True)
        top = values[0]
        second = values[1] if len(values) > 1 else 0
        margin = max(0, top - second)
        score += min(12, top // 5)
        score += min(10, margin // 4)

    engineering_match = re.search(
        r"工程评分\s*(\d+)/100.*?角色\s*(\d+).*?角色发言\s*(\d+).*?"
        r"议程\s*(\d+).*?现场工序\s*(\d+).*?工程实体\s*(\d+).*?"
        r"工程资料\s*(\d+).*?责任链\s*(\d+)",
        reason,
    )
    if engineering_match:
        evidence_score, roles, attributed, agendas, actions, objects, docs, windows = [
            int(item) for item in engineering_match.groups()
        ]
        score += min(14, evidence_score // 7)
        score += min(10, roles * 2 + attributed * 2 + agendas + windows * 2)
        score += min(6, (actions + objects + docs) // 2)

    if "建议" in reason and "强证据优先" in reason:
        score -= 12
    if "置信度不足" in reason or "暂不可用" in reason:
        score -= 10
    if "其他会议" in reason:
        score -= 6

    return max(52, min(96, score))


def infer_app_mode(transcript: str, requested_mode="auto"):
    if is_talk_mode(requested_mode):
        return "talk", "手动指定为工程例会。"
    if is_general_mode(requested_mode):
        return "general", "手动指定为通用会议纪要。"
    if is_personal_mode(requested_mode):
        return "personal", "手动指定为个人助手。"
    if is_interview_mode(requested_mode):
        return "interview", "手动指定为面试记录。"
    if is_customer_visit_mode(requested_mode):
        return "customer_visit", "手动指定为客户拜访。"

    sample = "".join(_classification_sample(transcript).split())
    scene_opening = sample[:360]
    engineering_score = keyword_score(sample, ENGINEERING_KEYWORDS)
    other_score = keyword_score(sample, NON_ENGINEERING_KEYWORDS)
    personal_score = keyword_score(sample, PERSONAL_ASSISTANT_KEYWORDS)
    interview_score = keyword_score(sample, INTERVIEW_KEYWORDS)
    customer_visit_score = keyword_score(sample, CUSTOMER_VISIT_KEYWORDS)
    evidence = analyze_engineering_evidence(transcript)
    engineering_reason = (
        f"工程评分 {evidence.score}/100，角色 {len(evidence.role_groups)}，"
        f"角色发言 {len(evidence.attributed_roles)}，议程 {len(evidence.agendas)}，"
        f"现场工序 {len(evidence.actions)}，工程实体 {len(evidence.objects)}，"
        f"工程资料 {len(evidence.deliverables)}，责任链 {evidence.evidence_windows}"
    )
    interview_hard_lock = has_any(scene_opening, ["面试", "候选人", "应聘", "复面", "录用"]) and has_any(
        scene_opening, ["岗位", "简历", "薪资", "到岗", "HR", "用人部门", "能力评价"]
    )
    customer_hard_lock = has_any(scene_opening, ["客户拜访", "拜访客户", "客户方", "客户诉求"]) or (
        "客户" in scene_opening and has_any(scene_opening, ["需求调研", "方案沟通", "合作意向", "试点", "后续跟进"])
    )
    personal_hard_lock = has_any(scene_opening, [
        "个人备忘", "提醒自己", "我的待办", "个人复盘", "我明天", "我今天",
    ])
    scene_scores = {
        "personal": personal_score,
        "interview": interview_score,
        "customer_visit": customer_visit_score,
    }
    scene_labels = {
        "personal": "个人助手",
        "interview": "面试记录",
        "customer_visit": "客户拜访",
    }
    best_scene, best_scene_score = max(scene_scores.items(), key=lambda item: item[1])

    engineering_declared_first = evidence.strong and bool(evidence.explicit_meetings) and (
        _first_keyword_index(sample, evidence.explicit_meetings)
        < _first_keyword_index(sample, (
            "面试", "候选人", "应聘", "客户拜访", "拜访客户", "客户诉求",
            "个人备忘", "提醒自己", "我的待办", "个人复盘",
        ))
    )

    # 明确的面试/拜访/个人备忘上下文先判。候选人讲了很多工程履历、拜访的是施工
    # 客户，也不会因此变成工程例会；旧版用工程关键词分数反压这些强语义，顺序错了。
    # 但开场已明确自称工程例会时，后续一句“面试分包项目经理/跟进客户/形成备忘”
    # 只是议程内容，不能反过来吞掉整场会议。
    if engineering_declared_first:
        return "talk", f"自动识别为工程例会（工程例会高置信强证据；{engineering_reason}）。"
    if interview_hard_lock:
        return "interview", f"自动识别为面试记录（面试强证据；个人 {personal_score}，面试 {interview_score}，客户拜访 {customer_visit_score}，工程 {engineering_score}）。"
    if customer_hard_lock:
        return "customer_visit", f"自动识别为客户拜访（客户拜访强证据；个人 {personal_score}，面试 {interview_score}，客户拜访 {customer_visit_score}，工程 {engineering_score}）。"
    if personal_hard_lock and not interview_hard_lock and not customer_hard_lock:
        return "personal", f"自动识别为个人助手（个人事项强证据；个人 {personal_score}，面试 {interview_score}，客户拜访 {customer_visit_score}，工程 {engineering_score}）。"
    if evidence.strong:
        return "talk", f"自动识别为工程例会（工程例会高置信强证据；{engineering_reason}）。"

    personal_only_context = personal_score >= 8 and not (
        evidence.candidate or interview_hard_lock or customer_hard_lock
        or evidence.role_groups or evidence.deliverables
    )
    if personal_only_context:
        return "personal", f"自动识别为个人助手（个人事项强证据；个人 {personal_score}，面试 {interview_score}，客户拜访 {customer_visit_score}，工程 {engineering_score}）。"
    if best_scene_score >= 10 and best_scene_score >= engineering_score + 4:
        return best_scene, f"自动识别为{scene_labels[best_scene]}（个人 {personal_score}，面试 {interview_score}，客户拜访 {customer_visit_score}，工程 {engineering_score}）。"
    if evidence.blocked:
        boundary = "命中专题/培训/产品/行政/否定等反证"
    elif evidence.candidate:
        boundary = "仅为工程相关候选，未达到多角色、多议程、周期与责任链门槛"
    else:
        boundary = "未形成工程例会的复合证据链"
    return "general", f"自动识别为通用会议纪要（{boundary}；{engineering_reason}）。"


# 模型给的理由要显示给用户,按提示词自己的口径「不超过40字」收敛,留一倍余量。
MAX_MODEL_REASON_CHARS = 80


def _sanitized_reason(value) -> str:
    """把模型返回的理由收敛成一行短文本。"""
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    text = re.sub(r"\s{2,}", " ", text)
    return text[:MAX_MODEL_REASON_CHARS]


def _validated_model_evidence(data: dict, transcript: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """只接受转写中真实存在的短引文，并确认引文覆盖至少两个证据族。"""
    raw_items = data.get("evidence", [])
    if not isinstance(raw_items, list):
        return (), ()
    source = re.sub(r"\s+", "", _classification_sample(transcript))
    quotes: list[str] = []
    seen_quotes: set[str] = set()
    categories: set[str] = set()
    for raw in raw_items[:6]:
        quote = _sanitized_reason(raw).strip(" ‘ ’ “ ” \"'")
        compact = re.sub(r"\s+", "", quote)
        if not 2 <= len(compact) <= 48 or compact not in source or compact in seen_quotes:
            continue
        seen_quotes.add(compact)
        quotes.append(quote)
        if _group_hits(compact, ENGINEERING_ROLE_GROUPS):
            categories.add("role")
        if has_any(compact, ENGINEERING_CONCRETE_ACTIONS):
            categories.add("action")
        if has_any(compact, ENGINEERING_OBJECTS):
            categories.add("object")
        if has_any(compact, ENGINEERING_DELIVERABLES):
            categories.add("deliverable")
        if _group_hits(compact, ENGINEERING_AGENDA_GROUPS):
            categories.add("agenda")
        if has_any(compact, ENGINEERING_CYCLE_MARKERS):
            categories.add("cycle")
        if has_any(compact, ENGINEERING_COORDINATION_MARKERS):
            categories.add("coordination")
    return tuple(quotes), tuple(sorted(categories))


def _iter_json_objects(text: str):
    """按大括号配对切出文本里的候选 JSON 对象。

    只按深度配对,并跳过字符串内部的括号与转义 —— 否则 reason 里出现一个 "}"
    就会提前截断。
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                yield text[start:index + 1]
                start = -1


def _parse_scene_payload(raw: str) -> dict:
    """从模型输出里取出场景 JSON。

    原先是 re.search(r"\\{.*\\}", raw, re.S):贪婪匹配从第一个 { 一路吃到最后一个 },
    模型只要在 JSON 之外多写一对花括号,整段就解析失败。实测两种常见输出会中招:
    「解释里复述了转写中的 {某某}」、「先吐一个思考对象再给答案」。后果不是报错
    而是静默退回规则兜底 —— 场景可能判错,用户只看到一句 Python 的 JSONDecodeError。

    改为按括号配对切出所有候选,优先取带 scene 字段的那个(答案通常在最后)。
    """
    candidates = []
    for chunk in _iter_json_objects(raw):
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(data, dict):
            candidates.append(data)
    for data in reversed(candidates):
        if "scene" in data:
            return data
    if candidates:
        return candidates[-1]
    # 一个候选都没有:让原本的解析错误照常抛出,交给上层兜底
    return json.loads(raw)


def infer_app_mode_best_effort(transcript: str, app_mode="auto"):
    deterministic_mode, deterministic_reason = infer_app_mode(transcript, app_mode)
    if not is_auto_mode(app_mode):
        return deterministic_mode, deterministic_reason
    engineering_evidence = analyze_engineering_evidence(transcript)
    fast_model = os.getenv("JKINCO_SCENE_CLASSIFIER_MODEL", "").strip()
    fast_fallback = os.getenv("JKINCO_SCENE_CLASSIFIER_FALLBACK_MODEL", "").strip()

    classifier_prompt = f"""你是筑听上线版的轻量场景分类器。只根据转写事实识别场景，不生成纪要。

总原则：
1. 只能依据转写里的业务事实分类，不能依据输出格式词分类。
2. “会议、总结、纪要、待办、跟进、复盘、今天、明天、项目”都是通用词，不能单独决定场景。
3. 优先寻找“多个工程责任主体各自发言 + 多个生产控制议程 + 周期复盘/责任时限”组成的链路。
4. 工程例会采用高精度门槛；证据不足或类型不确定时返回 general，绝不因为“项目、进度、质量、安全、验收”等泛词判为工程。

允许场景：
- talk：周期性的工程例会/监理例会/工地例会。典型证据是施工、监理、建设/代建等至少两类主体有各自的汇报/要求，且覆盖进度、质量、安全、资源、技术资料中的多个议程，并有上周/本周/下周、整改报送、责任与时限。五方全部到场不是必要条件；“五方验收”一个词也不能重复充当两类证据。很短的录音只有在开场明确自称工程/监理/工地例会时，才可用两类角色互动、具体工序和工程资料补足。
- general：所有非工程例会，包括管理汇报、经营分析、跨部门同步、产品/研发项目周会、培训研讨和普通会议；也包括仅围绕一个事故、围墙、单项安全/质量问题的工程专题会，因为它不应套常规工程例会模板。由模型按原文内容自由组织通用纪要。旧版“灵犀管理简报”也归入 general。
- personal：个人备忘/个人复盘/灵感/个人提醒。必要证据通常是第一人称个人安排、个人提醒、个人事项、个人想法。只有在缺少工程主体、客户主体、候选人/HR 证据时才能判为 personal；普通“会议概述、待办、总结、项目复盘、客户反馈”不是 personal 的决定性证据。
- interview：招聘面试场景。必要证据通常包括候选人、应聘、岗位、简历、HR、面试官、用人部门、薪资、到岗、稳定性、复面、录用、背调、能力评价。若出现候选人与岗位/HR/录用链路，优先判为 interview。
- customer_visit：客户拜访/需求调研/售前沟通/合作跟进场景。必要证据通常包括客户方、拜访客户、客户诉求、需求调研、方案沟通、试点、合作意向、客户顾虑、报价、交付周期、下一轮沟通。只有“客户反馈”但没有拜访/需求/合作链路时，不要判为 customer_visit。

易错反例：
- “推进五方验收并完成竣工资料归档” => general；只有一条工程任务，不能证明是周期性工程例会。
- “个人备忘：明天复盘客户反馈并提醒自己提交周报” => personal，不是 customer_visit。
- “候选人应聘项目经理岗位，HR 评估薪资和到岗时间” => interview，不是 talk。
- “拜访客户，客户希望开展试点并约定下周提交方案” => customer_visit，不是 general。
- “各部门月度管理汇报，讨论经营风险和领导决策” => general。
- “产品项目周会讨论研发进度、数据质量、账号安全和功能验收” => general，不是 talk。
- “培训讲解施工单位、监理单位、质量控制和竣工资料” => general；这是培训中的术语引用。
- “施工、监理、代建召开专题会，只处理围墙开裂及当天修补” => general；这是单一议题工程专题会。
- “施工单位汇报钢筋绑扎，监理单位要求隐蔽验收并提交专项方案” => general；工程相关但缺少例会周期/多议程，保守拒识。
- “工程例会：施工单位汇报钢筋绑扎，监理单位要求隐蔽验收并提交专项方案” => talk。

evidence 必须给出转写中的 2-4 段原文短引文，每段不超过24字，禁止改写或补造。
返回严格 JSON，不要 Markdown：{{"scene":"talk|general|personal|interview|customer_visit","reason":"不超过40字","evidence":["原文引文1","原文引文2"]}}

{SOURCE_GUARD}

转写（长文本已等距保留首、中、尾）：
{fence_source(_classification_sample(transcript, limit=12000))}"""
    try:
        raw = call_llm(
            classifier_prompt,
            timeout=int(os.getenv("JKINCO_SCENE_CLASSIFIER_TIMEOUT", "25")),
            model_name=fast_model,
            fallback_model=fast_fallback,
            thinking=False,
            temperature=0.0,
        ).strip()
        data = _parse_scene_payload(raw)
        scene = str(data.get("scene", "")).strip()
        if scene == "lingxi":
            scene = "general"
        allowed = {"talk", "general", "personal", "interview", "customer_visit"}
        if scene not in allowed:
            raise ValueError("模型返回未知场景")
        # 模型返回的理由同样是不可信内容:转写可以左右它,而它会随「识别理由」
        # 一路显示给参会成员、并写进历史记录。scene 有白名单挡着,reason 没有 ——
        # 不限长会让一段超长文本撑大记录,不清换行则能伪造成多行提示。
        # 提示词本来就要求「不超过40字」,这里按它的口径强制。
        model_reason = _sanitized_reason(data.get("reason", ""))
        model_quotes, model_categories = _validated_model_evidence(data, transcript)
        if scene == deterministic_mode:
            confidence = calibrated_scene_confidence(deterministic_mode, deterministic_reason, scene)
            return scene, f"规则与{fast_model}一致：{model_reason or deterministic_reason}（证据置信 {confidence}%）"
        # 强证据必须先判。这两道原先是反的:「模型说 talk 就保守归入 general」
        # 排在前面,于是模型一旦误判成工程例会,连「面试强证据」「客户拜访强证据」
        # 这种本地高置信判定也会被丢掉,结果退化成 general —— 用户拿到的模板就错了
        # (面试录音套上通用纪要,该有的候选人评价章节全没有)。实测:
        #   面试强证据   规则=interview      + 模型说 talk → general
        #   客户拜访强证据 规则=customer_visit + 模型说 talk → general
        # 而模型说 personal/general 时强证据本来是正常胜出的 —— 只有 talk 会击穿,
        # 正说明问题出在顺序而不是判据。
        strong_rule = any(
            marker in deterministic_reason
            for marker in ["工程例会高置信强证据", "面试强证据", "客户拜访强证据", "个人事项强证据"]
        )
        if scene == "talk" and deterministic_mode != "talk" and not strong_rule:
            promotion_structure = bool(engineering_evidence.explicit_meetings) or (
                len(engineering_evidence.agendas) >= 3
                and _has_weekly_cycle(engineering_evidence.cycle_markers)
            ) or engineering_evidence.evidence_windows >= 2
            grounded_talk = (
                engineering_evidence.candidate
                and not engineering_evidence.blocked
                and promotion_structure
                and len(model_quotes) >= 2
                and len(model_categories) >= 3
            )
            if grounded_talk:
                confidence = min(91, max(78, engineering_evidence.score + 10))
                return "talk", (
                    f"{fast_model} 用转写原文复核工程候选通过：{model_reason}"
                    f"（{len(model_quotes)} 段引文/{len(model_categories)} 类证据，证据置信 {confidence}%）。"
                )
            confidence = calibrated_scene_confidence("general", deterministic_reason, None)
            return "general", (
                f"{fast_model} 建议工程例会，但未通过本地复合门槛或原文引文核验；"
                f"保守归入通用会议纪要（证据置信 {confidence}%）。"
            )
        if strong_rule:
            confidence = calibrated_scene_confidence(deterministic_mode, f"{deterministic_reason}；模型建议冲突，强证据优先", scene)
            return deterministic_mode, f"{deterministic_reason}；{fast_model} 建议{mode_label(scene)}但本地强证据优先（证据置信 {confidence}%）。"
        model_supported_reason = f"自动识别为{mode_label(scene)}（{model_reason}）。"
        confidence = calibrated_scene_confidence(scene, model_supported_reason, scene)
        if confidence >= 78:
            return scene, f"{fast_model} 快速复核为{mode_label(scene)}：{model_reason}（证据置信 {confidence}%；规则初判为{mode_label(deterministic_mode)}）"
        fallback_confidence = calibrated_scene_confidence(deterministic_mode, deterministic_reason, None)
        return deterministic_mode, f"{deterministic_reason}；{fast_model} 复核证据不足（证据置信 {confidence}%），保持规则结果（证据置信 {fallback_confidence}%）。"
    except Exception as error:
        # 异常文本同样要过净化:这句会随「识别理由」显示给参会成员并写进历史记录。
        # 上面对模型 reason 收敛了长度与换行,这条兜底路径原先直接插值 {error},
        # 等于把那道处理绕开 —— 模型侧的报错可以很长、可以带换行。
        return deterministic_mode, (
            f"{deterministic_reason}；{fast_model} 场景复核暂不可用，"
            f"已使用本地规则兜底：{_sanitized_reason(error)}"
        )
