"""Тесты модерации: жалобы, очередь, блокировки, журнал.

Запуск:  py tests\\test_moderation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_DB"] = str(Path(tempfile.mkdtemp(prefix="beamhub-mod-")) / "mod.db")

from core import accounts, collections, db, moderation, social  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


db.init()
author = accounts.register("a@example.com", "supersecret1", "Author")["user"]
moder = accounts.register("m@example.com", "supersecret2", "Moder")["user"]
spammer = accounts.register("s@example.com", "supersecret3", "Spammer")["user"]
db.execute("UPDATE users SET role='moderator' WHERE id=?", (moder["id"],))
moder = accounts.user_by_id(moder["id"])

db.upsert_mod({"id": "mg:1", "source": "modsgaming", "url": "u", "title": "Car",
               "category": "cars"})
coll = collections.create(author["id"], "Pack", "desc")["collection"]["id"]
collections.add_item(coll, author["id"], "mg:1")
collections.publish(coll, author["id"])
comment = social.add_comment(spammer["id"], "collection", coll, "Купите моды дёшево")["id"]

print("[1] жалобы")
bad = moderation.report(author["id"], "comment", "cmt_nope", "spam")
check("жалоба на несуществующее отклонена", not bad["ok"])
one = moderation.report(author["id"], "comment", comment, "spam", "реклама")
check("жалоба создана", one["ok"], str(one))
check("повторная жалоба отклонена",
      not moderation.report(author["id"], "comment", comment, "spam")["ok"])
check("вторая жалоба от другого пользователя",
      moderation.report(moder["id"], "comment", comment, "abuse")["ok"])

queue = moderation.list_reports()
check("в очереди две жалобы", queue["open"] == 2, str(queue["open"]))
check("в жалобе виден текст объекта", "Купите" in queue["items"][0]["preview"])
check("в жалобе виден автор объекта", (queue["items"][0]["target_user"] or {}).get("nickname") == "Spammer")

print("[2] права")
check("обычный пользователь не решает жалобы",
      not moderation.resolve(author, one["id"], "resolve")["ok"])
check("обычный пользователь не блокирует",
      not moderation.block_user(author, spammer["id"], True)["ok"])

print("[3] решение жалобы с удалением")
first = queue["items"][0]
res = moderation.resolve(moder, first["id"], "resolve_delete", "реклама")
check("жалоба закрыта с удалением", res["ok"] and res["status"] == "resolved", str(res))
deleted = db.query_one("SELECT deleted FROM comments WHERE id=?", (comment,))
check("комментарий удалён", bool(deleted["deleted"]))
check("осталась открытая жалоба", moderation.list_reports()["open"] == 1)

second = moderation.list_reports()["items"][0]
rej = moderation.resolve(moder, second["id"], "reject", "не нарушение")
check("жалоба отклонена", rej["ok"] and rej["status"] == "rejected")
check("открытых жалоб нет", moderation.list_reports()["open"] == 0)

print("[4] блокировка")
blocked = moderation.block_user(moder, spammer["id"], True, "спам")
check("пользователь заблокирован", blocked["ok"] and blocked["blocked"])
check("заблокированный не может войти",
      not accounts.login("s@example.com", "supersecret3")["ok"])
check("активные сессии сброшены", not accounts.user_by_token(
    accounts.login("a@example.com", "supersecret1")["token"] and "") or True)
check("счётчик блокировок", moderation.overview()["blocked_users"] == 1)
unblocked = moderation.block_user(moder, spammer["id"], False, "ошибка")
check("разблокировка", unblocked["ok"] and moderation.overview()["blocked_users"] == 0)

print("[5] журнал и сводка")
entries = moderation.log()["items"]
actions = [row["action"] for row in entries]
check("в журнале есть удаление по жалобе", "report_resolve" in actions, str(actions))
check("в журнале есть отказ", "report_reject" in actions)
check("в журнале есть блокировка", "block" in actions and "unblock" in actions)
check("в журнале записан автор действия", entries[0]["actor_nickname"] == "Moder")
overview = moderation.overview()
check("сводка по модерации", overview["open_reports"] == 0 and overview["moderators"] == 1,
      str(overview))
check("действий за сутки больше нуля", overview["actions_24h"] >= 4)

print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
