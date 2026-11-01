"""What is retried, what never is, and what happens when the answer is lost."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from northstar_webchat.dealership import DealershipClient, RetryPolicy
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import AmendRequest, VehicleSearch

from .conftest import (
    API_KEY,
    BASE,
    dealership,
    error,
    internal_error,
    lookup_record,
    page,
    vehicle,
    workshop_record,
    workshop_slot,
)
from .test_writes import (
    BOOKING,
    LOOKUP,
    enquiry,
    lookup,
    record,
    drive_request,
    td_record,
    verified_booking,
    workshop,
    ws_record,
)


def listed() -> httpx.Response:
    return httpx.Response(200, json={"items": [dealership()]})


TRANSIENT = [
    httpx.ConnectError("refused"),
    httpx.ConnectTimeout("connect"),
    httpx.ReadTimeout("read"),
    httpx.ReadError("reset"),
    httpx.RemoteProtocolError("dropped"),
    httpx.Response(429),
    httpx.Response(500),
    httpx.Response(502),
    httpx.Response(503),
    httpx.Response(504),
]
AMBIGUOUS = [
    httpx.ReadTimeout("read"),
    httpx.WriteTimeout("write"),
    httpx.ReadError("reset"),
    httpx.RemoteProtocolError("dropped"),
]


def label(item: Any) -> str:
    return type(item).__name__ if isinstance(item, Exception) else f"HTTP{item.status_code}"


class TestReadRetries:
    @pytest.mark.parametrize("failure", TRANSIENT, ids=label)
    async def test_a_transient_failure_is_retried(
        self, client: DealershipClient, api: respx.MockRouter, failure: Any
    ) -> None:
        route = api.get("/api/dealerships").mock(side_effect=[failure, listed()])
        result = await client.list_dealerships()
        assert isinstance(result, o.DealershipsListed)
        assert route.call_count == 2

    async def test_retries_stop_at_the_limit(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.get("/api/dealerships").mock(side_effect=httpx.ReadTimeout("read"))
        result = await client.list_dealerships()
        assert isinstance(result, o.PlatformUnavailable)
        assert result.attempts == 3
        assert route.call_count == 3

    async def test_backoff_doubles(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        sleeps: list[float],
    ) -> None:
        api.get("/api/dealerships").mock(side_effect=httpx.ReadTimeout("read"))
        await client.list_dealerships()
        assert sleeps == [0.2, 0.4]

    async def test_backoff_is_capped(self, api: respx.MockRouter) -> None:
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        api.get("/api/dealerships").mock(side_effect=httpx.ReadTimeout("read"))
        policy = RetryPolicy(max_attempts=5, base_delay=0.5, max_delay=1.0)
        async with DealershipClient(
            BASE, API_KEY, retry=policy, sleep=sleep, rng=lambda: 0.0
        ) as client:
            await client.list_dealerships()
        assert delays == [0.5, 1.0, 1.0, 1.0]

    async def test_jitter_spreads_the_delay(self, api: respx.MockRouter) -> None:
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        api.get("/api/dealerships").mock(side_effect=httpx.ReadTimeout("read"))
        async with DealershipClient(BASE, API_KEY, sleep=sleep, rng=lambda: 1.0) as client:
            await client.list_dealerships()
        assert delays == pytest.approx([0.25, 0.5])

    async def test_retry_after_is_honoured_up_to_the_cap(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        sleeps: list[float],
    ) -> None:
        api.get("/api/dealerships").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0.7"}),
                httpx.Response(429, headers={"Retry-After": "120"}),
                listed(),
            ]
        )
        await client.list_dealerships()
        assert sleeps == [0.7, 1.0]

    async def test_a_turn_stops_retrying_at_its_deadline(self, api: respx.MockRouter) -> None:
        clock = [0.0]

        async def sleep(delay: float) -> None:
            clock[0] += delay

        def slow(request: httpx.Request) -> httpx.Response:
            clock[0] += 5.0
            raise httpx.ReadTimeout("read", request=request)

        route = api.get("/api/dealerships").mock(side_effect=slow)
        async with DealershipClient(
            BASE,
            API_KEY,
            retry=RetryPolicy(max_attempts=5, deadline=8.0),
            sleep=sleep,
            monotonic=lambda: clock[0],
            rng=lambda: 0.0,
        ) as client:
            result = await client.list_dealerships()
        assert isinstance(result, o.PlatformUnavailable)
        assert route.call_count == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 422])
    async def test_a_4xx_is_never_retried(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        sleeps: list[float],
        status: int,
    ) -> None:
        route = api.get("/api/dealerships/x").mock(error(status, "ANY"))
        await client.get_dealership("x")
        assert route.call_count == 1
        assert sleeps == []

    @pytest.mark.parametrize(
        "body",
        ["not json", "", "[]", '{"items": "nope"}', '{"items": [{"id": "x"}]}', "null"],
    )
    async def test_a_malformed_200_is_never_retried(
        self, client: DealershipClient, api: respx.MockRouter, body: str
    ) -> None:
        route = api.get("/api/dealerships").mock(httpx.Response(200, text=body))
        result = await client.list_dealerships()
        assert isinstance(result, o.MalformedResponse)
        assert route.call_count == 1

    async def test_a_malformed_5xx_body_does_not_break_retries(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/dealerships").mock(httpx.Response(502, text="<html>Bad gateway</html>"))
        assert isinstance(await client.list_dealerships(), o.PlatformUnavailable)


class TestLookupRetries:
    async def test_lookup_changes_nothing_so_a_timeout_is_retried(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post(LOOKUP).mock(
            side_effect=[httpx.ReadTimeout("read"), httpx.Response(200, json=lookup_record())]
        )
        result = await client.find_workshop_booking(lookup())
        assert isinstance(result, o.WorkshopBookingFound)
        assert route.call_count == 2

    async def test_a_mismatch_is_never_retried(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post(LOOKUP).mock(error(404, "BOOKING_NOT_FOUND"))
        await client.find_workshop_booking(lookup())
        assert route.call_count == 1


class TestKeyedCreateRetries:
    @pytest.mark.parametrize("failure", AMBIGUOUS, ids=label)
    async def test_a_lost_response_resends_the_same_key_and_bytes(
        self, client: DealershipClient, api: respx.MockRouter, failure: Any
    ) -> None:
        route = api.post("/api/test-drive-bookings").mock(
            side_effect=[failure, httpx.Response(201, json=td_record(idempotentReplay=True))]
        )
        result = await client.book_test_drive(drive_request())
        assert isinstance(result, o.TestDriveBooked)
        assert result.replayed
        first, second = (call.request for call in route.calls)
        assert first.headers["idempotency-key"] == second.headers["idempotency-key"]
        assert first.content == second.content

    @pytest.mark.parametrize("status", [502, 503, 504, 429])
    async def test_a_gateway_failure_is_retried_on_a_booking(
        self, client: DealershipClient, api: respx.MockRouter, status: int
    ) -> None:
        route = api.post("/api/test-drive-bookings").mock(
            side_effect=[httpx.Response(status), httpx.Response(201, json=td_record())]
        )
        assert isinstance(await client.book_test_drive(drive_request()), o.TestDriveBooked)
        assert route.call_count == 2

    async def test_a_500_is_retried_on_a_non_booking_create(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/sales-enquiries").mock(
            side_effect=[internal_error(), httpx.Response(201, json=record("SALE", "received"))]
        )
        assert isinstance(await client.send_enquiry(enquiry()), o.EnquiryReceived)
        assert route.call_count == 2

    async def test_exhausted_retries_leave_the_outcome_unknown(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/test-drive-bookings").mock(side_effect=httpx.ReadTimeout("read"))
        result = await client.book_test_drive(drive_request())
        assert result == o.WriteOutcomeUnknown(
            operation="book_test_drive",
            reason="no_response",
            attempts=3,
            idempotency_key="key-1",
        )
        assert route.call_count == 3

    async def test_exhausted_gateway_errors_leave_the_outcome_unknown(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/sales-enquiries").mock(httpx.Response(503))
        result = await client.send_enquiry(enquiry())
        assert isinstance(result, o.WriteOutcomeUnknown)
        assert result.reason == "server_error"

    async def test_a_write_that_never_connected_is_known_not_sent(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/test-drive-bookings").mock(side_effect=httpx.ConnectError("refused"))
        result = await client.book_test_drive(drive_request())
        assert result == o.WriteNotSent(operation="book_test_drive", attempts=3)

    async def test_one_lost_response_makes_the_whole_write_unknown(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/test-drive-bookings").mock(
            side_effect=[
                httpx.ReadTimeout("read"),
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused"),
            ]
        )
        assert isinstance(await client.book_test_drive(drive_request()), o.WriteOutcomeUnknown)

    async def test_a_retry_that_finds_the_slot_taken_is_a_real_refusal(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.post("/api/test-drive-bookings").mock(
            side_effect=[httpx.ReadTimeout("read"), error(409, "SLOT_UNAVAILABLE")]
        )
        assert await client.book_test_drive(drive_request()) == o.SlotTaken(slot_id="td-slot-0001")

    @pytest.mark.parametrize("operation", ["drive", "workshop"])
    async def test_a_500_on_a_booking_is_never_retried_despite_retryable_true(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        sleeps: list[float],
        operation: str,
    ) -> None:
        if operation == "drive":
            route = api.post("/api/test-drive-bookings").mock(internal_error())
            result: o.Outcome = await client.book_test_drive(drive_request())
        else:
            route = api.post("/api/workshop-bookings").mock(internal_error())
            result = await client.book_workshop(workshop())
        assert isinstance(result, o.SlotUnbookable)
        assert route.call_count == 1
        assert sleeps == []

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422])
    async def test_a_4xx_on_a_write_is_never_retried(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        sleeps: list[float],
        status: int,
    ) -> None:
        route = api.post("/api/sales-enquiries").mock(error(status, "ANY"))
        await client.send_enquiry(enquiry())
        assert route.call_count == 1
        assert sleeps == []

    async def test_a_malformed_201_is_not_resent(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.post("/api/workshop-bookings").mock(httpx.Response(201, text="{oops"))
        result = await client.book_workshop(workshop())
        assert result == o.WriteOutcomeUnknown(
            operation="book_workshop",
            reason="malformed_response",
            attempts=1,
            idempotency_key="key-1",
        )
        assert route.call_count == 1

    async def test_a_201_missing_its_reference_is_not_success(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = ws_record()
        del payload["reference"]
        api.post("/api/workshop-bookings").mock(httpx.Response(201, json=payload))
        assert isinstance(await client.book_workshop(workshop()), o.WriteOutcomeUnknown)


class TestAmendHasNoKeySoIsNeverResent:
    def move(self) -> AmendRequest:
        return AmendRequest(
            booking=verified_booking(),
            target_slot=workshop_slot(slot_id="ws-slot-0009", serviceTypeId="full-service"),
        )

    @pytest.mark.parametrize("failure", AMBIGUOUS, ids=label)
    async def test_a_lost_response_is_checked_by_reading_and_applied(
        self, client: DealershipClient, api: respx.MockRouter, failure: Any
    ) -> None:
        patch = api.patch(BOOKING).mock(side_effect=failure)
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record(slotId="ws-slot-0009")))
        result = await client.amend_workshop_booking(self.move())
        assert isinstance(result, o.BookingAmended)
        assert result.reconciled
        assert result.slot_id == "ws-slot-0009"
        assert patch.call_count == 1

    async def test_a_lost_response_with_no_change_is_reported_not_applied(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        patch = api.patch(BOOKING).mock(side_effect=httpx.ReadTimeout("read"))
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record()))
        result = await client.amend_workshop_booking(self.move())
        assert result == o.AmendNotApplied(booking_id="wsb-seeded-001")
        assert patch.call_count == 1

    @pytest.mark.parametrize(
        "patch_fields",
        [{"mileage": 500}, {"notes": "New"}],
        ids=["mileage", "notes"],
    )
    async def test_reconciliation_checks_the_requested_fields(
        self, client: DealershipClient, api: respx.MockRouter, patch_fields: dict[str, Any]
    ) -> None:
        api.patch(BOOKING).mock(side_effect=httpx.ReadTimeout("read"))
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record(**patch_fields)))
        result = await client.amend_workshop_booking(
            AmendRequest(booking=verified_booking(), **patch_fields)
        )
        assert isinstance(result, o.BookingAmended)
        assert result.reconciled

    async def test_if_the_read_also_fails_the_outcome_is_unknown(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.patch(BOOKING).mock(side_effect=httpx.ReadTimeout("read"))
        api.get(BOOKING).mock(side_effect=httpx.ReadTimeout("read"))
        result = await client.amend_workshop_booking(self.move())
        assert isinstance(result, o.WriteOutcomeUnknown)
        assert result.idempotency_key is None

    async def test_a_gateway_failure_is_checked_not_resent(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        patch = api.patch(BOOKING).mock(httpx.Response(502))
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record()))
        result = await client.amend_workshop_booking(self.move())
        assert isinstance(result, o.AmendNotApplied)
        assert patch.call_count == 1

    async def test_a_500_on_a_slot_move_means_that_slot_is_unbookable(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        patch = api.patch(BOOKING).mock(internal_error())
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record()))
        result = await client.amend_workshop_booking(self.move())
        assert result == o.SlotUnbookable(slot_id="ws-slot-0009")
        assert patch.call_count == 1

    async def test_a_failed_connection_is_safe_to_retry(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        patch = api.patch(BOOKING).mock(
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.Response(200, json=workshop_record(slotId="ws-slot-0009")),
            ]
        )
        result = await client.amend_workshop_booking(self.move())
        assert isinstance(result, o.BookingAmended)
        assert not result.reconciled
        assert patch.call_count == 2

    async def test_a_write_that_never_connected_is_known_not_sent(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        get = api.get(BOOKING)
        api.patch(BOOKING).mock(side_effect=httpx.ConnectError("refused"))
        result = await client.amend_workshop_booking(self.move())
        assert isinstance(result, o.WriteNotSent)
        assert not get.called


class TestCancelHasNoKeySoIsNeverResent:
    @pytest.mark.parametrize("failure", AMBIGUOUS, ids=label)
    async def test_a_lost_response_is_checked_by_reading(
        self, client: DealershipClient, api: respx.MockRouter, failure: Any
    ) -> None:
        api.get(BOOKING).mock(
            side_effect=[
                httpx.Response(200, json=workshop_record()),
                httpx.Response(200, json=workshop_record("cancelled")),
            ]
        )
        delete = api.delete(BOOKING).mock(side_effect=failure)
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert isinstance(result, o.BookingCancelled)
        assert result.reconciled
        assert not result.already_cancelled
        assert delete.call_count == 1

    async def test_a_booking_still_live_is_reported_not_applied(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(httpx.Response(200, json=workshop_record()))
        delete = api.delete(BOOKING).mock(httpx.Response(503))
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert result == o.CancelNotApplied(booking_id="wsb-seeded-001")
        assert delete.call_count == 1

    async def test_if_the_check_also_fails_the_outcome_is_unknown(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(
            side_effect=[
                httpx.Response(200, json=workshop_record()),
                httpx.ReadTimeout("read"),
                httpx.ReadTimeout("read"),
                httpx.ReadTimeout("read"),
            ]
        )
        api.delete(BOOKING).mock(side_effect=httpx.ReadTimeout("read"))
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert isinstance(result, o.WriteOutcomeUnknown)

    async def test_a_malformed_200_is_checked_by_reading(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get(BOOKING).mock(
            side_effect=[
                httpx.Response(200, json=workshop_record()),
                httpx.Response(200, json=workshop_record("cancelled")),
            ]
        )
        api.delete(BOOKING).mock(httpx.Response(200, text="oops"))
        result = await client.cancel_workshop_booking("wsb-seeded-001")
        assert isinstance(result, o.BookingCancelled)
        assert result.reconciled


class TestSearchUsesRetries:
    async def test_a_stock_page_is_retried_on_its_own(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        answers = iter(
            [httpx.ReadTimeout("read"), httpx.Response(200, json=page([vehicle()]))]
        )

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.params["availability"] == "available":
                item = next(answers)
                if isinstance(item, Exception):
                    raise item
                return item
            return httpx.Response(200, json=page([]))

        api.get("/api/vehicles").mock(side_effect=respond)
        result = await client.search_stock(VehicleSearch())
        assert isinstance(result, o.StockSearched)
        assert len(result.available) == 1

