"""Каталог модов в родном окне (tkinter) — без браузера и без WebView.

Интерфейс повторяет веб-версию: сайдбар с категориями, сетка карточек с превью,
поиск, сортировка, библиотека, загрузки и настройки. Данные берутся у локального
API (тот же процесс), картинки — через встроенный прокси с кешем.
"""
from __future__ import annotations

import json
import shutil
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import cloud, cloud_defaults, config, display, i18n, theme
from .version import VERSION
from .util import (CATEGORY_LABELS, CATEGORY_ORDER, human_ago, human_date, human_size,
                    looks_installed, title_key, title_tokens)

BG = "#08090c"
SOFT = "#0d0f14"
PANEL = "#12141b"
LINE = "#232734"
LINE_SOFT = "#1a1e27"
SURFACE_2 = "#171a22"
SURFACE_3 = "#20242f"
BORDER_STRONG = "#4b5670"
TEXT = "#f0f2f7"
MUTED = "#9aa3b4"
DIM = "#7a8291"
ACCENT = "#ff7a1a"
ACCENT_SOFT = "#33200f"
ACCENT_SOFT_TEXT = "#ffc08a"
GREEN = "#34c87b"
GREEN_SOFT = "#16241a"
GREEN_TEXT = "#9fe3b4"
WARN_SOFT = "#2a2210"
WARN_TEXT = "#ffd6a3"
DANGER_TEXT = "#ff9a9a"
DANGER_SOFT = "#2a1518"
HOVER_BG = "#171a22"
ACTIVE_BG = "#20242f"
DUP_BG = "#241317"
DUP_LINE = "#8a4a1c"
SOURCE_NAMES = {"playground": "playground.ru", "modsgaming": "modsgaming.ru",
                "beamngland": "beamngland.com", "beamngmods": "beamngmods.net",
                "link": "по ссылке"}

FONT = "Segoe UI"          # в App.__init__ подменится на Segoe UI Variable (Windows 11)
FONT_BOLD = "Segoe UI Semibold"

# Единая сетка экранов 2026: поля окна 16, секции 18, строки 32–64.
PAGE_PAD = display.px(16)
SECTION_GAP = display.px(16)
ROW_GAP = display.px(4)
TITLE_PX = 22
SECTION_PX = 13
LEAD_PX = 11
BODY_PX = 10
CAPTION_PX = 9

# Карточка 2026: картинка строго 16:9, одинаковая высота у всех карточек,
# скругление только сверху — низ упирается в текстовый блок.
IMG_W, IMG_H = display.px(288), display.px(162)
IMG_RADIUS = display.px(14)
CARD_PAD = display.px(8)
CARD_W = IMG_W
CARD_GAP = display.px(12)
SIDEBAR_W = display.px(264)
NAV_H = display.px(40)

