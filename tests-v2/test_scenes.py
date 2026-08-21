"""场景/模式模块的行为契约测试。

固定 jkinco_scenes 的输入→输出,并验证 JKincoListen 仍以同名 re-export,
确保从单体抽出后所有 core.xxx 调用点行为完全不变。
"""
import os

# 本文件含 re-export 测试,需导入单体;单体启动会校验必填变量。
# 显式补齐,保证本文件可单独运行,不依赖其它测试文件的执行顺序。
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/chat/completions")
os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("DINGTALK_WEBHOOK", "http://127.0.0.1:1/webhook")
os.environ.setdefault("DINGTALK_SECRET", "test-secret")

import jkinco_scenes as scenes


def test_mode_label_covers_all_scenes():
    assert scenes.mode_label("talk") == "工程例会"
    assert scenes.mode_label("general") == "通用会议纪要"
    assert scenes.mode_label("personal") == "个人助手"
    assert scenes.mode_label("interview") == "面试记录"
    assert scenes.mode_label("customer_visit") == "客户拜访"
    assert scenes.mode_label("auto") == "智能识别"
    assert scenes.mode_label(None) == "智能识别"


def test_legacy_lingxi_maps_to_general():
    assert scenes.is_lingxi_mode("管理简报") is True
    assert scenes.is_general_mode("lingxi") is True
    assert scenes.mode_label("管理简报") == "通用会议纪要"


def test_chinese_aliases_recognized():
    assert scenes.is_talk_mode("工程例会") is True
    assert scenes.is_personal_mode("个人助手") is True
    assert scenes.is_customer_visit_mode("客户拜访") is True
    assert scenes.is_auto_mode("智能识别") is True


def test_canonical_mode_accepts_supported_aliases_and_rejects_unknown_values():
    assert scenes.canonical_mode(None) == "auto"
    assert scenes.canonical_mode("智能识别") == "auto"
    assert scenes.canonical_mode("工程例会") == "talk"
    assert scenes.canonical_mode("管理简报") == "general"
    assert scenes.canonical_mode("个人助手") == "personal"
    assert scenes.canonical_mode("面试记录") == "interview"
    assert scenes.canonical_mode("客户拜访") == "customer_visit"
    try:
        scenes.canonical_mode("engineering-ish")
    except ValueError as error:
        assert str(error) == "不支持的会议场景"
    else:
        raise AssertionError("未知场景不应静默进入分类或模板分支")


def test_output_title_per_mode():
    assert scenes.output_title("general") == "会议纪要"
    assert scenes.output_title("personal") == "个人备忘录"
    assert scenes.output_title("interview") == "面试记录与候选人反馈表"
    assert scenes.output_title("customer_visit") == "客户拜访会议纪要"
    assert scenes.output_title("talk") == "会 议 纪 要"


def test_history_mode_label_uses_status_text_fallback():
    # 从状态文本猜出来的场景,名字也要用规范名。这里原先断言「会议纪要」——
    # 那正是历史列表与场景页签叫不同名字的根源,已随 history_mode_label 一起改正。
    assert scenes.history_mode_label("auto", "工程例会现场纪要") == "工程例会"
    assert scenes.history_mode_label("auto", "其他会议") == "通用会议纪要"
    assert scenes.history_mode_label("personal", "") == "个人助手"
    assert scenes.history_mode_label("auto", "") == "智能"


def test_history_label_matches_the_scene_tab():
    """历史列表与场景页签必须叫同一个名字。

    此前工程例会在历史里显示成「会议纪要」,而场景页签、mode_label、提示词都叫
    「工程例会」—— 生产 86 条记录里 26 条对不上,而且一直在产生新的。
    """
    for mode in ("talk", "general", "personal", "interview", "customer_visit"):
        assert scenes.history_mode_label(mode, "") == scenes.mode_label(mode), mode


def test_status_text_cannot_override_a_known_scene():
    """状态文案只是给人看的中文,不该反推场景。

    生产里出现过 mode=general 却因为状态里带「客户拜访」四个字而被标成客户拜访。
    """
    assert scenes.history_mode_label("general", "已按客户拜访模板推送") == "通用会议纪要"
    assert scenes.history_mode_label("talk", "个人助手已生成") == "工程例会"


def test_status_text_is_still_used_when_the_scene_is_unknown():
    """场景判不出来时,状态里的线索仍比「智能」有信息量。"""
    assert scenes.history_mode_label("auto", "已按面试记录模板生成") == "面试记录"
    assert scenes.history_mode_label("auto", "其他会议") == "通用会议纪要"


def test_reexport_from_monolith_is_identical():
    from backend import core

    for name in ("mode_label", "canonical_mode"):
        assert getattr(core, name) is getattr(scenes, name), f"{name} 未正确 re-export"
