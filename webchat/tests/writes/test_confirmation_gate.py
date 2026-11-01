"""The model proposes, the server validates, and only the customer's click writes."""

from __future__ import annotations

import inspect
import json
import threading
from typing import Any

import httpx
import pytest
from sqlalchemy import inspect as sql_inspect

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.persistence.store import Store
from northstar_webchat.persistence.tables import metadata
from northstar_webchat.workflows.types import (
    ConfirmAction,
    Phase,
    Source,
    StartWorkflow,
    WorkflowKind,
)
from northstar_webchat.writes.api import COOKIE, create_app
from northstar_webchat.writes.service import Proposal, WriteService

from .conftest import (
    CONTACT,
    Faults,
    counts,
    first_drive_slot,
    ready_callback,
    ready_cancel,
    ready_test_drive,
    token_of,
    writes_sent,
)


class TestTheModelCanOnlyPropose:
    async def test_proposing_every_slot_cannot_write(
        self, service: WriteService, faults: Faults, clean_platform: str
    ) -> None:
        await service.start("s1", WorkflowKind.CALLBACK)
        values: dict[str, Any] = {
            "dealership_id": "northstar-manchester",
            "department": "sales",
            "reason": "Discuss finance options",
            **CONTACT,
        }
        text = " ".join(str(value) for value in values.values())
        view = await service.propose(
            "s1", [Proposal(name=k, value=v) for k, v in values.items()], text
        )
        assert view.status == "confirming"
        assert writes_sent(faults, "/api/") == 0
        assert counts(clean_platform)["callbackRequests"] == 0

    async def test_an_inferred_value_gets_no_block(self, service: WriteService) -> None:
        await service.start("s1", WorkflowKind.VEHICLE_INTEREST)
        for name, value in CONTACT.items():
            await service.fill("s1", name, value)
        view = await service.propose(
            "s1", [Proposal(name="vehicle_id", value="veh-007")], "that one you mentioned"
        )
        assert view.status == "collecting"
        assert view.block is None
        assert any("vehicle_id" in prompt for prompt in view.prompts)

    def test_propose_has_no_way_to_carry_a_token(self) -> None:
        parameters = inspect.signature(WriteService.propose).parameters
        assert "token" not in parameters

    def test_the_service_only_lets_a_customer_confirm(self) -> None:
        source = inspect.getsource(WriteService.confirm)
        assert "Source.CUSTOMER" in source
        assert "Source.LLM" not in source

    async def test_the_model_never_sees_a_token_or_booking_id(
        self, service: WriteService
    ) -> None:
        view = await ready_cancel(service)
        context = json.dumps(service.llm_context("s1"))
        assert token_of(view) not in context
        assert "wsb-seeded-001" not in context

    async def test_the_model_sees_no_verification_details(self, service: WriteService) -> None:
        await ready_cancel(service)
        context = json.dumps(service.llm_context("s1"))
        assert "Taylor" not in context
        assert "07700900123" not in context


