"""Каталог модов в родном окне (tkinter) — без браузера и без WebView.

Интерфейс повторяет веб-версию: сайдбар с категориями, сетка карточек с превью,
поиск, сортировка, библиотека, загрузки и настройки. Данные берутся у локального
API (тот же процесс), картинки — через встроенный прокси с кешем.
"""
from __future__ import annotations

import json
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import messagebox, ttk

from . import i18n, theme
from .util import human_ago

BG = "#0d0e11"
SOFT = "#12141a"
PANEL = "#171a21"
LINE = "#262b36"
TEXT = "#e9ebf0"
MUTED = "#8d93a1"
DIM = "#6b7280"
ACCENT = "#ff7a1a"
GREEN = "#35c46b"
HOVER_BG = "#1b1f28"
ACTIVE_BG = "#232833"
DUP_BG = "#2a1618"
DUP_LINE = "#7a2f33"
SOURCE_NAMES = {"playground": "playground.ru", "modsgaming": "modsgaming.ru",
                "beamngland": "beamngland.com", "beamngmods": "beamngmods.net",
                "link": "по ссылке"}

FONT = "Segoe UI"          # в App.__init__ подменится на Segoe UI Variable (Windows 11)
FONT_BOLD = "Segoe UI Semibold"

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
        self.user_token = ""
        try:
            from . import security

            self.token = security.token()
        except Exception:  # noqa: BLE001
            self.token = ""

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"X-BeamHub-Token": self.token}
        if self.user_token:
            headers["X-BeamHub-User"] = self.user_token
        if extra:
            headers.update(extra)
        return headers

    def get(self, path: str, **params) -> dict:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(request, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data,
            headers=self._headers({"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"ok": False, "error": body[:200]}
            data.setdefault("ok", False)
            return data

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
        self.user: dict | None = None
        self.coll_kind = "popular"

        root.title("BeamHub — каталог модов BeamNG.drive")
        root.geometry("1360x860")
        root.minsize(1100, 700)
        root.configure(bg=BG)
        global FONT, FONT_BOLD
        FONT, FONT_BOLD = theme.font_setup()
        theme.dark_titlebar(root)
        self._styles()
        self._build()
        self.reload()
        self.refresh_meta()
        self.poll_events()
        self.root.after(5000, lambda: self.check_updates(silent=True))
        self.root.after(1500, self._lang_tick)
        threading.Thread(target=self._restore_session, daemon=True).start()

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
        style.configure("Vertical.TScrollbar", background="#2a2f3a", troughcolor=BG,
                        bordercolor=BG, arrowcolor=BG, arrowsize=0, width=10,
                        gripcount=0, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", "#3a4150")])
        style.configure("TCombobox", padding=7, borderwidth=0, relief="flat")

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
                         insertbackground=TEXT, relief="flat", font=(FONT, 11))
        entry.pack(fill="x", padx=8, ipady=6)
        placeholder = tk.Label(search_box, text="Поиск: bmw, карта, vaz, дрифт…", bg=PANEL,
                               fg=DIM, font=(FONT, 11))
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

        theme.RoundButton(top, "Обновить каталог", self.on_sync, height=32, font=(FONT, 9),
                          bg=PANEL, fg=TEXT, hover=ACTIVE_BG, radius=8).pack(side="left", padx=8)
        theme.RoundButton(top, "＋ Ссылка", self.on_add_link, height=32, font=(FONT, 9),
                          bg=PANEL, fg=TEXT, hover=ACTIVE_BG, radius=8).pack(side="left")
        self.account_button = theme.RoundButton(top, "Войти", self.open_auth, height=32,
                                                width=124, font=(FONT, 9), bg=PANEL, fg=TEXT,
                                                hover=ACTIVE_BG, radius=8)
        self.account_button.pack(side="left", padx=(8, 0))
        self.lang_button = theme.RoundButton(top, self._lang_label(), self.toggle_language,
                                             height=32, font=(FONT_BOLD, 9), width=54,
                                             bg=PANEL, fg=TEXT, hover=ACTIVE_BG, radius=8)
        self.lang_button.pack(side="left", padx=8)

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
                                    font=(FONT_BOLD, 17))
        self.title_label.pack(side="left")
        self.count_label = tk.Label(self.head, text="", bg=BG, fg=DIM, font=(FONT, 10))
        self.count_label.pack(side="left", padx=12)

        filters = tk.Frame(self.head, bg=BG)
        filters.pack(side="right")
        self.filter_game = tk.BooleanVar(value=False)
        self.filter_image = tk.BooleanVar(value=False)
        self.filter_fav = tk.BooleanVar(value=False)
        self.filter_chips: dict[str, object] = {}
        for text, var, key in (("0.40.x", self.filter_game, "game"),
                               ("с обложкой", self.filter_image, "image"),
                               ("избранное", self.filter_fav, "fav")):
            chip = theme.RoundButton(filters, text, lambda v=var, k=key: self._toggle_chip(v, k),
                                     height=28, radius=14, bg=PANEL, fg=MUTED,
                                     hover=ACTIVE_BG, font=(FONT, 9))
            chip.pack(side="left", padx=3)
            self.filter_chips[key] = chip

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
                               font=(FONT, 9), padx=14, pady=6)
        self.status.grid(row=2, column=0, sticky="ew")

    NAV_ITEMS = (("catalog", "Каталог", "grid"),
                 ("collections", "Сборки", "layers"),
                 ("library", "Библиотека", "book"),
                 ("updates", "Обновления", "arrow"),
                 ("inbox", "Загрузки", "tray"),
                 ("settings", "Настройки", "gear"))

    def _build_sidebar(self) -> None:
        brand = tk.Frame(self.sidebar, bg=SOFT)
        brand.pack(fill="x", padx=14, pady=(16, 10))
        tk.Label(brand, text="B", bg=ACCENT, fg="#14100c", font=(FONT_BOLD, 15),
                 width=2).pack(side="left")
        titles = tk.Frame(brand, bg=SOFT)
        titles.pack(side="left", padx=10)
        tk.Label(titles, text="BeamHub", bg=SOFT, fg=TEXT, font=(FONT_BOLD, 14),
                 anchor="w").pack(anchor="w")
        tk.Label(titles, text="каталог модов BeamNG.drive", bg=SOFT, fg=DIM,
                 font=(FONT, 8), anchor="w").pack(anchor="w")

        self.nav_buttons: dict[str, tk.Frame] = {}
        self.nav_parts: dict[str, dict] = {}
        for key, title, icon in self.NAV_ITEMS:
            row = tk.Frame(self.sidebar, bg=SOFT, cursor="hand2", height=36)
            row.pack(fill="x", padx=6, pady=1)
            row.pack_propagate(False)
            bar = tk.Canvas(row, width=3, bg=SOFT, highlightthickness=0, bd=0)
            bar.pack(side="left", fill="y")
            icon_canvas = tk.Canvas(row, width=24, height=36, bg=SOFT, highlightthickness=0, bd=0)
            icon_canvas.pack(side="left", padx=(10, 4))
            label = tk.Label(row, text=title, bg=SOFT, fg=MUTED, font=(FONT, 10), anchor="w")
            label.pack(side="left", fill="x", expand=True)
            badge = tk.Label(row, text="", bg=SOFT, fg=DIM, font=(FONT_BOLD, 8), padx=6)
            badge.pack(side="right", padx=(4, 8))
            self._draw_icon(icon_canvas, icon, MUTED)
            self.nav_parts[key] = {"row": row, "bar": bar, "icon": icon_canvas, "kind": icon,
                                   "label": label, "badge": badge}
            self.nav_buttons[key] = row
            for widget in (row, bar, icon_canvas, label, badge):
                widget.bind("<Button-1>", lambda _e, k=key: self.switch(k))
                widget.bind("<Enter>", lambda _e, k=key: self._nav_hover(k, True))
                widget.bind("<Leave>", lambda _e, k=key: self._nav_hover(k, False))

        tk.Frame(self.sidebar, bg=LINE, height=1).pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(self.sidebar, text="КАТЕГОРИИ", bg=SOFT, fg=DIM, font=(FONT, 8),
                 anchor="w").pack(fill="x", padx=18, pady=(4, 4))
        self.cat_frame = tk.Frame(self.sidebar, bg=SOFT)
        self.cat_frame.pack(fill="both", expand=True, padx=8)

        foot = tk.Frame(self.sidebar, bg=SOFT)
        foot.pack(side="bottom", fill="x", padx=14, pady=12)
        self.foot_label = tk.Label(foot, text="", bg=SOFT, fg=DIM, font=(FONT, 8),
                                   justify="left", wraplength=200, anchor="w")
        self.foot_label.pack(anchor="w")

        tk.Button(self.sidebar, text="Остановить BeamHub", bg=SOFT, fg="#ff9a9a",
                  activebackground="#241214", activeforeground="#ffb3b3", relief="flat",
                  font=(FONT, 9), anchor="w", padx=14, pady=6, cursor="hand2",
                  command=self.on_quit).pack(side="bottom", fill="x", padx=8)

    def _draw_icon(self, canvas: tk.Canvas, kind: str, color: str) -> None:
        """Иконки рисуем сами: emoji в tkinter выглядят по-разному на разных Windows."""
        canvas.delete("all")
        if kind == "grid":
            for dx, dy in ((0, 0), (10, 0), (0, 10), (10, 10)):
                canvas.create_rectangle(5 + dx, 10 + dy, 11 + dx, 16 + dy, fill=color, width=0)
        elif kind == "book":
            canvas.create_rectangle(4, 9, 10, 27, fill=color, width=0)
            canvas.create_rectangle(12, 9, 18, 27, fill=color, width=0)
        elif kind == "arrow":
            canvas.create_line(11, 8, 11, 23, fill=color, width=2)
            canvas.create_line(6, 18, 11, 24, 16, 18, fill=color, width=2)
        elif kind == "tray":
            canvas.create_line(4, 22, 4, 27, 18, 27, 18, 22, fill=color, width=2)
            canvas.create_line(11, 7, 11, 18, fill=color, width=2)
            canvas.create_line(6, 14, 11, 20, 16, 14, fill=color, width=2)
        elif kind == "layers":
            canvas.create_polygon(11, 6, 19, 11, 11, 16, 3, 11, fill="", outline=color, width=1)
            canvas.create_line(3, 15, 11, 20, 19, 15, fill=color, width=1)
            canvas.create_line(3, 19, 11, 24, 19, 19, fill=color, width=1)
        elif kind == "gear":
            canvas.create_oval(6, 12, 16, 22, outline=color, width=2)
            for coords in ((10, 5, 12, 9), (10, 25, 12, 29), (3, 16, 6, 18), (16, 16, 19, 18)):
                canvas.create_line(*coords, fill=color, width=2)

    def _nav_background(self, key: str, hover: bool = False) -> str:
        if key == self.view:
            return ACTIVE_BG
        return HOVER_BG if hover else SOFT

    def _paint_nav(self, key: str, hover: bool = False) -> None:
        parts = self.nav_parts.get(key)
        if not parts:
            return
        active = key == self.view
        bg = self._nav_background(key, hover)
        for name in ("row", "bar", "icon", "label"):
            parts[name].configure(bg=bg)
        if not parts["badge"].cget("text"):
            parts["badge"].configure(bg=bg)
        parts["label"].configure(fg=(TEXT if active else (TEXT if hover else MUTED)),
                                 font=(FONT_BOLD if active else FONT, 10))
        self._draw_icon(parts["icon"], parts["kind"], ACCENT if active else (TEXT if hover else MUTED))
        parts["bar"].delete("all")
        if active:
            parts["bar"].create_rectangle(0, 7, 3, 29, fill=ACCENT, width=0)

    def _nav_hover(self, key: str, on: bool) -> None:
        if key != self.view:
            self._paint_nav(key, hover=on)

    def _set_active_nav(self, view: str) -> None:
        for key in self.nav_parts:
            self._paint_nav(key)
            parts = self.nav_parts[key]
            parts["bar"].delete("all")
            if key == view:
                parts["bar"].create_rectangle(0, 7, 3, 29, fill=ACCENT, width=0)

    def set_badge(self, key: str, value: int | str, accent: bool = False) -> None:
        parts = self.nav_parts.get(key)
        if not parts:
            return
        text = str(value) if value else ""
        if text:
            parts["badge"].configure(text=text, bg=(ACCENT if accent else "#2a2f3a"),
                                     fg=("#1a0f04" if accent else DIM))
        else:
            parts["badge"].configure(text="", bg=self._nav_background(key))

    def toast(self, text: str, kind: str = "info") -> None:
        """Всплывающее уведомление снизу справа — чтобы события не терялись в строке статуса."""
        text = i18n.tr(text)
        palette = {"info": (PANEL, TEXT), "ok": ("#16241a", "#b8e6c2"),
                   "error": ("#2a1518", "#ffb3b3")}
        bg, fg = palette.get(kind, palette["info"])
        try:
            window = tk.Toplevel(self.root)
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            frame = tk.Frame(window, bg=bg, highlightthickness=1, highlightbackground=LINE)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=text, bg=bg, fg=fg, font=(FONT, 9),
                     wraplength=320, justify="left", padx=12, pady=8).pack()
            window.update_idletasks()
            width = min(360, frame.winfo_reqwidth())
            height = frame.winfo_reqheight()
            x = self.root.winfo_rootx() + self.root.winfo_width() - width - 40
            y = self.root.winfo_rooty() + self.root.winfo_height() - height - 62
            window.geometry(f"{width}x{height}+{x}+{y}")
            window.attributes("-alpha", 0.0)

            def fade(step: float = 0.0) -> None:
                if step >= 1.0:
                    window.after(1800, lambda: close(1.0))
                    return
                try:
                    window.attributes("-alpha", step)
                except tk.TclError:
                    return
                window.after(30, lambda: fade(step + 0.12))

            def close(step: float) -> None:
                if step <= 0.0:
                    window.destroy()
                    return
                try:
                    window.attributes("-alpha", step)
                except tk.TclError:
                    window.destroy()
                    return
                window.after(30, lambda: close(step - 0.12))

            fade()
        except Exception:  # noqa: BLE001
            self.set_status(text)

    def _toggle_chip(self, var: tk.BooleanVar, key: str) -> None:
        var.set(not var.get())
        self._paint_chips()
        self.on_filters()

    def _paint_chips(self) -> None:
        for key, var in (("game", self.filter_game), ("image", self.filter_image),
                         ("fav", self.filter_fav)):
            chip = self.filter_chips.get(key)
            if chip is None:
                continue
            if var.get():
                chip.set_palette(bg="#3a2410", fg="#ffc79b", hover="#4a2e14")
            else:
                chip.set_palette(bg=PANEL, fg=MUTED, hover=ACTIVE_BG)

    def refresh_all(self) -> None:
        """Перерисовать текущую вкладку и обновить счётчики в меню."""
        self.switch(self.view)
        self.refresh_meta()

    # ---------------------------------------------------------------- язык
    def _lang_label(self) -> str:
        return "EN" if i18n.current() == "ru" else "RU"

    def toggle_language(self) -> None:
        self.set_language("en" if i18n.current() == "ru" else "ru")

    def _lang_tick(self) -> None:
        """Подстраховка: перевести то, что появилось после перерисовки (диалоги, тосты)."""
        try:
            self._translate_ui(self.root)
        except Exception:  # noqa: BLE001
            pass
        self.root.after(2500, self._lang_tick)

    def set_language(self, code: str) -> None:
        i18n.set_lang(code)
        try:
            self.api.post("/api/settings", {"language": i18n.current()})
        except Exception:  # noqa: BLE001
            pass
        self._translate_ui(self.root)
        self.refresh_all()
        try:
            self.lang_button.configure(text=self._lang_label())
        except Exception:  # noqa: BLE001
            pass

    def _translate_ui(self, widget=None) -> None:
        """Перевести все подписи в дереве виджетов (и вернуть русские при откате)."""
        widget = widget or self.root
        english = i18n.current() == "en"
        try:
            klass = widget.winfo_class()
        except Exception:  # noqa: BLE001
            return

        if klass in ("Tk", "Toplevel"):
            try:
                current = widget.title()
                if english and re.search("[А-Яа-яЁё]", current):
                    widget._ru_title = getattr(widget, "_ru_title", current)
                    widget.title(i18n.tr(current))
                elif not english and getattr(widget, "_ru_title", None):
                    widget.title(widget._ru_title)
            except Exception:  # noqa: BLE001
                pass

        if klass in ("Label", "Button", "Checkbutton", "Radiobutton", "TLabel", "TButton"):
            try:
                current = widget.cget("text")
            except Exception:  # noqa: BLE001
                current = None
            if current is not None:
                if english:
                    original = getattr(widget, "_ru_text", None)
                    # текст поменялся на другой русский — запоминаем новую основу
                    if re.search("[А-Яа-яЁё]", current) and (not original or current != i18n.tr(original)):
                        widget._ru_text = current
                        original = current
                    if original:
                        translated = i18n.tr(original)
                        if translated != current:
                            widget.configure(text=translated)
                else:
                    original = getattr(widget, "_ru_text", None)
                    if original and current != original:
                        widget.configure(text=original)

        if klass == "TCombobox":
            try:
                values = list(widget.cget("values"))
            except Exception:  # noqa: BLE001
                values = []
            if english and widget.get() and re.search("[А-Яа-яЁё]", widget.get()):
                widget._ru_value = getattr(widget, "_ru_value", widget.get())
                widget.set(i18n.tr(widget._ru_value))
            elif not english and getattr(widget, "_ru_value", None):
                widget.set(widget._ru_value)
            if values and english:
                widget._ru_values = getattr(widget, "_ru_values", values)
                widget.configure(values=[i18n.tr(v) for v in getattr(widget, "_ru_values", values)])
            elif values and not english and getattr(widget, "_ru_values", None):
                widget.configure(values=list(widget._ru_values))

        if isinstance(widget, tk.Canvas):
            store = getattr(widget, "_ru_items", {})
            for item in widget.find_all():
                if widget.type(item) != "text":
                    continue
                current = widget.itemcget(item, "text")
                if not current:
                    continue
                if english:
                    if re.search("[А-Яа-яЁё]", current):
                        store[item] = current
                    original = store.get(item)
                    if original:
                        new = i18n.tr(original)
                        if new != current:
                            widget.itemconfigure(item, text=new)
                else:
                    original = store.get(item)
                    if original and current != original:
                        widget.itemconfigure(item, text=original)
            widget._ru_items = store

        for child in widget.winfo_children():
            self._translate_ui(child)

    # ---------------------------------------------------------------- навигация
    def switch(self, view: str) -> None:
        self.view = view
        self._set_active_nav(view)
        titles = {"catalog": "Каталог модов", "collections": "Сборки", "library": "Библиотека",
                  "updates": "Обновления", "inbox": "Загрузки", "settings": "Настройки"}
        self.title_label.configure(text=titles[view])
        if view == "catalog":
            self.reload()
        elif view == "collections":
            self.render_collections()
        elif view == "library":
            self.render_library()
        elif view == "updates":
            self.render_updates()
        elif view == "inbox":
            self.render_inbox()
        else:
            self.render_settings()
        self._translate_ui(self.root)

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
                      font=(FONT, 10), padx=18, pady=8, cursor="hand2",
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
        thumb = tk.Label(holder, bg="#0a0b0e", bd=0, text="", fg=DIM, font=(FONT, 8))
        thumb.pack(fill="both", expand=True)
        if item.get("image"):
            self._request_image(item["image"], thumb, item)
        else:
            self._set_placeholder(thumb, item)
        if duplicate:
            sources = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            badge_text = f"дубликат · {item.get('duplicate_count')}"
            badge = tk.Label(holder, text=badge_text, bg="#4a2a12", fg="#ffc79b",
                             font=(FONT_BOLD, 8), padx=6, pady=2)
            badge.place(relx=0, rely=1.0, x=6, y=-6, anchor="sw")
            badge.bind("<Enter>", lambda _e, b=badge, s=sources: b.configure(
                text=f"дубликат · ещё {s}" if s else b.cget("text")))

        badge = tk.Frame(card, bg=PANEL)
        badge.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(badge, text=item.get("category_label", "Другое"), bg=PANEL, fg="#ffc79b",
                 font=(FONT, 8)).pack(side="left")
        size = item.get("size_text") or ""
        if size:
            tk.Label(badge, text=size, bg=PANEL, fg=DIM, font=(FONT, 8)).pack(side="right")

        title = item.get("title") or "Без названия"
        label = tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=(FONT_BOLD, 10),
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
        tk.Label(card, text=" · ".join(meta_bits), bg=PANEL, fg=DIM, font=(FONT, 8),
                 anchor="w").pack(fill="x", padx=10)

        actions = tk.Frame(card, bg=PANEL)
        actions.pack(fill="x", padx=10, pady=(8, 10))
        text = "Установлен" if item.get("installed") else "Скачать"
        button = theme.RoundButton(actions, text, lambda i=item: self.install(i),
                                   variant="primary", height=30, radius=8, font=(FONT_BOLD, 9))
        button.pack(side="left", fill="x", expand=True)
        star = tk.Button(actions, text="★" if item.get("favorite") else "☆", bg=PANEL,
                         fg=("#ffd08a" if item.get("favorite") else MUTED), relief="flat",
                         font=(FONT, 11), cursor="hand2", activebackground="#232833",
                         activeforeground=TEXT)
        star.configure(command=lambda i=item, b=star: self.toggle_favorite(i, b))
        star.pack(side="left", padx=(4, 0))
        tk.Button(actions, text="…", bg=PANEL, fg=MUTED, relief="flat", font=(FONT, 9),
                  cursor="hand2", activebackground="#232833", activeforeground=TEXT,
                  command=lambda i=item: self.details(i)).pack(side="left", padx=(4, 0))

        for widget in (thumb, label):
            widget.bind("<Double-Button-1>", lambda _e, i=item: self.details(i))
        self._bind_card_hover(card, duplicate)
        return card

    def _bind_card_hover(self, card: tk.Frame, duplicate: bool) -> None:
        """Подсветка карточки при наведении — без мигания, когда ведёшь по дочерним виджетам."""
        normal = DUP_LINE if duplicate else LINE

        def inside() -> bool:
            x, y = card.winfo_pointerxy()
            widget = card.winfo_containing(x, y)
            while widget is not None:
                if widget is card:
                    return True
                widget = getattr(widget, "master", None)
            return False

        def enter(_event=None) -> None:
            card.configure(highlightbackground="#55617a")

        def leave(_event=None) -> None:
            card.after(30, lambda: None if inside() else card.configure(highlightbackground=normal))

        widgets = [card]
        pending = list(card.winfo_children())
        while pending:
            widget = pending.pop()
            widgets.append(widget)
            pending.extend(widget.winfo_children())
        for widget in widgets:
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

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
            label.configure(text=(item.get("title") or "")[:24], fg=MUTED, font=(FONT, 8))
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
                          font=(FONT_BOLD, 14), wraplength=700, justify="left",
                          anchor="w")
        header.pack(fill="x", padx=18, pady=(16, 4))

        meta = " · ".join(filter(None, [
            item.get("category_label"), f"игра {item['game_version']}" if item.get("game_version") else "",
            f"версия {item['version']}" if item.get("version") else "",
            item.get("size_text"), f"{item['downloads']} скачиваний" if item.get("downloads") else "",
            item.get("ago")]))
        tk.Label(window, text=meta, bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=18)
        if item.get("duplicate"):
            names = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            tk.Label(window, text=f"Такой же мод есть ещё на: {names or 'других сайтах'} "
                                  f"(всего {item.get('duplicate_count')})",
                     bg=BG, fg="#ffb27a", font=(FONT, 9), anchor="w",
                     wraplength=700, justify="left").pack(fill="x", padx=18, pady=(4, 0))

        text = tk.Text(window, bg=PANEL, fg="#c3c8d4", relief="flat", wrap="word",
                       font=(FONT, 10), padx=12, pady=10, height=12)
        text.insert("1.0", item.get("description") or "Описание на странице мода.")
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, padx=18, pady=12)

        extras = tk.Frame(window, bg=BG)
        extras.pack(fill="x", padx=18, pady=(0, 8))
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        theme.RoundButton(buttons, "Скачать", lambda: self.install(item), variant="primary",
                          height=34, radius=9, font=(FONT_BOLD, 10)).pack(side="left")
        theme.RoundButton(buttons, "＋ В сборку", lambda: self.add_to_collection_from_catalog(item),
                          height=34, radius=9, bg=PANEL, fg=TEXT, hover=ACTIVE_BG,
                          font=(FONT, 10)).pack(side="left", padx=8)
        theme.RoundButton(buttons, "Открыть страницу", lambda: self._open_page(item),
                          height=34, radius=9, bg=PANEL, fg=TEXT, hover=ACTIVE_BG,
                          font=(FONT, 10)).pack(side="left", padx=8)
        threading.Thread(target=self._details_extras, args=(window, extras, item),
                         daemon=True).start()

    # ---------------------------------------------------------------- доп. сведения
    def _details_extras(self, window: tk.Misc, frame: tk.Frame, item: dict) -> None:
        """Совместимость и похожие моды подгружаем в фоне — окно открывается сразу."""
        compat: dict | None = None
        similar: dict = {"items": []}
        try:
            compat = self.api.get("/api/compat", id=item["id"])
        except Exception:  # noqa: BLE001
            compat = None
        try:
            similar = self.api.get("/api/recommend/mods", id=item["id"], limit=6)
        except Exception:  # noqa: BLE001
            similar = {"items": []}

        def apply() -> None:
            if not frame.winfo_exists():
                return
            if compat and compat.get("ok"):
                verdict = compat.get("verdict") or {}
                file_info = compat.get("file") or {}
                game = compat.get("game") or {}
                colors = {"ok": "#8fe0a4", "missing": "#ffb3b3", "unknown": "#ffd6a3"}
                status_color = {"проверено BeamHub": "#8fe0a4",
                                "неизвестно": "#ffd6a3"}.get(verdict.get("file"), TEXT)
                tk.Label(frame, text="Совместимость", bg=BG, fg=TEXT, font=(FONT_BOLD, 10),
                         anchor="w").pack(fill="x", pady=(6, 2))
                tk.Label(frame,
                         text=f"Файл: {verdict.get('file')} — {file_info.get('detail', '')}",
                         bg=BG, fg=status_color, font=(FONT, 8), anchor="w",
                         wraplength=700, justify="left").pack(fill="x")
                tk.Label(frame,
                         text=f"Версия игры: заявлено «{game.get('declared') or 'не указано'}», "
                              f"установлено «{game.get('installed') or 'не определено'}»",
                         bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x")
                conflicts = compat.get("conflicts") or []
                if conflicts:
                    names = ", ".join(row.get("title") or row.get("filename") or "?"
                                      for row in conflicts[:3])
                    tk.Label(frame, text=f"Возможные конфликты: {names}", bg=BG, fg="#ffb27a",
                             font=(FONT, 8), anchor="w", wraplength=700,
                             justify="left").pack(fill="x")
                else:
                    tk.Label(frame, text="Конфликтов с установленными модами не найдено",
                             bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x")
            items = (similar or {}).get("items") or []
            if items:
                tk.Label(frame, text="Похожие моды", bg=BG, fg=TEXT, font=(FONT_BOLD, 10),
                         anchor="w").pack(fill="x", pady=(8, 2))
                for row in items[:5]:
                    line = tk.Frame(frame, bg=BG)
                    line.pack(fill="x", pady=1)
                    tk.Label(line, text=row.get("title") or row["id"], bg=BG, fg=TEXT,
                             font=(FONT, 9), anchor="w").pack(side="left", fill="x", expand=True)
                    theme.RoundButton(line, "Открыть",
                                      lambda r=row: self.details_by_id(r["id"]),
                                      height=24, radius=8, font=(FONT, 8)).pack(side="right")
            self._translate_ui(window)
        self.root.after(0, apply)

    def details_by_id(self, mod_id: str) -> None:
        try:
            data = self.api.get("/api/mod", id=mod_id)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Не удалось открыть мод: {exc}", "error")
            return
        if not data.get("ok") and data.get("item") is None:
            self.toast(data.get("error", "Мод не найден"), "error")
            return
        self.details(data.get("item") or data)

    def stats_window(self) -> None:
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Статистика сообщества")
        window.geometry("820x720")
        window.transient(self.root)
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def render() -> None:
            for child in inner.winfo_children():
                child.destroy()
            try:
                data = self.api.get("/api/stats")
            except Exception as exc:  # noqa: BLE001
                tk.Label(inner, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
                return
            tk.Label(inner,
                     text=f"Пользователей: {data.get('users', 0)} "
                          f"(активных за 7 дней: {data.get('active_users_7d', 0)}) · "
                          f"сборок: {data.get('collections', 0)} · "
                          f"установок: {data.get('installs', 0)} · "
                          f"просмотров: {data.get('views', 0)}",
                     bg=BG, fg=TEXT, font=(FONT_BOLD, 10), anchor="w", wraplength=760,
                     justify="left").pack(fill="x", padx=18, pady=(14, 2))
            tk.Label(inner, text=f"Комментариев: {data.get('comments', 0)} · "
                                 f"оценок: {data.get('ratings', 0)} · "
                                 f"отзывов с текстом: {data.get('reviews', 0)}",
                     bg=BG, fg=DIM, font=(FONT, 9), anchor="w").pack(fill="x", padx=18)

            def section(title: str) -> None:
                tk.Label(inner, text=title, bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                         anchor="w").pack(fill="x", padx=18, pady=(12, 2))

            section("Популярные моды (по установкам)")
            if not data.get("top_mods"):
                tk.Label(inner, text="Пока нет установок — статистика появится, когда моды "
                                     "начнут ставить через BeamHub.",
                         bg=BG, fg=DIM, font=(FONT, 9), anchor="w", wraplength=760,
                         justify="left").pack(fill="x", padx=24)
            for index, row in enumerate(data.get("top_mods", [])[:8], start=1):
                tk.Label(inner,
                         text=f"{index}. {row.get('title')} — установок "
                              f"{row.get('installs', 0)}, кликов {row.get('downloads', 0)}, "
                              f"просмотров {row.get('views', 0)}",
                         bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", padx=24)

            section("Популярные сборки")
            if not data.get("top_collections"):
                tk.Label(inner, text="Опубликованных сборок пока нет.", bg=BG, fg=DIM,
                         font=(FONT, 9), anchor="w").pack(fill="x", padx=24)
            for index, row in enumerate(data.get("top_collections", [])[:8], start=1):
                tk.Label(inner,
                         text=f"{index}. {row.get('title')} — установок {row.get('installs', 0)}, "
                              f"просмотров {row.get('views', 0)}, модов {row.get('items_count', 0)}",
                         bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", padx=24)

            section("Популярные категории")
            for row in data.get("categories", [])[:8]:
                tk.Label(inner, text=f"{row.get('category')} — {row.get('mods')} модов",
                         bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", padx=24)

            section("Активность за 14 дней")
            activity = data.get("activity") or []
            if not activity:
                tk.Label(inner, text="Пока нет событий.", bg=BG, fg=DIM, font=(FONT, 9),
                         anchor="w").pack(fill="x", padx=24)
            for row in activity[-14:]:
                tk.Label(inner, text=f"{row.get('day')} · {row.get('kind')}: {row.get('events')}",
                         bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x", padx=24)
            self._translate_ui(window)

        render()

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
                 bg=BG, fg=DIM, font=(FONT, 9), wraplength=760, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(toolbar, text="Сопоставить с каталогом", bg=PANEL, fg=TEXT, relief="flat",
                  font=(FONT, 9), padx=12, pady=5, cursor="hand2",
                  command=self.match_library).pack(side="right")
        if not items:
            tk.Label(frame, text="Пока пусто. Установите мод из каталога.", bg=BG, fg=MUTED,
                     font=(FONT, 11)).pack(anchor="w", pady=20)
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
                     font=(FONT_BOLD, 10), anchor="w").pack(fill="x")
            tk.Label(info, text=f"{row.get('filename')} · {row.get('size_text')} · "
                                f"{row.get('installed_text')}",
                     bg=row_bg, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x")
            state = tk.Label(line, text="включён" if row.get("active") else "выключен",
                             bg=row_bg, fg=(GREEN if row.get("active") else "#ff9a9a"),
                             font=(FONT, 8))
            state.pack(side="left", padx=8)
            if row.get("duplicate"):
                reason = row.get("duplicate_reason") or "файл"
                tk.Label(line, text=f"ДУБЛИКАТ ({reason})", bg=DUP_BG, fg="#ff8f8f",
                         font=(FONT_BOLD, 8)).pack(side="left", padx=4)
            if not row.get("matched"):
                tk.Label(line, text="не привязан", bg=row_bg, fg=DIM,
                         font=(FONT, 8)).pack(side="left", padx=4)
            if row.get("update_available"):
                tk.Label(line, text=f"обновление {row.get('catalog_version')}", bg=PANEL,
                         fg="#ffd6a3", font=(FONT, 8)).pack(side="left", padx=4)
            tk.Button(line, text="Выключить" if row.get("active") else "Включить",
                      bg=PANEL, fg=TEXT, relief="flat", font=(FONT, 9), cursor="hand2",
                      command=lambda r=row: self.toggle_mod(r)).pack(side="left", padx=4)
            tk.Button(line, text="Удалить", bg=PANEL, fg="#ff9a9a", relief="flat",
                      font=(FONT, 9), cursor="hand2",
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

    # ---------------------------------------------------------------- сборки
    COLL_CATEGORIES = (("drift", "Drift"), ("offroad", "Offroad"), ("jdm", "JDM"),
                       ("racing", "Racing"), ("realistic", "Realistic Driving"),
                       ("tuning", "Tuning"), ("stunt", "Stunt"), ("city", "City"),
                       ("mixed", "Mixed"), ("other", "Другое"))

    def _category_label(self, key: str) -> str:
        return dict(self.COLL_CATEGORIES).get(key, key or "other")

    def _restore_session(self) -> None:
        """Поднять сохранённую сессию: токен лежит в локальных настройках."""
        try:
            state = self.api.get("/api/state")
            token = (state.get("settings") or {}).get("user_token") or ""
        except Exception:  # noqa: BLE001
            token = ""
        if not token:
            self.root.after(0, self._apply_user)
            return
        self.api.user_token = token
        self.load_user()

    def load_user(self) -> None:
        try:
            data = self.api.get("/api/auth/me")
            self.user = data.get("user") if data.get("ok") else None
        except Exception:  # noqa: BLE001
            self.user = None
        self.root.after(0, self._apply_user)

    def _apply_user(self) -> None:
        nick = (self.user or {}).get("nickname") or "Войти"
        try:
            self.account_button.configure(text=nick)
        except Exception:  # noqa: BLE001
            pass
        try:
            base = getattr(self, "_mods_text", "")
            self.foot_label.configure(text=(f"{nick}\n" if self.user else "") + base)
        except Exception:  # noqa: BLE001
            pass
        if self.view in ("collections", "settings"):
            self.refresh_all()

    def open_auth(self) -> None:
        if self.user:
            self.logout()
            return
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Аккаунт BeamHub")
        window.geometry("420x330")
        window.transient(self.root)

        mode = {"value": "login"}

        tk.Label(window, text="BeamHub", bg=BG, fg=ACCENT, font=(FONT_BOLD, 16),
                 anchor="w").pack(fill="x", padx=20, pady=(16, 2))
        subtitle = tk.Label(window, text="Войдите, чтобы публиковать сборки и ставить оценки",
                            bg=BG, fg=DIM, font=(FONT, 9), anchor="w", wraplength=370,
                            justify="left")
        subtitle.pack(fill="x", padx=20, pady=(0, 10))

        fields = tk.Frame(window, bg=BG)
        fields.pack(fill="x", padx=20)

        def field(label: str, show: bool = False) -> tk.Entry:
            tk.Label(fields, text=label, bg=BG, fg=MUTED, font=(FONT, 9),
                     anchor="w").pack(fill="x", pady=(6, 0))
            entry = tk.Entry(fields, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                             font=(FONT, 10), show=("•" if show else ""))
            entry.pack(fill="x", ipady=5, pady=2)
            return entry

        email = field("Email")
        password = field("Пароль", show=True)
        nickname = field("Никнейм (для регистрации)")

        status = tk.Label(window, text="", bg=BG, fg="#ffb3b3", font=(FONT, 9), anchor="w",
                          wraplength=370, justify="left")
        status.pack(fill="x", padx=20, pady=(6, 0))

        def submit() -> None:
            payload = {"email": email.get().strip(), "password": password.get()}
            path = "/api/auth/login"
            if mode["value"] == "register":
                payload["nickname"] = nickname.get().strip()
                path = "/api/auth/register"
            result = self.api.post(path, payload)
            if not result.get("ok"):
                status.configure(text=result.get("error", "Не получилось"))
                return
            self.api.user_token = result.get("token", "")
            self.user = result.get("user")
            try:
                self.api.post("/api/settings", {"user_token": self.api.user_token})
            except Exception:  # noqa: BLE001
                pass
            window.destroy()
            self.toast(f"Вы вошли как {self.user.get('nickname')}", "ok")
            self._apply_user()
            self.refresh_all()

        def toggle_mode() -> None:
            if mode["value"] == "login":
                mode["value"] = "register"
                title_button.configure(text="У меня уже есть аккаунт")
                subtitle.configure(text="Регистрация: email, пароль и никнейм")
                submit_button.configure(text="Зарегистрироваться")
            else:
                mode["value"] = "login"
                title_button.configure(text="Создать аккаунт")
                subtitle.configure(text="Войдите, чтобы публиковать сборки и ставить оценки")
                submit_button.configure(text="Войти")

        actions = tk.Frame(window, bg=BG)
        actions.pack(fill="x", padx=20, pady=12)
        submit_button = theme.RoundButton(actions, "Войти", submit, variant="primary",
                                          height=34, radius=9, font=(FONT_BOLD, 10))
        submit_button.pack(side="left")
        title_button = theme.RoundButton(actions, "Создать аккаунт", toggle_mode, height=34,
                                         radius=9, font=(FONT, 9))
        title_button.pack(side="left", padx=8)
        tk.Label(window, text="Аккаунт хранится локально в BeamHub; вход через Google — на подходе.",
                 bg=BG, fg=DIM, font=(FONT, 8), anchor="w", wraplength=370, justify="left"
                 ).pack(fill="x", padx=20, pady=(0, 12))
        email.focus_set()
        self._translate_ui(window)

    def logout(self) -> None:
        try:
            self.api.post("/api/auth/logout", {})
        except Exception:  # noqa: BLE001
            pass
        self.api.user_token = ""
        self.user = None
        try:
            self.api.post("/api/settings", {"user_token": ""})
        except Exception:  # noqa: BLE001
            pass
        self.toast("Вы вышли из аккаунта")
        self._apply_user()
        self.refresh_all()

    def set_coll_kind(self, kind: str) -> None:
        self.coll_kind = kind
        self.render_collections()

    def render_collections(self) -> None:
        frame = self._scrollable_frame()
        self.count_label.configure(text="")
        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        for key, label in (("popular", "Популярные"), ("recommended", "Рекомендуемые"),
                           ("new", "Новинки"), ("mine", "Мои")):
            if key == "mine" and not self.user:
                continue
            theme.RoundButton(toolbar, label, lambda k=key: self.set_coll_kind(k), height=28,
                              radius=14, font=(FONT, 9),
                              variant=("primary" if self.coll_kind == key else "ghost")
                              ).pack(side="left", padx=(0, 6))
        if self.user:
            theme.RoundButton(toolbar, "＋ Создать сборку", self.create_collection,
                              variant="primary", height=30, radius=9,
                              font=(FONT_BOLD, 9)).pack(side="right")
        else:
            theme.RoundButton(toolbar, "Войти", self.open_auth, height=30, radius=9,
                              font=(FONT, 9)).pack(side="right")
        hint = ("Сборки — это подборки модов каталога: drift, offroad, JDM и другие. "
                "Смотреть можно без входа, создавать и оценивать — после входа в аккаунт.")
        tk.Label(frame, text=hint, bg=BG, fg=DIM, font=(FONT, 9), wraplength=780,
                 justify="left", anchor="w").pack(fill="x", pady=(0, 10))

        if self.coll_kind == "recommended":
            try:
                raw = self.api.get("/api/recommend/collections", limit=40)
            except Exception as exc:  # noqa: BLE001
                tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
                return
            items = [{
                "id": row.get("id"), "title": row.get("title"),
                "category": row.get("category"), "items_count": row.get("items_count", 0),
                "installs": row.get("installs", 0), "views": row.get("views", 0),
                "owner_nickname": row.get("owner_nickname"), "tags": [],
                "rating": {"avg": round(float(row.get("avg_rating") or 0), 2),
                           "count": row.get("rating_count", 0)},
            } for row in raw.get("items", [])]
            total = len(items)
        else:
            try:
                data = self.api.get("/api/collections", kind=self.coll_kind, limit=40)
            except Exception as exc:  # noqa: BLE001
                tk.Label(frame, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
                return
            items = data.get("items", [])
            total = data.get("total", len(items))
        self.count_label.configure(text=f"{total} сборок")
        if not items:
            text = ("У вас пока нет сборок — создайте первую." if self.coll_kind == "mine"
                    else "Пока никто не опубликовал сборок. Будьте первым!")
            tk.Label(frame, text=text, bg=BG, fg=MUTED, font=(FONT, 11)).pack(anchor="w", pady=20)
            return
        for coll in items:
            self._collection_row(frame, coll)

    def _collection_row(self, parent: tk.Frame, coll: dict) -> None:
        row = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE,
                       cursor="hand2")
        row.pack(fill="x", pady=4)
        cover = tk.Label(row, text=(coll.get("title") or "?")[:2].upper(), bg="#241a10",
                         fg=ACCENT, font=(FONT_BOLD, 16), width=6, height=3)
        cover.pack(side="left", padx=(10, 12), pady=8)
        info = tk.Frame(row, bg=PANEL)
        info.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(info, text=coll.get("title") or "Без названия", bg=PANEL, fg=TEXT,
                 font=(FONT_BOLD, 11), anchor="w").pack(fill="x")
        rating = coll.get("rating") or {}
        meta = (f"{coll.get('owner_nickname') or 'автор'} · "
                f"{self._category_label(coll.get('category'))} · "
                f"{coll.get('items_count', 0)} модов · "
                f"★ {rating.get('avg', 0):.1f} ({rating.get('count', 0)}) · "
                f"{coll.get('installs', 0)} установок")
        tk.Label(info, text=meta, bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x")
        tags = coll.get("tags") or []
        if tags:
            tk.Label(info, text=" · ".join("#" + t for t in tags[:6]), bg=PANEL, fg="#9fb4d8",
                     font=(FONT, 8), anchor="w").pack(fill="x")
        theme.RoundButton(row, "Открыть", lambda c=coll["id"]: self.collection_details(c),
                          height=30, radius=8, font=(FONT, 9)).pack(side="right", padx=12)
        for widget in (row, cover, info):
            widget.bind("<Button-1>", lambda _e, c=coll["id"]: self.collection_details(c))

    def create_collection(self) -> None:
        if not self.user:
            self.open_auth()
            return
        self.collection_edit(None)

    def collection_edit(self, coll_id: str | None, collection: dict | None = None) -> None:
        collection = collection or {}
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Новая сборка" if not coll_id else "Редактирование сборки")
        window.geometry("520x520")
        window.transient(self.root)

        def field(label: str, value: str = "") -> tk.Entry:
            tk.Label(window, text=label, bg=BG, fg=MUTED, font=(FONT, 9),
                     anchor="w").pack(fill="x", padx=20, pady=(8, 0))
            entry = tk.Entry(window, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                             font=(FONT, 10))
            entry.insert(0, value)
            entry.pack(fill="x", padx=20, ipady=5)
            return entry

        title = field("Название", collection.get("title") or "")
        tk.Label(window, text="Описание", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=20, pady=(8, 0))
        description = tk.Text(window, height=4, bg=PANEL, fg=TEXT, relief="flat",
                              insertbackground=TEXT, font=(FONT, 10), wrap="word")
        description.insert("1.0", collection.get("description") or "")
        description.pack(fill="x", padx=20)

        tk.Label(window, text="Категория", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=20, pady=(8, 0))
        labels = [label for _key, label in self.COLL_CATEGORIES]
        keys = [key for key, _label in self.COLL_CATEGORIES]
        current = collection.get("category") or "other"
        combo = ttk.Combobox(window, values=labels, state="readonly",
                             font=(FONT, 10))
        combo.set(self._category_label(current))
        combo.pack(fill="x", padx=20, ipady=3)

        tags = field("Теги через запятую", ", ".join(collection.get("tags") or []))
        status = tk.Label(window, text="", bg=BG, fg="#ffb3b3", font=(FONT, 9), anchor="w")
        status.pack(fill="x", padx=20, pady=(6, 0))

        def save() -> None:
            payload = {
                "title": title.get().strip(),
                "description": description.get("1.0", "end").strip(),
                "category": keys[labels.index(combo.get())] if combo.get() in labels else "other",
                "tags": tags.get(),
            }
            if coll_id:
                payload["id"] = coll_id
                result = self.api.post("/api/collections/update", payload)
            else:
                result = self.api.post("/api/collections/create", payload)
            if not result.get("ok"):
                status.configure(text=result.get("error", "Не получилось"))
                return
            window.destroy()
            self.toast("Сборка сохранена", "ok")
            self.refresh_all()

        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=20, pady=14)
        theme.RoundButton(buttons, "Сохранить", save, variant="primary", height=32, radius=9,
                          font=(FONT_BOLD, 9)).pack(side="left")
        theme.RoundButton(buttons, "Отмена", window.destroy, height=32, radius=9,
                          font=(FONT, 9)).pack(side="left", padx=8)
        title.focus_set()
        self._translate_ui(window)

    def collection_details(self, coll_id: str) -> None:
        try:
            data = self.api.get("/api/collection", id=coll_id)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Не удалось открыть сборку: {exc}", "error")
            return
        if not data.get("ok"):
            self.toast(data.get("error", "Сборка не найдена"), "error")
            return
        coll = data["collection"]
        try:
            plan = self.api.get("/api/collections/plan", id=coll_id)
        except Exception:  # noqa: BLE001
            plan = {"installed": [], "missing": [], "total": len(coll.get("items", []))}
        installed = set(plan.get("installed") or [])
        rating = coll.get("rating") or {}

        window = tk.Toplevel(self.root, bg=BG)
        window.title(coll.get("title") or "Сборка")
        window.geometry("780x680")
        window.transient(self.root)

        head = tk.Frame(window, bg=BG)
        head.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(head, text=coll.get("title"), bg=BG, fg=TEXT, font=(FONT_BOLD, 16),
                 anchor="w").pack(fill="x")
        owner = (coll.get("owner") or {}).get("nickname") or "автор"
        tk.Label(head, text=f"{owner} · {self._category_label(coll.get('category'))} · "
                            f"v{coll.get('version')} · ★ {rating.get('avg', 0):.1f} "
                            f"({rating.get('count', 0)}) · {coll.get('views', 0)} просмотров · "
                            f"{coll.get('installs', 0)} установок",
                 bg=BG, fg=DIM, font=(FONT, 9), anchor="w").pack(fill="x")
        if coll.get("description"):
            tk.Label(window, text=coll.get("description"), bg=BG, fg=MUTED, font=(FONT, 9),
                     wraplength=730, justify="left", anchor="w").pack(fill="x", padx=20,
                                                                     pady=(6, 0))
        tags = coll.get("tags") or []
        if tags:
            tk.Label(window, text=" ".join("#" + t for t in tags), bg=BG, fg="#9fb4d8",
                     font=(FONT, 9), anchor="w").pack(fill="x", padx=20, pady=(4, 0))

        actions = tk.Frame(window, bg=BG)
        actions.pack(fill="x", padx=20, pady=10)
        missing_count = len(plan.get("missing") or [])
        theme.RoundButton(
            actions,
            "Установить сборку" + (f" ({missing_count})" if missing_count else ""),
            lambda: self.apply_collection(coll_id, window), variant="primary",
            height=32, radius=9, font=(FONT_BOLD, 9)).pack(side="left")
        if not coll.get("is_owner"):
            theme.RoundButton(actions, "Пожаловаться",
                              lambda: self.report_dialog("collection", coll_id,
                                                         coll.get("title") or ""),
                              height=32, radius=9, font=(FONT, 9)).pack(side="left", padx=8)
        theme.RoundButton(actions, f"Отзывы ({coll.get('comments_count', 0)})",
                          lambda: self.collection_reviews(coll_id, coll.get("title") or ""),
                          height=32, radius=9, font=(FONT, 9)).pack(side="left", padx=8)
        if coll.get("is_owner"):
            theme.RoundButton(actions, "＋ Добавить мод",
                              lambda: self.pick_mod(coll_id, window), height=32, radius=9,
                              font=(FONT, 9)).pack(side="left", padx=8)
            theme.RoundButton(actions, "Редактировать",
                              lambda: self.collection_edit(coll_id, coll), height=32, radius=9,
                              font=(FONT, 9)).pack(side="left")
            theme.RoundButton(
                actions, "Опубликовать" if not coll.get("published") else "Обновить версию",
                lambda: self.publish_collection(coll_id, window), height=32, radius=9,
                font=(FONT, 9)).pack(side="left", padx=8)
            theme.RoundButton(actions, "Удалить",
                              lambda: self.delete_collection(coll_id, window), height=32,
                              radius=9, font=(FONT, 9)).pack(side="right")

        tk.Label(window, text=f"Состав: {len(coll.get('items', []))} модов · "
                              f"установлено {len(installed)}",
                 bg=BG, fg=TEXT, font=(FONT_BOLD, 10), anchor="w").pack(fill="x", padx=20,
                                                                       pady=(4, 4))
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True, padx=20)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        for index, item in enumerate(coll.get("items", []), start=1):
            line = tk.Frame(inner, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            line.pack(fill="x", pady=3)
            mark = "✓" if item["mod_id"] in installed else str(index)
            tk.Label(line, text=mark, bg=("#16241a" if item["mod_id"] in installed else PANEL),
                     fg=("#8fe0a4" if item["mod_id"] in installed else DIM),
                     font=(FONT_BOLD, 10), width=3).pack(side="left", padx=6, pady=6)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, pady=6)
            tk.Label(info, text=item.get("title") or item["mod_id"], bg=PANEL, fg=TEXT,
                     font=(FONT, 10), anchor="w").pack(fill="x")
            tk.Label(info, text=f"{item.get('category') or ''} · {item.get('size_text') or ''}",
                     bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x")
            if coll.get("is_owner"):
                theme.RoundButton(line, "Убрать",
                                  lambda m=item["mod_id"]: self.remove_from_collection(coll_id, m,
                                                                                       window),
                                  height=28, radius=8, font=(FONT, 8)).pack(side="right", padx=8)

        history = coll.get("versions") or []
        if history:
            tk.Label(window, text="История версий: " + ", ".join(
                f"v{v['version']} ({v.get('count', 0)} модов)" for v in history[:6]),
                bg=BG, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x", padx=20, pady=(6, 12))
        self._translate_ui(window)

    # ---------------------------------------------------------------- отзывы
    def collection_reviews(self, coll_id: str, title: str = "") -> None:
        window = tk.Toplevel(self.root, bg=BG)
        window.title(f"Отзывы — {title}" if title else "Отзывы")
        window.geometry("760x720")
        window.transient(self.root)
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True)

        stars_var = {"value": 0}

        def render() -> None:
            for child in body.winfo_children():
                child.destroy()
            try:
                data = self.api.get("/api/collection", id=coll_id)
                comments_data = self.api.get("/api/comments", target_type="collection",
                                             target_id=coll_id)
                reviews_data = self.api.get("/api/reviews", target_type="collection",
                                            target_id=coll_id)
            except Exception as exc:  # noqa: BLE001
                tk.Label(body, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
                return
            coll = data.get("collection") or {}
            summary = reviews_data.get("summary") or {}
            mine = coll.get("my_rating") or {}
            if not stars_var["value"]:
                stars_var["value"] = int(mine.get("stars") or 0)

            head = tk.Frame(body, bg=BG)
            head.pack(fill="x", padx=18, pady=(14, 4))
            tk.Label(head, text=coll.get("title") or title, bg=BG, fg=TEXT,
                     font=(FONT_BOLD, 14), anchor="w").pack(fill="x")
            tk.Label(head, text=f"★ {summary.get('avg', 0):.1f} · "
                                f"{summary.get('count', 0)} оценок · "
                                f"{summary.get('installed_reviews', 0)} от установивших · "
                                f"{comments_data.get('count', 0)} комментариев",
                     bg=BG, fg=DIM, font=(FONT, 9), anchor="w").pack(fill="x")

            if self.user:
                box = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
                box.pack(fill="x", padx=18, pady=8)
                tk.Label(box, text="Ваша оценка", bg=PANEL, fg=TEXT, font=(FONT_BOLD, 10),
                         anchor="w").pack(fill="x", padx=10, pady=(8, 2))
                star_row = tk.Frame(box, bg=PANEL)
                star_row.pack(anchor="w", padx=10)

                def set_stars(value: int) -> None:
                    stars_var["value"] = value
                    for index, button in enumerate(star_buttons, start=1):
                        button.configure(text="★" if index <= value else "☆")

                star_buttons = []
                for value in range(1, 6):
                    button = theme.RoundButton(
                        star_row, "★" if value <= stars_var["value"] else "☆",
                        lambda v=value: set_stars(v), width=34, height=30, radius=8,
                        font=(FONT, 13), bg=PANEL, fg="#ffd08a", hover=ACTIVE_BG)
                    button.pack(side="left", padx=2)
                    star_buttons.append(button)

                review_text = tk.Text(box, height=3, bg="#12141a", fg=TEXT, relief="flat",
                                      insertbackground=TEXT, font=(FONT, 9), wrap="word")
                review_text.insert("1.0", mine.get("review") or "")
                review_text.pack(fill="x", padx=10, pady=(6, 4))

                def save_rating() -> None:
                    stars = stars_var["value"]
                    if not stars:
                        self.toast("Поставьте оценку от 1 до 5", "error")
                        return
                    result = self.api.post("/api/ratings", {
                        "target_type": "collection", "target_id": coll_id,
                        "stars": stars,
                        "review": review_text.get("1.0", "end").strip(),
                    })
                    if not result.get("ok"):
                        self.toast(result.get("error", "Не удалось сохранить"), "error")
                        return
                    self.toast("Оценка сохранена", "ok")
                    render()

                def clear_rating() -> None:
                    self.api.post("/api/ratings/delete", {"target_type": "collection",
                                                          "target_id": coll_id})
                    stars_var["value"] = 0
                    render()

                actions = tk.Frame(box, bg=PANEL)
                actions.pack(fill="x", padx=10, pady=(0, 8))
                theme.RoundButton(actions, "Сохранить оценку", save_rating, variant="primary",
                                  height=30, radius=8, font=(FONT_BOLD, 9)).pack(side="left")
                theme.RoundButton(actions, "Удалить оценку", clear_rating, height=30, radius=8,
                                  font=(FONT, 9)).pack(side="left", padx=8)
            else:
                row = tk.Frame(body, bg=BG)
                row.pack(fill="x", padx=18, pady=8)
                tk.Label(row, text="Войдите, чтобы оценивать и комментировать", bg=BG, fg=DIM,
                         font=(FONT, 9)).pack(side="left")
                theme.RoundButton(row, "Войти", self.open_auth, variant="primary", height=30,
                                  radius=8, font=(FONT_BOLD, 9)).pack(side="left", padx=8)

            # отзывы с текстом
            reviews = [r for r in reviews_data.get("items", []) if r.get("review")]
            if reviews:
                tk.Label(body, text="Отзывы", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                         anchor="w").pack(fill="x", padx=18, pady=(8, 2))
                for review in reviews[:10]:
                    line = tk.Frame(body, bg=PANEL)
                    line.pack(fill="x", padx=18, pady=2)
                    head_line = tk.Frame(line, bg=PANEL)
                    head_line.pack(fill="x", padx=10, pady=(6, 0))
                    tk.Label(head_line, text="★" * review["stars"], bg=PANEL, fg="#ffd08a",
                             font=(FONT, 9)).pack(side="left")
                    tk.Label(head_line, text=review["nickname"], bg=PANEL, fg=TEXT,
                             font=(FONT_BOLD, 9)).pack(side="left", padx=6)
                    if review.get("installed"):
                        tk.Label(head_line, text="✓ установил", bg="#16241a", fg="#8fe0a4",
                                 font=(FONT, 8), padx=6).pack(side="left")
                    tk.Label(line, text=review["review"], bg=PANEL, fg=MUTED, font=(FONT, 9),
                             wraplength=680, justify="left", anchor="w").pack(
                        fill="x", padx=10, pady=(2, 8))

            tk.Label(body, text="Комментарии", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                     anchor="w").pack(fill="x", padx=18, pady=(10, 2))
            if not comments_data.get("items"):
                tk.Label(body, text="Пока никто не комментировал.", bg=BG, fg=DIM,
                         font=(FONT, 9), anchor="w").pack(fill="x", padx=18)

            def comment_block(parent: tk.Misc, comment: dict, depth: int = 0) -> None:
                wrap = tk.Frame(parent, bg=BG)
                wrap.pack(fill="x", padx=(18 + depth * 26, 18), pady=2)
                line = tk.Frame(wrap, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
                line.pack(fill="x")
                top = tk.Frame(line, bg=PANEL)
                top.pack(fill="x", padx=10, pady=(6, 0))
                author = comment.get("author") or {}
                tk.Label(top, text=author.get("nickname") or "?", bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, 9)).pack(side="left")
                tk.Label(top, text=" · " + human_ago(comment.get("created_at")), bg=PANEL,
                         fg=DIM, font=(FONT, 8)).pack(side="left")
                if comment.get("edited"):
                    tk.Label(top, text=" · изменён", bg=PANEL, fg=DIM, font=(FONT, 8)
                             ).pack(side="left")
                if comment.get("deleted"):
                    tk.Label(line, text="Комментарий удалён", bg=PANEL, fg=DIM, font=(FONT, 9),
                             anchor="w").pack(fill="x", padx=10, pady=(2, 8))
                    return
                tk.Label(line, text=comment.get("body"), bg=PANEL, fg=TEXT, font=(FONT, 9),
                         wraplength=640, justify="left", anchor="w").pack(
                    fill="x", padx=10, pady=(2, 4))
                tools = tk.Frame(line, bg=PANEL)
                tools.pack(fill="x", padx=10, pady=(0, 6))
                if self.user:
                    theme.RoundButton(
                        tools, "Ответить",
                        lambda c=comment: reply_box(c["id"], author.get("nickname") or ""),
                        height=26, radius=8, font=(FONT, 8)).pack(side="left", padx=(0, 6))
                if comment.get("is_mine"):
                    theme.RoundButton(tools, "Изменить",
                                      lambda c=comment: edit_comment(c["id"], c.get("body")),
                                      height=26, radius=8, font=(FONT, 8)
                                      ).pack(side="left", padx=(0, 6))
                if comment.get("is_mine") or comment.get("can_moderate"):
                    theme.RoundButton(tools, "Удалить",
                                      lambda c=comment: remove_comment(c["id"]),
                                      height=26, radius=8, font=(FONT, 8)).pack(side="left")
                for reply in comment.get("replies") or []:
                    comment_block(parent, reply, depth + 1)

            for comment in comments_data.get("items", []):
                comment_block(body, comment)

            if self.user:
                compose = tk.Frame(body, bg=BG)
                compose.pack(fill="x", padx=18, pady=(10, 16))
                entry = tk.Text(compose, height=2, bg=PANEL, fg=TEXT, relief="flat",
                                insertbackground=TEXT, font=(FONT, 9), wrap="word")
                entry.pack(fill="x", pady=(0, 4))

                def send_comment() -> None:
                    text = entry.get("1.0", "end").strip()
                    if not text:
                        return
                    result = self.api.post("/api/comments", {
                        "target_type": "collection", "target_id": coll_id, "body": text})
                    if not result.get("ok"):
                        self.toast(result.get("error", "Не удалось отправить"), "error")
                        return
                    render()

                theme.RoundButton(compose, "Отправить", send_comment, variant="primary",
                                  height=30, radius=8, font=(FONT_BOLD, 9)).pack(side="left")
                compose_entry = entry
            else:
                compose_entry = None

            def reply_box(parent_id: str, nickname: str) -> None:
                if compose_entry is None:
                    return
                compose_entry.focus_set()
                compose_entry.delete("1.0", "end")
                compose_entry.insert("1.0", f"@{nickname}, ")

            def edit_comment(comment_id: str, old: str) -> None:
                dialog = tk.Toplevel(window, bg=BG)
                dialog.title("Изменить комментарий")
                dialog.geometry("520x260")
                dialog.transient(window)
                text = tk.Text(dialog, height=6, bg=PANEL, fg=TEXT, relief="flat",
                               insertbackground=TEXT, font=(FONT, 9), wrap="word")
                text.insert("1.0", old or "")
                text.pack(fill="both", expand=True, padx=14, pady=12)

                def save() -> None:
                    result = self.api.post("/api/comments/update",
                                           {"id": comment_id, "body": text.get("1.0", "end").strip()})
                    if not result.get("ok"):
                        self.toast(result.get("error", "Не удалось изменить"), "error")
                        return
                    dialog.destroy()
                    render()

                theme.RoundButton(dialog, "Сохранить", save, variant="primary", height=30,
                                  radius=8, font=(FONT_BOLD, 9)).pack(padx=14, pady=(0, 12))

            def remove_comment(comment_id: str) -> None:
                result = self.api.post("/api/comments/delete", {"id": comment_id})
                if not result.get("ok"):
                    self.toast(result.get("error", "Не удалось удалить"), "error")
                    return
                render()

            self._translate_ui(window)

        render()

    # ---------------------------------------------------------------- модерация
    REPORT_REASONS = (("spam", "Спам"), ("abuse", "Оскорбления"), ("adult", "18+"),
                      ("offtopic", "Не по теме"), ("copyright", "Нарушение прав"),
                      ("other", "Другое"))

    def is_moderator(self) -> bool:
        return bool(self.user and self.user.get("role") in ("moderator", "admin"))

    def report_dialog(self, target_type: str, target_id: str, title: str = "") -> None:
        if not self.user:
            self.open_auth()
            return
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Жалоба")
        window.geometry("460x330")
        window.transient(self.root)
        tk.Label(window, text="На что жалуетесь?", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                 anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        if title:
            tk.Label(window, text=title[:120], bg=BG, fg=DIM, font=(FONT, 9), anchor="w",
                     wraplength=420, justify="left").pack(fill="x", padx=18)
        labels = [label for _key, label in self.REPORT_REASONS]
        keys = [key for key, _label in self.REPORT_REASONS]
        combo = ttk.Combobox(window, values=labels, state="readonly", font=(FONT, 9))
        combo.set(labels[0])
        combo.pack(fill="x", padx=18, pady=(8, 4))
        details = tk.Text(window, height=4, bg=PANEL, fg=TEXT, relief="flat",
                          insertbackground=TEXT, font=(FONT, 9), wrap="word")
        details.pack(fill="x", padx=18)
        status = tk.Label(window, text="", bg=BG, fg="#ffb3b3", font=(FONT, 9), anchor="w")
        status.pack(fill="x", padx=18, pady=(4, 0))

        def send() -> None:
            reason = keys[labels.index(combo.get())] if combo.get() in labels else "other"
            result = self.api.post("/api/moderation/report", {
                "target_type": target_type, "target_id": target_id, "reason": reason,
                "details": details.get("1.0", "end").strip()})
            if not result.get("ok"):
                status.configure(text=result.get("error", "Не удалось отправить"))
                return
            window.destroy()
            self.toast("Жалоба отправлена модераторам", "ok")

        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=18, pady=10)
        theme.RoundButton(buttons, "Отправить", send, variant="primary", height=30, radius=8,
                          font=(FONT_BOLD, 9)).pack(side="left")
        theme.RoundButton(buttons, "Отмена", window.destroy, height=30, radius=8,
                          font=(FONT, 9)).pack(side="left", padx=8)
        self._translate_ui(window)

    def moderation_panel(self) -> None:
        if not self.is_moderator():
            self.toast("Нужны права модератора", "error")
            return
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Модерация")
        window.geometry("840x720")
        window.transient(self.root)
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def resolve(report_id: str, action: str) -> None:
            result = self.api.post("/api/moderation/resolve", {"id": report_id, "action": action})
            if not result.get("ok"):
                self.toast(result.get("error", "Не получилось"), "error")
                return
            self.toast("Жалоба обработана", "ok")
            render()

        def block(user_id: str) -> None:
            result = self.api.post("/api/moderation/block",
                                   {"user_id": user_id, "blocked": True,
                                    "reason": "по жалобе"})
            if not result.get("ok"):
                self.toast(result.get("error", "Не получилось"), "error")
                return
            self.toast("Пользователь заблокирован", "ok")
            render()

        def render() -> None:
            for child in inner.winfo_children():
                child.destroy()
            try:
                overview = self.api.get("/api/moderation/overview")
                reports = self.api.get("/api/moderation/reports", status="open")
                log = self.api.get("/api/moderation/log")
            except Exception as exc:  # noqa: BLE001
                tk.Label(inner, text=f"Ошибка: {exc}", bg=BG, fg=MUTED).pack(anchor="w")
                return
            tk.Label(inner, text=f"Открытых жалоб: {overview.get('open_reports', 0)} · "
                                 f"заблокировано: {overview.get('blocked_users', 0)} · "
                                 f"модераторов: {overview.get('moderators', 0)} · "
                                 f"действий за сутки: {overview.get('actions_24h', 0)}",
                     bg=BG, fg=TEXT, font=(FONT_BOLD, 10), anchor="w").pack(
                fill="x", padx=18, pady=(14, 8))

            tk.Label(inner, text="Очередь жалоб", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                     anchor="w").pack(fill="x", padx=18)
            if not reports.get("items"):
                tk.Label(inner, text="Пусто — жалоб нет.", bg=BG, fg=DIM, font=(FONT, 9),
                         anchor="w").pack(fill="x", padx=18, pady=4)
            for report in reports.get("items", []):
                card = tk.Frame(inner, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
                card.pack(fill="x", padx=18, pady=3)
                top = tk.Frame(card, bg=PANEL)
                top.pack(fill="x", padx=10, pady=(6, 0))
                tk.Label(top, text=f"{report.get('target_type')} · {report.get('reason')}",
                         bg=PANEL, fg="#ffc79b", font=(FONT_BOLD, 9)).pack(side="left")
                tk.Label(top, text=f" · от {report.get('reporter_nickname') or '?'}", bg=PANEL,
                         fg=DIM, font=(FONT, 8)).pack(side="left")
                tk.Label(card, text=(report.get("preview") or "(без текста)")[:140], bg=PANEL,
                         fg=TEXT, font=(FONT, 9), wraplength=760, justify="left", anchor="w",
                         height=1).pack(fill="x", padx=10, pady=(2, 4))
                target_user = report.get("target_user") or {}
                tools = tk.Frame(card, bg=PANEL)
                tools.pack(fill="x", padx=10, pady=(0, 6))
                theme.RoundButton(
                    tools, "Удалить и закрыть",
                    lambda r=report: resolve(r["id"], "resolve_delete"),
                    variant="primary", height=26, radius=8, font=(FONT, 8)).pack(side="left")
                theme.RoundButton(tools, "Отклонить",
                                  lambda r=report: resolve(r["id"], "reject"),
                                  height=26, radius=8, font=(FONT, 8)).pack(side="left", padx=6)
                if target_user.get("id") and not target_user.get("blocked"):
                    theme.RoundButton(
                        tools, f"Блокировать {target_user.get('nickname')}",
                        lambda u=target_user["id"]: block(u),
                        height=26, radius=8, font=(FONT, 8)).pack(side="left")

            tk.Label(inner, text="Журнал действий", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                     anchor="w").pack(fill="x", padx=18, pady=(12, 2))
            for entry in log.get("items", [])[:25]:
                tk.Label(inner,
                         text=f"{entry.get('actor_nickname') or '?'} · {entry.get('action')} · "
                              f"{entry.get('target_type')} {entry.get('target_id')} · "
                              f"{human_ago(entry.get('created_at'))}",
                         bg=BG, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x", padx=18)
            self._translate_ui(window)

        render()

    # ---------------------------------------------------------------- облако
    def google_login(self, window: tk.Misc | None = None) -> None:
        try:
            status = self.api.get("/api/auth/cloud")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Ошибка: {exc}", "error")
            return
        if not status.get("configured"):
            self.toast("Сначала укажите адрес Supabase и anon key", "error")
            return
        result = self.api.post("/api/auth/google/start", {})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось начать вход"), "error")
            return
        state = result.get("state", "")
        self.toast("Открыл браузер — выберите аккаунт Google", "ok")
        if window is not None:
            window.destroy()

        def wait() -> None:
            for _ in range(120):
                try:
                    poll = self.api.get("/api/auth/google/poll", state=state)
                except Exception:  # noqa: BLE001
                    poll = {}
                if poll.get("status") == "done" and poll.get("token"):
                    self.api.user_token = poll["token"]
                    self.user = poll.get("user")
                    try:
                        self.api.post("/api/settings", {"user_token": poll["token"]})
                    except Exception:  # noqa: BLE001
                        pass
                    self.root.after(0, lambda: self.toast(
                        f"Вход выполнен: {(self.user or {}).get('nickname')}", "ok"))
                    self.root.after(0, self._apply_user)
                    self.root.after(0, self.refresh_all)
                    self.root.after(0, self.sync_pull)
                    return
                if poll.get("status") == "error":
                    message = poll.get("error", "вход не удался")
                    self.root.after(0, lambda m=message: self.toast(m, "error"))
                    return
                time.sleep(1)
        threading.Thread(target=wait, daemon=True).start()

    def sync_push(self) -> None:
        def work() -> None:
            try:
                result = self.api.post("/api/sync/push", {})
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda e=exc: self.toast(f"Ошибка: {e}", "error"))
                return
            if not result.get("ok"):
                message = result.get("error", "не вышло")
                self.root.after(0, lambda m=message: self.toast(m, "error"))
                return
            text = f"Отправлено сборок: {result.get('pushed')} из {result.get('total')}"
            if result.get("errors"):
                text += " · часть не ушла"
            self.root.after(0, lambda t=text: self.toast(t, "ok"))
            self.root.after(0, self.refresh_all)
        threading.Thread(target=work, daemon=True).start()

    def sync_pull(self) -> None:
        def work() -> None:
            try:
                result = self.api.post("/api/sync/pull", {})
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda e=exc: self.toast(f"Ошибка: {e}", "error"))
                return
            if not result.get("ok"):
                message = result.get("error", "не вышло")
                self.root.after(0, lambda m=message: self.toast(m, "error"))
                return
            pulled = result.get("pulled", 0)
            self.root.after(0, lambda n=pulled: self.toast(f"Получено сборок: {n}", "ok"))
            self.root.after(0, self.refresh_all)
        threading.Thread(target=work, daemon=True).start()

    def cloud_block(self, frame: tk.Frame, settings: dict) -> None:
        block = tk.Frame(frame, bg=BG)
        block.pack(fill="x", pady=8)
        tk.Label(block, text="Облако (Supabase) — вход через Google и общие сборки",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        row = tk.Frame(block, bg=BG)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(row, text="Project URL", bg=BG, fg=DIM, font=(FONT, 8)).pack(side="left")
        url_var = tk.StringVar(value=settings.get("cloud_url") or "")
        url_entry = tk.Entry(row, textvariable=url_var, bg=PANEL, fg=TEXT, relief="flat",
                             insertbackground=TEXT, font=(FONT, 9))
        url_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        row2 = tk.Frame(block, bg=BG)
        row2.pack(fill="x", pady=(4, 0))
        tk.Label(row2, text="anon key", bg=BG, fg=DIM, font=(FONT, 8)).pack(side="left")
        key_var = tk.StringVar(value=settings.get("cloud_anon_key") or "")
        key_entry = tk.Entry(row2, textvariable=key_var, bg=PANEL, fg=TEXT, relief="flat",
                             insertbackground=TEXT, font=(FONT, 9), show="•")
        key_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        buttons = tk.Frame(block, bg=BG)
        buttons.pack(fill="x", pady=(6, 0))

        def save_cloud() -> None:
            result = self.api.post("/api/cloud/settings", {
                "url": url_var.get().strip(), "anon_key": key_var.get().strip()})
            if not result.get("ok"):
                self.toast(result.get("error", "Не сохранилось"), "error")
                return
            self.toast("Настройки облака сохранены" if result.get("configured")
                       else "Сохранено, но облако не настроено", "ok")
            self.refresh_all()

        theme.RoundButton(buttons, "Сохранить облако", save_cloud, height=30, radius=8,
                          font=(FONT, 9)).pack(side="left")
        theme.RoundButton(buttons, "Войти через Google", self.google_login, variant="primary",
                          height=30, radius=8, font=(FONT_BOLD, 9)).pack(side="left", padx=8)
        theme.RoundButton(buttons, "Мои сборки в облако", self.sync_push, height=30, radius=8,
                          font=(FONT, 9)).pack(side="left", padx=(0, 8))
        theme.RoundButton(buttons, "Загрузить из облака", self.sync_pull, height=30, radius=8,
                          font=(FONT, 9)).pack(side="left")
        configured = bool((settings.get("cloud_url") or "").startswith("https://")
                          and settings.get("cloud_anon_key"))
        tk.Label(block,
                 text=("Подключено: сборки и профиль могут синхронизироваться" if configured
                       else "Не подключено — аккаунты и сборки пока локальные"),
                 bg=BG, fg=("#8fe0a4" if configured else DIM), font=(FONT, 8),
                 anchor="w", wraplength=760, justify="left").pack(fill="x", pady=(4, 0))
    def publish_collection(self, coll_id: str, window: tk.Misc | None = None) -> None:
        result = self.api.post("/api/collections/publish", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось опубликовать"), "error")
            return
        version = (result.get("collection") or {}).get("version")
        self.toast(f"Сборка опубликована, версия {version}", "ok")
        if window is not None:
            window.destroy()
        self.render_collections()

    def delete_collection(self, coll_id: str, window: tk.Misc | None = None) -> None:
        if not messagebox.askyesno("BeamHub", "Удалить сборку? Это действие необратимо."):
            return
        result = self.api.post("/api/collections/delete", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось удалить"), "error")
            return
        self.toast("Сборка удалена")
        if window is not None:
            window.destroy()
        self.render_collections()

    def remove_from_collection(self, coll_id: str, mod_id: str,
                               window: tk.Misc | None = None) -> None:
        result = self.api.post("/api/collections/items",
                               {"id": coll_id, "mod_id": mod_id, "action": "remove"})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось убрать мод"), "error")
            return
        if window is not None:
            window.destroy()
        self.collection_details(coll_id)

    def apply_collection(self, coll_id: str, window: tk.Misc | None = None) -> None:
        result = self.api.post("/api/collections/apply", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось начать установку"), "error")
            return
        self.toast(result.get("message", "Открыл мод"), "ok")
        if window is not None:
            window.destroy()
        self.collection_details(coll_id)

    def pick_mod(self, coll_id: str, window: tk.Misc | None = None) -> None:
        picker = tk.Toplevel(self.root, bg=BG)
        picker.title("Добавить мод в сборку")
        picker.geometry("560x520")
        picker.transient(self.root)
        search = tk.Entry(picker, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                          font=(FONT, 10))
        search.pack(fill="x", padx=16, pady=12, ipady=5)
        results = tk.Frame(picker, bg=BG)
        results.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        canvas = tk.Canvas(results, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(results, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def search_now(*_args) -> None:
            query = search.get().strip()
            try:
                data = self.api.get("/api/mods", q=query, limit=20)
            except Exception as exc:  # noqa: BLE001
                self.toast(f"Ошибка поиска: {exc}", "error")
                return
            for child in inner.winfo_children():
                child.destroy()
            for mod in data.get("items", []):
                line = tk.Frame(inner, bg=PANEL)
                line.pack(fill="x", pady=2)
                tk.Label(line, text=mod.get("title") or mod["id"], bg=PANEL, fg=TEXT,
                         font=(FONT, 9), anchor="w").pack(side="left", fill="x", expand=True,
                                                          padx=8, pady=6)
                theme.RoundButton(
                    line, "Добавить",
                    lambda m=mod: self._add_to_collection(coll_id, m, picker),
                    variant="primary", height=28, radius=8, font=(FONT_BOLD, 8)
                ).pack(side="right", padx=6)
            self._translate_ui(inner)

        search.bind("<Return>", search_now)
        theme.RoundButton(picker, "Найти", search_now, variant="primary", height=30, radius=8,
                          font=(FONT_BOLD, 9)).pack(padx=16, pady=(0, 10))
        search_now()

    def _add_to_collection(self, coll_id: str, mod: dict, window: tk.Misc | None = None) -> None:
        result = self.api.post("/api/collections/items", {"id": coll_id, "mod_id": mod["id"]})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось добавить"), "error")
            return
        self.toast(f"Добавлено: {mod.get('title')}", "ok")
        if window is not None:
            window.destroy()

    def add_to_collection_from_catalog(self, mod: dict) -> None:
        if not self.user:
            self.open_auth()
            return
        try:
            data = self.api.get("/api/collections", kind="mine", limit=40)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Ошибка: {exc}", "error")
            return
        mine = data.get("items", [])
        window = tk.Toplevel(self.root, bg=BG)
        window.title("В какую сборку добавить?")
        window.geometry("460x420")
        window.transient(self.root)
        tk.Label(window, text=mod.get("title") or "", bg=BG, fg=TEXT, font=(FONT_BOLD, 11),
                 anchor="w", wraplength=420, justify="left").pack(fill="x", padx=16, pady=(14, 8))
        for coll in mine:
            line = tk.Frame(window, bg=PANEL)
            line.pack(fill="x", padx=16, pady=2)
            tk.Label(line, text=coll.get("title"), bg=PANEL, fg=TEXT, font=(FONT, 9),
                     anchor="w").pack(side="left", fill="x", expand=True, padx=8, pady=6)
            theme.RoundButton(line, "Добавить",
                              lambda c=coll["id"]: self._add_to_collection(c, mod, window),
                              variant="primary", height=28, radius=8,
                              font=(FONT_BOLD, 8)).pack(side="right", padx=6)
        theme.RoundButton(window, "＋ Новая сборка", lambda: (window.destroy(),
                                                             self.create_collection()),
                          height=30, radius=9, font=(FONT, 9)).pack(padx=16, pady=12)
        self._translate_ui(window)

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
                 bg=BG, fg=DIM, font=(FONT, 9), wraplength=700, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        if valid:
            theme.RoundButton(toolbar, f"Установить все ({len(valid)})",
                              lambda: self.install_all(valid, total_size), variant="primary",
                              height=32, radius=8, font=(FONT_BOLD, 9)).pack(side="right")

        if not items:
            tk.Label(frame, text="Новых архивов нет.", bg=BG, fg=MUTED,
                     font=(FONT, 11)).pack(anchor="w", pady=20)
            return
        for row in items:
            line = tk.Frame(frame, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            line.pack(fill="x", pady=4)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            tk.Label(info, text=row.get("name"), bg=PANEL, fg=TEXT, font=(FONT, 10),
                     anchor="w").pack(fill="x")
            tk.Label(info, text=f"{row.get('size_text')} · "
                                f"{'мод' if row.get('valid') else 'не похоже на мод'}",
                     bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w").pack(fill="x")
            button = theme.RoundButton(line, "Установить", lambda r=row: self.install_file(r),
                                        variant="primary", height=30, radius=8,
                                        font=(FONT_BOLD, 9))
            if not row.get("valid"):
                button.configure(state="disabled")
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
        ]
        for key, label, value in rows:
            block = tk.Frame(frame, bg=BG)
            block.pack(fill="x", pady=8)
            tk.Label(block, text=label, bg=BG, fg=MUTED, font=(FONT, 9),
                     anchor="w").pack(fill="x")
            var = tk.StringVar(value=value or "")
            entry = tk.Entry(block, textvariable=var, bg=PANEL, fg=TEXT, relief="flat",
                             insertbackground=TEXT, font=(FONT, 10))
            entry.pack(fill="x", ipady=5, pady=3)
            self._setting_vars[key] = var

        auto_var = tk.BooleanVar(value=bool(settings.get("auto_install", True)))
        self._setting_vars["auto_install"] = auto_var
        tk.Checkbutton(frame, text="Ставить новые архивы из «Загрузок» автоматически",
                       variable=auto_var, bg=BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10)).pack(anchor="w", pady=6)

        move_var = tk.BooleanVar(value=bool(settings.get("move_from_downloads", False)))
        self._setting_vars["move_from_downloads"] = move_var
        tk.Checkbutton(frame, text="Переносить архив из «Загрузок» в папку модов "
                                   "(освобождает место на диске)",
                       variable=move_var, bg=BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10)).pack(anchor="w", pady=6)

        lang_block = tk.Frame(frame, bg=BG)
        lang_block.pack(fill="x", pady=8)
        tk.Label(lang_block, text="Язык", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x")
        lang_chips = tk.Frame(lang_block, bg=BG)
        lang_chips.pack(anchor="w", pady=4)
        for code, label in (("ru", "Русский"), ("en", "English")):
            theme.RoundButton(lang_chips, label, lambda c=code: self.set_language(c),
                              height=30, radius=8, font=(FONT, 9),
                              variant=("primary" if i18n.current() == code else "ghost")
                              ).pack(side="left", padx=(0, 8))
        acc_block = tk.Frame(frame, bg=BG)
        acc_block.pack(fill="x", pady=8)
        tk.Label(acc_block, text="Аккаунт", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x")
        acc_row = tk.Frame(acc_block, bg=BG)
        acc_row.pack(anchor="w", pady=4)
        if self.user:
            stats = (self.user or {}).get("stats") or {}
            tk.Label(acc_row,
                     text=f"{self.user.get('nickname')} · {self.user.get('role')} · "
                          f"сборок {stats.get('collections', 0)} · "
                          f"установок {stats.get('installs', 0)}",
                     bg=BG, fg=TEXT, font=(FONT, 9)).pack(side="left", padx=(0, 10))
            theme.RoundButton(acc_row, "Выйти", self.logout, height=30, radius=8,
                              font=(FONT, 9)).pack(side="left")
            if self.is_moderator():
                theme.RoundButton(acc_row, "Модерация", self.moderation_panel, variant="primary",
                                  height=30, radius=8,
                                  font=(FONT_BOLD, 9)).pack(side="left", padx=8)
        else:
            tk.Label(acc_row, text="Войдите, чтобы публиковать сборки и оценивать чужие",
                     bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left", padx=(0, 10))
            theme.RoundButton(acc_row, "Войти", self.open_auth, variant="primary", height=30,
                              radius=8, font=(FONT, 9)).pack(side="left")
        self.cloud_block(frame, settings)
        stats_row = tk.Frame(frame, bg=BG)
        stats_row.pack(fill="x", pady=4)
        theme.RoundButton(stats_row, "Статистика сообщества", self.stats_window, height=30,
                          radius=8, font=(FONT, 9)).pack(side="left")
        buttons = tk.Frame(frame, bg=BG)
        buttons.pack(fill="x", pady=10)
        theme.RoundButton(buttons, "Сохранить", self.save_settings, variant="primary",
                          height=34, radius=9, font=(FONT_BOLD, 10)).pack(side="left")
        tk.Button(buttons, text="Пересобрать библиотеку", bg=PANEL, fg=TEXT, relief="flat",
                  font=(FONT, 10), padx=12, pady=6, cursor="hand2",
                  command=self.rescan).pack(side="left", padx=8)
        tk.Button(buttons, text="Проверить обновления", bg=PANEL, fg=TEXT, relief="flat",
                  font=(FONT, 10), padx=12, pady=6, cursor="hand2",
                  command=lambda: self.check_updates(silent=False)).pack(side="left")
        tk.Button(buttons, text="Обратная связь", bg=PANEL, fg=TEXT, relief="flat",
                  font=(FONT, 10), padx=12, pady=6, cursor="hand2",
                  command=self.send_feedback).pack(side="left", padx=8)
        tk.Button(buttons, text="Экспорт списка", bg=PANEL, fg=TEXT, relief="flat",
                  font=(FONT, 10), padx=12, pady=6, cursor="hand2",
                  command=self.export_library).pack(side="left")

        info = (f"Версия {state.get('version')} · каталог: {state.get('counts', {}).get('mods')} модов · "
                f"установлено: {state.get('counts', {}).get('installed')}\n"
                f"Игра: {state.get('game_path') or 'не найдена'}\n"
                f"Данные: {state.get('data_dir', '')}")
        tk.Label(frame, text=info, bg=BG, fg=DIM, font=(FONT, 9), justify="left",
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
        """Диалог обратной связи: игрок пишет, приложение добавляет диагностику,
        дальше открывается GitHub Issue с готовым текстом."""
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Обратная связь")
        window.geometry("720x600")
        window.transient(self.root)

        tk.Label(window, text="Что случилось?", bg=BG, fg=TEXT,
                 font=(FONT_BOLD, 11), anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        note = tk.Text(window, height=3, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                       font=(FONT, 10), wrap="word")
        note.pack(fill="x", padx=18)

        options = tk.Frame(window, bg=BG)
        options.pack(fill="x", padx=18, pady=8)
        paths_var = tk.BooleanVar(value=False)
        log_var = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="добавить путь к игре", variable=paths_var, bg=BG, fg=MUTED,
                       selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 9), command=lambda: refresh()).pack(side="left")
        tk.Checkbutton(options, text="добавить хвост лога", variable=log_var, bg=BG, fg=MUTED,
                       selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 9), command=lambda: refresh()).pack(side="left", padx=12)

        tk.Label(window, text="Так это уйдёт в issue на GitHub (можно поправить):", bg=BG, fg=DIM,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=18)
        preview = tk.Text(window, height=16, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                          font=("Consolas", 9), wrap="word")
        preview.pack(fill="both", expand=True, padx=18, pady=(2, 8))

        status = tk.Label(window, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        status.pack(fill="x", padx=18)

        def refresh() -> None:
            def work() -> None:
                try:
                    query = {"paths": "1" if paths_var.get() else "0",
                             "log": "1" if log_var.get() else "0",
                             "note": note.get("1.0", "end").strip()}
                    data = self.api.get("/api/feedback", **query)

                    def apply() -> None:
                        preview.delete("1.0", "end")
                        preview.insert("1.0", data.get("report", ""))
                        status.configure(text="Нужен аккаунт GitHub — issue откроется с готовым текстом")
                    self.root.after(0, apply)
                except Exception as exc:  # noqa: BLE001
                    self.root.after(0, lambda: status.configure(text=f"Ошибка: {exc}"))
            threading.Thread(target=work, daemon=True).start()

        def open_issue() -> None:
            try:
                result = self.api.post("/api/feedback/open", {
                    "report": preview.get("1.0", "end").strip(),
                    "title": (note.get("1.0", "end").strip().splitlines() or [""])[0][:80],
                })
                status.configure(text="Открыл GitHub — проверь текст и нажми «Submit new issue»"
                                 if result.get("ok") else "Не удалось открыть браузер")
            except Exception as exc:  # noqa: BLE001
                status.configure(text=f"Ошибка: {exc}")

        def copy_text() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(preview.get("1.0", "end").strip())
            status.configure(text="Текст скопирован — можно вставить в любое место")

        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=18, pady=(4, 16))
        theme.RoundButton(buttons, "Открыть GitHub", open_issue, variant="primary",
                          height=34, radius=9, font=(FONT_BOLD, 10)).pack(side="left")
        theme.RoundButton(buttons, "Скопировать текст", copy_text, height=34, radius=9,
                          bg=PANEL, fg=TEXT, hover=ACTIVE_BG, font=(FONT, 10)
                          ).pack(side="left", padx=8)

        refresh()
        note.focus_set()

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
                     font=(FONT, 11)).pack(anchor="w", pady=20)
            return
        for row in rows:
            line = tk.Frame(frame, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            line.pack(fill="x", pady=4)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            tk.Label(info, text=row.get("title") or row.get("filename"), bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, 10), anchor="w").pack(fill="x")
            if row.get("update_available"):
                note = f"установлено {row.get('version')} → в каталоге {row.get('catalog_version')}"
                color = "#ffd6a3"
            else:
                note = f"версия файла неизвестна, в каталоге {row.get('catalog_version')}"
                color = MUTED
            tk.Label(info, text=note, bg=PANEL, fg=color, font=(FONT, 8),
                     anchor="w").pack(fill="x")
            theme.RoundButton(line, "Скачать новую",
                              lambda r=row: self.install({"id": r["mod_id"],
                                                          "title": r.get("title")}),
                              variant="primary", height=30, radius=8,
                              font=(FONT_BOLD, 9)).pack(side="left", padx=10)

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
        self.status.configure(text=i18n.tr(text))

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
        self._mods_text = f"Папка модов:\n{state.get('settings', {}).get('mods_dir', '')}"
        self.foot_label.configure(text=self._mods_text)
        counts = state.get("counts", {})
        self.set_badge("catalog", counts.get("mods", 0))
        self.set_badge("library", counts.get("installed", 0))
        self.set_badge("updates", state.get("updates", 0), accent=bool(state.get("updates")))
        self.set_badge("inbox", state.get("inbox_count", 0))

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

        lang = (state.get("settings") or {}).get("language")
        if lang and lang != i18n.current() and not getattr(self, "_lang_from_settings", False):
            self._lang_from_settings = True
            i18n.set_lang(lang)
        if state.get("game_running"):
            self.status.configure(
                text="BeamNG.drive запущена — моды подхватятся после перезапуска игры",
                fg="#ffd6a3")
        cats = [(c["key"], c["label"], c["count"]) for c in state.get("categories", [])
                if c.get("count")]
        total = state.get("counts", {}).get("mods", 0)
        signature = (self.category, total, tuple(cats))
        if getattr(self, "_cat_signature", None) != signature:
            self._cat_signature = signature
            self._build_categories(total, cats)
        else:
            for key in self.cat_parts:
                self._paint_cat(key)

    CAT_ICONS = {"all": "all", "cars": "car", "maps": "map", "tools": "tool",
                 "planes": "plane", "bikes": "bike", "boats": "boat", "other": "box"}

    def _draw_cat_icon(self, canvas: tk.Canvas, kind: str, color: str) -> None:
        canvas.delete("all")
        if kind == "all":
            for x, y in ((3, 10), (9, 10), (3, 16), (9, 16)):
                canvas.create_rectangle(x, y, x + 4, y + 4, fill=color, width=0)
        elif kind == "car":
            canvas.create_polygon(3, 18, 5, 12, 14, 12, 17, 16, 17, 18,
                                  fill=color, width=0, smooth=False)
            canvas.create_oval(5, 16, 10, 21, fill=color, width=0)
            canvas.create_oval(12, 16, 17, 21, fill=color, width=0)
        elif kind == "map":
            canvas.create_polygon(3, 11, 7, 13, 11, 11, 15, 13, 15, 19, 11, 17,
                                  7, 19, 3, 17, fill="", outline=color, width=1)
            canvas.create_line(7, 13, 7, 19, fill=color, width=1)
            canvas.create_line(11, 11, 11, 17, fill=color, width=1)
        elif kind == "tool":
            canvas.create_line(4, 20, 13, 11, fill=color, width=3)
            canvas.create_oval(12, 8, 17, 13, outline=color, width=2)
        elif kind == "plane":
            canvas.create_polygon(3, 16, 16, 9, 13, 15, 17, 18, 13, 20, fill=color, width=0)
        elif kind == "bike":
            canvas.create_oval(4, 15, 9, 20, outline=color, width=1)
            canvas.create_oval(12, 15, 17, 20, outline=color, width=1)
            canvas.create_line(6, 17, 10, 13, 14, 17, fill=color, width=1)
            canvas.create_line(10, 13, 10, 18, fill=color, width=1)
        elif kind == "boat":
            canvas.create_polygon(3, 16, 17, 16, 14, 20, 6, 20, fill=color, width=0)
            canvas.create_line(10, 9, 10, 16, fill=color, width=1)
            canvas.create_polygon(10, 9, 10, 15, 15, 15, fill=color, width=0)
        elif kind == "box":
            canvas.create_rectangle(4, 11, 16, 20, outline=color, width=1)
            canvas.create_line(4, 13, 16, 13, fill=color, width=1)

    def _build_categories(self, total: int, cats: list[tuple[str, str, int]]) -> None:
        for child in self.cat_frame.winfo_children():
            child.destroy()
        self.cat_parts: dict[str, dict] = {}
        rows = [("all", "Все моды", total)] + list(cats)
        for key, label, count in rows:
            icon = self.CAT_ICONS.get(key, "box")
            row = tk.Frame(self.cat_frame, bg=SOFT, cursor="hand2", height=30)
            row.pack(fill="x", pady=1, padx=2)
            row.pack_propagate(False)
            bar = tk.Canvas(row, width=3, bg=SOFT, highlightthickness=0, bd=0)
            bar.pack(side="left", fill="y")
            icon_canvas = tk.Canvas(row, width=20, height=30, bg=SOFT, highlightthickness=0, bd=0)
            icon_canvas.pack(side="left", padx=(8, 6))
            text = tk.Label(row, text=label, bg=SOFT, fg=MUTED, font=(FONT, 9), anchor="w")
            text.pack(side="left", fill="x", expand=True)
            badge = tk.Label(row, text=str(count), bg=SOFT, fg=DIM, font=(FONT, 8), padx=4)
            badge.pack(side="right", padx=(2, 6))
            self._draw_cat_icon(icon_canvas, icon, MUTED)
            self.cat_parts[key] = {"row": row, "bar": bar, "icon": icon_canvas, "kind": icon,
                                   "label": text, "badge": badge}
            for widget in (row, bar, icon_canvas, text, badge):
                widget.bind("<Button-1>", lambda _e, k=key: self.pick_category(k))
                widget.bind("<Enter>", lambda _e, k=key: self._paint_cat(k, hover=True))
                widget.bind("<Leave>", lambda _e, k=key: self._paint_cat(k))
            self._paint_cat(key)
        self._translate_ui(self.root)

    def _paint_cat(self, key: str, hover: bool = False) -> None:
        parts = self.cat_parts.get(key)
        if not parts:
            return
        active = key == self.category
        bg = ACTIVE_BG if active else (HOVER_BG if hover else SOFT)
        for name in ("row", "bar", "icon", "label"):
            parts[name].configure(bg=bg)
        parts["badge"].configure(bg=bg,
                                 fg=(ACCENT if active else DIM),
                                 font=(FONT_BOLD, 8) if active else (FONT, 8))
        parts["label"].configure(fg=(TEXT if active else (TEXT if hover else MUTED)),
                                 font=(FONT_BOLD if active else FONT, 9))
        self._draw_cat_icon(parts["icon"], parts["kind"],
                            ACCENT if active else (TEXT if hover else MUTED))
        parts["bar"].delete("all")
        if active:
            parts["bar"].create_rectangle(0, 6, 3, 24, fill=ACCENT, width=0)

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
                        kind = event.get("kind")
                        text = event.get("text", "")
                        if kind in ("installed", "error", "duplicate", "inbox_done"):
                            tone = "error" if kind == "error" else ("ok" if kind == "installed"
                                                                    else "info")
                            self.root.after(0, lambda t=text, k=tone: self.toast(t, k))
                        if kind == "inbox_done":
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
