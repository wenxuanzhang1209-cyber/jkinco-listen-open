"""本地强证据必须优先于模型建议 —— 包括模型说「工程例会」的时候。

发现路径:覆盖率显示 jkinco_classifier.py 只有 67%,而未覆盖的 368-398 正是
「大模型调用成功」之后的全部决策逻辑。测试套件把大模型端点指向不可达地址,
于是 call_llm 必然抛异常、直接走 except —— 也就是说**线上真正会跑的那条主路径,
一行都没测过**。

补测时发现的 bug:infer_app_mode_best_effort 里的判断顺序是

    if scene == deterministic_mode: ...
    if scene == "talk" and deterministic_mode != "talk":   # ← 先
        return "general", "...保守归入通用会议纪要..."
    strong_rule = any(marker in deterministic_reason for marker in
                      ["自动识别为工程例会", "面试强证据", "客户拜访强证据", "个人事项强证据"])
    if strong_rule:                                        # ← 后,到不了
        return deterministic_mode, "...本地强证据优先..."

「模型说 talk」这道保守闸排在强证据之前,所以模型一旦误判成工程例会,
连「面试强证据」「客户拜访强证据」这种本地高置信判定也会被丢掉,结果退化成
general。实测:

    面试强证据   规则=interview      + 模型说 talk → general   ❌
    客户拜访强证据 规则=customer_visit + 模型说 talk → general   ❌
    (模型说 personal / general 时强证据正常胜出,只有 talk 会击穿)

后果是用户拿到的模板不对:一段面试录音套上通用会议纪要,该有的
「候选人评价」章节全没有,而纪要是要归档的正式记录。
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-scene-strong-"))

import pytest

import jkinco_classifier as classifier

INTERVIEW = (
    "今天面试候选人张工，请他自我介绍一下工作经历。"
    "你在上一家单位负责哪些项目？期望薪资是多少？评价一下他的专业能力和沟通表达。"
)
CUSTOMER_VISIT = (
    "今天拜访客户李总，介绍我们的服务方案，了解他们的采购意向和预算。"
    "商务洽谈了报价，客户对合作意向比较积极。"
)


def _model_says(scene: str):
    payload = json.dumps({"scene": scene, "reason": "讨论了施工进度"}, ensure_ascii=False)
    return lambda *args, **kwargs: payload


@pytest.mark.parametrize("transcript,expected", [
    (INTERVIEW, "interview"),
    (CUSTOMER_VISIT, "customer_visit"),
])
def test_strong_rule_survives_a_talk_suggestion(transcript, expected):
    """模型误判成工程例会时，本地强证据不能被丢掉。"""
    assert classifier.infer_app_mode(transcript, "auto")[0] == expected, "前提不成立:规则本身没判出强证据"
    with patch.object(classifier, "call_llm", side_effect=_model_says("talk")):
        mode, reason = classifier.infer_app_mode_best_effort(transcript, "auto")
    assert mode == expected, f"强证据被模型的 talk 建议击穿，退化成 {mode}"
    assert "强证据" in reason, f"理由里应说明是强证据优先，实际:{reason}"


@pytest.mark.parametrize("model_scene", ["personal", "general", "interview"])
def test_strong_rule_also_survives_other_suggestions(model_scene):
    """反向对照:换成别的建议时本来就是对的，修复不能把这些弄坏。"""
    with patch.object(classifier, "call_llm", side_effect=_model_says(model_scene)):
        mode, _ = classifier.infer_app_mode_best_effort(CUSTOMER_VISIT, "auto")
    assert mode == "customer_visit"


def test_weak_rules_still_defer_conservatively_on_talk():
    """没有强证据时，模型说工程例会仍应保守归入通用 —— 这条原本的意图要保留。"""
    vague = "今天我们碰个头，把上周的事情过一遍，然后看看接下来怎么安排。"
    assert classifier.infer_app_mode(vague, "auto")[0] == "general"
    with patch.object(classifier, "call_llm", side_effect=_model_says("talk")):
        mode, reason = classifier.infer_app_mode_best_effort(vague, "auto")
    assert mode == "general"
    assert "工程证据链" in reason or "保守" in reason
