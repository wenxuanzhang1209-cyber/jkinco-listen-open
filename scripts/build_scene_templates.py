#!/usr/bin/env python3
"""Build clean scene templates and rendered sample deliverables."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_templates import (
    build_branded_report,
    build_report_docx,
    convert_docx_to_pdf,
    prepare_talk_template,
)


TEMPLATE_DIR = ROOT / "templates" / "v2"
SAMPLE_DIR = ROOT / "samples" / "scene-templates"
LOGO_PATH = ROOT / "assets" / "sribs-meeting-logo.jpeg"
TALK_TEMPLATE = TEMPLATE_DIR / "会议纪要_工地例会原版式模板.docx"


BLANK_REPORTS = {
    "auto": """智能识别场景路由
一、识别结果
目标场景：{{AUTO_SCENE}}
识别依据：{{EVIDENCE}}
建议模板：{{TEMPLATE}}

二、处理说明
系统将自动套用会议纪要、管理简报、个人备忘录、面试记录或客户拜访模板。""",
    "lingxi": """管理简报
汇报周期：{{PERIOD}}
汇报对象：{{AUDIENCE}}
信息来源：{{SOURCE}}

一、总体概览
本周期关键动态：{{KEY_UPDATE}}
需要领导重点关注：{{FOCUS}}

二、重要事项进展
事项 | 负责人 | 最新进展 | 下一步
{{ITEM}} | {{OWNER}} | {{PROGRESS}} | {{NEXT_STEP}}

三、风险与异常
风险 | 影响 | 建议处理
{{RISK}} | {{IMPACT}} | {{ACTION}}

四、需决策与协调
{{DECISION}}""",
    "personal": """个人备忘录
记录主题：{{TOPIC}}
关联项目：{{PROJECT}}
记录时间：{{TIME}}

一、摘要结论
{{CONCLUSION}}

二、记录要点
{{NOTES}}

三、待办事项
事项 | 优先级 | 计划完成 | 状态
{{ACTION}} | {{PRIORITY}} | {{DUE}} | {{STATUS}}

四、风险与提醒
{{RISK}}

五、个人复盘
{{REVIEW}}""",
    "interview": """面试记录与候选人反馈表
候选人：{{CANDIDATE}}
应聘岗位：{{POSITION}}
面试轮次：{{ROUND}}
面试时间：{{TIME}}

一、面试结论摘要
综合评价：{{ASSESSMENT}}
建议结果：{{RESULT}}

二、面试过程记录
环节 | 提问重点 | 候选人反馈 | 观察记录
{{STAGE}} | {{QUESTION}} | {{RESPONSE}} | {{OBSERVATION}}

三、能力评价矩阵
评价维度 | 评分 | 评价依据 | 后续验证
{{DIMENSION}} | {{SCORE}} | {{EVIDENCE}} | {{VERIFY}}

四、优势、风险与后续安排
{{FOLLOW_UP}}""",
    "customer_visit": """客户拜访会议纪要
拜访客户：{{CUSTOMER}}
拜访日期：{{DATE}}
拜访地点：{{LOCATION}}

一、拜访背景
{{BACKGROUND}}

二、客户核心诉求
关注点 | 具体诉求 | 初步回应
{{FOCUS}} | {{NEED}} | {{RESPONSE}}

三、会议沟通要点
{{DISCUSSION}}

四、待办与跟进
事项 | 责任方 | 完成时间 | 交付物
{{ACTION}} | {{OWNER}} | {{DUE}} | {{DELIVERABLE}}

五、机会与风险判断
{{JUDGEMENT}}""",
}


SAMPLES = {
    "auto": """智能识别场景路由
一、识别结果
目标场景：会议纪要
识别依据：五方验收、施工单位、监理单位、竣工资料归档形成组合证据
建议模板：会议纪要_工地例会原版式模板

二、处理说明
已自动选择工程会议纪要模板，并进入人工校核流程。""",
    "talk": """会 议 纪 要
项目名称：示例建设项目
编号：ZT-DEMO-001
会议名称：项目质量安全与进度协调例会
会议时间：2026年7月11日 09:30-10:30
会议地点：项目现场会议室
与会人员：建设、监理、设计、施工单位项目负责人
主持人：项目总监

一、施工单位
资源配置情况
1. 现场管理人员与主要班组已到位。
现场安全管理情况
1. 完成临边防护复查，临时用电问题限期整改。
施工进度管理情况
1. 本周完成主体结构施工计划的90%。
2. 下周推进机电预留预埋与材料报验。

