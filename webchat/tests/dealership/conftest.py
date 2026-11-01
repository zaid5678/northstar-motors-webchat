"""Payloads copy real platform responses, including the personal fields we must not keep."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership.models import (
    Contact,
    TestDriveSlot,
    WorkshopSlot,
)

BASE = "http://platform.test"
API_KEY = "test-key-do-not-log"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
FUTURE = "2026-11-05T09:00:00Z"
PAST = "2026-10-01T09:00:00Z"
PII = {
    "firstName": "Jamie",
    "lastName": "Taylor",
    "email": "jamie.taylor@example.com",
    "phone": "07700900123",
}


def vehicle(vehicle_id: str = "veh-001", **overrides: Any) -> dict[str, Any]:
    return {
        "id": vehicle_id,
        "dealershipId": "northstar-manchester",
        "make": "BMW",
        "model": "1 Series",
        "variant": "118i M Sport",
        "year": 2020,
        "pricePence": 1850000,
        "monthlyPricePence": 27380,
        "mileage": 3500,
        "fuelType": "Petrol",
        "transmission": "Automatic",
        "colour": "Thundernight Purple",
        "bodyStyle": "Hatchback",
        "availability": "available",
        "registration": "NS01 CAR",
        "description": "A carefully selected 2020 BMW 1 Series.",
        "updatedAt": "2026-11-01T11:51:55Z",
        "dealershipName": "Northstar Manchester",
        "dealershipTown": "Manchester",
        "images": ["/assets/vehicles/veh-001.jpg"],
        **overrides,
    }


def page(items: list[dict[str, Any]], page_number: int = 1, total_pages: int = 1) -> dict[str, Any]:
    return {
        "items": items,
        "pagination": {
            "page": page_number,
            "pageSize": 50,
            "totalItems": len(items),
            "totalPages": total_pages,
        },
    }


def availability(state: str = "available", **overrides: Any) -> dict[str, Any]:
    return {
        "vehicleId": "veh-001",
        "availability": state,
        "canEnquire": True,
        "canBookTestDrive": state == "available",
        "canRegisterInterest": state == "reserved",
        "nextTestDriveSlot": {"id": "td-slot-0001", "startsAt": FUTURE},
        **overrides,
    }


def td_slot(slot_id: str = "td-slot-0001", **overrides: Any) -> dict[str, Any]:
    return {
        "id": slot_id,
        "dealershipId": "northstar-manchester",
        "vehicleId": "veh-001",
        "startsAt": FUTURE,
        "status": "available",
        "dealershipName": "Northstar Manchester",
        "make": "BMW",
        "model": "1 Series",
        "variant": "118i M Sport",
        **overrides,
    }


def ws_slot(slot_id: str = "ws-slot-0002", **overrides: Any) -> dict[str, Any]:
    return {
        "id": slot_id,
        "dealershipId": "northstar-manchester",
        "serviceTypeId": "mot",
        "startsAt": FUTURE,
        "status": "available",
        "dealershipName": "Northstar Manchester",
        "serviceName": "MOT",
        "durationMinutes": 60,
        "priceFromPence": 5499,
        **overrides,
    }


def offer(offer_id: str = "offer-01", **overrides: Any) -> dict[str, Any]:
    return {
        "id": offer_id,
        "make": "BMW",
        "model": "1 Series",
        "title": "BMW 1 Series 118i M Sport",
        "productType": "PCP",
        "monthlyPricePence": 36400,
        "upfrontPaymentPence": 324900,
        "apr": 5.9,
        "termMonths": 48,
        "annualMileage": 8000,
        "expiresOn": "2027-01-30",
        "description": "Published Northstar Motors new-car offer.",
        "image": "/assets/vehicles/veh-001.jpg",
        **overrides,
    }


def dealership(dealership_id: str = "northstar-manchester") -> dict[str, Any]:
    return {
        "id": dealership_id,
        "name": "Northstar Manchester",
        "town": "Manchester",
        "postcode": "M20 2YY",
        "addressLine": "101 Kingsway",
        "phone": "0161 555 0101",
        "email": "manchester@northstarmotors.example",
        "latitude": 53.424,
        "longitude": -2.231,
        "brands": ["BMW", "MINI", "Volvo"],
    }


def business_information() -> dict[str, Any]:
    return {
        "organisation": "Northstar Motors",
        "currency": "GBP",
        "market": "United Kingdom",
        "finance": {"notice": "Finance is subject to status.", "minimumAge": 18},
        "partExchange": {"estimateNotice": "Online estimates are indicative."},
        "privacyContact": "privacy@northstarmotors.example",
    }


def workshop_record(status: str = "confirmed", **overrides: Any) -> dict[str, Any]:
    return {
        "id": "wsb-seeded-001",
        "reference": "WORK-10001",
        "slotId": "ws-slot-0001",
        "dealershipId": "northstar-manchester",
        "serviceTypeId": "full-service",
        "registration": "AB12 CDE",
        "mileage": 42150,
        **PII,
        "notes": "Customer will wait at the dealership.",
        "status": status,
        "createdAt": "2026-11-01T11:52:17Z",
        "updatedAt": "2026-11-01T11:52:17Z",
        "cancelledAt": "2026-11-01T11:53:00Z" if status == "cancelled" else None,
        **overrides,
    }


def lookup_record(status: str = "confirmed", **overrides: Any) -> dict[str, Any]:
    return workshop_record(
        status,
        startsAt="2026-11-02T09:00:00Z",
        dealershipName="Northstar Manchester",
        dealershipAddressLine="101 Kingsway",
        dealershipTown="Manchester",
        dealershipPostcode="M20 2YY",
        serviceTypeName="Full service",
        **overrides,
    )


def error(
    status: int,
    code: str,
    *,
    retryable: bool = False,
    field_errors: dict[str, str] | None = None,
    message: str = "Rejected.",
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": code,
                "message": message,
                "fieldErrors": field_errors or {},
                "retryable": retryable,
            }
        },
    )


def internal_error() -> httpx.Response:
    return error(500, "INTERNAL_ERROR", retryable=True, message="Could not process the request.")


def contact() -> Contact:
    return Contact(
        first_name="Probe", last_name="Tester", email="probe@example.com", phone="07700900999"
    )


def drive_slot(**overrides: Any) -> TestDriveSlot:
    return TestDriveSlot.model_validate(td_slot(**overrides))


def workshop_slot(**overrides: Any) -> WorkshopSlot:
    return WorkshopSlot.model_validate(ws_slot(**overrides))


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def client(sleeps: list[float]) -> AsyncIterator[DealershipClient]:
    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    async with DealershipClient(
        BASE, API_KEY, sleep=record_sleep, now=lambda: NOW, rng=lambda: 0.0
    ) as instance:
        yield instance
