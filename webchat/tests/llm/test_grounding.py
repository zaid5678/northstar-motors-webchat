"""The grounding validator. Each adversarial case has an honest twin that must still pass."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from northstar_webchat.dealership import outcomes as o
from northstar_webchat.dealership.models import (
    OpeningHours,
    Offer,
    Pagination,
    TestDriveSlot,
    Vehicle,
)
from northstar_webchat.turns.grounding import (
    Facts,
    Violation,
    check_reply,
    facts_from,
    fill_placeholders,
)

SITE = "http://localhost:4173"


def vehicle(vehicle_id: str = "veh-001", **overrides: object) -> Vehicle:
    values: dict[str, object] = {
        "id": vehicle_id,
        "dealership_id": "northstar-manchester",
        "dealership_name": "Northstar Manchester",
        "dealership_town": "Manchester",
        "make": "BMW",
        "model": "1 Series",
        "variant": "118i M Sport",
        "year": 2020,
        "price_pence": 1850000,
        "mileage": 3500,
        "fuel_type": "Petrol",
        "transmission": "Automatic",
        "colour": "Thundernight Purple",
        "body_style": "Hatchback",
        "availability": "available",
        "images": ["/assets/vehicles/veh-001.jpg"],
        **overrides,
    }
    return Vehicle.model_validate(values)


def search(*vehicles: Vehicle) -> o.VehiclesListed:
    pagination = Pagination(page=1, page_size=12, total_items=len(vehicles), total_pages=1)
    return o.VehiclesListed(vehicles=list(vehicles), pagination=pagination)


def slots() -> o.TestDriveSlotsFound:
    slot = TestDriveSlot(
        id="td-1",
        dealership_id="northstar-manchester",
        dealership_name="Northstar Manchester",
        starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
        status="available",
        vehicle_id="veh-001",
        make="BMW",
        model="1 Series",
        variant="118i M Sport",
    )
    return o.TestDriveSlotsFound(slots=[slot], past_hidden=0, truncated=False)


def offers() -> o.OffersListed:
    offer = Offer(
        id="offer-01",
        make="BMW",
        model="1 Series",
        title="BMW 1 Series 118i M Sport",
        product_type="PCP",
        monthly_price_pence=36400,
        upfront_payment_pence=324900,
        apr=5.9,
        term_months=48,
        annual_mileage=8000,
        expires_on=datetime(2027, 1, 30).date(),
        description="Published offer.",
        image="/assets/vehicles/veh-001.jpg",
    )
    return o.OffersListed(offers=[offer], expired_hidden=0)


def hours() -> o.OpeningHoursFound:
    row = {"department": "sales", "day": "Saturday", "dayOfWeek": 5, "opensAt": "09:00", "closesAt": "17:00", "closed": False}
    parsed = OpeningHours.model_validate(
        {
            "dealershipId": "northstar-manchester",
            "weekly": [row],
            "holidayExceptions": [
                {"department": "sales", "date": "2026-12-01", "label": "Bank holiday", "opensAt": "10:00", "closesAt": "16:00", "closed": False}
            ],
        }
    )
    return o.OpeningHoursFound(hours=parsed)


def facts(*outcomes: o.Outcome) -> Facts:
    return facts_from(outcomes, site_url=SITE)


def kinds(violations: list[Violation]) -> list[str]:
    return [item.kind for item in violations]


class TestPrices:
    @pytest.mark.parametrize(
        "text",
        [
            "It's yours for £1.",
            "This one is £15,000.",
            "Only £18,499.",
            "It costs 15000 pounds.",
            "Yours for 18 grand.",
            "A bargain at £17.5k.",
            "Around £20k.",
            "£199 per month.",
            "GBP 12000 for this car.",
        ],
    )
    def test_invented_prices_are_caught(self, text: str) -> None:
        assert "price" in kinds(check_reply(text, facts(search(vehicle()))))

    @pytest.mark.parametrize(
        "text",
        [
            "It is £18,500.",
            "It's priced at £18,500.00.",
            "That's £18.5k.",
            "It costs 18,500 pounds.",
            "£18500 for this one.",
        ],
    )
    def test_true_prices_in_any_format_pass(self, text: str) -> None:
        assert check_reply(text, facts(search(vehicle()))) == []

    def test_a_price_with_no_tool_result_is_caught(self) -> None:
        assert "price" in kinds(check_reply("That service is £89.", Facts()))

    def test_a_null_price_gives_no_fact_to_match(self) -> None:
        found = facts(search(vehicle(price_pence=None)))
        assert "price" in kinds(check_reply("It's about £20,000.", found))

    def test_price_on_request_wording_passes(self) -> None:
        found = facts(search(vehicle(price_pence=None)))
        assert check_reply("This one is price on request.", found) == []

    def test_a_used_car_monthly_figure_is_not_a_fact(self) -> None:
        """D-012: the monthly field is not modelled, so any figure is invented."""
        assert "price" in kinds(check_reply("From £273.80 a month.", facts(search(vehicle()))))

    def test_a_published_offer_figure_passes(self) -> None:
        assert check_reply("From £364 a month, with £3,249 upfront.", facts(offers())) == []

    def test_the_customers_budget_can_be_repeated_when_the_filter_holds_it(self) -> None:
        found = facts(search(vehicle()))
        found.prices.add(3000000)
        assert check_reply("Here's one under your £30,000 budget.", found) == []

    def test_echoing_a_price_the_customer_guessed_does_not_make_it_true(self) -> None:
        found = facts(search(vehicle()))
        text = "Yes, it's £5."
        assert "price" in kinds(check_reply(text, found, customer_text="is it £5?"))


class TestVehicleIds:
    def test_an_invented_id_is_caught(self) -> None:
        assert "vehicle_id" in kinds(check_reply("Try veh-999.", facts(search(vehicle()))))

    def test_a_real_id_passes(self) -> None:
        assert check_reply("Try veh-001.", facts(search(vehicle()))) == []

    def test_ids_are_case_insensitive(self) -> None:
        assert "vehicle_id" in kinds(check_reply("Try VEH-999.", facts(search(vehicle()))))


class TestUrls:
    @pytest.mark.parametrize(
        "text",
        [
            "See https://evil.example/pay now.",
            "Visit www.northstar-deals.example for more.",
            "[Click here](http://evil.example)",
            "Look at http://localhost:4173/?vehicle=veh-999",
            "http://localhost:4173.evil.example/",
        ],
    )
    def test_unknown_urls_are_caught(self, text: str) -> None:
        assert "url" in kinds(check_reply(text, facts(search(vehicle()))))

    @pytest.mark.parametrize(
        "text",
        [
            "See http://localhost:4173/?vehicle=veh-001 for details.",
            "See http://localhost:4173/?vehicle=veh-001.",
            "[View it](http://localhost:4173/?vehicle=veh-001)",
            "Browse http://localhost:4173/",
            "Browse http://localhost:4173",
        ],
    )
    def test_real_links_pass(self, text: str) -> None:
        assert check_reply(text, facts(search(vehicle()))) == []


class TestDates:
    @pytest.mark.parametrize(
        "text",
        [
            "We have a slot on 5 November.",
            "Come in on November 5th.",
            "How about 05/11/2026?",
            "Booked for 2026-11-05.",
            "It's free on Tuesday.",
            "Come in at 10:30.",
            "Come in at 3pm.",
            "See you tomorrow.",
            "We can do next week.",
            "2 November 2027 works.",
        ],
    )
    def test_invented_dates_and_times_are_caught(self, text: str) -> None:
        found = check_reply(text, facts(slots()))
        assert "date" in kinds(found) or "time" in kinds(found)

    @pytest.mark.parametrize(
        "text",
        [
            "There's a slot on 2 November.",
            "There's a slot on Monday 2 November 2026 at 09:00.",
            "November 2nd at 9am works.",
            "02/11/2026 at 9:00am.",
            "Monday at 09:00.",
        ],
    )
    def test_true_dates_in_any_format_pass(self, text: str) -> None:
        assert check_reply(text, facts(slots())) == []

    def test_slot_times_are_compared_in_uk_time(self) -> None:
        summer = slots().model_copy()
        moved = summer.slots[0].model_copy(
            update={"starts_at": datetime(2027, 6, 7, 16, 0, tzinfo=timezone.utc)}
        )
        found = facts(o.TestDriveSlotsFound(slots=[moved], past_hidden=0, truncated=False))
        assert check_reply("Monday at 17:00.", found) == []
        assert "time" in kinds(check_reply("Monday at 16:00.", found))

    def test_opening_hours_are_facts(self) -> None:
        found = facts(hours())
        assert check_reply("Sales is open 09:00 to 17:00 on Saturday.", found) == []
        assert check_reply("On 1 December sales is open 10am to 4pm.", found) == []

    def test_invented_hours_are_caught(self) -> None:
        assert "time" in kinds(check_reply("Open until 19:00.", facts(hours())))

    def test_repeating_the_customers_own_day_passes(self) -> None:
        text = "Saturday works, let me check."
        assert check_reply(text, facts(slots()), customer_text="can I come saturday") == []

    def test_repeating_the_customers_own_time_word_passes(self) -> None:
        assert check_reply("Tomorrow it is.", facts(), customer_text="tomorrow please") == []

    def test_an_offer_expiry_is_a_fact(self) -> None:
        assert check_reply("Ends on 30 January 2027.", facts(offers())) == []

    @pytest.mark.parametrize(
        "text",
        [
            "We're here 24/7.",
            "The 2020 BMW 1 Series has 3,500 miles.",
            "A 118i with a 120-point inspection.",
            "1 of 12 results.",
        ],
    )
    def test_ordinary_numbers_are_not_dates(self, text: str) -> None:
        assert check_reply(text, facts(search(vehicle()))) == []


class TestFinanceRates:
    def test_an_invented_apr_is_caught(self) -> None:
        assert "apr" in kinds(check_reply("Just 0% APR.", facts(offers())))

    def test_a_published_apr_passes(self) -> None:
        assert check_reply("That's 5.9% APR.", facts(offers())) == []


class TestReferences:
    def test_an_invented_reference_is_caught(self) -> None:
        assert "reference" in kinds(check_reply("Your reference is TEST-ABC12345.", Facts()))

    def test_a_real_reference_passes(self) -> None:
        done = o.CallbackRequested(id="c", reference="CALL-ABC12345", replayed=False)
        assert check_reply("Your reference is CALL-ABC12345.", facts(done)) == []


class TestClaims:
    def test_calling_a_sold_car_available_is_caught(self) -> None:
        found = facts(search(vehicle(availability="sold")))
        assert "availability" in kinds(check_reply("The 1 Series is available now.", found))

    def test_calling_a_reserved_car_in_stock_is_caught(self) -> None:
        found = facts(search(vehicle(availability="reserved")))
        assert "availability" in kinds(check_reply("That 1 Series is in stock.", found))

    @pytest.mark.parametrize(
        "text",
        [
            "Sorry, the 1 Series is not available.",
            "The 1 Series is no longer available.",
            "The 1 Series isn't available for a test drive.",
            "The 1 Series is reserved.",
        ],
    )
    def test_saying_it_honestly_passes(self, text: str) -> None:
        found = facts(search(vehicle(availability="reserved")))
        assert check_reply(text, found) == []

    def test_one_available_and_one_sold_of_a_model_is_not_flagged(self) -> None:
        """Lookalikes: the claim could be true of either, so it is the card's job to separate."""
        found = facts(
            search(vehicle("veh-013", availability="sold"), vehicle("veh-049"))
        )
        assert check_reply("The 1 Series is available.", found) == []

    def test_a_lookalike_pair_is_not_flagged_in_either_order(self) -> None:
        pair = [vehicle("veh-013", availability="sold"), vehicle("veh-049")]
        assert check_reply("The 1 Series is available.", facts(search(*pair))) == []
        assert check_reply("The 1 Series is available.", facts(search(*pair[::-1]))) == []

    @pytest.mark.parametrize(
        "text",
        [
            "I guarantee finance approval.",
            "You're definitely approved.",
            "We promise to call you today.",
            "I've held the car for you.",
        ],
    )
    def test_promises_are_caught(self, text: str) -> None:
        assert "promise" in kinds(check_reply(text, Facts()))

    @pytest.mark.parametrize(
        "text",
        [
            "I can't guarantee a callback time.",
            "We can't promise an exact time.",
            "That doesn't guarantee availability.",
        ],
    )
    def test_refusing_to_promise_passes(self, text: str) -> None:
        assert check_reply(text, Facts()) == []

    def test_claiming_a_booking_without_one_is_caught(self) -> None:
        assert "confirmed" in kinds(check_reply("Your test drive is booked!", Facts()))

    def test_a_real_booking_can_be_called_booked(self) -> None:
        done = o.CallbackRequested(id="c", reference="CALL-ABC12345", replayed=False)
        assert "confirmed" in kinds(check_reply("Your callback is confirmed.", facts(done)))

    def test_a_confirmed_booking_passes(self) -> None:
        booked = o.TestDriveBooked(
            id="t",
            reference="TEST-ABC12345",
            slot_id="s",
            vehicle_id="veh-001",
            dealership_id="d",
            dealership_name="Northstar Manchester",
            starts_at=datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
            replayed=False,
        )
        assert check_reply("Your test drive is booked for 2 November.", facts(booked)) == []


