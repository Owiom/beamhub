"""Playground.ru: каталог файлов BeamNG.drive + разбор страницы мода."""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urlsplit

from .. import db
from ..http import fetch
from ..util import BRANDS, clean_text, human_date, now_ts, parse_size_text, prettify_words

SOURCE = "playground"
BASE = "https://www.playground.ru"
SITEMAP = BASE + "/sitemap/file/list.xml"

# слова-бренды, по которым узнаём машину в названии
_BRAND_WORDS = {brand for brand in BRANDS if len(brand) >= 3}

CATEGORY_MAP = {
    "transport": "cars",
    "maps": "maps",
    "ui": "tools",
    "modpacks": "other",
    "gameplay": "other",
    "graphics": "other",
    "models": "other",
    "sound": "other",
    "rus": "other",
    "weapons": "other",
    "clothing": "other",
}

_ENTRY_RE = re.compile(
    r"<url>\s*<loc>(?P<loc>[^<]+)</loc>\s*(?:<lastmod>(?P<lastmod>[^<]+)</lastmod>)?", re.S
)


def _parse_lastmod(text: str | None) -> int:
    if not text:
        return 0
    try:
        return int(time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0


def _title_from_slug(slug: str) -> str:
    s = re.sub(r"^beamng_drive_", "", slug.lower())
    s = re.sub(r"_\d+_\d+_\d+$", "", s)
    words = [w for w in s.split("_") if w]
    return prettify_words(" ".join(words))[:120]


def _category_guess(slug: str) -> str:
    low = slug.lower()
    if re.search(r"karta|map|trassa|gorod|route|track", low):
        return "maps"
    if re.search(r"avto|car|mashin|truck|bus|moto|bike|vehicle|uaz|vaz|bmw|audi", low):
        return "cars"
    if re.search(r"prilozhenie|app|ui|interfejs|hud", low):
        return "tools"
    return "other"


def sync(force: bool = False) -> int:
    """Список файлов из sitemap playground.ru (шаг 50k URL, BeamNG — около 600)."""
    last = db.get_meta("pg_synced")
    if not force and last and now_ts() - int(last) < 6 * 3600:
        return 0

    resp = fetch(SITEMAP, timeout=120, retries=1)
    entries = _ENTRY_RE.findall(resp.text)
    known = {row["id"] for row in db.query_mods(limit=200000)["items"]}

    added = 0
    for loc, lastmod in entries:
        if "/beamng_drive/file/" not in loc:
            continue
        tail = urlsplit(loc).path.rstrip("/").split("-")[-1]
        if not tail.isdigit():
            continue
        mod_id = f"pg:{tail}"
        if mod_id in known:
            continue
        slug = urlsplit(loc).path.rstrip("/").split("/")[-1].rsplit("-", 1)[0]
        db.upsert_mod({
            "id": mod_id,
            "source": SOURCE,
            "url": loc,
            "ext_id": tail,
            "title": _title_from_slug(slug),
            "category": _category_guess(slug),
            "published": _parse_lastmod(lastmod),
            "download_url": loc,
            "enriched_at": 0,
            "created_at": now_ts(),
        })
        added += 1

    db.set_meta("pg_synced", str(now_ts()))
    return added


def sync_status() -> str:
    ts = db.get_meta("pg_synced")
    return human_date(int(ts)) if ts else ""


def _category_from_text(title: str, slug: str) -> str:
    """На странице файла нет его категории (в меню только общие ссылки), поэтому по тексту."""
    blob = f"{title} {slug}".lower()
    low_slug = slug.lower()
    for key, value in CATEGORY_MAP.items():
        if f"/{key}" in low_slug:
            return value
    if re.search(r"\b(karta|map|maps|trassa|gorod|city|track|route|level|nurburgring|location)\b", blob):
        return "maps"
    if re.search(r"\b(app|apps|ui|hud|interface|interfejs|script|lua|tool|manager|speedometer)\b", blob):
        return "tools"
    if re.search(r"\b(plane|aircraft|helicopter|samol[её]t|cessna|boeing)\b", blob):
        return "planes"
    if re.search(r"\b(boat|ship|korabl|yacht)\b", blob):
        return "boats"
    if re.search(r"\b(moto|bike|motorcycle|motocikl|quad|atv)\b", blob):
        return "bikes"
    if re.search(r"\b(car|cars|vehicle|truck|bus|sedan|suv|coupe|pickup|van|hatchback|wagon)\b", blob):
        return "cars"
    if re.search(r"\b(19|20)\d{2}\b", blob):
        return "cars"
    if any(re.search(rf"\b{re.escape(brand)}\b", blob) for brand in _BRAND_WORDS):
        return "cars"
    return "other"


def _clean_title(raw: str) -> tuple[str, str]:
    """'BeamNG.drive "BMW E39" [v1.2]' -> ('BMW E39', '1.2')."""
    text = clean_text(raw)
    version = ""
    match = re.search(r"\[([^\]]{1,24})\]\s*$", text)
    if match:
        version = re.sub(r"^[vV]\.?\s*", "", match.group(1)).strip()
        text = text[:match.start()].strip()
    text = re.sub(r"^BeamNG\.?\s*drive\s*", "", text, flags=re.I).strip()
    text = text.strip(' "\'«»')
    return text or raw.strip(), version


def parse_url(url: str) -> dict:
    """Возвращает запись мода по ссылке на страницу файла."""
    resp = fetch(url, referer=BASE + "/", timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Страница недоступна (HTTP {resp.status})")
    html = resp.text

    title = ""
    h1 = re.search(r'<h1[^>]*class="post-title"[^>]*>(.*?)</h1>', html, re.S | re.I)
    if h1:
        title = clean_text(h1.group(1))
    if not title:
        og = re.search(r'property="og:title"\s+content="([^"]+)"', html, re.I)
        title = clean_text(og.group(1)) if og else ""
        title = re.sub(r'^Скачать\s+BeamNG\.drive\s*', '', title).strip(' "«»')
    title = title.split(" - ")[0].strip()

    title, version = _clean_title(title)

    image = ""
    og_image = re.search(r'property="og:image"\s+content="([^"]+)"', html, re.I)
    if og_image:
        image = og_image.group(1)

    size_text = ""
    file_name = ""
    file_id = ""
    downloads = 0
    pg = re.search(r"<pg-file\b([^>]*)>", html, re.I)
    if pg:
        attrs = dict(re.findall(r'data-([a-z\-]+)="([^"]*)"', pg.group(1)))
        size_text = attrs.get("size", "")
        file_name = attrs.get("name", "")
        file_id = attrs.get("file-id", "")
        try:
            downloads = int(attrs.get("download-count") or 0)
        except ValueError:
            downloads = 0

    published = 0
    date_match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html) or \
        re.search(r'<time[^>]*datetime="([^"]+)"', html)
    if date_match:
        try:
            published = int(datetime.fromisoformat(date_match.group(1)).timestamp())
        except ValueError:
            published = 0

    mod_id = f"pg:{file_id or urlsplit(url).path.rstrip('/').split('-')[-1]}"
    return {
        "id": mod_id,
        "source": SOURCE,
        "url": url,
        "ext_id": file_id,
        "title": title or file_name or "Мод с playground.ru",
        "category": _category_from_text(title, urlsplit(url).path),
        "version": version,
        "size_text": size_text,
        "size_bytes": parse_size_text(size_text),
        "image": image,
        "description": clean_text((re.search(r'<meta\s+name="description"\s+content="([^"]*)"',
                                              html, re.I) or [None, ""])[1])[:400],
        "downloads": downloads,
        "published": published,
        "download_url": "",
        "enriched_at": now_ts(),
        "created_at": now_ts(),
    }


def enrich_one(mod: dict) -> dict | None:
    try:
        fresh = parse_url(mod["url"])
    except Exception as exc:  # noqa: BLE001
        print(f"[playground] {exc}")
        return None
    fresh.pop("id", None)
    fresh.pop("source", None)
    fresh.pop("url", None)
    fresh.pop("created_at", None)
    return fresh


def resolve_download(mod: dict) -> str:
    return mod["url"]
