"""结构化报告生成。

从 JKincoListen.py 单体抽出的 AI 生成层:把转写文本按场景生成结构化纪要
(超长转写自动分块后汇总)、第二轮事实/模板/责任闭环质检,以及会议概览。
JKincoListen.py 通过 re-import 保持向后兼容。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from jkinco_llm import call_llm
from jkinco_prompts import (
    SOURCE_GUARD,
    CUSTOMER_VISIT_TEMPLATE,
    INTERVIEW_RECORD_TEMPLATE,
    LLM_DIRECT_MAX_CHARS,
    MINUTES_TEMPLATE,
    PERSONAL_ASSISTANT_TEMPLATE,
    build_chunk_prompt,
    build_minutes_prompt,
    build_overview_prompt,
    fence_source,
    minutes_thinking_enabled,
    split_text,
)

from jkinco_logging import get_logger
from jkinco_text import redact_secrets

LOGGER = get_logger("reports")


def generate_minutes(transcript: str, app_mode="talk") -> str:
    if len(transcript) <= LLM_DIRECT_MAX_CHARS:
        draft = call_llm(build_minutes_prompt(transcript, app_mode), thinking=minutes_thinking_enabled())
        return quality_refine_minutes(draft, transcript, app_mode)

    chunks = split_text(transcript)
    LOGGER.info("长文本分 %d 段并行提取后合成纪要", len(chunks))
    max_workers = max(1, min(len(chunks), int(os.getenv("JKINCO_LLM_PARALLEL", "3"))))

    def extract_one(index: int, chunk: str) -> str | None:
        try:
            return call_llm(build_chunk_prompt(chunk, index, len(chunks), app_mode), thinking=False)
        except Exception as error:
            # 单段失败不能拖垮整份纪要。原先用的是 pool.map,任何一段抛异常都会在
            # 收集结果时重新抛出 —— 一场三小时的会分七段,第三段抖一下,前面六次
            # 调用的钱照付、结果全丢,用户只看到「纪要生成失败」。转写本身还在,
            # 重试也要从头再跑一遍全部片段。
            # 少一段的细节,远好过整份纪要没有。
            LOGGER.warning("第 %d/%d 段提取失败,该段以占位说明代替:%s", index, len(chunks), error)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        extracts = list(pool.map(lambda item: extract_one(item[0], item[1]), enumerate(chunks, start=1)))

    if not any(extract for extract in extracts):
        # 全军覆没说明不是抖动,是模型或网络真的不可用。此时没有任何素材可合成,
        # 硬编一份「全是待确认」的纪要只会掩盖故障,让人以为会议内容就这么点。
        raise RuntimeError("长转写的全部片段提取均失败，请稍后重试")

    failed_count = sum(1 for extract in extracts if not extract)
    combined_extract = "\n\n---\n\n".join(
        f"## 片段 {index}\n{extract if extract else '（本段提取失败，内容缺失，请以原始转写为准）'}"
        for index, extract in enumerate(extracts, start=1)
    )
    if failed_count:
        LOGGER.warning("共 %d/%d 段提取失败,纪要按现有片段合成", failed_count, len(chunks))
    draft = call_llm(build_minutes_prompt(combined_extract, app_mode), timeout=240, thinking=minutes_thinking_enabled())
    return quality_refine_minutes(draft, transcript, app_mode)


def quality_refine_minutes(draft: str, transcript: str, app_mode="talk") -> str:
    if os.getenv("JKINCO_QUALITY_REVIEW", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return draft
    template_map = {
        "talk": MINUTES_TEMPLATE,
        "general": "无固定模板：根据会议事实自主组织主题、议题、决议、待办和风险。",
        "personal": PERSONAL_ASSISTANT_TEMPLATE,
        "interview": INTERVIEW_RECORD_TEMPLATE,
        "customer_visit": CUSTOMER_VISIT_TEMPLATE,
        "lingxi": "无固定模板：根据会议事实自主组织主题、议题、决议、待办和风险。",
    }
    template = template_map.get(app_mode, MINUTES_TEMPLATE)
    prompt = f"""你是筑听上线版的报告质量总审。请对草稿做最后一次事实与交付质量修订，并只输出修订后的完整报告。

硬性门槛：
1. 工程例会及其他专业场景严格保持对应模板；通用会议纪要无固定模板，应按原文内容采用最清晰的结构。
2. 所有事实必须来自原始转写；禁止补造姓名、单位、时间、评分、薪资、承诺或结论。
3. 未提及的信息写“未提及”或“待确认”，不能留示例数据。
4. 待办必须尽量包含事项、责任人、截止时间、交付物和状态；原文缺失则明确待确认。
5. 面试场景必须区分“候选人陈述”和“HR 观察”；客户拜访必须区分“客户反馈”和“内部判断”；工程场景必须区分各责任主体；个人场景必须突出优先级和提醒。
6. 删除重复、空泛、AI口吻和任何模板示例，不输出审查说明。

目标模板：
{template}

{SOURCE_GUARD}

原始转写：
{fence_source(transcript[:12000])}

待修订草稿：
{fence_source(draft)}
"""
    try:
        # 编辑修订类任务不需要深度思考模式；关闭后单次复核延迟从分钟级降到十秒级。
        refined = call_llm(prompt, timeout=240, thinking=False).strip()
        return refined if len(refined) >= max(80, int(len(draft) * 0.45)) else draft
    except Exception as error:
        LOGGER.warning("报告质量复核不可用,保留首轮结果:%s", error)
        return draft


def generate_meeting_overview(summary_text: str, transcript: str = "", app_mode="auto") -> str:
    summary_text = str(summary_text or "").strip()
    transcript = str(transcript or "").strip()
    if not summary_text or summary_text in {"*等待处理...*", "等待处理..."}:
        return "*等待生成会议概览...*"
    try:
        # 概览是浏览层提取任务：轻量模型 + 关闭思考，主模型只作兜底。
        return call_llm(
            build_overview_prompt(summary_text, transcript, app_mode),
            timeout=120,
            model_name=os.getenv("JKINCO_OVERVIEW_MODEL", ""),
            fallback_model=os.getenv("LLM_MODEL_NAME", ""),
            thinking=False,
        )
    except Exception as error:
        # 记日志:上面的质量复核失败会记一条,这里原先什么都不留 —— 概览若对所有
        # 用户都开始失败,运维侧看不到任何迹象,只能等人打开某场会才发现。
        LOGGER.warning("会议概览生成失败:%s", error)
        # 这段文案会落库并展示给全体成员,而它是拼进 Markdown 的:异常文本里只要
        # 带一个换行加「## 」,概览里就会多出一个假章节。call_llm 那侧已脱敏并
        # 截断,但非模型来源的异常不经过它,这里统一收敛。
        detail = redact_secrets(" ".join(str(error).split()))[:200]
        return (
            f"## 一、会议概述\n- 概览生成失败：{detail}\n\n"
            "## 二、会议流程\n- 待确认\n\n## 三、会议结论\n- 待确认\n\n## 四、待办事项\n- 待确认"
        )
