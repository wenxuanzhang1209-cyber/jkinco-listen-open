"""纠错表要照着模型真正会犯的错写，不是照着「人会怎么写错」写。

来历：拿 10 段加噪的工程会议音频过一遍本地 paraformer-zh，命中 36 个目标词中的 30 个，
6 处失误全落在工程术语上。而当时表里那 13 条纠错**一条都没救回来**——因为想象中的
错法和模型实际犯的错完全不是一回事：

    表里写的      模型实际给出的
    旁占          航站记录        「航站」是常见词，模型更愿意选它
    危大公程      微大工程        声母近似
    塌落度        石滩落度        连前面的「时」一起听错了
    检验坯        （没撞到）
    见证取洋      （没撞到）

补进这五条之后，那四段从 7/12 变成 12/12。

收录标准没有放宽：错误形式在中文里必须**根本不成词**，替换才不可能误伤。
「航站」本身是词（航站楼），但「航站记录」不是——所以替换的是四字整体，不是「航站」。
"""
from __future__ import annotations

import pytest

from jkinco_lexicon import DOMAIN_CORRECTIONS, correct_domain_terms

#: 本地 paraformer-zh 在加噪工程音频上的真实输出（截取），以及期望修复的词。
REAL_ASR_OUTPUT = [
    ("监理单位对这个检验批做了平行检验，航站记录已经归档。", "旁站记录"),
    ("这个属于微大工程专项施工方案，必须经过专家论证。", "危大工程"),
    ("钢筋原材富是要见证取样，送检见证员和取样员都要签字。", "钢筋原材复试"),
    ("混凝土进场石滩落度不合格，同条件是块也要重做。", "坍落度"),
    ("混凝土进场石滩落度不合格，同条件是块也要重做。", "同条件试块"),
]


@pytest.mark.parametrize("transcript, expected", REAL_ASR_OUTPUT)
def test_real_asr_mistakes_are_corrected(transcript, expected):
    assert expected in correct_domain_terms(transcript), (
        f"这是本地模型真实犯过的错，纠错表应当认得：{transcript!r}"
    )


#: 这些必须**不**被改动。每一条都是「看起来像错法、其实是正常中文」。
MUST_NOT_TOUCH = [
    "航站楼今天很拥挤",                 # 「航站」本身是词
    "这块石滩上有很多游客",             # 「石滩」本身是词
    "他是块好料子",                     # 「是块」在口语里成立
    "微大之间的差别很小",               # 「微」「大」相邻但不成词组
    "原材料很富足",                     # 「富」出现但不是「富是」
    "经理例会定在周五",                 # 经理开的会，不该改成监理
    "浇注成型是铸造工艺",               # 浇注在冶金里就是这个词
    "隐蔽验收已经完成",                 # 「隐蔽工程验收」的通行简称
]


@pytest.mark.parametrize("text", MUST_NOT_TOUCH)
def test_normal_chinese_is_never_damaged(text):
    """宁可漏掉一些，也不要改坏一句对的。

    「航站」「石滩」「是块」单独都是合法中文，所以纠错项必须是更长的整体
    （航站记录 / 石滩落度 / 同条件是块），不能只匹配那两个字。
    """
    assert correct_domain_terms(text) == text, f"改坏了正常中文：{text!r}"


def test_every_correction_target_is_not_a_word_on_its_own():
    """收录标准的自检：错误形式必须足够长、足够不成词。

    两字的错误形式极易误伤（「旁占」是唯一例外，它确实不成词），
    新增的几条都是四字以上的整体。
    """
    short = [wrong for wrong, _ in DOMAIN_CORRECTIONS if len(wrong) < 3]
    assert short == ["旁占"], (
        f"出现了新的短错误形式，误伤风险高，请确认它在中文里真的不成词：{short}"
    )


def test_corrections_do_not_chain():
    """一条纠错的输出不能是另一条的输入 —— 那会让结果依赖字典顺序。"""
    rights = {right for _, right in DOMAIN_CORRECTIONS}
    chained = [w for w, _ in DOMAIN_CORRECTIONS if any(w in r for r in rights)]
    assert not chained, f"这些错误形式出现在别的纠错结果里，会造成连锁替换：{chained}"
