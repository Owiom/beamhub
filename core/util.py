"""Вспомогательные функции: разбор слагов, чистка текста, форматирование."""
from __future__ import annotations

import re
import time
import unicodedata

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Частые бренды — чтобы «vaz_2106» превращалось в «ВАЗ 2106», а не «Vaz 2106».
BRANDS = {
    "vaz": "ВАЗ", "gaz": "ГАЗ", "uaz": "УАЗ", "zil": "ЗИЛ", "izh": "ИЖ",
    "kamaz": "КАМАЗ", "moskvich": "Москвич", "lada": "Lada", "gazelle": "Газель",
    "bmw": "BMW", "audi": "Audi", "vw": "VW", "volkswagen": "Volkswagen",
    "mercedes": "Mercedes", "mercedesbenz": "Mercedes-Benz", "benz": "Benz",
    "toyota": "Toyota", "lexus": "Lexus", "nissan": "Nissan", "honda": "Honda",
    "acura": "Acura", "mazda": "Mazda", "subaru": "Subaru", "mitsubishi": "Mitsubishi",
    "suzuki": "Suzuki", "daihatsu": "Daihatsu", "infiniti": "Infiniti",
    "ford": "Ford", "chevrolet": "Chevrolet", "chevy": "Chevy", "dodge": "Dodge",
    "chrysler": "Chrysler", "jeep": "Jeep", "cadillac": "Cadillac", "lincoln": "Lincoln",
    "gmc": "GMC", "tesla": "Tesla", "ram": "RAM", "hummer": "Hummer",
    "opel": "Opel", "renault": "Renault", "peugeot": "Peugeot", "citroen": "Citroën",
    "skoda": "Škoda", "seat": "SEAT", "volvo": "Volvo", "saab": "Saab",
    "porsche": "Porsche", "ferrari": "Ferrari", "lamborghini": "Lamborghini",
    "maserati": "Maserati", "bugatti": "Bugatti", "mclaren": "McLaren",
    "aston": "Aston", "jaguar": "Jaguar", "bentley": "Bentley", "rolls": "Rolls",
    "mini": "MINI", "fiat": "Fiat", "alfa": "Alfa", "romeo": "Romeo",
    "kia": "Kia", "hyundai": "Hyundai", "daewoo": "Daewoo", "samsung": "Samsung",
    "scania": "Scania", "man": "MAN", "iveco": "Iveco", "daf": "DAF",
    "kenworth": "Kenworth", "peterbilt": "Peterbilt", "freightliner": "Freightliner",
    "gavril": "Gavril", "ibishu": "Ibishu", "bruckell": "Bruckell", "etk": "ETK",
    "cherrier": "Cherrier", "autobello": "Autobello", "wenty": "Wentward",
    "solstice": "Solstice", "capsule": "Capsule", "lego": "LEGO", "gta": "GTA",
}

CATEGORY_LABELS = {
    "cars": "Машины",
    "maps": "Карты",
    "tools": "Инструменты",
    "planes": "Самолёты",
    "bikes": "Мотоциклы",
    "boats": "Корабли",
    "other": "Другое",
}

