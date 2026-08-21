"""危险部件的黑名单必须按规范化后的路径匹配。

_validate_archive 里同时存在两种路径写法:

    normalized_name = item.filename.replace("\\\\", "/")   # 只换了反斜杠
    normalized = PurePosixPath(normalized_name)           # 真正规范化

路径穿越检查用的是后者(正确),而宏 / ActiveX / 嵌入对象的前缀黑名单用的是
前者 —— 于是 `./word/vbaProject.bin`、`word//vbaProject.bin` 这类等价写法能
整个绕过黑名单:PurePosixPath 会把 `./` 和 `//` 折叠掉,所以穿越检查放行;
而字符串前缀比较看到的还是带 `./` 的原样,`startswith("word/vbaproject")` 不成立。

兜底的那道 `"word/vbaProject.bin" in names` 也拦不住 —— names 里存的同样是原名。
大小写反而是处理了的(.lower()),这说明作者想到了等价写法,只是漏了路径这一维。

模板会被渲染进用户导出的 .docx 并在集团内传阅,这条链路上不该出现宏。
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-archive-"))

import pytest

from backend.custom_templates import _validate_archive

CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
DOCUMENT = '<?xml version="1.0"?><w:document xmlns:w="x"><w:body/></w:document>'


def _docx_with(extra_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", DOCUMENT)
        archive.writestr(extra_name, b"\x00\x01payload" * 40)
    return buffer.getvalue()


@pytest.mark.parametrize("name", [
    "word/vbaProject.bin",        # 直白写法 —— 原本就拦得住
    "./word/vbaProject.bin",      # 前导 ./  —— 原本能绕过
    "word//vbaProject.bin",       # 重复斜杠 —— 原本能绕过
    "WORD/VBAPROJECT.BIN",        # 大小写 —— 原本拦得住
    "./word/activex/activeX1.xml",
    "word//embeddings/oleObject1.bin",
])
def test_dangerous_parts_are_blocked_however_the_path_is_spelled(name):
    with pytest.raises(ValueError, match="宏|ActiveX|嵌入"):
        _validate_archive(_docx_with(name))


def test_ordinary_template_still_passes():
    """反向:正常模板不能被误伤。"""
    assert _validate_archive(_docx_with("word/styles.xml")) == []
