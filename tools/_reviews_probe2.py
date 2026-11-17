"""Проверка страниц отзывов и добавления мода."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from core.tkui import App  # noqa: E402

base, token, coll_id = sys.argv[1:4]

root = tk.Tk()
root.geometry("1200x780")
app = App(root, base)
app.api.user_token = token
app.load_user()


def run() -> None:
    before = len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
    app.switch("collections")
    app.collection_reviews(coll_id, "Тест")
    after = len([w for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
    print("отзывы в окне:", after == before, "| страница:", app._page_active)
    stars = []
    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, theme_round()):
                try:
                    if child.cget("text") in ("★", "☆"):
                        stars.append(child)
                except Exception:
                    pass
            walk(child)
    from core import theme as theme_mod
    def theme_round():
        return theme_mod.RoundButton
    walk(app.page)
    print("звёзд найдено:", len(stars),
          "| hover есть:", bool(stars and stars[0].bind("<Enter>")))
    app.pick_mod(coll_id)
    print("добавление мода страницей:", app._page_active)
    root.after(400, root.destroy)


root.after(1800, run)
root.mainloop()
