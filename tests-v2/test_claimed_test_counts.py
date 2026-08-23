"""README、文档和发帖草稿里写的测试数，必须等于真实的测试数。

写这条测试的时候，同一个仓库里躺着四个不同的数字：

    README.md            tests-895 passing
    docs/ARCHITECTURE    contains 895 tests
    docs/launch/reddit   894 tests
    CHANGELOG            891 tests

真实值是 958。没有任何东西会因为这个报错 —— 徽章是一张图片，
发帖草稿是纯文本，文档没人跑。它只在一个场合暴露：有人真的
`git clone` 下来跑了一遍，发现对不上。而那个人恰好是最认真的读者。

数字每加一个测试就变，靠人记住去改是没戏的。所以让它自己盯着。

CHANGELOG 不在扫描范围内 —— 那里的「891 tests」描述的是某次发布当时的
状态，是历史记录，改了反而是篡改。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 只扫「现在时」的声明。CHANGELOG 写的是过去某个版本，不动。
HISTORICAL = {"CHANGELOG.md"}

#: 三种写法：徽章 URL、英文正文、中文正文。
CLAIM_PATTERNS = (
    re.compile(r"tests-(\d{3,})(?:%20|-)passing"),
    re.compile(r"(\d{3,})\s+tests\b"),
    re.compile(r"(\d{3,})\s+passing\b"),
    re.compile(r"(\d{3,})\s*个测试"),
)

#: 属于别的仓库的数字，扫到了也不该拿本仓库的数去比。
OTHER_REPOS = re.compile(r"jkinco-slides|recipe-miniprogram|personal-life-hub")


def _tracked_markdown() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines()
            if line and Path(line).name not in HISTORICAL]


def _claims() -> list[tuple[Path, int, int, str]]:
    """返回 (文件, 行号, 声称的数字, 那一行)。"""
    found = []
    for path in _tracked_markdown():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OTHER_REPOS.search(line):
                continue
            for pattern in CLAIM_PATTERNS:
                for match in pattern.finditer(line):
                    found.append((path, lineno, int(match.group(1)), line.strip()))
    return found


def test_the_scanner_finds_the_places_that_claim_a_number(request):
    """先确认这个扫描器不是在空转。

    如果正则一个都匹配不上，下面那条断言就永远是绿的 —— 而仓库里
    的数字照样可以全是错的。
    """
    if not _tracked_markdown():
        pytest.skip("仓库里没有 markdown")
    assert _claims(), (
        "一处测试数声明都没扫到。要么 README 真的不提测试数了（那就删掉这个文件），"
        "要么 CLAIM_PATTERNS 跟不上新写法了。"
    )


def test_every_claimed_test_count_is_the_real_one(request):
    """扫到的每个数字，都要等于 pytest 实际收集到的数量。"""
    config = request.config
    testpaths = list(config.getini("testpaths") or [])
    unfiltered = list(config.args) in ([], testpaths)
    if config.option.keyword or config.option.markexpr or not unfiltered:
        pytest.skip("这是一次过滤后的运行，收集数不代表全量")

    actual = request.session.testscollected
    wrong = [(path.relative_to(ROOT), lineno, claimed, line)
             for path, lineno, claimed, line in _claims() if claimed != actual]
    assert not wrong, "实际有 {} 个测试，但这些地方写的不是：\n{}".format(
        actual,
        "\n".join(f"  {p}:{n}  写着 {c}\n      {line}" for p, n, c, line in wrong),
    )
