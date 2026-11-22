"""Динамичный вертикальный ролик BeamHub: быстрые склейки, кинетический текст, бит."""
from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "ad-shots-107"
OUT = ROOT / "docs" / "promo"
FRAMES = OUT / "frames3"
OUT.mkdir(parents=True, exist_ok=True)
FRAMES.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
FPS = 30
DURATION = 12.6
TOTAL = int(FPS * DURATION)

ACCENT = (255, 122, 26)
WHITE = (245, 246, 250)
MUTED = (150, 158, 176)
DARK = (13, 15, 20)
FONT_DIR = ROOT / "assets" / "fonts"


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    name = "Inter-Bold.ttf" if bold else "Inter-Regular.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


F_HUGE = font(150)
F_BIG = font(92)
F_MID = font(64)
F_SMALL = font(38)
F_TINY = font(28)


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def pop(x: float, overshoot: float = 1.12) -> float:
    """Пружинка: 0 → чуть больше 1 → 1."""
    x = max(0.0, min(1.0, x))
    if x < 0.7:
        return overshoot * (x / 0.7)
    return overshoot - (overshoot - 1) * ((x - 0.7) / 0.3)


def bg(t: float) -> Image.Image:
    image = Image.new("RGB", (W, H), (9, 10, 14))
    d = ImageDraw.Draw(image)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(8 + 6 * k), int(9 + 6 * k), int(13 + 8 * k)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    pulse = 90 + 25 * math.sin(t * 7)
    g.ellipse([-260, -420 + int(20 * math.sin(t * 3)), W + 260, 520], fill=(int(pulse), int(pulse * 0.45), 8))
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    return Image.blend(image, glow, 0.6)


def shot_card(image: Image.Image, box: tuple[int, int, int, int], radius: int = 26) -> Image.Image:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pic = image.resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w + 80, h + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([40, 40, w + 40, h + 40], radius, fill=(0, 0, 0, 190))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    canvas.alpha_composite(shadow, (x0 - 40, y0 - 36))
    canvas.paste(pic, (x0, y0), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, outline=(60, 66, 82), width=3)
    return canvas


SHOTS_LIST = [
    # (start, image, crop, title, accent)
    (0.00, None, None, "BEAMNG", ""),
    (0.80, None, None, "МОДЫ", "БЕЗ ТОРРЕНТОВ"),
    (1.70, "catalog", (140, 240, 1300, 1180), "2 276 МОДОВ", ""),
    (2.55, "catalog", (620, 260, 1780, 1200), "ФИЛЬТРЫ", "ПО ЦЕНЕ НЕРВОВ"),
    (3.40, "catalog", (300, 620, 1500, 1360), "КАТЕГОРИИ", "ВСЁ В ОДНОМ МЕСТЕ"),
    (4.25, "catalog", (1080, 800, 1800, 1300), "СТАВИТ САМ", "ИЗ «ЗАГРУЗОК»"),
    (5.10, "catalog", (980, 760, 1760, 1330), "В ОДИН КЛИК", ""),
    (5.95, "collections", (300, 150, 1660, 1010), "ГОТОВЫЕ СБОРКИ", ""),
    (6.80, "collections", (300, 400, 1660, 980), "ДРИФТ · ОФФРОУД", "JDM · ДРАГ"),
    (7.65, "favorites", (180, 60, 900, 420), "ИЗБРАННОЕ", "ПОД РУКОЙ"),
    (8.50, "inbox", (180, 60, 1100, 500), "ИЗ ПАПКИ ЗАГРУЗОК", ""),
    (9.35, "catalog", (700, 700, 1750, 1330), "БЕЗ ТОРРЕНТОВ", "БЕЗ РЕГИСТРАЦИИ"),
    (10.20, "collections", (300, 150, 1660, 1010), "СБОРКИ СООБЩЕСТВА", ""),
]

CTA_START = 11.05


