"""Маленькое окно-лаунчер: показывает, что BeamHub запущен, и кнопку «Открыть каталог».

Нужно для exe-версии: если браузер не открылся автоматически (запрет ShellExecute,
не задан браузер по умолчанию), пользователь всё равно видит окно и кнопку, а не тишину.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import font as tkfont

BG = "#0d0e11"
PANEL = "#171a21"
ACCENT = "#ff7a1a"
TEXT = "#e9ebf0"
MUTED = "#8d93a1"


def available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _open(url: str, opener) -> None:
    threading.Thread(target=opener, args=(url,), daemon=True).start()


def show(url: str, stats=None, opener=None, on_close=None, icon: str | None = None,
         already_running: bool = False) -> None:
    """Блокирующее окно. stats() -> (модов, установлено). on_close() — остановка сервера."""
    root = tk.Tk()
    root.title("BeamHub")
    root.configure(bg=BG)
    root.geometry("480x290")
    root.minsize(420, 270)
    root.resizable(False, False)
    if icon:
        try:
            root.iconbitmap(icon)
        except Exception:  # noqa: BLE001
            pass

    title_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
    text_font = tkfont.Font(family="Segoe UI", size=10)
    small_font = tkfont.Font(family="Segoe UI", size=9)
    button_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=20, pady=(18, 6))

    badge = tk.Label(header, text="B", bg=ACCENT, fg="#14100c", font=title_font,
                     width=2, height=1)
    badge.pack(side="left")
    titles = tk.Frame(header, bg=BG)
    titles.pack(side="left", padx=12)
    tk.Label(titles, text="BeamHub", bg=BG, fg=TEXT, font=title_font, anchor="w").pack(anchor="w")
    tk.Label(titles, text="каталог модов BeamNG.drive", bg=BG, fg=MUTED, font=small_font,
             anchor="w").pack(anchor="w")

    card = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground="#262b36")
    card.pack(fill="x", padx=20, pady=12)

    status = "BeamHub уже запущен" if already_running else "Сервер работает"
    tk.Label(card, text=status, bg=PANEL, fg="#a9f0c3" if not already_running else "#ffd6a3",
             font=text_font, anchor="w").pack(fill="x", padx=14, pady=(12, 2))
    link = tk.Label(card, text=url, bg=PANEL, fg=ACCENT, font=text_font, anchor="w", cursor="hand2")
    link.pack(fill="x", padx=14)
    if opener:
        link.bind("<Button-1>", lambda _event: _open(url, opener))

    counter = tk.Label(card, text="", bg=PANEL, fg=MUTED, font=small_font, anchor="w")
    counter.pack(fill="x", padx=14, pady=(2, 12))

    def refresh() -> None:
        if stats:
            try:
                mods, installed = stats()
                counter.config(text=f"{mods} модов в каталоге · {installed} установлено")
            except Exception:  # noqa: BLE001
                pass
        root.after(4000, refresh)

    refresh()

    buttons = tk.Frame(root, bg=BG)
    buttons.pack(fill="x", padx=20, pady=(2, 10))

    def open_clicked() -> None:
        if opener:
            _open(url, opener)

    open_btn = tk.Button(buttons, text="Открыть каталог", command=open_clicked, font=button_font,
                         bg=ACCENT, fg="#1a0f04", activebackground="#ff9350",
                         activeforeground="#1a0f04", relief="flat", bd=0, padx=16, pady=7,
                         cursor="hand2")
    open_btn.pack(side="left")

    def stop_clicked() -> None:
        if on_close:
            on_close()
        root.destroy()

    stop_text = "Закрыть" if already_running else "Остановить"
    tk.Button(buttons, text=stop_text, command=stop_clicked, font=text_font,
              bg=PANEL, fg=TEXT, activebackground="#232833", activeforeground=TEXT,
              relief="flat", bd=0, padx=16, pady=7, cursor="hand2").pack(side="left", padx=8)

    tk.Label(root, text="Если браузер не открылся — нажмите «Открыть каталог» "
                        "или введите адрес вручную.",
             bg=BG, fg="#6b7280", font=small_font, wraplength=440, justify="left",
             anchor="w").pack(fill="x", padx=20, pady=(0, 14))

    root.protocol("WM_DELETE_WINDOW", stop_clicked)
    root.update_idletasks()
    width, height = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 3
    root.geometry(f"+{x}+{y}")
    root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.after(200, root.focus_force)
    root.mainloop()
