"""Модерация: жалобы, очередь, блокировки и журнал действий.

Роли: user < moderator < admin. Проверка прав — на сервере, не только в интерфейсе.
Все действия с чужим контентом пишутся в admin_actions.
"""
from __future__ import annotations

import uuid

from . import accounts, db, social
from .util import now_ts

TARGETS = ("comment", "collection", "user", "mod")
REASONS = ("spam", "abuse", "adult", "offtopic", "copyright", "other")
REPORT_LIMIT = 10
REPORT_WINDOW = 3600


def _rid() -> str:
    return "rpt_" + uuid.uuid4().hex[:12]


def _target_exists(target_type: str, target_id: str) -> bool:
    if target_type == "comment":
        return bool(db.query_one("SELECT 1 FROM comments WHERE id=?", (target_id,)))
    if target_type == "collection":
        return bool(db.query_one("SELECT 1 FROM collections WHERE id=?", (target_id,)))
    if target_type == "user":
        return bool(db.query_one("SELECT 1 FROM users WHERE id=?", (target_id,)))
    if target_type == "mod":
        return bool(db.query_one("SELECT 1 FROM mods WHERE id=?", (target_id,)))
    return False


def report(user_id: str, target_type: str, target_id: str, reason: str = "other",
           details: str = "") -> dict:
    if target_type not in TARGETS:
        return {"ok": False, "error": "Неизвестный тип жалобы."}
    if reason not in REASONS:
        reason = "other"
    if not _target_exists(target_type, target_id):
        return {"ok": False, "error": "Объект не найден."}
    if not social._allow(f"report:{user_id}", REPORT_LIMIT, REPORT_WINDOW):
        return {"ok": False, "error": "Слишком много жалоб за час."}
    duplicate = db.query_one(
        "SELECT id FROM reports WHERE user_id=? AND target_type=? AND target_id=? "
        "AND status='open'", (user_id, target_type, target_id))
    if duplicate:
        return {"ok": False, "error": "Жалоба на этот объект уже отправлена."}
    report_id = _rid()
    db.execute(
        "INSERT INTO reports(id,target_type,target_id,user_id,reason,details,status,created_at)"
        " VALUES(?,?,?,?,?,?, 'open', ?)",
        (report_id, target_type, target_id, user_id, reason, (details or "")[:1000], now_ts()))
    return {"ok": True, "id": report_id}


def list_reports(status: str = "open", limit: int = 100) -> dict:
    where = "WHERE r.status=?" if status else ""
    params: list = [status] if status else []
    rows = db.query(
        f"SELECT r.*, u.nickname AS reporter_nickname FROM reports r "
        f"LEFT JOIN users u ON u.id=r.user_id {where} ORDER BY r.created_at LIMIT ?",
        [*params, limit])
    items = []
    for row in rows:
        item = dict(row)
        item["preview"] = _preview(row["target_type"], row["target_id"])
        item["target_user"] = _target_author(row["target_type"], row["target_id"])
        items.append(item)
    return {"ok": True, "items": items,
            "open": db.query_one("SELECT COUNT(*) AS c FROM reports WHERE status='open'")["c"]}


def _preview(target_type: str, target_id: str) -> str:
    if target_type == "comment":
        row = db.query_one("SELECT body FROM comments WHERE id=?", (target_id,))
        return ((row or {}).get("body") or "")[:160]
    if target_type == "collection":
        row = db.query_one("SELECT title FROM collections WHERE id=?", (target_id,))
        return ((row or {}).get("title") or "")[:160]
    if target_type == "user":
        row = db.query_one("SELECT nickname FROM users WHERE id=?", (target_id,))
        return ((row or {}).get("nickname") or "")
    if target_type == "mod":
        row = db.query_one("SELECT title FROM mods WHERE id=?", (target_id,))
        return ((row or {}).get("title") or "")[:160]
    return ""


def _target_author(target_type: str, target_id: str) -> dict | None:
    if target_type == "comment":
        row = db.query_one("SELECT user_id FROM comments WHERE id=?", (target_id,))
    elif target_type == "collection":
        row = db.query_one("SELECT owner_id AS user_id FROM collections WHERE id=?", (target_id,))
    elif target_type == "user":
        row = {"user_id": target_id}
    else:
        return None
    if not row:
        return None
    user = db.query_one("SELECT id, nickname, blocked FROM users WHERE id=?", (row["user_id"],))
    return user


def resolve(actor: dict, report_id: str, action: str = "resolve", note: str = "") -> dict:
    if not accounts.is_moderator(actor):
        return {"ok": False, "error": "Нужны права модератора."}
    row = db.query_one("SELECT * FROM reports WHERE id=?", (report_id,))
    if not row:
        return {"ok": False, "error": "Жалоба не найдена."}
    status = "resolved" if action in ("resolve", "resolve_delete") else "rejected"
    db.execute("UPDATE reports SET status=?, resolved_by=?, resolved_at=? WHERE id=?",
               (status, actor["id"], now_ts(), report_id))
    remove = action == "resolve_delete"
    if remove:
        _remove_target(actor, row["target_type"], row["target_id"])
    _log(actor["id"], f"report_{'resolve' if status == 'resolved' else 'reject'}",
         row["target_type"], row["target_id"], note)
    return {"ok": True, "status": status, "removed": remove}


def _remove_target(actor: dict, target_type: str, target_id: str) -> None:
    if target_type == "comment":
        social.delete_comment(actor["id"], target_id, True)
    elif target_type == "collection":
        db.execute("UPDATE collections SET published=0 WHERE id=?", (target_id,))
        _log(actor["id"], "unpublish_collection", "collection", target_id, "")


def _log(actor_id: str, action: str, target_type: str, target_id: str, note: str = "") -> None:
    db.execute("INSERT INTO admin_actions(actor_id,action,target_type,target_id,note,created_at)"
               " VALUES(?,?,?,?,?,?)",
               (actor_id, action, target_type, target_id, (note or "")[:500], now_ts()))


def block_user(actor: dict, user_id: str, blocked: bool = True, reason: str = "") -> dict:
    if not accounts.is_moderator(actor):
        return {"ok": False, "error": "Нужны права модератора."}
    target = accounts.user_by_id(user_id)
    if not target:
        return {"ok": False, "error": "Пользователь не найден."}
    if target["role"] == "admin" and actor["role"] != "admin":
        return {"ok": False, "error": "Администратора может блокировать только администратор."}
    accounts.set_blocked(user_id, blocked, reason, actor["id"])
    return {"ok": True, "blocked": blocked}


def log(limit: int = 100) -> dict:
    rows = db.query(
        "SELECT a.*, u.nickname AS actor_nickname FROM admin_actions a "
        "LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT ?", (limit,))
    return {"ok": True, "items": rows}


def overview() -> dict:
    open_reports = db.query_one("SELECT COUNT(*) AS c FROM reports WHERE status='open'")["c"]
    blocked = db.query_one("SELECT COUNT(*) AS c FROM users WHERE blocked=1")["c"]
    moderators = db.query_one(
        "SELECT COUNT(*) AS c FROM users WHERE role IN ('moderator','admin')")["c"]
    actions_today = db.query_one(
        "SELECT COUNT(*) AS c FROM admin_actions WHERE created_at > strftime('%s','now') - 86400"
    )["c"]
    return {"open_reports": open_reports, "blocked_users": blocked,
            "moderators": moderators, "actions_24h": actions_today}
