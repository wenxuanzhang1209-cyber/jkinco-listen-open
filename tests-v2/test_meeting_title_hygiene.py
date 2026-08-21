"""会议标题是要显示给别人看的,必须先收敛再落库。

同一个代码库里自定义模板的名字早就过 _clean_name 了,会议标题却只校验长度
(min_length=1, max_length=80)。实测能原样存进去的有:控制字符、空字节、
双向文本覆写符,以及纯空白 —— 纯空白的标题在会议列表里就是一片空白,
谁也认不出那是哪场会。

双向覆写符(U+202A–U+202E、U+2066–U+2069)值得单独说:它们在 XML 里完全合法,
strip_control_characters 不会碰,也不该碰。但它们会让后面的文字按相反方向渲染 ——
标题会显示在别人的会议列表里,那是个能骗到人的位置。LRM/RLM 不清:那两个是
混排文本的正常用法。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.main import app
from jkinco_text import clean_display_title

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")

DANGEROUS = "\x00\x0b\x1f‪‫‬‭‮⁦⁧⁨⁩"


@pytest.mark.parametrize(
    "raw, expected, label",
    [
        ("标题\x00\x0b", "标题", "控制字符"),
        ("a\x00b", "ab", "空字节"),
        ("abc‮gfd", "abcgfd", "RTL 覆写"),
        ("⁦伪装⁩", "伪装", "双向隔离符"),
        ("   ", "未命名会议", "纯空白"),
        ("  多余   空白  ", "多余 空白", "多余空白压平"),
        ("项目周会", "项目周会", "正常标题原样保留"),
        ("周会 🎉", "周会 🎉", "emoji 不得误删"),
        ("长" * 200, "长" * 80, "超长按上限截断"),
    ],
)
def test_clean_display_title(raw, expected, label):
    assert clean_display_title(raw) == expected, label


def test_lrm_and_rlm_are_left_alone():
    """那两个是混排文本的正常用法,不属于伪装手段。"""
    assert clean_display_title("abc‎‏def") == "abc‎‏def"


def test_created_meeting_title_is_clean():
    """接口层必须真的接上 —— 单测函数正确但没被调用是最常见的失效方式。"""
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        for raw in ("标题\x00\x0b", "abc‮gfd", "   "):
            response = client.post("/api/meetings", json={"title": raw})
            assert response.status_code == 200, response.text
            title = response.json()["title"]
            assert not any(char in title for char in DANGEROUS), f"危险字符落库了:{title!r}"
            assert title.strip(), "标题不该是空白"


def test_normal_titles_still_round_trip():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        response = client.post("/api/meetings", json={"title": "三标段周例会 🎉"})
        assert response.json()["title"] == "三标段周例会 🎉"
