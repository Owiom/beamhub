"""Провайдер beamngland.com — простой каталог с прямыми ссылками на modsfire.

Список модов берётся из sitemap (727 страниц mod.php?id=N), карточка мода
дотягивается лениво: название, картинка, категория, версия и ссылка на скачивание.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from .. import db
from ..http import fetch
from ..util import clean_text, human_date, now_ts

SOURCE = "beamngland"
BASE = "https://beamngland.com"
SITEMAP = BASE + "/sitemap.php"

CATEGORY_MAP = {1: "cars", 2: "maps", 3: "other"}

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


def sync(force: bool = False) -> int:
    last = db.get_meta("bl_synced")
    if not force and last and now_ts() - int(last) < 6 * 3600:
        return 0

    resp = fetch(SITEMAP, timeout=60)
    known = {row["id"] for row in db.query_mods(limit=200000)["items"]}
    added = 0
    for loc, lastmod in _ENTRY_RE.findall(resp.text):
        match = re.search(r"mod\.php\?id=(\d+)", loc)
        if not match:
            continue
        ext_id = match.group(1)
        mod_id = f"bl:{ext_id}"
        if mod_id in known:
            continue
        db.upsert_mod({
            "id": mod_id,
            "source": SOURCE,
            "url": f"{BASE}/mod.php?id={ext_id}",
            "ext_id": ext_id,
            "title": f"BeamNG Land #{ext_id}",
            "category": "other",
            "published": _parse_lastmod(lastmod),
            "download_url": f"{BASE}/mod.php?id={ext_id}",
            "enriched_at": 0,
            "created_at": now_ts(),
        })
        added += 1
    db.set_meta("bl_synced", str(now_ts()))
    return added


def parse_page(html: str, mod: dict) -> dict:
    data: dict = {}
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if match:
        title = clean_text(match.group(1))
        title = re.sub(r"\s*-\s*BeamNG Land\s*$", "", title, flags=re.I)
    if not title:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if h1:
            title = clean_text(h1.group(1))
    if title:
        data["title"] = title[:180]

    version = re.search(r"\sv(\d+(?:\.\d+){0,3})\b", data.get("title", ""))
    if version:
        data["version"] = version.group(1)
    game_version = re.search(r"\((\d+\.\d+\.x)\)", data.get("title", ""))
    if game_version:
        data["game_version"] = game_version.group(1)

    image = ""
    upload = re.search(r'/uploads/([a-f0-9]{16,}\.(?:jpg|jpeg|png|webp))', html, re.I)
    if upload:
        image = f"{BASE}/uploads/{upload.group(1)}"
    if image:
        data["image"] = image

    category = "other"
    cat_match = re.search(r"category\.php\?id=(\d+)", html)
    if cat_match:
        category = CATEGORY_MAP.get(int(cat_match.group(1)), "other")
    data["category"] = category

    download = ""
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        label = clean_text(text).lower()
        if "download" in label or "скачать" in label:
            download = urljoin(BASE, href)
            break
    if download:
        data["download_url"] = download

    description = ""
    desc = re.search(r'class="mod-description"[^>]*>(.*?)</div>', html, re.S | re.I)
    if desc:
        description = clean_text(desc.group(1))[:600]
    if description:
        data["description"] = description

    date = re.search(r"Added:\s*(\d{1,2})/(\d{1,2})/(\d{4})", html, re.I)
    if date:
        try:
            data["published"] = int(time.mktime(time.strptime(
                f"{date.group(3)}-{date.group(2)}-{date.group(1)}", "%Y-%m-%d")))
        except ValueError:
            pass

    data["enriched_at"] = now_ts()
    return data


def enrich_one(mod: dict) -> dict | None:
    try:
        resp = fetch(mod["url"], referer=BASE + "/", timeout=25)
    except Exception as exc:  # noqa: BLE001
        print(f"[beamngland] {exc}")
        return None
    if not resp.ok:
        return None
    return parse_page(resp.text, mod)


def resolve_download(mod: dict) -> str:
    return mod.get("download_url") or mod["url"]


def sync_status() -> str:
    ts = db.get_meta("bl_synced")
    return human_date(int(ts)) if ts else ""
