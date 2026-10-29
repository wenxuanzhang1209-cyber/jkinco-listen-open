#!/usr/bin/env python3
"""Build product-tour GIFs from the public JKinco Listen download page.

The capture step uses the Playwright dependency already installed for the
frontend. This script turns those frames into compact README assets and keeps
the visual tied to the real production product instead of a mock-up.

    cd frontend && npm install
    cd ..
    python3 scripts/build_production_tour.py

Dependencies: Node.js, Playwright, Chrome/Chromium, and Pillow.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CAPTURE_SCRIPT = ROOT / "scripts" / "capture_production_tour.mjs"
ICON_PATH = ROOT / "assets" / "jkinco-listen-app-icon.png"

DURATION_MS = 150
COMPOSITE_SIZE = (1200, 675)
INK = (6, 18, 40)
WHITE = (255, 255, 255)
BLUE = (64, 148, 255)
CYAN = (61, 195, 238)
MUTED = (160, 178, 207)
CARD = (12, 31, 60)
CARD_LINE = (35, 65, 108)

LATIN_FONTS = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass(frozen=True)
class Demo:
    kind: str
    eyebrow: str
    title: str


DEMOS = (
    Demo("recording", "01 · CAPTURE", "Live transcript"),
    Demo("generation", "02 · FILE", "Minutes + exports"),
    Demo("sona", "03 · ASK", "Grounded meeting Q&A"),
)


def _font(size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    for candidate in LATIN_FONTS:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size, index=index)
        except OSError:
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    raise SystemExit("No suitable Latin font found.")


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=255,
    )
    return mask


def _fit_device(image: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    image = image.convert("RGB")
    scale = min(max_size[0] / image.width, max_size[1] / image.height)
    size = (round(image.width * scale), round(image.height * scale))
    image = image.resize(size, Image.Resampling.LANCZOS)
    image.putalpha(_rounded_mask(size, max(18, round(size[0] * 0.075))))
    return image


def _global_palette(frames: list[Image.Image], colors: int) -> Image.Image:
    sample = frames[len(frames) // 2].convert("RGB")
    return sample.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )


def _save_gif(frames: list[Image.Image], output: Path, colors: int, poster: Path) -> None:
    palette = _global_palette(frames, colors)
    indexed = [
        frame.convert("RGB").quantize(
            palette=palette,
            dither=Image.Dither.NONE,
        )
        for frame in frames
    ]
    frames[-1].convert("RGB").save(poster, optimize=True)
    indexed[0].save(
        output,
        save_all=True,
        append_images=indexed[1:],
        duration=DURATION_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )


def _background() -> Image.Image:
    image = Image.new("RGB", COMPOSITE_SIZE, (5, 17, 38))
    draw = ImageDraw.Draw(image)
    for y in range(COMPOSITE_SIZE[1]):
        ratio = y / COMPOSITE_SIZE[1]
        colour = tuple(
            round(start + (end - start) * ratio)
            for start, end in zip((7, 23, 49), (4, 13, 31))
        )
        draw.line((0, y, COMPOSITE_SIZE[0], y), fill=colour)

    glow = Image.new("RGBA", COMPOSITE_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((560, -250, 1270, 390), fill=(31, 116, 255, 70))
    glow_draw.ellipse((-260, 330, 420, 900), fill=(35, 190, 238, 30))
    glow = glow.filter(ImageFilter.GaussianBlur(83))
    return Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")


def _composite_frame(devices: list[Image.Image], index: int) -> Image.Image:
    image = _background().convert("RGBA")
    draw = ImageDraw.Draw(image)

    icon = Image.open(ICON_PATH).convert("RGBA").resize((52, 52), Image.Resampling.LANCZOS)
    image.alpha_composite(icon, (54, 32))
    draw.text((120, 36), "JKinco Listen", font=_font(27, index=1), fill=WHITE)
    draw.text(
        (120, 67),
        "From live voice to a document people can file.",
        font=_font(16),
        fill=MUTED,
    )

    pill = "PRODUCTION APP · iOS / ANDROID / HARMONYOS"
    pill_font = _font(15, index=1)
    pill_width = round(draw.textlength(pill, font=pill_font)) + 36
    draw.rounded_rectangle(
        (1138 - pill_width, 42, 1138, 79),
        radius=19,
        fill=(16, 45, 86),
        outline=(43, 96, 168),
        width=1,
    )
    draw.text(
        (1138 - pill_width + 18, 51),
        pill,
        font=pill_font,
        fill=(167, 205, 255),
    )

    card_xs = (65, 440, 815)
    for demo, device, card_x in zip(DEMOS, devices, card_xs):
        draw.rounded_rectangle(
            (card_x, 111, card_x + 320, 638),
            radius=30,
            fill=CARD,
            outline=CARD_LINE,
            width=2,
        )
        draw.text((card_x + 22, 129), demo.eyebrow, font=_font(14, index=1), fill=CYAN)
        draw.text((card_x + 22, 151), demo.title, font=_font(20, index=1), fill=WHITE)

        fitted = _fit_device(device, (244, 432))
        x = card_x + (320 - fitted.width) // 2
        y = 180 + (432 - fitted.height) // 2
        image.alpha_composite(fitted, (x, y))

    draw.line((65, 648, 1135, 648), fill=(24, 52, 91), width=1)
    draw.text(
        (65, 652),
        "jkincolisten.cloud/download/ios",
        font=_font(13, index=1),
        fill=BLUE,
    )
    draw.text(
        (1135 - draw.textlength("Tap once. Leave with the record.", font=_font(13)), 652),
        "Tap once. Leave with the record.",
        font=_font(13),
        fill=MUTED,
    )
    return image.convert("RGB")


def _load_series(frame_dir: Path) -> list[Image.Image]:
    paths = sorted(frame_dir.glob("frame-*.png"))
    if not paths:
        raise SystemExit(f"No captured frames in {frame_dir}")
    return [Image.open(path).convert("RGB") for path in paths]


def main() -> int:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to capture the public product tour.")
    if not (ROOT / "frontend" / "node_modules" / "playwright").exists():
        raise SystemExit("Run `npm install` in frontend/ before building the product tour.")

    with tempfile.TemporaryDirectory(prefix="jkinco-product-tour-") as tmp:
        capture_dir = Path(tmp)
        subprocess.run(
            [node, str(CAPTURE_SCRIPT), str(capture_dir)],
            cwd=ROOT,
            check=True,
        )

        series = {
            demo.kind: _load_series(capture_dir / demo.kind)
            for demo in DEMOS
        }
        shortest = min(len(frames) for frames in series.values())
        if shortest < 2:
            raise SystemExit("The product-tour capture returned too few frames.")

        for demo in DEMOS:
            frames = series[demo.kind][:shortest]
            _save_gif(
                frames,
                DOCS / f"production-{demo.kind}.gif",
                colors=96,
                poster=DOCS / f"production-{demo.kind}.png",
            )

        composite = [
            _composite_frame([series[demo.kind][index] for demo in DEMOS], index)
            for index in range(shortest)
        ]
        _save_gif(
            composite,
            DOCS / "production-download-tour.gif",
            colors=80,
            poster=DOCS / "production-download-tour.png",
        )

    for output in sorted(DOCS.glob("production-*.gif")) + sorted(DOCS.glob("production-*.png")):
        print(f"{output.relative_to(ROOT)}\t{output.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
