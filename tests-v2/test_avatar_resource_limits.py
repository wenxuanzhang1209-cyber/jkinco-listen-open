"""头像上传是一条没有护栏的 CPU/内存密集路径。

_build_avatar_data_uri 的注释自己写明了它是 CPU 密集的(解码 + 缩放 + WebP
method=6),却只有两道限制:文件 2MB、以及 PIL 自带的 89 兆像素阈值。两道都不够:

  - 2MB 挡不住解压炸弹。纯色 PNG 压缩比极高 —— 实测一张 75KB 的 PNG 可以是
    80 兆像素,刚好压在 PIL 阈值下方,解码后让进程多吃约 650MB、耗时约 0.5 秒。
  - 这个接口原先没有任何限流(enforce_expensive_rate_limit 只用在
    classify/assistant/export/dingtalk 上),一个账号 —— 含免注册访客 ——
    连续上传就能持续占着核与内存。生产那台机器只有 2 核。

最终存的头像是 256×256,任何超过几百万像素的输入都是纯浪费,所以尺寸判断放在
解码之前(Image.open 只读文件头),这是唯一能在付出内存代价之前拦住的位置。
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.main as main
from backend.main import app

ADMIN_USERNAME, _, ADMIN_PASSWORD = main.os.environ["JKINCO_AUTH"].split(",", 1)[0].partition(":")


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, "PNG")
    return buffer.getvalue()


def _bomb(width: int, height: int) -> bytes:
    """纯色大图:文件很小,解码后很大。"""
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None          # 只为「造」这张图,造完立刻还原
    try:
        buffer = io.BytesIO()
        Image.new("L", (width, height), 0).save(buffer, "PNG")
        return buffer.getvalue()
    finally:
        Image.MAX_IMAGE_PIXELS = previous


@pytest.fixture
def client():
    with TestClient(app) as session:
        session.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        yield session


def _upload(client, data: bytes):
    return client.put("/api/profile", files={"avatar": ("a.png", data, "image/png")},
                      data={"display_name": "管理员"})


def test_a_small_file_that_decodes_huge_is_rejected_before_decoding(client):
    """核心回归:75KB / 80 兆像素,压在 PIL 阈值下方。"""
    data = _bomb(10000, 8000)
    assert len(data) < 2 * 1024 * 1024, "这张图本身就超过 2MB,测不到要测的东西"
    response = _upload(client, data)
    assert response.status_code == 413, f"解压炸弹被接受了:{response.status_code}"
    assert "分辨率过高" in response.text


def test_real_camera_photos_are_still_accepted(client):
    """误伤代价很高:手机直出照片必须能当头像。"""
    for width, height, label in [(4000, 3000, "12MP 手机"), (8000, 6000, "48MP 手机")]:
        response = _upload(client, _png(width, height))
        assert response.status_code == 200, f"{label} 被拒了:{response.text[:80]}"


def test_the_limit_leaves_room_above_real_cameras():
    """阈值不能定得比真实设备还低 —— 那会变成天天误伤。"""
    assert main.MAX_AVATAR_PIXELS >= 48_000_000


def test_pil_own_guard_still_covers_the_extreme_case(client):
    """远超阈值的那些仍由 PIL 自己挡下,两道防线都要在。"""
    response = _upload(client, _bomb(20000, 20000))
    assert response.status_code in {400, 413}


def test_avatar_upload_is_rate_limited(client):
    """没有限流的话,合法尺寸的图也能被连续上传持续占核。"""
    small = _png(400, 400)
    codes = [_upload(client, small).status_code for _ in range(main.EXPENSIVE_OPERATION_LIMITS["avatar"] + 4)]
    assert 429 in codes, f"头像上传没有额度限制:{codes}"


def test_changing_only_the_display_name_is_not_rate_limited(client):
    """额度只该计在真正要处理图片的请求上。"""
    for _ in range(12):
        assert client.put("/api/profile", data={"display_name": "管理员"}).status_code == 200
