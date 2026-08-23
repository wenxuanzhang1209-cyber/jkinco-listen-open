#!/usr/bin/env python3
"""生成本仓库的 GitHub 社交预览图（分享链接时显示的那张卡片）。

    python3 scripts/build_social_preview.py            # 只生成本仓库的
    python3 scripts/build_social_preview.py --all DIR  # 生成全家族的，用于对比

没设过社交预览的仓库，GitHub 会自动拼一张通用卡：仓库名、头像、几个灰色数字。
链接发到 Twitter、Slack、Discord、HN，别人看到的就是那张 —— 一张不说明
这个项目是干什么的图。这个脚本把那张图换成一张说人话的。

为什么是脚本而不是手工导出的 PNG：手工产物没法维护。仓库里原先那张
`docs/social-preview.png` 就是这样 —— 它的 logo 是一个加载失败的占位符，
从图上直接能看出来，但没人能修，因为没有任何生成方式。

为什么 SPECS 里放着全家族六个仓库：因为它们本来就是一个产品。
同一套版式、同一个 logo、同一条页脚，读到任何一个仓库的这个文件，
都能看到另外五个是什么。改版式只改这一个文件，六张卡一起变。

尺寸固定 1280×640 —— GitHub 的推荐值，也是各家卡片的安全比例。
文案英文在前：这张图出现的场合以英文读者为主，中文放副标题。

依赖：pillow。中文字体从系统里找，找不到就跳过中文行，
而不是画出一排方块。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OWNER = "wenxuanzhang1209-cyber"

#: 本仓库在 SPECS 里的键。同一份脚本发到六个仓库，只有这两行不同。
REPO = "jkinco-listen-open"
LOGO_RELATIVE = "assets/jkinco_logo_cropped.png"

W, H = 1280, 640
BG_TOP = (13, 17, 23)          # GitHub 深色底，卡片贴在深色 UI 上不突兀
BG_BOTTOM = (16, 24, 38)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
AMBER = (255, 200, 87)
WHITE = (255, 255, 255)
MUTED = (139, 155, 176)
GRID = (22, 30, 44)
CHIP_IDLE = (58, 70, 92)

#: 中文字体候选。macOS 与常见 Linux 发行版都覆盖到。
CJK_FONTS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
LATIN_FONTS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

MARGIN = 76
MAX_TEXT_WIDTH = W - MARGIN * 2

# ---------------------------------------------------------------------------
# 每张卡的文案。
#
# 规矩只有一条：**chips 里的每一项都要能在仓库里查到**。
# 写「fast」「powerful」「best-in-class」这类词，第一个较真的读者就会来问
# 凭什么，而你答不上来。写「269 tests」「zero dependencies」「10,000 recipes」，
# 他自己就去仓库里数了 —— 数得对，信任就建立了。
#
# headline 是两行，每行若干 (文字, 颜色) 段。字号自动收缩以适应宽度。
# ---------------------------------------------------------------------------
SPECS: dict[str, dict] = {
    "jkinco-listen-open": {
        "name": "JKinco Listen",
        "cjk_sub": "筑听 · 开源本地版",
        "headline": [
            [("Audio in. Filed minutes out.", WHITE)],
            [("Nothing leaves ", WHITE), ("your machine.", AMBER)],
        ],
        "cjk_line": "录音进，结构化纪要出。数据不出你的电脑。",
        "flow": ["Local ASR", "scene detection", "DOCX / PDF"],
        # 960: pytest --collect-only 的实际数量，由 test_social_preview.py 盯着
        "chips": [("FunASR + Ollama", False), ("Zero API key", True),
                  ("Offline capable", True), ("960 tests", False), ("MIT", False)],
    },
    "jkinco-slides": {
        "name": "JKinco Slides",
        "cjk_sub": "AI 原生的演示文稿工作台",
        "headline": [
            [("Every object stays editable.", WHITE)],
            [("PPTX survives ", WHITE), ("the round trip.", AMBER)],
        ],
        "cjk_line": "每个对象都可编辑，PPTX 进出不失真。",
        "flow": ["Prompt", "editable objects", "PPTX in / PPTX out"],
        # 269: README 徽章与 CI 一致；18: packages/ 下的目录数
        "chips": [("TypeScript", False), ("18 packages", False),
                  ("269 tests", True), ("Round-trip safe", True), ("MIT", False)],
    },
    "personal-life-hub": {
        "name": "NORTH",
        "cjk_sub": "个人工作与生活中枢",
        "headline": [
            [("Work, life, and private notes.", WHITE)],
            [("One timeline, ", WHITE), ("real boundaries.", AMBER)],
        ],
        "cjk_line": "同一条时间线，用空间边界避免互相干扰。",
        "flow": ["Capture", "spaces", "one timeline"],
        # 源码里 0 处 fetch/axios，数据只进 localStorage
        "chips": [("React + TypeScript", False), ("100% local storage", True),
                  ("No network calls", True), ("MIT", False)],
    },
    "recipe-miniprogram": {
        "name": "Recipe Mini Program",
        "cjk_sub": "家常菜小程序 · 附数据质检报告",
        "headline": [
            [("10,000 recipes — and an audit", WHITE)],
            [("that names ", WHITE), ("its own defect.", AMBER)],
        ],
        "cjk_line": "一万道菜，以及一份点名自己缺陷的质检报告。",
        "flow": ["Seeded data", "quality audit", "shipped with the repo"],
        # 10,000 / 96,908: 从捆绑的 recipe.sqlite 数出来的；63: 集成测试数
        "chips": [("10,000 recipes", False), ("96,908 ingredient links", False),
                  ("63 tests", True), ("Audit included", True), ("MIT", False)],
    },
    "JKinco-Skills-Lab": {
        "name": "JKinco Skills Lab",
        "cjk_sub": "把品牌设计固化成可复用技能",
        "headline": [
            [("Design work, frozen into skills", WHITE)],
            [("that produce ", WHITE), ("the same result.", AMBER)],
        ],
        "cjk_line": "输入需求，输出结构稿、提示词、质检结果。",
        "flow": ["Brief", "skill", "checked output"],
        # 2: 仓库根下两个 Skill 目录，各含 SKILL.md
        "chips": [("Agent Skills format", False), ("2 skills", False),
                  ("Quality-checked output", True), ("MIT", False)],
    },
    "jkinco-tools": {
        "name": "JKinco Tools",
        "cjk_sub": "会自检的小自动化脚本",
        "headline": [
            [("Small automation scripts", WHITE)],
            [("that ", WHITE), ("verify their own work.", AMBER)],
        ],
        "cjk_line": "轻量自动化脚本——每一步都自己校验结果。",
        "flow": ["Run", "self-check", "fail loudly"],
        # requirements.txt 里 0 行非注释依赖：只用标准库
        "chips": [("Python stdlib only", False), ("Zero dependencies", True),
                  ("Self-verifying", True), ("MIT", False)],
    },
}


def _font(paths: list[str], size: int, index: int = 0):
    from PIL import ImageFont

    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return None


def _fit(draw, segments, paths, start_size: int, max_width: int, index: int = 0):
    """挑一个能把整行放进 max_width 的最大字号。

    标题长度各仓库不一样（「Small automation scripts」和
    「10,000 recipes — and an audit」差了 30%），写死字号必然有一张会溢出画布。
    溢出不会报错，只会被裁掉半个单词。
    """
    text = "".join(segment for segment, _ in segments)
    for size in range(start_size, 28, -2):
        font = _font(paths, size, index=index) or _font(paths, size)
        if font is None:
            return None, start_size
        if draw.textlength(text, font=font) <= max_width:
            return font, size
    return _font(paths, 30, index=index) or _font(paths, 30), 30


def render(spec: dict, repo: str, logo_path: Path, output: Path) -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(image)

    # 竖向渐变 + 右上一团光，避免大片死黑
    for y in range(H):
        ratio = y / H
        draw.line([(0, y), (W, y)],
                  fill=tuple(int(a + (b - a) * ratio) for a, b in zip(BG_TOP, BG_BOTTOM)))
    glow = Image.new("RGB", (W, H), BG_BOTTOM)
    glow_draw = ImageDraw.Draw(glow)
    for radius in range(360, 0, -12):
        alpha = (360 - radius) / 360 * 0.10
        glow_draw.ellipse([1040 - radius, 60 - radius, 1040 + radius, 60 + radius],
                          fill=tuple(int(b + (a - b) * alpha) for a, b in zip(BLUE, BG_BOTTOM)))
    image = Image.blend(image, glow, 0.55)
    draw = ImageDraw.Draw(image)

    # 细网格，密度低到只提供质感
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=GRID, width=1)

    latin_bold = _font(LATIN_FONTS, 30, index=1) or _font(LATIN_FONTS, 30)
    cjk_sub = _font(CJK_FONTS, 26)
    body = _font(LATIN_FONTS, 27)
    chip_font = _font(LATIN_FONTS, 22)
    url_font = _font(LATIN_FONTS, 21)

    # 真的把 logo 画进去。按宽度缩放而不是塞进正方形 —— logo 是横版的，
    # thumbnail((84,84)) 会让它只有 24px 高，在 1280 宽的卡片上小到看不清。
    x0 = MARGIN
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize((140, max(1, round(logo.height * 140 / logo.width))),
                           Image.LANCZOS)
        image.paste(logo, (x0, 118 - logo.height // 2), logo)
        x0 += logo.width + 26

    if latin_bold:
        draw.text((x0, 112), spec["name"], font=latin_bold, fill=WHITE)
    if cjk_sub:
        draw.text((x0, 150), spec["cjk_sub"], font=cjk_sub, fill=BLUE)

    # 主张：英文在前，这张图主要出现在英文语境里
    y = 224
    for line in spec["headline"]:
        font, size = _fit(draw, line, LATIN_FONTS, 62, MAX_TEXT_WIDTH, index=1)
        if font is None:
            break
        x = MARGIN
        for text, colour in line:
            draw.text((x, y), text, font=font, fill=colour)
            x += draw.textlength(text, font=font)
        y += size + 14
    if cjk_sub:
        draw.text((MARGIN + 2, 388), spec["cjk_line"], font=cjk_sub, fill=MUTED)

    # 流程行。箭头单独用 CJK 字体画。
    #
    # 第一版整行用 Helvetica 画，箭头「→」渲染成了方块 —— 拉丁字体没有这个
    # 字形，而 PIL 不报错，只会画一个豆腐块。这种错只能靠看一眼产物发现，
    # 生成成功不等于画对了。
    if body:
        arrow_font = _font(CJK_FONTS, 27) or body
        x = MARGIN
        for index, part in enumerate(spec["flow"]):
            if index:
                draw.text((x, 432), "→", font=arrow_font, fill=BLUE)
                x += draw.textlength("→", font=arrow_font) + 16
            draw.text((x, 432), part, font=body, fill=MUTED)
            x += draw.textlength(part, font=body) + 16

    if chip_font:
        x, y = MARGIN, 494
        for text, highlight in spec["chips"]:
            width = draw.textlength(text, font=chip_font) + 34
            draw.rounded_rectangle([x, y, x + width, y + 44], radius=22,
                                   outline=GREEN if highlight else CHIP_IDLE, width=2)
            draw.text((x + 17, y + 10), text, font=chip_font,
                      fill=WHITE if highlight else MUTED)
            x += width + 14

    if url_font:
        text = f"github.com/{OWNER}/{repo}"
        draw.text((W - MARGIN - draw.textlength(text, font=url_font), 574),
                  text, font=url_font, fill=BLUE)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    return output


def main() -> int:
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("需要 pillow：pip install pillow")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO, choices=sorted(SPECS),
                        help="生成哪个仓库的卡片（默认：本仓库）")
    parser.add_argument("--all", metavar="DIR",
                        help="生成全家族六张，输出到 DIR，用于并排对比版式")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    logo = root / LOGO_RELATIVE

    if args.all:
        out_dir = Path(args.all)
        for repo, spec in SPECS.items():
            path = render(spec, repo, logo, out_dir / f"{repo}.png")
            print(f"  {path}")
        return 0

    output = root / "docs" / "social-preview.png"
    path = render(SPECS[args.repo], args.repo, logo, output)
    size = path.stat().st_size / 1024
    print(f"已生成 {path.relative_to(root)}  {W}x{H}  {size:.0f} KB")
    print("\n这张图不会自动生效 —— GitHub 的社交预览只能在网页上设置：")
    print("  仓库 → Settings → General → Social preview → Upload an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
