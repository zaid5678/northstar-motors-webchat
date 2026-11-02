"""Conversation memory (D-029): the server keeps the last search and the cars it showed. The model
only names a change ("diesel") or a position ("the second one"), and code applies it, so the model
can never point at a car the customer was not shown."""

from __future__ import annotations

import json

import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.llm.interpret import interpret
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.llm.types import InterpretationSource
from northstar_webchat.persistence.store import Store
from northstar_webchat.turns.compose import TextSource
from northstar_webchat.turns.engine import NO_COMPARE, NOT_UNDERSTOOD, TurnEngine
from northstar_webchat.workflows.types import Provenance, WorkflowKind
from northstar_webchat.writes.service import WriteService

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
SUVS = json.dumps({"intent": "vehicle_search", "filters": {"body_style": "SUV"}})
DIESEL = json.dumps({"intent": "vehicle_search", "refine": True, "filters": {"fuel_type": "Diesel"}})
WRITTEN = "Here you go."


def engine(llm: ScriptedLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url=SITE)


def refs(intent: str, *positions: int) -> str:
    return json.dumps({"intent": intent, "vehicle_refs": list(positions)})


async def details(client: DealershipClient, vehicle_id: str) -> o.VehicleFound:  # noqa: F811
    found = await client.get_vehicle(vehicle_id)
    assert isinstance(found, o.VehicleFound)
    return found


