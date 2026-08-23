#!/usr/bin/env python3
"""生成 GitHub 社交预览图（分享链接时显示的那张卡片）。

    python3 scripts/build_social_preview.py

写成脚本而不是手工导出一张 PNG，是因为手工产物没法维护：仓库里原先那张
`docs/social-preview.png` 没有任何生成方式，改一个字都得重新找设计工具，
而且它的 logo 是一个加载失败的占位符 —— 从图上就能看出来，但没人能修。

尺寸固定 1280×640：GitHub 的推荐值，也是 Twitter/Slack/Discord 卡片的安全比例。
文案以英文为主：这张图出现的场合（HN、Reddit、Twitter）以英文读者为主，
中文放副标题。

依赖：pillow。中文字体从系统里找，找不到就退回英文单语版本而不是画出方块。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "social-preview.png"

W, H = 1280, 640
BG_TOP = (13, 17, 23)          # GitHub 深色底
BG_BOTTOM = (16, 24, 38)
ACCENT = (88, 166, 255)        # 蓝
ACCENT_2 = (63, 185, 80)       # 绿
AMBER = (255, 200, 87)
WHITE = (255, 255, 255)
MUTED = (139, 155, 176)

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


def _font(paths: list[str], size: int, index: int = 0):
    from PIL import ImageFont

    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return None


def main() -> int:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("需要 pillow：pip install pillow")

    image = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(image)

    # 竖向渐变 + 右上角的一团光，避免大片死黑
    for y in range(H):
        ratio = y / H
        draw.line(
            [(0, y), (W, y)],
            fill=tuple(int(a + (b - a) * ratio) for a, b in zip(BG_TOP, BG_BOTTOM)),
        )
    glow = Image.new("RGB", (W, H), BG_BOTTOM)
    glow_draw = ImageDraw.Draw(glow)
    for radius in range(360, 0, -12):
        alpha = (360 - radius) / 360 * 0.10
        glow_draw.ellipse(
            [1040 - radius, 60 - radius, 1040 + radius, 60 + radius],
            fill=tuple(int(b + (a - b) * alpha) for a, b in zip(ACCENT, BG_BOTTOM)),
        )
    image = Image.blend(image, glow, 0.55)
    draw = ImageDraw.Draw(image)

    # 细网格，密度低到只提供质感
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=(22, 30, 44), width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=(22, 30, 44), width=1)

    latin_bold = _font(LATIN_FONTS, 30, index=1) or _font(LATIN_FONTS, 30)
    title = _font(LATIN_FONTS, 62, index=1) or _font(LATIN_FONTS, 62)
    cjk_sub = _font(CJK_FONTS, 26)
    body = _font(LATIN_FONTS, 27)
    chip = _font(LATIN_FONTS, 22)
    url = _font(LATIN_FONTS, 21)

    # 真的把 logo 画进去。原先那张图的 logo 是加载失败的占位符 —— 用本地文件
    # 而不是外链，图就不会再坏。
    logo_path = ROOT / "assets" / "jkinco_logo_cropped.png"
    x0 = 76
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        # 按宽度缩放而不是塞进正方形：logo 是横版的，thumbnail((84,84)) 会让它
        # 只有 24px 高，在 1280 宽的卡片上小到看不清。
        logo = logo.resize((140, max(1, round(logo.height * 140 / logo.width))),
                           Image.LANCZOS)
        image.paste(logo, (x0, 118 - logo.height // 2), logo)
        x0 += logo.width + 26

    if latin_bold:
        draw.text((x0, 112), "JKinco Listen", font=latin_bold, fill=WHITE)
    if cjk_sub:
        draw.text((x0, 150), "筑听 · 开源本地版", font=cjk_sub, fill=ACCENT)

    # 主张：英文在前，因为这张图主要出现在英文语境里
    if title:
        draw.text((76, 224), "Audio in. Filed minutes out.", font=title, fill=WHITE)
        draw.text((76, 300), "Nothing leaves ", font=title, fill=WHITE)
        offset = draw.textlength("Nothing leaves ", font=title)
        draw.text((76 + offset, 300), "your machine.", font=title, fill=AMBER)
    if cjk_sub:
        draw.text((78, 388), "录音进，结构化纪要出。数据不出你的电脑。", font=cjk_sub, fill=MUTED)

    # 分段画，箭头单独用 CJK 字体。
    #
    # 第一版整行用 Helvetica 画，箭头「→」渲染成了方块 —— 拉丁字体没有这个字形，
    # 而 PIL 不会报错，只会画一个豆腐块。这种错只能靠**看一眼产物**发现，
    # 生成成功不等于画对了。
    if body:
        arrow_font = _font(CJK_FONTS, 27) or body
        x = 76
        for index, part in enumerate(["Local ASR", "scene detection", "DOCX / PDF"]):
            if index:
                draw.text((x, 432), "\u2192", font=arrow_font, fill=ACCENT)
                x += draw.textlength("\u2192", font=arrow_font) + 16
            draw.text((x, 432), part, font=body, fill=MUTED)
            x += draw.textlength(part, font=body) + 16

    # 事实性标签。每一条都能在仓库里查到，不写形容词。
    chips = ["FunASR + Ollama", "Zero API key", "Offline capable", "948 tests", "MIT"]
    x, y = 76, 494
    for index, text in enumerate(chips):
        if not chip:
            break
        width = draw.textlength(text, font=chip) + 34
        colour = ACCENT_2 if index in (1, 2) else (58, 70, 92)
        draw.rounded_rectangle([x, y, x + width, y + 44], radius=22,
                               outline=colour, width=2)
        draw.text((x + 17, y + 10), text, font=chip, fill=WHITE if index in (1, 2) else MUTED)
        x += width + 14

    if url:
        text = "github.com/wenxuanzhang1209-cyber/jkinco-listen-open"
        draw.text((W - 76 - draw.textlength(text, font=url), 574), text, font=url, fill=ACCENT)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, optimize=True)
    print(f"已生成 {OUTPUT.relative_to(ROOT)}  {image.size[0]}x{image.size[1]}  "
          f"{OUTPUT.stat().st_size / 1024:.0f} KB")
    print("\n这张图不会自动生效 —— GitHub 的社交预览只能在网页上设置：")
    print("  仓库 → Settings → General → Social preview → Upload an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
