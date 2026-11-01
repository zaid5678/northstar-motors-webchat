"""Builds the app from the environment. Run with:
uvicorn --factory northstar_webchat.main:create_from_env --port 4020
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import FastAPI

from .dealership import DealershipClient
from .llm.adapter import build_llm
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


def create_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    values = os.environ if env is None else env
    api_key = _required(values, "NORTHSTAR_API_KEY")
    pepper = _required(values, "WEBCHAT_PEPPER").encode()
    platform_url = values.get("NORTHSTAR_PLATFORM_URL", "http://localhost:4010")
    store = Store(values.get("WEBCHAT_DATABASE_URL", "sqlite:///webchat.sqlite3"))
    store.migrate()
    client = DealershipClient(platform_url, api_key)
    service = WriteService(store, client, pepper=pepper)
    engine = TurnEngine(
        build_llm(values),
        client,
        service,
        site_url=values.get("NORTHSTAR_SITE_URL", "http://localhost:4173"),
        # The browser loads images, so this must be an address the customer can reach.
        asset_base=values.get("NORTHSTAR_PUBLIC_PLATFORM_URL", "http://localhost:4010"),
    )
    origin = values.get("WEBCHAT_ALLOWED_ORIGIN", "http://localhost:4173")
    inspector = values.get("WEBCHAT_DEV_INSPECTOR") == "1"
    return create_app(service, engine, allowed_origin=origin, inspector=inspector)
