"""Saved transcripts and SSE, over HTTP against the real platform."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.llm.types import Message
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.writes.api import COOKIE, WIDGET, create_app
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import ready_test_drive, token_of
from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

SEARCH = json.dumps({"intent": "vehicle_search", "filters": {"fuel_type": "Hybrid"}})
ORIGIN = "http://localhost:4173"


def browser_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chat.test")


def make_app(llm: Any, client: DealershipClient, service: WriteService) -> Any:  # noqa: F811
    turn = TurnEngine(llm, client, service, site_url=ORIGIN)
    return create_app(service, turn, allowed_origin=ORIGIN)


def events_of(body: str) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if lines:
            name = next(line[7:] for line in lines if line.startswith("event: "))
            data = next(line[6:] for line in lines if line.startswith("data: "))
            found.append((name, json.loads(data)))
    return found


@pytest.fixture
async def http(client: DealershipClient, service: WriteService) -> AsyncIterator[Any]:  # noqa: F811
    llm = ScriptedLLM([SEARCH, "Here are the hybrids."])
    async with browser_for(make_app(llm, client, service)) as browser:
        browser.cookies.set(COOKIE, "web")
        yield browser


class TestStream:
    async def test_progress_comes_before_the_reply(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/chat/stream", json={"text": "hybrid please"})
        assert response.headers["content-type"].startswith("text/event-stream")
        events = events_of(response.text)
        assert [name for name, _ in events][-1] == "reply"
        stages = [data["stage"] for name, data in events if name == "progress"]
        assert stages == ["understanding", "checking", "writing"]
        assert events[-1][1]["text"] == "Here are the hybrids."

    async def test_a_stream_needs_a_session(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            response = await browser.post("/api/chat/stream", json={"text": "hi"})
        assert response.status_code == 401

    async def test_a_turn_finishes_if_the_browser_goes_away(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        """A refresh mid-reply must not lose the answer."""

        class Held(ScriptedLLM):
            async def complete(self, messages: list[Message], **options: Any) -> str:
                await release.wait()
                return await super().complete(messages, **options)

        release = asyncio.Event()
        app = make_app(Held([SEARCH, "Here you go."]), client, service)
        async with browser_for(app) as browser:
            browser.cookies.set(COOKIE, "web")
            request = asyncio.create_task(
                browser.post("/api/chat/stream", json={"text": "hybrid please"})
            )
            await asyncio.sleep(0.05)
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            middle = (await browser.get("/api/chat/transcript")).json()
            assert middle["pending"] is True
            release.set()
            for _ in range(100):
                done = (await browser.get("/api/chat/transcript")).json()
                if not done["pending"]:
                    break
                await asyncio.sleep(0.02)
        assert done["messages"][-1]["text"] == "Here you go."


class TestTranscript:
    async def test_a_conversation_survives_a_refresh(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/stream", json={"text": "hybrid please"})
        saved = (await http.get("/api/chat/transcript")).json()
        assert [item["role"] for item in saved["messages"]] == ["user", "assistant"]
        assert saved["messages"][0]["text"] == "hybrid please"
        assert saved["messages"][1]["text"] == "Here are the hybrids."
        assert saved["messages"][1]["vehicles"]
        assert saved["pending"] is False

    async def test_it_survives_a_server_restart(
        self, client: DealershipClient, service: WriteService, db_url: str  # noqa: F811
    ) -> None:
        from northstar_webchat.persistence.store import Store

        llm = ScriptedLLM([SEARCH, "Here you go."])
        async with browser_for(make_app(llm, client, service)) as browser:
            browser.cookies.set(COOKIE, "web")
            await browser.post("/api/chat/stream", json={"text": "hybrid please"})
        restarted = WriteService(Store(db_url), client, pepper=b"test-pepper")
        async with browser_for(make_app(ScriptedLLM([]), client, restarted)) as browser:
            browser.cookies.set(COOKIE, "web")
            saved = (await browser.get("/api/chat/transcript")).json()
        assert len(saved["messages"]) == 2

    async def test_sessions_cannot_read_each_other(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([SEARCH, "Here you go."])
        async with browser_for(make_app(llm, client, service)) as browser:
            browser.cookies.set(COOKIE, "one")
            await browser.post("/api/chat/stream", json={"text": "hybrid please"})
            browser.cookies.set(COOKIE, "two")
            saved = (await browser.get("/api/chat/transcript")).json()
        assert saved["messages"] == []

    async def test_no_token_is_ever_stored_in_a_transcript(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        view = await ready_test_drive(service, client, "web")
        turn = TurnEngine(
            ScriptedLLM([json.dumps({"intent": "other"}), "Anything else?"]),
            client,
            service,
            site_url=ORIGIN,
        )
        await turn.message("web", "hello")
        assert token_of(view) not in json.dumps(service.store.messages("web"))

    async def test_an_empty_session_has_an_empty_transcript(
        self, http: httpx.AsyncClient
    ) -> None:
        saved = (await http.get("/api/chat/transcript")).json()
        assert saved["messages"] == []
        assert saved["pending"] is False
        assert saved["last_event_id"] == 0
        assert {item["id"] for item in saved["actions"]} >= {"find_stock", "callback"}


class TestBrowserAccess:
    async def test_an_existing_session_is_kept(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/session")
        assert "set-cookie" not in response.headers

    async def test_a_new_browser_gets_a_session(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            assert "set-cookie" in (await browser.post("/api/session")).headers

    async def test_the_website_may_call_with_credentials(self, http: httpx.AsyncClient) -> None:
        response = await http.get("/api/chat/transcript", headers={"Origin": ORIGIN})
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    @pytest.mark.parametrize(
        "origin",
        ["http://127.0.0.1:4173", "http://192.168.1.20:4173", "http://10.0.0.5:4173"],
    )
    async def test_the_website_works_from_a_local_address(
        self, http: httpx.AsyncClient, origin: str
    ) -> None:
        response = await http.get("/api/chat/transcript", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin

    @pytest.mark.parametrize(
        "origin",
        ["http://evil.example:4173", "http://192.168.1.20:9999", "http://192.168.1.20.evil.example:4173"],
    )
    async def test_other_addresses_may_not(self, http: httpx.AsyncClient, origin: str) -> None:
        response = await http.get("/api/chat/transcript", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers

    async def test_other_websites_may_not(self, http: httpx.AsyncClient) -> None:
        response = await http.get(
            "/api/chat/transcript", headers={"Origin": "https://evil.example"}
        )
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.skipif(
        not WIDGET.is_file(), reason="Build it first: cd webchat/widget && npm ci && npm run build"
    )
    async def test_the_widget_is_served_as_javascript(self, http: httpx.AsyncClient) -> None:
        response = await http.get("/widget.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]
        assert "northstar-local-development" not in response.text


def ids_of(body: str) -> list[int]:
    return [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]


class TestResume:
    async def test_every_event_has_a_rising_id(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/chat/stream", json={"text": "hybrid please"})
        found = ids_of(response.text)
        assert len(found) == 4
        assert found == sorted(found)

    async def test_resuming_replays_only_what_was_missed(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/chat/stream", json={"text": "hybrid please"})
        second = ids_of(response.text)[1]
        resumed = await http.get("/api/chat/stream", headers={"Last-Event-ID": str(second)})
        events = events_of(resumed.text)
        assert [name for name, _ in events] == ["progress", "reply"]
        assert ids_of(resumed.text)[0] > second

    async def test_resuming_from_the_end_returns_nothing(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/chat/stream", json={"text": "hybrid please"})
        last = ids_of(response.text)[-1]
        resumed = await http.get("/api/chat/stream", headers={"Last-Event-ID": str(last)})
        assert events_of(resumed.text) == []

    async def test_a_resume_follows_a_turn_still_running(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        class Held(ScriptedLLM):
            async def complete(self, messages: list[Message], **options: Any) -> str:
                await release.wait()
                return await super().complete(messages, **options)

        release = asyncio.Event()
        app = make_app(Held([SEARCH, "Here you go."]), client, service)
        async with browser_for(app) as browser:
            browser.cookies.set(COOKIE, "web")
            request = asyncio.create_task(
                browser.post("/api/chat/stream", json={"text": "hybrid please"})
            )
            await asyncio.sleep(0.05)
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            saved = (await browser.get("/api/chat/transcript")).json()
            resume = asyncio.create_task(
                browser.get(
                    "/api/chat/stream", headers={"Last-Event-ID": str(saved["last_event_id"])}
                )
            )
            await asyncio.sleep(0.05)
            release.set()
            resumed = await asyncio.wait_for(resume, timeout=5)
        assert events_of(resumed.text)[-1][1]["text"] == "Here you go."

    async def test_a_resume_waits_for_the_reply_event_after_the_reply_is_saved(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        """The reply reaches the transcript a moment before its stream event is stored. A browser
        that refreshes in that gap must still get the reply, not an empty stream."""
        from datetime import datetime, timezone

        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            browser.cookies.set(COOKIE, "web")
            now = datetime.now(timezone.utc)
            service.store.add_message("web", "user", {"text": "hi"}, now)
            service.store.add_message("web", "assistant", {"text": "Done"}, now)
            resume = asyncio.create_task(
                browser.get("/api/chat/stream", headers={"Last-Event-ID": "0"})
            )
            await asyncio.sleep(0.3)
            service.store.add_stream_event("web", "reply", {"text": "Done"}, now)
            resumed = await asyncio.wait_for(resume, timeout=5)
        assert [name for name, _ in events_of(resumed.text)] == ["reply"]

    async def test_a_resume_gives_up_if_no_reply_event_ever_arrives(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        from datetime import datetime, timezone

        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            browser.cookies.set(COOKIE, "web")
            now = datetime.now(timezone.utc)
            service.store.add_message("web", "user", {"text": "hi"}, now)
            service.store.add_message("web", "assistant", {"text": "Done"}, now)
            resumed = await asyncio.wait_for(
                browser.get("/api/chat/stream", headers={"Last-Event-ID": "0"}), timeout=10
            )
        assert events_of(resumed.text) == []

    async def test_events_belong_to_one_session(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/stream", json={"text": "hybrid please"})
        http.cookies.set(COOKIE, "someone-else")
        resumed = await http.get("/api/chat/stream", headers={"Last-Event-ID": "0"})
        assert events_of(resumed.text) == []

    async def test_a_bad_event_id_is_refused(self, http: httpx.AsyncClient) -> None:
        response = await http.get("/api/chat/stream", headers={"Last-Event-ID": "nonsense"})
        assert response.status_code == 400

    async def test_no_token_is_stored_with_stream_events(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        view = await ready_test_drive(service, client, "web")
        llm = ScriptedLLM([json.dumps({"intent": "other"}), "Anything else?"])
        async with browser_for(make_app(llm, client, service)) as browser:
            browser.cookies.set(COOKIE, "web")
            await browser.post("/api/chat/stream", json={"text": "hello"})
        assert token_of(view) not in json.dumps(service.store.stream_events("web", 0))

    async def test_a_failed_turn_ends_with_an_error_not_a_hang(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        class Broken(ScriptedLLM):
            async def complete(self, messages: list[Message], **options: Any) -> str:
                raise RuntimeError("bug")

        async with browser_for(make_app(Broken(), client, service)) as browser:
            browser.cookies.set(COOKIE, "web")
            response = await browser.post("/api/chat/stream", json={"text": "hi"})
            saved = (await browser.get("/api/chat/transcript")).json()
        assert events_of(response.text)[-1][0] == "error"
        assert saved["pending"] is False


class TestFormsAndConfirmation:
    async def start(self, http: httpx.AsyncClient, action: str = "callback") -> dict[str, Any]:
        reply = (await http.post("/api/chat/action", json={"id": action})).json()
        return reply["view"]

    async def test_a_workflow_says_what_it_needs(self, http: httpx.AsyncClient) -> None:
        view = await self.start(http)
        fields = {item["name"]: item for item in view["fields"]}
        assert fields["email"]["kind"] == "email"
        assert fields["phone"]["kind"] == "tel"
        assert fields["department"]["kind"] == "select"
        assert {o["value"] for o in fields["department"]["options"]} == {
            "sales",
            "service",
            "parts",
        }
        assert fields["reason"]["label"]

    async def test_dealerships_are_live_choices(self, http: httpx.AsyncClient) -> None:
        view = await self.start(http)
        site = next(item for item in view["fields"] if item["name"] == "dealership_id")
        assert len(site["options"]) == 4
        assert site["options"][0]["label"].startswith("Northstar")

    async def test_slot_times_appear_once_they_can_be_looked_up(
        self, http: httpx.AsyncClient
    ) -> None:
        view = await self.start(http, "test_drive")
        assert "slot_id" not in {item["name"] for item in view["fields"]}
        filled = (
            await http.post("/api/chat/slot", json={"name": "vehicle_id", "value": "veh-001"})
        ).json()
        slot = next(item for item in filled["fields"] if item["name"] == "slot_id")
        assert slot["options"]
        assert "at" in slot["options"][0]["label"]

    async def test_verification_asks_for_the_four_details(self, http: httpx.AsyncClient) -> None:
        view = await self.start(http, "booking_retrieval")
        assert view["status"] == "verifying"
        assert {item["name"] for item in view["fields"]} == {
            "reference",
            "last_name",
            "registration",
            "phone",
        }

    async def test_an_inferred_value_is_offered_back_for_confirmation(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        from northstar_webchat.workflows.types import WorkflowKind
        from northstar_webchat.writes.service import Proposal

        await service.start("web", WorkflowKind.VEHICLE_INTEREST)
        view = await service.propose("web", [Proposal(name="vehicle_id", value="veh-007")], "it")
        field = next(item for item in view.fields if item.name == "vehicle_id")
        assert field.value == "veh-007"

    async def test_an_inferred_optional_value_is_also_offered_back(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        """Otherwise the customer could never confirm it, and the form would stall."""
        from northstar_webchat.workflows.types import WorkflowKind
        from northstar_webchat.writes.service import Proposal

        await service.start("web", WorkflowKind.CALLBACK)
        view = await service.propose("web", [Proposal(name="vehicle_id", value="veh-007")], "it")
        assert "vehicle_id" in {item.name for item in view.fields}

    async def test_a_known_unbookable_slot_is_not_offered(
        self, http: httpx.AsyncClient, service: WriteService  # noqa: F811
    ) -> None:
        from datetime import datetime, timedelta, timezone

        await self.start(http, "test_drive")
        filled = (
            await http.post("/api/chat/slot", json={"name": "vehicle_id", "value": "veh-001"})
        ).json()
        slot = next(item for item in filled["fields"] if item["name"] == "slot_id")
        bad = slot["options"][0]["value"]
        now = datetime.now(timezone.utc)
        service.store.mark_unbookable(bad, now + timedelta(hours=1))
        again = (await http.get("/api/conversation/state")).json()
        refreshed = next(item for item in again["fields"] if item["name"] == "slot_id")
        assert bad not in {option["value"] for option in refreshed["options"]}

    async def test_a_confirmed_result_joins_the_transcript_once(
        self, http: httpx.AsyncClient, client: DealershipClient  # noqa: F811
    ) -> None:
        await self.start(http)
        values = {
            "dealership_id": "northstar-manchester",
            "department": "sales",
            "reason": "Discuss finance",
            "first_name": "Probe",
            "last_name": "Tester",
            "email": "probe@example.com",
            "phone": "07700900999",
        }
        view: dict[str, Any] = {}
        for name, value in values.items():
            view = (await http.post("/api/chat/slot", json={"name": name, "value": value})).json()
        token = view["block"]["token"]
        await http.post("/api/confirmations", json={"token": token})
        await http.post("/api/confirmations", json={"token": token})
        saved = (await http.get("/api/chat/transcript")).json()
        assert sum("CALL-" in item["text"] for item in saved["messages"]) == 1


class TestFormsSayWhatIsOptional:
    async def start(self, http: httpx.AsyncClient, action: str) -> dict[str, Any]:
        return (await http.post("/api/chat/action", json={"id": action})).json()["view"]

    async def test_optional_fields_are_offered_and_marked(self, http: httpx.AsyncClient) -> None:
        view = await self.start(http, "callback")
        fields = {item["name"]: item for item in view["fields"]}
        assert fields["reason"]["required"] is True
        assert fields["preferred_time"]["required"] is False
        assert fields["vehicle_id"]["required"] is False

    async def test_notes_are_offered_on_a_test_drive(self, http: httpx.AsyncClient) -> None:
        view = await self.start(http, "test_drive")
        notes = next(item for item in view["fields"] if item["name"] == "notes")
        assert notes["required"] is False

    async def test_an_amendment_needs_only_one_of_its_fields(
        self, http: httpx.AsyncClient
    ) -> None:
        from ..writes.conftest import TAYLOR

        await self.start(http, "workshop_amend")
        view = (await http.post("/api/chat/identity", json=TAYLOR)).json()
        fields = {item["name"]: item for item in view["fields"]}
        assert set(fields) == {"target_slot_id", "mileage", "notes"}
        assert not any(item["required"] for item in fields.values())

    async def test_every_workflow_has_a_button(self, http: httpx.AsyncClient) -> None:
        saved = (await http.get("/api/chat/transcript")).json()
        ids = {item["id"] for item in saved["actions"]}
        assert {
            "test_drive",
            "workshop_create",
            "workshop_amend",
            "workshop_cancel",
            "booking_retrieval",
            "part_exchange",
            "callback",
            "message",
            "sales_enquiry",
            "vehicle_interest",
        } <= ids


class TestRetrievalIsShown:
    async def test_a_verified_booking_joins_the_transcript(self, http: httpx.AsyncClient) -> None:
        from ..writes.conftest import TAYLOR

        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        view = (await http.post("/api/chat/identity", json=TAYLOR)).json()
        assert view["status"] == "completed"
        saved = (await http.get("/api/chat/transcript")).json()
        last = saved["messages"][-1]["text"]
        assert "Full service" in last and "Northstar Manchester" in last
        assert "Status: booked" in last

    async def test_asking_twice_does_not_repeat_it(self, http: httpx.AsyncClient) -> None:
        from ..writes.conftest import TAYLOR

        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/chat/identity", json=TAYLOR)
        await http.get("/api/conversation/state")
        saved = (await http.get("/api/chat/transcript")).json()
        assert sum("Full service" in item["text"] for item in saved["messages"]) == 1


class TestInspector:
    def app(self, client: DealershipClient, service: WriteService, *, on: bool) -> Any:  # noqa: F811
        llm = ScriptedLLM([SEARCH, "A steal at £99!", "Here you go."])
        turn = TurnEngine(llm, client, service, site_url=ORIGIN)
        return create_app(service, turn, allowed_origin=ORIGIN, inspector=on)

    async def test_it_does_not_exist_unless_switched_on(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        async with browser_for(self.app(client, service, on=False)) as browser:
            browser.cookies.set(COOKIE, "web")
            assert (await browser.get("/api/dev/inspect")).status_code == 404

    async def test_it_shows_state_tools_and_verdicts(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        async with browser_for(self.app(client, service, on=True)) as browser:
            browser.cookies.set(COOKIE, "web")
            await browser.post("/api/chat/stream", json={"text": "hybrid please"})
            data = (await browser.get("/api/dev/inspect")).json()
        assert data["tool_calls"][-1]["outcomes"] == ["StockSearched"]
        verdict = data["verdicts"][-1]
        assert verdict["result"] == "regenerated"
        assert verdict["rejected"] == ["price"]

    async def test_it_shows_pending_operations_without_secrets(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        view = await ready_test_drive(service, client, "web")
        async with browser_for(self.app(client, service, on=True)) as browser:
            browser.cookies.set(COOKIE, "web")
            data = (await browser.get("/api/dev/inspect")).json()
        assert data["session"]["phase"] == "awaiting_confirmation"
        assert data["pending"]["confirmation"] is True
        dumped = json.dumps(data)
        assert token_of(view) not in dumped
        assert "probe@example.com" not in dumped


class TestHiddenForms:
    """A new question puts the form aside, and Continue brings it back (D-034)."""

    async def test_continue_brings_the_form_back(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/chat/action", json={"id": "opening_hours"})
        state = (await http.get("/api/conversation/state")).json()
        assert state["hidden"] and state["resume"] == "Continue checking your booking"
        shown = (await http.post("/api/conversation/continue")).json()
        assert not shown["hidden"] and shown["status"] == "verifying"
        assert [field["name"] for field in shown["fields"]] == [
            "reference", "last_name", "registration", "phone",
        ]

    async def test_continue_needs_a_session(self, client: DealershipClient, service: WriteService) -> None:  # noqa: F811
        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            response = await browser.post("/api/conversation/continue")
        assert response.status_code in (401, 403)


class TestCancellingATask:
    """A customer can drop an unfinished task, but never one that is being sent."""

    CANCELLED = "That's cancelled, and nothing was sent to the dealership."

    async def test_a_form_can_be_cancelled(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        view = (await http.post("/api/conversation/cancel")).json()
        assert view["status"] == "abandoned" and not view["fields"]
        saved = (await http.get("/api/chat/transcript")).json()
        assert saved["messages"][-1]["text"].startswith(self.CANCELLED)
        # "What else can I help with?" needs something to press.
        assert "callback" in {action["id"] for action in saved["messages"][-1]["actions"]}

    async def test_another_task_can_start_afterwards(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/conversation/cancel")
        reply = (await http.post("/api/chat/action", json={"id": "callback"})).json()
        assert "Finish or cancel" not in reply["text"]
        assert reply["view"]["status"] == "collecting"

    async def test_a_cancelled_card_cannot_be_confirmed(
        self, http: httpx.AsyncClient, service: WriteService  # noqa: F811
    ) -> None:
        await http.post("/api/chat/action", json={"id": "callback"})
        values = {
            "dealership_id": "northstar-manchester",
            "department": "sales",
            "reason": "Discuss finance",
            "first_name": "Probe",
            "last_name": "Tester",
            "email": "probe@example.com",
            "phone": "07700900999",
        }
        for name, value in values.items():
            view = (await http.post("/api/chat/slot", json={"name": name, "value": value})).json()
        token = view["block"]["token"]
        await http.post("/api/conversation/cancel")
        after = (await http.post("/api/confirmations", json={"token": token})).json()
        assert after["status"] != "completed"
        assert service.store.write_requests("web") == []

    async def test_a_form_put_aside_can_be_cancelled(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/chat/action", json={"id": "opening_hours"})
        view = (await http.post("/api/conversation/cancel")).json()
        assert view["status"] == "abandoned" and not view["hidden"]

    async def test_with_nothing_to_cancel_nothing_is_said(self, http: httpx.AsyncClient) -> None:
        before = (await http.get("/api/chat/transcript")).json()["messages"]
        view = (await http.post("/api/conversation/cancel")).json()
        assert view["status"] == "idle"
        assert (await http.get("/api/chat/transcript")).json()["messages"] == before

    async def test_it_needs_a_session(self, client: DealershipClient, service: WriteService) -> None:  # noqa: F811
        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            response = await browser.post("/api/conversation/cancel")
        assert response.status_code == 401

    async def test_cancelling_twice_says_it_once(self, http: httpx.AsyncClient) -> None:
        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/conversation/cancel")
        await http.post("/api/conversation/cancel")
        saved = (await http.get("/api/chat/transcript")).json()["messages"]
        assert sum(item["text"].startswith(self.CANCELLED) for item in saved) == 1


class TestNewChat:
    """A customer can start afresh. The old conversation stays on the server (D-036)."""

    CALLBACK = {
        "dealership_id": "northstar-manchester",
        "department": "sales",
        "reason": "Discuss finance",
        "first_name": "Probe",
        "last_name": "Tester",
        "email": "probe@example.com",
        "phone": "07700900999",
    }

    async def new_chat(self, http: httpx.AsyncClient) -> str:
        """As a browser would: the new cookie replaces the old one."""
        response = await http.post("/api/session/new")
        assert response.status_code == 200
        fresh = response.cookies[COOKIE]
        http.cookies.clear()
        http.cookies.set(COOKIE, fresh)
        return fresh

    async def test_it_gives_a_new_empty_conversation(
        self, http: httpx.AsyncClient, service: WriteService  # noqa: F811
    ) -> None:
        await http.post("/api/chat/action", json={"id": "opening_hours"})
        fresh = await self.new_chat(http)
        assert fresh != "web"
        saved = (await http.get("/api/chat/transcript")).json()
        assert saved["messages"] == [] and saved["view"]["status"] == "idle"
        assert service.store.messages("web")  # The old one is kept.

    async def test_the_cookie_is_as_safe_as_the_first(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/session/new")
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie

    async def test_an_unfinished_form_is_cancelled(
        self, http: httpx.AsyncClient, service: WriteService  # noqa: F811
    ) -> None:
        from northstar_webchat.workflows.types import Phase

        await http.post("/api/chat/action", json={"id": "booking_retrieval"})
        await http.post("/api/session/new")
        old = service.store.load("web").active
        assert old is not None and old.phase is Phase.ABANDONED

    async def test_the_saved_search_is_not_carried_over(
        self, http: httpx.AsyncClient, service: WriteService  # noqa: F811
    ) -> None:
        await http.post("/api/chat/message", json={"text": "hybrid please"})
        assert service.store.recall("web") is not None
        fresh = await self.new_chat(http)
        assert service.store.recall(fresh) is None

    async def test_it_is_refused_while_an_outcome_is_unknown(
        self, http: httpx.AsyncClient, service: WriteService, faults: Any  # noqa: F811
    ) -> None:
        """Otherwise the customer would lose the Check again button for a booking that may exist."""
        faults.lose_response("POST", "/api/callback-requests", times=99)
        await http.post("/api/chat/action", json={"id": "callback"})
        for name, value in self.CALLBACK.items():
            view = (await http.post("/api/chat/slot", json={"name": name, "value": value})).json()
        view = (await http.post("/api/confirmations", json={"token": view["block"]["token"]})).json()
        assert view["status"] == "unknown"
        response = await http.post("/api/session/new")
        assert response.status_code == 409
        assert COOKIE not in response.cookies

    async def test_with_no_session_it_just_starts_one(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        async with browser_for(make_app(ScriptedLLM([]), client, service)) as browser:
            response = await browser.post("/api/session/new")
            assert response.status_code == 200 and browser.cookies.get(COOKIE)
