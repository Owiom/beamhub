"""Привязка уже установленных модов к каталогу.

Моды, поставленные до BeamHub (или вручную), лежат в папке игры без связи с каталогом:
нет mod_id, версии и, значит, проверки обновлений. Здесь мы их сопоставляем по
названию и размеру файла, а версию берём из каталога только при точном совпадении
размера — чтобы не показывать ложные «обновления».
"""
from __future__ import annotations

import re

from . import db
from .util import title_key

SIZE_TOLERANCE = 0.015


def _close_size(a: int, b: int) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= max(8192, int(a * SIZE_TOLERANCE))


def version_from_name(text: str) -> str:
    """'Карта ... v1.90 (0.39.x).zip' -> '1.90'."""
    match = re.search(r"\bv[.\s]?(\d+(?:[._]\d+){0,3})", (text or "").lower())
    if not match:
        return ""
    return match.group(1).replace("_", ".")


def _catalog_indexes() -> tuple[dict, list]:
    """title_key -> [моды] и список (размер, мод) для поиска по размеру."""
    mods = db.query_mods(limit=200000)["items"]
    by_key: dict[str, list[dict]] = {}
    by_size: list[tuple[int, dict]] = []
    for mod in mods:
        key = mod.get("title_key") or title_key(mod.get("title") or "")
        if key:
            by_key.setdefault(key, []).append(mod)
        if mod.get("size_bytes"):
            by_size.append((int(mod["size_bytes"]), mod))
    by_size.sort(key=lambda pair: pair[0])
    return by_key, by_size


def _size_candidates(size: int, by_size: list[tuple[int, dict]]) -> list[dict]:
    if not size:
        return []
    low, high = int(size * (1 - SIZE_TOLERANCE)), int(size * (1 + SIZE_TOLERANCE))
    return [mod for mod_size, mod in by_size if low <= mod_size <= high]


def match_row(row: dict, by_key: dict, by_size: list) -> tuple[dict | None, str, str]:
    """Вернуть (мод каталога, как сопоставили, версия установленного файла)."""
    text = f"{row.get('title') or ''} {row.get('filename') or ''}"
    key = title_key(row.get("title") or row.get("filename") or "")
    candidates = list(by_key.get(key, []))
    size = int(row.get("size") or 0)
    found_version = version_from_name(text)

    if candidates:
        same_size = [mod for mod in candidates if _close_size(size, int(mod.get("size_bytes") or 0))]
        pool = same_size or candidates

        if found_version:
            exact = [mod for mod in pool if (mod.get("version") or "") == found_version]
            if exact:
                return exact[0], "version", found_version
            newest = max(pool, key=lambda m: (m.get("published") or 0, m.get("downloads") or 0))
            return newest, "version-family", found_version

        if len(pool) == 1:
            only = pool[0]
            confidence = "size" if same_size else "title"
            return only, confidence, only.get("version") or ""

        newest = max(pool, key=lambda m: (m.get("published") or 0, m.get("downloads") or 0))
        return newest, "family", ""

    sized = _size_candidates(size, by_size)
    if len(sized) == 1:
        return sized[0], "size-only", ""
    return None, "", ""


def match_all() -> dict:
    """Сопоставить все несвязанные строки библиотеки. Возвращает статистику."""
    rows = [row for row in db.installed_list() if not row.get("mod_id")]
    if not rows:
        return {"checked": 0, "matched": 0, "details": []}

    by_key, by_size = _catalog_indexes()
    matched = 0
    details = []
    for row in rows:
        mod, how, version = match_row(row, by_key, by_size)
        if not mod:
            details.append({"file": row["filename"], "status": "не найден"})
            continue
        db.installed_add({
            **row,
            "mod_id": mod["id"],
            "version": version,
            "source_url": mod.get("url") or row.get("source_url") or "",
            "title": mod.get("title") or row.get("title") or "",
        })
        matched += 1
        details.append({
            "file": row["filename"],
            "status": how,
            "mod": mod.get("title"),
            "version": version or "?",
            "catalog_version": mod.get("version") or "?",
        })
    return {"checked": len(rows), "matched": matched, "details": details}
