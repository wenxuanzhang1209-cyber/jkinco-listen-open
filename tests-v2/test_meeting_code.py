"""会议号解析契约。

会议号在库里存成 NNN-NNN-NNN,但用户拿到的号来自微信、邮件或口头转述,
常见形态有纯数字、带空格、全角横杠、复制时混入的不可见字符。
只按原样精确匹配会让「654821848」这种完全合法的输入进不去会议。

规整只做「抽数字重排」,不做模糊匹配:位数不符时原样返回,由上层按查不到处理 ——
否则会把不相干的输入误匹配到别人的会议上。
"""
import uuid

import pytest

import backend.meetings as meetings
from backend.meetings import normalize_meeting_code


@pytest.mark.parametrize("raw,expected", [
    ("654821848", "654-821-848"),          # 纯数字
    ("654-821-848", "654-821-848"),        # 已是标准格式
    ("654 821 848", "654-821-848"),        # 空格分隔
    ("654—821—848", "654-821-848"),        # 全角破折号
    (" 654-821-848 ", "654-821-848"),      # 首尾空白
    ("654.821.848", "654-821-848"),        # 点号分隔
    ("#筑听会议：654-821-848", "654-821-848"),  # 直接粘贴邀请文案里的一行
    ("654​821​848", "654-821-848"),  # 零宽字符(复制常见)
])
def test_common_input_forms_normalize_to_stored_format(raw, expected):
    assert normalize_meeting_code(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "12345", "1234567890", "abc", "654-821"])
def test_wrong_length_is_returned_untouched(raw):
    """位数不对时原样返回,绝不猜测补全 —— 否则可能匹配到别人的会议。"""
    assert normalize_meeting_code(raw) == raw.strip()


def test_lookup_accepts_plain_digits(monkeypatch, tmp_path):
    """核心回归:用纯数字也要能查到那场会议。"""
    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "meetings.db")
    meetings.init_meeting_db()

    meeting_id = uuid.uuid4().hex
    code = "654-821-848"
    with meetings.db() as connection:
        connection.execute(
            """INSERT INTO meetings (id,meeting_code,room_name,title,creator_username,host_username,
               status,allow_guest,allow_chat,allow_screen_share,realtime_transcription_enabled,
               auto_minutes_enabled,auto_record,created_at,updated_at)
               VALUES (?,?,?,?,'alice','alice','active',1,1,1,1,1,0,0,0)""",
            (meeting_id, code, f"room-{meeting_id}", "会议号解析测试"),
        )

    for form in (code, "654821848", "654 821 848", " 654-821-848 "):
        found = meetings._meeting(form)
        assert found["id"] == meeting_id, f"用 {form!r} 未能查到会议"

    # 用会议 id 本身仍然可查(邀请链接走的是 id)
    assert meetings._meeting(meeting_id)["id"] == meeting_id


def test_unknown_code_still_404(monkeypatch, tmp_path):
    """规整不能变成模糊匹配:不存在的号必须仍报不存在。"""
    from fastapi import HTTPException

    monkeypatch.setattr(meetings, "DB_PATH", tmp_path / "meetings.db")
    meetings.init_meeting_db()
    with pytest.raises(HTTPException) as error:
        meetings._meeting("111222333")
    assert error.value.status_code == 404
