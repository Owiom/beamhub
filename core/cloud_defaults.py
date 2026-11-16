"""Владельческие настройки облака: ключи, вшитые в сборку.

Обычный игрок не должен ничего настраивать. Владелец один раз указывает
Project URL и anon key — они попадают в `cloud_defaults.json` рядом с BeamHub.exe
(или в переменные окружения), и у всех пользователей остаётся одна кнопка «Войти».
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import config

FILE_NAME = "cloud_defaults.json"


def _roots() -> list[Path]:
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    roots.append(Path.cwd())
    return roots


def load() -> tuple[str, str]:
    """Вшитые значения: переменные окружения важнее файла."""
    url = (os.environ.get("BEAMHUB_CLOUD_URL") or "").strip().rstrip("/")
    key = (os.environ.get("BEAMHUB_CLOUD_ANON") or "").strip()
    if url and key:
        return url, key
    if os.environ.get("BEAMHUB_NO_CLOUD_DEFAULTS") == "1":
        return "", ""
    for root in _roots():
        candidate = root / FILE_NAME
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        file_url = (data.get("url") or "").strip().rstrip("/")
        file_key = (data.get("anon_key") or "").strip()
        if file_url and file_key:
            return file_url, file_key
    return "", ""


def baked() -> bool:
    url, key = load()
    return bool(url and key)


def describe() -> str:
    url, _key = load()
    host = url.split("//", 1)[-1] if url else ""
    return host
