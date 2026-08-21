"""问筑听:平台助手问答。

从 JKincoListen.py 单体抽出。基于当前会议上下文与历史会议检索回答用户提问,
检索严格按 owner_username 过滤,保证用户只能问到自己有权访问的会议。
JKincoListen.py 通过 re-import 保持向后兼容。
"""
from __future__ import annotations

import os
import re

from jkinco_history import (
    iter_meeting_history,
    _history_visible_to,
    history_time_label,
    load_meeting_history,
)
from jkinco_llm import call_llm
from jkinco_prompts import SOURCE_GUARD, fence_source
from jkinco_scenes import mode_label
from jkinco_logging import get_logger
from jkinco_text import compact_text, user_facing_error

LOGGER = get_logger("assistant")


def selected_history_context(record_id, owner_username="", history=None):
    if not record_id:
        return ""
    for item in iter_meeting_history() if history is None else history:
        if item.get("id") == record_id and _history_visible_to(item, owner_username):
            return "\n".join([
                f"标题：{item.get('title', '未命名会议')}",
                f"场景：{item.get('mode_label', '智能')}",
                f"时间：{history_time_label(item.get('created_at'))}",
                f"概览：{compact_text(item.get('overview'), 1400)}",
                f"纪要：{compact_text(item.get('summary'), 2200)}",
                f"原始转写：{compact_text(item.get('transcript'), 1800)}",
            ])
    return ""


def recent_history_index(limit=8, owner_username="", history=None):
    rows = []
    source = iter_meeting_history() if history is None else history
    visible_items = [item for item in source if _history_visible_to(item, owner_username)][:limit]
    for index, item in enumerate(visible_items, start=1):
        digest = item.get("overview") or item.get("summary") or item.get("transcript")
        rows.append(
            f"{index}. {item.get('title', '未命名会议')}｜{item.get('mode_label', '智能')}｜"
            f"{history_time_label(item.get('created_at'))}｜{compact_text(digest, 240)}"
        )
    return "\n".join(rows) or "暂无历史会议。"


