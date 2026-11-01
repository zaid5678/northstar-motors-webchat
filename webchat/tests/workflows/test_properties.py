"""Illegal states must be unreachable, whatever order events arrive in.

Two approaches: an exhaustive walk of every reachable state from a fixed event catalogue, and
random sequences that also move the clock and replay stale tokens.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from northstar_webchat.dealership import outcomes as o
from northstar_webchat.workflows.machine import (
    LOOKUP_OUTCOMES,
    READ_ONLY_OUTCOMES,
    WRITE_OUTCOMES,
    transition,
    usable_values,
)
from northstar_webchat.workflows.types import (
    DEFINITIONS,
    EDITABLE_PHASES,
    MAX_FAILED_ATTEMPTS,
    TERMINAL_PHASES,
    VERIFICATION_IDLE_LIMIT,
    AbandonWorkflow,
    CheckPreconditions,
    ConfirmAction,
    DetailsRejected,
    Event,
    IdentityCheckUnavailable,
    IdentityFormatError,
    IdentityMismatch,
    IdentitySubmitted,
    IdentityVerified,
    Incoming,
    LookUpBooking,
    NoticeShown,
    Phase,
    PreconditionFailed,
    PreconditionsPassed,
    Provenance,
    ReadBackBooking,
    ReadBackResult,
    ResubmitRequested,
    Session,
    ShowBooking,
    SlotConfirmed,
    SlotLost,
    SlotProposed,
    SlotRejected,
    Source,
    StartWorkflow,
    SubmitWrite,
    Transition,
    WorkflowKind,
    WriteCompleted,
    WriteFailed,
    WriteStep,
    WriteUnknown,
)

from .conftest import OTHER_REF_HASH, SECRET, START, sample_value, submitted

KINDS = list(WorkflowKind)
CUSTOMER, LLM, PAGE, PLATFORM, SYSTEM = (
    Source.CUSTOMER,
    Source.LLM,
    Source.PAGE,
    Source.PLATFORM,
    Source.SYSTEM,
)


def incoming(source: Source, event: Event, at: Any = START, n: int = 0) -> Incoming:
    return Incoming(source=source, event=event, at=at, token=f"t{n}", idempotency_key=f"k{n}")


def needs_filling(session: Session) -> list[tuple[Source, Event]]:
    """Events that complete the active workflow, in one step each, for the state walk."""
    if session.active is None:
        return []
    definition = DEFINITIONS[session.active.kind]
    names = definition.required + definition.any_of[:1]
    found: list[tuple[Source, Event]] = []
    for name in names:
        if name not in session.active.slots:
            value = sample_value(name, session.active.kind)
            found.append((CUSTOMER, SlotProposed(name=name, value=value)))
    return found[:1]


FIXED_EVENTS: list[tuple[Source, Event]] = [
    (CUSTOMER, AbandonWorkflow()),
    (CUSTOMER, submitted()),
    (CUSTOMER, ResubmitRequested()),
    (CUSTOMER, ConfirmAction(token="forged")),
    (PLATFORM, IdentityVerified(booking_id="wsb-1", cancelled=False)),
    (PLATFORM, IdentityVerified(booking_id="wsb-1", cancelled=True)),
    (PLATFORM, IdentityMismatch()),
    (PLATFORM, IdentityFormatError(fields=("phone",))),
    (PLATFORM, IdentityCheckUnavailable()),
    (PLATFORM, WriteCompleted()),
    (PLATFORM, WriteFailed(reason="not_sent")),
    (PLATFORM, WriteUnknown(reason="no_response")),
    (PLATFORM, SlotLost(slot_id="s", unbookable=True)),
    (PLATFORM, DetailsRejected(slots=("phone",))),
    (PLATFORM, ReadBackResult(applied=True)),
    (PLATFORM, ReadBackResult(applied=False)),
    (SYSTEM, PreconditionsPassed()),
    (SYSTEM, PreconditionsPassed(service_change=True)),
    (SYSTEM, PreconditionFailed(reason="gone", clear=("slot_id",))),
    (SYSTEM, PreconditionFailed(reason="vehicle_reserved")),
    (SYSTEM, NoticeShown(notice="privacy_statement")),
    (SYSTEM, NoticeShown(notice="cancel_warning")),
    # Events arriving from a channel that must not produce them.
    (LLM, ConfirmAction(token="t0")),
    (LLM, IdentityVerified(booking_id="wsb-1", cancelled=False)),
    (LLM, WriteCompleted()),
    (LLM, AbandonWorkflow()),
    (LLM, submitted()),
    (CUSTOMER, IdentityVerified(booking_id="wsb-1", cancelled=False)),
    (CUSTOMER, WriteCompleted()),
    (SYSTEM, ConfirmAction(token="t0")),
    (PLATFORM, ConfirmAction(token="t0")),
    (PAGE, ConfirmAction(token="t0")),
]


BOOKING_KINDS = (
    WorkflowKind.BOOKING_RETRIEVAL,
    WorkflowKind.WORKSHOP_AMEND,
    WorkflowKind.WORKSHOP_CANCEL,
)
GROUPS = [BOOKING_KINDS] + [(kind,) for kind in KINDS if kind not in BOOKING_KINDS]


def events_for(session: Session, kinds: tuple[WorkflowKind, ...]) -> list[tuple[Source, Event]]:
    events = list(FIXED_EVENTS) + needs_filling(session)
    if session.active is not None:
        if session.active.pending is not None:
            events.append((CUSTOMER, ConfirmAction(token=session.active.pending.token)))
        name = next(iter(sorted(DEFINITIONS[session.active.kind].slot_names)), None)
        if name is not None:
            events.append((LLM, SlotProposed(name=name, value="guess", customer_text="hm")))
            events.append((CUSTOMER, SlotConfirmed(name=name)))
            events.append((CUSTOMER, SlotRejected(name=name)))
            events.append((CUSTOMER, SlotProposed(name=name, value="changed")))
    events += [(CUSTOMER, StartWorkflow(kind=kind)) for kind in kinds]
    return events


def key_of(session: Session) -> str:
    return session.model_dump_json()


Edge = tuple[str, Source, Event, Transition]


MAX_WALK_STATES = 50_000


def walk() -> tuple[dict[str, Session], list[Edge]]:
    """Every state reachable from the catalogue. Each workflow is walked alone, except the three
    booking workflows, which share verification. Parked workflows are not expanded, because
    `unresolved` could otherwise grow without end."""
    seen: dict[str, Session] = {}
    edges: list[Edge] = []
    for kinds in GROUPS:
        start = Session()
        seen[key_of(start)] = start
        queue = deque([start])
        visited = {key_of(start)}
        while queue:
            session = queue.popleft()
            if session.unresolved:
                continue
            for source, event in events_for(session, kinds):
                result = transition(session, incoming(source, event))
                edges.append((key_of(session), source, event, result))
                key = key_of(result.session)
                seen.setdefault(key, result.session)
                assert len(seen) < MAX_WALK_STATES, "the state space has stopped being finite"
                if key not in visited:
                    visited.add(key)
                    queue.append(result.session)
    return seen, edges


@pytest.fixture(scope="module")
def graph() -> tuple[dict[str, Session], list[Edge]]:
    return walk()


class TestExhaustiveWalk:
    def test_the_walk_reaches_every_phase_of_every_workflow(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, _ = graph
        reached = {(s.active.kind, s.active.phase) for s in seen.values() if s.active}
        assert (WorkflowKind.TEST_DRIVE, Phase.COMPLETED) in reached
        assert (WorkflowKind.WORKSHOP_AMEND, Phase.TWO_BOOKINGS) in reached
        assert (WorkflowKind.WORKSHOP_CANCEL, Phase.READING_BACK) in reached
        assert (WorkflowKind.BOOKING_RETRIEVAL, Phase.LOCKED) in reached
        assert {phase for _, phase in reached} == set(Phase)

    def test_a_rejected_event_changes_nothing(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        for key, _, _, result in graph[1]:
            if not result.accepted:
                assert key_of(result.session) == key

    def test_events_from_the_wrong_channel_are_always_rejected(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        for _, source, event, result in graph[1]:
            if source is LLM and not isinstance(event, SlotProposed):
                assert not result.accepted
            if source is not PLATFORM and isinstance(
                event, (IdentityVerified, WriteCompleted, WriteUnknown, ReadBackResult)
            ):
                assert not result.accepted
            if source is not CUSTOMER and isinstance(event, ConfirmAction):
                assert not result.accepted

    def test_only_a_real_match_or_live_verification_shows_a_booking(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, edges = graph
        for key, source, event, result in edges:
            if any(isinstance(item, ShowBooking) for item in result.effects):
                verified_now = source is PLATFORM and isinstance(event, IdentityVerified)
                assert verified_now or seen[key].verification is not None

    def test_every_write_follows_confirmation(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, edges = graph
        for key, source, event, result in edges:
            before = seen[key].active
            for effect in result.effects:
                if isinstance(effect, CheckPreconditions):
                    assert source is CUSTOMER and isinstance(event, ConfirmAction)
                    assert before is not None
                    assert before.phase is Phase.AWAITING_CONFIRMATION
                if isinstance(effect, SubmitWrite):
                    assert before is not None
                    assert before.action is not None
                    assert before.phase in {
                        Phase.RECHECKING,
                        Phase.EXECUTING,
                        Phase.OUTCOME_UNKNOWN,
                    }

    def test_a_write_never_uses_an_inferred_or_missing_slot(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        for _, _, _, result in graph[1]:
            active = result.session.active
            if active is None:
                continue
            if active.phase in {Phase.AWAITING_CONFIRMATION, Phase.RECHECKING, Phase.EXECUTING}:
                definition = DEFINITIONS[active.kind]
                assert set(definition.required) <= set(active.slots)
                assert not definition.any_of or set(definition.any_of) & set(active.slots)
                assert all(s.provenance is not Provenance.INFERRED for s in active.slots.values())
                assert not active.offers
                assert set(definition.notices) <= active.notices_shown

    def test_booking_changes_need_verification_for_collecting_onwards(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        for session in graph[0].values():
            active = session.active
            if active and DEFINITIONS[active.kind].needs_verification:
                if active.phase in EDITABLE_PHASES:
                    assert session.verification is not None
                    assert not session.verification.cancelled

    def test_a_terminal_workflow_never_changes_unless_replaced(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, edges = graph
        for key, _, event, result in edges:
            before = seen[key].active
            if before and before.phase in TERMINAL_PHASES and not isinstance(event, StartWorkflow):
                assert result.session.active == before

    def test_unkeyed_writes_are_never_resent(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, edges = graph
        for key, _, event, result in edges:
            before = seen[key].active
            if isinstance(event, ResubmitRequested) and before and result.accepted:
                if not DEFINITIONS[before.kind].keyed and before.step is WriteStep.PRIMARY:
                    assert not any(isinstance(e, SubmitWrite) for e in result.effects)
                    assert any(isinstance(e, ReadBackBooking) for e in result.effects)

    def test_a_token_exists_only_while_awaiting_confirmation(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        for session in graph[0].values():
            active = session.active
            if active is not None:
                assert (active.pending is not None) == (
                    active.phase is Phase.AWAITING_CONFIRMATION
                )

    def test_every_state_can_reach_the_end(
        self, graph: tuple[dict[str, Session], list[Edge]]
    ) -> None:
        seen, edges = graph
        backwards: dict[str, set[str]] = {}
        for key, _, _, result in edges:
            backwards.setdefault(key_of(result.session), set()).add(key)
        done = {
            key
            for key, session in seen.items()
            if session.active is None
            or session.active.phase in TERMINAL_PHASES
            or session.unresolved
        }
        queue = deque(done)
        while queue:
            for earlier in backwards.get(queue.popleft(), set()):
                if earlier not in done:
                    done.add(earlier)
                    queue.append(earlier)
        stuck = [
            (seen[key].active.kind, seen[key].active.phase)
            for key in seen
            if key not in done and seen[key].active
        ]
        assert not stuck, stuck[:5]


def test_every_client_outcome_is_classified() -> None:
    def subclasses(cls: type) -> set[type]:
        found = set(cls.__subclasses__())
        for child in cls.__subclasses__():
            found |= subclasses(child)
        return found

    classified = set(LOOKUP_OUTCOMES) | set(WRITE_OUTCOMES) | set(READ_ONLY_OUTCOMES)
    assert subclasses(o.Outcome) == classified


class Random:
    """Plays a list of operations, checking the invariants after every step."""

    def __init__(self) -> None:
        self.session = Session()
        self.now = START
        self.n = 0
        self.tokens: list[str] = []

    def send(self, source: Source, event: Event) -> None:
        self.n += 1
        before = self.session
        locked = before.locked_until is not None and before.locked_until > self.now
        result = transition(before, incoming(source, event, self.now, self.n))
        self.session = result.session
        if not result.accepted:
            assert result.session == before
        active = self.session.active
        if active is not None:
            assert (active.pending is not None) == (active.phase is Phase.AWAITING_CONFIRMATION)
        if before.active and before.active.pending:
            self.tokens.append(before.active.pending.token)
        for effect in result.effects:
            if isinstance(effect, LookUpBooking):
                assert not locked
                assert isinstance(event, IdentitySubmitted) and source is CUSTOMER
            if isinstance(effect, ShowBooking):
                live = (
                    before.verification is not None
                    and self.now - before.verification.last_activity_at < VERIFICATION_IDLE_LIMIT
                )
                assert live or (source is PLATFORM and isinstance(event, IdentityVerified))
        assert self.session.failed_attempts <= MAX_FAILED_ATTEMPTS
        if self.session.active is not None:
            usable = usable_values(self.session.active)
            for name, slot in self.session.active.slots.items():
                assert (name in usable) == (slot.provenance is not Provenance.INFERRED)
        assert SECRET not in self.session.model_dump_json()
        assert Session.model_validate_json(self.session.model_dump_json()) == self.session


def op(play: Callable[[Random, Any], None], value: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return value.map(lambda item: (play, item))


def do_start(game: Random, item: tuple[WorkflowKind, bool]) -> None:
    game.send(
        CUSTOMER,
        StartWorkflow(kind=item[0], booking_reference_hash=OTHER_REF_HASH if item[1] else None),
    )


def do_fill(game: Random, source: Source) -> None:
    if game.session.active is None:
        return
    kind = game.session.active.kind
    definition = DEFINITIONS[kind]
    for name in definition.required + definition.any_of[:1]:
        game.send(source, SlotProposed(name=name, value=sample_value(name, kind), customer_text="x"))


def do_confirm(game: Random, stale: bool) -> None:
    active = game.session.active
    if stale and game.tokens:
        game.send(CUSTOMER, ConfirmAction(token=game.tokens[0]))
    elif active and active.pending:
        game.send(CUSTOMER, ConfirmAction(token=active.pending.token))


def do_event(game: Random, item: tuple[Source, Event]) -> None:
    game.send(*item)


def do_verify(game: Random, cancelled: bool) -> None:
    game.send(CUSTOMER, submitted())
    game.send(PLATFORM, IdentityVerified(booking_id="wsb-1", cancelled=cancelled))


def do_wait(game: Random, minutes: int) -> None:
    game.now += timedelta(minutes=minutes)


OPERATIONS = st.one_of(
    op(do_start, st.tuples(st.sampled_from(KINDS), st.booleans())),
    op(do_fill, st.sampled_from([CUSTOMER, LLM])),
    op(do_confirm, st.booleans()),
    op(do_verify, st.booleans()),
    op(do_event, st.sampled_from(FIXED_EVENTS)),
    op(do_wait, st.sampled_from([1, 9, 16, 60])),
)


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(st.lists(OPERATIONS, max_size=60))
def test_random_sequences_keep_every_invariant(operations: list[Any]) -> None:
    game = Random()
    for play, item in operations:
        play(game, item)
