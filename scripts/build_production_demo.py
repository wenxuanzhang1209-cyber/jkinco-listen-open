#!/usr/bin/env python3
"""Build the production-app demo used by the repository README.

The source frames are public screenshots from the production product tour at
https://jkincolisten.cloud/download/ios, captured 2026-10-29. They remain separate so the
marketing GIF can be rebuilt when the public product changes.

    python3 scripts/build_production_demo.py

Dependencies: pillow.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent.parent
FRAME_DIR = ROOT / "docs" / "production-app-frames"
ICON_PATH = ROOT / "assets" / "jkinco-listen-app-icon.png"
GIF_PATH = ROOT / "docs" / "production-app-demo.gif"
POSTER_PATH = ROOT / "docs" / "production-app-demo-poster.png"

W, H = 1200, 675
INK = (8, 23, 48)
BLUE = (32, 111, 244)
BLUE_DARK = (12, 61, 155)
CYAN = (59, 185, 234)
MUTED = (91, 108, 133)
LINE = (219, 228, 242)
WHITE = (255, 255, 255)

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


@dataclass(frozen=True)
class Scene:
    kicker: str
    english_title: tuple[str, str]
    chinese_line: str
    bullets: tuple[str, str, str]
    frames: tuple[tuple[str, int], ...]


SCENES = (
    Scene(
        kicker="01 · LIVE CAPTURE",
        english_title=("Record once.", "See every word."),
        chinese_line="边开会，边看到字幕。",
        bullets=(
            "One-tap mobile recording",
            "Transcript grows in real time",
            "Automatic scene selection",
        ),
        frames=(("recording-1.png", 1100), ("recording-2.png", 1200)),
    ),
    Scene(
        kicker="02 · AUTOMATIC MINUTES",
        english_title=("The meeting ends.", "The minutes arrive."),
        chinese_line="散会即自动成稿，直接导出 Word / PDF。",
        bullets=(
            "Structured minutes",
            "Decisions and action items",
            "Word / PDF export",
        ),
        frames=(("minutes-progress.png", 900), ("minutes-ready.png", 1800)),
    ),
    Scene(
        kicker="03 · MEETING Q&A",
        english_title=("Ask the room.", "Keep the thread."),
        chinese_line="基于纪要与原文追问，把下一步问出来。",
        bullets=(
            "Sona grounded Q&A",
            "Decisions, owners, next steps",
            "Answers tied to the meeting",
        ),
        frames=(("sona-idle.png", 1100), ("sona-answer.png", 2200)),
    ),
)


def _font(paths: list[str], size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    for path in paths:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise SystemExit("No suitable font found; install PingFang, Noto Sans CJK, or DejaVu Sans.")


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255
    )
    return mask


def _background() -> Image.Image:
    image = Image.new("RGB", (W, H), (246, 250, 255))
    draw = ImageDraw.Draw(image)
    for y in range(H):
        ratio = y / H
        colour = tuple(
            round(a + (b - a) * ratio)
            for a, b in zip((247, 251, 255), (235, 244, 255))
        )
        draw.line((0, y, W, y), fill=colour)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((755, -180, 1370, 435), fill=(56, 145, 255, 50))
    glow_draw.ellipse((-260, 420, 390, 930), fill=(78, 195, 255, 28))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")

    grid = ImageDraw.Draw(image)
    for x in range(0, W, 56):
        grid.line((x, 0, x, H), fill=(255, 255, 255), width=1)
    return image


def _phone(frame_path: Path) -> Image.Image:
    frame = Image.open(frame_path).convert("RGB")
    max_width, max_height = 276, 548
    scale = min(max_width / frame.width, max_height / frame.height)
    size = (round(frame.width * scale), round(frame.height * scale))
    frame = frame.resize(size, Image.Resampling.LANCZOS)
    frame.putalpha(_rounded_mask(size, max(18, round(size[0] * 38 / 392))))
    return frame


def _draw_phone(image: Image.Image, frame_path: Path) -> None:
    phone = _phone(frame_path)
    x = 842 - phone.width // 2
    y = 331 - phone.height // 2

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (x + 7, y + 17, x + phone.width + 7, y + phone.height + 17),
        radius=32,
        fill=(24, 58, 108, 58),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    image.alpha_composite(shadow)

    border = Image.new("RGBA", (phone.width + 10, phone.height + 10), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border)
    border_draw.rounded_rectangle(
        (0, 0, border.width - 1, border.height - 1),
        radius=32,
        fill=(255, 255, 255, 235),
    )
    image.alpha_composite(border, (x - 5, y - 5))
    image.alpha_composite(phone, (x, y))


def _draw_scene(image: Image.Image, scene: Scene, frame_path: Path) -> Image.Image:
    image = image.convert("RGBA")
    draw = ImageDraw.Draw(image)
    headline = _font(LATIN_FONTS, 55, index=1)
    cjk = _font(CJK_FONTS, 26, index=1)
    body = _font(LATIN_FONTS, 24)
    small = _font(LATIN_FONTS, 19)
    kicker = _font(LATIN_FONTS, 18, index=1)
    url_font = _font(LATIN_FONTS, 20, index=1)

    icon = Image.open(ICON_PATH).convert("RGBA")
    icon = icon.resize((48, 48), Image.Resampling.LANCZOS)
    image.alpha_composite(icon, (64, 42))

    draw.text((126, 46), "JKinco Listen", font=_font(LATIN_FONTS, 25, index=1), fill=INK)
    draw.text((126, 75), "筑听 · production product tour", font=cjk, fill=MUTED)
    pill = "PRODUCTION APP"
    pill_width = draw.textlength(pill, font=kicker) + 28
    draw.rounded_rectangle((300, 49, 300 + pill_width, 80), radius=16, fill=(225, 237, 255))
    draw.text((314, 57), pill, font=kicker, fill=BLUE_DARK)

    draw.text((64, 132), scene.kicker, font=kicker, fill=BLUE)
    y = 165
    for line in scene.english_title:
        draw.text((64, y), line, font=headline, fill=INK)
        y += 60
    draw.text((64, y + 4), scene.chinese_line, font=cjk, fill=BLUE_DARK)

    y = 340
    for bullet in scene.bullets:
        draw.ellipse((67, y + 8, 77, y + 18), fill=CYAN)
        draw.text((94, y), bullet, font=body, fill=INK)
        y += 42

    draw.text((64, 492), "FOR ANY HIGH-CONTEXT MEETING", font=kicker, fill=BLUE)
    draw.text(
        (64, 519),
        "Management · Customer · Project · Interview · Training · Research",
        font=small,
        fill=MUTED,
    )

    draw.rounded_rectangle((64, 574, 708, 626), radius=18, fill=WHITE, outline=LINE, width=2)
    draw.text((84, 588), "Try the production app", font=url_font, fill=INK)
    draw.text(
        (682 - draw.textlength("jkincolisten.cloud/download/ios", font=small), 591),
        "jkincolisten.cloud/download/ios",
        font=small,
        fill=BLUE,
    )
    draw.text((712, 585), "→", font=url_font, fill=BLUE)

    _draw_phone(image, frame_path)
    draw.text((64, 642), "Production app for iOS · Android · HarmonyOS", font=small, fill=MUTED)
    return image


def main() -> int:
    missing = [FRAME_DIR / name for scene in SCENES for name, _ in scene.frames if not (FRAME_DIR / name).exists()]
    if missing:
        raise SystemExit("Missing production frames:\n" + "\n".join(f"  {path}" for path in missing))
    if not ICON_PATH.exists():
        raise SystemExit(f"Missing app icon: {ICON_PATH}")

    frames: list[Image.Image] = []
    durations: list[int] = []
    for scene_index, scene in enumerate(SCENES):
        for frame_name, duration in scene.frames:
            image = _background()
            image = _draw_scene(image, scene, FRAME_DIR / frame_name).convert("RGBA")
            frames.append(image)
            durations.append(duration)
            if scene_index == 0 and frame_name == "recording-2.png":
                image.convert("RGB").save(POSTER_PATH, optimize=True)

    # A shared adaptive palette avoids the colour flashing that per-frame quantisation creates.
    palette = frames[0].convert("RGB").quantize(
        colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
    )
    converted = []
    for frame in frames:
        item = frame.convert("RGB").quantize(
            palette=palette, dither=Image.Dither.FLOYDSTEINBERG
        )
        converted.append(item)

    converted[0].save(
        GIF_PATH,
        save_all=True,
        append_images=converted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"Wrote {GIF_PATH.relative_to(ROOT)} ({GIF_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"Wrote {POSTER_PATH.relative_to(ROOT)} ({POSTER_PATH.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
