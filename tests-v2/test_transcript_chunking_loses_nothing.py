"""长转写分块不能丢内容。

超过 LLM_DIRECT_MAX_CHARS 的转写会被 split_text 切成多块分别提取、再合成纪要。
丢一块就是纪要里少了一段会议内容 —— 而这种缺失没有任何迹象:纪要照样通顺、
照样有结论,只是那十分钟的讨论不见了,读的人无从察觉。

现有用例只是「用」这个函数(断言切出了几块),没有一条守住这个不变量。
"""
from __future__ import annotations

import pytest

from jkinco_prompts import LLM_CHUNK_CHARS, split_text


def _visible(text: str) -> str:
    """比较时忽略空白 —— split_text 会 strip 每一块,那是有意的。"""
    return "".join(character for character in text if not character.isspace())


CASES = {
    "正常中文带句号": "这是一句话。" * 3000,
    "完全没有标点": "工程" * 12000,
    "标点全挤在前 60%": "。" * 100 + "无标点内容" * 3000,
    "恰好等于分块上限": "字" * LLM_CHUNK_CHARS,
    "上限加一": "字" * (LLM_CHUNK_CHARS + 1),
    "只有标点": "。！？；\n" * 2000,
    "超长单句": "这是一个非常长的句子没有任何标点" * 6000,
    "混合换行": ("段落内容\n" * 500 + "。") * 30,
}


@pytest.mark.parametrize("label", sorted(CASES))
def test_no_content_is_lost(label):
    text = CASES[label]
    assert _visible("".join(split_text(text))) == _visible(text), f"{label}:分块丢了内容"


@pytest.mark.parametrize("label", sorted(CASES))
def test_no_chunk_exceeds_the_limit(label):
    """超长的块会让单次模型调用超预算,而那一块的提取会整块失败。"""
    for chunk in split_text(CASES[label]):
        assert len(chunk) <= LLM_CHUNK_CHARS, f"{label}:出现 {len(chunk)} 字的块"


@pytest.mark.parametrize("label", sorted(CASES))
def test_order_is_preserved(label):
    """块必须按原顺序 —— 乱序会让「先讨论后决议」在纪要里颠倒。"""
    text = CASES[label]
    cursor = 0
    for chunk in split_text(text):
        stripped = _visible(chunk)
        if not stripped:
            continue
        position = _visible(text).find(stripped, cursor)
        assert position >= cursor, f"{label}:块的顺序乱了"
        cursor = position + len(stripped)


@pytest.mark.parametrize("text", ["", "   \n\t  ", "短文本"])
def test_trivial_inputs(text):
    chunks = split_text(text)
    assert _visible("".join(chunks)) == _visible(text)


def test_splitting_prefers_sentence_boundaries():
    """能在句号处断就不要从句子中间断 —— 半句话送进模型提取不出完整事实。

    句长必须不能整除分块长度,否则硬切也会「恰好」落在句末,这条就成了恒真。
    第一版用了 10 字的句子配 1000 字的块,把断句逻辑整个关掉后用例照样通过。
    这里用 7 字句配 1000 字块:1000/7 除不尽,硬切必然落在句子中间。
    """
    sentence = "完整的句子。"  # 6 字
    text = ("一" + sentence) * 3000  # 7 字一句
    chunks = split_text(text, max_chars=1000)
    ending_well = sum(1 for chunk in chunks if chunk.endswith("。"))
    assert ending_well >= len(chunks) - 1, f"只有 {ending_well}/{len(chunks)} 块在句末断开"


def test_does_not_hang_on_pathological_input():
    """没有标点、且长度恰好落在边界上的输入不能让循环停住。"""
    for size in (LLM_CHUNK_CHARS - 1, LLM_CHUNK_CHARS, LLM_CHUNK_CHARS + 1, LLM_CHUNK_CHARS * 3):
        chunks = split_text("字" * size)
        assert chunks and sum(len(chunk) for chunk in chunks) == size
