"""Workflow transitions: a pure function from (session, event) to (session, effects).

Nothing here does I/O or reads the clock. An event from the wrong channel, or one with no entry for
the current phase, is rejected and leaves the session exactly as it was (T2). Provenance is worked
out here from the event's source, so a caller cannot claim a value came from the customer (S2).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from datetime import datetime

from pydantic.alias_generators import to_snake

from ..dealership import outcomes as o
from .types import (
    ABANDONABLE_PHASES,
    CONFIRMATION_TTL,
    DEFINITIONS,
    EDITABLE_PHASES,
    LOCK_DURATION,
    MAX_FAILED_ATTEMPTS,
    PROVENANCE_RANK,
    TERMINAL_PHASES,
    VERIFICATION_IDLE_LIMIT,
    AbandonWorkflow,
    Action,
    AskToAbandon,
    AskToConfirmSlot,
    CheckPreconditions,
    ConfirmAction,
    DetailsRejected,
    Effect,
    Event,
    IdentityCheckUnavailable,
    IdentityFormatError,
    IdentityMismatch,
    IdentitySubmitted,
    IdentityVerified,
    Incoming,
    LookUpBooking,
    MarkSlotUnbookable,
    NoticeShown,
    Pending,
    Phase,
    PreconditionFailed,
    PreconditionsPassed,
    Provenance,
    ReadBackBooking,
    ReadBackResult,
    RecordReferenceFailure,
    RequestConfirmation,
    ResubmitRequested,
    Session,
    ShowBooking,
    ShowNotice,
    SlotConfirmed,
    SlotLost,
    SlotProposed,
    SlotRejected,
    SlotScalar,
    SlotValue,
    Source,
    StartWorkflow,
    SubmitWrite,
    Transition,
    Verification,
    Workflow,
    WorkflowKind,
    WriteCompleted,
    WriteFailed,
    WriteInterrupted,
    WriteStep,
    WriteUnknown,
)

CUSTOMER = frozenset({Source.CUSTOMER})
PLATFORM = frozenset({Source.PLATFORM})
SYSTEM = frozenset({Source.SYSTEM})

# Which channel may produce each event (T4). The LLM can only ever propose a slot value.
ALLOWED_SOURCES: dict[type[Event], frozenset[Source]] = {
    StartWorkflow: CUSTOMER,
    AbandonWorkflow: CUSTOMER,
    SlotProposed: frozenset({Source.CUSTOMER, Source.LLM, Source.PAGE}),
    SlotConfirmed: CUSTOMER,
    SlotRejected: CUSTOMER,
    IdentitySubmitted: CUSTOMER,
    ConfirmAction: CUSTOMER,
    # SYSTEM is server-start recovery. It can only resend what the customer already confirmed.
    ResubmitRequested: frozenset({Source.CUSTOMER, Source.SYSTEM}),
    WriteInterrupted: SYSTEM,
    NoticeShown: SYSTEM,
    PreconditionsPassed: SYSTEM,
    PreconditionFailed: SYSTEM,
    IdentityVerified: PLATFORM,
    IdentityMismatch: PLATFORM,
    IdentityFormatError: PLATFORM,
    IdentityCheckUnavailable: frozenset({Source.PLATFORM, Source.SYSTEM}),
    WriteCompleted: PLATFORM,
    WriteFailed: PLATFORM,
    WriteUnknown: PLATFORM,
    SlotLost: PLATFORM,
    DetailsRejected: PLATFORM,
    ReadBackResult: PLATFORM,
}


def _normalise(value: object) -> str:
    return " ".join(str(value).casefold().split())


def provenance_is_user(value: SlotScalar, customer_text: str | None) -> bool:
    """True only if the customer's own message contains the value as a whole word or phrase."""
    if customer_text is None:
        return False
    return (
        re.search(rf"(?<!\w){re.escape(_normalise(value))}(?!\w)", _normalise(customer_text))
        is not None
    )


def usable_values(workflow: Workflow) -> dict[str, SlotScalar]:
    """The only values the pipeline may pass to a platform call, read or write (S3)."""
    return {
        name: slot.value
        for name, slot in workflow.slots.items()
        if slot.provenance is not Provenance.INFERRED
    }


