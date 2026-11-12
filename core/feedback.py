"""Обратная связь: собираем отчёт и открываем GitHub Issue или письмо.

Игроку не нужно искать лог и вспоминать версию — всё подставляется само,
а перед отправкой он видит текст и может убрать лишнее.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import config, db
from .version import VERSION

REPO = "Owiom/beamhub"
ISSUE_URL = f"https://github.com/{REPO}/issues/new"
MAX_URL = 7000


def _log_paths() -> list[Path]:
    paths = [config.DATA_DIR / "beamhub.log"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.append(Path(local) / "BeamHub" / "beamhub.log")
    return paths


def log_tail(lines: int = 30, max_chars: int = 2200) -> str:
    """Последние осмысленные строки лога — без шума http-запросов."""
    text = ""
    for path in _log_paths():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            break
    if not text.strip():
        return ""
    meaningful = [line for line in text.splitlines() if not line.startswith("[http]")]
    if not meaningful:
        meaningful = text.splitlines()
    tail = "\n".join(meaningful[-lines:])
    return tail[-max_chars:]


def _counts() -> tuple[int, int]:
    try:
        return db.count_mods(), len(db.installed_list())
    except Exception:  # noqa: BLE001
        return 0, 0


def diagnostics(include_paths: bool = True) -> list[str]:
    cfg = config.load()
    mods, installed = _counts()
    rows = [
        f"- BeamHub: {VERSION}",
        f"- Windows: {platform.release()} ({platform.version()})",
        f"- Сборка: {'exe' if config.is_frozen() else 'из исходников'}, Python {platform.python_version()}",
        f"- Модов в каталоге: {mods}, в библиотеке: {installed}",
        f"- Источник обновлений: {cfg.get('update_repo') or 'не задан'}",
    ]
    if include_paths:
        rows.append(f"- Игра: {cfg.get('game_path') or 'не найдена'}")
        rows.append(f"- Папка модов: {cfg.get('mods_dir') or 'не найдена'}")
    return rows


def build_report(note: str = "", include_paths: bool = True, include_log: bool = True) -> str:
    """Текст отчёта: для GitHub — markdown, для письма — обычный текст."""
    parts = ["### Что случилось", note.strip() or "(опиши в двух словах: что делал и что пошло не так)", ""]
    parts.append("### Окружение")
    parts.extend(diagnostics(include_paths))
    if include_log:
        tail = log_tail()
        if tail:
            parts.extend(["", "<details><summary>Лог (последние строки)</summary>", "",
                          "```", tail, "```", "", "</details>"])
    parts.extend(["", "---", f"Отправлено из BeamHub {VERSION}"])
    return "\n".join(parts)


def plain_report(report: str) -> str:
    """Убираем markdown-обёртки для письма."""
    lines = []
    for line in report.splitlines():
        if line.startswith("<details") or line.startswith("</details") or line.strip() == "```":
            continue
        if line.startswith("### "):
            lines.append(line[4:].upper())
            continue
        lines.append(line)
    return "\n".join(lines)


def subject(note: str = "") -> str:
    first = (note.strip().splitlines() or [""])[0][:80]
    return f"BeamHub {VERSION}: {first}" if first else f"BeamHub {VERSION}: обратная связь"


def issue_link(report: str, title: str) -> str:
    body = report[:MAX_URL]
    return f"{ISSUE_URL}?title={quote(title)}&body={quote(body)}"


def mail_link(email: str, report: str, title: str) -> str:
    if not email:
        return ""
    return f"mailto:{email}?subject={quote(title)}&body={quote(plain_report(report)[:MAX_URL])}"


def send_report(endpoint: str, report: str, title: str = "", contact: str = "") -> dict:
    """Отправить отчёт прямо из приложения на настроенный приёмник.

    Приёмник — наш собственный эндпоинт (Cloudflare Worker и подобное):
    он держит токен бота/почты у себя, поэтому в exe никаких секретов нет.
    """
    import json
    import urllib.error
    import urllib.request

    url = (endpoint or "").strip()
    if not url:
        return {"ok": False, "message": "Приёмник обратной связи не настроен."}
    host = urllib.parse.urlsplit(url).hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1")
    if not url.startswith("https://") and not local:
        return {"ok": False, "message": "Приёмник должен работать по https."}

    payload = json.dumps({
        "title": title or subject(),
        "report": report[:12000],
        "contact": contact[:120],
        "version": VERSION,
        "honeypot": "",
    }, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": f"BeamHub/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            answer = resp.read(600).decode("utf-8", "replace")
        return {"ok": True, "message": "Отчёт отправлен.", "answer": answer}
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace")
        return {"ok": False, "message": f"Приёмник ответил {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Не удалось отправить: {exc}"}
