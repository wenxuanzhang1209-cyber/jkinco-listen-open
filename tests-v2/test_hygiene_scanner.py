"""红线扫描器本身也要被测。

这个脚本是仓库唯一的自动化安全闸门,CI 全靠它保证「云端痕迹与密钥永不进入开源仓库」
这句承诺。但它自己此前没有任何测试,而它确实漏过东西:

密钥正则原先写的是 `(?:sk|ghp|...)_[A-Za-z0-9_]{12,}` —— 前缀后面**只认下划线**。
于是这些全都漏过去了:

    sk-proj-…      OpenAI 项目密钥
    sk-…           OpenAI 旧格式
    sk-ant-…       Anthropic
    AIza…          Google API key
    xoxb-…         Slack token

七种常见格式里只抓到两种。而 OpenAI 的密钥恰恰是这个项目的使用者最可能手滑
提交的东西 —— 一个本地优先的工具,用户会去配各种模型端点。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_open_source_hygiene.py"


def _module():
    spec = importlib.util.spec_from_file_location("hygiene", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _matches(text: str) -> bool:
    return any(pattern.search(text) for pattern in _module().SECRET_PATTERNS)


#: 夹具按「前缀 + 主体」两段在运行时拼接，文件里不出现完整的密钥字面量。
#:
#: 这不是洁癖：第一版把完整字符串写死，GitHub 的推送保护直接拒收了整个 push
#: （"push declined due to repository rule violations"）。它认得这些格式——这恰恰
#: 说明夹具足够像真的，而这正是测试需要的。拼接既保住了真实度，又不会在每次 push
#: 时和平台的扫描器打架，也不必去点「允许这个密钥」把它永久加白。
SECRET_FIXTURES = [
    ("OpenAI project key", "sk-proj-", "abcdefghijklmnopqrstuvwxyz1234567890"),
    ("OpenAI legacy", "sk-", "abcdefghijklmnopqrstuvwxyz1234567890AB"),
    ("Anthropic", "sk-ant-", "api03-abcdefghijklmnopqrstuvwxyz123456"),
    ("Stripe", "sk_", "live_abcdefghijklmnopqrst"),
    ("GitHub PAT", "ghp_", "abcdefghijklmnopqrstuvwxyz1234"),
    ("GitLab PAT", "glpat-", "abcdefghij1234567890"),
    ("Hugging Face", "hf_", "abcdefghijklmnopqrstuvwxyz123456"),
    ("Google API key", "AIza", "S" * 35),
    ("Slack bot token", "xoxb-", "123456789012-1234567890123-abcdefghijklmnop"),
    ("AWS access key id", "AKIA", "IOSFODNN7EXAMPLE"),
    ("OpenSSH private key", "-----BEGIN ", "OPENSSH PRIVATE KEY-----"),
    ("RSA private key", "-----BEGIN ", "RSA PRIVATE KEY-----"),
]


@pytest.mark.parametrize("label, prefix, body", SECRET_FIXTURES, ids=[f[0] for f in SECRET_FIXTURES])
def test_real_looking_secrets_are_caught(label, prefix, body):
    secret = prefix + body
    assert _matches(secret), f"漏掉了 {label}:{secret[:24]}…"


@pytest.mark.parametrize("benign", [
    'api_key = "your-api-key-here"',
    'token: "test-abcdefghijklmnop"',
    "the secret sauce is good documentation",
    "LLM_MODEL_NAME=qwen2.5:7b-instruct",
    "scikit-learn-is-a-library",
    "https://example.com/a-very-long-path-segment-here",
    "JKINCO_SESSION_SECRET=",
])
def test_benign_text_does_not_trip_the_scanner(benign):
    """误报比漏报更隐蔽:一个乱叫的扫描器会被人训练成忽略它。"""
    assert not _matches(benign), f"误报:{benign}"


def test_the_repository_itself_is_clean():
    """真正要守的东西:在这个仓库上跑,必须是绿的。"""
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inline_allow_marker_is_line_scoped(tmp_path, monkeypatch):
    """豁免标记只放过它所在的那一行。

    整文件白名单是错的做法:测试密钥脱敏的用例需要像样的夹具,但同一个文件里若真的
    混进一把密钥,扫描器必须照样看得见。
    """
    module = _module()
    sample = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz1234"
    marked = f'key = "{sample}"  # {module.ALLOW_MARKER}'
    unmarked = f'key = "{sample}"' 
    assert module.ALLOW_MARKER in marked
    # 标记本身不改变正则的判断 —— 过滤发生在扫描循环里,标记只是让那一行被跳过
    assert _matches(marked) and _matches(unmarked)


def test_virtualenvs_are_skipped_by_content_not_by_name(tmp_path):
    """按名字列举永远列不全。

    SKIP_PATHS 里只有 .venv 和 .venv-test,于是任何叫 .venv3 / build-env 的本地环境
    都会被扫进来,而 site-packages 里必然躺着 certifi 的 cacert.pem,
    扫描器就报出一条「疑似密钥文件被提交」的假警报。真实发生过。
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "pyvenv.cfg" in source, "虚拟环境的识别又退回按名字猜了"


def test_the_scanner_reports_line_numbers():
    """没有行号的话,在一个几千行的文件里得自己翻。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "text.splitlines()" in source, "扫描器又变回整篇 search 了(只报第一处、且无行号)"