def hash_reference(reference: str, pepper: bytes) -> str:
    """Keyed, because booking references are short enough to guess from a plain hash (V5)."""
    normalised = "".join(reference.split()).upper().encode()
    return hmac.new(pepper, normalised, hashlib.sha256).hexdigest()


def _provenance(source: Source, event: SlotProposed) -> Provenance:
    if source is Source.CUSTOMER:
        return Provenance.USER
    if source is Source.PAGE:
        return Provenance.PAGE_CONTEXT
    if provenance_is_user(event.value, event.customer_text):
        return Provenance.USER
    return Provenance.INFERRED


def _values(workflow: Workflow) -> dict[str, SlotScalar]:
    return {name: slot.value for name, slot in workflow.slots.items()}


def _digest(workflow: Workflow) -> str:
    content = json.dumps(
        {"kind": workflow.kind, "values": _values(workflow)}, sort_keys=True, default=str
    )
    return hashlib.sha256(content.encode()).hexdigest()


def _rejected(session: Session, reason: str, *effects: Effect) -> Transition:
    return Transition(session=session, accepted=False, effects=effects, reason=reason)


def _is_ready(workflow: Workflow) -> bool:
    definition = DEFINITIONS[workflow.kind]
    slots = workflow.slots
    return (
        all(name in slots for name in definition.required)
        and (not definition.any_of or any(name in slots for name in definition.any_of))
        and all(slot.provenance is not Provenance.INFERRED for slot in slots.values())
        and not workflow.offers
        and set(definition.notices) <= workflow.notices_shown
    )


def _advance(workflow: Workflow, incoming: Incoming) -> tuple[Workflow, tuple[Effect, ...]]:
    """Moves a complete, fully confirmed form to its confirmation card. Anything unfinished
    produces the prompts the customer needs instead."""
    if workflow.phase is not Phase.COLLECTING:
        return workflow, ()
    if not _is_ready(workflow):
        definition = DEFINITIONS[workflow.kind]
        prompts: list[Effect] = [
            ShowNotice(notice=notice)
            for notice in definition.notices
            if notice not in workflow.notices_shown
        ]
        names = [n for n, slot in workflow.slots.items() if slot.provenance is Provenance.INFERRED]
        prompts += [AskToConfirmSlot(name=name) for name in [*names, *workflow.offers]]
        return workflow, tuple(prompts)
    pending = Pending(
        token=incoming.token,
        digest=_digest(workflow),
        idempotency_key=incoming.idempotency_key,
        expires_at=incoming.at + CONFIRMATION_TTL,
    )
    ready = workflow.model_copy(update={"phase": Phase.AWAITING_CONFIRMATION, "pending": pending})
    return ready, (RequestConfirmation(token=pending.token, values=_values(ready)),)


def _reopened(workflow: Workflow, **changes: object) -> Workflow:
    """Back to collecting. Any confirmation card is void from this point (C3)."""
    return workflow.model_copy(
        update={
            "phase": Phase.COLLECTING,
            "pending": None,
            "action": None,
            "step": WriteStep.PRIMARY,
            "failure_reason": None,
            **changes,
        }
    )


def _without(slots: dict[str, SlotValue], names: Iterable[str]) -> dict[str, SlotValue]:
    removed = set(names)
    return {name: slot for name, slot in slots.items() if name not in removed}


def _applied(workflow: Workflow, name: str, value: SlotValue) -> Workflow:
    """Replaces a slot and clears whatever depended on it (C1, C2)."""
    dependants = DEFINITIONS[workflow.kind].depends_on.get(name, ())
    slots = _without(workflow.slots, dependants)
    offers = _without(workflow.offers, [name, *dependants])
    return _reopened(workflow, slots={**slots, name: value}, offers=offers)


