"""Совместимость модов: доступность файла, версия игры, конфликты, обновления.

Честность важнее оптимизма: мы различаем «проверено BeamHub» (реально сходили
по ссылке), «заявлено автором» (версия игры из карточки) и «неизвестно»
(файлообменник не отдаёт HEAD). Никаких выводов «подтверждено совместимо» без фактов.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config, db
from .util import now_ts

CHECK_TTL = 6 * 3600          # результат проверки ссылки живёт 6 часов
TIMEOUT = 8
UA = "BeamHub-compat/1.0"


def _cache_key(mod_id: str) -> str:
    return f"compat:file:{mod_id}"


def _load_cache(mod_id: str) -> dict | None:
    raw = db.get_meta(_cache_key(mod_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if now_ts() - int(data.get("checked_at") or 0) > CHECK_TTL:
        return None
    return data


def check_file(url: str, mod_id: str = "", force: bool = False) -> dict:
    """Проверить ссылку на файл. Кешируем, чтобы не дёргать сайт при каждом открытии."""
    if not url:
        return {"status": "unknown", "detail": "в карточке нет ссылки на файл"}
    if mod_id and not force:
        cached = _load_cache(mod_id)
        if cached:
            return cached
    result = {"status": "unknown", "detail": "", "checked_at": now_ts()}
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            result["status"] = "ok"
            result["code"] = response.status
            result["detail"] = "файл отвечает"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405, 501):
            result.update(status="unknown", code=exc.code,
                          detail="сайт не отдаёт HEAD — проверить нельзя")
        elif exc.code in (404, 410):
            result.update(status="missing", code=exc.code, detail="файл не найден")
        else:
            result.update(status="unknown", code=exc.code, detail=f"ответ сайта {exc.code}")
    except Exception as exc:  # noqa: BLE001
        result.update(status="unknown", detail=f"нет ответа: {type(exc).__name__}")
    if mod_id:
        db.set_meta(_cache_key(mod_id), json.dumps(result, ensure_ascii=False))
    return result


def game_version() -> str | None:
    """Версия игры из папки установки, если её можно прочитать."""
    cfg = config.load()
    path = cfg.get("game_path") or ""
    if not path:
        return None
    from pathlib import Path

    for name in ("version.txt", "GameVersion.txt", "version.json"):
        candidate = Path(path) / name
        if candidate.exists():
            try:
                return candidate.read_text(encoding="utf-8", errors="replace").strip()[:40]
            except OSError:
                continue
    return None


def _installed_same_title(mod: dict) -> list[dict]:
    from .util import title_key

    key = mod.get("title_key") or title_key(mod.get("title") or "")
    if not key:
        return []
    rows = db.query("SELECT mod_id, filename, title, version FROM installed")
    conflicts = []
    for row in rows:
        if row.get("mod_id") == mod["id"]:
            continue
        if title_key(row.get("title") or row.get("filename") or "") == key:
            conflicts.append(row)
    return conflicts


def _installed_by_author(mod: dict) -> list[dict]:
    author = (mod.get("author") or "").strip().lower()
    if not author:
        return []
    rows = db.query(
        "SELECT i.mod_id, i.filename, i.title FROM installed i JOIN mods m ON m.id = i.mod_id "
        "WHERE lower(m.author) = ? AND i.mod_id <> ?", (author, mod["id"]))
    return rows


def report(mod_id: str, force: bool = False) -> dict:
    mod = db.get_mod(mod_id)
    if not mod:
        return {"ok": False, "error": "Мод не найден."}
    file_info = check_file(mod.get("download_url") or mod.get("url") or "", mod_id, force)
    declared = mod.get("game_version") or ""
    local_game = game_version()
    conflicts = _installed_same_title(mod) + _installed_by_author(mod)
    seen = set()
    unique_conflicts = []
    for row in conflicts:
        key = (row.get("mod_id"), row.get("filename"))
        if key in seen:
            continue
        seen.add(key)
        unique_conflicts.append(row)
    verdict = {
        "file": "проверено BeamHub" if file_info["status"] in ("ok", "missing") else "неизвестно",
        "game": "заявлено автором" if declared else "не указано",
        "conflicts": "найдены" if unique_conflicts else "не найдено",
    }
    match = None
    if declared and local_game:
        declared_digits = "".join(ch for ch in declared if ch.isdigit() or ch == ".")
        local_digits = "".join(ch for ch in local_game if ch.isdigit() or ch == ".")
        if declared_digits and local_digits:
            match = declared_digits[:4] == local_digits[:4]
    return {
        "ok": True,
        "mod": {"id": mod["id"], "title": mod.get("title"), "version": mod.get("version")},
        "file": file_info,
        "game": {"declared": declared, "installed": local_game, "matches": match},
        "conflicts": unique_conflicts,
        "verdict": verdict,
        "checked_at": now_ts(),
    }
