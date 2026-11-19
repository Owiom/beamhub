"""Дизайн-система BeamHub 2026: тёмная тема, оранжевый акцент, мягкие поверхности.

Здесь живут токены оформления (палитра, радиусы, отступы, шкала шрифтов,
состояния кнопок) и скруглённые контролы на Canvas. Всё опционально: если
система старая или шрифта нет, приложение просто остаётся с Segoe UI и
квадратными углами — падать нечему.
"""
from __future__ import annotations

from . import display, i18n

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------- палитра
# Принцип: почти чёрный фон, поверхности на 3 ступени светлее, границы почти
# не видны, оранжевый используется точечно — как акцент, а не как заливка.
PALETTE = {
    "bg": "#08090c",            # фон окна — глубже, чем любая поверхность
    "surface": "#12141b",       # карточки, панели, поля
    "surface_2": "#171a22",     # приподнятые поверхности, hover
    "surface_3": "#20242f",     # нажатие, выбранный элемент
    "border": "#232734",        # единственная линия сетки интерфейса
    "border_soft": "#1a1e27",   # «мягкая» рамка карточек в покое
    "border_strong": "#4b5670",  # рамка при наведении, фокус
    "text": "#f0f2f7",          # основной текст
    "muted": "#9aa3b4",         # второй план (контраст 7.4:1 на surface)
    "dim": "#7a8291",           # подписи, метаданные (контраст 5.2:1 на surface)
    "accent": "#ff7a1a",        # акцент: одна кнопка на экран
    "accent_hover": "#ff8f3d",
    "accent_press": "#e96a0b",
    "accent_soft": "#33200f",   # плашка «дубликат», активный чип
    "accent_soft_text": "#ffc08a",
    "on_accent": "#170d04",     # текст на оранжевом
    "success": "#34c87b",
    "success_soft": "#16241a",   # плашка «установлен», «проверено»
    "success_text": "#9fe3b4",
    "warning": "#f2b33d",
    "warning_soft": "#2a2210",
    "warning_text": "#ffd6a3",
    "danger": "#f05252",
    "danger_soft": "#2a1518",    # удаление, ошибка
    "danger_text": "#ff9a9a",
}

# Плоские псевдонимы: удобно, когда модуль импортирует конкретные цвета.
BG = PALETTE["bg"]
SURFACE = PALETTE["surface"]
SURFACE_2 = PALETTE["surface_2"]
SURFACE_3 = PALETTE["surface_3"]
BORDER = PALETTE["border"]
TEXT = PALETTE["text"]
MUTED = PALETTE["muted"]
DIM = PALETTE["dim"]
ACCENT = PALETTE["accent"]
SUCCESS = PALETTE["success"]
WARNING = PALETTE["warning"]

# ---------------------------------------------------------------- радиусы
# 10 — мелкие контролы и чипы, 12 — кнопки/поля/карточки по умолчанию,
# 14 — «таблетки»-чипы и крупные кнопки. Всё, что больше 14, в 2026 не нужно.
RADIUS_SM = 10
RADIUS = 12
RADIUS_LG = 14
RADIUS_XS = 8  # только для бейджей внутри карточки

# ---------------------------------------------------------------- отступы
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

# ---------------------------------------------------------------- шрифты
# Segoe UI Variable идёт с Windows 11; на Windows 10 остаётся Segoe UI.
# Размеры — только из этой шкалы, чтобы интерфейс не «плыл».
# 9 — подписи, 10 — тело, 11 — поля/описания, 13 — секции, 18 — заголовок
# экрана, 22 — титул-«дисплей» (самый крупный текст в приложении).
TYPE = {
    "caption": 9,   # метаданные, бейджи, статус-бар
    "body": 10,     # основной текст интерфейса
    "large": 11,    # поля ввода, описания
    "section": 13,  # заголовки секций, страниц
    "title": 18,    # заголовок экрана
    "display": 22,  # титул экрана каталога
}