def apply_expiry(session: Session, at: datetime) -> Session:
    """Applies the passage of time. A write in progress is not interrupted (V7)."""
    if session.locked_until is not None and at >= session.locked_until:
        active = session.active
        if active is not None and active.phase is Phase.LOCKED:
            active = active.model_copy(update={"phase": Phase.VERIFYING})
        session = session.model_copy(
            update={"locked_until": None, "failed_attempts": 0, "active": active}
        )
    verification = session.verification
    if verification is not None and at - verification.last_activity_at >= VERIFICATION_IDLE_LIMIT:
        active = session.active
        if (
            active is not None
            and DEFINITIONS[active.kind].needs_verification
            and active.phase in EDITABLE_PHASES
        ):
            active = active.model_copy(update={"phase": Phase.VERIFYING, "pending": None})
        session = session.model_copy(update={"verification": None, "active": active})
    return session


def _with(
    session: Session, workflow: Workflow, *effects: Effect, **changes: object
) -> Transition:
    updated = session.model_copy(update={"active": workflow, **changes})
    return Transition(session=updated, accepted=True, effects=effects)


def _collected(
    session: Session, workflow: Workflow, incoming: Incoming, *effects: Effect
) -> Transition:
    workflow, prompts = _advance(workflow, incoming)
    return _with(session, workflow, *effects, *prompts)


def _after_verification(
    session: Session, workflow: Workflow, incoming: Incoming, verification: Verification
) -> Transition:
    if workflow.kind is WorkflowKind.BOOKING_RETRIEVAL:
        done = workflow.model_copy(update={"phase": Phase.COMPLETED})
        return _with(session, done, ShowBooking(booking_id=verification.booking_id))
    if verification.cancelled:
        failed = workflow.model_copy(
            update={"phase": Phase.FAILED, "failure_reason": "booking_cancelled"}
        )
        return _with(session, failed)
    return _collected(session, workflow.model_copy(update={"phase": Phase.COLLECTING}), incoming)


def _on_start(session: Session, event: StartWorkflow, incoming: Incoming) -> Transition:
    active = session.active
    unresolved = session.unresolved
    if active is not None and active.phase is Phase.OUTCOME_UNKNOWN:
        # Moving on must not lose a write that may have happened.
        unresolved = (*unresolved, active)
    elif active is not None and active.phase in ABANDONABLE_PHASES:
        return _rejected(session, "workflow_active", AskToAbandon(kind=active.kind))
    elif active is not None and active.phase not in TERMINAL_PHASES:
        return _rejected(session, "write_in_progress")

    definition = DEFINITIONS[event.kind]
    carried = {
        name: SlotValue(value=value, provenance=Provenance.INFERRED, set_at=incoming.at)
        for name, value in event.carried.items()
        if name in definition.slot_names and name not in definition.gated
    }
    workflow = Workflow(kind=event.kind, phase=Phase.COLLECTING, slots=carried)
    session = session.model_copy(update={"unresolved": unresolved, "checking_reference": None})
    if not definition.needs_verification:
        return _collected(session, workflow, incoming)

    if session.locked_until is not None:
        return _with(session, workflow.model_copy(update={"phase": Phase.LOCKED}))
    verification = session.verification
    if verification is not None and event.booking_reference_hash not in (
        None,
        verification.reference_hash,
    ):
        session = session.model_copy(update={"verification": None})  # V8
        verification = None
    if verification is None:
        return _with(session, workflow.model_copy(update={"phase": Phase.VERIFYING}))
    return _after_verification(session, workflow, incoming, verification)


def _on_abandon(session: Session, workflow: Workflow) -> Transition:
    if workflow.phase not in ABANDONABLE_PHASES:
        return _rejected(session, "cannot_abandon")
    return _with(session, workflow.model_copy(update={"phase": Phase.ABANDONED, "pending": None}))