class TestServerValidation:
    async def test_the_block_shows_facts_the_server_fetched(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        view = await ready_test_drive(service, client)
        assert view.block is not None
        rows = {row.label: row.value for row in view.block.rows}
        assert rows["Car"].startswith("BMW 1 Series")
        assert rows["Where"] == "Northstar Manchester"
        assert "2026" in rows["When"] or "2027" in rows["When"]

    async def test_a_slot_that_does_not_exist_gets_no_block(self, service: WriteService) -> None:
        await service.start("s1", WorkflowKind.TEST_DRIVE)
        await service.fill("s1", "vehicle_id", "veh-001")
        await service.fill("s1", "slot_id", "td-slot-9999")
        view = None
        for name, value in CONTACT.items():
            view = await service.fill("s1", name, value)
        assert view is not None
        assert view.block is None
        assert "no longer available" in view.message.lower()

    async def test_a_reserved_car_gets_no_block(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        slots = await first_drive_slot(client, "veh-001")
        await service.start("s1", WorkflowKind.TEST_DRIVE)
        await service.fill("s1", "vehicle_id", "veh-007")
        await service.fill("s1", "slot_id", slots.id)
        view = None
        for name, value in CONTACT.items():
            view = await service.fill("s1", name, value)
        assert view is not None
        assert view.block is None
        assert "reserved" in view.message.lower()

    async def test_a_slot_outside_department_hours_gets_no_block(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        slot = await first_drive_slot(client)

        def closed(response: httpx.Response) -> httpx.Response:
            body = response.json()
            for row in body["weekly"]:
                row.update(opensAt=None, closesAt=None, closed=True)
            return httpx.Response(200, json=body, request=response.request)

        faults.rewrite("GET", "/opening-hours", closed, times=99)
        await service.start("s1", WorkflowKind.TEST_DRIVE)
        await service.fill("s1", "vehicle_id", "veh-001")
        await service.fill("s1", "slot_id", slot.id)
        view = None
        for name, value in CONTACT.items():
            view = await service.fill("s1", name, value)
        assert view is not None
        assert view.block is None
        assert "closed" in view.message.lower()

    async def test_hours_that_change_after_the_block_are_seen_by_the_write(
        self, service: WriteService, client: DealershipClient, faults: Faults
    ) -> None:
        """A cached copy must never decide a write (W3, D-024)."""
        view = await ready_test_drive(service, client)

        def closed(response: httpx.Response) -> httpx.Response:
            body = response.json()
            for row in body["weekly"]:
                row.update(opensAt=None, closesAt=None, closed=True)
            return httpx.Response(200, json=body, request=response.request)

        faults.rewrite("GET", "/opening-hours", closed, times=99)
        result = await service.confirm("s1", token_of(view))
        assert result.status != "completed"
        assert writes_sent(faults, "/api/test-drive-bookings") == 0

    async def test_a_known_unbookable_slot_gets_no_block(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        cancel = await ready_cancel(service)
        await service.confirm("s1", token_of(cancel))
        await service.start("s2", WorkflowKind.WORKSHOP_CREATE)
        values = {
            "dealership_id": "northstar-manchester",
            "service_type_id": "full-service",
            "slot_id": "ws-slot-0001",
            "registration": "AB12 CDE",
            "mileage": 1000,
            **CONTACT,
        }
        view = None
        for name, value in values.items():
            view = await service.fill("s2", name, value)
        assert view is not None
        assert view.block is None
        assert "no longer available" in view.message.lower()

    async def test_the_cancel_block_carries_the_warning(self, service: WriteService) -> None:
        view = await ready_cancel(service)
        assert view.block is not None
        assert any("may not be available" in item for item in view.block.warnings)

    async def test_a_service_change_is_refused_for_now(
        self, service: WriteService, client: DealershipClient
    ) -> None:
        await service.start("s1", WorkflowKind.WORKSHOP_AMEND)
        from .conftest import TAYLOR

        await service.submit_identity("s1", **TAYLOR)
        found = await client.find_workshop_slots(
            dealership_id="northstar-liverpool", service_type_id="diagnostic"
        )
        slot = found.slots[0]  # type: ignore[union-attr]
        view = await service.fill("s1", "target_slot_id", slot.id)
        assert view.block is None
        assert "different service" in view.message.lower()


class TestVerificationThroughTheService:
    async def test_identity_never_enters_storage(self, service: WriteService) -> None:
        await ready_cancel(service)
        dumped = json.dumps(service.store.dump("s1"))
        assert "07700900123" not in dumped
        assert "Taylor" not in dumped

    async def test_five_failures_lock_and_the_ledger_spans_sessions(
        self, service: WriteService
    ) -> None:
        wrong = {
            "reference": "WORK-10001",
            "last_name": "Wrong",
            "registration": "AB12 CDE",
            "phone": "07700900123",
        }
        for number in range(5):
            session = f"attacker-{number}"
            await service.start(session, WorkflowKind.BOOKING_RETRIEVAL)
            await service.submit_identity(session, **wrong)
        await service.start("fresh", WorkflowKind.BOOKING_RETRIEVAL)
        view = await service.submit_identity("fresh", **{**wrong, "last_name": "Taylor"})
        assert view.status == "locked"

    async def test_a_format_error_is_not_counted(self, service: WriteService) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        view = await service.submit_identity(
            "s1", reference="WORK-10001", last_name="Taylor", registration="AB12 CDE", phone="123"
        )
        assert view.status == "verifying"
        assert any("phone" in prompt for prompt in view.prompts)
        assert service.store.reference_failures_total() == 0

    async def test_a_mismatch_reveals_nothing(self, service: WriteService) -> None:
        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        view = await service.submit_identity(
            "s1", reference="WORK-10001", last_name="Wrong", registration="AB12 CDE", phone="07700900123"
        )
        assert view.status == "verifying"
        assert "surname" not in view.message.lower()
        assert "couldn't match" in view.message.lower()

    async def test_a_retrieved_booking_shows_appointment_details(
        self, service: WriteService
    ) -> None:
        from .conftest import TAYLOR

        await service.start("s1", WorkflowKind.BOOKING_RETRIEVAL)
        view = await service.submit_identity("s1", **TAYLOR)
        assert view.status == "completed"
        assert "Full service" in view.message
        assert view.reference == "WORK-10001"


class TestStore:
    def test_migrations_match_the_tables(self, store: Store) -> None:
        tables = set(sql_inspect(store.engine).get_table_names()) - {"alembic_version"}
        assert tables == set(metadata.tables)

    def test_migrating_twice_is_harmless(self, store: Store) -> None:
        store.migrate()

    def test_a_token_can_be_claimed_only_once_across_threads(
        self, store: Store
    ) -> None:
        """Eight threads with their own connections race for one token."""
        session_id = "race"
        store.apply(session_id, Source.CUSTOMER, StartWorkflow(kind=WorkflowKind.CALLBACK))
        from northstar_webchat.workflows.types import SlotProposed

        values = {
            "dealership_id": "d",
            "department": "sales",
            "reason": "Reason text",
            **CONTACT,
        }
        for name, value in values.items():
            store.apply(session_id, Source.CUSTOMER, SlotProposed(name=name, value=value))
        active = store.load(session_id).active
        assert active is not None and active.pending is not None
        token = active.pending.token

        accepted: list[bool] = []
        barrier = threading.Barrier(8)

        def race() -> None:
            barrier.wait()
            result = Store(store.url).apply(session_id, Source.CUSTOMER, ConfirmAction(token=token))
            accepted.append(result.accepted)

        threads = [threading.Thread(target=race) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert accepted.count(True) == 1
        assert store.phase(session_id) is Phase.RECHECKING

    def test_a_rejected_event_is_still_logged(self, store: Store) -> None:
        store.apply("s1", Source.LLM, StartWorkflow(kind=WorkflowKind.CALLBACK))
        assert store.kinds("s1") == ["StartWorkflow:rejected"]

    def test_the_event_log_is_append_only(self, store: Store) -> None:
        store.apply("s1", Source.CUSTOMER, StartWorkflow(kind=WorkflowKind.CALLBACK))
        store.apply("s1", Source.CUSTOMER, StartWorkflow(kind=WorkflowKind.MESSAGE))
        sequence = [row["seq"] for row in store.dump("s1")]
        assert sequence == sorted(sequence)
        assert len(set(sequence)) == len(sequence)


@pytest.fixture
async def http(service: WriteService):  # type: ignore[no-untyped-def]
    app = create_app(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
    ) as browser:
        yield browser


class TestHttp:
    async def test_the_click_needs_the_session_cookie(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/confirmations", json={"token": "x"})
        assert response.status_code == 401

    async def test_a_session_cookie_is_http_only(self, http: httpx.AsyncClient) -> None:
        response = await http.post("/api/session")
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    async def test_a_click_over_http_writes_once(
        self,
        http: httpx.AsyncClient,
        service: WriteService,
        client: DealershipClient,
        clean_platform: str,
    ) -> None:
        view = await ready_test_drive(service, client, "browser")
        http.cookies.set(COOKIE, "browser")
        first = await http.post("/api/confirmations", json={"token": token_of(view)})
        second = await http.post("/api/confirmations", json={"token": token_of(view)})
        assert first.status_code == second.status_code == 200
        assert first.json()["status"] == "completed"
        assert second.json()["reference"] == first.json()["reference"]
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_refreshing_reads_state_without_a_token(
        self, http: httpx.AsyncClient, service: WriteService
    ) -> None:
        await ready_callback(service, "browser")
        http.cookies.set(COOKIE, "browser")
        response = await http.get("/api/conversation/state")
        assert response.json()["status"] == "confirming"

    async def test_starting_the_app_recovers_interrupted_writes(
        self, service: WriteService, client: DealershipClient, clean_platform: str
    ) -> None:
        view = await ready_test_drive(service, client, "browser")
        service.store.apply("browser", Source.CUSTOMER, ConfirmAction(token=token_of(view)))
        assert service.store.phase("browser") is Phase.RECHECKING
        app = create_app(service)
        async with app.router.lifespan_context(app):
            pass
        assert service.store.phase("browser") is Phase.COMPLETED
        assert counts(clean_platform)["testDriveBookings"] == 1

    async def test_check_again_needs_the_session_cookie(self, http: httpx.AsyncClient) -> None:
        assert (await http.post("/api/confirmations/check-again")).status_code == 401
