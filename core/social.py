"""Комментарии, ответы и оценки сборок и модов.

Правила, которые здесь зашиты и проверяются тестами:
  * один пользователь — одна оценка на объект (повторная запись обновляет её);
  * комментарии и отзывы можно править и удалять только свои (или модератору);
  * удаление мягкое: текст скрывается, ветка не рвётся;
  * лимиты частоты защищают от спама;
  * отметка «установил через BeamHub» ставится по факту, а не на слово.
"""
from __future__ import annotations

import uuid

from . import accounts, collections, db
from .util import now_ts

BODY_MIN = 1
BODY_MAX = 1500
REVIEW_MAX = 2000
COMMENT_LIMIT = 12
COMMENT_WINDOW = 600          # 10 минут
RATING_LIMIT = 30
RATING_WINDOW = 3600          # час
TARGETS = ("collection", "mod")


def _rid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def _allow(key: str, limit: int, window: int) -> bool:
    """Простой счётчик частоты: одна запись на ключ, окно фиксированной длины."""
    now = now_ts()
    row = db.query_one("SELECT window_start, count FROM rate_limits WHERE key=?", (key,))
    if not row or now - int(row["window_start"] or 0) > window:
        db.execute("INSERT INTO rate_limits(key,window_start,count) VALUES(?,?,1) "
                   "ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start, "
                   "count=1", (key, now))
        return True
    if int(row["count"] or 0) >= limit:
        return False
    db.execute("UPDATE rate_limits SET count=count+1 WHERE key=?", (key,))
    return True


def _target_ok(target_type: str, target_id: str) -> tuple[bool, str]:
    if target_type not in TARGETS or not target_id:
        return False, "Неизвестный объект."
    if target_type == "collection":
        row = db.query_one("SELECT published, owner_id FROM collections WHERE id=?", (target_id,))
        if not row:
            return False, "Сборка не найдена."
        return True, ""
    if not db.get_mod(target_id):
        return False, "Мод не найден."
    return True, ""


def _user_brief(user_id: str) -> dict:
    row = db.query_one("SELECT id, nickname, avatar_color, role FROM users WHERE id=?", (user_id,))
    return row or {"id": user_id, "nickname": "удалённый пользователь",
                   "avatar_color": "#6b7280", "role": "user"}


# ---------------------------------------------------------------- комментарии
def add_comment(user_id: str, target_type: str, target_id: str, body: str,
                parent_id: str = "") -> dict:
    ok, error = _target_ok(target_type, target_id)
    if not ok:
        return {"ok": False, "error": error}
    text = (body or "").strip()
    if len(text) < BODY_MIN:
        return {"ok": False, "error": "Комментарий пустой."}
    if len(text) > BODY_MAX:
        return {"ok": False, "error": f"Комментарий длиннее {BODY_MAX} символов."}
    if not _allow(f"comment:{user_id}", COMMENT_LIMIT, COMMENT_WINDOW):
        return {"ok": False, "error": "Слишком часто. Подождите пару минут."}

    thread = ""
    if parent_id:
        parent = db.query_one("SELECT * FROM comments WHERE id=?", (parent_id,))
        if not parent or parent["target_type"] != target_type or parent["target_id"] != target_id:
            return {"ok": False, "error": "Комментарий, на который вы отвечаете, не найден."}
        if parent["deleted"]:
            return {"ok": False, "error": "Ветка удалена."}
        # ответ на ответ прикрепляем к корню ветки — глубина не растёт
        thread = parent["parent_id"] or parent["id"]

    comment_id = _rid("cmt_")
    db.execute(
        "INSERT INTO comments(id,target_type,target_id,user_id,parent_id,body,created_at,"
        "updated_at,deleted) VALUES(?,?,?,?,?,?,?,?,0)",
        (comment_id, target_type, target_id, user_id, thread, text, now_ts(), now_ts()))
    return {"ok": True, "id": comment_id}


def update_comment(user_id: str, comment_id: str, body: str) -> dict:
    comment = db.query_one("SELECT * FROM comments WHERE id=?", (comment_id,))
    if not comment or comment["deleted"]:
        return {"ok": False, "error": "Комментарий не найден."}
    if comment["user_id"] != user_id:
        return {"ok": False, "error": "Можно править только свои комментарии."}
    text = (body or "").strip()
    if not text or len(text) > BODY_MAX:
        return {"ok": False, "error": f"Комментарий: 1–{BODY_MAX} символов."}
    db.execute("UPDATE comments SET body=?, updated_at=? WHERE id=?",
               (text, now_ts(), comment_id))
    return {"ok": True}


def delete_comment(user_id: str, comment_id: str, is_moderator: bool = False) -> dict:
    comment = db.query_one("SELECT * FROM comments WHERE id=?", (comment_id,))
    if not comment:
        return {"ok": False, "error": "Комментарий не найден."}
    if comment["user_id"] != user_id and not is_moderator:
        return {"ok": False, "error": "Можно удалять только свои комментарии."}
    db.execute("UPDATE comments SET deleted=1, body='', updated_at=? WHERE id=?",
               (now_ts(), comment_id))
    if is_moderator and comment["user_id"] != user_id:
        db.execute("INSERT INTO admin_actions(actor_id,action,target_type,target_id,note,"
                   "created_at) VALUES(?,?,?,?,?,?)",
                   (user_id, "delete_comment", "comment", comment_id, "", now_ts()))
    return {"ok": True}


