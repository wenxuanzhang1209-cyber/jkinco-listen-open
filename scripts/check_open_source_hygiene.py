#!/usr/bin/env python3
"""开源版红线检查：确保仓库中不出现云端模型、密钥与生产内网痕迹。

在 CI 和本地 git pre-commit 中运行。任何违规内容都会让检查失败：
  - 云端模型 API 名称 / 域名 / 环境变量
  - 生产环境域名、IP、备案信息
  - 疑似 API Key / 私钥 / Token
  - 意外提交的 .env / 证书文件
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TERMS = (
    "dashscope",
    "aliyun",
    "aliyuncs",
    "maas",
    "fun-asr",
    "qwen-plus",
    "qwen3.7",
    "qwen3.6",
    "qwen-audio",
    "deepseek-v4",
    "百炼",
    "阿里云",
    "沪ICP",
    "ICP备案",
    "jkincolisten.cloud",
    "compatible-mode",
    "X-DashScope",
    "DASHSCOPE",
    "JKINCO_CLOUD_ASR",
    "JKINCO_REALTIME_ASR",
    "JKINCO_ASR_VOCABULARY_ID",
    "筑衍",
    "47.103.",
)

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|apikey|token|secret)\s*[:=]\s*['\"](?!test-)[A-Za-z0-9_\-/+]{16,}['\"]", re.IGNORECASE),
)

SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".docx", ".pdf", ".doc", ".xlsx", ".pptx",
    ".lock", ".woff", ".woff2", ".ttf", ".otf", ".eot",
}
SKIP_PATHS = {".git", "node_modules", ".venv", ".venv-test", "frontend/dist", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"check_open_source_hygiene.py"}


def iter_text_files():
    for path in ROOT.rglob("*"):
        if any(part in SKIP_PATHS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def main() -> int:
    problems: list[str] = []
    for path in iter_text_files():
        if path.name in {".env", ".env.local", ".env.production"} or path.suffix in {".pem", ".key", ".p12", ".pfx"}:
            problems.append(f"{path.relative_to(ROOT)}: 疑似密钥/环境文件被提交")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        for term in FORBIDDEN_TERMS:
            if term.lower() in lowered:
                problems.append(f"{path.relative_to(ROOT)}: 出现云端/生产痕迹「{term}」")
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                problems.append(f"{path.relative_to(ROOT)}: 疑似密钥「{match.group(0)[:24]}…」")
    if problems:
        print("❌ 开源版红线检查未通过：")
        for problem in problems:
            print("  -", problem)
        return 1
    print("✅ 开源版红线检查通过：未发现云端模型痕迹、密钥或生产内网信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
