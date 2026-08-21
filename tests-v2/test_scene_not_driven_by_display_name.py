"""场景判定只看说了什么,不看谁在房间里。

实时会议的转写是「说话人：内容」逐行拼的,而显示名由参会者自己填。
_finalize_minutes 原先把这份带署名的文本喂给场景分类器 —— 于是显示名里的字
和会上说的话有同等分量,而且它出现在每一行。

实测:一场内容完全中性的会,参会者只要把显示名改掉就能翻掉场景 ——

    显示名「客户拜访」 -> customer_visit（客户拜访强证据）
    显示名「候选人」   -> interview（面试 10）

场景决定用哪套模板出纪要:一场普通项目会会被套上「HR面试记录与候选人反馈」
的版式归档。分类器的关键词表本就是描述「会上谈了什么」的,用说话内容判才贴合
原意,也顺手去掉了这个操纵入口。

与「实时会议的空转写闸门被署名前缀架空」是同一个形态:函数没错,喂给它的东西
错了。两处都改用 _final_speech_only。
"""
from __future__ import annotations

import inspect

import pytest

import backend.meetings as meetings
from jkinco_classifier import infer_app_mode

NEUTRAL_SPEECH = "我们下周把这个事情再对一下，先这样。"


@pytest.mark.parametrize("display_name", ["施工单位", "客户拜访", "候选人", "监理单位", "面试"])
def test_a_display_name_cannot_flip_the_scene(display_name):
    """判的是说话内容,所以名字里写什么都不影响结果。"""
    assert infer_app_mode(NEUTRAL_SPEECH, "auto")[0] == "general"


@pytest.mark.parametrize("display_name", ["客户拜访", "候选人"])
def test_the_formatted_transcript_would_have_been_flipped(display_name):
    """先证明这个坑是真的,否则上面那条是恒真的。"""
    formatted = f"{display_name}：{NEUTRAL_SPEECH}\n主持人：好的。"
    assert infer_app_mode(formatted, "auto")[0] != "general", (
        "带署名的文本不再受影响了?那说明分类器变了,本文件的前提需要重看"
    )


def test_finalize_classifies_the_speech_only():
    source = inspect.getsource(meetings._finalize_minutes)
    assert "infer_app_mode_best_effort(speech_only" in source, "又拿带署名的文本去判场景了"
    # 生成纪要仍要用带署名的那份 —— 模型需要知道谁说了什么
    assert "generate_minutes(transcript" in source, "纪要不该丢掉说话人信息"


@pytest.mark.parametrize(
    "text, expected, label",
    [
        (
            "施工单位汇报上周混凝土浇筑进度，监理单位要求本周完成质量检查、临边防护和隐蔽验收，"
            "建设单位确认下周班组节点并报送专项施工方案。",
            "talk",
            "工程例会",
        ),
        ("候选人介绍了过往项目经验，用人部门询问了薪资期望与到岗时间。", "interview", "面试"),
        ("客户方提出需求调研的范围，我们介绍了方案沟通的下一步与合作意向。", "customer_visit", "客户拜访"),
        (NEUTRAL_SPEECH, "general", "通用"),
    ],
)
def test_real_content_still_classifies_correctly(text, expected, label):
    """去掉署名不能让真实内容判不出来 —— 误伤的代价是所有人的纪要都套错模板。"""
    assert infer_app_mode(text, "auto")[0] == expected, label
