"""会议聊天是发给别人看的,同样要先收敛。

与转写署名那条不同,聊天不进纪要、不进模型 —— 只被聊天列表读取。所以这纯粹是
展示层的问题,严重性低得多。但同一类字符在显示名和会议标题上都已经收敛了,
留一处不管是任意的:

  - 控制字符在界面上是空白,却会混进搜索;
  - 双向覆写符(U+202A–U+202E、U+2066–U+2069)让后面的文字反向渲染,
    可以把一条消息显示成另一条。

聊天与标题的处理刻意不同:**保留换行**。多行消息是正常内容,压平会改变用户
实际写的东西。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.meetings as meeting_service
from backend.main import app
from jkinco_text import clean_message_text

ADMIN_USERNAME, _, ADMIN_PASSWORD = meeting_service.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


@pytest.fixture
def room():
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        meeting = client.post("/api/meetings", json={"title": "聊天收敛"}).json()
        client.post(f"/api/meetings/{meeting['meeting_code']}/join", json={"display_name": "管理员"})
        yield client, meeting


@pytest.mark.parametrize(
    "raw, expected, label",
    [
        ("消息\x00\x0b", "消息", "控制字符"),
        ("abc‮gfd", "abcgfd", "RTL 覆写"),
        ("第一行\n第二行", "第一行\n第二行", "多行必须原样保留"),
        ("收到 🎉", "收到 🎉", "emoji"),
        ("  两边空白  ", "两边空白", "两边空白"),
    ],
)
def test_clean_message_text(raw, expected, label):
    assert clean_message_text(raw) == expected, label


def test_sent_message_is_clean(room):
    client, meeting = room
    response = client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "消息\x00abc‮gfd"})
    assert response.status_code == 200, response.text
    stored = response.json()["message"]
    assert not any(char in stored for char in "\x00\x0b‮")


def test_a_multiline_message_is_not_flattened(room):
    """和标题不同:聊天保留换行。"""
    client, meeting = room
    response = client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "第一行\n第二行"})
    assert response.json()["message"] == "第一行\n第二行"


@pytest.mark.parametrize("blank", ["   ", "\x00\x0b", "‮"])
def test_a_message_that_cleans_to_nothing_is_rejected(room, blank):
    """min_length=1 挡的是原始长度;清洗后可能什么都不剩,那会在所有人的聊天里留个空气泡。"""
    client, meeting = room
    response = client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": blank})
    assert response.status_code == 400, f"空消息被接受了:{response.text[:80]}"


def test_normal_messages_still_work(room):
    client, meeting = room
    response = client.post(f"/api/meetings/{meeting['id']}/chat", json={"message": "收到，我这边没问题"})
    assert response.status_code == 200
    assert response.json()["message"] == "收到，我这边没问题"
