"""Живая проверка входа через Google на фейковом Supabase.

Поднимает фейковый GoTrue, запускает настоящее приложение с временной базой,
проходит поток: start → callback → poll → /api/auth/me.
Запуск:  py tools\\cloud_e2e.py
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
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_NO_CLOUD_DEFAULTS"] = "1"
FAKE_PORT = 8985
APP_PORT = 8987
USER = {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "email": "cloud@example.com",
        "user_metadata": {"full_name": "Cloud Rider", "avatar_url": "https://x/a.png"}}


class FakeGoTrue(BaseHTTPRequestHandler):
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
        if parsed.path == "/auth/v1/token" and "pkce" in parsed.query:
            if body.get("auth_code") != "good-code" or not body.get("code_verifier"):
                self._json(400, {"error_description": "код не принят"})
                return
            self._json(200, {"access_token": "at-live", "refresh_token": "rt-live",
                             "expires_in": 3600, "user": USER})
            return
        self._json(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802
        if self.path == "/auth/v1/user":
            self._json(200, USER)
            return
        if self.path.startswith("/auth/v1/authorize"):
            self.send_response(302)
            self.send_header(
                "Location",
                "https://accounts.google.com/o/oauth2/v2/auth"
                "?client_id=123-abc.apps.googleusercontent.com")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def api(path: str, payload: dict | None = None, token: str = "", local: str = "") -> dict:
    headers = {"X-BeamHub-Token": local, "X-BeamHub-User": token,
               "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{APP_PORT}{path}", data=data,
                                     headers=headers,
                                     method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    fake = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), FakeGoTrue)
    threading.Thread(target=fake.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp(prefix="beamhub-e2e-"))
    env = {**os.environ, "BEAMHUB_DB": str(tmp / "e2e.db"), "PYTHONUTF8": "1"}
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--no-browser", "--port", str(APP_PORT),
         "--no-enrich"],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/api/ping", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/", timeout=10) as resp:
            html = resp.read().decode()
        local = re.search(r'name="beamhub-token" content="([^"]+)"', html).group(1)

        print("0) сбрасываем настройки облака")
        api("/api/cloud/settings", {"url": "", "anon_key": ""}, local=local)

        print("1) облако не настроено")
        status = api("/api/auth/cloud", local=local)
        print("   configured:", status["configured"])
        start_fail = api("/api/auth/google/start", {}, local=local)
        print("   start без ключей:", start_fail)
        assert not status["configured"] and not start_fail["ok"]

        print("2) сохраняем настройки Supabase (фейковый адрес)")
        saved = api("/api/cloud/settings",
                    {"url": f"http://127.0.0.1:{FAKE_PORT}", "anon_key": "anon-live"},
                    local=local)
        print("   сохранено, configured:", saved.get("configured"))
        assert saved.get("ok") and saved.get("configured")

        print("3) старт входа")
        start = api("/api/auth/google/start", {}, local=local)
        state = start.get("state", "")
        print("   state:", state[:10], "url:", start.get("url", "")[:80])
        assert start.get("ok") and state
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(start["url"]).query)
        assert query["provider"] == ["google"]
        assert query["redirect_to"] == [f"http://127.0.0.1:{APP_PORT}/api/auth/callback"]

        print("4) браузер вернулся с кодом")
        callback_url = (f"http://127.0.0.1:{APP_PORT}/api/auth/callback"
                        f"?code=good-code&state={urllib.parse.quote(state)}")
        with urllib.request.urlopen(callback_url, timeout=20) as resp:
            page = resp.read().decode()
        print("   страница:", "Готово" in page, "| html:", page.startswith("<!doctype"))
        assert "Готово" in page

        print("5) опрос состояния и профиль")
        poll = api(f"/api/auth/google/poll?state={urllib.parse.quote(state)}")
        print("   status:", poll.get("status"), "user:", (poll.get("user") or {}).get("nickname"))
        assert poll["status"] == "done" and poll["user"]["provider"] == "google"
        me = api("/api/auth/me", token=poll["token"])
        print("   /api/auth/me:", me["user"]["nickname"], me["user"]["role"])
        assert me["user"]["nickname"] == "Cloud Rider"

        print("6) повторный state не принимается")
        try:
            with urllib.request.urlopen(callback_url, timeout=10) as resp:
                page2 = resp.read().decode()
            print("   страница:", "устарела" in page2)
            assert "устарела" in page2
        except Exception as exc:  # noqa: BLE001
            print("   ошибка:", type(exc).__name__)

        print("\nИТОГ: облачный вход работает на фейковом Supabase")
        return 0
    finally:
        try:
            api("/api/cloud/settings", {"url": "", "anon_key": ""}, local=local)
        except Exception:  # noqa: BLE001
            pass
        process.terminate()
        fake.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