class TestRefining:
    async def test_a_refinement_keeps_the_earlier_filters(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([SUVS, WRITTEN, DIESEL, WRITTEN]), client, service)
        await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "actually make that diesel")
        assert reply.vehicles
        for card in reply.vehicles:
            vehicle = (await details(client, card.id)).vehicle
            assert (vehicle.body_style, vehicle.fuel_type) == ("SUV", "Diesel")

    async def test_a_new_search_replaces_the_earlier_filters(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        fresh = json.dumps({"intent": "vehicle_search", "filters": {"fuel_type": "Diesel"}})
        chat = engine(ScriptedLLM([SUVS, WRITTEN, fresh, WRITTEN]), client, service)
        await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "show me diesels")
        styles = {(await details(client, card.id)).vehicle.body_style for card in reply.vehicles}
        assert styles - {"SUV"}, "a fresh search must not keep the earlier body style"

    async def test_a_refinement_with_nothing_before_it_is_a_plain_search(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        reply = await engine(ScriptedLLM([DIESEL, WRITTEN]), client, service).message(
            "s1", "make that diesel"
        )
        assert reply.vehicles
        for card in reply.vehicles:
            assert (await details(client, card.id)).vehicle.fuel_type == "Diesel"

    async def test_memory_belongs_to_one_session(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([SUVS, WRITTEN, DIESEL, WRITTEN]), client, service)
        await chat.message("s1", "show me suvs")
        reply = await chat.message("s2", "make that diesel")
        styles = {(await details(client, card.id)).vehicle.body_style for card in reply.vehicles}
        assert styles - {"SUV"}


class TestPositions:
    async def test_the_second_one_is_the_second_card_shown(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(
            ScriptedLLM([SUVS, WRITTEN, refs("vehicle_status", 2), WRITTEN]), client, service
        )
        shown = await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "is the second one still for sale?")
        assert [card.id for card in reply.vehicles] == [shown.vehicles[1].id]

    async def test_the_browse_button_is_remembered_too(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([refs("vehicle_status", 1), WRITTEN]), client, service)
        shown = await chat.action("s1", "find_stock")
        reply = await chat.message("s1", "is the first one available?")
        assert [card.id for card in reply.vehicles] == [shown.vehicles[0].id]

    async def test_memory_survives_a_restart(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        shown = await engine(ScriptedLLM([SUVS, WRITTEN]), client, service).message("s1", "suvs")
        later = engine(ScriptedLLM([refs("vehicle_status", 3), WRITTEN]), client, service)
        reply = await later.message("s1", "what about the third one")
        assert [card.id for card in reply.vehicles] == [shown.vehicles[2].id]

    async def test_a_position_past_the_cars_shown_is_refused(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(
            ScriptedLLM([SUVS, WRITTEN, refs("vehicle_status", 9), refs("vehicle_status", 9)]),
            client,
            service,
        )
        await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "is the ninth one for sale")
        assert reply.text == NOT_UNDERSTOOD
        assert not reply.vehicles

    async def test_a_position_with_nothing_shown_is_refused(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([refs("vehicle_status", 1), refs("vehicle_status", 1)])
        reply = await engine(llm, client, service).message("s1", "is the first one for sale")
        assert reply.text == NOT_UNDERSTOOD

    async def test_a_test_drive_on_the_second_one_proposes_that_car_for_confirmation(
        self, client: DealershipClient, service: WriteService, store: Store  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([SUVS, WRITTEN, refs("test_drive", 2)]), client, service)
        shown = await chat.message("s1", "show me suvs")
        await chat.message("s1", "can I test drive the second one")
        workflow = store.load("s1").active
        assert workflow is not None and workflow.kind is WorkflowKind.TEST_DRIVE
        slot = workflow.slots["vehicle_id"]
        assert slot.value == shown.vehicles[1].id
        assert slot.provenance is Provenance.INFERRED  # So the customer still confirms the car.

    async def test_the_model_sees_positions_and_titles_but_never_ids(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([SUVS, WRITTEN, refs("vehicle_status", 1), WRITTEN])
        chat = engine(llm, client, service)
        shown = await chat.message("s1", "show me suvs")
        await chat.message("s1", "is the first one still there")
        prompt = llm.calls[2][-1].content
        assert f"1: {shown.vehicles[0].title}" in prompt
        assert "veh-" not in prompt


class TestComparing:
    async def test_a_comparison_is_written_by_code_from_live_data(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([SUVS, WRITTEN, refs("compare_vehicles", 1, 2)])
        chat = engine(llm, client, service)
        shown = await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "compare the first two")
        assert reply.source is TextSource.TEMPLATE
        assert len(llm.calls) == 3  # Interpretation only: the model never writes a comparison.
        assert [card.id for card in reply.vehicles] == [card.id for card in shown.vehicles[:2]]
        for card in reply.vehicles:
            vehicle = (await details(client, card.id)).vehicle
            assert card.title in reply.text
            assert card.price in reply.text
            assert f"{vehicle.mileage:,} miles" in reply.text
            assert vehicle.fuel_type in reply.text

    async def test_compare_them_compares_every_car_shown(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        chat = engine(
            ScriptedLLM([SUVS, WRITTEN, json.dumps({"intent": "compare_vehicles"})]),
            client,
            service,
        )
        shown = await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "compare them")
        assert [card.id for card in reply.vehicles] == [card.id for card in shown.vehicles]

    @pytest.mark.parametrize(
        ("script", "reading"),
        [([], json.dumps({"intent": "compare_vehicles"})), ([SUVS, WRITTEN], refs("compare_vehicles", 1))],
        ids=["nothing shown", "one car named"],
    )
    async def test_comparing_fewer_than_two_cars_is_explained(
        self, client: DealershipClient, service: WriteService, script: list[str], reading: str  # noqa: F811
    ) -> None:
        chat = engine(ScriptedLLM([*script, reading]), client, service)
        if script:
            await chat.message("s1", "show me suvs")
        reply = await chat.message("s1", "compare it")
        assert reply.text == NO_COMPARE
        assert reply.source is TextSource.TEMPLATE


class TestInterpretation:
    async def test_positions_are_checked_against_what_was_shown(self) -> None:
        llm = ScriptedLLM([refs("vehicle_status", 4), refs("vehicle_status", 2)])
        result = await interpret(llm, "the fourth one", shown=3)
        assert result.source is InterpretationSource.REPAIRED
        assert result.interpretation.vehicle_refs == [2]

    async def test_zero_is_not_a_position(self) -> None:
        llm = ScriptedLLM([refs("vehicle_status", 0), refs("vehicle_status", 0)])
        result = await interpret(llm, "that one", shown=3)
        assert result.source is InterpretationSource.FALLBACK
