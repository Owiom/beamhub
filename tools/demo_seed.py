"""Демо-данные для проверки платформы: 2 мода, два пользователя, сборка, отзыв, комментарий.

    py tools\\demo_seed.py http://127.0.0.1:8955
Печатает id сборки и токен автора (для скриншотов и ручной проверки).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"


def _api_token() -> str:
    with urllib.request.urlopen(BASE + "/", timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    match = re.search(r'name="beamhub-token" content="([^"]+)"', html)
    return match.group(1) if match else ""


TOKEN = _api_token()
from core import db  # noqa: E402  (работает, если запускать из папки beamhub)


def call(path: str, payload: dict, user: str = "") -> dict:
    headers = {"Content-Type": "application/json; charset=utf-8", "X-BeamHub-Token": TOKEN}
    if user:
        headers["X-BeamHub-User"] = user
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8", "replace") or "{}")


def login_or_register(email: str, password: str, nickname: str) -> tuple[str, str]:
    result = call("/api/auth/register", {"email": email, "password": password,
                                         "nickname": nickname})
    if not result.get("token"):
        result = call("/api/auth/login", {"email": email, "password": password})
    return result.get("token", ""), (result.get("user") or {}).get("id", "")


if __name__ == "__main__":
    db.init()
    for mod_id, title, category, version in (
            ("mg:101", "Nissan Silvia S15 Rocket Bunny", "cars", "1.0"),
            ("mg:102", "Ebisu Minami Circuit", "maps", "2.1")):
        db.upsert_mod({"id": mod_id, "source": "modsgaming",
                       "url": f"https://example.com/{mod_id}", "title": title,
                       "category": category, "version": version})
    print("модов в базе:", db.count_mods())

    author_token, _ = login_or_register("author@example.com", "supersecret1", "DriftKing")
    fan_token, _ = login_or_register("fan@example.com", "supersecret2", "NightRider")

    coll = call("/api/collections/create", {
        "title": "JDM Drift Pack",
        "description": "Классика японского дрифта: Nissan Silvia S15 Rocket Bunny и трасса "
                       "Ebisu Minami. Установка одной кнопкой — уже стоящие моды пропускаются.",
        "category": "jdm", "tags": "drift, jdm, nissan, ebisu", "game_version": "0.40.x",
    }, author_token)
    coll_id = (coll.get("collection") or {}).get("id", "")
    call("/api/collections/items", {"id": coll_id, "mod_id": "mg:101"}, author_token)
    call("/api/collections/items", {"id": coll_id, "mod_id": "mg:102"}, author_token)
    call("/api/collections/publish", {"id": coll_id, "note": "Первая версия"}, author_token)

    call("/api/ratings", {"target_type": "collection", "target_id": coll_id, "stars": 5,
                          "review": "Поставил целиком, всё встало без конфликтов. "
                                    "Не хватает пары карт для полноты."}, fan_token)
    call("/api/comments", {"target_type": "collection", "target_id": coll_id,
                           "body": "Спасибо! А планируешь добавить трассу с дождём?"}, fan_token)
    print("сборка:", coll_id)
    print("токен автора:", author_token)
    print("токен фаната:", fan_token)
