"""What the customer reads. All of it comes from code, never from the model (D-005)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from ..dealership.models import LONDON
from ..workflows.types import WorkflowKind

Status = Literal[
    "idle",
    "collecting",
    "verifying",
    "locked",
    "confirming",
    "processing",
    "completed",
    "failed",
    "unknown",
    "two_bookings",
    "abandoned",
]


class Row(BaseModel):
    label: str
    value: str


FieldKind = Literal["text", "email", "tel", "number", "textarea", "select"]


class Option(BaseModel):
    value: str
    label: str


class Field(BaseModel):
    name: str
    label: str
    kind: FieldKind
    options: list[Option] = []
    value: str | None = None
    required: bool = True


class ConfirmationBlock(BaseModel):
    title: str
    rows: list[Row]
    warnings: list[str] = []
    token: str
    expires_at: str
    button: str = "Confirm"


class View(BaseModel):
    status: Status
    message: str = ""
    block: ConfirmationBlock | None = None
    prompts: list[str] = []
    notices: list[str] = []
    fields: list[Field] = []
    reference: str | None = None
    can_check_again: bool = False
    # The customer asked something else, so the form or card waits behind `resume` (D-034).
    hidden: bool = False
    resume: str = ""


def pretty(value: str) -> str:
    """A stored choice as a customer would write it: part-exchange becomes Part exchange."""
    return value.replace("-", " ").replace("_", " ").capitalize()


def format_when(moment: datetime) -> str:
    local = moment.astimezone(LONDON)
    return f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y')} at {local.strftime('%H:%M')}"


REFUSALS = {
    "slot_unavailable": "That time is no longer available. Please choose another.",
    "department_closed": "The department is closed at that time. Please choose another.",
    "vehicle_reserved": "That car is now reserved, so a test drive can't be booked. "
    "You can register your interest instead.",
    "vehicle_sold": "That car has been sold, so a test drive can't be booked.",
    "vehicle_not_found": "We couldn't find that car. Please choose another.",
    "booking_cancelled": "That booking has already been cancelled.",
    "service_change_unsupported": "Changing to a different service isn't available in chat yet. "
    "Please choose a time for your current service, or contact the dealership.",
    "platform_unavailable": "We can't reach the dealership system right now. "
    "Nothing has been changed. Please try again shortly.",
}

FAILURES = {
    **REFUSALS,
    "vehicle_unavailable": "That car can no longer be booked, so this is not booked.",
    "interest_not_accepted": "Interest can only be registered on a reserved car.",
    "booking_mismatch": "Something went wrong with this booking. Please contact the dealership "
    "before making any plans.",
    "booking_no_longer_active": "This has been cancelled, so you are not booked.",
    "confirmation_stale": "That request timed out before it was sent, so nothing was booked. "
    "Please try again.",
    "not_sent": "We couldn't reach the dealership, so this was not sent. Please try again shortly.",
    "not_found": "We couldn't find that record, so nothing was changed.",
    "integration_fault": "Something went wrong on our side. Please contact the dealership.",
    "malformed_response": "We couldn't confirm this. Please contact the dealership.",
    "details_rejected": "The dealership system didn't accept those details.",
}

COLLECTING_REASONS = {
    "slot_lost": "That time has just been taken. Please choose another.",
    "details_rejected": "Some of those details weren't accepted. Please check them.",
    **REFUSALS,
}

PROCESSING = {
    WorkflowKind.TEST_DRIVE: "We're booking your test drive.",
    WorkflowKind.WORKSHOP_CREATE: "We're booking your workshop appointment.",
    WorkflowKind.WORKSHOP_AMEND: "We're updating your booking.",
    WorkflowKind.WORKSHOP_CANCEL: "We're cancelling your booking.",
}

RESUME = {
    WorkflowKind.TEST_DRIVE: "Continue booking your test drive",
    WorkflowKind.WORKSHOP_CREATE: "Continue booking your service",
    WorkflowKind.WORKSHOP_AMEND: "Continue changing your booking",
    WorkflowKind.WORKSHOP_CANCEL: "Continue cancelling your booking",
    WorkflowKind.BOOKING_RETRIEVAL: "Continue checking your booking",
    WorkflowKind.PART_EXCHANGE: "Continue your part-exchange estimate",
    WorkflowKind.CALLBACK: "Continue your callback request",
    WorkflowKind.MESSAGE: "Continue your message",
    WorkflowKind.SALES_ENQUIRY: "Continue your enquiry",
    WorkflowKind.VEHICLE_INTEREST: "Continue registering your interest",
}
# Only a form or a confirmation card can wait. Anything being sent is always shown.
HIDEABLE = frozenset({"collecting", "verifying", "confirming"})

UNKNOWN = (
    "We're still checking whether this went through, so please don't book again. "
    "We'll keep it on record, and you can check again in a moment."
)

NOT_APPLIED = {
    WorkflowKind.WORKSHOP_CANCEL: "Your booking has not been cancelled. Please try again.",
    WorkflowKind.WORKSHOP_AMEND: "Your booking has not been changed. Please try again.",
}


def failed_message(kind: WorkflowKind, reason: str | None) -> str:
    if reason == "not_applied":
        return NOT_APPLIED.get(kind, "This did not go through. Please try again.")
    return FAILURES.get(reason or "", "This did not go through. Please try again.")


def _pounds(pence: int) -> str:
    return f"£{pence / 100:,.0f}"


def completed_message(kind: WorkflowKind, outcome: dict[str, Any] | None) -> str:
    reference = (outcome or {}).get("reference", "")
    suffix = f" Your reference is {reference}." if reference else ""
    if kind is WorkflowKind.TEST_DRIVE and outcome:
        when = format_when(datetime.fromisoformat(outcome["starts_at"]))
        return f"Your test drive is confirmed for {when}.{suffix}"
    if kind is WorkflowKind.WORKSHOP_CREATE and outcome:
        when = format_when(datetime.fromisoformat(outcome["starts_at"]))
        return f"Your {outcome['service_name']} is confirmed for {when}.{suffix}"
    if kind is WorkflowKind.WORKSHOP_AMEND:
        return f"Your booking has been updated.{suffix}"
    if kind is WorkflowKind.WORKSHOP_CANCEL:
        return f"Your booking has been cancelled.{suffix}"
    if kind is WorkflowKind.BOOKING_RETRIEVAL and outcome and "booking" in outcome:
        booking = outcome["booking"]
        state = "cancelled" if booking["status"] == "cancelled" else "booked"
        if booking.get("starts_at") is None:
            # Only a lookup returns the time, and it needs the customer's details again.
            return f"Booking {booking['reference']}. Status: {state}."
        when = format_when(datetime.fromisoformat(booking["starts_at"]))
        return (
            f"{booking['service_type_name']} at {booking['dealership_name']} on {when}. "
            f"Status: {state}."
        )
    if kind is WorkflowKind.PART_EXCHANGE and outcome and "notice" in outcome:
        low = _pounds(outcome["estimate_low_pence"])
        high = _pounds(outcome["estimate_high_pence"])
        return f"Your indicative estimate is {low} to {high}. {outcome['notice']}{suffix}"
    if kind is WorkflowKind.CALLBACK:
        return f"The dealership has your request. We can't promise an exact time.{suffix}"
    if kind is WorkflowKind.MESSAGE:
        return f"The department can now read your message. Nobody has replied yet.{suffix}"
    if kind is WorkflowKind.SALES_ENQUIRY:
        return f"Your enquiry has been saved for the dealership.{suffix}"
    if kind is WorkflowKind.VEHICLE_INTEREST:
        return (
            "We've noted your interest. This doesn't hold the car or give you a place in a queue."
            f"{suffix}"
        )
    return f"Done.{suffix}"


def outcome_reference(outcome: dict[str, Any] | None) -> str | None:
    if not outcome:
        return None
    if "booking" in outcome:
        return str(outcome["booking"]["reference"])
    reference = outcome.get("reference")
    return str(reference) if reference else None
