#!/usr/bin/env python3
"""One-off migration: regenerate meaningless history titles (未提及/未命名 etc.)."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import JKincoListen as core


def title_is_bad(title: str) -> bool:
    compact = str(title or "").replace(" ", "")
    if not compact or compact == "未命名会议":
        return True
    return any(marker in compact[:6] for marker in core.TITLE_INVALID_MARKERS)


def main() -> None:
    items = core.load_meeting_history()
    if not items:
        print("历史为空，无需迁移。")
        return
    backup = core.HISTORY_FILE.with_suffix(f".bak-{time.strftime('%Y%m%d%H%M%S')}.json")
    shutil.copy2(core.HISTORY_FILE, backup)
    fixed = 0
    for item in items:
        if not title_is_bad(item.get("title", "")):
            continue
        new_title = core.generate_meeting_title(
            item.get("transcript", ""),
            item.get("summary", ""),
            item.get("overview", ""),
            item.get("mode", "auto"),
        )
        print(f"{item.get('id')}: 「{item.get('title')}」 -> 「{new_title}」")
        item["title"] = new_title
        fixed += 1
    if fixed:
        core.write_meeting_history(items)
    print(f"共修复 {fixed} 条标题；备份：{backup}")


if __name__ == "__main__":
    main()
