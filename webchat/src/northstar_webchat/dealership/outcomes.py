"""What a platform call meant for the customer.

Client methods return one of these and never raise for a platform result. Each has a `kind`, so an
outcome can be stored as a session event (D-003).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from .models import (
    Availability,
    BusinessInformation,
    Dealership,
    Offer,
    OpeningHours,
    Pagination,
    ServiceType,
    TestDriveBooking,
    TestDriveSlot,
    Vehicle,
    VehicleDetails,
    WorkshopBooking,
    WorkshopSlot,
)


class Outcome(BaseModel):
    model_config = ConfigDict(frozen=True)


# Failures shared across operations.


class PlatformUnavailable(Outcome):
    """A read got no usable answer after its retries."""

    kind: Literal["platform_unavailable"] = "platform_unavailable"
    attempts: int
    detail: str


class MalformedResponse(Outcome):
    """A read returned success with a body we could not trust."""

    kind: Literal["malformed_response"] = "malformed_response"
    operation: str
    detail: str


class NotFound(Outcome):
    kind: Literal["not_found"] = "not_found"
    code: str


class InvalidDetails(Outcome):
    """The customer can correct this: `field_errors` says what to change."""

    kind: Literal["invalid_details"] = "invalid_details"
    message: str
    field_errors: dict[str, str]


class IntegrationFault(Outcome):
    """Our bug or misconfiguration, such as a bad key. Log it and never show it to the customer."""

    kind: Literal["integration_fault"] = "integration_fault"
    status: int
    code: str | None


class WriteNotSent(Outcome):
    """Every attempt failed before reaching the platform, so nothing happened."""

    kind: Literal["write_not_sent"] = "write_not_sent"
    operation: str
    attempts: int


class WriteOutcomeUnknown(Outcome):
    """The write may have happened. Chat must not claim success or failure. A create can be
    resubmitted with the same key, and the platform will return the record if it exists."""

    kind: Literal["write_outcome_unknown"] = "write_outcome_unknown"
    operation: str
    reason: Literal["no_response", "server_error", "malformed_response"]
    attempts: int
    idempotency_key: str | None = None


ReadFailure = PlatformUnavailable | MalformedResponse | IntegrationFault
WriteFailure = WriteNotSent | WriteOutcomeUnknown | InvalidDetails | NotFound | IntegrationFault


# Catalogue and reference reads.


class VehiclesListed(Outcome):
    kind: Literal["vehicles_listed"] = "vehicles_listed"
    vehicles: list[Vehicle]
    pagination: Pagination


class StockSearched(Outcome):
    """Matches grouped by state. A budget search drops null-price cars on the platform, so they are
    fetched separately into `price_on_request` (D-013)."""

    kind: Literal["stock_searched"] = "stock_searched"
    available: list[Vehicle]
    reserved: list[Vehicle]
    sold: list[Vehicle]
    price_on_request: list[Vehicle]


class VehicleFound(Outcome):
    kind: Literal["vehicle_found"] = "vehicle_found"
    vehicle: VehicleDetails


class VehicleStatus(Outcome):
    """`next_slot_id` is set only when a test drive can really be booked: the platform also returns
    one for reserved and sold vehicles."""

    kind: Literal["vehicle_status"] = "vehicle_status"
    vehicle_id: str
    availability: Availability
    can_enquire: bool
    can_book_test_drive: bool
    can_register_interest: bool
    next_slot_id: str | None
    next_slot_starts_at: AwareDatetime | None


class OffersListed(Outcome):
    kind: Literal["offers_listed"] = "offers_listed"
    offers: list[Offer]
    expired_hidden: int


class OfferFound(Outcome):
    kind: Literal["offer_found"] = "offer_found"
    offer: Offer


class OfferExpired(Outcome):
    kind: Literal["offer_expired"] = "offer_expired"
    offer_id: str


class DealershipsListed(Outcome):
    kind: Literal["dealerships_listed"] = "dealerships_listed"
    dealerships: list[Dealership]


class DealershipFound(Outcome):
    kind: Literal["dealership_found"] = "dealership_found"
    dealership: Dealership


class OpeningHoursFound(Outcome):
    kind: Literal["opening_hours_found"] = "opening_hours_found"
    hours: OpeningHours


class WorkshopLocationsListed(Outcome):
    kind: Literal["workshop_locations_listed"] = "workshop_locations_listed"
    locations: list[Dealership]


class ServiceTypesListed(Outcome):
    kind: Literal["service_types_listed"] = "service_types_listed"
    service_types: list[ServiceType]


class BusinessInformationFound(Outcome):
    kind: Literal["business_information_found"] = "business_information_found"
    information: BusinessInformation


class TestDriveSlotsFound(Outcome):
    """`truncated` means the platform's silent 100-row cap was reached, so more slots may exist."""

    __test__ = False

    kind: Literal["test_drive_slots_found"] = "test_drive_slots_found"
    slots: list[TestDriveSlot]
    past_hidden: int
    truncated: bool


class TestDriveNotBookable(Outcome):
    __test__ = False

    kind: Literal["test_drive_not_bookable"] = "test_drive_not_bookable"
    vehicle_id: str
    availability: Availability
    can_register_interest: bool


class WorkshopSlotsFound(Outcome):
    """An empty list is a valid answer, as at Bolton in its first seven days."""

    kind: Literal["workshop_slots_found"] = "workshop_slots_found"
    slots: list[WorkshopSlot]
    past_hidden: int
    truncated: bool


# Records with no commitment beyond their own wording.


class EnquiryReceived(Outcome):
    """Saved for the dealership. It promises no reply, finance decision, or vehicle availability."""

    kind: Literal["enquiry_received"] = "enquiry_received"
    id: str
    reference: str
    replayed: bool


