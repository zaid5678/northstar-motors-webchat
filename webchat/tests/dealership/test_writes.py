"""Writes: what each platform result means for the customer."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

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
    WorkshopBooking,
    WorkshopBookingRequest,
    WorkshopLookupRequest,
)

from .conftest import (
    PAST,
    PII,
    business_information,
    contact,
    error,
    internal_error,
    lookup_record,
    drive_slot,
    workshop_record,
    workshop_slot,
)


def created(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(201, json=payload)


def record(prefix: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{prefix.lower()}-1",
        "reference": f"{prefix}-ABC12345",
        **PII,
        "status": status,
        "createdAt": "2026-11-01T12:00:00Z",
        **extra,
    }


def td_record(**extra: Any) -> dict[str, Any]:
    return record(
        "TEST",
        "confirmed",
        slotId="td-slot-0001",
        dealershipId="northstar-manchester",
        vehicleId="veh-001",
        notes=None,
        **extra,
    )


def ws_record(**extra: Any) -> dict[str, Any]:
    return workshop_record(
        id="wsb-1",
        reference="WORK-ABC12345",
        slotId="ws-slot-0002",
        serviceTypeId="mot",
        **extra,
    )


def enquiry(**extra: Any) -> SalesEnquiryRequest:
    return SalesEnquiryRequest(
        idempotency_key="key-1",
        dealership_id="northstar-manchester",
        enquiry_type="availability",
        contact=contact(),
        message="Is it still there?",
        **extra,
    )


def interest() -> VehicleInterestRequest:
    return VehicleInterestRequest(idempotency_key="key-1", vehicle_id="veh-007", contact=contact())


def callback() -> CallbackRequest:
    return CallbackRequest(
        idempotency_key="key-1",
        dealership_id="northstar-manchester",
        department="sales",
        contact=contact(),
        preferred_time="3am on a Sunday",
        reason="Discuss finance",
    )


def message() -> DealershipMessageRequest:
    return DealershipMessageRequest(
        idempotency_key="key-1",
        dealership_id="northstar-manchester",
        department="general",
        subject="Hello",
        message="Please call me",
        preferred_contact_method="phone",
        contact=contact(),
    )


def estimate() -> PartExchangeRequest:
    return PartExchangeRequest(
        idempotency_key="key-1",
        dealership_id="northstar-manchester",
        registration="AB12 CDE",
        mileage=54000,
        condition="good",
        contact=contact(),
    )


def drive_request(**overrides: Any) -> TestDriveRequest:
    values: dict[str, Any] = {
        "idempotency_key": "key-1",
        "slot": drive_slot(),
        "vehicle_id": "veh-001",
        "contact": contact(),
        **overrides,
    }
    return TestDriveRequest(**values)


def workshop(**overrides: Any) -> WorkshopBookingRequest:
    values: dict[str, Any] = {
        "idempotency_key": "key-1",
        "slot": workshop_slot(),
        "registration": "AB12 CDE",
        "mileage": 1000,
        "contact": contact(),
        **overrides,
    }
    return WorkshopBookingRequest(**values)


def verified_booking(**overrides: Any) -> WorkshopBooking:
    return WorkshopBooking.model_validate(lookup_record(**overrides))


def lookup(**overrides: Any) -> WorkshopLookupRequest:
    values = {
        "reference": "WORK-10001",
        "last_name": "Taylor",
        "registration": "AB12 CDE",
        "phone": "07700900123",
        **overrides,
    }
    return WorkshopLookupRequest(**values)


LOOKUP = "/api/workshop-bookings/lookup"
BOOKING = "/api/workshop-bookings/wsb-seeded-001"


class TestRequestsAreStable:
    def test_a_stored_request_rebuilds_the_identical_body(self) -> None:
        original = workshop(notes="Please check the brakes")
        restored = WorkshopBookingRequest.model_validate_json(original.model_dump_json())
        assert restored.body() == original.body()
        assert restored.idempotency_key == original.idempotency_key

    def test_a_write_cannot_be_built_without_a_key(self) -> None:
        with pytest.raises(ValidationError):
            VehicleInterestRequest(vehicle_id="veh-007", contact=contact())  # type: ignore[call-arg]

    def test_a_blank_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            VehicleInterestRequest(idempotency_key="   ", vehicle_id="veh-007", contact=contact())

    def test_bodies_flatten_contact_and_use_slot_ids(self) -> None:
        assert drive_request().body() == {
            "slotId": "td-slot-0001",
            "firstName": "Probe",
            "lastName": "Tester",
            "email": "probe@example.com",
            "phone": "07700900999",
        }

    def test_a_0044_phone_is_normalised_before_it_is_stored(self) -> None:
        details = Contact(
            first_name="Probe",
            last_name="Tester",
            email="probe@example.com",
            phone="0044 7700 900999",
        )
        assert details.phone == "07700900999"


class TestRecordsThatCommitToNothing:
    async def test_enquiry_on_a_sold_car_is_only_received(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/sales-enquiries").mock(created(record("SALE", "received")))
        result = await client.send_enquiry(enquiry(vehicle_id="veh-013"))
        assert result == o.EnquiryReceived(id="sale-1", reference="SALE-ABC12345", replayed=False)

    async def test_interest_is_registered(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/vehicle-interests").mock(created(record("WAIT", "registered")))
        result = await client.register_interest(interest())
        assert isinstance(result, o.InterestRegistered)
        assert "position" not in result.model_dump()

    async def test_interest_on_a_car_that_is_not_reserved_is_refused(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/vehicle-interests").mock(error(409, "VEHICLE_NOT_RESERVED"))
        result = await client.register_interest(interest())
        assert result == o.InterestNotAccepted(vehicle_id="veh-007")

    async def test_callback_is_requested(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/callback-requests").mock(created(record("CALL", "requested")))
        result = await client.request_callback(callback())
        assert isinstance(result, o.CallbackRequested)
        assert json.loads(route.calls.last.request.content)["preferredTime"] == "3am on a Sunday"

    async def test_message_is_received(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/dealership-messages").mock(created(record("MSG", "received")))
        assert isinstance(await client.leave_message(message()), o.MessageReceived)

    async def test_every_create_carries_key_and_auth(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        from .conftest import API_KEY

        route = api.post("/api/sales-enquiries").mock(created(record("SALE", "received")))
        await client.send_enquiry(enquiry())
        headers = route.calls.last.request.headers
        assert headers["idempotency-key"] == "key-1"
        assert headers["x-api-key"] == API_KEY

    @pytest.mark.parametrize("status", ["pending", "confirmed", ""])
    async def test_a_status_we_do_not_recognise_is_never_called_success(
        self, client: DealershipClient, api: respx.MockRouter, status: str
    ) -> None:
        api.post("/api/sales-enquiries").mock(created(record("SALE", status)))
        result = await client.send_enquiry(enquiry())
        assert isinstance(result, o.WriteOutcomeUnknown)
        assert result.reason == "malformed_response"
        assert result.idempotency_key == "key-1"

    async def test_a_replay_is_marked_so_it_is_not_reported_as_new(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/sales-enquiries").mock(
            created(record("SALE", "received", idempotentReplay=True))
        )
        result = await client.send_enquiry(enquiry())
        assert isinstance(result, o.EnquiryReceived)
        assert result.replayed


class TestEstimates:
    def row(self, **extra: Any) -> dict[str, Any]:
        fields = {"estimateLowPence": 1290000, "estimateHighPence": 1445000, **extra}
        return record("PX", "estimated", **fields)

    async def test_first_response_keeps_its_notice(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        info = api.get("/api/business-information")
        api.post("/api/part-exchange-valuations").mock(
            created(self.row(estimateNotice="Indicative only."))
        )
        result = await client.request_estimate(estimate())
        assert isinstance(result, o.EstimateGiven)
        assert result.notice == "Indicative only."
        assert not info.called

    async def test_a_replay_gets_its_lost_notice_back(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/part-exchange-valuations").mock(created(self.row(idempotentReplay=True)))
        api.get("/api/business-information").mock(
            httpx.Response(200, json=business_information())
        )
        result = await client.request_estimate(estimate())
        assert isinstance(result, o.EstimateGiven)
        assert result.notice == "Online estimates are indicative."
        assert result.replayed

    async def test_figures_are_withheld_if_no_notice_can_be_found(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/part-exchange-valuations").mock(created(self.row(idempotentReplay=True)))
        api.get("/api/business-information").mock(httpx.Response(503))
        result = await client.request_estimate(estimate())
        assert result == o.EstimateWithoutNotice(
            id="px-1", reference="PX-ABC12345", replayed=True
        )
        assert "1290000" not in result.model_dump_json()

    async def test_an_inverted_range_is_not_believed(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/part-exchange-valuations").mock(
            created(self.row(estimateLowPence=2000000, estimateNotice="Indicative only."))
        )
        result = await client.request_estimate(estimate())
        assert isinstance(result, o.WriteOutcomeUnknown)


class TestTestDriveBooking:
    async def test_confirmation_carries_the_time_and_place_the_response_lacks(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/test-drive-bookings").mock(created(td_record()))
        result = await client.book_test_drive(drive_request())
        assert isinstance(result, o.TestDriveBooked)
        assert result.reference == "TEST-ABC12345"
        assert result.dealership_name == "Northstar Manchester"
        assert result.starts_at == drive_slot().starts_at
        assert not result.replayed

    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("VEHICLE_RESERVED", o.VehicleReserved(vehicle_id="veh-001")),
            ("VEHICLE_UNAVAILABLE", o.VehicleUnavailable(vehicle_id="veh-001")),
            ("SLOT_UNAVAILABLE", o.SlotTaken(slot_id="td-slot-0001")),
        ],
    )
    async def test_refusals_are_named_outcomes(
        self, client: DealershipClient, api: respx.MockRouter, code: str, expected: o.Outcome
    ) -> None:
        api.post("/api/test-drive-bookings").mock(error(409, code))
        assert await client.book_test_drive(drive_request()) == expected

    async def test_a_past_slot_is_refused_without_calling_the_platform(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/test-drive-bookings")
        result = await client.book_test_drive(drive_request(slot=drive_slot(startsAt=PAST)))
        assert result == o.SlotInPast(slot_id="td-slot-0001")
        assert not route.called

    async def test_a_slot_for_another_car_is_refused_without_calling_the_platform(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/test-drive-bookings")
        result = await client.book_test_drive(drive_request(vehicle_id="veh-002"))
        assert result == o.SlotVehicleMismatch(
            slot_id="td-slot-0001", expected_vehicle_id="veh-002", slot_vehicle_id="veh-001"
        )
        assert not route.called

    async def test_booking_the_wrong_car_is_flagged_not_confirmed(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = {**td_record(), "vehicleId": "veh-999"}
        api.post("/api/test-drive-bookings").mock(created(payload))
        result = await client.book_test_drive(drive_request())
        assert isinstance(result, o.BookingMismatch)
        assert result.booked_vehicle_id == "veh-999"

    async def test_the_vehicle_id_is_not_sent_to_the_platform(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/test-drive-bookings").mock(created(td_record()))
        await client.book_test_drive(drive_request())
        assert "vehicleId" not in json.loads(route.calls.last.request.content)

    async def test_a_saved_booking_can_be_read_back_without_pii(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/test-drive-bookings/test-1").mock(httpx.Response(200, json=td_record()))
        result = await client.get_test_drive_booking("test-1")
        assert isinstance(result, o.TestDriveBookingFound)
        assert PII["email"] not in result.model_dump_json()


class TestWorkshopBooking:
    async def test_confirmation_carries_service_and_time(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/workshop-bookings").mock(created(ws_record()))
        result = await client.book_workshop(workshop())
        assert isinstance(result, o.WorkshopBooked)
        assert result.service_name == "MOT"
        assert result.starts_at == workshop_slot().starts_at

    async def test_a_replay_of_a_cancelled_booking_is_not_a_booking(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = {
            **ws_record(),
            "status": "cancelled",
            "cancelledAt": "2026-11-01T12:30:00Z",
            "idempotentReplay": True,
        }
        api.post("/api/workshop-bookings").mock(created(payload))
        result = await client.book_workshop(workshop())
        assert result == o.BookingNoLongerActive(id="wsb-1", reference="WORK-ABC12345")

    async def test_a_taken_or_blocked_slot(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/workshop-bookings").mock(error(409, "SLOT_UNAVAILABLE"))
        assert await client.book_workshop(workshop()) == o.SlotTaken(slot_id="ws-slot-0002")

    async def test_a_released_slot_failure_is_a_named_outcome(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/workshop-bookings").mock(internal_error())
        assert await client.book_workshop(workshop()) == o.SlotUnbookable(slot_id="ws-slot-0002")

    async def test_a_past_slot_is_refused_without_calling_the_platform(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/workshop-bookings")
        result = await client.book_workshop(workshop(slot=workshop_slot(startsAt=PAST)))
        assert result == o.SlotInPast(slot_id="ws-slot-0002")
        assert not route.called


class TestLookup:
    async def test_found_booking_has_appointment_details(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post(LOOKUP).mock(httpx.Response(200, json=lookup_record()))
        result = await client.find_workshop_booking(lookup())
        assert isinstance(result, o.WorkshopBookingFound)
        assert result.booking.service_type_name == "Full service"
        assert result.booking.dealership_postcode == "M20 2YY"
        assert not result.booking.is_cancelled

    async def test_personal_details_never_leave_the_client(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post(LOOKUP).mock(httpx.Response(200, json=lookup_record()))
        result = await client.find_workshop_booking(lookup())
        dumped = result.model_dump_json()
        for value in [*PII.values(), "AB12 CDE", "Customer will wait"]:
            assert value not in dumped

    async def test_a_cancelled_booking_is_found_and_marked(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post(LOOKUP).mock(httpx.Response(200, json=lookup_record("cancelled")))
        result = await client.find_workshop_booking(lookup())
        assert isinstance(result, o.WorkshopBookingFound)
        assert result.booking.is_cancelled

    async def test_a_mismatch_reveals_nothing(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post(LOOKUP).mock(error(404, "BOOKING_NOT_FOUND", message="No match."))
        result = await client.find_workshop_booking(lookup())
        assert result == o.BookingNotMatched()

    async def test_a_format_error_is_correctable(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post(LOOKUP).mock(
            error(422, "VALIDATION_ERROR", field_errors={"phone": "Enter a UK number."})
        )
        result = await client.find_workshop_booking(lookup(phone="123"))
        assert isinstance(result, o.InvalidDetails)
        assert result.field_errors == {"phone": "Enter a UK number."}

    async def test_hyphens_are_removed_from_registrations(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post(LOOKUP).mock(httpx.Response(200, json=lookup_record()))
        await client.find_workshop_booking(lookup(registration="ab12-cde"))
        assert json.loads(route.calls.last.request.content)["registration"] == "AB12 CDE"

    async def test_lookup_sends_no_idempotency_key(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post(LOOKUP).mock(httpx.Response(200, json=lookup_record()))
        await client.find_workshop_booking(lookup())
        assert "idempotency-key" not in route.calls.last.request.headers


class TestAmend:
    def test_an_empty_amendment_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            AmendRequest(booking=verified_booking())

    async def test_a_move_reports_the_new_time(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        patched = workshop_record(slotId="ws-slot-0009")
        route = api.patch(BOOKING).mock(httpx.Response(200, json=patched))
        slot = workshop_slot(
            slot_id="ws-slot-0009", serviceTypeId="full-service", startsAt="2026-11-06T09:00:00Z"
        )
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), target_slot=slot)
        )
        assert isinstance(result, o.BookingAmended)
        assert result.starts_at == slot.starts_at
        assert not result.site_changed
        assert not result.service_changed
        assert json.loads(route.calls.last.request.content) == {"slotId": "ws-slot-0009"}

    async def test_a_silent_change_of_site_and_service_is_reported(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.patch(BOOKING).mock(
            httpx.Response(
                200,
                json=workshop_record(
                    slotId="ws-slot-0010",
                    dealershipId="northstar-liverpool",
                    serviceTypeId="diagnostic",
                ),
            )
        )
        slot = workshop_slot(
            slot_id="ws-slot-0010",
            dealershipId="northstar-liverpool",
            serviceTypeId="diagnostic",
        )
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), target_slot=slot)
        )
        assert isinstance(result, o.BookingAmended)
        assert result.site_changed
        assert result.service_changed

    async def test_a_notes_only_change_has_no_new_time(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.patch(BOOKING).mock(httpx.Response(200, json=workshop_record(notes="New")))
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), notes="New")
        )
        assert isinstance(result, o.BookingAmended)
        assert result.starts_at is None
        assert json.loads(route.calls.last.request.content) == {"notes": "New"}
        assert "idempotency-key" not in route.calls.last.request.headers

    async def test_a_cancelled_booking_cannot_be_amended(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.patch(BOOKING).mock(error(409, "BOOKING_CANCELLED"))
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), notes="New")
        )
        assert result == o.BookingAlreadyCancelled(booking_id="wsb-seeded-001")

    async def test_a_taken_slot_leaves_the_booking_alone(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.patch(BOOKING).mock(error(409, "SLOT_UNAVAILABLE"))
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), target_slot=workshop_slot())
        )
        assert result == o.SlotTaken(slot_id="ws-slot-0002")

    async def test_a_past_slot_is_refused_without_calling_the_platform(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.patch(BOOKING)
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), target_slot=workshop_slot(startsAt=PAST))
        )
        assert result == o.SlotInPast(slot_id="ws-slot-0002")
        assert not route.called

    async def test_an_unknown_booking(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.patch(BOOKING).mock(error(404, "NOT_FOUND"))
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), notes="New")
        )
        assert result == o.NotFound(code="NOT_FOUND")


class TestCancel:
    async def test_cancelling_reports_the_released_slot(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record()))
        api.delete(BOOKING).mock(httpx.Response(200, json=workshop_record("cancelled")))
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert result == o.BookingCancelled(
            id="wsb-seeded-001",
            reference="WORK-10001",
            slot_id="ws-slot-0001",
            already_cancelled=False,
            reconciled=False,
        )

    async def test_an_already_cancelled_booking_is_not_cancelled_again(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record("cancelled")))
        delete = api.delete(BOOKING)
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert isinstance(result, o.BookingCancelled)
        assert result.already_cancelled
        assert not delete.called

    async def test_nothing_is_cancelled_if_the_booking_cannot_be_read(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(httpx.Response(503))
        delete = api.delete(BOOKING)
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert isinstance(result, o.PlatformUnavailable)
        assert not delete.called

    async def test_an_unknown_booking(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(error(404, "NOT_FOUND"))
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert result == o.NotFound(code="NOT_FOUND")


class TestRejections:
    async def test_validation_errors_reach_the_customer(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/dealership-messages").mock(
            error(422, "VALIDATION_ERROR", field_errors={"phone": "Enter a UK number."})
        )
        result = await client.leave_message(message())
        assert isinstance(result, o.InvalidDetails)
        assert result.field_errors == {"phone": "Enter a UK number."}

    async def test_an_unknown_dealership_is_not_found(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/sales-enquiries").mock(error(404, "NOT_FOUND"))
        assert await client.send_enquiry(enquiry()) == o.NotFound(code="NOT_FOUND")

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (401, "UNAUTHORISED"),
            (409, "IDEMPOTENCY_CONFLICT"),
            (400, "IDEMPOTENCY_KEY_REQUIRED"),
            (400, "INVALID_IDEMPOTENCY_KEY"),
            (400, "INVALID_JSON"),
            (413, "REQUEST_TOO_LARGE"),
            (409, "A_CODE_WE_HAVE_NEVER_SEEN"),
        ],
    )
    async def test_our_own_faults_are_not_customer_errors(
        self, client: DealershipClient, api: respx.MockRouter, status: int, code: str
    ) -> None:
        api.post("/api/sales-enquiries").mock(error(status, code))
        assert await client.send_enquiry(enquiry()) == o.IntegrationFault(status=status, code=code)

    async def test_a_4xx_without_our_error_shape_is_still_a_fault(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/sales-enquiries").mock(httpx.Response(403, text="<html>Forbidden</html>"))
        assert await client.send_enquiry(enquiry()) == o.IntegrationFault(status=403, code=None)
