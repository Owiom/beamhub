"""Аккаунты: локальные профили, сессии, подписки.

Провайдер авторизации вынесен в отдельную функцию, чтобы позже подключить Google
через Supabase без переделки остального кода: локальный вход нужен, чтобы платформа
работала офлайн и её можно было проверить без внешних сервисов.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid

from . import db
from .util import now_ts

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
NICK_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_\- ]{3,24}$")
ROLES = ("user", "moderator", "admin")
PASSWORD_ITERATIONS = 120_000
AVATAR_COLORS = ("#ff7a1a", "#35c46b", "#3ca0dc", "#c078ff", "#ffc14d", "#ff6b8b", "#4dd0c8")


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"),
                               PASSWORD_ITERATIONS).hex()


def _color_for(seed: str) -> str:
    return AVATAR_COLORS[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(AVATAR_COLORS)]


def _public(user: dict | None) -> dict | None:
    if not user:
        return None
    keys = ("id", "nickname", "bio", "avatar_color", "avatar_url", "role", "created_at",
            "blocked", "provider")
    result = {k: user.get(k) for k in keys}
    result["followers"] = followers_count(user["id"])
    result["following"] = following_count(user["id"])
    return result


def _nickname_taken(nickname: str, except_id: str = "") -> bool:
    row = db.query_one("SELECT id FROM users WHERE lower(nickname)=lower(?)", (nickname,))
    return bool(row and row["id"] != except_id)


def _email_taken(email: str, except_id: str = "") -> bool:
    row = db.query_one("SELECT id FROM users WHERE lower(email)=lower(?)", (email,))
    return bool(row and row["id"] != except_id)


def _maybe_first_admin(user_id: str) -> None:
    """Если это единственный аккаунт — даём админку, чтобы было кому управлять."""
    total = db.query_one("SELECT COUNT(*) AS c FROM users")
    admins = db.query_one("SELECT COUNT(*) AS c FROM users WHERE role='admin'")
    if (total or {}).get("c") == 1 and not (admins or {}).get("c"):
        db.execute("UPDATE users SET role='admin' WHERE id=?", (user_id,))


def register(email: str, password: str, nickname: str) -> dict:
    email = (email or "").strip().lower()
    nickname = (nickname or "").strip()
    if not EMAIL_RE.match(email):
        return {"ok": False, "error": "Некорректный email."}
    if len(password or "") < 8:
        return {"ok": False, "error": "Пароль должен быть не короче 8 символов."}
    if not NICK_RE.match(nickname):
        return {"ok": False, "error": "Никнейм: 3–24 символа, буквы, цифры, пробел, _ и -."}
    if _email_taken(email):
        return {"ok": False, "error": "Этот email уже зарегистрирован."}
    if _nickname_taken(nickname):
        return {"ok": False, "error": "Такой никнейм уже занят."}
    salt = secrets.token_hex(16)
    user_id = "usr_" + uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO users(id,email,nickname,bio,avatar_color,provider,password_hash,"
        "password_salt,role,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, email, nickname, "", _color_for(email), "local",
         _hash_password(password, salt), salt, "user", now_ts(), now_ts()))
    _maybe_first_admin(user_id)
    return {"ok": True, "user": _public(user_by_id(user_id)), "token": create_session(user_id)}


def login(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    row = db.query_one("SELECT * FROM users WHERE lower(email)=?", (email,))
    if not row or row["provider"] != "local":
        return {"ok": False, "error": "Неверный email или пароль."}
    expected = _hash_password(password or "", row["password_salt"])
    if not hmac.compare_digest(expected, row["password_hash"]):
        return {"ok": False, "error": "Неверный email или пароль."}
    if row["blocked"]:
        return {"ok": False, "error": f"Аккаунт заблокирован: {row['blocked_reason'] or 'без причины'}"}
    db.execute("UPDATE users SET last_seen=? WHERE id=?", (now_ts(), row["id"]))
    return {"ok": True, "user": _public(row), "token": create_session(row["id"])}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions(token,user_id,created_at,last_seen) VALUES(?,?,?,?)",
               (token, user_id, now_ts(), now_ts()))
    return token


def logout(token: str) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_by_token(token: str) -> dict | None:
    if not token:
        return None
    row = db.query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?", (token,))
    if not row:
        return None
    if row["blocked"]:
        return None
    db.execute("UPDATE sessions SET last_seen=? WHERE token=?", (now_ts(), token))
    return row


def user_by_id(user_id: str) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE id=?", (user_id,))


def profile(user_id: str) -> dict | None:
    user = user_by_id(user_id)
    if not user:
        return None
    data = _public(user)
    data["stats"] = collection_stats(user_id)
    data["followers"] = followers_count(user_id)
    data["following"] = following_count(user_id)
    return data


def collection_stats(user_id: str) -> dict:
    row = db.query_one(
        "SELECT COUNT(*) AS collections, COALESCE(SUM(views),0) AS views, "
        "COALESCE(SUM(installs),0) AS installs FROM collections WHERE owner_id=? AND published=1",
        (user_id,))
    comments = db.query_one("SELECT COUNT(*) AS c FROM comments WHERE user_id=? AND deleted=0",
                            (user_id,))
    ratings = db.query_one("SELECT COUNT(*) AS c FROM ratings WHERE user_id=?", (user_id,))
    return {"collections": (row or {}).get("collections", 0),
            "views": (row or {}).get("views", 0),
            "installs": (row or {}).get("installs", 0),
            "comments": (comments or {}).get("c", 0),
            "ratings": (ratings or {}).get("c", 0)}


def update_profile(user_id: str, **fields) -> dict:
    allowed = {"nickname", "bio", "avatar_color", "avatar_url"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "nickname" in patch:
        nickname = str(patch["nickname"]).strip()
        if not NICK_RE.match(nickname):
            return {"ok": False, "error": "Никнейм: 3–24 символа, буквы, цифры, пробел, _ и -."}
        if _nickname_taken(nickname, user_id):
            return {"ok": False, "error": "Такой никнейм уже занят."}
        patch["nickname"] = nickname
    if "bio" in patch:
        patch["bio"] = str(patch["bio"])[:400]
    if "avatar_color" in patch and not re.match(r"^#[0-9a-fA-F]{6}$", str(patch["avatar_color"])):
        patch.pop("avatar_color")
    if not patch:
        return {"ok": True, "user": profile(user_id)}
    sets = ",".join(f"{k}=?" for k in patch)
    db.execute(f"UPDATE users SET {sets} WHERE id=?", (*patch.values(), user_id))
    return {"ok": True, "user": profile(user_id)}


def upsert_provider_user(provider: str, provider_id: str, email: str, nickname: str) -> dict:
    """Вход через внешний провайдер (Google). Пароль не нужен."""
    row = db.query_one("SELECT * FROM users WHERE provider=? AND provider_id=?",
                       (provider, provider_id))
    if row:
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (now_ts(), row["id"]))
        return {"ok": True, "user": _public(row), "token": create_session(row["id"])}
    if email and _email_taken(email):
        existing = db.query_one("SELECT * FROM users WHERE lower(email)=?", (email.lower(),))
        if existing:
            db.execute("UPDATE users SET provider=?, provider_id=?, last_seen=? WHERE id=?",
                       (provider, provider_id, now_ts(), existing["id"]))
            return {"ok": True, "user": _public(existing), "token": create_session(existing["id"])}
    base = (nickname or (email or "user").split("@")[0]).strip()[:24] or "user"
    nickname_final = base
    suffix = 1
    while _nickname_taken(nickname_final):
        nickname_final = f"{base[:20]}{suffix}"
        suffix += 1
    user_id = "usr_" + uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO users(id,email,nickname,bio,avatar_color,provider,provider_id,role,"
        "created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user_id, (email or "").lower(), nickname_final, "", _color_for(email or user_id),
         provider, provider_id, "user", now_ts(), now_ts()))
    _maybe_first_admin(user_id)
    return {"ok": True, "user": _public(user_by_id(user_id)), "token": create_session(user_id)}


# --- подписки ---
def follow(follower_id: str, followee_id: str) -> dict:
    if follower_id == followee_id:
        return {"ok": False, "error": "Нельзя подписаться на себя."}
    if not user_by_id(followee_id):
        return {"ok": False, "error": "Пользователь не найден."}
    db.execute("INSERT OR IGNORE INTO follows(follower_id,followee_id,created_at) VALUES(?,?,?)",
               (follower_id, followee_id, now_ts()))
    return {"ok": True, "following": True, "followers": followers_count(followee_id)}


def unfollow(follower_id: str, followee_id: str) -> dict:
    db.execute("DELETE FROM follows WHERE follower_id=? AND followee_id=?",
               (follower_id, followee_id))
    return {"ok": True, "following": False, "followers": followers_count(followee_id)}


def is_following(follower_id: str, followee_id: str) -> bool:
    if not follower_id:
        return False
    return bool(db.query_one("SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?",
                             (follower_id, followee_id)))


def followers_count(user_id: str) -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM follows WHERE followee_id=?", (user_id,))
    return int((row or {}).get("c", 0))


def following_count(user_id: str) -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM follows WHERE follower_id=?", (user_id,))
    return int((row or {}).get("c", 0))


def followers(user_id: str) -> list[dict]:
    rows = db.query(
        "SELECT u.id, u.nickname, u.avatar_color, u.bio FROM follows f "
        "JOIN users u ON u.id = f.follower_id WHERE f.followee_id=? "
        "ORDER BY f.created_at DESC", (user_id,))
    return rows


def following(user_id: str) -> list[dict]:
    rows = db.query(
        "SELECT u.id, u.nickname, u.avatar_color, u.bio FROM follows f "
        "JOIN users u ON u.id = f.followee_id WHERE f.follower_id=? "
        "ORDER BY f.created_at DESC", (user_id,))
    return rows


# --- модерация аккаунтов (этап 5, заготовка) ---
def set_blocked(user_id: str, blocked: bool, reason: str = "", actor_id: str = "") -> dict:
    db.execute("UPDATE users SET blocked=?, blocked_reason=? WHERE id=?",
               (1 if blocked else 0, reason, user_id))
    if blocked:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    db.execute("INSERT INTO admin_actions(actor_id,action,target_type,target_id,note,created_at) "
               "VALUES(?,?,?,?,?,?)",
               (actor_id, "block" if blocked else "unblock", "user", user_id, reason, now_ts()))
    return {"ok": True}


def is_moderator(user: dict | None) -> bool:
    return bool(user and user.get("role") in ("moderator", "admin"))
