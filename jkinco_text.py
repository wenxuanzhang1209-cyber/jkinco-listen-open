"""文本清洗与格式化工具。

从 JKincoListen.py 单体抽出的纯函数集合:Markdown 清洗、文本压缩、PDF 换行、
文件大小可读化。零外部依赖、零状态,被导出与报告链路共用。
JKincoListen.py 通过 re-import 保持向后兼容。
"""
from __future__ import annotations

import os
import re
import unicodedata


# XML 1.0 允许的控制字符只有制表、换行、回车三个。其余的(空字节、响铃、
# 终端转义序列等)会让 python-docx 在写文档时直接抛
# 「All strings must be XML compatible: no NULL bytes」—— 一场会的纪要里只要
# 混进一个,Word 导出就永久失败,而 PDF 那条路照常能导,用户只会觉得 Word 坏了。
#
# 这些字符对任何下游都没有意义,所以在清洗这一层就去掉,而不是只在导出处兜底:
# 纪要还要进钉钉、进模板渲染、进大模型,每处各修一遍迟早会漏。
# 来源不止一处 —— 语音识别的结果、用户粘贴的文本、以及 /api/process 直接提交的
# live_text(实测含空字节的提交返回 200,一路畅通)。
# 上界要写到 \U0010FFFF,不能停在 \uFFFD:emoji 与部分生僻汉字(𠮷、𩸽)都在
# 基本平面之外,写成 \uFFFD 会把它们一并删掉 —— 会议纪要里出现这些字很正常。
_XML_INVALID = re.compile(r"[^\x09\x0a\x0d\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")


def strip_control_characters(value):
    """去掉 XML 不接受的控制字符,保留制表/换行/回车。"""
    return _XML_INVALID.sub("", str(value or ""))


def clean_markdown_text(markdown_text):
    text = strip_control_characters(markdown_text).strip()
    if not text or text in {"*等待处理...*", "等待处理..."}:
        return "暂无可导出的结构化纪要。"
    text = re.sub(r"```(?:markdown)?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def markdown_lines(markdown_text):
    return [line.rstrip() for line in clean_markdown_text(markdown_text).splitlines()]


def wrap_pdf_line(text, canvas, font_name, font_size, max_width):
    if not text:
        return [""]
    lines = []
    current = ""
    for char in text:
        test = current + char
        if canvas.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def human_file_size(path):
    try:
        size = path.stat().st_size
    except OSError:
        return "未知大小"
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return "未知大小"


def compact_text(text, limit=2200):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def redact_secrets(text):
    """抹掉文本里的凭证与内部端点信息。

    第三方库的异常常把请求 URL 原样带出,而凭证往往就在 URL 的 query 里。这些
    字符串会被拼进「场景识别理由」「概览生成失败」等文案,落库并展示给用户 ——
    钉钉链路早就为此做了脱敏,大模型链路却没有,同一类问题只堵了一半。

    同时截断:异常里那串 SSL 内部细节(_ssl.c:1000 之类)对用户毫无意义,
    只会把界面撑得很难看。
    """
    redacted = re.sub(
        r"((?:access_token|token|sign|key|secret|password|api[-_]?key)=)[^&\s'\"]+",
        r"\1<REDACTED>",
        str(text),
        flags=re.IGNORECASE,
    )
    for name in ("DINGTALK_WEBHOOK", "DINGTALK_SECRET", "LLM_API_KEY", "LLM_BASE_URL",
                 "LIVEKIT_API_SECRET", "JKINCO_SESSION_SECRET"):
        value = os.getenv(name, "")
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "<REDACTED>")
    # URL 里的 userinfo 形式(https://用户:口令@host)。当前 endpoint 用的是
    # Authorization 头,走不到这里,但换成带认证的代理就会 —— 上面那条按 env 值
    # 整串替换只在异常原样带出完整 URL 时才生效,截断或改写过就漏了。
    redacted = re.sub(r"(://)[^/\s:@]+:[^/\s@]+@", r"\1<REDACTED>@", redacted)
    # 内部主机名也不该回显给用户
    redacted = re.sub(r"host='[^']*'", "host='<REDACTED>'", redacted)
    redacted = re.sub(r"url:\s*\S+", "url: <REDACTED>", redacted)
    return redacted


# 纯语气词/应答词。只有当整段转写「除了这些什么都不剩」时才判为无内容 ——
# 逐词删除是不行的:「嗯，那就这么定了」删掉「嗯」之后仍是有效内容,而
# 「定了」这种词本身也在真实纪要里出现。
_FILLER_ONLY = re.compile(
    r"^(?:[嗯呃啊哦噢喔呀哎唉诶欸唔嘶哈呵嘿喂咦哟嗨]|"
    r"[Uu]h+|[Uu]m+|[Aa]h+|[Ee]h+|[Hh]mm+|[Oo]h+|[Mm]hm+)+$"
)


