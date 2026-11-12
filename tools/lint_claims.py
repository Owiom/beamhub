"""Проверка текстов и подписей на расхождение с реальным поведением.

Запуск:  py tools\\lint_claims.py
Код возврата 0 — всё сходится, 1 — есть проблемы (используется в CI).

Ловит три класса ошибок:
  1. обещания, которых приложение не выполняет («сам скачивает», «в один клик»);
  2. цифры в тексте, разошедшиеся с реальными (количество модов, источников);
  3. битые ссылки на картинки и файлы в README.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (регулярка, пояснение, что писать вместо, исключение — контекст, где оборот уместен)
FORBIDDEN: list[tuple[str, str, str, str | None]] = [
    (r"сам[аи]?\s+скачива(ет|ть)", "приложение не качает архивы — это делает браузер",
     "«открывается страница мода, архив качает браузер»", None),
    (r"скачивает\s+архив", "приложение не качает архивы — это делает браузер",
     "«подхватывает архив из „Загрузок“»", None),
    (r"downloads?\s+the\s+archive", "the app does not download archives — the browser does",
     "«the browser fetches the file»", None),
    (r"установка\s+в\s+один\s+клик", "установка требует шага в браузере",
     "«скачал — дальше само»", None),
    (r"one[\s‑-]?click\s+install", "installation needs a browser step",
     "«download once, installed automatically»", None),
    (r"без\s+браузера", "скачивание файлов идёт через браузер",
     "«браузер нужен только для скачивания»",
     r"не\s+обеща|через\s+браузер|отч[её]т|обратн|фидбек|feedback"),
    (r"обходит\s+(cloudflare|капчу)", "обход Cloudflare запрещён и не реализован",
     "«сайты отдают файл только браузеру»", None),
    (r"все\s+моды\s+без\s+рекламы", "это неправда: реклама на сайтах остаётся",
     "«каталог без лишних вкладок»", r"не\s+обеща"),
    (r"встроенн(ый|ая)\s+(проверка|антивирус)", "встроенного антивируса нет",
     "«проверяется структура архива»", None),
]

# что обязано быть в README, иначе инструкция снова начнёт врать
REQUIRED = {
    "README.md": ["браузер", "Загрузок", "папку игры"],
    "README.en.md": ["browser", "Downloads", "mods"],
}

DOCS = ["README.md", "README.en.md", "PROMO.md", "CONTRIBUTING.md", "SECURITY.md",
        "ЧИТАЙ-МЕНЯ.txt", "PUBLISH.md"]

problems: list[str] = []


def check_claims() -> None:
    for name in DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, why, better, unless in FORBIDDEN:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                line_text = text.splitlines()[line - 1] if line <= len(text.splitlines()) else ""
                if unless and re.search(unless, line_text, re.IGNORECASE):
                    continue
                problems.append(f"{name}:{line}: «{match.group(0)}» — {why}. Лучше: {better}")


def check_required() -> None:
    for name, needles in REQUIRED.items():
        path = ROOT / name
        if not path.exists():
            problems.append(f"{name}: файл не найден")
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for needle in needles:
            if needle.lower() not in text:
                problems.append(f"{name}: нет упоминания «{needle}» — инструкция потеряла шаг")


def db_count() -> int:
    for candidate in (Path(__file__).resolve().parent.parent / "seed" / "beamhub.db",
                      Path(__file__).resolve().parent.parent / "data" / "beamhub.db"):
        if candidate.exists():
            try:
                con = sqlite3.connect(candidate)
                row = con.execute("SELECT COUNT(*) FROM mods").fetchone()
                con.close()
                return int(row[0])
            except sqlite3.Error:
                continue
    return 0


def check_numbers() -> None:
    real = db_count()
    sys.path.insert(0, str(ROOT))
    try:
        from core.providers import CATALOG_PROVIDERS

        real_sources = len(CATALOG_PROVIDERS)
    except Exception:  # noqa: BLE001
        real_sources = 0

    for name in ("README.md", "README.en.md", "PROMO.md"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"(\d{2,5})\s*(мод|mods?)\b", text, re.IGNORECASE):
            stated = int(match.group(1))
            if real and abs(stated - real) > max(50, real * 0.1):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: «{match.group(0)}», а в каталоге {real}")
        for match in re.finditer(r"(\d)\s*(источник|sources?)\b", text, re.IGNORECASE):
            stated = int(match.group(1))
            if real_sources and stated != real_sources:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: «{match.group(0)}», а провайдеров {real_sources}")


def check_links() -> None:
    for name in ("README.md", "README.en.md"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (ROOT / target.split("#")[0]).exists():
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: ссылка на «{target}» не существует")


ALLOWED_BUTTON_CONTEXT = {"render_inbox", "render_library", "render_updates",
                          "install_file", "install_all"}


def _enclosing_def(lines: list[str], index: int) -> str:
    for line in reversed(lines[:index]):
        match = re.match(r"\s*def\s+(\w+)", line)
        if match:
            return match.group(1)
    return ""


def check_ui_labels() -> None:
    """Кнопка каталога открывает браузер — она не должна называться «Установить»."""
    tk = ROOT / "core" / "tkui.py"
    js = ROOT / "web" / "app.js"
    if tk.exists():
        text = tk.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if "текст" in line:
                continue
            if re.search(r'text="Установить"', line):
                owner = _enclosing_def(lines, index)
                if owner in ALLOWED_BUTTON_CONTEXT:
                    continue
                problems.append(f"core/tkui.py:{index + 1}: кнопка «Установить» в {owner or '?'} — "
                                f"если это карточка каталога, она открывает браузер, назови «Скачать»")
    if js.exists():
        text = js.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"'Установить(\s+снова)?'", text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"web/app.js:{line}: подпись «{match.group(0)}» в каталоге — "
                            f"там открывается браузер, правильнее «Скачать»")


def check_versions() -> None:
    """Версия в текстовых файлах не должна отставать от core/version.py."""
    version_file = ROOT / "core" / "version.py"
    if not version_file.exists():
        return
    match = re.search(r'VERSION\s*=\s*"([\d.]+)"', version_file.read_text(encoding="utf-8"))
    if not match:
        return
    current = match.group(1)
    for name in ("ЧИТАЙ-МЕНЯ.txt", "README.md", "PUBLISH.md"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for found in re.finditer(r"BeamHub\s+(\d+\.\d+)", text):
            if found.group(1) != current.rsplit(".", 1)[0] and found.group(1) != current:
                line = text.count("\n", 0, found.start()) + 1
                problems.append(f"{name}:{line}: указана версия {found.group(1)}, "
                                f"а в core/version.py {current}")


def main() -> int:
    check_claims()
    check_required()
    check_numbers()
    check_links()
    check_ui_labels()
    check_versions()

    if problems:
        print(f"НАЙДЕНО ПРОБЛЕМ: {len(problems)}\n")
        for item in problems:
            print(" -", item)
        return 1
    print("Тексты и подписи сходятся с поведением: обещаний скачивания внутри приложения нет, "
          "цифры совпадают, ссылки живые.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
