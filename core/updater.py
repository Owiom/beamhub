"""Обновление «по воздуху» через GitHub Releases.

Проверка: api.github.com/repos/<owner>/<repo>/releases/latest.
Установка: скачиваем zip из ассетов, распаковываем рядом и запускаем cmd-скрипт,
который ждёт закрытия BeamHub, подменяет файлы и запускает новую версию.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import config
from .http import fetch
from .version import VERSION, version_tuple

API = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"


def repo() -> str:
    return (config.load().get("update_repo") or "").strip().strip("/")


def configured() -> bool:
    value = repo()
    return bool(value) and "/" in value and "ваш" not in value.lower()


def check() -> dict:
    """Возвращает {status, ...}. status: 'disabled' | 'current' | 'update' | 'error'."""
    if not configured():
        return {"status": "disabled", "message": "Репозиторий обновлений не указан в настройках.",
                "current": VERSION}

    url = API.format(repo=repo())
    try:
        resp = fetch(url, timeout=20, retries=1)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "404" in text:
            return {"status": "error", "current": VERSION,
                    "message": "В репозитории пока нет релизов — проверьте тег вида v0.6.0."}
        return {"status": "error", "message": f"Не удалось проверить обновления: {exc}",
                "current": VERSION}

    if not resp.ok:
        return {"status": "error", "message": f"GitHub ответил {resp.status}", "current": VERSION}

    try:
        data = json.loads(resp.text)
    except ValueError:
        return {"status": "error", "message": "Некорректный ответ GitHub", "current": VERSION}

    tag = (data.get("tag_name") or "").strip()
    latest = tag.lstrip("vV")
    if not latest:
        return {"status": "error", "message": "В релизе нет тега версии", "current": VERSION}

    info = {
        "current": VERSION,
        "latest": latest,
        "url": data.get("html_url") or RELEASES_PAGE.format(repo=repo()),
        "notes": (data.get("body") or "").strip()[:1500],
        "published": data.get("published_at") or "",
    }
    if version_tuple(latest) <= version_tuple(VERSION):
        return {"status": "current", "message": f"Установлена последняя версия {VERSION}", **info}

    asset = None
    for item in data.get("assets") or []:
        name = (item.get("name") or "").lower()
        if name.endswith(".zip"):
            asset = item
            break
    if asset is None:
        return {"status": "update", "message": f"Вышла версия {latest}", "asset_url": "",
                "installable": False, **info}

    return {"status": "update", "message": f"Вышла версия {latest}",
            "asset_url": asset.get("browser_download_url") or "",
            "asset_name": asset.get("name") or "",
            "digest": asset.get("digest") or "",
            "installable": True, **info}


def _install_dir() -> Path:
    if config.is_frozen():
        return Path(sys.executable).resolve().parent
    return config.APP_DIR


def _download(url: str, target: Path, timeout: int = 180) -> None:
    request = urllib.request.Request(url, headers={
        "User-Agent": "BeamHub-updater", "Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=timeout) as resp, open(target, "wb") as handle:
        shutil.copyfileobj(resp, handle, 1 << 16)


def _write_script(path: Path, install_dir: Path, staging: Path, new_root: Path, pid: int) -> None:
    body = f"""@echo off
chcp 65001 >nul
set PID={pid}
:wait
taskkill /PID %PID% /F >nul 2>&1
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
timeout /t 1 /nobreak >nul
xcopy "{new_root}\\*" "{install_dir}\\" /E /Y /I /Q >nul
if exist "{install_dir}\\BeamHub.exe" start "" "{install_dir}\\BeamHub.exe"
timeout /t 2 /nobreak >nul
rd /s /q "{staging}"
del "%~f0"
"""
    path.write_text(body, encoding="utf-8")


def apply_update(asset_url: str, version: str = "", digest: str = "") -> dict:
    """Скачать и установить обновление. Приложение должно закрыться сразу после вызова."""
    if not asset_url:
        return {"ok": False, "message": "В релизе нет zip-архива для обновления."}
    if not config.is_frozen():
        return {"ok": False, "message": "Автообновление работает только в собранной версии."}

    install_dir = _install_dir()
    staging = install_dir / "update_tmp"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive = staging / "update.zip"
        _download(asset_url, archive)

        if digest.startswith("sha256:"):
            import hashlib

            expected = digest.split(":", 1)[1].strip().lower()
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if expected and actual != expected:
                shutil.rmtree(staging, ignore_errors=True)
                return {"ok": False,
                        "message": "Контрольная сумма обновления не совпала — установка отменена."}

        unpacked = staging / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)

        candidates = [p for p in unpacked.iterdir() if p.is_dir()]
        new_root = candidates[0] if len(candidates) == 1 else unpacked
        if not (new_root / "BeamHub.exe").exists() and not (new_root / "_internal").exists():
            for path in unpacked.rglob("BeamHub.exe"):
                new_root = path.parent
                break

        script = staging / "apply.cmd"
        _write_script(script, install_dir, staging, new_root, os.getpid())
        subprocess.Popen(["cmd", "/c", str(script)],
                         creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
                         close_fds=True)
        return {"ok": True, "message": f"Обновление {version} скачано. BeamHub перезапустится."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Не удалось установить обновление: {exc}"}


def feedback_url() -> str:
    """mailto-ссылка с версией и путями — чтобы письмо было информативным."""
    from urllib.parse import quote

    cfg = config.load()
    email = (cfg.get("feedback_email") or "").strip()
    if not email:
        return ""
    subject = f"BeamHub {VERSION}"
    body = (
        "\n\n---\n"
        f"Версия: {VERSION}\n"
        f"ОС: {sys.platform} {os.environ.get('OS', '')}\n"
        f"Игра: {cfg.get('game_path') or 'не найдена'}\n"
        f"Папка модов: {cfg.get('mods_dir') or 'не найдена'}\n"
        f"Данные: {config.DATA_DIR}\n"
    )
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"
