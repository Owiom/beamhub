"""Защита локального API.

Модель угроз: сервер слушает 127.0.0.1, но до него дотягивается браузер.
Значит опасны две вещи:
  * CSRF — чужая страница шлёт POST на localhost (создать/удалить/поменять настройки);
  * DNS rebinding — чужой домен резолвится в 127.0.0.1, и страница становится
    «тем же источником», читая ответы API.

Поэтому: строгая проверка Host, Origin, Content-Type и одноразовый токен на POST.
Токен знает только наше окно (в процессе) и наша же HTML-страница.
"""
from __future__ import annotations

import ipaddress
import secrets

TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def token() -> str:
    return TOKEN


def host_allowed(host_header: str | None) -> bool:
    """Host должен быть локальным: 127.0.0.1:8787, localhost:8787, [::1]:8787."""
    if not host_header:
        return False
    value = host_header.strip().lower()
    if value.startswith("["):
        host = value[1:value.find("]")] if "]" in value else value
    else:
        host = value.rsplit(":", 1)[0] if ":" in value else value
    return host in ALLOWED_HOSTS


def origin_allowed(origin: str | None, port: int) -> bool:
    """Origin либо отсутствует (не браузер), либо наш собственный."""
    if origin is None or origin == "null":
        return False if origin == "null" else True
    value = origin.strip().lower()
    return value in {f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                     f"http://[::1]:{port}"}


def token_ok(value: str | None) -> bool:
    return bool(value) and secrets.compare_digest(value, TOKEN)


def safe_web_url(url: str) -> bool:
    """Разрешаем открывать только http(s) и mailto — никаких file:/ms-msdt:."""
    value = (url or "").strip().lower()
    return value.startswith(("http://", "https://", "mailto:"))


def is_public_host(host: str) -> bool:
    """Хост ведёт в публичную сеть (защита от SSRF к localhost и LAN)."""
    if not host:
        return False
    host = host.strip().strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() in {"localhost", "localhost.localdomain"}:
            return False
        return True  # домен проверим по резолву отдельно
    return not (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified)


def resolve_is_public(host: str) -> bool:
    """Резолвим имя и проверяем все адреса (DNS может указывать на 127.0.0.1)."""
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            address = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            return False
    return True
