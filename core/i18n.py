"""Перевод интерфейса: русский по умолчанию, английский — по переключателю.

Перевод применяется к уже собранным виджетам: у каждого запоминается исходный
русский текст, поэтому переключение туда-обратно ничего не теряет.
"""
from __future__ import annotations

import re

LANG = "ru"

# точные совпадения: строки интерфейса
EN: dict[str, str] = {
    # навигация и заголовки
    "Каталог": "Catalog",
    "Моды, поставленные до BeamHub, привязываются к каталогу по названию и размеру — так для них работают обновления.":
        "Mods installed before BeamHub are matched with the catalog by name and size — "
        "that's what makes updates work for them.",
    "Здесь лежат архивы из папки «Загрузки» — они ещё не установлены. Библиотека — это то, что уже попало в папку модов игры.":
        "These are archives from your Downloads folder — not installed yet. "
        "The library is what's already in the game's mods folder.",
    "включён": "enabled",
    "не привязан": "not matched",
    "не похоже на мод": "not a mod",
    "мод": "mod",
    "Библиотека": "Library",
    "Обновления": "Updates",
    "Загрузки": "Downloads",
    "Настройки": "Settings",
    "Каталог модов": "Mod catalog",
    "Библиотека — это то, что уже попало в папку модов игры.":
        "The library is what's already in the game's mods folder.",
    "КАТЕГОРИИ": "CATEGORIES",
    "Все моды": "All mods",
    "Машины": "Cars",
    "Карты": "Maps",
    "Инструменты": "Tools",
    "Самолёты": "Planes",
    "Мотоциклы": "Bikes",
    "Корабли": "Boats",
    "Другое": "Other",
    "Мод": "Mod",
    # кнопки
    "Скачать": "Download",
    "Установлен": "Installed",
    "Установить": "Install",
    "Открыть страницу": "Open page",
    "Сохранить": "Save",
    "Пересобрать библиотеку": "Rescan library",
    "Проверить обновления": "Check for updates",
    "Обратная связь": "Feedback",
    "Экспорт списка": "Export list",
    "Сопоставить с каталогом": "Match with catalog",
    "Показать ещё": "Show more",
    "Удалить": "Delete",
    "Включить": "Enable",
    "Выключить": "Disable",
    "Остановить BeamHub": "Quit BeamHub",
    "Остановить BeamHub?": "Quit BeamHub?",
    "Открыть GitHub": "Open GitHub",
    "Скопировать текст": "Copy text",
    "Скачать новую": "Get update",
    "Обновить каталог": "Refresh catalog",
    "＋ Ссылка": "+ Link",
    # фильтры и сортировка
    "с обложкой": "with cover",
    "избранное": "favorites",
    "Сначала новые": "Newest first",
    "Сначала старые": "Oldest first",
    "По названию А→Я": "Name A→Z",
    "По названию Я→А": "Name Z→A",
    "По размеру": "By size",
    "По популярности": "By popularity",
    "Все источники": "All sources",
    "Поиск: bmw, карта, vaz, дрифт…": "Search: bmw, map, vaz, drift…",
    "Найдено:": "Found:",
    # статусы
    "Готово": "Ready",
    "Не удалось": "Failed",
    "Не удалось открыть браузер": "Could not open the browser",
    "Не удалось начать установку": "Could not start the installation",
    "Обновляю каталог…": "Refreshing the catalog…",
    "Скачиваю обновление…": "Downloading the update…",
    "Без названия": "Untitled",
    "Все установленные моды актуальны.": "All installed mods are up to date.",
    "Обновлений нет": "No updates",
    "Новых архивов нет.": "No new archives.",
    "Пока пусто. Установите мод из каталога.": "Nothing here yet. Install a mod from the catalog.",
    "Уже установлен": "Already installed",
    "Обновление": "Update",
    "Обновление BeamHub": "BeamHub update",
    "Добавить мод по ссылке": "Add a mod by link",
    "Ссылка на страницу мода:": "Link to the mod page:",
    "Описание на странице мода.": "Description is on the mod page.",
    "Ошибка": "Error",
    "Ошибка проверки обновлений": "Update check failed",
    "Настройки сохранены": "Settings saved",
    "Открыл браузер — жду файл": "Opened the browser — waiting for the file",
    "Открыл GitHub — проверь текст и нажми «Submit new issue»":
        "Opened GitHub — review the text and press “Submit new issue”",
    "Нужен аккаунт GitHub — issue откроется с готовым текстом":
        "A GitHub account is needed — the issue opens with the text ready",
    "Текст скопирован — можно вставить в любое место":
        "Text copied — you can paste it anywhere",
    "Сопоставляю моды с каталогом…": "Matching mods with the catalog…",
    "Сопоставление с каталогом": "Catalog matching",
    "Для привязанных модов теперь работают обновления.":
        "Updates now work for the matched mods.",
    "Нужно обновить BeamHub": "BeamHub update is available",
    # настройки
    "Папка модов игры": "Game mods folder",
    "Папка загрузок": "Downloads folder",
    "GitHub-репозиторий обновлений (владелец/репозиторий)":
        "GitHub repo for updates (owner/repo)",
    "Ставить новые архивы из «Загрузок» автоматически":
        "Auto-install new archives from Downloads",
    "Переносить архив из «Загрузок» в папку модов ":
        "Move the archive from Downloads into the mods folder ",
    "Переносить архив из «Загрузок» в папку модов (освобождает место на диске)":
        "Move archives from Downloads into the mods folder (frees disk space)",
    "(освобождает место на диске)": "(frees disk space)",
    "Язык": "Language",
    # библиотека и загрузки
    "Моды, поставленные до BeamHub, привязываются к каталогу ":
        "Mods installed before BeamHub are matched with the catalog ",
    "Здесь лежат архивы из папки «Загрузки» — они ещё не установлены. ":
        "These are archives from your Downloads folder — not installed yet. ",
    "ДУБЛИКАТ ({reason})": "DUPLICATE ({reason})",
    "Скачайте вручную: {data.get('url', '')}": "Download manually: {data.get('url', '')}",
    "Так это уйдёт в issue на GitHub (можно поправить):":
        "This is what goes into the GitHub issue (editable):",
    "Что случилось?": "What happened?",
    "добавить путь к игре": "include the game path",
    "добавить хвост лога": "include the log tail",
    "Куда ответить (необязательно):": "Where to reply (optional):",
    "каталог модов BeamNG.drive": "BeamNG.drive mod catalog",
    "BeamHub — каталог модов BeamNG.drive": "BeamHub — BeamNG.drive mod catalog",
    "Папка модов:": "Mods folder:",
    "Готово": "Ready",
    "Все источники": "All sources",
    "Сначала новые": "Newest first",
}

