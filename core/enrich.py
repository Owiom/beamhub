"""Фоновое обогащение каталога: тянем заголовки, картинки, размеры со страниц модов."""
from __future__ import annotations

import queue
import threading

from . import config, db
from .providers import enrich
from .state import HUB


def run_sync() -> None:
    from .providers import CATALOG_PROVIDERS

    total = 0
    for provider in CATALOG_PROVIDERS:
        try:
            total += provider.sync()
        except Exception as exc:  # noqa: BLE001
            HUB.emit("warn", f"Не удалось обновить каталог {provider.SOURCE}: {exc}")
    if total:
        HUB.emit("catalog", f"Каталог обновлён: +{total} модов")


def start_enrichment() -> None:
    ids = db.pending_enrich(limit=4000)
    if not ids:
        return
    if HUB.progress["running"]:
        return

    HUB.progress.update({"done": 0, "total": len(ids), "running": True})
    workers = max(1, min(6, int(config.load().get("enrich_workers", 4))))
    work: queue.Queue[str] = queue.Queue()
    for mod_id in ids:
        work.put(mod_id)

    def worker() -> None:
        while True:
            try:
                mod_id = work.get_nowait()
            except queue.Empty:
                return
            mod = db.get_mod(mod_id)
            if mod:
                try:
                    data = enrich(mod)
                    if data:
                        data.pop("id", None)
                        db.update_mod(mod_id, **data)
                except Exception as exc:  # noqa: BLE001
                    print(f"[enrich] {mod_id}: {exc}")
            with HUB.lock:
                HUB.progress["done"] += 1
            work.task_done()

    threads = [threading.Thread(target=worker, name=f"enrich-{i}", daemon=True) for i in range(workers)]
    for thread in threads:
        thread.start()

    def watcher() -> None:
        for thread in threads:
            thread.join()
        with HUB.lock:
            HUB.progress["running"] = False
        HUB.emit("catalog", "Метаданные каталога обновлены")

    threading.Thread(target=watcher, name="enrich-watch", daemon=True).start()