def _on_slot_proposed(
    session: Session, workflow: Workflow, event: SlotProposed, incoming: Incoming
) -> Transition:
    definition = DEFINITIONS[workflow.kind]
    name = event.name
    if workflow.phase not in EDITABLE_PHASES:
        return _rejected(session, "not_editable")
    if name not in definition.slot_names:
        return _rejected(session, "unknown_slot")
    if incoming.source is Source.PAGE and not event.validated:
        return _rejected(session, "page_value_not_validated")
    if not str(event.value).strip():
        return _rejected(session, "blank_value")
    allowed = definition.allowed.get(name)
    if allowed is not None and event.value not in allowed:
        return _rejected(session, "value_not_allowed")
    if name in definition.gated and set(definition.notices) - workflow.notices_shown:
        return _rejected(
            session, "notice_required", *(ShowNotice(notice=n) for n in definition.notices)
        )

    proposed = SlotValue(
        value=event.value, provenance=_provenance(incoming.source, event), set_at=incoming.at
    )
    current = workflow.slots.get(name)
    if current is not None and current.value == event.value:
        if PROVENANCE_RANK[proposed.provenance] <= PROVENANCE_RANK[current.provenance]:
            return _rejected(session, "unchanged")
        upgraded = workflow.model_copy(update={"slots": {**workflow.slots, name: proposed}})
        return _collected(session, upgraded, incoming)
    if current is not None and (
        PROVENANCE_RANK[proposed.provenance] < PROVENANCE_RANK[current.provenance]
    ):
        offered = _reopened(workflow, offers={**workflow.offers, name: proposed})
        return _collected(session, offered, incoming)  # C4
    return _collected(session, _applied(workflow, name, proposed), incoming)


def _on_slot_confirmed(
    session: Session, workflow: Workflow, event: SlotConfirmed, incoming: Incoming
) -> Transition:
    if workflow.phase not in EDITABLE_PHASES:
        return _rejected(session, "not_editable")
    offer = workflow.offers.get(event.name)
    current = workflow.slots.get(event.name)
    if offer is not None:
        accepted = offer.model_copy(update={"provenance": Provenance.USER})
        return _collected(session, _applied(workflow, event.name, accepted), incoming)
    if current is None or current.provenance is not Provenance.INFERRED:
        return _rejected(session, "nothing_to_confirm")
    confirmed = current.model_copy(update={"provenance": Provenance.USER})
    updated = workflow.model_copy(update={"slots": {**workflow.slots, event.name: confirmed}})
    return _collected(session, updated, incoming)


def _on_slot_rejected(
    session: Session, workflow: Workflow, event: SlotRejected, incoming: Incoming
) -> Transition:
    if workflow.phase not in EDITABLE_PHASES:
        return _rejected(session, "not_editable")
    if event.name in workflow.offers:
        return _collected(
            session,
            workflow.model_copy(update={"offers": _without(workflow.offers, [event.name])}),
            incoming,
        )
    current = workflow.slots.get(event.name)
    if current is None or current.provenance is not Provenance.INFERRED:
        return _rejected(session, "nothing_to_reject")
    updated = workflow.model_copy(update={"slots": _without(workflow.slots, [event.name])})
    return _collected(session, updated, incoming)


def _on_notice(
    session: Session, workflow: Workflow, event: NoticeShown, incoming: Incoming
) -> Transition:
    definition = DEFINITIONS[workflow.kind]
    if (
        workflow.phase is not Phase.COLLECTING
        or event.notice not in definition.notices
        or event.notice in workflow.notices_shown
    ):
        return _rejected(session, "notice_not_expected")
    shown = workflow.notices_shown | {event.notice}
    return _collected(session, workflow.model_copy(update={"notices_shown": shown}), incoming)


def _on_identity_submitted(
    session: Session, workflow: Workflow, event: IdentitySubmitted, incoming: Incoming
) -> Transition:
    if workflow.phase is not Phase.VERIFYING or session.locked_until is not None:
        return _rejected(session, "not_verifying")
    until = event.reference_locked_until
    if until is not None and until > incoming.at:
        return _with(
            session, workflow.model_copy(update={"phase": Phase.LOCKED}), locked_until=until
        )
    return _with(
        session,
        workflow.model_copy(update={"phase": Phase.CHECKING_IDENTITY}),
        LookUpBooking(identity=event.identity),
        checking_reference=event.reference_hash,
    )


def _on_identity_verified(
    session: Session, workflow: Workflow, event: IdentityVerified, incoming: Incoming
) -> Transition:
    if workflow.phase is not Phase.CHECKING_IDENTITY or session.checking_reference is None:
        return _rejected(session, "not_checking")
    verification = Verification(
        booking_id=event.booking_id,
        reference_hash=session.checking_reference,
        cancelled=event.cancelled,
        last_activity_at=incoming.at,
    )
    session = session.model_copy(
        update={"verification": verification, "failed_attempts": 0, "checking_reference": None}
    )
    return _after_verification(session, workflow, incoming, verification)


