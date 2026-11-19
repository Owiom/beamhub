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
    ("Драг: базовый набор", "racing", ["drag", "official"],
     ("drag", "quarter", "top speed", "launch", "nitro")),
    ("Форсаж: тематический набор", "racing", ["fast", "furious", "official"],
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
        f"SELECT id FROM mods WHERE ({clauses}) ORDER BY downloads DESC LIMIT ?",
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
    return created
