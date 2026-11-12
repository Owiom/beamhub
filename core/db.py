"""Хранилище каталога и библиотеки (SQLite, один файл в data/)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from .util import now_ts

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
# BEAMHUB_DB позволяет тестам и отладке работать со своей базой
DB_PATH = Path(os.environ.get("BEAMHUB_DB") or (DATA_DIR / "beamhub.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS mods(
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    ext_id TEXT,
    title TEXT,
    category TEXT DEFAULT 'other',
    game_version TEXT DEFAULT '',
    version TEXT DEFAULT '',
    size_text TEXT DEFAULT '',
    size_bytes INTEGER DEFAULT 0,
    image TEXT DEFAULT '',
    author TEXT DEFAULT '',
    description TEXT DEFAULT '',
    published INTEGER DEFAULT 0,
    downloads INTEGER DEFAULT 0,
    download_url TEXT DEFAULT '',
    title_key TEXT DEFAULT '',
    enriched_at INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS installed(
    key TEXT PRIMARY KEY,
    mod_id TEXT,
    filename TEXT,
    path TEXT,
    sha256 TEXT,
    size INTEGER DEFAULT 0,
    installed_at INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    source_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    version TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS favorites(mod_id TEXT PRIMARY KEY, added_at INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS mods_cat ON mods(category);
CREATE INDEX IF NOT EXISTS mods_published ON mods(published DESC);
CREATE INDEX IF NOT EXISTS mods_title ON mods(title);
"""

MOD_FIELDS = [
    "id", "source", "url", "ext_id", "title", "category", "game_version", "version",
    "size_text", "size_bytes", "image", "author", "description", "published",
    "downloads", "download_url", "title_key", "enriched_at", "created_at",
]


def _backfill_title_keys() -> None:
    """Досчитываем нормализованные имена для старых баз (нужно для поиска дубликатов)."""
    from .util import title_key

    with _lock:
        rows = _db().execute(
            "SELECT id, title FROM mods WHERE title_key IS NULL OR title_key = ''").fetchall()
        if not rows:
            return
        for row in rows:
            try:
                _db().execute("UPDATE mods SET title_key=? WHERE id=?",
                              (title_key(row["title"] or ""), row["id"]))
            except sqlite3.Error:
                continue
        _db().commit()
        print(f"[beamhub] досчитал ключи дубликатов: {len(rows)}")


def init() -> None:
    global _conn
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.executescript(SCHEMA)
            # миграции для баз, созданных прошлыми версиями
            columns = {row["name"] for row in _conn.execute("PRAGMA table_info(installed)")}
            if "version" not in columns:
                _conn.execute("ALTER TABLE installed ADD COLUMN version TEXT DEFAULT ''")
            mod_columns = {row["name"] for row in _conn.execute("PRAGMA table_info(mods)")}
            if "downloads" not in mod_columns:
                _conn.execute("ALTER TABLE mods ADD COLUMN downloads INTEGER DEFAULT 0")
            if "title_key" not in mod_columns:
                _conn.execute("ALTER TABLE mods ADD COLUMN title_key TEXT DEFAULT ''")
            _conn.execute("CREATE INDEX IF NOT EXISTS mods_title_key ON mods(title_key)")
            _conn.commit()
            _backfill_title_keys()


def _db() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


def get_meta(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = _db().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_meta(key: str, value: str) -> None:
    with _lock:
        _db().execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (key, value))
        _db().commit()


def upsert_mod(mod: dict) -> None:
    from .util import title_key

    mod = dict(mod)
    mod.setdefault("created_at", now_ts())
    if "title_key" not in mod:
        mod["title_key"] = title_key(mod.get("title") or "")
    cols = [c for c in MOD_FIELDS if c in mod]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (f"INSERT INTO mods({','.join(cols)}) VALUES({placeholders}) "
           f"ON CONFLICT(id) DO UPDATE SET {updates}")
    with _lock:
        _db().execute(sql, [mod[c] for c in cols])
        _db().commit()


def update_mod(mod_id: str, **fields) -> None:
    if not fields:
        return
    if "title" in fields and "title_key" not in fields:
        from .util import title_key

        fields["title_key"] = title_key(fields["title"] or "")
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock:
        _db().execute(f"UPDATE mods SET {sets} WHERE id=?", [*fields.values(), mod_id])
        _db().commit()