def _on_identity_mismatch(session: Session, workflow: Workflow, incoming: Incoming) -> Transition:
    if workflow.phase is not Phase.CHECKING_IDENTITY or session.checking_reference is None:
        return _rejected(session, "not_checking")
    attempts = session.failed_attempts + 1
    recorded = RecordReferenceFailure(reference_hash=session.checking_reference)
    if attempts >= MAX_FAILED_ATTEMPTS:
        return _with(
            session,
            workflow.model_copy(update={"phase": Phase.LOCKED}),
            recorded,
            failed_attempts=attempts,
            locked_until=incoming.at + LOCK_DURATION,
            checking_reference=None,
        )
    return _with(
        session,
        workflow.model_copy(update={"phase": Phase.VERIFYING}),
        recorded,
        failed_attempts=attempts,
        checking_reference=None,
    )


def _on_identity_not_checked(session: Session, workflow: Workflow) -> Transition:
    """A format error or an outage. Neither is a guess, so neither uses an attempt (Q3)."""
    if workflow.phase is not Phase.CHECKING_IDENTITY:
        return _rejected(session, "not_checking")
    return _with(
        session, workflow.model_copy(update={"phase": Phase.VERIFYING}), checking_reference=None
    )


def _on_confirm(
    session: Session, workflow: Workflow, event: ConfirmAction, incoming: Incoming
) -> Transition:
    pending = workflow.pending
    if workflow.phase is not Phase.AWAITING_CONFIRMATION or pending is None:
        return _rejected(session, "nothing_to_confirm")
    if not hmac.compare_digest(event.token, pending.token):
        return _rejected(session, "wrong_token")
    if pending.digest != _digest(workflow):
        return _rejected(session, "action_changed")
    if incoming.at >= pending.expires_at:
        renewed = pending.model_copy(
            update={"token": incoming.token, "expires_at": incoming.at + CONFIRMATION_TTL}
        )
        card = RequestConfirmation(token=renewed.token, values=_values(workflow))
        return _with(session, workflow.model_copy(update={"pending": renewed}), card)
    verification = session.verification
    booking_id = (
        verification.booking_id
        if verification is not None and DEFINITIONS[workflow.kind].needs_verification
        else None
    )
    action = Action(
        digest=pending.digest,
        idempotency_key=pending.idempotency_key,
        values=_values(workflow),
        booking_id=booking_id,
    )
    confirmed = workflow.model_copy(
        update={"phase": Phase.RECHECKING, "pending": None, "action": action}
    )
    return _with(
        session,
        confirmed,
        CheckPreconditions(kind=workflow.kind, values=action.values, booking_id=booking_id),
    )


def _submit(workflow: Workflow) -> SubmitWrite:
    action = workflow.action
    assert action is not None
    return SubmitWrite(
        kind=workflow.kind,
        step=workflow.step,
        values=action.values,
        idempotency_key=action.idempotency_key,
        booking_id=action.booking_id,
    )


def _on_preconditions_passed(
    session: Session, workflow: Workflow, event: PreconditionsPassed
) -> Transition:
    if workflow.phase is not Phase.RECHECKING:
        return _rejected(session, "not_rechecking")
    replacing = event.service_change and workflow.kind is WorkflowKind.WORKSHOP_AMEND
    step = WriteStep.BOOK_REPLACEMENT if replacing else WriteStep.PRIMARY
    executing = workflow.model_copy(update={"phase": Phase.EXECUTING, "step": step})
    return _with(session, executing, _submit(executing))


def _on_precondition_failed(
    session: Session, workflow: Workflow, event: PreconditionFailed, incoming: Incoming
) -> Transition:
    if workflow.phase is not Phase.RECHECKING:
        return _rejected(session, "not_rechecking")
    if not event.clear:
        failed = workflow.model_copy(
            update={"phase": Phase.FAILED, "failure_reason": event.reason}
        )
        return _with(session, failed)
    reopened = _reopened(
        workflow, slots=_without(workflow.slots, event.clear), failure_reason=event.reason
    )
    return _collected(session, reopened, incoming)


