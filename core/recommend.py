"""Рекомендации: похожие моды, популярное, новинки, рекомендуемое.

Без платных API и «нейросетей»: считаем по категории, тегам названия, автору,
оценкам и фактическим установкам. Дешёвые запросы к SQLite, кеша не требуют.
"""
from __future__ import annotations

import re

from . import db
from .util import title_key

STOP_WORDS = {"the", "and", "for", "with", "mod", "mods", "v", "beta", "update", "fix",
              "карта", "мод", "машина", "авто"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Zа-яА-Я0-9]{3,}", (text or "").lower())
    return {word for word in words if word not in STOP_WORDS}


def similarity(a: dict, b: dict) -> float:
    """0..1: общие слова названия, категория, автор, теги."""
    score = 0.0
    if a.get("category") and a.get("category") == b.get("category"):
        score += 0.35
    ta, tb = _tokens(a.get("title")), _tokens(b.get("title"))
    if ta and tb:
        overlap = len(ta & tb) / len(ta | tb)
        score += 0.45 * overlap
    if a.get("author") and a.get("author") == b.get("author"):
        score += 0.15
    if a.get("game_version") and a.get("game_version") == b.get("game_version"):
        score += 0.05
    return round(min(1.0, score), 3)


def similar_mods(mod_id: str, limit: int = 8) -> dict:
    mod = db.get_mod(mod_id)
    if not mod:
        return {"ok": False, "error": "Мод не найден."}
    keys = title_key(mod.get("title") or "")
    candidates = db.query(
        "SELECT id, title, image, category, version, game_version, author, source, url, "
        "size_bytes, size_text, downloads FROM mods "
        "WHERE id <> ? AND (category = ? OR title_key = ?) "
        "ORDER BY downloads DESC, published DESC LIMIT 120",
        (mod_id, mod.get("category") or "", keys))
    scored = [(similarity(mod, row), row) for row in candidates]
    scored = [pair for pair in scored if pair[0] > 0.2]
    scored.sort(key=lambda pair: (-pair[0], -int(pair[1].get("downloads") or 0)))
    items = []
    for score, row in scored[:limit]:
        item = dict(row)
        item["similarity"] = score
        items.append(item)
    return {"ok": True, "items": items, "total": len(items)}


def _stats_map(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db.query(f"SELECT * FROM mod_stats WHERE mod_id IN ({marks})", ids)
    return {row["mod_id"]: row for row in rows}


def popular_mods(limit: int = 12) -> dict:
    rows = db.query(
        "SELECT m.id, m.title, m.image, m.category, m.version, m.game_version, m.source, "
        "m.url, m.downloads, s.installs, s.views FROM mods m "
        "JOIN mod_stats s ON s.mod_id = m.id "
        "ORDER BY (s.installs * 3 + s.downloads + s.views) DESC LIMIT ?", (limit,))
    return {"ok": True, "items": rows}


def new_mods(limit: int = 12) -> dict:
    rows = db.query(
        "SELECT id, title, image, category, version, game_version, source, url, published, "
        "downloads FROM mods WHERE created_at > 0 ORDER BY published DESC, created_at DESC "
        "LIMIT ?", (limit,))
    return {"ok": True, "items": rows}


def categories_from_searches(searches: list[str]) -> set[str]:
    """Категории, угаданные по недавним запросам (карта, дрифт, скин…)."""
    from .util import CATEGORY_LABELS

    found: set[str] = set()
    for query in searches or []:
        text = (query or "").lower()
        for key, label in CATEGORY_LABELS.items():
            if key in text or label.lower() in text:
                found.add(key)
    return found


def recommended_mods(user_id: str = "", limit: int = 12,
                     searches: list[str] | None = None) -> dict:
    """Если есть аккаунт — по категориям установленного и избранного, иначе популярное."""
    categories: set[str] = categories_from_searches(searches or [])
    if user_id:
        rows = db.query(
            "SELECT DISTINCT m.category FROM installed i JOIN mods m ON m.id = i.mod_id "
            "WHERE i.mod_id IS NOT NULL AND i.mod_id <> ''")
        categories.update(row["category"] for row in rows if row.get("category"))
        favs = db.query("SELECT m.category FROM favorites f JOIN mods m ON m.id = f.mod_id")
        categories.update(row["category"] for row in favs if row.get("category"))
    if not categories:
        return popular_mods(limit)

    marks = ",".join("?" for _ in categories)
    rows = db.query(
        f"SELECT m.id, m.title, m.image, m.category, m.version, m.game_version, m.source, "
        f"m.url, m.downloads, s.installs, s.views FROM mods m "
        f"LEFT JOIN mod_stats s ON s.mod_id = m.id "
        f"WHERE m.category IN ({marks}) ORDER BY (COALESCE(s.installs,0) * 3 + "
        f"COALESCE(s.views,0) + m.downloads) DESC LIMIT ?",
        [*categories, limit])
    return {"ok": True, "items": rows, "basis": sorted(categories)}


def recommended_collections(limit: int = 8) -> dict:
    """Рейтинг сборок: оценка × число оценок + установки, свежесть как небольшой бонус."""
    rows = db.query(
        "SELECT c.id, c.title, c.cover, c.category, c.installs, c.views, c.version, "
        "c.owner_id, u.nickname AS owner_nickname, "
        "(SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id=c.id) AS items_count, "
        "(SELECT COALESCE(AVG(stars),0) FROM ratings r WHERE r.target_type='collection' "
        " AND r.target_id=c.id) AS avg_rating, "
        "(SELECT COUNT(*) FROM ratings r WHERE r.target_type='collection' "
        " AND r.target_id=c.id) AS rating_count "
        "FROM collections c LEFT JOIN users u ON u.id=c.owner_id "
        "WHERE c.published=1 "
        "ORDER BY (COALESCE(avg_rating,0) * COALESCE(rating_count,0) * 2 + c.installs * 2 "
        "+ c.views * 0.1) DESC LIMIT ?", (limit,))
    return {"ok": True, "items": rows}


def popular_categories(limit: int = 8) -> dict:
    rows = db.query(
        "SELECT category, COUNT(*) AS mods FROM mods GROUP BY category "
        "ORDER BY mods DESC LIMIT ?", (limit,))
    return {"ok": True, "items": rows}
