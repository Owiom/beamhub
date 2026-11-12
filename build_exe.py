"""Сборка BeamHub.exe (один файл, без консоли).

    py build_exe.py
    py build_exe.py --onedir      # папка вместо одного файла (быстрее стартует)

Результат: dist/BeamHub.exe
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# на западных консолях (cp1252) русские print падают — принудительно UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
SEED_DB = SEED_DIR / "beamhub.db"
SOURCE_DB = ROOT / "data" / "beamhub.db"
ICON = ROOT / "beamhub.ico"


def prepare_seed() -> None:
    """Кладём в сборку готовый каталог, чтобы у пользователя всё было сразу."""
    SEED_DIR.mkdir(exist_ok=True)
    if SOURCE_DB.exists():
        shutil.copyfile(SOURCE_DB, SEED_DB)
        print(f"каталог для комплекта: {SOURCE_DB} -> {SEED_DB} "
              f"({SEED_DB.stat().st_size / 1024 ** 2:.1f} МБ)")
    elif SEED_DB.exists():
        print("использую существующий seed/beamhub.db")
    else:
        print("ВНИМАНИЕ: нет data/beamhub.db — exe соберётся с пустым каталогом")


def ensure_icon() -> None:
    if ICON.exists():
        return
    print("иконки нет, рисую…")
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)


def build(onefile: bool) -> Path:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "BeamHub",
        "--icon", str(ICON),
        "--add-data", f"{ROOT / 'web'};web",
        "--add-data", f"{SEED_DIR};seed",
        "--add-data", f"{ICON};.",
        "--collect-submodules", "core",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "pytest",
    ]
    args.append("--onefile" if onefile else "--onedir")
    args.append("--windowed")
    args.append(str(ROOT / "app.py"))

    print("\n" + " ".join(args) + "\n")
    subprocess.run(args, check=True, cwd=str(ROOT))
    exe = ROOT / "dist" / ("BeamHub.exe" if onefile else Path("BeamHub") / "BeamHub.exe")
    return exe


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка BeamHub.exe")
    parser.add_argument("--onedir", action="store_true", help="собрать папку вместо одного файла")
    args = parser.parse_args()

    prepare_seed()
    ensure_icon()
    exe = build(onefile=not args.onedir)
    if exe.exists():
        size = exe.stat().st_size / 1024 ** 2
        print(f"\nГОТОВО: {exe}  ({size:.1f} МБ)")
    else:
        print(f"\nНе нашёл результат: {exe}")
        sys.exit(1)


if __name__ == "__main__":
    main()