def _finished(session: Session, workflow: Workflow) -> Transition:
    done = workflow.model_copy(update={"phase": Phase.COMPLETED})
    cancelled_original = (
        workflow.kind is WorkflowKind.WORKSHOP_CANCEL or workflow.step is WriteStep.CANCEL_ORIGINAL
    )
    verification = session.verification
    if cancelled_original and verification is not None:
        session = session.model_copy(
            update={"verification": verification.model_copy(update={"cancelled": True})}
        )
    return _with(session, done)


def _on_write_completed(session: Session, workflow: Workflow) -> Transition:
    if workflow.phase is not Phase.EXECUTING:
        return _rejected(session, "not_executing")
    if workflow.step is WriteStep.BOOK_REPLACEMENT:
        next_step = workflow.model_copy(update={"step": WriteStep.CANCEL_ORIGINAL})
        return _with(session, next_step, _submit(next_step))
    return _finished(session, workflow)


def _ended_with_two_bookings(session: Session, workflow: Workflow, reason: str) -> Transition:
    ended = workflow.model_copy(update={"phase": Phase.TWO_BOOKINGS, "failure_reason": reason})
    return _with(session, ended)


def _on_write_failed(session: Session, workflow: Workflow, event: WriteFailed) -> Transition:
    if workflow.phase is not Phase.EXECUTING:
        return _rejected(session, "not_executing")
    if workflow.step is WriteStep.CANCEL_ORIGINAL:
        return _ended_with_two_bookings(session, workflow, event.reason)
    failed = workflow.model_copy(update={"phase": Phase.FAILED, "failure_reason": event.reason})
    return _with(session, failed)


def _on_write_unknown(session: Session, workflow: Workflow, event: WriteUnknown) -> Transition:
    if workflow.phase not in (Phase.EXECUTING, Phase.READING_BACK):
        return _rejected(session, "not_executing")
    if workflow.step is WriteStep.CANCEL_ORIGINAL:
        return _ended_with_two_bookings(session, workflow, event.reason)
    unknown = workflow.model_copy(
        update={"phase": Phase.OUTCOME_UNKNOWN, "failure_reason": event.reason}
    )
    return _with(session, unknown)


def _on_write_interrupted(session: Session, workflow: Workflow) -> Transition:
    if workflow.phase not in (Phase.EXECUTING, Phase.READING_BACK):
        return _rejected(session, "not_executing")
    unknown = workflow.model_copy(
        update={"phase": Phase.OUTCOME_UNKNOWN, "failure_reason": "interrupted"}
    )
    return _with(session, unknown)


def _on_slot_lost(
    session: Session, workflow: Workflow, event: SlotLost, incoming: Incoming
) -> Transition:
    name = "target_slot_id" if workflow.kind is WorkflowKind.WORKSHOP_AMEND else "slot_id"
    if (
        workflow.phase is not Phase.EXECUTING
        or workflow.step is WriteStep.CANCEL_ORIGINAL
        or name not in workflow.slots
    ):
        return _rejected(session, "no_slot_to_lose")
    effects: tuple[Effect, ...] = (
        (MarkSlotUnbookable(slot_id=event.slot_id),) if event.unbookable else ()
    )
    reopened = _reopened(
        workflow, slots=_without(workflow.slots, [name]), failure_reason="slot_lost"
    )
    return _collected(session, reopened, incoming, *effects)


def _on_details_rejected(
    session: Session, workflow: Workflow, event: DetailsRejected, incoming: Incoming
) -> Transition:
    if workflow.phase is not Phase.EXECUTING:
        return _rejected(session, "not_executing")
    if workflow.step is WriteStep.CANCEL_ORIGINAL:
        return _ended_with_two_bookings(session, workflow, "details_rejected")
    if not set(event.slots) & set(workflow.slots):
        failed = workflow.model_copy(
            update={"phase": Phase.FAILED, "failure_reason": "details_rejected"}
        )
        return _with(session, failed)
    reopened = _reopened(
        workflow, slots=_without(workflow.slots, event.slots), failure_reason="details_rejected"
    )
    return _collected(session, reopened, incoming)


