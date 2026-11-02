"""One customer turn. The LLM appears twice, and the turn still works if it never answers.

Lookups, buttons, forms, and confirmations do not depend on the model. If it is down, free text gets
an honest message and buttons (D-006), and anything already in progress carries on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from datetime import datetime, timezone

from pydantic import BaseModel

from .. import ops
from ..dealership import outcomes as o
from ..dealership.client import DealershipClient
from ..dealership.models import (
    Dealership,
    Department,
    OpeningHours,
    Vehicle,
    VehicleSearch,
)
from ..llm.interpret import interpret
from ..llm.types import (
    MAX_SHOWN,
    WORKFLOW_INTENTS,
    Intent,
    Interpretation,
    InterpretationSource,
    LLMClient,
)
from ..workflows.types import DEFINITIONS, Source, WorkflowKind
from ..writes.present import View
from ..writes.service import Proposal, WriteService
from .compose import Composed, TextSource, compose
from .grounding import Facts, facts_from, pounds
from .notices import NoticeBook, notices_for

PLATFORM_DOWN = "We can't reach the dealership system right now. Please try again shortly."
FINANCE_WITHHELD = "We can't show finance offers right now. Please try again shortly."
DEGRADED = "I'm having trouble understanding messages right now. These options still work:"
NOT_UNDERSTOOD = "I didn't quite catch that. Here's what I can help with:"
GREETING = "I can help you find a car, book a test drive or service, or contact a dealership."
NO_CAR = (
    "I can check a specific car from its page on our website. Open the car you're interested in "
    "and ask me there, or browse our cars below."
)
NO_STOCK = "I couldn't find any cars matching that. Try changing what you're looking for."
NO_OFFERS = "There are no published offers for that right now."
BUSY = "I'm still working on your last message. Please wait a moment."
NO_COMPARE = (
    "I can compare cars I've already shown you. Search for some cars first, then ask me to "
    "compare them."
)
# Only a press of the button starts these (D-030). A misread "thanks" must never lead to a card that
# cancels or moves a real booking.
OFFERED = {
    WorkflowKind.WORKSHOP_CANCEL: "Do you want to cancel a workshop booking? Press the button to start.",
    WorkflowKind.WORKSHOP_AMEND: "Do you want to change a workshop booking? Press the button to start.",
}
MAX_LISTED = MAX_SHOWN
MAX_QUEUED_TURNS = 3
DEPARTMENTS: tuple[Department, ...] = ("sales", "service", "parts")


class Action(BaseModel):
    id: str
    label: str


class VehicleCard(BaseModel):
    id: str
    title: str
    price: str
    availability: str
    url: str
    image: str | None


class TurnReply(BaseModel):
    text: str
    source: TextSource
    notices: list[str] = []
    actions: list[Action] = []
    view: View | None = None
    degraded: bool = False
    vehicles: list[VehicleCard] = []


LOOKUPS = (
    Action(id="find_stock", label="Browse cars"),
    Action(id="opening_hours", label="Opening hours"),
    Action(id="new_car_offers", label="New car offers"),
)
WORKFLOW_BUTTONS = (
    Action(id=WorkflowKind.TEST_DRIVE.value, label="Book a test drive"),
    Action(id=WorkflowKind.WORKSHOP_CREATE.value, label="Book a service"),
    Action(id=WorkflowKind.WORKSHOP_AMEND.value, label="Change a booking"),
    Action(id=WorkflowKind.WORKSHOP_CANCEL.value, label="Cancel a booking"),
    Action(id=WorkflowKind.BOOKING_RETRIEVAL.value, label="Check my booking"),
    Action(id=WorkflowKind.PART_EXCHANGE.value, label="Part-exchange estimate"),
    Action(id=WorkflowKind.SALES_ENQUIRY.value, label="Make an enquiry"),
    Action(id=WorkflowKind.VEHICLE_INTEREST.value, label="Register interest"),
    Action(id=WorkflowKind.CALLBACK.value, label="Request a callback"),
    Action(id=WorkflowKind.MESSAGE.value, label="Leave a message"),
)
MENU = [*LOOKUPS, *WORKFLOW_BUTTONS]
BUTTON_WORKFLOWS = {action.id: WorkflowKind(action.id) for action in WORKFLOW_BUTTONS}


def _price(vehicle: Vehicle) -> str:
    return "Price on request" if vehicle.price_pence is None else pounds(vehicle.price_pence)


def _title(vehicle: Vehicle) -> str:
    return f"{vehicle.year} {vehicle.make} {vehicle.model} {vehicle.variant}"


def _stock(outcome: o.StockSearched) -> list[Vehicle]:
    return [*outcome.available, *outcome.reserved, *outcome.sold, *outcome.price_on_request]


def _list_lines(vehicles: Sequence[Vehicle]) -> str:
    return "\n".join(
        f"{_title(item)}: {_price(item)} ({item.availability})" for item in vehicles[:MAX_LISTED]
    )


def _department_week(hours: OpeningHours, department: Department) -> str:
    groups: list[tuple[str, str, str]] = []
    for row in sorted(
        (item for item in hours.weekly if item.department == department),
        key=lambda item: item.day_of_week,
    ):
        state = "closed" if row.closed else f"{row.opens_at} to {row.closes_at}"
        day = row.day[:3]
        if groups and groups[-1][2] == state:
            groups[-1] = (groups[-1][0], day, state)
        else:
            groups.append((day, day, state))
    parts = [
        f"{first} {state}" if first == last else f"{first} to {last} {state}"
        for first, last, state in groups
    ]
    return f"{department.title()}: {'; '.join(parts)}"


def _address(site: Dealership) -> str:
    return f"{site.name}, {site.address_line}, {site.town} {site.postcode}"


def _site_hours(site: Dealership, hours: OpeningHours) -> str:
    lines = [f"{_address(site)}. Phone {site.phone}, email {site.email}."]
    lines += [_department_week(hours, department) for department in DEPARTMENTS]
    for day in sorted({item.date for item in hours.holiday_exceptions}):
        rows = [item for item in hours.holiday_exceptions if item.date == day]
        changes = [
            f"{item.department} closed"
            if item.closed
            else f"{item.department} {item.opens_at} to {item.closes_at}"
            for item in rows
        ]
        listed = {item.department for item in rows}
        changes += [f"{name}: no change listed" for name in DEPARTMENTS if name not in listed]
        when = f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}"
        lines.append(f"{rows[0].label} on {when}: {', '.join(changes)}.")
    return "\n".join(lines)


def _comparison(vehicle: Vehicle) -> str:
    return "\n".join(
        (
            _title(vehicle),
            f"Price: {_price(vehicle)}",
            f"Mileage: {vehicle.mileage:,} miles",
            f"Fuel: {vehicle.fuel_type}",
            f"Gearbox: {vehicle.transmission}",
            f"Body: {vehicle.body_style}",
            f"Colour: {vehicle.colour}",
            f"At: {vehicle.dealership_name}",
            f"Status: {vehicle.availability}",
        )
    )


def _summaries(vehicles: Sequence[Vehicle]) -> list[str]:
    """Short on purpose: a local model reads about 20 tokens a second, and cards show the rest."""
    return [f"{_title(item)}, {_price(item)}, {item.availability}" for item in vehicles]


Progress = Callable[[str], Awaitable[None]]
PROGRESS: ContextVar[Progress | None] = ContextVar("progress", default=None)
# What the model made of this turn, for its log line. Kinds and names only, never the text.
READING: ContextVar[dict[str, str] | None] = ContextVar("reading", default=None)


async def _say(stage: str) -> None:
    report = PROGRESS.get()
    if report is not None:
        await report(stage)


class _Queue:
    """One lock per session. Locks wake waiters in arrival order, and nothing is kept once idle."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.count = 0


