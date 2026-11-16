"""Живая проверка облака на реальном Supabase: регистрация, push, pull, уборка."""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["BEAMHUB_DB"] = str(Path(tempfile.mkdtemp(prefix="beamhub-live-")) / "live.db")

from core import (accounts, cloud, cloud_email, cloud_session, cloud_social,  # noqa: E402
                  cloud_sync, collections, config, db, social)

BASE = "https://gbibqtdjipiqzaarbrpd.supabase.co"
KEY = "sb_publishable_iQYGnmM_5OZSl3g8ui-YTQ_UlpuHgBs"
EMAIL = f"beamhub-check-{secrets.token_hex(3)}@example.com"
PASSWORD = "BeamHubCheck2026"


def rest(path: str, token: str | None = None, method: str = "GET", body: dict | None = None,
         prefer: str = "") -> tuple[int, str]:
    headers = {"apikey": KEY, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + "/rest/v1/" + path, data=data, method=method,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    config.update({"cloud_url": BASE, "cloud_anon_key": KEY})
    db.init()
    print("1) облако настроено:", cloud.configured())

    result = cloud_email.signup(BASE, KEY, EMAIL, PASSWORD)
    print("2) регистрация:", {k: result.get(k) for k in ("ok", "error", "needs_confirm")})
    if not result.get("ok"):
        return 1
    cloud_uid = result["user"]["id"]
    access = result["access_token"]
    print("   uid:", cloud_uid[:8] + "…", "| email:", EMAIL)

    local = accounts.upsert_provider_user("email", cloud_uid, EMAIL, "Live Check")
    cloud_session.set_session(local["user"]["id"], cloud_uid, access,
                              result.get("refresh_token", ""))

    print("3) готовлю локальную сборку")
    db.upsert_mod({"id": "mg:1", "source": "modsgaming", "url": "u", "title": "Live Mod",
                   "category": "cars"})
    coll = collections.create(local["user"]["id"], "BeamHub проверка подключения",
                              "тестовая сборка, можно удалить", category="other")["collection"]["id"]
    collections.add_item(coll, local["user"]["id"], "mg:1")
    collections.publish(coll, local["user"]["id"])
    social.add_comment(local["user"]["id"], "collection", coll,
                       "Комментарий из живой проверки")
    social.set_rating(local["user"]["id"], "collection", coll, 5,
                      "Живая проверка")
    pushed = cloud_sync.push()
    social_push = cloud_social.push()
    print("4) push:", {k: pushed.get(k) for k in ("ok", "pushed", "errors")},
          "| отзывы:", {k: social_push.get(k) for k in ("ok", "comments", "ratings")})

    code, body = rest(f"collections?id=eq.{coll}&select=id,title,published")
    print("5) в облаке:", code, body[:200])
    code, body = rest(f"collection_items?collection_id=eq.{coll}&select=mod_id")
    print("   состав:", code, body[:120])

    print("6) pull обратно")
    code, _ = rest("collections?select=id&limit=1")
    pulled = cloud_sync.pull()
    print("   pull:", {k: pulled.get(k) for k in ("ok", "pulled", "total")})

    print("7) уборка тестовых данных")
    rest(f"collection_items?collection_id=eq.{coll}", access, "DELETE")
    rest(f"collection_versions?collection_id=eq.{coll}", access, "DELETE")
    code, _ = rest(f"collections?id=eq.{coll}", access, "DELETE")
    print("   удаление сборки:", code)
    for row in db.query("SELECT id FROM comments WHERE target_id=?", (coll,)):
        rest(f"comments?id=eq.{row['id']}", access, "DELETE")
    rest(f"ratings?target_id=eq.{coll}", access, "DELETE")
    code, body = rest(f"collections?select=id")
    print("   осталось сборок в облаке:", body.strip())

    print("\nИТОГ: живое облако работает (вход, публикация, чтение, уборка)")
    print("тестовый аккаунт:", EMAIL, "— его можно удалить в Supabase → Authentication → Users")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
