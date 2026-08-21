"""问筑听的历史可见性必须按当前用户过滤。

_history_visible_to 在 owner_username 为空时放行全部记录 —— 这对单机桌面版是
正确的(只有一个人在用),但对多用户的网页端就是「谁都能读到别人的会议」。
网页端必须始终显式传入当前用户,这条测试就是钉住这一点:
任何人以后改了接口层、忘了传用户名,这里会立刻失败。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.main as main
from jkinco_assistant import ask_xiaozhi, ask_xiaozhi_chat
from jkinco_history import _history_visible_to


def test_web_endpoint_passes_the_viewer_identity():
    """接口层必须把 require_user 得到的用户名传进 ask_xiaozhi。"""
    source = inspect.getsource(main.assistant)
    assert "username = require_user(request)" in source
    # 最后一个位置参数就是 owner_username
    assert "username,\n    )" in source or "username)" in source, (
        f"assistant 接口没有把用户名传给 ask_xiaozhi:\n{source}"
    )


def test_visibility_filters_by_owner():
    mine = {"id": "a", "owner_username": "u1"}
    others = {"id": "b", "owner_username": "u2"}
    shared = {"id": "c", "owner_username": "u2", "shared_usernames": ["u1"]}

    assert _history_visible_to(mine, "u1") is True
    assert _history_visible_to(others, "u1") is False
    assert _history_visible_to(shared, "u1") is True


def test_empty_viewer_is_permissive_and_must_stay_desktop_only():
    """空身份放行全部 —— 这是单机版语义。

    这条测试不是认可这个行为,而是把它钉成「已知且刻意」:一旦有人让网页端
    走到这条分支,上面那条测试会失败。
    """
    assert _history_visible_to({"owner_username": "别人"}, "") is True


def test_chat_entry_forwards_the_viewer():
    """单体的会话入口必须能透传身份,否则这个参数在这一层就断了。"""
    params = inspect.signature(ask_xiaozhi_chat).parameters
    assert "owner_username" in params, "ask_xiaozhi_chat 未接收 owner_username"
    body = inspect.getsource(ask_xiaozhi_chat)
    assert "owner_username=owner_username" in body, "接收了但没往下传"
    assert "owner_username" in inspect.signature(ask_xiaozhi).parameters
