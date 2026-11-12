"""Провайдер modsgaming.ru — каталог модов BeamNG.drive.

Каталог строится по sitemap (разрешён в robots.txt), метаданные подтягиваются
по странице мода лениво, в фоне. Скачивание отдаётся браузеру: сайт закрыт
Cloudflare-челленджем, поэтому прямой запрос из скрипта получает 403.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit

from .. import db
from ..http import fetch
from ..util import clean_text, human_date, now_ts, parse_size_text, parse_slug_title

SOURCE = "modsgaming"
BASE = "https://modsgaming.ru"
SITEMAP = BASE + "/sitemap-load.xml"
DOWNLOAD_TEMPLATE = BASE + "/load/0-0-1-{ext_id}-20"

CATEGORY_MAP = {
    "mashiny": "cars",
    "karty": "maps",
    "mod_programmy": "tools",
    "samolety": "planes",
    "motocikly": "bikes",
    "korabli": "boats",
    "drugie_mody": "other",
    "drugie": "other",
}

_ITEM_RE = re.compile(
    r"<url>\s*<loc>(?P<loc>[^<]+)</loc>\s*(?:<lastmod>(?P<lastmod>[^<]+)</lastmod>)?", re.S
)


def _parse_lastmod(text: str | None) -> int:
    if not text:
        return 0
    try:
        return int(time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0


def sync(force: bool = False) -> int:
    """Обновляет список модов из sitemap. Возвращает число записей BeamNG."""
    last = db.get_meta("mg_synced")
    if not force and last and now_ts() - int(last) < 6 * 3600:
        return 0

    resp = fetch(SITEMAP, timeout=45)
    entries = _ITEM_RE.findall(resp.text)
    known = {row["id"] for row in db.query_mods(limit=100000)["items"]}

    added = 0
    for loc, lastmod in entries:
        if "/load/beamng_drive/" not in loc:
            continue
        parts = urlsplit(loc).path.strip("/").split("/")
        # ['load', 'beamng_drive', '<category>', '<slug>', '<section>-<cat>-<sub>-<id>']
        if len(parts) < 5:
            continue
        category = CATEGORY_MAP.get(parts[2], "other")
        slug = parts[3]
        ext_id = parts[4].split("-")[-1]
        mod_id = f"mg:{ext_id}"
        meta = parse_slug_title(slug)
        published = _parse_lastmod(lastmod)
        if mod_id in known:
            db.update_mod(mod_id, url=loc, published=published or 0)
            continue
        db.upsert_mod({
            "id": mod_id,
            "source": SOURCE,
            "url": loc,
            "ext_id": ext_id,
            "title": meta["title"],
            "category": category,
            "game_version": meta["game_version"],
            "version": meta["version"],
            "image": "",
            "published": published,
            "download_url": DOWNLOAD_TEMPLATE.format(ext_id=ext_id),
            "enriched_at": 0,
            "created_at": now_ts(),
        })
        added += 1

    db.set_meta("mg_synced", str(now_ts()))
    return added


def parse_page(html: str, mod: dict) -> dict:
    data: dict = {}

    title = ""
    raw_title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if raw_title:
        title = clean_text(raw_title.group(1))
        title = re.split(r"\s+-\s+", title)[0]
        title = re.sub(r"^\s*(мод|mod)\s+", "", title, flags=re.I)
        title = re.sub(r"\s*для\s+beamng\.?drive.*$", "", title, flags=re.I)
        title = re.sub(r"\s*для\s+beamng.*$", "", title, flags=re.I)
    if title:
        data["title"] = title.strip()[:180]

    image = ""
    og = re.search(r'property="og:image"\s+content="([^"]+)"', html, re.I)
    if og:
        image = og.group(1)
    if not image:
        shot = re.search(r'class="screendiv-1"[^>]*background:url\(([^)]+)\)', html)
        if shot:
            image = shot.group(1).strip("'\"")
    if not image:
        shot = re.search(r'(/_ld/\d+/\d+\.(?:jpg|jpeg|png|webp))', html, re.I)
        if shot:
            image = shot.group(1)
    if image:
        data["image"] = urljoin(BASE, image)

    size = re.search(r'class="filesize-span"[^>]*>([^<]+)<', html)
    if size:
        text = clean_text(size.group(1))
        data["size_text"] = text
        data["size_bytes"] = parse_size_text(text)

    author = re.search(r"Автор\s*мода\s*:\s*</b>\s*([^<]+)", html, re.I)
    if author:
        data["author"] = clean_text(author.group(1))[:80]

    body = re.search(r'<td class="eText"[^>]*>(.*?)</td>', html, re.S | re.I)
    if body:
        text = clean_text(body.group(1))
        text = re.sub(r"Автор\s*мода\s*:.*$", "", text, flags=re.I).strip()
        if text:
            data["description"] = text[:600]

    # Счётчик скачиваний самого мода стоит в его карточке, до блока «Скачать».
    cut = html.find("filesize-span")
    head = html[:cut] if cut > 0 else html[:20000]
    dl = re.findall(r"fa-download[^>]*>\s*</i>\s*([\d\s]+)", head)
    if dl:
        try:
            data["downloads"] = int(re.sub(r"\s+", "", dl[-1]))
        except ValueError:
            pass

    game_version = re.search(r"\((\d+\.\d+\.x)\)", data.get("title", ""))
    if game_version:
        data["game_version"] = game_version.group(1)

    version = re.search(r"\sv(\d+(?:\.\d+){0,3})\b", data.get("title", ""))
    if version:
        data["version"] = version.group(1)
    elif data.get("title"):
        # в настоящем названии версии нет — не тащим мусорную версию из слага
        data["version"] = ""

    data["enriched_at"] = now_ts()
    data["download_url"] = DOWNLOAD_TEMPLATE.format(ext_id=mod["ext_id"])
    return data


def enrich_one(mod: dict) -> dict | None:
    try:
        resp = fetch(mod["url"], referer=BASE + "/", timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[modsgaming] не удалось прочитать {mod['url']}: {exc}")
        return None
    if not resp.ok:
        return None
    return parse_page(resp.text, mod)


def resolve_download(mod: dict) -> str:
    if mod.get("download_url"):
        return mod["download_url"]
    return DOWNLOAD_TEMPLATE.format(ext_id=mod["ext_id"])


def sync_status() -> str:
    ts = db.get_meta("mg_synced")
    return human_date(int(ts)) if ts else ""
