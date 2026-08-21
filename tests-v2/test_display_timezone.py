"""面向人的时间必须是北京时间。

用户反馈:页面上的会议时间比北京时间晚 8 小时。根因不在前端 —— python:3.12-slim
默认时区是 UTC,而后端有十余处直接用 time.strftime / time.localtime 生成给人看的
时间。全部使用者都在中国,于是这些时间统统慢了 8 小时:

  - 会议提示里的开始时刻(用户截图里那条)
  - 导出 Word/PDF 的「导出时间」「生成日期」
  - 历史记录的标题日期与列表时间
  - 喂给大模型的「当前日期」—— 它据此推算「本周」「明天」这类相对说法,
    错的日期会直接写进纪要正文

逐处改成带时区的写法要动十余个文件,且以后新写的代码还会继续踩;设对进程时区
则一次覆盖全部。本文件守住这个设定:镜像里必须声明 TZ,且运行时确实生效。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"
BEIJING = timezone(timedelta(hours=8))


def test_image_declares_the_deployment_timezone():
    """镜像必须显式声明 TZ —— 基础镜像默认 UTC,不声明就是错 8 小时。"""
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"\bTZ=Asia/Shanghai\b", content), (
        "Dockerfile 未设置 TZ=Asia/Shanghai,面向用户的时间会比北京时间慢 8 小时"
    )


@pytest.mark.skipif(
    time.strftime("%z") != "+0800",
    reason="本机不是东八区;该断言在容器内(TZ=Asia/Shanghai)才有意义",
)
def test_local_time_matches_beijing():
    formatted = time.strftime("%m月%d日 %H:%M", time.localtime())
    expected = datetime.now(BEIJING).strftime("%m月%d日 %H:%M")
    assert formatted == expected


def test_no_new_utc_only_formatting_creeps_in():
    """提醒:面向用户的时间一律走进程时区,不要再引入写死 UTC 的格式化。

    这里只挡住显式的 utcnow/utcfromtimestamp —— 它们无视 TZ 设定,会把刚修好的
    问题重新引入,而且比默认时区更难发现。
    """
    root = DOCKERFILE.parent
    offenders = []
    for path in list(root.glob("backend/*.py")) + list(root.glob("jkinco_*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\b(utcnow|utcfromtimestamp)\s*\(", text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line} {match.group(1)}()")
    assert not offenders, "面向用户的时间不应写死 UTC:" + ", ".join(offenders)
