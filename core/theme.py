"""Оформление под Windows 11: шрифты, тёмный заголовок окна, скругления.

Всё опционально: если система старая или шрифта нет, приложение просто
остаётся с Segoe UI и квадратными углами — падать нечему.
"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont

# DWMWA_*
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWC_ROUND = 2
DWMSBT_MAINWINDOW = 2  # Mica

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


def font_setup() -> tuple[str, str]:
    """Вернуть (обычный, полужирный) шрифты для интерфейса."""
    base = pick_family(PREFERRED)
    semibold = "Segoe UI Semibold"
    if base.startswith("Segoe UI Variable"):
        semibold = base
    elif semibold.lower() not in _families():
        semibold = base
    return base, semibold


def dark_titlebar(window: tk.Misc) -> None:
    """Тёмный заголовок и скруглённые углы — как у нативных приложений Windows 11."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                  ctypes.byref(value), ctypes.sizeof(value))
        corner = ctypes.c_int(DWMWC_ROUND)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                  ctypes.byref(corner), ctypes.sizeof(corner))
        backdrop = ctypes.c_int(DWMSBT_MAINWINDOW)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                  ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    except Exception:  # noqa: BLE001
        pass


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int,
                 **kwargs) -> int:
    """Скруглённый прямоугольник из линий и дуг — в tkinter его нет из коробки."""
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundButton(tk.Canvas):
    """Кнопка со скруглёнными углами и подсветкой при наведении.

    variant: "primary" — оранжевая с тёмным текстом, "ghost" — тихая серая.
    """

    def __init__(self, parent: tk.Misc, text: str, command=None, *, width: int | None = None,
                 height: int = 34, radius: int = 9, variant: str = "ghost",
                 bg: str = "#171a21", fg: str = "#e9ebf0", hover: str = "#222733",
                 accent: str = "#ff7a1a", accent_hover: str = "#ff8f3d", font=None,
                 disabled_bg: str = "#1b1e26", disabled_fg: str = "#6b7280", **kwargs) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg=parent["bg"], **kwargs)
        self._text = text
        self._command = command
        self._radius = radius
        self._variant = variant
        self._bg = bg
        self._fg = fg
        self._hover = hover
        self._accent = accent
        self._accent_hover = accent_hover
        self._font = font
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._state = "normal"
        self._hovered = False
        self.configure(height=height, width=width or max(90, self._measure() + 34))
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

    def _palette(self) -> tuple[str, str]:
        if self._state == "disabled":
            return self._disabled_bg, self._disabled_fg
        if self._variant == "primary":
            if self._state == "pressed":
                return self._accent, "#1a0f04"
            return (self._accent_hover if self._hovered else self._accent), "#1a0f04"
        if self._state == "pressed":
            return "#2b3242", self._fg
        return (self._hover if self._hovered else self._bg), self._fg

    def _draw(self) -> None:
        self.delete("all")
        wid = self.winfo_width()
        hei = self.winfo_height()
        if wid <= 1:
            wid = int(float(self.cget("width")))
        if hei <= 1:
            hei = int(float(self.cget("height")))
        fill, color = self._palette()
        rounded_rect(self, 1, 1, wid - 1, hei - 1, self._radius, fill=fill, outline="")
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
        was_pressed = self._state == "pressed"
        self._state = "normal"
        self._draw()
        if was_pressed and self._command:
            self._command()

    # --- совместимость с tk.Button ---
    def configure(self, **kwargs):  # noqa: N802
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            self.itemconfigure("all", text=self._text)
            if kwargs.get("width") is None:
                super().configure(width=max(90, self._measure() + 34))
        if "state" in kwargs:
            self._state = "normal" if kwargs.pop("state") in ("normal", True) else "disabled"
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure

    def set_palette(self, bg: str | None = None, fg: str | None = None,
                    hover: str | None = None) -> None:
        """Сменить цвета без пересоздания — для чипов-переключателей."""
        if bg:
            self._bg = bg
        if fg:
            self._fg = fg
        if hover:
            self._hover = hover
        self._variant = "ghost"
        self._draw()

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)
