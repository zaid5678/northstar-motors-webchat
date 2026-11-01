"""Tells the widget what to ask for. Choices come from live platform data, never from the model."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from ..dealership import outcomes as o
from ..dealership.client import DealershipClient
from ..dealership.models import TestDriveSlot, VehicleSearch, WorkshopSlot
from ..persistence.repository import Repository
from ..workflows.machine import usable_values
from ..workflows.types import DEFINITIONS, Provenance, Workflow, WorkflowKind
from .present import Field, FieldKind, Option, format_when

MAX_CHOICES = 20
LABELS = {
    "vehicle_id": "Car",
    "slot_id": "Time",
    "target_slot_id": "New time",
    "dealership_id": "Dealership",
    "service_type_id": "Service",
    "department": "Department",
    "enquiry_type": "What is it about?",
    "reason": "What is it about?",
    "message": "Your message",
    "subject": "Subject",
    "preferred_contact_method": "How should we contact you?",
    "preferred_time": "Best time to call",
    "registration": "Registration",
    "mileage": "Mileage",
    "condition": "Condition",
    "notes": "Notes",
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "reference": "Booking reference",
}
KINDS: dict[str, FieldKind] = {
    "email": "email",
    "phone": "tel",
    "mileage": "number",
    "reason": "textarea",
    "message": "textarea",
    "notes": "textarea",
}
IDENTITY = ("reference", "last_name", "registration", "phone")


def _text_field(name: str, value: str | None = None) -> Field:
    return Field(name=name, label=LABELS.get(name, name), kind=KINDS.get(name, "text"), value=value)


def identity_fields() -> list[Field]:
    return [_text_field(name) for name in IDENTITY]


def _pretty(value: str) -> str:
    return value.replace("-", " ").capitalize()


class FormBuilder:
    def __init__(
        self, client: DealershipClient, store: Repository, now: Callable[[], datetime]
    ) -> None:
        self._client = client
        self._store = store
        self._now = now

    def _bookable(self, slots: Iterable[TestDriveSlot | WorkshopSlot]) -> list[Option]:
        return [
            Option(value=slot.id, label=format_when(slot.starts_at))
            for slot in slots
            if not self._store.is_unbookable(slot.id, self._now())
        ][:MAX_CHOICES]

    async def _vehicles(self, kind: WorkflowKind) -> list[Option]:
        wanted = "reserved" if kind is WorkflowKind.VEHICLE_INTEREST else "available"
        found = await self._client.search_vehicles(
            VehicleSearch(availability=wanted, page_size=50)
        )
        if not isinstance(found, o.VehiclesListed):
            return []
        return [
            Option(value=item.id, label=f"{item.year} {item.make} {item.model} {item.variant}")
            for item in found.vehicles
        ]

    async def _slots(
        self, workflow: Workflow, booking_id: str | None
    ) -> list[Option] | None:
        """None means a choice this depends on has not been made yet."""
        values = usable_values(workflow)
        if workflow.kind is WorkflowKind.TEST_DRIVE:
            if "vehicle_id" not in values:
                return None
            drives = await self._client.find_test_drive_slots(str(values["vehicle_id"]))
            return self._bookable(drives.slots) if isinstance(drives, o.TestDriveSlotsFound) else []
        site, service = values.get("dealership_id"), values.get("service_type_id")
        if workflow.kind is WorkflowKind.WORKSHOP_AMEND and booking_id:
            current = await self._client.get_workshop_booking(booking_id)
            if not isinstance(current, o.WorkshopBookingFound):
                return []
            site = current.booking.dealership_id
            service = current.booking.service_type_id
        if site is None or service is None:
            return None
        found = await self._client.find_workshop_slots(
            dealership_id=str(site), service_type_id=str(service)
        )
        return self._bookable(found.slots) if isinstance(found, o.WorkshopSlotsFound) else []

    async def _field(
        self, workflow: Workflow, name: str, booking_id: str | None, value: str | None
    ) -> Field | None:
        allowed = DEFINITIONS[workflow.kind].allowed.get(name)
        options: list[Option] | None = None
        if allowed:
            options = [Option(value=item, label=_pretty(item)) for item in sorted(allowed)]
        elif name == "dealership_id":
            sites = await self._client.list_dealerships()
            if isinstance(sites, o.DealershipsListed):
                options = [Option(value=s.id, label=s.name) for s in sites.dealerships]
        elif name == "service_type_id":
            services = await self._client.list_service_types()
            if isinstance(services, o.ServiceTypesListed):
                options = [Option(value=s.id, label=s.name) for s in services.service_types]
        elif name == "vehicle_id":
            options = await self._vehicles(workflow.kind)
        elif name in ("slot_id", "target_slot_id"):
            options = await self._slots(workflow, booking_id)
            if options is None:
                return None
        if options:
            return Field(
                name=name,
                label=LABELS.get(name, name),
                kind="select",
                options=options,
                value=value,
            )
        return _text_field(name, value)

    async def build(
        self, workflow: Workflow, booking_id: str | None, also: Iterable[str] = ()
    ) -> list[Field]:
        """Missing required slots, plus anything inferred, which comes back filled in so the
        customer can confirm or correct it (S3)."""
        definition = DEFINITIONS[workflow.kind]
        known = usable_values(workflow)
        names = [name for name in definition.required if name not in known]
        names += [name for name in definition.any_of if name not in known]
        names += [name for name in also if name not in names]
        names += [
            name
            for name, slot in workflow.slots.items()
            if slot.provenance is Provenance.INFERRED and name not in names
        ]
        names += [name for name in workflow.offers if name not in names]
        fields: list[Field] = []
        for name in names:
            slot = workflow.offers.get(name) or workflow.slots.get(name)
            value = None if slot is None or name in also else str(slot.value)
            field = await self._field(workflow, name, booking_id, value)
            if field is not None:
                fields.append(field)
        return fields
