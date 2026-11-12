"""Открыть окно BeamHub на нужной вкладке (для проверки вёрстки).

    py tools/ui_probe.py http://127.0.0.1:8841 library
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from core.tkui import App  # noqa: E402

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
view = sys.argv[2] if len(sys.argv) > 2 else "catalog"

root = tk.Tk()
app = App(root, url)
root.after(1200, lambda: app.switch(view))
root.mainloop()
