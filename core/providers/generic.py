"""Универсальный провайдер: любая ссылка на страницу мода (worldofmods, beamng.com и т.п.)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..http import fetch
from ..util import clean_text, now_ts, parse_size_text

SOURCE = "link"
BASE = ""


def guess_category(url: str, title: str, html: str) -> str:
    blob = f"{url} {title}".lower()
    if re.search(r"karta|map|level|трасс|карт|city|route", blob):
        return "maps"
    if re.search(r"avto|car|vehicle|машин|auto|truck|bus|moto|bike", blob):
        return "cars"
    if re.search(r"\bui\b|app|interface|hud|интерфейс", blob):
        return "tools"
    if re.search(r"plane|aircraft|самолёт|самолет|heli", blob):
        return "planes"
    if re.search(r"boat|ship|корабл", blob):
        return "boats"
    return "other"


def parse_url(url: str) -> dict:
    resp = fetch(url, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Страница недоступна (HTTP {resp.status})")
    html = resp.text

    title = ""
    og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html, re.I)
    if og_title:
        title = clean_text(og_title.group(1))
    if not title:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        if h1:
            title = clean_text(h1.group(1))
    if not title:
        doc_title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = clean_text(doc_title.group(1)) if doc_title else ""
    title = re.sub(r"\s*[|—–-]\s*(BeamNG|World of Mods|playground\.ru|modsgaming).*$", "", title, flags=re.I)
    title = re.sub(r"^(скачать|download|мод)\s+", "", title, flags=re.I).strip()

    image = ""
    og_image = re.search(r'property="og:image"\s+content="([^"]*)"', html, re.I)
    if og_image:
        image = og_image.group(1)

    size_text = ""
    size_match = re.search(r"(\d[\d.,]*\s?(?:GB|MB|ГБ|МБ))", html)
    if size_match:
        size_text = size_match.group(1)

    description = ""
    desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.I)
    if desc_match:
        description = clean_text(desc_match.group(1))[:400]

    ext = re.sub(r"[^a-z0-9]+", "-", urlsplit(url).netloc + urlsplit(url).path.lower())[:90]
    return {
        "id": f"link:{abs(hash(url)) % (10 ** 12)}",
        "source": SOURCE,
        "url": url,
        "ext_id": "",
        "title": title or "Мод по ссылке",
        "category": guess_category(url, title, html),
        "size_text": size_text,
        "size_bytes": parse_size_text(size_text),
        "image": image,
        "description": description,
        "download_url": "",
        "enriched_at": now_ts(),
        "created_at": now_ts(),
    }


def enrich_one(mod: dict) -> dict | None:
    try:
        fresh = parse_url(mod["url"])
    except Exception as exc:  # noqa: BLE001
        print(f"[link] {exc}")
        return None
    fresh.pop("id", None)
    fresh.pop("source", None)
    fresh.pop("url", None)
    fresh.pop("created_at", None)
    return fresh


def resolve_download(mod: dict) -> str:
    return mod["url"]
