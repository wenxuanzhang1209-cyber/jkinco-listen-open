#!/usr/bin/env python3
"""把 README、文档和社交预览图里写的测试数，同步成真实的那个。

    python3 scripts/sync_claimed_numbers.py          # 改
    python3 scripts/sync_claimed_numbers.py --check   # 只报告，不改（CI 用）

tests-v2/test_claimed_test_counts.py 会在数字对不上时让测试挂掉。
这个脚本是它的另一半：挂掉之后，一条命令改回来。

为什么需要这么一对东西：这个数字每加一条测试就变。手动改的问题不是
麻烦，是**会漏**。写这套东西的那天，同一个仓库里同时躺着 895、894、
891 三个版本，全都过期，全都没人发现 —— 因为徽章是图片、发帖草稿是
纯文本、文档没人跑，没有任何东西会因此报错。

CHANGELOG 不在同步范围内：那里的数字描述的是某次发布当时的状态，
是历史记录，改了反而是篡改。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORICAL = {"CHANGELOG.md"}

#: 三种写法：徽章 URL、英文正文、中文正文。和测试里的口径保持一致。
PATTERNS = (
    re.compile(r"(tests-)(\d{3,})((?:%20|-)passing)"),
    re.compile(r"(\b)(\d{3,})(\s+tests\b)"),
    re.compile(r"(\b)(\d{3,})(\s+passing\b)"),
    re.compile(r"()(\d{3,})(\s*个测试)"),
)

#: 属于别的仓库的数字，扫到了也不该改成本仓库的数。
OTHER_REPOS = re.compile(r"jkinco-slides|recipe-miniprogram|personal-life-hub")


def actual_test_count() -> int:
    # 不要在这里加 -q：pyproject.toml 的 addopts 里已经有一个，
    # 两个叠成 -qq，pytest 就不再打印「N tests collected」那行汇总了，
    # 只剩每个文件一行。（CI 里踩过同一个坑，见 .github/workflows/ci.yml）
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--no-header"],
        cwd=ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout + result.stderr)
    if match is None:
        sys.exit("pytest 收集失败：\n" + (result.stdout + result.stderr)[-800:])
    return int(match.group(1))


def markdown_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines()
            if line and Path(line).name not in HISTORICAL]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只报告哪些地方对不上，不改动文件")
    args = parser.parse_args()

    count = actual_test_count()
    print(f"实际测试数：{count}\n")

    stale = []
    for path in markdown_files():
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if OTHER_REPOS.search(line):
                continue
            new = line
            for pattern in PATTERNS:
                new = pattern.sub(
                    lambda m: f"{m.group(1)}{count}{m.group(3)}", new)
            if new != line:
                stale.append((path.relative_to(ROOT), index + 1, line.strip()))
                lines[index] = new
        updated = "".join(lines)
        if updated != original and not args.check:
            path.write_text(updated, encoding="utf-8")

    # 社交预览图上的标签也写着这个数字
    card_script = ROOT / "scripts" / "build_social_preview.py"
    card_text = card_script.read_text(encoding="utf-8")
    card_new = re.sub(r'\("(\d{3,}) tests", False\)',
                      f'("{count} tests", False)', card_text, count=1)
    card_changed = card_new != card_text
    if card_changed:
        stale.append((card_script.relative_to(ROOT), 0, "社交预览图标签"))
        if not args.check:
            card_script.write_text(card_new, encoding="utf-8")

    if not stale:
        print("全部一致，无需改动。")
        return 0

    for path, lineno, line in stale:
        where = f"{path}:{lineno}" if lineno else str(path)
        print(f"  {where}\n      {line[:100]}")

    if args.check:
        print(f"\n{len(stale)} 处对不上。跑 scripts/sync_claimed_numbers.py 修。")
        return 1

    print(f"\n已更新 {len(stale)} 处。")
    if card_changed:
        print("社交预览图的标签变了，记得重新生成："
              "\n  python3 scripts/build_social_preview.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