class TestPlaceholders:
    def test_a_placeholder_is_filled_from_facts(self) -> None:
        found = facts(search(vehicle()))
        filled = fill_placeholders("It's {price:vehicle_1}.", found)
        assert filled == "It's £18,500."
        assert check_reply(filled, found) == []

    def test_a_price_on_request_placeholder_says_so(self) -> None:
        found = facts(search(vehicle(price_pence=None)))
        assert "price on request" in fill_placeholders("{price:vehicle_1}", found).lower()

    def test_an_unknown_placeholder_is_caught(self) -> None:
        text = fill_placeholders("It's {price:vehicle_9}.", facts(search(vehicle())))
        assert "placeholder" in kinds(check_reply(text, facts(search(vehicle()))))


# Honest sentences a good reply might contain. A failure here is a false positive.
HONEST = [
    "I found a 2020 BMW 1 Series 118i M Sport in Thundernight Purple.",
    "It has done 3,500 miles and is at Northstar Manchester.",
    "It's a petrol automatic hatchback.",
    "It is £18,500. Would you like to book a test drive?",
    "Would you like to compare it with another car?",
    "Which day suits you best?",
    "What time of day works for you?",
    "I can check availability for you.",
    "Is there a particular budget you have in mind?",
    "You can see it at http://localhost:4173/?vehicle=veh-001.",
    "We have a 120-point inspection on selected cars.",
    "Would an automatic suit you?",
    "Great choice! Happy to help with anything else.",
    "Let me know if you'd like a callback.",
    "I can't confirm a price for that one.",
    "Would you like to be booked in?",
    "Once you've confirmed the details, I'll book it.",
    "You'll need to confirm before anything is booked.",
    "Feel free to ask me anything.",
    "The car has 5 doors and 5 seats.",
    "It was first registered in 2020.",
    "We can look at 2 or 3 options.",
    "That's the top 1 of 3 results.",
]


