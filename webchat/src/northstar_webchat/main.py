"""Builds the app from the environment. Run with:
uvicorn --factory northstar_webchat.main:create_from_env --port 4020
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI

from . import ops
from .dealership import DealershipClient
from .llm.adapter import LLMSettings, build_llm, choose_settings
from .persistence.store import Store
from .turns.engine import TurnEngine
from .writes.api import create_app
from .writes.service import WriteService


class SettingsError(RuntimeError):
    pass


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if not value:
        raise SettingsError(f"{name} must be set. See webchat/.env.example.")
    return value


def _pepper(values: Mapping[str, str]) -> bytes:
    """A set pepper wins. Otherwise one is made on first start and kept in its file (D-032)."""
    if values.get("WEBCHAT_PEPPER") or not values.get("WEBCHAT_PEPPER_FILE"):
        return _required(values, "WEBCHAT_PEPPER").encode()
    path = Path(values["WEBCHAT_PEPPER_FILE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive, so a second start never replaces a pepper the stored hashes depend on.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(secrets.token_hex(32))
    pepper = path.read_text(encoding="utf-8").strip()
    if not pepper:
        raise SettingsError(f"{path} is empty. Delete it to make a new pepper.")
    return pepper.encode()


def _choice(llm: LLMSettings | None, values: Mapping[str, str]) -> str | None:
    if llm is None:
        return None
    return "fallback" if llm == LLMSettings.fallback_from_env(values) else "first"


def create_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    values = os.environ if env is None else env
    ops.configure(level=values.get("WEBCHAT_LOG_LEVEL", "INFO"))
    api_key = _required(values, "NORTHSTAR_API_KEY")
    pepper = _pepper(values)
    platform_url = values.get("NORTHSTAR_PLATFORM_URL", "http://localhost:4010")
    store = Store(values.get("WEBCHAT_DATABASE_URL", "sqlite:///webchat.sqlite3"))
    store.migrate()
    client = DealershipClient(platform_url, api_key)
    service = WriteService(store, client, pepper=pepper)
    llm = choose_settings(values)
    engine = TurnEngine(
        build_llm(llm),
        client,
        service,
        site_url=values.get("NORTHSTAR_SITE_URL", "http://localhost:4173"),
        # The browser loads images, so this must be an address the customer can reach.
        asset_base=values.get("NORTHSTAR_PUBLIC_PLATFORM_URL", "http://localhost:4010"),
    )
    origin = values.get("WEBCHAT_ALLOWED_ORIGIN", "http://localhost:4173")
    inspector = values.get("WEBCHAT_DEV_INSPECTOR") == "1"
    # No keys, and no database path, which can include a password on other databases.
    ops.log(
        "startup",
        platform=platform_url,
        model=llm.model if llm else None,
        llm_configured=llm is not None,
        llm_choice=_choice(llm, values),
        inspector=inspector,
    )
    return create_app(service, engine, allowed_origin=origin, inspector=inspector)
