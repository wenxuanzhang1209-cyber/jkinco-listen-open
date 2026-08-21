"""模板场景取值必须显式校验,不得静默忽略。

原先是「不在合法集合里就悄悄用回旧值/默认值」:用户在界面上改了场景、接口
也返回成功,实际却没改,没有任何地方能看出问题。命中这条路径的情况很常见 ——
大小写写错(TALK)、误传中文标签(工程例会)、拼错(enginering)。
同一个函数里插入方式是明确报错的,两者态度不该不一致。
"""
import sys
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.custom_templates as CT
from backend.custom_templates import SCENARIOS, init_custom_template_db, update_template


def _seed_template(owner: str = "u1", scenario: str = "talk") -> str:
    init_custom_template_db()
    tid = uuid.uuid4().hex
    now = time.time()
    with CT.PROFILE_DB_LOCK, sqlite3.connect(CT.PROFILE_DB) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(custom_templates)")}
        values = {
            "id": tid, "owner_username": owner, "name": "测试模板", "scenario": scenario,
            "original_filename": "t.docx", "filename": "t.docx", "content": b"x",
            "created_at": now, "updated_at": now, "is_default": 0, "parse_status": "ready",
            "insertion_strategy": "append", "insertion_target": "append:new-page",
            "analysis_json": "{}", "sha256": "x", "content_size": 1, "version": 1,
        }
        used = {k: v for k, v in values.items() if k in columns}
        connection.execute(
            f"INSERT INTO custom_templates({','.join(used)}) VALUES({','.join('?' * len(used))})",
            tuple(used.values()),
        )
    return tid


@pytest.mark.parametrize("bad", ["enginering", "工程例会", "TALK", "unknown_scene"])
def test_invalid_scenario_is_rejected_not_ignored(bad):
    tid = _seed_template()
    with pytest.raises(ValueError) as excinfo:
        update_template("u1", tid, scenario=bad)
    assert "场景" in str(excinfo.value)


def test_valid_scenario_still_applies():
    tid = _seed_template(scenario="talk")
    updated = update_template("u1", tid, scenario="general")
    assert updated["scenario"] == "general"


def test_omitted_scenario_keeps_current():
    """不传场景时保持原值 —— 这是合法的部分更新,不该报错。"""
    tid = _seed_template(scenario="interview")
    updated = update_template("u1", tid, name="改个名字")
    assert updated["scenario"] == "interview"
    assert updated["name"] == "改个名字"


def test_create_does_not_silently_fall_back_to_general():
    """上传模板时传错场景,不能悄悄归到 general —— 那等于把模板放进了错误的分类。"""
    with pytest.raises(ValueError):
        CT._validated_scenario("不存在的场景", "general")
    assert CT._validated_scenario(None, "general") == "general"
    for ok in SCENARIOS:
        assert CT._validated_scenario(ok, "general") == ok