@pytest.mark.parametrize("text", HONEST)
def test_honest_replies_are_not_rejected(text: str) -> None:
    assert check_reply(text, facts(search(vehicle()))) == []


# From a real run of the local model (D-023). Each was wrongly rejected before tuning.
REAL_FALSE_POSITIVES = [
    "Just let me know what you're looking for today!",
    "Hello! I'd be happy to help you browse our cars. Let me know what you'd like to do today!",
    "Is there anything else I can help with today?",
]


@pytest.mark.parametrize("text", REAL_FALSE_POSITIVES)
def test_today_as_a_figure_of_speech_passes(text: str) -> None:
    assert check_reply(text, Facts()) == []


@pytest.mark.parametrize(
    "text",
    ["We're open today until late.", "We have a slot today.", "You can come in today."],
)
def test_today_as_a_real_claim_is_still_caught(text: str) -> None:
    assert "date" in kinds(check_reply(text, Facts()))


class TestAttributes:
    """Real failures: sentences with no number in them that were simply untrue."""

    def test_claiming_a_gearbox_nobody_has_is_caught(self) -> None:
        text = "I've found a few great options with manual gearboxes."
        found = check_reply(text, facts(search(vehicle())), customer_text="got any manual cars?")
        assert "attribute" in kinds(found)

    def test_claiming_the_wrong_fuel_is_caught(self) -> None:
        text = "We have some great diesel options available right now."
        assert "attribute" in kinds(check_reply(text, facts(search(vehicle()))))

    def test_claiming_the_wrong_make_is_caught(self) -> None:
        assert "attribute" in kinds(check_reply("We have a great Kia.", facts(search(vehicle()))))

    @pytest.mark.parametrize(
        "text",
        [
            "We have a petrol automatic BMW hatchback.",
            "Sorry, we don't have any manual cars.",
            "Would you prefer a diesel?",
            "Every car we have is automatic.",
        ],
    )
    def test_true_or_negative_or_question_passes(self, text: str) -> None:
        assert check_reply(text, facts(search(vehicle()))) == []

    def test_no_vehicle_results_means_no_attribute_check(self) -> None:
        assert check_reply("We sell petrol and diesel cars.", Facts()) == []
