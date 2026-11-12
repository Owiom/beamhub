"""Общее состояние приложения: события, ожидание скачивания, слежка за Downloads."""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from pathlib import Path

from . import config, db, installer
from .util import human_size, now_ts, title_key

ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z"}
COPY_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


class Hub:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.events: deque = deque(maxlen=80)
        self.seq = 0
        self.pending: dict | None = None
        self.last_seen = time.time()  # когда последний раз обращались из интерфейса
        self.progress = {"done": 0, "total": 0, "running": False}
        self._seen: dict[str, tuple[int, int]] = {}
        self._stable: dict[str, int] = {}
        self._baseline: set[str] | None = None
        self._baseline_dir: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inbox_cache: dict[str, tuple[tuple[int, int], dict]] = {}
        self._bulk_running = False

    # ---------- события ----------
    def touch(self) -> None:
        """Отметить, что интерфейс жив (окно открыто и опрашивает сервер)."""
        self.last_seen = time.time()

    def emit(self, kind: str, text: str, **extra) -> dict:
        with self.lock:
            self.seq += 1
            event = {"seq": self.seq, "ts": now_ts(), "kind": kind, "text": text, **extra}
            self.events.append(event)
        print(f"[beamhub] {kind}: {text}")
        return event

    def events_since(self, since: int) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["seq"] > since]

    # ---------- ожидание файла ----------
    def set_pending(self, mod: dict, url: str) -> None:
        with self.lock:
            self.pending = {"mod_id": mod["id"], "title": mod.get("title") or "",
                            "url": url, "page": mod.get("url"), "ts": now_ts()}

    def clear_pending(self) -> None:
        with self.lock:
            self.pending = None

    def _take_pending(self) -> dict | None:
        with self.lock:
            if not self.pending:
                return None
            age = now_ts() - self.pending["ts"]
            if age > 20 * 60:
                self.pending = None
                return None
            return self.pending

    # ---------- слежка ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="beamhub-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        ticks = 0
        while not self._stop.is_set():
            try:
                self._scan_once()
                ticks += 1
                if ticks % 30 == 0:  # раз в минуту сверяем библиотеку с папкой модов
                    installer.scan_library()
                    installer.hash_missing(2)  # и досчитываем хеши для поиска дубликатов
            except Exception as exc:  # noqa: BLE001
                print(f"[beamhub] watcher: {exc}")
            self._stop.wait(2.0)

    def _archive_files(self, folder: Path) -> list[Path]:
        try:
            return [p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in ARCHIVE_SUFFIXES]
        except OSError:
            return []

    def _scan_once(self) -> None:
        cfg = config.load()
        folder = Path(cfg.get("downloads_dir") or Path.home() / "Downloads")
        if not folder.is_dir():
            return

        if self._baseline is None or self._baseline_dir != str(folder).lower():
            # при старте (или смене папки) запоминаем, что уже лежало в загрузках
            self._baseline = {str(p).lower() for p in self._archive_files(folder)}
            self._baseline_dir = str(folder).lower()
            self._seen.clear()
            self._stable.clear()
            return

        fresh: list[Path] = []
        for entry in self._archive_files(folder):
            key = str(entry).lower()
            if key in self._baseline:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            signature = (stat.st_size, int(stat.st_mtime))
            if self._seen.get(key) != signature:
                self._seen[key] = signature
                self._stable[key] = 0
                continue
            self._stable[key] = self._stable.get(key, 0) + 1
            if self._stable[key] >= 2:
                fresh.append(entry)

        if not fresh:
            return
        if len(fresh) > 3:
            # кто-то скинул сразу много архивов: молча ставить не будем
            for entry in fresh:
                self._baseline.add(str(entry).lower())
            self.emit("inbox", f"Нашёл {len(fresh)} новых архивов — поставьте их вручную во вкладке «Загрузки».")
            return

        for entry in fresh:
            if self._handle_new_archive(entry, cfg):
                self._baseline.add(str(entry).lower())
            else:
                # файл ещё пишется или недокачан — вернёмся к нему на следующем круге
                self._stable.pop(str(entry).lower(), None)
                self._seen.pop(str(entry).lower(), None)

    def _handle_new_archive(self, path: Path, cfg: dict) -> bool:
        """True — файл обработан (или окончательно отброшен), False — попробуем ещё раз."""
        pending = self._take_pending()
        if path.suffix.lower() not in {".zip"}:
            self.emit("warn", f"«{path.name}» — не ZIP. Нужен 7-Zip, чтобы установить такой архив.",
                      file=str(path))
            return True
        info = installer.inspect_archive(path)
        if not info["valid"]:
            if info.get("error", "").startswith(("bad_zip", "empty")):
                return False  # архив ещё не дописан
            if pending:
                self.emit("warn", f"«{path.name}» не похож на мод BeamNG.drive — пропустил.")
            return True
        if not cfg.get("auto_install", True) and not pending:
            self.emit("inbox", f"Новый архив: {path.name} ({human_size(info['size'])}) — можно установить.",
                      file=str(path))
            return True

        mod = None
        if pending:
            mod = db.get_mod(pending["mod_id"]) or {"id": pending["mod_id"],
                                                   "title": pending["title"],
                                                   "url": pending.get("page") or ""}
        result = installer.install_archive(path, mod, cfg)
        if result["status"] == "installed":
            title = result["installed"]["title"]
            self.emit("installed", f"Установлено: {title}", mod_id=(mod or {}).get("id"),
                      filename=result["installed"]["filename"])
            self.clear_pending()
            try:
                from . import matcher

                threading.Thread(target=matcher.match_all, daemon=True).start()
            except Exception:  # noqa: BLE001
                pass
            return True
        if result["status"] == "duplicate":
            self.emit("duplicate", f"«{path.stem}» уже установлен — пропустил.", mod_id=(mod or {}).get("id"))
            self.clear_pending()
            return True
        self.emit("error", f"Не удалось установить «{path.name}»: {result.get('message', '')}")
        return False

    # ---------- инбокс ----------
    def install_all_inbox(self) -> dict:
        """Поставить всё, что лежит в «Загрузках» и похоже на мод."""
        if self._bulk_running:
            return {"started": False, "reason": "Установка уже идёт"}
        items = [item for item in self.inbox() if item.get("valid")]
        if not items:
            return {"started": False, "reason": "В «Загрузках» нет архивов модов"}
        # сначала ставим файлы с «чистым» именем, копии браузера «(1)» уйдут в пропуск
        items.sort(key=lambda item: (bool(COPY_SUFFIX.search(Path(item["name"]).stem)),
                                     item["name"].lower()))
        self._bulk_running = True
        threading.Thread(target=self._install_all_worker, args=(items,), daemon=True).start()
        return {"started": True, "total": len(items),
                "size": sum(int(item.get("size") or 0) for item in items)}

    def _install_all_worker(self, items: list[dict]) -> None:
        installed = skipped = failed = 0
        cfg = config.load()
        move = bool(cfg.get("move_from_downloads"))
        seen: dict[tuple[str, int], str] = {}
        for row in db.installed_list():
            key = (title_key(row.get("title") or row["filename"]), int(row.get("size") or 0))
            seen.setdefault(key, row["filename"])

        try:
            for index, item in enumerate(items, start=1):
                name = item["name"]
                key = (title_key(name), int(item.get("size") or 0))
                if key in seen:
                    skipped += 1
                    self.emit("progress", f"[{index}/{len(items)}] «{name}» — уже есть: "
                                          f"{seen[key]}", done=index, total=len(items))
                    continue
                self.emit("progress", f"[{index}/{len(items)}] ставим «{name}»…",
                          done=index - 1, total=len(items))
                try:
                    result = installer.install_archive(Path(item["path"]), None, cfg,
                                                       move_archive=move)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.emit("error", f"«{name}»: {exc}")
                    continue
                if result["status"] == "installed":
                    installed += 1
                    seen[key] = name
                    self.emit("installed", f"Установлено: {result['installed']['title']}")
                elif result["status"] == "duplicate":
                    skipped += 1
                else:
                    failed += 1
                    self.emit("error", f"«{name}»: {result.get('message', 'не установился')}")
        finally:
            self._bulk_running = False
            self._inbox_cache.clear()
            try:
                from . import matcher

                matcher.match_all()
            except Exception:  # noqa: BLE001
                pass
            self.emit("inbox_done",
                      f"Готово: установлено {installed}, пропущено {skipped}, ошибок {failed}",
                      installed=installed, skipped=skipped, failed=failed, total=len(items))

    def inbox(self) -> list[dict]:
        cfg = config.load()
        folder = Path(cfg.get("downloads_dir") or Path.home() / "Downloads")
        if not folder.is_dir():
            return []
        installed_paths = {str(Path(row["path"])).lower() for row in db.installed_list()}
        items = []
        for entry in folder.iterdir():
            if not entry.is_file() or entry.suffix.lower() not in ARCHIVE_SUFFIXES:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            key = str(entry).lower()
            if key in installed_paths:
                continue
            signature = (stat.st_size, int(stat.st_mtime))
            cached = self._inbox_cache.get(key)
            if cached and cached[0] == signature:
                info = cached[1]
            else:
                info = installer.inspect_archive(entry)
                self._inbox_cache[key] = (signature, info)
            items.append({
                "path": str(entry),
                "name": entry.name,
                "size": stat.st_size,
                "size_text": human_size(stat.st_size),
                "modified": int(stat.st_mtime),
                "kind": info.get("kind", "other"),
                "valid": info.get("valid", False),
                "error": info.get("error", ""),
            })
        items.sort(key=lambda x: x["modified"], reverse=True)
        return items


HUB = Hub()


def open_in_browser(url: str) -> None:
    """Открыть ссылку в системе. Только http(s) и mailto — никаких file:/ms-*."""
    from .security import safe_web_url

    if not safe_web_url(url):
        print(f"[beamhub] отказался открывать {url[:80]!r}")
        return
    os.startfile(url)  # type: ignore[attr-defined]