# подстроки и шаблоны для динамических строк
RULES: list[tuple[str, str]] = [
    (r"^Установлено: (.+)$", r"Installed: \1"),
    (r"^Добавлено: (.+)$", r"Added: \1"),
    (r"^Добавлено модов: (\d+)$", r"Mods added: \1"),
    (r"^Ошибка: (.+)$", r"Error: \1"),
    (r"^Ошибка проверки: (.+)$", r"Check failed: \1"),
    (r"^Библиотека пересобрана: (.+) файлов$", r"Library rescanned: \1 files"),
    (r"^Список из (.+) модов сохранён:$", r"List of \1 mods saved:"),
    (r"^Сопоставлено: (\d+) из (\d+)\.$", r"Matched \1 of \2."),
    (r"^Вышла версия (.+) \(у вас (.+)\)\.$", r"Version \1 is out (you have \2)."),
    (r"^Доступно обновление (.+)$", r"Update available: \1"),
    (r"^Ставлю (\d+) архивов…$", r"Installing \1 archives…"),
    (r"^Открываю (.+)…$", r"Opening \1…"),
    (r"^Удалить (.+) из папки модов\?$", r"Delete \1 from the mods folder?"),
    (r"^(\d+) файлов$", r"\1 files"),
    (r"^(\d+) архивов$", r"\1 archives"),
    (r"^(\d+) модов$", r"\1 mods"),
    (r"^(\d+) обновлений$", r"\1 updates"),
    (r"^(\d+) скачиваний$", r"\1 downloads"),
    (r" (\d+) без версии$", r" \1 without version"),
    (r" · дублей: (\d+)$", r" · duplicates: \1"),
    (r" · привязано к каталогу: (\d+)$", r" · matched: \1"),
    (r" · к установке (\d+)$", r" · ready to install: \1"),
    (r"установлено (.+) → в каталоге (.+)$", r"installed \1 → catalog \2"),
    (r"версия файла неизвестна, в каталоге (.+)$", r"file version unknown, catalog \1"),
    (r"^(\d+) обновлений · (\d+) без версии$", r"\1 updates · \2 without version"),
    (r"^Есть обновление: (.+)$", r"Update available: \1"),
    (r"^мод (.+)$", r"v\1"),
    (r"^дубликат · (\d+)$", r"duplicate · \1"),
    (r"^дубликат · ещё (.+)$", r"duplicate · also \1"),
    (r"^(\d+) дн назад$", r"\1 d ago"),
    (r"^(\d+) ч назад$", r"\1 h ago"),
    (r"^(\d+) мин назад$", r"\1 min ago"),
    (r"^(\d+) мес назад$", r"\1 mo ago"),
    (r"^(\d+) скачиваний$", r"\1 downloads"),
    (r"^Установлена последняя версия (.+)$", r"You are on the latest version \1"),
    (r"(\d+) ч ago$", r"\1 h ago"),
    (r"^Обновлений не найдено$", r"No updates found"),
    (r"Версия (.+?) · каталог: (\d+) модов · установлено: (\d+)",
     r"Version \1 · catalog: \2 mods · installed: \3"),
]


SUBS: list[tuple[str, str]] = [
    (" дн назад", " d ago"),
    (" ч назад", " h ago"),
    (" мин назад", " min ago"),
    (" мес назад", " mo ago"),
    (" мод ", " v"),
    ("Папка модов:", "Mods folder:"),
    ("скачиваний", "downloads"),
    ("дубликат", "duplicate"),
    (" · ещё ", " · also "),
    ("из папки модов", "from the mods folder"),
    ("не найдена", "not found"),
    (" не задан", " not set"),
    (" ГБ", " GB"),
    (" МБ", " MB"),
    (" КБ", " KB"),
    (" файлов", " files"),
    (" архивов", " archives"),
    ("назад", "ago"),
    ("Игра:", "Game:"),
    ("Данные:", "Data:"),
    ("Версия ", "Version "),
]


def current() -> str:
    return LANG


def set_lang(code: str) -> None:
    global LANG
    LANG = "en" if str(code).lower().startswith("en") else "ru"


def tr(text: str) -> str:
    """Перевести строку интерфейса. Неизвестное остаётся как есть."""
    if not text or LANG == "ru":
        return text
    exact = EN.get(text)
    if exact:
        return exact
    result = text
    for pattern, repl in RULES:
        new = re.sub(pattern, repl, result, count=1)
        if new != result:
            result = new
            break
    for ru, en in SUBS:
        if ru in result:
            result = result.replace(ru, en)
    return result
