"""Пользовательские сборки модов: создание, публикация, установка, статистика.

Сборка — это набор ссылок на существующие моды каталога. Сами ZIP-файлы остаются
в локальном кэше или на сайтах-источниках: в базе только идентификаторы модов.
"""
from __future__ import annotations

import json
import re
import uuid

from . import db
from .util import now_ts, title_key

MAX_ITEMS = 200
MAX_TAGS = 8
CATEGORIES = ("drift", "offroad", "jdm", "racing", "realistic", "tuning", "stunt",
              "city", "mixed", "other")


def _id() -> str:
    return "col_" + uuid.uuid4().hex[:12]


def _tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,;]", raw) if part.strip()]
    tags: list[str] = []
    for item in raw or []:
        text = str(item).strip().lower()[:20]
        if text and text not in tags:
            tags.append(text)
    return tags[:MAX_TAGS]


def _row(row: dict) -> dict:
    data = dict(row)
    try:
        data["tags"] = json.loads(data.get("tags") or "[]")
    except json.JSONDecodeError:
        data["tags"] = []
    data["published"] = bool(data.get("published"))
    return data


def create(owner_id: str, title: str, description: str = "", category: str = "other",
           tags=None, cover: str = "", game_version: str = "") -> dict:
    title = (title or "").strip()
    if len(title) < 3:
        return {"ok": False, "error": "Название сборки: минимум 3 символа."}
    if len(title) > 80:
        return {"ok": False, "error": "Название сборки: максимум 80 символов."}
    if category not in CATEGORIES:
        category = "other"
    coll_id = _id()
    now = now_ts()
    db.execute(
        "INSERT INTO collections(id,owner_id,title,description,cover,category,tags,published,"
        "game_version,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (coll_id, owner_id, title, (description or "")[:4000], (cover or "")[:500], category,
         json.dumps(_tags(tags), ensure_ascii=False), 0, (game_version or "")[:20], 1, now, now))
    return {"ok": True, "collection": get(coll_id, owner_id)["collection"]}


def update(coll_id: str, owner_id: str, **fields) -> dict:
    coll = db.query_one("SELECT * FROM collections WHERE id=?", (coll_id,))
    if not coll:
        return {"ok": False, "error": "Сборка не найдена."}
    if coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    patch: dict = {}
    if "title" in fields:
        title = str(fields["title"]).strip()
        if not 3 <= len(title) <= 80:
            return {"ok": False, "error": "Название сборки: 3–80 символов."}
        patch["title"] = title
    if "description" in fields:
        patch["description"] = str(fields["description"])[:4000]
    if "cover" in fields:
        patch["cover"] = str(fields["cover"])[:500]
    if "category" in fields:
        patch["category"] = fields["category"] if fields["category"] in CATEGORIES else "other"
    if "tags" in fields:
        patch["tags"] = json.dumps(_tags(fields["tags"]), ensure_ascii=False)
    if "game_version" in fields:
        patch["game_version"] = str(fields["game_version"])[:20]
    if not patch:
        return {"ok": True, "collection": get(coll_id, owner_id)["collection"]}
    patch["updated_at"] = now_ts()
    sets = ",".join(f"{k}=?" for k in patch)
    db.execute(f"UPDATE collections SET {sets} WHERE id=?", (*patch.values(), coll_id))
    return {"ok": True, "collection": get(coll_id, owner_id)["collection"]}


def delete(coll_id: str, owner_id: str, is_admin: bool = False) -> dict:
    coll = db.query_one("SELECT * FROM collections WHERE id=?", (coll_id,))
    if not coll:
        return {"ok": False, "error": "Сборка не найдена."}
    if coll["owner_id"] != owner_id and not is_admin:
        return {"ok": False, "error": "Удалять сборку может только автор."}
    db.execute("DELETE FROM collection_items WHERE collection_id=?", (coll_id,))
    db.execute("DELETE FROM collection_versions WHERE collection_id=?", (coll_id,))
    db.execute("DELETE FROM collections WHERE id=?", (coll_id,))
    return {"ok": True}


