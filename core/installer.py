"""Установка модов в BeamNG.drive: проверка архива, распаковка обёртки, библиотека."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from . import config, db
from .util import now_ts, safe_filename

WRAPPER_OK = ("levels", "vehicles", "ui", "lua", "scripts", "art", "content", "gameplay")
KNOWN_KINDS = {
    "levels": "maps",
    "vehicles": "cars",
    "ui": "tools",
    "lua": "tools",
    "scripts": "tools",
    "art": "other",
    "content": "other",
    "gameplay": "other",
}
SEVEN_ZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _zip_names(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return [n for n in zf.namelist() if not n.endswith("/")]


def _paths_are_safe(names: list[str]) -> bool:
    """Защита от zip-slip: никаких абсолютных путей и '..' внутри архива."""
    for name in names:
        candidate = Path(name.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts or (candidate.drive and ":" in name):
            return False
    return True


def _common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    first = names[0].split("/")[0]
    if not all(n.startswith(first + "/") for n in names):
        return ""
    if first.lower() in WRAPPER_OK:
        # это не обёртка, а корень мода (levels/, vehicles/, …) — не трогаем
        return ""
    return first


def inspect_archive(path: Path) -> dict:
    """Что за мод в архиве: карта/машина/приложение и нужно ли убрать обёртку."""
    result = {"valid": False, "kind": "other", "root": "", "files": 0, "size": path.stat().st_size,
              "error": ""}
    if path.suffix.lower() != ".zip":
        return {**result, "error": "not_zip"}
    try:
        names = _zip_names(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return {**result, "error": f"bad_zip: {exc}"}
    result["files"] = len(names)
    if not names:
        return {**result, "error": "empty"}
    if not _paths_are_safe(names):
        return {**result, "error": "unsafe_paths"}

    prefix = _common_prefix(names)
    rel = [n[len(prefix) + 1:] for n in names] if prefix else names
    result["root"] = prefix

    kinds = set()
    for name in rel:
        head = name.split("/")[0].lower()
        if head in KNOWN_KINDS:
            kinds.add(KNOWN_KINDS[head])
    has_info = any(n.lower() in ("info.json", "mod_info.json") for n in rel)
    if kinds:
        # приоритет: карта > машина > инструмент > прочее
        for candidate in ("maps", "cars", "tools", "other"):
            if candidate in kinds:
                result["kind"] = candidate
                break
        result["valid"] = True
    elif has_info:
        result["valid"] = True
        result["kind"] = "other"
    else:
        result["error"] = "unknown_layout"
    return result


def _repack_without_wrapper(archive: Path, prefix: str, target: Path) -> None:
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not name.startswith(prefix + "/"):
                continue
            if "__MACOSX" in name or name.endswith(".DS_Store"):
                continue
            inner = name[len(prefix) + 1:]
            if not _paths_are_safe([inner]):
                continue
            dst.writestr(inner, src.read(info.filename))


def _seven_zip() -> str | None:
    for candidate in SEVEN_ZIP_PATHS:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("7z")
    return found


def convert_to_zip(archive: Path, workdir: Path) -> Path:
    """RAR/7Z -> ZIP через 7-Zip, если он установлен."""
    exe = _seven_zip()
    if not exe:
        raise RuntimeError("Архив не ZIP. Установите 7-Zip — BeamHub научится открывать .rar и .7z.")
    out_dir = workdir / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, "x", "-y", f"-o{out_dir}", str(archive)],
                   check=True, capture_output=True, creationflags=0x08000000)
    target = workdir / (archive.stem + ".zip")
    root_dir = out_dir.resolve()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(out_dir):
            for name in files:
                full = Path(root) / name
                resolved = full.resolve()
                if not resolved.is_relative_to(root_dir):
                    raise RuntimeError("Архив содержит ссылки наружу — установка отменена")
                zf.write(full, resolved.relative_to(root_dir).as_posix())
    return target


def _unique_name(mods_dir: Path, wanted: str) -> str:
    stem = Path(wanted).stem
    candidate = f"{stem}.zip"
    counter = 2
    while (mods_dir / candidate).exists():
        candidate = f"{stem}_{counter}.zip"
        counter += 1
    return candidate


def _drop_previous(mod_id: str, keep_key: str, cfg: dict) -> None:
    """После установки новой версии убираем старые файлы того же мода."""
    for row in db.installed_for_mod(mod_id):
        if row["key"] == keep_key:
            continue
        remove(row, cfg)


def install_archive(path: Path, mod: dict | None = None, cfg: dict | None = None,
                    move_archive: bool = False) -> dict:
    """Главная операция: положить архив в папку модов и записать в библиотеку."""
    cfg = cfg or config.load()
    path = Path(path)
    if not path.exists():
        return {"status": "error", "message": "Файл не найден"}

    workroot = config.DATA_DIR / "tmp"
    workroot.mkdir(parents=True, exist_ok=True)
    workdir = workroot / f"job_{uuid.uuid4().hex[:10]}"
    workdir.mkdir()
    try:
        archive = path
        if path.suffix.lower() != ".zip":
            archive = convert_to_zip(path, workdir)

        info = inspect_archive(archive)
        if not info["valid"]:
            return {"status": "error",
                    "message": "Это не похоже на мод BeamNG.drive (нет levels/, vehicles/, ui/ или info.json)"}

        digest = sha256_file(archive)
        duplicate = db.installed_by_hash(digest)
        if duplicate:
            return {"status": "duplicate", "message": "Этот мод уже установлен",
                    "installed": duplicate}

        mods_dir = config.ensure_mods_dir(cfg)
        display_name = (mod or {}).get("title") or path.stem
        # обновление ставим на место старого файла, чтобы не плодить «_2»
        previous = db.installed_for_mod(mod["id"]) if mod and mod.get("id") else []
        reuse = previous[0]["filename"] if previous else ""
        filename = reuse or _unique_name(mods_dir, safe_filename(display_name) + ".zip")
        target = mods_dir / filename
        partial = mods_dir / (filename + ".part")

        if info["root"]:
            _repack_without_wrapper(archive, info["root"], partial)
        else:
            shutil.copyfile(archive, partial)
        os.replace(partial, target)

        entry = {
            "key": filename.lower(),
            "mod_id": (mod or {}).get("id"),
            "filename": filename,
            "path": str(target),
            "sha256": digest,
            "size": target.stat().st_size,
            "installed_at": now_ts(),
            "active": True,
            "source_url": (mod or {}).get("url", ""),
            "title": display_name,
            "version": (mod or {}).get("version", ""),
        }
        db.installed_add(entry)
        if mod and mod.get("id"):
            _drop_previous(mod["id"], entry["key"], cfg)
        if move_archive:
            try:
                if archive.parent.resolve() != mods_dir.resolve():
                    archive.unlink(missing_ok=True)
            except OSError:
                pass
        return {"status": "installed", "installed": entry, "kind": info["kind"]}
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def mods_disabled_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or config.load()
    mods_dir = config.ensure_mods_dir(cfg)
    disabled = mods_dir.parent / "mods_disabled"
    disabled.mkdir(parents=True, exist_ok=True)
    return disabled


def toggle(item: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    key = item["key"]
    path = Path(item["path"])
    if not path.exists():
        return {"status": "error", "message": "Файл не найден на диске"}
    if item.get("active"):
        target = mods_disabled_dir(cfg) / path.name
        shutil.move(str(path), str(target))
        db.installed_add({**item, "path": str(target), "active": False})
        return {"status": "ok", "active": False}
    mods_dir = config.ensure_mods_dir(cfg)
    target = mods_dir / path.name
    if target.exists():
        target = mods_dir / _unique_name(mods_dir, path.name)
    shutil.move(str(path), str(target))
    db.installed_add({**item, "path": str(target), "active": True, "key": target.name.lower()})
    if target.name.lower() != key:
        db.installed_remove(key)
    return {"status": "ok", "active": True}


def remove(item: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    path = Path(item["path"])
    allowed_roots = [config.ensure_mods_dir(cfg).resolve(), mods_disabled_dir(cfg).resolve()]
    try:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            return {"status": "error", "message": "Отказано: файл вне папки модов"}
        if resolved.exists():
            resolved.unlink()
    except OSError as exc:
        return {"status": "error", "message": f"Не удалось удалить: {exc}"}
    db.installed_remove(item["key"])
    return {"status": "ok"}


def _read_game_db(mods_dir: Path) -> dict:
    """db.json самой игры: там видно, что реально подключено."""
    path = mods_dir / "db.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return {}
    out = {}
    for key, value in (data.get("mods") or {}).items():
        filename = (value.get("filename") or "").lower()
        if not filename:
            continue
        title = ((value.get("modData") or {}).get("name")
                 or (value.get("modData") or {}).get("title") or key)
        out[filename] = title
    return out


def hash_missing(limit: int = 3) -> int:
    """Досчитываем sha256 для модов, добавленных в библиотеку не через приложение."""
    done = 0
    for row in db.installed_list():
        if row.get("sha256"):
            continue
        path = Path(row.get("path") or "")
        if not path.exists() or not path.is_file():
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            continue
        db.installed_add({**row, "sha256": digest})
        done += 1
        if done >= limit:
            break
    return done


def scan_library(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load()
    try:
        mods_dir = config.ensure_mods_dir(cfg)
    except RuntimeError:
        return db.installed_list()

    game_titles = _read_game_db(mods_dir)
    on_disk = {p.name.lower(): p for p in mods_dir.glob("*.zip") if p.is_file()}
    known = {row["key"]: row for row in db.installed_list()}

    for key, path in on_disk.items():
        if key in known:
            row = known[key]
            if row["path"] != str(path):
                db.installed_add({**row, "path": str(path)})
            continue
        db.installed_add({
            "key": key,
            "mod_id": None,
            "filename": path.name,
            "path": str(path),
            "sha256": "",
            "size": path.stat().st_size,
            "installed_at": int(path.stat().st_mtime),
            "active": True,
            "source_url": "",
            "title": game_titles.get(key) or path.stem.replace("_", " "),
        })

    for key, row in known.items():
        if row.get("active") and key not in on_disk and not Path(row["path"]).exists():
            db.installed_remove(key)
    return db.installed_list()
