"""外接录音设备接口的安全契约。

发现(实测复现):接入设备后,任意登录用户都能拿到
  - 服务器绝对路径(暴露部署目录结构)
  - 完整目录层级与文件名,而文件名常带业务信息(实测样例「张总_并购谈判」
    「李工_投标底价」「20260728_密谈.wav」)
且不做任何归属校验 —— 录音属于谁、谁该看得见,完全没有区分。

据实查明的调用情况(据此选择默认关闭而非仅脱敏):
  - Web 前端不调用该接口(「筑听读取」页只展示静态说明);
  - 桌面端 Gradio 单体在进程内直调 recorder_dropdown_data(),不经 HTTP;
  - 仓库内无任何脚本调用。
公网部署下它没有消费方,只是攻击面。
"""
import base64
import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient
from helpers import solve_captcha


def _device_with_sensitive_names() -> str:
    root = pathlib.Path(tempfile.mkdtemp(prefix="jk-device-")) / "JKINCO_REC"
    (root / "张总_并购谈判").mkdir(parents=True)
    (root / "张总_并购谈判" / "20260728_密谈.wav").write_bytes(b"RIFF0000")
    (root / "李工_投标底价").mkdir(parents=True)
    (root / "李工_投标底价" / "报价复核.mp3").write_bytes(b"ID3000")
    return str(root)


def _logged_in_client(username: str) -> TestClient:
    import backend.main as main

    client = TestClient(main.app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        login = client.post("/api/auth/login", json={"username": username, "password": "StrongPass123"})
        assert login.status_code == 200, login.text
    return client


def test_disabled_by_default_returns_nothing(monkeypatch):
    """默认必须关闭:公网部署下无消费方,不应对外暴露任何设备信息。"""
    import backend.main as main

    monkeypatch.setattr(main, "DEVICE_READER_ENABLED", False)
    monkeypatch.setenv("JKINCO_RECORDER_ROOTS", _device_with_sensitive_names())

    body = _logged_in_client("devuser1").get("/api/device/recordings").json()
    assert body["items"] == []
    assert "未启用" in body["status"]


def test_default_flag_value_is_off():
    """守住默认值本身:有人把默认改成开启时必须失败。"""
    import importlib

    import backend.main as main

    previous = os.environ.pop("JKINCO_DEVICE_READER_ENABLED", None)
    try:
        importlib.reload(main)
        assert main.DEVICE_READER_ENABLED is False, "设备读取默认必须关闭"
    finally:
        if previous is not None:
            os.environ["JKINCO_DEVICE_READER_ENABLED"] = previous
        importlib.reload(main)


def test_absolute_paths_are_never_returned_even_when_enabled(monkeypatch):
    """核心回归:即使显式开启,也不得回显服务器绝对路径。

    没有任何接口接受客户端传入的路径,回显它对功能毫无用处,只暴露部署目录结构。
    """
    import backend.main as main

    device_root = _device_with_sensitive_names()
    monkeypatch.setattr(main, "DEVICE_READER_ENABLED", True)
    monkeypatch.setenv("JKINCO_RECORDER_ROOTS", device_root)

    response = _logged_in_client("devuser2").get("/api/device/recordings")
    assert response.status_code == 200
    raw = response.text

    assert device_root not in raw, "响应中出现了服务器绝对路径"
    assert "/var/" not in raw and "/tmp/" not in raw, f"疑似绝对路径泄露: {raw[:300]}"
    for item in response.json()["items"]:
        assert "path" not in item, "items 中不应再包含 path 字段"


def test_anonymous_cannot_reach_the_endpoint():
    import backend.main as main

    assert TestClient(main.app).get("/api/device/recordings").status_code == 401


def test_desktop_in_process_api_is_untouched(monkeypatch):
    """桌面端走进程内直调,必须仍能拿到真实路径 —— 修复不能波及它。"""
    monkeypatch.setenv("JKINCO_RECORDER_ROOTS", _device_with_sensitive_names())

    import jkinco_devices as devices

    choices, status = devices.recorder_dropdown_data()
    assert choices, "桌面端应仍能发现设备录音"
    for _label, path in choices:
        assert os.path.isabs(path), "进程内调用仍需返回可直接打开的绝对路径"
