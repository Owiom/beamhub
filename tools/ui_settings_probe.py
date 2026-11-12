"""Открыть окно BeamHub сразу на вкладке «Настройки» (для проверки вёрстки)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from core.tkui import App  # noqa: E402

root = tk.Tk()
app = App(root, sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8822")
root.after(1200, lambda: app.switch("settings"))
root.mainloop()
