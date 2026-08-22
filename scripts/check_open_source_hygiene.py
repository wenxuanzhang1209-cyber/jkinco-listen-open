#!/usr/bin/env python3
"""开源版红线检查：确保仓库中不出现云端模型、密钥与生产内网痕迹。

在 CI 和本地 git pre-commit 中运行。任何违规内容都会让检查失败：
  - 云端模型 API 名称 / 域名 / 环境变量
  - 生产环境域名、IP、备案信息
  - 疑似 API Key / 私钥 / Token
  - 意外提交的 .env / 证书文件
"""

from __future__ import annotations

import pathlib
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

#: 行内豁免标记。写在哪一行,就只放过哪一行 —— 不放过整个文件。
#:
#: 存在的理由:测试密钥脱敏的用例必须拿「长得像真密钥」的字符串当夹具,否则测不出
#: 东西来。把整个测试文件加进白名单是错的 —— 那样真有一把密钥被误提交到测试里,
#: 扫描器就看不见了。逐行豁免是可见的、可 grep 的,评审时一眼能看出放过了什么。
ALLOW_MARKER = "hygiene:allow-secret"

SECRET_PATTERNS = (
    # 前缀后面既可能是下划线也可能是连字符。原先只写了 `_`，于是 OpenAI 的
    # `sk-proj-…`、`sk-…` 与 Anthropic 的 `sk-ant-…` 全都漏过去了 —— 而那正是
    # 这个项目的使用者最可能手滑提交的东西。实测七种常见格式，原正则只抓到两种。
    re.compile(r"\b(?:sk|ghp|gho|ghs|ghu|ghr|github_pat)[-_][A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                      # AWS access key
    # 不加尾部 \b:真实长度是 AIza + 35，但密钥常被拼进更长的串里，
    # 卡死长度会让「多一个字符」就整条漏过去。宁可稍宽，不可漏。
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),                    # Google API key
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),             # Slack token
    re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}"),                 # GitLab PAT
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),                      # Hugging Face token
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|apikey|token|secret|password)\s*[:=]\s*['\"](?!test-|your-|xxx|<)[A-Za-z0-9_\-/+]{16,}['\"]", re.IGNORECASE),
)

SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".docx", ".pdf", ".doc", ".xlsx", ".pptx",
    ".lock", ".woff", ".woff2", ".ttf", ".otf", ".eot",
}
SKIP_PATHS = {".git", "node_modules", ".venv", ".venv-test", "frontend/dist", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"check_open_source_hygiene.py"}


def _virtualenv_dirs() -> set[pathlib.Path]:
    """按内容找出虚拟环境目录，而不是靠名字猜。

    照名字列举永远列不全：SKIP_PATHS 里只有 .venv 和 .venv-test，于是任何叫
    .venv3 / env / build-env 的本地环境都会被扫进来，而 site-packages 里必然躺着
    certifi 的 cacert.pem —— 扫描器于是报出一条「疑似密钥被提交」的假警报。

    这很要命：一个对着本地环境乱叫的检查器，会训练贡献者忽略它的输出，
    而它存在的全部意义是在真出事那次被人当回事。

    pyvenv.cfg 是虚拟环境的标志文件（PEP 405），按它判定不会漏也不会误伤。
    """
    return {marker.parent for marker in ROOT.rglob("pyvenv.cfg")}


def iter_text_files():
    virtualenvs = _virtualenv_dirs()
    for path in ROOT.rglob("*"):
        if any(part in SKIP_PATHS for part in path.parts):
            continue
        if any(env in path.parents for env in virtualenvs):
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
        # 逐行扫,而不是整篇 search。原先只报第一个命中,一个文件里有两处就只看得见
        # 一处;而且没有行号,得自己去文件里翻。
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{number}: 疑似密钥「{match.group(0)[:24]}…」"
                    )
                    break
    if problems:
        print("❌ 开源版红线检查未通过：")
        for problem in problems:
            print("  -", problem)
        return 1
    print("✅ 开源版红线检查通过：未发现云端模型痕迹、密钥或生产内网信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
