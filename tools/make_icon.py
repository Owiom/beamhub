"""Рисует иконку BeamHub (beamhub.ico) без внешних ассетов."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512
RADIUS = 116
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\tahomabd.ttf",
]


def gradient(size: int) -> Image.Image:
    top = (255, 138, 51)
    bottom = (198, 66, 0)
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return image


def main() -> None:
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)
    canvas = Image.composite(gradient(SIZE), Image.new("RGB", (SIZE, SIZE), (0, 0, 0)), mask)
    canvas = canvas.convert("RGBA")

    font = None
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            font = ImageFont.truetype(path, 340)
            break
    draw = ImageDraw.Draw(canvas)
    if font:
        box = draw.textbbox((0, 0), "B", font=font)
        x = (SIZE - (box[2] - box[0])) / 2 - box[0]
        y = (SIZE - (box[3] - box[1])) / 2 - box[1] - 12
        draw.text((x, y), "B", font=font, fill=(20, 16, 12, 255))
    else:  # на всякий случай без шрифта
        draw.rectangle([176, 150, 336, 362], fill=(20, 16, 12, 255))

    png_path = ROOT / "beamhub-icon.png"
    canvas.save(png_path)
    canvas.save(ROOT / "beamhub.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"готово: {png_path.name}, beamhub.ico")


if __name__ == "__main__":
    main()
