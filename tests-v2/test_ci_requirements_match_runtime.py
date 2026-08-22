"""CI 装的东西必须和运行时装的东西一致。

`requirements-ci.txt` 刻意不写 `-r requirements.txt`：测试从不导入 FunASR
（jkinco_asr.py 是在函数内部惰性导入的），而把它拉进来会让 CI 的安装体积从
148MB / 52 个包涨到 774MB / 109 个——白涨，且每个 Dependabot PR 都要付一遍。

但这个做法开了一个口子：两份清单可能对同一个包写出不同的版本约束，
于是 CI 测的是一个运行时根本不会用的版本，而「CI 全绿」变成了一句空话。
最危险的形式是**上界**——`requirements.txt` 里写 `fastapi<0.140`、
CI 那份还是 `<1` 的话，CI 会拿 0.141 跑测试，而镜像里装的是 0.139。

本文件把这个口子堵上：两份清单共有的包，版本约束必须逐字相同。
FunASR 是唯一被有意排除的，明确写在下面。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "requirements.txt"
CI = ROOT / "requirements-ci.txt"

#: 有意不进 CI 的运行时依赖。加进这个集合前请先确认：测试真的不需要它。
DELIBERATELY_EXCLUDED = {"funasr"}

#: 只有 CI 需要的测试工具，运行时镜像不该有。
TEST_ONLY = {"pytest", "httpx"}


def _requirements(path: pathlib.Path) -> dict[str, str]:
    """解析成 {包名: 完整约束}。extras 归到包名里（uvicorn[standard] -> uvicorn）。"""
    parsed: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[")[0]
        for separator in (">=", "<=", "==", "~=", "!=", ">", "<"):
            name = name.split(separator)[0]
        parsed[name.strip().lower()] = line
    return parsed


def test_shared_packages_have_identical_constraints():
    """共有的包，约束必须一模一样 —— 否则 CI 测的不是将要发布的东西。"""
    runtime, ci = _requirements(RUNTIME), _requirements(CI)
    mismatched = {
        name: (runtime[name], ci[name])
        for name in set(runtime) & set(ci)
        if runtime[name] != ci[name]
    }
    assert not mismatched, (
        "requirements.txt 与 requirements-ci.txt 对同一个包写了不同的约束，"
        f"CI 会测到运行时不会用的版本：{mismatched}"
    )


def test_ci_covers_every_runtime_package_except_the_excluded_ones():
    """运行时新增了依赖却忘了同步给 CI，会以 ImportError 的形式炸在半路上。

    那种失败虽然响亮，但发生在测试执行阶段而不是清单检查阶段 —— 排查要多绕一圈。
    这条测试让它在更靠前的地方就说清楚是怎么回事。
    """
    runtime, ci = _requirements(RUNTIME), _requirements(CI)
    missing = set(runtime) - set(ci) - DELIBERATELY_EXCLUDED
    assert not missing, (
        f"这些运行时依赖没有同步到 requirements-ci.txt：{sorted(missing)}。"
        "要么加进去，要么在 DELIBERATELY_EXCLUDED 里写明为什么不需要。"
    )


def test_ci_does_not_smuggle_in_extra_runtime_packages():
    """反向：CI 里出现了运行时没有的包（测试工具除外），说明有依赖没进镜像。"""
    runtime, ci = _requirements(RUNTIME), _requirements(CI)
    extra = set(ci) - set(runtime) - TEST_ONLY
    assert not extra, (
        f"requirements-ci.txt 里有运行时没有的包：{sorted(extra)}。"
        "测试能过但镜像里没有，等于线上必崩。"
    )


def test_funasr_stays_out_of_ci_and_inside_runtime():
    """这条测的是那个「白装 626MB」的教训本身。

    requirements-ci.txt 原先第一行是 `-r requirements.txt`，注释却写着
    「不含 FunASR/torch，保持测试轻量」—— 实现和意图相反，而且没人发现，
    因为没有任何东西在检查它。
    """
    assert "funasr" in _requirements(RUNTIME), "运行时必须带本地 ASR"
    assert "funasr" not in _requirements(CI), "CI 又把 FunASR 拉回来了（+626MB）"
    # 只看有效行。第一版写成在全文里搜 "-r requirements.txt"，结果匹配到了上面那段
    # 「刻意不这么写」的注释本身 —— 在注释里匹配代码是个经典的假阳性。
    directives = [
        line.split("#", 1)[0].strip()
        for line in CI.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert "-r requirements.txt" not in directives, (
        "requirements-ci.txt 又开始整个引用 requirements.txt 了"
    )


@pytest.mark.parametrize("package", sorted(TEST_ONLY))
def test_test_tools_do_not_leak_into_the_runtime_image(package):
    assert package not in _requirements(RUNTIME), f"{package} 是测试工具，不该进运行时镜像"
