"""The real app with a predictable model, for browser tests. Started by conftest."""

from __future__ import annotations

import asyncio
import json
import os

import uvicorn

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.types import FailureKind, LLMUnavailable, Message
from northstar_webchat.persistence.store import Store
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.writes.api import create_app
from northstar_webchat.writes.service import WriteService


class PredictableLLM:
    """Answers from the words it is sent, so a browser test can steer it."""

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        text = messages[-1].content
        # Long enough for a slow browser to see each progress stage. Interpretation is the first
        # stage a customer sees, so it is held longest.
        await asyncio.sleep(1.5 if schema is not None else 0.4)
        if "modeldown" in text:
            raise LLMUnavailable(FailureKind.RATE_LIMITED, retry_after=0)
        if schema is None:
            if "evilscript" in text:
                return "<img src=x onerror=\"window.hacked=true\"> Here you go."
            return "Here are the hybrids I found."
        return json.dumps({"intent": "vehicle_search", "filters": {"fuel_type": "Hybrid"}})


def main() -> None:
    store = Store(os.environ["WEBCHAT_DATABASE_URL"])
    store.migrate()
    client = DealershipClient(os.environ["NORTHSTAR_PLATFORM_URL"], "northstar-local-development")
    service = WriteService(store, client, pepper=b"e2e")
    site = "http://localhost:4173"
    engine = TurnEngine(
        PredictableLLM(), client, service, site_url=site, asset_base="http://localhost:4010"
    )
    app = create_app(service, engine, allowed_origin=site, inspector=True)
    uvicorn.run(app, host="127.0.0.1", port=4020, log_level="warning")


if __name__ == "__main__":
    main()
