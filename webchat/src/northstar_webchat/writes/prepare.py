"""Server-side validation. Turns workflow slots into an exact client request, using live data.

Runs twice: to render the confirmation block, and again just before the write (W3). Only values
the customer can stand behind reach this module, because it reads them through `usable_values`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from pydantic import BaseModel

from ..dealership import outcomes as o
from ..dealership.client import DealershipClient
from ..dealership.models import (
    AmendRequest,
    CallbackRequest,
    Contact,
    DealershipMessageRequest,
    Department,
    PartExchangeRequest,
    SalesEnquiryRequest,
    Slot,
    TestDriveRequest,
    VehicleInterestRequest,
    Wire,
    WorkshopBookingRequest,
    WorkshopSlot,
)
from ..persistence.repository import Repository
from ..workflows.types import SlotScalar, WorkflowKind
from .present import Row, format_when, pretty


class CancelRequest(Wire):
    booking_id: str


class Prepared(BaseModel):
    request: str
    title: str
    rows: list[Row]
    warnings: list[str] = []


class Refused(BaseModel):
    """`clear` names slots the customer can change. If empty, the workflow cannot continue."""

    reason: str
    clear: tuple[str, ...] = ()


Values = Mapping[str, SlotScalar]
UNAVAILABLE = Refused(reason="platform_unavailable")


def _text(values: Values, name: str) -> str:
    return str(values[name])


def _optional(values: Values, name: str) -> str | None:
    return str(values[name]) if name in values else None


def _contact(values: Values) -> Contact:
    return Contact(
        first_name=_text(values, "first_name"),
        last_name=_text(values, "last_name"),
        email=_text(values, "email"),
        phone=_text(values, "phone"),
    )


def _optional_rows(values: Values, **labels: str) -> list[Row]:
    """Rows for optional slots the customer filled in, so nothing saved is hidden."""
    return [Row(label=label, value=str(values[name])) for name, label in labels.items() if name in values]


def _contact_rows(values: Values) -> list[Row]:
    contact = _contact(values)
    return [
        Row(label="Name", value=f"{contact.first_name} {contact.last_name}"),
        Row(label="Email", value=contact.email),
        Row(label="Phone", value=contact.phone),
    ]


class Preparer:
    def __init__(
        self, client: DealershipClient, store: Repository, now: Callable[[], datetime]
    ) -> None:
        self._client = client
        self._store = store
        self._now = now

    async def prepare(
        self, kind: WorkflowKind, values: Values, booking_id: str | None, key: str
    ) -> Prepared | Refused:
        if kind is WorkflowKind.TEST_DRIVE:
            return await self._test_drive(values, key)
        if kind is WorkflowKind.WORKSHOP_CREATE:
            return await self._workshop(values, key)
        if kind is WorkflowKind.WORKSHOP_AMEND:
            assert booking_id is not None
            return await self._amend(values, booking_id)
        if kind is WorkflowKind.WORKSHOP_CANCEL:
            assert booking_id is not None
            return await self._cancel(booking_id)
        return await self._record(kind, values, key)

    async def _open_at(self, slot: Slot, department: Department, clear: str) -> Refused | None:
        """D-010: a slot starting outside the department's hours for that date is refused."""
        found = await self._client.get_opening_hours(slot.dealership_id, fresh=True)
        if not isinstance(found, o.OpeningHoursFound):
            return UNAVAILABLE
        local = slot.starts_at_local
        hours = found.hours.on(local.date(), department)
        opens, closes, now = hours.opens_at, hours.closes_at, local.strftime("%H:%M")
        if hours.closed or opens is None or closes is None or not opens <= now < closes:
            return Refused(reason="department_closed", clear=(clear,))
        return None

    async def _site(self, dealership_id: str) -> str | None:
        found = await self._client.list_dealerships()
        if not isinstance(found, o.DealershipsListed):
            return None
        return next((item.name for item in found.dealerships if item.id == dealership_id), None)

    async def _car(self, vehicle_id: str) -> str | None:
        found = await self._client.get_vehicle(vehicle_id)
        if not isinstance(found, o.VehicleFound):
            return None
        item = found.vehicle
        return f"{item.year} {item.make} {item.model} {item.variant}"

    async def _service(self, service_id: str) -> str | None:
        found = await self._client.list_service_types()
        if not isinstance(found, o.ServiceTypesListed):
            return None
        return next((item.name for item in found.service_types if item.id == service_id), None)

    def _is_bad_slot(self, slot_id: str) -> bool:
        return self._store.is_unbookable(slot_id, self._now())

    async def _test_drive(self, values: Values, key: str) -> Prepared | Refused:
        vehicle_id, slot_id = _text(values, "vehicle_id"), _text(values, "slot_id")
        found = await self._client.find_test_drive_slots(vehicle_id)
        if isinstance(found, o.TestDriveNotBookable):
            return Refused(reason=f"vehicle_{found.availability}")
        if isinstance(found, o.NotFound):
            return Refused(reason="vehicle_not_found", clear=("vehicle_id",))
        if not isinstance(found, o.TestDriveSlotsFound):
            return UNAVAILABLE
        slot = next((item for item in found.slots if item.id == slot_id), None)
        if slot is None or self._is_bad_slot(slot_id):
            return Refused(reason="slot_unavailable", clear=("slot_id",))
        closed = await self._open_at(slot, "sales", "slot_id")
        if closed is not None:
            return closed
        request = TestDriveRequest(
            idempotency_key=key,
            slot=slot,
            vehicle_id=vehicle_id,
            contact=_contact(values),
            notes=_optional(values, "notes"),
        )
        return Prepared(
            request=request.model_dump_json(),
            title="Confirm your test drive",
            rows=[
                Row(label="Car", value=f"{slot.make} {slot.model} {slot.variant}"),
                Row(label="When", value=format_when(slot.starts_at)),
                Row(label="Where", value=slot.dealership_name),
                *_optional_rows(values, notes="Notes"),
                *_contact_rows(values),
            ],
        )

    async def _workshop(self, values: Values, key: str) -> Prepared | Refused:
        slot_id = _text(values, "slot_id")
        found = await self._client.find_workshop_slots(
            dealership_id=_text(values, "dealership_id"),
            service_type_id=_text(values, "service_type_id"),
        )
        if not isinstance(found, o.WorkshopSlotsFound):
            return UNAVAILABLE
        slot = next((item for item in found.slots if item.id == slot_id), None)
        if slot is None or self._is_bad_slot(slot_id):
            return Refused(reason="slot_unavailable", clear=("slot_id",))
        closed = await self._open_at(slot, "service", "slot_id")
        if closed is not None:
            return closed
        request = WorkshopBookingRequest(
            idempotency_key=key,
            slot=slot,
            registration=_text(values, "registration"),
            mileage=int(values["mileage"]),
            contact=_contact(values),
            notes=_optional(values, "notes"),
        )
        return Prepared(
            request=request.model_dump_json(),
            title="Confirm your workshop booking",
            rows=[
                Row(label="Service", value=slot.service_name),
                Row(label="When", value=format_when(slot.starts_at)),
                Row(label="Where", value=slot.dealership_name),
                Row(label="Registration", value=request.registration),
                Row(label="Mileage", value=f"{request.mileage:,} miles"),
                *_optional_rows(values, notes="Notes"),
                *_contact_rows(values),
            ],
        )

    async def _slot_anywhere(self, slot_id: str) -> WorkshopSlot | Refused | None:
        """The platform caps a listing at 100, so each site is searched separately."""
        sites = await self._client.list_dealerships()
        if not isinstance(sites, o.DealershipsListed):
            return UNAVAILABLE
        for site in sites.dealerships:
            found = await self._client.find_workshop_slots(dealership_id=site.id)
            if not isinstance(found, o.WorkshopSlotsFound):
                return UNAVAILABLE
            for slot in found.slots:
                if slot.id == slot_id:
                    return slot
        return None

    async def _amend(self, values: Values, booking_id: str) -> Prepared | Refused:
        current = await self._client.get_workshop_booking(booking_id)
        if not isinstance(current, o.WorkshopBookingFound):
            return UNAVAILABLE
        booking = current.booking
        if booking.is_cancelled:
            return Refused(reason="booking_cancelled")
        target: WorkshopSlot | None = None
        rows: list[Row] = []
        warnings: list[str] = []
        if "target_slot_id" in values:
            slot_id = _text(values, "target_slot_id")
            located = await self._slot_anywhere(slot_id)
            if isinstance(located, Refused):
                return located
            if located is None or self._is_bad_slot(slot_id):
                return Refused(reason="slot_unavailable", clear=("target_slot_id",))
            if located.service_type_id != booking.service_type_id:
                # D-009's replacement needs the customer's contact details, which the client
                # deliberately does not expose. See LIMITATIONS.md.
                return Refused(reason="service_change_unsupported", clear=("target_slot_id",))
            closed = await self._open_at(located, "service", "target_slot_id")
            if closed is not None:
                return closed
            target = located
            rows.append(Row(label="New time", value=format_when(located.starts_at)))
            if located.dealership_id != booking.dealership_id:
                rows.append(Row(label="New location", value=located.dealership_name))
                warnings.append("This moves your booking to a different dealership.")
        mileage = int(values["mileage"]) if "mileage" in values else None
        notes = _optional(values, "notes")
        if mileage is not None:
            rows.append(Row(label="Mileage", value=f"{mileage:,}"))
        if notes is not None:
            rows.append(Row(label="Notes", value=notes))
        request = AmendRequest(booking=booking, target_slot=target, mileage=mileage, notes=notes)
        return Prepared(
            request=request.model_dump_json(),
            title="Confirm your booking change",
            rows=[Row(label="Booking", value=booking.reference), *rows],
            warnings=warnings,
        )

    async def _cancel(self, booking_id: str) -> Prepared | Refused:
        current = await self._client.get_workshop_booking(booking_id)
        if not isinstance(current, o.WorkshopBookingFound):
            return UNAVAILABLE
        if current.booking.is_cancelled:
            return Refused(reason="booking_cancelled")
        booking = current.booking
        service = await self._service(booking.service_type_id)
        site = await self._site(booking.dealership_id)
        if service is None or site is None:
            return UNAVAILABLE
        return Prepared(
            request=CancelRequest(booking_id=booking_id).model_dump_json(),
            title="Cancel your booking",
            rows=[
                Row(label="Booking", value=booking.reference),
                Row(label="Service", value=service),
                Row(label="Where", value=site),
            ],
            warnings=["Once cancelled, this appointment time may not be available again."],
        )

    async def _record(self, kind: WorkflowKind, values: Values, key: str) -> Prepared | Refused:
        contact = _contact(values)
        rows = _contact_rows(values)
        site_id = _text(values, "dealership_id") if "dealership_id" in values else ""
        site = await self._site(site_id) if site_id else ""
        if site is None:
            return UNAVAILABLE
        car = ""
        if "vehicle_id" in values:
            named = await self._car(_text(values, "vehicle_id"))
            if named is None:
                return Refused(reason="vehicle_not_found", clear=("vehicle_id",))
            car = named
        if kind is WorkflowKind.CALLBACK:
            callback = CallbackRequest(
                idempotency_key=key,
                dealership_id=site_id,
                department=_text(values, "department"),
                vehicle_id=_optional(values, "vehicle_id"),
                contact=contact,
                preferred_time=_optional(values, "preferred_time"),
                reason=_text(values, "reason"),
            )
            shown = [
                Row(label="Dealership", value=site),
                Row(label="Department", value=pretty(callback.department)),
                Row(label="About", value=callback.reason),
                *([Row(label="Car", value=car)] if car else []),
                *_optional_rows(values, preferred_time="Best time to call"),
            ]
            return Prepared(
                request=callback.model_dump_json(), title="Request a callback", rows=[*shown, *rows]
            )
        if kind is WorkflowKind.MESSAGE:
            message = DealershipMessageRequest(
                idempotency_key=key,
                dealership_id=site_id,
                department=_text(values, "department"),
                subject=_text(values, "subject"),
                message=_text(values, "message"),
                preferred_contact_method=_text(values, "preferred_contact_method"),
                contact=contact,
            )
            shown = [
                Row(label="Dealership", value=site),
                Row(label="Department", value=pretty(message.department)),
                Row(label="Subject", value=message.subject),
                Row(label="Message", value=message.message),
                Row(label="Contact by", value=pretty(message.preferred_contact_method)),
            ]
            return Prepared(
                request=message.model_dump_json(), title="Send your message", rows=[*shown, *rows]
            )
        if kind is WorkflowKind.SALES_ENQUIRY:
            enquiry = SalesEnquiryRequest(
                idempotency_key=key,
                dealership_id=site_id,
                vehicle_id=_optional(values, "vehicle_id"),
                enquiry_type=_text(values, "enquiry_type"),
                contact=contact,
                message=_text(values, "message"),
            )
            shown = [
                Row(label="Dealership", value=site),
                Row(label="About", value=pretty(enquiry.enquiry_type)),
                *([Row(label="Car", value=car)] if car else []),
                Row(label="Message", value=enquiry.message),
            ]
            return Prepared(
                request=enquiry.model_dump_json(), title="Send your enquiry", rows=[*shown, *rows]
            )
        if kind is WorkflowKind.VEHICLE_INTEREST:
            interest = VehicleInterestRequest(
                idempotency_key=key,
                vehicle_id=_text(values, "vehicle_id"),
                contact=contact,
                notes=_optional(values, "notes"),
            )
            shown = [Row(label="Car", value=car), *_optional_rows(values, notes="Notes")]
            return Prepared(
                request=interest.model_dump_json(),
                title="Register your interest",
                rows=[*shown, *rows],
            )
        estimate = PartExchangeRequest(
            idempotency_key=key,
            dealership_id=site_id,
            registration=_text(values, "registration"),
            mileage=int(values["mileage"]),
            condition=_text(values, "condition"),
            contact=contact,
        )
        shown = [
            Row(label="Dealership", value=site),
            Row(label="Registration", value=estimate.registration),
            Row(label="Mileage", value=f"{estimate.mileage:,} miles"),
            Row(label="Condition", value=pretty(estimate.condition)),
        ]
        return Prepared(
            request=estimate.model_dump_json(), title="Get your estimate", rows=[*shown, *rows]
        )
