"""场景分类器要能从模型的真实输出里取出 JSON。

原先用 re.search(r"\\{.*\\}", raw, re.S) —— re.S 配上贪婪的 .*,匹配范围是
「第一个 { 到最后一个 }」。模型只要在 JSON 之外多写一对花括号,整段就解析失败。

失败是静默的,这才是它值得修的原因:异常被上层接住,退回本地规则兜底,接口
照常返回 200,场景可能判错,而用户只看到「场景复核暂不可用:Expecting value:
line 1 column 1 (char 0)」这样一句 Python 报错。没有任何告警,也没人会去查。

实测两种输出会中招,都是模型的常见行为:
  - 解释里复述了转写中的花括号(「转写提到 {项目A}」);
  - 先吐一个思考对象、再给答案(两个并列的 JSON)。
"""
from __future__ import annotations

import json

import pytest

import jkinco_classifier as classifier


REALISTIC_OUTPUTS = [
    ('{"scene":"personal","reason":"个人备忘"}', "personal", "裸 JSON"),
    ('```json\n{"scene":"personal","reason":"个人备忘"}\n```', "personal", "markdown 包裹"),
    ('根据转写内容判断：\n{"scene":"interview","reason":"面试"}', "interview", "前面带解释"),
    ('转写提到 {项目A} 的情况：\n{"scene":"general","reason":"通用"}', "general", "解释里含花括号"),
    ('{"thought":"先看关键词"}\n{"scene":"talk","reason":"工程"}', "talk", "思考块+答案"),
    ('{"scene":"general","reason":"通用"}\n以上为判断结果。', "general", "答案后带收尾"),
    ('{"scene":"general","reason":"提到了{预算}"}', "general", "reason 内含花括号"),
    ('{"scene":"general","reason":"公式 a} b"}', "general", "reason 内含右花括号"),
    ('{"scene":"personal","meta":{"n":1},"reason":"备忘"}', "personal", "嵌套对象"),
]


@pytest.mark.parametrize("raw, expected, label", REALISTIC_OUTPUTS)
def test_scene_is_extracted_from_realistic_model_output(raw, expected, label):
    assert classifier._parse_scene_payload(raw).get("scene") == expected, f"{label} 解析失败"


def test_the_old_greedy_regex_would_have_failed_these():
    """自检:如果这些用例在旧实现下也能过,那它们就没在守任何东西。"""
    import re

    def old_way(raw):
        match = re.search(r"\{.*\}", raw, re.S)
        return json.loads(match.group(0) if match else raw)

    broken = []
    for raw, _expected, label in REALISTIC_OUTPUTS:
        try:
            old_way(raw)
        except ValueError:
            broken.append(label)
    assert "解释里含花括号" in broken and "思考块+答案" in broken, (
        "这两种输出本应是旧实现的失败场景,用例失去了针对性"
    )


def test_answer_wins_over_a_preceding_thought_object():
    """两个对象并列时必须取带 scene 的那个,而不是第一个。"""
    raw = '{"reason":"这是思考,没有 scene"}\n{"scene":"customer_visit","reason":"客户拜访"}'
    assert classifier._parse_scene_payload(raw)["scene"] == "customer_visit"


def test_output_without_any_json_still_raises():
    """完全没有 JSON 时要照常抛错 —— 上层据此退回规则兜底,这个行为不能变。"""
    with pytest.raises(ValueError):
        classifier._parse_scene_payload("我无法判断这段转写的场景。")


def test_unbalanced_braces_do_not_hang_or_crash():
    for raw in ("{" * 500, "}" * 500, '{"scene":"general"' , '{"a":"' + "{" * 200):
        with pytest.raises(ValueError):
            classifier._parse_scene_payload(raw)


def test_scanner_skips_braces_inside_strings():
    chunks = list(classifier._iter_json_objects('{"a":"}{"}'))
    assert chunks == ['{"a":"}{"}'], "字符串内部的括号被当成了结构括号"


def test_scanner_handles_escaped_quotes():
    chunks = list(classifier._iter_json_objects(r'{"a":"say \"hi\" }"}'))
    assert len(chunks) == 1 and json.loads(chunks[0])["a"] == 'say "hi" }'


# --- 兜底文案同样是要显示给用户的内容 ---

def test_fallback_reason_is_sanitized():
    """复核失败那条路径原先直接插值 {error},绕开了对模型 reason 做的净化。

    这句会随「识别理由」显示给参会成员并写进历史记录 —— 模型侧的报错可以很长、
    可以带换行,不收敛就能把界面撑坏或伪造成多行提示。
    """
    from unittest.mock import patch

    noisy = RuntimeError("第一行\n第二行\r\n" + "x" * 500)
    with patch.object(classifier, "call_llm", side_effect=noisy):
        _mode, reason = classifier.infer_app_mode_best_effort("今天讨论一下下周安排。", "auto")
    tail = reason.split("已使用本地规则兜底：", 1)[1]
    assert "\n" not in tail and "\r" not in tail, "兜底文案里仍有换行"
    assert len(tail) <= classifier.MAX_MODEL_REASON_CHARS, f"兜底文案未收敛,长 {len(tail)}"


def test_fallback_still_returns_the_rule_based_scene():
    """净化不能把兜底本身改坏:模型不可用时仍要给出规则判定的场景。"""
    from unittest.mock import patch

    with patch.object(classifier, "call_llm", side_effect=RuntimeError("模型不可用")):
        mode, reason = classifier.infer_app_mode_best_effort("今天讨论一下下周安排。", "auto")
    assert mode in {"talk", "general", "personal", "interview", "customer_visit"}
    assert "场景复核暂不可用" in reason
