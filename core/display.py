"""HiDPI/4K: DPI-awareness и единый масштаб интерфейса."""
from __future__ import annotations

import ctypes
import os
from functools import lru_cache


def enable_dpi_awareness() -> None:
    """Сказать Windows, что приложение само рисует в физических пикселях."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


@lru_cache(maxsize=1)
def ui_scale() -> float:
    """Масштаб интерфейса: 1.0 = 96 DPI, 2.0 = 4K при 200 %.

    Порядок: BEAMHUB_UI_SCALE → системный DPI → ширина экрана.
    """
    override = os.environ.get("BEAMHUB_UI_SCALE")
    if override:
        try:
            return max(1.0, float(override.replace(",", ".")))
        except ValueError:
            pass
    if os.name != "nt":
        return 1.0
    scale = 1.0
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        scale = max(1.0, round(dpi / 96.0, 2))
    except Exception:  # noqa: BLE001
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            scale = max(1.0, round(dpi / 96.0, 2))
        except Exception:  # noqa: BLE001
            scale = 1.0
    try:
        width = int(ctypes.windll.user32.GetSystemMetrics(0))
        if width >= 3200 and scale < 1.5:
            scale = 2.0  # 4K-монитор без масштаба Windows — всё равно крупно и резко
    except Exception:  # noqa: BLE001
        pass
    return scale


def px(value: float) -> int:
    """Логические единицы → физические пиксели."""
    return int(round(float(value) * ui_scale()))


enable_dpi_awareness()
