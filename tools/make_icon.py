"""Иконка BeamHub: оранжевый квадрат с силуэтом машины (понятно и на 16 px)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512
RADIUS = 116


def gradient(size: int) -> Image.Image:
    top = (255, 146, 64)
    bottom = (206, 74, 4)
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
    draw = ImageDraw.Draw(canvas)
    white = (255, 252, 246, 255)
    dark = (32, 18, 8, 255)

    # силуэт машины
    body = [(76, 336), (92, 288), (150, 272), (186, 224), (326, 224), (372, 272),
            (428, 288), (436, 336), (436, 362), (76, 362)]
    draw.polygon(body, fill=white)
    # окна
    draw.polygon([(206, 242), (316, 242), (344, 272), (178, 272)], fill=dark)
    # колёса
    for cx in (162, 356):
        draw.ellipse([cx - 54, 306, cx + 54, 414], fill=dark)
        draw.ellipse([cx - 22, 338, cx + 22, 382], fill=white)
    # фара
    draw.rounded_rectangle([92, 300, 126, 320], 8, fill=dark)

    png_path = ROOT / "beamhub-icon.png"
    canvas.save(png_path)
    canvas.save(ROOT / "beamhub.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"готово: {png_path.name}, beamhub.ico")


if __name__ == "__main__":
    main()