try:
    from PIL import Image, ImageOps, ImageTk

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
        self.page_no = 1
        self.items: list[dict] = []
        self._images: dict[str, object] = {}
        self._placeholders: dict[str, object] = {}
        self._img_queue: list[tuple[str, str]] = []
        self._img_busy = False
        self._search_job = None
        self._event_seq = 0
        self._cards_frame = None
        self.user: dict | None = None
        self.coll_kind = "popular"

        root.title(f"BeamHub {VERSION} — каталог модов BeamNG.drive")
        root.tk.call("tk", "scaling", display.ui_scale() * 96.0 / 72.0)
        root.geometry(f"{display.px(1400)}x{display.px(920)}")
        root.minsize(display.px(1180), display.px(720))
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
        style.configure("TCombobox", fieldbackground=SURFACE_2, background=SURFACE_2,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE,
                        lightcolor=SURFACE_2, darkcolor=SURFACE_2,
                        padding=(11, 9), arrowsize=14, font=(FONT, BODY_PX))
        style.map("TCombobox",
                  fieldbackground=[("readonly", SURFACE_2), ("active", SURFACE_3)],
                  background=[("active", SURFACE_3)],
                  foreground=[("readonly", TEXT)],
                  arrowcolor=[("active", TEXT)],
                  selectbackground=[("readonly", SURFACE_2)],
                  bordercolor=[("focus", BORDER_STRONG), ("active", BORDER_STRONG)])
        style.configure("Vertical.TScrollbar", background=SURFACE_3, troughcolor=BG,
                        bordercolor=BG, arrowcolor=BG, arrowsize=0, width=12,
                        gripcount=0, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", BORDER_STRONG)])

    # ---------------------------------------------------------------- каркас
    def _build(self) -> None:
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        body = tk.Frame(self.root, bg=BG)
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.sidebar = tk.Frame(body, bg=SOFT, width=SIDEBAR_W)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.pack_propagate(False)
        self._build_sidebar()

        self.main = tk.Frame(body, bg=BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(1, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        self.head = tk.Frame(self.main, bg=BG)
        self.head.grid(row=0, column=0, sticky="ew", padx=PAGE_PAD, pady=(PAGE_PAD, 6))
        self._build_header()

        self.content = tk.Frame(self.main, bg=BG)
        self.content.grid(row=1, column=0, sticky="nsew", padx=PAGE_PAD, pady=(8, 0))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.content, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.content, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.main_scroll = scroll
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self._columns = 0
        self.canvas.bind("<Configure>", self._on_resize, add="+")
        self.page = tk.Frame(self.content, bg=BG)
        self._page_active = False
        self._page_canvas = None

        tk.Frame(self.root, bg=LINE, height=1).grid(row=1, column=0, sticky="ew")
        status_bar = tk.Frame(self.root, bg=SOFT)
        status_bar.grid(row=2, column=0, sticky="ew")
        self.status = tk.Label(status_bar, text="Готово", bg=SOFT, fg=MUTED, anchor="w",
                               font=(FONT, CAPTION_PX), padx=PAGE_PAD, pady=6)
        self.status.pack(side="left", fill="x", expand=True)
        tk.Label(status_bar, text=f"BeamHub {VERSION}", bg=SOFT, fg=DIM,
                 font=(FONT, CAPTION_PX), padx=PAGE_PAD).pack(side="right")
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass

    def _build_header(self) -> None:
        """Шапка: крупный титул с подписью, поиск 40 px, чипы и аккаунт-аватар."""
        top = tk.Frame(self.head, bg=BG)
        top.pack(fill="x")
        self.head_top = top

        self.page_back = theme.RoundButton(
            top, "← Назад", self._hide_page, height=32, width=92, radius=10,
            font=(FONT_BOLD, BODY_PX, "bold"), bg=PANEL, fg=MUTED,
            hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG)

        titles = tk.Frame(top, bg=BG)
        titles.pack(side="left", anchor="w")
        self._titles_frame = titles
        self.title_label = tk.Label(titles, text="Каталог модов", bg=BG, fg=TEXT,
                                    font=(FONT_BOLD, TITLE_PX, "bold"), anchor="w")
        self.title_label.pack(anchor="w")
        self.count_label = tk.Label(titles, text="", bg=BG, fg=MUTED,
                                    font=(FONT, BODY_PX), anchor="w")
        self.count_label.pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(top, bg=BG)
        actions.pack(side="right", anchor="e")
        self.account_button = theme.AvatarButton(actions, "Войти", self.account_menu,
                                                 height=40, font=(FONT, BODY_PX))
        self.account_button.pack(side="right")
        self.lang_button = theme.RoundButton(actions, self._lang_label(), self.toggle_language,
                                             height=36, width=48, radius=12,
                                             font=(FONT_BOLD, BODY_PX, "bold"), bg=PANEL, fg=MUTED,
                                             hover=SURFACE_3, border=LINE,
                                             border_hover=BORDER_STRONG)
        self.lang_button.pack(side="right", padx=(0, 8))
        theme.RoundButton(actions, "＋ Ссылка", self.on_add_link, height=36, font=(FONT, BODY_PX),
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG).pack(side="right", padx=(0, 8))
        self.fav_button = theme.RoundButton(actions, "★ Избранное", self.toggle_favorites,
                                            height=36, font=(FONT, BODY_PX), bg=PANEL, fg=TEXT,
                                            hover=SURFACE_3, border=LINE,
                                            border_hover=BORDER_STRONG)
        self.fav_button.pack(side="right", padx=(0, 8))

        tools = tk.Frame(self.head, bg=BG)
        tools.pack(fill="x", pady=(14, 0))
        self.tools_row = tools

        search_shell = theme.RoundedFrame(tools, radius=12, fill=SURFACE_2, border=LINE,
                                          padding=1, width=380, height=40)
        search_shell.pack(side="left")
        self.search_shell = search_shell
        area = search_shell.body
        glass = tk.Canvas(area, width=18, height=18, bg=SURFACE_2,
                          highlightthickness=0, bd=0)
        glass.place(x=13, rely=0.5, anchor="w")
        self._draw_search_icon(glass, MUTED)
        self.search_var = tk.StringVar()
        entry = tk.Entry(area, textvariable=self.search_var, bg=SURFACE_2, fg=TEXT,
                         insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0,
                         font=(FONT, LEAD_PX))
        entry.pack(fill="x", padx=(40, 12), ipady=8)
        placeholder = tk.Label(area, text="Поиск: bmw, карта, vaz, дрифт…", bg=SURFACE_2,
                               fg=DIM, font=(FONT, LEAD_PX))
        placeholder.place(x=40, rely=0.5, anchor="w")
        placeholder.bind("<Button-1>", lambda _e: entry.focus_set())

        def search_focus(_event=None) -> None:
            placeholder.place_forget()
            search_shell.set_border(BORDER_STRONG)

        def search_blur(_event=None) -> None:
            if not self.search_var.get():
                placeholder.place(x=40, rely=0.5, anchor="w")
            search_shell.set_border(LINE)

        entry.bind("<FocusIn>", search_focus)
        entry.bind("<FocusOut>", search_blur)
        entry.bind("<Return>", lambda _e: self.on_search())
        self.search_var.trace_add("write", lambda *_: self._search_debounced())

        self.sort_var = tk.StringVar(value="Сначала новые")
        sorts = ["Сначала новые", "Сначала старые", "По названию А→Я", "По названию Я→А",
                 "По размеру", "По популярности"]
        combo = ttk.Combobox(tools, textvariable=self.sort_var, values=sorts, width=18,
                             state="readonly")
        combo.pack(side="left", padx=(10, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.on_sort())

        self.source_var = tk.StringVar(value="Все источники")
        self._source_map = {"Все источники": "all"}
        self.source_combo = ttk.Combobox(tools, textvariable=self.source_var,
                                         values=["Все источники"], width=17, state="readonly")
        self.source_combo.pack(side="left", padx=(8, 0))
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_source())

        theme.RoundButton(tools, "Обновить каталог", self.on_sync, height=36, font=(FONT, BODY_PX),
                          bg=PANEL, fg=MUTED, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG).pack(side="right")

        row = tk.Frame(self.head, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self.chips_row = row
        tk.Label(row, text="ФИЛЬТРЫ", bg=BG, fg=DIM,
                 font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left", padx=(2, 10))
        filters = tk.Frame(row, bg=BG)
        filters.pack(side="left")
        self.filter_game = tk.BooleanVar(value=False)
        self.filter_image = tk.BooleanVar(value=False)
        self.filter_fav = tk.BooleanVar(value=False)
        self.filter_verified = tk.BooleanVar(value=False)
        self.filter_fresh = tk.BooleanVar(value=False)
        self.filter_chips: dict[str, object] = {}
        for text, var, key in (("0.40.x", self.filter_game, "game"),
                               ("с обложкой", self.filter_image, "image"),
                               ("проверенные", self.filter_verified, "verified"),
                               ("свежие", self.filter_fresh, "fresh"),
                               ):
            chip = theme.RoundButton(filters, text, lambda v=var, k=key: self._toggle_chip(v, k),
                                     height=28, radius=14, bg=SURFACE_2, fg=MUTED,
                                     hover=SURFACE_3, font=(FONT, CAPTION_PX), border=LINE,
                                     border_hover=BORDER_STRONG)
            chip.pack(side="left", padx=(0, 6))
            self.filter_chips[key] = chip

    NAV_ITEMS = (("catalog", "Каталог", "grid"),
                 ("collections", "Сборки", "layers"),
                 ("library", "Библиотека", "book"),
                 ("updates", "Обновления", "arrow"),
                 ("inbox", "Загрузки", "tray"),
                 ("settings", "Настройки", "gear"))

    def _build_sidebar(self) -> None:
        brand = tk.Frame(self.sidebar, bg=SOFT)
        brand.pack(fill="x", padx=PAGE_PAD, pady=(PAGE_PAD, 16))
        logo = tk.Canvas(brand, width=34, height=34, bg=SOFT, highlightthickness=0, bd=0)
        logo.pack(side="left")
        theme.rounded_rect(logo, 1, 1, 33, 33, 10, fill=ACCENT)
        logo.create_text(17, 17, text="B", fill="#170d04", font=(FONT_BOLD, 13, "bold"))
        titles = tk.Frame(brand, bg=SOFT)
        titles.pack(side="left", padx=11)
        tk.Label(titles, text="BeamHub", bg=SOFT, fg=TEXT, font=(FONT_BOLD, 13, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(titles, text="каталог модов BeamNG.drive", bg=SOFT, fg=DIM,
                 font=(FONT, CAPTION_PX), anchor="w").pack(anchor="w", pady=(1, 0))

        self.nav_buttons: dict[str, tk.Canvas] = {}
        self.nav_parts: dict[str, dict] = {}
        self._nav_titles = {key: title for key, title, _icon in self.NAV_ITEMS}
        for key, _title, icon in self.NAV_ITEMS:
            row = tk.Canvas(self.sidebar, height=NAV_H, bg=SOFT, highlightthickness=0,
                            bd=0, cursor="hand2")
            row.pack(fill="x", padx=12, pady=1)
            row.bind("<Button-1>", lambda _e, k=key: self.switch(k))
            row.bind("<Enter>", lambda _e, k=key: self._nav_hover(k, True))
            row.bind("<Leave>", lambda _e, k=key: self._nav_hover(k, False))
            row.bind("<Configure>", lambda _e, k=key: self._paint_nav(k))
            self.nav_parts[key] = {"canvas": row, "kind": icon, "badge": "",
                                   "accent": False}
            self.nav_buttons[key] = row

        tk.Frame(self.sidebar, bg=LINE, height=1).pack(fill="x", padx=PAGE_PAD, pady=(16, 10))
        tk.Label(self.sidebar, text="КАТЕГОРИИ", bg=SOFT, fg=DIM, font=(FONT_BOLD, BODY_PX, "bold"),
                 anchor="w").pack(fill="x", padx=22, pady=(2, 6))
        self.cat_frame = tk.Frame(self.sidebar, bg=SOFT)
        self.cat_frame.pack(fill="both", expand=True, padx=10)

        theme.RoundButton(self.sidebar, "Остановить BeamHub", self.on_quit, height=32,
                          font=(FONT, CAPTION_PX), bg=SOFT, fg=DANGER_TEXT,
                          hover=DANGER_SOFT, border=LINE, border_hover=DUP_LINE
                          ).pack(side="bottom", fill="x", padx=12, pady=(0, 12))

        foot = tk.Frame(self.sidebar, bg=SOFT)
        foot.pack(side="bottom", fill="x", padx=PAGE_PAD, pady=(0, 10))
        tk.Label(foot, text="Папка модов", bg=SOFT, fg=DIM, font=(FONT, CAPTION_PX),
                 anchor="w").pack(anchor="w")
        self.foot_label = tk.Label(foot, text="", bg=SOFT, fg=MUTED, font=(FONT, CAPTION_PX),
                                   justify="left", wraplength=224, anchor="w")
        self.foot_label.pack(fill="x", pady=(2, 0))

    def _draw_icon(self, canvas: tk.Canvas, kind: str, color: str, size: int = 18,
                   cx: float | None = None, cy: float | None = None,
                   clear: bool = True) -> None:
        """Иконки рисуем сами: emoji в tkinter выглядят по-разному на разных Windows.

        Размер 18–20 px: линия 2 px читается на 100 % масштабе и не «шумит»,
        как прежние 12-пиксельные пиктограммы.
        """
        if clear:
            canvas.delete("all")
        family = theme.icon_family()
        glyph = theme.icon_glyph(kind)
        if family and glyph:
            try:
                canvas.create_text(cx if cx is not None else size / 2,
                                   cy if cy is not None else size / 2,
                                   text=glyph, font=(family, -display.px(size)),
                                   fill=color)
                return
            except tk.TclError:
                pass
        try:
            width = max(canvas.winfo_width(), int(float(canvas.cget("width")) or 22))
            height = max(canvas.winfo_height(), int(float(canvas.cget("height")) or 30))
        except (tk.TclError, ValueError):
            width, height = 22, 30
        if cx is None:
            cx = width / 2
        if cy is None:
            cy = height / 2
        half = (size - 2) / 2
        if kind == "grid":
            step = size * 0.52
            box = size * 0.36
            for dx, dy in ((0, 0), (step, 0), (0, step), (step, step)):
                x, y = cx - half + dx, cy - half + dy
                canvas.create_rectangle(x, y, x + box, y + box, fill=color, width=0)
        elif kind == "book":
            canvas.create_line(cx - 6, cy - half, cx - 6, cy + half, fill=color, width=2)
            canvas.create_line(cx + 6, cy - half, cx + 6, cy + half, fill=color, width=2)
            canvas.create_line(cx - 6, cy - half, cx + 6, cy - half, fill=color, width=2)
            canvas.create_line(cx - 6, cy + half, cx + 6, cy + half, fill=color, width=2)
            canvas.create_line(cx, cy - half, cx, cy + half, fill=color, width=1)
        elif kind == "arrow":
            canvas.create_line(cx, cy + 7, cx, cy - 7, fill=color, width=2)
            canvas.create_line(cx - 5, cy - 1, cx, cy - 6, fill=color, width=2,
                               capstyle="round")
            canvas.create_line(cx + 5, cy - 1, cx, cy - 6, fill=color, width=2,
                               capstyle="round")
        elif kind == "tray":
            canvas.create_line(cx - 7, cy + 2, cx - 7, cy + 7, cx + 7, cy + 7, cx + 7, cy + 2,
                               fill=color, width=2, joinstyle="round")
            canvas.create_line(cx, cy - 7, cx, cy + 2, fill=color, width=2)
            canvas.create_line(cx - 4, cy - 1, cx, cy + 3, cx + 4, cy - 1, fill=color,
                               width=2, capstyle="round")
        elif kind == "layers":
            canvas.create_polygon(cx, cy - 7, cx + 7, cy - 2, cx, cy + 3, cx - 7, cy - 2,
                                  fill="", outline=color, width=2)
            canvas.create_line(cx - 7, cy + 3, cx, cy + 8, cx + 7, cy + 3, fill=color,
                               width=2, joinstyle="round")
        elif kind == "gear":
            canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline=color, width=2)
            canvas.create_oval(cx - 1, cy - 1, cx + 1, cy + 1, fill=color, width=0)
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0),
                           (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)):
                canvas.create_line(cx + dx * 6, cy + dy * 6, cx + dx * 8.5, cy + dy * 8.5,
                                   fill=color, width=2, capstyle="round")

    def _draw_search_icon(self, canvas: tk.Canvas, color: str = DIM) -> None:
        canvas.delete("all")
        canvas.create_oval(2, 2, 12, 12, outline=color, width=2)
        canvas.create_line(11, 11, 16, 16, fill=color, width=2, capstyle="round")

    def _draw_empty_icon(self, canvas: tk.Canvas, kind: str = "search",
                         size: int = 64) -> None:
        """Крупная иконка пустого состояния: мягкая плашка + лупа/слои/коробка."""
        canvas.delete("all")
        scale = size / 64
        radius = max(10, int(18 * scale))
        theme.rounded_rect(canvas, 1, 1, size - 1, size - 1, radius,
                           fill=SURFACE_2, outline=LINE)

        def point(x: float, y: float) -> tuple[float, float]:
            return x * scale, y * scale

        def line(*coords, **kwargs) -> None:
            pairs = [point(coords[index], coords[index + 1])
                     for index in range(0, len(coords), 2)]
            flat = [value for pair in pairs for value in pair]
            canvas.create_line(*flat, **kwargs)

        if kind == "search":
            x1, y1 = point(21, 20)
            x2, y2 = point(39, 38)
            canvas.create_oval(x1, y1, x2, y2, outline=ACCENT_SOFT_TEXT,
                               width=max(2, int(2 * scale)))
            line(37, 36, 45, 44, fill=ACCENT_SOFT_TEXT, width=max(3, int(3 * scale)),
                 capstyle="round")
        elif kind == "layers":
            p1, p2, p3, p4 = point(32, 20), point(45, 28), point(32, 36), point(19, 28)
            canvas.create_polygon(*p1, *p2, *p3, *p4, fill="", outline=ACCENT_SOFT_TEXT,
                                  width=max(2, int(2 * scale)))
            line(19, 35, 32, 43, 45, 35, fill=ACCENT_SOFT_TEXT,
                 width=max(2, int(2 * scale)))
        else:
            line(20, 28, 20, 44, 44, 44, 44, 28, fill=ACCENT_SOFT_TEXT,
                 width=max(2, int(2 * scale)), joinstyle="round")
            line(20, 28, 32, 36, 44, 28, fill=ACCENT_SOFT_TEXT, width=max(2, int(2 * scale)))
            line(32, 36, 32, 44, fill=ACCENT_SOFT_TEXT, width=max(2, int(2 * scale)))
            line(25, 24, 39, 24, fill=ACCENT_SOFT_TEXT, width=max(2, int(2 * scale)))

    def _nav_background(self, key: str, hover: bool = False) -> str:
        if key == self.view:
            return ACTIVE_BG
        return SURFACE_2 if hover else SOFT

    def _paint_nav(self, key: str, hover: bool = False) -> None:
        """Нарисовать пункт меню: скруглённая плашка, акцентная полоска, иконка 19 px."""
        parts = self.nav_parts.get(key)
        if not parts:
            return
        canvas = parts["canvas"]
        canvas.delete("all")
        width = canvas.winfo_width()
        if width <= 1:
            width = max(220, self.sidebar.winfo_width() - 24)
        height = NAV_H
        active = key == self.view
        if active or hover:
            fill = ACTIVE_BG if active else SURFACE_2
            theme.rounded_rect(canvas, 1, 1, width - 1, height - 1, 12,
                               fill=fill, outline="")
        if active:
            theme.rounded_rect(canvas, 9, 12, 12, height - 12, 2, fill=ACCENT)
        icon_color = ACCENT if active else (TEXT if hover else MUTED)
        self._draw_icon(canvas, parts["kind"], icon_color, size=19,
                        cx=30, cy=height // 2, clear=False)
        title = self._nav_titles.get(key, "")
        canvas.create_text(48, height // 2, text=i18n.tr(title), anchor="w",
                           fill=(TEXT if (active or hover) else MUTED),
                           font=(FONT_BOLD, 11, "bold") if active else (FONT, 11))
        badge = str(parts.get("badge") or "")
        if badge:
            accent = bool(parts.get("accent"))
            x2 = width - 12
            x1 = x2 - (16 + max(1, len(badge)) * 7)
            theme.rounded_rect(canvas, x1, 12, x2, 28, 8,
                               fill=(ACCENT if accent else SURFACE_3), outline="")
            canvas.create_text((x1 + x2) / 2, 20, text=badge,
                               fill=("#170d04" if accent else MUTED),
                               font=(FONT_BOLD, BODY_PX, "bold"))

    def _nav_hover(self, key: str, on: bool) -> None:
        if key != self.view:
            self._paint_nav(key, hover=on)

    def _set_active_nav(self, view: str) -> None:
        for key in self.nav_parts:
            self._paint_nav(key)

    def set_badge(self, key: str, value: int | str, accent: bool = False) -> None:
        parts = self.nav_parts.get(key)
        if not parts:
            return
        parts["badge"] = str(value) if value else ""
        parts["accent"] = bool(accent)
        self._paint_nav(key)

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
                         ("fav", self.filter_fav),
                         ("verified", self.filter_verified),
                         ("fresh", self.filter_fresh)):
            chip = self.filter_chips.get(key)
            if chip is None:
                continue
            self._paint_chip(chip, bool(var.get()))

    def refresh_all(self) -> None:
        """Перерисовать текущую вкладку и обновить счётчики в меню."""
        self.switch(self.view)
        self.refresh_meta()

    # ---------------------------------------------------------------- язык
    def _lang_label(self) -> str:
        return "EN" if i18n.current() == "ru" else "RU"

    def toggle_favorites(self) -> None:
        """Отдельная кнопка фильтра избранного."""
        self.filter_fav.set(not self.filter_fav.get())
        self._paint_fav_button()
        if self.view != "catalog":
            self.switch("catalog")
        else:
            self.reload()

    def _paint_fav_button(self) -> None:
        try:
            active = bool(self.filter_fav.get())
            self.fav_button.configure(text="★ Избранное")
            self.fav_button.set_palette(bg=(ACCENT_SOFT if active else PANEL),
                                        fg=(ACCENT_SOFT_TEXT if active else TEXT),
                                        hover=(SURFACE_3 if not active else ACCENT_SOFT),
                                        border=(ACCENT_SOFT if active else LINE))
        except Exception:  # noqa: BLE001
            pass

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
        if getattr(self, "_page_active", False):
            self._translate_ui(self.page)
        else:
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
                    if re.search("[А-Яа-яЁё]", current) and (
                            not original or current not in (original,
                                                           getattr(widget, "_en_text", None))):
                        widget._ru_text = current
                        original = current
                    if original:
                        translated = i18n.tr(original)
                        widget._en_text = translated
                        if translated != current:
                            widget.configure(text=translated)
                else:
                    original = getattr(widget, "_ru_text", None)
                    # Возвращаем русский только если на экране наш перевод,
                    # а не новый счётчик/статус, поставленный логикой.
                    if original:
                        shown = getattr(widget, "_en_text", None) or i18n.EN.get(original, original)
                        if current == shown and current != original:
                            widget.configure(text=original)

        if klass == "TCombobox":
            try:
                values = list(widget.cget("values"))
            except Exception:  # noqa: BLE001
                values = []
            if english and widget.get() and re.search("[А-Яа-яЁё]", widget.get()):
                widget._ru_value = getattr(widget, "_ru_value", widget.get())
                widget.set(i18n.tr(widget._ru_value))
            elif not english:
                original = getattr(widget, "_ru_value", None)
                if original and widget.get() != original and widget.get() == i18n.EN.get(original, original):
                    widget.set(original)
            if values and english:
                widget._ru_values = getattr(widget, "_ru_values", values)
                widget.configure(values=[i18n.tr(v) for v in getattr(widget, "_ru_values", values)])
            elif values and not english and getattr(widget, "_ru_values", None):
                stored = list(widget._ru_values)
                if list(values) == [i18n.EN.get(v, v) for v in stored]:
                    widget.configure(values=stored)

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
                    if original and current != original and current == i18n.EN.get(original, original):
                        widget.itemconfigure(item, text=original)
            widget._ru_items = store

        for child in widget.winfo_children():
            self._translate_ui(child)

    # ---------------------------------------------------------------- навигация
    def switch(self, view: str) -> None:
        if getattr(self, "_page_active", False):
            self._close_page_silent()
        self.view = view
        self._set_active_nav(view)
        titles = {"catalog": "Каталог модов", "collections": "Сборки", "library": "Библиотека",
                  "updates": "Обновления", "inbox": "Загрузки", "settings": "Настройки"}
        self._set_header_title(titles[view])
        self._set_header_count("")
        self._sync_header_tools()
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

    def _set_header_title(self, text: str) -> None:
        """Заголовок экрана 22 px — единственный крупный заголовок на странице."""
        self.title_label.configure(text=text)
        try:
            self.title_label._ru_text = text  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _set_header_count(self, text: str) -> None:
        self.count_label.configure(text=text)
        try:
            self.count_label._ru_text = text  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _short_title(title: str, limit: int = 72) -> str:
        text = " ".join(str(title or "").split())
        if len(text) > limit:
            text = text[:limit - 1].rstrip(" ,—·-") + "…"
        return text or "Без названия"

    @staticmethod
    def _short_path(path: str, limit: int = 34) -> str:
        text = str(path or "").strip()
        if not text:
            return "не выбрана"
        if len(text) <= limit:
            return text
        parts = [part for part in re.split(r"[\\/]+", text) if part]
        tail = "\\".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        return "…\\" + tail

    @staticmethod
    def _plural(count, one: str, few: str, many: str) -> str:
        """Русская форма после числа: 1 мод, 2 мода, 5 модов."""
        try:
            value = abs(int(count or 0))
        except (TypeError, ValueError):
            return many
        if value % 10 == 1 and value % 100 != 11:
            return one
        if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
            return few
        return many

    def _restore_section_title(self) -> None:
        title, title_ru, count, count_ru = getattr(
            self, "_page_backup", ("Каталог модов", "Каталог модов", "", ""))
        title_text = title_ru or title
        count_text = count_ru or count
        if i18n.current() == "en":
            title_text = i18n.tr(title_text)
            count_text = i18n.tr(count_text)
        self._set_header_title(title_text)
        self._set_header_count(count_text)

    def _close_page_silent(self) -> None:
        """Закрыть страницу без вызова «назад» — для перехода по сайдбару."""
        self._page_back = None
        self._page_active = False
        self.page.grid_remove()
        try:
            self.page_back.pack_forget()
        except Exception:  # noqa: BLE001
            pass
        self.canvas.grid(row=0, column=0, sticky="nsew")
        try:
            self.main_scroll.grid(row=0, column=1, sticky="ns")
        except Exception:  # noqa: BLE001
            pass

    def _sync_header_tools(self) -> None:
        """Поиск и фильтры каталога живут только на вкладке каталога."""
        show = self.view == "catalog"
        for frame, pady in ((getattr(self, "tools_row", None), (14, 0)),
                            (getattr(self, "chips_row", None), (10, 0))):
            if frame is None:
                continue
            if show:
                if not frame.winfo_manager():
                    frame.pack(fill="x", pady=pady)
            else:
                frame.pack_forget()

    def _clear(self) -> None:
        for child in self.canvas.winfo_children():
            child.destroy()
        self.canvas.delete("all")

    def _show_page(self, title: str, back=None) -> tk.Frame:
        """Открыть страницу внутри окна: титул 22 px в шапке, «← Назад» рядом."""
        if not getattr(self, "_page_active", False):
            self._page_backup = (
                self.title_label.cget("text"),
                getattr(self.title_label, "_ru_text", self.title_label.cget("text")),
                self.count_label.cget("text"),
                getattr(self.count_label, "_ru_text", self.count_label.cget("text")),
            )
        previous = getattr(self, "_page_back", None)
        self._page_back = back if back is not None else previous
        self._page_active = True
        try:
            self.page_back.pack(side="left", before=self._titles_frame, padx=(0, 12),
                                pady=(4, 0))
        except Exception:  # noqa: BLE001
            self.page_back.pack(side="left", padx=(0, 12), pady=(4, 0))
        self._set_header_title(self._short_title(title))
        self._set_header_count("")
        self.canvas.grid_remove()
        try:
            self.main_scroll.grid_remove()
        except Exception:  # noqa: BLE001
            pass
        self.page.grid(row=0, column=0, sticky="nsew")
        for child in self.page.winfo_children():
            child.destroy()
        body = tk.Frame(self.page, bg=BG)
        body.pack(fill="both", expand=True, padx=0, pady=(0, PAGE_PAD))
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)
        frame = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.bind("<Configure>",
                    lambda event, c=canvas, w=window_id: c.itemconfigure(
                        w, width=max(100, event.width - 8)))

        def sync_page(_event=None) -> None:
            bbox = canvas.bbox("all")
            if bbox:
                height = max(bbox[3], canvas.winfo_height())
                canvas.configure(scrollregion=(0, 0, bbox[2], height))

        frame.bind("<Configure>", sync_page)
        canvas.bind("<Configure>", sync_page, add="+")
        self._page_canvas = canvas
        return frame

    def _hide_page(self) -> None:
        back = getattr(self, "_page_back", None)
        self._close_page_silent()
        if callable(back):
            self._restore_section_title()
            back()
        else:
            # «Назад» из страницы, открытой из меню: перерисовываем раздел,
            # чтобы заголовок, счётчик и содержимое снова совпадали.
            self.switch(self.view)

    def _scrollable_frame(self) -> tk.Frame:
        self._clear()
        frame = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=frame, anchor="nw")

        def sync_scroll(_event=None) -> None:
            bbox = self.canvas.bbox("all")
            if bbox:
                height = max(bbox[3], self.canvas.winfo_height())
                self.canvas.configure(scrollregion=(0, 0, bbox[2], height))

        def fit_width(event=None) -> None:
            width = event.width if event is not None else self.canvas.winfo_width()
            if width > 100:
                self.canvas.itemconfigure(window, width=max(100, width - 8))

        frame.bind("<Configure>", sync_scroll)
        self.canvas.bind("<Configure>", sync_scroll, add="+")
        self.canvas.bind("<Configure>", fit_width, add="+")
        fit_width()
        self.root.after_idle(lambda: fit_width(None))
        self._cards_frame = frame
        return frame

    # ---------------------------------------------------------------- компоненты
    def _section(self, parent: tk.Misc, text: str, hint: str = "",
                 pady=(SECTION_GAP, 6)) -> tk.Frame:
        """Заголовок секции 13 px; одинаковые отступы на всех экранах."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", pady=pady)
        tk.Label(wrap, text=text, bg=BG, fg=TEXT, font=(FONT_BOLD, SECTION_PX, "bold"),
                 anchor="w").pack(side="left")
        if hint:
            tk.Label(wrap, text=hint, bg=BG, fg=DIM, font=(FONT, CAPTION_PX),
                     anchor="w").pack(side="left", padx=(10, 0), pady=(3, 0))
        return wrap

    def _panel(self, parent: tk.Misc, pady=(0, 12), radius: int = 14,
               fill: str = PANEL, border: str = LINE_SOFT) -> tk.Frame:
        """Скруглённая панель-контейнер; возвращает .body для содержимого."""
        outer = theme.RoundedFrame(parent, radius=radius, fill=fill, border=border, padding=1)
        outer.pack(fill="x", pady=pady)
        return outer.body

    def _stat_rows(self, parent: tk.Misc, items: list[tuple[str, object]],
                   pady=(0, 12)) -> tk.Frame:
        """Аккуратные строки «подпись — число»: без плиток и сетки рамок."""
        body = self._panel(parent, pady=pady)
        for index, (label, value) in enumerate(items):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text=label, bg=PANEL, fg=MUTED, font=(FONT, BODY_PX),
                     anchor="w").pack(side="left", padx=14, pady=9)
            tk.Label(row, text=str(value), bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, 18, "bold"), anchor="e").pack(side="right", padx=14,
                                                                    pady=3)
            if index < len(items) - 1:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        return body

    def _kv_rows(self, parent: tk.Misc, items: list[tuple[str, str]],
                 pady=(0, 12)) -> tk.Frame:
        """Строки «подпись — значение» для путей, версий и совместимости."""
        body = self._panel(parent, pady=pady)
        for index, (label, value) in enumerate(items):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text=label, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                     width=24, anchor="nw", justify="left").pack(side="left", padx=(14, 8),
                                                                  pady=8)
            tk.Label(row, text=value or "—", bg=PANEL, fg=TEXT, font=(FONT, BODY_PX),
                     anchor="w", justify="left", wraplength=980).pack(side="left", fill="x",
                                                                     expand=True, padx=(0, 14),
                                                                     pady=8)
            if index < len(items) - 1:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        return body

    def _action_group(self, parent: tk.Misc, caption: str = "") -> tk.Frame:
        """Ряд действий с подписью-группой: «Установка», «Управление»."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        if caption:
            tk.Label(row, text=caption, bg=BG, fg=DIM, font=(FONT, CAPTION_PX),
                     width=12, anchor="w").pack(side="left")
        buttons = tk.Frame(row, bg=BG)
        buttons.pack(side="left", fill="x", expand=True)
        return buttons

    def _chip(self, parent: tk.Misc, text: str, active: bool, command) -> theme.RoundButton:
        chip = theme.RoundButton(parent, text, command, height=28, radius=14,
                                 font=(FONT, CAPTION_PX), bg=SURFACE_2, fg=MUTED,
                                 hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG)
        chip._is_chip = True  # type: ignore[attr-defined]
        self._paint_chip(chip, active)
        return chip

    @staticmethod
    def _paint_chip(chip, active: bool) -> None:
        if active:
            chip.set_palette(bg=ACCENT_SOFT, fg=ACCENT_SOFT_TEXT, hover="#3f2814",
                             border=ACCENT_SOFT)
        else:
            chip.set_palette(bg=SURFACE_2, fg=MUTED, hover=SURFACE_3, border=LINE)

    def _pill(self, parent: tk.Misc, text: str, kind: str = "neutral") -> tk.Label:
        colors = {"ok": (GREEN_SOFT, GREEN_TEXT), "warn": (WARN_SOFT, WARN_TEXT),
                  "danger": (DANGER_SOFT, "#ff8f8f"), "neutral": (SURFACE_3, MUTED)}
        bg, fg = colors.get(kind, colors["neutral"])
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, CAPTION_PX),
                        padx=8, pady=2)

    def _search_box(self, parent: tk.Misc, placeholder: str, on_submit,
                    width: int = 360) -> tuple[tk.Widget, tk.Widget, tk.StringVar]:
        """Поиск с лупой и плейсхолдером — тот же компонент, что в шапке."""
        shell = theme.RoundedFrame(parent, radius=12, fill=SURFACE_2, border=LINE,
                                   padding=1, width=width, height=38)
        area = shell.body
        glass = tk.Canvas(area, width=18, height=18, bg=SURFACE_2, highlightthickness=0, bd=0)
        glass.place(x=13, rely=0.5, anchor="w")
        self._draw_search_icon(glass, MUTED)
        var = tk.StringVar()
        entry = tk.Entry(area, textvariable=var, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT,
                         relief="flat", bd=0, highlightthickness=0, font=(FONT, LEAD_PX))
        entry.pack(fill="x", padx=(40, 12), ipady=7)
        hint = tk.Label(area, text=placeholder, bg=SURFACE_2, fg=DIM, font=(FONT, LEAD_PX))
        hint.place(x=40, rely=0.5, anchor="w")
        hint.bind("<Button-1>", lambda _e: entry.focus_set())

        def focus(_event=None) -> None:
            hint.place_forget()
            shell.set_border(BORDER_STRONG)

        def blur(_event=None) -> None:
            if not var.get():
                hint.place(x=40, rely=0.5, anchor="w")
            shell.set_border(LINE)

        entry.bind("<FocusIn>", focus)
        entry.bind("<FocusOut>", blur)
        entry.bind("<Return>", lambda _e: on_submit())
        return shell, entry, var

    def _rounded_entry(self, parent: tk.Misc, variable: tk.StringVar, width: int = 52,
                       show: str = "") -> tuple[tk.Widget, tk.Entry]:
        shell = theme.RoundedFrame(parent, radius=10, fill=SURFACE_2, border=LINE,
                                   padding=1, width=width * 7 + 28, height=36)
        entry = tk.Entry(shell.body, textvariable=variable, bg=SURFACE_2, fg=TEXT,
                         relief="flat", bd=0, highlightthickness=0, insertbackground=TEXT,
                         font=(FONT, BODY_PX), show=show, width=width)
        entry.pack(fill="x", padx=10, ipady=6)
        entry.bind("<FocusIn>", lambda _e: shell.set_border(BORDER_STRONG))
        entry.bind("<FocusOut>", lambda _e: shell.set_border(LINE))
        return shell, entry

    def _field(self, parent: tk.Misc, label: str, variable: tk.StringVar,
               width: int = 52, show: str = "") -> tk.Entry:
        tk.Label(parent, text=label, bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        shell, entry = self._rounded_entry(parent, variable, width=width, show=show)
        shell.pack(anchor="w", padx=14, pady=(0, 2))
        return entry

    def _toggle_row(self, parent: tk.Misc, text: str, variable: tk.BooleanVar,
                    hint: str = "") -> tk.Frame:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=14, pady=9)
        texts = tk.Frame(row, bg=PANEL)
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, text=text, bg=PANEL, fg=TEXT, font=(FONT, BODY_PX),
                 anchor="w").pack(fill="x")
        if hint:
            tk.Label(texts, text=hint, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                     anchor="w", justify="left", wraplength=860).pack(fill="x", pady=(2, 0))
        theme.ToggleSwitch(row, variable).pack(side="right", padx=(12, 0))
        return row

    def _thumb_box(self, parent: tk.Misc, item: dict, size: tuple[int, int] = (96, 54),
                   radius: int = 10) -> tk.Frame:
        box = tk.Frame(parent, bg=PANEL, width=size[0], height=size[1])
        box.pack_propagate(False)
        label = tk.Label(box, bg=PANEL, bd=0, text="")
        label.pack(fill="both", expand=True)
        self._set_placeholder(label, item, size, radius)
        if item.get("image"):
            self._request_image(item["image"], label, item, size, radius)
        return box

    def _cover_box(self, parent: tk.Misc, coll: dict, size: tuple[int, int] = (96, 54),
                   radius: int = 10) -> tk.Frame:
        box = tk.Frame(parent, bg=PANEL, width=size[0], height=size[1])
        box.pack_propagate(False)
        label = tk.Label(box, bg=PANEL, bd=0, text="")
        label.pack(fill="both", expand=True)
        title = (coll.get("title") or "?").strip()
        letters = "".join(part[0] for part in title.split()[:2]).upper() or "?"
        colors = {"drift": (255, 122, 26), "offroad": (120, 200, 130), "jdm": (255, 160, 80),
                  "racing": (240, 100, 100), "city": (90, 190, 210), "maps": (60, 160, 220),
                  "tuning": (200, 160, 255), "realistic": (230, 200, 120),
                  "stunt": (255, 200, 80), "mixed": (150, 155, 170)}
        color = colors.get(coll.get("category") or "", (150, 155, 170))
        photo = None
        path = coll.get("cover") or ""
        if path:
            photo = self._local_photo(path, size, radius)
        if photo is None:
            photo = self._letters_photo(letters, color, size, radius,
                                        fill=SURFACE_2, outline=LINE)
        if photo is not None:
            label.configure(image=photo)
            label.image = photo
        return box

    def _empty_state(self, parent: tk.Misc, title: str, hint: str, icon: str = "search",
                     action=None, action_text: str = "", primary: bool = False,
                     compact: bool = False) -> tk.Frame:
        """Пустое состояние: иконка, заголовок, подсказка и действие (если есть)."""
        box = tk.Frame(parent, bg=BG)
        size = 46 if compact else 64
        canvas = tk.Canvas(box, width=size, height=size, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(pady=(0, 10 if compact else 14))
        self._draw_empty_icon(canvas, icon, size)
        tk.Label(box, text=title, bg=BG, fg=TEXT,
                 font=(FONT_BOLD, SECTION_PX if compact else 18, "bold")).pack()
        tk.Label(box, text=hint, bg=BG, fg=MUTED, font=(FONT, BODY_PX),
                 wraplength=360 if compact else 520, justify="center").pack(pady=(6, 0))
        if action is not None and action_text:
            theme.RoundButton(box, action_text, action, height=30 if compact else 34,
                              radius=10,
                              variant=("primary" if primary else "ghost"),
                              font=(FONT_BOLD if primary else FONT, 9 if compact else 10,
                                    "bold" if primary else "normal")
                              ).pack(pady=(10 if compact else 14, 0))
        return box

    def _on_wheel(self, event) -> None:
        if getattr(self, "_page_active", False):
            canvas = getattr(self, "_page_canvas", None)
            if canvas is not None:
                canvas.yview_scroll(int(-event.delta / 120), "units")
            return
        if self.view in ("catalog", "library", "inbox", "collections", "updates", "settings"):
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ---------------------------------------------------------------- каталог
    def reload(self, append: bool = False) -> None:
        if not append:
            self.offset = 0
            self.items = []
            self.page_no = 1
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
        if self.filter_verified.get():
            params["verified"] = "1"
        if self.filter_fresh.get():
            params["fresh"] = "1"
        try:
            data = self.api.get("/api/mods", **params)
        except Exception as exc:  # noqa: BLE001
            self.count_label.configure(text=f"ошибка: {exc}")
            return
        self.total = data.get("total", 0)
        self.items.extend(data.get("items", []))
        self.count_label.configure(text=f"{self.total} {self._plural(self.total, 'мод', 'мода', 'модов')}")
        self._columns = 0
        self._render_cards(append)
        self.root.after(350, self._apply_resize)

    def _grid_columns(self) -> int:
        width = 0
        try:
            width = self.canvas.winfo_width()
        except Exception:  # noqa: BLE001
            width = 0
        if width <= 200:
            try:
                width = max(0, self.root.winfo_width() - 240)
            except Exception:  # noqa: BLE001
                width = 0
        if width <= 200:
            width = 1100
        return max(2, min(10, width // (IMG_W + CARD_GAP + 14)))

    def _on_resize(self, _event=None) -> None:
        """На широком окне показываем больше карточек в ряд (с задержкой)."""
        if getattr(self, "_page_active", False) or self.view != "catalog":
            return
        if getattr(self, "_resize_pending", False):
            return
        self._resize_pending = True
        self.root.after(250, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_pending = False
        if getattr(self, "_page_active", False) or self.view != "catalog":
            return
        if not getattr(self, "items", None) or not getattr(self, "_cards_frame", None):
            return
        columns = self._grid_columns()
        if columns == getattr(self, "_columns", 0):
            return
        self._render_cards(False)

    def _catalog_empty_hint(self) -> str:
        filtered = bool(self.query or self.filter_game.get() or self.filter_image.get()
                        or self.filter_fav.get() or self.category != "all")
        if filtered:
            return ("Попробуйте изменить запрос или сбросить фильтры — "
                    "в каталоге тысячи модов.")
        return "Нажмите «Обновить каталог», чтобы загрузить моды."

    def _reset_catalog(self) -> None:
        """Сбросить поиск и фильтры — действие пустого состояния каталога."""
        self.query = ""
        self.category = "all"
        self.filter_game.set(False)
        self.filter_image.set(False)
        self.filter_fav.set(False)
        self.filter_verified.set(False)
        self.filter_fresh.set(False)
        try:
            self.search_var.set("")
        except Exception:  # noqa: BLE001
            pass
        self._paint_chips()
        self._paint_fav_button()
        self.pick_category("all")

    def _render_cards(self, append: bool) -> None:
        frame = self._cards_frame if append and self._cards_frame else self._scrollable_frame()
        columns = self._grid_columns()
        self._columns = columns
        # перерисовываем все карточки: проще и предсказуемее, чем считать индексы
        for child in frame.winfo_children():
            child.destroy()
        if not self.items:
            filtered = bool(self.query or self.filter_game.get() or self.filter_image.get()
                            or self.filter_fav.get() or self.category != "all")
            self._set_header_count("0 модов")
            if filtered:
                empty = self._empty_state(frame, "Ничего не найдено",
                                          self._catalog_empty_hint(), "search",
                                          action=self._reset_catalog,
                                          action_text="Сбросить фильтры", primary=True)
            else:
                empty = self._empty_state(frame, "Пока пусто",
                                          self._catalog_empty_hint(), "box",
                                          action=self.on_sync,
                                          action_text="Обновить каталог", primary=True)
            empty.pack(pady=(88, 40))
            return
        for index, item in enumerate(self.items):
            row, col = divmod(index, columns)
            card = self._card(frame, item)
            card.grid(row=row, column=col, padx=CARD_GAP // 2, pady=CARD_GAP // 2, sticky="n")
        more_row = (len(self.items) + columns - 1) // columns
        pager = tk.Frame(frame, bg=BG)
        pager.grid(row=more_row, column=0, columnspan=columns, pady=18)
        page_size = 24
        pages = max(1, (self.total + page_size - 1) // page_size)
        current = self.page_no

        def page_button(label: str, target: int, enabled: bool = True) -> None:
            button = theme.RoundButton(pager, label,
                                       (lambda: self.goto_page(target)) if enabled else None,
                                       height=30, width=38, radius=10, font=(FONT, 9),
                                       variant=("primary" if target == current and enabled
                                                else "ghost"))
            button.pack(side="left", padx=2)
            if not enabled:
                button.configure(state="disabled")

        if pages > 1:
            page_button("‹", current - 1, current > 1)
            first = 1
            last = pages
            if pages > 7:
                first = max(1, current - 2)
                last = min(pages, first + 4)
                first = max(1, last - 4)
            if first > 1:
                page_button("1", 1)
                if first > 2:
                    tk.Label(pager, text="…", bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")
            for number in range(first, last + 1):
                page_button(str(number), number)
            if last < pages:
                if last < pages - 1:
                    tk.Label(pager, text="…", bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")
                page_button(str(pages), pages)
            page_button("›", current + 1, current < pages)

        if len(self.items) < self.total:
            theme.RoundButton(pager, "Показать ещё", self.load_more, height=30, radius=8,
                              font=(FONT, 9)).pack(side="left", padx=(10, 0))

    def goto_page(self, page: int) -> None:
        page = max(1, page)
        self.page_no = page
        self.offset = (page - 1) * 24
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
        if self.filter_verified.get():
            params["verified"] = "1"
        if self.filter_fresh.get():
            params["fresh"] = "1"
        try:
            data = self.api.get("/api/mods", **params)
        except Exception as exc:  # noqa: BLE001
            self.count_label.configure(text=f"ошибка: {exc}")
            return
        self.total = data.get("total", 0)
        self.items = data.get("items", [])
        self.count_label.configure(text=f"{self.total} {self._plural(self.total, 'мод', 'мода', 'модов')} · стр. {self.page_no}")
        self._columns = 0
        self._render_cards(False)
        self.canvas.yview_moveto(0.0)

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
        if self.filter_verified.get():
            params["verified"] = "1"
        if self.filter_fresh.get():
            params["fresh"] = "1"
        try:
            data = self.api.get("/api/mods", **params)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")
            return
        self.items.extend(data.get("items", []))
        self.total = data.get("total", self.total)
        self.page_no = (len(self.items) + 23) // 24
        self._render_cards(True)
        try:
            self.count_label.configure(text=f"{self.total} {self._plural(self.total, 'мод', 'мода', 'модов')} · стр. {self.page_no}")
        except Exception:  # noqa: BLE001
            pass
        self.root.after(120, lambda: self.canvas.yview_moveto(1.0))

    def _bind_click_tree(self, widget: tk.Misc, callback) -> None:
        """Клик по любому месту плитки, кроме кнопок — у них своё действие."""
        skip = (tk.Button, theme.RoundButton)
        if not isinstance(widget, skip):
            widget.bind("<Button-1>", lambda _e, cb=callback: cb())
        for child in widget.winfo_children():
            self._bind_click_tree(child, callback)

    def _is_russian(self, item: dict) -> bool:
        """Мод с русской локализацией: ищем пометки в названии и описании."""
        blob = f"{item.get('title') or ''} {item.get('description') or ''}".lower()
        return bool(re.search(r"russian|русск|русиф|\bru\b", blob))

    def _card(self, parent: tk.Frame, item: dict) -> tk.Frame:
        duplicate = bool(item.get("duplicate"))
        outer = theme.RoundedFrame(parent, radius=16, fill=PANEL,
                                   border=(DUP_LINE if duplicate else LINE_SOFT),
                                   padding=8, width=IMG_W + display.px(18))
        card = outer.body
        holder = tk.Frame(card, bg=PANEL, width=IMG_W, height=IMG_H)
        holder.pack(fill="x")
        holder.pack_propagate(False)  # фиксированная высота, пока картинки нет
        thumb = tk.Label(holder, bg=PANEL, bd=0, text="", fg=MUTED, font=(FONT, 9))
        thumb.pack(fill="both", expand=True)
        self._set_placeholder(thumb, item)
        if item.get("image"):
            self._request_image(item["image"], thumb, item)
        if duplicate:
            sources = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            badge_text = f"дубликат · {item.get('duplicate_count')}"
            badge = tk.Label(holder, text=badge_text, bg=ACCENT_SOFT, fg=ACCENT_SOFT_TEXT,
                             font=(FONT_BOLD, BODY_PX, "bold"), padx=8, pady=3)
            badge.place(relx=0, rely=1.0, x=10, y=-10, anchor="sw")
            badge.bind("<Enter>", lambda _e, b=badge, s=sources: b.configure(
                text=f"дубликат · ещё {s}" if s else b.cget("text")))

        badge_row = tk.Frame(card, bg=PANEL, height=16)
        badge_row.pack(fill="x", padx=12, pady=(10, 0))
        badge_row.pack_propagate(False)
        tk.Label(badge_row, text=item.get("category_label", "Другое"), bg=PANEL,
                 fg=ACCENT_SOFT_TEXT, font=(FONT, 9), anchor="w").pack(side="left")
        if self._is_russian(item):
            tk.Label(badge_row, text="RU", bg=SURFACE_3, fg=ACCENT_SOFT_TEXT,
                     font=(FONT_BOLD, 8, "bold"), padx=4).pack(side="left", padx=(6, 0))
        size = item.get("size_text") or ""
        if size:
            tk.Label(badge_row, text=size, bg=PANEL, fg=DIM, font=(FONT, 9),
                     anchor="e").pack(side="right")

        title = item.get("title") or "Без названия"
        label = tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=(FONT_BOLD, 10, "bold"),
                         wraplength=IMG_W - 26, justify="left", anchor="nw", height=2)
        label.pack(fill="x", padx=12, pady=(5, 1))

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
        tk.Label(card, text=" · ".join(meta_bits), bg=PANEL, fg=DIM, font=(FONT, 9),
                 anchor="w", height=1).pack(fill="x", padx=12)

        actions = tk.Frame(card, bg=PANEL, height=32)
        actions.pack(fill="x", padx=12, pady=(10, 12))
        actions.pack_propagate(False)
        text = "Установлен" if item.get("installed") else "Скачать"
        button = theme.RoundButton(actions, text, lambda i=item: self.install(i),
                                   variant="primary", height=32, radius=10,
                                   font=(FONT_BOLD, BODY_PX, "bold"))
        button.pack(side="left", fill="x", expand=True)
        star = theme.RoundButton(actions, "★" if item.get("favorite") else "☆",
                                 width=34, height=32, radius=10, bg=PANEL,
                                 fg=("#ffd08a" if item.get("favorite") else MUTED),
                                 hover=SURFACE_3, font=(FONT, 11))
        star.configure(command=lambda i=item, b=star: self.toggle_favorite(i, b))
        star.pack(side="left", padx=(6, 0))
        theme.RoundButton(actions, "⋯", lambda i=item: self.details(i),
                          width=34, height=32, radius=10, bg=PANEL, fg=MUTED,
                          hover=SURFACE_3, font=(FONT, 13)).pack(side="left", padx=(6, 0))

        self._bind_click_tree(card, lambda i=item: self.details(i))
        self._bind_card_hover(outer, duplicate)
        return outer

    def _bind_card_hover(self, card: tk.Canvas, duplicate: bool) -> None:
        """Подсветка рамки карточки при наведении — без мигания."""
        normal = DUP_LINE if duplicate else LINE_SOFT
        hot = "#6b3a18" if duplicate else BORDER_STRONG

        def inside() -> bool:
            x, y = card.winfo_pointerxy()
            widget = card.winfo_containing(x, y)
            while widget is not None:
                if widget is card:
                    return True
                widget = getattr(widget, "master", None)
            return False

        def enter(_event=None) -> None:
            if hasattr(card, "set_border"):
                card.set_border(hot)

        def leave(_event=None) -> None:
            def restore() -> None:
                if inside():
                    return
                if hasattr(card, "set_border"):
                    card.set_border(normal)
            card.after(30, restore)

        widgets = [card]
        pending = list(card.winfo_children())
        while pending:
            widget = pending.pop()
            widgets.append(widget)
            pending.extend(widget.winfo_children())
        for widget in widgets:
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

    def _request_image(self, url: str, label: tk.Label, item: dict | None = None,
                       size: tuple[int, int] | None = None,
                       radius: int | None = None) -> None:
        size = size or (IMG_W, IMG_H)
        radius = IMG_RADIUS if radius is None else radius
        key = f"{url}|{size[0]}x{size[1]}|{radius}"
        cached = self._images.get(key)
        if cached is not None:
            try:
                label.configure(image=cached)
                label.image = cached
            except tk.TclError:
                pass
            return
        threading.Thread(target=self._load_image,
                         args=(url, label, item or {}, size, radius), daemon=True).start()

    @staticmethod
    def _rgb(color: str) -> tuple[int, int, int]:
        value = color.lstrip("#")
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    def _round_image(self, image, radius: int, top_only: bool = False):
        """Скруглить изображение: все углы или только верхние (обложка карточки)."""
        if not HAS_PIL:
            return image
        from PIL import ImageDraw

        width, height = image.size
        mask_height = height * 2 if top_only else height
        mask = Image.new("L", (width, mask_height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, mask_height - 1), radius=radius, fill=255)
        if top_only:
            mask = mask.crop((0, 0, width, height))
        base = Image.new("RGB", (width, height), self._rgb(PANEL))
        base.paste(image, (0, 0), mask)
        return base

    def _round_top(self, image):
        """Скруглить только верхние углы обложки — низ карточки остаётся прямым."""
        return self._round_image(image, IMG_RADIUS, top_only=True)

    def _letters_photo(self, letters: str, color: tuple[int, int, int],
                       size: tuple[int, int], radius: int,
                       fill: str = PANEL, outline: str = "") -> object | None:
        """Заглушка-обложка нужного размера: две буквы названия, без обрезки."""
        if not HAS_PIL:
            return None
        key = f"letters|{letters}|{color}|{size[0]}x{size[1]}|{radius}|{fill}|{outline}"
        cached = self._placeholders.get(key)
        if cached is not None:
            return cached
        from PIL import ImageDraw

        image = Image.new("RGB", size, self._rgb(fill))
        draw = ImageDraw.Draw(image)
        if outline:
            draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius,
                                   outline=outline, width=1)
        font_size = max(13, int(size[1] * 0.34))
        try:
            from PIL import ImageFont

            font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", font_size)
        except Exception:  # noqa: BLE001
            font = None
        if font is not None:
            draw.text((size[0] / 2, size[1] / 2), letters, fill=color, font=font, anchor="mm")
        else:
            draw.text((size[0] / 2 - 12, size[1] / 2 - 8), letters, fill=color)
        photo = ImageTk.PhotoImage(self._round_image(image, radius))
        self._placeholders[key] = photo
        return photo

    def _local_photo(self, path: str, size: tuple[int, int], radius: int) -> object | None:
        """Локальная картинка (обложка сборки, аватар) в нужном размере и со скруглением."""
        if not HAS_PIL:
            return None
        key = f"local|{path}|{size[0]}x{size[1]}|{radius}"
        cached = self._images.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageOps

            file = Path(config.DATA_DIR) / str(path)
            with Image.open(file) as image:
                fitted = ImageOps.fit(image.convert("RGB"), size, Image.LANCZOS)
            photo = ImageTk.PhotoImage(self._round_image(fitted, radius))
            self._images[key] = photo
            return photo
        except Exception:  # noqa: BLE001
            return None

    def _set_placeholder(self, label: tk.Label, item: dict,
                         size: tuple[int, int] | None = None,
                         radius: int | None = None) -> None:
        """Заглушка вместо обложки: две буквы имени на плашке категории."""
        size = size or (IMG_W, IMG_H)
        size = (display.px(size[0]), display.px(size[1]))
        radius = (IMG_RADIUS if size == (IMG_W, IMG_H) else display.px(10)) \
            if radius is None else display.px(radius)
        title = (item.get("title") or "?").strip()
        letters = "".join(part[0] for part in title.split()[:2]).upper() or "?"
        colors = {"cars": (255, 122, 26), "maps": (60, 160, 220), "tools": (120, 200, 130),
                  "planes": (200, 160, 255), "bikes": (255, 200, 80), "boats": (90, 200, 210)}
        color = colors.get(item.get("category") or "other", (150, 155, 170))
        photo = self._letters_photo(letters, color, size, radius)
        if photo is None:
            label.configure(text=title[:24], fg=MUTED, font=(FONT, CAPTION_PX))
            return
        label.configure(image=photo)
        label.image = photo

    def _load_image(self, url: str, label: tk.Label, item: dict,
                    size: tuple[int, int], radius: int) -> None:
        try:
            raw = self.api.image(url)
            if not HAS_PIL:
                return
            import io

            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image = ImageOps.fit(image, size, Image.LANCZOS)
            image = self._round_image(image, radius, top_only=(size == (IMG_W, IMG_H)))
        except Exception as exc:  # noqa: BLE001
            print(f"[tkui] картинка не загрузилась: {exc}")
            try:
                self.root.after(0, lambda: self._set_placeholder(label, item, size, radius)
                                if label.winfo_exists() else None)
            except Exception:  # noqa: BLE001
                pass
            return

        def apply() -> None:
            try:
                if not label.winfo_exists():
                    return
                photo = ImageTk.PhotoImage(image)
                self._images[f"{url}|{size[0]}x{size[1]}|{radius}"] = photo
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
                label.configure(image=photo)
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

    def details(self, item: dict, back=None) -> None:
        page = self._show_page(item.get("title") or "Мод", back=back)
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", pady=(0, 4))

        holder = tk.Frame(top, bg=BG, width=384, height=216)
        holder.pack(side="left")
        holder.pack_propagate(False)
        preview = tk.Label(holder, bg=PANEL, bd=0, text="")
        preview.pack(fill="both", expand=True)
        self._set_placeholder(preview, item, (384, 216), 14)
        if item.get("image"):
            self._request_image(item["image"], preview, item, (384, 216), 14)

        side = tk.Frame(top, bg=BG)
        side.pack(side="left", fill="both", expand=True, padx=(20, 0))
        category = item.get("category_label") or "—"
        if item.get("duplicate"):
            category += f"  ·  дубликат · {item.get('duplicate_count')}"
        rows = (
            ("Категория", category),
            ("Версия игры", item.get("game_version") or "не указана"),
            ("Версия мода", item.get("version") or "не указана"),
            ("Размер", item.get("size_text") or "—"),
            ("Скачиваний", str(item.get("downloads") or 0)),
            ("Добавлен", item.get("ago") or "—"),
        )
        for label, value in rows:
            line = tk.Frame(side, bg=BG)
            line.pack(fill="x", pady=2)
            tk.Label(line, text=label, bg=BG, fg=DIM, font=(FONT, CAPTION_PX),
                     width=16, anchor="w").pack(side="left")
            tk.Label(line, text=value, bg=BG, fg=TEXT, font=(FONT, BODY_PX),
                     anchor="w").pack(side="left")
        if item.get("duplicate"):
            names = ", ".join(SOURCE_NAMES.get(s, s) for s in item.get("duplicate_sources") or [])
            self._pill(side, f"Такой же мод есть ещё на: {names or 'других сайтах'}",
                       "warn").pack(anchor="w", pady=(8, 0))

        actions = tk.Frame(side, bg=BG)
        actions.pack(fill="x", pady=(14, 0))
        theme.RoundButton(actions, "Скачать", lambda: self.install(item), variant="primary",
                          height=34, radius=10,
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        theme.RoundButton(actions, "＋ В сборку", lambda: self.pick_collection_for_mod(item),
                          height=34, radius=10, bg=PANEL, fg=TEXT, hover=ACTIVE_BG,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, BODY_PX)).pack(side="left", padx=8)
        theme.RoundButton(actions, "Открыть страницу", lambda: self._open_page(item),
                          height=34, radius=10, bg=PANEL, fg=TEXT, hover=ACTIVE_BG,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, BODY_PX)).pack(side="left")

        self._section(page, "Описание", pady=(16, 6))
        description = self._panel(page, pady=(0, 4))
        tk.Label(description, text=item.get("description") or "Описание на странице мода.",
                 bg=PANEL, fg="#c3c8d4", font=(FONT, BODY_PX), wraplength=1280,
                 justify="left", anchor="w").pack(fill="x", padx=14, pady=12)

        extras = tk.Frame(page, bg=BG)
        extras.pack(fill="x")
        threading.Thread(target=self._details_extras, args=(page, extras, item),
                         daemon=True).start()
        self._translate_ui(page)

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
                head = self._section(frame, "Совместимость", pady=(16, 6))
                known = "проверено" in str(verdict.get("file") or "").lower()
                self._pill(head, verdict.get("file") or "неизвестно",
                           "ok" if known else "neutral").pack(side="right")
                compat_rows = [
                    ("Файл", file_info.get("detail", "") or "—"),
                    ("Версия игры", f"заявлено «{game.get('declared') or 'не указано'}», "
                                    f"установлено «{game.get('installed') or 'не определено'}»"),
                ]
                conflicts = compat.get("conflicts") or []
                if conflicts:
                    names = ", ".join(row.get("title") or row.get("filename") or "?"
                                      for row in conflicts[:3])
                    compat_rows.append(("Конфликты", names))
                else:
                    compat_rows.append(("Конфликты", "с установленными модами не найдено"))
                self._kv_rows(frame, compat_rows, pady=(0, 4))
            items = (similar or {}).get("items") or []
            if items:
                self._section(frame, "Похожие моды", pady=(16, 6))
                panel = self._panel(frame, pady=(0, 4))
                for index, row in enumerate(items[:5]):
                    line = tk.Frame(panel, bg=PANEL)
                    line.pack(fill="x", padx=10, pady=5)
                    thumb = self._thumb_box(line, row, (96, 54), 10)
                    thumb.pack(side="left", padx=(0, 10))
                    info = tk.Frame(line, bg=PANEL)
                    info.pack(side="left", fill="x", expand=True)
                    tk.Label(info, text=row.get("title") or row["id"], bg=PANEL, fg=TEXT,
                             font=(FONT, BODY_PX), anchor="w").pack(fill="x")
                    meta = " · ".join(filter(None, [
                        CATEGORY_LABELS.get(row.get("category") or "", ""),
                        row.get("size_text") or ""]))
                    tk.Label(info, text=meta or "мод каталога", bg=PANEL, fg=DIM,
                             font=(FONT, CAPTION_PX), anchor="w").pack(fill="x")
                    self._bind_click_tree(
                        line, lambda r=row, cur=item: self.details_by_id(
                            r["id"], back=lambda: self.details(cur)))
                    if index < len(items[:5]) - 1:
                        tk.Frame(panel, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
            self._translate_ui(window)
        self.root.after(0, apply)

    def details_by_id(self, mod_id: str, back=None) -> None:
        try:
            data = self.api.get("/api/mod", id=mod_id)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Не удалось открыть мод: {exc}", "error")
            return
        if not data.get("ok") and data.get("item") is None:
            self.toast(data.get("error", "Мод не найден"), "error")
            return
        self.details(data.get("item") or data, back=back)

    def stats_window(self) -> None:
        page = self._show_page("Статистика сообщества")
        try:
            data = self.api.get("/api/stats")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(page, "Статистика недоступна", f"Ошибка: {exc}", "box",
                              action=self.stats_window, action_text="Повторить",
                              compact=True).pack(pady=30)
            return

        self._section(page, "Сводка", pady=(0, 6))
        self._stat_rows(page, [
            ("Пользователи", data.get("users", 0)),
            ("Активны за 7 дней", data.get("active_users_7d", 0)),
            ("Сборки", data.get("collections", 0)),
            ("Просмотры", data.get("views", 0)),
            ("Установки", data.get("installs", 0)),
            ("Отзывы", data.get("comments", 0)),
            ("Оценки", data.get("ratings", 0)),
        ])

        activity = data.get("activity") or []
        if activity:
            totals: dict = {}
            for row in activity:
                day = str(row.get("day") or "")
                totals[day] = totals.get(day, 0) + int(row.get("events") or 0)
            self._section(page, "Активность", "события за 14 дней")
            chart = tk.Frame(page, bg=BG)
            chart.pack(fill="x", pady=(0, 12))
            peak = max(totals.values()) or 1
            for day in sorted(totals)[-14:]:
                value = totals[day]
                col = tk.Frame(chart, bg=BG)
                col.pack(side="left", padx=3)
                height = max(4, int(44 * value / peak))
                tk.Frame(col, bg=BG, height=44 - height).pack()
                tk.Frame(col, bg=ACCENT, width=20, height=height).pack()
                tk.Label(col, text=str(value), bg=BG, fg=MUTED,
                         font=(FONT, CAPTION_PX)).pack(pady=(2, 0))
                tk.Label(col, text=day[5:], bg=BG, fg=DIM,
                         font=(FONT, CAPTION_PX)).pack()

        self._section(page, "Топ модов", "по установкам, кликам и просмотрам")
        top = data.get("top_mods") or []
        if not top:
            self._empty_state(page, "Пока пусто",
                              "Статистика появится, когда моды начнут устанавливать.",
                              "box", compact=True).pack(pady=12)
        else:
            body = self._panel(page)
            for index, row in enumerate(top[:8], start=1):
                line = tk.Frame(body, bg=PANEL)
                line.pack(fill="x")
                tk.Label(line, text=f"{index:02d}", bg=PANEL, fg=DIM,
                         font=(FONT, CAPTION_PX), width=4, anchor="w").pack(side="left",
                                                                             padx=(14, 4),
                                                                             pady=10)
                tk.Label(line, text=row.get("title") or "мод", bg=PANEL, fg=TEXT,
                         font=(FONT, BODY_PX), anchor="w").pack(side="left", fill="x",
                                                               expand=True, pady=10)
                tk.Label(line, text=f"установок {row.get('installs', 0)}   ·   "
                                    f"кликов {row.get('downloads', 0)}   ·   "
                                    f"просмотров {row.get('views', 0)}",
                         bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX)).pack(side="right", padx=14)
                if index < len(top[:8]):
                    tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)

        self._section(page, "Топ сборок", "по установкам и просмотрам")
        collections = (data.get("top_collections") or [])[:8]
        if not collections:
            self._empty_state(page, "Сборок пока нет",
                              "Опубликуйте сборку — она появится в этом списке.",
                              "layers", compact=True).pack(pady=12)
        else:
            body = self._panel(page)
            for index, row in enumerate(collections, start=1):
                line = tk.Frame(body, bg=PANEL)
                line.pack(fill="x")
                tk.Label(line, text=f"{index:02d}", bg=PANEL, fg=DIM,
                         font=(FONT, CAPTION_PX), width=4, anchor="w").pack(side="left",
                                                                             padx=(14, 4),
                                                                             pady=10)
                tk.Label(line, text=row.get("title") or "сборка", bg=PANEL, fg=TEXT,
                         font=(FONT, BODY_PX), anchor="w").pack(side="left", fill="x",
                                                                expand=True, pady=10)
                tk.Label(line, text=f"установок {row.get('installs', 0)}   ·   "
                                    f"просмотров {row.get('views', 0)}   ·   "
                                    f"модов {row.get('items_count', 0)}",
                         bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX)).pack(side="right", padx=14)
                if index < len(collections):
                    tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)

        self._section(page, "Категории", "модов в каталоге")
        categories = data.get("categories") or []
        if not categories:
            self._empty_state(page, "Нет данных",
                              "Категории появятся после загрузки каталога.",
                              "box", compact=True).pack(pady=12)
            return
        body = self._panel(page, pady=(0, 16))
        for index, row in enumerate(categories[:10]):
            line = tk.Frame(body, bg=PANEL)
            line.pack(fill="x")
            key = str(row.get("category") or "")
            tk.Label(line, text=CATEGORY_LABELS.get(key, key or "Другое"), bg=PANEL, fg=TEXT,
                     font=(FONT, BODY_PX), anchor="w").pack(side="left", padx=14, pady=9)
            tk.Label(line, text=str(row.get("mods", 0)), bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, 13, "bold"), anchor="e").pack(side="right", padx=14)
            if index < len(categories[:10]) - 1:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)


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
        self._set_header_count("")
        try:
            data = self.api.get("/api/library")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(frame, "Библиотека недоступна", f"Ошибка: {exc}", "box",
                              action=self.render_library, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        items = data.get("items", [])
        duplicates = data.get("duplicates", 0)
        matched = data.get("matched", 0)
        header = f"{len(items)} {self._plural(len(items), 'файл', 'файла', 'файлов')}"
        if matched:
            header += f" · привязано к каталогу: {matched}"
        if duplicates:
            header += f" · дублей: {duplicates}"
        self._set_header_count(header)

        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 6))
        tk.Label(toolbar, text="Моды, поставленные до BeamHub, привязываются к каталогу "
                               "по названию и размеру — так для них работают обновления.",
                 bg=BG, fg=DIM, font=(FONT, CAPTION_PX), wraplength=780, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        theme.RoundButton(toolbar, "Сопоставить с каталогом", self.match_library, height=32,
                          radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG, font=(FONT, CAPTION_PX)
                          ).pack(side="right", padx=(12, 4))

        if not items:
            self._empty_state(frame, "Библиотека пуста",
                              "Пока пусто. Установите мод из каталога — он появится здесь.",
                              "box", action=lambda: self.switch("catalog"),
                              action_text="Открыть каталог").pack(pady=44)
            return
        for row in items:
            duplicate = bool(row.get("duplicate"))
            outer = theme.RoundedFrame(frame, radius=12, fill=PANEL,
                                       border=(DUP_LINE if duplicate else LINE_SOFT), padding=1)
            outer.pack(fill="x", pady=3)
            line = tk.Frame(outer.body, bg=PANEL)
            line.pack(fill="x", padx=14, pady=8)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            shown_title = row.get("catalog_title") or row.get("title") or row.get("filename")
            tk.Label(info, text=shown_title, bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            tk.Label(info, text=f"{row.get('filename')} · {row.get('size_text')} · "
                                f"{row.get('installed_text')}",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x")
            badges_needed = (duplicate or not row.get("matched")
                             or row.get("update_available"))
            if badges_needed:
                badges = tk.Frame(info, bg=PANEL)
                badges.pack(fill="x", pady=(4, 0))
                if duplicate:
                    reason = row.get("duplicate_reason") or "файл"
                    self._pill(badges, f"дубликат · {reason}", "danger").pack(
                        side="left", padx=(0, 6))
                if not row.get("matched"):
                    self._pill(badges, "не привязан", "neutral").pack(side="left",
                                                                      padx=(0, 6))
                if row.get("update_available"):
                    self._pill(badges, f"обновление {row.get('catalog_version')}",
                               "warn").pack(side="left")
            actions = tk.Frame(line, bg=PANEL)
            actions.pack(side="right", padx=(12, 0))
            theme.RoundButton(actions, "Выключить" if row.get("active") else "Включить",
                              lambda r=row: self.toggle_mod(r), height=28, radius=10,
                              bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=(0, 6))
            theme.RoundButton(actions, "Удалить", lambda r=row: self.remove_mod(r), height=28,
                              radius=10, bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT,
                              border=LINE, border_hover=DUP_LINE,
                              font=(FONT, CAPTION_PX)).pack(side="left")

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
                matched = stats.get("matched", 0)
                checked = stats.get("checked", 0)
                self.root.after(0, lambda: self.set_status(
                    f"Сопоставлено: {matched} из {checked}"))
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
        if self.view in ("collections", "settings"):
            self.refresh_all()

    def account_menu(self) -> None:
        if not self.user:
            self.open_auth()
            return
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=ACCENT,
                       activeforeground="#111318", bd=0)
        nick = (self.user or {}).get("nickname") or "аккаунт"
        menu.add_command(label=nick, state="disabled")
        menu.add_separator()
        menu.add_command(label="Мой профиль", command=self.my_profile)
        menu.add_command(label="Мои сборки", command=self._menu_my_collections)
        menu.add_command(label="Настройки", command=lambda: self.switch("settings"))
        if self.is_moderator():
            menu.add_command(label="Админ-панель", command=self.admin_panel)
        menu.add_separator()
        menu.add_command(label="Выйти", command=self.logout)
        try:
            x = self.account_button.winfo_rootx()
            y = self.account_button.winfo_rooty() + self.account_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _menu_my_collections(self) -> None:
        page = self._show_page("Мои сборки", back=self.my_profile)
        try:
            data = self.api.get("/api/collections", kind="mine", limit=60)
        except Exception as exc:  # noqa: BLE001
            self._empty_state(page, "Сборки недоступны", f"Ошибка: {exc}", "layers",
                              action=self._menu_my_collections, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        items = data.get("items", [])
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", pady=(0, 10))
        theme.RoundButton(top, "＋ Создать сборку", self.create_collection,
                          variant="primary", height=34, radius=10,
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        tk.Label(top, text=f"{len(items)} {self._plural(len(items), 'сборка', 'сборки', 'сборок')}", bg=BG, fg=MUTED, font=(FONT, BODY_PX)
                 ).pack(side="left", padx=12)
        if not items:
            self._empty_state(page, "Сборок пока нет",
                              "Создайте первую сборку — соберите моды в один набор.",
                              "layers", action=self.create_collection,
                              action_text="Создать сборку", primary=True,
                              compact=True).pack(pady=24)
            return
        for coll in items:
            outer = theme.RoundedFrame(page, radius=12, fill=PANEL, border=LINE_SOFT, padding=1)
            outer.pack(fill="x", pady=3)
            row = tk.Frame(outer.body, bg=PANEL)
            row.pack(fill="x", padx=14, pady=8)
            info = tk.Frame(row, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=coll.get("title") or "сборка", bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            rating = coll.get("rating") or {}
            state = "опубликована" if coll.get("published") else "черновик"
            tk.Label(info, text=f"{coll.get('items_count', 0)} "
                                    f"{self._plural(coll.get('items_count', 0), 'мод', 'мода', 'модов')} · ★ "
                                f"{float(rating.get('avg') or 0):.1f} "
                                f"({rating.get('count', 0)}) · {state}",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x")
            theme.RoundButton(row, "Открыть",
                              lambda c=coll["id"]: self.collection_details(c),
                              height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=(12, 0))

    def my_profile(self) -> None:
        page = self._show_page("Мой профиль")
        try:
            data = self.api.get("/api/user", id=(self.user or {}).get("id"))
        except Exception as exc:  # noqa: BLE001
            self._empty_state(page, "Профиль недоступен", f"Ошибка: {exc}", "box",
                              action=self.my_profile, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        user = data.get("user") or {}
        stats = user.get("stats") or {}
        nick = user.get("nickname") or "профиль"

        head_panel = self._panel(page, pady=(0, 8))
        head = tk.Frame(head_panel, bg=PANEL)
        head.pack(fill="x", padx=16, pady=14)
        avatar_holder = tk.Frame(head, bg=PANEL, width=64, height=64)
        avatar_holder.pack(side="left")
        avatar_holder.pack_propagate(False)
        avatar_label = tk.Label(avatar_holder, bg=PANEL, fg=ACCENT,
                                font=(FONT_BOLD, 22, "bold"),
                                text=(nick[:1] or "?").upper())
        avatar_label.pack(fill="both", expand=True)
        avatar_photo = None
        if user.get("avatar_url"):
            avatar_photo = self._local_photo(str(user["avatar_url"]), (64, 64), 16)
        if avatar_photo is None:
            avatar_photo = self._letters_photo((nick[:1] or "?").upper(),
                                               (255, 122, 26), (64, 64), 16,
                                               fill=SURFACE_2, outline=LINE)
        if avatar_photo is not None:
            avatar_label.configure(image=avatar_photo, text="")
            avatar_label.image = avatar_photo
        names = tk.Frame(head, bg=PANEL)
        names.pack(side="left", padx=14, fill="x", expand=True)
        title_line = tk.Frame(names, bg=PANEL)
        title_line.pack(fill="x")
        tk.Label(title_line, text=nick, bg=PANEL, fg=TEXT, font=(FONT_BOLD, 18, "bold"),
                 anchor="w").pack(side="left")
        role = {"admin": "администратор", "moderator": "модератор"}.get(
            user.get("role") or "", "участник")
        self._pill(title_line, role, "warn" if user.get("role") in ("admin", "moderator")
                   else "neutral").pack(side="left", padx=(10, 0))
        meta = " · ".join(filter(None, [
            user.get("email") or "аккаунт BeamHub",
            f"с {human_date(user.get('created_at'))}" if user.get("created_at") else "",
        ]))
        tk.Label(names, text=meta, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(2, 0))

        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=(0, 12))

        def change_avatar() -> None:
            picked = filedialog.askopenfilename(
                title="Фото профиля",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp")])
            if not picked:
                return
            try:
                folder = Path(config.DATA_DIR) / "avatars"
                folder.mkdir(parents=True, exist_ok=True)
                name = f"{self.user['id']}.png"
                from PIL import Image

                with Image.open(picked) as image:
                    image.convert("RGB").resize((128, 128), Image.LANCZOS).save(
                        folder / name, "PNG")
            except Exception as exc:  # noqa: BLE001
                self.toast(f"Не удалось: {exc}", "error")
                return
            result = self.api.post("/api/profile", {"avatar_url": f"avatars/{name}"})
            if not result.get("ok"):
                self.toast(result.get("error", "Не получилось"), "error")
                return
            self.toast("Фото обновлено", "ok")
            self.my_profile()

        theme.RoundButton(actions, "＋ Создать сборку", self.create_collection,
                          variant="primary", height=34, radius=10,
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        theme.RoundButton(actions, "Сменить фото", change_avatar, height=34, radius=10,
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, BODY_PX)).pack(side="left", padx=8)
        theme.RoundButton(actions, "Все сборки", self._menu_my_collections, height=34,
                          radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, BODY_PX)).pack(side="left")
        if self.is_moderator():
            theme.RoundButton(actions, "Админ-панель", self.admin_panel, height=34,
                              radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, BODY_PX)).pack(side="left", padx=8)
        theme.RoundButton(actions, "Выйти", self.logout, height=34, radius=10,
                          bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT, border=LINE,
                          border_hover=DUP_LINE,
                          font=(FONT, BODY_PX)).pack(side="right", padx=(0, 4))

        self._section(page, "Сводка", pady=(0, 6))
        self._stat_rows(page, [
            ("Сборки", stats.get("collections", 0)),
            ("Подписчики", user.get("followers", 0)),
            ("Подписки", user.get("following", 0)),
            ("Просмотры", stats.get("views", 0)),
            ("Установки", stats.get("installs", 0)),
            ("Отзывы", stats.get("comments", 0)),
            ("Оценки", stats.get("ratings", 0)),
        ])

        collections_list = user.get("collections") or []
        self._section(page, "Мои сборки", f"{len(collections_list)} шт.", pady=(8, 6))
        if not collections_list:
            self._empty_state(page, "Сборок пока нет",
                              "Соберите моды в набор — он появится здесь и в разделе «Сборки».",
                              "layers", action=self.create_collection,
                              action_text="Создать сборку", compact=True).pack(pady=18)
            return
        body = self._panel(page, pady=(0, 4))
        for index, row in enumerate(collections_list[:8]):
            line = tk.Frame(body, bg=PANEL)
            line.pack(fill="x")
            rating = row.get("rating") or {}
            tk.Label(line, text=row.get("title") or "сборка", bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(side="left",
                                                                        fill="x",
                                                                        expand=True,
                                                                        padx=14, pady=9)
            tk.Label(line, text=f"{row.get('items_count', 0)} {self._plural(row.get('items_count', 0), 'мод', 'мода', 'модов')} · "
                                f"★ {float(rating.get('avg') or 0):.1f} "
                                f"({rating.get('count', 0)}) · "
                                f"{row.get('installs', 0)} установок",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            theme.RoundButton(line, "Открыть",
                              lambda c=row.get("id"): self.collection_details(c),
                              height=28, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=10)
            if index < len(collections_list[:8]) - 1:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)


    def open_auth(self) -> None:
        if self.user:
            self.logout()
            return
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Аккаунт BeamHub")
        window.geometry("420x310")
        window.transient(self.root)

        mode = {"value": "login"}

        tk.Label(window, text="BeamHub", bg=BG, fg=ACCENT, font=(FONT_BOLD, 18, "bold"),
                 anchor="w").pack(fill="x", padx=20, pady=(16, 2))
        subtitle = tk.Label(window, text="Войдите, чтобы публиковать сборки и ставить оценки",
                            bg=BG, fg=DIM, font=(FONT, 9), anchor="w", wraplength=370,
                            justify="left")
        subtitle.pack(fill="x", padx=20, pady=(0, 10))

        fields = tk.Frame(window, bg=BG)
        fields.pack(fill="x", padx=20)

        def field(label: str, show: bool = False) -> tk.Entry:
            label_widget = tk.Label(fields, text=label, bg=BG, fg=MUTED, font=(FONT, 9),
                                    anchor="w")
            label_widget.pack(fill="x", pady=(6, 0))
            entry = tk.Entry(fields, bg=PANEL, fg=TEXT, relief="flat", insertbackground=TEXT,
                             font=(FONT, 10), show=("•" if show else ""))
            entry.pack(fill="x", ipady=5, pady=2)
            entry._label = label_widget  # type: ignore[attr-defined]
            return entry

        email = field("Email")
        password = field("Пароль", show=True)
        nickname = field("Никнейм (для регистрации)")

        status = tk.Label(window, text="", bg=BG, fg="#ffb3b3", font=(FONT, 9), anchor="w",
                          wraplength=370, justify="left")
        status.pack(fill="x", padx=20, pady=(6, 0))
        try:
            cloud_ready = bool(self.api.get("/api/auth/cloud").get("configured"))
        except Exception:  # noqa: BLE001
            cloud_ready = False
        if cloud_ready:
            cloud_actions = tk.Frame(window, bg=BG)
            cloud_actions.pack(fill="x", padx=20, pady=(8, 0))

            def cloud_submit(kind: str) -> None:
                result = self.cloud_auth(email.get().strip(), password.get(), kind)
                if not result.get("ok"):
                    status.configure(text=result.get("error", "Не получилось"))
                    return
                window.destroy()
                self.toast(f"Вход выполнен: {(self.user or {}).get('nickname')}", "ok")

            theme.RoundButton(cloud_actions, "Войти по email", lambda: cloud_submit("login"),
                              variant="primary", height=32, radius=9,
                              font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
            theme.RoundButton(cloud_actions, "Создать аккаунт", lambda: cloud_submit("signup"),
                              height=32, radius=9, font=(FONT, 9)).pack(side="left", padx=8)

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
                                          height=34, radius=9, font=(FONT_BOLD, 10, "bold"))
        submit_button.pack(side="left")
        title_button = theme.RoundButton(actions, "Создать аккаунт", toggle_mode, height=34,
                                         radius=9, font=(FONT, 9))
        title_button.pack(side="left", padx=8)
        if cloud_ready:
            nickname._label.pack_forget()  # type: ignore[attr-defined]
            nickname.pack_forget()
            actions.pack_forget()
            local_link = tk.Label(window, text="У меня локальный аккаунт без облака",
                                  bg=BG, fg=MUTED, font=(FONT, 9, "underline"),
                                  cursor="hand2", anchor="w")
            local_link.pack(fill="x", padx=20, pady=(0, 8))

            def show_local() -> None:
                local_link.pack_forget()
                cloud_actions.pack_forget()
                actions.pack(fill="x", padx=20, pady=12)

            local_link.bind("<Button-1>", lambda _e: show_local())
        tk.Label(window, text="Облачный аккаунт: email и пароль. Локальный вход работает "
                           "без облака и без интернета.",
                 bg=BG, fg=DIM, font=(FONT, 9), anchor="w", wraplength=370, justify="left"
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
        self._coll_form = None
        self._apply_user()
        self.refresh_all()

    def set_coll_kind(self, kind: str) -> None:
        self.coll_kind = kind
        self.render_collections()

    def render_collections(self) -> None:
        if getattr(self, "_page_active", False):
            self._close_page_silent()
        frame = self._scrollable_frame()
        self._set_header_count("")
        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        form_open = bool(getattr(self, "_coll_form", None))
        chips: list[tuple[str, str]] = []
        if not form_open:
            chips += [("popular", "Популярные"), ("recommended", "Для вас")]
        chips += [("new", "Новинки"), ("mine", "Мои")]
        for key, label in chips:
            if key == "mine" and not self.user:
                continue
            self._chip(toolbar, label, self.coll_kind == key,
                       lambda k=key: self.set_coll_kind(k)).pack(side="left", padx=(0, 6))
        if self.user:
            if not form_open:
                theme.RoundButton(toolbar, "＋ Создать сборку", self.create_collection,
                                  variant="primary", height=30, radius=10,
                                  font=(FONT_BOLD, BODY_PX, "bold")).pack(side="right",
                                                                             padx=(0, 6))
        else:
            theme.RoundButton(toolbar, "Войти", self.open_auth, height=30, radius=10,
                              bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=(0, 6))
        hint = ("Сборки — это подборки модов каталога: drift, offroad, JDM и другие. "
                "Смотреть можно без входа, создавать и оценивать — после входа в аккаунт.")
        tk.Label(frame, text=hint, bg=BG, fg=DIM, font=(FONT, CAPTION_PX), wraplength=820,
                 justify="left", anchor="w").pack(fill="x", pady=(0, 8))

        if getattr(self, "_coll_form", None):
            self._render_coll_form(frame)

        if self.coll_kind == "recommended":
            try:
                raw = self.api.get("/api/recommend/collections", limit=40)
            except Exception as exc:  # noqa: BLE001
                self._empty_state(frame, "Не удалось загрузить", f"Ошибка: {exc}", "layers",
                                  action=self.render_collections, action_text="Повторить",
                                  compact=True).pack(pady=30)
                return
            items = [{
                "id": row.get("id"), "title": row.get("title"),
                "cover": row.get("cover") or "",
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
                self._empty_state(frame, "Сборки недоступны", f"Ошибка: {exc}", "layers",
                                  action=self.render_collections, action_text="Повторить",
                                  compact=True).pack(pady=30)
                return
            items = data.get("items", [])
            total = data.get("total", len(items))
        self._set_header_count(f"{total} {self._plural(total, 'сборка', 'сборки', 'сборок')}")
        if not items:
            text = ("У вас пока нет сборок — создайте первую." if self.coll_kind == "mine"
                    else "Пока никто не опубликовал сборок. Будьте первым!")
            self._empty_state(frame, "Сборок пока нет", text, "layers",
                              action=self.create_collection,
                              action_text="Создать сборку",
                              primary=bool(self.user)).pack(pady=44)
            return
        for coll in items:
            self._collection_row(frame, coll)

    def _collection_row(self, parent: tk.Frame, coll: dict) -> None:
        outer = theme.RoundedFrame(parent, radius=12, fill=PANEL, border=LINE_SOFT, padding=1)
        outer.pack(fill="x", pady=3)
        row = tk.Frame(outer.body, bg=PANEL, cursor="hand2")
        row.pack(fill="x", padx=10, pady=8)
        cover = self._cover_box(row, coll, (96, 54), 10)
        cover.pack(side="left", padx=(0, 12))
        info = tk.Frame(row, bg=PANEL)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=coll.get("title") or "Без названия", bg=PANEL, fg=TEXT,
                 font=(FONT_BOLD, SECTION_PX, "bold"), anchor="w").pack(fill="x")
        rating = coll.get("rating") or {}
        meta = (f"{coll.get('owner_nickname') or 'автор'} · "
                f"{self._category_label(coll.get('category'))} · "
                f"{coll.get('items_count', 0)} {self._plural(coll.get('items_count', 0), 'мод', 'мода', 'модов')} · "
                f"★ {rating.get('avg', 0):.1f} ({rating.get('count', 0)}) · "
                f"{coll.get('installs', 0)} установок")
        tk.Label(info, text=meta, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(2, 0))
        tags = coll.get("tags") or []
        if tags:
            tk.Label(info, text=" · ".join("#" + t for t in tags[:6]), bg=PANEL,
                     fg=ACCENT_SOFT_TEXT, font=(FONT, CAPTION_PX), anchor="w").pack(
                fill="x", pady=(3, 0))
        theme.RoundButton(row, "Открыть", lambda c=coll["id"]: self.collection_details(c),
                          height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="right", padx=(12, 0))
        self._bind_click_tree(row, lambda c=coll["id"]: self.collection_details(c))

    def create_collection(self) -> None:
        if not self.user:
            self.open_auth()
            return
        self._coll_form = {"id": None, "data": {}}
        if self.view == "collections":
            self.render_collections()
        else:
            self.switch("collections")

    def _inline_form(self, frame: tk.Frame, title: str) -> tk.Frame:
        outer = theme.RoundedFrame(frame, radius=14, fill=PANEL, border=LINE_SOFT, padding=1)
        outer.pack(fill="x", pady=(0, 10))
        card = outer.body
        tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=(FONT_BOLD, SECTION_PX, "bold"),
                 anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        return card

    def _render_coll_form(self, frame: tk.Frame) -> None:
        form = getattr(self, "_coll_form", None) or {}
        coll_id = form.get("id")
        collection = form.get("data") or {}
        card = self._inline_form(frame, "Новая сборка" if not coll_id else "Редактирование сборки")
        body = tk.Frame(card, bg=PANEL)
        body.pack(fill="x", padx=14, pady=(0, 12))

        def field(label: str, value: str = "") -> tk.Entry:
            tk.Label(body, text=label, bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                     anchor="w").pack(fill="x", pady=(8, 4))
            var = tk.StringVar(value=value)
            shell, entry = self._rounded_entry(body, var, width=46)
            entry._beam_var = var  # type: ignore[attr-defined]
            shell.pack(anchor="w", pady=(0, 2))
            return entry

        title = field("Название", collection.get("title") or "")
        tk.Label(body, text="Описание", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(8, 4))
        description_shell = theme.RoundedFrame(body, radius=10, fill=SURFACE_2, border=LINE,
                                               padding=1)
        description_shell.pack(fill="x")
        description = tk.Text(description_shell.body, height=3, bg=SURFACE_2, fg=TEXT,
                              relief="flat", insertbackground=TEXT, font=(FONT, BODY_PX),
                              wrap="word", highlightthickness=0, bd=0)
        description.insert("1.0", collection.get("description") or "")
        description.pack(fill="x", padx=10, pady=6)

        labels = [label for _key, label in self.COLL_CATEGORIES]
        keys = [key for key, _label in self.COLL_CATEGORIES]
        tk.Label(body, text="Категория", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(8, 4))
        combo = ttk.Combobox(body, values=labels, state="readonly", width=30,
                             font=(FONT, BODY_PX))
        combo.set(self._category_label(collection.get("category") or "other"))
        combo.pack(anchor="w")
        tags = field("Теги через запятую", ", ".join(collection.get("tags") or []))
        cover_state = {"path": collection.get("cover") or ""}
        tk.Label(body, text="Обложка", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(8, 4))
        cover_row = tk.Frame(body, bg=PANEL)
        cover_row.pack(fill="x")
        cover_label = tk.Label(cover_row, text=self._short_path(cover_state["path"], 46)
                               if cover_state["path"] else "не выбрана",
                               bg=PANEL, fg=(TEXT if cover_state["path"] else DIM),
                               font=(FONT, CAPTION_PX), anchor="w")
        cover_label.pack(side="left")

        def choose_cover() -> None:
            picked = filedialog.askopenfilename(
                title="Обложка сборки",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp")])
            if not picked:
                return
            try:
                covers = Path(config.DATA_DIR) / "covers"
                covers.mkdir(parents=True, exist_ok=True)
                suffix = Path(picked).suffix.lower() or ".png"
                name = f"cover_{int(time.time())}{suffix}"
                shutil.copy2(picked, covers / name)
            except OSError as exc:
                cover_label.configure(text=f"не удалось: {exc}", fg=DANGER_TEXT)
                return
            cover_state["path"] = f"covers/{name}"
            cover_label.configure(text=self._short_path(cover_state["path"], 46),
                                  fg=GREEN_TEXT)

        theme.RoundButton(cover_row, "Выбрать фото", choose_cover, height=28, radius=10,
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=(10, 0))
        status = tk.Label(body, text="", bg=PANEL, fg=DANGER_TEXT, font=(FONT, CAPTION_PX),
                          anchor="w")
        status.pack(fill="x", pady=(6, 0))

        def close() -> None:
            self._coll_form = None
            self.render_collections()

        def save() -> None:
            payload = {
                "title": title.get().strip(),
                "description": description.get("1.0", "end").strip(),
                "category": keys[labels.index(combo.get())] if combo.get() in labels else "other",
                "tags": tags.get(),
                "cover": cover_state["path"],
            }
            if coll_id:
                payload["id"] = coll_id
                result = self.api.post("/api/collections/update", payload)
            else:
                result = self.api.post("/api/collections/create", payload)
            if not result.get("ok"):
                status.configure(text=result.get("error", "Не получилось"))
                return
            self.toast("Сборка сохранена", "ok")
            close()

        actions = tk.Frame(body, bg=PANEL)
        actions.pack(fill="x", pady=(12, 0))
        theme.RoundButton(actions, "Сохранить", save, variant="primary", height=32, radius=10,
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        theme.RoundButton(actions, "Отмена", close, height=32, radius=10,
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, BODY_PX)).pack(side="left", padx=8)
        title.focus_set()

    def collection_edit(self, coll_id: str | None, collection: dict | None = None) -> None:
        self._coll_form = {"id": coll_id, "data": collection or {}}
        if self.view == "collections":
            self.render_collections()
        else:
            self.switch("collections")

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

        window = self._show_page(coll.get("title") or "Сборка")
        self._page_key = coll_id
        missing_count = len(plan.get("missing") or [])
        owner = (coll.get("owner") or {}).get("nickname") or "автор"

        summary = self._panel(window, pady=(0, 6))
        head = tk.Frame(summary, bg=PANEL)
        head.pack(fill="x", padx=16, pady=12)
        cover = self._cover_box(head, coll, (128, 72), 12)
        cover.pack(side="left")
        info = tk.Frame(head, bg=PANEL)
        info.pack(side="left", fill="x", expand=True, padx=(14, 0))
        meta = (f"{owner} · {self._category_label(coll.get('category'))} · "
                f"v{coll.get('version')} · ★ {rating.get('avg', 0):.1f} "
                f"({rating.get('count', 0)}) · {coll.get('views', 0)} просмотров · "
                f"{coll.get('installs', 0)} установок")
        tk.Label(info, text=meta, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x")
        if coll.get("description"):
            tk.Label(info, text=coll.get("description"), bg=PANEL, fg=MUTED,
                     font=(FONT, BODY_PX), wraplength=900, justify="left", anchor="w").pack(
                fill="x", pady=(6, 0))
        tags = coll.get("tags") or []
        if tags:
            tk.Label(info, text=" ".join("#" + t for t in tags), bg=PANEL,
                     fg=ACCENT_SOFT_TEXT, font=(FONT, CAPTION_PX), anchor="w").pack(
                fill="x", pady=(4, 0))

        actions = tk.Frame(window, bg=BG)
        actions.pack(fill="x", pady=(0, 2))
        install_row = self._action_group(actions, "Установка")
        primary = theme.RoundButton(
            install_row,
            (f"Скачать недостающие ({missing_count})" if missing_count else "Всё установлено"),
            (lambda: self.apply_collection(coll_id)) if missing_count else None,
            variant="primary", height=32, radius=10,
            font=(FONT_BOLD, BODY_PX, "bold"))
        primary.pack(side="left")
        if not missing_count:
            primary.configure(state="disabled")
        theme.RoundButton(install_row, "Открыть все ссылки",
                          lambda: self.open_all_links(coll_id), height=32, radius=10,
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
        theme.RoundButton(install_row, "Автоустановка",
                          lambda: self.auto_install_collection(coll_id), height=32, radius=10,
                          bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left")

        manage = self._action_group(actions, "Действия")
        theme.RoundButton(manage, "Отзывы и комментарии",
                          lambda: self.collection_reviews(coll_id, coll.get("title") or ""),
                          height=32, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left")
        if coll.get("is_owner"):
            theme.RoundButton(manage, "＋ Добавить мод", lambda: self.pick_mod(coll_id),
                              height=32, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            theme.RoundButton(manage, "Редактировать",
                              lambda: self.collection_edit(coll_id, coll), height=32,
                              radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left")
            theme.RoundButton(
                manage, "Опубликовать" if not coll.get("published") else "Обновить версию",
                lambda: self.publish_collection(coll_id), height=32, radius=10,
                bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                border_hover=BORDER_STRONG,
                font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            theme.RoundButton(manage, "Удалить",
                              lambda: self.delete_collection(coll_id), height=32,
                              radius=10, bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT,
                              border=LINE, border_hover=DUP_LINE,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=(0, 4))
        else:
            theme.RoundButton(manage, "Пожаловаться",
                              lambda: self.report_dialog("collection", coll_id,
                                                         coll.get("title") or ""),
                              height=32, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
        install_hint = (f"Каждая кнопка открывает страницу одного мода — их осталось "
                        f"{missing_count}." if missing_count
                        else "Все моды сборки уже установлены.")
        tk.Label(window, text=install_hint, bg=BG, fg=(DIM if missing_count else GREEN_TEXT),
                 font=(FONT, CAPTION_PX), anchor="w").pack(fill="x", pady=(0, 4))

        self._section(window, "Состав",
                     f"{len(coll.get('items', []))} {self._plural(len(coll.get('items', [])), 'мод', 'мода', 'модов')} · установлено {len(installed)}",
                     pady=(12, 6))
        items = coll.get("items", [])
        catalog: dict = {}
        if items:
            try:
                ids = ",".join(item["mod_id"] for item in items[:120])
                data = self.api.get("/api/mods/by-ids", ids=ids)
                catalog = {row["id"]: row for row in data.get("items", [])}
            except Exception:  # noqa: BLE001
                catalog = {}
        visible = [item for item in items if item["mod_id"] in catalog] if catalog else items
        if not visible:
            empty = self._empty_state(
                window, "В сборке пока пусто",
                "Добавьте моды из каталога — у каждого будет превью и статус установки.",
                "layers",
                action=(lambda: self.pick_mod(coll_id)) if coll.get("is_owner") else None,
                action_text="Добавить мод", compact=True,
                primary=bool(coll.get("is_owner")))
            empty.pack(pady=24)
        else:
            body = self._panel(window, pady=(0, 8))
            for index, item in enumerate(visible, start=1):
                done = item["mod_id"] in installed
                mod_item = catalog.get(item["mod_id"])
                line = tk.Frame(body, bg=PANEL)
                line.pack(fill="x", padx=10, pady=6)
                tk.Label(line, text=("✓" if done else f"{index:02d}"),
                         bg=(GREEN_SOFT if done else PANEL),
                         fg=(GREEN_TEXT if done else DIM),
                         font=(FONT_BOLD, BODY_PX, "bold"), width=3,
                         anchor="center").pack(side="left", padx=(4, 10))
                thumb = self._thumb_box(line, mod_item or item, (96, 54), 10)
                thumb.pack(side="left", padx=(0, 10))
                info = tk.Frame(line, bg=PANEL)
                info.pack(side="left", fill="x", expand=True)
                tk.Label(info, text=item.get("title") or item["mod_id"], bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
                category = CATEGORY_LABELS.get(item.get("category") or "",
                                               item.get("category") or "")
                tk.Label(info, text=f"{category} · {item.get('size_text') or ''}",
                         bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x")
                tk.Label(info, text="установлен" if done else "не установлен", bg=PANEL,
                         fg=(GREEN_TEXT if done else DIM), font=(FONT, CAPTION_PX),
                         anchor="w").pack(fill="x", pady=(2, 0))
                if coll.get("is_owner"):
                    theme.RoundButton(line, "Убрать",
                                      lambda m=item["mod_id"]:
                                      self.remove_from_collection(coll_id, m),
                                      height=28, radius=10, bg=PANEL, fg=TEXT,
                                      hover=DANGER_SOFT, border=LINE,
                                      border_hover=DUP_LINE,
                                      font=(FONT, CAPTION_PX)).pack(side="right", padx=8)
                if mod_item is not None:
                    self._bind_click_tree(
                        line, lambda m=mod_item, c=coll_id: self.details(
                            m, back=lambda: self.collection_details(c)))
                if index < len(visible):
                    tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)

        history = coll.get("versions") or []
        if history:
            tk.Label(window, text="История версий: " + ", ".join(
                f"v{v['version']} ({v.get('count', 0)} {self._plural(v.get('count', 0), 'мод', 'мода', 'модов')})" for v in history[:6]),
                bg=BG, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x", pady=(6, 12))
        self._translate_ui(window)

    # ---------------------------------------------------------------- отзывы
    def collection_reviews(self, coll_id: str, title: str = "") -> None:
        page = self._show_page(f"Отзывы — {title}" if title else "Отзывы",
                               back=lambda: self.collection_details(coll_id))
        body = page
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
            head.pack(fill="x", pady=(0, 6))
            tk.Label(head, text=f"★ {summary.get('avg', 0):.1f} · "
                                f"{summary.get('count', 0)} оценок · "
                                f"{summary.get('installed_reviews', 0)} от установивших · "
                                f"{comments_data.get('count', 0)} комментариев",
                     bg=BG, fg=DIM, font=(FONT, 9), anchor="w").pack(fill="x")

            if self.user:
                box = self._panel(body, pady=(0, 10))
                tk.Label(box, text="Ваша оценка", bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, SECTION_PX, "bold"), anchor="w").pack(
                    fill="x", padx=16, pady=(10, 2))
                star_row = tk.Frame(box, bg=PANEL)
                star_row.pack(anchor="w", padx=16)
                star_buttons: list = []

                def paint(value: int) -> None:
                    for index, button in enumerate(star_buttons, start=1):
                        button.configure(text="★" if index <= value else "☆",
                                         fg=("#ffd08a" if index <= value else MUTED))

                def preview(value: int) -> None:
                    for index, button in enumerate(star_buttons, start=1):
                        button.configure(text="★" if index <= value else "☆",
                                         fg=("#ffe0a8" if index <= value else MUTED))

                for value in range(1, 6):
                    button = theme.RoundButton(
                        star_row, "★" if value <= stars_var["value"] else "☆",
                        lambda v=value: (stars_var.update({"value": v}), paint(v)),
                        width=36, height=34, radius=10, font=(FONT, 13), bg=PANEL,
                        fg="#ffd08a", hover=ACTIVE_BG)
                    button.pack(side="left", padx=2)
                    button.bind("<Enter>", lambda _e, v=value: preview(v), add="+")
                    button.bind("<Leave>", lambda _e: paint(stars_var["value"]), add="+")
                    star_buttons.append(button)
                paint(stars_var["value"])

                tk.Label(box, text="Ваш отзыв (необязательно)", bg=PANEL, fg=MUTED,
                         font=(FONT, CAPTION_PX), anchor="w").pack(fill="x", padx=16,
                                                                  pady=(8, 4))
                review_shell = theme.RoundedFrame(box, radius=10, fill=SURFACE_2, border=LINE,
                                                  padding=1)
                review_shell.pack(fill="x", padx=16, pady=(0, 4))
                review_text = tk.Text(review_shell.body, height=3, bg=SURFACE_2, fg=TEXT,
                                      relief="flat", insertbackground=TEXT,
                                      font=(FONT, BODY_PX), wrap="word",
                                      highlightthickness=0, bd=0)
                review_text.insert("1.0", mine.get("review") or "")
                review_text.pack(fill="x", padx=10, pady=6)

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
                actions.pack(fill="x", padx=16, pady=(6, 12))
                theme.RoundButton(actions, "Сохранить оценку", save_rating, variant="primary",
                                  height=32, radius=10,
                                  font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
                theme.RoundButton(actions, "Удалить оценку", clear_rating, height=32,
                                  radius=10, bg=PANEL, fg=MUTED, hover=DANGER_SOFT,
                                  border=LINE, border_hover=DUP_LINE,
                                  font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            else:
                row = tk.Frame(body, bg=BG)
                row.pack(fill="x", pady=6)
                tk.Label(row, text="Войдите, чтобы оценивать и писать отзывы", bg=BG, fg=DIM,
                         font=(FONT, BODY_PX)).pack(side="left")
                theme.RoundButton(row, "Войти", self.open_auth, variant="primary", height=30,
                                  radius=10,
                                  font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left", padx=8)

            reviews = [r for r in reviews_data.get("items", []) if r.get("review")]
            self._section(body, "Все отзывы", f"{len(reviews)} шт." if reviews else "",
                          pady=(16, 6))
            if not reviews:
                self._empty_state(body, "Отзывов пока нет",
                                  "Поставьте оценку и напишите пару слов — это помогает автору.",
                                  "box", compact=True).pack(pady=16)
            review_body = self._panel(body, pady=(0, 4)) if reviews else None
            for index, review in enumerate(reviews[:12]):
                line = tk.Frame(review_body, bg=PANEL)
                line.pack(fill="x", padx=12, pady=8)
                head_line = tk.Frame(line, bg=PANEL)
                head_line.pack(fill="x")
                tk.Label(head_line, text="★" * review["stars"], bg=PANEL, fg="#ffd08a",
                         font=(FONT, CAPTION_PX)).pack(side="left")
                tk.Label(head_line, text=review["nickname"], bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left", padx=6)
                if review.get("installed"):
                    self._pill(head_line, "✓ есть в библиотеке", "ok").pack(side="left", padx=(2, 0))
                tk.Label(line, text=review["review"], bg=PANEL, fg=MUTED,
                         font=(FONT, BODY_PX), wraplength=980, justify="left",
                         anchor="w").pack(fill="x", pady=(4, 0))
                if index < len(reviews[:12]) - 1:
                    tk.Frame(review_body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)

            comments = comments_data.get("items") or []
            self._section(body, "Обсуждение", f"{len(comments)} сообщений",
                          pady=(16, 6))
            reply_state = {"parent": ""}
            composer = theme.RoundedFrame(body, radius=12, fill=PANEL, border=LINE_SOFT,
                                          padding=1)
            composer.pack(fill="x", pady=(0, 10))
            inner = composer.body
            reply_hint = tk.Label(inner, text="", bg=PANEL, fg=ACCENT_SOFT_TEXT,
                                  font=(FONT, CAPTION_PX), anchor="w")
            reply_hint.pack(fill="x", padx=10, pady=(8, 0))
            field = tk.Text(inner, height=2, bg=SURFACE_2, fg=TEXT, insertbackground=TEXT,
                            bd=0, wrap="word", font=(FONT, BODY_PX), padx=10, pady=8)
            field.pack(fill="x", padx=10, pady=(6, 4))
            composer_row = tk.Frame(inner, bg=PANEL)
            composer_row.pack(fill="x", padx=10, pady=(0, 8))

            def send_comment() -> None:
                text_value = field.get("1.0", "end").strip()
                if not text_value:
                    self.toast("Напишите сообщение", "error")
                    return
                if not self.user:
                    self.toast("Войдите, чтобы писать", "error")
                    return
                result = self.api.post("/api/comments", {
                    "target_type": "collection", "target_id": coll_id,
                    "body": text_value, "parent_id": reply_state["parent"]})
                if not result.get("ok"):
                    self.toast(result.get("error", "Не получилось отправить"), "error")
                    return
                reply_state["parent"] = ""
                render()

            def cancel_reply() -> None:
                reply_state["parent"] = ""
                reply_hint.configure(text="")

            theme.RoundButton(composer_row, "Отправить", send_comment, variant="primary",
                              height=30, radius=9,
                              font=(FONT_BOLD, CAPTION_PX)).pack(side="left")
            theme.RoundButton(composer_row, "Отмена", cancel_reply, height=30, radius=9,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            if not self.user:
                tk.Label(composer_row, text="Войдите, чтобы писать", bg=PANEL, fg=DIM,
                         font=(FONT, CAPTION_PX)).pack(side="left", padx=8)

            def comment_block(parent: tk.Misc, comment: dict, depth: int = 0) -> None:
                wrap = tk.Frame(parent, bg=BG)
                wrap.pack(fill="x", padx=(depth * 22, 0), pady=3)
                outer = theme.RoundedFrame(wrap, radius=12, fill=PANEL, border=LINE_SOFT,
                                           padding=1)
                outer.pack(fill="x")
                line = outer.body
                top = tk.Frame(line, bg=PANEL)
                top.pack(fill="x", padx=12, pady=(8, 0))
                author = comment.get("author") or {}
                tk.Label(top, text=author.get("nickname") or "?", bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
                tk.Label(top, text=" · " + human_ago(comment.get("created_at")), bg=PANEL,
                         fg=DIM, font=(FONT, CAPTION_PX)).pack(side="left")
                if comment.get("deleted"):
                    tk.Label(line, text="Комментарий удалён", bg=PANEL, fg=DIM,
                             font=(FONT, CAPTION_PX), anchor="w").pack(
                        fill="x", padx=12, pady=(2, 8))
                    return
                tk.Label(line, text=comment.get("body"), bg=PANEL, fg=TEXT,
                         font=(FONT, BODY_PX), wraplength=980, justify="left",
                         anchor="w").pack(fill="x", padx=12, pady=(2, 4))
                if not comment.get("deleted") and self.user:
                    reply_row = tk.Frame(line, bg=PANEL)
                    reply_row.pack(fill="x", padx=12, pady=(0, 8))
                    theme.RoundButton(
                        reply_row, "Ответить",
                        lambda c=comment: (
                            reply_state.__setitem__("parent", c["id"]),
                            reply_hint.configure(
                                text=f"Ответ на {((c.get('author') or {}).get('nickname') or '?')}"),
                            field.focus_set()),
                        height=28, radius=10, bg=PANEL, fg=MUTED,
                        hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG,
                        font=(FONT, CAPTION_PX)).pack(side="left")
                if comment.get("is_mine") or comment.get("can_moderate"):
                    tools = tk.Frame(line, bg=PANEL)
                    tools.pack(fill="x", padx=12, pady=(0, 8))
                    theme.RoundButton(
                        tools, "Удалить",
                        lambda c=comment: (self.api.post(
                            "/api/comments/delete", {"id": c["id"]}), render())[-1],
                        height=28, radius=10, bg=PANEL, fg=DANGER_TEXT,
                        hover=DANGER_SOFT, border=LINE, border_hover=DUP_LINE,
                        font=(FONT, CAPTION_PX)).pack(side="left")
                for reply in comment.get("replies") or []:
                    comment_block(parent, reply, depth + 1)

            for comment in comments:
                comment_block(body, comment)
            self._translate_ui(page)

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
        tk.Label(window, text="На что жалуетесь?", bg=BG, fg=TEXT, font=(FONT_BOLD, 11, "bold"),
                 anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(window, text="Жалоба попадёт модераторам — раздел «Модерация» "
                              "в настройках аккаунта.",
                 bg=BG, fg=DIM, font=(FONT, CAPTION_PX), anchor="w",
                 wraplength=420, justify="left").pack(fill="x", padx=18, pady=(0, 4))
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
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        theme.RoundButton(buttons, "Отмена", window.destroy, height=30, radius=8,
                          font=(FONT, 9)).pack(side="left", padx=8)
        self._translate_ui(window)

    def admin_panel(self) -> None:
        if not self.is_moderator():
            self.toast("Нужны права администратора", "error")
            return
        page = self._show_page("Админ-панель")
        try:
            data = self.api.get("/api/admin/stats")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(page, "Админ-панель недоступна", f"Ошибка: {exc}", "box",
                              action=self.admin_panel, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        if not data.get("ok"):
            self._empty_state(page, "Нет доступа", data.get("error", "Нужны права администратора"),
                              "box", compact=True).pack(pady=30)
            return
        counts = data.get("counts") or {}
        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=(0, 8))
        reports_open = int(counts.get("reports_open", 0) or 0)
        theme.RoundButton(
            actions, f"Модерация и жалобы ({reports_open})", self.moderation_panel,
            variant=("primary" if reports_open else "ghost"), height=34, radius=10,
            font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        self._section(page, "Сводка", pady=(0, 6))
        self._stat_rows(page, [
            ("Модов в каталоге", counts.get("mods", 0)),
            ("Установлено", counts.get("installed", 0)),
            ("Избранное", counts.get("favorites", 0)),
            ("Обновления", counts.get("updates", 0)),
            ("Дубликаты", counts.get("duplicates", 0)),
            ("Пользователи", counts.get("users", 0)),
            ("Сборки", counts.get("collections", 0)),
            ("Опубликовано", counts.get("published", 0)),
            ("Комментарии", counts.get("comments", 0)),
            ("Оценки", counts.get("ratings", 0)),
            ("Жалобы", counts.get("reports_open", 0)),
        ])

        size_mb = round(int(data.get("db_bytes") or 0) / 1024 / 1024, 2)
        hours = int(data.get("uptime_seconds") or 0) // 3600
        minutes = (int(data.get("uptime_seconds") or 0) % 3600) // 60
        paths = data.get("paths") or {}
        self._section(page, "Система", pady=(8, 6))
        self._kv_rows(page, [
            ("Версия", str(data.get("version") or "—")),
            ("База", f"{size_mb} МБ"),
            ("Работает", f"{hours} ч {minutes} мин"),
            ("Моды", paths.get("mods_dir") or "—"),
            ("Игра", paths.get("game_path") or "—"),
            ("Данные", paths.get("data_dir") or "—"),
        ])

        events = data.get("events") or []
        self._section(page, "События", f"{len(events)} типов", pady=(8, 6))
        if events:
            events_body = self._panel(page, pady=(0, 4))
            for index, row in enumerate(events):
                line = tk.Frame(events_body, bg=PANEL)
                line.pack(fill="x")
                tk.Label(line, text=str(row.get("kind") or "—"), bg=PANEL, fg=MUTED,
                         font=(FONT, BODY_PX), anchor="w").pack(side="left", padx=14, pady=8)
                tk.Label(line, text=str(row.get("n", 0)), bg=PANEL, fg=TEXT,
                         font=(FONT_BOLD, SECTION_PX, "bold"), anchor="e").pack(side="right",
                                                                             padx=14)
                if index < len(events) - 1:
                    tk.Frame(events_body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        else:
            self._empty_state(page, "Событий пока нет",
                              "Здесь появятся установки, оценки и жалобы.",
                              "box", compact=True).pack(pady=16)

        recent = data.get("recent") or []
        self._section(page, "Последние действия", f"{len(recent)} записей", pady=(8, 6))
        if not recent:
            self._empty_state(page, "Пока пусто", "Действия модераторов появятся здесь.",
                              "box", compact=True).pack(pady=16)
            return
        recent_body = self._panel(page, pady=(0, 16))
        for index, row in enumerate(recent[:10]):
            line = tk.Frame(recent_body, bg=PANEL)
            line.pack(fill="x")
            target = row.get("mod_id") or row.get("collection_id") or ""
            tk.Label(line, text=human_ago(row.get("created_at")), bg=PANEL, fg=DIM,
                     font=(FONT, CAPTION_PX), width=14, anchor="w").pack(side="left", padx=14,
                                                                        pady=8)
            tk.Label(line, text=f"{row.get('kind') or '—'}  {target}", bg=PANEL, fg=TEXT,
                     font=(FONT, BODY_PX), anchor="w").pack(side="left", fill="x",
                                                            expand=True, pady=8)
            if index < len(recent[:10]) - 1:
                tk.Frame(recent_body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)

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
            tk.Label(inner, text="Модерация", bg=BG, fg=TEXT,
                     font=(FONT_BOLD, TITLE_PX, "bold"), anchor="w").pack(
                fill="x", padx=18, pady=(16, 2))
            tk.Label(inner, text=f"Открытых жалоб: {overview.get('open_reports', 0)} · "
                                 f"заблокировано: {overview.get('blocked_users', 0)} · "
                                 f"модераторов: {overview.get('moderators', 0)} · "
                                 f"действий за сутки: {overview.get('actions_24h', 0)}",
                     bg=BG, fg=MUTED, font=(FONT, CAPTION_PX), anchor="w").pack(
                fill="x", padx=18, pady=(0, 8))

            tk.Label(inner, text="Очередь жалоб", bg=BG, fg=TEXT,
                     font=(FONT_BOLD, SECTION_PX, "bold"),
                     anchor="w").pack(fill="x", padx=18)
            if not reports.get("items"):
                self._empty_state(inner, "Жалоб нет", "Очередь пуста — всё спокойно.",
                                  "box", compact=True).pack(pady=12)
            for report in reports.get("items", []):
                outer = theme.RoundedFrame(inner, radius=12, fill=PANEL, border=LINE_SOFT,
                                           padding=1)
                outer.pack(fill="x", padx=18, pady=3)
                card = tk.Frame(outer.body, bg=PANEL)
                card.pack(fill="x", padx=12, pady=8)
                top = tk.Frame(card, bg=PANEL)
                top.pack(fill="x")
                tk.Label(top, text=f"{report.get('target_type')} · {report.get('reason')}",
                         bg=PANEL, fg=WARN_TEXT, font=(FONT_BOLD, BODY_PX, "bold")).pack(
                    side="left")
                tk.Label(top, text=f" · от {report.get('reporter_nickname') or '?'}", bg=PANEL,
                         fg=DIM, font=(FONT, CAPTION_PX)).pack(side="left")
                tk.Label(card, text=(report.get("preview") or "(без текста)")[:140], bg=PANEL,
                         fg=TEXT, font=(FONT, BODY_PX), wraplength=760, justify="left",
                         anchor="w", height=1).pack(fill="x", pady=(3, 4))
                target_user = report.get("target_user") or {}
                tools = tk.Frame(card, bg=PANEL)
                tools.pack(fill="x")
                theme.RoundButton(
                    tools, "Удалить и закрыть",
                    lambda r=report: resolve(r["id"], "resolve_delete"),
                    variant="primary", height=28, radius=10,
                    font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
                theme.RoundButton(tools, "Отклонить",
                                  lambda r=report: resolve(r["id"], "reject"),
                                  height=28, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                                  border=LINE, border_hover=BORDER_STRONG,
                                  font=(FONT, CAPTION_PX)).pack(side="left", padx=6)
                if target_user.get("id") and not target_user.get("blocked"):
                    theme.RoundButton(
                        tools, f"Блокировать {target_user.get('nickname')}",
                        lambda u=target_user["id"]: block(u),
                        height=28, radius=10, bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT,
                        border=LINE, border_hover=DUP_LINE,
                        font=(FONT, CAPTION_PX)).pack(side="left")

            tk.Label(inner, text="Журнал действий", bg=BG, fg=TEXT,
                     font=(FONT_BOLD, SECTION_PX, "bold"),
                     anchor="w").pack(fill="x", padx=18, pady=(14, 4))
            for entry in log.get("items", [])[:25]:
                tk.Label(inner,
                         text=f"{entry.get('actor_nickname') or '?'} · {entry.get('action')} · "
                              f"{entry.get('target_type')} {entry.get('target_id')} · "
                              f"{human_ago(entry.get('created_at'))}",
                         bg=BG, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(
                    fill="x", padx=18, pady=1)
            self._translate_ui(window)

        render()

    # ---------------------------------------------------------------- облако
    def cloud_auth(self, email: str, password: str, mode: str) -> dict:
        try:
            result = self.api.post("/api/auth/email",
                                   {"email": email, "password": password, "mode": mode})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Ошибка: {exc}"}
        if not result.get("ok"):
            return result
        self.api.user_token = result["token"]
        self.user = result.get("user")
        try:
            self.api.post("/api/settings", {"user_token": result["token"]})
        except Exception:  # noqa: BLE001
            pass
        self._apply_user()
        self.refresh_all()
        self.sync_pull()
        return result

    def email_auth(self, mode: str) -> None:
        email = (self._cloud_email_var.get() if hasattr(self, "_cloud_email_var") else "").strip()
        password = (self._cloud_pass_var.get() if hasattr(self, "_cloud_pass_var") else "")
        if not email or not password:
            self.toast("Введите email и пароль", "error")
            return
        result = self.cloud_auth(email, password, mode)
        if not result.get("ok"):
            self.toast(result.get("error", "Не получилось войти"), "error")
            return
        self.toast(f"Вход выполнен: {(self.user or {}).get('nickname')}", "ok")

    def google_login(self, window: tk.Misc | None = None) -> None:
        self.oauth_login("google", window)

    def oauth_login(self, provider: str, window: tk.Misc | None = None) -> None:
        try:
            status = self.api.get("/api/auth/cloud")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Ошибка: {exc}", "error")
            return
        if not status.get("configured"):
            self.toast("Сначала укажите адрес Supabase и anon key", "error")
            return
        result = self.api.post("/api/auth/oauth/start", {"provider": provider})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось начать вход"), "error")
            return
        state = result.get("state", "")
        titles = {"google": "Google", "discord": "Discord"}
        self.toast(f"Открыл браузер — выберите аккаунт {titles.get(provider, provider)}",
                   "ok")
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
            text = (f"Отправлено сборок: {result.get('pushed')} из {result.get('total')} · "
                    f"комментариев {result.get('comments', 0)}, "
                    f"оценок {result.get('ratings', 0)}, "
                    f"жалоб {result.get('reports', 0)}")
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
            message = (f"Получено сборок: {result.get('pulled', 0)} · "
                       f"комментариев {result.get('comments', 0)}, "
                       f"оценок {result.get('ratings', 0)}, "
                       f"жалоб {result.get('reports', 0)}")
            self.root.after(0, lambda m=message: self.toast(m, "ok"))
            self.root.after(0, self.refresh_all)
        threading.Thread(target=work, daemon=True).start()

    def cloud_block(self, frame: tk.Frame, settings: dict) -> None:
        block = self._panel(frame, pady=(0, 4))
        baked = False
        try:
            baked = cloud_defaults.baked() and not (settings.get("cloud_url")
                                                    and settings.get("cloud_anon_key"))
        except Exception:  # noqa: BLE001
            baked = False
        configured = cloud.configured()
        top = tk.Frame(block, bg=PANEL)
        top.pack(fill="x", padx=14, pady=(10, 4))
        texts = tk.Frame(top, bg=PANEL)
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, text="Синхронизация с облаком", bg=PANEL, fg=TEXT,
                 font=(FONT, BODY_PX), anchor="w").pack(fill="x")
        note = ("Сборки и профиль синхронизируются между устройствами."
                if configured else "Не подключено — аккаунты и сборки пока только локальные.")
        tk.Label(texts, text=note, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w",
                 wraplength=820, justify="left").pack(fill="x", pady=(2, 0))
        self._pill(top, "подключено" if configured else "не подключено",
                   "ok" if configured else "neutral").pack(side="right", padx=(12, 0))
        if baked:
            tk.Label(block, text=f"Адрес задан владельцем: {cloud_defaults.describe()}",
                     bg=PANEL, fg=GREEN_TEXT, font=(FONT, CAPTION_PX), anchor="w").pack(
                fill="x", padx=14, pady=(0, 6))

        row = tk.Frame(block, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(0, 0))
        tk.Label(row, text="Project URL", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(6, 4))
        url_var = tk.StringVar(value=settings.get("cloud_url") or "")
        url_shell, _url_entry = self._rounded_entry(row, url_var, width=48)
        url_shell.pack(anchor="w")
        key_row = tk.Frame(block, bg=PANEL)
        key_row.pack(fill="x", padx=14)
        tk.Label(key_row, text="anon key", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(6, 4))
        key_var = tk.StringVar(value=settings.get("cloud_anon_key") or "")
        key_shell, _key_entry = self._rounded_entry(key_row, key_var, width=48, show="•")
        key_shell.pack(anchor="w")
        if baked:
            row.pack_forget()
            key_row.pack_forget()
        buttons = tk.Frame(block, bg=PANEL)
        buttons.pack(fill="x", padx=14, pady=(8, 4))

        def save_cloud() -> None:
            result = self.api.post("/api/cloud/settings", {
                "url": url_var.get().strip(), "anon_key": key_var.get().strip()})
            if not result.get("ok"):
                self.toast(result.get("error", "Не сохранилось"), "error")
                return
            self.toast("Настройки облака сохранены" if result.get("configured")
                       else "Сохранено, но облако не настроено", "ok")
            self.refresh_all()

        if not baked:
            theme.RoundButton(buttons, "Сохранить облако", save_cloud, height=30, radius=10,
                              bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left")

        logged = bool(self.user)
        if logged:
            tk.Frame(block, bg=LINE_SOFT, height=1).pack(fill="x", padx=12, pady=(6, 0))
            status_row = tk.Frame(block, bg=PANEL)
            status_row.pack(fill="x", padx=14, pady=(8, 1))
            self._pill(status_row, f"вы вошли как {(self.user or {}).get('nickname')}",
                       "ok").pack(side="left")
            sync_row = tk.Frame(block, bg=PANEL)
            sync_row.pack(fill="x", padx=14, pady=(6, 10))
            theme.RoundButton(sync_row, "Отправить сборки в облако", self.sync_push,
                              height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left")
            theme.RoundButton(sync_row, "Загрузить из облака", self.sync_pull, height=30,
                              radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
            theme.RoundButton(sync_row, "Выйти", self.logout, height=30, radius=10,
                              bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT, border=LINE,
                              border_hover=DUP_LINE,
                              font=(FONT, CAPTION_PX)).pack(side="right")
            return

        tk.Frame(block, bg=LINE_SOFT, height=1).pack(fill="x", padx=12, pady=(6, 0))
        self._cloud_email_var = tk.StringVar()
        self._cloud_pass_var = tk.StringVar()
        email_row = tk.Frame(block, bg=PANEL)
        email_row.pack(fill="x", padx=14)
        tk.Label(email_row, text="email", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(8, 4))
        email_shell, _email_entry = self._rounded_entry(email_row, self._cloud_email_var,
                                                        width=40)
        email_shell.pack(anchor="w")
        pass_row = tk.Frame(block, bg=PANEL)
        pass_row.pack(fill="x", padx=14)
        tk.Label(pass_row, text="пароль", bg=PANEL, fg=MUTED, font=(FONT, CAPTION_PX),
                 anchor="w").pack(fill="x", pady=(6, 4))
        pass_shell, _pass_entry = self._rounded_entry(pass_row, self._cloud_pass_var,
                                                      width=40, show="•")
        pass_shell.pack(anchor="w")
        mail_buttons = tk.Frame(block, bg=PANEL)
        mail_buttons.pack(fill="x", padx=14, pady=(8, 10))
        theme.RoundButton(mail_buttons, "Войти по email", lambda: self.email_auth("login"),
                          height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left")
        theme.RoundButton(mail_buttons, "Создать аккаунт", lambda: self.email_auth("signup"),
                          height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
        theme.RoundButton(mail_buttons, "Отправить сборки в облако", self.sync_push,
                          height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                          border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=(8, 0))
        theme.RoundButton(mail_buttons, "Загрузить из облака", self.sync_pull, height=30,
                          radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
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

    def refresh_collection_later(self, coll_id: str) -> None:
        """Перерисовать сборку через 4 с, но только если её ещё смотрят."""
        def check() -> None:
            if (getattr(self, "_page_active", False)
                    and getattr(self, "_page_key", None) == coll_id):
                self.collection_details(coll_id)

        self.root.after(4000, check)

    def open_all_links(self, coll_id: str) -> None:
        result = self.api.post("/api/collections/open-all", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось открыть ссылки"), "error")
            return
        self.toast(result.get("message", "Открыл ссылки"), "ok")

    def auto_install_collection(self, coll_id: str) -> None:
        result = self.api.post("/api/collections/install", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось начать автоустановку"), "error")
            return
        self.toast(result.get("message", "Скачиваю сборку…"), "ok")
        self.set_status(result.get("message", "Скачиваю сборку…"))
        self.refresh_collection_later(coll_id)

    def apply_collection(self, coll_id: str, window: tk.Misc | None = None) -> None:
        result = self.api.post("/api/collections/sequence", {"id": coll_id})
        if not result.get("ok"):
            self.toast(result.get("error", "Не удалось начать установку"), "error")
            return
        self.toast(result.get("message", "Скачиваю сборку…"), "ok")
        self.set_status(result.get("message", "Скачиваю сборку…"))
        self.refresh_collection_later(coll_id)

    def _pick_categories(self) -> list[dict]:
        """Категории каталога с количеством — для подбора мода в сборку."""
        try:
            state = self.api.get("/api/state")
            rows = [row for row in state.get("categories", []) if row.get("count")]
            return rows or [{"key": "cars", "label": CATEGORY_LABELS.get("cars", "Машины"),
                             "count": 0}]
        except Exception:  # noqa: BLE001
            return []

    def pick_mod(self, coll_id: str, window: tk.Misc | None = None) -> None:
        page = self._show_page("Добавить мод в сборку",
                               back=lambda: self.collection_details(coll_id))
        state: dict = {"cat": "", "q": "", "offset": 0, "total": 0, "items": [],
                       "body": None, "more": None}
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x", pady=(0, 8))

        def search_now() -> None:
            state["q"] = search_var.get().strip()
            draw(True)

        search_shell, search_entry, search_var = self._search_box(
            top, "Название мода: supra, карта, drift…", search_now, width=460)
        search_shell.pack(side="left")
        theme.RoundButton(top, "Найти", search_now, height=38, radius=10, bg=PANEL, fg=TEXT,
                          hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
        found_label = tk.Label(top, text="", bg=BG, fg=DIM, font=(FONT, CAPTION_PX))
        found_label.pack(side="right", padx=(0, 4))

        chips = tk.Frame(page, bg=BG)
        chips.pack(fill="x", pady=(0, 8))
        grid = tk.Frame(page, bg=BG)
        grid.pack(fill="both", expand=True)

        def set_cat(cat: str) -> None:
            state["cat"] = cat
            draw(True)

        def draw_chips() -> None:
            for child in chips.winfo_children():
                child.destroy()
            options = [("", "Все")] + [(row["key"], f"{row['label']} ({row['count']})")
                                       for row in self._pick_categories()]
            for key, label in options:
                self._chip(chips, label, state["cat"] == key,
                           lambda k=key: set_cat(k)).pack(side="left", padx=(0, 6))

        def ensure_body() -> tk.Frame:
            body = state.get("body")
            if body is None or not body.winfo_exists():
                body = self._panel(grid, pady=(0, 4), border=LINE_SOFT)
                state["body"] = body
            return body

        def add_row(mod: dict) -> None:
            body = ensure_body()
            line = tk.Frame(body, bg=PANEL)
            line.pack(fill="x", padx=10, pady=5)
            thumb = self._thumb_box(line, mod, (96, 54), 10)
            thumb.pack(side="left", padx=(0, 10))
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=mod.get("title") or mod["id"], bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            meta = " · ".join(filter(None, [
                CATEGORY_LABELS.get(mod.get("category") or "", mod.get("category") or ""),
                mod.get("size_text") or ""]))
            tk.Label(info, text=meta, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                     anchor="w").pack(fill="x")
            theme.RoundButton(line, "＋ Добавить",
                              lambda m=mod: self._add_to_collection_page(coll_id, m),
                              height=28, radius=10, bg=SURFACE_2, fg=ACCENT_SOFT_TEXT,
                              hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=8)
            self._bind_click_tree(line, lambda m=mod: self.details(m))
            if state["items"]:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
            state["items"].append(mod)

        def draw(reset: bool = True) -> None:
            draw_chips()
            if reset:
                for child in grid.winfo_children():
                    child.destroy()
                state["body"] = None
                state["more"] = None
                state["offset"] = 0
                state["items"] = []
            else:
                more = state.get("more")
                if more is not None and more.winfo_exists():
                    more.destroy()
                state["more"] = None
            params = {"limit": 60, "offset": state["offset"]}
            if state["q"]:
                params["q"] = state["q"]
            if state["cat"]:
                params["cat"] = state["cat"]
            try:
                data = self.api.get("/api/mods", **params)
            except Exception as exc:  # noqa: BLE001
                self._empty_state(grid, "Каталог недоступен", f"Ошибка: {exc}", "box",
                                  action=lambda: draw(True), action_text="Повторить",
                                  compact=True).pack(pady=24)
                return
            items = data.get("items", [])
            state["total"] = data.get("total", state["total"])
            state["offset"] += len(items)
            if not items and reset:
                empty = self._empty_state(
                    grid, "Ничего не найдено",
                    "Попробуйте другой запрос или категорию — или сбросьте фильтры.",
                    "search", action=lambda: reset_pick(), action_text="Сбросить поиск",
                    compact=True)
                empty.pack(pady=24)
            for mod in items:
                add_row(mod)
            found_label.configure(text=f"показано {len(state['items'])} из {state['total']}")
            if state["offset"] < state["total"]:
                more = tk.Frame(grid, bg=BG)
                more.pack(fill="x", pady=10)
                state["more"] = more
                theme.RoundButton(
                    more,
                    f"Показать ещё ({state['total'] - state['offset']} осталось)",
                    lambda: draw(False), height=32, radius=10, bg=PANEL, fg=TEXT,
                    hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG,
                    font=(FONT, CAPTION_PX, "bold")).pack()

        def reset_pick() -> None:
            state["q"] = ""
            state["cat"] = ""
            try:
                search_var.set("")
            except Exception:  # noqa: BLE001
                pass
            draw(True)

        draw(True)

    def pick_collection_for_mod(self, mod: dict) -> None:
        """Выбрать свою сборку для мода со страницы мода (иначе кнопка пустая)."""
        if not self.user:
            self.open_auth()
            return
        try:
            data = self.api.get("/api/collections", kind="mine", limit=60)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Ошибка: {exc}", "error")
            return
        mine = data.get("items") or []
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Добавить в сборку")
        window.geometry("500x460")
        window.transient(self.root)
        tk.Label(window, text="Добавить в сборку", bg=BG, fg=TEXT,
                 font=(FONT_BOLD, 18, "bold"), anchor="w").pack(
            fill="x", padx=18, pady=(16, 2))
        tk.Label(window, text=self._short_title(mod.get("title") or "мод", 60), bg=BG,
                 fg=MUTED, font=(FONT, BODY_PX), anchor="w", wraplength=440,
                 justify="left").pack(fill="x", padx=18, pady=(0, 8))
        if not mine:
            self._empty_state(
                window, "Сборок пока нет",
                "Создайте первую сборку — мод добавится в неё одним нажатием.",
                "layers", action=lambda: (window.destroy(), self.create_collection()),
                action_text="Создать сборку", compact=True).pack(pady=20)
            theme.RoundButton(window, "Закрыть", window.destroy, height=30, radius=10,
                              bg=PANEL, fg=MUTED, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(pady=8)
            self._translate_ui(window)
            return
        outer = theme.RoundedFrame(window, radius=12, fill=PANEL, border=LINE_SOFT, padding=1)
        outer.pack(fill="x", padx=18, pady=(0, 8))
        body = outer.body
        for index, coll in enumerate(mine[:6]):
            line = tk.Frame(body, bg=PANEL)
            line.pack(fill="x", padx=12, pady=7)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=coll.get("title") or "сборка", bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            rating = coll.get("rating") or {}
            state = "опубликована" if coll.get("published") else "черновик"
            tk.Label(info, text=f"{coll.get('items_count', 0)} "
                                f"{self._plural(coll.get('items_count', 0), 'мод', 'мода', 'модов')}"
                                f" · ★ {float(rating.get('avg') or 0):.1f} · {state}",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x")
            theme.RoundButton(line, "Добавить",
                              lambda c=coll["id"]: self._add_to_collection(c, mod, window),
                              height=28, radius=10, bg=SURFACE_2, fg=ACCENT_SOFT_TEXT,
                              hover=SURFACE_3, border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="right", padx=(10, 0))
            if index < len(mine[:6]) - 1:
                tk.Frame(body, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        theme.RoundButton(window, "Закрыть", window.destroy, height=30, radius=10,
                          bg=PANEL, fg=MUTED, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(pady=6)
        self._translate_ui(window)

    def _add_to_collection_page(self, coll_id: str, mod: dict) -> None:
        result = self.api.post("/api/collections/items",
                               {"id": coll_id, "mod_id": mod["id"], "action": "add"})
        if not result.get("ok"):
            self.toast(result.get("error", "Не получилось"), "error")
            return
        self.toast(f"Добавлено: {mod.get('title') or mod['id']}", "ok")

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
        frame = getattr(self, "_cards_frame", None)
        if frame is None:
            self.toast("Сначала откройте каталог", "error")
            return
        for child in frame.winfo_children():
            if getattr(child, "_is_add_bar", False):
                child.destroy()
        shell = theme.RoundedFrame(frame, radius=14, fill=PANEL, border=LINE_SOFT, padding=1)
        shell._is_add_bar = True  # type: ignore[attr-defined]
        children = [child for child in frame.winfo_children() if child is not shell]
        if children:
            shell.pack(fill="x", pady=(0, 8), before=children[0])
        else:
            shell.pack(fill="x", pady=(0, 8))
        bar = shell.body
        tk.Label(bar, text=f"Добавить «{mod.get('title') or ''}» в сборку:",
                 bg=PANEL, fg=TEXT, font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(
            fill="x", padx=14, pady=(10, 2))
        row = tk.Frame(bar, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(0, 10))
        mine = data.get("items", [])
        if not mine:
            tk.Label(row, text="Сборок пока нет — создайте первую в разделе «Сборки».",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(side="left")
        for coll in mine[:6]:
            theme.RoundButton(row, coll.get("title") or "сборка",
                              lambda c=coll["id"]: self._add_to_collection_inline(c, mod, shell),
                              height=28, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=3)
        theme.RoundButton(row, "Закрыть", shell.destroy, height=28, radius=10,
                          bg=PANEL, fg=MUTED, hover=SURFACE_3, border=LINE,
                          border_hover=BORDER_STRONG,
                          font=(FONT, CAPTION_PX)).pack(side="right")

    def _add_to_collection_inline(self, coll_id: str, mod: dict, bar: tk.Frame) -> None:
        result = self.api.post("/api/collections/items",
                               {"id": coll_id, "mod_id": mod["id"], "action": "add"})
        if not result.get("ok"):
            self.toast(result.get("error", "Не получилось"), "error")
            return
        bar.destroy()
        self.toast("Мод добавлен в сборку", "ok")
        self.refresh_all()

    ARCHIVE_REASONS = {
        "not_zip": "это не ZIP-архив (RAR/7z нужно распаковать или поставить 7-Zip)",
        "bad_zip": "архив повреждён или недокачан",
        "empty": "архив пустой",
        "unsafe_paths": "внутри архива небезопасные пути",
        "unknown_layout": "нет папок vehicles/, levels/, ui/ и файла info.json — "
                          "похоже, это не мод BeamNG.drive",
    }

    def _archive_reason(self, code: str) -> str:
        """Человеческое объяснение, почему архив не приняли."""
        base = code.split(":", 1)[0].strip()
        return self.ARCHIVE_REASONS.get(base, "неизвестный формат")

    def render_inbox(self) -> None:
        frame = self._scrollable_frame()
        self._set_header_count("")
        try:
            data = self.api.get("/api/inbox")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(frame, "Загрузки недоступны", f"Ошибка: {exc}", "box",
                              action=self.render_inbox, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        items = data.get("items", [])
        valid = [item for item in items if item.get("valid")]
        total_size = sum(int(item.get("size") or 0) for item in valid)
        self._set_header_count(f"{len(items)} {self._plural(len(items), 'архив', 'архива', 'архивов')} · к установке {len(valid)}")

        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 6))
        tk.Label(toolbar,
                 text="Здесь лежат архивы из папки «Загрузки» — они ещё не установлены. "
                      "Библиотека — это то, что уже попало в папку модов игры.",
                 bg=BG, fg=DIM, font=(FONT, CAPTION_PX), wraplength=760, justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)
        if valid:
            theme.RoundButton(toolbar, f"Установить все ({len(valid)})",
                              lambda: self.install_all(valid, total_size), variant="primary",
                              height=32, radius=10,
                              font=(FONT_BOLD, BODY_PX, "bold")).pack(side="right",
                                                                         padx=(12, 6))

        if not items:
            self._empty_state(frame, "Новых архивов нет",
                              "Скачайте мод в каталоге — архив появится здесь и его можно "
                              "будет установить одной кнопкой.",
                              "box", action=self.render_inbox,
                              action_text="Обновить список").pack(pady=44)
            return
        try:
            library = self.api.get("/api/library")
            library_items = library.get("items") or library.get("installed") or []
        except Exception:  # noqa: BLE001
            library_items = []
        installed_names = {str(item.get("filename") or "").lower() for item in library_items}
        installed_hashes = {str(item.get("sha256") or "").lower() for item in library_items}
        installed_hashes.discard("")
        installed_archives = {str(item.get("archive_name") or "").lower() for item in library_items}
        installed_archives.discard("")
        installed_tokens = [title_tokens(str(item.get("title") or item.get("filename") or ""))
                            for item in library_items]
        installed_keys = {title_key(item.get("title") or item.get("filename") or "")
                          for item in library_items}
        installed_keys.discard("")
        if items:
            tk.Label(frame, text="Архивы, скачанные при работающем BeamHub, ставятся сами; "
                                 "старые — кнопкой «Установить все» или по одному.",
                     bg=BG, fg=DIM, font=(FONT, CAPTION_PX), anchor="w",
                     wraplength=1100, justify="left").pack(fill="x", pady=(0, 6))
        for row in items:
            already = (str(row.get("name") or "").lower() in installed_archives
                       or looks_installed(str(row.get("name") or ""), installed_keys,
                                          installed_tokens,
                                          str(row.get("sha256") or ""), installed_hashes))
            outer = theme.RoundedFrame(frame, radius=12, fill=PANEL, border=LINE_SOFT, padding=1)
            outer.pack(fill="x", pady=3)
            line = tk.Frame(outer.body, bg=PANEL)
            line.pack(fill="x", padx=14, pady=8)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=row.get("name"), bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            reason = self._archive_reason(row.get("error") or "")
            if not row.get("valid"):
                meta_text = f"{row.get('size_text')} · не похоже на мод: {reason}"
            elif row.get("kind") == "bundle":
                meta_text = f"{row.get('size_text')} · архив с несколькими модами"
            else:
                meta_text = f"{row.get('size_text')} · мод"
            tk.Label(info, text=meta_text, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                     anchor="w", wraplength=520, justify="left").pack(fill="x")
            badges_needed = (not row.get("valid")) or already
            if badges_needed:
                badges = tk.Frame(info, bg=PANEL)
                badges.pack(fill="x", pady=(4, 0))
                if not row.get("valid"):
                    self._pill(badges, "не похоже на мод", "warn").pack(side="left")
                elif already:
                    self._pill(badges, "уже установлен", "ok").pack(side="left", padx=(0, 8))
                    tk.Label(badges, text="можно поставить заново", bg=PANEL, fg=DIM,
                             font=(FONT, CAPTION_PX)).pack(side="left")
            button = theme.RoundButton(line, "Установить заново" if already else "Установить",
                                       lambda r=row: self.install_file(r),
                                       variant=("ghost" if already else "primary"), height=30,
                                       radius=10,
                                       font=(FONT_BOLD if not already else FONT, CAPTION_PX,
                                             "bold" if not already else "normal"))
            if not row.get("valid"):
                button.configure(state="disabled")
            button.pack(side="right", padx=4)

    def install_all(self, items: list[dict], total_size: int) -> None:
        size_text = human_size(total_size) if total_size else ""
        if not messagebox.askyesno(
                "BeamHub",
                f"Установить {len(items)} {self._plural(len(items), 'архив', 'архива', 'архивов')} ({size_text}) в папку модов?\n\n"
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
        self.set_status(f"Ставлю {result.get('total', len(items))} {self._plural(result.get('total', len(items)), 'архив', 'архива', 'архивов')}…")

    def install_file(self, row: dict) -> None:
        def work() -> None:
            try:
                result = self.api.post("/api/install-file", {"path": row["path"]})
                text = ("Уже установлен" if result.get("status") == "duplicate"
                        else result.get("message") or "Установлено")
                self.root.after(0, lambda: (self.set_status(text), self.render_inbox()))
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self.set_status(f"Ошибка: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- настройки
    def render_settings(self) -> None:
        frame = self._scrollable_frame()
        self._set_header_count("")
        try:
            state = self.api.get("/api/state")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(frame, "Настройки недоступны", f"Ошибка: {exc}", "box",
                              action=self.render_settings, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        settings = state.get("settings", {})
        self._setting_vars = {}

        self._section(frame, "Папки", "пути к игре и загрузкам", pady=(0, 6))
        paths_body = self._panel(frame, pady=(0, 4))
        rows = [
            ("mods_dir", "Папка модов игры", settings.get("mods_dir", "")),
            ("downloads_dir", "Папка загрузок", settings.get("downloads_dir", "")),
            ("update_repo", "GitHub-репозиторий обновлений (владелец/репозиторий)",
             settings.get("update_repo", "")),
        ]
        for key, label, value in rows:
            var = tk.StringVar(value=value or "")
            self._field(paths_body, label, var, width=64)
            self._setting_vars[key] = var

        self._section(frame, "Поведение", pady=(12, 6))
        behavior = self._panel(frame, pady=(0, 4))
        auto_var = tk.BooleanVar(value=bool(settings.get("auto_install", True)))
        self._setting_vars["auto_install"] = auto_var
        self._toggle_row(behavior, "Ставить новые архивы из «Загрузок» автоматически",
                         auto_var,
                         "BeamHub сам установит архив, скачанный из каталога.")
        existing_var = tk.BooleanVar(value=bool(settings.get("install_existing", True)))
        self._setting_vars["install_existing"] = existing_var
        tk.Frame(behavior, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        self._toggle_row(behavior, "Ставить архивы, скачанные до запуска BeamHub",
                         existing_var, "Иначе их нужно ставить вручную в «Загрузках».")
        tk.Frame(behavior, bg=LINE_SOFT, height=1).pack(fill="x", padx=12)
        move_var = tk.BooleanVar(value=bool(settings.get("move_from_downloads", False)))
        self._setting_vars["move_from_downloads"] = move_var
        self._toggle_row(behavior, "Переносить архив из «Загрузок» в папку модов",
                         move_var, "Освобождает место на диске; исходный архив удаляется.")

        self._section(frame, "Язык", pady=(12, 6))
        lang_body = self._panel(frame, pady=(0, 4))
        lang_row = tk.Frame(lang_body, bg=PANEL)
        lang_row.pack(fill="x", padx=14, pady=10)
        tk.Label(lang_row, text="Язык интерфейса", bg=PANEL, fg=TEXT, font=(FONT, BODY_PX),
                 anchor="w").pack(side="left", fill="x", expand=True)
        for code, label in (("ru", "Русский"), ("en", "English")):
            self._chip(lang_row, label, i18n.current() == code,
                       lambda c=code: self.set_language(c)).pack(side="left", padx=(6, 0))

        self._section(frame, "Аккаунт", pady=(12, 6))
        acc_body = self._panel(frame, pady=(0, 4))
        acc = tk.Frame(acc_body, bg=PANEL)
        acc.pack(fill="x", padx=14, pady=10)
        acc_text = tk.Frame(acc, bg=PANEL)
        acc_text.pack(fill="x")
        if self.user:
            stats = (self.user or {}).get("stats") or {}
            line = tk.Frame(acc_text, bg=PANEL)
            line.pack(fill="x")
            tk.Label(line, text=self.user.get("nickname") or "аккаунт", bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
            role = {"admin": "администратор", "moderator": "модератор"}.get(
                self.user.get("role") or "", "участник")
            self._pill(line, role, "warn" if self.is_moderator() else "neutral").pack(
                side="left", padx=(8, 0))
            tk.Label(acc_text,
                     text=f"сборок {stats.get('collections', 0)} · "
                          f"установок {stats.get('installs', 0)} · "
                          f"оценок {stats.get('ratings', 0)}",
                     bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX), anchor="w").pack(fill="x",
                                                                                pady=(2, 0))
            acc_actions = tk.Frame(acc, bg=PANEL)
            acc_actions.pack(fill="x", pady=(8, 0))
            theme.RoundButton(acc_actions, "Статистика сообщества", self.stats_window,
                              height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left")
            if self.is_moderator():
                theme.RoundButton(acc_actions, "Модерация и жалобы", self.moderation_panel, height=30,
                                  radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                                  border_hover=BORDER_STRONG,
                                  font=(FONT, CAPTION_PX)).pack(side="left", padx=8)
                theme.RoundButton(acc_actions, "Админ-панель", self.admin_panel, height=30,
                                  radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                                  border_hover=BORDER_STRONG,
                                  font=(FONT, CAPTION_PX)).pack(side="left")
            theme.RoundButton(acc_actions, "Выйти", self.logout, height=30, radius=10,
                              bg=PANEL, fg=DANGER_TEXT, hover=DANGER_SOFT, border=LINE,
                              border_hover=DUP_LINE,
                              font=(FONT, CAPTION_PX)).pack(side="right")
        else:
            tk.Label(acc_text, text="Войдите, чтобы публиковать сборки и оценивать чужие",
                     bg=PANEL, fg=MUTED, font=(FONT, BODY_PX), anchor="w").pack(fill="x")
            acc_actions = tk.Frame(acc, bg=PANEL)
            acc_actions.pack(fill="x", pady=(8, 0))
            theme.RoundButton(acc_actions, "Войти", self.open_auth, height=30, radius=10,
                              bg=PANEL, fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left")
            theme.RoundButton(acc_actions, "Статистика сообщества", self.stats_window,
                              height=30, radius=10, bg=PANEL, fg=TEXT, hover=SURFACE_3,
                              border=LINE, border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=8)

        self._section(frame, "Облако и синхронизация", pady=(12, 6))
        self.cloud_block(frame, settings)

        self._section(frame, "Сервис", pady=(12, 6))
        service = self._panel(frame, pady=(0, 4))
        service_row = tk.Frame(service, bg=PANEL)
        service_row.pack(fill="x", padx=14, pady=10)
        for label, command in (("Пересобрать библиотеку", self.rescan),
                               ("Проверить обновления",
                                lambda: self.check_updates(silent=False)),
                               ("Обратная связь", self.send_feedback),
                               ("Экспорт списка", self.export_library)):
            theme.RoundButton(service_row, label, command, height=30, radius=10, bg=PANEL,
                              fg=TEXT, hover=SURFACE_3, border=LINE,
                              border_hover=BORDER_STRONG,
                              font=(FONT, CAPTION_PX)).pack(side="left", padx=(0, 8))

        save_row = tk.Frame(frame, bg=BG)
        save_row.pack(fill="x", pady=(16, 8))
        theme.RoundButton(save_row, "Сохранить", self.save_settings, variant="primary",
                          height=36, radius=10,
                          font=(FONT_BOLD, BODY_PX, "bold")).pack(side="left")
        tk.Label(save_row, text="Настройки сохраняются на этом компьютере", bg=BG, fg=DIM,
                 font=(FONT, CAPTION_PX)).pack(side="left", padx=12)

        self._section(frame, "О программе", pady=(8, 6))
        counts = state.get("counts", {})
        self._kv_rows(frame, [
            ("Версия", str(state.get("version") or "—")),
            ("Каталог", f"{counts.get('mods', 0)} {self._plural(counts.get('mods', 0), 'мод', 'мода', 'модов')} · установлено "
                        f"{counts.get('installed', 0)}"),
            ("Игра", state.get("game_path") or "не найдена"),
            ("Данные", str(state.get("data_dir", ""))),
        ], pady=(0, 16))

    def save_settings(self) -> None:
        payload = {key: var.get() for key, var in self._setting_vars.items()}
        payload["auto_install"] = bool(self._setting_vars["auto_install"].get())
        for key, label in (("mods_dir", "папка модов"), ("downloads_dir", "папка загрузок")):
            value = (payload.get(key) or "").strip()
            if value and not Path(value).exists():
                self.set_status(f"Не сохранил: {label} не существует — {value}")
                return
        try:
            self.api.post("/api/settings", payload)
            self.set_status("Настройки сохранены")
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Ошибка: {exc}")

    def rescan(self) -> None:
        try:
            data = self.api.post("/api/library/rescan", {})
            self.set_status(f"Библиотека пересобрана: {data.get('count')} {self._plural(data.get('count'), 'файл', 'файла', 'файлов')}")
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
                 font=(FONT_BOLD, 11, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 4))
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
                          height=34, radius=9, font=(FONT_BOLD, 10, "bold")).pack(side="left")
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
                    "Экспорт", f"Список из {data.get('count')} {self._plural(data.get('count'), 'мод', 'мода', 'модов')} сохранён:\n{data.get('path')}"))
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
        self._set_header_count("")
        try:
            data = self.api.get("/api/library")
        except Exception as exc:  # noqa: BLE001
            self._empty_state(frame, "Обновления недоступны", f"Ошибка: {exc}", "box",
                              action=self.render_updates, action_text="Повторить",
                              compact=True).pack(pady=30)
            return
        rows = [row for row in data.get("items", [])
                if row.get("update_available") or row.get("version_unknown")]
        known = sum(1 for row in rows if row.get("update_available"))
        self._set_header_count(
            f"{known} {self._plural(known, 'обновление', 'обновления', 'обновлений')}" + (f" · {len(rows) - known} без версии"
                                    if len(rows) > known else ""))
        if not rows:
            self._empty_state(frame, "Всё актуально",
                              "Все установленные моды актуальны. Проверить снова можно в "
                              "настройках или по кнопке ниже.",
                              "arrow",
                              action=lambda: self.render_updates(),
                              action_text="Проверить ещё раз").pack(pady=44)
            return
        self._section(frame, "Доступны обновления",
                      "скачайте новую версию и установите поверх", pady=(0, 6))
        for row in rows:
            outer = theme.RoundedFrame(frame, radius=12, fill=PANEL, border=LINE_SOFT, padding=1)
            outer.pack(fill="x", pady=3)
            line = tk.Frame(outer.body, bg=PANEL)
            line.pack(fill="x", padx=14, pady=8)
            info = tk.Frame(line, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=row.get("title") or row.get("filename"), bg=PANEL, fg=TEXT,
                     font=(FONT_BOLD, BODY_PX, "bold"), anchor="w").pack(fill="x")
            if row.get("update_available"):
                note = (f"установлено {row.get('version')} → "
                        f"в каталоге {row.get('catalog_version')}")
                kind, pill = "warn", f"обновление {row.get('catalog_version')}"
            else:
                note = f"версия файла неизвестна, в каталоге {row.get('catalog_version')}"
                kind, pill = "neutral", "версия неизвестна"
            row_badges = tk.Frame(info, bg=PANEL)
            row_badges.pack(fill="x", pady=(4, 0))
            self._pill(row_badges, pill, kind).pack(side="left", padx=(0, 8))
            tk.Label(row_badges, text=note, bg=PANEL, fg=DIM, font=(FONT, CAPTION_PX),
                     anchor="w").pack(side="left")
            theme.RoundButton(line, "Скачать новую",
                              lambda r=row: self.install({"id": r["mod_id"],
                                                          "title": r.get("title")}),
                              variant="primary", height=30, radius=10,
                              font=(FONT_BOLD, BODY_PX, "bold")).pack(side="right", padx=4)

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
                if data.get("external"):
                    self.api.post("/api/open-external", {"url": data.get("url") or url.strip()})
                    message = data.get("error") or "Открыл страницу в браузере"
                    self.root.after(0, lambda: self.set_status(message))
                    return
                if not data.get("ok"):
                    message = data.get("error") or "Не удалось добавить мод"
                    self.root.after(0, lambda: self.set_status(f"Ошибка: {message}"))
                    return
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
        mods_dir = state.get("settings", {}).get("mods_dir", "")
        self._mods_text = self._short_path(mods_dir, 34)
        self.foot_label.configure(text=self._mods_text or "не выбрана")
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
            row = tk.Frame(self.cat_frame, bg=SOFT, cursor="hand2", height=32)
            row.pack(fill="x", pady=1, padx=2)
            row.pack_propagate(False)
            bar = tk.Canvas(row, width=4, bg=SOFT, highlightthickness=0, bd=0)
            bar.pack(side="left", fill="y")
            icon_canvas = tk.Canvas(row, width=22, height=32, bg=SOFT,
                                    highlightthickness=0, bd=0)
            icon_canvas.pack(side="left", padx=(8, 6))
            text = tk.Label(row, text=label, bg=SOFT, fg=MUTED, font=(FONT, 10), anchor="w")
            text.pack(side="left", fill="x", expand=True)
            badge = tk.Label(row, text=str(count), bg=SOFT, fg=DIM, font=(FONT, 9), padx=4)
            badge.pack(side="right", padx=(2, 8))
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
        bg = SURFACE_2 if hover else SOFT
        for name in ("row", "bar", "icon", "label"):
            parts[name].configure(bg=bg)
        ink = ACCENT if active else (TEXT if hover else MUTED)
        parts["badge"].configure(bg=bg, fg=(ACCENT if active else DIM),
                                 font=(FONT_BOLD, BODY_PX, "bold") if active else (FONT, 9))
        parts["label"].configure(fg=ink,
                                 font=(FONT_BOLD, 10, "bold") if active else (FONT, 10))
        self._draw_cat_icon(parts["icon"], parts["kind"], ink)
        parts["bar"].delete("all")
        if active:
            theme.rounded_rect(parts["bar"], 0, 9, 3, 23, 2, fill=ACCENT)

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
