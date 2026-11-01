"""Typed platform data and write requests.

Models keep only the fields the webchat may use. Unknown fields are ignored, which is how customer
contact details and `monthlyPricePence` stay out of the domain (D-012, D-019). A field with no
default must be present in the response, so a missing `pricePence` is malformed rather than being
read as "price on request".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Generic, Literal, Self, TypeVar
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)
from pydantic.alias_generators import to_camel

LONDON = ZoneInfo("Europe/London")

Availability = Literal["available", "reserved", "sold"]
FuelType = Literal["Petrol", "Diesel", "Hybrid", "Electric"]
BodyStyle = Literal["SUV", "Hatchback", "Saloon", "Estate"]
Transmission = Literal["Automatic", "Manual"]
SortOrder = Literal["newest", "priceAsc", "priceDesc", "mileageAsc"]
ProductType = Literal["PCP", "PCH"]
Department = Literal["sales", "service", "parts"]
MessageDepartment = Literal["sales", "service", "parts", "general"]
EnquiryType = Literal["general", "availability", "finance", "part-exchange"]
ContactMethod = Literal["email", "phone"]
VehicleCondition = Literal["excellent", "good", "fair"]
BookingStatus = Literal["confirmed", "cancelled"]
T = TypeVar("T", bound=BaseModel)


def normalise_phone(value: str) -> str:
    """The platform handles spaces and `+44` itself but rejects a `0044` prefix."""
    digits = re.sub(r"[\s().-]", "", value)
    return f"0{digits[4:]}" if digits.startswith("0044") else value


def normalise_registration(value: str) -> str:
    """The platform accepts only letters, digits, and spaces."""
    return " ".join(value.replace("-", " ").upper().split())


Phone = Annotated[str, AfterValidator(normalise_phone)]
Registration = Annotated[str, AfterValidator(normalise_registration)]


class Wire(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="ignore"
    )


class Items(Wire, Generic[T]):
    items: list[T]


class Vehicle(Wire):
    id: str
    dealership_id: str
    dealership_name: str
    dealership_town: str
    make: str
    model: str
    variant: str
    year: int
    price_pence: int | None
    mileage: NonNegativeInt
    fuel_type: str
    transmission: str
    colour: str
    body_style: str
    availability: Availability
    images: list[str]

    @property
    def price_on_request(self) -> bool:
        return self.price_pence is None


class VehicleDetails(Vehicle):
    description: str
    highlights: list[str]


class Pagination(Wire):
    page: PositiveInt
    page_size: PositiveInt
    total_items: NonNegativeInt
    total_pages: NonNegativeInt


class VehiclePage(Wire):
    items: list[Vehicle]
    pagination: Pagination


class VehicleSearch(Wire):
    """Search filters. Values are checked here because the platform answers a bad filter with an
    empty 200, which looks exactly like having no stock."""

    q: str | None = None
    make: str | None = None
    model: str | None = None
    fuel_type: FuelType | None = None
    transmission: Transmission | None = None
    body_style: BodyStyle | None = None
    availability: Availability | None = None
    dealership_id: str | None = None
    min_price_pence: NonNegativeInt | None = None
    max_price_pence: NonNegativeInt | None = None
    max_mileage: NonNegativeInt | None = None
    min_year: PositiveInt | None = None
    sort: SortOrder = "newest"
    page: PositiveInt = 1
    page_size: int = Field(default=12, ge=1, le=50)

    def to_params(self) -> dict[str, str]:
        dumped = self.model_dump(by_alias=True, exclude_none=True)
        return {key: str(value) for key, value in dumped.items()}


class NextSlot(Wire):
    id: str
    starts_at: AwareDatetime


class AvailabilityRecord(Wire):
    vehicle_id: str
    availability: Availability
    can_enquire: bool
    can_book_test_drive: bool
    can_register_interest: bool
    next_test_drive_slot: NextSlot | None


class Offer(Wire):
    id: str
    make: str
    model: str
    title: str
    product_type: ProductType
    monthly_price_pence: NonNegativeInt
    upfront_payment_pence: NonNegativeInt
    apr: float | None
    term_months: PositiveInt
    annual_mileage: PositiveInt
    expires_on: date
    description: str
    image: str


class Dealership(Wire):
    id: str
    name: str
    town: str
    postcode: str
    address_line: str
    phone: str
    email: str
    brands: list[str]


class WeeklyHours(Wire):
    department: Department
    day: str
    day_of_week: int = Field(ge=0, le=6)
    opens_at: str | None
    closes_at: str | None
    closed: bool


class HolidayException(Wire):
    department: Department
    date: date
    label: str
    opens_at: str | None
    closes_at: str | None
    closed: bool


class EffectiveHours(Wire):
    department: Department
    date: date
    opens_at: str | None
    closes_at: str | None
    closed: bool
    holiday_label: str | None
    exception_listed: bool
    other_departments_excepted: bool


class OpeningHours(Wire):
    dealership_id: str
    weekly: list[WeeklyHours]
    holiday_exceptions: list[HolidayException]

    def on(self, day: date, department: Department) -> EffectiveHours:
        """Hours for one department on one date. An exception replaces the weekly row, and a
        department with no row for the day counts as closed rather than guessed at."""
        same_day = [item for item in self.holiday_exceptions if item.date == day]
        for item in same_day:
            if item.department == department:
                return EffectiveHours(
                    department=department,
                    date=day,
                    opens_at=item.opens_at,
                    closes_at=item.closes_at,
                    closed=item.closed,
                    holiday_label=item.label,
                    exception_listed=True,
                    other_departments_excepted=False,
                )
        row = next(
            (
                item
                for item in self.weekly
                if item.day_of_week == day.weekday() and item.department == department
            ),
            None,
        )
        return EffectiveHours(
            department=department,
            date=day,
            opens_at=row.opens_at if row else None,
            closes_at=row.closes_at if row else None,
            closed=row.closed if row else True,
            holiday_label=None,
            exception_listed=False,
            other_departments_excepted=bool(same_day),
        )


class ServiceType(Wire):
    id: str
    name: str
    description: str
    duration_minutes: PositiveInt
    price_from_pence: NonNegativeInt | None

    @property
    def price_on_request(self) -> bool:
        return self.price_from_pence is None


class Slot(Wire):
    id: str
    dealership_id: str
    dealership_name: str
    starts_at: AwareDatetime
    status: Literal["available"]

    @property
    def starts_at_local(self) -> datetime:
        return self.starts_at.astimezone(LONDON)


class TestDriveSlot(Slot):
    __test__ = False

    vehicle_id: str
    make: str
    model: str
    variant: str


class WorkshopSlot(Slot):
    service_type_id: str
    service_name: str
    duration_minutes: PositiveInt
    price_from_pence: NonNegativeInt | None


class FinanceInformation(Wire):
    notice: str
    minimum_age: PositiveInt


class PartExchangeInformation(Wire):
    estimate_notice: str


class BusinessInformation(Wire):
    finance: FinanceInformation
    part_exchange: PartExchangeInformation
    privacy_contact: str


class WorkshopBooking(Wire):
    """A customer's booking, without their contact details, registration, mileage, or notes."""

    id: str
    reference: str
    slot_id: str
    dealership_id: str
    service_type_id: str
    status: BookingStatus
    cancelled_at: AwareDatetime | None
    starts_at: AwareDatetime | None = None
    dealership_name: str | None = None
    dealership_address_line: str | None = None
    dealership_town: str | None = None
    dealership_postcode: str | None = None
    service_type_name: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


