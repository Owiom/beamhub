"""Тесты синхронизации сборок с облаком (фейковый PostgREST).

Запуск:  py tests\\test_sync.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_DB"] = str(Path(tempfile.mkdtemp(prefix="beamhub-sync-")) / "sync.db")

from core import (accounts, cloud, cloud_moderation, cloud_session, cloud_social,
                  cloud_sync, collections, db, moderation, social)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


STORE: dict[str, list[dict]] = {"collections": [], "collection_items": [], "profiles": [],
                                "comments": [], "ratings": [], "reports": [],
                                "admin_actions": []}


class FakePostgrest(BaseHTTPRequestHandler):
    def _send(self, code: int, payload=None) -> None:
        raw = json.dumps(payload if payload is not None else []).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _params(self) -> dict:
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlsplit(self.path).query).items()}

    def _table(self) -> str:
        return urllib.parse.urlsplit(self.path).path.rsplit("/", 1)[-1]

    def do_POST(self):  # noqa: N802
        table = self._table()
        length = int(self.headers.get("content-length") or 0)
        rows = json.loads(self.rfile.read(length) or b"[]")
        if isinstance(rows, dict):
            rows = [rows]
        target = STORE.setdefault(table, [])
        for row in rows:
            key = "id" if "id" in row else None
            if table == "collection_items":
                target[:] = [old for old in target
                             if not (old["collection_id"] == row["collection_id"]
                                     and old["mod_id"] == row["mod_id"])]
                target.append(row)
                continue
            if table == "ratings":
                target[:] = [old for old in target if not (
                    old.get("target_type") == row.get("target_type")
                    and old.get("target_id") == row.get("target_id")
                    and old.get("user_id") == row.get("user_id"))]
                target.append(row)
                continue
            if key:
                target[:] = [old for old in target if old.get(key) != row[key]]
            target.append(row)
        self._send(201, rows)

    def do_DELETE(self):  # noqa: N802
        table = self._table()
        params = self._params()
        target = STORE.setdefault(table, [])
        condition = params.get("collection_id", "")
        if condition.startswith("eq."):
            value = condition[3:]
            target[:] = [row for row in target if row.get("collection_id") != value]
        self._send(204)

    def do_PATCH(self):  # noqa: N802
        table = self._table()
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        params = self._params()
        target = STORE.setdefault(table, [])
        condition = params.get("id", "")
        if condition.startswith("eq."):
            value = condition[3:]
            for row in target:
                if row.get("id") == value:
                    row.update(body)
        self._send(204)

    def do_GET(self):  # noqa: N802
        table = self._table()
        params = self._params()
        rows = list(STORE.get(table, []))
        if "published" in params and params["published"] == "eq.true":
            rows = [row for row in rows if row.get("published")]
        if "id" in params and params["id"].startswith("eq."):
            value = params["id"][3:]
            rows = [row for row in rows if row.get("id") == value]
        if "target_type" in params and params["target_type"].startswith("eq."):
            value = params["target_type"][3:]
            rows = [row for row in rows if row.get("target_type") == value]
        if "target_id" in params:
            cond = params["target_id"]
            if cond.startswith("eq."):
                rows = [row for row in rows if row.get("target_id") == cond[3:]]
            elif cond.startswith("in.("):
                ids = [part.strip().strip('"') for part in cond[4:-1].split(",")]
                rows = [row for row in rows if row.get("target_id") in ids]
        if "id" in params and params["id"].startswith("in.("):
            ids = [part.strip().strip('"') for part in params["id"][4:-1].split(",")]
            rows = [row for row in rows if row.get("id") in ids]
        if "collection_id" in params:
            cond = params["collection_id"]
            if cond.startswith("eq."):
                rows = [row for row in rows if row.get("collection_id") == cond[3:]]
            elif cond.startswith("in.("):
                ids = [part.strip().strip('"') for part in cond[4:-1].split(",")]
                rows = [row for row in rows if row.get("collection_id") in ids]
        rows = sorted(rows, key=lambda row: (row.get("position", 0), row.get("created_at", 0)))
        self._send(200, rows)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8991), FakePostgrest)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8991"
cloud.settings = lambda: (BASE, "anon-test")  # type: ignore[assignment]

db.init()
cloud_session.clear()  # сброс прошлых настроек облака
CLOUD_UID = "99999999-8888-7777-6666-555555555555"

print("[1] статус")
status = cloud_sync.status()
check("облако настроено", status["configured"])
check("без входа не готово к отправке", not status["signed_in"])

print("[2] отправка сборок")
guest = accounts.register("g@example.com", "supersecret1", "Guest")["user"]["id"]
google_local = accounts.upsert_provider_user("google", CLOUD_UID, "cloud@example.com",
                                             "Cloud Author")["user"]["id"]
coll = collections.create(google_local, "Cloud Drift", "облачная сборка",
                          category="drift", tags="drift, cloud")["collection"]["id"]
db.upsert_mod({"id": "mg:1", "source": "modsgaming", "url": "u", "title": "Car A",
               "category": "cars"})
db.upsert_mod({"id": "mg:2", "source": "modsgaming", "url": "u", "title": "Map B",
               "category": "maps"})
collections.add_item(coll, google_local, "mg:1")
collections.add_item(coll, google_local, "mg:2")
collections.publish(coll, google_local)

cfg = db.get_meta("cloud_sync_seeded")
cloud_session.set_session(google_local, CLOUD_UID, "at-test", "rt-test")
check("сессия облака активна", cloud_session.signed_in())

draft = collections.create(google_local, "Draft only")["collection"]["id"]
check("черновик отправлять нельзя", cloud_sync.push()["pushed"] == 1,
      str(len(STORE["collections"])))
check("в облако ушла одна сборка", len(STORE["collections"]) == 1)
check("поля сборки на месте", STORE["collections"][0]["title"] == "Cloud Drift"
      and STORE["collections"][0]["category"] == "drift")
check("теги как массив", STORE["collections"][0]["tags"] == ["drift", "cloud"])
check("состав отправлен", len(STORE["collection_items"]) == 2)
check("позиции по порядку", [row["position"] for row in STORE["collection_items"]] == [0, 1])

collections.remove_item(coll, google_local, "mg:2")
cloud_sync.push()
check("повторная отправка обновляет состав", len(STORE["collection_items"]) == 1)
check("сборка не задвоилась", len(STORE["collections"]) == 1)

print("[3] получение сборок из облака")
STORE["collections"].append({
    "id": "col_remote_1", "owner_id": "aaaa-bbbb", "title": "Offroad Pack",
    "description": "чужой сборка", "cover": "", "category": "offroad",
    "tags": ["offroad"], "published": True, "version": 2, "views": 5, "installs": 3,
    "downloads": 1, "game_version": "0.40.x", "created_at": 1,
})
STORE["collection_items"].append({"collection_id": "col_remote_1", "mod_id": "mg:2",
                                  "position": 0, "note": ""})
STORE["profiles"].append({"id": "aaaa-bbbb", "nickname": "Remote Guru"})

pulled = cloud_sync.pull()
check("pull выполнился", pulled["ok"] and pulled["pulled"] == 2, str(pulled))
remote = db.query_one("SELECT * FROM collections WHERE id='col_remote_1'")
check("чужая сборка сохранена", remote is not None)
check("помечена как удалённая", bool(remote["remote"]))
check("состав сохранён", len(collections.items("col_remote_1")) == 1)
owner = db.query_one("SELECT * FROM users WHERE id=?", (remote["owner_id"],))
check("автор-заглушка с ником", owner and owner["nickname"] == "Remote Guru", str(owner))

for row in STORE["collections"]:
    if row["id"] == "col_remote_1":
        row["views"] += 10
        row["version"] = 3
cloud_sync.pull()
check("повторный pull обновляет, не дублирует",
      len(db.query("SELECT * FROM collections WHERE id='col_remote_1'")) == 1)
check("просмотры обновились",
      db.query_one("SELECT views FROM collections WHERE id='col_remote_1'")["views"] == 15)
check("локальная отправленная сборка не затёрта как remote",
      db.query_one("SELECT remote FROM collections WHERE id=?", (coll,))["remote"] == 0)


print("[5] синхронизация отзывов")
cloud_session.set_session(google_local, CLOUD_UID, "at-test", "rt-test")
my_comment = social.add_comment(google_local, "collection", coll, "Моя сборка, мой отзыв")["id"]
social.set_rating(google_local, "collection", coll, 5, "Пять звёзд из облака")
pushed_social = cloud_social.push()
check("отзывы отправлены", pushed_social["ok"] and pushed_social["comments"] == 1
      and pushed_social["ratings"] == 1, str(pushed_social))
cloud_comments = [row for row in STORE["comments"] if row.get("target_id") == coll]
check("комментарий в облаке", len(cloud_comments) == 1, str(cloud_comments))
check("автор комментария — облачный uid",
      cloud_comments[0]["user_id"] == CLOUD_UID)
cloud_ratings = [row for row in STORE["ratings"] if row.get("target_id") == coll]
check("оценка в облаке", len(cloud_ratings) == 1)
check("оценка с текстом", cloud_ratings[0]["review"] == "Пять звёзд из облака")

STORE["comments"].append({
    "id": "cmt_remote_1", "target_type": "collection", "target_id": "col_remote_1",
    "user_id": "aaaa-bbbb", "parent_id": None, "body": "Чужой комментарий из облака",
    "deleted": False, "created_at": 1,
})
STORE["ratings"].append({
    "target_type": "collection", "target_id": "col_remote_1", "user_id": "aaaa-bbbb",
    "stars": 4, "review": "Хорошая сборка", "installed": True, "created_at": 1,
})
pulled_social = cloud_social.pull()
check("отзывы получены", pulled_social["ok"] and pulled_social["comments"] >= 1,
      str(pulled_social))
remote_comments = social.comments("collection", "col_remote_1")["items"]
check("чужой комментарий сохранён", len(remote_comments) == 1)
check("автор-заглушка подставлен", remote_comments[0]["author"]["nickname"] == "Remote Guru")
check("оценка чужой сборки", social.rating_summary("collection", "col_remote_1")["count"] == 1)
check("отметка установившего сохранена",
      social.reviews("collection", "col_remote_1")["items"][0]["installed"])

cloud_social.pull()
check("повторный pull не дублирует комментарии",
      len(db.query("SELECT * FROM comments WHERE target_id='col_remote_1'")) == 1)
check("повторный pull не дублирует оценки",
      len(db.query("SELECT * FROM ratings WHERE target_id='col_remote_1'")) == 1)


print("[6] синхронизация модерации")
report_id = moderation.report(google_local, "collection", coll, "spam", "реклама в сборке")["id"]
mod_user = db.query_one("SELECT * FROM users WHERE id=?", (google_local,))
db.execute("UPDATE users SET role='moderator' WHERE id=?", (google_local,))
mod_user = db.query_one("SELECT * FROM users WHERE id=?", (google_local,))
pushed_mod = cloud_moderation.push(user=mod_user)
check("жалоба ушла в облако", pushed_mod["ok"] and pushed_mod["reports"] >= 1,
      str(pushed_mod))
cloud_reports = [row for row in STORE["reports"] if row.get("id") == report_id]
check("жалоба в облаке", len(cloud_reports) == 1, str(cloud_reports))
check("автор жалобы — облачный uid",
      cloud_reports and cloud_reports[0]["user_id"] == CLOUD_UID)

db.execute("UPDATE reports SET status='resolved' WHERE id=?", (report_id,))
resolution = cloud_moderation.push_resolution(report_id, "resolved", CLOUD_UID)
check("решение отправлено", resolution.get("ok"), str(resolution))
check("статус в облаке обновлён",
      [row for row in STORE["reports"] if row["id"] == report_id][0]["status"] == "resolved")

STORE["reports"].append({
    "id": "rpt_remote_1", "target_type": "collection", "target_id": "col_remote_1",
    "user_id": "aaaa-bbbb", "reason": "abuse", "details": "чужой жалоба",
    "status": "open", "resolved_by": None, "resolved_at": 0, "created_at": 1,
})
STORE["admin_actions"].append({
    "actor_id": "aaaa-bbbb", "action": "report_resolve", "target_type": "comment",
    "target_id": "cmt_x", "note": "облачное действие", "created_at": 2,
})
pulled_mod = cloud_moderation.pull(user=mod_user)
check("очередь забрана", pulled_mod["ok"] and pulled_mod["reports"] >= 1, str(pulled_mod))
remote_report = db.query_one("SELECT * FROM reports WHERE id='rpt_remote_1'")
check("чужая жалоба сохранена", remote_report is not None)
reporter = db.query_one("SELECT nickname FROM users WHERE id=?", (remote_report["user_id"],))
check("автор жалобы — заглушка с ником", reporter and reporter["nickname"] == "Remote Guru",
      str(reporter))
check("журнал забран", pulled_mod["actions"] >= 1, str(pulled_mod))
check("действие модератора в локальном журнале",
      db.query_one("SELECT id FROM admin_actions WHERE note='облачное действие'") is not None)

cloud_moderation.pull(user=mod_user)
check("повторный pull не дублирует жалобы",
      len(db.query("SELECT * FROM reports WHERE id='rpt_remote_1'")) == 1)
check("повторный pull не дублирует журнал",
      len(db.query("SELECT * FROM admin_actions WHERE note='облачное действие'")) == 1)

reader = accounts.register("reader@example.com", "supersecret9", "Reader")["user"]["id"]
check("обычный пользователь не забирает очередь",
      cloud_moderation.pull(user=db.query_one("SELECT * FROM users WHERE id=?", (reader,))
                            ).get("reports", 0) == 0)

print("[4] ошибки")
cloud_session.clear()
check("без входа отправка не идёт", not cloud_sync.push()["ok"])
cloud.settings = lambda: ("", "")  # type: ignore[assignment]
check("без облака pull не идёт", not cloud_sync.pull()["ok"])
check("без облака push не идёт", not cloud_sync.push()["ok"])

server.shutdown()
print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
