"""社交预览图生成器的测试。

这里守的是一类**不会报错的错**：PIL 遇到字体里没有的字符不会抛异常，
它会画一个 .notdef 字形 —— 屏幕上就是一个方块。第一版卡片的箭头「→」
就是这么变成方块的，用拉丁字体画的，而脚本打印「已生成」一切正常。

所以有了 `_is_tofu`：把字符渲染出来，和一个保证不存在的私用区码位
（U+E000）渲染出来的图比。一模一样，就说明字体在用同一个 .notdef 兜底，
也就是屏幕上会出现方块。

这些卡片是别人点开仓库之前看到的第一样东西。画错了没人会告诉你，
他们只是不点。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_social_preview.py"

pytest.importorskip("PIL", reason="社交预览图需要 pillow")


def _module():
    spec = importlib.util.spec_from_file_location("build_social_preview", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_social_preview"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _module()


def test_this_repo_has_a_spec(gen):
    """REPO 常量必须能在 SPECS 里查到 —— 否则脚本一跑就 KeyError。"""
    assert gen.REPO in gen.SPECS


def test_every_spec_is_complete(gen):
    required = {"name", "cjk_sub", "headline", "cjk_line", "flow", "chips"}
    for repo, spec in gen.SPECS.items():
        assert required <= set(spec), f"{repo} 缺字段：{required - set(spec)}"
        assert len(spec["headline"]) == 2, f"{repo} 主张必须正好两行"
        assert spec["chips"], f"{repo} 没有标签"


def test_the_whole_family_is_listed(gen):
    """SPECS 就是「这些仓库是一个产品」的可执行证明。

    少一个，那个仓库的卡片就没人生成，它在分享链接里长得跟别人不一样。
    """
    assert set(gen.SPECS) == {
        "jkinco-listen-open", "jkinco-slides", "personal-life-hub",
        "recipe-miniprogram", "JKinco-Skills-Lab", "jkinco-tools",
    }


def _is_tofu(font, char: str) -> bool:
    """这个字符会不会被画成方块。

    拿它和 U+E000（私用区，任何正常字体都不会给它字形）比较渲染结果。
    两者位图完全相同，说明都落到了同一个 .notdef 兜底字形上。
    """
    notdef = font.getmask("")
    mask = font.getmask(char)
    if mask.size != notdef.size:
        return False
    return bytes(mask) == bytes(notdef)


def test_no_character_renders_as_a_box(gen):
    """卡片上出现的每个字符，都要在实际用来画它的那个字体里有字形。

    这正是第一版翻车的地方：箭头交给了拉丁字体。
    """
    latin = gen._font(gen.LATIN_FONTS, 27)
    cjk = gen._font(gen.CJK_FONTS, 27)
    if latin is None:
        pytest.skip("这台机器上没有拉丁字体")

    for repo, spec in gen.SPECS.items():
        latin_text = "".join(
            text for line in spec["headline"] for text, _ in line
        ) + "".join(spec["flow"]) + "".join(text for text, _ in spec["chips"]) + spec["name"]
        for char in set(latin_text):
            assert not _is_tofu(latin, char), f"{repo}: 拉丁字体画不出 {char!r}"

        # 箭头走 CJK 字体，中文行同理
        if cjk is not None:
            for char in set(spec["cjk_line"] + spec["cjk_sub"] + "→"):
                assert not _is_tofu(cjk, char), f"{repo}: 中文字体画不出 {char!r}"


def test_headlines_fit_inside_the_canvas(gen):
    """标题长度各仓库差 30%，字号必须收得住。

    溢出不报错，只是把半个单词裁在画布外面。
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (gen.W, gen.H)))
    for repo, spec in gen.SPECS.items():
        for line in spec["headline"]:
            font, size = gen._fit(draw, line, gen.LATIN_FONTS, 62,
                                  gen.MAX_TEXT_WIDTH, index=1)
            if font is None:
                pytest.skip("这台机器上没有拉丁字体")
            text = "".join(segment for segment, _ in line)
            width = draw.textlength(text, font=font)
            assert width <= gen.MAX_TEXT_WIDTH, (
                f"{repo}: 「{text}」在 {size}px 下宽 {width:.0f}，超出 {gen.MAX_TEXT_WIDTH}"
            )
            assert size >= 40, f"{repo}: 「{text}」被压到 {size}px，卡片上看不清"


def test_chips_and_flow_stay_on_the_card(gen):
    """标签行和流程行都是横排的，加一个词就可能顶出右边缘。"""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (gen.W, gen.H)))
    chip_font = gen._font(gen.LATIN_FONTS, 22)
    body = gen._font(gen.LATIN_FONTS, 27)
    if chip_font is None or body is None:
        pytest.skip("这台机器上没有拉丁字体")

    for repo, spec in gen.SPECS.items():
        x = gen.MARGIN
        for text, _ in spec["chips"]:
            x += draw.textlength(text, font=chip_font) + 34 + 14
        assert x <= gen.W - gen.MARGIN, f"{repo}: 标签行超出右边缘（{x:.0f}）"

        x = gen.MARGIN
        for index, part in enumerate(spec["flow"]):
            if index:
                x += draw.textlength("→", font=body) + 16
            x += draw.textlength(part, font=body) + 16
        assert x <= gen.W - gen.MARGIN, f"{repo}: 流程行超出右边缘（{x:.0f}）"