def relevant_history_context(question, limit=5, owner_username="", history=None):
    normalized = re.sub(r"\s+", "", str(question or "").lower())
    grams = {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))}
    generic = {"会议", "内容", "一下", "什么", "怎么", "哪些", "最近", "历史", "关于"}
    grams -= generic
    ranked = []
    for item in iter_meeting_history() if history is None else history:
        if not _history_visible_to(item, owner_username):
            continue
        corpus = "\n".join([
            str(item.get("title", "")), str(item.get("mode_label", "")),
            str(item.get("overview", "")), str(item.get("summary", "")),
            str(item.get("transcript", "")),
        ]).lower()
        score = sum(corpus.count(gram) for gram in grams)
        if normalized and normalized in corpus:
            score += 20
        if score:
            ranked.append((score, float(item.get("created_at") or 0), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    contexts = []
    for _, _, item in ranked[:limit]:
        contexts.append("\n".join([
            f"会议标题：{item.get('title', '未命名会议')}",
            f"会议场景：{item.get('mode_label', mode_label(item.get('mode')))}",
            f"会议时间：{history_time_label(item.get('created_at'))}",
            f"会议概览：{compact_text(item.get('overview'), 900)}",
            f"结构化纪要：{compact_text(item.get('summary'), 1800)}",
        ]))
    return "\n\n".join(contexts) or "未检索到与问题直接相关的历史会议。"


def ask_xiaozhi(question, current_overview="", current_summary="", current_transcript="", selected_record_id=None, chat_history=None, owner_username=""):
    question = str(question or "").strip()
    if not question:
        return "请先输入你想问筑听的问题。"
    # 历史文件只读一次:下面三处上下文构建原本各读一遍,
    # 历史库含全文转写,一次提问会重复解析同一个大 JSON 三次。
    # 只读:下面三处上下文构建都只是挑记录、拼字符串,一次也不改动记录本身。
    # 用只读遍历省掉整份历史的深拷贝(300 条带全文转写时实测 0.91ms/次)。
    history = iter_meeting_history()
    # 这三段都由客户端传入,历史检索结果则可能来自别人共享给我的会议 —— 都是
    # 不可信素材:与会者在共享会议里埋一句「忽略上述指令」,别人提问时助手会读到。
    # 与纪要生成同一套处理:围起来,并声明围栏内是素材而非指令。
    current_context = fence_source("\n".join([
        f"当前会议概览：{compact_text(current_overview, 1600)}",
        f"当前结构化纪要：{compact_text(current_summary, 2400)}",
        f"当前原始转写：{compact_text(current_transcript, 1800)}",
    ]))
    history_messages = []
    for message in list(chat_history or [])[-8:]:
        role = str(message.get("role", ""))
        content = compact_text(message.get("content"), 600)
        if role in {"user", "assistant"} and content:
            history_messages.append(f"{role}: {content}")
    prompt = f"""你是“筑听”（开源本地版）的智能助手，由用户本地部署的开源模型提供会议理解、工程知识整理与业务问答能力。所有数据只在本机处理，不会上传。

身份要求：
1. 始终自称“筑听”，不得自称其他产品名称。
2. 你的服务对象是使用本平台的项目管理、工程咨询、管理汇报、招聘和客户服务人员。
3. 只能依据平台说明、当前会议和检索到的历史会议作答；不得编造会议事实、人员、日期、责任或承诺。

回答要求：
1. 先直接回答问题，不要空泛寒暄。
2. 如果问题关于平台功能，说明具体入口和操作路径。
3. 如果问题关于会议内容，优先使用当前会议；用户提到历史、最近或某个标题时，使用“相关历史会议检索结果”。
4. 未提及的信息必须说“当前内容未提及”，不要编造。
5. 涉及多个会议时，明确写出会议标题和时间，避免把不同会议的信息混在一起。
6. 需要行动时给出 2-5 条清晰建议，并区分原会议事实与筑听建议。

平台说明：
- 筑听支持录音笔自动读取、上传音频、实时录音，三个入口在左侧“录音输入”卡片顶部切换。
- 智能识别会自动路由到会议纪要、管理简报、个人助手、面试记录、客户拜访。
- 输出包括会议概览、结构化纪要、人工校核、原始转写、DOCX/PDF 导出和钉钉推送。
- 导出 Word/PDF：生成完成后，打开右侧结果区的“结构化纪要”标签页，正文下方有“导出 DOCX”和“导出 PDF”按钮，点击后出现下载链接。
- 人工校核：结果区“人工校核”标签页内编辑校核稿并点击“应用校核稿”，之后的展示、导出和推送都使用校核稿。
- 钉钉推送：在左侧处理方式中选择“生成并推送钉钉”，或在“人工校核”标签页点击“推送校核稿到钉钉”；推送状态显示在“钉钉推送状态”栏。
- 左侧“开始录音”新建会议并进入实时录音，“导入录音”进入上传/设备读取，“历史会议”打开可搜索的历史档案。

{SOURCE_GUARD}

最近对话：
{fence_source(chr(10).join(history_messages) or "暂无上下文对话。")}

当前会议：
{current_context}

选中历史会议：
{fence_source(selected_history_context(selected_record_id, owner_username, history) or "未选择历史会议。")}

最近历史会议索引：
{fence_source(recent_history_index(owner_username=owner_username, history=history))}

相关历史会议检索结果：
{fence_source(relevant_history_context(question, owner_username=owner_username, history=history))}

用户问题：
{question}"""
    try:
        return call_llm(
            prompt,
            timeout=int(os.getenv("JKINCO_XIAOZHI_TIMEOUT", "35")),
            model_name=os.getenv("JKINCO_XIAOZHI_MODEL", ""),
            fallback_model=os.getenv("JKINCO_XIAOZHI_FALLBACK_MODEL", ""),
            thinking=False,
            temperature=0.2,
        ).strip()
    except Exception as error:
        # 记日志 + 收敛:这句直接显示在对话里,而助手失败此前不留任何痕迹。
        LOGGER.warning("助手回答失败:%s", error)
        return f"筑听暂时回答失败：{user_facing_error(error)}"


def ask_xiaozhi_chat(question, history, current_overview="", current_summary="", current_transcript="", selected_record_id=None, owner_username=""):
    """单体(Gradio 桌面版)的会话入口。

    owner_username 必须一路透传下去:它是历史记录的可见性依据,为空时
    _history_visible_to 会放行全部记录 —— 那对单机桌面版是正确的(只有一个人
    在用),但对多用户的网页端就是「谁都能读到别人的会议」。原先这个参数在这一层
    就断了,网页端若接入这个入口会静默拿到全量数据。网页端走的是 ask_xiaozhi
    并显式传了用户名,故当前不受影响。
    """
    question = str(question or "").strip()
    messages = list(history or [])
    if not question:
        return messages, ""
    messages.append({"role": "user", "content": question})
    answer = ask_xiaozhi(
        question,
        current_overview=current_overview,
        current_summary=current_summary,
        current_transcript=current_transcript,
        selected_record_id=selected_record_id,
        chat_history=messages,
        owner_username=owner_username,
    )
    messages.append({"role": "assistant", "content": answer})
    return messages, ""