def get_mod(mod_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM mods WHERE id=?", (mod_id,)).fetchone()
    return dict(row) if row else None


def count_mods() -> int:
    with _lock:
        return _db().execute("SELECT COUNT(*) c FROM mods").fetchone()["c"]


def pending_enrich(limit: int = 5000, max_age: int = 60 * 60 * 24 * 30) -> list[str]:
    cutoff = now_ts() - max_age
    with _lock:
        rows = _db().execute(
            # сначала те, у кого нет даты: у них и название может быть заглушкой
            "SELECT id FROM mods WHERE enriched_at < ? "
            "ORDER BY (published > 0) ASC, published DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [r["id"] for r in rows]


def query_mods(q: str = "", category: str = "", source: str = "", sort: str = "new",
               offset: int = 0, limit: int = 60, enriched_only: bool = False,
               game: str = "", has_image: bool = False, favorites_only: bool = False) -> dict:
    where = []
    params: list = []
    if q:
        where.append("(title LIKE ? OR description LIKE ? OR author LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if category and category != "all":
        where.append("category = ?")
        params.append(category)
    if source and source != "all":
        where.append("source = ?")
        params.append(source)
    if enriched_only:
        where.append("enriched_at > 0")
    if game:
        where.append("(game_version = ? OR title LIKE ?)")
        params += [game, f"%({game}%"]
    if has_image:
        where.append("image <> ''")
    if favorites_only:
        where.append("id IN (SELECT mod_id FROM favorites)")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = {
        "new": "published DESC, title COLLATE NOCASE ASC",
        "old": "published ASC, title COLLATE NOCASE ASC",
        "title": "title COLLATE NOCASE ASC",
        "title_desc": "title COLLATE NOCASE DESC",
        "size": "size_bytes DESC, title COLLATE NOCASE ASC",
        "downloads": "downloads DESC, published DESC",
    }.get(sort, "published DESC")
    with _lock:
        total = _db().execute(f"SELECT COUNT(*) c FROM mods {clause}", params).fetchone()["c"]
        rows = _db().execute(
            f"SELECT * FROM mods {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def category_counts() -> dict:
    with _lock:
        rows = _db().execute("SELECT category, COUNT(*) c FROM mods GROUP BY category").fetchall()
    return {r["category"]: r["c"] for r in rows}


def installed_list() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM installed ORDER BY installed_at DESC").fetchall()
    return [dict(r) for r in rows]


def installed_by_key(key: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM installed WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def installed_by_hash(sha: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM installed WHERE sha256=?", (sha,)).fetchone()
    return dict(row) if row else None


def installed_add(entry: dict) -> None:
    with _lock:
        _db().execute(
            "INSERT INTO installed(key,mod_id,filename,path,sha256,size,installed_at,active,source_url,title,version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "mod_id=excluded.mod_id, filename=excluded.filename, path=excluded.path, sha256=excluded.sha256, "
            "size=excluded.size, installed_at=excluded.installed_at, active=excluded.active, "
            "source_url=excluded.source_url, title=excluded.title, version=excluded.version",
            (entry["key"], entry.get("mod_id"), entry.get("filename"), entry.get("path"),
             entry.get("sha256"), entry.get("size", 0), entry.get("installed_at", now_ts()),
             1 if entry.get("active", True) else 0, entry.get("source_url", ""),
             entry.get("title", ""), entry.get("version", "")),
        )
        _db().commit()


def source_counts() -> dict:
    with _lock:
        rows = _db().execute("SELECT source, COUNT(*) c FROM mods GROUP BY source").fetchall()
    return {r["source"]: r["c"] for r in rows}


# ---------------------------------------------------------------- избранное
def favorite_ids() -> set[str]:
    with _lock:
        rows = _db().execute("SELECT mod_id FROM favorites").fetchall()
    return {r["mod_id"] for r in rows}


def favorite_toggle(mod_id: str) -> bool:
    """Возвращает новое состояние: True — в избранном."""
    with _lock:
        exists = _db().execute("SELECT 1 FROM favorites WHERE mod_id=?", (mod_id,)).fetchone()
        if exists:
            _db().execute("DELETE FROM favorites WHERE mod_id=?", (mod_id,))
            _db().commit()
            return False
        _db().execute("INSERT INTO favorites(mod_id, added_at) VALUES(?,?)", (mod_id, now_ts()))
        _db().commit()
        return True


def favorites_count() -> int:
    with _lock:
        return _db().execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"]


def duplicate_hashes() -> dict:
    """sha256 -> сколько файлов с таким содержимым (для поиска дубликатов)."""
    with _lock:
        rows = _db().execute(
            "SELECT sha256, COUNT(*) c FROM installed WHERE sha256 <> '' GROUP BY sha256 "
            "HAVING c > 1").fetchall()
    return {r["sha256"]: r["c"] for r in rows}


def library_export() -> list[dict]:
    with _lock:
        rows = _db().execute(
            "SELECT key, mod_id, title, filename, version, size, installed_at, active, source_url "
            "FROM installed ORDER BY installed_at DESC").fetchall()
    return [dict(r) for r in rows]


def updates_count() -> int:
    """Сколько установленных модов имеет в каталоге более новую версию."""
    with _lock:
        row = _db().execute(
            "SELECT COUNT(*) c FROM installed i JOIN mods m ON m.id = i.mod_id "
            "WHERE i.version <> '' AND m.version <> '' AND i.version <> m.version").fetchone()
    return row["c"]


def update_rows() -> list[dict]:
    with _lock:
        rows = _db().execute(
            "SELECT i.*, m.version AS catalog_version, m.title AS catalog_title "
            "FROM installed i JOIN mods m ON m.id = i.mod_id "
            "WHERE i.version <> '' AND m.version <> '' AND i.version <> m.version "
            "ORDER BY i.installed_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- дубликаты
def title_key_counts(keys: list[str] | set[str]) -> dict:
    """title_key -> {count, sources}. Для отметки «такой же мод есть на другом сайте»."""
    keys = [k for k in keys if k]
    if not keys:
        return {}
    result: dict[str, dict] = {}
    chunk = 400
    with _lock:
        for start in range(0, len(keys), chunk):
            part = keys[start:start + chunk]
            placeholders = ",".join("?" for _ in part)
            rows = _db().execute(
                f"SELECT title_key, COUNT(*) c, GROUP_CONCAT(DISTINCT source) s "
                f"FROM mods WHERE title_key IN ({placeholders}) GROUP BY title_key",
                part).fetchall()
            for row in rows:
                result[row["title_key"]] = {
                    "count": row["c"],
                    "sources": sorted({s for s in (row["s"] or "").split(",") if s}),
                }
    return result


def installed_doubles() -> dict:
    """Ключи установленных файлов, у которых есть «близнец».

    Совпадение по sha256, по mod_id (один мод поставлен дважды) или по title_key
    (один и тот же мод разными архивами).
    """
    from .util import title_key

    doubles: dict[str, str] = {}
    with _lock:
        rows = _db().execute(
            "SELECT key, mod_id, sha256, title FROM installed").fetchall()
    by_hash: dict[str, list[str]] = {}
    by_mod: dict[str, list[str]] = {}
    by_title: dict[str, list[str]] = {}
    for row in rows:
        if row["sha256"]:
            by_hash.setdefault(row["sha256"], []).append(row["key"])
        if row["mod_id"]:
            by_mod.setdefault(row["mod_id"], []).append(row["key"])
        key = title_key(row["title"] or "")
        if key:
            by_title.setdefault(key, []).append(row["key"])
    for group, reason in ((by_hash, "файл"), (by_mod, "мод"), (by_title, "название")):
        for keys in group.values():
            if len(keys) > 1:
                for key in keys:
                    doubles.setdefault(key, reason)
    return doubles


def installed_remove(key: str) -> None:
    with _lock:
        _db().execute("DELETE FROM installed WHERE key=?", (key,))
        _db().commit()


def installed_for_mod(mod_id: str) -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM installed WHERE mod_id=?", (mod_id,)).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with _lock:
        db = _db()
        mods = db.execute("SELECT COUNT(*) c FROM mods").fetchone()["c"]
        enriched = db.execute("SELECT COUNT(*) c FROM mods WHERE enriched_at>0").fetchone()["c"]
        inst = db.execute("SELECT COUNT(*) c FROM installed").fetchone()["c"]
        size = db.execute("SELECT COALESCE(SUM(size),0) s FROM installed").fetchone()["s"]
    return {"mods": mods, "enriched": enriched, "installed": inst, "installed_bytes": size}


def export_json() -> str:
    with _lock:
        rows = _db().execute("SELECT * FROM mods").fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)
