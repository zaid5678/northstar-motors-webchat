"""Workflow data: slots, states, events, and effects. Everything here is plain, frozen data.

A session holds at most one active workflow (D-021). The verification record and attempt count sit
on the session, not the workflow, so abandoning a workflow cannot reset them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, SecretStr

CONFIRMATION_TTL = timedelta(minutes=10)
VERIFICATION_IDLE_LIMIT = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5

SlotScalar = str | int


class WorkflowKind(StrEnum):
    TEST_DRIVE = "test_drive"
    WORKSHOP_CREATE = "workshop_create"
    WORKSHOP_AMEND = "workshop_amend"
    WORKSHOP_CANCEL = "workshop_cancel"
    BOOKING_RETRIEVAL = "booking_retrieval"
    PART_EXCHANGE = "part_exchange"
    CALLBACK = "callback"
    MESSAGE = "message"
    SALES_ENQUIRY = "sales_enquiry"
    VEHICLE_INTEREST = "vehicle_interest"


class Phase(StrEnum):
    VERIFYING = "verifying"
    CHECKING_IDENTITY = "checking_identity"
    LOCKED = "locked"
    COLLECTING = "collecting"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RECHECKING = "rechecking"
    EXECUTING = "executing"
    READING_BACK = "reading_back"
    OUTCOME_UNKNOWN = "outcome_unknown"
    COMPLETED = "completed"
    FAILED = "failed"
    TWO_BOOKINGS = "two_bookings"
    ABANDONED = "abandoned"


TERMINAL_PHASES = frozenset(
    {Phase.COMPLETED, Phase.FAILED, Phase.TWO_BOOKINGS, Phase.ABANDONED}
)
ABANDONABLE_PHASES = frozenset(
    {Phase.VERIFYING, Phase.LOCKED, Phase.COLLECTING, Phase.AWAITING_CONFIRMATION}
)
EDITABLE_PHASES = frozenset({Phase.COLLECTING, Phase.AWAITING_CONFIRMATION})


class WriteStep(StrEnum):
    PRIMARY = "primary"
    BOOK_REPLACEMENT = "book_replacement"
    CANCEL_ORIGINAL = "cancel_original"


class Provenance(StrEnum):
    USER = "user"
    PAGE_CONTEXT = "page_context"
    INFERRED = "inferred"


PROVENANCE_RANK = {Provenance.INFERRED: 0, Provenance.PAGE_CONTEXT: 1, Provenance.USER: 2}


class Source(StrEnum):
    """Who produced an event. CUSTOMER is a form, a button, or the pre-router, never the LLM."""

    CUSTOMER = "customer"
    LLM = "llm"
    PAGE = "page"
    PLATFORM = "platform"
    SYSTEM = "system"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


CONTACT_SLOTS = ("first_name", "last_name", "email", "phone")


@dataclass(frozen=True)
class WorkflowDefinition:
    """`required` must all be filled, and at least one of `any_of` when it is not empty.
    `depends_on` lists the slots cleared when a slot changes. `gated` slots are refused until
    every notice has been shown. `keyed` writes can be resent safely with their key."""

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    depends_on: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    allowed: Mapping[str, frozenset[str]] = field(default_factory=dict)
    notices: tuple[str, ...] = ()
    gated: frozenset[str] = frozenset()
    needs_verification: bool = False
    keyed: bool = True

    @property
    def slot_names(self) -> frozenset[str]:
        return frozenset(self.required + self.optional + self.any_of)


DEPARTMENTS = frozenset({"sales", "service", "parts"})

DEFINITIONS: Mapping[WorkflowKind, WorkflowDefinition] = MappingProxyType(
    {
        WorkflowKind.TEST_DRIVE: WorkflowDefinition(
            required=("vehicle_id", "slot_id", *CONTACT_SLOTS),
            optional=("notes",),
            depends_on={"vehicle_id": ("slot_id",)},
        ),
        WorkflowKind.WORKSHOP_CREATE: WorkflowDefinition(
            required=(
                "dealership_id",
                "service_type_id",
                "slot_id",
                "registration",
                "mileage",
                *CONTACT_SLOTS,
            ),
            optional=("notes",),
            depends_on={"dealership_id": ("slot_id",), "service_type_id": ("slot_id",)},
        ),
        WorkflowKind.WORKSHOP_AMEND: WorkflowDefinition(
            any_of=("target_slot_id", "mileage", "notes"),
            needs_verification=True,
            keyed=False,
        ),
        WorkflowKind.WORKSHOP_CANCEL: WorkflowDefinition(
            notices=("cancel_warning",),
            needs_verification=True,
            keyed=False,
        ),
        WorkflowKind.BOOKING_RETRIEVAL: WorkflowDefinition(needs_verification=True),
        WorkflowKind.PART_EXCHANGE: WorkflowDefinition(
            required=("dealership_id", "registration", "mileage", "condition", *CONTACT_SLOTS),
            allowed={"condition": frozenset({"excellent", "good", "fair"})},
            notices=("privacy_statement",),
            gated=frozenset(CONTACT_SLOTS),
        ),
        WorkflowKind.CALLBACK: WorkflowDefinition(
            required=("dealership_id", "department", "reason", *CONTACT_SLOTS),
            optional=("vehicle_id", "preferred_time"),
            allowed={"department": DEPARTMENTS},
        ),
        WorkflowKind.MESSAGE: WorkflowDefinition(
            required=(
                "dealership_id",
                "department",
                "subject",
                "message",
                "preferred_contact_method",
                *CONTACT_SLOTS,
            ),
            allowed={
                "department": DEPARTMENTS | {"general"},
                "preferred_contact_method": frozenset({"email", "phone"}),
            },
        ),
        WorkflowKind.SALES_ENQUIRY: WorkflowDefinition(
            required=("dealership_id", "enquiry_type", "message", *CONTACT_SLOTS),
            optional=("vehicle_id",),
            allowed={
                "enquiry_type": frozenset({"general", "availability", "finance", "part-exchange"})
            },
        ),
        WorkflowKind.VEHICLE_INTEREST: WorkflowDefinition(
            required=("vehicle_id", *CONTACT_SLOTS),
            optional=("notes",),
        ),
    }
)


class SlotValue(Frozen):
    value: SlotScalar
    provenance: Provenance
    set_at: datetime


class Pending(Frozen):
    """A confirmation card waiting on the customer. The token is never shown to the LLM."""

    token: str
    digest: str
    idempotency_key: str
    expires_at: datetime


class Action(Frozen):
    """The exact request the customer confirmed. A resend reuses it unchanged (D-004)."""

    digest: str
    idempotency_key: str
    values: dict[str, SlotScalar]
    booking_id: str | None = None


class Workflow(Frozen):
    kind: WorkflowKind
    phase: Phase
    slots: dict[str, SlotValue] = {}
    offers: dict[str, SlotValue] = {}
    notices_shown: frozenset[str] = frozenset()
    pending: Pending | None = None
    action: Action | None = None
    step: WriteStep = WriteStep.PRIMARY
    failure_reason: str | None = None


class Verification(Frozen):
    """Proof for one booking. The booking ID is server-side only (V4)."""

    booking_id: str
    reference_hash: str
    cancelled: bool
    last_activity_at: datetime


class Session(Frozen):
    active: Workflow | None = None
    verification: Verification | None = None
    failed_attempts: int = 0
    locked_until: datetime | None = None
    checking_reference: str | None = None
    unresolved: tuple[Workflow, ...] = ()


class IdentityDetails(Frozen):
    """The four lookup values. They are masked in output and dropped when stored (V3)."""

    reference: SecretStr
    last_name: SecretStr
    registration: SecretStr
    phone: SecretStr


# Events.


class Event(Frozen):
    pass


class StartWorkflow(Event):
    """`booking_reference_hash` names the booking when the customer gave one. `carried` values from
    earlier in the session always start as inferred (S5)."""

    kind: WorkflowKind
    booking_reference_hash: str | None = None
    carried: dict[str, SlotScalar] = {}


class AbandonWorkflow(Event):
    pass


class SlotProposed(Event):
    """`customer_text` is the message an LLM value was taken from, used to work out provenance."""

    name: str
    value: SlotScalar
    customer_text: str | None = None
    validated: bool = False


class SlotConfirmed(Event):
    name: str


class SlotRejected(Event):
    name: str


class NoticeShown(Event):
    notice: str


class IdentitySubmitted(Event):
    """`reference_locked_until` comes from the cross-session ledger, read before this event."""

    identity: IdentityDetails = Field(exclude=True, repr=False)
    reference_hash: str
    reference_locked_until: datetime | None = None


class IdentityVerified(Event):
    booking_id: str
    cancelled: bool


class IdentityMismatch(Event):
    pass


class IdentityFormatError(Event):
    fields: tuple[str, ...]


class IdentityCheckUnavailable(Event):
    pass


class ConfirmAction(Event):
    token: str


class PreconditionsPassed(Event):
    """`service_change` starts the two-step replacement (D-009)."""

    service_change: bool = False


class PreconditionFailed(Event):
    """`clear` names slots the customer can fill again. If empty, the workflow fails."""

    reason: str
    clear: tuple[str, ...] = ()


class WriteCompleted(Event):
    pass


class SlotLost(Event):
    slot_id: str
    unbookable: bool


class DetailsRejected(Event):
    slots: tuple[str, ...]


class WriteFailed(Event):
    reason: str


class WriteUnknown(Event):
    reason: str


class ResubmitRequested(Event):
    pass


class WriteInterrupted(Event):
    """The server stopped while a write was in flight, so nothing is known about its result."""


class ReadBackResult(Event):
    applied: bool


class Incoming(Frozen):
    """One event plus its context. `token` and `idempotency_key` are fresh random values from the
    caller, so `transition` stays pure, and are used only if a new confirmation is issued."""

    source: Source
    event: Event = Field(repr=False)
    at: datetime
    token: str
    idempotency_key: str


# Effects: what the pipeline must do next.


class Effect(Frozen):
    pass


class AskToConfirmSlot(Effect):
    name: str


class ShowNotice(Effect):
    notice: str


class AskToAbandon(Effect):
    kind: WorkflowKind


class LookUpBooking(Effect):
    identity: IdentityDetails = Field(exclude=True, repr=False)


class RecordReferenceFailure(Effect):
    reference_hash: str


class ShowBooking(Effect):
    booking_id: str


class RequestConfirmation(Effect):
    token: str
    values: dict[str, SlotScalar]


class CheckPreconditions(Effect):
    kind: WorkflowKind
    values: dict[str, SlotScalar]
    booking_id: str | None


class SubmitWrite(Effect):
    kind: WorkflowKind
    step: WriteStep
    values: dict[str, SlotScalar]
    idempotency_key: str
    booking_id: str | None


class ReadBackBooking(Effect):
    booking_id: str | None


class MarkSlotUnbookable(Effect):
    slot_id: str


class Transition(Frozen):
    """`accepted` is False when the event is not allowed, and `session` is then unchanged (T2)."""

    session: Session
    accepted: bool
    effects: tuple[Effect, ...] = ()
    reason: str | None = None
