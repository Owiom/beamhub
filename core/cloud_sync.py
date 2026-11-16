"""Синхронизация сборок с Supabase.

Направления:
  * push — отправить мои опубликованные сборки в облако (нужен вход через Google);
  * pull — забрать опубликованные сборки других авторов в локальную базу.

Сборки опознаются одним и тем же текстовым id (`col_...`), поэтому повторная
синхронизация обновляет запись, а не плодит копии.
"""
from __future__ import annotations

import json

from . import cloud, collections, config, db
from .util import now_ts

PULL_LIMIT = 60


def _cloud_uid() -> str:
    """uuid пользователя Supabase, сохранённый при входе через Google."""
    return (config.load().get("cloud_uid") or "").strip()


def status() -> dict:
    return {"configured": cloud.configured(), "signed_in": bool(_cloud_uid()),
            "cloud_uid": _cloud_uid(),
            "cloud_url": cloud.settings()[0]}


def push(token: str | None = None) -> dict:
    """Отправить мои локальные опубликованные сборки в облако."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    uid = _cloud_uid()
    if not uid:
        return {"ok": False, "error": "Войдите через Google — тогда сборки уйдут в облако."}
    base, key = cloud.settings()
    access = token or _access_token()
    if not access:
        return {"ok": False, "error": "Нет токена облака — войдите заново."}

    local_user = db.query_one(
        "SELECT id FROM users WHERE provider='google' AND provider_id=?", (uid,))
    if not local_user:
        return {"ok": False, "error": "Локальный аккаунт не связан с облаком."}

    mine = db.query(
        "SELECT * FROM collections WHERE owner_id=? AND published=1 AND COALESCE(remote,0)=0",
        (local_user["id"],))
    pushed = 0
    errors = []
    for coll in mine:
        row = {
            "id": coll["id"],
            "owner_id": uid,
            "title": coll["title"],
            "description": coll.get("description") or "",
            "cover": coll.get("cover") or "",
            "category": coll.get("category") or "other",
            "tags": json.loads(coll.get("tags") or "[]"),
            "published": True,
            "game_version": coll.get("game_version") or "",
            "version": int(coll.get("version") or 1),
            "views": int(coll.get("views") or 0),
            "installs": int(coll.get("installs") or 0),
            "downloads": int(coll.get("downloads") or 0),
        }
        result = _upsert(base, key, "collections", row, access)
        if not result["ok"]:
            errors.append(f"{coll['title']}: {result.get('error')}")
            continue
        _replace_items(base, key, coll["id"], collections.items(coll["id"]), access)
        pushed += 1
    return {"ok": True, "pushed": pushed, "total": len(mine), "errors": errors}


def _access_token() -> str:
    from . import cloud_session

    return cloud_session.access_token()


def _upsert(base: str, key: str, table: str, row: dict, token: str) -> dict:
    import urllib.error
    import urllib.request

    data = json.dumps([row], ensure_ascii=False).encode("utf-8")
    url = f"{base.rstrip('/')}/rest/v1/{table}?on_conflict=id"
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


def _replace_items(base: str, key: str, coll_id: str, items: list[dict], token: str) -> None:
    import urllib.error
    import urllib.request

    headers = {"apikey": key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    # удалить прежний состав и записать новый — проще и предсказуемее, чем diff
    delete_url = f"{base.rstrip('/')}/rest/v1/collection_items?collection_id=eq.{coll_id}"
    try:
        request = urllib.request.Request(delete_url, method="DELETE", headers=headers)
        with urllib.request.urlopen(request, timeout=20):
            pass
    except Exception:  # noqa: BLE001
        pass
    if not items:
        return
    rows = [{"collection_id": coll_id, "mod_id": item["mod_id"],
             "position": index, "note": item.get("note") or ""}
            for index, item in enumerate(items)]
    request = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/collection_items",
        data=json.dumps(rows, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={**headers, "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            pass
    except Exception:  # noqa: BLE001
        pass


def pull(limit: int = PULL_LIMIT) -> dict:
    """Забрать опубликованные сборки из облака в локальную базу (только чтение)."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    base, key = cloud.settings()
    select = ("id,owner_id,title,description,cover,category,tags,version,views,installs,"
              "downloads,game_version,created_at")
    query = (f"collections?published=eq.true&select={select}"
             f"&order=created_at.desc&limit={int(limit)}")
    result = _get(base, key, query)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error", "нет ответа")}
    rows = result["items"]
    if not rows:
        return {"ok": True, "pulled": 0, "total": 0}

    ids = [row["id"] for row in rows]
    marks = ",".join(f'"{cid}"' for cid in ids)
    items_result = _get(base, key,
                        f"collection_items?collection_id=in.({marks})"
                        f"&select=collection_id,mod_id,position,note&order=position")
    by_collection: dict[str, list[dict]] = {}
    for item in items_result["items"]:
        by_collection.setdefault(item["collection_id"], []).append(item)

    pulled = 0
    now = now_ts()
    for row in rows:
        owner_uid = row.get("owner_id") or ""
        stub_id = "usr_cloud_" + owner_uid.replace("-", "")[:10]
        nickname = _owner_nickname(base, key, owner_uid) or "автор из облака"
        db.execute(
            "INSERT INTO users(id,nickname,provider,provider_id,role,created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (stub_id, nickname[:24], "cloud", owner_uid, "user", now))
        db.execute(
            "INSERT INTO collections(id,owner_id,title,description,cover,category,tags,"
            "published,game_version,version,views,installs,downloads,created_at,updated_at,remote) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, cover=excluded.cover, "
            "category=excluded.category, tags=excluded.tags, version=excluded.version, "
            "views=excluded.views, installs=excluded.installs, updated_at=excluded.updated_at",
            (row["id"], stub_id, row["title"], row.get("description") or "",
             row.get("cover") or "", row.get("category") or "other",
             json.dumps(row.get("tags") or [], ensure_ascii=False), 1,
             row.get("game_version") or "", int(row.get("version") or 1),
             int(row.get("views") or 0), int(row.get("installs") or 0),
             int(row.get("downloads") or 0), now, now))
        db.execute("DELETE FROM collection_items WHERE collection_id=?", (row["id"],))
        for item in by_collection.get(row["id"], []):
            db.execute(
                "INSERT OR IGNORE INTO collection_items(collection_id,mod_id,position,note,"
                "added_at) VALUES(?,?,?,?,?)",
                (row["id"], item["mod_id"], int(item.get("position") or 0),
                 item.get("note") or "", now))
        pulled += 1
    return {"ok": True, "pulled": pulled, "total": len(rows)}


def _get(base: str, key: str, query: str) -> dict:
    import urllib.error
    import urllib.request

    url = f"{base.rstrip('/')}/rest/v1/{query}"
    request = urllib.request.Request(
        url, headers={"apikey": key, "Authorization": f"Bearer {key}",
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


def _owner_nickname(base: str, key: str, owner_uid: str) -> str:
    if not owner_uid:
        return ""
    result = _get(base, key, f"profiles?id=eq.{owner_uid}&select=nickname")
    if result["ok"] and result["items"]:
        return result["items"][0].get("nickname") or ""
    return ""