class InterestRegistered(Outcome):
    """Interest noted. It is not a queue place, although the reference starts `WAIT-`."""

    kind: Literal["interest_registered"] = "interest_registered"
    id: str
    reference: str
    replayed: bool


class CallbackRequested(Outcome):
    """The dealership has the request. No callback time is promised."""

    kind: Literal["callback_requested"] = "callback_requested"
    id: str
    reference: str
    replayed: bool


class MessageReceived(Outcome):
    """The department can read it. Nobody has replied yet."""

    kind: Literal["message_received"] = "message_received"
    id: str
    reference: str
    replayed: bool


class EstimateGiven(Outcome):
    """An indicative range, and never without its notice."""

    kind: Literal["estimate_given"] = "estimate_given"
    id: str
    reference: str
    estimate_low_pence: int
    estimate_high_pence: int
    notice: str
    replayed: bool


class EstimateWithoutNotice(Outcome):
    """The estimate exists, but its notice could not be fetched, so the figures are withheld."""

    kind: Literal["estimate_without_notice"] = "estimate_without_notice"
    id: str
    reference: str
    replayed: bool


class InterestNotAccepted(Outcome):
    """The vehicle is not reserved: it is available, or sold."""

    kind: Literal["interest_not_accepted"] = "interest_not_accepted"
    vehicle_id: str


# Booking results.


class TestDriveBooked(Outcome):
    """The platform's response has no time or names, so they are carried from the chosen slot."""

    __test__ = False

    kind: Literal["test_drive_booked"] = "test_drive_booked"
    id: str
    reference: str
    slot_id: str
    vehicle_id: str
    dealership_id: str
    dealership_name: str
    starts_at: AwareDatetime
    replayed: bool


class WorkshopBooked(Outcome):
    kind: Literal["workshop_booked"] = "workshop_booked"
    id: str
    reference: str
    slot_id: str
    dealership_id: str
    dealership_name: str
    service_type_id: str
    service_name: str
    starts_at: AwareDatetime
    replayed: bool


class VehicleReserved(Outcome):
    kind: Literal["vehicle_reserved"] = "vehicle_reserved"
    vehicle_id: str


class VehicleUnavailable(Outcome):
    kind: Literal["vehicle_unavailable"] = "vehicle_unavailable"
    vehicle_id: str


class SlotTaken(Outcome):
    """Someone else claimed the slot, or it is blocked. Offer another."""

    kind: Literal["slot_taken"] = "slot_taken"
    slot_id: str


class SlotUnbookable(Outcome):
    """The platform failed on this slot, as it always does for one released by cancellation.
    Hide the slot (D-007) and offer another."""

    kind: Literal["slot_unbookable"] = "slot_unbookable"
    slot_id: str


class SlotInPast(Outcome):
    """Refused without calling the platform, which would accept it."""

    kind: Literal["slot_in_past"] = "slot_in_past"
    slot_id: str


class SlotVehicleMismatch(Outcome):
    """Refused without calling the platform: this slot belongs to a different car."""

    kind: Literal["slot_vehicle_mismatch"] = "slot_vehicle_mismatch"
    slot_id: str
    expected_vehicle_id: str
    slot_vehicle_id: str


class BookingMismatch(Outcome):
    """The platform booked a different car from the one requested. Needs a person to resolve."""

    kind: Literal["booking_mismatch"] = "booking_mismatch"
    id: str
    reference: str
    expected_vehicle_id: str
    booked_vehicle_id: str


class BookingNoLongerActive(Outcome):
    """A replay found the booking, but it has since been cancelled, so nothing is booked."""

    kind: Literal["booking_no_longer_active"] = "booking_no_longer_active"
    id: str
    reference: str


# Existing workshop bookings.


class WorkshopBookingFound(Outcome):
    """Check `booking.is_cancelled`: a cancelled booking is still found."""

    kind: Literal["workshop_booking_found"] = "workshop_booking_found"
    booking: WorkshopBooking


class TestDriveBookingFound(Outcome):
    __test__ = False

    kind: Literal["test_drive_booking_found"] = "test_drive_booking_found"
    booking: TestDriveBooking


class BookingNotMatched(Outcome):
    """Deliberately says nothing about which detail was wrong."""

    kind: Literal["booking_not_matched"] = "booking_not_matched"


class BookingAmended(Outcome):
    """`site_changed` and `service_changed` report what the platform silently allows.
    `starts_at` is known only for a slot move, because the response has no time."""

    kind: Literal["booking_amended"] = "booking_amended"
    id: str
    reference: str
    slot_id: str
    dealership_id: str
    service_type_id: str
    starts_at: AwareDatetime | None
    site_changed: bool
    service_changed: bool
    reconciled: bool


class AmendNotApplied(Outcome):
    """The response was lost, and a re-read shows the booking did not change."""

    kind: Literal["amend_not_applied"] = "amend_not_applied"
    booking_id: str


class BookingAlreadyCancelled(Outcome):
    kind: Literal["booking_already_cancelled"] = "booking_already_cancelled"
    booking_id: str


class BookingCancelled(Outcome):
    """`already_cancelled` means no action was taken now. `slot_id` feeds the D-007 table."""

    kind: Literal["booking_cancelled"] = "booking_cancelled"
    id: str
    reference: str
    slot_id: str
    already_cancelled: bool
    reconciled: bool


class CancelNotApplied(Outcome):
    """The response was lost, and a re-read shows the booking is still live."""

    kind: Literal["cancel_not_applied"] = "cancel_not_applied"
    booking_id: str
