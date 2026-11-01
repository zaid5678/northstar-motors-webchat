"""Typed client for the dealership platform.

Every method returns a domain outcome (`outcomes.py`) and never raises for a platform result. What
a retry may do depends on what the platform guarantees for that call, which `_Mode` records:

- READ: nothing changes, so any transient failure is retried.
- KEYED_CREATE: the same key and body replay the original record, so a lost response is retried.
- SLOT_CLAIM: as KEYED_CREATE, except a 500. A slot released by cancellation fails this way every
  time while claiming `retryable: true` (D-007), so `retryable` is never trusted.
- UNKEYED_WRITE: `PATCH` and `DELETE` have no key, so a request that may have arrived is never
  resent. We re-read the booking instead (D-004). Only a failed connection is retried.

4xx responses are final in every mode, and so is a 2xx with a body we cannot read.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum, auto
from types import TracebackType
from typing import Any, Literal, Self, TypeVar, cast

import httpx
from pydantic import BaseModel, ValidationError

from . import outcomes as o
from .models import (
    LONDON,
    AmendRequest,
    AvailabilityRecord,
    BusinessInformation,
    CallbackRecord,
    CallbackRequest,
    Dealership,
    DealershipMessageRequest,
    EnquiryRecord,
    InterestRecord,
    Items,
    MessageRecord,
    Offer,
    OpeningHours,
    PartExchangeRequest,
    ProductType,
    SalesEnquiryRequest,
    ServiceType,
    Slot,
    TestDriveBooking,
    TestDriveRecord,
    TestDriveRequest,
    TestDriveSlot,
    ValuationRecord,
    Vehicle,
    VehicleDetails,
    VehiclePage,
    VehicleInterestRequest,
    VehicleSearch,
    Wire,
    WorkshopBooking,
    WorkshopBookingRequest,
    WorkshopLookupRequest,
    WorkshopRecord,
    WorkshopSlot,
    WriteRequest,
)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)
F = TypeVar("F", bound=o.Outcome)
LostReason = Literal["no_response", "server_error", "malformed_response"]
NO_REFUSALS: Mapping[str, o.WriteNotSent] = {}
TestDriveRefusal = o.VehicleReserved | o.VehicleUnavailable | o.SlotTaken | o.SlotUnbookable

SLOT_LIST_CAP = 100
MAX_STOCK_PAGES = 10
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# These fail before the request reaches the platform, so even an unkeyed write is safe to resend.
NOT_DELIVERED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Catalogue reads only. Availability, slots, and bookings are never cached, because a write's
# last-moment recheck must see the truth (W3).
VEHICLE_TTL = 30.0
REFERENCE_TTL = 300.0


class _Mode(Enum):
    READ = auto()
    KEYED_CREATE = auto()
    SLOT_CLAIM = auto()
    UNKEYED_WRITE = auto()


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.2
    max_delay: float = 1.0
    deadline: float = 12.0


@dataclass(frozen=True)
class _Exchange:
    """The last attempt, and whether any attempt may have reached the platform."""

    response: httpx.Response | None
    maybe_delivered: bool
    attempts: int
    detail: str


class _ErrorDetail(Wire):
    code: str
    message: str = ""
    field_errors: dict[str, str] = {}


class _ErrorBody(Wire):
    error: _ErrorDetail


def _error_of(response: httpx.Response) -> _ErrorDetail | None:
    try:
        return _ErrorBody.model_validate_json(response.content).error
    except ValidationError:
        return None


def _encode(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def _is_gateway_failure(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


def _date_params(date_from: date | None, date_to: date | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if date_from is not None:
        params["dateFrom"] = date_from.isoformat()
    if date_to is not None:
        params["dateTo"] = date_to.isoformat()
    return params


class DealershipClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        retry: RetryPolicy = RetryPolicy(),
        timeout: httpx.Timeout = httpx.Timeout(5.0, connect=2.0),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
        transport: httpx.AsyncBaseTransport | None = None,
        max_concurrent: int = 10,
        cache_size: int = 256,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._auth = {"X-API-Key": api_key}
        self._retry = retry
        self._sleep = sleep
        self._now = now
        self._monotonic = monotonic
        self._rng = rng
        # The platform has a listen backlog of 5 and refuses bursts, so bursts queue here (D-024).
        self._gate = asyncio.Semaphore(max_concurrent)
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, BaseModel]] = OrderedDict()
        self._cache_size = cache_size
        self._filling: dict[tuple[Any, ...], asyncio.Lock] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # Transport.

    def _should_retry(self, mode: _Mode, exchange: _Exchange) -> bool:
        response = exchange.response
        if response is None:
            return mode is not _Mode.UNKEYED_WRITE or not exchange.maybe_delivered
        if mode is _Mode.UNKEYED_WRITE or response.status_code not in RETRY_STATUSES:
            return False
        return not (mode is _Mode.SLOT_CLAIM and response.status_code == 500)

    def _delay(self, attempt: int, response: httpx.Response | None) -> float:
        policy = self._retry
        if response is not None and "Retry-After" in response.headers:
            try:
                return min(float(str(response.headers["Retry-After"])), policy.max_delay)
            except ValueError:
                pass
        delay = min(policy.base_delay * 2.0 ** (attempt - 1), policy.max_delay)
        return delay * (1 + 0.25 * self._rng())

    async def _exchange(
        self,
        method: str,
        path: str,
        mode: _Mode,
        *,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _Exchange:
        started = self._monotonic()
        attempt = 0
        maybe_delivered = False
        while True:
            attempt += 1
            response: httpx.Response | None = None
            try:
                async with self._gate:  # Released before any backoff, so a retry holds no place.
                    response = await self._http.request(
                        method, path, params=params, content=content, headers=headers
                    )
                maybe_delivered = True
                detail = f"HTTP {response.status_code}"
            except NOT_DELIVERED as error:
                detail = type(error).__name__
            except httpx.TransportError as error:
                maybe_delivered = True
                detail = type(error).__name__
            exchange = _Exchange(response, maybe_delivered, attempt, detail)
            if attempt >= self._retry.max_attempts or not self._should_retry(mode, exchange):
                return exchange
            delay = self._delay(attempt, response)
            if self._monotonic() - started + delay >= self._retry.deadline:
                return exchange
            await self._sleep(delay)

    # Reads.

    def _read_failure(
        self, exchange: _Exchange
    ) -> o.PlatformUnavailable | o.NotFound | o.IntegrationFault:
        response = exchange.response
        if response is None or _is_gateway_failure(response):
            return o.PlatformUnavailable(attempts=exchange.attempts, detail=exchange.detail)
        error = _error_of(response)
        if response.status_code == 404:
            return o.NotFound(code=error.code if error else "NOT_FOUND")
        return o.IntegrationFault(status=response.status_code, code=error.code if error else None)

    def _parse(self, operation: str, model: type[T], response: httpx.Response) -> T | o.MalformedResponse:
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            # Only field names are kept: the error's input could be a customer's details.
            fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
            return o.MalformedResponse(operation=operation, detail=f"invalid fields: {fields}")

    async def _fetch(
        self,
        operation: str,
        path: str,
        model: type[T],
        *,
        params: Mapping[str, str] | None = None,
        auth: bool = False,
    ) -> T | o.NotFound | o.ReadFailure:
        return await self._get(operation, path, model, params=params, auth=auth)

    def _cached(self, key: tuple[Any, ...], model: type[T]) -> T | None:
        entry = self._cache.get(key)
        if entry is None or self._monotonic() >= entry[0]:
            return None
        return cast(T, entry[1])

    async def _fetch_cached(
        self,
        operation: str,
        path: str,
        model: type[T],
        ttl: float,
        *,
        params: Mapping[str, str] | None = None,
        fresh: bool = False,
    ) -> T | o.NotFound | o.ReadFailure:
        """Only one caller fetches a missing entry, so a burst of misses makes one request."""
        key = (path, tuple(sorted((params or {}).items())))
        if not fresh:
            hit = self._cached(key, model)
            if hit is not None:
                return hit
        lock = self._filling.setdefault(key, asyncio.Lock())
        async with lock:
            if not fresh:
                hit = self._cached(key, model)
                if hit is not None:
                    return hit
            found = await self._get(operation, path, model, params=params)
            if isinstance(found, model):
                self._cache[key] = (self._monotonic() + ttl, found)
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        if not lock.locked():
            self._filling.pop(key, None)
        return found

    async def _get(
        self,
        operation: str,
        path: str,
        model: type[T],
        *,
        params: Mapping[str, str] | None = None,
        auth: bool = False,
    ) -> T | o.NotFound | o.ReadFailure:
        exchange = await self._exchange(
            "GET", path, _Mode.READ, params=params, headers=self._auth if auth else None
        )
        response = exchange.response
        if response is None or not response.is_success:
            return self._read_failure(exchange)
        return self._parse(operation, model, response)

    async def _list(
        self,
        operation: str,
        path: str,
        item: type[T],
        *,
        params: Mapping[str, str] | None = None,
        ttl: float | None = None,
    ) -> list[T] | o.ReadFailure:
        model = Items[item]  # type: ignore[valid-type]
        page = (
            await self._fetch(operation, path, model, params=params)
            if ttl is None
            else await self._fetch_cached(operation, path, model, ttl, params=params)
        )
        if isinstance(page, o.NotFound):
            return o.IntegrationFault(status=404, code=page.code)
        if isinstance(page, Items):
            return list(page.items)
        return page

    def _today(self) -> date:
        return self._now().astimezone(LONDON).date()

    def _is_past(self, slot: Slot) -> bool:
        return slot.starts_at <= self._now()

    async def is_healthy(self) -> bool:
        """A single attempt with no retries, for the chat's "unavailable" state."""
        try:
            response = await self._http.get("/health")
            return response.is_success and bool(response.json().get("status") == "ok")
        except (httpx.TransportError, ValueError, AttributeError):
            return False

    async def list_dealerships(self) -> o.DealershipsListed | o.ReadFailure:
        found = await self._list(
            "list_dealerships", "/api/dealerships", Dealership, ttl=REFERENCE_TTL
        )
        return found if not isinstance(found, list) else o.DealershipsListed(dealerships=found)

    async def get_dealership(self, dealership_id: str) -> o.DealershipFound | o.NotFound | o.ReadFailure:
        found = await self._fetch_cached(
            "get_dealership", f"/api/dealerships/{dealership_id}", Dealership, REFERENCE_TTL
        )
        return o.DealershipFound(dealership=found) if isinstance(found, Dealership) else found

    async def get_opening_hours(
        self, dealership_id: str, *, fresh: bool = False
    ) -> o.OpeningHoursFound | o.NotFound | o.ReadFailure:
        """`fresh` skips the cache, for checks that a write depends on."""
        found = await self._fetch_cached(
            "get_opening_hours",
            f"/api/dealerships/{dealership_id}/opening-hours",
            OpeningHours,
            REFERENCE_TTL,
            fresh=fresh,
        )
        return o.OpeningHoursFound(hours=found) if isinstance(found, OpeningHours) else found

    async def list_service_types(self) -> o.ServiceTypesListed | o.ReadFailure:
        found = await self._list(
            "list_service_types", "/api/service-types", ServiceType, ttl=REFERENCE_TTL
        )
        return found if not isinstance(found, list) else o.ServiceTypesListed(service_types=found)

    async def get_business_information(self) -> o.BusinessInformationFound | o.ReadFailure:
        found = await self._fetch_cached(
            "get_business_information",
            "/api/business-information",
            BusinessInformation,
            REFERENCE_TTL,
        )
        if isinstance(found, o.NotFound):
            return o.IntegrationFault(status=404, code=found.code)
        if isinstance(found, BusinessInformation):
            return o.BusinessInformationFound(information=found)
        return found

    async def search_vehicles(self, search: VehicleSearch) -> o.VehiclesListed | o.ReadFailure:
        found = await self._fetch_cached(
            "search_vehicles", "/api/vehicles", VehiclePage, VEHICLE_TTL, params=search.to_params()
        )
        if isinstance(found, o.NotFound):
            return o.IntegrationFault(status=404, code=found.code)
        if isinstance(found, VehiclePage):
            return o.VehiclesListed(vehicles=found.items, pagination=found.pagination)
        return found

    async def _all_vehicles(self, search: VehicleSearch) -> list[Vehicle] | o.ReadFailure:
        vehicles: list[Vehicle] = []
        for number in range(1, MAX_STOCK_PAGES + 1):
            listed = await self.search_vehicles(
                search.model_copy(update={"page": number, "page_size": 50})
            )
            if not isinstance(listed, o.VehiclesListed):
                return listed
            vehicles.extend(listed.vehicles)
            if number >= listed.pagination.total_pages:
                break
        return vehicles

    async def search_stock(
        self, search: VehicleSearch, *, include_sold: bool = False
    ) -> o.StockSearched | o.ReadFailure:
        """Reads every matching page, grouped by state. Any failed query fails the whole search,
        because a partial answer would look like cars that do not exist."""
        states = [search.availability] if search.availability else ["available", "reserved"]
        if include_sold and search.availability is None:
            states.append("sold")
        groups: dict[str, list[Vehicle]] = {"available": [], "reserved": [], "sold": []}
        for state in states:
            found = await self._all_vehicles(search.model_copy(update={"availability": state}))
            if not isinstance(found, list):
                return found
            groups[state] = found

        price_on_request: list[Vehicle] = []
        if search.min_price_pence is not None or search.max_price_pence is not None:
            for state in states:
                if state == "sold":
                    continue
                unpriced = search.model_copy(
                    update={"availability": state, "min_price_pence": None, "max_price_pence": None}
                )
                found = await self._all_vehicles(unpriced)
                if not isinstance(found, list):
                    return found
                price_on_request.extend(item for item in found if item.price_on_request)
        return o.StockSearched(
            available=groups["available"],
            reserved=groups["reserved"],
            sold=groups["sold"],
            price_on_request=price_on_request,
        )

    async def get_vehicle(self, vehicle_id: str) -> o.VehicleFound | o.NotFound | o.ReadFailure:
        found = await self._fetch_cached(
            "get_vehicle", f"/api/vehicles/{vehicle_id}", VehicleDetails, VEHICLE_TTL
        )
        return o.VehicleFound(vehicle=found) if isinstance(found, VehicleDetails) else found

    async def get_vehicle_status(self, vehicle_id: str) -> o.VehicleStatus | o.NotFound | o.ReadFailure:
        found = await self._fetch(
            "get_vehicle_status", f"/api/vehicles/{vehicle_id}/availability", AvailabilityRecord
        )
        if not isinstance(found, AvailabilityRecord):
            return found
        bookable = found.can_book_test_drive and found.availability == "available"
        slot = found.next_test_drive_slot if bookable else None
        return o.VehicleStatus(
            vehicle_id=found.vehicle_id,
            availability=found.availability,
            can_enquire=found.can_enquire,
            can_book_test_drive=bookable,
            can_register_interest=found.can_register_interest,
            next_slot_id=slot.id if slot else None,
            next_slot_starts_at=slot.starts_at if slot else None,
        )

    async def list_offers(
        self, *, make: str | None = None, product_type: ProductType | None = None
    ) -> o.OffersListed | o.ReadFailure:
        params = {"make": make, "productType": product_type}
        found = await self._list(
            "list_offers",
            "/api/offers",
            Offer,
            params={key: value for key, value in params.items() if value},
            ttl=REFERENCE_TTL,
        )
        if not isinstance(found, list):
            return found
        live = [item for item in found if item.expires_on >= self._today()]
        return o.OffersListed(offers=live, expired_hidden=len(found) - len(live))

    async def get_offer(self, offer_id: str) -> o.OfferFound | o.OfferExpired | o.NotFound | o.ReadFailure:
        found = await self._fetch_cached("get_offer", f"/api/offers/{offer_id}", Offer, REFERENCE_TTL)
        if not isinstance(found, Offer):
            return found
        if found.expires_on < self._today():
            return o.OfferExpired(offer_id=offer_id)
        return o.OfferFound(offer=found)

    async def find_test_drive_slots(
        self, vehicle_id: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> o.TestDriveSlotsFound | o.TestDriveNotBookable | o.NotFound | o.ReadFailure:
        """The platform lists slots for reserved and sold cars, so state is checked first."""
        status = await self.get_vehicle_status(vehicle_id)
        if not isinstance(status, o.VehicleStatus):
            return status
        if not status.can_book_test_drive:
            return o.TestDriveNotBookable(
                vehicle_id=vehicle_id,
                availability=status.availability,
                can_register_interest=status.can_register_interest,
            )
        found = await self._list(
            "find_test_drive_slots",
            "/api/test-drive-slots",
            TestDriveSlot,
            params={"vehicleId": vehicle_id, **_date_params(date_from, date_to)},
        )
        if not isinstance(found, list):
            return found
        for_this_car = [slot for slot in found if slot.vehicle_id == vehicle_id]
        upcoming = [slot for slot in for_this_car if not self._is_past(slot)]
        return o.TestDriveSlotsFound(
            slots=upcoming,
            past_hidden=len(for_this_car) - len(upcoming),
            truncated=len(found) >= SLOT_LIST_CAP,
        )

    async def find_workshop_slots(
        self,
        *,
        dealership_id: str | None = None,
        service_type_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> o.WorkshopSlotsFound | o.ReadFailure:
        params = _date_params(date_from, date_to)
        if dealership_id:
            params["dealershipId"] = dealership_id
        if service_type_id:
            params["serviceTypeId"] = service_type_id
        found = await self._list(
            "find_workshop_slots", "/api/workshop-availability", WorkshopSlot, params=params
        )
        if not isinstance(found, list):
            return found
        upcoming = [slot for slot in found if not self._is_past(slot)]
        return o.WorkshopSlotsFound(
            slots=upcoming,
            past_hidden=len(found) - len(upcoming),
            truncated=len(found) >= SLOT_LIST_CAP,
        )

    async def get_workshop_booking(
        self, booking_id: str
    ) -> o.WorkshopBookingFound | o.NotFound | o.ReadFailure:
        found = await self._fetch(
            "get_workshop_booking",
            f"/api/workshop-bookings/{booking_id}",
            WorkshopBooking,
            auth=True,
        )
        return o.WorkshopBookingFound(booking=found) if isinstance(found, WorkshopBooking) else found

    async def get_test_drive_booking(
        self, booking_id: str
    ) -> o.TestDriveBookingFound | o.NotFound | o.ReadFailure:
        found = await self._fetch(
            "get_test_drive_booking",
            f"/api/test-drive-bookings/{booking_id}",
            TestDriveBooking,
            auth=True,
        )
        return o.TestDriveBookingFound(booking=found) if isinstance(found, TestDriveBooking) else found

    async def find_workshop_booking(
        self, lookup: WorkshopLookupRequest
    ) -> o.WorkshopBookingFound | o.BookingNotMatched | o.InvalidDetails | o.ReadFailure:
        """A `POST` that changes nothing, so it retries as a read. Attempt limits belong to the
        caller (D-008): a retry here repeats one attempt, it does not guess a new one."""
        exchange = await self._exchange(
            "POST",
            "/api/workshop-bookings/lookup",
            _Mode.READ,
            content=_encode(lookup.body()),
            headers={**self._auth, "Content-Type": "application/json"},
        )
        response = exchange.response
        if response is not None and response.is_success:
            found = self._parse("find_workshop_booking", WorkshopBooking, response)
            return o.WorkshopBookingFound(booking=found) if isinstance(found, WorkshopBooking) else found
        if response is not None:
            error = _error_of(response)
            if error and error.code == "BOOKING_NOT_FOUND":
                return o.BookingNotMatched()
            if response.status_code == 422:
                return o.InvalidDetails(
                    message=error.message if error else "",
                    field_errors=error.field_errors if error else {},
                )
        failure = self._read_failure(exchange)
        if isinstance(failure, o.NotFound):
            return o.IntegrationFault(status=404, code=failure.code)
        return failure

    # Writes.

    def _write_failure(
        self,
        operation: str,
        exchange: _Exchange,
        *,
        refusals: Mapping[str, F],
        key: str | None = None,
    ) -> F | o.WriteFailure:
        response = exchange.response
        if response is None:
            if not exchange.maybe_delivered:
                return o.WriteNotSent(operation=operation, attempts=exchange.attempts)
            return self._unknown(operation, "no_response", exchange, key)
        if _is_gateway_failure(response):
            return self._unknown(operation, "server_error", exchange, key)
        error = _error_of(response)
        if error and error.code in refusals:
            return refusals[error.code]
        if response.status_code == 422:
            return o.InvalidDetails(
                message=error.message if error else "",
                field_errors=error.field_errors if error else {},
            )
        if response.status_code == 404:
            return o.NotFound(code=error.code if error else "NOT_FOUND")
        return o.IntegrationFault(status=response.status_code, code=error.code if error else None)

    def _unknown(
        self, operation: str, reason: LostReason, exchange: _Exchange, key: str | None
    ) -> o.WriteOutcomeUnknown:
        return o.WriteOutcomeUnknown(
            operation=operation, reason=reason, attempts=exchange.attempts, idempotency_key=key
        )

    async def _create(
        self,
        operation: str,
        path: str,
        request: WriteRequest,
        record: type[R],
        *,
        refusals: Mapping[str, F],
        mode: _Mode = _Mode.KEYED_CREATE,
        on_server_error: F | None = None,
    ) -> R | F | o.WriteFailure:
        key = request.idempotency_key
        exchange = await self._exchange(
            "POST",
            path,
            mode,
            content=_encode(request.body()),
            headers={**self._auth, "Idempotency-Key": key, "Content-Type": "application/json"},
        )
        response = exchange.response
        if response is not None and response.is_success:
            parsed = self._parse(operation, record, response)
            if isinstance(parsed, o.MalformedResponse):
                return self._unknown(operation, "malformed_response", exchange, key)
            return parsed
        if response is not None and response.status_code == 500 and on_server_error:
            return on_server_error
        return self._write_failure(operation, exchange, key=key, refusals=refusals)

    async def send_enquiry(self, request: SalesEnquiryRequest) -> o.EnquiryReceived | o.WriteFailure:
        found = await self._create(
            "send_enquiry", "/api/sales-enquiries", request, EnquiryRecord, refusals=NO_REFUSALS
        )
        if not isinstance(found, EnquiryRecord):
            return found
        return o.EnquiryReceived(
            id=found.id, reference=found.reference, replayed=found.idempotent_replay
        )

    async def register_interest(
        self, request: VehicleInterestRequest
    ) -> o.InterestRegistered | o.InterestNotAccepted | o.WriteFailure:
        found = await self._create(
            "register_interest",
            "/api/vehicle-interests",
            request,
            InterestRecord,
            refusals={"VEHICLE_NOT_RESERVED": o.InterestNotAccepted(vehicle_id=request.vehicle_id)},
        )
        if not isinstance(found, InterestRecord):
            return found
        return o.InterestRegistered(
            id=found.id, reference=found.reference, replayed=found.idempotent_replay
        )

    async def request_callback(self, request: CallbackRequest) -> o.CallbackRequested | o.WriteFailure:
        found = await self._create(
            "request_callback",
            "/api/callback-requests",
            request,
            CallbackRecord,
            refusals=NO_REFUSALS,
        )
        if not isinstance(found, CallbackRecord):
            return found
        return o.CallbackRequested(
            id=found.id, reference=found.reference, replayed=found.idempotent_replay
        )

    async def leave_message(self, request: DealershipMessageRequest) -> o.MessageReceived | o.WriteFailure:
        found = await self._create(
            "leave_message",
            "/api/dealership-messages",
            request,
            MessageRecord,
            refusals=NO_REFUSALS,
        )
        if not isinstance(found, MessageRecord):
            return found
        return o.MessageReceived(
            id=found.id, reference=found.reference, replayed=found.idempotent_replay
        )

    async def request_estimate(
        self, request: PartExchangeRequest
    ) -> o.EstimateGiven | o.EstimateWithoutNotice | o.WriteFailure:
        found = await self._create(
            "request_estimate",
            "/api/part-exchange-valuations",
            request,
            ValuationRecord,
            refusals=NO_REFUSALS,
        )
        if not isinstance(found, ValuationRecord):
            return found
        notice = found.estimate_notice
        if notice is None:
            # Replays drop the notice, and an estimate must never appear without one.
            information = await self.get_business_information()
            if not isinstance(information, o.BusinessInformationFound):
                return o.EstimateWithoutNotice(
                    id=found.id, reference=found.reference, replayed=found.idempotent_replay
                )
            notice = information.information.part_exchange.estimate_notice
        return o.EstimateGiven(
            id=found.id,
            reference=found.reference,
            estimate_low_pence=found.estimate_low_pence,
            estimate_high_pence=found.estimate_high_pence,
            notice=notice,
            replayed=found.idempotent_replay,
        )

    async def book_test_drive(
        self, request: TestDriveRequest
    ) -> (
        o.TestDriveBooked
        | o.VehicleReserved
        | o.VehicleUnavailable
        | o.SlotTaken
        | o.SlotUnbookable
        | o.SlotInPast
        | o.SlotVehicleMismatch
        | o.BookingMismatch
        | o.WriteFailure
    ):
        slot = request.slot
        if slot.vehicle_id != request.vehicle_id:
            return o.SlotVehicleMismatch(
                slot_id=slot.id,
                expected_vehicle_id=request.vehicle_id,
                slot_vehicle_id=slot.vehicle_id,
            )
        if self._is_past(slot):
            return o.SlotInPast(slot_id=slot.id)
        refusals: dict[str, TestDriveRefusal] = {
            "VEHICLE_RESERVED": o.VehicleReserved(vehicle_id=request.vehicle_id),
            "VEHICLE_UNAVAILABLE": o.VehicleUnavailable(vehicle_id=request.vehicle_id),
            "SLOT_UNAVAILABLE": o.SlotTaken(slot_id=slot.id),
        }
        found = await self._create(
            "book_test_drive",
            "/api/test-drive-bookings",
            request,
            TestDriveRecord,
            mode=_Mode.SLOT_CLAIM,
            refusals=refusals,
            on_server_error=o.SlotUnbookable(slot_id=slot.id),
        )
        if not isinstance(found, TestDriveRecord):
            return found
        if found.vehicle_id != request.vehicle_id:
            return o.BookingMismatch(
                id=found.id,
                reference=found.reference,
                expected_vehicle_id=request.vehicle_id,
                booked_vehicle_id=found.vehicle_id,
            )
        return o.TestDriveBooked(
            id=found.id,
            reference=found.reference,
            slot_id=found.slot_id,
            vehicle_id=found.vehicle_id,
            dealership_id=found.dealership_id,
            dealership_name=slot.dealership_name,
            starts_at=slot.starts_at,
            replayed=found.idempotent_replay,
        )

    async def book_workshop(
        self, request: WorkshopBookingRequest
    ) -> (
        o.WorkshopBooked
        | o.SlotTaken
        | o.SlotUnbookable
        | o.SlotInPast
        | o.BookingNoLongerActive
        | o.WriteFailure
    ):
        slot = request.slot
        if self._is_past(slot):
            return o.SlotInPast(slot_id=slot.id)
        refusals: dict[str, o.SlotTaken | o.SlotUnbookable] = {
            "SLOT_UNAVAILABLE": o.SlotTaken(slot_id=slot.id)
        }
        found = await self._create(
            "book_workshop",
            "/api/workshop-bookings",
            request,
            WorkshopRecord,
            mode=_Mode.SLOT_CLAIM,
            refusals=refusals,
            on_server_error=o.SlotUnbookable(slot_id=slot.id),
        )
        if not isinstance(found, WorkshopRecord):
            return found
        if found.is_cancelled:
            return o.BookingNoLongerActive(id=found.id, reference=found.reference)
        return o.WorkshopBooked(
            id=found.id,
            reference=found.reference,
            slot_id=found.slot_id,
            dealership_id=found.dealership_id,
            dealership_name=slot.dealership_name,
            service_type_id=found.service_type_id,
            service_name=slot.service_name,
            starts_at=slot.starts_at,
            replayed=found.idempotent_replay,
        )

    def _amended(self, request: AmendRequest, record: WorkshopRecord, *, reconciled: bool) -> o.BookingAmended:
        before = request.booking
        return o.BookingAmended(
            id=record.id,
            reference=record.reference,
            slot_id=record.slot_id,
            dealership_id=record.dealership_id,
            service_type_id=record.service_type_id,
            starts_at=request.target_slot.starts_at if request.target_slot else None,
            site_changed=record.dealership_id != before.dealership_id,
            service_changed=record.service_type_id != before.service_type_id,
            reconciled=reconciled,
        )

    async def _reread(self, operation: str, path: str) -> WorkshopRecord | None:
        found = await self._fetch(operation, path, WorkshopRecord, auth=True)
        return found if isinstance(found, WorkshopRecord) else None

    @staticmethod
    def _lost_reason(exchange: _Exchange, *, malformed: bool = False) -> LostReason:
        if malformed:
            return "malformed_response"
        return "no_response" if exchange.response is None else "server_error"

    async def amend_workshop_booking(
        self, request: AmendRequest
    ) -> (
        o.BookingAmended
        | o.AmendNotApplied
        | o.BookingAlreadyCancelled
        | o.SlotTaken
        | o.SlotUnbookable
        | o.SlotInPast
        | o.WriteFailure
    ):
        operation = "amend_workshop_booking"
        booking_id = request.booking.id
        slot = request.target_slot
        if slot is not None and self._is_past(slot):
            return o.SlotInPast(slot_id=slot.id)
        path = f"/api/workshop-bookings/{booking_id}"
        exchange = await self._exchange(
            "PATCH",
            path,
            _Mode.UNKEYED_WRITE,
            content=_encode(request.body()),
            headers={**self._auth, "Content-Type": "application/json"},
        )
        response = exchange.response
        malformed = False
        if response is not None and response.is_success:
            parsed = self._parse(operation, WorkshopRecord, response)
            if isinstance(parsed, WorkshopRecord):
                return self._amended(request, parsed, reconciled=False)
            malformed = True
        elif not exchange.maybe_delivered or (
            response is not None and not _is_gateway_failure(response)
        ):
            refusals: dict[str, o.BookingAlreadyCancelled | o.SlotTaken] = {
                "BOOKING_CANCELLED": o.BookingAlreadyCancelled(booking_id=booking_id)
            }
            if slot is not None:
                refusals["SLOT_UNAVAILABLE"] = o.SlotTaken(slot_id=slot.id)
            return self._write_failure(operation, exchange, refusals=refusals)

        record = await self._reread(operation, path)
        if record is None:
            return self._unknown(operation, self._lost_reason(exchange, malformed=malformed), exchange, None)
        if record.is_cancelled:
            return o.BookingAlreadyCancelled(booking_id=booking_id)
        if request.applied_to(record):
            return self._amended(request, record, reconciled=True)
        if slot is not None and response is not None and response.status_code == 500:
            return o.SlotUnbookable(slot_id=slot.id)
        return o.AmendNotApplied(booking_id=booking_id)

    async def cancel_workshop_booking(
        self, booking_id: str
    ) -> o.BookingCancelled | o.CancelNotApplied | o.WriteFailure | o.ReadFailure:
        """Reads first, because a repeat `DELETE` looks exactly like a first one."""
        operation = "cancel_workshop_booking"
        path = f"/api/workshop-bookings/{booking_id}"
        before = await self._fetch(operation, path, WorkshopRecord, auth=True)
        if not isinstance(before, WorkshopRecord):
            return before
        if before.is_cancelled:
            return self._cancelled(before, already_cancelled=True, reconciled=False)

        exchange = await self._exchange(
            "DELETE", path, _Mode.UNKEYED_WRITE, headers=self._auth
        )
        response = exchange.response
        malformed = False
        if response is not None and response.is_success:
            parsed = self._parse(operation, WorkshopRecord, response)
            if isinstance(parsed, WorkshopRecord) and parsed.is_cancelled:
                return self._cancelled(parsed, already_cancelled=False, reconciled=False)
            malformed = True
        elif not exchange.maybe_delivered or (
            response is not None and not _is_gateway_failure(response)
        ):
            return self._write_failure(operation, exchange, refusals=NO_REFUSALS)

        record = await self._reread(operation, path)
        if record is None:
            return self._unknown(operation, self._lost_reason(exchange, malformed=malformed), exchange, None)
        if record.is_cancelled:
            return self._cancelled(record, already_cancelled=False, reconciled=True)
        return o.CancelNotApplied(booking_id=booking_id)

    async def amendment_applied(self, request: AmendRequest) -> bool | o.NotFound | o.ReadFailure:
        """Whether a booking already shows this amendment, without resending it."""
        found = await self._fetch(
            "amendment_applied",
            f"/api/workshop-bookings/{request.booking.id}",
            WorkshopRecord,
            auth=True,
        )
        return request.applied_to(found) if isinstance(found, WorkshopRecord) else found

    @staticmethod
    def _cancelled(record: WorkshopRecord, *, already_cancelled: bool, reconciled: bool) -> o.BookingCancelled:
        return o.BookingCancelled(
            id=record.id,
            reference=record.reference,
            slot_id=record.slot_id,
            already_cancelled=already_cancelled,
            reconciled=reconciled,
        )
