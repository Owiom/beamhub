"""Облако: Supabase (Auth + REST) для аккаунтов Google и общих данных.

Пока ключи не заданы, приложение работает локально: `configured()` возвращает False,
и весь облачный код просто не вызывается. Логика разбита на маленькие функции,
которые принимают url/ключ явно — так их можно проверить на фейковом сервере.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config
from .version import VERSION

TIMEOUT = 20
UA = f"BeamHub/{VERSION}"


def settings() -> tuple[str, str]:
    """Свои настройки важнее вшитых владельцем; вшитые — важнее пустых."""
    from . import cloud_defaults

    cfg = config.load()
    url = (cfg.get("cloud_url") or "").strip().rstrip("/")
    key = (cfg.get("cloud_anon_key") or "").strip()
    if url and key:
        return url, key
    baked_url, baked_key = cloud_defaults.load()
    return (baked_url, baked_key) if baked_url and baked_key else (url, key)


def configured() -> bool:
    url, key = settings()
    if not key or not url:
        return False
    if url.startswith("https://"):
        return True
    # http разрешаем только для локального тестового сервера
    return url.startswith("http://127.0.0.1") or url.startswith("http://localhost")


def _headers(key: str, token: str = "") -> dict:
    headers = {"apikey": key, "Content-Type": "application/json",
               "User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(url: str, method: str = "GET", key: str = "", token: str = "",
             body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=_headers(key, token))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return exc.code, payload


# ---------------------------------------------------------------- PKCE
def make_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(base: str, redirect_to: str, state: str, verifier: str,
                  provider: str = "google") -> str:
    """Ссылка на вход через провайдера (Supabase GoTrue, поток PKCE)."""
    params = {
        "provider": provider,
        "redirect_to": redirect_to,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "s256",
        "state": state,
    }
    return f"{base.rstrip('/')}/auth/v1/authorize?" + urllib.parse.urlencode(params)


def exchange_code(base: str, key: str, code: str, verifier: str) -> dict:
    """Обменять код на сессию. Возвращает {ok, access_token, refresh_token, user}."""
    status, payload = _request(
        f"{base.rstrip('/')}/auth/v1/token?grant_type=pkce", "POST", key,
        body={"auth_code": code, "code_verifier": verifier})
    if status != 200 or not payload.get("access_token"):
        return {"ok": False, "error": payload.get("error_description")
                or payload.get("msg") or f"ответ {status}"}
    return {"ok": True, "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", ""),
            "expires_in": payload.get("expires_in", 3600),
            "user": payload.get("user") or {}}


def refresh(base: str, key: str, refresh_token: str) -> dict:
    status, payload = _request(
        f"{base.rstrip('/')}/auth/v1/token?grant_type=refresh_token", "POST", key,
        body={"refresh_token": refresh_token})
    if status != 200 or not payload.get("access_token"):
        return {"ok": False, "error": payload.get("error_description") or f"ответ {status}"}
    return {"ok": True, "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", refresh_token),
            "user": payload.get("user") or {}}


def me(base: str, key: str, access_token: str) -> dict:
    status, payload = _request(f"{base.rstrip('/')}/auth/v1/user", "GET", key,
                               token=access_token)
    if status != 200:
        return {"ok": False, "error": payload.get("msg") or f"ответ {status}"}
    return {"ok": True, "user": payload}


def nickname_from(user: dict) -> str:
    meta = user.get("user_metadata") or {}
    for key in ("full_name", "name", "preferred_username", "user_name"):
        value = (meta.get(key) or "").strip()
        if value:
            return value[:24]
    email = (user.get("email") or "").split("@")[0]
    return (email or "user")[:24]


def picture_from(user: dict) -> str:
    meta = user.get("user_metadata") or {}
    return (meta.get("avatar_url") or meta.get("picture") or "")[:500]


# ---------------------------------------------------------------- данные
def rest_select(base: str, key: str, table: str, params: dict, token: str = "") -> dict:
    query = urllib.parse.urlencode(params)
    status, payload = _request(f"{base.rstrip('/')}/rest/v1/{table}?{query}", "GET", key,
                               token=token)
    return {"ok": status in (200, 206), "items": payload if isinstance(payload, list) else [],
            "status": status, "error": payload if not isinstance(payload, list) else ""}


def rest_insert(base: str, key: str, table: str, row: dict, token: str = "") -> dict:
    status, payload = _request(f"{base.rstrip('/')}/rest/v1/{table}", "POST", key, token,
                               body=row)
    return {"ok": status in (200, 201, 204), "items": payload if isinstance(payload, list)
            else [payload], "status": status}


class SessionStore:
    """Короткоживущие состояния OAuth (state/verifier) — в памяти процесса."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def add(self, state: str, verifier: str, redirect_to: str) -> None:
        self._items[state] = {"verifier": verifier, "redirect_to": redirect_to,
                              "created": time.time()}

    def pop(self, state: str) -> dict | None:
        item = self._items.pop(state, None)
        if not item:
            return None
        if time.time() - item["created"] > 600:
            return None
        return item

    def clear(self) -> None:
        self._items.clear()
