"""_validate_archive 每一道防线的表征测试。

这个函数是全库复杂度最高的一处(58 行、28 个分支、嵌套 8 层),而且是安全校验:
模板会被渲染进用户导出的 .docx 并在集团内传阅。原有测试覆盖了其中四道
(不安全路径、压缩比、XML 实体、外部资源关系),另外几道没有独立的测试 ——
它们全靠这一大坨代码碰巧还对。

这里把每一道单独钉住,一是补网,二是给后续的结构重构留一个可回归的基准:
重构只该改变代码的形状,不该改变任何一条的判定。
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-defense-"))

import pytest

import backend.custom_templates as templates
from backend.custom_templates import _validate_archive

CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)
DOCUMENT = '<?xml version="1.0"?><w:document xmlns:w="x"><w:body/></w:document>'


def _build(entries: dict[str, bytes | str], *, content_types: str = CONTENT_TYPES) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", DOCUMENT)
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_a_plain_template_passes_with_no_warnings():
    assert _validate_archive(_build({"word/styles.xml": "<x/>"})) == []


def test_missing_required_parts_are_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", DOCUMENT)  # 少了 [Content_Types].xml
    with pytest.raises(ValueError, match="不是有效的 DOCX"):
        _validate_archive(buffer.getvalue())


def test_a_file_that_is_not_a_zip_is_rejected():
    with pytest.raises(ValueError, match="不是有效的 DOCX"):
        _validate_archive(b"this is plainly not a zip archive")


def test_symlink_entries_are_rejected():
    """符号链接可以指向压缩包外 —— 渲染时会读到服务器上的任意文件。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", DOCUMENT)
        info = zipfile.ZipInfo("word/link.xml")
        info.external_attr = (0o120000 | 0o777) << 16  # S_IFLNK
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match="符号链接"):
        _validate_archive(buffer.getvalue())


def test_too_many_entries_are_rejected(monkeypatch):
    monkeypatch.setattr(templates, "MAX_ARCHIVE_ENTRIES", 4)
    payload = _build({f"word/part{i}.xml": "<x/>" for i in range(8)})
    with pytest.raises(ValueError, match="文件数量异常"):
        _validate_archive(payload)


def test_oversized_uncompressed_total_is_rejected(monkeypatch):
    monkeypatch.setattr(templates, "MAX_UNCOMPRESSED_BYTES", 500)
    with pytest.raises(ValueError, match="解压后体积过大"):
        _validate_archive(_build({"word/big.bin": os.urandom(4000)}))


def test_oversized_xml_total_is_rejected(monkeypatch):
    monkeypatch.setattr(templates, "MAX_XML_BYTES", 200)
    # 用随机字节避免先踩到压缩比那道
    blob = os.urandom(3000).hex()
    with pytest.raises(ValueError, match="XML 内容体积异常"):
        _validate_archive(_build({"word/big.xml": blob}))


def test_macro_enabled_content_types_are_rejected():
    macro_types = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>'
        "</Types>"
    )
    with pytest.raises(ValueError, match="宏"):
        _validate_archive(_build({}, content_types=macro_types))


def test_corrupt_rels_is_reported_as_such():
    with pytest.raises(ValueError, match="关系文件已损坏"):
        _validate_archive(_build({"word/_rels/document.xml.rels": "<<<not xml"}))


def test_external_hyperlink_is_allowed_but_warned():
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="r1" Target="https://example.com" TargetMode="External" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"/>'
        "</Relationships>"
    )
    assert _validate_archive(_build({"word/_rels/document.xml.rels": rels})) == [
        "模板包含外部超链接，导出时将原样保留"
    ]


def test_duplicate_warnings_are_collapsed():
    """同一种警告出现多次只报一次 —— 否则界面会刷出一串一模一样的提示。"""
    link = (
        '<Relationship Id="r{i}" Target="https://example.com/{i}" TargetMode="External" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"/>'
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(link.format(i=i) for i in range(5))
        + "</Relationships>"
    )
    assert len(_validate_archive(_build({"word/_rels/document.xml.rels": rels}))) == 1
