"""Рекламный вертикальный ролик BeamHub 1080×1920 (v2: чище фон, аккуратный CTA)."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "ad-shots"
OUT = ROOT / "docs" / "promo"
FRAMES = OUT / "frames"
OUT.mkdir(parents=True, exist_ok=True)
FRAMES.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
FPS = 30
DURATION = 17.0
TOTAL = int(FPS * DURATION)

ACCENT = (255, 122, 26)
TEXT = (240, 242, 247)
MUTED = (158, 166, 184)
DIM = (120, 128, 146)
DARK = (17, 19, 24)

FONT_DIR = ROOT / "assets" / "fonts"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Inter-Bold.ttf" if bold else "Inter-Regular.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


F_LOGO = font(152, True)
F_TITLE = font(96, True)
F_H1 = font(74, True)
F_H2 = font(48, True)
F_BODY = font(40)
F_SMALL = font(30)
F_TINY = font(24)


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def background(t: float = 0.0) -> Image.Image:
    """Тёмный градиент + мягкое оранжевое свечение сверху, без видимых краёв."""
    base = Image.new("RGB", (W, H), (10, 11, 16))
    d = ImageDraw.Draw(base)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(9 + 7 * k), int(10 + 7 * k), int(15 + 9 * k)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-300, -520, W + 300, 620], fill=(70, 34, 10))
    glow = glow.filter(ImageFilter.GaussianBlur(180))
    frame = Image.blend(base, glow, 0.55)
    # лёгкая виньетка
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    sd.ellipse([-W * 0.35, -H * 0.2, W * 1.35, H * 1.15], fill=140)
    shade = shade.filter(ImageFilter.GaussianBlur(220))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(frame, dark, shade)


def paste_round(canvas: Image.Image, img: Image.Image, box: tuple[int, int, int, int],
                radius: int = 30) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    pic = img.resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    shadow = Image.new("RGBA", (w + 90, h + 90), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([45, 45, w + 45, h + 45], radius,
                                             fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    canvas.paste(shadow, (x0 - 45, y0 - 40), shadow)
    canvas.paste(pic, (x0, y0), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, outline=(52, 57, 70), width=2)


def centered(d: ImageDraw.ImageDraw, y: int, text: str, fnt, fill=TEXT) -> None:
    width = d.textlength(text, font=fnt)
    d.text(((W - width) / 2, y), text, font=fnt, fill=fill)


def story_bars(d: ImageDraw.ImageDraw, index: int, progress: float) -> None:
    gap, margin, height = 8, 40, 6
    seg = (W - margin * 2 - gap * 3) / 4
    for i in range(4):
        x = margin + i * (seg + gap)
        d.rounded_rectangle([x, 44, x + seg, 44 + height], 3, fill=(58, 62, 74))
        if i < index:
            d.rounded_rectangle([x, 44, x + seg, 44 + height], 3, fill=ACCENT)
        elif i == index:
            d.rounded_rectangle([x, 44, x + seg * max(0.05, progress), 44 + height],
                                3, fill=ACCENT)


def caption(d: ImageDraw.ImageDraw, title: str, sub: str, progress: float) -> None:
    rise = int(24 * (1 - ease(progress)))
    centered(d, 1270 + rise, title, F_H1)
    centered(d, 1372 + rise, sub, F_BODY, MUTED)
    bar_w = int(120 * ease(progress))
    d.rounded_rectangle([(W - bar_w) / 2, 1470, (W + bar_w) / 2, 1478], 4, fill=ACCENT)


def scene_intro(t: float) -> Image.Image:
    image = background(t)
    d = ImageDraw.Draw(image)
    p = ease(t / 0.9)
    size = int(150 * (0.72 + 0.28 * p))
    cx, cy = W // 2, 720
    d.rounded_rectangle([cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2],
                        int(size * 0.24), fill=ACCENT)
    d.text((cx - size * 0.15, cy - size * 0.32), "B", font=font(int(size * 0.55), True),
           fill=DARK)
    centered(d, 940 - int(26 * (1 - p)), "BEAMHUB", F_TITLE)
    centered(d, 1062, "каталог модов BeamNG.drive", F_BODY, MUTED)
    centered(d, 1140, "1.0.6  ·  RU / EN  ·  4K", F_SMALL, DIM)
    return image


def scene_shot(index: int, image_path: Path, t_local: float, length: float,
               title: str, sub: str, crop: tuple[int, int, int, int]) -> Image.Image:
    image = background(t_local + index)
    d = ImageDraw.Draw(image)
    progress = min(1.0, t_local / length)
    story_bars(d, index, progress)
    shot = Image.open(image_path).convert("RGB").crop(crop)
    zoom = 1.0 + 0.04 * ease(progress)
    card_w, card_h = 1000, int(1000 * shot.height / shot.width)
    if card_h > 930:
        card_h = 930
        card_w = int(card_h * shot.width / shot.height)
    card_w, card_h = int(card_w * zoom), int(card_h * zoom)
    left = (W - card_w) // 2
    top = 236 - int(22 * ease(progress))
    paste_round(image, shot, (left, top, left + card_w, top + card_h), 30)
    if index == 2:
        fade_w = 80
        grad = Image.new("L", (fade_w, 1), 0)
        for x in range(fade_w):
            grad.putpixel((x, 0), int(255 * (1 - x / fade_w)))
        grad = grad.resize((fade_w, card_h))
        strip = Image.new("RGB", (fade_w, card_h), (13, 15, 20))
        image.paste(strip, (left, top), grad)
    caption(d, title, sub, progress)
    centered(d, 1780, "BeamHub 1.0.6  ·  Windows", F_TINY, (86, 92, 106))
    return image


def scene_cta(t: float) -> Image.Image:
    image = background(t + 10)
    d = ImageDraw.Draw(image)
    p = ease(t / 0.8)
    story_bars(d, 4, 1.0)
    centered(d, 520 - int(18 * (1 - p)), "Скачай", F_H2, MUTED)
    centered(d, 590, "BEAMHUB", F_TITLE)
    centered(d, 730, "каталог, сборки, установка в один клик", F_BODY, MUTED)

    url = "github.com/Owiom/beamhub"
    label_font = font(46, True)
    text_w = d.textlength(url, font=label_font)
    box_w = int(text_w + 120)
    box = [(W - box_w) // 2, 850, (W + box_w) // 2, 960]
    d.rounded_rectangle(box, 26, fill=ACCENT)
    d.text(((W - text_w) / 2, 878), url, font=label_font, fill=DARK)

    centered(d, 1050, "4K  ·  RU / EN  ·  без торрентов", F_SMALL, DIM)
    centered(d, 1110, "Windows 10 / 11  ·  бесплатно", F_SMALL, (96, 102, 116))
    return image


def render_frame(t: float) -> Image.Image:
    scenes = [
        (0.0, 2.4, lambda lt: scene_intro(lt)),
        (2.4, 6.4, lambda lt: scene_shot(1, SHOTS / "catalog.png", lt, 4.0,
                                          "2 274 мода", "фильтры · категории · обновления",
                                          (0, 0, 1400, 1369))),
        (6.4, 10.0, lambda lt: scene_shot(2, SHOTS / "collections.png", lt, 3.6,
                                           "Готовые сборки", "дрифт · оффроуд · JDM · драг",
                                           (340, 150, 1660, 1010))),
        (10.0, 13.4, lambda lt: scene_shot(3, SHOTS / "catalog.png", lt, 3.4,
                                            "Установка в один клик",
                                            "мод встаёт сам из «Загрузок»",
                                            (1100, 860, 1760, 1270))),
        (13.4, DURATION, lambda lt: scene_cta(lt)),
    ]
    current = scenes[-1]
    for start, end, builder in scenes:
        if t < end:
            current = (start, end, builder)
            break
    start, end, builder = current
    frame = builder(t - start)
    fade = 0.35
    if end - t < fade and end < DURATION:
        nxt = next(((s, e, b) for s, e, b in scenes if s == end), None)
        if nxt:
            frame = Image.blend(frame, nxt[2](0.0), (1 - (end - t) / fade) * 0.85)
    return frame


def main() -> None:
    print("кадров:", TOTAL)
    for i in range(TOTAL):
        render_frame(i / FPS).save(FRAMES / f"{i:04d}.png")
        if i % 90 == 0:
            print("  ", i)
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    video = OUT / "beamhub-tiktok.mp4"
    subprocess.run([
        ffmpeg, "-y", "-framerate", str(FPS), "-i", str(FRAMES / "%04d.png"),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("видео:", video, round(video.stat().st_size / 1024 / 1024, 1), "МБ")
    render_frame(0.6).save(OUT / "cover.jpg", quality=92)


if __name__ == "__main__":
    main()
