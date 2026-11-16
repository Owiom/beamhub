"""Сквозная проверка синхронизации через HTTP-эндпоинты приложения.

Поднимает фейковый Supabase (Auth + PostgREST) и настоящее приложение,
проходит путь: вход через Google → сборка → комментарий и оценка → push →
вторая сборка из облака → pull → жалоба → push/pull модерации.

Запуск:  py tools\\sync_e2e.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_NO_CLOUD_DEFAULTS"] = "1"
FAKE_PORT = 8993
APP_PORT = 8995
CLOUD_UID = "cccccccc-dddd-eeee-ffff-000000000000"
USER = {"id": CLOUD_UID, "email": "sync@example.com",
        "user_metadata": {"full_name": "Sync Rider"}}

STORE: dict[str, list[dict]] = {
    "collections": [], "collection_items": [], "comments": [], "ratings": [],
    "reports": [], "admin_actions": [], "profiles": [],
}


class Fake(BaseHTTPRequestHandler):
    # ---------------------------------------------------------------- helpers
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

    # ---------------------------------------------------------------- auth
    def _auth_post(self, parsed: urllib.parse.SplitResult) -> bool:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if parsed.path == "/auth/v1/token" and "pkce" in parsed.query:
            if body.get("auth_code") != "good-code" or not body.get("code_verifier"):
                self._send(400, {"error_description": "код не принят"})
                return True
            self._send(200, {"access_token": "at-live", "refresh_token": "rt-live",
                             "expires_in": 3600, "user": USER})
            return True
        return False

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/auth/v1/"):
            if self._auth_post(parsed):
                return
            self._send(404, {"error": "not found"})
            return
        table = self._table()
        length = int(self.headers.get("content-length") or 0)
        rows = json.loads(self.rfile.read(length) or b"[]")
        if isinstance(rows, dict):
            rows = [rows]
        target = STORE.setdefault(table, [])
        for row in rows:
            if table == "collection_items":
                target[:] = [old for old in target
                             if not (old["collection_id"] == row["collection_id"]
                                     and old["mod_id"] == row["mod_id"])]
            elif table == "ratings":
                target[:] = [old for old in target if not (
                    old.get("target_type") == row.get("target_type")
                    and old.get("target_id") == row.get("target_id")
                    and old.get("user_id") == row.get("user_id"))]
            elif "id" in row:
                target[:] = [old for old in target if old.get("id") != row["id"]]
            target.append(row)
        self._send(201, rows)

    def do_PATCH(self):  # noqa: N802
        table = self._table()
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        condition = self._params().get("id", "")
        if condition.startswith("eq."):
            for row in STORE.setdefault(table, []):
                if row.get("id") == condition[3:]:
                    row.update(body)
        self._send(204)

    def do_DELETE(self):  # noqa: N802
        table = self._table()
        condition = self._params().get("collection_id", "")
        if condition.startswith("eq."):
            STORE.setdefault(table, [])[:] = [
                row for row in STORE[table] if row.get("collection_id") != condition[3:]]
        self._send(204)

    def do_GET(self):  # noqa: N802
        if urllib.parse.urlsplit(self.path).path == "/auth/v1/user":
            self._send(200, USER)
            return
        if urllib.parse.urlsplit(self.path).path == "/auth/v1/authorize":
            self.send_response(302)
            self.send_header(
                "Location",
                "https://accounts.google.com/o/oauth2/v2/auth"
                "?client_id=123-abc.apps.googleusercontent.com")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        table = self._table()
        params = self._params()
        rows = list(STORE.get(table, []))
        if params.get("published") == "eq.true":
            rows = [row for row in rows if row.get("published")]
        for key, value in (("id", params.get("id")), ("collection_id", params.get("collection_id")),
                           ("target_id", params.get("target_id"))):
            if not value:
                continue
            if value.startswith("eq."):
                rows = [row for row in rows if row.get(key) == value[3:]]
            elif value.startswith("in.("):
                ids = [part.strip().strip('"') for part in value[4:-1].split(",")]
                rows = [row for row in rows if row.get(key) in ids]
        if params.get("target_type", "").startswith("eq."):
            rows = [row for row in rows
                    if row.get("target_type") == params["target_type"][3:]]
        limit = int(params.get("limit") or 200)
        self._send(200, rows[:limit])

    def log_message(self, *args):
        pass


def http(path: str, payload: dict | None = None, token: str = "", local: str = "",
         base: str = "") -> tuple[int, dict | str]:
    headers = {"X-BeamHub-Token": local, "X-BeamHub-User": token,
               "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base or f"http://127.0.0.1:{APP_PORT}{path}",
                                     data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def wait_app() -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/api/ping", timeout=1)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("приложение не поднялось")


def main() -> int:
    fake = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp(prefix="beamhub-synce2e-"))
    env = {**os.environ, "BEAMHUB_DB": str(tmp / "e2e.db"), "PYTHONUTF8": "1"}
    # моды в каталог — нужны, чтобы собрать сборку
    seed = tmp / "seed.py"
    seed.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "from core import db\n"
        "db.init()\n"
        "for i in (1, 2):\n"
        "    db.upsert_mod({'id': f'mg:{i}', 'source': 'modsgaming', 'url': 'u',\n"
        "                   'title': f'Mod {i}', 'category': 'cars'})\n"
        "print('моды готовы')\n", encoding="utf-8")
    subprocess.run([sys.executable, str(seed)], env=env, check=True,
                   stdout=subprocess.DEVNULL)

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--no-browser", "--port", str(APP_PORT),
         "--no-enrich"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_app()
        status, html = http("/")
        local = re.search(r'name="beamhub-token" content="([^"]+)"', html).group(1)

        print("1) настройки облака и вход через Google")
        code, saved = http("/api/cloud/settings",
                           {"url": f"http://127.0.0.1:{FAKE_PORT}", "anon_key": "anon"},
                           local=local)
        assert saved.get("configured"), saved
        code, start = http("/api/auth/google/start", {}, local=local)
        assert start.get("ok"), start
        state = start["state"]
        callback = (f"http://127.0.0.1:{APP_PORT}/api/auth/callback"
                    f"?code=good-code&state={urllib.parse.quote(state)}")
        with urllib.request.urlopen(callback, timeout=25) as resp:
            assert "Готово" in resp.read().decode()
        code, poll = http(f"/api/auth/google/poll?state={urllib.parse.quote(state)}")
        assert poll.get("status") == "done", poll
        token = poll["token"]
        print("   вход выполнен:", poll["user"]["nickname"])

        promote = tmp / "promote.py"
        promote.write_text(
            "import sys\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from core import db\n"
            "db.init()\n"
            f"db.execute(\"UPDATE users SET role='moderator' WHERE provider='google' \"\n"
            f"           \"AND provider_id=?\", (\"{CLOUD_UID}\",))\n"
            "print('роль выдана')\n", encoding="utf-8")
        subprocess.run([sys.executable, str(promote)], env=env, check=True,
                       stdout=subprocess.DEVNULL)

        print("2) создаю сборку, комментарий и оценку")
        code, created = http("/api/collections/create",
                             {"title": "Sync Pack", "description": "для теста",
                              "category": "drift", "tags": "sync, cloud"}, token=token,
                             local=local)
        assert created.get("ok"), created
        coll = created["collection"]["id"]
        for mod_id in ("mg:1", "mg:2"):
            code, added = http("/api/collections/items",
                               {"id": coll, "mod_id": mod_id, "action": "add"},
                               token=token, local=local)
            assert added.get("ok"), added
        http("/api/collections/publish", {"id": coll}, token=token, local=local)
        code, comment = http("/api/comments",
                             {"target_type": "collection", "target_id": coll,
                              "body": "Отзыв из интеграционного теста"}, token=token,
                             local=local)
        assert comment.get("ok"), comment
        code, rating = http("/api/ratings",
                            {"target_type": "collection", "target_id": coll,
                             "stars": 5, "review": "Отлично"}, token=token, local=local)
        assert rating.get("ok"), rating

        print("3) push")
        code, pushed = http("/api/sync/push", {}, token=token, local=local)
        print("   ", {k: pushed.get(k) for k in ("ok", "pushed", "comments", "ratings")})
        assert pushed.get("ok") and pushed["pushed"] == 1, pushed
        assert len(STORE["collections"]) == 1, STORE["collections"]
        assert STORE["collections"][0]["id"] == coll
        assert len(STORE["collection_items"]) == 2
        assert len(STORE["comments"]) == 1 and STORE["comments"][0]["user_id"] == CLOUD_UID
        assert len(STORE["ratings"]) == 1 and STORE["ratings"][0]["stars"] == 5

        print("4) чужая сборка в облаке → pull")
        STORE["collections"].append({
            "id": "col_remote_e2e", "owner_id": "aaaa-remote", "title": "Remote Pack",
            "description": "", "cover": "", "category": "offroad", "tags": ["offroad"],
            "published": True, "version": 1, "views": 7, "installs": 2, "downloads": 0,
            "game_version": "0.40.x", "created_at": 1})
        STORE["collection_items"].append({"collection_id": "col_remote_e2e", "mod_id": "mg:1",
                                          "position": 0, "note": ""})
        STORE["comments"].append({"id": "cmt_remote_e2e", "target_type": "collection",
                                  "target_id": "col_remote_e2e", "user_id": "aaaa-remote",
                                  "parent_id": None, "body": "Комментарий из облака",
                                  "deleted": False, "created_at": 1})
        STORE["ratings"].append({"target_type": "collection", "target_id": "col_remote_e2e",
                                 "user_id": "aaaa-remote", "stars": 4, "review": "",
                                 "installed": False, "created_at": 1})
        STORE["profiles"].append({"id": "aaaa-remote", "nickname": "Remote E2E"})
        code, pulled = http("/api/sync/pull", {}, token=token, local=local)
        print("   ", {k: pulled.get(k) for k in ("ok", "pulled", "comments", "ratings")})
        assert pulled.get("ok") and pulled["pulled"] >= 1, pulled
        code, colls = http("/api/collections?kind=popular", local=local)
        titles = [item["title"] for item in colls.get("items", [])]
        assert "Remote Pack" in titles, titles
        code, comments = http("/api/comments?target_type=collection&target_id=col_remote_e2e",
                              local=local)
        assert any(c.get("body") == "Комментарий из облака" for c in comments.get("items", [])), \
            comments.get("items")
        print("   чужая сборка и комментарий видны локально")

        STORE["reports"].append({
            "id": "rpt_remote_e2e", "target_type": "collection",
            "target_id": "col_remote_e2e", "user_id": "aaaa-remote", "reason": "abuse",
            "details": "жалоба из облака", "status": "open", "resolved_by": None,
            "resolved_at": 0, "created_at": 2})
        STORE["admin_actions"].append({
            "actor_id": "aaaa-remote", "action": "report_resolve", "target_type": "comment",
            "target_id": "cmt_x", "note": "облачное действие e2e", "created_at": 3})

        print("5) жалоба → push → pull модерации")
        code, reported = http("/api/moderation/report",
                              {"target_type": "collection", "target_id": "col_remote_e2e",
                               "reason": "spam", "details": "тест"}, token=token, local=local)
        assert reported.get("ok"), reported
        code, mod_push = http("/api/sync/push", {}, token=token, local=local)
        assert mod_push.get("reports", 0) >= 1, mod_push
        assert len(STORE["reports"]) >= 1, STORE["reports"]
        print("   жалоба ушла в облако:", STORE["reports"][0]["id"])
        code, mod_pull = http("/api/sync/pull", {}, token=token, local=local)
        assert mod_pull.get("ok"), mod_pull
        print("   пул модерации: жалоб", mod_pull.get("reports"), "журнал",
              mod_pull.get("actions"))
        assert mod_pull.get("reports", 0) >= 1, mod_pull
        assert mod_pull.get("actions", 0) >= 1, mod_pull
        code, reports = http("/api/moderation/reports?status=open", token=token, local=local)
        assert any(row["id"] == "rpt_remote_e2e" for row in reports.get("items", [])), reports
        print("   чужая жалоба в локальной очереди")

        print("6) состояние после синхронизации")
        code, stats = http("/api/stats", local=local)
        print("   сборок:", stats.get("collections"), "оценок:", stats.get("ratings"))
        assert stats.get("collections", 0) >= 1

        print("\nИТОГ: синхронизация через HTTP работает")
        return 0
    finally:
        try:
            http("/api/cloud/settings", {"url": "", "anon_key": ""}, local=local)
        except Exception:  # noqa: BLE001
            pass
        process.terminate()
        fake.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
