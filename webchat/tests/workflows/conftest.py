"""A small driver for feeding events to the pure machine."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import SecretStr

from northstar_webchat.workflows.machine import transition
from northstar_webchat.workflows.types import (
    DEFINITIONS,
    Effect,
    Event,
    IdentityDetails,
    IdentitySubmitted,
    IdentityVerified,
    Incoming,
    NoticeShown,
    Phase,
    PreconditionsPassed,
    RequestConfirmation,
    Session,
    SlotProposed,
    SlotScalar,
    Source,
    StartWorkflow,
    Transition,
    Workflow,
    WorkflowKind,
    ConfirmAction,
)

START = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SECRET = "07700900123"
REF_HASH = "ref-hash-a"
OTHER_REF_HASH = "ref-hash-b"


def identity() -> IdentityDetails:
    return IdentityDetails(
        reference=SecretStr("WORK-10001"),
        last_name=SecretStr("Taylor"),
        registration=SecretStr("AB12 CDE"),
        phone=SecretStr(SECRET),
    )


def submitted(reference_hash: str = REF_HASH, locked_until: datetime | None = None) -> IdentitySubmitted:
    return IdentitySubmitted(
        identity=identity(), reference_hash=reference_hash, reference_locked_until=locked_until
    )


def sample_value(name: str, kind: WorkflowKind) -> SlotScalar:
    allowed = DEFINITIONS[kind].allowed.get(name)
    if allowed:
        return sorted(allowed)[0]
    return 1000 if name == "mileage" else f"{name}-value"


@dataclass
class Driver:
    """Feeds events, tracks the clock, and keeps every result for assertions."""

    session: Session = field(default_factory=Session)
    now: datetime = START
    counter: int = 0
    last: Transition | None = None

    def send(self, source: Source, event: Event) -> Transition:
        self.counter += 1
        result = transition(
            self.session,
            Incoming(
                source=source,
                event=event,
                at=self.now,
                token=f"token-{self.counter}",
                idempotency_key=f"key-{self.counter}",
            ),
        )
        self.session = result.session
        self.last = result
        return result

    def customer(self, event: Event) -> Transition:
        return self.send(Source.CUSTOMER, event)

    def platform(self, event: Event) -> Transition:
        return self.send(Source.PLATFORM, event)

    def system(self, event: Event) -> Transition:
        return self.send(Source.SYSTEM, event)

    def llm(self, event: Event) -> Transition:
        return self.send(Source.LLM, event)

    def send_page(self, name: str, value: SlotScalar, *, validated: bool = True) -> Transition:
        return self.send(Source.PAGE, SlotProposed(name=name, value=value, validated=validated))

    def wait(self, delta: timedelta) -> None:
        self.now += delta

    @property
    def active(self) -> Workflow:
        assert self.session.active is not None
        return self.session.active

    @property
    def phase(self) -> Phase:
        return self.active.phase

    def effects(self, kind: type[Effect]) -> list[Effect]:
        assert self.last is not None
        return [item for item in self.last.effects if isinstance(item, kind)]

    @property
    def token(self) -> str:
        assert self.active.pending is not None
        return self.active.pending.token

    def fill(self, names: Iterable[str] | None = None) -> None:
        definition = DEFINITIONS[self.active.kind]
        for notice in definition.notices:
            self.system(NoticeShown(notice=notice))
        chosen = definition.required if names is None else tuple(names)
        for name in chosen:
            self.customer(SlotProposed(name=name, value=sample_value(name, self.active.kind)))

    def start(self, kind: WorkflowKind, **fields: object) -> Transition:
        return self.customer(StartWorkflow(kind=kind, **fields))  # type: ignore[arg-type]

    def verify(self, *, booking_id: str = "wsb-1", cancelled: bool = False) -> None:
        self.customer(submitted())
        self.platform(IdentityVerified(booking_id=booking_id, cancelled=cancelled))

    def ready(self, kind: WorkflowKind) -> None:
        """Bring a workflow to its confirmation card."""
        self.start(kind)
        if DEFINITIONS[kind].needs_verification:
            self.verify()
        self.fill()
        if kind is WorkflowKind.WORKSHOP_AMEND:
            self.customer(SlotProposed(name="mileage", value=2000))

    def to_write(self, kind: WorkflowKind) -> None:
        self.ready(kind)
        self.customer(ConfirmAction(token=self.token))
        self.system(PreconditionsPassed())


def confirmation_values(result: Transition) -> dict[str, SlotScalar]:
    cards = [item for item in result.effects if isinstance(item, RequestConfirmation)]
    assert len(cards) == 1
    return cards[0].values
