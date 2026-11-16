"""Удаляет демо-аккаунты и их данные из реальной базы (после тестов интерфейса)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402

EMAILS = ["author@example.com", "fan@example.com", "drift@example.com",
          "drift2@example.com", "demo@example.com", "smoke@example.com"]

db.init()
users = db.query(
    f"SELECT id, email, nickname FROM users WHERE lower(email) IN ({','.join('?' for _ in EMAILS)})",
    [email.lower() for email in EMAILS])
print("найдено демо-пользователей:", len(users))
for user in users:
    collections = db.query("SELECT id FROM collections WHERE owner_id=?", (user["id"],))
    for coll in collections:
        db.execute("DELETE FROM collection_items WHERE collection_id=?", (coll["id"],))
        db.execute("DELETE FROM collection_versions WHERE collection_id=?", (coll["id"],))
        db.execute("DELETE FROM comments WHERE target_type='collection' AND target_id=?",
                   (coll["id"],))
        db.execute("DELETE FROM ratings WHERE target_type='collection' AND target_id=?",
                   (coll["id"],))
        db.execute("DELETE FROM collections WHERE id=?", (coll["id"],))
    db.execute("DELETE FROM comments WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM ratings WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM follows WHERE follower_id=? OR followee_id=?", (user["id"], user["id"]))
    db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM users WHERE id=?", (user["id"],))
    print("  удалён:", user["nickname"], user["email"])

tokens = db.query("SELECT token FROM sessions WHERE user_id NOT IN (SELECT id FROM users)")
for row in tokens:
    db.execute("DELETE FROM sessions WHERE token=?", (row["token"],))
print("осталось пользователей:", db.query_one("SELECT COUNT(*) AS c FROM users")["c"])
print("осталось сборок:", db.query_one("SELECT COUNT(*) AS c FROM collections")["c"])
