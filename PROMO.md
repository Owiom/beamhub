# Продвижение BeamHub: что заполнить и где написать

Честно: «зафорсить» звёзды нельзя — накрутка и спам по сообществам заканчиваются баном,
а не установками. Работает другое: понятная страница, скриншоты, живой ответ на вопросы
и 2–3 уместных поста в тематических местах.

Ниже готовые тексты, осталось вставить ссылку и нажать «отправить».

---

## 1. Настройки репозитория (5 минут, самый большой эффект)

**Description** (Settings → General → Description):

```
Каталог модов для BeamNG.drive: 2791 мод с четырёх сайтов, установка без ручного распаковывания, библиотека, обновления. Windows, .exe
```

**Topics** (Settings → Topics, копируй списком):

```
beamng beamngdrive beamng-drive mods mod-manager launcher mod-catalog gaming-tools python tkinter windows pyinstaller desktop-app modding beamng-mods
```

**Social preview** (Settings → Social preview → Upload image):
`docs/banner.png` — 1280×640, уже нарисован.

**Прочее:**
- закрепи последний релиз (Releases → ⋯ → Edit → галочка «Set as latest»);
- в About справа добавь ссылку на релиз и на `docs/catalog.png`;
- имя репозитория `beamhub` похоже на опечатку — переименуй в `beamhub`
  (Settings → Rename, GitHub сохранит редирект со старого адреса).

---

## 2. Reddit — r/BeamNG (английский)

Правила сабреддита требуют флейр и не любят наглый самопиар: пиши как «сделал штуку,
делюсь, скажите что не так». Лучше с коротким видео/GIF установки мода.

**Title:** `I made a launcher that installs BeamNG mods straight into the mods folder`

**Body:**

> Got tired of hunting mods across file hosts, so I built a small Windows app that
> aggregates four BeamNG mod catalogs (modsgaming, playground, beamngland, beamngmods)
> into one window: categories, search, filters by game version, favorites.
>
> How it works: you press Install → the mod page opens in your browser (the sites
> only hand out files to a real browser behind Cloudflare) → the app watches the
> Downloads folder, validates the archive, strips wrapper folders, dedupes by SHA‑256
> and drops it into `mods`. Duplicates from different sites are marked, the library
> tracks versions and flags updates.
>
> It's a portable .exe, no install, no account, nothing phoned home — the local server
> binds to 127.0.0.1 and the API is hardened against CSRF/DNS‑rebinding.
>
> Repo (MIT) and release: https://github.com/Owiom/beamhub
> Would love feedback on what's missing — collections? screenshot gallery? silent updates?

**Флейр:** `Mod` или `Tool` — если сабреддит требует флейр для не-модов, попробуй `Discussion`.

---

## 3. BeamNG.com форум

Раздел **Mods → Tools** (или общий «Modding»), тема-анонс, обновляй первый пост при релизах.

**Заголовок:** `BeamHub — mod catalog + installer for Windows (open source)`

**Текст:**

> Hi! I built BeamHub, a small Windows launcher that collects BeamNG mods from several
> catalogs in one place and installs them without manual unzipping.
>
> - ~2800 mods from modsgaming.ru, playground.ru, beamngland.com, beamngmods.net
> - categories, search, filters by game version, favorites, duplicate detection
> - install: you press Download, the browser fetches the file, the app validates the
>   archive, repacks it if needed and places it into the mods folder
> - library with enable/disable/delete and "update available" badges
> - portable exe, MIT, source on GitHub: https://github.com/Owiom/beamhub
>
> Downloads still go through the browser, because the sites use Cloudflare and file
> hosts — the app picks the file up from the Downloads folder.
>
> Feedback and bug reports are very welcome.

---

## 4. Discord

В официальном сервере BeamNG самопиар запрещён — не рискуй аккаунтом.
Ищи тематические серверы моддеров и каналы `#mods`, `#showcase`, `#tools` и
сначала спроси модератора, можно ли выложить.

**Короткий текст:**

> Сделал каталог-лаунчер модов для BeamNG: 2791 мод с четырёх сайтов, скачал — и мод уже стоит,
> библиотека и поиск дубликатов. Portable exe, MIT, без регистрации.
> https://github.com/Owiom/beamhub — буду рад баг-репортам.

---

## 5. Telegram / VK (русскоязычные)

Группы вроде «BeamNG.drive | Моды», «BeamNG RU». Сначала прочитай правила: во многих
каналах посты только через админа.

**Текст:**

> Привет. Сделал BeamHub — лаунчер-каталог модов для BeamNG.drive.
>
> Что умеет:
> • 2791 мод с modsgaming, playground, beamngland и beamngmods в одном окне
> • категории, поиск, фильтры по версии игры, избранное
> • скачал → поставилось само: архив проверяется, лишняя папка-обёртка срезается,
>   дубликаты по хешу не ставятся
> • библиотека: включить/выключить/удалить, метки «обновление» и «дубликат»
> • обновляется сам через GitHub Releases
>
> Portable exe, ничего ставить не нужно: https://github.com/Owiom/beamhub
> Моды качаются через браузер (сайты не отдают файл скрипту), приложение подхватывает
> архив из «Загрузок» и кладёт в папку игры.
>
> Баги и идеи — в issues, отвечу.

---

## 6. YouTube / Shorts

Если запишешь 30–40 секунд экрана «открыл каталог → нажал установить → мод в игре»,
это даст больше переходов, чем любой текстовый пост. В описание:

```
BeamHub — каталог модов для BeamNG.drive: https://github.com/Owiom/beamhub
```

---

## Чек-лист запуска

- [ ] описание и темы репозитория заполнены
- [ ] social preview загружен (`docs/banner.png`)
- [ ] имя репозитория переименовано в `beamhub`
- [ ] последний релиз помечен как latest, в нём есть zip
- [ ] README открывается и скриншоты грузятся (проверь в приватном окне)
- [ ] в issue-шаблонах ссылки ведут на твой репозиторий
- [ ] один пост в Reddit, один на форуме BeamNG, один в Telegram — **не больше**, иначе бан
- [ ] в течение суток отвечать на комментарии: первые отзывы решают

## Чего не делать

- не покупать звёзды и не просить «поставь звезду за мод» — GitHub это ловит, доверие теряется;
- не постить в один день в десять сообществ — это выглядит как спам, модераторы банят;
- не обещать «все моды без рекламы и файлообменников» — это неправда, браузерный шаг остаётся;
- не выкладывать exe без исходников и без релиза в репозитории — люди справедливо не доверяют.

## Реалистичные ожидания

Первые 20–50 звёзд приходят от одного удачного поста в правильном месте, дальше работает
сарафан и удобство. Если пост не зашёл — это не про качество кода, а про то, что аудитория
не увидела пользу в первых двух строках. Пробуй разные формулировки: «боль» (файлообменники),
«демо» (гифка установки), «цифры» (2791 мод в одном окне).