CATEGORY_ORDER = ["cars", "maps", "tools", "planes", "bikes", "boats", "other"]


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (
        text.replace("&quot;", '"').replace("&amp;", "&").replace("&nbsp;", " ")
        .replace("&laquo;", "«").replace("&raquo;", "»").replace("&#039;", "'")
        .replace("&mdash;", "—").replace("&ndash;", "–").replace("&lt;", "<").replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", text).strip()


def prettify_words(text: str) -> str:
    out = []
    for word in re.split(r"[\s_]+", text):
        if not word:
            continue
        low = word.lower()
        if low in BRANDS:
            out.append(BRANDS[low])
        elif re.fullmatch(r"[a-z]?\d+[\w\-]*", low):
            out.append(word.upper() if len(word) <= 4 and word.isalpha() else word)
        elif len(word) <= 3 and word.isalpha():
            out.append(word.upper())
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def parse_slug_title(slug: str) -> dict:
    """mod_bmw_x5_e53_v1_0_0_40_x_dlja_beamng_drive -> title/version/game_version."""
    s = slug.strip().lower()
    s = re.sub(r"^mod_", "", s)
    s = re.sub(r"_dlja_beamng_drive$", "", s)
    s = re.sub(r"_dlja_beamng$", "", s)

    game_version = ""
    gm = re.search(r"(\d+)_(\d+)_x", s)
    if gm:
        game_version = f"{gm.group(1)}.{gm.group(2)}.x"

    version = ""
    vm = re.search(r"(?:^|_)v(\d+(?:_\d+){0,4})(?:_|$)", s)
    if vm:
        version = vm.group(1).replace("_", ".")

    cut = len(s)
    for m in (gm, vm):
        if m and m.start() < cut:
            cut = m.start()
    head = s[:cut].strip("_")
    if not head:
        head = s
    title = prettify_words(head)
    title = re.sub(r"\s+", " ", title).strip(" -_")
    if len(title) > 90:
        title = title[:87].rstrip() + "…"
    return {"title": title, "version": version, "game_version": game_version}


def human_size(num: int | None) -> str:
    if not num:
        return ""
    units = ["Б", "КБ", "МБ", "ГБ"]
    size = float(num)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} Б"


def parse_size_text(text: str) -> int:
    if not text:
        return 0
    m = re.search(r"([\d.,]+)\s*(KB|MB|GB|КБ|МБ|ГБ|B|Б)", text, re.I)
    if not m:
        return 0
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    mult = {"b": 1, "б": 1, "kb": 1024, "кб": 1024, "mb": 1024 ** 2, "мб": 1024 ** 2,
            "gb": 1024 ** 3, "гб": 1024 ** 3}[unit]
    return int(value * mult)


def human_date(ts: int | None) -> str:
    if not ts:
        return ""
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def human_ago(ts: int | None) -> str:
    if not ts:
        return ""
    diff = int(time.time() - ts)
    if diff < 60:
        return "только что"
    if diff < 3600:
        return f"{diff // 60} мин назад"
    if diff < 86400:
        return f"{diff // 3600} ч назад"
    if diff < 86400 * 30:
        return f"{diff // 86400} дн назад"
    return human_date(ts)


def title_key(title: str) -> str:
    """Нормализованное имя мода: без версий, знаков и служебных слов.

    "Mercedes-Benz S-Class W221 v1.0 Переделка (0.40.x)" -> "mercedes benz s class w221 переделка"
    Нужно, чтобы один и тот же мод с разных сайтов считался дубликатом.
    """
    text = (title or "").lower().replace("ё", "е")
    text = re.sub(r"\(\s*0?\.\d{1,2}(\.x)?\s*\)", " ", text)      # (0.40.x)
    text = re.sub(r"\[\s*v?[\d.]+\s*\]", " ", text)               # [1.0]
    text = re.sub(r"\bv\s?\d+(?:\.\d+){0,3}\b", " ", text)        # v1.2.3
    text = re.sub(r"\b\d+\.\d+(?:\.\d+)*\b", " ", text)           # 1.2.3
    text = re.sub(r"\b(19|20)\d{2}\b", " ", text)                 # годы выпуска
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(мод|mod|карта|map|машина|car)\s+", "", text)
    text = re.sub(r"\s+(для beamng.*|for beamng.*)$", "", text)
    text = re.sub(r"_\d+$", "", text)          # хвост _2 от нашего же установщика
    text = re.sub(r"\s+\d$", "", text).strip()  # "... (2)" -> "..."
    return text[:120]


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "")
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    return name[:150] or "mod"


def now_ts() -> int:
    return int(time.time())


_game_state = {"checked": 0.0, "running": False}


def is_game_running() -> bool:
    """Запущена ли BeamNG.drive (моды читаются при старте игры — полезно предупредить)."""
    now = time.time()
    if now - _game_state["checked"] < 8:
        return bool(_game_state["running"])
    running = False
    try:
        import subprocess

        out = subprocess.run(
            ["tasklist", "/fi", "IMAGENAME eq BeamNG.drive.exe", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000,
            encoding="cp866", errors="ignore",
        ).stdout
        running = "beamng.drive.exe" in out.lower()
    except Exception:  # noqa: BLE001
        running = False
    _game_state.update({"checked": now, "running": running})
    return running
