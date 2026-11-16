"""Синхронизация комментариев и оценок с облаком.

Логика та же, что у сборок: свои записи отправляем, чужие забираем.
id комментариев текстовые (`cmt_...`) и совпадают в локали и облаке,
поэтому повторная синхронизация обновляет, а не дублирует.
"""
from __future__ import annotations

import json

from . import cloud, cloud_session, config, db
from .util import now_ts

MAX_TARGETS = 100


def _cloud_uid() -> str:
    return cloud_session.cloud_uid() or (config.load().get("cloud_uid") or "")


def _local_user(uid: str) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE provider='google' AND provider_id=?", (uid,))


def _stub_user(uid: str, nickname: str = "") -> str:
    stub_id = "usr_cloud_" + (uid or "").replace("-", "")[:10]
    name = (nickname or "пользователь облака")[:24]
    db.execute(
        "INSERT INTO users(id,nickname,provider,provider_id,role,created_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname "
        "WHERE excluded.nickname <> 'пользователь облака' OR users.nickname = ''",
        (stub_id, name, "cloud", uid, "user", now_ts()))
    return stub_id


def push(token: str | None = None) -> dict:
    """Отправить мои комментарии и оценки по облачным сборкам."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    uid = _cloud_uid()
    access = token or cloud_session.access_token()
    if not uid or not access:
        return {"ok": False, "error": "Войдите через Google — тогда отзывы уйдут в облако."}
    user = _local_user(uid)
    if not user:
        return {"ok": False, "error": "Локальный аккаунт не связан с облаком."}
    base, key = cloud.settings()

    comments = db.query(
        "SELECT * FROM comments WHERE user_id=? AND deleted=0", (user["id"],))
    comment_rows = [{
        "id": row["id"], "target_type": row["target_type"], "target_id": row["target_id"],
        "user_id": uid, "parent_id": row.get("parent_id") or None,
        "body": row["body"], "deleted": False,
    } for row in comments]
    if comment_rows:
        _upsert(base, key, "comments", comment_rows, access, "id")

    ratings = db.query("SELECT * FROM ratings WHERE user_id=? AND stars > 0",
                       (user["id"],))
    rating_rows = [{
        "target_type": row["target_type"], "target_id": row["target_id"], "user_id": uid,
        "stars": int(row["stars"]), "review": row.get("review") or "",
        "installed": bool(row.get("installed")),
    } for row in ratings]
    if rating_rows:
        _upsert(base, key, "ratings", rating_rows, access,
                "target_type,target_id,user_id")

    return {"ok": True, "comments": len(comment_rows), "ratings": len(rating_rows)}


def pull() -> dict:
    """Забрать чужие комментарии и оценки по сборкам, которые есть локально."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    base, key = cloud.settings()
    ids = [row["id"] for row in db.query(
        "SELECT id FROM collections WHERE published=1 OR COALESCE(remote,0)=1 "
        "ORDER BY updated_at DESC LIMIT ?", (MAX_TARGETS,))]
    if not ids:
        return {"ok": True, "comments": 0, "ratings": 0}
    marks = ",".join(f'"{cid}"' for cid in ids)

    comments = _get(base, key,
                    f"comments?target_type=eq.collection&target_id=in.({marks})"
                    f"&select=id,target_type,target_id,user_id,parent_id,body,deleted,created_at")
    nicknames = _nicknames(base, key, {row.get("user_id") for row in comments["items"]})
    comment_count = 0
    for row in comments["items"]:
        if row.get("deleted"):
            continue
        stub = _stub_user(row.get("user_id") or "", nicknames.get(row.get("user_id"), ""))
        db.execute(
            "INSERT INTO comments(id,target_type,target_id,user_id,parent_id,body,created_at,"
            "updated_at,deleted) VALUES(?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT(id) DO UPDATE SET body=excluded.body, "
            "parent_id=excluded.parent_id",
            (row["id"], row.get("target_type") or "collection", row.get("target_id") or "",
             stub, row.get("parent_id") or "", row.get("body") or "",
             int(row.get("created_at") or now_ts()), now_ts()))
        comment_count += 1

    ratings = _get(base, key,
                   f"ratings?target_type=eq.collection&target_id=in.({marks})"
                   f"&select=target_type,target_id,user_id,stars,review,installed,created_at")
    nicknames.update(_nicknames(base, key, {row.get("user_id") for row in ratings["items"]}))
    rating_count = 0
    for row in ratings["items"]:
        if not row.get("stars"):
            continue
        stub = _stub_user(row.get("user_id") or "", nicknames.get(row.get("user_id"), ""))
        db.execute(
            "INSERT INTO ratings(target_type,target_id,user_id,stars,review,installed,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(target_type,target_id,user_id) DO UPDATE SET stars=excluded.stars, "
            "review=excluded.review, installed=excluded.installed",
            (row.get("target_type") or "collection", row.get("target_id") or "", stub,
             int(row["stars"]), row.get("review") or "", 1 if row.get("installed") else 0,
             int(row.get("created_at") or now_ts()), now_ts()))
        rating_count += 1
    return {"ok": True, "comments": comment_count, "ratings": rating_count}


def _nicknames(base: str, key: str, uids: set) -> dict[str, str]:
    clean = [uid for uid in uids if uid]
    if not clean:
        return {}
    marks = ",".join(f'"{uid}"' for uid in clean)
    result = _get(base, key, f"profiles?id=in.({marks})&select=id,nickname")
    return {row.get("id"): row.get("nickname") or "" for row in result["items"]}


def _upsert(base: str, key: str, table: str, rows: list[dict], token: str,
            on_conflict: str) -> dict:
    import urllib.error
    import urllib.request

    data = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    url = f"{base.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}"
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return {"ok": resp.status in (200, 201, 204)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _get(base: str, key: str, query: str) -> dict:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{query}",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "items": payload if isinstance(payload, list) else []}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:200],
                "items": []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__, "items": []}
