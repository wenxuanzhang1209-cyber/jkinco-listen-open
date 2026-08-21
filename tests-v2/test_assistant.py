"""问筑听的性能与隔离契约。"""
import os
import tempfile

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-assistant-"))
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"

import jkinco_assistant as assistant


def test_one_question_loads_history_only_once(monkeypatch):
    """三处上下文构建曾各读一次历史文件;历史含全文转写,重复解析代价高。

    现改为读一次传下去。这里用计数锁死,防止后续改动把重复读加回来。
    """
    calls = {"n": 0}
    # 读取走的是只读遍历(iter_meeting_history):历史只被读、不被改,不必为每次
    # 提问深拷贝整份记录。这里计的仍是「读了几次」,与函数换名无关。
    original = assistant.iter_meeting_history

    def counting():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(assistant, "iter_meeting_history", counting)
    # 模型不可达,ask_xiaozhi 会兜底返回错误串;上下文构建在此之前已完成
    assistant.ask_xiaozhi("本周进度如何", owner_username="admin")
    assert calls["n"] == 1, f"一次提问读了 {calls['n']} 次历史,应为 1 次"


def test_context_builders_accept_shared_history():
    """三个上下文构建函数都能接收外部传入的历史,且不再自行读文件。"""
    shared = [{
        "id": "m1", "title": "工程例会", "mode": "talk", "mode_label": "会议纪要",
        "created_at": 1780000000.0, "overview": "概览", "summary": "纪要",
        "transcript": "施工单位汇报进度", "owner_username": "admin",
    }]
    assert "工程例会" in assistant.selected_history_context("m1", "admin", shared)
    assert "工程例会" in assistant.recent_history_index(owner_username="admin", history=shared)
    assert "工程例会" in assistant.relevant_history_context("进度", owner_username="admin", history=shared)


def test_history_isolation_blocks_other_users():
    """检索必须按 owner 过滤,用户不能通过提问套取他人会议。"""
    shared = [{
        "id": "m2", "title": "他人的会议", "mode": "general", "mode_label": "通用会议纪要",
        "created_at": 1780000000.0, "overview": "机密", "summary": "机密",
        "transcript": "机密内容", "owner_username": "someone_else",
    }]
    assert assistant.selected_history_context("m2", "intruder", shared) == ""
    assert "他人的会议" not in assistant.recent_history_index(owner_username="intruder", history=shared)
    assert "他人的会议" not in assistant.relevant_history_context("机密", owner_username="intruder", history=shared)