def _mods_by_id(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db.query(f"SELECT id,title,image,category,version,game_version,source,url,"
                    f"size_bytes,size_text FROM mods WHERE id IN ({marks})", ids)
    return {row["id"]: row for row in rows}


def items(coll_id: str) -> list[dict]:
    rows = db.query(
        "SELECT ci.mod_id, ci.position, ci.note, ci.added_at, m.title, m.image, m.category,"
        " m.version, m.game_version, m.source, m.url, m.size_bytes, m.size_text "
        "FROM collection_items ci LEFT JOIN mods m ON m.id = ci.mod_id "
        "WHERE ci.collection_id=? ORDER BY ci.position, ci.added_at", (coll_id,))
    return rows


def get(coll_id: str, viewer_id: str = "") -> dict:
    row = db.query_one("SELECT * FROM collections WHERE id=?", (coll_id,))
    if not row:
        return {"ok": False, "error": "Сборка не найдена."}
    coll = _row(row)
    if not coll["published"] and coll["owner_id"] != viewer_id:
        return {"ok": False, "error": "Сборка ещё не опубликована."}
    coll["items"] = items(coll_id)
    owner = db.query_one("SELECT id,nickname,avatar_color,bio FROM users WHERE id=?",
                         (coll["owner_id"],))
    coll["owner"] = owner or {"id": coll["owner_id"], "nickname": "удалённый пользователь",
                              "avatar_color": "#6b7280", "bio": ""}
    coll["is_owner"] = coll["owner_id"] == viewer_id
    coll["rating"] = rating_summary("collection", coll_id)
    coll["versions"] = db.query(
        "SELECT version, note, created_at, snapshot FROM collection_versions "
        "WHERE collection_id=? ORDER BY version DESC LIMIT 20", (coll_id,))
    for entry in coll["versions"]:
        try:
            entry["snapshot"] = json.loads(entry.get("snapshot") or "[]")
        except json.JSONDecodeError:
            entry["snapshot"] = []
        entry["count"] = len(entry["snapshot"])
    return {"ok": True, "collection": coll}


def rating_summary(target_type: str, target_id: str) -> dict:
    row = db.query_one(
        "SELECT COUNT(*) AS count, COALESCE(AVG(stars),0) AS avg, "
        "COALESCE(SUM(installed),0) AS installed FROM ratings "
        "WHERE target_type=? AND target_id=? AND stars > 0", (target_type, target_id))
    return {"count": int((row or {}).get("count", 0)),
            "avg": round(float((row or {}).get("avg", 0)), 2),
            "installed": int((row or {}).get("installed", 0))}


def add_item(coll_id: str, owner_id: str, mod_id: str, note: str = "") -> dict:
    coll = db.query_one("SELECT * FROM collections WHERE id=?", (coll_id,))
    if not coll:
        return {"ok": False, "error": "Сборка не найдена."}
    if coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    if not db.query_one("SELECT 1 FROM mods WHERE id=?", (mod_id,)):
        return {"ok": False, "error": "Мод не найден в каталоге."}
    count = db.query_one("SELECT COUNT(*) AS c FROM collection_items WHERE collection_id=?",
                         (coll_id,))["c"]
    if count >= MAX_ITEMS:
        return {"ok": False, "error": f"В сборке уже {MAX_ITEMS} модов — это предел."}
    position = db.query_one(
        "SELECT COALESCE(MAX(position),-1)+1 AS p FROM collection_items WHERE collection_id=?",
        (coll_id,))["p"]
    db.execute("INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,added_at)"
               " VALUES(?,?,?,?,?)", (coll_id, mod_id, position, (note or "")[:200], now_ts()))
    db.execute("UPDATE collections SET updated_at=? WHERE id=?", (now_ts(), coll_id))
    return {"ok": True, "items": items(coll_id)}


def remove_item(coll_id: str, owner_id: str, mod_id: str) -> dict:
    coll = db.query_one("SELECT owner_id FROM collections WHERE id=?", (coll_id,))
    if not coll or coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    db.execute("DELETE FROM collection_items WHERE collection_id=? AND mod_id=?",
               (coll_id, mod_id))
    db.execute("UPDATE collections SET updated_at=? WHERE id=?", (now_ts(), coll_id))
    return {"ok": True, "items": items(coll_id)}


def reorder(coll_id: str, owner_id: str, order: list[str]) -> dict:
    coll = db.query_one("SELECT owner_id FROM collections WHERE id=?", (coll_id,))
    if not coll or coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    for position, mod_id in enumerate(order):
        db.execute("UPDATE collection_items SET position=? WHERE collection_id=? AND mod_id=?",
                   (position, coll_id, mod_id))
    return {"ok": True, "items": items(coll_id)}


def _snapshot(coll_id: str, version: int, note: str) -> None:
    payload = [{"mod_id": row["mod_id"], "note": row.get("note") or ""} for row in items(coll_id)]
    db.execute(
        "INSERT INTO collection_versions(collection_id,version,snapshot,note,created_at)"
        " VALUES(?,?,?,?,?)",
        (coll_id, version, json.dumps(payload, ensure_ascii=False), (note or "")[:300], now_ts()))


def publish(coll_id: str, owner_id: str, note: str = "") -> dict:
    coll = db.query_one("SELECT * FROM collections WHERE id=?", (coll_id,))
    if not coll:
        return {"ok": False, "error": "Сборка не найдена."}
    if coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    if not items(coll_id):
        return {"ok": False, "error": "Добавьте хотя бы один мод — пустую сборку публиковать нельзя."}
    version = int(coll["version"] or 1)
    if coll["published"]:
        version += 1
    _snapshot(coll_id, version, note or "Публикация")
    db.execute("UPDATE collections SET published=1, version=?, updated_at=? WHERE id=?",
               (version, now_ts(), coll_id))
    return {"ok": True, "collection": get(coll_id, owner_id)["collection"]}


def unpublish(coll_id: str, owner_id: str) -> dict:
    coll = db.query_one("SELECT owner_id FROM collections WHERE id=?", (coll_id,))
    if not coll or coll["owner_id"] != owner_id:
        return {"ok": False, "error": "Это не ваша сборка."}
    db.execute("UPDATE collections SET published=0, updated_at=? WHERE id=?",
               (now_ts(), coll_id))
    return {"ok": True}


def record_event(kind: str, coll_id: str = "", mod_id: str = "", user_id: str = "") -> None:
    db.execute("INSERT INTO stats_events(kind,mod_id,collection_id,user_id,created_at)"
               " VALUES(?,?,?,?,?)", (kind, mod_id, coll_id, user_id, now_ts()))
    if coll_id:
        column = {"view": "views", "download": "downloads", "install": "installs"}.get(kind)
        if column:
            db.execute(f"UPDATE collections SET {column}={column}+1 WHERE id=?", (coll_id,))
    if mod_id:
        db.execute("INSERT INTO mod_stats(mod_id) VALUES(?) ON CONFLICT(mod_id) DO NOTHING",
                   (mod_id,))
        column = {"view": "views", "download": "downloads", "install": "installs"}.get(kind)
        if column:
            db.execute(f"UPDATE mod_stats SET {column}={column}+1 WHERE mod_id=?", (mod_id,))


def list_collections(kind: str = "popular", query: str = "", category: str = "",
                     limit: int = 24, offset: int = 0, viewer_id: str = "") -> dict:
    where = ["c.published=1"]
    params: list = []
    if kind == "mine":
        where = ["c.owner_id=?"]
        params.append(viewer_id)
    elif category:
        where.append("c.category=?")
        params.append(category)
    if query:
        where.append("(lower(c.title) LIKE ? OR lower(c.description) LIKE ?)")
        like = f"%{query.lower()}%"
        params.extend([like, like])
    order = {"new": "c.created_at DESC", "popular": "(c.installs*3 + c.views) DESC",
             "rating": "c.installs DESC", "mine": "c.updated_at DESC"}.get(kind, "c.created_at DESC")
    clause = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS c FROM collections c WHERE {clause}", params)["c"]
    rows = db.query(
        f"SELECT c.*, u.nickname AS owner_nickname, u.avatar_color AS owner_color, "
        f"(SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id=c.id) AS items_count, "
        f"(SELECT COUNT(*) FROM comments cm WHERE cm.target_type='collection' "
        f" AND cm.target_id=c.id AND cm.deleted=0) AS comments_count "
        f"FROM collections c LEFT JOIN users u ON u.id=c.owner_id "
        f"WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?", [*params, limit, offset])
    result = []
    for row in rows:
        data = _row(row)
        data["rating"] = rating_summary("collection", row["id"])
        result.append(data)
    return {"ok": True, "items": result, "total": total, "kind": kind}


def plan(coll_id: str, viewer_id: str = "") -> dict:
    """Что из сборки уже стоит, чего не хватает и что качать первым."""
    data = get(coll_id, viewer_id)
    if not data.get("ok"):
        return data
    coll = data["collection"]
    installed = {row.get("mod_id") for row in db.installed_list() if row.get("mod_id")}
    installed_keys = {title_key(row.get("title") or "") for row in db.installed_list()}
    missing, present = [], []
    for item in coll["items"]:
        key = title_key(item.get("title") or "")
        if item["mod_id"] in installed or key in installed_keys:
            present.append(item["mod_id"])
        else:
            missing.append(item)
    return {"ok": True, "collection": coll, "total": len(coll["items"]),
            "installed": present, "missing": missing, "next": missing[0] if missing else None}


def stats() -> dict:
    users = db.query_one("SELECT COUNT(*) AS c FROM users")["c"]
    published = db.query_one("SELECT COUNT(*) AS c FROM collections WHERE published=1")["c"]
    installs = db.query_one("SELECT COALESCE(SUM(installs),0) AS s FROM collections")["s"]
    comments = db.query_one("SELECT COUNT(*) AS c FROM comments WHERE deleted=0")["c"]
    ratings = db.query_one("SELECT COUNT(*) AS c FROM ratings")["c"]
    top_mods = db.query(
        "SELECT m.id, m.title, m.image, s.installs, s.downloads, s.views FROM mod_stats s "
        "JOIN mods m ON m.id=s.mod_id ORDER BY s.installs DESC, s.views DESC LIMIT 10")
    top_collections = db.query(
        "SELECT id, title, cover, installs, views, category FROM collections WHERE published=1 "
        "ORDER BY installs DESC, views DESC LIMIT 10")
    days = db.query(
        "SELECT date(created_at,'unixepoch') AS day, COUNT(*) AS events FROM stats_events "
        "WHERE created_at > strftime('%s','now') - 14*86400 GROUP BY day ORDER BY day")
    return {"users": users, "collections": published, "installs": installs,
            "comments": comments, "ratings": ratings, "top_mods": top_mods,
            "top_collections": top_collections, "activity": days}
