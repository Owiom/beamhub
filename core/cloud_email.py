"""Вход по email через Supabase (без Google Cloud).

Два запроса к GoTrue: регистрация (`/auth/v1/signup`) и вход по паролю
(`/auth/v1/token?grant_type=password`). Оба работают с публичным anon key.
"""
from __future__ import annotations

import re

from . import cloud

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PASSWORD_MIN = 8


def validate(email: str, password: str) -> str:
    if not EMAIL_RE.match((email or "").strip()):
        return "Похоже, это не email."
    if len(password or "") < PASSWORD_MIN:
        return f"Пароль короче {PASSWORD_MIN} символов."
    return ""


def signup(base: str, key: str, email: str, password: str) -> dict:
    status, payload = cloud._request(f"{base.rstrip('/')}/auth/v1/signup", "POST", key,
                                     body={"email": email.strip(),
                                           "password": password,
                                           "data": {}})
    if status in (200, 201):
        if payload.get("access_token"):
            return {"ok": True, "access_token": payload["access_token"],
                    "refresh_token": payload.get("refresh_token", ""),
                    "user": payload.get("user") or {}}
        # Supabase требует подтверждение почты — вернём понятный признак
        return {"ok": False, "needs_confirm": True,
                "user": payload.get("user") or payload,
                "error": "Аккаунт создан, но почта не подтверждена."}
    message = payload.get("msg") or payload.get("error_description") or payload.get("error")
    if status == 422 and "already" in str(message).lower():
        return {"ok": False, "exists": True,
                "error": "Такой email уже зарегистрирован — войдите паролем."}
    return {"ok": False, "error": str(message or f"ответ {status}")}


def login(base: str, key: str, email: str, password: str) -> dict:
    status, payload = cloud._request(
        f"{base.rstrip('/')}/auth/v1/token?grant_type=password", "POST", key,
        body={"email": email.strip(), "password": password})
    if status == 200 and payload.get("access_token"):
        return {"ok": True, "access_token": payload["access_token"],
                "refresh_token": payload.get("refresh_token", ""),
                "user": payload.get("user") or {}}
    message = payload.get("error_description") or payload.get("msg") or payload.get("error")
    if "confirm" in str(message).lower():
        return {"ok": False, "needs_confirm": True,
                "error": "Почта не подтверждена. Подтвердите её или включите "
                         "«Confirm email = off» в Supabase."}
    return {"ok": False, "error": str(message or "Неверный email или пароль.")}


def nickname_from_email(email: str) -> str:
    local = (email or "").split("@")[0].replace(".", " ").replace("_", " ").strip()
    return (local or "игрок")[:24]