def caption(d: ImageDraw.ImageDraw, title: str, accent: str, t: float,
            duration: float) -> None:
    p = ease(t / min(0.32, duration / 2))
    scale = pop(t / 0.3)
    size = 92
    f = font(max(20, int(size * scale)))
    tw = d.textlength(title, font=f)
    if tw > W * 0.9:
        size = 62
        f = font(max(18, int(size * scale)))
        tw = d.textlength(title, font=f)
    y = 1390 + int(46 * (1 - p))
    d.text(((W - tw) / 2, y), title, font=f, fill=WHITE)
    if accent:
        fa = font(max(16, int(38 * scale)))
        aw = d.textlength(accent, font=fa)
        ay = y + int(112 * scale)
        d.text(((W - aw) / 2, ay), accent, font=fa, fill=MUTED)
        d.rounded_rectangle([(W - 140) / 2, ay + 56, (W + 140) / 2, ay + 64], 4,
                            fill=ACCENT)


def load(name: str) -> Image.Image:
    return Image.open(SHOTS / f"{name}.png").convert("RGB")


CACHE = {}


def get(name: str) -> Image.Image:
    if name not in CACHE:
        CACHE[name] = load(name)
    return CACHE[name]


def frame_for(shot_index: int, local: float, duration: float) -> Image.Image:
    start, name, crop, title, accent = SHOTS_LIST[shot_index]
    image = bg(local + shot_index).convert("RGBA")

    if name is None:
        d = ImageDraw.Draw(image)
        if shot_index == 0:
            f = font(120)
            tw = d.textlength("BEAMNG", font=f)
            d.text(((W - tw) / 2, 760), "BEAMNG", font=f, fill=MUTED)
            f2 = F_HUGE
            tw2 = d.textlength("DRIVE", font=f2)
            d.text(((W - tw2) / 2, 880), "DRIVE", font=f2, fill=WHITE)
            d.rounded_rectangle([(W - 180) / 2, 1090, (W + 180) / 2, 1102], 6, fill=ACCENT)
        else:
            p2 = pop(local / 0.3)
            f = font(int(150 * p2))
            tw = d.textlength("МОДЫ", font=f)
            d.text(((W - tw) / 2, 720), "МОДЫ", font=f, fill=WHITE)
            sub = "БЕЗ ТОРРЕНТОВ"
            sf = F_MID
            sw = d.textlength(sub, font=sf)
            d.text(((W - sw) / 2, 940), sub, font=sf, fill=ACCENT)
            d.rounded_rectangle([(W - 160) / 2, 1050, (W + 160) / 2, 1060], 5,
                                fill=ACCENT)
        return image.convert("RGB")

    src = get(name)
    x0, y0, x1, y1 = crop
    w, h = x1 - x0, y1 - y0
    card_w = 1000
    card_h = int(card_w * h / w)
    if card_h > 900:
        card_h = 900
        card_w = int(card_h * w / h)

    # пунч-зум и лёгкий поворот на каждой склейке
    zoom = 1.0 + 0.06 * math.sin(math.pi * min(1.0, local / duration))
    card_w = int(card_w * zoom)
    card_h = int(card_h * zoom)
    angle = 1.4 if shot_index % 2 else -1.4
    angle *= (1 - min(1.0, local / 0.25))
    card = shot_card(src.crop((x0, y0, x1, y1)), (0, 0, card_w, card_h))
    if angle:
        card = card.rotate(angle, resample=Image.BICUBIC, expand=False)
    top = 300 - int(24 * ease(local / duration))
    left = (W - card.width) // 2
    image.alpha_composite(card, (left, top))

    d = ImageDraw.Draw(image)
    caption(d, title, accent, local, duration)

    # белая вспышка на склейке
    if local < 0.07:
        flash = Image.new("RGB", (W, H), (255, 248, 240))
        image = Image.blend(image.convert("RGB"), flash, 0.55 * (1 - local / 0.07))
        return image
    return image.convert("RGB")


