"""Пакетное обогащение каталога (для отладки из консоли).

    py tools/enrich_batch.py --limit 60
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402
from core.providers import enrich  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    db.init()
    ids = db.pending_enrich(limit=args.limit)
    print(f"К обогащению: {len(ids)}")
    queue = list(ids)
    done = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal done
        while True:
            with lock:
                if not queue:
                    return
                mod_id = queue.pop(0)
            mod = db.get_mod(mod_id)
            if mod:
                try:
                    data = enrich(mod)
                    if data:
                        data.pop("id", None)
                        db.update_mod(mod_id, **data)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! {mod_id}: {exc}")
            with lock:
                done += 1
                if done % 10 == 0:
                    print(f"  {done}/{len(ids)}")

    started = time.time()
    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"Готово за {time.time() - started:.1f} c, обогащено {done}")


if __name__ == "__main__":
    main()
