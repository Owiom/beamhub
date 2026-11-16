"""Выдать роль пользователю: py tools\\grant_role.py email@example.com admin|moderator|user"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import accounts, db  # noqa: E402

ROLES = ("user", "moderator", "admin")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    email = sys.argv[1].strip().lower()
    role = sys.argv[2].strip().lower()
    if role not in ROLES:
        print("Роль должна быть одной из:", ", ".join(ROLES))
        return 2
    db.init()
    user = db.query_one("SELECT id, nickname, role FROM users WHERE lower(email)=?", (email,))
    if not user:
        print("Пользователь не найден:", email)
        return 1
    db.execute("UPDATE users SET role=? WHERE id=?", (role, user["id"]))
    db.execute("INSERT INTO admin_actions(actor_id,action,target_type,target_id,note,created_at)"
               " VALUES(?,?,?,?,?,?)",
               (user["id"], "set_role", "user", user["id"], role, db.now_ts()))
    print(f"{user['nickname']} ({email}): {user['role']} -> {role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
