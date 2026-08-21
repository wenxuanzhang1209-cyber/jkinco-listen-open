"""镜像依赖与模块边界契约（开源本地版）。

开源版只有一份运行时依赖 requirements.txt（含本地 ASR FunASR），
镜像不携带任何云端凭据、不携带旧的单机版界面，后端也不得反向依赖单体。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
DOCKERFILE = ROOT / "Dockerfile"


def _requirement_names(path: pathlib.Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[")[0]
        for separator in (">=", "<=", "==", "~=", "!=", ">", "<"):
            name = name.split(separator)[0]
        names.add(name.strip().lower())
    return names


def test_runtime_requirements_include_local_asr():
    """本地语音识别（FunASR）必须出现在运行时依赖里。"""
    assert "funasr" in _requirement_names(REQUIREMENTS)


def test_dockerfile_builds_frontend_and_serves_backend():
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "npm run build" in content
    assert "backend.main:app" in content
    assert "TZ=Asia/Shanghai" in content


def test_dockerfile_does_not_ship_the_desktop_monolith():
    """镜像不得拷贝 JKincoListen.py（已从开源版移除）。"""
    content = DOCKERFILE.read_text(encoding="utf-8")
    copy_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("COPY")]
    assert not any("JKincoListen.py" in line for line in copy_lines), copy_lines


def test_backend_never_imports_a_desktop_monolith():
    offenders = []
    for path in sorted((ROOT / "backend").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any("JKincoListen" in alias.name for alias in node.names):
                offenders.append(path.name)
            if isinstance(node, ast.ImportFrom) and node.module and "JKincoListen" in node.module:
                offenders.append(path.name)
    assert not offenders, f"后端不应 import 单机版界面：{offenders}"


@pytest.mark.parametrize("package", ["fastapi", "funasr"])
def test_key_runtime_packages_are_pinned_in_requirements(package):
    assert package in _requirement_names(REQUIREMENTS)
