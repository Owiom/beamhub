"""Сессия облака: access/refresh токены Supabase, привязка к локальному аккаунту."""
from __future__ import annotations

from . import config

_state: dict[str, str] = {"user_id": "", "cloud_uid": "", "access": "", "refresh": ""}


def set_session(local_user_id: str, cloud_uid: str, access: str, refresh: str = "") -> None:
    _state.update({"user_id": local_user_id, "cloud_uid": cloud_uid,
                   "access": access, "refresh": refresh or _state.get("refresh", "")})
    config.update({"cloud_uid": cloud_uid, "cloud_refresh": refresh or ""})


def access_token() -> str:
    return _state.get("access", "")


def refresh_token() -> str:
    return _state.get("refresh", "") or (config.load().get("cloud_refresh") or "")


def cloud_uid() -> str:
    return _state.get("cloud_uid", "") or (config.load().get("cloud_uid") or "")


def local_user_id() -> str:
    return _state.get("user_id", "")


def signed_in() -> bool:
    return bool(access_token() and cloud_uid())


def clear() -> None:
    _state.update({"user_id": "", "cloud_uid": "", "access": "", "refresh": ""})
    config.update({"cloud_uid": "", "cloud_refresh": ""})
