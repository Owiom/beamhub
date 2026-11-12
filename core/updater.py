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


def staged_dir() -> Path:
    return _install_dir() / "update_new"


def cleanup_staged() -> None:
    """Остатки неудачного обновления не должны мешать следующему запуску."""
    if staged_dir().exists():
        shutil.rmtree(staged_dir(), ignore_errors=True)


def _helper_script() -> str:
    return r"""param(
    [int]$TargetPid,
    [string]$From,
    [string]$To,
    [string]$Stage
)
$ErrorActionPreference = "SilentlyContinue"

for ($i = 0; $i -lt 300; $i++) {
    if (-not (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 1

$exe = Join-Path $To "BeamHub.exe"
for ($try = 0; $try -lt 12; $try++) {
    Copy-Item -Path (Join-Path $From "*") -Destination $To -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $exe) { break }
    Start-Sleep -Seconds 2
}
Start-Process -FilePath $exe -WorkingDirectory $To
Start-Sleep -Seconds 2
Remove-Item -Path $Stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def stage_update(asset_url: str, version: str = "", digest: str = "") -> dict:
    """Скачать обновление, распаковать рядом и запустить подменяющий скрипт.

    Свои загруженные DLL перезаписать нельзя, поэтому файлы меняет отдельный
    процесс (без окна), который дожидается нашего выхода.
    """
    if not asset_url:
        return {"ok": False, "message": "В релизе нет zip-архива для обновления."}
    if not config.is_frozen():
        return {"ok": False, "message": "Автообновление работает только в собранной версии."}

    staging = staged_dir()
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
        if not (new_root / "BeamHub.exe").exists():
            for path in unpacked.rglob("BeamHub.exe"):
                new_root = path.parent
                break
        if not (new_root / "BeamHub.exe").exists():
            shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "message": "В архиве обновления нет BeamHub.exe."}

        archive.unlink(missing_ok=True)
        script = Path(tempfile.gettempdir()) / f"beamhub-update-{os.getpid()}.ps1"
        script.write_text(_helper_script(), encoding="utf-8-sig")
        powershell = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                      / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        subprocess.Popen(
            [str(powershell), "-NoProfile", "-NoLogo", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-TargetPid", str(os.getpid()),
             "-From", str(new_root), "-To", str(_install_dir()), "-Stage", str(staging)],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
        )
        return {"ok": True,
                "message": f"Обновление {version} скачано. BeamHub перезапустится сам."}
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "message": f"Не удалось скачать обновление: {exc}"}


def apply_staged() -> bool:
    """Подменить файлы скачанным обновлением. Вызывать при старте, до создания окна."""
    if not config.is_frozen():
        return False
    install = _install_dir()
    # хвосты прошлых обновлений удаляем, если они уже не заняты
    for leftover in (install / "_internal.old", install / "BeamHub.exe.old"):
        try:
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            elif leftover.exists():
                leftover.unlink(missing_ok=True)
        except OSError:
            pass

    if not (staged_dir() / "pending.json").exists():
        return False
    new_root = staged_dir() / "BeamHub"
    if not (new_root / "BeamHub.exe").exists():
        cleanup_staged()
        return False

    exe = Path(sys.executable).resolve()
    try:
        internal = install / "_internal"
        if internal.exists():
            internal.rename(install / "_internal.old")
        if exe.exists():
            exe.rename(install / "BeamHub.exe.old")
        shutil.copytree(new_root, install, dirs_exist_ok=True)
        print("[beamhub] обновление установлено, перезапускаюсь")
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] не удалось применить обновление: {exc}")
        cleanup_staged()
        return False
    finally:
        shutil.rmtree(staged_dir(), ignore_errors=True)

    try:
        subprocess.Popen([str(exe), *sys.argv[1:]], cwd=str(install), close_fds=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[beamhub] перезапуск не удался: {exc}")
    os._exit(0)


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

        script = Path(tempfile.gettempdir()) / f"beamhub-update-{os.getpid()}.ps1"
        _write_script(script)
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        command = [
            str(powershell), "-NoProfile", "-NoLogo", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script),
            "-TargetPid", str(os.getpid()), "-From", str(new_root), "-To", str(install_dir),
        ]
        subprocess.Popen(command, creationflags=0x00000008 | 0x00000200,  # DETACHED | NEW_GROUP
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
