"""基准脚本是 README 里那个数字的来源，它坏了 README 就成了空话。

这个脚本不像业务代码那样每天被跑到，所以它最容易在别人改词库、改脚本参数时
悄悄失效。下面几条守的是「它仍然测的是它声称在测的东西」。

真正的运行需要下载 1–2GB 模型，不适合放进单元测试；这里做的是契约检查：
目标词必须真的来自词库、README 里的数字必须和脚本的题量对得上、
以及那些让基准有意义的降质参数没有被人调回「干净音频」。
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "benchmark_domain_accuracy.py"


def _load():
    """只取常量，不执行 main（那会去下载模型）。"""
    spec = importlib.util.spec_from_file_location("bench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_parses_and_exposes_its_clips():
    module = _load()
    assert len(module.CLIPS) == 10, "README 说的是十段音频"
    for name, sentence, targets in module.CLIPS:
        assert name and sentence and targets
        for target in targets:
            assert target in sentence, f"{name}: 目标词 {target!r} 不在这段话里，永远不可能命中"


def test_total_targets_match_the_readme():
    """README 写的是 36 个目标词。脚本改了题量而 README 没跟着改，数字就对不上了。"""
    module = _load()
    total = sum(len(targets) for _, _, targets in module.CLIPS)
    assert total == 36, f"脚本现在有 {total} 个目标词，README 里写的是 36"
    for readme in ("README.md", "README.zh-CN.md"):
        text = (ROOT / readme).read_text(encoding="utf-8")
        assert "36" in text, f"{readme} 里的题量对不上脚本"


def test_targets_come_from_the_lexicon():
    """考的必须是词库真正偏置的词，否则这个基准测的不是这个项目的能力。"""
    from jkinco_lexicon import _SCENE_TERMS, terms_for

    module = _load()
    known = {term for scene in _SCENE_TERMS for term in terms_for(scene)}
    targets = {t for _, _, ts in module.CLIPS for t in ts}
    # 允许少量长词组不在词表里（词表有 15 字上限），但主体必须来自词库
    outside = {t for t in targets if t not in known}
    assert len(outside) <= len(targets) * 0.35, (
        f"过多目标词不在词库里，基准测的不是本项目的能力：{sorted(outside)}"
    )


def test_audio_is_actually_degraded():
    """干净 TTS 上模型几乎不犯错 —— 把降质去掉，这个基准就永远是满分。

    实测：不加噪时十段里只有一处失误；加噪后是六处。差别全在这几个参数上。
    """
    module = _load()
    for knob in ("atempo", "anoisesrc", "alimiter"):
        assert knob in module.NOISE_FILTER, f"降质参数少了 {knob}，基准会失去区分度"


def test_it_degrades_gracefully_without_dependencies():
    """缺依赖时要说清楚缺哪个、怎么装，而不是抛 ImportError 栈。

    第一版这里漏写了 torchaudio（funasr 的隐式依赖），照着提示装完仍然跑不起来 ——
    是真跑一遍才发现的。
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "torchaudio" in source, "torchaudio 是隐式依赖，提示里必须写"
    tree = ast.parse(source)
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert handlers, "没有任何依赖检查"


@pytest.mark.parametrize("readme", ["README.md", "README.zh-CN.md"])
def test_readme_points_at_the_script(readme):
    text = (ROOT / readme).read_text(encoding="utf-8")
    assert "scripts/benchmark_domain_accuracy.py" in text, (
        "README 声称可复现，却没告诉读者跑哪个脚本"
    )


def test_published_numbers_match_the_recorded_results():
    """README 与结果页里的数字必须和 docs/benchmark-results.json 对得上。

    这份 JSON 是脚本真实跑出来的输出。手抄的数字会漂 —— 这次就漂过一次：
    修正标点比对口径之后总分从 35/36 变成 36/36，而 README、CHANGELOG、
    结果页三处都要跟着改。让测试来盯着，比让人记得住可靠。
    """
    import json

    recorded = json.loads((ROOT / "docs" / "benchmark-results.json").read_text(encoding="utf-8"))
    raw, corrected, total = recorded["raw"], recorded["corrected"], recorded["total"]

    for name in ("README.md", "README.zh-CN.md", "docs/BENCHMARK.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert f"{corrected}" in text and f"{raw}" in text, (
            f"{name} 里的数字和 docs/benchmark-results.json 对不上"
            f"（记录的是 {raw}/{total} -> {corrected}/{total}）"
        )


def test_punctuation_is_stripped_before_comparing():
    """标点插在词中间不算识别错误。

    实测「见证取样送检」被模型断成「见证取样，送检」—— 每个字都对。
    按原样比对会把它算成失败，那是在低估模型，也会让人误以为词库该为此负责。
    """
    module = _load()
    assert hasattr(module, "strip_punctuation"), "标点归一化被去掉了，分数会被低估"
    assert module.strip_punctuation("见证取样，送检") == "见证取样送检"
