"""Каталог модов в родном окне (tkinter) — без браузера и без WebView.

Интерфейс повторяет веб-версию: сайдбар с категориями, сетка карточек с превью,
поиск, сортировка, библиотека, загрузки и настройки. Данные берутся у локального
API (тот же процесс), картинки — через встроенный прокси с кешем.
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
import urllib.parse
import urllib.request
from tkinter import messagebox, ttk

BG = "#0d0e11"
SOFT = "#12141a"
PANEL = "#171a21"
LINE = "#262b36"
TEXT = "#e9ebf0"
MUTED = "#8d93a1"
DIM = "#6b7280"
ACCENT = "#ff7a1a"
GREEN = "#35c46b"
DUP_BG = "#2a1618"
DUP_LINE = "#7a2f33"
SOURCE_NAMES = {"playground": "playground.ru", "modsgaming": "modsgaming.ru",
                "beamngland": "beamngland.com", "beamngmods": "beamngmods.net",
                "link": "по ссылке"}

CARD_W = 232
IMG_W, IMG_H = 232, 130

try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False


def available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


class Api:
    """Тонкий клиент к локальному API."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        try:
            from . import security

            self.token = security.token()
        except Exception:  # noqa: BLE001
            self.token = ""

    def get(self, path: str, **params) -> dict:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json", "X-BeamHub-Token": self.token})
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def image(self, url: str) -> bytes:
        target = f"{self.base}/api/img?u={urllib.parse.quote(url, safe='')}"
        with urllib.request.urlopen(target, timeout=25) as resp:
            return resp.read()


