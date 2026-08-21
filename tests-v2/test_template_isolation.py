"""自定义模板的归属隔离契约。

模板是用户上传的真实业务文档(纪要模板往往带公司抬头、项目名、内部格式),
其归属边界与会议/历史/任务同等重要。本文件遍历模板的全部接口,用两个真实账号
断言:B 上传的模板,A 既看不到、下载不到、也改不动删不掉。

与其它隔离测试一样,越权一律按「不存在」处理(404),不确认资源是否存在,
避免用模板 id 做探测。
"""
import base64
import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from helpers import solve_captcha

SECRET_IN_TEMPLATE = "上海建科内部投标模板_请勿外传"


def _docx_bytes(marker: str = SECRET_IN_TEMPLATE) -> bytes:
    """构造一个最小但合法的 docx(zip + word/document.xml)。"""
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{marker}</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>{{会议纪要}}</w:t></w:r></w:p>"
        "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _client(username: str) -> TestClient:
    client = TestClient(main.app)
    challenge = client.get("/api/auth/captcha").json()
    answer = solve_captcha(challenge["token"])
    registered = client.post("/api/auth/register", json={
        "username": username, "display_name": username, "password": "StrongPass123",
        "captcha_token": challenge["token"], "captcha_answer": answer,
    })
    if registered.status_code not in (200, 201):
        assert client.post("/api/auth/login", json={
            "username": username, "password": "StrongPass123",
        }).status_code == 200
    return client


