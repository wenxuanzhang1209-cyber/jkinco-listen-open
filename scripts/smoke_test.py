#!/usr/bin/env python3
"""开源本地版的冒烟测试。

离线部分不依赖任何模型服务：验证场景规则路由、全场景 DOCX/PDF 导出、
处理模式与推送保护。--online / --asr-audio 需要本地模型已就绪。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backend import core  # noqa: E402  （触发 .env 加载与模块契约）
from jkinco_classifier import infer_app_mode  # noqa: E402
from jkinco_export import export_summary_docx, export_summary_pdf  # noqa: E402


ROUTE_CASES = {
    "personal": "这是我的个人备忘，明天提醒我提交周报并复盘今天的工作。",
    "interview": "今天面试候选人张某，应聘产品经理岗位，建议进入复面。",
    "customer_visit": "本次客户拜访重点讨论客户诉求、试点方案和后续跟进。",
    "talk": "监理单位主持工程例会，施工单位汇报现场进度、安全整改和验收资料。",
    "talk_acceptance": "项目验收推进与竣工资料归档管理会议，明确五方验收组织安排及各专业单位配合要求，统一竣工资料汇总节点和提交流程。",
    "talk_formal": "第二十七次工程例会：施工单位汇报A区钢筋绑扎、模板安装和混凝土浇筑进度，监理单位要求落实质量控制、安全控制与现场整改，施工组织设计及专项施工方案应完成报审，建设单位要求按图施工。",
    "general": "各部门进行月度管理汇报，讨论经营风险、待办事项和领导决策。",
    "general_project": "产品项目周会讨论研发进度、数据质量、账号安全和功能验收，决定周五完成联调。",
    "general_quality": "信息系统项目复盘，讨论交付质量、安全风险和项目验收，明确产品经理跟进。",
}

# 规则证据门控是保守设计：证据不足的“像工程会”不得套工程模板。
EXPECTED_ROUTES = {
    "personal": "personal",
    "interview": "interview",
    "customer_visit": "customer_visit",
    "talk": "general",
    "talk_acceptance": "general",
    "talk_formal": "talk",
    "general": "general",
    "general_project": "general",
    "general_quality": "general",
}

SUMMARY_CASES = {
    "auto": "# 智能识别场景路由\n## 识别结果\n目标场景：会议纪要。",
    "talk": "# 会 议 纪 要\n## 会议内容\n施工单位汇报进度。",
    "general": "# 会议纪要\n## 重点事项\n本周工作正常。",
    "personal": "# 个人备忘录\n## 核心事项\n- 明天提交周报。",
    "interview": "# 面试记录与候选人反馈表\n## 候选人基本信息\n候选人：张某",
    "customer_visit": "# 客户拜访会议纪要\n## 客户诉求\n希望开展试点。",
}

ONLINE_CASES = {
    "talk": "2026年7月10日召开工程监理例会。施工单位汇报进度和安全整改，监理要求按时提交验收资料。",
    "personal": "个人备忘：明天上午提交项目周报，下午复盘客户反馈，先确认数据再联系负责人。",
    "interview": "候选人应聘产品经理，有五年企业软件经验，表达清晰，案例完整，建议进入业务复面。",
    "customer_visit": "拜访客户，客户希望八月开展试点，关注数据安全和交付周期，项目经理周五前提交方案。",
}


def run_offline_checks() -> None:
    for case_name, transcript in ROUTE_CASES.items():
        expected = EXPECTED_ROUTES[case_name]
        actual, reason = infer_app_mode(transcript)
        assert actual == expected, f"场景识别失败：用例 {case_name}，期望 {expected}，实际 {actual}，原因 {reason}"

    for mode, summary in SUMMARY_CASES.items():
        docx = Path(export_summary_docx(summary, mode))
        pdf = Path(export_summary_pdf(summary, mode))
        assert docx.exists() and docx.stat().st_size > 0, f"DOCX 导出失败：{mode}"
        assert pdf.exists() and pdf.stat().st_size > 0, f"PDF 导出失败：{mode}"

    assert core.should_push_to_dingtalk("生成并推送钉钉")
    assert not core.should_push_to_dingtalk("生成纪要，暂不推送")
    print("离线检查通过：场景路由、全场景导出、处理模式与推送保护均通过。")


def run_online_checks() -> None:
    for mode, transcript in ONLINE_CASES.items():
        result = core.generate_minutes(transcript, mode)
        assert len(result.strip()) >= 80, f"本地模型输出过短：{mode}"
        print(f"在线场景通过：{mode}，{len(result)} 字符。")


def run_asr_check(audio_path: str) -> None:
    transcript = core.transcribe_audio(audio_path).strip()
    assert transcript, "ASR 返回空文本"
    print(f"ASR 通过：{transcript}")


def main() -> None:
    parser = argparse.ArgumentParser(description="筑听开源本地版冒烟测试")
    parser.add_argument("--online", action="store_true", help="调用本地 LLM 验证核心业务场景")
    parser.add_argument("--asr-audio", help="使用指定音频验证本地 ASR")
    args = parser.parse_args()

    run_offline_checks()
    if args.online:
        run_online_checks()
    if args.asr_audio:
        run_asr_check(args.asr_audio)


if __name__ == "__main__":
    main()