class App:
    def __init__(self, root: tk.Tk, base: str) -> None:
        self.root = root
        self.api = Api(base)
        self.view = "catalog"
        self.category = "all"
        self.query = ""
        self.sort = "new"
        self.source = "all"
        self.offset = 0
        self.total = 0
        self.items: list[dict] = []
        self._images: dict[str, object] = {}
        self._img_queue: list[tuple[str, str]] = []
        self._img_busy = False
        self._search_job = None
        self._event_seq = 0
        self._cards_frame = None

        root.title("BeamHub — каталог модов BeamNG.drive")
        root.geometry("1360x860")
        root.minsize(1100, 700)
        root.configure(bg=BG)
        self._styles()
        self._build()
        self.reload()
        self.refresh_meta()
        self.poll_events()
        self.root.after(5000, lambda: self.check_updates(silent=True))

    # ---------------------------------------------------------------- стиль
    def _styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL)])
        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)

    # ---------------------------------------------------------------- каркас
    def _build(self) -> None:
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        top = tk.Frame(self.root, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))

        self.search_var = tk.StringVar()
        search_box = tk.Frame(top, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        search_box.pack(side="left", fill="x", expand=True)
        entry = tk.Entry(search_box, textvariable=self.search_var, bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief="flat", font=("Segoe UI", 11))
        entry.pack(fill="x", padx=8, ipady=6)
        placeholder = tk.Label(search_box, text="Поиск: bmw, карта, vaz, дрифт…", bg=PANEL,
                               fg=DIM, font=("Segoe UI", 11))
        placeholder.place(x=10, y=7)
        placeholder.bind("<Button-1>", lambda _e: entry.focus_set())
        entry.bind("<FocusIn>", lambda _e: placeholder.place_forget())
        entry.bind("<FocusOut>", lambda _e: (placeholder.place(x=10, y=7)
                                             if not self.search_var.get() else None))
        entry.bind("<Return>", lambda _e: self.on_search())
        self.search_var.trace_add("write", lambda *_: self._search_debounced())

        self.sort_var = tk.StringVar(value="Сначала новые")
        sorts = ["Сначала новые", "Сначала старые", "По названию А→Я", "По названию Я→А",
                 "По размеру", "По популярности"]
        combo = ttk.Combobox(top, textvariable=self.sort_var, values=sorts, width=18,
                             state="readonly")
        combo.pack(side="left", padx=(10, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.on_sort())

        self.source_var = tk.StringVar(value="Все источники")
        self._source_map = {"Все источники": "all"}
        self.source_combo = ttk.Combobox(top, textvariable=self.source_var,
                                         values=["Все источники"], width=17, state="readonly")
        self.source_combo.pack(side="left", padx=(8, 0))
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_source())

        tk.Button(top, text="Обновить каталог", command=self.on_sync, bg=PANEL, fg=TEXT,
                  activebackground="#232833", activeforeground=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, cursor="hand2").pack(side="left", padx=8)
        tk.Button(top, text="＋ Ссылка", command=self.on_add_link, bg=PANEL, fg=TEXT,
                  activebackground="#232833", activeforeground=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, cursor="hand2").pack(side="left")

        body = tk.Frame(self.root, bg=BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.sidebar = tk.Frame(body, bg=SOFT, width=232)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self._build_sidebar()

        self.main = tk.Frame(body, bg=BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(1, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self.head = tk.Frame(self.main, bg=BG)
        self.head.grid(row=0, column=0, sticky="ew", padx=18, pady=(6, 0))
        self.title_label = tk.Label(self.head, text="Каталог модов", bg=BG, fg=TEXT,
                                    font=("Segoe UI", 17, "bold"))
        self.title_label.pack(side="left")
        self.count_label = tk.Label(self.head, text="", bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.count_label.pack(side="left", padx=12)

        filters = tk.Frame(self.head, bg=BG)
        filters.pack(side="right")
        self.filter_game = tk.BooleanVar(value=False)
        self.filter_image = tk.BooleanVar(value=False)
        self.filter_fav = tk.BooleanVar(value=False)
        for text, var in (("0.40.x", self.filter_game), ("с обложкой", self.filter_image),
                          ("избранное", self.filter_fav)):
            tk.Checkbutton(filters, text=text, variable=var, bg=BG, fg=MUTED,
                           selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
                           font=("Segoe UI", 9), command=self.on_filters,
                           padx=4).pack(side="left")

        self.content = tk.Frame(self.main, bg=BG)
        self.content.grid(row=1, column=0, sticky="nsew", padx=18, pady=(4, 0))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.content, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.content, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        self.status = tk.Label(self.root, text="Готово", bg=SOFT, fg=MUTED, anchor="w",
                               font=("Segoe UI", 9), padx=14, pady=6)
        self.status.grid(row=2, column=0, sticky="ew")

    def _build_sidebar(self) -> None:
        brand = tk.Frame(self.sidebar, bg=SOFT)
        brand.pack(fill="x", padx=14, pady=(16, 10))
        tk.Label(brand, text="B", bg=ACCENT, fg="#14100c", font=("Segoe UI", 15, "bold"),
                 width=2).pack(side="left")
        titles = tk.Frame(brand, bg=SOFT)
        titles.pack(side="left", padx=10)
        tk.Label(titles, text="BeamHub", bg=SOFT, fg=TEXT, font=("Segoe UI", 14, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(titles, text="каталог модов BeamNG.drive", bg=SOFT, fg=DIM,
                 font=("Segoe UI", 8), anchor="w").pack(anchor="w")

        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label in (("catalog", "Каталог"), ("library", "Библиотека"),
                           ("updates", "Обновления"), ("inbox", "Загрузки"),
                           ("settings", "Настройки")):
            button = tk.Button(self.sidebar, text=label, anchor="w", bg=SOFT, fg=MUTED,
                               activebackground="#191c24", activeforeground=TEXT,
                               relief="flat", font=("Segoe UI", 11), padx=14, pady=7,
                               cursor="hand2", command=lambda k=key: self.switch(k))
            button.pack(fill="x", padx=8, pady=1)
            self.nav_buttons[key] = button

        tk.Label(self.sidebar, text="КАТЕГОРИИ", bg=SOFT, fg=DIM, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        self.cat_frame = tk.Frame(self.sidebar, bg=SOFT)
        self.cat_frame.pack(fill="both", expand=True, padx=8)

        foot = tk.Frame(self.sidebar, bg=SOFT)
        foot.pack(side="bottom", fill="x", padx=14, pady=12)
        self.foot_label = tk.Label(foot, text="", bg=SOFT, fg=DIM, font=("Segoe UI", 8),
                                   justify="left", wraplength=200, anchor="w")
        self.foot_label.pack(anchor="w")

        tk.Button(self.sidebar, text="Остановить BeamHub", bg=SOFT, fg="#ff9a9a",
                  activebackground="#241214", activeforeground="#ffb3b3", relief="flat",
                  font=("Segoe UI", 9), anchor="w", padx=14, pady=6, cursor="hand2",
                  command=self.on_quit).pack(side="bottom", fill="x", padx=8)

    def refresh_all(self) -> None:
        """Перерисовать текущую вкладку и обновить счётчики в меню."""
        self.switch(self.view)
        self.refresh_meta()

    # ---------------------------------------------------------------- навигация
    def switch(self, view: str) -> None:
        self.view = view
        for key, button in self.nav_buttons.items():
            button.configure(bg="#22262f" if key == view else SOFT,
                             fg=TEXT if key == view else MUTED)
        titles = {"catalog": "Каталог модов", "library": "Библиотека",
                  "updates": "Обновления", "inbox": "Загрузки", "settings": "Настройки"}
        self.title_label.configure(text=titles[view])
        if view == "catalog":
            self.reload()
        elif view == "library":
            self.render_library()
        elif view == "updates":
            self.render_updates()
        elif view == "inbox":
            self.render_inbox()
        else:
            self.render_settings()

    def _clear(self) -> None:
        for child in self.canvas.winfo_children():
            child.destroy()
        self.canvas.delete("all")

    def _scrollable_frame(self) -> tk.Frame:
        self._clear()
        frame = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self._cards_frame = frame
        return frame

    def _on_wheel(self, event) -> None:
        if self.view in ("catalog", "library", "inbox"):
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ---------------------------------------------------------------- каталог
    def reload(self, append: bool = False) -> None:
        if not append:
            self.offset = 0
            self.items = []
            self._clear()
            self.count_label.configure(text="загружаю…")
        params = {"q": self.query, "cat": self.category, "sort": self.sort,
                  "source": self.source, "offset": self.offset, "limit": 24}
        if self.filter_game.get():
            params["game"] = "0.40.x"
        if self.filter_image.get():
            params["img"] = "1"
        if self.filter_fav.get():
            params["fav"] = "1"
        try:
            data = self.api.get("/api/mods", **params)
        except Exception as exc:  # noqa: BLE001
            self.count_label.configure(text=f"ошибка: {exc}")
            return
        self.total = data.get("total", 0)
        self.items.extend(data.get("items", []))
        self.count_label.configure(text=f"{self.total} модов")
        self._render_cards(append)

    def _render_cards(self, append: bool) -> None:
        frame = self._cards_frame if append and self._cards_frame else self._scrollable_frame()
        columns = 4
        # перерисовываем все карточки: проще и предсказуемее, чем считать индексы
        for child in frame.winfo_children():
            child.destroy()
        for index, item in enumerate(self.items):
            row, col = divmod(index, columns)
            card = self._card(frame, item)
            card.grid(row=row, column=col, padx=7, pady=7, sticky="n")
        more_row = (len(self.items) + columns - 1) // columns
        if len(self.items) < self.total:
            tk.Button(frame, text="Показать ещё", bg=PANEL, fg=TEXT, relief="flat",
                      font=("Segoe UI", 10), padx=18, pady=8, cursor="hand2",
                      activebackground="#232833",
                      command=self.load_more).grid(row=more_row, column=0, columnspan=columns,
                                                   pady=14)

    def load_more(self) -> None:
        self.offset = len(self.items)
        params = {"q": self.query, "cat": self.category, "sort": self.sort,
                  "source": self.source, "offset": self.offset, "limit": 24}
        if self.filter_game.get():
            params["game"] = "0.40.x"
        if self.filter_image.get():
            params["img"] = "1"
        if self.filter_fav.get():
            params["fav"] = "1"
        try:
            data = self.api.get("/api/mods", **params)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")
            return
        self.items.extend(data.get("items", []))
        self.total = data.get("total", self.total)
        self._render_cards(True)

    def _card(self, parent: tk.Frame, item: dict) -> tk.Frame:
        duplicate = bool(item.get("duplicate"))
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=(DUP_LINE if duplicate else LINE))
        holder = tk.Frame(card, bg="#0a0b0e", width=IMG_W, height=IMG_H)
        holder.pack(fill="x")
        holder.pack_propagate(False)  # фиксированная высота, пока картинки нет
        thumb = tk.Label(holder, bg="#0a0b0e", bd=0, text="", fg=DIM, font=("Segoe UI", 8))
        thumb.pack(fill="both", expand=True)
        if item.get("image"):
            self._request_image(item["image"], thumb, item)
        else:
            self._set_placeholder(thumb, item)
        if duplicate:
            sources = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            badge_text = f"дубликат · {item.get('duplicate_count')}"
            badge = tk.Label(holder, text=badge_text, bg="#4a2a12", fg="#ffc79b",
                             font=("Segoe UI", 8, "bold"), padx=6, pady=2)
            badge.place(relx=0, rely=1.0, x=6, y=-6, anchor="sw")
            badge.bind("<Enter>", lambda _e, b=badge, s=sources: b.configure(
                text=f"дубликат · ещё {s}" if s else b.cget("text")))

        badge = tk.Frame(card, bg=PANEL)
        badge.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(badge, text=item.get("category_label", "Другое"), bg=PANEL, fg="#ffc79b",
                 font=("Segoe UI", 8)).pack(side="left")
        size = item.get("size_text") or ""
        if size:
            tk.Label(badge, text=size, bg=PANEL, fg=DIM, font=("Segoe UI", 8)).pack(side="right")

        title = item.get("title") or "Без названия"
        label = tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"),
                         wraplength=CARD_W - 22, justify="left", anchor="w", height=2)
        label.pack(fill="x", padx=10, pady=(4, 2))

        meta_bits = []
        if item.get("game_version"):
            meta_bits.append(f"v{item['game_version']}")
        else:
            source_names = {"playground": "playground.ru", "modsgaming": "modsgaming.ru",
                            "beamngland": "beamngland.com", "beamngmods": "beamngmods.net",
                            "link": "по ссылке"}
            label_text = source_names.get(item.get("source", ""))
            if label_text:
                meta_bits.append(label_text)
        if item.get("version"):
            meta_bits.append(f"мод {item['version']}")
        if item.get("ago"):
            meta_bits.append(item["ago"])
        tk.Label(card, text=" · ".join(meta_bits), bg=PANEL, fg=DIM, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=10)

        actions = tk.Frame(card, bg=PANEL)
        actions.pack(fill="x", padx=10, pady=(8, 10))
        text = "Установлен" if item.get("installed") else "Установить"
        button = tk.Button(actions, text=text, bg=ACCENT, fg="#1a0f04", relief="flat",
                           font=("Segoe UI", 9, "bold"), cursor="hand2",
                           activebackground="#ff9350", command=lambda i=item, b=None: self.install(i))
        button.pack(side="left", fill="x", expand=True, ipady=3)
        star = tk.Button(actions, text="★" if item.get("favorite") else "☆", bg=PANEL,
                         fg=("#ffd08a" if item.get("favorite") else MUTED), relief="flat",
                         font=("Segoe UI", 11), cursor="hand2", activebackground="#232833",
                         activeforeground=TEXT)
        star.configure(command=lambda i=item, b=star: self.toggle_favorite(i, b))
        star.pack(side="left", padx=(4, 0))
        tk.Button(actions, text="…", bg=PANEL, fg=MUTED, relief="flat", font=("Segoe UI", 9),
                  cursor="hand2", activebackground="#232833", activeforeground=TEXT,
                  command=lambda i=item: self.details(i)).pack(side="left", padx=(4, 0))

        for widget in (thumb, label):
            widget.bind("<Double-Button-1>", lambda _e, i=item: self.details(i))
        return card

    def _request_image(self, url: str, label: tk.Label, item: dict | None = None) -> None:
        cached = self._images.get(url)
        if cached is not None:
            label.configure(image=cached)
            label.image = cached
            return
        threading.Thread(target=self._load_image, args=(url, label, item or {}), daemon=True).start()

    def _set_placeholder(self, label: tk.Label, item: dict) -> None:
        """Заглушка вместо обложки: две буквы названия на плашке категории."""
        if not HAS_PIL:
            label.configure(text=(item.get("title") or "")[:24], fg=MUTED, font=("Segoe UI", 8))
            return
        title = (item.get("title") or "?").strip()
        letters = "".join(part[0] for part in title.split()[:2]).upper() or "?"
        colors = {"cars": (255, 122, 26), "maps": (60, 160, 220), "tools": (120, 200, 130),
                  "planes": (200, 160, 255), "bikes": (255, 200, 80), "boats": (90, 200, 210)}
        color = colors.get(item.get("category") or "other", (150, 155, 170))
        image = Image.new("RGB", (IMG_W, IMG_H), (18, 20, 26))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        try:
            from PIL import ImageFont

            font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 44)
        except Exception:  # noqa: BLE001
            font = None
        box = draw.textbbox((0, 0), letters, font=font) if font else (0, 0, 40, 40)
        draw.text(((IMG_W - (box[2] - box[0])) / 2, (IMG_H - (box[3] - box[1])) / 2 - 6),
                  letters, fill=color, font=font)
        photo = ImageTk.PhotoImage(image)
        label.configure(image=photo)
        label.image = photo

    def _load_image(self, url: str, label: tk.Label, item: dict) -> None:
        try:
            raw = self.api.image(url)
            if not HAS_PIL:
                return
            import io

            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image = image.resize((IMG_W, IMG_H), Image.LANCZOS)
        except Exception as exc:  # noqa: BLE001
            print(f"[tkui] картинка не загрузилась: {exc}")
            try:
                self.root.after(0, lambda: self._set_placeholder(label, item)
                                if label.winfo_exists() else None)
            except Exception:  # noqa: BLE001
                pass
            return

        def apply() -> None:
            try:
                if not label.winfo_exists():
                    return
                photo = ImageTk.PhotoImage(image)
                self._images[url] = photo
                label.configure(image=photo)
                label.image = photo
            except Exception as exc:  # noqa: BLE001
                print(f"[tkui] PhotoImage: {exc}")

        try:
            self.root.after(0, apply)
        except Exception:  # noqa: BLE001
            pass

    def _apply_image(self, label: tk.Label, photo) -> None:
        try:
            if label.winfo_exists():
                label.configure(image=photo, width=IMG_W, height=IMG_H)
                label.image = photo
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- действия
    def install(self, item: dict) -> None:
        self.set_status(f"Открываю {item.get('title', 'мод')}…")
        def work() -> None:
            try:
                result = self.api.post("/api/install", {"id": item["id"]})
                message = result.get("message") or "Открыл браузер — жду файл"
                self.root.after(0, lambda: self.set_status(message))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def details(self, item: dict) -> None:
        window = tk.Toplevel(self.root, bg=BG)
        window.title(item.get("title") or "Мод")
        window.geometry("760x560")
        header = tk.Label(window, text=item.get("title") or "", bg=BG, fg=TEXT,
                          font=("Segoe UI", 14, "bold"), wraplength=700, justify="left",
                          anchor="w")
        header.pack(fill="x", padx=18, pady=(16, 4))

        meta = " · ".join(filter(None, [
            item.get("category_label"), f"игра {item['game_version']}" if item.get("game_version") else "",
            f"версия {item['version']}" if item.get("version") else "",
            item.get("size_text"), f"{item['downloads']} скачиваний" if item.get("downloads") else "",
            item.get("ago")]))
        tk.Label(window, text=meta, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(fill="x", padx=18)
        if item.get("duplicate"):
            names = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            tk.Label(window, text=f"Такой же мод есть ещё на: {names or 'других сайтах'} "
                                  f"(всего {item.get('duplicate_count')})",
                     bg=BG, fg="#ffb27a", font=("Segoe UI", 9), anchor="w",
                     wraplength=700, justify="left").pack(fill="x", padx=18, pady=(4, 0))

        text = tk.Text(window, bg=PANEL, fg="#c3c8d4", relief="flat", wrap="word",
                       font=("Segoe UI", 10), padx=12, pady=10, height=12)
        text.insert("1.0", item.get("description") or "Описание на странице мода.")
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, padx=18, pady=12)

        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        tk.Button(buttons, text="Установить", bg=ACCENT, fg="#1a0f04", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=18, pady=6, cursor="hand2",
                  command=lambda: self.install(item)).pack(side="left")
        tk.Button(buttons, text="Открыть страницу", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=14, pady=6, cursor="hand2",
                  command=lambda: self._open_page(item)).pack(side="left", padx=8)

    def _open_page(self, item: dict) -> None:
        def work() -> None:
            try:
                self.api.post("/api/open", {"id": item["id"]})
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- библиотека
    def render_library(self) -> None:
        frame = self._scrollable_frame()
        self.count_label.configure(text="")
        try:
            data = self.api.get("/api/library")
        except Exception as exc:  # noqa: BLE001
            tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
            return
        items = data.get("items", [])
        duplicates = data.get("duplicates", 0)
        matched = data.get("matched", 0)
        header = f"{len(items)} файлов"
        if matched:
            header += f" · привязано к каталогу: {matched}"
        if duplicates:
            header += f" · дублей: {duplicates}"
        self.count_label.configure(text=header)

        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="Моды, поставленные до BeamHub, привязываются к каталогу "
                               "по названию и размеру — так для них работают обновления.",
                 bg=BG, fg=DIM, font=("Segoe UI", 9), wraplength=760, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(toolbar, text="Сопоставить с каталогом", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 9), padx=12, pady=5, cursor="hand2",
                  command=self.match_library).pack(side="right")
        if not items:
            tk.Label(frame, text="Пока пусто. Установите мод из каталога.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w", pady=20)
            return
        for row in items:
            line = tk.Frame(frame, bg=(DUP_BG if row.get("duplicate") else PANEL),
                            highlightthickness=1,
                            highlightbackground=(DUP_LINE if row.get("duplicate") else LINE))
            line.pack(fill="x", pady=4)
            row_bg = DUP_BG if row.get("duplicate") else PANEL
            info = tk.Frame(line, bg=row_bg)
            info.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            shown_title = row.get("catalog_title") or row.get("title") or row.get("filename")
            tk.Label(info, text=shown_title, bg=row_bg, fg=TEXT,
                     font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
            tk.Label(info, text=f"{row.get('filename')} · {row.get('size_text')} · "
                                f"{row.get('installed_text')}",
                     bg=row_bg, fg=DIM, font=("Segoe UI", 8), anchor="w").pack(fill="x")
            state = tk.Label(line, text="включён" if row.get("active") else "выключен",
                             bg=row_bg, fg=(GREEN if row.get("active") else "#ff9a9a"),
                             font=("Segoe UI", 8))
            state.pack(side="left", padx=8)
            if row.get("duplicate"):
                reason = row.get("duplicate_reason") or "файл"
                tk.Label(line, text=f"ДУБЛИКАТ ({reason})", bg=DUP_BG, fg="#ff8f8f",
                         font=("Segoe UI", 8, "bold")).pack(side="left", padx=4)
            if not row.get("matched"):
                tk.Label(line, text="не привязан", bg=row_bg, fg=DIM,
                         font=("Segoe UI", 8)).pack(side="left", padx=4)
            if row.get("update_available"):
                tk.Label(line, text=f"обновление {row.get('catalog_version')}", bg=PANEL,
                         fg="#ffd6a3", font=("Segoe UI", 8)).pack(side="left", padx=4)
            tk.Button(line, text="Выключить" if row.get("active") else "Включить",
                      bg=PANEL, fg=TEXT, relief="flat", font=("Segoe UI", 9), cursor="hand2",
                      command=lambda r=row: self.toggle_mod(r)).pack(side="left", padx=4)
            tk.Button(line, text="Удалить", bg=PANEL, fg="#ff9a9a", relief="flat",
                      font=("Segoe UI", 9), cursor="hand2",
                      command=lambda r=row: self.remove_mod(r)).pack(side="left", padx=(0, 10))

    def toggle_mod(self, row: dict) -> None:
        try:
            self.api.post("/api/library/toggle", {"key": row["key"]})
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")
        self.render_library()

    def match_library(self) -> None:
        self.set_status("Сопоставляю моды с каталогом…")

        def work() -> None:
            try:
                stats = self.api.post("/api/library/match", {})
                text = (f"Сопоставлено: {stats.get('matched')} из {stats.get('checked')}.\n"
                        "Для привязанных модов теперь работают обновления.")
                self.root.after(0, lambda: messagebox.showinfo("Сопоставление с каталогом", text))
                self.root.after(0, self.render_library)
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def remove_mod(self, row: dict) -> None:
        if not messagebox.askyesno("BeamHub", f"Удалить {row['filename']} из папки модов?"):
            return
        try:
            self.api.post("/api/library/remove", {"key": row["key"]})
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")
        self.render_library()

    # ---------------------------------------------------------------- загрузки
    def render_inbox(self) -> None:
        frame = self._scrollable_frame()
        self.count_label.configure(text="")
        try:
            data = self.api.get("/api/inbox")
        except Exception as exc:  # noqa: BLE001
            tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
            return
        items = data.get("items", [])
        valid = [item for item in items if item.get("valid")]
        total_size = sum(int(item.get("size") or 0) for item in valid)
        self.count_label.configure(text=f"{len(items)} архивов · к установке {len(valid)}")

        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar,
                 text="Здесь лежат архивы из папки «Загрузки» — они ещё не установлены. "
                      "Библиотека — это то, что уже попало в папку модов игры.",
                 bg=BG, fg=DIM, font=("Segoe UI", 9), wraplength=700, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        if valid:
            tk.Button(toolbar, text=f"Установить все ({len(valid)})", bg=ACCENT, fg="#1a0f04",
                      relief="flat", font=("Segoe UI", 9, "bold"), padx=12, pady=5,
                      cursor="hand2", command=lambda: self.install_all(valid, total_size)
                      ).pack(side="right")

        if not items:
            tk.Label(frame, text="Новых архивов нет.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w", pady=20)
            return
        for row in items:
            line = tk.Frame(frame, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            line.pack(fill="x", pady=4)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            tk.Label(info, text=row.get("name"), bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                     anchor="w").pack(fill="x")
            tk.Label(info, text=f"{row.get('size_text')} · "
                                f"{'мод' if row.get('valid') else 'не похоже на мод'}",
                     bg=PANEL, fg=DIM, font=("Segoe UI", 8), anchor="w").pack(fill="x")
            button = tk.Button(line, text="Установить", bg=ACCENT, fg="#1a0f04", relief="flat",
                               font=("Segoe UI", 9, "bold"), cursor="hand2",
                               state=("normal" if row.get("valid") else "disabled"),
                               command=lambda r=row: self.install_file(r))
            button.pack(side="left", padx=10)

    def install_all(self, items: list[dict], total_size: int) -> None:
        size_text = human_size(total_size) if total_size else ""
        if not messagebox.askyesno(
                "BeamHub",
                f"Установить {len(items)} архивов ({size_text}) в папку модов?\n\n"
                "Уже установленное и копии с одинаковым названием будут пропущены. "
                "Это может занять несколько минут."):
            return
        try:
            result = self.api.post("/api/inbox/install-all", {})
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")
            return
        if not result.get("started"):
            self.set_status(result.get("reason") or "Не удалось начать установку")
            return
        self.set_status(f"Ставлю {result.get('total', len(items))} архивов…")

    def install_file(self, row: dict) -> None:
        def work() -> None:
            try:
                result = self.api.post("/api/install-file", {"path": row["path"]})
                text = "Уже установлен" if result.get("status") == "duplicate" else "Установлено"
                self.root.after(0, lambda: (self.set_status(text), self.render_inbox()))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- настройки
    def render_settings(self) -> None:
        frame = self._scrollable_frame()
        self.count_label.configure(text="")
        try:
            state = self.api.get("/api/state")
        except Exception as exc:  # noqa: BLE001
            tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
            return
        settings = state.get("settings", {})
        self._setting_vars = {}
        rows = [
            ("mods_dir", "Папка модов игры", settings.get("mods_dir", "")),
            ("downloads_dir", "Папка загрузок", settings.get("downloads_dir", "")),
            ("update_repo", "GitHub-репозиторий обновлений (владелец/репозиторий)",
             settings.get("update_repo", "")),
            ("feedback_email", "Почта для обратной связи", settings.get("feedback_email", "")),
        ]
        for key, label, value in rows:
            block = tk.Frame(frame, bg=BG)
            block.pack(fill="x", pady=8)
            tk.Label(block, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                     anchor="w").pack(fill="x")
            var = tk.StringVar(value=value or "")
            entry = tk.Entry(block, textvariable=var, bg=PANEL, fg=TEXT, relief="flat",
                             insertbackground=TEXT, font=("Segoe UI", 10))
            entry.pack(fill="x", ipady=5, pady=3)
            self._setting_vars[key] = var

        auto_var = tk.BooleanVar(value=bool(settings.get("auto_install", True)))
        self._setting_vars["auto_install"] = auto_var
        tk.Checkbutton(frame, text="Ставить новые архивы из «Загрузок» автоматически",
                       variable=auto_var, bg=BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Segoe UI", 10)).pack(anchor="w", pady=6)

        move_var = tk.BooleanVar(value=bool(settings.get("move_from_downloads", False)))
        self._setting_vars["move_from_downloads"] = move_var
        tk.Checkbutton(frame, text="Переносить архив из «Загрузок» в папку модов "
                                   "(освобождает место на диске)",
                       variable=move_var, bg=BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Segoe UI", 10)).pack(anchor="w", pady=6)

        buttons = tk.Frame(frame, bg=BG)
        buttons.pack(fill="x", pady=10)
        tk.Button(buttons, text="Сохранить", bg=ACCENT, fg="#1a0f04", relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=16, pady=6, cursor="hand2",
                  command=self.save_settings).pack(side="left")
        tk.Button(buttons, text="Пересобрать библиотеку", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=6, cursor="hand2",
                  command=self.rescan).pack(side="left", padx=8)
        tk.Button(buttons, text="Проверить обновления", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=6, cursor="hand2",
                  command=lambda: self.check_updates(silent=False)).pack(side="left")
        tk.Button(buttons, text="Написать автору", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=6, cursor="hand2",
                  command=self.send_feedback).pack(side="left", padx=8)
        tk.Button(buttons, text="Экспорт списка", bg=PANEL, fg=TEXT, relief="flat",
                  font=("Segoe UI", 10), padx=12, pady=6, cursor="hand2",
                  command=self.export_library).pack(side="left")

        info = (f"Версия {state.get('version')} · каталог: {state.get('counts', {}).get('mods')} модов · "
                f"установлено: {state.get('counts', {}).get('installed')}\n"
                f"Игра: {state.get('game_path') or 'не найдена'}\n"
                f"Данные: {state.get('data_dir', '')}")
        tk.Label(frame, text=info, bg=BG, fg=DIM, font=("Segoe UI", 9), justify="left",
                 anchor="w").pack(fill="x", pady=12)

    def save_settings(self) -> None:
        payload = {key: var.get() for key, var in self._setting_vars.items()}
        payload["auto_install"] = bool(self._setting_vars["auto_install"].get())
        try:
            self.api.post("/api/settings", payload)
            self.set_status("Настройки сохранены")
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")

    def rescan(self) -> None:
        try:
            data = self.api.post("/api/library/rescan", {})
            self.set_status(f"Библиотека пересобрана: {data.get('count')} файлов")
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")

    # ---------------------------------------------------------------- обновления
    def check_updates(self, silent: bool = True) -> None:
        self._update_result_cb = lambda data: self._show_update(data, silent)

        def work() -> None:
            try:
                data = self.api.get("/api/update/check")
            except Exception as exc:  # noqa: BLE001
                data = {"status": "error", "message": f"Ошибка проверки: {exc}"}
            self.root.after(0, lambda: self._update_result_cb(data))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, data: dict, silent: bool) -> None:
        status = data.get("status")
        if status == "update":
            latest = data.get("latest", "?")
            self.set_status(f"Доступно обновление {latest}")
            notes = (data.get("notes") or "").strip()
            if notes:
                notes = "\n\n" + notes[:600]
            if messagebox.askyesno("Обновление BeamHub",
                                   f"Вышла версия {latest} (у вас {data.get('current')})."
                                   f"{notes}\n\nСкачать и установить сейчас?"):
                self.apply_update(data)
            return
        if status == "current":
            self.set_status(data.get("message", "Обновлений нет"))
            if not silent:
                messagebox.showinfo("Обновления", data.get("message", "Обновлений нет"))
            return
        if status == "disabled":
            if not silent:
                messagebox.showinfo("Обновления",
                                    "В настройках не указан GitHub-репозиторий для обновлений.")
            return
        if status == "error":
            self.set_status(data.get("message", "Ошибка проверки обновлений"))
            if not silent:
                messagebox.showwarning("Обновления", data.get("message", "Ошибка"))

    def apply_update(self, data: dict) -> None:
        asset = data.get("asset_url") or ""
        if not asset:
            messagebox.showinfo("Обновление",
                                "В релизе нет архива для автообновления.\n"
                                f"Скачайте вручную: {data.get('url', '')}")
            return
        self.set_status("Скачиваю обновление…")

        def work() -> None:
            try:
                result = self.api.post("/api/update/apply",
                                       {"asset_url": asset, "version": data.get("latest", ""),
                                        "digest": data.get("digest", "")})
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "message": f"Ошибка: {exc}"}
            self.root.after(0, lambda: self._finish_update(result))

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, result: dict) -> None:
        if not result.get("ok"):
            messagebox.showerror("Обновление", result.get("message", "Не удалось"))
            return
        messagebox.showinfo("Обновление", result.get("message", "Готово. BeamHub перезапустится."))
        try:
            self.api.post("/api/quit", {})
        except Exception:  # noqa: BLE001
            pass
        self.root.after(400, self.root.destroy)

    def send_feedback(self) -> None:
        def work() -> None:
            try:
                data = self.api.get("/api/feedback")
                if not data.get("ok"):
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Обратная связь",
                        data.get("message", "Почта не настроена.") +
                        "\n\nУкажите её в поле «Почта для обратной связи»."))
                    return
                self.root.after(0, lambda: self.set_status(
                    "Открыл почтовый клиент — письмо уже с версией и путями"))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def export_library(self) -> None:
        def work() -> None:
            try:
                data = self.api.post("/api/library/export", {})
                self.root.after(0, lambda: messagebox.showinfo(
                    "Экспорт", f"Список из {data.get('count')} модов сохранён:\n{data.get('path')}"))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- прочее
    def _search_debounced(self) -> None:
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(400, self.on_search)

    def on_search(self) -> None:
        self.query = self.search_var.get().strip()
        self.switch("catalog")

    def on_sort(self) -> None:
        mapping = {"Сначала новые": "new", "Сначала старые": "old", "По названию А→Я": "title",
                   "По названию Я→А": "title_desc", "По размеру": "size",
                   "По популярности": "downloads"}
        self.sort = mapping.get(self.sort_var.get(), "new")
        self.switch("catalog")

    def on_source(self) -> None:
        self.source = self._source_map.get(self.source_var.get(), "all")
        self.switch("catalog")

    def on_filters(self) -> None:
        self.switch("catalog")

    def toggle_favorite(self, item: dict, button: tk.Button) -> None:
        def work() -> None:
            try:
                result = self.api.post("/api/favorite", {"id": item["id"]})
                state = bool(result.get("favorite"))
                item["favorite"] = state

                def apply() -> None:
                    if button.winfo_exists():
                        button.configure(text="★" if state else "☆",
                                         fg="#ffd08a" if state else MUTED)
                    if self.filter_fav.get():
                        self.switch("catalog")
                    self.refresh_meta()

                self.root.after(0, apply)
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def render_updates(self) -> None:
        frame = self._scrollable_frame()
        self.count_label.configure(text="")
        try:
            data = self.api.get("/api/library")
        except Exception as exc:  # noqa: BLE001
            tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
            return
        rows = [row for row in data.get("items", [])
                if row.get("update_available") or row.get("version_unknown")]
        known = sum(1 for row in rows if row.get("update_available"))
        self.count_label.configure(
            text=f"{known} обновлений" + (f" · {len(rows) - known} без версии" if len(rows) > known else ""))
        if not rows:
            tk.Label(frame, text="Все установленные моды актуальны.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w", pady=20)
            return
        for row in rows:
            line = tk.Frame(frame, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            line.pack(fill="x", pady=4)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            tk.Label(info, text=row.get("title") or row.get("filename"), bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x")
            if row.get("update_available"):
                note = f"установлено {row.get('version')} → в каталоге {row.get('catalog_version')}"
                color = "#ffd6a3"
            else:
                note = f"версия файла неизвестна, в каталоге {row.get('catalog_version')}"
                color = MUTED
            tk.Label(info, text=note, bg=PANEL, fg=color, font=("Segoe UI", 8),
                     anchor="w").pack(fill="x")
            tk.Button(line, text="Скачать новую", bg=ACCENT, fg="#1a0f04", relief="flat",
                      font=("Segoe UI", 9, "bold"), padx=12, cursor="hand2",
                      command=lambda r=row: self.install({"id": r["mod_id"],
                                                          "title": r.get("title")})
                      ).pack(side="left", padx=10)

    def on_sync(self) -> None:
        self.set_status("Обновляю каталог…")

        def work() -> None:
            try:
                data = self.api.post("/api/sync", {})
                self.root.after(0, lambda: self.set_status(f"Добавлено модов: {data.get('added')}"))
                self.root.after(0, lambda: self.switch("catalog"))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def on_add_link(self) -> None:
        from tkinter import simpledialog

        url = simpledialog.askstring("Добавить мод по ссылке",
                                     "Ссылка на страницу мода:", parent=self.root)
        if not url:
            return
        self.set_status("Читаю страницу…")

        def work() -> None:
            try:
                data = self.api.post("/api/add-link", {"url": url.strip()})
                title = data.get("item", {}).get("title", "мод")
                self.root.after(0, lambda: self.set_status(f"Добавлено: {title}"))
                self.root.after(0, lambda: self.switch("catalog"))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def on_quit(self) -> None:
        if not messagebox.askyesno("BeamHub", "Остановить BeamHub?"):
            return
        try:
            self.api.post("/api/quit", {})
        except Exception:  # noqa: BLE001
            pass
        self.root.after(300, self.root.destroy)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def refresh_meta(self) -> None:
        def work() -> None:
            try:
                state = self.api.get("/api/state")
                self.root.after(0, lambda: self._apply_meta(state))
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.root.after(15000, self.refresh_meta)
        threading.Thread(target=work, daemon=True).start()

    def _apply_meta(self, state: dict) -> None:
        self.foot_label.configure(text=f"Папка модов:\n{state.get('settings', {}).get('mods_dir', '')}")
        counts = state.get("counts", {})
        self.nav_buttons["catalog"].configure(text=f"Каталог   {counts.get('mods', 0)}")
        self.nav_buttons["library"].configure(text=f"Библиотека   {counts.get('installed', 0)}")
        self.nav_buttons["updates"].configure(text=f"Обновления   {state.get('updates', 0)}")
        self.nav_buttons["inbox"].configure(text=f"Загрузки   {state.get('inbox_count', 0)}")

        sources = state.get("sources", [])
        if sources:
            self._source_map = {"Все источники": "all"}
            labels = ["Все источники"]
            for item in sources:
                label = f"{item['label']} ({item['count']})"
                self._source_map[label] = item["key"]
                labels.append(label)
            self.source_combo.configure(values=labels)
            if self.source_var.get() not in self._source_map:
                self.source_var.set("Все источники")
                self.source = "all"

        if state.get("game_running"):
            self.status.configure(
                text="BeamNG.drive запущена — моды подхватятся после перезапуска игры",
                fg="#ffd6a3")
        cats = state.get("categories", [])
        for child in self.cat_frame.winfo_children():
            child.destroy()
        all_row = tk.Button(self.cat_frame, text=f"Все моды   {state.get('counts', {}).get('mods', 0)}",
                            bg=SOFT, fg=TEXT if self.category == "all" else MUTED, relief="flat",
                            anchor="w", font=("Segoe UI", 10), padx=10, pady=5, cursor="hand2",
                            command=lambda: self.pick_category("all"))
        all_row.pack(fill="x", pady=1)
        for cat in cats:
            if not cat.get("count"):
                continue
            row = tk.Button(self.cat_frame, text=f"{cat['label']}   {cat['count']}",
                            bg=SOFT, fg=TEXT if self.category == cat["key"] else MUTED,
                            relief="flat", anchor="w", font=("Segoe UI", 10), padx=10, pady=5,
                            cursor="hand2", command=lambda k=cat["key"]: self.pick_category(k))
            row.pack(fill="x", pady=1)

    def pick_category(self, key: str) -> None:
        self.category = key
        self.switch("catalog")
        self.refresh_meta()

    def poll_events(self) -> None:
        def work() -> None:
            try:
                data = self.api.get("/api/events", since=self._event_seq)
                self._event_seq = data.get("seq", self._event_seq)
                events = data.get("events", [])
                if events:
                    last = events[-1]
                    self.root.after(0, lambda t=last.get("text", ""): self.set_status(t))
                    for event in events:
                        if event.get("kind") == "inbox_done":
                            text = (f"Установлено: {event.get('installed', 0)}\n"
                                    f"Пропущено: {event.get('skipped', 0)}\n"
                                    f"Ошибок: {event.get('failed', 0)}")
                            self.root.after(0, lambda t=text: messagebox.showinfo(
                                "Установка архивов", t))
                            self.root.after(0, self.refresh_all)
                            break
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.root.after(2500, self.poll_events)
        threading.Thread(target=work, daemon=True).start()


def run(base_url: str) -> None:
    root = tk.Tk()
    App(root, base_url)
    root.mainloop()
