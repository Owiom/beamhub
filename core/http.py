"""Аккуратный HTTP-клиент: gzip, кодировки, повторы, ограничение частоты запросов."""
from __future__ import annotations

import gzip
import io
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

from .util import UA

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_min_interval = 0.25  # секунд между запросами к одному хосту


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: dict
    raw: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _decode(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    encodings = []
    if match:
        encodings.append(match.group(1))
    encodings += ["utf-8", "cp1251"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def fetch(url: str, referer: str | None = None, timeout: int = 30, retries: int = 2,
          min_interval: float | None = None, allow_404: bool = False) -> Response:
    host = urllib.parse.urlsplit(url).netloc
    interval = _min_interval if min_interval is None else min_interval

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        with _lock:
            wait = (_last_hit.get(host, 0.0) + interval) - time.time()
            if wait > 0:
                time.sleep(min(wait, 2.0))
            _last_hit[host] = time.time()

        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if referer:
            headers["Referer"] = referer

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_CTX) as resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if encoding == "gzip":
                    raw = gzip.decompress(raw)
                elif encoding == "deflate":
                    raw = zlib.decompress(raw)
                return Response(
                    url=resp.geturl(),
                    status=resp.status,
                    text=_decode(raw, resp.headers.get("Content-Type", "")),
                    headers=dict(resp.headers),
                    raw=raw,
                )
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return Response(url=url, status=404, text="", headers={}, raw=b"")
            last_error = exc
            if exc.code in (403, 404, 410, 451):
                break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Не удалось загрузить {url}: {last_error}")


def fetch_bytes(url: str, referer: str | None = None, timeout: int = 40,
                safe: bool = False) -> bytes:
    """Забрать двоичные данные. safe=True — только публичные адреса и без SSRF-редиректов."""
    from . import security

    if safe:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise RuntimeError("Разрешены только http(s) адреса")
        if not security.resolve_is_public(parts.hostname or ""):
            raise RuntimeError("Адрес ведёт в локальную сеть")

    headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
               "Accept-Encoding": "gzip"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SafeRedirect()) if safe else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as resp:
        raw = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Редирект на localhost/приватный адрес — это SSRF, обрываем."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        from . import security

        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme not in ("http", "https") or not security.resolve_is_public(
                parts.hostname or ""):
            raise urllib.error.HTTPError(newurl, code, "redirect to private address", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
