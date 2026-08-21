"""异常走到界面上之前必须先过 user_facing_error()。

这个项目里异常是会被人看见的:任务失败消息、会议概览、助手回答、模板风险提示,
都会把 str(error) 拼进展示文案。同一类问题因此被反复写错四次:

  - 不脱敏:第三方库的异常会把完整请求 URL 原样带出(凭证常在 query 里),
    OSError 则带出服务器绝对路径;
  - 不限长:异常可以很长,而这些文案有的要落库、有的要塞进固定版式;
  - 不清换行:概览是 Markdown,异常里一个换行加「## 」就能插出一个假章节。

三件事必须一起做,少做一件就是一个新的洞 —— 所以收进 user_facing_error(),
并由本文件扫描全代码库守住「不许再裸插值」。逐个修完还会有第五处,能守住的
规则才有意义。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from jkinco_text import USER_FACING_ERROR_CHARS, user_facing_error

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _source_files() -> list[pathlib.Path]:
    files = sorted(ROOT.glob("jkinco_*.py")) + sorted((ROOT / "backend").glob("*.py"))
    # JKincoListen.py 是 Gradio 单体,不在服务端部署路径上
    return [path for path in files if path.name != "JKincoListen.py"]


class _RawInterpolation(ast.NodeVisitor):
    """找「except 块里 return 一个直接插值了异常变量的 f-string」。"""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.bound: list[str] = []
        self.hits: list[str] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.append(node.name)
            self.generic_visit(node)
            self.bound.pop()
        else:
            self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if self.bound and isinstance(node.value, ast.JoinedStr):
            for part in ast.walk(node.value):
                if isinstance(part, ast.FormattedValue):
                    inner = part.value
                    # 已经过处理的算合规
                    if isinstance(inner, ast.Call):
                        continue
                    for name in ast.walk(inner):
                        if isinstance(name, ast.Name) and name.id in self.bound:
                            self.hits.append(f"{self.path.name}:{node.lineno}")
        self.generic_visit(node)


def test_no_raw_exception_interpolation_in_returned_text():
    offenders: list[str] = []
    for path in _source_files():
        scanner = _RawInterpolation(path)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8")))
        offenders.extend(scanner.hits)
    assert not offenders, (
        "这些位置把异常直接插进了要返回给用户的文案,请改用 user_facing_error():"
        + ", ".join(offenders)
    )


def test_the_scan_would_catch_a_regression():
    """自检:扫描器必须真的能抓到这种写法,否则上面那条是恒真的。"""
    bad = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as error:\n"
        "        return f'失败:{error}'\n"
    )
    scanner = _RawInterpolation(pathlib.Path("sample.py"))
    scanner.visit(ast.parse(bad))
    assert scanner.hits, "扫描器抓不到裸插值,这条守卫是空的"

    good = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as error:\n"
        "        return f'失败:{user_facing_error(error)}'\n"
    )
    scanner = _RawInterpolation(pathlib.Path("sample.py"))
    scanner.visit(ast.parse(good))
    assert not scanner.hits, "处理过的写法被误报了"


# --- user_facing_error 自身的契约 ---

def test_collapses_newlines():
    assert "\n" not in user_facing_error(RuntimeError("第一行\n第二行"))
    assert "\r" not in user_facing_error(RuntimeError("a\r\nb"))


def test_bounds_length():
    assert len(user_facing_error(RuntimeError("x" * 5000))) == USER_FACING_ERROR_CHARS


def test_redacts_credentials():
    out = user_facing_error(RuntimeError("请求 https://user:s3cret@host/v1?api_key=abcdef123 失败"))
    assert "s3cret" not in out and "abcdef123" not in out


def test_tolerates_none_and_empty():
    assert user_facing_error(None) == ""
    assert user_facing_error(RuntimeError("")) == ""


def test_keeps_the_useful_part():
    """收敛不能把信息抹光 —— 用户要能看出大概出了什么事。"""
    assert "模型返回空内容" in user_facing_error(RuntimeError("模型返回空内容"))


@pytest.mark.parametrize(
    "module_name, function_name",
    [
        ("backend.main", "run_processing_job"),
        ("jkinco_reports", "generate_meeting_overview"),
        ("jkinco_assistant", "ask_xiaozhi"),
    ],
)
def test_known_user_facing_paths_log_their_failures(module_name, function_name):
    """失败必须留痕。这些路径此前静默降级,生产上看不到任何迹象。"""
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    source = inspect.getsource(getattr(module, function_name))
    assert "LOGGER." in source, f"{module_name}.{function_name} 的失败路径不记日志"
