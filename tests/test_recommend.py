"""Тесты рекомендаций и совместимости.

Запуск:  py tests\\test_recommend.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="beamhub-rec-"))
os.environ["BEAMHUB_DB"] = str(TMP / "rec.db")

from core import accounts, collections, compat, db, recommend  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


db.init()


def add_mod(mod_id: str, title: str, category: str, author: str = "", version: str = "1.0",
            game: str = "0.40.x") -> None:
    db.upsert_mod({"id": mod_id, "source": "modsgaming", "url": f"https://example.com/{mod_id}",
                   "title": title, "category": category, "version": version,
                   "game_version": game, "author": author, "downloads": 10,
                   "download_url": f"https://example.com/dl/{mod_id}"})


add_mod("mg:1", "Nissan Silvia S15 Rocket Bunny", "cars", "DriftWorks", game="0.40.x")
add_mod("mg:2", "Nissan Silvia S14 Rocket Bunny", "cars", "DriftWorks", game="0.40.x")
add_mod("mg:3", "Nissan Skyline R34", "cars", "JDMGarage")
add_mod("mg:4", "Ebisu Minami Circuit", "maps", "TrackLab")
add_mod("mg:5", "BMW E36 Drift Missile", "cars", "BavariaMods", game="0.39.x")

print("[1] похожесть")
base = db.get_mod("mg:1")
s14 = db.get_mod("mg:2")
track = db.get_mod("mg:4")
check("одинаковый автор и категория — близко", recommend.similarity(base, s14) > 0.6,
      str(recommend.similarity(base, s14)))
check("разные категории — далеко", recommend.similarity(base, track) < 0.5,
      str(recommend.similarity(base, track)))
check("сам с собой — максимум", recommend.similarity(base, base) >= 0.9)

similar = recommend.similar_mods("mg:1", limit=5)
ids = [item["id"] for item in similar["items"]]
check("похожие найдены", similar["ok"] and similar["items"], str(ids))
check("самого себя в похожих нет", "mg:1" not in ids)
check("S14 первым в списке", ids and ids[0] == "mg:2", str(ids))
check("карта не попала в похожие", "mg:4" not in ids, str(ids))
check("несуществующий мод", not recommend.similar_mods("nope")["ok"])

print("[2] популярное и новинки")
user = accounts.register("u@example.com", "supersecret1", "User")["user"]["id"]
db.installed_add({"key": "a.zip", "mod_id": "mg:1", "filename": "a.zip",
                  "path": str(TMP / "a.zip"), "size": 1, "title": "Nissan Silvia S15",
                  "version": "1.0"})
collections.record_event("install", mod_id="mg:1")
collections.record_event("view", mod_id="mg:2")
popular = recommend.popular_mods(5)
check("популярное не пустое", popular["ok"] and popular["items"])
check("установленный мод выше просмотренного", popular["items"][0]["id"] == "mg:1",
      str([row["id"] for row in popular["items"]]))
fresh = recommend.new_mods(5)
check("новинки возвращают моды", fresh["ok"] and len(fresh["items"]) >= 5)
recs = recommend.recommended_mods(user, 5)
check("рекомендации учитывают установленную категорию",
      recs["ok"] and recs.get("basis") == ["cars"], str(recs.get("basis")))
recs_anon = recommend.recommended_mods("", 5)
check("без аккаунта — популярное", recs_anon["ok"] and recs_anon["items"])

print("[3] сборки")
owner = accounts.register("o@example.com", "supersecret2", "Owner")["user"]["id"]
first = collections.create(owner, "Drift Pack", "", category="drift")["collection"]["id"]
collections.add_item(first, owner, "mg:1")
collections.publish(first, owner)
second = collections.create(owner, "Track Pack", "", category="racing")["collection"]["id"]
collections.add_item(second, owner, "mg:4")
collections.publish(second, owner)

from core import social  # noqa: E402

social.set_rating(user, "collection", first, 5, "отлично")
rec_collections = recommend.recommended_collections(5)
check("рекомендованные сборки", rec_collections["ok"] and rec_collections["items"])
check("оценённая сборка выше", rec_collections["items"][0]["id"] == first,
      str([row["id"] for row in rec_collections["items"]]))
check("у сборки есть средняя оценка", rec_collections["items"][0]["avg_rating"] == 5.0)
check("популярные категории", recommend.popular_categories(3)["items"][0]["category"] == "cars")

print("[4] совместимость: ссылка и конфликты")


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):  # noqa: N802
        if self.path.startswith("/ok"):
            self.send_response(200)
            self.end_headers()
        elif self.path.startswith("/gone"):
            self.send_response(404)
            self.end_headers()
        elif self.path.startswith("/blocked"):
            self.send_response(403)
            self.end_headers()
        else:
            self.send_response(500)
            self.end_headers()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8971), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

check("файл доступен", compat.check_file("http://127.0.0.1:8971/ok.zip")["status"] == "ok")
check("файл пропал", compat.check_file("http://127.0.0.1:8971/gone.zip")["status"] == "missing")
check("HEAD запрещён — честно неизвестно",
      compat.check_file("http://127.0.0.1:8971/blocked.zip")["status"] == "unknown")
check("ошибка сервера — неизвестно",
      compat.check_file("http://127.0.0.1:8971/err.zip")["status"] == "unknown")
check("битая ссылка — неизвестно", compat.check_file("")["status"] == "unknown")

print("[5] отчёт по моду")
db.upsert_mod({"id": "mg:6", "source": "modsgaming", "url": "http://127.0.0.1:8971/ok",
               "title": "Nissan Silvia S15 Rocket Bunny", "category": "cars",
               "game_version": "0.40.x", "download_url": "http://127.0.0.1:8971/ok.zip"})
db.installed_add({"key": "dup.zip", "mod_id": None, "filename": "dup.zip",
                  "path": str(TMP / "dup.zip"), "size": 1,
                  "title": "Nissan Silvia S15 Rocket Bunny", "version": "1.0"})
report = compat.report("mg:6")
check("отчёт строится", report["ok"], str(report)[:200])
check("файл проверен", report["verdict"]["file"] == "проверено BeamHub", str(report["verdict"]))
check("конфликт найден", report["verdict"]["conflicts"] == "найдены"
      and report["conflicts"], str(report["conflicts"]))
check("версия игры заявлена автором", report["verdict"]["game"] == "заявлено автором")
check("совпадение версии с установленной игрой не выдумано",
      report["game"]["matches"] is None or isinstance(report["game"]["matches"], bool))
check("неизвестный мод", not compat.report("nope")["ok"])
server.shutdown()

print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