def has_meaningful_speech(transcript) -> bool:
    """这段转写里有没有值得整理成纪要的内容。

    「什么都没说」时 ASR 往往不返回空串,而是给出一两个语气词或一个句号 ——
    呼吸声、键盘声、空调声都会诱发。此前两处闸门都写作 `if not transcript`,
    只挡完全空串,于是这种转写照样进入生成流程:提示词里 <会议素材> 近乎为空,
    而指令仍是「请把以下转写整理为《会议纪要》」,模型只能照章节要求硬造标题。
    严格模板的几档更明显 —— 整套章节骨架配上「待确认」,看起来像一份真纪要。

    判据不能用长度阈值:个人备忘的「明天下午三点开会」只有 8 个字,是完全有效
    的内容。这里改为「剥掉空白与标点后,剩下的是不是只有语气词」,既挡得住
    噪音,也不会误伤任何一句真话。
    """
    text = strip_control_characters(transcript)
    core = "".join(
        char for char in text
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "S", "C"))
    )
    if not core:
        return False
    return not _FILLER_ONLY.match(core)


# 双向文本的覆写/嵌入控制符。它们在 XML 里完全合法,strip_control_characters
# 不会碰(也不该碰)—— 但它们会让后面的文字按相反方向渲染,足以把一段标题伪装
# 成另一段。会议标题会显示在别人的会议列表里,这是个能骗到人的位置。
# 只清覆写与隔离符,不动 LRM/RLM(U+200E/U+200F):那两个是混排文本的正常用法。
BIDI_OVERRIDES = re.compile(r"[‪-‮⁦-⁩]")


def clean_display_title(value, fallback: str = "未命名会议", limit: int = 80) -> str:
    """把用户输入的标题收敛成可以安全显示的一行。

    同一个代码库里模板名早就过 _clean_name 了,会议标题却只校验长度 —— 实测
    控制字符、空字节、双向覆写符都能原样存进去,纯空白的标题也照收(界面上
    就是一片空白,谁也认不出那是哪场会)。
    """
    text = BIDI_OVERRIDES.sub("", strip_control_characters(value))
    return " ".join(text.split())[:limit] or fallback


def clean_message_text(value) -> str:
    """收敛会展示给其他人的消息正文。

    比标题轻:**保留换行**——多行消息是正常内容,压平会改变用户写的东西。只去掉
    两类看不见却能骗人的字符:控制字符(在界面上是空白,却会混进搜索与导出),
    以及双向覆写符(让后面的文字反向渲染)。会议聊天不进纪要、不进模型,所以这
    纯粹是展示层的问题 —— 但消息是发给别人看的,同一类字符在别处已经收敛了。
    """
    return BIDI_OVERRIDES.sub("", strip_control_characters(value)).strip()


def clean_speaker_name(value, fallback: str = "", limit: int = 30) -> str:
    """收敛会作为转写署名使用的名字。

    比标题多清一样东西:冒号。转写是按「说话人：内容」逐行拼起来的,名字里带一个
    冒号,这一行读起来就有两个说话人 ——「张三 李四：我同意」。压平换行已经挡住了
    「凭空造出一整行别人的发言」,但署名要进的是共享并归档的正式记录,值得把这点
    歧义也一并去掉。标题不做这一步:「周会：三标段」是正常写法。
    """
    return clean_display_title(value, fallback=fallback, limit=limit).replace("：", " ").replace(":", " ").strip() or fallback


USER_FACING_ERROR_CHARS = 200


def user_facing_error(error, limit: int = USER_FACING_ERROR_CHARS) -> str:
    """把异常收敛成一句可以展示给用户的文本。

    异常在这个项目里会走到界面上:任务失败消息、会议概览、助手回答、模板风险
    提示。原先各处直接插值 str(error),于是同一类问题被反复写错三次:

      - 不脱敏:第三方库的异常会把完整请求 URL 原样带出,凭证常在 query 里;
        OSError 则会带出服务器的绝对路径。
      - 不限长:异常可以很长,而这些文案有的要落库、有的要塞进固定版式。
      - 不清换行:概览是 Markdown,异常里一个换行加「## 」就能插出一个假章节。

    三件事必须一起做,少做一件就是一个新的洞 —— 所以收在一个函数里,并由
    tests-v2/test_user_facing_errors.py 扫描全代码库守住「不许再裸插值」。
    """
    text = " ".join(str(error or "").split())
    return redact_secrets(text)[:limit]
