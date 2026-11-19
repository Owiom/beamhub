"""Реестр провайдеров каталога."""
from __future__ import annotations

from . import beamngland, beamngmods, generic, modsgaming

PROVIDERS = {
    modsgaming.SOURCE: modsgaming,
    beamngland.SOURCE: beamngland,
    beamngmods.SOURCE: beamngmods,
    generic.SOURCE: generic,
}

# Провайдеры с массовым каталогом (синхронизация из sitemap/категорий).
CATALOG_PROVIDERS = [modsgaming, beamngland, beamngmods]

SOURCE_LABELS = {
    "modsgaming": "modsgaming.ru",
    "beamngland": "beamngland.com",
    "beamngmods": "beamngmods.net",
    "link": "по ссылке",
}


def enrich(mod: dict) -> dict | None:
    provider = PROVIDERS.get(mod.get("source"))
    if not provider:
        return None
    return provider.enrich_one(mod)


def resolve_download(mod: dict) -> str:
    provider = PROVIDERS.get(mod.get("source"))
    if provider and hasattr(provider, "resolve_download"):
        return provider.resolve_download(mod)
    return mod.get("download_url") or mod.get("url") or ""
