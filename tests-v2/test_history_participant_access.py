"""参会者对历史会议的可见性契约。

一场会开完,参会的所有人都应该在「最近会议」里看到它 —— 只能看,不能改。
读权限比写权限宽一档:参会者能读能导出,但覆盖定稿只能是创建者或管理员,
否则一场多人会里谁都能改,就没有「定稿」可言。

这两条边界一旦被后来的改动弄反,表现是「别人的会出现在我的列表里并且我能改」
或者「我参加过的会在列表里根本不出现」,都不会有任何报错,所以在这里钉死。
"""
import pytest

from backend.history import (
    history_editable_by,
    history_shared_usernames,
    history_visible_to,
    serialize_history,
)


@pytest.fixture()
def record():
    return {
        "id": "rec-1",
        "title": "数智化项目进度同步",
        "owner_username": "dpmeet",
        "shared_usernames": ["xiaosiqi", "liuzhenyu"],
        "summary": "纪要正文",
        "transcript": "转写全文",
    }


def test_creator_can_read_and_edit(record):
    assert history_visible_to(record, "dpmeet", admin=False)
    assert history_editable_by(record, "dpmeet", admin=False)


@pytest.mark.parametrize("member", ["xiaosiqi", "liuzhenyu"])
def test_participant_can_read_but_not_edit(record, member):
    """本次需求的核心:参会者看得到,但改不了。"""
    assert history_visible_to(record, member, admin=False)
    assert not history_editable_by(record, member, admin=False)


def test_outsider_sees_nothing(record):
    assert not history_visible_to(record, "someone-else", admin=False)
    assert not history_editable_by(record, "someone-else", admin=False)


def test_admin_keeps_full_access(record):
    assert history_visible_to(record, "root", admin=True)
    assert history_editable_by(record, "root", admin=True)


def test_participant_payload_is_marked_read_only(record, monkeypatch):
    """前端靠 read_only 决定是否显示保存/推送按钮,并把校核框置为只读。"""
    # serialize_history 不接收 admin,内部会去查账号库;这里只验权限口径,把它按普通用户处理
    monkeypatch.setattr("backend.history.is_admin", lambda username: False)
    assert serialize_history(record, compact=True, viewer="xiaosiqi")["read_only"] is True
    assert serialize_history(record, compact=True, viewer="dpmeet")["read_only"] is False


def test_payload_without_viewer_keeps_the_old_shape(record):
    """老调用方不传 viewer 时不能凭空多出字段。"""
    assert "read_only" not in serialize_history(record, compact=True)


@pytest.mark.parametrize("shared,expected", [
    (None, []),
    ("xiaosiqi", []),           # 存成字符串是坏数据,不能被当成单个用户名放行
    (["", "  ", "bob"], ["bob"]),
    ([None, "alice"], ["alice"]),
])
def test_malformed_shared_list_never_grants_access(record, shared, expected):
    """名单字段脏了要退成「谁都不共享」,绝不能反向放开权限。"""
    record["shared_usernames"] = shared
    assert history_shared_usernames(record) == expected
    assert not history_visible_to(record, "", admin=False)


def test_blank_username_is_never_treated_as_a_member(record):
    """访客没有账号,用户名可能是空串 —— 空串不能匹配上任何记录。"""
    record["shared_usernames"] = ["", "xiaosiqi"]
    assert not history_visible_to(record, "", admin=False)
    assert not history_visible_to(record, "   ", admin=False)


def test_admin_flag_is_honoured_and_avoids_per_record_lookups(record, monkeypatch):
    """列表接口已判定过一次管理员身份,serialize_history 不该每条再查一遍账号库。

    每次查询都会新建一个 sqlite 连接,59 条记录实测 29.6ms,占 /api/history 的八成。
    这里既验证传入的 admin 被采纳,也验证它确实不再触发查询。
    """
    calls = []

    def spy(username):
        calls.append(username)
        return False

    monkeypatch.setattr("backend.history.is_admin", spy)

    # 传入 admin 时:不查库,且结论按传入值走
    payload = serialize_history(record, compact=True, viewer="xiaosiqi", admin=True)
    assert payload["read_only"] is False, "传入 admin=True 未被采纳"
    assert calls == [], "传入 admin 后仍然查询了账号库"

    # 不传时保持原行为:自行判定
    payload = serialize_history(record, compact=True, viewer="xiaosiqi")
    assert payload["read_only"] is True
    assert calls == ["xiaosiqi"]