class TestDriveBooking(Wire):
    __test__ = False

    id: str
    reference: str
    slot_id: str
    dealership_id: str
    vehicle_id: str
    status: Literal["confirmed"]


class WorkshopRecord(WorkshopBooking):
    """Internal: the fields needed to tell whether an amendment was applied."""

    mileage: int
    notes: str | None
    idempotent_replay: bool = False


class TestDriveRecord(TestDriveBooking):
    __test__ = False

    idempotent_replay: bool = False


class CreatedRecord(Wire):
    id: str
    reference: str
    idempotent_replay: bool = False


class EnquiryRecord(CreatedRecord):
    status: Literal["received"]


class InterestRecord(CreatedRecord):
    status: Literal["registered"]


class CallbackRecord(CreatedRecord):
    status: Literal["requested"]


class MessageRecord(CreatedRecord):
    status: Literal["received"]


class ValuationRecord(CreatedRecord):
    status: Literal["estimated"]
    estimate_low_pence: NonNegativeInt
    estimate_high_pence: NonNegativeInt
    estimate_notice: str | None = None

    @model_validator(mode="after")
    def range_is_ordered(self) -> Self:
        if self.estimate_low_pence > self.estimate_high_pence:
            raise ValueError("estimate range is inverted")
        return self


class Contact(Wire):
    first_name: str
    last_name: str
    email: str
    phone: Phone


