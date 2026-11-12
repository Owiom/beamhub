"""Проверка установщика без риска для настоящей игры: всё в temp-папке."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)
os.environ["BEAMHUB_DB"] = str(TMP_ROOT / "test-install.db")
if Path(os.environ["BEAMHUB_DB"]).exists():
    Path(os.environ["BEAMHUB_DB"]).unlink()

from core import db, installer  # noqa: E402


def make_zip(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def main() -> int:
    failures = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(("  OK   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    tmp_root = TMP_ROOT
    tmp_path = tmp_root / f"test_{os.getpid()}"
    if tmp_path.exists():
        shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True)
    tmp = str(tmp_path)
    try:
        root = Path(tmp)
        mods_dir = root / "mods"
        mods_dir.mkdir()
        cfg = {"mods_dir": str(mods_dir), "downloads_dir": str(root / "dl"), "auto_install": True}
        (root / "dl").mkdir()

        print("[1] обычный мод машин")
        car = root / "car.zip"
        make_zip(car, {
            "vehicles/mycar/info.json": '{"name":"My Car"}',
            "vehicles/mycar/mycar.jbeam": "{}",
            "art/shapes/mycar.dae": "x",
        })

        print("[2] мод с обёрткой в папке")
        wrapped = root / "wrapped.zip"
        make_zip(wrapped, {
            "MyMap/levels/mymap/info.json": "{}",
            "MyMap/levels/mymap/main.mission": "{}",
        })

        print("[3] карта без обёртки (корень levels/)")
        plain_map = root / "plain.zip"
        make_zip(plain_map, {
            "levels/bigmap/info.json": "{}",
            "levels/bigmap/art/cubemaps/x.dds": "y",
        })

        print("[4] не мод")
        junk = root / "junk.zip"
        make_zip(junk, {"notes.txt": "hello", "music/song.mp3": "x"})

        info = installer.inspect_archive(car)
        check("машина распознана как cars", info["valid"] and info["kind"] == "cars", str(info))
        info = installer.inspect_archive(wrapped)
        check("карта с обёрткой распознана", info["valid"] and info["kind"] == "maps", str(info))
        check("обёртка определена", info["root"] == "MyMap", str(info["root"]))
        info = installer.inspect_archive(plain_map)
        check("levels/ не считается обёрткой", info["valid"] and info["kind"] == "maps", str(info))
        info = installer.inspect_archive(junk)
        check("мусор отклонён", not info["valid"], str(info))

        result = installer.install_archive(car, {"id": "test:1", "title": "My Car", "url": "u"}, cfg)
        check("установка прошла", result["status"] == "installed", str(result))
        target = mods_dir / "My Car.zip"
        check("файл в папке модов", target.exists(), str(list(mods_dir.iterdir())))
        if target.exists():
            with zipfile.ZipFile(target) as zf:
                check("структура сохранена", "vehicles/mycar/info.json" in zf.namelist())
        check("запись в библиотеке", db.installed_by_key("my car.zip") is not None)

        again = installer.install_archive(car, {"id": "test:1", "title": "My Car"}, cfg)
        check("дубликат по хешу отклонён", again["status"] == "duplicate", str(again))

        result = installer.install_archive(wrapped, {"id": "test:2", "title": "My Map"}, cfg)
        check("карта с обёрткой установлена", result["status"] == "installed", str(result))
        map_zip = mods_dir / "My Map.zip"
        if map_zip.exists():
            with zipfile.ZipFile(map_zip) as zf:
                names = zf.namelist()
            check("обёртка срезана", "levels/mymap/info.json" in names, str(names[:3]))

        bad = installer.install_archive(junk, None, cfg)
        check("мусор не установлен", bad["status"] == "error", str(bad))

        row = db.installed_by_key("my car.zip")
        toggled = installer.toggle(row, cfg)
        check("выключение работает", toggled["status"] == "ok" and not (mods_dir / "My Car.zip").exists())
        disabled = root / "mods_disabled" / "My Car.zip"
        check("файл в mods_disabled", disabled.exists(), str(disabled))
        row = db.installed_by_key("my car.zip")
        toggled = installer.toggle(row, cfg)
        check("включение обратно", toggled["status"] == "ok" and (mods_dir / "My Car.zip").exists())

        row = db.installed_by_key("my car.zip")
        removed = installer.remove(row, cfg)
        check("удаление работает", removed["status"] == "ok" and not (mods_dir / "My Car.zip").exists())
        check("запись убрана", db.installed_by_key("my car.zip") is None)

        outside = root / "outside.zip"
        make_zip(outside, {"vehicles/x/info.json": "{}"})
        fake = {"key": "outside.zip", "path": str(outside), "filename": "outside.zip"}
        check("удаление вне папки модов запрещено", installer.remove(fake, cfg)["status"] == "error")
        check("файл не тронут", outside.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("ПРОВАЛОВ:", len(failures) if failures else "нет")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