def _on_resubmit(session: Session, workflow: Workflow) -> Transition:
    if workflow.phase is not Phase.OUTCOME_UNKNOWN or workflow.action is None:
        return _rejected(session, "nothing_to_resubmit")
    if not DEFINITIONS[workflow.kind].keyed and workflow.step is WriteStep.PRIMARY:
        # No key means a resend cannot be recognised as a repeat, so we only ever read (W5).
        reading = workflow.model_copy(update={"phase": Phase.READING_BACK})
        return _with(session, reading, ReadBackBooking(booking_id=workflow.action.booking_id))
    executing = workflow.model_copy(update={"phase": Phase.EXECUTING})
    return _with(session, executing, _submit(executing))


def _on_read_back(session: Session, workflow: Workflow, event: ReadBackResult) -> Transition:
    if workflow.phase is not Phase.READING_BACK:
        return _rejected(session, "not_reading_back")
    if event.applied:
        return _finished(session, workflow)
    failed = workflow.model_copy(update={"phase": Phase.FAILED, "failure_reason": "not_applied"})
    return _with(session, failed)


def _dispatch(session: Session, workflow: Workflow, incoming: Incoming) -> Transition:
    event = incoming.event
    if isinstance(event, AbandonWorkflow):
        return _on_abandon(session, workflow)
    if isinstance(event, SlotProposed):
        return _on_slot_proposed(session, workflow, event, incoming)
    if isinstance(event, SlotConfirmed):
        return _on_slot_confirmed(session, workflow, event, incoming)
    if isinstance(event, SlotRejected):
        return _on_slot_rejected(session, workflow, event, incoming)
    if isinstance(event, NoticeShown):
        return _on_notice(session, workflow, event, incoming)
    if isinstance(event, IdentitySubmitted):
        return _on_identity_submitted(session, workflow, event, incoming)
    if isinstance(event, IdentityVerified):
        return _on_identity_verified(session, workflow, event, incoming)
    if isinstance(event, IdentityMismatch):
        return _on_identity_mismatch(session, workflow, incoming)
    if isinstance(event, (IdentityFormatError, IdentityCheckUnavailable)):
        return _on_identity_not_checked(session, workflow)
    if isinstance(event, ConfirmAction):
        return _on_confirm(session, workflow, event, incoming)
    if isinstance(event, PreconditionsPassed):
        return _on_preconditions_passed(session, workflow, event)
    if isinstance(event, PreconditionFailed):
        return _on_precondition_failed(session, workflow, event, incoming)
    if isinstance(event, WriteCompleted):
        return _on_write_completed(session, workflow)
    if isinstance(event, WriteFailed):
        return _on_write_failed(session, workflow, event)
    if isinstance(event, WriteUnknown):
        return _on_write_unknown(session, workflow, event)
    if isinstance(event, WriteInterrupted):
        return _on_write_interrupted(session, workflow)
    if isinstance(event, SlotLost):
        return _on_slot_lost(session, workflow, event, incoming)
    if isinstance(event, DetailsRejected):
        return _on_details_rejected(session, workflow, event, incoming)
    if isinstance(event, ResubmitRequested):
        return _on_resubmit(session, workflow)
    if isinstance(event, ReadBackResult):
        return _on_read_back(session, workflow, event)
    return _rejected(session, "unknown_event")


def transition(session: Session, incoming: Incoming) -> Transition:
    event = incoming.event
    if incoming.source not in ALLOWED_SOURCES.get(type(event), frozenset()):
        return _rejected(session, "wrong_channel")
    current = apply_expiry(session, incoming.at)
    if isinstance(event, StartWorkflow):
        result = _on_start(current, event, incoming)
    elif current.active is None:
        result = _rejected(current, "no_active_workflow")
    else:
        result = _dispatch(current, current.active, incoming)
    if not result.accepted:
        return result.model_copy(update={"session": session})
    verification = result.session.verification
    if verification is None:
        return result
    touched = verification.model_copy(update={"last_activity_at": incoming.at})
    return result.model_copy(
        update={"session": result.session.model_copy(update={"verification": touched})}
    )


# Client outcomes to events (T5). Every outcome type must be in exactly one of these groups,
# which a test enforces, so a new outcome cannot be forgotten.