def test_render_produces_the_documented_size(tmp_path, gen):
    """GitHub 按 1280×640 裁切，尺寸错了会被压扁或裁掉。"""
    from PIL import Image

    output = gen.render(gen.SPECS[gen.REPO], gen.REPO,
                        ROOT / gen.LOGO_RELATIVE, tmp_path / "card.png")
    with Image.open(output) as image:
        assert image.size == (1280, 640)


def test_committed_card_matches_the_script(gen):
    """仓库里那张 PNG 必须是当前脚本的产物。

    不然改了文案却忘了重新生成，仓库里躺着的还是旧卡 —— 而这件事
    只有在别人分享链接的时候才会暴露。

    比的是写在 PNG 元数据里的**文案指纹**，不是文件字节。
    第一版比的是字节，结果 CI 一跑就红：那张图在 macOS 上用 PingFang
    和 Helvetica 渲染，Ubuntu runner 上只有 DejaVu，字宽不同，
    字节当然对不上。字体不同不代表图过期，但文案不同一定代表。
    """
    from PIL import Image

    committed = ROOT / "docs" / "social-preview.png"
    if not committed.exists():
        pytest.skip("还没有提交过社交预览图")

    with Image.open(committed) as image:
        stored = image.text.get("jkinco-spec")
        assert image.size == (gen.W, gen.H)

    assert stored, (
        "这张 PNG 里没有 jkinco-spec 指纹，说明它不是当前脚本生成的。"
        "重新跑一次 scripts/build_social_preview.py"
    )
    assert stored == gen.fingerprint(gen.SPECS[gen.REPO], gen.REPO), (
        "docs/social-preview.png 里的文案和 SPECS 里的对不上，"
        "重新跑一次 scripts/build_social_preview.py"
    )


def test_fingerprint_reacts_to_a_copy_change(gen):
    """指纹本身要真的会变，否则上面那条测试是空转。"""
    import copy

    spec = copy.deepcopy(gen.SPECS[gen.REPO])
    before = gen.fingerprint(spec, gen.REPO)
    spec["chips"] = list(spec["chips"]) + [("New claim", False)]
    assert gen.fingerprint(spec, gen.REPO) != before


def test_the_tofu_detector_actually_detects_tofu(gen):
    """给检测器本身做体检。

    没有这一条，上面那些「没有方块」的断言可能全是空转 —— `_is_tofu`
    永远返回 False 的话，它们照样绿。所以这里拿**当初真正翻车的那个组合**
    去问它：拉丁字体画箭头，你认不认得出来。
    """
    latin = gen._font(gen.LATIN_FONTS, 27)
    cjk = gen._font(gen.CJK_FONTS, 27)
    if latin is None or cjk is None:
        pytest.skip("这台机器上缺字体")

    assert _is_tofu(latin, "→"), "检测器失灵：这正是第一版画出方块的那个组合"
    assert _is_tofu(latin, "筑"), "检测器失灵：拉丁字体没有汉字"
    assert not _is_tofu(cjk, "→"), "误报：中文字体是有箭头的"
    assert not _is_tofu(latin, "A"), "误报：拉丁字体当然有 A"


def test_the_test_count_on_the_card_is_current(request, gen):
    """卡片上写着「960 tests」—— 这个数字必须是真的。

    加了这条测试本身就让它从 948 变成了 957。数字写在图片里，
    过期了不会有任何报错，只有别人数了发现对不上。所以让它自己核对：
    跑全量的时候，拿 pytest 实际收集到的数量比。

    只在跑全量时检查 —— 用 -k 过滤或只跑单个文件时收集数当然对不上。
    """
    chips = dict(gen.SPECS["jkinco-listen-open"]["chips"])
    claimed = next(int(text.split()[0]) for text in chips if text.endswith("tests"))

    # 怎么判断「这是一次全量运行」：无参调用时 pytest 会把 pyproject 里的
    # testpaths 填进 config.args，所以 args == testpaths 就是没加过滤条件。
    # 第一版拿 rootpath 去比，结果全量运行也被判成过滤，测试永远 skip ——
    # 一个永远不跑的测试比没有还糟，它看起来是绿的。
    config = request.config
    testpaths = list(config.getini("testpaths") or [])
    unfiltered = list(config.args) in ([], testpaths)
    if config.option.keyword or config.option.markexpr or not unfiltered:
        pytest.skip("这是一次过滤后的运行，收集数不代表全量")

    collected = request.session.testscollected
    assert claimed == collected, (
        f"卡片上写着 {claimed} 个测试，实际收集到 {collected} 个。"
        f"改 scripts/build_social_preview.py 里的标签，然后重新生成图片。"
    )
