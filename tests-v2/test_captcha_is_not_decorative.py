"""注册验证码不该形同虚设。

它挡的是「脚本批量注册」。注册另有按 IP 的频次限制,登录另有失败计数与锁定 ——
验证码不承担强防护。但原实现有两处让它退化成纯装饰,两处都是一行脚本就能过:

  1. token 是 base64(答案:过期:nonce:签名) —— 答案明文写在里面。HMAC 只防伪造,
     不防读取,base64 解一下就拿到了。
  2. SVG 里的题目是 <text> 纯文本(「3 + 5 = ?」),连 token 都不用解,
     取文字就能算。

只修其中一条没有意义:修了 token 还能读 SVG 文字,修了 SVG 还能解 token。
现在答案只以 HMAC 摘要进 token,题目用 7 段数码管的 <path> 画,全图无文本节点。
这挡不住 OCR,也从没打算挡 —— 目标是让上面那两种一行脚本失效。
"""
from __future__ import annotations

import base64
import re

import pytest

from backend.auth import _DIGIT_SEGMENTS, _digit_path, make_captcha, verify_captcha

# 标准 7 段数码管编码,独立于实现里那张表 —— 两边对不上就是画错了
STANDARD_SEGMENTS = {
    1: set("bc"), 2: set("abdeg"), 3: set("abcdg"), 4: set("bcfg"),
    5: set("acdfg"), 6: set("acdefg"), 7: set("abc"), 8: set("abcdefg"),
}


def _svg_of(captcha: dict) -> str:
    return base64.b64decode(captcha["image"].split(",", 1)[1]).decode()


def _solve(captcha: dict) -> int | None:
    """穷举 2..16,找出唯一能通过校验的答案。"""
    hits = [value for value in range(2, 17) if verify_captcha(captcha["token"], str(value))]
    return hits[0] if len(hits) == 1 else None


def test_the_answer_is_not_readable_from_the_token():
    """核心回归:base64 解开 token 不能得到答案。"""
    captcha = make_captcha()
    answer = _solve(captcha)
    assert answer is not None, "验证码本身坏了,没有唯一解"
    decoded = base64.urlsafe_b64decode(captcha["token"].encode()).decode()
    # 答案是 2..16 的一个数;它不该以独立字段的形式出现在 token 里
    first_field = decoded.split(":", 1)[0]
    assert first_field != str(answer), "答案仍明文写在 token 的第一段里"
    assert len(first_field) == 64, "第一段应是 sha256 摘要"


def test_the_question_is_not_readable_as_text():
    """SVG 里不能有任何文本节点 —— 取文字就能算的话,画得再花也没用。"""
    svg = _svg_of(make_captcha())
    assert "<text" not in svg, "SVG 里仍有 <text> 节点"
    assert not "".join(re.findall(r">([^<]+)<", svg)).strip(), "SVG 里仍能提取到文字"


def test_the_correct_answer_still_passes():
    for _ in range(20):
        captcha = make_captcha()
        assert _solve(captcha) is not None, "存在无解或多解的验证码"


def test_wrong_answers_are_rejected():
    captcha = make_captcha()
    answer = _solve(captcha)
    for value in range(2, 17):
        if value != answer:
            assert not verify_captcha(captcha["token"], str(value))


@pytest.mark.parametrize("token", ["", "!!!not-base64!!!", base64.urlsafe_b64encode(b"a:b:c:d").decode()])
def test_malformed_tokens_are_rejected_without_crashing(token):
    assert verify_captcha(token, "8") is False


def test_full_width_digits_do_not_crash():
    captcha = make_captcha()
    assert verify_captcha(captcha["token"], "８") is False


def test_a_token_cannot_be_forged_for_a_chosen_answer():
    """签名仍要挡住「自己造一个 token」。"""
    import hashlib
    import hmac

    forged_digest = hashlib.sha256(b"8:whatever").hexdigest()
    forged = base64.urlsafe_b64encode(f"{forged_digest}:9999999999:whatever:{'0'*64}".encode()).decode()
    assert verify_captcha(forged, "8") is False


@pytest.mark.parametrize("digit", sorted(STANDARD_SEGMENTS))
def test_digits_match_the_standard_seven_segment_encoding(digit):
    """画错一段,用户就认不出题目 —— 而这是没人会写用例去查的那种错。"""
    assert set(_DIGIT_SEGMENTS[digit]) == STANDARD_SEGMENTS[digit]


@pytest.mark.parametrize("digit", sorted(STANDARD_SEGMENTS))
def test_digit_strokes_stay_inside_their_cell(digit):
    path = _digit_path(digit, 18, 13, 12, 18)
    points = [(int(x), int(y)) for x, y in re.findall(r"[ML](\d+) (\d+)", path)]
    assert points, "没画出任何线段"
    assert all(18 <= x <= 30 and 13 <= y <= 31 for x, y in points), f"{digit} 画出了单元格"
