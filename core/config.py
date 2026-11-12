"""Настройки BeamHub: пути к игре, папкам и флаги поведения."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True, если запущены из собранного BeamHub.exe."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Папка с ресурсами (web/, seed/): рядом с исходниками или внутри exe."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Данные (база, настройки, кеш): рядом с исходниками, в %LOCALAPPDATA%\\BeamHub
    или в папке из переменной BEAMHUB_DATA (портативный режим)."""
    override = os.environ.get("BEAMHUB_DATA")
    if override:
        base = Path(override)
    elif is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BeamHub"
    else:
        base = resource_dir() / "data"

    candidates = [base]
    if is_frozen():
        # портативный режим: если рядом с exe можно писать — храним данные там
        candidates.append(Path(sys.executable).resolve().parent / "BeamHubData")
    candidates += [Path.home() / "BeamHub", Path.home() / "AppData" / "Roaming" / "BeamHub",
                   Path(os.environ.get("TEMP") or ".") / "BeamHub"]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    raise RuntimeError("Не нашёл папку для данных: %LOCALAPPDATA%, домашняя папка и TEMP недоступны")


APP_DIR = resource_dir()
DATA_DIR = _data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS = {
    "mods_dir": "",
    "downloads_dir": str(Path.home() / "Downloads"),
    "auto_install": True,
    # после установки переносить архив из «Загрузок» в папку модов (а не копировать)
    "move_from_downloads": False,
    "game_path": "",
    "enrich_workers": 4,
    "language": "ru",
    # GitHub-репозиторий для обновлений: "владелец/репозиторий"
    "update_repo": "Owiom/beamhub",
    # почта автора: кнопка «Написать без аккаунта» в обратной связи
    "feedback_email": "sparco56@gmail.com",
    # https-приёмник для отправки отчёта прямо из приложения (Cloudflare Worker и т.п.)
    "feedback_endpoint": "",
    "auto_check_updates": True,
}

_cached = None


def _steam_libraries() -> list[Path]:
    libs: list[Path] = []
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    ]
    for steam in candidates:
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('"path"'):
                continue
            parts = line.split('"')
            if len(parts) >= 4:
                libs.append(Path(parts[3]))
        if steam not in libs:
            libs.append(steam)
    return libs


def find_game_dir() -> str:
    """Ищем установленную BeamNG.drive через Steam-библиотеки."""
    for lib in _steam_libraries():
        game = lib / "steamapps" / "common" / "BeamNG.drive"
        if (game / "Bin64").exists() or (game / "content").exists():
            return str(game)
    return ""


def _candidate_mod_dirs() -> list[Path]:
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        base = Path(local) / "BeamNG"
        # BeamNG 0.40+: userdata в %LOCALAPPDATA%\BeamNG\BeamNG.drive\current
        out.append(base / "BeamNG.drive" / "current" / "mods")
        # более старые сборки: %LOCALAPPDATA%\BeamNG.drive\<версия>
        plain = Path(local) / "BeamNG.drive"
        if plain.exists():
            for sub in plain.iterdir():
                if sub.is_dir():
                    out.append(sub / "mods")
    doc = Path.home() / "Documents" / "BeamNG.drive" / "mods"
    out.append(doc)
    game = find_game_dir()
    if game:
        out.append(Path(game) / "mods")
        out.append(Path(game) / "content" / "mods")
    return out


def detect_mods_dir() -> str:
    """Выбираем папку модов: сначала та, где уже есть db.json, потом самая свежая."""
    best = None
    best_score = -1
    for cand in _candidate_mod_dirs():
        if not cand.is_dir():
            continue
        score = 0
        if (cand / "db.json").exists():
            score += 100
        try:
            score += len(list(cand.glob("*.zip")))
        except OSError:
            pass
        if score > best_score:
            best, best_score = cand, score
    if best is not None:
        return str(best)
    # папки ещё нет — предлагаем путь по умолчанию, создадим при установке
    for cand in _candidate_mod_dirs():
        if cand.parent.exists():
            return str(cand)
    return ""


def _migrate(data: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS or k.startswith("providers")})
    # пустое значение в старом settings.json не должно затирать новый дефолт
    # (например, почту обратной связи, появившуюся в свежей версии)
    for key, default in DEFAULTS.items():
        if default and not merged.get(key):
            merged[key] = default
    return merged


def load() -> dict:
    global _cached
    if _cached is not None:
        return _cached
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    cfg = _migrate(data)
    changed = False
    if not cfg["mods_dir"]:
        cfg["mods_dir"] = detect_mods_dir()
        changed = True
    if not cfg["game_path"]:
        cfg["game_path"] = find_game_dir()
        changed = True
    if not cfg["downloads_dir"] or not Path(cfg["downloads_dir"]).exists():
        cfg["downloads_dir"] = str(Path.home() / "Downloads")
        changed = True
    _cached = cfg
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> dict:
    global _cached
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    SETTINGS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    _cached = clean
    return clean


def update(patch: dict) -> dict:
    cfg = dict(load())
    for k, v in patch.items():
        if k in DEFAULTS:
            cfg[k] = v
    return save(cfg)


def ensure_mods_dir(cfg: dict) -> Path:
    raw = cfg.get("mods_dir") or detect_mods_dir()
    if not raw:
        raise RuntimeError("Не найдена папка модов BeamNG.drive. Укажите её в настройках.")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path
