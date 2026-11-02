"""Cancelling or changing a booking is offered as a button, never started by the model (D-030).

The audit showed a verified customer typing "thanks, that's all", read as a cancel, being shown a
cancel card for their real booking."""

from __future__ import annotations

import json

import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.persistence.store import Store
from northstar_webchat.turns.compose import TextSource
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.workflows.types import WorkflowKind
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import TAYLOR
from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

OFFERED = {
    WorkflowKind.WORKSHOP_CANCEL: "Cancel a booking",
    WorkflowKind.WORKSHOP_AMEND: "Change a booking",
}


def engine(llm: ScriptedLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url="http://localhost:4173")


def reading(kind: WorkflowKind) -> str:
    return json.dumps({"intent": kind.value})


@pytest.mark.parametrize("kind", list(OFFERED))
class TestOffered:
    async def test_the_model_cannot_start_it(
        self, client: DealershipClient, service: WriteService, store: Store, kind: WorkflowKind  # noqa: F811
    ) -> None:
        reply = await engine(ScriptedLLM([reading(kind)]), client, service).message("s1", "hmm")
        assert store.load("s1").active is None
        assert "StartWorkflow" not in store.kinds("s1")
        assert [(item.id, item.label) for item in reply.actions] == [(kind.value, OFFERED[kind])]
        assert reply.source is TextSource.TEMPLATE

    async def test_the_offer_is_logged_as_the_model_reading(
        self, client: DealershipClient, service: WriteService, store: Store, kind: WorkflowKind  # noqa: F811
    ) -> None:
        await engine(ScriptedLLM([reading(kind)]), client, service).message("s1", "hmm")
        offers = [row for row in store.dump("s1") if row["kind"] == "workflow_offered"]
        assert [(row["source"], row["payload"]["workflow"]) for row in offers] == [
            ("llm", kind.value)
        ]

    async def test_pressing_the_offered_button_starts_it(
        self, client: DealershipClient, service: WriteService, store: Store, kind: WorkflowKind  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([reading(kind)]), client, service)
        reply = await chat.message("s1", "I need to sort my booking")
        await chat.action("s1", reply.actions[0].id)
        workflow = store.load("s1").active
        assert workflow is not None and workflow.kind is kind

    async def test_a_verified_customer_saying_thanks_gets_no_card(
        self, client: DealershipClient, service: WriteService, kind: WorkflowKind  # noqa: F811
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        await service.submit_identity("s1", **TAYLOR)
        reply = await engine(ScriptedLLM([reading(kind)]), client, service).message(
            "s1", "thanks, that's all"
        )
        view = await service.view("s1")
        assert view.block is None or view.block.token is None
        assert reply.actions


class TestStartedStraightAway:
    @pytest.mark.parametrize(
        "kind",
        [kind for kind in WorkflowKind if kind not in OFFERED],
    )
    async def test_other_workflows_still_start_and_the_log_says_the_model_read_it(
        self, client: DealershipClient, service: WriteService, store: Store, kind: WorkflowKind  # noqa: F811
    ) -> None:
        await engine(ScriptedLLM([reading(kind)]), client, service).message("s1", "please")
        workflow = store.load("s1").active
        assert workflow is not None and workflow.kind is kind
        readings = [row for row in store.dump("s1") if row["kind"] == "workflow_from_message"]
        assert [(row["source"], row["payload"]["workflow"]) for row in readings] == [
            ("llm", kind.value)
        ]
