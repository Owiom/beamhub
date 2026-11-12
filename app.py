"""BeamHub — локальный лаунчер-каталог модов для BeamNG.drive.

Запуск:  py app.py            (откроет браузер)
         py app.py --no-browser --port 8787
         BeamHub.exe          (собранная версия, см. build_exe.py)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _startup_failure(details: str) -> None:
    """Ошибка до запуска сервера: показать её, даже если консоли нет."""
    print(details)
    target = None
    for folder in (os.environ.get("LOCALAPPDATA"), os.environ.get("TEMP"), str(Path.home())):
        if not folder:
            continue
        try:
            target = Path(folder) / "BeamHub-startup-error.txt"
            target.write_text(details, encoding="utf-8")
            break
        except OSError:
            target = None
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "BeamHub не смог запуститься.\n\n" + details.strip().splitlines()[-1][:300] +
            (f"\n\nПодробности: {target}" if target else ""),
            "BeamHub", 0x10)
    except Exception:  # noqa: BLE001
        pass


try:
    from core import config, db, enrich, installer, security, updater  # noqa: E402
    from core.version import VERSION  # noqa: E402
    from core.providers import CATALOG_PROVIDERS, SOURCE_LABELS, modsgaming  # noqa: E402
    from core.providers import generic, playground  # noqa: E402
    from core.state import HUB, open_in_browser  # noqa: E402
    from core.util import CATEGORY_LABELS, CATEGORY_ORDER, human_ago, human_date, human_size, is_game_running  # noqa: E402
except Exception:  # noqa: BLE001
    _startup_failure(traceback.format_exc())
    raise SystemExit(1)

try:  # окно-лаунчер не критично: без tkinter приложение всё равно работает
    from core import launcher_ui  # noqa: E402
except Exception as _gui_error:  # noqa: BLE001
    print(f"[beamhub] окно недоступно: {_gui_error}")
    launcher_ui = None

try:  # встроенное окно на WebView2 — основной режим для exe
    from core import appwindow  # noqa: E402
except Exception as _desktop_error:  # noqa: BLE001
    print(f"[beamhub] окно приложения недоступно: {_desktop_error}")
    appwindow = None

try:  # каталог в родном окне (tkinter) — основной режим
    from core import tkui  # noqa: E402
except Exception as _tk_error:  # noqa: BLE001
    print(f"[beamhub] родное окно недоступно: {_tk_error}")
    tkui = None

WEB_DIR = config.APP_DIR / "web"
CACHE_DIR = config.DATA_DIR / "img-cache"
SEED_DB = config.APP_DIR / "seed" / "beamhub.db"
_IMG_FAILED: dict[str, float] = {}


# ---------------------------------------------------------------- helpers
def json_default(value):
    return str(value)


def mod_payload(mod: dict, favorites: set[str] | None = None,
                dups: dict | None = None) -> dict:
    installed = db.installed_for_mod(mod["id"]) if mod.get("id") else []
    installed_active = any(item.get("active") for item in installed)
    is_fav = (mod["id"] in favorites) if favorites is not None else False
    key = mod.get("title_key") or ""
    info = (dups or {}).get(key) or {}
    same_count = int(info.get("count") or 0)
    return {
        "id": mod["id"],
        "favorite": is_fav,
        "duplicate": same_count > 1,
        "duplicate_count": same_count,
        "duplicate_sources": info.get("sources") or [],
        "source": mod.get("source", ""),
        "title": mod.get("title") or "Без названия",
        "category": mod.get("category") or "other",
        "category_label": CATEGORY_LABELS.get(mod.get("category") or "other", "Другое"),
        "version": mod.get("version") or "",
        "game_version": mod.get("game_version") or "",
        "size_text": mod.get("size_text") or human_size(mod.get("size_bytes") or 0),
        "image": mod.get("image") or "",
        "author": mod.get("author") or "",
        "description": mod.get("description") or "",
        "published": mod.get("published") or 0,
        "published_text": human_date(mod.get("published")),
        "ago": human_ago(mod.get("published")),
        "downloads": mod.get("downloads") or 0,
        "url": mod.get("url") or "",
        "enriched": bool(mod.get("enriched_at")),
        "installed": bool(installed),
        "installed_active": installed_active,
    }


def _is_private_host(host: str) -> bool:
    if not host:
        return True
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if re.fullmatch(r"(10|127)\.\d+\.\d+\.\d+", host) or host.startswith("192.168."):
        return True
    if re.fullmatch(r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+", host):
        return True
    return False


def proxy_image(url: str) -> tuple[bytes, str]:
    from core.http import fetch_bytes

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode()).hexdigest()
    for ext in (".jpg", ".png", ".webp"):
        candidate = CACHE_DIR / (digest + ext)
        if candidate.exists():
            return candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "image/jpeg"

    now = time.time()
    failed_at = _IMG_FAILED.get(url)
    if failed_at and now - failed_at < 3600:
        raise RuntimeError("Картинка недоступна (пробовали недавно)")

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or _is_private_host(parts.hostname or ""):
        raise RuntimeError("Недопустимый адрес картинки")
    if not security.resolve_is_public(parts.hostname or ""):
        raise RuntimeError("Адрес картинки ведёт в локальную сеть")
    referer = f"{parts.scheme}://{parts.netloc}/"
    try:
        raw = fetch_bytes(url, referer=referer, timeout=10, safe=True)
    except Exception:
        _IMG_FAILED[url] = now
        if len(_IMG_FAILED) > 500:
            _IMG_FAILED.clear()
        raise
    content_type = "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        content_type, ext = "image/png", ".png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        content_type, ext = "image/webp", ".webp"
    else:
        ext = ".jpg"
    (CACHE_DIR / (digest + ext)).write_bytes(raw)
    return raw, content_type


def state_payload() -> dict:
    cfg = config.load()
    stats = db.stats()
    counts = db.category_counts()
    sources = db.source_counts()
    mods_dir = cfg.get("mods_dir") or ""
    labels = CATEGORY_LABELS
    return {
        "version": VERSION,
        "settings": cfg,
        "stats": stats,
        "progress": HUB.progress,
        "pending": HUB.pending,
        "inbox_count": len(HUB.inbox()),
        "game_running": is_game_running(),
        "favorites": db.favorites_count(),
        "updates": db.updates_count(),
        "duplicates": len(db.installed_doubles()),
        "sources": [
            {"key": key, "label": label, "count": sources.get(key, 0)}
            for key, label in SOURCE_LABELS.items() if sources.get(key)
        ],
        "categories": [
            {"key": key, "label": labels[key], "count": counts.get(key, 0)}
            for key in CATEGORY_ORDER
        ],
        "mods_dir_exists": bool(mods_dir and Path(mods_dir).is_dir()),
        "data_dir": str(config.DATA_DIR),
        "game_path": cfg.get("game_path") or "",
        "catalog_synced": " · ".join(filter(None, [
            getattr(p, "sync_status", lambda: "")() for p in CATALOG_PROVIDERS])),
        "counts": {"mods": stats["mods"], "enriched": stats["enriched"], "installed": stats["installed"]},
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Браузер обрывает соединения постоянно — не засоряем лог."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# ---------------------------------------------------------------- handlers
class Handler(BaseHTTPRequestHandler):
    server_version = f"BeamHub/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли
        if "/api/state" in (args[0] if args else ""):
            return
        print(f"[http] {self.address_string()} {fmt % args}")

    def handle_error(self, request, client_address):
        """Браузер часто обрывает соединение — это не ошибка приложения."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

    # ---------- служебное ----------
    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False) -> None:
        HUB.touch()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _deny(self, reason: str, status: int = 403) -> bool:
        body = json.dumps({"ok": False, "error": f"Запрос отклонён: {reason}"},
                          ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")
        return False

    def _guard_host(self) -> bool:
        """Защита от DNS rebinding: Host обязан быть локальным."""
        if security.host_allowed(self.headers.get("Host")):
            return True
        print(f"[beamhub] отклонён запрос с Host={self.headers.get('Host')!r}")
        return self._deny("недопустимый Host")

    def _guard_write(self) -> bool:
        """POST: свой Origin, JSON и токен из интерфейса."""
        if not self._guard_host():
            return False
        port = self.server.server_port
        if not security.origin_allowed(self.headers.get("Origin"), port):
            return self._deny("чужой Origin")
        content_type = (self.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("application/json"):
            return self._deny("нужен Content-Type: application/json")
        if not security.token_ok(self.headers.get("X-BeamHub-Token")):
            return self._deny("нет токена интерфейса")
        return True

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=json_default).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _static(self, path: Path, cache: bool = True) -> None:
        if not path.exists() or not path.is_file():
            self._error("Не найдено", 404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        body = path.read_bytes()
        if path.name == "index.html":
            # токен для POST-запросов из интерфейса
            body = body.replace(b"__BEAMHUB_TOKEN__", security.token().encode())
        self._send(200, body, content_type, cache=cache)

    # ---------- GET ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if not self._guard_host():
            return

        try:
            if path == "/api/ping":
                self._json({"app": "beamhub", "version": VERSION})
                return
            if path == "/" or path == "/index.html":
                self._static(WEB_DIR / "index.html", cache=False)
                return
            if path.startswith("/static/"):
                rel = path[len("/static/"):]
                target = (WEB_DIR / rel).resolve()
                if not target.is_relative_to(WEB_DIR.resolve()):
                    self._error("Отказано", 403)
                    return
                self._static(target)
                return
            if path == "/api/state":
                self._json(state_payload())
                return
            if path == "/api/mods":
                q = (query.get("q") or [""])[0].strip()
                category = (query.get("cat") or ["all"])[0]
                source = (query.get("source") or ["all"])[0]
                sort = (query.get("sort") or ["new"])[0]
                offset = int((query.get("offset") or ["0"])[0])
                limit = min(120, int((query.get("limit") or ["48"])[0]))
                game = (query.get("game") or [""])[0]
                has_image = (query.get("img") or ["0"])[0] == "1"
                favorites_only = (query.get("fav") or ["0"])[0] == "1"
                result = db.query_mods(q=q, category=category, source=source, sort=sort,
                                       offset=offset, limit=limit, game=game,
                                       has_image=has_image, favorites_only=favorites_only)
                favorites = db.favorite_ids()
                dups = db.title_key_counts({m.get("title_key") for m in result["items"]})
                self._json({
                    "ok": True,
                    "total": result["total"],
                    "offset": offset,
                    "items": [mod_payload(m, favorites, dups) for m in result["items"]],
                })
                return
            if path == "/api/mod":
                mod_id = (query.get("id") or [""])[0]
                mod = db.get_mod(mod_id)
                if not mod:
                    self._error("Мод не найден", 404)
                    return
                if not mod.get("enriched_at"):
                    data = None
                    try:
                        from core.providers import enrich as enrich_mod
                        data = enrich_mod(mod)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[mod] enrich fail {mod_id}: {exc}")
                    if data:
                        data.pop("id", None)
                        db.update_mod(mod_id, **data)
                        mod = db.get_mod(mod_id) or mod
                dups = db.title_key_counts({mod.get("title_key") or ""})
                self._json({"ok": True, "item": mod_payload(mod, db.favorite_ids(), dups)})
                return
            if path == "/api/library":
                installer.scan_library()
                rows = db.installed_list()
                if any(not row.get("sha256") for row in rows):
                    # хеши нужны для поиска дублей — считаем в фоне, не тормозя ответ
                    threading.Thread(target=installer.hash_missing, args=(4,),
                                     daemon=True).start()
                doubles = db.installed_doubles()
                items = []
                for row in rows:
                    mod = db.get_mod(row["mod_id"]) if row.get("mod_id") else None
                    catalog_version = (mod or {}).get("version") or ""
                    has_update = bool(catalog_version and row.get("version")
                                      and catalog_version != row["version"])
                    reason = doubles.get(row["key"], "")
                    items.append({
                        "key": row["key"],
                        "mod_id": row.get("mod_id") or "",
                        "title": row.get("title") or Path(row["filename"]).stem,
                        "filename": row["filename"],
                        "path": row["path"],
                        "size": row.get("size") or 0,
                        "size_text": human_size(row.get("size") or 0),
                        "active": bool(row.get("active")),
                        "installed_at": row.get("installed_at") or 0,
                        "installed_text": human_ago(row.get("installed_at")),
                        "version": row.get("version") or "",
                        "catalog_version": catalog_version,
                        "update_available": has_update,
                        "duplicate": bool(reason),
                        "duplicate_reason": reason,
                    })
                self._json({"ok": True, "items": items, "mods_dir": config.load().get("mods_dir"),
                            "game_running": is_game_running(),
                            "updates": sum(1 for i in items if i["update_available"]),
                            "duplicates": sum(1 for i in items if i["duplicate"])})
                return
            if path == "/api/inbox":
                self._json({"ok": True, "items": HUB.inbox(),
                            "downloads_dir": config.load().get("downloads_dir")})
                return
            if path == "/api/events":
                since = int((query.get("since") or ["0"])[0])
                self._json({"ok": True, "seq": HUB.seq, "events": HUB.events_since(since)})
                return
            if path == "/api/img":
                url = (query.get("u") or [""])[0]
                if not url:
                    self._error("Нет адреса", 400)
                    return
                raw, content_type = proxy_image(url)
                self._send(200, raw, content_type, cache=True)
                return
            if path == "/api/detect":
                self._json({"ok": True, "mods_dir": config.detect_mods_dir(),
                            "game_path": config.find_game_dir()})
                return
            if path == "/api/update/check":
                self._json({"ok": True, **updater.check()})
                return
            if path == "/api/feedback":
                url = updater.feedback_url()
                if not url:
                    self._json({"ok": False,
                                "message": "Почта для обратной связи не задана в настройках."})
                    return
                open_in_browser(url)
                self._json({"ok": True, "url": url})
                return
            self._error("Не найдено", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(str(exc), 500)

    # ---------- POST ----------
    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not self._guard_write():
            return
        body = self._body()
        try:
            if path == "/api/quit":
                self._json({"ok": True, "message": "BeamHub остановлен. Вкладку можно закрыть."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/update/apply":
                result = updater.stage_update(body.get("asset_url", ""),
                                              body.get("version", ""),
                                              body.get("digest", ""))
                self._json({"ok": bool(result.get("ok")), **result})
                if result.get("ok"):
                    # выходим, чтобы внешний скрипт смог подменить файлы
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    threading.Timer(1.5, lambda: os._exit(0)).start()
                return
            if path == "/api/install":
                self._install(body)
                return
            if path == "/api/install-file":
                target = Path(body.get("path", "")).resolve()
                downloads = Path(config.load().get("downloads_dir") or Path.home() / "Downloads")
                try:
                    inside_downloads = target.is_relative_to(downloads.resolve())
                except (ValueError, OSError):
                    inside_downloads = False
                if not inside_downloads:
                    self._error("Ставить можно только архивы из папки загрузок", 403)
                    return
                if not target.exists():
                    self._error("Файл не найден")
                    return
                result = installer.install_archive(target)
                self._json({"ok": result["status"] in ("installed", "duplicate"), **result})
                if result["status"] == "installed":
                    HUB.emit("installed", f"Установлено: {result['installed']['title']}")
                return
            if path == "/api/favorite":
                mod_id = (body.get("id") or "").strip()
                if not mod_id or not db.get_mod(mod_id):
                    self._error("Мод не найден", 404)
                    return
                state = db.favorite_toggle(mod_id)
                self._json({"ok": True, "favorite": state})
                return
            if path == "/api/library/export":
                import time as _time

                target = config.DATA_DIR / f"beamhub-library-{_time.strftime('%Y%m%d-%H%M')}.json"
                payload = {"app": "BeamHub", "version": VERSION, "exported": int(_time.time()),
                           "mods": db.library_export()}
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                self._json({"ok": True, "path": str(target), "count": len(payload["mods"])})
                return
            if path == "/api/library/toggle":
                row = db.installed_by_key(body.get("key", ""))
                if not row:
                    self._error("Не найдено", 404)
                    return
                self._json({"ok": True, **installer.toggle(row)})
                return
            if path == "/api/library/remove":
                row = db.installed_by_key(body.get("key", ""))
                if not row:
                    self._error("Не найдено", 404)
                    return
                self._json({"ok": True, **installer.remove(row)})
                return
            if path == "/api/library/rescan":
                items = installer.scan_library()
                self._json({"ok": True, "count": len(items)})
                return
            if path == "/api/settings":
                cfg = config.update(body)
                HUB.emit("settings", "Настройки сохранены")
                self._json({"ok": True, "settings": cfg})
                return
            if path == "/api/sync":
                added = 0
                for provider in CATALOG_PROVIDERS:
                    try:
                        added += provider.sync(force=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[sync] {provider.SOURCE}: {exc}")
                enrich.start_enrichment()
                self._json({"ok": True, "added": added})
                return
            if path == "/api/add-link":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    self._error("Нужна ссылка http(s)")
                    return
                host = urllib.parse.urlsplit(url).netloc.lower()
                if "playground.ru" in host:
                    mod = playground.parse_url(url)
                else:
                    mod = generic.parse_url(url)
                db.upsert_mod(mod)
                created = db.get_mod(mod["id"]) or mod
                dups = db.title_key_counts({created.get("title_key") or ""})
                self._json({"ok": True, "item": mod_payload(created, db.favorite_ids(), dups)})
                return
            if path == "/api/open":
                mod = db.get_mod(body.get("id", ""))
                if not mod:
                    self._error("Мод не найден", 404)
                    return
                from core.providers import resolve_download
                url = body.get("target") or resolve_download(mod)
                open_in_browser(url)
                self._json({"ok": True, "opened": url})
                return
            self._error("Не найдено", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(str(exc), 500)

    # ---------- установка из каталога ----------
    def _install(self, body: dict) -> None:
        mod = db.get_mod(body.get("id", ""))
        if not mod:
            self._error("Мод не найден", 404)
            return
        mode = body.get("mode") or "browser"
        from core.providers import resolve_download

        if mode == "download":
            # Прямая загрузка внутри приложения (работает там, где нет Cloudflare).
            import urllib.request

            from core.http import UA

            url = resolve_download(mod)
            target = Path(config.load().get("downloads_dir") or Path.home() / "Downloads") / (
                (mod.get("title") or "mod")[:80].replace("/", "_") + ".zip")
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": mod.get("url") or ""})
            with urllib.request.urlopen(request, timeout=120) as resp, open(target, "wb") as handle:
                while chunk := resp.read(1 << 16):
                    handle.write(chunk)
            result = installer.install_archive(target, mod)
            self._json({"ok": result["status"] == "installed", "mode": "download", **result})
            return

        url = resolve_download(mod)
        HUB.set_pending(mod, url)
        if body.get("no_open"):
            self._json({"ok": True, "mode": "browser", "opened": None, "url": url,
                        "message": "Жду файл: скачайте его по ссылке, BeamHub поставит сам."})
            return
        open_in_browser(url)
        self._json({"ok": True, "mode": "browser", "opened": url,
                    "message": "Открыл браузер. Как только архив докачается, BeamHub поставит его сам."})


# ---------------------------------------------------------------- startup
def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def setup_logging() -> None:
    """В собранном exe нет консоли — весь вывод пишем в %LOCALAPPDATA%\\BeamHub\\beamhub.log."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_path = config.DATA_DIR / "beamhub.log"
        if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
            log_path.write_text("", encoding="utf-8")  # не растим лог бесконечно
        stream = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = stream
        sys.stderr = stream
        print(f"\n--- запуск {time.strftime('%d.%m.%Y %H:%M:%S')} ---")
    except OSError:
        pass


def show_error(message: str) -> None:
    """Сообщение об ошибке без консоли."""
    print(message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "BeamHub", 0x10)
    except Exception:  # noqa: BLE001
        pass


def seed_database() -> None:
    """Первый запуск exe: разворачиваем готовый каталог из комплекта."""
    if not config.is_frozen():
        return
    target = config.DATA_DIR / "beamhub.db"
    if target.exists() or not SEED_DB.exists():
        return
    try:
        import shutil

        shutil.copyfile(SEED_DB, target)
        print(f"[beamhub] каталог развёрнут из комплекта: {SEED_DB}")
    except OSError as exc:
        print(f"[beamhub] не удалось развернуть каталог: {exc}")


def running_instance(port: int) -> str | None:
    """Если BeamHub уже запущен — вернуть его адрес, чтобы не поднимать второй."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("app") == "beamhub":
            return f"http://127.0.0.1:{port}/"
    except Exception:  # noqa: BLE001
        return None
    return None


def open_url(url: str) -> bool:
    """Открыть ссылку в браузере: несколько способов, потому что первый может быть запрещён."""
    import subprocess

    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] webbrowser: {exc}")
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] startfile: {exc}")
    for command in (["cmd", "/c", "start", "", url], ["rundll32", "url.dll,FileProtocolHandler", url]):
        try:
            subprocess.Popen(command, creationflags=0x08000000, close_fds=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[beamhub] {' '.join(command[:2])}: {exc}")
    return False


def tell_user(title: str, message: str) -> None:
    """Показать диалог (когда консоли нет и браузер не открылся)."""
    print(f"{title}: {message}")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
    except Exception:  # noqa: BLE001
        pass


def write_shortcut(url: str) -> None:
    """Файл-ярлык «Открыть BeamHub.url», чтобы можно было зайти без автозапуска браузера."""
    if not config.is_frozen():
        return
    options = [Path(sys.executable).resolve().parent, config.DATA_DIR]
    for folder in options:
        try:
            target = folder / "Открыть BeamHub.url"
            target.write_text(f"[InternetShortcut]\nURL={url}\nIconIndex=0\n", encoding="utf-8")
            return
        except OSError:
            continue


def bootstrap() -> None:
    seed_database()
    config.load()
    db.init()
    try:
        installer.scan_library()
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] library scan: {exc}")
    HUB.start()


def warm_catalog() -> None:
    """Первый проход при старте, дальше раз в сутки — тихо, в фоне."""
    while True:
        try:
            enrich.run_sync()
            enrich.start_enrichment()
        except Exception as exc:  # noqa: BLE001
            print(f"[beamhub] автообновление каталога: {exc}")
        time.sleep(24 * 3600)


def main() -> None:
    parser = argparse.ArgumentParser(description="BeamHub — каталог модов BeamNG.drive")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true",
                        help="без окна и без браузера (headless)")
    parser.add_argument("--browser", action="store_true",
                        help="открыть в системном браузере вместо окна приложения")
    parser.add_argument("--no-enrich", action="store_true")
    parser.add_argument("--diagnose", action="store_true", help="проверить окно и движок, выйти")
    parser.add_argument("--sync", action="store_true", help="только обновить каталог и выйти")
    args = parser.parse_args()

    setup_logging()
    updater.apply_staged()  # если обновление скачано — подменяем файлы и стартуем заново

    if args.diagnose:
        print("=== diagnose ===")
        print("frozen:", config.is_frozen())
        print("desktop module:", desktop is not None)
        if desktop is not None:
            print("desktop.available():", desktop.available())
        try:
            import webview

            print("webview:", getattr(webview, "__file__", "?"))
            print("webview version:", getattr(webview, "__version__", "?"))
        except Exception as exc:  # noqa: BLE001
            print("webview import FAILED:", repr(exc))
        try:
            import clr  # noqa: F401

            print("clr: ok")
        except Exception as exc:  # noqa: BLE001
            print("clr import FAILED:", repr(exc))
        try:
            import clr_loader

            print("clr_loader:", getattr(clr_loader, "__file__", "?"))
        except Exception as exc:  # noqa: BLE001
            print("clr_loader FAILED:", repr(exc))
        print("=== /diagnose ===")
        return

    def wants_gui() -> bool:
        return not args.no_browser and not args.browser

    if not args.sync:
        existing = running_instance(args.port)
        if existing:
            print(f"[beamhub] уже запущен: {existing}")
            if wants_gui():
                if appwindow is not None and appwindow.available():
                    appwindow.launch(existing, config.DATA_DIR / "ui-profile")
                    return
                open_url(existing)
                if config.is_frozen() and launcher_ui is not None and launcher_ui.available():
                    launcher_ui.show(existing, opener=open_url, already_running=True,
                                     icon=str(config.APP_DIR / "beamhub.ico"))
            return

    bootstrap()
    if args.sync:
        count = modsgaming.sync(force=True)
        print(f"Добавлено модов: {count}, всего в базе: {db.count_mods()}")
        return

    port = free_port(args.port)
    server = Server(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"

    if not args.no_enrich:
        threading.Thread(target=warm_catalog, name="catalog", daemon=True).start()

    print("=" * 60)
    print(f"  BeamHub {VERSION} — {url}")
    print(f"  Моды:   {config.load().get('mods_dir')}")
    print(f"  Игра:   {config.load().get('game_path')}")
    print(f"  Данные: {config.DATA_DIR}")
    print("=" * 60)

    write_shortcut(url)
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()

    def stop() -> None:
        HUB.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    def finish() -> None:
        HUB.stop()
        server.shutdown()
        server.server_close()

    # 1) Основной режим: каталог в родном окне приложения (tkinter).
    if wants_gui() and tkui is not None and tkui.available():
        try:
            tkui.run(url)
            finish()
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[beamhub] родное окно не открылось: {exc}")

    # 2) Окно на движке Edge/Chrome (если tkinter недоступен).
    if wants_gui() and appwindow is not None and appwindow.available():
        process = appwindow.launch(url, config.DATA_DIR / "ui-profile")
        if process is not None:
            HUB.touch()
            try:
                while True:
                    time.sleep(1.5)
                    if appwindow.is_alive(process):
                        HUB.touch()
                        continue
                    # окно закрылось: ждём, не осталось ли открытой страницы
                    if time.time() - HUB.last_seen > 20:
                        break
            except KeyboardInterrupt:
                pass
            finally:
                appwindow.kill(process)
                finish()
            return
        print("[beamhub] окно приложения не запустилось, показываю в браузере")

    # 2) Запасной вариант: маленькое окно-лаунчер.
    if wants_gui() and launcher_ui is not None and launcher_ui.available():
        def stats() -> tuple[int, int]:
            data = db.stats()
            return data["mods"], data["installed"]

        try:
            launcher_ui.show(url, stats=stats, opener=open_url, on_close=stop,
                             icon=str(config.APP_DIR / "beamhub.ico"))
        except Exception as exc:  # noqa: BLE001
            print(f"[beamhub] окно не открылось: {exc}")
            open_url(url)
        finally:
            finish()
        return

    # 3) Совсем без GUI: системный браузер или headless.
    if wants_gui():
        open_url(url)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        finish()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        details = traceback.format_exc()
        print(details)
        show_error("BeamHub не смог запуститься.\n\n" + details[-1200:] +
                   f"\n\nЛог: {config.DATA_DIR / 'beamhub.log'}")
        sys.exit(1)
