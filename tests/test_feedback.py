"""Проверка отправки отчёта на локальный приёмник."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import feedback  # noqa: E402

received: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        try:
            received.append(json.loads(raw))
        except json.JSONDecodeError:
            received.append({"raw": raw[:100]})
        if self.path.startswith("/fail"):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8931), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

report = feedback.build_report("не ставится мод", False, False)

print("1) пустой приёмник:", feedback.send_report("", report)["ok"] is False)

result = feedback.send_report("http://127.0.0.1:8931/hook", report, "заголовок", "test@mail")
print("2) успешная отправка:", result)
print("   пришло полей:", sorted(received[0].keys()) if received else "нет")
print("   honeypot пустой:", received[0].get("honeypot") == "" if received else "нет")

failed = feedback.send_report("http://127.0.0.1:8931/fail", report)
print("3) ответ 500 обработан:", failed["ok"] is False, failed["message"][:40])

insecure = feedback.send_report("http://example.com/hook", report)
print("4) http вне localhost запрещён:", insecure["ok"] is False, insecure["message"])

server.shutdown()
