"""Тесты платформы: аккаунты, подписки, сборки, статистика.

Запуск:  py tests\\test_platform.py
Работает на отдельной временной базе (BEAMHUB_DB), реальную библиотеку не трогает.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="beamhub-platform-"))
os.environ["BEAMHUB_DB"] = str(TMP / "platform.db")

from core import accounts, collections, db  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


db.init()

print("[1] регистрация и вход")
reg_a = accounts.register("a@example.com", "supersecret1", "Alice")
check("регистрация A", reg_a["ok"], str(reg_a))
token_a = reg_a["token"]
user_a = reg_a["user"]["id"]

reg_b = accounts.register("b@example.com", "supersecret2", "Bob")
check("регистрация B", reg_b["ok"], str(reg_b))
token_b = reg_b["token"]
user_b = reg_b["user"]["id"]

check("повтор email запрещён", not accounts.register("A@example.com", "supersecret3", "Alice2")["ok"])
check("короткий пароль запрещён", not accounts.register("c@example.com", "123", "Carl")["ok"])
check("плохой ник отклонён", not accounts.register("d@example.com", "supersecret4", "x")["ok"])
check("вход по токену", accounts.user_by_token(token_a)["id"] == user_a)
check("неверный пароль", not accounts.login("a@example.com", "wrongpass")["ok"])
check("вход работает", accounts.login("a@example.com", "supersecret1")["ok"])

print("[2] профиль и подписки")
upd = accounts.update_profile(user_a, nickname="AliceRacer", bio="drift enjoyer")
check("ник изменён", upd["ok"] and upd["user"]["nickname"] == "AliceRacer", str(upd))
check("чужой ник занят", not accounts.update_profile(user_b, nickname="AliceRacer")["ok"])
check("подписка", accounts.follow(user_a, user_b)["ok"])
check("повторная подписка не дублируется", accounts.followers_count(user_b) == 1)
check("подписка на себя запрещена", not accounts.follow(user_a, user_a)["ok"])
check("лента подписок", len(accounts.following(user_a)) == 1)
check("отписка", accounts.unfollow(user_a, user_b)["ok"] and accounts.followers_count(user_b) == 0)
check("профиль со статистикой", "stats" in accounts.profile(user_a))

print("[3] сборки")
now = db.now_ts()
db.upsert_mod({"id": "mg:1", "source": "modsgaming", "url": "https://example.com/1",
               "title": "Nissan Silvia S15 Drift", "category": "cars", "version": "1.0",
               "created_at": now})
db.upsert_mod({"id": "mg:2", "source": "modsgaming", "url": "https://example.com/2",
               "title": "Ebisu Circuit", "category": "maps", "version": "2.0",
               "created_at": now})

bad = collections.create(user_a, "ab")
check("короткое название отклонено", not bad["ok"])

created = collections.create(user_a, "JDM Drift Pack", "Классика дрифта",
                             category="jdm", tags="drift, jdm, nissan")
check("создание сборки", created["ok"], str(created))
coll_id = created["collection"]["id"]
check("теги сохранены", created["collection"]["tags"] == ["drift", "jdm", "nissan"])
check("сборка в черновике", created["collection"]["published"] is False)

check("добавление мода", collections.add_item(coll_id, user_a, "mg:1")["ok"])
check("добавление второго", collections.add_item(coll_id, user_a, "mg:2")["ok"])
check("чужой мод не добавить", not collections.add_item(coll_id, user_b, "mg:1")["ok"])
check("несуществующий мод не добавить",
      not collections.add_item(coll_id, user_a, "no:such")["ok"])
check("чужой не может менять", not collections.update(coll_id, user_b, title="Hacked")["ok"])

check("пустую сборку публиковать нельзя",
      not collections.publish(collections.create(user_a, "Empty one")["collection"]["id"],
                              user_a)["ok"])

draft_visible_other = collections.get(coll_id, user_b)
check("черновик скрыт от других", not draft_visible_other["ok"], str(draft_visible_other))

published = collections.publish(coll_id, user_a, "первая версия")
check("публикация", published["ok"], str(published))
check("версия 1 в истории", len(published["collection"]["versions"]) == 1)
check("опубликованный виден другим", collections.get(coll_id, user_b)["ok"])

again = collections.publish(coll_id, user_a, "обновление состава")
check("повторная публикация поднимает версию", again["collection"]["version"] == 2)
check("в истории две версии", len(again["collection"]["versions"]) == 2)

print("[4] установка сборки")
first_plan = collections.plan(coll_id, user_a)
check("план: 2 мода, оба отсутствуют", len(first_plan["missing"]) == 2 and not first_plan["installed"])

db.installed_add({"key": "nissan silvia s15 drift.zip", "mod_id": "mg:1",
                  "filename": "Nissan Silvia S15 Drift.zip", "path": str(TMP / "x.zip"),
                  "size": 10, "title": "Nissan Silvia S15 Drift", "version": "1.0"})
second_plan = collections.plan(coll_id, user_a)
check("установленный мод учтён", second_plan["installed"] == ["mg:1"])
check("недостающий мод остался", [row["mod_id"] for row in second_plan["missing"]] == ["mg:2"])
check("следующий на установку", (second_plan["next"] or {}).get("mod_id") == "mg:2")

print("[5] списки и статистика")
popular = collections.list_collections("popular", viewer_id=user_b)
check("опубликованная сборка в списке", popular["total"] == 1, str(popular["total"]))
mine = collections.list_collections("mine", viewer_id=user_a)
check("свои сборки видны", mine["total"] == 2 and any(c["id"] == coll_id for c in mine["items"]))
search = collections.list_collections("popular", query="jdm")
check("поиск по названию", search["total"] == 1)
check("поиск без совпадений", collections.list_collections("popular", query="offroad")["total"] == 0)

collections.record_event("view", coll_id=coll_id)
collections.record_event("download", coll_id=coll_id, mod_id="mg:2")
collections.record_event("install", coll_id=coll_id, mod_id="mg:1")
collections.record_event("view", mod_id="mg:1")

fresh = collections.get(coll_id, user_a)["collection"]
check("просмотры сборки", fresh["views"] == 1, str(fresh["views"]))
check("скачивания сборки", fresh["downloads"] == 1)
check("установки сборки", fresh["installs"] == 1)
stats = collections.stats()
check("статистика пользователей", stats["users"] == 2)
check("топ модов по установкам", stats["top_mods"] and stats["top_mods"][0]["id"] == "mg:1")
check("активность за 14 дней", len(stats["activity"]) >= 1)

print("[6] удаление")
check("чужой не удалит", not collections.delete(coll_id, user_b)["ok"])
check("автор удаляет", collections.delete(coll_id, user_a)["ok"])
check("сборки больше нет", not collections.get(coll_id, user_a)["ok"])

print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