# Полужирный набираем весом 600: одно семейство на всё приложение, а не
# отдельный «Segoe UI Semibold», который на Windows 11 не подхватывается.
PREFERRED = (
    "Segoe UI Variable Text",   # Windows 11
    "Segoe UI Variable",
    "Segoe UI",                 # Windows 10
    "Tahoma",
)
PREFERRED_DISPLAY = (
    "Segoe UI Variable Display",
    "Segoe UI Variable Text",
    "Segoe UI",
)

# ---------------------------------------------------------------- состояния
# Одна таблица на все контролы: и кнопки Canvas, и карточки в tkui.py читают
# цвета отсюда, поэтому hover/active/disabled выглядят одинаково везде.
BUTTONS = {
    "primary": {
        "bg": PALETTE["accent"],
        "hover": PALETTE["accent_hover"],
        "press": PALETTE["accent_press"],
        "fg": PALETTE["on_accent"],
        "disabled_bg": "#23262e",
        "disabled_fg": "#5a6172",
    },
    "ghost": {
        "bg": "#151821",
        "hover": PALETTE["surface_3"],
        "press": "#2a3040",
        "fg": PALETTE["text"],
        "disabled_bg": "#14161d",
        "disabled_fg": "#5a6172",
    },
}

CARD = {
    "bg": PALETTE["surface"],
    "bg_hover": "#171b24",
    "border": PALETTE["border_soft"],
    "border_hover": PALETTE["border_strong"],
    "duplicate_border": "#8a4a1c",
    "badge_bg": PALETTE["accent_soft"],
    "badge_fg": PALETTE["accent_soft_text"],
}

# DWMWA_*
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWC_ROUND = 2
DWMSBT_MAINWINDOW = 2  # Mica


# ---------------------------------------------------------------- утилиты
def mix(color: str, other: str = "#000000", amount: float = 0.12) -> str:
    """Смешать два #rrggbb: amount=0 — color, amount=1 — other.

    Нужен, чтобы получить цвет нажатия из цвета наведения без таблицы
    на каждый вариант кнопки.
    """
    try:
        a = color.lstrip("#")
        b = other.lstrip("#")
        if len(a) != 6 or len(b) != 6:
            raise ValueError
        parts = []
        for index in range(3):
            left = int(a[index * 2:index * 2 + 2], 16)
            right = int(b[index * 2:index * 2 + 2], 16)
            parts.append(round(left + (right - left) * amount))
        return "#" + "".join(f"{value:02x}" for value in parts)
    except (ValueError, TypeError):
        return color


def card_colors(hover: bool = False, duplicate: bool = False) -> tuple[str, str]:
    """Цвета карточки: (фон, рамка).

    Жёстких рамок больше нет: в покое рамка почти совпадает с фоном, при
    наведении поднимается и фон, и рамка. Дубликат получает тёплую рамку.
    """
    background = CARD["bg_hover"] if hover else CARD["bg"]
    if duplicate:
        border = "#8a4a1c" if hover else CARD["duplicate_border"]
    else:
        border = CARD["border_hover"] if hover else CARD["border"]
    return background, border


def _families() -> set[str]:
    try:
        return {name.lower() for name in tkfont.families()}
    except tk.TclError:
        return set()


def pick_family(candidates: tuple[str, ...]) -> str:
    available = _families()
    for name in candidates:
        if name.lower() in available:
            return name
    return "Segoe UI"


_RESOLVED: tuple[str, str] | None = None


def font_setup() -> tuple[str, str]:
    """Вернуть (обычный, полужирный) шрифты для интерфейса."""
    global _RESOLVED
    base = pick_family(PREFERRED)
    semibold = "Segoe UI Semibold"
    if base.startswith("Segoe UI Variable"):
        semibold = base
    elif semibold.lower() not in _families():
        semibold = base
    _RESOLVED = (base, semibold)
    return base, semibold


