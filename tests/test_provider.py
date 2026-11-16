"""Проверка провайдеров до входа: Discord/Google Client ID.

Запуск:  py tests\\test_provider.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_NO_CLOUD_DEFAULTS"] = "1"

from core import cloud_check  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def make_handler(bad: bool):
    class FakeAuth(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/auth/v1/authorize":
                self._json(404, {})
                return
            provider = (urllib.parse.parse_qs(parsed.query).get("provider") or ["google"])[0]
            if provider == "discord":
                if bad:
                    self._redirect("https://discord.com/oauth2/authorize"
                                   "?client_id=owiom%27s+project")
                else:
                    self._redirect("https://discord.com/oauth2/authorize"
                                   "?client_id=123456789012345678")
                return
            if provider == "google":
                if bad:
                    self._json(400, {"error": "validation_failed",
                                     "msg": "Unsupported provider: provider is not enabled"})
                else:
                    self._redirect("https://accounts.google.com/o/oauth2/v2/auth"
                                   "?client_id=123-abc.apps.googleusercontent.com")
                return
            self._json(400, {})

        def log_message(self, *args):
            pass

    return FakeAuth


def start(bad: bool, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(bad))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


bad_server = start(True, 8985)
good_server = start(False, 8986)
BAD = "http://127.0.0.1:8985"
GOOD = "http://127.0.0.1:8986"

print("[1] Discord")
problem = cloud_check.provider_problem(BAD, "key", "discord", "http://x/cb")
check("название вместо Client ID поймано", "числовой ID" in problem, problem)
check("подсказка про OAuth2", "OAuth2" in problem, problem)
good = cloud_check.provider_problem(GOOD, "key", "discord", "http://x/cb")
check("нормальный ID проходит", good == "", good)

print("[2] Google")
off = cloud_check.provider_problem(BAD, "key", "google", "http://x/cb")
check("выключенный провайдер объяснён", "not enabled" in off, off)
google_ok = cloud_check.provider_problem(GOOD, "key", "google", "http://x/cb")
check("нормальный Google ID проходит", google_ok == "", google_ok)

print("[3] устойчивость")
check("недоступная сеть не блокирует вход",
      cloud_check.provider_problem("http://127.0.0.1:9/", "key", "discord", "http://x/cb") == "")

bad_server.shutdown()
good_server.shutdown()
print()
if failures:
    print("ПРОВАЛОВ:", len(failures))
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("ПРОВАЛОВ: нет")
