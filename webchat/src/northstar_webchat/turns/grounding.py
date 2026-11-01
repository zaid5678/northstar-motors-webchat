"""Checks a reply against this turn's tool results.

Every price, vehicle ID, URL, date, time, APR, and reference in the text must come from a tool
result. Correct values pass however they are written, because models drop placeholders often
(D-023). Prices are never accepted just because the customer said them: "is it £5?" must not make
£5 true. Days and times may be repeated, since echoing them claims nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime


from pydantic import BaseModel

from ..dealership import outcomes as o
from ..dealership.models import LONDON, Vehicle


class Violation(BaseModel):
    kind: str
    value: str


class Facts(BaseModel):
    prices: set[int] = set()
    vehicle_ids: set[str] = set()
    urls: set[str] = set()
    dates: set[str] = set()
    weekdays: set[str] = set()
    times: set[str] = set()
    aprs: set[str] = set()
    references: set[str] = set()
    confirmed: bool = False
    unavailable_models: set[str] = set()
    attributes: set[str] = set()
    has_vehicles: bool = False
    offers_listed: bool = False
    placeholders: dict[str, str] = {}


def pounds(pence: int) -> str:
    return f"£{pence / 100:,.0f}" if pence % 100 == 0 else f"£{pence / 100:,.2f}"


def _add_moment(facts: Facts, moment: datetime) -> None:
    local = moment.astimezone(LONDON)
    _add_date(facts, local.date())
    facts.times.add(local.strftime("%H:%M"))


def _add_date(facts: Facts, day: date) -> None:
    facts.dates.add(day.isoformat())
    facts.weekdays.add(day.strftime("%A").lower())


def _add_hours(facts: Facts, opens: str | None, closes: str | None) -> None:
    facts.times.update(item for item in (opens, closes) if item)


def _vehicles(outcome: o.Outcome) -> Iterable[Vehicle]:
    if isinstance(outcome, o.VehiclesListed):
        return outcome.vehicles
    if isinstance(outcome, o.StockSearched):
        return [*outcome.available, *outcome.reserved, *outcome.sold, *outcome.price_on_request]
    if isinstance(outcome, o.VehicleFound):
        return [outcome.vehicle]
    return []


def facts_from(outcomes: Sequence[o.Outcome], *, site_url: str) -> Facts:
    site = site_url.rstrip("/")
    facts = Facts(urls={site})
    seen: dict[str, Vehicle] = {}
    for outcome in outcomes:
        for vehicle in _vehicles(outcome):
            seen.setdefault(vehicle.id, vehicle)
        reference = getattr(outcome, "reference", None)
        if isinstance(reference, str):
            facts.references.add(reference)
        _add_outcome(facts, outcome)
    models: dict[str, bool] = {}
    for number, vehicle in enumerate(seen.values(), start=1):
        facts.vehicle_ids.add(vehicle.id)
        facts.has_vehicles = True
        facts.attributes.update(
            item.casefold()
            for item in (vehicle.make, vehicle.fuel_type, vehicle.transmission, vehicle.body_style)
        )
        facts.urls.add(f"{site}/?vehicle={vehicle.id}")
        if vehicle.price_pence is not None:
            facts.prices.add(vehicle.price_pence)
        price = "price on request" if vehicle.price_pence is None else pounds(vehicle.price_pence)
        facts.placeholders[f"price:vehicle_{number}"] = price
        label = vehicle.model.casefold()
        models[label] = models.get(label, False) or vehicle.availability == "available"
    facts.unavailable_models = {label for label, any_free in models.items() if not any_free}
    return facts


def _add_outcome(facts: Facts, outcome: o.Outcome) -> None:
    if isinstance(outcome, (o.OffersListed, o.OfferFound)):
        offers = outcome.offers if isinstance(outcome, o.OffersListed) else [outcome.offer]
        facts.offers_listed = facts.offers_listed or bool(offers)
        for offer in offers:
            facts.prices.update({offer.monthly_price_pence, offer.upfront_payment_pence})
            if offer.apr is not None:
                facts.aprs.add(str(float(offer.apr)))
            _add_date(facts, offer.expires_on)
    elif isinstance(outcome, o.ServiceTypesListed):
        facts.prices.update(
            s.price_from_pence for s in outcome.service_types if s.price_from_pence is not None
        )
    elif isinstance(outcome, (o.TestDriveSlotsFound, o.WorkshopSlotsFound)):
        for slot in outcome.slots:
            _add_moment(facts, slot.starts_at)
            price = getattr(slot, "price_from_pence", None)
            if price is not None:
                facts.prices.add(price)
    elif isinstance(outcome, o.VehicleStatus):
        if outcome.next_slot_starts_at is not None:
            _add_moment(facts, outcome.next_slot_starts_at)
    elif isinstance(outcome, o.OpeningHoursFound):
        for row in outcome.hours.weekly:
            facts.weekdays.add(row.day.lower())
            _add_hours(facts, row.opens_at, row.closes_at)
        for exception in outcome.hours.holiday_exceptions:
            _add_date(facts, exception.date)
            _add_hours(facts, exception.opens_at, exception.closes_at)
    elif isinstance(outcome, (o.TestDriveBooked, o.WorkshopBooked)):
        facts.confirmed = True
        _add_moment(facts, outcome.starts_at)
    elif isinstance(outcome, o.BookingAmended):
        facts.confirmed = True
        if outcome.starts_at is not None:
            _add_moment(facts, outcome.starts_at)
    elif isinstance(outcome, o.EstimateGiven):
        facts.prices.update({outcome.estimate_low_pence, outcome.estimate_high_pence})
    elif isinstance(outcome, o.WorkshopBookingFound):
        facts.references.add(outcome.booking.reference)
        if outcome.booking.starts_at is not None:
            _add_moment(facts, outcome.booking.starts_at)


NUMBER = r"\d[\d,]*(?:\.\d+)?"
PRICE_SYMBOL = re.compile(rf"(?:£|\bGBP\s?)\s*({NUMBER})\s*(k\b)?", re.IGNORECASE)
PRICE_WORD = re.compile(rf"\b({NUMBER})\s*(k\s+)?(pounds|quid|grand)\b", re.IGNORECASE)
VEHICLE_ID = re.compile(r"\bveh-\d+\b", re.IGNORECASE)
URL = re.compile(r"(?:https?://|www\.)[^\s)\]>\"']+", re.IGNORECASE)
REFERENCE = re.compile(r"\b(?:WORK|TEST|SALE|WAIT|CALL|MSG|PX)-[A-Z0-9]{4,}\b")
PLACEHOLDER = re.compile(r"\{[a-z_]+:[^{}]*\}")
MONTHS = (
    "january february march april may june july august september october november december"
).split()
MONTH = "|".join(MONTHS)
DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH})\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
MONTH_DAY = re.compile(
    rf"\b({MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
NUMERIC_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
WEEKDAY = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.IGNORECASE
)
RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|next week|this week|next month|this weekend|next weekend)\b",
    re.IGNORECASE,
)
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
HOUR = re.compile(r"(?<![:\d])\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
APR = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:rep(?:resentative)?\.?\s*)?apr\b|\bapr\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
ATTRIBUTE = re.compile(
    r"\b(petrol|diesel|hybrid|electric|manual|automatic|suv|estate|saloon|hatchback"
    r"|bmw|mini|jaguar|land rover|volvo|kia)s?\b",
    re.IGNORECASE,
)
TIME_CLAIM = re.compile(r"\b(?:open|closed|slots?|appointments?|come in|collect)\b", re.IGNORECASE)
# Topics only code may speak about: notices and published offers carry them. A model that mentions
# one outside a question or a refusal is inventing terms.
RESTRICTED = re.compile(
    r"\b(warrant(?:y|ies)|financ(?:e|ing)|credit|loans?|deliver(?:y|ies|ed|s)?|insur(?:ance|ed)"
    r"|discount(?:s|ed)?|deposits?|interest rates?|(?<!feel )free)\b",
    re.IGNORECASE,
)
NEGATED = re.compile(r"\b(?:not|never|cannot|no longer|unable)\b|n['’]t\b", re.IGNORECASE)
AVAILABLE = re.compile(r"\b(?:available|in stock)\b", re.IGNORECASE)
PROMISE = re.compile(
    r"\b(?:guarantee[sd]?|promise[sd]?|approved|held the car|holding the car)\b", re.IGNORECASE
)
BOOKED = re.compile(
    r"\b(?:(?:your [\w -]+?|it|that)\s+(?:is|has been|was)\s+(?:now\s+)?(?:booked|confirmed)"
    r"|(?:you['’]re|you are)\s+(?:all\s+)?(?:booked|confirmed)"
    r"|(?:i['’]ve|i have|we['’]ve|we have)\s+(?:now\s+)?(?:booked|confirmed|reserved))\b",
    re.IGNORECASE,
)


def _flatten(text: str) -> str:
    return " ".join(text.casefold().split())


def _pence(number: str, thousands: bool) -> int:
    value = float(number.rstrip(",").replace(",", ""))
    return round(value * (1000 if thousands else 1) * 100)


def _prices(text: str, facts: Facts) -> Iterable[Violation]:
    for match in PRICE_SYMBOL.finditer(text):
        if _pence(match[1], bool(match[2])) not in facts.prices:
            yield Violation(kind="price", value=match[0].strip())
    for match in PRICE_WORD.finditer(text):
        thousands = bool(match[2]) or match[3].lower() == "grand"
        if _pence(match[1], thousands) not in facts.prices:
            yield Violation(kind="price", value=match[0].strip())


def _day_known(facts: Facts, month: int, day: int, year: int | None) -> bool:
    for known in facts.dates:
        found = date.fromisoformat(known)
        if (found.month, found.day) == (month, day) and year in (None, found.year):
            return True
    return False


def _dates(text: str, facts: Facts, customer: str) -> Iterable[Violation]:
    def unknown(match: re.Match[str], month: int, day: int, year: int | None) -> Violation | None:
        if _flatten(match[0]) in customer or _day_known(facts, month, day, year):
            return None
        return Violation(kind="date", value=match[0])

    found: list[Violation | None] = []
    for match in DAY_MONTH.finditer(text):
        month = MONTHS.index(match[2].lower()) + 1
        found.append(unknown(match, month, int(match[1]), int(match[3]) if match[3] else None))
    for match in MONTH_DAY.finditer(text):
        month = MONTHS.index(match[1].lower()) + 1
        found.append(unknown(match, month, int(match[2]), int(match[3]) if match[3] else None))
    for match in NUMERIC_DATE.finditer(text):
        day, month = int(match[1]), int(match[2])
        if match[0] == "24/7" or not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        year = int(match[3]) if match[3] else None
        if year is not None and year < 100:
            year += 2000
        found.append(unknown(match, month, day, year))
    for match in ISO_DATE.finditer(text):
        found.append(unknown(match, int(match[2]), int(match[3]), int(match[1])))
    for match in WEEKDAY.finditer(text):
        name = match[1].lower()
        if name not in facts.weekdays and name not in customer:
            found.append(Violation(kind="date", value=match[0]))
    for match in RELATIVE.finditer(text):
        word = match[1].lower()
        if word in customer or (word == "today" and not _claims_a_time(text, match)):
            continue  # "What can I help with today?" states nothing.
        found.append(Violation(kind="date", value=match[0]))
    return [item for item in found if item is not None]


def _claims_a_time(text: str, match: re.Match[str]) -> bool:
    start = max(text.rfind(mark, 0, match.start()) for mark in ".!?\n") + 1
    ends = [text.find(mark, match.end()) for mark in ".!?\n"]
    end = min((item for item in ends if item != -1), default=len(text))
    return TIME_CLAIM.search(text[start:end]) is not None


def _clock(hour: int, minute: int, meridiem: str | None) -> str:
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    return f"{hour:02d}:{minute:02d}"


def _times(text: str, facts: Facts, customer: str) -> Iterable[Violation]:
    for match in CLOCK.finditer(text):
        known = _clock(int(match[1]), int(match[2]), match[3]) in facts.times
        if not known and _flatten(match[0]) not in customer:
            yield Violation(kind="time", value=match[0])
    for match in HOUR.finditer(text):
        known = _clock(int(match[1]), 0, match[2]) in facts.times
        if not known and _flatten(match[0]) not in customer:
            yield Violation(kind="time", value=match[0])


def _urls(text: str, facts: Facts) -> Iterable[Violation]:
    allowed = {url.rstrip("/").casefold() for url in facts.urls}
    for match in URL.finditer(text):
        url = match[0].rstrip(".,;:!?")
        if url.rstrip("/").casefold() not in allowed:
            yield Violation(kind="url", value=url)


def _claims(text: str, facts: Facts) -> Iterable[Violation]:
    for sentence in re.findall(r"[^.!?\n]+[.!?\n]?", text):
        lowered = sentence.casefold()
        negated = NEGATED.search(sentence) is not None
        if facts.has_vehicles and not negated and not sentence.rstrip().endswith("?"):
            for match in ATTRIBUTE.finditer(sentence):
                if match[1].casefold() not in facts.attributes:
                    yield Violation(kind="attribute", value=match[0])
        if not negated and AVAILABLE.search(sentence):
            for model in facts.unavailable_models:
                if model in lowered:
                    yield Violation(kind="availability", value=sentence.strip())
        if not negated and not sentence.rstrip().endswith("?"):
            for match in RESTRICTED.finditer(sentence):
                finance = match[1].casefold().startswith("financ")
                if not (finance and facts.offers_listed):
                    yield Violation(kind="topic", value=match[0])
        if not negated and PROMISE.search(sentence):
            yield Violation(kind="promise", value=sentence.strip())
        if not negated and not facts.confirmed and BOOKED.search(sentence):
            yield Violation(kind="confirmed", value=sentence.strip())


def check_reply(text: str, facts: Facts, *, customer_text: str = "") -> list[Violation]:
    customer = _flatten(customer_text)
    found = [*_prices(text, facts), *_dates(text, facts, customer), *_times(text, facts, customer)]
    found += [*_urls(text, facts), *_claims(text, facts)]
    known_ids = {item.casefold() for item in facts.vehicle_ids}
    found += [
        Violation(kind="vehicle_id", value=m[0])
        for m in VEHICLE_ID.finditer(text)
        if m[0].casefold() not in known_ids
    ]
    found += [
        Violation(kind="reference", value=m[0])
        for m in REFERENCE.finditer(text)
        if m[0] not in facts.references
    ]
    for match in APR.finditer(text):
        if str(float(match[1] or match[2])) not in facts.aprs:
            found.append(Violation(kind="apr", value=match[0]))
    found += [Violation(kind="placeholder", value=m[0]) for m in PLACEHOLDER.finditer(text)]
    return found


def fill_placeholders(text: str, facts: Facts) -> str:
    """Unknown placeholders stay in the text, where `check_reply` rejects them."""

    def replace(match: re.Match[str]) -> str:
        return facts.placeholders.get(match[0][1:-1], match[0])

    return PLACEHOLDER.sub(replace, text)