二、监理单位
质量控制情况
1. 钢筋隐蔽验收前须完成自检并提交资料。
安全控制情况
1. 高处作业与动火作业必须落实旁站和审批。

三、设计单位
1. 本周确认预留洞口调整图纸。

四、建设单位
1. 各方按节点完成整改闭环并书面反馈。""",
    "lingxi": """管理简报
汇报周期：2026年第28周
汇报对象：经营管理层
信息来源：项目周例会

一、总体概览
本周期关键动态：三个重点项目按计划推进，一个项目存在回款延迟。
需要领导重点关注：协调客户确认付款节点。

二、重要事项进展
事项 | 负责人 | 最新进展 | 下一步
项目A交付 | 张经理 | 已完成阶段验收 | 7月15日前归档
项目B回款 | 李经理 | 客户内部审批中 | 提交补充材料

三、风险与异常
风险 | 影响 | 建议处理
回款延迟 | 影响季度现金流 | 由分管领导牵头协调

四、需决策与协调
建议批准项目B专项客户沟通方案。""",
    "personal": """个人备忘录
记录主题：本周项目工作复盘
关联项目：数字化建设项目
记录时间：2026年7月11日

一、摘要结论
核心流程已打通，下一阶段聚焦真实业务样本验收。

二、记录要点
1. 完成实时录音、场景识别和模板导出联调。
2. 设备自动读取仍需补充Windows真实设备测试。

三、待办事项
事项 | 优先级 | 计划完成 | 状态
补充四场景样本 | 高 | 7月14日 | 进行中
组织用户验收 | 高 | 7月16日 | 未开始

四、风险与提醒
正式域名备案完成前，应使用可信HTTPS入口录音。

五、个人复盘
先验证完整闭环，再继续扩展非核心功能。""",
    "interview": """面试记录与候选人反馈表
候选人：示例候选人
应聘岗位：项目数字化产品经理
面试轮次：第一轮
面试时间：2026年7月11日

一、面试结论摘要
综合评价：具备工程行业经验和跨团队推进能力。
建议结果：建议进入复试。

二、面试过程记录
环节 | 提问重点 | 候选人反馈 | 观察记录
项目经历 | 复杂项目推进方法 | 能拆解里程碑并追踪风险 | 回答结构清晰
业务理解 | 工程数字化价值 | 强调数据闭环与现场可用性 | 行业理解较好

三、能力评价矩阵
评价维度 | 评分 | 评价依据 | 后续验证
岗位匹配度 | 4/5 | 有工程产品落地经历 | 复试核验规模
沟通表达 | 4/5 | 表达清晰且能举例 | 继续观察压力沟通

四、优势、风险与后续安排
优势为行业经验和执行闭环；需核验团队管理跨度。""",
    "customer_visit": """客户拜访会议纪要
拜访客户：示例客户集团
拜访日期：2026年7月11日
拜访地点：客户总部会议室

一、拜访背景
围绕工程项目会议知识沉淀与智能报告开展需求沟通。

二、客户核心诉求
关注点 | 具体诉求 | 初步回应
识别准确率 | 工程术语与多人会议准确识别 | 提供领域词表与人工校核
数据安全 | 会议资料分级授权 | 支持私有化部署与权限控制

三、会议沟通要点
客户认可自动场景识别方向，建议先选择一个项目试点。

四、待办与跟进
事项 | 责任方 | 完成时间 | 交付物
提交试点方案 | 建科方 | 7月15日 | 试点实施方案
确认样本范围 | 客户方 | 7月16日 | 脱敏录音清单

五、机会与风险判断
试点意向明确；需提前确认数据合规边界。""",
}


def build(reference: Path) -> None:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    prepare_talk_template(reference, TALK_TEMPLATE, LOGO_PATH)

    for mode, report in BLANK_REPORTS.items():
        if mode == "talk":
            continue
        build_branded_report(report, mode, LOGO_PATH, TEMPLATE_DIR / f"{mode}_定制模板.docx")

    for mode, report in SAMPLES.items():
        docx_path = SAMPLE_DIR / f"{mode}_输出样例.docx"
        build_report_docx(report, mode, docx_path, talk_template=TALK_TEMPLATE, logo=LOGO_PATH)
        if not convert_docx_to_pdf(docx_path, SAMPLE_DIR / f"{mode}_输出样例.pdf"):
            print(f"PDF_SKIPPED {mode}: LibreOffice unavailable")
    print(TEMPLATE_DIR)
    print(SAMPLE_DIR)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    build(args.reference.resolve())


if __name__ == "__main__":
    main()
