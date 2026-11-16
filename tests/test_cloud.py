"""Тесты облачного входа: PKCE, обмен кода, профиль, локальная привязка аккаунта.

Сеть не нужна: поднимается локальный «фейковый Supabase».
Запуск:  py tests\\test_cloud.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_DB"] = str(Path(tempfile.mkdtemp(prefix="beamhub-cloud-")) / "cloud.db")

from core import accounts, cloud, db  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


USER = {"id": "11111111-2222-3333-4444-555555555555", "email": "rider@example.com",
        "user_metadata": {"full_name": "Cloud Rider", "avatar_url": "https://x/a.png"}}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if parsed.path == "/auth/v1/token" and "grant_type=pkce" in parsed.query:
            if body.get("auth_code") != "good-code":
                self._json(400, {"error": "invalid_grant",
                                 "error_description": "код не принят"})
                return
            if not body.get("code_verifier"):
                self._json(400, {"error": "invalid_request",
                                 "error_description": "нет code_verifier"})
                return
            self._json(200, {"access_token": "at-1", "refresh_token": "rt-1",
                             "expires_in": 3600, "user": USER})
            return
        if parsed.path == "/auth/v1/token" and "grant_type=refresh_token" in parsed.query:
            if body.get("refresh_token") != "rt-1":
                self._json(400, {"error_description": "refresh не принят"})
                return
            self._json(200, {"access_token": "at-2", "refresh_token": "rt-2", "user": USER})
            return
        self._json(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802
        if self.path == "/auth/v1/user":
            if self.headers.get("authorization") == "Bearer at-1":
                self._json(200, USER)
                return
            self._json(401, {"msg": "нет токена"})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8981), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8981"
KEY = "anon-test-key"

db.init()
from core import config as _config  # noqa: E402
_config.update({"cloud_url": "", "cloud_anon_key": "",
                "cloud_uid": "", "cloud_refresh": ""})

print("[1] настройка облака")
check("по умолчанию облако не настроено", not cloud.configured())
original_settings = cloud.settings
cloud.settings = lambda: ("https://demo.supabase.co", KEY)  # type: ignore[assignment]
check("с ключами облако считается настроенным", cloud.configured())
cloud.settings = lambda: ("http://insecure.example", KEY)  # type: ignore[assignment]
check("http без https не принимается", not cloud.configured())
cloud.settings = original_settings  # type: ignore[assignment]

print("[2] PKCE")
verifier = cloud.make_verifier()
check("verifier длиной 43–128", 43 <= len(verifier) <= 128, str(len(verifier)))
challenge = cloud.challenge_for(verifier)
expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
check("challenge = S256(verifier)", challenge == expected)
check("challenge без паддинга", "=" not in challenge)
url = cloud.authorize_url(BASE, "http://127.0.0.1:8787/api/auth/callback", "st-1", verifier)
query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
check("в ссылке google", query.get("provider") == ["google"])
check("в ссылке redirect", query.get("redirect_to") == ["http://127.0.0.1:8787/api/auth/callback"])
check("в ссылке challenge", query.get("code_challenge") == [challenge])
check("метод S256", query.get("code_challenge_method") == ["s256"])
check("state передаётся", query.get("state") == ["st-1"])

print("[3] обмен кода и профиль")
ok = cloud.exchange_code(BASE, KEY, "good-code", verifier)
check("обмен кода", ok["ok"] and ok["access_token"] == "at-1", str(ok))
check("refresh-токен получен", ok.get("refresh_token") == "rt-1")
bad = cloud.exchange_code(BASE, KEY, "bad-code", verifier)
check("плохой код отклонён", not bad["ok"] and "код" in bad["error"].lower(), str(bad))
no_verifier = cloud.exchange_code(BASE, KEY, "good-code", "")
check("без verifier сервер отказывает", not no_verifier["ok"])
profile = cloud.me(BASE, KEY, "at-1")
check("профиль получен", profile["ok"] and profile["user"]["email"] == "rider@example.com")
check("чужой токен не проходит", not cloud.me(BASE, KEY, "at-x")["ok"])
refreshed = cloud.refresh(BASE, KEY, "rt-1")
check("обновление токена", refreshed["ok"] and refreshed["access_token"] == "at-2")
check("плохой refresh отклонён", not cloud.refresh(BASE, KEY, "rt-x")["ok"])

print("[4] ник и аватар из Google")
check("ник из full_name", cloud.nickname_from(USER) == "Cloud Rider")
check("ник из email, если нет имени",
      cloud.nickname_from({"email": "some.user@example.com"}) == "some.user")
check("аватар из метаданных", cloud.picture_from(USER) == "https://x/a.png")
check("пустой аватар без ошибки", cloud.picture_from({}) == "")

print("[5] привязка к локальному аккаунту")
first = accounts.upsert_provider_user("google", USER["id"], USER["email"],
                                      cloud.nickname_from(USER))
check("аккаунт создан", first["ok"] and first["user"]["provider"] == "google", str(first))
check("токен сессии выдан", bool(first.get("token")))
second = accounts.upsert_provider_user("google", USER["id"], USER["email"], "Other Name")
check("повторный вход — тот же пользователь", second["user"]["id"] == first["user"]["id"])
check("в базе один пользователь", db.query_one(
    "SELECT COUNT(*) AS c FROM users WHERE provider='google'")["c"] == 1)
check("вход по токену работает",
      accounts.user_by_token(first["token"])["id"] == first["user"]["id"])

print("[6] состояния OAuth")
store = cloud.SessionStore()
store.add("st-1", verifier, "http://127.0.0.1:8787/api/auth/callback")
item = store.pop("st-1")
check("state найден и удалён", item and item["verifier"] == verifier)
check("повторно state не срабатывает", store.pop("st-1") is None)
store.add("st-2", verifier, "x")
resource = store._items["st-2"]
resource["created"] = time.time() - 3600
check("просроченный state отклонён", store.pop("st-2") is None)

server.shutdown()
print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
