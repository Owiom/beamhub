"""Синхронизация модерации: жалобы и журнал действий.

* push — свои жалобы отправляет любой вошедший пользователь;
  журнал действий отправляют только модераторы;
* pull — очередь жалоб и журнал забирают только модераторы (RLS это же и проверяет);
* решение по жалобе, принятое локально, отправляется в облако через PATCH.
"""
from __future__ import annotations

import json

from . import cloud, cloud_session, config, db
from .util import now_ts


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


def push(user: dict | None = None, token: str | None = None) -> dict:
    """Отправить мои жалобы; модератор — ещё и журнал действий."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    uid = _cloud_uid()
    access = token or cloud_session.access_token()
    local = _local_user(uid) if uid else None
    if user is None:
        user = local
    if not user or not uid or not access:
        return {"ok": False, "error": "Нужен вход через Google."}
    base, key = cloud.settings()

    reports = db.query("SELECT * FROM reports WHERE user_id=?", (user["id"],))
    report_rows = [{
        "id": row["id"], "target_type": row["target_type"], "target_id": row["target_id"],
        "user_id": uid, "reason": row.get("reason") or "other",
        "details": row.get("details") or "", "status": row.get("status") or "open",
    } for row in reports]
    if report_rows:
        _upsert(base, key, "reports", report_rows, access, "id")

    is_staff = user.get("role") in ("moderator", "admin")
    actions = 0
    if is_staff:
        rows = db.query("SELECT * FROM admin_actions WHERE actor_id=? ORDER BY id DESC LIMIT 200",
                        (user["id"],))
        payload = [{
            "actor_id": uid, "action": row["action"],
            "target_type": row.get("target_type") or "",
            "target_id": row.get("target_id") or "", "note": row.get("note") or "",
        } for row in rows]
        if payload:
            _insert(base, key, "admin_actions", payload, access)
            actions = len(payload)
    return {"ok": True, "reports": len(report_rows), "actions": actions}


def pull(user: dict | None = None) -> dict:
    """Забрать очередь жалоб и журнал — только для модераторов."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    base, key = cloud.settings()
    if user is None:
        uid = _cloud_uid()
        user = _local_user(uid) if uid else None
    if not user or user.get("role") not in ("moderator", "admin"):
        return {"ok": True, "reports": 0, "actions": 0, "skipped": "нужны права модератора"}

    reports = _get(base, key,
                   "reports?select=id,target_type,target_id,user_id,reason,details,status,"
                   "resolved_by,resolved_at,created_at&order=created_at.desc&limit=200")
    nicknames = _nicknames(base, key, {row.get("user_id") for row in reports["items"]})
    count = 0
    for row in reports["items"]:
        reporter = _stub_user(row.get("user_id") or "", nicknames.get(row.get("user_id"), ""))
        db.execute(
            "INSERT INTO reports(id,target_type,target_id,user_id,reason,details,status,"
            "resolved_by,resolved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "details=excluded.details",
            (row["id"], row.get("target_type") or "", row.get("target_id") or "", reporter,
             row.get("reason") or "other", row.get("details") or "",
             row.get("status") or "open", "", int(row.get("resolved_at") or 0),
             int(row.get("created_at") or now_ts())))
        count += 1

    actions = _get(base, key,
                   "admin_actions?select=actor_id,action,target_type,target_id,note,created_at"
                   "&order=created_at.desc&limit=200")
    action_count = 0
    for row in actions["items"]:
        actor = _stub_user(row.get("actor_id") or "", "")
        exists = db.query_one(
            "SELECT id FROM admin_actions WHERE actor_id=? AND action=? AND target_id=? "
            "AND created_at=?", (actor, row.get("action") or "", row.get("target_id") or "",
                                 int(row.get("created_at") or 0)))
        if exists:
            continue
        db.execute(
            "INSERT INTO admin_actions(actor_id,action,target_type,target_id,note,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (actor, row.get("action") or "", row.get("target_type") or "",
             row.get("target_id") or "", row.get("note") or "",
             int(row.get("created_at") or now_ts())))
        action_count += 1
    return {"ok": True, "reports": count, "actions": action_count}


def push_resolution(report_id: str, status: str, resolved_by: str = "") -> dict:
    """Отправить решение по жалобе в облако."""
    if not cloud.configured():
        return {"ok": False, "error": "Облако не настроено."}
    uid = _cloud_uid()
    access = cloud_session.access_token()
    if not uid or not access:
        return {"ok": False, "error": "Нужен вход через Google."}
    base, key = cloud.settings()
    payload = {"status": status, "resolved_at": now_ts()}
    if resolved_by:
        payload["resolved_by"] = resolved_by
    return _patch(base, key, f"reports?id=eq.{report_id}", payload, access)


def _upsert(base: str, key: str, table: str, rows: list[dict], token: str,
            on_conflict: str) -> dict:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}",
        data=json.dumps(rows, ensure_ascii=False).encode("utf-8"), method="POST",
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


def _insert(base: str, key: str, table: str, rows: list[dict], token: str) -> dict:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{table}",
        data=json.dumps(rows, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return {"ok": resp.status in (200, 201, 204)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _patch(base: str, key: str, query: str, payload: dict, token: str) -> dict:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{query}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="PATCH",
        headers={"apikey": key, "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return {"ok": resp.status in (200, 204)}
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


def _nicknames(base: str, key: str, uids: set) -> dict[str, str]:
    """Ники авторов. Сначала одним запросом, при неудаче — по одному."""
    clean = [uid for uid in uids if uid]
    if not clean:
        return {}
    result = _get(base, key,
                  "profiles?id=in.(" + ",".join(f'"{uid}"' for uid in clean) + ")"
                  "&select=id,nickname")
    names = {row.get("id"): row.get("nickname") or "" for row in result["items"]}
    for uid in clean:
        if names.get(uid):
            continue
        single = _get(base, key, f"profiles?id=eq.{uid}&select=id,nickname")
        if single["ok"] and single["items"]:
            names[uid] = single["items"][0].get("nickname") or ""
    return names
