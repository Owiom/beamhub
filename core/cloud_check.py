"""Проверка провайдера до открытия браузера.

Supabase собирает ссылку на Discord/Google сам, поэтому ошибку в Client ID
видно только после перехода. Мы заранее запрашиваем authorize без редиректа
и смотрим, что уходит провайдеру.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import cloud


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def provider_problem(base: str, key: str, provider: str, redirect_to: str) -> str:
    """Возвращает текст проблемы или пустую строку, если всё выглядит нормально."""
    verifier = cloud.make_verifier()
    url = cloud.authorize_url(base, redirect_to, "check", verifier, provider=provider)
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"apikey": key, "User-Agent": "BeamHub/check"})
    try:
        with opener.open(request, timeout=15) as resp:
            status = resp.status
            location = resp.headers.get("Location", "")
            body = resp.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        location = exc.headers.get("Location", "") if exc.headers else ""
        body = exc.read(400).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""  # сеть недоступна — не мешаем пользователю

    if location:
        parsed = urllib.parse.urlsplit(location)
        params = urllib.parse.parse_qs(parsed.query)
        client_id = (params.get("client_id") or [""])[0]
        if parsed.netloc.endswith("discord.com"):
            if not re.fullmatch(r"\d{15,25}", client_id or ""):
                return (f"В Supabase в поле Client ID для Discord записано «{client_id or 'пусто'}», "
                        "а нужен числовой ID приложения. Возьмите его в Discord Developer Portal: "
                        "OAuth2 → Client ID (число из 18–19 цифр).")
        if parsed.netloc.endswith("accounts.google.com"):
            if not client_id.endswith(".apps.googleusercontent.com"):
                return ("В Supabase в поле Client ID для Google записано "
                        f"«{client_id or 'пусто'}», а нужен ID вида "
                        "1234567890-abc.apps.googleusercontent.com.")
        return ""

    if status >= 400 or body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        message = " ".join(str(value) for value in payload.values()) if payload else body[:160]
        if provider == "discord" and "snowflake" in message.lower():
            return ("Discord не принял Client ID: в Supabase указано не число. "
                    "Вставьте Client ID из Discord Developer Portal → OAuth2.")
        if message:
            return f"Провайдер {provider} ответил: {message[:200]}"
    return ""