class WriteRequest(Wire):
    """A customer-confirmed write. It round-trips through JSON so the pending-action row can hold
    it, and a retry rebuilds exactly the same key and body (D-004)."""

    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"\S")

    def body(self) -> dict[str, Any]:
        """The platform's JSON body, which is the same every time for a stored request."""
        body = self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude={"idempotency_key", "contact", "slot"},
        )
        contact = getattr(self, "contact", None)
        if contact is not None:
            body |= contact.model_dump(mode="json", by_alias=True)
        slot = getattr(self, "slot", None)
        if slot is not None:
            body["slotId"] = slot.id
        return body


class SalesEnquiryRequest(WriteRequest):
    dealership_id: str
    vehicle_id: str | None = None
    enquiry_type: EnquiryType
    contact: Contact
    message: str = Field(min_length=5, max_length=2000)


class VehicleInterestRequest(WriteRequest):
    vehicle_id: str
    contact: Contact
    notes: str | None = None


class CallbackRequest(WriteRequest):
    dealership_id: str
    department: Department
    vehicle_id: str | None = None
    contact: Contact
    preferred_time: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)


class DealershipMessageRequest(WriteRequest):
    dealership_id: str
    department: MessageDepartment
    subject: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=5, max_length=2000)
    preferred_contact_method: ContactMethod
    contact: Contact


class PartExchangeRequest(WriteRequest):
    dealership_id: str
    registration: Registration
    mileage: int = Field(ge=0, le=1_000_000)
    condition: VehicleCondition
    contact: Contact


class TestDriveRequest(WriteRequest):
    """`vehicle_id` is the car the customer chose. The platform request has no vehicle field, so a
    stale slot would otherwise book a different car without any error."""

    __test__ = False

    slot: TestDriveSlot
    vehicle_id: str
    contact: Contact
    notes: str | None = Field(default=None, max_length=2000)

    def body(self) -> dict[str, Any]:
        body = super().body()
        del body["vehicleId"]
        return body


class WorkshopBookingRequest(WriteRequest):
    slot: WorkshopSlot
    registration: Registration
    mileage: int = Field(ge=0, le=1_000_000)
    contact: Contact
    notes: str | None = Field(default=None, max_length=2000)


class WorkshopLookupRequest(Wire):
    reference: str
    last_name: str
    registration: Registration
    phone: Phone

    def body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class AmendRequest(Wire):
    """Changes to a verified booking. The platform has no idempotency key for `PATCH`."""

    booking: WorkshopBooking
    target_slot: WorkshopSlot | None = None
    mileage: int | None = Field(default=None, ge=0, le=1_000_000)
    notes: str | None = None

    @model_validator(mode="after")
    def something_changes(self) -> Self:
        if self.target_slot is None and self.mileage is None and self.notes is None:
            raise ValueError("an amendment must change something")
        return self

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.target_slot is not None:
            body["slotId"] = self.target_slot.id
        if self.mileage is not None:
            body["mileage"] = self.mileage
        if self.notes is not None:
            body["notes"] = self.notes
        return body

    def applied_to(self, record: WorkshopRecord) -> bool:
        """Whether `record` already shows every requested change."""
        if self.target_slot is not None and record.slot_id != self.target_slot.id:
            return False
        if self.mileage is not None and record.mileage != self.mileage:
            return False
        return self.notes is None or record.notes == self.notes