def cta(local: float) -> Image.Image:
    image = bg(local + 20).convert("RGBA")
    d = ImageDraw.Draw(image)
    p = pop(local / 0.4)
    f = font(int(160 * p))
    tw = d.textlength("BEAMHUB", font=f)
    d.text(((W - tw) / 2, 640), "BEAMHUB", font=f, fill=WHITE)
    d.text(((W - d.textlength("моды BeamNG.drive", font=F_MID)) / 2, 860),
           "моды BeamNG.drive", font=F_MID, fill=MUTED)

    label = "github.com/Owiom/beamhub"
    lf = font(44)
    tw2 = d.textlength(label, font=lf)
    box_w = int(tw2 + 110)
    box = [(W - box_w) // 2, 1010, (W + box_w) // 2, 1112]
    d.rounded_rectangle(box, 24, fill=ACCENT)
    d.text(((W - tw2) / 2, 1036), label, font=lf, fill=DARK)

    d.text(((W - d.textlength("скачай и играй", font=F_SMALL)) / 2, 1190),
           "скачай и играй", font=F_SMALL, fill=MUTED)
    return image.convert("RGB")


def render(t: float) -> Image.Image:
    if t >= CTA_START:
        return cta(t - CTA_START)
    idx = 0
    for i, (start, *_rest) in enumerate(SHOTS_LIST):
        if t >= start:
            idx = i
    start = SHOTS_LIST[idx][0]
    end = SHOTS_LIST[idx + 1][0] if idx + 1 < len(SHOTS_LIST) else CTA_START
    return frame_for(idx, t - start, max(0.2, end - start))


def make_audio(path: Path) -> None:
    sr = 44100
    n = int(DURATION * sr)
    mix = np.zeros(n, dtype=np.float32)
    bpm = 128
    beat = 60 / bpm
    total_beats = int(DURATION / beat)

    def add(sig: np.ndarray, at: float, gain: float) -> None:
        start = int(at * sr)
        end = min(n, start + len(sig))
        if end > start:
            mix[start:end] += sig[: end - start] * gain

    t = np.arange(int(0.18 * sr)) / sr
    kick = np.sin(2 * np.pi * (52 + 60 * np.exp(-t * 35)) * t) * np.exp(-t * 22)
    add(kick.astype(np.float32), 0.0, 0.0)
    noise = np.random.default_rng(7).normal(0, 1, int(0.09 * sr)).astype(np.float32)
    hat = noise * np.exp(-np.arange(len(noise)) / sr * 90)

    for b in range(total_beats):
        at = b * beat
        add(kick, at, 0.9)
        if b % 4 in (1, 3):
            add(hat, at, 0.22)
        add(hat, at + beat / 2, 0.12)
        # бас
        bass_t = np.arange(int(beat * sr)) / sr
        note = 55.0 if b % 8 < 4 else 49.0
        bass = (np.sin(2 * np.pi * note * bass_t) * 0.5
                + np.sin(2 * np.pi * note * 2 * bass_t) * 0.15)
        bass *= np.exp(-bass_t * 6)
        add(bass.astype(np.float32), at, 0.55)

    peak = float(np.max(np.abs(mix))) or 1.0
    mix = (mix / peak * 0.85 * 32767).astype(np.int16)
    stereo = np.column_stack([mix, mix])
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(stereo.tobytes())


def main() -> None:
    print("кадров:", TOTAL)
    for i in range(TOTAL):
        render(i / FPS).save(FRAMES / f"{i:04d}.png")
        if i % 60 == 0:
            print("  ", i)
    audio = OUT / "beat.wav"
    make_audio(audio)
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    video = OUT / "beamhub-tiktok-v2.mp4"
    subprocess.run([
        ffmpeg, "-y", "-framerate", str(FPS), "-i", str(FRAMES / "%04d.png"),
        "-i", str(audio), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-movflags", "+faststart", str(video),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("видео:", video, round(video.stat().st_size / 1024 / 1024, 2), "МБ")
    render(1.9).save(OUT / "cover-v2.jpg", quality=92)


if __name__ == "__main__":
    main()
