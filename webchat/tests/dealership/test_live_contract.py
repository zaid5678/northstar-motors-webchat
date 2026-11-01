"""The client against the real, unmodified platform, on a private port and temporary database.

The respx tests only prove the client matches our fixtures. These prove the fixtures match reality.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.support import PLATFORM_COMMAND, START_LIMIT_SECONDS

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import (
    AmendRequest,
    CallbackRequest,
    Contact,
    DealershipMessageRequest,
    PartExchangeRequest,
    SalesEnquiryRequest,
    TestDriveRequest,
    VehicleInterestRequest,
    VehicleSearch,
    WorkshopBookingRequest,
    WorkshopLookupRequest,
    WorkshopSlot,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="function")]

PLATFORM = Path(__file__).resolve().parents[3] / "dealership-platform"
API_KEY = "northstar-local-development"
ADMIN_KEY = "northstar-local-admin"
CONTACT = Contact(
    first_name="Probe", last_name="Tester", email="probe@example.com", phone="07700900999"
)
TAYLOR = WorkshopLookupRequest(
    reference="WORK-10001", last_name="Taylor", registration="AB12 CDE", phone="07700900123"
)


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture(scope="module")
def platform(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        PLATFORM_COMMAND,
        cwd=PLATFORM,
        env={
            **os.environ,
            "NORTHSTAR_API_KEY": API_KEY,
            "NORTHSTAR_ADMIN_KEY": ADMIN_KEY,
            "NORTHSTAR_DATA_PATH": str(tmp_path_factory.mktemp("platform") / "live.sqlite3"),
            "NORTHSTAR_PORT": str(port),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + START_LIMIT_SECONDS
    while True:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1):
                break
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                process.terminate()
                raise RuntimeError("the platform did not start")
            time.sleep(0.05)
    yield base
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
async def client(platform: str) -> AsyncIterator[DealershipClient]:
    request = urllib.request.Request(
        f"{platform}/admin/api/reset", method="POST", headers={"X-Admin-Key": ADMIN_KEY}
    )
    urllib.request.urlopen(request, timeout=5).close()
    async with DealershipClient(platform, API_KEY) as instance:
        yield instance


async def slot_for(client: DealershipClient, **filters: Any) -> WorkshopSlot:
    found = await client.find_workshop_slots(**filters)
    assert isinstance(found, o.WorkshopSlotsFound)
    return found.slots[0]


def booking_request(slot: WorkshopSlot) -> WorkshopBookingRequest:
    return WorkshopBookingRequest(
        idempotency_key=key(), slot=slot, registration="ab12-cde", mileage=1000, contact=CONTACT
    )


async def test_every_catalogue_read_parses(client: DealershipClient) -> None:
    assert await client.is_healthy()
    dealerships = await client.list_dealerships()
    assert isinstance(dealerships, o.DealershipsListed)
    assert len(dealerships.dealerships) == 4
    assert isinstance(await client.get_dealership("northstar-bolton"), o.DealershipFound)
    assert isinstance(await client.list_service_types(), o.ServiceTypesListed)
    assert isinstance(await client.get_business_information(), o.BusinessInformationFound)
    offers = await client.list_offers()
    assert isinstance(offers, o.OffersListed)
    assert len(offers.offers) == 8
    assert isinstance(await client.get_offer("offer-01"), o.OfferFound)
    assert isinstance(await client.get_vehicle("veh-001"), o.VehicleFound)
    assert await client.get_vehicle("veh-999") == o.NotFound(code="NOT_FOUND")


async def test_seeded_holiday_closes_service_and_leaves_parts(client: DealershipClient) -> None:
    found = await client.get_opening_hours("northstar-manchester")
    assert isinstance(found, o.OpeningHoursFound)
    holiday = found.hours.holiday_exceptions[0].date
    assert found.hours.on(holiday, "service").closed
    assert found.hours.on(holiday, "sales").opens_at == "10:00"
    parts = found.hours.on(holiday, "parts")
    assert not parts.exception_listed
    assert parts.other_departments_excepted


async def test_stock_search_covers_the_whole_seed(client: DealershipClient) -> None:
    found = await client.search_stock(VehicleSearch(), include_sold=True)
    assert isinstance(found, o.StockSearched)
    assert (len(found.available), len(found.reserved), len(found.sold)) == (55, 3, 2)


async def test_budget_search_recovers_both_price_on_request_cars(client: DealershipClient) -> None:
    found = await client.search_stock(VehicleSearch(max_price_pence=99_999_999))
    assert isinstance(found, o.StockSearched)
    assert sorted(v.id for v in found.price_on_request) == ["veh-019", "veh-046"]


@pytest.mark.parametrize(
    ("vehicle_id", "state", "interest"),
    [("veh-007", "reserved", True), ("veh-013", "sold", False)],
)
async def test_seeded_cars_that_cannot_be_driven(
    client: DealershipClient, vehicle_id: str, state: str, interest: bool
) -> None:
    status = await client.get_vehicle_status(vehicle_id)
    assert isinstance(status, o.VehicleStatus)
    assert status.next_slot_id is None
    assert await client.find_test_drive_slots(vehicle_id) == o.TestDriveNotBookable(
        vehicle_id=vehicle_id, availability=state, can_register_interest=interest
    )


async def test_test_drive_booking_replay_and_conflict(client: DealershipClient) -> None:
    slots = await client.find_test_drive_slots("veh-001")
    assert isinstance(slots, o.TestDriveSlotsFound)
    request = TestDriveRequest(
        idempotency_key=key(), slot=slots.slots[0], vehicle_id="veh-001", contact=CONTACT
    )
    first = await client.book_test_drive(request)
    assert isinstance(first, o.TestDriveBooked)
    assert first.reference.startswith("TEST-")

    stored = TestDriveRequest.model_validate_json(request.model_dump_json())
    replay = await client.book_test_drive(stored)
    assert isinstance(replay, o.TestDriveBooked)
    assert replay.replayed
    assert replay.id == first.id

    taken = await client.book_test_drive(request.model_copy(update={"idempotency_key": key()}))
    assert taken == o.SlotTaken(slot_id=slots.slots[0].id)

    saved = await client.get_test_drive_booking(first.id)
    assert isinstance(saved, o.TestDriveBookingFound)


async def test_a_body_that_changed_under_the_same_key_is_our_fault(
    client: DealershipClient,
) -> None:
    request = SalesEnquiryRequest(
        idempotency_key=key(),
        dealership_id="northstar-manchester",
        enquiry_type="general",
        contact=CONTACT,
        message="First version of the message",
    )
    assert isinstance(await client.send_enquiry(request), o.EnquiryReceived)
    changed = request.model_copy(update={"message": "Second version of the message"})
    assert await client.send_enquiry(changed) == o.IntegrationFault(
        status=409, code="IDEMPOTENCY_CONFLICT"
    )


async def test_interest_is_only_for_reserved_cars(client: DealershipClient) -> None:
    def interest(vehicle_id: str) -> VehicleInterestRequest:
        return VehicleInterestRequest(
            idempotency_key=key(), vehicle_id=vehicle_id, contact=CONTACT
        )

    assert isinstance(await client.register_interest(interest("veh-007")), o.InterestRegistered)
    for vehicle_id in ("veh-001", "veh-013"):
        assert await client.register_interest(interest(vehicle_id)) == o.InterestNotAccepted(
            vehicle_id=vehicle_id
        )


async def test_records_that_commit_to_nothing(client: DealershipClient) -> None:
    enquiry = await client.send_enquiry(
        SalesEnquiryRequest(
            idempotency_key=key(),
            dealership_id="northstar-manchester",
            vehicle_id="veh-013",
            enquiry_type="availability",
            contact=CONTACT,
            message="Is this still for sale?",
        )
    )
    assert isinstance(enquiry, o.EnquiryReceived)
    callback = await client.request_callback(
        CallbackRequest(
            idempotency_key=key(),
            dealership_id="northstar-manchester",
            department="sales",
            contact=CONTACT,
            preferred_time="3am on a Sunday",
            reason="Discuss finance",
        )
    )
    assert isinstance(callback, o.CallbackRequested)
    message = await client.leave_message(
        DealershipMessageRequest(
            idempotency_key=key(),
            dealership_id="northstar-manchester",
            department="general",
            subject="Hello",
            message="Please call me back",
            preferred_contact_method="phone",
            contact=CONTACT,
        )
    )
    assert isinstance(message, o.MessageReceived)


async def test_invalid_contact_details_are_correctable(client: DealershipClient) -> None:
    result = await client.leave_message(
        DealershipMessageRequest(
            idempotency_key=key(),
            dealership_id="northstar-manchester",
            department="general",
            subject="Hello",
            message="Please call me back",
            preferred_contact_method="phone",
            contact=CONTACT.model_copy(update={"phone": "02071234567"}),
        )
    )
    assert isinstance(result, o.InvalidDetails)
    assert "phone" in result.field_errors


async def test_an_estimate_keeps_its_notice_on_replay(client: DealershipClient) -> None:
    request = PartExchangeRequest(
        idempotency_key=key(),
        dealership_id="northstar-manchester",
        registration="AB12 CDE",
        mileage=54000,
        condition="good",
        contact=CONTACT,
    )
    first = await client.request_estimate(request)
    replay = await client.request_estimate(request)
    assert isinstance(first, o.EstimateGiven)
    assert isinstance(replay, o.EstimateGiven)
    assert replay.replayed
    assert replay.notice == first.notice
    assert replay.estimate_low_pence == first.estimate_low_pence


async def test_lookup_accepts_natural_formats_and_hides_pii(client: DealershipClient) -> None:
    found = await client.find_workshop_booking(
        TAYLOR.model_copy(
            update={"reference": "work-10001", "last_name": "TAYLOR", "phone": "+44 7700 900123"}
        )
    )
    assert isinstance(found, o.WorkshopBookingFound)
    assert found.booking.service_type_name == "Full service"
    assert found.booking.starts_at is not None
    dumped = found.model_dump_json()
    assert "jamie" not in dumped.lower()
    assert "07700900123" not in dumped
    assert "AB12" not in dumped


async def test_hyphenated_registration_matches(client: DealershipClient) -> None:
    lookup = WorkshopLookupRequest(
        reference="WORK-10001", last_name="Taylor", registration="ab12-cde", phone="07700900123"
    )
    assert isinstance(await client.find_workshop_booking(lookup), o.WorkshopBookingFound)


@pytest.mark.parametrize(
    "change",
    [
        {"reference": "WORK-99999"},
        {"last_name": "Morgan"},
        {"registration": "XY34 ZZZ"},
        {"phone": "07700900456"},
    ],
)
async def test_any_wrong_detail_gives_the_same_answer(
    client: DealershipClient, change: dict[str, str]
) -> None:
    result = await client.find_workshop_booking(TAYLOR.model_copy(update=change))
    assert result == o.BookingNotMatched()


async def test_a_workshop_move_across_site_and_service_is_reported(
    client: DealershipClient,
) -> None:
    found = await client.find_workshop_booking(TAYLOR)
    assert isinstance(found, o.WorkshopBookingFound)
    target = await slot_for(
        client, dealership_id="northstar-liverpool", service_type_id="diagnostic"
    )
    result = await client.amend_workshop_booking(
        AmendRequest(booking=found.booking, target_slot=target)
    )
    assert isinstance(result, o.BookingAmended)
    assert result.site_changed
    assert result.service_changed
    assert result.starts_at == target.starts_at


async def test_a_released_slot_is_reported_unbookable_with_one_attempt(
    client: DealershipClient,
) -> None:
    cancelled = await client.cancel_workshop_booking("wsb-seeded-001")
    assert isinstance(cancelled, o.BookingCancelled)
    assert not cancelled.already_cancelled
    assert cancelled.slot_id == "ws-slot-0001"

    again = await client.cancel_workshop_booking("wsb-seeded-001")
    assert isinstance(again, o.BookingCancelled)
    assert again.already_cancelled

    found = await client.find_workshop_booking(TAYLOR)
    assert isinstance(found, o.WorkshopBookingFound)
    assert found.booking.is_cancelled
    assert await client.amend_workshop_booking(
        AmendRequest(booking=found.booking, notes="Try to amend")
    ) == o.BookingAlreadyCancelled(booking_id="wsb-seeded-001")

    released = await client.find_workshop_slots(dealership_id="northstar-manchester")
    assert isinstance(released, o.WorkshopSlotsFound)
    slot = next(item for item in released.slots if item.id == "ws-slot-0001")
    assert await client.book_workshop(booking_request(slot)) == o.SlotUnbookable(
        slot_id="ws-slot-0001"
    )


async def test_moving_into_a_released_slot_leaves_the_booking_alone(
    client: DealershipClient,
) -> None:
    await client.cancel_workshop_booking("wsb-seeded-001")
    released = await client.find_workshop_slots(dealership_id="northstar-manchester")
    assert isinstance(released, o.WorkshopSlotsFound)
    poisoned = next(item for item in released.slots if item.id == "ws-slot-0001")
    live = await client.book_workshop(
        booking_request(next(item for item in released.slots if item.id != "ws-slot-0001"))
    )
    assert isinstance(live, o.WorkshopBooked)
    before = await client.get_workshop_booking(live.id)
    assert isinstance(before, o.WorkshopBookingFound)
    result = await client.amend_workshop_booking(
        AmendRequest(booking=before.booking, target_slot=poisoned)
    )
    assert result == o.SlotUnbookable(slot_id="ws-slot-0001")
    after = await client.get_workshop_booking(live.id)
    assert isinstance(after, o.WorkshopBookingFound)
    assert after.booking.slot_id == live.slot_id


async def test_a_replay_after_cancellation_is_not_a_booking(client: DealershipClient) -> None:
    request = booking_request(await slot_for(client, dealership_id="northstar-stockport"))
    booked = await client.book_workshop(request)
    assert isinstance(booked, o.WorkshopBooked)
    await client.cancel_workshop_booking(booked.id)
    assert await client.book_workshop(request) == o.BookingNoLongerActive(
        id=booked.id, reference=booked.reference
    )


async def test_a_wrong_api_key_is_a_fault_not_a_customer_error(platform: str) -> None:
    async with DealershipClient(platform, "wrong-key") as client:
        result = await client.get_workshop_booking("wsb-seeded-001")
    assert result == o.IntegrationFault(status=401, code="UNAUTHORISED")
