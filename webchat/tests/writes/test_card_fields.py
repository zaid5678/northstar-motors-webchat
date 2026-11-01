"""Every field a write will save must appear on its confirmation card (D-028). A customer who clicks
Confirm should be agreeing to exactly what will be sent, with names instead of internal IDs."""

from __future__ import annotations

import re
from typing import Any

import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.workflows.types import DEFINITIONS, WorkflowKind
from northstar_webchat.writes.present import View
from northstar_webchat.writes.service import WriteService

from .conftest import CONTACT, TAYLOR, first_drive_slot, first_workshop_slot

K = WorkflowKind
SLOT = "<slot>"
WHEN = "<when>"
WHEN_PATTERN = re.compile(r"\d{1,2} \w+ 20\d\d at \d\d:\d\d")
CONTACT_SHOWN = ["Probe Tester", "probe@example.com", "07700900999"]

# Every slot of every write, with a value that cannot be mistaken for anything else on the card.
VALUES: dict[WorkflowKind, dict[str, Any]] = {
    K.TEST_DRIVE: {"vehicle_id": "veh-001", "slot_id": SLOT, **CONTACT, "notes": "NOTE-TD"},
    K.WORKSHOP_CREATE: {
        "dealership_id": "northstar-manchester",
        "service_type_id": "mot",
        "slot_id": SLOT,
        "registration": "AB12 CDE",
        "mileage": 54321,
        **CONTACT,
        "notes": "NOTE-WS",
    },
    K.WORKSHOP_AMEND: {"target_slot_id": SLOT, "mileage": 43210, "notes": "NOTE-AM"},
    K.WORKSHOP_CANCEL: {},
    K.CALLBACK: {
        "dealership_id": "northstar-bolton",
        "department": "parts",
        "reason": "REASON-CB",
        "vehicle_id": "veh-001",
        "preferred_time": "TIME-CB",
        **CONTACT,
    },
    K.MESSAGE: {
        "dealership_id": "northstar-bolton",
        "department": "service",
        "subject": "SUBJECT-MS",
        "message": "BODY-MS",
        "preferred_contact_method": "phone",
        **CONTACT,
    },
    K.SALES_ENQUIRY: {
        "dealership_id": "northstar-bolton",
        "enquiry_type": "finance",
        "vehicle_id": "veh-001",
        "message": "BODY-EN",
        **CONTACT,
    },
    K.VEHICLE_INTEREST: {"vehicle_id": "veh-007", "notes": "NOTE-VI", **CONTACT},
    K.PART_EXCHANGE: {
        "dealership_id": "northstar-bolton",
        "registration": "AB12 CDE",
        "mileage": 54321,
        "condition": "fair",
        **CONTACT,
    },
}
EXPECT: dict[WorkflowKind, list[str]] = {
    K.TEST_DRIVE: ["BMW 1 Series", "Northstar Manchester", "NOTE-TD", WHEN],
    K.WORKSHOP_CREATE: ["MOT", "Northstar Manchester", "AB12 CDE", "54,321", "NOTE-WS", WHEN],
    K.WORKSHOP_AMEND: ["WORK-10001", "43,210", "NOTE-AM", WHEN],
    K.WORKSHOP_CANCEL: ["WORK-10001", "Full service", "Northstar Manchester"],
    K.CALLBACK: ["Northstar Bolton", "Parts", "REASON-CB", "BMW 1 Series", "TIME-CB"],
    K.MESSAGE: ["Northstar Bolton", "Service", "SUBJECT-MS", "BODY-MS", "Phone"],
    K.SALES_ENQUIRY: ["Northstar Bolton", "Finance", "BMW 1 Series", "BODY-EN"],
    K.VEHICLE_INTEREST: ["Jaguar F-PACE", "NOTE-VI"],
    K.PART_EXCHANGE: ["Northstar Bolton", "AB12 CDE", "54,321", "Fair"],
}
NO_CONTACT = {K.WORKSHOP_AMEND, K.WORKSHOP_CANCEL}


def card_text(view: View) -> str:
    assert view.block is not None, (view.status, view.message)
    rows = [f"{row.label}: {row.value}" for row in view.block.rows]
    return "\n".join([view.block.title, *rows, *view.block.warnings])


async def make_card(kind: WorkflowKind, client: DealershipClient, service: WriteService) -> View:
    values = dict(VALUES[kind])
    if kind is K.TEST_DRIVE:
        replacement = (await first_drive_slot(client)).id
    elif kind is K.WORKSHOP_CREATE:
        replacement = (await first_workshop_slot(client)).id
    else:
        found = await client.find_workshop_slots(
            dealership_id="northstar-manchester", service_type_id="full-service"
        )
        assert isinstance(found, o.WorkshopSlotsFound)
        replacement = found.slots[0].id
    values = {name: replacement if value == SLOT else value for name, value in values.items()}
    await service.start("s1", kind)
    view = await service.view("s1")
    if DEFINITIONS[kind].needs_verification:
        view = await service.submit_identity("s1", **TAYLOR)
    for name, value in values.items():
        view = await service.fill("s1", name, value)
    assert view.status == "confirming", (kind, view.status, view.message, view.fields)
    return view


def test_every_slot_of_every_write_is_covered_by_this_test() -> None:
    """Adding a slot to a workflow fails here until its card and this test are updated."""
    for kind, values in VALUES.items():
        definition = DEFINITIONS[kind]
        slots = set(definition.required + definition.optional + definition.any_of)
        assert slots <= set(values), (kind, slots - set(values))
    assert set(VALUES) == set(DEFINITIONS) - {K.BOOKING_RETRIEVAL}


@pytest.mark.parametrize("kind", sorted(VALUES, key=lambda item: item.value))
async def test_the_card_shows_everything_that_will_be_saved(
    kind: WorkflowKind, client: DealershipClient, service: WriteService
) -> None:
    text = card_text(await make_card(kind, client, service))
    for expected in EXPECT[kind]:
        if expected == WHEN:
            assert WHEN_PATTERN.search(text), f"no date and time on the {kind.value} card:\n{text}"
        else:
            assert expected in text, f"{expected!r} missing from the {kind.value} card:\n{text}"
    if kind not in NO_CONTACT:
        for shown in CONTACT_SHOWN:
            assert shown in text, f"{shown!r} missing from the {kind.value} card:\n{text}"


@pytest.mark.parametrize("kind", sorted(VALUES, key=lambda item: item.value))
async def test_no_internal_id_is_shown(
    kind: WorkflowKind, client: DealershipClient, service: WriteService
) -> None:
    text = card_text(await make_card(kind, client, service))
    for internal in ("northstar-", "veh-0", "-slot-", "service-and-mot", "full-service", "wsb-"):
        assert internal not in text, f"{internal!r} leaked onto the {kind.value} card:\n{text}"