@pytest.fixture(scope="module")
def victim_template():
    """用户 B 上传一份带机密标识的模板。"""
    suffix = uuid.uuid4().hex[:6]
    owner = _client(f"tplowner{suffix}")
    response = owner.post(
        "/api/custom-templates",
        files={"file": ("内部模板.docx", _docx_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"name": "内部投标模板", "scenario": "talk"},
    )
    assert response.status_code == 201, response.text
    return {"owner": owner, "template": response.json(), "username": f"tplowner{suffix}"}


def _template_id(victim_template) -> str:
    template = victim_template["template"]
    return template.get("id") or template.get("template_id")


def test_upload_response_never_leaks_raw_document_bytes(victim_template):
    """上传响应只应返回元数据。原始 DOCX 二进制既无用又会撑爆响应体。"""
    body = victim_template["template"]
    serialized = str(body)
    assert "content" not in body, "响应里带出了模板原始二进制"
    assert "PK" not in serialized[:200], "疑似把 zip 头写进了响应"


def test_other_user_cannot_list_or_read_the_template(victim_template):
    """核心回归:A 的列表里不能出现 B 的模板,详情也必须 404。"""
    attacker = _client(f"tplattacker{uuid.uuid4().hex[:6]}")
    template_id = _template_id(victim_template)

    listing = attacker.get("/api/custom-templates")
    assert listing.status_code == 200
    assert template_id not in listing.text, "他人模板出现在列表中"
    assert SECRET_IN_TEMPLATE not in listing.text

    detail = attacker.get(f"/api/custom-templates/{template_id}")
    assert detail.status_code == 404, "他人模板详情可读"
    assert SECRET_IN_TEMPLATE not in detail.text


def test_other_user_cannot_download_the_template(victim_template):
    """下载是最直接的泄露路径:拿到的是完整的原始业务文档。"""
    attacker = _client(f"tpldl{uuid.uuid4().hex[:6]}")
    response = attacker.get(f"/api/custom-templates/{_template_id(victim_template)}/download")
    assert response.status_code == 404
    assert SECRET_IN_TEMPLATE.encode() not in response.content


def test_other_user_cannot_modify_or_delete_the_template(victim_template):
    """写操作必须被拒,且不能真的改到 B 的数据。"""
    attacker = _client(f"tplwrite{uuid.uuid4().hex[:6]}")
    template_id = _template_id(victim_template)

    patched = attacker.patch(
        f"/api/custom-templates/{template_id}",
        json={"name": "已被篡改", "is_default": True},
    )
    assert patched.status_code == 404

    removed = attacker.delete(f"/api/custom-templates/{template_id}")
    assert removed.status_code == 404

    # B 的模板必须原封不动
    owner_view = victim_template["owner"].get(f"/api/custom-templates/{template_id}")
    assert owner_view.status_code == 200
    assert owner_view.json().get("name") != "已被篡改"


def test_anonymous_is_rejected_on_every_template_route(victim_template):
    anonymous = TestClient(main.app)
    template_id = _template_id(victim_template)
    routes = [
        ("GET", "/api/custom-templates", None),
        ("GET", f"/api/custom-templates/{template_id}", None),
        ("GET", f"/api/custom-templates/{template_id}/download", None),
        ("PATCH", f"/api/custom-templates/{template_id}", {"name": "x"}),
        ("DELETE", f"/api/custom-templates/{template_id}", None),
    ]
    for method, path, body in routes:
        response = anonymous.request(method, path, json=body) if body else anonymous.request(method, path)
        assert response.status_code == 401, f"{method} {path} 未登录返回 {response.status_code}"
        assert SECRET_IN_TEMPLATE not in response.text


def test_owner_retains_full_access(victim_template):
    """反向验证:隔离不能把模板拥有者一起挡住。"""
    owner = victim_template["owner"]
    template_id = _template_id(victim_template)
    assert owner.get("/api/custom-templates").status_code == 200
    assert owner.get(f"/api/custom-templates/{template_id}").status_code == 200
    download = owner.get(f"/api/custom-templates/{template_id}/download")
    assert download.status_code == 200
    # docx 是 zip,内容被压缩过,必须解包后再比对,不能在原始字节里搜明文
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert SECRET_IN_TEMPLATE in document_xml, "下载到的模板不是原始文件"


def test_non_docx_upload_is_rejected():
    """伪装成 docx 的任意文件必须拒收 —— 解析器会去解 zip 与 XML。"""
    client = _client(f"tplbad{uuid.uuid4().hex[:6]}")
    response = client.post(
        "/api/custom-templates",
        files={"file": ("fake.docx", b"MZ\x90\x00 this is not a docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"name": "伪装文件", "scenario": "general"},
    )
    assert response.status_code in (400, 413), response.text


def test_list_does_not_carry_structural_analysis(victim_template):
    """列表接口不得返回结构解析结果。

    analysis 是完整的文档大纲 + 占位符 + 插入候选,实测每条约 33KB,而列表界面
    只显示名称、场景与大小。带上它会让 40 个模板的列表响应从 15.7KB 涨到 1.3MB、
    耗时从 1.8ms 涨到 53ms —— 纯属把详情数据塞进列表的低效序列化。
    """
    owner = victim_template["owner"]
    listing = owner.get("/api/custom-templates")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert items, "列表为空,用例前提不成立"
    for item in items:
        assert not item.get("analysis"), "列表项带上了结构解析结果"
    # 列表仍须包含界面真正需要的展示字段
    for key in ("id", "name", "scenario", "is_default", "content_size", "updated_at"):
        assert key in items[0], f"列表缺少展示字段 {key}"


def test_detail_still_carries_structural_analysis(victim_template):
    """详情接口必须仍带解析结果 —— 插入位置选择完全依赖它。"""
    owner = victim_template["owner"]
    detail = owner.get(f"/api/custom-templates/{_template_id(victim_template)}").json()
    analysis = detail.get("analysis")
    assert analysis, "详情缺少结构解析结果"
    for key in ("placeholders", "insertion_candidates", "recommended_target"):
        assert key in analysis, f"解析结果缺少 {key}"


def test_patch_response_matches_detail_shape(victim_template):
    """保存设置后的响应要与详情同形,否则前端保存完会丢掉解析结果。"""
    owner = victim_template["owner"]
    template_id = _template_id(victim_template)
    patched = owner.patch(f"/api/custom-templates/{template_id}", json={"scenario": "general"})
    assert patched.status_code == 200
    assert patched.json().get("analysis"), "PATCH 响应缺少 analysis,前端会失去插入位置候选"