LOOKUP_OUTCOMES: tuple[type[o.Outcome], ...] = (
    o.WorkshopBookingFound,
    o.BookingNotMatched,
    o.InvalidDetails,
    o.PlatformUnavailable,
    o.MalformedResponse,
    o.IntegrationFault,
)

COMPLETED_OUTCOMES: tuple[type[o.Outcome], ...] = (
    o.EnquiryReceived,
    o.InterestRegistered,
    o.CallbackRequested,
    o.MessageReceived,
    o.EstimateGiven,
    o.TestDriveBooked,
    o.WorkshopBooked,
    o.BookingAmended,
    o.BookingCancelled,
)

FAILURE_REASONS: dict[type[o.Outcome], str] = {
    o.VehicleReserved: "vehicle_reserved",
    o.VehicleUnavailable: "vehicle_unavailable",
    o.InterestNotAccepted: "interest_not_accepted",
    o.BookingMismatch: "booking_mismatch",
    o.BookingNoLongerActive: "booking_no_longer_active",
    o.BookingAlreadyCancelled: "booking_cancelled",
    o.AmendNotApplied: "not_applied",
    o.CancelNotApplied: "not_applied",
    o.WriteNotSent: "not_sent",
    o.NotFound: "not_found",
    o.IntegrationFault: "integration_fault",
    o.PlatformUnavailable: "platform_unavailable",
    o.MalformedResponse: "malformed_response",
}

WRITE_OUTCOMES: tuple[type[o.Outcome], ...] = (
    *COMPLETED_OUTCOMES,
    *FAILURE_REASONS,
    o.SlotTaken,
    o.SlotInPast,
    o.SlotVehicleMismatch,
    o.SlotUnbookable,
    o.InvalidDetails,
    o.WriteOutcomeUnknown,
    o.EstimateWithoutNotice,
)

READ_ONLY_OUTCOMES: tuple[type[o.Outcome], ...] = (
    o.VehiclesListed,
    o.StockSearched,
    o.VehicleFound,
    o.VehicleStatus,
    o.OffersListed,
    o.OfferFound,
    o.OfferExpired,
    o.DealershipsListed,
    o.DealershipFound,
    o.OpeningHoursFound,
    o.ServiceTypesListed,
    o.BusinessInformationFound,
    o.TestDriveSlotsFound,
    o.TestDriveNotBookable,
    o.WorkshopSlotsFound,
    o.TestDriveBookingFound,
)


def lookup_event(outcome: o.Outcome) -> Event:
    if isinstance(outcome, o.WorkshopBookingFound):
        return IdentityVerified(
            booking_id=outcome.booking.id, cancelled=outcome.booking.is_cancelled
        )
    if isinstance(outcome, o.BookingNotMatched):
        return IdentityMismatch()
    if isinstance(outcome, o.InvalidDetails):
        return IdentityFormatError(fields=tuple(to_snake(name) for name in outcome.field_errors))
    if isinstance(outcome, (o.PlatformUnavailable, o.MalformedResponse, o.IntegrationFault)):
        return IdentityCheckUnavailable()
    raise TypeError(f"not a lookup outcome: {type(outcome).__name__}")


def write_event(outcome: o.Outcome) -> Event:
    if isinstance(outcome, COMPLETED_OUTCOMES):
        return WriteCompleted()
    if isinstance(outcome, (o.SlotTaken, o.SlotInPast, o.SlotVehicleMismatch)):
        return SlotLost(slot_id=outcome.slot_id, unbookable=False)
    if isinstance(outcome, o.SlotUnbookable):
        return SlotLost(slot_id=outcome.slot_id, unbookable=True)
    if isinstance(outcome, o.InvalidDetails):
        return DetailsRejected(slots=tuple(to_snake(name) for name in outcome.field_errors))
    if isinstance(outcome, o.WriteOutcomeUnknown):
        return WriteUnknown(reason=outcome.reason)
    if isinstance(outcome, o.EstimateWithoutNotice):
        # Resending the same request replays the estimate and tries the notice again.
        return WriteUnknown(reason="notice_missing")
    reason = FAILURE_REASONS.get(type(outcome))
    if reason is None:
        raise TypeError(f"not a write outcome: {type(outcome).__name__}")
    return WriteFailed(reason=reason)