def comments(target_type: str, target_id: str, viewer_id: str = "") -> dict:
    rows = db.query(
        "SELECT * FROM comments WHERE target_type=? AND target_id=? ORDER BY created_at",
        (target_type, target_id))
    viewer = accounts.user_by_id(viewer_id) if viewer_id else None
    can_moderate = accounts.is_moderator(viewer)

    def public(row: dict, reply: bool = False) -> dict:
        author = _user_brief(row["user_id"])
        return {
            "id": row["id"],
            "body": row["body"] if not row["deleted"] else "",
            "deleted": bool(row["deleted"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "edited": bool(row["updated_at"] and row["updated_at"] - row["created_at"] > 2),
            "author": author,
            "is_mine": bool(viewer_id and row["user_id"] == viewer_id),
            "can_moderate": bool(can_moderate),
            "replies": [] if reply else [],
        }

    roots: list[dict] = []
    index: dict[str, dict] = {}
    for row in rows:
        item = public(row)
        index[row["id"]] = item
        if row["parent_id"]:
            parent = index.get(row["parent_id"])
            if parent is not None:
                parent["replies"].append(item)
            else:
                roots.append(item)
        else:
            roots.append(item)
    total = sum(1 for row in rows if not row["deleted"])
    return {"ok": True, "items": roots, "count": total,
            "can_post": bool(viewer_id)}


# ---------------------------------------------------------------- оценки
def set_rating(user_id: str, target_type: str, target_id: str, stars: int,
               review: str = "") -> dict:
    ok, error = _target_ok(target_type, target_id)
    if not ok:
        return {"ok": False, "error": error}
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Оценка должна быть целым числом 1–5."}
    if not 1 <= stars <= 5:
        return {"ok": False, "error": "Оценка должна быть от 1 до 5."}
    text = (review or "").strip()[:REVIEW_MAX]
    if not _allow(f"rate:{user_id}", RATING_LIMIT, RATING_WINDOW):
        return {"ok": False, "error": "Слишком много оценок за час."}
    existing = db.query_one(
        "SELECT created_at FROM ratings WHERE target_type=? AND target_id=? AND user_id=?",
        (target_type, target_id, user_id))
    installed = 1 if has_installed(user_id, target_type, target_id) else 0
    db.execute(
        "INSERT INTO ratings(target_type,target_id,user_id,stars,review,installed,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(target_type,target_id,user_id) DO UPDATE SET stars=excluded.stars, "
        "review=excluded.review, installed=excluded.installed, updated_at=excluded.updated_at",
        (target_type, target_id, user_id, stars, text, installed,
         (existing or {}).get("created_at") or now_ts(), now_ts()))
    return {"ok": True, "rating": my_rating(user_id, target_type, target_id),
            "summary": rating_summary(target_type, target_id)}


def delete_rating(user_id: str, target_type: str, target_id: str) -> dict:
    db.execute("DELETE FROM ratings WHERE user_id=? AND target_type=? AND target_id=?",
               (user_id, target_type, target_id))
    return {"ok": True, "summary": rating_summary(target_type, target_id)}


def my_rating(user_id: str, target_type: str, target_id: str) -> dict | None:
    if not user_id:
        return None
    return db.query_one(
        "SELECT stars, review, installed, created_at, updated_at FROM ratings "
        "WHERE target_type=? AND target_id=? AND user_id=?",
        (target_type, target_id, user_id))


def rating_summary(target_type: str, target_id: str) -> dict:
    rows = db.query("SELECT stars, installed FROM ratings WHERE target_type=? AND target_id=?",
                    (target_type, target_id))
    rated = [row for row in rows if row["stars"] > 0]
    distribution = {star: 0 for star in range(1, 6)}
    for row in rated:
        distribution[int(row["stars"])] = distribution.get(int(row["stars"]), 0) + 1
    total = len(rated)
    average = round(sum(int(row["stars"]) for row in rated) / total, 2) if total else 0.0
    return {"count": total, "avg": average, "distribution": distribution,
            "installed_reviews": sum(1 for row in rated if row["installed"])}


def reviews(target_type: str, target_id: str, viewer_id: str = "",
            only_installed: bool = False, limit: int = 50) -> dict:
    query = ("SELECT r.*, u.nickname, u.avatar_color, u.role FROM ratings r "
             "LEFT JOIN users u ON u.id=r.user_id "
             "WHERE r.target_type=? AND r.target_id=? AND r.stars > 0")
    params: list = [target_type, target_id]
    if only_installed:
        query += " AND r.installed=1"
    query += " ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(query, params)
    items = []
    for row in rows:
        items.append({
            "user_id": row["user_id"],
            "nickname": row.get("nickname") or "пользователь",
            "avatar_color": row.get("avatar_color") or "#6b7280",
            "role": row.get("role") or "user",
            "stars": int(row["stars"]),
            "review": row["review"] or "",
            "installed": bool(row["installed"]),
            "created_at": row["created_at"],
            "is_mine": bool(viewer_id and row["user_id"] == viewer_id),
        })
    return {"ok": True, "items": items, "summary": rating_summary(target_type, target_id)}


def has_installed(user_id: str, target_type: str, target_id: str) -> bool:
    """Реальный факт установки: либо событие установки, либо мод уже в библиотеке."""
    if target_type == "mod":
        return bool(db.query_one("SELECT 1 FROM installed WHERE mod_id=?", (target_id,)))
    plan = collections.plan(target_id, user_id)
    if not plan.get("ok"):
        return False
    return bool(plan.get("installed"))
