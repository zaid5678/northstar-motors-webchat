"""A new question hides an unfinished form, and a Continue button brings it back (D-034).

The form used to stay pinned under every later reply, which no real customer chat does."""

from __future__ import annotations

import json

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.persistence.store import Store
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.workflows.types import Phase, WorkflowKind
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import CONTACT, TAYLOR
from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

HOURS = json.dumps({"intent": "opening_hours"})
CALLBACK = {
    "dealership_id": "northstar-manchester",
    "department": "sales",
    "reason": "A question about finance",
    **CONTACT,
}


def engine(llm: ScriptedLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url="http://localhost:4173")


async def confirming(service: WriteService) -> None:  # noqa: F811
    await service.start("s1", WorkflowKind.CALLBACK)
    for name, value in CALLBACK.items():
        view = await service.fill("s1", name, value)
    assert view.status == "confirming" and view.block is not None


class TestANewQuestion:
    async def test_a_typed_question_hides_the_form(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        reply = await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        assert "Northstar" in reply.text
        assert reply.view is not None and reply.view.status == "verifying"
        assert reply.view.hidden
        assert reply.view.resume == "Continue checking your booking"

    async def test_a_button_question_hides_it_too(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        reply = await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        assert reply.view is not None and reply.view.hidden

    async def test_it_stays_hidden_after_a_refresh(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        assert (await service.view("s1")).hidden

    async def test_the_task_itself_is_kept(
        self, client: DealershipClient, service: WriteService, store: Store  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        active = store.load("s1").active
        assert active is not None and active.phase is Phase.VERIFYING
        assert "AbandonWorkflow" not in store.kinds("s1")

    async def test_the_confirmation_card_is_hidden(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await confirming(service)
        reply = await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        assert reply.view is not None and reply.view.status == "confirming"
        assert reply.view.hidden
        assert reply.view.resume == "Continue your callback request"


class TestContinue:
    async def test_it_brings_the_form_back_with_what_was_filled(
        self, client: DealershipClient, service: WriteService, store: Store  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.CALLBACK)
        await service.fill("s1", "reason", "A question about finance")
        before = [field.name for field in (await service.view("s1")).fields]
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        view = await service.show_form("s1")
        assert not view.hidden and view.status == "collecting"
        assert [field.name for field in view.fields] == before
        active = store.load("s1").active
        assert active is not None and active.slots["reason"].value == "A question about finance"

    async def test_the_card_comes_back_and_still_confirms(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await confirming(service)
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        view = await service.show_form("s1")
        assert not view.hidden and view.block is not None
        done = await service.confirm("s1", view.block.token)
        assert done.status == "completed"

    async def test_it_is_not_a_turn_so_nothing_is_added_to_the_log(
        self, client: DealershipClient, service: WriteService, store: Store  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        before = store.kinds("s1")
        await service.show_form("s1")
        assert store.kinds("s1") == before


class TestShownAgain:
    async def test_any_change_to_the_task_shows_it(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.CALLBACK)
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        view = await service.fill("s1", "reason", "A question about finance")
        assert not view.hidden

    async def test_submitting_the_hidden_form_still_works(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        """A second tab may still show the form. Its answer must count, and show the result."""
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        view = await service.submit_identity("s1", **TAYLOR)
        assert not view.hidden and view.status == "completed"

    async def test_starting_another_task_shows_the_old_one_to_finish(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        reply = await engine(ScriptedLLM([]), client, service).action(
            "s1", WorkflowKind.CALLBACK.value
        )
        assert reply.view is not None and reply.view.status == "verifying"
        assert not reply.view.hidden

    async def test_a_typed_request_for_a_task_is_not_hidden(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        reading = json.dumps({"intent": WorkflowKind.CALLBACK.value})
        reply = await engine(ScriptedLLM([reading]), client, service).message(
            "s1", "can someone call me"
        )
        assert reply.view is not None and reply.view.status == "collecting"
        assert not reply.view.hidden


class TestNeverHidden:
    async def test_with_no_task_there_is_nothing_to_hide(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        reply = await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        assert reply.view is not None and not reply.view.hidden and reply.view.resume == ""

    async def test_a_finished_task_is_not_hidden(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        await confirming(service)
        view = await service.view("s1")
        assert view.block is not None
        await service.confirm("s1", view.block.token)
        reply = await engine(ScriptedLLM([HOURS]), client, service).message("s1", "bolton hours?")
        assert reply.view is not None and not reply.view.hidden

    async def test_hiding_never_writes_to_the_workflow(
        self, client: DealershipClient, service: WriteService, store: Store  # noqa: F811
    ) -> None:
        await confirming(service)
        before = store.load("s1").active
        await engine(ScriptedLLM([]), client, service).action("s1", "opening_hours")
        assert store.load("s1").active == before
