"""Провайдер beamngmods.net — каталог по категориям, названия и обложки из списка.

Страницы категорий отдают карточки с названием, версией и картинкой, поэтому
каталог наполняется сразу. Ссылка на скачивание (modsfire) — со страницы мода.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import db
from ..http import fetch
from ..util import clean_text, human_date, now_ts

SOURCE = "beamngmods"
BASE = "https://beamngmods.net"

# путь категории -> наша категория
CATEGORIES = [
    ("cars", "cars"), ("trucks", "cars"), ("suvs", "cars"), ("buses", "cars"),
    ("racing", "cars"), ("tractors", "cars"), ("trailers", "cars"),
    ("other_vehicles", "cars"), ("map", "maps"), ("objects", "other"),
    ("textures", "other"), ("attachments", "other"), ("other", "other"),
]
MAX_PAGES = 12  # на категорию; глубже — уже древние моды

_CARD_RE = re.compile(
    r'<a href="(?P<url>/(?P<cat>[a-z_]+)/(?P<id>[A-Za-z0-9]+)/)" class="product-item">'
    r"(?P<body>.*?)</a>", re.S)
_IMG_RE = re.compile(r'<img[^>]+src="(?P<src>[^"]+)"[^>]*alt="(?P<alt>[^"]*)"', re.S)
_NAME_RE = re.compile(r'<h2>(?P<name>.*?)</h2>', re.S)
_VER_RE = re.compile(r'class="mod-ver"[^>]*>\s*(?P<ver>v?[\d.]+)', re.S)


def sync(force: bool = False) -> int:
    last = db.get_meta("bm_synced")
    if not force and last and now_ts() - int(last) < 6 * 3600:
        return 0

    known = {row["id"] for row in db.query_mods(limit=200000)["items"]}
    added = 0
    for path, category in CATEGORIES:
        for page in range(1, MAX_PAGES + 1):
            url = f"{BASE}/{path}/" if page == 1 else f"{BASE}/{path}/page{page}/"
            try:
                resp = fetch(url, referer=BASE + "/", timeout=25, allow_404=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[beamngmods] {url}: {exc}")
                break
            if not resp.ok:
                break
            cards = list(_CARD_RE.finditer(resp.text))
            if not cards:
                break
            for card in cards:
                ext_id = card.group("id")
                mod_id = f"bm:{ext_id}"
                if mod_id in known:
                    continue
                body = card.group("body")
                name_match = _NAME_RE.search(body)
                title = clean_text(name_match.group("name")) if name_match else ext_id
                version = ""
                ver_match = _VER_RE.search(body)
                if ver_match:
                    version = ver_match.group("ver").lstrip("vV").strip()
                    title = re.sub(r"\s*v?[\d.]+\s*$", "", title).strip()
                image = ""
                img_match = _IMG_RE.search(body)
                if img_match:
                    image = urljoin(BASE, img_match.group("src"))
                    if not name_match and img_match.group("alt"):
                        title = clean_text(img_match.group("alt"))
                db.upsert_mod({
                    "id": mod_id,
                    "source": SOURCE,
                    "url": urljoin(BASE, card.group("url")),
                    "ext_id": ext_id,
                    "title": title[:180] or ext_id,
                    "category": category,
                    "version": version,
                    "image": image,
                    "download_url": "",
                    "published": 0,
                    "enriched_at": 0,
                    "created_at": now_ts(),
                })
                added += 1
    db.set_meta("bm_synced", str(now_ts()))
    return added


def parse_page(html: str, mod: dict) -> dict:
    data: dict = {}
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if match:
        title = clean_text(match.group(1))
        title = re.sub(r"^\s*Download\s+", "", title, flags=re.I)
        title = re.sub(r"\s*[-–|]\s*BeamNGmods.*$", "", title, flags=re.I)
    if title:
        data["title"] = title[:180]

    version = re.search(r"\bv(\d+(?:\.\d+){0,3})\b", html)
    if version:
        data["version"] = version.group(1)

    image = ""
    og = re.search(r'property="og:image"\s+content="([^"]+)"', html, re.I)
    if og:
        image = og.group(1)
    if not image:
        material = re.search(r'/files/materials/\d+/img\d+\.(?:jpg|jpeg|png|webp)', html, re.I)
        if material:
            image = urljoin(BASE, material.group(0))
    if image:
        data["image"] = urljoin(BASE, image)

    download = ""
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(r"modsfire|mega\.|mediafire|drive\.google|\.zip($|\?)", href, re.I):
            download = href
            break
    if download:
        data["download_url"] = download

    desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.I)
    if desc:
        data["description"] = clean_text(desc.group(1))[:600]

    data["enriched_at"] = now_ts()
    return data


def enrich_one(mod: dict) -> dict | None:
    try:
        resp = fetch(mod["url"], referer=BASE + "/", timeout=25)
    except Exception as exc:  # noqa: BLE001
        print(f"[beamngmods] {exc}")
        return None
    if not resp.ok:
        return None
    return parse_page(resp.text, mod)


def resolve_download(mod: dict) -> str:
    return mod.get("download_url") or mod["url"]


def sync_status() -> str:
    ts = db.get_meta("bm_synced")
    return human_date(int(ts)) if ts else ""
