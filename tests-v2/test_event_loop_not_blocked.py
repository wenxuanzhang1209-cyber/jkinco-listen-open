"""async 端点不得在事件循环里做阻塞工作。

服务只有一个 uvicorn worker,事件循环是所有并发请求的唯一调度点。一旦某个
async 端点同步执行了耗时操作,这段时间内**所有**其他请求都在排队 —— 会议心跳、
实时转写轮询会一起卡住,表现为「传个文件,开会的人集体掉线」。

两处实测过的阻塞点:
  ffprobe 读音频时长   一小时音频 114ms,超时上限 60 秒
  docx 模板解析        16 KB 模板 27.7ms

这类问题不会有任何报错,只有在并发下才显形。

判据不能用「是否主线程」:TestClient 本身就在非主线程里跑 ASGI,那个断言恒成立,
回退修复后测试照样通过 —— 我第一版就是这么写的,毫无价值。可靠的判据是
asyncio.get_running_loop():在事件循环线程上它返回 loop,在线程池 worker 里它抛
RuntimeError。抛异常才证明这段代码确实没有占着事件循环。
"""
import asyncio
import base64
import io
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from helpers import solve_captcha


def _is_off_event_loop() -> bool:
    """当前是否运行在事件循环之外(即线程池 worker)。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


@pytest.fixture()
def client():
    return TestClient(main.app)


def _login(client: TestClient) -> str:
    username = "loop" + uuid.uuid4().hex[:8]
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    return username


def test_audio_probe_runs_off_the_event_loop(client, monkeypatch):
    """ffprobe 必须在线程池里跑,不能占着事件循环。

    校验函数已从 audio_duration_seconds 换成 has_audio_stream(用「有无音频流」
    判定有效性,而不是「能否读出时长」—— 后者会把浏览器录音全部误判为损坏),
    这里跟着换成监视新的那个。两者都是同步 ffprobe 调用,阻塞风险相同。
    """
    _login(client)
    seen: dict[str, str] = {}

    def spy(path):
        seen["off_loop"] = _is_off_event_loop()
        return True  # 判定为有效音频,让请求继续走下去

    monkeypatch.setattr(main, "has_audio_stream", spy)
    # 只提交,不等待处理任务本身
    monkeypatch.setattr(main.EXECUTOR, "submit", lambda *a, **k: None)

    response = client.post(
        "/api/process",
        files={"audio": ("meeting.mp3", io.BytesIO(b"x" * 2048), "audio/mpeg")},
        data={"process_mode": "只转写，不推送", "app_mode": "auto"},
    )

    assert response.status_code in (200, 202), response.text
    assert "off_loop" in seen, "ffprobe 没有被调用,用例失去意义"
    assert seen["off_loop"], "ffprobe 仍在事件循环线程上执行,会卡住所有并发请求"


def test_template_parsing_runs_off_the_event_loop(client, monkeypatch):
    """docx 解析同理:解压 + 解析 XML + 落库,全是同步阻塞。"""
    _login(client)
    seen: dict[str, str] = {}

    def spy(*args, **kwargs):
        seen["off_loop"] = _is_off_event_loop()
        return {"id": "t1", "name": "样例模板"}

    monkeypatch.setattr(main, "create_template", spy)

    response = client.post(
        "/api/custom-templates",
        files={"file": ("模板.docx", io.BytesIO(b"PK\x03\x04fake"),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"name": "样例模板", "scenario": "general"},
    )

    assert response.status_code == 201, response.text
    assert "off_loop" in seen, "create_template 没有被调用,用例失去意义"
    assert seen["off_loop"], "docx 解析仍在事件循环线程上执行,会卡住所有并发请求"