class TurnEngine:
    def __init__(
        self,
        llm: LLMClient,
        client: DealershipClient,
        service: WriteService,
        *,
        site_url: str,
        asset_base: str = "",
    ) -> None:
        self.llm = llm
        self._client = client
        self._service = service
        self._site = site_url.rstrip("/")
        self._assets = asset_base.rstrip("/")
        self._notices = NoticeBook(client)
        self._queues: dict[str, _Queue] = {}

    @property
    def sessions_waiting(self) -> int:
        return len(self._queues)

    async def _in_order(
        self, session_id: str, run: Callable[[], Awaitable[TurnReply]]
    ) -> TurnReply:
        """Runs a session's turns one at a time (D-024). Single-process only. Confirmations do not
        come through here: they are already safe, and must not wait behind a slow model."""
        queue = self._queues.setdefault(session_id, _Queue())
        if queue.count > MAX_QUEUED_TURNS:
            self._service.store.note(
                session_id, "turn_busy", Source.SYSTEM, datetime.now(timezone.utc)
            )
            return await self._finish(session_id, BUSY, TextSource.TEMPLATE)
        queue.count += 1
        try:
            async with queue.lock:
                return await run()
        finally:
            queue.count -= 1
            if queue.count == 0:
                self._queues.pop(session_id, None)

    def _card(self, vehicle: Vehicle) -> VehicleCard:
        return VehicleCard(
            id=vehicle.id,
            title=_title(vehicle),
            price=_price(vehicle),
            availability=vehicle.availability,
            url=f"{self._site}/?vehicle={vehicle.id}",
            image=f"{self._assets}{vehicle.images[0]}" if vehicle.images else None,
        )

    def _remember(self, session_id: str, search: VehicleSearch, vehicles: Sequence[Vehicle]) -> None:
        """Stored in the order the cards are shown, so a position means the same car to both."""
        self._service.store.remember(
            session_id,
            {
                "search": search.model_dump(mode="json", exclude_none=True),
                "shown": [{"id": item.id, "title": _title(item)} for item in vehicles[:MAX_LISTED]],
            },
            datetime.now(timezone.utc),
        )

    def _shown(self, session_id: str) -> list[dict[str, str]]:
        memory = self._service.store.recall(session_id)
        return list(memory["shown"]) if memory else []

    async def _finish(
        self,
        session_id: str,
        text: str,
        source: TextSource,
        *,
        outcomes: Sequence[o.Outcome] = (),
        vehicles: Sequence[Vehicle] = (),
        actions: Sequence[Action] = (),
        degraded: bool = False,
    ) -> TurnReply:
        """The last step of every turn. Notices are added here, whatever the text came from."""
        if outcomes:
            self._service.store.note(
                session_id,
                "tool_call",
                Source.SYSTEM,
                datetime.now(timezone.utc),
                outcomes=[type(item).__name__ for item in outcomes],
            )
        view = await self._service.view(session_id)
        active = self._service.store.load(session_id).active
        notices = list(view.notices)
        for kind in notices_for(active, outcomes):
            notice = await self._notices.text(kind)
            if notice is not None:
                notices.append(notice)
        return TurnReply(
            text=text,
            source=source,
            notices=list(dict.fromkeys(notices)),
            actions=list(actions),
            view=view,
            degraded=degraded,
            vehicles=[self._card(item) for item in vehicles[:MAX_LISTED]],
        )

    async def _written(
        self,
        session_id: str,
        text: str,
        *,
        outcomes: Sequence[o.Outcome],
        data: dict[str, object],
        template: str,
        vehicles: Sequence[Vehicle] = (),
        facts: Facts | None = None,
    ) -> TurnReply:
        await _say("writing")
        composed: Composed = await compose(
            self.llm,
            facts=facts or facts_from(outcomes, site_url=self._site),
            data=data,
            template=template,
            customer_text=text,
        )
        self._service.store.note(
            session_id,
            "grounding",
            Source.SYSTEM,
            datetime.now(timezone.utc),
            result=composed.source.value,
            rejected=sorted({item.kind for item in composed.rejected}),
        )
        if composed.rejected:
            ops.warn(
                "grounding_rejected",
                session=ops.session_tag(session_id),
                kinds=sorted({item.kind for item in composed.rejected}),
                reply=composed.source.value,
            )
            self._service.store.note(
                session_id,
                "grounding_rejected",
                Source.SYSTEM,
                datetime.now(timezone.utc),
                kinds=sorted({item.kind for item in composed.rejected}),
            )
        return await self._finish(
            session_id, composed.text, composed.source, outcomes=outcomes, vehicles=vehicles
        )

    async def _menu(self, session_id: str, text: str, *, degraded: bool = False) -> TurnReply:
        return await self._finish(
            session_id, text, TextSource.TEMPLATE, actions=MENU, degraded=degraded
        )

    async def _in_workflow(self, session_id: str, view: View) -> TurnReply:
        text = view.message or "Let's get that sorted. Please fill in the details below."
        return await self._finish(session_id, text, TextSource.TEMPLATE)

    async def message(
        self,
        session_id: str,
        text: str,
        *,
        page_vehicle: str | None = None,
        progress: Progress | None = None,
    ) -> TurnReply:
        return await self._recorded(
            session_id,
            text,
            lambda: self._message(session_id, text, page_vehicle),
            progress,
            kind="message",
        )

    async def action(
        self, session_id: str, action_id: str, *, progress: Progress | None = None
    ) -> TurnReply:
        """A button press. No model is involved, so these all work in degraded mode."""
        label = next((item.label for item in MENU if item.id == action_id), action_id)
        return await self._recorded(
            session_id,
            label,
            lambda: self._action(session_id, action_id),
            progress,
            kind="action",
            action=action_id,
        )

    async def _recorded(
        self,
        session_id: str,
        text: str,
        run: Callable[[], Awaitable[TurnReply]],
        progress: Progress | None,
        *,
        kind: str,
        action: str | None = None,
    ) -> TurnReply:
        """Both sides of a turn are saved, so a refresh loses nothing. The reply is stored without
        its view, because a view can hold a confirmation token."""
        store = self._service.store
        store.add_message(session_id, "user", {"text": text}, datetime.now(timezone.utc))
        PROGRESS.set(progress)
        reading: dict[str, str] = {}
        READING.set(reading)
        started = time.monotonic()
        reply = await self._in_order(session_id, run)
        ops.log(
            "turn",
            session=ops.session_tag(session_id),
            kind=kind,
            action=action,
            intent=reading.get("intent"),
            interpretation=reading.get("interpretation"),
            reply=reply.source.value,
            degraded=reply.degraded,
            vehicles=len(reply.vehicles),
            workflow=reply.view.status if reply.view else None,
            ms=round((time.monotonic() - started) * 1000),
        )
        store.add_message(
            session_id,
            "assistant",
            reply.model_dump(mode="json", exclude={"view"}),
            datetime.now(timezone.utc),
        )
        return reply

    async def _message(self, session_id: str, text: str, page_vehicle: str | None) -> TurnReply:
        await _say("understanding")
        handles = frozenset({"vehicle_1"}) if page_vehicle else frozenset()
        shown = self._shown(session_id)
        context = self._service.llm_context(session_id)
        if shown:
            # Positions and titles only. The model never sees an ID, so it cannot name another car.
            context["shown_cars"] = [f"{place}: {item['title']}" for place, item in enumerate(shown, 1)]
        result = await interpret(self.llm, text, context=context, handles=handles, shown=len(shown))
        noted = READING.get()
        if noted is not None:
            noted.update(
                intent=result.interpretation.intent.value, interpretation=result.source.value
            )
        if result.source is InterpretationSource.UNAVAILABLE:
            return await self._menu(session_id, DEGRADED, degraded=True)
        if result.source is InterpretationSource.FALLBACK:
            return await self._menu(session_id, NOT_UNDERSTOOD)
        return await self._route(session_id, text, result.interpretation, page_vehicle)

    async def _route(
        self, session_id: str, text: str, reading: Interpretation, page_vehicle: str | None
    ) -> TurnReply:
        intent = reading.intent
        if intent is not Intent.OTHER:
            await _say("checking")
        shown = self._shown(session_id)
        # interpret() has already refused a position past the cars shown.
        chosen = list(dict.fromkeys(shown[place - 1]["id"] for place in reading.vehicle_refs))
        if intent in WORKFLOW_INTENTS:
            return await self._start(session_id, text, reading, page_vehicle, chosen)
        if intent is Intent.VEHICLE_SEARCH:
            return await self._search(session_id, text, reading)
        if intent is Intent.COMPARE_VEHICLES:
            return await self._compare(session_id, chosen or [item["id"] for item in shown])
        if intent is Intent.VEHICLE_STATUS:
            vehicle = chosen[0] if chosen else page_vehicle
            if vehicle:
                return await self._status(session_id, text, vehicle)
            return await self._menu(session_id, NO_CAR)
        if intent is Intent.OPENING_HOURS:
            return await self._hours(session_id, reading.dealership)
        if intent is Intent.NEW_CAR_OFFERS:
            filters = reading.filters
            return await self._offers(session_id, text, filters.make if filters else None)
        if intent is Intent.SERVICE_TYPES:
            return await self._services(session_id, text)
        # Deny by default (D-027): with no tool results there is nothing to check a reply against,
        # and a value check cannot catch a claim such as "no credit check". Code writes this one.
        return await self._menu(session_id, GREETING)

    async def _start(
        self,
        session_id: str,
        text: str,
        reading: Interpretation,
        page_vehicle: str | None,
        chosen: Sequence[str] = (),
    ) -> TurnReply:
        kind = WORKFLOW_INTENTS[reading.intent]
        store = self._service.store
        if kind in OFFERED:
            store.note(
                session_id, "workflow_offered", Source.LLM, datetime.now(timezone.utc),
                workflow=kind.value,
            )
            button = next(item for item in WORKFLOW_BUTTONS if item.id == kind.value)
            return await self._finish(
                session_id, OFFERED[kind], TextSource.TEMPLATE, actions=[button]
            )
        # The machine only accepts a start from the customer's channel. This note keeps the record
        # honest: the customer typed, and it was the model's reading of that text that chose this.
        store.note(
            session_id, "workflow_from_message", Source.LLM, datetime.now(timezone.utc),
            workflow=kind.value,
        )
        view = await self._service.start(session_id, kind)
        if page_vehicle:
            found = await self._client.get_vehicle(page_vehicle)
            if isinstance(found, o.VehicleFound):
                view = await self._service.page_value(session_id, "vehicle_id", page_vehicle)
        proposals = [Proposal(name=item.name, value=item.value) for item in reading.slots]
        if chosen and "vehicle_id" in DEFINITIONS[kind].slot_names:
            # A car from the list, through the model's channel, so the customer still confirms it.
            proposals.append(Proposal(name="vehicle_id", value=chosen[0]))
        if proposals:
            view = await self._service.propose(session_id, proposals, text)
        return await self._in_workflow(session_id, view)

    async def _search(self, session_id: str, text: str, reading: Interpretation) -> TurnReply:
        filters = reading.filters
        changes: dict[str, object] = {}
        if filters:
            changes = {
                name: value
                for name in ("q", "make", "fuel_type", "body_style", "transmission")
                if (value := getattr(filters, name)) is not None
            }
            if filters.max_price_pounds is not None:
                changes["max_price_pence"] = filters.max_price_pounds * 100
        if reading.dealership:
            changes["dealership_id"] = f"northstar-{reading.dealership}"
        memory = self._service.store.recall(session_id) if reading.refine else None
        base = VehicleSearch.model_validate(memory["search"]) if memory else VehicleSearch()
        # Validated again, so a stored search and the model's changes are both checked here.
        search = VehicleSearch.model_validate({**base.model_dump(), **changes})
        budget = None if search.max_price_pence is None else search.max_price_pence // 100
        return await self._stock_reply(session_id, text, search, budget)

    async def _stock_reply(
        self, session_id: str, text: str, search: VehicleSearch, budget: int | None
    ) -> TurnReply:
        found = await self._client.search_stock(search)
        if not isinstance(found, o.StockSearched):
            return await self._menu(session_id, PLATFORM_DOWN)
        vehicles = _stock(found)
        self._remember(session_id, search, vehicles)
        facts = facts_from([found], site_url=self._site)
        if budget is not None:
            facts.prices.add(budget * 100)  # The filter holds it, so repeating it is safe.
        if not vehicles:
            # Nothing to describe, so the model would only be inventing.
            return await self._finish(session_id, NO_STOCK, TextSource.TEMPLATE, outcomes=[found])
        template = f"I found {len(vehicles)} matching cars:\n{_list_lines(vehicles)}"
        data: dict[str, object] = {
            "total_matches": len(vehicles),
            "shown_on_cards": _summaries(vehicles[:MAX_LISTED]),
        }
        return await self._written(
            session_id,
            text,
            outcomes=[found],
            data=data,
            template=template,
            vehicles=vehicles,
            facts=facts,
        )

    async def _status(self, session_id: str, text: str, vehicle_id: str) -> TurnReply:
        status = await self._client.get_vehicle_status(vehicle_id)
        found = await self._client.get_vehicle(vehicle_id)
        if not isinstance(status, o.VehicleStatus) or not isinstance(found, o.VehicleFound):
            return await self._menu(session_id, PLATFORM_DOWN)
        template = f"{_title(found.vehicle)} is currently {status.availability}."
        data: dict[str, object] = {
            "vehicle": _summaries([found.vehicle])[0],
            "can_book_test_drive": status.can_book_test_drive,
            "can_register_interest": status.can_register_interest,
        }
        return await self._written(
            session_id,
            text,
            outcomes=[status, found],
            data=data,
            template=template,
            vehicles=[found.vehicle],
        )

    async def _compare(self, session_id: str, vehicle_ids: Sequence[str]) -> TurnReply:
        """Written by code from a live read of each car. A comparison is all facts, so there is
        nothing for the model to add, and plenty for it to get wrong."""
        if len(vehicle_ids) < 2:
            return await self._finish(session_id, NO_COMPARE, TextSource.TEMPLATE)
        found = [await self._client.get_vehicle(item) for item in vehicle_ids]
        if not all(isinstance(item, o.VehicleFound) for item in found):
            return await self._menu(session_id, PLATFORM_DOWN)
        vehicles = [item.vehicle for item in found if isinstance(item, o.VehicleFound)]
        return await self._finish(
            session_id,
            "\n\n".join(_comparison(item) for item in vehicles),
            TextSource.TEMPLATE,
            outcomes=found,
            vehicles=vehicles,
        )

    async def _hours_lines(self, town: str | None) -> list[str] | None:
        """Hours are always written by code (CLAUDE.md). A real model mixed up departments."""
        sites = await self._client.list_dealerships()
        if not isinstance(sites, o.DealershipsListed):
            return None
        lines: list[str] = []
        for site in sites.dealerships:
            if town and site.town.casefold() != town:
                continue
            found = await self._client.get_opening_hours(site.id)
            if not isinstance(found, o.OpeningHoursFound):
                return None
            lines.append(_site_hours(site, found.hours))
        return lines

    async def _hours(self, session_id: str, town: str | None) -> TurnReply:
        lines = await self._hours_lines(town)
        if lines is None:
            return await self._menu(session_id, PLATFORM_DOWN)
        return await self._finish(session_id, "\n\n".join(lines), TextSource.TEMPLATE)

    async def _offers(self, session_id: str, text: str, make: str | None) -> TurnReply:
        found = await self._client.list_offers(make=make)
        if not isinstance(found, o.OffersListed):
            return await self._menu(session_id, PLATFORM_DOWN)
        # A finance figure must never appear without its notice.
        for kind in notices_for(None, [found]):
            if await self._notices.text(kind) is None:
                return await self._finish(session_id, FINANCE_WITHHELD, TextSource.TEMPLATE)
        lines = [
            f"{item.title}: {pounds(item.monthly_price_pence)} a month, "
            f"{pounds(item.upfront_payment_pence)} upfront, {item.term_months} months, "
            f"{item.product_type}."
            for item in found.offers
        ]
        if not lines:
            return await self._finish(session_id, NO_OFFERS, TextSource.TEMPLATE, outcomes=[found])
        template = "\n".join(lines)
        data: dict[str, object] = {"offers": lines}
        return await self._written(
            session_id, text, outcomes=[found], data=data, template=template
        )

    async def _services(self, session_id: str, text: str) -> TurnReply:
        found = await self._client.list_service_types()
        sites = await self._client.list_workshop_locations()
        if not isinstance(found, o.ServiceTypesListed) or not isinstance(
            sites, o.WorkshopLocationsListed
        ):
            return await self._menu(session_id, PLATFORM_DOWN)
        lines = [
            f"{item.name}: "
            + ("price on request" if item.price_from_pence is None else f"from {pounds(item.price_from_pence)}")
            for item in found.service_types
        ]
        # Addresses are written by code after the reply, like hours, so they cannot be garbled.
        workshops = "Our workshops:\n" + "\n".join(_address(site) for site in sites.locations)
        data: dict[str, object] = {"services": lines}
        reply = await self._written(
            session_id, text, outcomes=[found], data=data, template="\n".join(lines)
        )
        return reply.model_copy(update={"text": f"{reply.text}\n\n{workshops}"})

    async def _action(self, session_id: str, action_id: str) -> TurnReply:
        if action_id in BUTTON_WORKFLOWS:
            view = await self._service.start(session_id, BUTTON_WORKFLOWS[action_id])
            return await self._in_workflow(session_id, view)
        if action_id == "find_stock":
            search = VehicleSearch(sort="newest")
            found = await self._client.search_stock(search)
            if not isinstance(found, o.StockSearched):
                return await self._menu(session_id, PLATFORM_DOWN)
            vehicles = _stock(found)
            self._remember(session_id, search, vehicles)
            return await self._finish(
                session_id,
                f"Here are some of our {len(vehicles)} cars:\n{_list_lines(vehicles)}",
                TextSource.TEMPLATE,
                outcomes=[found],
                vehicles=vehicles,
            )
        if action_id == "opening_hours":
            return await self._hours(session_id, None)
        if action_id == "new_car_offers":
            return await self._offer_button(session_id)
        return await self._menu(session_id, GREETING)

    async def _offer_button(self, session_id: str) -> TurnReply:
        found = await self._client.list_offers()
        if not isinstance(found, o.OffersListed):
            return await self._menu(session_id, PLATFORM_DOWN)
        for kind in notices_for(None, [found]):
            if await self._notices.text(kind) is None:
                return await self._finish(session_id, FINANCE_WITHHELD, TextSource.TEMPLATE)
        lines = [
            f"{item.title}: {pounds(item.monthly_price_pence)} a month."
            for item in found.offers
        ]
        return await self._finish(
            session_id, "\n".join(lines), TextSource.TEMPLATE, outcomes=[found]
        )