def ui_font(size: int = TYPE["body"], semibold: bool = False) -> tuple:
    """Шрифт из шкалы: theme.ui_font(TYPE["title"], semibold=True).

    Полужирный — это тот же Segoe UI Variable с весом bold: отдельного
    семейства «Segoe UI Variable Semibold» в системе нет, а «Segoe UI Semibold»
    на Windows 11 выглядит как шрифт из прошлого десятилетия.
    """
    base, bold = _RESOLVED or ("Segoe UI", "Segoe UI Semibold")
    if semibold:
        return (bold, size, "bold")
    return (base, size)


def dark_titlebar(window: tk.Misc) -> None:
    """Тёмный заголовок, скруглённые углы и тёмная кромка окна.

    Как у нативных приложений Windows 11. Неизвестные атрибуты система
    игнорирует, поэтому на Windows 10 ничего не ломается.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi

        def attr(index: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, index, ctypes.byref(data), ctypes.sizeof(data))

        def rgb(color: str) -> int:
            # COLORREF: 0x00BBGGRR
            value = color.lstrip("#")
            red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
            return red | (green << 8) | (blue << 16)

        attr(DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        attr(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWC_ROUND)
        attr(DWMWA_CAPTION_COLOR, rgb(PALETTE["bg"]))
        attr(DWMWA_BORDER_COLOR, rgb(PALETTE["bg"]))
        attr(DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_MAINWINDOW)
    except Exception:  # noqa: BLE001
        pass


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int,
                 **kwargs) -> int:
    """Скруглённый прямоугольник из линий и дуг — в tkinter его нет из коробки."""
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundButton(tk.Canvas):
    """Кнопка со скруглёнными углами и подсветкой при наведении.

    variant: "primary" — оранжевая с тёмным текстом (одна на экран),
    "ghost" — тихая поверхность для всего остального.

    Цвета по умолчанию берутся из BUTTONS, радиус — из RADIUS. Наведение
    поднимает поверхность на одну ступень, нажатие опускает на полступени;
    у primary наведение светлее, нажатие темнее акцента.
    """

    def __init__(self, parent: tk.Misc, text: str, command=None, *, width: int | None = None,
                 height: int = 36, radius: int | None = None, variant: str = "ghost",
                 bg: str | None = None, fg: str | None = None, hover: str | None = None,
                 press: str | None = None, accent: str | None = None,
                 accent_hover: str | None = None,
                 font=None, disabled_bg: str | None = None, disabled_fg: str | None = None,
                 border: str = "", border_hover: str = "", **kwargs) -> None:
        base = BUTTONS["primary" if variant == "primary" else "ghost"]
        try:
            parent_bg = parent.cget("bg")
        except Exception:  # noqa: BLE001  (ttk-контейнеры не знают про bg)
            parent_bg = PALETTE["bg"]
        super().__init__(parent, highlightthickness=0, bd=0, bg=parent_bg, **kwargs)
        self._text = text
        self._command = command
        self._radius = display.px(radius) if radius is not None else None
        self._variant = "primary" if variant == "primary" else "ghost"
        self._bg = bg or base["bg"]
        self._fg = fg or base["fg"]
        self._hover = hover or base["hover"]
        self._press = press
        self._accent = accent or BUTTONS["primary"]["bg"]
        self._accent_hover = accent_hover or BUTTONS["primary"]["hover"]
        self._accent_press = (mix(accent, "#000000", 0.10) if accent
                             else BUTTONS["primary"]["press"])
        self._font = font or ui_font(TYPE["body"], semibold=(self._variant == "primary"))
        self._disabled_bg = disabled_bg or base["disabled_bg"]
        self._disabled_fg = disabled_fg or base["disabled_fg"]
        self._border = border
        self._border_hover = border_hover
        self._state = "normal"
        self._hovered = False
        self.configure(height=display.px(height),
                       width=(display.px(width) if width
                              else max(display.px(90), self._measure() + display.px(34))))
        self._draw()
        for event, handler in (("<Enter>", self._on_enter), ("<Leave>", self._on_leave),
                               ("<Button-1>", self._on_press),
                               ("<ButtonRelease-1>", self._on_release)):
            self.bind(event, handler)
        self.bind("<Configure>", lambda _e: self._draw())
        self.configure(cursor="hand2")

    def _measure(self) -> int:
        try:
            return tkfont.Font(font=self._font).measure(self._text)
        except tk.TclError:
            return len(self._text) * 7

    def _effective_radius(self, height: int) -> int:
        """Радиус держим в коридоре системы и не больше половины высоты.

        Старые вызовы с radius=8 не выпадают из системы: минимум — RADIUS_SM.
        """
        radius = display.px(RADIUS if self._radius is None else self._radius)
        radius = max(display.px(RADIUS_SM), min(radius, display.px(RADIUS_LG)))
        return max(2, min(radius, height // 2 - 1))

    def _palette(self) -> tuple[str, str, str]:
        if self._state == "disabled":
            return self._disabled_bg, self._disabled_fg, ""
        if self._variant == "primary":
            if self._state == "pressed":
                fill = self._accent_press
            elif self._hovered:
                fill = self._accent_hover
            else:
                fill = self._accent
            return fill, BUTTONS["primary"]["fg"], ""
        if self._state == "pressed":
            fill = self._press or mix(self._hover, "#000000", 0.12)
        elif self._hovered:
            fill = self._hover
        else:
            fill = self._bg
        border = (self._border_hover or self._border) if self._hovered else self._border
        return fill, self._fg, border

    def _draw(self) -> None:
        self.delete("all")
        wid = self.winfo_width()
        hei = self.winfo_height()
        if wid <= 1:
            wid = int(float(self.cget("width")))
        if hei <= 1:
            hei = int(float(self.cget("height")))
        fill, color, border = self._palette()
        radius = self._effective_radius(hei)
        outline = {"outline": border, "width": 1} if border else {"outline": ""}
        rounded_rect(self, 1, 1, wid - 1, hei - 1, radius, fill=fill, **outline)
        self.create_text(wid // 2, hei // 2, text=self._text, fill=color, font=self._font)

    # --- события ---
    def _on_enter(self, _event=None) -> None:
        if self._state == "normal":
            self._hovered = True
            self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if self._state == "normal":
            self._state = "pressed"
            self._draw()

    def _on_release(self, _event=None) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._state == "pressed"
        self._state = "normal"
        self._draw()
        if was_pressed and self._command:
            self._command()

    # --- совместимость с tk.Button ---
    def configure(self, cnf=None, **kwargs):  # noqa: N802
        if isinstance(cnf, dict):
            cnf = dict(cnf)
            for key in ("text", "fg", "bg", "hover", "press", "state", "command",
                        "radius", "variant", "border", "border_hover", "font"):
                if key in cnf:
                    kwargs[key] = cnf.pop(key)
            if cnf:
                super().configure(cnf)
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "fg" in kwargs:
            self._fg = kwargs.pop("fg")
        if "bg" in kwargs:
            self._bg = kwargs.pop("bg")
        if "hover" in kwargs:
            self._hover = kwargs.pop("hover")
        if "press" in kwargs:
            self._press = kwargs.pop("press")
        if "radius" in kwargs:
            self._radius = kwargs.pop("radius")
        if "font" in kwargs:
            self._font = kwargs.pop("font")
        if "border" in kwargs:
            self._border = kwargs.pop("border")
        if "border_hover" in kwargs:
            self._border_hover = kwargs.pop("border_hover")
        if "variant" in kwargs:
            value = kwargs.pop("variant")
            if value in ("primary", "ghost"):
                self._variant = value
        if "state" in kwargs:
            value = kwargs.pop("state")
            self._state = "normal" if value in ("normal", True, "active") else "disabled"
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure

    def set_palette(self, bg: str | None = None, fg: str | None = None,
                    hover: str | None = None, press: str | None = None,
                    border: str | None = None) -> None:
        """Сменить цвета без пересоздания — для чипов-переключателей.

        Вызывается и при снятии выделения, поэтому кнопка возвращается в
        вариант ghost: чип — это не primary, у него своя логика цвета.
        """
        self._variant = "ghost"
        if bg:
            self._bg = bg
            self._border = ""  # цветная заливка говорит сама за себя
        if fg:
            self._fg = fg
        if hover:
            self._hover = hover
        if press:
            self._press = press
        if border is not None:
            self._border = border
        self._draw()

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

class AvatarButton(tk.Canvas):
    """Кнопка аккаунта: круглый аватар + имя в скруглённой «таблетке».

    Гость видит нейтральный кружок с силуэтом, вошедший — оранжевый кружок с
    первой буквой ника. Ширина считается по тексту (ник обрезается по
    max_width), поэтому шапка не разъезжается от длинного ника.
    """

    GUEST_TEXTS = {"", "войти", "вход", "войти в аккаунт", "sign in", "log in"}

    def __init__(self, parent: tk.Misc, text: str = "", command=None, *, height: int = 40,
                 font=None, max_width: int = 240, **kwargs) -> None:
        try:
            parent_bg = parent.cget("bg")
        except Exception:  # noqa: BLE001
            parent_bg = PALETTE["bg"]
        super().__init__(parent, height=height, width=150, highlightthickness=0, bd=0,
                         bg=parent_bg, cursor="hand2", **kwargs)
        self._text = str(text or "Войти")
        self._command = command
        self._height = height
        self._font = font or ui_font(TYPE["body"])
        self._max_width = max_width
        self._hovered = False
        self._guest = self._is_guest()
        self._label = tk.Label(self, text=self._text, bd=0, padx=0, pady=0,
                               bg=BUTTONS["ghost"]["bg"], fg=PALETTE["text"],
                               font=self._font, cursor="hand2")
        self._label.place(x=height + 4, y=height // 2, anchor="w")
        for widget in (self, self._label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", lambda _e: self._draw())
        self._layout()

    # --- геометрия ---
    def _is_guest(self) -> bool:
        return self._text.strip().lower() in self.GUEST_TEXTS

    def _measured(self, text: str) -> int:
        try:
            return tkfont.Font(font=self._font).measure(text)
        except tk.TclError:
            return len(text) * 7

    def _fit_text(self) -> str:
        limit = self._max_width - self._height - 34
        text = i18n.tr(self._text)
        if self._measured(text) <= limit:
            return text
        while text and self._measured(text + "…") > limit:
            text = text[:-1]
        return (text + "…") if text else "…"

    def _layout(self) -> None:
        shown = self._fit_text()
        self._label.configure(text=shown, font=self._font)
        width = self._height + 6 + self._measured(shown) + 30
        self.configure(width=max(96, min(width, self._max_width)))
        self._draw()

    # --- отрисовка ---
    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            width = int(float(self.cget("width")))
        height = self._height
        fill = PALETTE["surface_3"] if self._hovered else PALETTE["surface_2"]
        border = PALETTE["border_strong"] if self._hovered else PALETTE["border"]
        rounded_rect(self, 1, 1, width - 1, height - 1, height // 2 - 1,
                     fill=fill, outline=border, width=1)
        radius = (height - 12) // 2
        cx, cy = 6 + radius, height // 2
        if self._guest:
            self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                             fill=PALETTE["surface_3"], outline="")
            self.create_oval(cx - 3, cy - 7, cx + 3, cy - 1,
                             fill=PALETTE["accent_soft_text"], outline="")
            self.create_arc(cx - 7, cy - 2, cx + 7, cy + 9, start=200, extent=140,
                            style="arc", outline=PALETTE["accent_soft_text"], width=2)
        else:
            self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                             fill=PALETTE["accent"], outline="")
            self.create_text(cx, cy, text=(self._text.strip()[:1] or "?").upper(),
                             fill=PALETTE["on_accent"],
                             font=(self._font[0], TYPE["body"], "bold"))
        self.create_line(width - 18, cy - 2, width - 13, cy + 3, width - 8, cy - 2,
                         fill=PALETTE["dim"], width=2, capstyle="round", joinstyle="round")
        self._label.configure(bg=fill, fg=PALETTE["text"],
                              font=(self._font[0], self._font[1],
                                    "bold" if not self._guest else "normal"))

    # --- события ---
    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        def settle() -> None:
            try:
                x, y = self.winfo_pointerxy()
                widget = self.winfo_containing(x, y)
            except tk.TclError:
                widget = None
            while widget is not None and widget is not self:
                widget = getattr(widget, "master", None)
            if widget is self:
                return
            self._hovered = False
            self._draw()

        self.after(30, settle)

    def _on_click(self, _event=None) -> None:
        if self._command:
            self._command()

    # --- совместимость с RoundButton/tk.Button ---
    def configure(self, cnf=None, **kwargs):  # noqa: N802
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        text = kwargs.pop("text", None)
        if text is not None:
            self._text = str(text)
            self._guest = self._is_guest()
            self._layout()
        for key in ("fg", "bg", "hover", "press", "state", "radius", "variant",
                    "border", "border_hover", "command"):
            if key in kwargs and key == "command":
                self._command = kwargs.pop(key)
            else:
                kwargs.pop(key, None)
        if "font" in kwargs:
            self._font = kwargs.pop("font")
            self._layout()
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)


class RoundedFrame(tk.Canvas):
    """Панель со скруглением и рамкой; содержимое кладём в .body."""

    def __init__(self, parent: tk.Misc, *, radius: int = 14, fill: str = "#15171e",
                 border: str = "#242832", padding: int = 1, **kwargs) -> None:
        radius = display.px(radius)
        padding = display.px(padding)
        super().__init__(parent, highlightthickness=0, bd=0, bg=parent["bg"], **kwargs)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._padding = padding
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(padding, padding, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.bind("<Configure>", self._draw)

    def _on_body(self, event) -> None:
        self.configure(height=event.height + self._padding * 2)

    def _draw(self, _event=None) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self.delete("panel")
        rounded_rect(self, 1, 1, width - 1, height - 1, self._radius,
                     fill=self._fill, outline=self._border, tags="panel")
        self.tag_lower("panel")
        self.itemconfigure(self._window, width=max(1, width - self._padding * 2))

    def set_border(self, color: str) -> None:
        self._border = color
        self._draw()

    def set_fill(self, color: str) -> None:
        self._fill = color
        self.body.configure(bg=color)
        self._draw()


class ToggleSwitch(tk.Canvas):
    """Переключатель 44×24 для настроек: включено — акцент, выключено — surface_3.

    Использует тот же BooleanVar, что и Checkbutton, поэтому логика настроек
    не меняется: меняется только внешний вид.
    """

    WIDTH = 44
    HEIGHT = 24

    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, command=None,
                 **kwargs) -> None:
        try:
            parent_bg = parent.cget("bg")
        except Exception:  # noqa: BLE001
            parent_bg = PALETTE["bg"]
        super().__init__(parent, width=self.WIDTH, height=self.HEIGHT, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self._var = variable
        self._command = command
        self._hovered = False
        self._draw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _draw(self) -> None:
        self.delete("all")
        width, height = self.WIDTH, self.HEIGHT
        on = bool(self._var.get())
        track = PALETTE["accent"] if on else PALETTE["surface_3"]
        if self._hovered:
            track = (mix(track, "#ffffff", 0.10) if on
                     else mix(track, "#ffffff", 0.07))
        rounded_rect(self, 1, 1, width - 1, height - 1, (height - 2) // 2,
                     fill=track, outline="")
        radius = height // 2 - 4
        cx = (width - radius - 4) if on else (radius + 4)
        self.create_oval(cx - radius, height // 2 - radius,
                         cx + radius, height // 2 + radius,
                         fill=PALETTE["text"], outline="")

    def _toggle(self, _event=None) -> None:
        self._var.set(not self._var.get())
        self._draw()
        if self._command:
            self._command()

    def _enter(self, _event=None) -> None:
        self._hovered = True
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hovered = False
        self._draw()

    def refresh(self) -> None:
        self._draw()
