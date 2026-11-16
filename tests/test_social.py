"""Тесты комментариев, ответов и оценок.

Запуск:  py tests\\test_social.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="beamhub-social-"))
os.environ["BEAMHUB_DB"] = str(TMP / "social.db")

from core import accounts, collections, db, social  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


db.init()
alice = accounts.register("alice@example.com", "supersecret1", "Alice")["user"]["id"]
bob = accounts.register("bob@example.com", "supersecret2", "Bob")["user"]["id"]
moder = accounts.register("mod@example.com", "supersecret3", "Moderator")["user"]["id"]
db.execute("UPDATE users SET role='moderator' WHERE id=?", (moder,))

db.upsert_mod({"id": "mg:1", "source": "modsgaming", "url": "https://example.com/1",
               "title": "Silvia S15", "category": "cars", "version": "1.0"})
db.upsert_mod({"id": "mg:2", "source": "modsgaming", "url": "https://example.com/2",
               "title": "Ebisu", "category": "maps", "version": "2.0"})
coll = collections.create(alice, "Drift Pack", "сборка", category="drift")["collection"]["id"]
collections.add_item(coll, alice, "mg:1")
collections.add_item(coll, alice, "mg:2")
collections.publish(coll, alice)

print("[1] комментарии")
root1 = social.add_comment(bob, "collection", coll, "Отличная подборка!")
check("комментарий создан", root1["ok"], str(root1))
reply = social.add_comment(alice, "collection", coll, "Спасибо!", root1["id"])
check("ответ создан", reply["ok"], str(reply))
reply2 = social.add_comment(moder, "collection", coll, "А где карта?", reply["id"])
check("ответ на ответ создан", reply2["ok"], str(reply2))

listing = social.comments("collection", coll, bob)
check("корневой комментарий один", len(listing["items"]) == 1, str(len(listing["items"])))
check("в ветке два ответа", len(listing["items"][0]["replies"]) == 2)
check("вложенность плоская", all(not r["replies"] for r in listing["items"][0]["replies"]))
check("счётчик без удалённых", listing["count"] == 3)

check("пустой комментарий отклонён",
      not social.add_comment(bob, "collection", coll, "   ")["ok"])
check("слишком длинный отклонён",
      not social.add_comment(bob, "collection", coll, "x" * 1600)["ok"])
check("чужой объект отклонён", not social.add_comment(bob, "collection", "col_nope", "привет")["ok"])
check("ответ на несуществующий отклонён",
      not social.add_comment(bob, "collection", coll, "текст", "cmt_nope")["ok"])

print("[2] правка и удаление комментариев")
check("автор правит свой", social.update_comment(bob, root1["id"], "Обновлённый текст")["ok"])
check("чужой не правит", not social.update_comment(alice, root1["id"], "подмена")["ok"])
check("пустая правка отклонена", not social.update_comment(bob, root1["id"], "")["ok"])
check("автор удаляет свой", social.delete_comment(bob, root1["id"])["ok"])
after = social.comments("collection", coll, bob)
check("удалённый скрыт", after["items"][0]["deleted"] and after["items"][0]["body"] == "")
check("счётчик уменьшился", after["count"] == 2, str(after["count"]))
check("чужой не удаляет", not social.delete_comment(alice, reply2["id"])["ok"])
check("модератор удаляет свой без записи в журнал",
      social.delete_comment(moder, reply2["id"], True)["ok"])
check("модератор удаляет чужой", social.delete_comment(moder, reply["id"], True)["ok"])
check("в журнале модерации запись",
      len(db.query("SELECT * FROM admin_actions WHERE action='delete_comment'")) == 1)

print("[3] оценки")
check("звёзды вне диапазона отклонены",
      not social.set_rating(bob, "collection", coll, 6)["ok"])
check("текст вместо числа отклонён",
      not social.set_rating(bob, "collection", coll, "пять")["ok"])
first = social.set_rating(bob, "collection", coll, 4, "Хорошо, но маловато карт")
check("оценка поставлена", first["ok"], str(first))
check("сводка: 1 оценка, среднее 4", first["summary"]["count"] == 1
      and first["summary"]["avg"] == 4.0, str(first["summary"]))

second = social.set_rating(bob, "collection", coll, 5, "Передумал — отлично")
check("повторная оценка обновляет, а не добавляет", second["summary"]["count"] == 1)
check("среднее обновилось", second["summary"]["avg"] == 5.0)
check("в базе одна запись",
      len(db.query("SELECT * FROM ratings WHERE user_id=?", (bob,))) == 1)

social.set_rating(alice, "collection", coll, 5, "Моя же сборка, но пусть")
summary = social.rating_summary("collection", coll)
check("две оценки, среднее 5", summary["count"] == 2 and summary["avg"] == 5.0, str(summary))
check("распределение по звёздам", summary["distribution"][5] == 2 and summary["distribution"][4] == 0)
check("не установившие не отмечены", summary["installed_reviews"] == 0)

print("[4] отметка «установил»")
db.installed_add({"key": "silvia.zip", "mod_id": "mg:1", "filename": "Silvia.zip",
                  "path": str(TMP / "silvia.zip"), "size": 10, "title": "Silvia S15",
                  "version": "1.0"})
social.set_rating(bob, "collection", coll, 5, "Поставил, всё работает")
check("после установки отметка появилась",
      social.my_rating(bob, "collection", coll)["installed"] == 1)
check("сводка учитывает установивших",
      social.rating_summary("collection", coll)["installed_reviews"] == 1)

only_installed = social.reviews("collection", coll, only_installed=True)
check("фильтр отзывов установивших", len(only_installed["items"]) == 1
      and only_installed["items"][0]["nickname"] == "Bob")
check("отзыв с текстом сохранён", only_installed["items"][0]["review"] == "Поставил, всё работает")
check("флаг «мой отзыв»", social.reviews("collection", coll, bob)["items"][0]["is_mine"])

print("[5] удаление оценки и лимиты")
check("удаление своей оценки", social.delete_rating(bob, "collection", coll)["ok"])
check("осталась одна", social.rating_summary("collection", coll)["count"] == 1)

limit_user = accounts.register("spam@example.com", "supersecret4", "Spammer")["user"]["id"]
allowed = sum(1 for i in range(social.COMMENT_LIMIT) if social.add_comment(
    limit_user, "collection", coll, f"спам {i}")["ok"])
blocked = social.add_comment(limit_user, "collection", coll, "ещё один")
check("лимит комментариев сработал",
      allowed == social.COMMENT_LIMIT and not blocked["ok"], f"allowed={allowed}")

print("[6] моды как объект оценки")
mod_rating = social.set_rating(alice, "mod", "mg:1", 5, "Машина огонь")
check("оценка мода", mod_rating["ok"], str(mod_rating))
check("сводка мода", social.rating_summary("mod", "mg:1")["count"] == 1)
check("сводка мода не смешивается со сборкой",
      social.rating_summary("collection", coll)["count"] == 1)

print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
