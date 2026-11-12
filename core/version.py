"""Единственный источник версии приложения."""

VERSION = "0.7.6"
APP_NAME = "BeamHub"


def version_tuple(text: str) -> tuple:
    """'v0.6.0' -> (0, 6, 0); нечисловые хвосты отбрасываются."""
    parts = []
    for chunk in str(text).lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])
