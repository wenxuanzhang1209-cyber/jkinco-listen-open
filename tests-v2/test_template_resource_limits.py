"""自定义模板的资源上限契约。

模板正文是直接存进 SQLite 的 BLOB(单个上限 10MB),解析结果也整份落库。原先
三处都没有封顶:

  1. 数量与总量不限。一个账号(含免注册访客)反复上传就能把磁盘写满 ——
     而磁盘写满时整个平台一起停摆,不只是模板功能。
  2. insertion_candidates 每段落至少一条且不限量。实测一个通过全部安全校验的
     4MB docx(20.7 万段落)解析出 20.7 万条候选,占用近 1GB 内存、落库 40MB。
  3. MAX_XML_BYTES 允许 12MB 的 document.xml,单次上传最坏要 11.6 秒 CPU。

其中第 2 点的修法有个必须钉死的细节:候选分两份计额度。占位符和语义标题决定
真正的插入位置,普通段落只是给人工下拉框兜底;混在一份里计数的话,文档开头
成片的普通段落会先把名额占满,后面真正的 {{minutes}} 反而被丢掉,推荐位置
就错了 —— 本文件用「占位符放在最后一段」的用例锁死这一点。
"""
from __future__ import annotations

import io
import random
import sqlite3
import string
import zipfile

import pytest

import backend.custom_templates as templates

CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    b'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
RELS = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    b'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)


def _docx(filler_paragraphs: int = 0, *, placeholder_at_end: bool = False) -> bytes:
    """拼一个能通过 validate_docx 全部检查的 docx。

    填充段落用随机字符,否则压缩比会超过 MAX_COMPRESSION_RATIO 而在归档层就被
    拒掉 —— 那样测的就不是解析层的上限了。
    """
    body = [] if placeholder_at_end else ["<w:p><w:r><w:t>{{minutes}}</w:t></w:r></w:p>"]
    for _ in range(filler_paragraphs):
        noise = "".join(random.choices(string.ascii_letters, k=30))
        body.append(f"<w:p><w:r><w:t>{noise}</w:t></w:r></w:p>")
    if placeholder_at_end:
        body.append("<w:p><w:r><w:t>{{minutes}}</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + "</w:body></w:document>"
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _schema():
    templates.init_custom_template_db()


def _stored_bytes(owner: str) -> int:
    with sqlite3.connect(templates.PROFILE_DB) as connection:
        return connection.execute(
            "SELECT COALESCE(SUM(content_size), 0) FROM custom_templates WHERE owner_username=?",
            (owner,),
        ).fetchone()[0]


def test_template_count_is_capped_and_deleting_frees_a_slot():
    owner = "quota-count-user"
    payload = _docx()
    for index in range(templates.MAX_TEMPLATES_PER_USER):
        templates.create_template(owner, f"模板{index}", "t.docx", payload)
    with pytest.raises(ValueError, match="数量已达上限"):
        templates.create_template(owner, "超额", "t.docx", payload)

    with sqlite3.connect(templates.PROFILE_DB) as connection:
        victim = connection.execute(
            "SELECT id FROM custom_templates WHERE owner_username=? LIMIT 1", (owner,)
        ).fetchone()[0]
        connection.execute("UPDATE custom_templates SET deleted_at=1 WHERE id=?", (victim,))
    # 数量按未删除的算,删掉就能腾出位置,否则用户会被自己删过的模板卡死。
    templates.create_template(owner, "删除后重传", "t.docx", payload)


def test_storage_cap_counts_soft_deleted_templates(monkeypatch):
    """「传满—删掉—再传」不能变成无上限写盘。"""
    owner = "quota-storage-user"
    payload = _docx(filler_paragraphs=800)
    monkeypatch.setattr(templates, "MAX_TEMPLATE_STORAGE_PER_USER", len(payload) * 3 + 16)

    uploads = 0
    while uploads < 50:
        try:
            templates.create_template(owner, f"大{uploads}", "t.docx", payload)
        except ValueError as error:
            assert "占用空间已达上限" in str(error)
            break
        uploads += 1
        with sqlite3.connect(templates.PROFILE_DB) as connection:
            connection.execute(
                "UPDATE custom_templates SET deleted_at=1 WHERE owner_username=?", (owner,)
            )
    else:
        pytest.fail("软删除后重传不受总量限制")

    assert _stored_bytes(owner) <= templates.MAX_TEMPLATE_STORAGE_PER_USER


def test_insertion_candidates_are_bounded():
    analysis = templates.analyze_docx(_docx(filler_paragraphs=3000))
    assert len(analysis["insertion_candidates"]) <= templates.MAX_INSERTION_CANDIDATES + 8
    assert len(analysis["placeholders"]) <= templates.MAX_PLACEHOLDERS
    assert any("段落过多" in message for message in analysis["risk_messages"])


def test_placeholder_at_the_end_survives_the_candidate_cap():
    """普通段落把名额占满时,末尾的 {{minutes}} 仍必须被识别为推荐位置。

    候选若共用一份额度,这里会退化成 append:new-page —— 纪要被追加到新页面,
    而不是替换用户在模板里明确标注的位置。
    """
    analysis = templates.analyze_docx(_docx(filler_paragraphs=3000, placeholder_at_end=True))
    assert analysis["parse_status"] == "ready"
    assert analysis["recommended_confidence"] == 1.0
    assert analysis["recommended_target"].startswith("placeholder:")


def test_oversized_document_xml_is_rejected_before_parsing():
    """这道限制在解析之前生效,决定单次上传能让服务器付出的最坏代价。"""
    oversized = _docx(filler_paragraphs=int(templates.MAX_XML_BYTES / 60) + 5000)
    with pytest.raises(ValueError, match="XML 内容体积异常"):
        templates.validate_docx(
            oversized,
            "big.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
