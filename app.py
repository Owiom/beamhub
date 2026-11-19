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
import secrets
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
    from core import (accounts, official, cloud, cloud_check, cloud_email, cloud_moderation,
                      cloud_session, cloud_social, cloud_sync, collections, compat,
                      config, db, enrich, feedback, installer, matcher, moderation,
                      recommend, security, social, updater)  # noqa: E402
    from core.version import VERSION  # noqa: E402
    from core.providers import CATALOG_PROVIDERS, SOURCE_LABELS, modsgaming  # noqa: E402
    from core.providers import generic, playground  # noqa: E402
    from core.state import HUB, open_in_browser  # noqa: E402
    from core.util import CATEGORY_LABELS, CATEGORY_ORDER, human_ago, human_date, human_size, is_game_running  # noqa: E402
except Exception:  # noqa: BLE001
    _startup_failure(traceback.format_exc())
    raise SystemExit(1)

# состояния входа через Google: короткоживущие, в памяти процесса
START = time.time()
CLOUD_STATES = cloud.SessionStore()
CLOUD_LOGINS: dict[str, dict] = {}
CLOUD_TOKENS: dict[str, dict] = {}

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
_IMG_SLOTS = threading.Semaphore(3)  # не больше трёх загрузок обложек одновременно


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
    if failed_at and now - failed_at < 600:
        raise RuntimeError("Картинка недоступна (пробовали недавно)")

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or _is_private_host(parts.hostname or ""):
        raise RuntimeError("Недопустимый адрес картинки")
    if not security.resolve_is_public(parts.hostname or ""):
        raise RuntimeError("Адрес картинки ведёт в локальную сеть")
    referer = f"{parts.scheme}://{parts.netloc}/"
    # beamngland отдаёт картинки очень медленно (~30 КБ за 15 с) — даём запас
    # и не пускаем больше трёх одновременных загрузок, иначе все упираются в таймаут
    with _IMG_SLOTS:
        try:
            raw = fetch_bytes(url, referer=referer, timeout=60, safe=True)
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
        "matched": db.matched_count(),
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
            if self._platform_get(path, query):
                return
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
            if path == "/api/mods/by-ids":
                ids = [part for part in (query.get("ids") or [""])[0].split(",")
                       if part.strip()][:120]
                if not ids:
                    self._json({"ok": True, "items": []})
                    return True
                marks = ",".join("?" for _ in ids)
                rows = db.query(
                    f"SELECT id, title, description, image, category, version, game_version, "
                    f"size_text, size_bytes, downloads, published, url, source, author "
                    f"FROM mods WHERE id IN ({marks})", ids)
                by_id = {row["id"]: row for row in rows}
                items = [by_id[mod_id] for mod_id in ids if mod_id in by_id]
                self._json({"ok": True, "items": items})
                return True
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
                        "catalog_title": (mod or {}).get("title") or "",
                        "update_available": has_update,
                        "matched": bool(row.get("mod_id")),
                        "version_unknown": bool(row.get("mod_id") and not row.get("version")
                                                and catalog_version),
                        "duplicate": bool(reason),
                        "duplicate_reason": reason,
                    })
                self._json({"ok": True, "items": items, "mods_dir": config.load().get("mods_dir"),
                            "game_running": is_game_running(),
                            "updates": sum(1 for i in items if i["update_available"]),
                            "unknown": sum(1 for i in items if i["version_unknown"]),
                            "matched": sum(1 for i in items if i["matched"]),
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
                note = urllib.parse.parse_qs(parsed.query).get("note", [""])[0]
                with_log = urllib.parse.parse_qs(parsed.query).get("log", ["1"])[0] != "0"
                with_paths = urllib.parse.parse_qs(parsed.query).get("paths", ["1"])[0] != "0"
                report = feedback.build_report(note, with_paths, with_log)
                title = feedback.subject(note)
                self._json({"ok": True, "report": report, "title": title,
                            "issue": feedback.issue_link(report, title),
                            "has_log": bool(feedback.log_tail())})
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
            if self._platform_post(path, body):
                return
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
            if path == "/api/inbox/install-all":
                self._json({"ok": True, **HUB.install_all_inbox()})
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
                    threading.Thread(target=matcher.match_all, daemon=True).start()
                return
            if path == "/api/favorite":
                mod_id = (body.get("id") or "").strip()
                if not mod_id or not db.get_mod(mod_id):
                    self._error("Мод не найден", 404)
                    return
                state = db.favorite_toggle(mod_id)
                self._json({"ok": True, "favorite": state})
                return
            if path == "/api/library/match":
                installer.scan_library()
                stats = matcher.match_all()
                self._json({"ok": True, **stats})
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
            if path == "/api/feedback/open":
                report = body.get("report") or feedback.build_report()
                title = body.get("title") or feedback.subject()
                url = feedback.issue_link(report, title)
                open_in_browser(url)
                self._json({"ok": True, "opened": url})
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
    # ---------- платформа: аккаунты и сборки ----------
    def _user_token(self) -> str:
        return (self.headers.get("X-BeamHub-User") or "").strip()

    def _current_user(self) -> dict | None:
        try:
            return accounts.user_by_token(self._user_token())
        except Exception:  # noqa: BLE001
            return None

    def _require_user(self) -> dict | None:
        user = self._current_user()
        if not user:
            self._json({"ok": False, "error": "Нужно войти в аккаунт."}, status=401)
        return user

    def _platform_get(self, path: str, query: dict) -> bool:
        def arg(name: str, default: str = "") -> str:
            return (query.get(name) or [default])[0]

        if path == "/api/auth/me":
            user = self._current_user()
            self._json({"ok": True, "user": accounts.profile(user["id"]) if user else None})
            return True
        if path == "/api/user":
            user_id = arg("id")
            data = accounts.profile(user_id)
            if not data:
                self._json({"ok": False, "error": "Пользователь не найден."}, status=404)
                return True
            viewer = self._current_user()
            data["is_me"] = bool(viewer and viewer["id"] == user_id)
            data["is_following"] = bool(viewer and accounts.is_following(viewer["id"], user_id))
            rows = collections.list_collections("mine", viewer_id=user_id)["items"]
            rows = [c for c in rows if c.get("published")]
            data["collections_count"] = len(rows)
            data["collections"] = [{
                "id": c.get("id"), "title": c.get("title"),
                "category": c.get("category"), "items_count": c.get("items_count", 0),
                "installs": c.get("installs", 0), "views": c.get("views", 0),
                "rating": c.get("rating") or {"avg": 0, "count": 0},
            } for c in rows[:12]]
            self._json({"ok": True, "user": data})
            return True
        if path == "/api/collections":
            kind = arg("kind", "popular")
            if kind == "mine":
                viewer = self._require_user()
                if not viewer:
                    return True
                result = collections.list_collections(kind, viewer_id=viewer["id"],
                                                      limit=min(60, int(arg("limit", "24") or 24)))
                self._json(result)
                return True
            result = collections.list_collections(
                kind=kind if kind in ("new", "popular", "rating") else "popular",
                query=arg("q"), category=arg("cat"),
                limit=min(60, int(arg("limit", "24") or 24)),
                offset=int(arg("offset", "0") or 0))
            self._json(result)
            return True
        if path == "/api/collection":
            viewer = self._current_user()
            coll_id = arg("id")
            data = collections.get(coll_id, viewer["id"] if viewer else "")
            if not data.get("ok"):
                self._json({"ok": False, "error": data.get("error", "Не найдено")}, status=404)
                return True
            coll = data["collection"]
            coll["my_rating"] = social.my_rating(viewer["id"] if viewer else "",
                                                 "collection", coll_id)
            coll["comments_count"] = social.comments("collection", coll_id)["count"]
            if coll["published"] and (not viewer or viewer["id"] != coll["owner_id"]):
                collections.record_event("view", coll_id=coll_id,
                                         user_id=viewer["id"] if viewer else "")
            self._json(data)
            return True
        if path == "/api/moderation/overview":
            user = self._require_user()
            if not user:
                return True
            if not accounts.is_moderator(user):
                self._json({"ok": False, "error": "Нужны права модератора."}, status=403)
                return True
            self._json({"ok": True, **moderation.overview()})
            return True
        if path == "/api/moderation/reports":
            user = self._require_user()
            if not user:
                return True
            if not accounts.is_moderator(user):
                self._json({"ok": False, "error": "Нужны права модератора."}, status=403)
                return True
            self._json(moderation.list_reports(arg("status", "open")))
            return True
        if path == "/api/moderation/log":
            user = self._require_user()
            if not user:
                return True
            if not accounts.is_moderator(user):
                self._json({"ok": False, "error": "Нужны права модератора."}, status=403)
                return True
            self._json(moderation.log())
            return True
        if path == "/api/comments":
            target_type = arg("target_type", "collection")
            self._json(social.comments(target_type, arg("target_id"),
                                       self._current_user()["id"] if self._current_user() else ""))
            return True
        if path == "/api/reviews":
            viewer = self._current_user()
            self._json(social.reviews(arg("target_type", "collection"), arg("target_id"),
                                      viewer["id"] if viewer else "",
                                      only_installed=arg("installed") == "1"))
            return True
        if path == "/api/collections/plan":
            viewer = self._current_user()
            data = collections.plan(arg("id"), viewer["id"] if viewer else "")
            self._json(data if data.get("ok") else
                       {"ok": False, "error": data.get("error", "Не найдено")})
            return True
        if path == "/api/sync/status":
            self._json({"ok": True, **cloud_sync.status()})
            return True
        if path == "/api/auth/cloud":
            url, key = cloud.settings()
            self._json({"ok": True, "configured": cloud.configured(),
                        "url": url, "has_key": bool(key)})
            return True
        if path == "/api/auth/google/poll":
            entry = CLOUD_LOGINS.get(arg("state")) or {}
            self._json({"ok": True, "status": entry.get("status", "pending"),
                        "user": entry.get("user"), "error": entry.get("error", ""),
                        "token": entry.get("token", "")})
            return True
        if path == "/api/auth/callback":
            self._cloud_callback(arg("code"), arg("state"))
            return True
        if path == "/api/admin/stats":
            user = self._require_user()
            if not user:
                return True
            if user.get("role") not in ("admin", "moderator"):
                self._json({"ok": False, "error": "Нужны права администратора."}, status=403)
                return True
            cfg = config.load()
            db_path = Path(db.DB_PATH) if hasattr(db, "DB_PATH") else None
            size = db_path.stat().st_size if db_path and db_path.exists() else 0
            installed = db.installed_list()
            events = db.query(
                "SELECT kind, COUNT(*) AS n FROM stats_events GROUP BY kind")
            recent = db.query(
                "SELECT kind, mod_id, collection_id, created_at FROM stats_events "
                "ORDER BY created_at DESC LIMIT 15")
            self._json({"ok": True, "version": VERSION, "uptime_seconds": int(time.time() - START),
                        "db_bytes": size,
                        "counts": {
                            "mods": db.count_mods(),
                            "installed": len(installed),
                            "favorites": db.favorites_count(),
                            "updates": db.updates_count(),
                            "duplicates": len(db.installed_doubles()),
                            "users": db.query_one("SELECT COUNT(*) AS c FROM users")["c"],
                            "collections": db.query_one(
                                "SELECT COUNT(*) AS c FROM collections")["c"],
                            "published": db.query_one(
                                "SELECT COUNT(*) AS c FROM collections WHERE published=1")["c"],
                            "comments": db.query_one("SELECT COUNT(*) AS c FROM comments")["c"],
                            "ratings": db.query_one("SELECT COUNT(*) AS c FROM ratings")["c"],
                            "reports_open": db.query_one(
                                "SELECT COUNT(*) AS c FROM reports WHERE status='open'")["c"],
                        },
                        "events": events, "recent": recent,
                        "sources": db.source_counts(),
                        "paths": {"mods_dir": cfg.get("mods_dir") or "",
                                  "game_path": cfg.get("game_path") or "",
                                  "data_dir": str(config.DATA_DIR)}})
            return True
        if path == "/api/stats":
            self._json({"ok": True, **collections.stats_public()})
            return True
        if path == "/api/search/log":
            query = (body.get("q") or "").strip()[:80]
            if query:
                raw = db.get_meta("recent_searches") or "[]"
                try:
                    items = json.loads(raw)
                except json.JSONDecodeError:
                    items = []
                items = [query] + [item for item in items if item != query][:9]
                db.set_meta("recent_searches", json.dumps(items, ensure_ascii=False))
            self._json({"ok": True})
            return True
        if path == "/api/recommend/mods":
            mod_id = arg("id")
            if mod_id:
                self._json(recommend.similar_mods(mod_id, limit=min(24, int(arg("limit", "8") or 8))))
            else:
                viewer = self._current_user()
                try:
                    searches = json.loads(db.get_meta("recent_searches") or "[]")
                except json.JSONDecodeError:
                    searches = []
                self._json(recommend.recommended_mods(
                    viewer["id"] if viewer else "",
                    limit=min(24, int(arg("limit", "12") or 12)), searches=searches))
            return True
        if path == "/api/recommend/collections":
            self._json(recommend.recommended_collections(limit=min(24, int(arg("limit", "8") or 8))))
            return True
        if path == "/api/compat":
            self._json(compat.report(arg("id"), force=arg("force") == "1"))
            return True
        if path == "/api/platform/stats":
            user = self._require_user()
            if not user:
                return True
            if not accounts.is_moderator(user):
                self._json({"ok": False, "error": "Нужны права модератора."}, status=403)
                return True
            self._json({"ok": True, **collections.stats()})
            return True
        return False

    def _platform_post(self, path: str, body: dict) -> bool:
        if path == "/api/auth/register":
            result = accounts.register(body.get("email", ""), body.get("password", ""),
                                       body.get("nickname", ""))
            self._json(result if result.get("ok") else
                       {"ok": False, "error": result.get("error")}, status=200)
            return True
        if path == "/api/auth/login":
            result = accounts.login(body.get("email", ""), body.get("password", ""))
            self._json(result if result.get("ok") else
                       {"ok": False, "error": result.get("error")})
            return True
        if path == "/api/auth/logout":
            accounts.logout(self._user_token())
            self._json({"ok": True})
            return True
        if path == "/api/profile":
            user = self._require_user()
            if not user:
                return True
            self._json(accounts.update_profile(user["id"], **{
                k: body[k] for k in ("nickname", "bio", "avatar_color") if k in body}))
            return True
        if path == "/api/follow":
            user = self._require_user()
            if not user:
                return True
            target = body.get("user_id", "")
            if body.get("action") == "unfollow":
                self._json(accounts.unfollow(user["id"], target))
            else:
                self._json(accounts.follow(user["id"], target))
            return True
        if path == "/api/sync/push":
            user = self._require_user()
            if not user:
                return True
            result = cloud_sync.push()
            if result.get("ok"):
                social_result = cloud_social.push()
                result["comments"] = social_result.get("comments", 0)
                result["ratings"] = social_result.get("ratings", 0)
                if not social_result.get("ok"):
                    result.setdefault("errors", []).append(social_result.get("error", ""))
                mod_result = cloud_moderation.push(user)
                result["reports"] = mod_result.get("reports", 0)
                result["actions"] = mod_result.get("actions", 0)
            self._json(result)
            return True
        if path == "/api/sync/pull":
            result = cloud_sync.pull()
            if result.get("ok"):
                social_result = cloud_social.pull()
                result["comments"] = social_result.get("comments", 0)
                result["ratings"] = social_result.get("ratings", 0)
                viewer = self._current_user()
                mod_result = cloud_moderation.pull(viewer)
                result["reports"] = mod_result.get("reports", 0)
                result["actions"] = mod_result.get("actions", 0)
            self._json(result)
            return True
        if path == "/api/auth/email":
            if not cloud.configured():
                self._json({"ok": False,
                            "error": "Сначала укажите адрес Supabase и anon key в настройках."})
                return True
            email = (body.get("email") or "").strip()
            password = body.get("password") or ""
            mode = body.get("mode") or "login"
            problem = cloud_email.validate(email, password)
            if problem:
                self._json({"ok": False, "error": problem})
                return True
            base, key = cloud.settings()
            result = (cloud_email.signup(base, key, email, password) if mode == "signup"
                      else cloud_email.login(base, key, email, password))
            if not result.get("ok"):
                self._json(result)
                return True
            user = result.get("user") or {}
            cloud_id = user.get("id") or email.lower()
            local = accounts.upsert_provider_user("email", cloud_id, email,
                                                  cloud_email.nickname_from_email(email))
            if not local.get("ok"):
                self._json({"ok": False, "error": "Не удалось создать локальный аккаунт."})
                return True
            cloud_session.set_session(local["user"]["id"], cloud_id,
                                      result["access_token"],
                                      result.get("refresh_token", ""))
            self._json({"ok": True, "token": local["token"], "user": local["user"],
                        "mode": mode})
            return True
        if path == "/api/cloud/settings":
            url = (body.get("url") or "").strip().rstrip("/")
            key = (body.get("anon_key") or "").strip()
            local_ok = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
            if url and not url.startswith("https://") and not local_ok:
                self._json({"ok": False, "error": "Адрес Supabase должен быть https."})
                return True
            config.update({"cloud_url": url, "cloud_anon_key": key})
            self._json({"ok": True, "configured": cloud.configured()})
            return True
        if path in ("/api/auth/google/start", "/api/auth/oauth/start"):
            if not cloud.configured():
                self._json({"ok": False,
                            "error": "Укажите адрес Supabase и anon key в настройках."})
                return True
            base, key = cloud.settings()
            port = self.server.server_address[1]
            redirect_to = f"http://127.0.0.1:{port}/api/auth/callback"
            provider = (body.get("provider") or "google").strip().lower()
            if provider not in ("google", "discord"):
                self._json({"ok": False, "error": "Такой провайдер не поддерживается."})
                return True
            problem = cloud_check.provider_problem(base, key, provider, redirect_to)
            if problem:
                self._json({"ok": False, "error": problem})
                return True
            state = secrets.token_urlsafe(18)
            verifier = cloud.make_verifier()
            CLOUD_STATES.add(state, verifier, redirect_to)
            CLOUD_LOGINS[state] = {"status": "pending", "provider": provider}
            url = cloud.authorize_url(base, redirect_to, state, verifier, provider=provider)
            open_in_browser(url)
            self._json({"ok": True, "state": state, "url": url, "provider": provider})
            return True
        if path == "/api/moderation/report":
            user = self._require_user()
            if not user:
                return True
            self._json(moderation.report(user["id"], body.get("target_type", ""),
                                         body.get("target_id", ""),
                                         body.get("reason", "other"),
                                         body.get("details", "")))
            return True
        if path == "/api/moderation/resolve":
            user = self._require_user()
            if not user:
                return True
            answer = moderation.resolve(user, body.get("id", ""),
                                        body.get("action", "resolve"),
                                        body.get("note", ""))
            if answer.get("ok"):
                cloud_moderation.push_resolution(body.get("id", ""), answer.get("status", ""))
            self._json(answer)
            return True
        if path == "/api/moderation/block":
            user = self._require_user()
            if not user:
                return True
            self._json(moderation.block_user(user, body.get("user_id", ""),
                                             bool(body.get("blocked", True)),
                                             body.get("reason", "")))
            return True
        if path == "/api/comments":
            user = self._require_user()
            if not user:
                return True
            result = social.add_comment(user["id"], body.get("target_type", "collection"),
                                        body.get("target_id", ""), body.get("body", ""),
                                        body.get("parent_id", ""))
            if not result.get("ok"):
                self._json(result)
                return True
            self._json(social.comments(body.get("target_type", "collection"),
                                       body.get("target_id", ""), user["id"]))
            return True
        if path == "/api/comments/update":
            user = self._require_user()
            if not user:
                return True
            self._json(social.update_comment(user["id"], body.get("id", ""),
                                             body.get("body", "")))
            return True
        if path == "/api/comments/delete":
            user = self._require_user()
            if not user:
                return True
            self._json(social.delete_comment(user["id"], body.get("id", ""),
                                             accounts.is_moderator(user)))
            return True
        if path == "/api/ratings":
            user = self._require_user()
            if not user:
                return True
            self._json(social.set_rating(user["id"], body.get("target_type", "collection"),
                                         body.get("target_id", ""), body.get("stars", 0),
                                         body.get("review", "")))
            return True
        if path == "/api/ratings/delete":
            user = self._require_user()
            if not user:
                return True
            self._json(social.delete_rating(user["id"], body.get("target_type", "collection"),
                                            body.get("target_id", "")))
            return True
        if path == "/api/collections/create":
            user = self._require_user()
            if not user:
                return True
            result = collections.create(user["id"], body.get("title", ""),
                                        body.get("description", ""),
                                        body.get("category", "other"),
                                        body.get("tags", []), body.get("cover", ""),
                                        body.get("game_version", ""))
            self._json(result)
            return True
        if path == "/api/collections/update":
            user = self._require_user()
            if not user:
                return True
            allowed = ("title", "description", "cover", "category", "tags", "game_version")
            fields = {k: body[k] for k in allowed if k in body}
            self._json(collections.update(body.get("id", ""), user["id"], **fields))
            return True
        if path == "/api/collections/delete":
            user = self._require_user()
            if not user:
                return True
            self._json(collections.delete(body.get("id", ""), user["id"],
                                          accounts.is_moderator(user)))
            return True
        if path == "/api/collections/items":
            user = self._require_user()
            if not user:
                return True
            coll_id = body.get("id", "")
            if body.get("action") == "remove":
                self._json(collections.remove_item(coll_id, user["id"], body.get("mod_id", "")))
            else:
                self._json(collections.add_item(coll_id, user["id"], body.get("mod_id", ""),
                                                body.get("note", "")))
            return True
        if path == "/api/collections/publish":
            user = self._require_user()
            if not user:
                return True
            coll_id = body.get("id", "")
            result = (collections.unpublish(coll_id, user["id"]) if body.get("action") == "unpublish"
                      else collections.publish(coll_id, user["id"], body.get("note", "")))
            self._json(result)
            return True
        if path == "/api/collections/open-all":
            user = self._current_user()
            if not user:
                self._json({"ok": False, "error": "Нужно войти в аккаунт."}, status=401)
                return True
            plan = collections.plan(body.get("id", ""), user["id"])
            if not plan.get("ok"):
                self._json(plan, status=404)
                return True
            missing = plan.get("missing") or []
            if not missing:
                self._json({"ok": True, "opened": 0, "message": "Вся сборка уже установлена."})
                return True
            HUB.reset_sequence()
            from core.providers import resolve_download

            opened = 0
            for item in missing:
                mod = db.get_mod(item["mod_id"])
                if not mod:
                    continue
                open_in_browser(resolve_download(mod) or mod.get("url") or "")
                opened += 1
            self._json({"ok": True, "opened": opened,
                        "message": f"Открыл ссылок: {opened}. Скачайте файлы — BeamHub поставит их сам."})
            return True
        if path == "/api/collections/sequence":
            user = self._current_user()
            if not user:
                self._json({"ok": False, "error": "Нужно войти в аккаунт."}, status=401)
                return True
            plan = collections.plan(body.get("id", ""), user["id"])
            if not plan.get("ok"):
                self._json(plan, status=404)
                return True
            missing = plan.get("missing") or []
            if not missing:
                self._json({"ok": True, "queued": 0, "message": "Вся сборка уже установлена."})
                return True
            mods = [db.get_mod(item["mod_id"]) for item in missing]
            mods = [mod for mod in mods if mod]
            count = HUB.set_sequence(mods)
            self._json({"ok": True, "queued": count,
                        "message": f"По очереди: {count} модов. После установки файла откроется следующий."})
            return True
        if path == "/api/collections/install":
            user = self._current_user()
            if not user:
                self._json({"ok": False, "error": "Нужно войти в аккаунт."}, status=401)
                return True
            coll_id = body.get("id", "")
            plan = collections.plan(coll_id, user["id"])
            if not plan.get("ok"):
                self._json(plan, status=404)
                return True
            missing = plan.get("missing") or []
            if not missing:
                self._json({"ok": True, "queued": 0,
                            "message": "Вся сборка уже установлена."})
                return True

            HUB.reset_sequence()

            def worker() -> None:
                done, failed = 0, 0
                for item in missing:
                    mod = db.get_mod(item["mod_id"])
                    if not mod:
                        failed += 1
                        continue
                    HUB.emit("progress", f"Скачиваю: {mod.get('title') or mod['id']}")
                    result = download_and_install(mod)
                    if result.get("status") in ("installed", "duplicate"):
                        done += 1
                        collections.record_event("install", mod_id=mod["id"],
                                                 coll_id=coll_id, user_id=user["id"])
                        HUB.emit("installed", f"Установлено: "
                                              f"{(result.get('installed') or {}).get('title') or mod['id']}")
                    else:
                        failed += 1
                        HUB.emit("error", f"Не удалось: {mod.get('title') or mod['id']}")
                HUB.emit("done", f"Сборка: установлено {done}, не удалось {failed}")
                try:
                    threading.Thread(target=matcher.match_all, daemon=True).start()
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(target=worker, daemon=True).start()
            self._json({"ok": True, "queued": len(missing),
                        "message": f"Скачиваю моды: {len(missing)}. Прогресс — в строке статуса."})
            return True
        if path == "/api/collections/apply":
            user = self._current_user()
            if not user:
                self._json({"ok": False, "error": "Нужно войти в аккаунт."}, status=401)
                return True
            plan = collections.plan(body.get("id", ""), user["id"])
            if not plan.get("ok"):
                self._json(plan, status=404)
                return True
            nxt = plan.get("next")
            if not nxt:
                self._json({"ok": True, "done": True, "plan": plan,
                            "message": "Вся сборка уже установлена."})
                return True
            mod = db.get_mod(nxt["mod_id"])
            from core.providers import resolve_download

            url = resolve_download(mod) if mod else ""
            if url:
                open_in_browser(url)
            collections.record_event("download", coll_id=body.get("id", ""),
                                     mod_id=nxt["mod_id"], user_id=user["id"])
            self._json({"ok": True, "done": False, "plan": plan, "next": nxt,
                        "message": f"Открыл страницу мода «{nxt.get('title')}». "
                                   f"Дальше BeamHub поставит файл сам."})
            return True
        return False

    def _cloud_callback(self, code: str, state: str) -> None:
        """Браузер вернулся с кодом Google: меняем его на сессию и показываем страницу."""
        entry = CLOUD_STATES.pop(state) if state else None
        if not entry:
            CLOUD_LOGINS[state or "?"] = {"status": "error",
                                          "error": "Ссылка устарела, начните вход заново."}
            self._html_page("Не получилось", "Ссылка устарела. Вернитесь в BeamHub и начните "
                                             "вход заново.", ok=False)
            return
        if not code:
            CLOUD_LOGINS[state] = {"status": "error", "error": "Google не передал код."}
            self._html_page("Отменено", "Вход отменён или не завершён.", ok=False)
            return
        base, key = cloud.settings()
        result = cloud.exchange_code(base, key, code, entry["verifier"])
        if not result.get("ok"):
            CLOUD_LOGINS[state] = {"status": "error",
                                   "error": result.get("error", "ошибка обмена")}
            self._html_page("Ошибка входа", str(result.get("error", "")), ok=False)
            return
        user = result.get("user") or {}
        if not user.get("id"):
            info = cloud.me(base, key, result["access_token"])
            user = info.get("user") or {}
        if not user.get("id"):
            CLOUD_LOGINS[state] = {"status": "error", "error": "Профиль не получен."}
            self._html_page("Ошибка входа", "Не удалось получить профиль Google.", ok=False)
            return
        provider = (entry or {}).get("provider") or "google"
        local = accounts.upsert_provider_user(provider, user["id"], user.get("email", ""),
                                              cloud.nickname_from(user))
        if not local.get("ok"):
            CLOUD_LOGINS[state] = {"status": "error", "error": "Не удалось создать аккаунт."}
            self._html_page("Ошибка", "Не удалось создать аккаунт в BeamHub.", ok=False)
            return
        CLOUD_TOKENS[local["user"]["id"]] = {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", ""),
        }
        cloud_session.set_session(local["user"]["id"], user["id"], result["access_token"],
                                  result.get("refresh_token", ""))
        CLOUD_LOGINS[state] = {"status": "done", "user": local["user"], "token": local["token"]}
        self._html_page("Готово", "Вход выполнен. Вернитесь в BeamHub — аккаунт уже активен.")

    def _html_page(self, title: str, message: str, ok: bool = True) -> None:
        color = "#35c46b" if ok else "#ff7a1a"
        page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{title} — BeamHub</title></head>
<body style="margin:0;background:#0d0e11;color:#e9ebf0;font-family:'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh">
<div style="text-align:center;max-width:520px;padding:32px">
<div style="font-size:42px;color:{color};font-weight:600">BeamHub</div>
<div style="font-size:22px;margin:16px 0 8px">{title}</div>
<div style="color:#8d93a1;line-height:1.5">{message}</div>
</div></body></html>"""
        raw = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _install(self, body: dict) -> None:
        mod = db.get_mod(body.get("id", ""))
        if not mod:
            self._error("Мод не найден", 404)
            return
        mode = body.get("mode") or "browser"
        from core.providers import resolve_download

        if mode == "download":
            result = download_and_install(mod)
            self._json({"ok": result.get("status") in ("installed", "duplicate"),
                        "mode": "download", **result})
            return

        url = resolve_download(mod)
        HUB.set_pending(mod, url)
        if body.get("no_open"):
            self._json({"ok": True, "mode": "browser", "opened": None, "url": url,
                        "message": "Жду файл: скачайте его по ссылке, BeamHub поставит сам."})
            return
        open_in_browser(url)
        self._json({"ok": True, "mode": "browser", "opened": url,
                    "message": "Открыл страницу мода в браузере. Нажми там «Скачать» — "
                               "как только архив докачается, BeamHub поставит его сам."})


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




def download_and_install(mod: dict) -> dict:
    """Скачать мод по прямой ссылке и поставить. Возвращает статус."""
    from core.providers import resolve_download

    url = resolve_download(mod) or ""
    if not url:
        return {"status": "failed", "error": "нет ссылки"}
    cfg = config.load()
    workdir = Path(config.DATA_DIR) / "tmp"
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / ((mod.get("title") or "mod")[:80].replace("/", "_")
                        .replace("\\", "_") + ".zip")
    try:
        import urllib.request

        from core.http import UA

        request = urllib.request.Request(
            url, headers={"User-Agent": UA, "Referer": mod.get("url") or ""})
        with urllib.request.urlopen(request, timeout=180) as resp, open(target, "wb") as handle:
            while chunk := resp.read(1 << 16):
                handle.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)[:200]}
    try:
        result = installer.install_archive(target, mod)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)[:200]}
    return result

def write_shortcut(url: str) -> None:
    """Ярлык на само приложение: веб-версия устарела и только путает."""
    if not config.is_frozen():
        return
    exe = Path(sys.executable).resolve()
    folder = exe.parent
    legacy = [folder / "Открыть BeamHub.url", config.DATA_DIR / "Открыть BeamHub.url",
              Path.home() / "Desktop" / "Открыть BeamHub.url"]
    for old in legacy:
        try:
            if old.exists():
                old.unlink()
        except OSError:
            pass
    link = folder / "BeamHub.lnk"
    if link.exists():
        return
    try:
        import subprocess

        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
             "$s.TargetPath='%s';$s.WorkingDirectory='%s';"
             "$s.IconLocation='%s';$s.Description='BeamHub — каталог модов BeamNG.drive';"
             "$s.Save()" % (link, exe, folder, exe)],
            check=False, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] ярлык не создан: {exc}")


def bootstrap() -> None:
    seed_database()
    config.load()
    db.init()
    try:
        if db.get_meta("orphans_purged") != "1":
            db.execute("DELETE FROM collection_items WHERE mod_id NOT IN (SELECT id FROM mods)")
            db.execute("DELETE FROM favorites WHERE mod_id NOT IN (SELECT id FROM mods)")
            db.set_meta("orphans_purged", "1")
            print("[beamhub] осиротевшие записи коллекций очищены")
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] orphan purge: {exc}")
    try:
        if db.get_meta("torrent_purged") != "1":
            removed = db.query("SELECT COUNT(*) AS c FROM mods WHERE source='playground'")[0]["c"]
            db.execute("DELETE FROM mods WHERE source='playground'")
            db.set_meta("torrent_purged", "1")
            if removed:
                print(f"[beamhub] удалено торрент-модов: {removed}")
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] torrent purge: {exc}")
    try:
        created = official.ensure()
        if created:
            print(f"[beamhub] официальных сборок добавлено: {created}")
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] official collections: {exc}")
    try:
        installer.scan_library()
        stats = matcher.match_all()
        if stats["matched"]:
            print(f"[beamhub] к каталогу привязано модов: {stats['matched']} из {stats['checked']}")
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
