"""Баннер репозитория (social preview) 1280x640 для GitHub."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1280, 640
BG = (13, 14, 17)
ACCENT = (255, 122, 26)
TEXT = (233, 235, 240)
MUTED = (141, 147, 161)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    path = Path(r"C:\Windows\Fonts") / name
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, image.size[0] - 1, image.size[1] - 1],
                                           radius=radius, fill=255)
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    return out


def main() -> None:
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    # мягкое свечение слева
    for i in range(220, 0, -4):
        alpha = int(26 * (1 - i / 220))
        draw.ellipse([-i, H // 2 - i, i, H // 2 + i], fill=(BG[0] + alpha, BG[1] + alpha // 3, BG[2]))

    draw.rounded_rectangle([64, 84, 136, 156], radius=20, fill=ACCENT)
    draw.text((88, 98), "B", font=font(52, True), fill=(20, 16, 12))

    draw.text((64, 178), "BeamHub", font=font(84, True), fill=TEXT)
    draw.text((66, 276), "лаунчер-каталог модов", font=font(30), fill=MUTED)
    draw.text((66, 314), "для BeamNG.drive", font=font(30), fill=MUTED)

    chips = ["2791 мод", "4 источника", "Windows"]
    x = 66
    for text in chips:
        width = draw.textlength(text, font=font(20, True)) + 30
        draw.rounded_rectangle([x, 372, x + width, 410], radius=19, outline=(60, 66, 80), width=2)
        draw.text((x + 15, 380), text, font=font(19, True), fill=TEXT)
        x += width + 10

    draw.text((66, 436), "каталог · установка в один клик", font=font(22), fill=(120, 126, 140))
    draw.text((66, 470), "библиотека · обновления · 4 сайта", font=font(22), fill=(120, 126, 140))
    draw.text((66, 540), "github.com/Owiom/beamhub", font=font(22, True), fill=ACCENT)

    shot_path = ROOT / "docs" / "catalog.png"
    if shot_path.exists():
        shot = Image.open(shot_path).convert("RGB")
        shot.thumbnail((600, 380))
        shot = rounded(shot, 16)
        pos = (W - shot.size[0] - 56, (H - shot.size[1]) // 2)
        canvas.paste(shot, pos)
        draw.rounded_rectangle([pos[0], pos[1], pos[0] + shot.size[0], pos[1] + shot.size[1]],
                               radius=16, outline=(52, 58, 70), width=2)

    out = ROOT / "docs" / "banner.png"
    canvas.save(out)
    print(f"готово: {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
