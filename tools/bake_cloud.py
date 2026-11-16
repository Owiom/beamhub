"""Вшивает ключи облака в сборку: рядом с BeamHub.exe появляется cloud_defaults.json.

    py tools\\bake_cloud.py https://xxxx.supabase.co <anon-key> [папка]
    py tools\\bake_cloud.py            # взять из BEAMHUB_CLOUD_URL и BEAMHUB_CLOUD_ANON

После этого обычный игрок ничего не настраивает: жмёт «Войти» и вводит email с паролем.
anon key — публичный ключ, он и так лежит в клиенте; доступ к данным закрывают RLS-политики.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    args = [arg for arg in sys.argv[1:] if arg.strip()]
    url = args[0] if args else os.environ.get("BEAMHUB_CLOUD_URL", "")
    key = args[1] if len(args) > 1 else os.environ.get("BEAMHUB_CLOUD_ANON", "")
    folder = Path(args[2]) if len(args) > 2 else ROOT / "dist" / "BeamHub"
    if not url or not key:
        print(__doc__)
        return 2
    if not url.startswith("https://"):
        print("Адрес должен начинаться с https://")
        return 2
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "cloud_defaults.json"
    target.write_text(json.dumps({"url": url, "anon_key": key}, indent=2), encoding="utf-8")
    print(f"готово: {target}")
    print("теперь соберите архив — у пользователей не будет полей с ключами")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
