"""Reads: 200 responses that mean less than they appear to (PLATFORM-NOTES section 2)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import OpeningHours, VehicleSearch

from .conftest import (
    PAST,
    availability,
    business_information,
    dealership,
    offer,
    page,
    td_slot,
    vehicle,
    ws_slot,
    workshop_slot,
)


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


class TestAuthentication:
    async def test_public_reads_do_not_send_the_api_key(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.get("/api/dealerships").mock(ok({"items": [dealership()]}))
        await client.list_dealerships()
        assert "x-api-key" not in route.calls.last.request.headers

    async def test_saved_record_reads_send_the_api_key(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        from .conftest import API_KEY, workshop_record

        route = api.get("/api/workshop-bookings/wsb-1").mock(ok(workshop_record()))
        await client.get_workshop_booking("wsb-1")
        assert route.calls.last.request.headers["x-api-key"] == API_KEY

    def test_the_api_key_never_appears_in_repr(self, client: DealershipClient) -> None:
        from .conftest import API_KEY

        assert API_KEY not in repr(client)


class TestVehicles:
    async def test_null_price_means_price_on_request(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = {**vehicle(pricePence=None, monthlyPricePence=None), "highlights": []}
        api.get("/api/vehicles/veh-019").mock(ok(payload))
        result = await client.get_vehicle("veh-019")
        assert isinstance(result, o.VehicleFound)
        assert result.vehicle.price_on_request

    async def test_a_missing_price_field_is_malformed_not_price_on_request(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = {**vehicle(), "highlights": []}
        del payload["pricePence"]
        api.get("/api/vehicles/veh-001").mock(ok(payload))
        assert isinstance(await client.get_vehicle("veh-001"), o.MalformedResponse)

    async def test_monthly_price_never_enters_the_domain(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001").mock(ok({**vehicle(), "highlights": ["Inspected"]}))
        result = await client.get_vehicle("veh-001")
        assert isinstance(result, o.VehicleFound)
        assert "monthly" not in result.model_dump_json().lower()

    async def test_unknown_vehicle_is_not_found(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        from .conftest import error

        api.get("/api/vehicles/nope").mock(error(404, "NOT_FOUND"))
        assert await client.get_vehicle("nope") == o.NotFound(code="NOT_FOUND")


class TestVehicleStatus:
    @pytest.mark.parametrize("state", ["reserved", "sold"])
    async def test_next_slot_is_hidden_when_a_test_drive_cannot_be_booked(
        self, client: DealershipClient, api: respx.MockRouter, state: str
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability(state)))
        result = await client.get_vehicle_status("veh-001")
        assert isinstance(result, o.VehicleStatus)
        assert result.availability == state
        assert not result.can_book_test_drive
        assert result.next_slot_id is None
        assert result.next_slot_starts_at is None

    async def test_available_vehicle_keeps_its_next_slot(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability()))
        result = await client.get_vehicle_status("veh-001")
        assert isinstance(result, o.VehicleStatus)
        assert result.can_book_test_drive
        assert result.next_slot_id == "td-slot-0001"

    async def test_available_vehicle_with_no_slot_cannot_be_booked(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        payload = availability(canBookTestDrive=False, nextTestDriveSlot=None)
        api.get("/api/vehicles/veh-001/availability").mock(ok(payload))
        result = await client.get_vehicle_status("veh-001")
        assert isinstance(result, o.VehicleStatus)
        assert result.availability == "available"
        assert not result.can_book_test_drive

    async def test_a_sold_car_is_never_bookable_even_if_the_flag_says_so(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(
            ok(availability("sold", canBookTestDrive=True))
        )
        result = await client.get_vehicle_status("veh-001")
        assert isinstance(result, o.VehicleStatus)
        assert not result.can_book_test_drive
        assert result.next_slot_id is None

    async def test_an_unknown_availability_value_is_malformed(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability("pending")))
        assert isinstance(await client.get_vehicle_status("veh-001"), o.MalformedResponse)


class TestSearch:
    @pytest.mark.parametrize(
        "bad",
        [
            {"fuel_type": "EV"},
            {"body_style": "4x4"},
            {"sort": "bogus"},
            {"availability": "bogus"},
            {"page_size": 0},
            {"page_size": 51},
            {"page": 0},
        ],
    )
    def test_filters_the_platform_would_silently_empty_are_refused(self, bad: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            VehicleSearch(**bad)

    async def test_filters_use_the_platforms_names(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.get("/api/vehicles").mock(ok(page([vehicle()])))
        await client.search_vehicles(
            VehicleSearch(body_style="SUV", fuel_type="Hybrid", max_price_pence=4500000)
        )
        params = route.calls.last.request.url.params
        assert params["bodyStyle"] == "SUV"
        assert params["fuelType"] == "Hybrid"
        assert params["maxPricePence"] == "4500000"

    async def test_no_matches_is_an_empty_result_not_a_failure(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles").mock(ok(page([])))
        result = await client.search_vehicles(VehicleSearch(transmission="Manual"))
        assert isinstance(result, o.VehiclesListed)
        assert result.vehicles == []


class TestStockSearch:
    def route(self, api: respx.MockRouter, state: str, items: list[dict[str, Any]]) -> respx.Route:
        return api.get("/api/vehicles", params={"availability": state}).mock(ok(page(items)))

    async def test_sold_cars_are_not_requested_by_default(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        self.route(api, "available", [vehicle("veh-001")])
        self.route(api, "reserved", [vehicle("veh-007", availability="reserved")])
        sold = self.route(api, "sold", [vehicle("veh-013", availability="sold")])
        result = await client.search_stock(VehicleSearch())
        assert isinstance(result, o.StockSearched)
        assert [v.id for v in result.available] == ["veh-001"]
        assert [v.id for v in result.reserved] == ["veh-007"]
        assert result.sold == []
        assert not sold.called

    async def test_sold_cars_are_included_when_asked_for(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        self.route(api, "available", [])
        self.route(api, "reserved", [])
        self.route(api, "sold", [vehicle("veh-013", availability="sold")])
        result = await client.search_stock(VehicleSearch(), include_sold=True)
        assert isinstance(result, o.StockSearched)
        assert [v.id for v in result.sold] == ["veh-013"]

    async def test_every_page_is_read(self, client: DealershipClient, api: respx.MockRouter) -> None:
        def pages(request: httpx.Request) -> httpx.Response:
            number = int(request.url.params.get("page", "1"))
            state = request.url.params["availability"]
            if state != "available":
                return ok(page([]))
            return ok(page([vehicle(f"veh-{number:03d}")], number, 2))

        api.get("/api/vehicles").mock(side_effect=pages)
        result = await client.search_stock(VehicleSearch())
        assert isinstance(result, o.StockSearched)
        assert [v.id for v in result.available] == ["veh-001", "veh-002"]

    async def test_budget_search_recovers_cars_with_no_price(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        def answer(request: httpx.Request) -> httpx.Response:
            state = request.url.params["availability"]
            if "maxPricePence" in request.url.params:
                return ok(page([vehicle("veh-001")] if state == "available" else []))
            if state == "available":
                return ok(page([vehicle("veh-001"), vehicle("veh-019", pricePence=None)]))
            return ok(page([]))

        api.get("/api/vehicles").mock(side_effect=answer)
        result = await client.search_stock(VehicleSearch(max_price_pence=2000000))
        assert isinstance(result, o.StockSearched)
        assert [v.id for v in result.available] == ["veh-001"]
        assert [v.id for v in result.price_on_request] == ["veh-019"]

    async def test_a_partial_answer_is_a_failure_not_fewer_cars(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        self.route(api, "available", [vehicle()])
        api.get("/api/vehicles", params={"availability": "reserved"}).mock(
            httpx.Response(503)
        )
        result = await client.search_stock(VehicleSearch())
        assert isinstance(result, o.PlatformUnavailable)


class TestTestDriveSlots:
    @pytest.mark.parametrize("state", ["reserved", "sold"])
    async def test_listed_slots_are_not_offered_for_a_car_that_cannot_be_driven(
        self, client: DealershipClient, api: respx.MockRouter, state: str
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability(state)))
        slots = api.get("/api/test-drive-slots").mock(ok({"items": [td_slot()]}))
        result = await client.find_test_drive_slots("veh-001")
        assert result == o.TestDriveNotBookable(
            vehicle_id="veh-001", availability=state, can_register_interest=state == "reserved"
        )
        assert not slots.called

    async def test_bookable_car_gets_its_slots_with_dates(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability()))
        route = api.get("/api/test-drive-slots").mock(ok({"items": [td_slot()]}))
        result = await client.find_test_drive_slots(
            "veh-001", date_from=date(2026, 11, 2), date_to=date(2026, 11, 9)
        )
        assert isinstance(result, o.TestDriveSlotsFound)
        assert [s.id for s in result.slots] == ["td-slot-0001"]
        assert route.calls.last.request.url.params["dateFrom"] == "2026-11-02"
        assert route.calls.last.request.url.params["dateTo"] == "2026-11-09"

    async def test_past_slots_are_hidden_and_counted(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability()))
        api.get("/api/test-drive-slots").mock(
            ok({"items": [td_slot("old", startsAt=PAST), td_slot("new")]})
        )
        result = await client.find_test_drive_slots("veh-001")
        assert isinstance(result, o.TestDriveSlotsFound)
        assert [s.id for s in result.slots] == ["new"]
        assert result.past_hidden == 1

    async def test_slots_for_other_cars_are_dropped(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability()))
        api.get("/api/test-drive-slots").mock(
            ok({"items": [td_slot("mine"), td_slot("other", vehicleId="veh-002")]})
        )
        result = await client.find_test_drive_slots("veh-001")
        assert isinstance(result, o.TestDriveSlotsFound)
        assert [s.id for s in result.slots] == ["mine"]

    async def test_the_silent_100_row_cap_is_reported(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/vehicles/veh-001/availability").mock(ok(availability()))
        api.get("/api/test-drive-slots").mock(
            ok({"items": [td_slot(f"s{i}") for i in range(100)]})
        )
        result = await client.find_test_drive_slots("veh-001")
        assert isinstance(result, o.TestDriveSlotsFound)
        assert result.truncated


class TestWorkshopSlots:
    async def test_no_slots_is_a_valid_answer_not_an_outage(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/workshop-availability").mock(ok({"items": []}))
        result = await client.find_workshop_slots(dealership_id="northstar-bolton")
        assert result == o.WorkshopSlotsFound(slots=[], past_hidden=0, truncated=False)

    async def test_past_slots_are_hidden_and_the_cap_is_reported(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        items = [ws_slot("old", startsAt=PAST)] + [ws_slot(f"s{i}") for i in range(99)]
        api.get("/api/workshop-availability").mock(ok({"items": items}))
        result = await client.find_workshop_slots()
        assert isinstance(result, o.WorkshopSlotsFound)
        assert len(result.slots) == 99
        assert result.past_hidden == 1
        assert result.truncated

    async def test_a_timestamp_with_no_zone_is_malformed(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/workshop-availability").mock(
            ok({"items": [ws_slot(startsAt="2026-11-05T09:00:00")]})
        )
        assert isinstance(await client.find_workshop_slots(), o.MalformedResponse)

    def test_summer_slots_are_shown_in_uk_time(self) -> None:
        slot = workshop_slot(startsAt="2027-06-01T16:00:00Z")
        assert slot.starts_at_local.hour == 17

    def test_winter_slots_match_utc(self) -> None:
        slot = workshop_slot(startsAt="2026-12-01T16:00:00Z")
        assert slot.starts_at_local.hour == 16


class TestOffers:
    async def test_expired_offers_are_hidden_and_counted(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/offers").mock(
            ok({"items": [offer("offer-01"), offer("offer-02", expiresOn="2026-10-31")]})
        )
        result = await client.list_offers()
        assert isinstance(result, o.OffersListed)
        assert [x.id for x in result.offers] == ["offer-01"]
        assert result.expired_hidden == 1

    async def test_an_offer_expiring_today_still_counts(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/offers").mock(ok({"items": [offer(expiresOn="2026-11-01")]}))
        result = await client.list_offers()
        assert isinstance(result, o.OffersListed)
        assert result.expired_hidden == 0

    async def test_getting_an_expired_offer_says_so(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/offers/offer-02").mock(ok(offer("offer-02", expiresOn="2026-10-31")))
        assert await client.get_offer("offer-02") == o.OfferExpired(offer_id="offer-02")

    async def test_pch_has_no_apr(self, client: DealershipClient, api: respx.MockRouter) -> None:
        api.get("/api/offers").mock(ok({"items": [offer(productType="PCH", apr=None)]}))
        result = await client.list_offers(product_type="PCH")
        assert isinstance(result, o.OffersListed)
        assert result.offers[0].apr is None

    async def test_a_make_with_no_offers_is_empty(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        route = api.get("/api/offers").mock(ok({"items": []}))
        result = await client.list_offers(make="Kia")
        assert result == o.OffersListed(offers=[], expired_hidden=0)
        assert route.calls.last.request.url.params["make"] == "Kia"


def hours() -> OpeningHours:
    def row(department: str, day: str, index: int, opens: str | None, closes: str | None) -> dict[str, Any]:
        return {
            "department": department,
            "day": day,
            "dayOfWeek": index,
            "opensAt": opens,
            "closesAt": closes,
            "closed": opens is None,
        }

    return OpeningHours.model_validate(
        {
            "dealershipId": "northstar-manchester",
            "weekly": [
                row("sales", "Tuesday", 1, "09:00", "18:00"),
                row("service", "Tuesday", 1, "08:30", "17:30"),
                row("parts", "Tuesday", 1, "08:30", "17:30"),
                row("sales", "Saturday", 5, "09:00", "17:00"),
                row("service", "Saturday", 5, "09:00", "13:00"),
                row("sales", "Sunday", 6, None, None),
            ],
            "holidayExceptions": [
                {
                    "department": "sales",
                    "date": "2026-12-01",
                    "label": "Bank holiday",
                    "opensAt": "10:00",
                    "closesAt": "16:00",
                    "closed": False,
                },
                {
                    "department": "service",
                    "date": "2026-12-01",
                    "label": "Bank holiday",
                    "opensAt": None,
                    "closesAt": None,
                    "closed": True,
                },
                {
                    "department": "sales",
                    "date": "2026-12-06",
                    "label": "Bank holiday",
                    "opensAt": "10:00",
                    "closesAt": "16:00",
                    "closed": False,
                },
            ],
        }
    )


class TestOpeningHours:
    HOLIDAY = date(2026, 12, 1)

    def test_reduced_hours_replace_the_weekly_hours(self) -> None:
        result = hours().on(self.HOLIDAY, "sales")
        assert (result.opens_at, result.closes_at) == ("10:00", "16:00")
        assert result.holiday_label == "Bank holiday"

    def test_a_closed_department_is_closed_despite_its_weekly_hours(self) -> None:
        result = hours().on(self.HOLIDAY, "service")
        assert result.closed
        assert result.opens_at is None

    def test_a_department_with_no_exception_keeps_weekly_hours_and_says_so(self) -> None:
        result = hours().on(self.HOLIDAY, "parts")
        assert result.opens_at == "08:30"
        assert not result.exception_listed
        assert result.other_departments_excepted
        assert result.holiday_label is None

    def test_an_ordinary_date_uses_weekly_hours(self) -> None:
        result = hours().on(date(2026, 11, 7), "service")
        assert (result.opens_at, result.closes_at) == ("09:00", "13:00")

    def test_a_holiday_exception_beats_a_closed_sunday(self) -> None:
        result = hours().on(date(2026, 12, 6), "sales")
        assert not result.closed
        assert result.opens_at == "10:00"

    def test_sunday_is_closed(self) -> None:
        assert hours().on(date(2026, 11, 8), "sales").closed

    def test_day_of_week_counts_from_monday(self) -> None:
        assert hours().on(date(2026, 11, 3), "sales").closes_at == "18:00"

    def test_a_day_with_no_row_is_closed_rather_than_guessed(self) -> None:
        assert hours().on(date(2026, 11, 8), "parts").closed

    async def test_hours_are_fetched_for_a_dealership(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/dealerships/northstar-manchester/opening-hours").mock(
            ok(hours().model_dump(mode="json", by_alias=True))
        )
        result = await client.get_opening_hours("northstar-manchester")
        assert isinstance(result, o.OpeningHoursFound)


class TestReference:
    async def test_null_and_zero_service_prices_stay_different(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        def service(service_id: str, price: int | None) -> dict[str, Any]:
            return {
                "id": service_id,
                "name": service_id,
                "description": "A service.",
                "durationMinutes": 60,
                "priceFromPence": price,
            }

        api.get("/api/service-types").mock(
            ok({"items": [service("tyre-fitting", None), service("recall", 0)]})
        )
        result = await client.list_service_types()
        assert isinstance(result, o.ServiceTypesListed)
        tyres, recall = result.service_types
        assert tyres.price_on_request
        assert not recall.price_on_request
        assert recall.price_from_pence == 0

    async def test_business_information(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/api/business-information").mock(ok(business_information()))
        result = await client.get_business_information()
        assert isinstance(result, o.BusinessInformationFound)
        assert result.information.finance.minimum_age == 18
        assert result.information.privacy_contact.startswith("privacy@")

    @pytest.mark.parametrize(
        ("response", "healthy"),
        [
            (httpx.Response(200, json={"status": "ok"}), True),
            (httpx.Response(200, json={"status": "degraded"}), False),
            (httpx.Response(503), False),
        ],
    )
    async def test_health(
        self,
        client: DealershipClient,
        api: respx.MockRouter,
        response: httpx.Response,
        healthy: bool,
    ) -> None:
        api.get("/health").mock(response)
        assert await client.is_healthy() is healthy

    async def test_health_never_raises(
        self, client: DealershipClient, api: respx.MockRouter
    ) -> None:
        api.get("/health").mock(side_effect=httpx.ConnectError("refused"))
        assert await client.is_healthy() is False
