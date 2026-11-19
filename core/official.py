"""Официальные сборки от команды BeamHub: базовые и тематические."""
from __future__ import annotations

import uuid

from . import db
from .util import now_ts

OFFICIAL_EMAIL = "team@beamhub.local"
OFFICIAL_NICK = "BeamHub Team"

# (заголовок, категория, теги, ключевые слова для поиска модов)
OFFICIAL = (
    ("Дрифт: базовый набор", "drift", ["drift", "jdm", "official"],
     ("drift", "jdm", "silvia", "s15", "s14", "e36", "supra", "rx-7", "rx7")),
    ("Оффроуд: базовый набор", "offroad", ["offroad", "4x4", "official"],
     ("offroad", "4x4", "jeep", "crawler", "trophy", "rally raid", "suv")),
    ("Стрит: базовый набор", "city", ["street", "urban", "official"],
     ("street", "urban", "city", "tuned", "tuning")),
    ("Драг: базовый набор", "cars", ["drag", "official"],
     ("drag", "quarter", "top speed", "launch", "nitro")),
    ("Форсаж: тематический набор", "cars", ["fast", "furious", "official"],
     ("supra", "skyline", "charger", "gtr", "rx-7", "eclipse", "mustang")),
    ("JDM классика", "jdm", ["jdm", "official"],
     ("skyline", "silvia", "supra", "rx-7", "ae86", "evo", "sti", "honda")),
    ("Русские машины", "other", ["russian", "official"],
     ("vaz", "lada", "uaz", "gaz", "ваз", "лада", "уаз", "газ", "нива")),
    ("Карты для покатушек", "maps", ["maps", "official"],
     ("track", "circuit", "map", "route", "city map")),
)


def _ensure_team_user() -> str:
    row = db.query_one("SELECT id FROM users WHERE email=?", (OFFICIAL_EMAIL,))
    if row:
        return row["id"]
    user_id = "usr_" + uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO users(id,email,nickname,bio,avatar_color,provider,provider_id,role,"
        "created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user_id, OFFICIAL_EMAIL, OFFICIAL_NICK,
         "Официальные подборки от команды BeamHub", "#ff7a1a", "official", "team",
         "admin", now_ts(), now_ts()))
    return user_id


def _pick_mods(words: tuple[str, ...], limit: int = 10) -> list[str]:
    clauses = " OR ".join("lower(title) LIKE ? OR lower(category) LIKE ?" for _ in words)
    params: list = []
    for word in words:
        like = f"%{word.lower()}%"
        params.extend([like, like])
    rows = db.query(
        f"SELECT id FROM mods WHERE ({clauses}) AND source <> 'playground' "
        f"ORDER BY downloads DESC LIMIT ?",
        [*params, limit])
    return [row["id"] for row in rows]


def ensure() -> int:
    """Создаёт официальные сборки, если их ещё нет. Возвращает число созданных."""
    team = _ensure_team_user()
    existing = {row["title"] for row in db.query(
        "SELECT title FROM collections WHERE owner_id=?", (team,))}
    created = 0
    for title, category, tags, words in OFFICIAL:
        if title in existing:
            continue
        mod_ids = _pick_mods(words)
        if not mod_ids:
            continue
        coll_id = "col_" + uuid.uuid4().hex[:12]
        now = now_ts()
        db.execute(
            "INSERT INTO collections(id,owner_id,title,description,cover,category,tags,published,"
            "game_version,version,views,installs,downloads,created_at,updated_at,remote) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (coll_id, team, title,
             "Официальная подборка BeamHub: собрана командой лаунчера.", "",
             category, ",".join(tags), 1, "", 1, 0, 0, 0, now, now))
        for index, mod_id in enumerate(mod_ids):
            db.execute(
                "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                "added_at) VALUES(?,?,?,?,?)",
                (coll_id, mod_id, index, "", now))
        created += 1
    cleanup_torrents()
    refill()
    return created


def cleanup_torrents() -> int:
    """Убирает из официальных подборок моды с торрент-источника и добирает замену."""
    team = db.query_one("SELECT id FROM users WHERE email=?", (OFFICIAL_EMAIL,))
    if not team:
        return 0
    removed = 0
    for title, _category, _tags, words in OFFICIAL:
        coll = db.query_one("SELECT id FROM collections WHERE owner_id=? AND title=?",
                            (team["id"], title))
        if not coll:
            continue
        rows = db.query(
            "SELECT ci.mod_id FROM collection_items ci JOIN mods m ON m.id = ci.mod_id "
            "WHERE ci.collection_id=? AND m.source='playground'", (coll["id"],))
        if not rows:
            continue
        db.execute(
            "DELETE FROM collection_items WHERE collection_id=? AND mod_id IN ("
            "SELECT id FROM mods WHERE source='playground')", (coll["id"],))
        removed += len(rows)
        have = db.query_one(
            "SELECT COUNT(*) AS c FROM collection_items WHERE collection_id=?",
            (coll["id"],))["c"]
        if have < 8:
            taken = {row["mod_id"] for row in db.query(
                "SELECT mod_id FROM collection_items WHERE collection_id=?", (coll["id"],))}
            fill = db.query(
                "SELECT id FROM mods WHERE category=? AND source <> 'playground' "
                "ORDER BY downloads DESC LIMIT 40", (_category,))
            for mod_id in [row["id"] for row in fill if row["id"] not in taken]:
                if have >= 8:
                    break
                db.execute(
                    "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                    "added_at) VALUES(?,?,?,?,?)",
                    (coll["id"], mod_id, have, "", now_ts()))
                have += 1
        if have < 10:
            extra = [mod_id for mod_id in _pick_mods(words, 20)
                     if mod_id not in {row["mod_id"] for row in db.query(
                         "SELECT mod_id FROM collection_items WHERE collection_id=?",
                         (coll["id"],))}]
            for index, mod_id in enumerate(extra[:10 - have]):
                db.execute(
                    "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                    "added_at) VALUES(?,?,?,?,?)",
                    (coll["id"], mod_id, have + index, "", now_ts()))
    return removed

def refill(target: int = 10) -> int:
    """Добирает официальные сборки до target модов (без торрентов)."""
    team = db.query_one("SELECT id FROM users WHERE email=?", (OFFICIAL_EMAIL,))
    if not team:
        return 0
    added = 0
    for title, category, _tags, words in OFFICIAL:
        coll = db.query_one("SELECT id FROM collections WHERE owner_id=? AND title=?",
                            (team["id"], title))
        if not coll:
            continue
        taken = {row["mod_id"] for row in db.query(
            "SELECT mod_id FROM collection_items WHERE collection_id=?", (coll["id"],))}
        have = len(taken)
        if have >= target:
            continue
        for mod_id in _pick_mods(words, target * 3):
            if have >= target:
                break
            if mod_id in taken:
                continue
            db.execute(
                "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                "added_at) VALUES(?,?,?,?,?)",
                (coll["id"], mod_id, have, "", now_ts()))
            taken.add(mod_id)
            have += 1
            added += 1
        if have < target:
            rows = db.query(
                "SELECT id FROM mods WHERE category=? AND source <> 'playground' "
                "ORDER BY downloads DESC LIMIT 80", (category,))
            for row in rows:
                if have >= target:
                    break
                if row["id"] in taken:
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                    "added_at) VALUES(?,?,?,?,?)",
                    (coll["id"], row["id"], have, "", now_ts()))
                taken.add(row["id"])
                have += 1
                added += 1
    return added
