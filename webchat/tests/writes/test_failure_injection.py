"""The five failure scenarios, each against the real platform. Every test checks three things: what
the customer sees, what the event log holds, and what really exists on the platform."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.persistence.store import Store
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.workflows.types import (
    CONFIRMATION_TTL,
    VERIFICATION_IDLE_LIMIT,
    ConfirmAction,
    Phase,
    Source,
    WorkflowKind,
)
from northstar_webchat.writes.service import RECOVERY_WINDOW, WriteService

from ..workflows.conftest import submitted
from .conftest import (
    CONTACT,
    TAYLOR,
    Faults,
    build_service,
    counts,
    first_workshop_slot,
    ready_callback,
    ready_cancel,
    ready_test_drive,
    token_of,
    writes_sent,
)

BOOKINGS = "/api/test-drive-bookings"
WORKSHOP = "/api/workshop-bookings"
CALLBACKS = "/api/callback-requests"
INTERNAL_ERROR = {
    "error": {
        "code": "INTERNAL_ERROR",
        "message": "Failed.",
        "fieldErrors": {},
        "retryable": True,
    }
}


class TestTimeoutAfterThePlatformAccepted:
    async def test_a_lost_response_reconciles_and_books_once(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "completed"
        assert result.reference is not None and result.reference.startswith("TEST-")
        assert "confirmed" in result.message.lower()
        assert counts(clean_platform)["testDriveBookings"] == 1
        assert writes_sent(faults, BOOKINGS) == 2
        trail = service.trail("s1")
        assert trail[-4:] == [
            "ConfirmAction",
            "PreconditionsPassed",
            "write_outcome:TestDriveBooked",
            "WriteCompleted",
        ]
        assert "write_intent" in service.store.kinds("s1")

    async def test_the_platform_replay_is_recorded_as_a_replay(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS)
        await service.confirm("s1", token_of(view))
        outcome = service.store.last_outcome("s1")
        assert outcome is not None
        assert outcome["replayed"] is True

    async def test_every_retry_lost_stays_unknown_then_reconciles(
        self,
        store: Store,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS, times=3)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1
        assert "reconcile" in service.store.kinds("s1")
        assert store.phase("s1") is Phase.COMPLETED

    async def test_a_long_outage_tells_the_customer_not_to_book_again(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS, times=99)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "unknown"
        assert result.can_check_again
        assert "don't book again" in result.message.lower()
        assert "confirmed" not in result.message.lower()
        assert counts(clean_platform)["testDriveBookings"] == 1

        faults.rules.clear()
        settled = await service.check_again("s1")
        assert settled.status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_every_attempt_used_the_same_key_and_body(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS, times=3)
        await service.confirm("s1", token_of(view))
        sent = service.store.write_requests("s1")
        assert len(sent) == 1

    async def test_a_cancel_whose_response_is_lost_is_read_back_not_resent(
        self,
        service: WriteService,
        faults: Faults,
    ) -> None:
        view = await ready_cancel(service)
        faults.lose_response("DELETE", WORKSHOP)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "completed"
        assert writes_sent(faults, WORKSHOP) == 1
        assert "WriteCompleted" in service.trail("s1")


class TestDoubleClickAndRefresh:
    async def test_five_simultaneous_clicks_make_one_booking(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        token = token_of(view)
        results = await asyncio.gather(*(service.confirm("s1", token) for _ in range(5)))

        assert counts(clean_platform)["testDriveBookings"] == 1
        assert writes_sent(faults, BOOKINGS) == 1
        assert {item.status for item in results} <= {"processing", "completed"}
        assert results[0].status == "completed"
        trail = service.trail("s1")
        assert trail.count("ConfirmAction") == 1
        assert trail.count("ConfirmAction:rejected") == 4

    async def test_a_second_click_during_the_write_shows_progress(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)
        arrived, release = faults.hold("POST", BOOKINGS)
        first = asyncio.create_task(service.confirm("s1", token_of(view)))
        await arrived.wait()
        second = await service.confirm("s1", token_of(view))
        assert second.status == "processing"
        assert second.block is None
        release.set()
        assert (await first).status == "completed"

    async def test_a_refresh_mid_request_shows_progress_then_the_result(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)
        arrived, release = faults.hold("POST", BOOKINGS)
        first = asyncio.create_task(service.confirm("s1", token_of(view)))
        await arrived.wait()
        refreshed = await service.view("s1")
        assert refreshed.status == "processing"
        assert "booking" in refreshed.message.lower()
        release.set()
        await first
        final = await service.view("s1")
        assert final.status == "completed"
        assert final.reference is not None

    async def test_a_dropped_connection_does_not_stop_the_write(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        arrived, release = faults.hold("POST", BOOKINGS)
        request = asyncio.create_task(service.confirm("s1", token_of(view)))
        await arrived.wait()
        request.cancel()  # What a closed browser tab does to the waiting request.
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await service.finish_background_work()
        assert (await service.view("s1")).status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_the_block_is_gone_after_a_refresh_once_used(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        view = await ready_test_drive(service, client)
        await service.confirm("s1", token_of(view))
        assert (await service.view("s1")).block is None

    async def test_refreshing_before_clicking_shows_the_same_block(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        view = await ready_test_drive(service, client)
        refreshed = await service.view("s1")
        assert refreshed.block is not None
        assert refreshed.block.token == token_of(view)


class TestTokenReplayAndExpiry:
    async def test_replaying_a_used_token_changes_nothing(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        await service.confirm("s1", token_of(view))
        replay = await service.confirm("s1", token_of(view))

        assert replay.status == "completed"
        assert replay.reference is not None
        assert writes_sent(faults, BOOKINGS) == 1
        assert counts(clean_platform)["testDriveBookings"] == 1
        assert service.trail("s1")[-1] == "ConfirmAction:rejected"

    async def test_an_expired_token_issues_a_new_block_and_writes_nothing(
        self, store: Store, client: DealershipClient, faults: Faults, clean_platform: str
    ) -> None:
        clock = Clock()
        service = WriteService(
            store, client, pepper=b"test-pepper", reconcile_delay=0.0, now=clock.now
        )
        view = await ready_test_drive(service, client)
        clock.advance(CONFIRMATION_TTL.total_seconds() + 1)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "confirming"
        assert token_of(result) != token_of(view)
        assert "expired" in result.message.lower()
        assert writes_sent(faults, BOOKINGS) == 0
        assert counts(clean_platform)["testDriveBookings"] == 0

        done = await service.confirm("s1", token_of(result))
        assert done.status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_the_expired_token_itself_stays_dead(
        self, store: Store, client: DealershipClient, faults: Faults
    ) -> None:
        clock = Clock()
        service = WriteService(
            store, client, pepper=b"test-pepper", reconcile_delay=0.0, now=clock.now
        )
        view = await ready_test_drive(service, client)
        clock.advance(CONFIRMATION_TTL.total_seconds() + 1)
        await service.confirm("s1", token_of(view))
        await service.confirm("s1", token_of(view))
        assert writes_sent(faults, BOOKINGS) == 0

    async def test_a_token_from_another_session_does_nothing(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client, "owner")
        await ready_callback(service, "attacker")
        result = await service.confirm("attacker", token_of(view))
        assert result.status == "confirming"
        assert writes_sent(faults, BOOKINGS) == 0

    async def test_a_forged_token_does_nothing(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        await ready_test_drive(service, client)
        result = await service.confirm("s1", "forged")
        assert result.status == "confirming"
        assert writes_sent(faults, BOOKINGS) == 0

    async def test_a_correction_kills_the_old_token(
        self, service: WriteService, faults: Faults
    ) -> None:
        view = await ready_callback(service)
        changed = await service.fill("s1", "reason", "A different reason entirely")
        assert token_of(changed) != token_of(view)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "confirming"
        assert writes_sent(faults, CALLBACKS) == 0

    async def test_no_token_reaches_the_event_log(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        view = await ready_test_drive(service, client)
        token = token_of(view)
        await service.confirm("s1", token)
        assert token not in json.dumps(service.store.dump("s1"))


class TestSuccessStatusButNotBusinessSuccess:
    @staticmethod
    def with_status(status: str):  # type: ignore[no-untyped-def]
        def rewrite(response: httpx.Response) -> httpx.Response:
            body = response.json()
            body["status"] = status
            return httpx.Response(response.status_code, json=body, request=response.request)

        return rewrite

    async def test_a_pending_booking_is_never_called_confirmed(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.rewrite("POST", BOOKINGS, self.with_status("pending"), times=99)
        result = await service.confirm("s1", token_of(view))

        assert result.status == "unknown"
        assert "confirmed" not in result.message.lower()
        assert service.store.last_outcome("s1")["kind"] == "write_outcome_unknown"  # type: ignore[index]
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_a_garbled_body_is_not_success_and_reconciles(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)

        def garble(response: httpx.Response) -> httpx.Response:
            return httpx.Response(201, text="{oops", request=response.request)

        faults.rewrite("POST", BOOKINGS, garble)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "completed"
        assert result.reference is not None
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_a_replay_of_a_cancelled_booking_is_not_a_booking(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        slot = await first_workshop_slot(client)
        values = {
            "dealership_id": "northstar-manchester",
            "service_type_id": "mot",
            "slot_id": slot.id,
            "registration": "AB12 CDE",
            "mileage": 1000,
            **CONTACT,
        }
        await service.start("s1", WorkflowKind.WORKSHOP_CREATE)
        view = None
        for name, value in values.items():
            view = await service.fill("s1", name, value)
        assert view is not None
        faults.rewrite("POST", WORKSHOP, self.with_cancelled)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "failed"
        assert "not booked" in result.message.lower()

    @staticmethod
    def with_cancelled(response: httpx.Response) -> httpx.Response:
        body = response.json()
        body["status"] = "cancelled"
        body["cancelledAt"] = "2026-11-01T12:00:00Z"
        return httpx.Response(response.status_code, json=body, request=response.request)

    async def test_saved_records_never_sound_like_promises(self, service: WriteService) -> None:
        view = await ready_callback(service)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "completed"
        assert "time" in result.message.lower() and "can't" in result.message.lower()
        assert "confirmed" not in result.message.lower()

    async def test_a_car_reserved_since_the_card_was_shown_writes_nothing(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)

        def reserved(response: httpx.Response) -> httpx.Response:
            body = response.json()
            body.update(availability="reserved", canBookTestDrive=False, canRegisterInterest=True)
            return httpx.Response(200, json=body, request=response.request)

        faults.rewrite("GET", "/availability", reserved)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "failed"
        assert "reserved" in result.message.lower()
        assert writes_sent(faults, BOOKINGS) == 0

    async def test_a_platform_refusal_is_shown_as_a_refusal(
        self, service: WriteService, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)

        def taken(response: httpx.Response) -> httpx.Response:
            body = {
                "error": {
                    "code": "SLOT_UNAVAILABLE",
                    "message": "Taken.",
                    "fieldErrors": {},
                    "retryable": False,
                }
            }
            return httpx.Response(409, json=body, request=response.request)

        # The platform really books here, then we pretend it refused: only the refusal path matters.
        faults.rewrite("POST", BOOKINGS, taken)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "collecting"
        assert "taken" in result.message.lower()
        assert result.block is None


def run_worker(platform: str, db_url: str, scenario: str, when: str) -> int:
    webchat = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, "-m", "tests.writes.crash_worker", platform, db_url, scenario, when],
        cwd=webchat,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 137, done.stderr[-2000:]
    return done.returncode


class TestServerRestart:
    async def test_dying_after_the_platform_accepted_still_books_once(
        self, store: Store, db_url: str, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        run_worker(clean_platform, db_url, "test_drive", "after")
        assert counts(clean_platform)["testDriveBookings"] == 1
        assert store.phase("s1") is Phase.EXECUTING

        restarted = build_service(Store(db_url), client)
        assert await restarted.recover() == 1

        view = await restarted.view("s1")
        assert view.status == "completed"
        assert view.reference is not None
        assert counts(clean_platform)["testDriveBookings"] == 1
        outcome = restarted.store.last_outcome("s1")
        assert outcome is not None and outcome["replayed"] is True
        assert "WriteInterrupted" in restarted.trail("s1")

    async def test_dying_before_the_request_left_books_once(
        self, store: Store, db_url: str, client: DealershipClient, clean_platform: str
    ) -> None:
        run_worker(clean_platform, db_url, "test_drive", "before")
        assert counts(clean_platform)["testDriveBookings"] == 0
        restarted = build_service(Store(db_url), client)
        await restarted.recover()
        assert (await restarted.view("s1")).status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_dying_after_a_cancel_landed_is_read_back(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        run_worker(clean_platform, db_url, "cancel", "after")
        restarted = build_service(Store(db_url), client)
        await restarted.recover()
        assert (await restarted.view("s1")).status == "completed"
        assert writes_sent(faults, WORKSHOP) == 0

    async def test_dying_before_a_cancel_left_is_reported_not_applied(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        run_worker(clean_platform, db_url, "cancel", "before")
        restarted = build_service(Store(db_url), client)
        await restarted.recover()
        view = await restarted.view("s1")
        assert view.status == "failed"
        assert "not" in view.message.lower() and "cancelled" in view.message.lower()
        assert writes_sent(faults, WORKSHOP) == 0

    async def test_a_recovered_cancel_still_marks_its_slot_unbookable(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str
    ) -> None:
        run_worker(clean_platform, db_url, "cancel", "after")
        restarted = build_service(Store(db_url), client)
        await restarted.recover()
        assert store.is_unbookable("ws-slot-0001", Clock().now())

    async def test_a_stale_write_is_not_sent_by_recovery(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        """Nobody should get a booking they confirmed long ago and may have given up on."""
        run_worker(clean_platform, db_url, "test_drive", "before")
        clock = Clock()
        clock.advance(RECOVERY_WINDOW.total_seconds() + 60)
        restarted = WriteService(
            Store(db_url), client, pepper=b"test-pepper", reconcile_delay=0.0, now=clock.now
        )
        await restarted.recover()
        assert writes_sent(faults, BOOKINGS) == 0
        view = await restarted.view("s1")
        assert view.status == "unknown"
        assert view.can_check_again

    async def test_a_stale_confirmation_that_never_wrote_is_reported(
        self, store: Store, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_test_drive(service, client)
        store.apply("s1", Source.CUSTOMER, ConfirmAction(token=token_of(view)))
        clock = Clock()
        clock.advance(RECOVERY_WINDOW.total_seconds() + 60)
        restarted = WriteService(
            store, client, pepper=b"test-pepper", reconcile_delay=0.0, now=clock.now
        )
        await restarted.recover()
        result = await restarted.view("s1")
        assert result.status == "failed"
        assert "nothing was booked" in result.message.lower()
        assert writes_sent(faults, BOOKINGS) == 0

    async def test_recovery_is_safe_to_run_twice(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str
    ) -> None:
        run_worker(clean_platform, db_url, "test_drive", "after")
        restarted = build_service(Store(db_url), client)
        assert await restarted.recover() == 1
        assert await restarted.recover() == 0
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_recovery_leaves_idle_and_finished_sessions_alone(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        await ready_test_drive(service, client, "waiting")
        view = await ready_callback(service, "done")
        await service.confirm("done", token_of(view))
        assert await service.recover() == 0
        assert (await service.view("waiting")).status == "confirming"

    async def test_a_lost_lookup_returns_to_verification(
        self, store: Store, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        store.apply("s1", Source.CUSTOMER, submitted())  # The lookup itself never runs.
        assert store.phase("s1") is Phase.CHECKING_IDENTITY
        await service.recover()
        assert (await service.view("s1")).status == "verifying"

    async def test_recovery_during_a_platform_outage_stays_unknown(
        self, db_url: str, store: Store, client: DealershipClient, clean_platform: str, faults: Faults
    ) -> None:
        run_worker(clean_platform, db_url, "test_drive", "after")
        faults.never_arrive("POST", BOOKINGS, times=99)
        restarted = build_service(Store(db_url), client)
        await restarted.recover()
        view = await restarted.view("s1")
        assert view.status == "unknown"
        assert view.can_check_again
        faults.rules.clear()
        assert (await restarted.check_again("s1")).status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1


class TestVerificationAcrossTime:
    async def test_expired_verification_asks_again_instead_of_trapping_the_customer(
        self, store: Store, client: DealershipClient, faults: Faults
    ) -> None:
        clock = Clock()
        service = WriteService(
            store, client, pepper=b"test-pepper", reconcile_delay=0.0, now=clock.now
        )
        view = await ready_cancel(service)
        clock.advance(VERIFICATION_IDLE_LIMIT.total_seconds() + 1)
        result = await service.confirm("s1", token_of(view))
        assert result.status == "verifying"
        assert result.block is None
        assert writes_sent(faults, WORKSHOP) == 0
        again = await service.submit_identity("s1", **TAYLOR)
        assert again.status == "confirming"

    async def test_a_second_retrieval_shows_the_current_status(
        self, service: WriteService
    ) -> None:
        view = await ready_cancel(service)
        await service.confirm("s1", token_of(view))
        again = await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        assert again.status == "completed"
        assert again.reference == "WORK-10001"
        assert "cancelled" in again.message.lower()


class TestKnownGaps:
    """Each test states what should be true, and fails today against the real platform. They are
    strict: if one starts passing, the suite fails, so this list cannot quietly go stale."""

    @pytest.mark.xfail(strict=True, reason="a 500 cannot be told apart from the released-slot bug")
    async def test_a_500_after_the_platform_accepted_must_not_say_the_slot_was_taken(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)

        def server_error(response: httpx.Response) -> httpx.Response:
            return httpx.Response(500, json=INTERNAL_ERROR, request=response.request)

        faults.rewrite("POST", BOOKINGS, server_error)
        result = await service.confirm("s1", token_of(view))
        assert counts(clean_platform)["testDriveBookings"] == 1
        assert result.status != "collecting"

    @pytest.mark.xfail(strict=True, reason="moving on from an unknown write allows a second one")
    async def test_moving_on_from_an_unknown_write_must_not_double_book(
        self,
        service: WriteService,
        client: DealershipClient,
        faults: Faults,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client)
        faults.lose_response("POST", BOOKINGS, times=9)
        await service.confirm("s1", token_of(view))
        assert (await service.view("s1")).status == "unknown"
        faults.rules.clear()

        found = await client.find_test_drive_slots("veh-001")
        assert isinstance(found, o.TestDriveSlotsFound)
        await service.start("s1", WorkflowKind.TEST_DRIVE)
        await service.fill("s1", "vehicle_id", "veh-001")
        await service.fill("s1", "slot_id", found.slots[0].id)
        second = None
        for name, value in CONTACT.items():
            second = await service.fill("s1", name, value)
        assert second is not None
        if second.block is not None:
            await service.confirm("s1", second.block.token)
        assert counts(clean_platform)["testDriveBookings"] == 1

    @pytest.mark.xfail(strict=True, reason="a read-back can run before a slow request lands")
    async def test_a_slow_cancel_must_not_be_reported_as_not_cancelled(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        view = await ready_cancel(service)
        land = faults.arrive_late("DELETE", WORKSHOP)
        result = await service.confirm("s1", token_of(view))
        land.set()
        await asyncio.gather(*faults.pending)
        found = await client.get_workshop_booking("wsb-seeded-001")
        assert isinstance(found, o.WorkshopBookingFound)
        assert found.booking.is_cancelled
        assert result.status != "failed"


class Clock:
    def __init__(self) -> None:
        from datetime import datetime, timezone

        self.moment = datetime.now(timezone.utc)

    def now(self):  # type: ignore[no-untyped-def]
        return self.moment

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.moment += timedelta(seconds=seconds)

