"""Окно приложения: Chromium-движок системы (Edge/Chrome) в режиме --app.

Это отдельное окно без вкладок, адресной строки и панели закладок — снаружи
не отличить от обычного десктопного приложения. Внутри — наш локальный интерфейс.

Почему так: встроить WebView2 напрямую мешает связка pythonnet + .NET, которая
в сборке PyInstaller не поднимается. Режим --app даёт тот же движок и тот же
результат без хрупких зависимостей.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

WINDOW_SIZE = (1440, 920)

_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_browser() -> str | None:
    for path in _CANDIDATES:
        if path and Path(path).exists():
            return path
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    return find_browser() is not None


def _clean_env() -> dict:
    """Окружение для браузера: убираем следы PyInstaller, иначе Chromium ловит чужие DLL."""
    import sys

    env = dict(os.environ)
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and meipass not in p]
        env["PATH"] = os.pathsep.join(parts)
    for key in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2", "PYTHONSTARTUP"):
        env.pop(key, None)
    return env


def launch(url: str, profile_dir: Path, size: tuple[int, int] = WINDOW_SIZE,
           app_id: str = "BeamHub") -> subprocess.Popen | None:
    """Запускает окно приложения. Возвращает процесс (None, если браузера нет)."""
    exe = find_browser()
    if not exe:
        return None
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={size[0]},{size[1]}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-background-networking",
        "--disable-features=Translate,MediaRouter",
        "--disable-sync",
        f"--app-name={app_id}",
    ]
    return subprocess.Popen(command, close_fds=True, env=_clean_env())


def is_alive(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def kill(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass
