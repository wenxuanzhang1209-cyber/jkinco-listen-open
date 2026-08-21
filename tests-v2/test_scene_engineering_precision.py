"""工程例会高精度门控：防止通用、专题及其它场景被工程词吞掉。"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import jkinco_classifier as classifier


@pytest.mark.parametrize("transcript", [
    "公司行政部门周例会讨论办公室搬迁。施工单位已进场，行政跟踪施工进度；随后讨论财务预算、人事安排和排班。",
    "产品项目周会讨论研发进度、数据质量和账号安全，建设单位客户提出功能验收要求，团队需要提交技术资料。",
    "物业部门例会通报施工单位在园区现场整改，随后讨论采购、预算、值班和人员排班。",
    "公司经营例会讨论施工单位合同付款、工程款预算、销售回款与下季度经营目标。",
    "今天的培训议程只有一个主题：讲解施工单位、监理单位、五方验收和竣工资料的管理要求。",
    "通用项目例会讨论质量控制、进度控制和技术资料提交，产品团队周五完成版本联调。",
    "这不是施工单位施工现场协调，也不涉及监理通知单，今天只讨论财务预算。",
    "产品团队复盘ASR错误：监理单位、施工现场、监理通知单这些词不该出现在转写里。",
    "总部例会转述外部项目情况：施工单位提交设计变更和工程款申请；主议题仍是经营、招聘和绩效。",
    "五方验收。",
])
def test_hard_general_negatives_do_not_lock_engineering(transcript):
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"
    assert not evidence.strong


def test_single_issue_engineering_topic_is_not_a_regular_engineering_meeting():
    transcript = (
        "施工现场围墙开裂，监理单位联系施工单位和代建单位召开专题会议。"
        "施工单位今日内完成修补，监理单位要求控制打桩速度并检查围墙。"
    )
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.topic_meeting and evidence.blocked
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"


@pytest.mark.parametrize("transcript, expected", [
    (
        "施工单位汇报上周完成混凝土浇筑；监理单位要求本周整改临边防护；"
        "建设单位确认下周节点，专项施工方案和检验批资料按时报送。",
        "talk",
    ),
    (
        "工程例会：总包单位汇报本周地下室钢筋绑扎进度，监理单位要求完成隐蔽验收，"
        "下周报送检验批和专项施工方案。",
        "talk",
    ),
])
def test_high_confidence_engineering_patterns(transcript, expected):
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.strong and evidence.score >= 82
    assert classifier.infer_app_mode(transcript, "auto")[0] == expected


@pytest.mark.parametrize("transcript", [
    "施工单位汇报本周进度，监理单位提出钢筋绑扎需要报验，检验批资料下周补齐。",
    "推进五方验收并完成竣工资料归档。",
    "施工单位汇报钢筋绑扎，监理单位要求隐蔽验收并提交专项方案。",
])
def test_engineering_related_short_updates_are_candidates_not_regular_meetings(transcript):
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.candidate and not evidence.strong
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"


@pytest.mark.parametrize("transcript, expected", [
    (
        "面试项目经理候选人，他曾在施工单位负责钢筋、混凝土、基坑和塔吊，"
        "熟悉专项施工方案与监理通知单；HR询问薪资和到岗时间。",
        "interview",
    ),
    (
        "客户拜访施工单位，了解客户诉求，讨论钢筋、模板、混凝土、基坑和专项施工方案，"
        "双方安排软件试点和下一轮方案沟通。",
        "customer_visit",
    ),
    (
        "个人备忘：提醒自己明天联系施工单位确认施工进度并提交监理通知单。",
        "personal",
    ),
])
def test_other_scene_strong_context_beats_engineering_vocabulary(transcript, expected):
    assert classifier.infer_app_mode(transcript, "auto")[0] == expected


def _model_payload(scene: str, evidence: list[str]) -> str:
    return json.dumps({
        "scene": scene,
        "reason": "角色、工序和资料形成当前责任链",
        "evidence": evidence,
    }, ensure_ascii=False)


def test_grounded_model_can_promote_a_compact_engineering_candidate():
    transcript = (
        "工程例会录音：总包单位本周完成地下室钢筋绑扎，明天安排混凝土浇筑，"
        "专项施工方案已报审。"
    )
    local = classifier.analyze_engineering_evidence(transcript)
    assert local.candidate and not local.strong
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"
    payload = _model_payload("talk", [
        "总包单位本周完成地下室钢筋绑扎",
        "专项施工方案已报审",
    ])
    with patch.object(classifier, "call_llm", return_value=payload):
        mode, reason = classifier.infer_app_mode_best_effort(transcript, "auto")
    assert mode == "talk"
    assert "原文" in reason or "引文" in reason


def test_model_cannot_promote_with_paraphrased_or_missing_quotes():
    transcript = (
        "工程例会录音：总包单位本周完成地下室钢筋绑扎，明天安排混凝土浇筑，"
        "专项施工方案已报审。"
    )
    payload = _model_payload("talk", ["多方讨论施工", "工程资料齐全"])
    with patch.object(classifier, "call_llm", return_value=payload):
        mode, reason = classifier.infer_app_mode_best_effort(transcript, "auto")
    assert mode == "general"
    assert "引文核验" in reason


def test_model_cannot_promote_a_training_case_even_with_exact_quotes():
    transcript = "培训讲解施工单位和监理单位的职责，并用五方验收、竣工资料作为案例。"
    payload = _model_payload("talk", ["施工单位和监理单位", "五方验收、竣工资料"])
    with patch.object(classifier, "call_llm", return_value=payload):
        mode, _ = classifier.infer_app_mode_best_effort(transcript, "auto")
    assert mode == "general"


def test_long_transcript_samples_the_middle_and_tail():
    filler = "各部门同步一般事项和行政安排。" * 900
    engineering = (
        "施工单位汇报本周钢筋绑扎，监理单位要求完成隐蔽验收，"
        "建设单位确认下周节点并报送专项施工方案。"
    )
    transcript = filler[:14000] + engineering + filler[14000:28000] + engineering
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.role_groups
    assert evidence.actions and evidence.deliverables


def test_candidate_flag_is_always_boolean():
    evidence = classifier.analyze_engineering_evidence(
        "施工单位汇报钢筋绑扎并报送专项施工方案。"
    )
    assert isinstance(evidence.candidate, bool)


@pytest.mark.parametrize("transcript", [
    "安全专题会议：施工单位汇报上周塔吊事故，监理单位本周整改临边防护，建设单位下周复查验收并提交专项方案、进度计划和班组安排。",
    "质量专题会：施工单位汇报上周钢筋问题，监理单位本周送检复试，建设单位下周复查验收并补齐专项方案。",
    "塔吊事故处置会：施工方汇报塔吊事故，监理方要求现场整改，甲方安排下周复查并提交专项方案。",
    "图纸会审协调会：施工单位、监理单位、设计单位讨论本周图纸问题和施工进度，下周完成设计变更。",
    "工程款支付协调会：建设单位、施工单位和监理单位核对上周进度款，本周提交工程签证，下周付款。",
])
def test_rich_engineering_topic_meetings_do_not_become_regular_meetings(transcript):
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.blocked and evidence.topic_meeting
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"


@pytest.mark.parametrize("transcript", [
    "产品研发周会：施工单位角色汇报上周进度，监理单位角色提出本周质量安全要求，建设单位角色安排下周资源和技术资料；这是工程例会识别功能的测试案例。",
    "培训课程示例：施工单位汇报上周进度，监理单位本周检查质量安全，建设单位下周安排资源并提交技术资料和专项方案。",
    "新闻稿报道：施工单位汇报上周进度，监理单位要求本周整改质量安全，建设单位下周安排班组并提交专项方案。",
    "物业工程维修周会：施工单位、监理单位和物业方讨论上周维修进度、本周安全质量和下周人员安排。",
    "公司经营周会：施工单位汇报上周工程款和施工进度，监理单位本周检查质量与临边防护，建设单位下周安排班组并提交专项方案，随后讨论销售回款。",
    "跨部门项目周会：施工单位汇报上周混凝土浇筑进度，监理单位本周检查质量与临边防护，建设单位下周安排班组并提交专项方案。",
    "物业项目周会：施工单位汇报上周混凝土浇筑进度，监理单位本周检查质量与临边防护，建设单位下周安排班组并提交专项方案。",
])
def test_meta_training_and_adjacent_weekly_meetings_are_blocked(transcript):
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.blocked
    assert classifier.infer_app_mode(transcript, "auto")[0] == "general"


def test_spoken_role_and_asr_variants_are_recognized_without_generic_word_collisions():
    transcript = (
        "工程例会：甲方要求乙方本周完成三层绑钢筋，下周浇砼，"
        "监理方提交专项施工方案并组织隐验和临电检查。"
    )
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert set(evidence.role_groups) >= {"owner", "contractor", "supervision"}
    assert evidence.strong
    assert classifier.infer_app_mode(transcript, "auto")[0] == "talk"

    speakerless = (
        "上周完成三层绑钢筋和支模，本周安排浇砼、临电整改及隐验，"
        "下周推进进度质量安全和班组资源，提交专项施工方案与技术资料。"
    )
    speakerless_evidence = classifier.analyze_engineering_evidence(speakerless)
    assert "contractor" not in speakerless_evidence.role_groups, "施工方案不能虚构施工方角色"


def test_closeout_engineering_meeting_needs_no_active_site_operation():
    transcript = (
        "工程例会收尾阶段：施工单位汇报本周完成情况并提交竣工资料；"
        "监理单位要求补齐验收资料和工程签证，落实质量验收；"
        "建设单位安排人员配置，确认五方验收并完成设计变更和技术资料归档。"
    )
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert not evidence.actions and not evidence.objects
    assert len(evidence.role_groups) >= 3
    assert len(evidence.agendas) >= 3
    assert len(evidence.deliverables) >= 2
    assert evidence.strong
    assert classifier.infer_app_mode(transcript, "auto")[0] == "talk"


def test_engineering_meeting_can_mention_recruiting_customer_and_responsibility_negation():
    transcript = (
        "工程例会：施工单位汇报上周混凝土浇筑进度，监理单位要求本周完成质量安全整改，"
        "建设单位安排下周班组并提交专项方案。这不是施工单位单方责任。"
        "会后面试项目经理候选人，HR确认到岗；后续跟进客户方案沟通并形成会议备忘录。"
    )
    evidence = classifier.analyze_engineering_evidence(transcript)
    assert evidence.strong and not evidence.blocked
    assert classifier.infer_app_mode(transcript, "auto")[0] == "talk"


def test_duplicate_model_quotes_cannot_promote_a_weak_candidate():
    transcript = "总包单位本周完成地下室钢筋绑扎，专项施工方案已报审。"
    payload = _model_payload("talk", ["钢筋绑扎", "钢筋绑扎"])
    with patch.object(classifier, "call_llm", return_value=payload):
        mode, reason = classifier.infer_app_mode_best_effort(transcript, "auto")
    assert mode == "general"
    assert "引文核验" in reason or "复合门槛" in reason
