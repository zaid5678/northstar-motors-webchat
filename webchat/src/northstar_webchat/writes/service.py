"""Confirmation-gated writes.

The model can only propose slot values. The server validates against live data, renders the block,
and issues a single-use token. Only `confirm`, reached by the customer's click, can start a write.

Every step commits before the next begins: the confirmed request is on disk before it leaves this
process, so a crash at any point leaves enough to finish the job or say honestly what is unknown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, SecretStr

from .. import ops
from ..dealership import outcomes as o
from ..dealership.client import DealershipClient
from ..dealership.models import (
    AmendRequest,
    CallbackRequest,
    DealershipMessageRequest,
    PartExchangeRequest,
    SalesEnquiryRequest,
    TestDriveRequest,
    VehicleInterestRequest,
    WorkshopBookingRequest,
    WorkshopLookupRequest,
)
from ..persistence.repository import Repository
from ..turns.notices import NoticeBook, NoticeKind
from ..workflows.machine import (
    apply_expiry,
    hash_reference,
    lookup_event,
    usable_values,
    write_event,
)
from ..workflows.types import (
    DEFINITIONS,
    AbandonWorkflow,
    AskToAbandon,
    CheckPreconditions,
    ConfirmAction,
    Effect,
    Event,
    IdentityCheckUnavailable,
    IdentityDetails,
    IdentityFormatError,
    IdentityMismatch,
    IdentitySubmitted,
    IdentityVerified,
    LookUpBooking,
    MarkSlotUnbookable,
    NoticeShown,
    Phase,
    PreconditionFailed,
    PreconditionsPassed,
    Provenance,
    ReadBackBooking,
    ReadBackResult,
    RecordReferenceFailure,
    RequestConfirmation,
    ResubmitRequested,
    ShowBooking,
    ShowNotice,
    SlotProposed,
    SlotScalar,
    Source,
    StartWorkflow,
    SubmitWrite,
    Transition,
    Workflow,
    WorkflowKind,
    WriteInterrupted,
    WriteCompleted,
    WriteUnknown,
)
from .fields import FormBuilder, identity_fields
from .prepare import CancelRequest, Prepared, Preparer, Refused
from .present import (
    COLLECTING_REASONS,
    HIDEABLE,
    PROCESSING,
    RESUME,
    REFUSALS,
    UNKNOWN,
    ConfirmationBlock,
    Row,
    View,
    completed_message,
    failed_message,
    outcome_reference,
)

__all__ = ["ConfirmationBlock", "Proposal", "Row", "View", "WriteService"]

UNBOOKABLE_AFTER_ERROR = timedelta(hours=1)  # D-007: a 500 might have been a passing fault.
UNBOOKABLE_AFTER_CANCEL = timedelta(days=30)
RECOVERY_WINDOW = timedelta(minutes=15)  # Beyond this, a resend could surprise the customer.
HIDDEN_TRAIL = frozenset({"write_intent"})


class Proposal(BaseModel):
    name: str
    value: SlotScalar


@dataclass
class _Reply:
    notices: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    message: str | None = None


class WriteService:
    def __init__(
        self,
        store: Repository,
        client: DealershipClient,
        *,
        pepper: bytes,
        reconcile_attempts: int = 2,
        reconcile_delay: float = 1.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self._client = client
        self._pepper = pepper
        self._reconcile_attempts = reconcile_attempts
        self._reconcile_delay = reconcile_delay
        self._now = now
        self._preparer = Preparer(client, store, now)
        self._forms = FormBuilder(client, store, now)
        self._notices = NoticeBook(client)
        self._tasks: set[asyncio.Task[None]] = set()

    def _apply(
        self,
        session_id: str,
        source: Source,
        event: Event,
        *,
        outcome: o.Outcome | None = None,
        intent: tuple[str, str, str] | None = None,
    ) -> Transition:
        return self.store.apply(
            session_id, source, event, at=self._now(), outcome=outcome, intent=intent
        )

    # What the customer and the model can do.

    async def start(
        self,
        session_id: str,
        kind: WorkflowKind,
        carried: dict[str, SlotScalar] | None = None,
    ) -> View:
        result = self._apply(
            session_id, Source.CUSTOMER, StartWorkflow(kind=kind, carried=carried or {})
        )
        return await self._after(session_id, result)

    async def propose(
        self, session_id: str, proposals: Sequence[Proposal], customer_text: str
    ) -> View:
        """The model's only way in. Values take the LLM channel, so they can never confirm."""
        reply = _Reply()
        for proposal in proposals:
            result = self._apply(
                session_id,
                Source.LLM,
                SlotProposed(
                    name=proposal.name, value=proposal.value, customer_text=customer_text
                ),
            )
            await self._process(session_id, result.effects, reply)
        return await self.view(session_id, reply)

    async def fill(self, session_id: str, name: str, value: SlotScalar) -> View:
        """A value from a form or button, which the customer entered themselves."""
        result = self._apply(session_id, Source.CUSTOMER, SlotProposed(name=name, value=value))
        return await self._after(session_id, result)

    async def page_value(self, session_id: str, name: str, value: SlotScalar) -> View:
        """A value from the page. The caller has checked it against the platform (S4)."""
        result = self._apply(
            session_id, Source.PAGE, SlotProposed(name=name, value=value, validated=True)
        )
        return await self._after(session_id, result)

    async def abandon(self, session_id: str) -> View:
        result = self._apply(session_id, Source.CUSTOMER, AbandonWorkflow())
        return await self._after(session_id, result)

    async def submit_identity(
        self, session_id: str, *, reference: str, last_name: str, registration: str, phone: str
    ) -> View:
        reference_hash = hash_reference(reference, self._pepper)
        identity = IdentityDetails(
            reference=SecretStr(reference),
            last_name=SecretStr(last_name),
            registration=SecretStr(registration),
            phone=SecretStr(phone),
        )
        result = self._apply(
            session_id,
            Source.CUSTOMER,
            IdentitySubmitted(
                identity=identity,
                reference_hash=reference_hash,
                reference_locked_until=self.store.reference_locked_until(
                    reference_hash, self._now()
                ),
            ),
        )
        reply = _Reply()
        await self._process(session_id, result.effects, reply, reference_hash=reference_hash)
        return await self.view(session_id, reply)

    async def confirm(self, session_id: str, token: str) -> View:
        """The customer's click. This is the only path into a write."""
        result = self._apply(session_id, Source.CUSTOMER, ConfirmAction(token=token))
        if not result.accepted:
            return await self.view(session_id)
        if not any(isinstance(item, CheckPreconditions) for item in result.effects):
            return await self.view(
                session_id,
                _Reply(message="That confirmation expired. Please check the details and confirm."),
            )
        # A closed tab cancels this request, and must not cancel the write behind it.
        await asyncio.shield(self._detach(session_id, result.effects))
        return await self.view(session_id)

    async def check_again(self, session_id: str) -> View:
        result = self._apply(session_id, Source.CUSTOMER, ResubmitRequested())
        if result.accepted:
            await asyncio.shield(self._detach(session_id, result.effects, reconcile=False))
        return await self.view(session_id)

    def _detach(
        self, session_id: str, effects: Sequence[Effect], *, reconcile: bool = True
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run_write(session_id, effects, reconcile=reconcile))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def finish_background_work(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # Running effects.

    async def _after(self, session_id: str, result: Transition) -> View:
        reply = _Reply()
        await self._process(session_id, result.effects, reply)
        return await self.view(session_id, reply)

    async def _run_write(
        self, session_id: str, effects: Sequence[Effect], *, reconcile: bool
    ) -> None:
        try:
            await self._process(session_id, effects, _Reply())
            if reconcile:
                await self._reconcile(session_id)
        except Exception as error:
            # An unexpected fault must not leave the customer on "processing" for ever.
            self.store.note(
                session_id, "internal_error", Source.SYSTEM, self._now(), error=type(error).__name__
            )
            ops.log(
                "write_interrupted",
                level=logging.ERROR,
                session=ops.session_tag(session_id),
                error=type(error).__name__,
            )
            self._apply(session_id, Source.SYSTEM, WriteInterrupted())

    async def _reconcile(self, session_id: str) -> None:
        for attempt in range(1, self._reconcile_attempts + 1):
            if self.store.phase(session_id) is not Phase.OUTCOME_UNKNOWN:
                return
            await asyncio.sleep(self._reconcile_delay * attempt)
            self.store.note(session_id, "reconcile", Source.SYSTEM, self._now(), attempt=attempt)
            result = self._apply(session_id, Source.SYSTEM, ResubmitRequested())
            await self._process(session_id, result.effects, _Reply())

    async def _process(
        self,
        session_id: str,
        effects: Sequence[Effect],
        reply: _Reply,
        *,
        reference_hash: str | None = None,
    ) -> None:
        queue = list(effects)
        while queue:
            effect = queue.pop(0)
            queue.extend(await self._perform(session_id, effect, reply, reference_hash))

    async def _perform(
        self, session_id: str, effect: Effect, reply: _Reply, reference_hash: str | None
    ) -> tuple[Effect, ...]:
        if isinstance(effect, ShowNotice):
            if effect.notice == "privacy_statement":
                text = await self._notices.text(NoticeKind.PRIVACY)
                if text is None:
                    return ()  # Not shown, so contact details stay gated.
                reply.notices.append(text)
            return self._apply(
                session_id, Source.SYSTEM, NoticeShown(notice=effect.notice)
            ).effects
        if isinstance(effect, LookUpBooking):
            return await self._look_up(session_id, effect, reply, reference_hash)
        if isinstance(effect, RecordReferenceFailure):
            self.store.record_reference_failure(effect.reference_hash, self._now())
            return ()
        if isinstance(effect, MarkSlotUnbookable):
            self.store.mark_unbookable(effect.slot_id, self._now() + UNBOOKABLE_AFTER_ERROR)
            return ()
        if isinstance(effect, CheckPreconditions):
            return await self._recheck(session_id, effect)
        if isinstance(effect, SubmitWrite):
            return await self._submit(session_id, effect)
        if isinstance(effect, ReadBackBooking):
            return await self._read_back(session_id)
        if isinstance(effect, ShowBooking) and reference_hash is None:
            # Not from a fresh lookup, so the booking must be re-read: it may have changed.
            found = await self._client.get_workshop_booking(effect.booking_id)
            if isinstance(found, o.WorkshopBookingFound):
                self.store.record_outcome(session_id, found, self._now())
            return ()
        if isinstance(effect, AskToAbandon):
            reply.message = "Finish or cancel what you're doing first."
        return ()

    async def _look_up(
        self, session_id: str, effect: LookUpBooking, reply: _Reply, reference_hash: str | None
    ) -> tuple[Effect, ...]:
        identity = effect.identity
        outcome = await self._client.find_workshop_booking(
            WorkshopLookupRequest(
                reference=identity.reference.get_secret_value(),
                last_name=identity.last_name.get_secret_value(),
                registration=identity.registration.get_secret_value(),
                phone=identity.phone.get_secret_value(),
            )
        )
        event = lookup_event(outcome)
        if isinstance(event, IdentityVerified):
            if reference_hash is not None:
                self.store.clear_reference_failures(reference_hash)
            result = self._apply(session_id, Source.PLATFORM, event, outcome=outcome)
            return result.effects
        if isinstance(event, IdentityMismatch):
            reply.message = "We couldn't match those details. Please check them and try again."
        elif isinstance(event, IdentityFormatError):
            reply.prompts.extend(f"Please check your {name}" for name in event.fields)
        elif isinstance(event, IdentityCheckUnavailable):
            reply.message = "We can't check that right now. Please try again shortly."
        return self._apply(session_id, Source.PLATFORM, event).effects

    async def _recheck(self, session_id: str, effect: CheckPreconditions) -> tuple[Effect, ...]:
        """W3: the last check before a write, on live data. The request commits with the
        transition to `executing`, so it is on disk before it can leave this process."""
        workflow = self.store.load(session_id).active
        assert workflow is not None and workflow.action is not None
        key = workflow.action.idempotency_key
        prepared = await self._preparer.prepare(effect.kind, effect.values, effect.booking_id, key)
        if isinstance(prepared, Refused):
            event: Event = PreconditionFailed(reason=prepared.reason, clear=prepared.clear)
            return self._apply(session_id, Source.SYSTEM, event).effects
        return self._apply(
            session_id,
            Source.SYSTEM,
            PreconditionsPassed(),
            intent=(key, effect.kind.value, prepared.request),
        ).effects

    async def _call_client(self, kind: WorkflowKind, request: str) -> o.Outcome:
        client = self._client
        if kind is WorkflowKind.TEST_DRIVE:
            return await client.book_test_drive(TestDriveRequest.model_validate_json(request))
        if kind is WorkflowKind.WORKSHOP_CREATE:
            return await client.book_workshop(WorkshopBookingRequest.model_validate_json(request))
        if kind is WorkflowKind.WORKSHOP_AMEND:
            return await client.amend_workshop_booking(AmendRequest.model_validate_json(request))
        if kind is WorkflowKind.WORKSHOP_CANCEL:
            booking_id = CancelRequest.model_validate_json(request).booking_id
            return await client.cancel_workshop_booking(booking_id)
        if kind is WorkflowKind.CALLBACK:
            return await client.request_callback(CallbackRequest.model_validate_json(request))
        if kind is WorkflowKind.MESSAGE:
            return await client.leave_message(DealershipMessageRequest.model_validate_json(request))
        if kind is WorkflowKind.SALES_ENQUIRY:
            return await client.send_enquiry(SalesEnquiryRequest.model_validate_json(request))
        if kind is WorkflowKind.VEHICLE_INTEREST:
            return await client.register_interest(
                VehicleInterestRequest.model_validate_json(request)
            )
        return await client.request_estimate(PartExchangeRequest.model_validate_json(request))

    async def _submit(self, session_id: str, effect: SubmitWrite) -> tuple[Effect, ...]:
        request = self.store.write_request(effect.idempotency_key)
        outcome = await self._call_client(effect.kind, request)
        return self._settle(session_id, outcome)

    def _settle(self, session_id: str, outcome: o.Outcome) -> tuple[Effect, ...]:
        workflow = self.store.load(session_id).active
        assert workflow is not None
        event = write_event(outcome)
        if isinstance(outcome, o.WriteNotSent) and workflow.failure_reason is not None:
            # An earlier attempt may have landed, so failing to send this one proves nothing.
            event = WriteUnknown(reason="no_response")
        ops.log(
            "write",
            level=logging.INFO if isinstance(event, WriteCompleted) else logging.WARNING,
            session=ops.session_tag(session_id),
            workflow=workflow.kind.value,
            outcome=type(outcome).__name__,
        )
        if isinstance(outcome, o.BookingCancelled):
            # D-007: the platform can never rebook this slot.
            self.store.mark_unbookable(outcome.slot_id, self._now() + UNBOOKABLE_AFTER_CANCEL)
        return self._apply(session_id, Source.PLATFORM, event, outcome=outcome).effects

    async def _read_back(self, session_id: str) -> tuple[Effect, ...]:
        """Amend and cancel have no key, so a lost write is only ever checked, never resent."""
        workflow = self.store.load(session_id).active
        assert workflow is not None and workflow.action is not None
        request = self.store.write_request(workflow.action.idempotency_key)
        applied: bool | None
        if workflow.kind is WorkflowKind.WORKSHOP_AMEND:
            checked = await self._client.amendment_applied(
                AmendRequest.model_validate_json(request)
            )
            applied = checked if isinstance(checked, bool) else None
        else:
            booking_id = CancelRequest.model_validate_json(request).booking_id
            found = await self._client.get_workshop_booking(booking_id)
            applied = (
                found.booking.is_cancelled if isinstance(found, o.WorkshopBookingFound) else None
            )
            if applied and isinstance(found, o.WorkshopBookingFound):
                self.store.mark_unbookable(
                    found.booking.slot_id, self._now() + UNBOOKABLE_AFTER_CANCEL
                )
        event: Event = (
            WriteUnknown(reason="no_response")
            if applied is None
            else ReadBackResult(applied=applied)
        )
        return self._apply(session_id, Source.PLATFORM, event).effects

    # Restart.

    async def recover(self) -> int:
        """Run once at start-up, before serving traffic. Returns how many sessions it touched."""
        sessions = self.store.in_flight()
        for session_id in sessions:
            await self._recover_one(session_id)
        return len(sessions)

    async def _recover_one(self, session_id: str) -> None:
        workflow = self.store.load(session_id).active
        if workflow is None:
            return
        self.store.note(
            session_id, "recovery", Source.SYSTEM, self._now(), phase=workflow.phase.value
        )
        if workflow.phase is Phase.CHECKING_IDENTITY:
            # The identity values are gone by design, so the customer simply enters them again.
            self._apply(session_id, Source.SYSTEM, IdentityCheckUnavailable())
            return
        changed = self.store.updated_at(session_id)
        stale = changed is not None and self._now() - changed > RECOVERY_WINDOW
        if workflow.phase is Phase.RECHECKING:
            action = workflow.action
            assert action is not None
            if stale:
                event = PreconditionFailed(reason="confirmation_stale")
                self._apply(session_id, Source.SYSTEM, event)
                return
            check = CheckPreconditions(
                kind=workflow.kind, values=action.values, booking_id=action.booking_id
            )
            await self._run_write(session_id, [check], reconcile=True)
            return
        if workflow.phase in (Phase.EXECUTING, Phase.READING_BACK):
            self._apply(session_id, Source.SYSTEM, WriteInterrupted())
        if stale and DEFINITIONS[workflow.kind].keyed:
            # Too old to send unprompted. The customer decides when they come back.
            return
        result = self._apply(session_id, Source.SYSTEM, ResubmitRequested())
        await self._run_write(session_id, result.effects, reconcile=True)

    # Reading state back.

    async def _block(
        self, session_id: str, workflow: Workflow, booking_id: str | None
    ) -> ConfirmationBlock | Refused:
        pending = workflow.pending
        assert pending is not None
        values = usable_values(workflow)
        prepared = await self._preparer.prepare(
            workflow.kind, values, booking_id, pending.idempotency_key
        )
        if isinstance(prepared, Refused):
            return prepared
        assert isinstance(prepared, Prepared)
        return ConfirmationBlock(
            title=prepared.title,
            rows=prepared.rows,
            warnings=prepared.warnings,
            token=pending.token,
            expires_at=pending.expires_at.isoformat(),
        )

    async def hide_form(self, session_id: str) -> None:
        """The customer asked something else. Nothing about the task changes (D-034). `view`
        decides what can be hidden, and every change to the task clears this, so a form that
        appears later is always shown."""
        self.store.hide_form(session_id, self._now())

    async def show_form(self, session_id: str) -> View:
        self.store.show_form(session_id)
        return await self.view(session_id)

    async def view(self, session_id: str, reply: _Reply | None = None) -> View:
        view = await self._view(session_id, reply)
        workflow = self.store.load(session_id).active
        if workflow is None or view.status not in HIDEABLE or not self.store.form_hidden(session_id):
            return view
        return view.model_copy(update={"hidden": True, "resume": RESUME[workflow.kind]})

    async def _view(self, session_id: str, reply: _Reply | None = None) -> View:
        reply = reply or _Reply()
        session = apply_expiry(self.store.load(session_id), self._now())
        workflow = session.active
        common: dict[str, Any] = {"notices": reply.notices, "prompts": list(reply.prompts)}
        if workflow is None:
            return View(status="idle", **common)
        phase = workflow.phase
        message = reply.message or ""
        outcome = self.store.last_outcome(session_id)
        if phase is Phase.COLLECTING:
            common["prompts"] += [
                f"Please confirm {name}"
                for name, slot in workflow.slots.items()
                if slot.provenance is Provenance.INFERRED
            ] + [f"Please confirm {name}" for name in workflow.offers]
            reason = COLLECTING_REASONS.get(workflow.failure_reason or "", "")
            verified = session.verification.booking_id if session.verification else None
            common["fields"] = await self._forms.build(workflow, verified)
            return View(status="collecting", message=message or reason, **common)
        if phase is Phase.AWAITING_CONFIRMATION:
            verification = session.verification
            booking_id = verification.booking_id if verification else None
            block = await self._block(session_id, workflow, booking_id)
            if isinstance(block, Refused):
                # The customer must be able to fix it, or they would be stuck on this message.
                common["fields"] = await self._forms.build(workflow, booking_id, block.clear)
                return View(status="collecting", message=REFUSALS[block.reason], **common)
            return View(status="confirming", message=message, block=block, **common)
        if phase is Phase.VERIFYING:
            return View(status="verifying", message=message, fields=identity_fields(), **common)
        if phase is Phase.LOCKED:
            text = "Too many attempts. Please try again later or contact the dealership."
            return View(status="locked", message=text, **common)
        if phase in (Phase.CHECKING_IDENTITY, Phase.RECHECKING, Phase.EXECUTING, Phase.READING_BACK):
            text = PROCESSING.get(workflow.kind, "We're sending that now.")
            return View(status="processing", message=text, **common)
        if phase is Phase.COMPLETED:
            return View(
                status="completed",
                message=completed_message(workflow.kind, outcome),
                reference=outcome_reference(outcome),
                **common,
            )
        if phase is Phase.FAILED:
            text = failed_message(workflow.kind, workflow.failure_reason)
            return View(status="failed", message=text, **common)
        if phase is Phase.OUTCOME_UNKNOWN:
            return View(status="unknown", message=UNKNOWN, can_check_again=True, **common)
        if phase is Phase.TWO_BOOKINGS:
            text = "You may have two bookings. Please contact the dealership to sort this out."
            return View(status="two_bookings", message=text, **common)
        return View(status="abandoned", **common)

    def llm_context(self, session_id: str) -> dict[str, Any]:
        """What the model may know. No tokens, booking IDs, or identity values (V3, V4)."""
        workflow = self.store.load(session_id).active
        if workflow is None:
            return {"workflow": None}
        definition = DEFINITIONS[workflow.kind]
        return {
            "workflow": workflow.kind.value,
            "phase": workflow.phase.value,
            "filled": sorted(workflow.slots),
            "missing": [name for name in definition.required if name not in workflow.slots],
        }

    def trail(self, session_id: str) -> list[str]:
        return [kind for kind in self.store.kinds(session_id) if kind not in HIDDEN_TRAIL]
