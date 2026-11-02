"""Operational logging (D-031): one JSON line per event, and never a customer's words or details.

The brief asks for operational logging. These lines are for whoever runs the service: what each turn
did and how long it took, and every AI, platform, write, and server failure."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import httpx
import pytest

from northstar_webchat import ops
from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership.client import RetryPolicy
from northstar_webchat.llm.adapter import GuardedLLM
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.llm.types import FailureKind, LLMUnavailable
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.writes.api import create_app
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import CONTACT, ready_callback, token_of
from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

SITE = "http://localhost:4173"
SEARCH = json.dumps({"intent": "vehicle_search", "filters": {"fuel_type": "Hybrid"}})


@pytest.fixture
def lines(caplog: pytest.LogCaptureFixture) -> Any:
    """Every operational line, formatted exactly as it would be written."""
    # Starting the app sends these lines to stderr only. Undo that, whatever ran before.
    ops.LOGGER.handlers.clear()
    ops.LOGGER.propagate = True
    caplog.set_level(logging.DEBUG, logger=ops.LOGGER.name)
    formatter = ops.JsonFormatter()

    def read() -> list[dict[str, Any]]:
        return [
            json.loads(formatter.format(record))
            for record in caplog.records
            if record.name == ops.LOGGER.name
        ]

    return read


def named(rows: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["event"] == event]


# Every field each event may carry. A new field must be added here on purpose, after checking that
# it cannot hold anything a customer typed or gave.
ALLOWED = {
    "turn": {"session", "kind", "action", "intent", "interpretation", "reply", "degraded",
             "vehicles", "workflow", "ms"},
    "llm_failure": {"kind", "breaker_opened"},
    "grounding_rejected": {"session", "kinds", "reply"},
    "platform_failure": {"method", "path", "attempts", "detail", "ms"},
    "write": {"session", "workflow", "outcome"},
    "write_interrupted": {"session", "error"},
    "server_error": {"method", "path", "error", "where"},
    "startup": {"platform", "model", "llm_configured", "inspector"},
}
BASE = {"at", "level", "event"}


def only_allowed_fields(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        assert row["event"] in ALLOWED, row["event"]
        assert set(row) - BASE <= ALLOWED[row["event"]], row


class TestFormat:
    def test_a_line_is_one_json_object_with_time_level_and_event(self) -> None:
        stream = io.StringIO()
        ops.configure(stream=stream, level="INFO")
        try:
            ops.log("startup", model="qwen")
        finally:
            ops.LOGGER.handlers.clear()
        row = json.loads(stream.getvalue().strip())
        assert row["event"] == "startup" and row["level"] == "info" and row["model"] == "qwen"
        assert row["at"].endswith("+00:00")

    def test_a_session_is_tagged_not_named(self) -> None:
        tag = ops.session_tag("secret-cookie-value")
        assert tag == ops.session_tag("secret-cookie-value")
        assert "secret" not in tag and len(tag) == 12


class TestTurns:
    async def test_a_turn_logs_what_it_did_and_how_long_it_took(
        self, client: DealershipClient, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        chat = TurnEngine(ScriptedLLM([SEARCH, "Here you go."]), client, service, site_url=SITE)
        await chat.message("s1", "any hybrids?")
        [turn] = named(lines(), "turn")
        assert turn["session"] == ops.session_tag("s1")
        assert turn["kind"] == "message"
        assert turn["intent"] == "vehicle_search"
        assert turn["interpretation"] == "model"
        assert turn["reply"] in ("model", "template")
        assert turn["vehicles"] > 0
        assert isinstance(turn["ms"], int)
        assert "any hybrids" not in json.dumps(lines())
        only_allowed_fields(lines())

    async def test_a_button_turn_logs_the_button(
        self, client: DealershipClient, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        await TurnEngine(ScriptedLLM([]), client, service, site_url=SITE).action("s1", "find_stock")
        [turn] = named(lines(), "turn")
        assert (turn["kind"], turn["action"]) == ("action", "find_stock")

    async def test_a_model_outage_is_logged_and_so_is_the_breaker(
        self, client: DealershipClient, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        down = LLMUnavailable(FailureKind.TIMEOUT)
        llm = GuardedLLM(ScriptedLLM([down, down, down]), failures_to_open=3)
        chat = TurnEngine(llm, client, service, site_url=SITE)
        for _ in range(3):
            await chat.message("s1", "hello")
        failures = named(lines(), "llm_failure")
        assert [row["kind"] for row in failures] == ["timeout"] * 3
        assert [row["breaker_opened"] for row in failures] == [False, False, True]
        assert all(row["level"] == "warning" for row in failures)
        assert {row["interpretation"] for row in named(lines(), "turn")} == {"unavailable"}
        only_allowed_fields(lines())

    async def test_a_rejected_reply_is_logged(
        self, client: DealershipClient, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        lie = "We have a hybrid for £1."
        chat = TurnEngine(ScriptedLLM([SEARCH, lie, lie]), client, service, site_url=SITE)
        await chat.message("s1", "any hybrids?")
        [rejected] = named(lines(), "grounding_rejected")
        assert "price" in rejected["kinds"]
        only_allowed_fields(lines())


class TestPlatform:
    async def test_an_unreachable_platform_is_logged_with_its_attempts(self, lines: Any) -> None:
        async with DealershipClient(
            "http://127.0.0.1:9", "key", retry=RetryPolicy(max_attempts=2, base_delay=0.001)
        ) as down:
            await down.list_dealerships()
        [failure] = named(lines(), "platform_failure")
        assert failure["method"] == "GET" and failure["path"] == "/api/dealerships"
        assert failure["attempts"] == 2
        assert failure["level"] == "warning"
        only_allowed_fields(lines())

    async def test_a_booking_id_in_a_path_is_not_logged(self, lines: Any) -> None:
        async with DealershipClient(
            "http://127.0.0.1:9", "key", retry=RetryPolicy(max_attempts=1)
        ) as down:
            await down.get_workshop_booking("bk-123456")
        [failure] = named(lines(), "platform_failure")
        assert failure["path"] == "/api/workshop-bookings/{id}"


class TestWrites:
    async def test_a_write_logs_its_workflow_and_outcome_and_no_customer_details(
        self, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        view = await ready_callback(service, "cookie-s1")
        await service.confirm("cookie-s1", token_of(view))
        await service.finish_background_work()
        [write] = named(lines(), "write")
        assert (write["workflow"], write["outcome"]) == ("callback", "CallbackRequested")
        everything = json.dumps(lines())
        for value in (*CONTACT.values(), "Discuss finance options", "cookie-s1"):
            assert value not in everything
        only_allowed_fields(lines())


class TestServerErrors:
    async def test_an_unhandled_error_is_logged_by_type_without_its_message(
        self, client: DealershipClient, service: WriteService, lines: Any  # noqa: F811
    ) -> None:
        app = create_app(
            service,
            TurnEngine(ScriptedLLM([]), client, service, site_url=SITE),
            allowed_origin=SITE,
        )

        @app.get("/boom")
        async def boom() -> None:
            raise ValueError("probe@example.com typed this")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://chat.test",
        ) as browser:
            response = await browser.get("/boom")
        assert response.status_code == 500
        [error] = named(lines(), "server_error")
        assert (error["path"], error["error"], error["level"]) == ("/boom", "ValueError", "error")
        assert "probe@example.com" not in json.dumps(lines())
        only_allowed_fields(lines())
