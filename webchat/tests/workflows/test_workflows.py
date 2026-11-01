"""Named cases for each invariant. IDs refer to the reviewed list in docs/DECISIONS.md (D-021)."""

from __future__ import annotations

from datetime import timedelta, datetime, timezone

import pytest

from northstar_webchat.dealership import outcomes as o
from northstar_webchat.workflows.machine import (
    hash_reference,
    lookup_event,
    provenance_is_user,
    usable_values,
    write_event,
)
from northstar_webchat.workflows.types import (
    CONFIRMATION_TTL,
    DEFINITIONS,
    LOCK_DURATION,
    MAX_FAILED_ATTEMPTS,
    TERMINAL_PHASES,
    VERIFICATION_IDLE_LIMIT,
    AbandonWorkflow,
    AskToAbandon,
    AskToConfirmSlot,
    CheckPreconditions,
    ConfirmAction,
    DetailsRejected,
    IdentityCheckUnavailable,
    IdentityFormatError,
    IdentityMismatch,
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
    Session,
    ShowBooking,
    ShowNotice,
    SlotConfirmed,
    SlotLost,
    SlotProposed,
    SlotRejected,
    StartWorkflow,
    SubmitWrite,
    WorkflowKind,
    WriteCompleted,
    WriteFailed,
    WriteStep,
    WriteUnknown,
)

from .conftest import OTHER_REF_HASH, REF_HASH, SECRET, Driver, sample_value, submitted

K = WorkflowKind
CREATES = [
    K.TEST_DRIVE,
    K.WORKSHOP_CREATE,
    K.PART_EXCHANGE,
    K.CALLBACK,
    K.MESSAGE,
    K.SALES_ENQUIRY,
    K.VEHICLE_INTEREST,
]


@pytest.fixture
def d() -> Driver:
    return Driver()


class TestLifecycle:
    @pytest.mark.parametrize("kind", CREATES)
    def test_each_create_runs_to_completed(self, d: Driver, kind: WorkflowKind) -> None:
        d.start(kind)
        assert d.phase is Phase.COLLECTING
        d.fill()
        assert d.phase is Phase.AWAITING_CONFIRMATION
        d.customer(ConfirmAction(token=d.token))
        assert d.phase is Phase.RECHECKING
        assert d.effects(CheckPreconditions)
        d.system(PreconditionsPassed())
        assert d.phase is Phase.EXECUTING
        assert d.effects(SubmitWrite)
        d.platform(WriteCompleted())
        assert d.phase is Phase.COMPLETED

    @pytest.mark.parametrize("kind", [K.WORKSHOP_AMEND, K.WORKSHOP_CANCEL])
    def test_each_booking_change_runs_to_completed(self, d: Driver, kind: WorkflowKind) -> None:
        d.to_write(kind)
        submit = d.effects(SubmitWrite)[0]
        assert isinstance(submit, SubmitWrite)
        assert submit.booking_id == "wsb-1"
        d.platform(WriteCompleted())
        assert d.phase is Phase.COMPLETED

    def test_cancelling_marks_the_verified_booking_cancelled(self, d: Driver) -> None:
        d.to_write(K.WORKSHOP_CANCEL)
        d.platform(WriteCompleted())
        assert d.session.verification is not None
        assert d.session.verification.cancelled

    def test_an_unknown_slot_name_is_rejected(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        before = d.session
        result = d.customer(SlotProposed(name="not_a_slot", value="x"))
        assert not result.accepted
        assert result.session == before

    def test_a_value_outside_the_allowed_set_is_rejected(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        result = d.customer(SlotProposed(name="department", value="general"))
        assert not result.accepted

    def test_general_is_allowed_for_a_message(self, d: Driver) -> None:
        d.start(K.MESSAGE)
        assert d.customer(SlotProposed(name="department", value="general")).accepted

    def test_a_blank_value_is_rejected(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        assert not d.customer(SlotProposed(name="reason", value="   ")).accepted

    def test_optional_slots_do_not_block_confirmation(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.fill()
        assert d.phase is Phase.AWAITING_CONFIRMATION
        assert "preferred_time" not in d.active.slots

    def test_amend_needs_at_least_one_change(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        assert d.phase is Phase.COLLECTING
        d.customer(SlotProposed(name="notes", value="Please check the brakes"))
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_cancel_shows_its_warning_before_confirmation(self, d: Driver) -> None:
        d.start(K.WORKSHOP_CANCEL)
        result = d.customer(submitted())
        result = d.platform(IdentityVerified(booking_id="wsb-1", cancelled=False))
        assert ShowNotice(notice="cancel_warning") in result.effects
        assert d.phase is Phase.COLLECTING
        d.system(NoticeShown(notice="cancel_warning"))
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_part_exchange_refuses_contact_details_before_the_privacy_statement(
        self, d: Driver
    ) -> None:
        result = d.start(K.PART_EXCHANGE)
        assert ShowNotice(notice="privacy_statement") in result.effects
        refused = d.customer(SlotProposed(name="email", value="a@example.com"))
        assert not refused.accepted
        d.system(NoticeShown(notice="privacy_statement"))
        assert d.customer(SlotProposed(name="email", value="a@example.com")).accepted


class TestProvenance:
    def test_a_form_value_is_user(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.customer(SlotProposed(name="reason", value="Discuss finance"))
        assert d.active.slots["reason"].provenance is Provenance.USER

    def test_an_llm_value_found_in_the_message_is_user(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.llm(
            SlotProposed(
                name="dealership_id", value="Stockport", customer_text="Is stockport open?"
            )
        )
        assert d.active.slots["dealership_id"].provenance is Provenance.USER

    def test_an_llm_value_not_in_the_message_is_inferred(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        result = d.llm(
            SlotProposed(name="vehicle_id", value="veh-049", customer_text="the red one")
        )
        assert d.active.slots["vehicle_id"].provenance is Provenance.INFERRED
        assert AskToConfirmSlot(name="vehicle_id") in result.effects

    def test_an_llm_value_with_no_message_is_inferred(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.llm(SlotProposed(name="reason", value="Discuss finance"))
        assert d.active.slots["reason"].provenance is Provenance.INFERRED

    @pytest.mark.parametrize(
        ("value", "text", "expected"),
        [
            ("Taylor", "my name is taylor", True),
            ("AB12 CDE", "reg is ab12   cde", True),
            (42000, "about 42000 miles", True),
            ("car", "I have a carburettor issue", False),
            (4200, "about 42000 miles", False),
            ("Taylor", None, False),
        ],
    )
    def test_literal_matching(self, value: str | int, text: str | None, expected: bool) -> None:
        assert provenance_is_user(value, text) is expected

    def test_a_validated_page_value_is_page_context_and_needs_no_confirmation(
        self, d: Driver
    ) -> None:
        d.start(K.TEST_DRIVE)
        result = d.send_page("vehicle_id", "veh-001")
        assert d.active.slots["vehicle_id"].provenance is Provenance.PAGE_CONTEXT
        assert not any(isinstance(item, AskToConfirmSlot) for item in result.effects)

    def test_an_unvalidated_page_value_is_rejected(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        assert not d.send_page("vehicle_id", "veh-999", validated=False).accepted

    def test_a_page_value_can_reach_the_confirmation_card(self, d: Driver) -> None:
        d.start(K.VEHICLE_INTEREST)
        d.send_page("vehicle_id", "veh-007")
        d.fill(n for n in DEFINITIONS[K.VEHICLE_INTEREST].required if n != "vehicle_id")
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_an_inferred_slot_blocks_confirmation_until_confirmed(self, d: Driver) -> None:
        d.start(K.VEHICLE_INTEREST)
        d.llm(SlotProposed(name="vehicle_id", value="veh-007", customer_text="that one"))
        d.fill(n for n in DEFINITIONS[K.VEHICLE_INTEREST].required if n != "vehicle_id")
        assert d.phase is Phase.COLLECTING
        d.customer(SlotConfirmed(name="vehicle_id"))
        assert d.active.slots["vehicle_id"].provenance is Provenance.USER
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_an_inferred_optional_slot_also_blocks(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.fill()
        d.llm(SlotProposed(name="vehicle_id", value="veh-001", customer_text="that car"))
        assert d.phase is Phase.COLLECTING

    def test_rejecting_an_inferred_slot_removes_it(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.llm(SlotProposed(name="vehicle_id", value="veh-049", customer_text="the red one"))
        d.customer(SlotRejected(name="vehicle_id"))
        assert "vehicle_id" not in d.active.slots

    def test_carried_values_start_as_inferred(self, d: Driver) -> None:
        d.customer(StartWorkflow(kind=K.CALLBACK, carried={"email": "a@example.com"}))
        assert d.active.slots["email"].provenance is Provenance.INFERRED

    def test_carried_values_cannot_skip_a_privacy_gate(self, d: Driver) -> None:
        d.customer(StartWorkflow(kind=K.PART_EXCHANGE, carried={"email": "a@example.com"}))
        assert "email" not in d.active.slots

    def test_the_llm_cannot_confirm_a_slot(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.llm(SlotProposed(name="vehicle_id", value="veh-049", customer_text="the red one"))
        assert not d.llm(SlotConfirmed(name="vehicle_id")).accepted
        assert d.active.slots["vehicle_id"].provenance is Provenance.INFERRED


class TestUsableValues:
    def test_inferred_values_cannot_be_read_for_a_platform_call(self, d: Driver) -> None:
        d.start(K.WORKSHOP_CREATE)
        d.llm(SlotProposed(name="dealership_id", value="northstar-bolton", customer_text="near me"))
        d.customer(SlotProposed(name="service_type_id", value="mot"))
        assert usable_values(d.active) == {"service_type_id": "mot"}

    def test_a_confirmed_value_becomes_usable(self, d: Driver) -> None:
        d.start(K.WORKSHOP_CREATE)
        d.llm(SlotProposed(name="dealership_id", value="northstar-bolton", customer_text="near me"))
        d.customer(SlotConfirmed(name="dealership_id"))
        assert usable_values(d.active) == {"dealership_id": "northstar-bolton"}

    def test_page_context_is_usable_straight_away(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.send_page("vehicle_id", "veh-001")
        assert usable_values(d.active) == {"vehicle_id": "veh-001"}

    def test_an_offered_value_is_not_usable(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.customer(SlotProposed(name="reason", value="Discuss finance"))
        d.llm(SlotProposed(name="reason", value="Other", customer_text="hmm"))
        assert usable_values(d.active) == {"reason": "Discuss finance"}


class TestReferenceHash:
    def test_it_ignores_case_and_spacing(self) -> None:
        assert hash_reference(" work-10001 ", b"pepper") == hash_reference("WORK-10001", b"pepper")

    def test_it_contains_no_trace_of_the_reference(self) -> None:
        assert "10001" not in hash_reference("WORK-10001", b"pepper")

    def test_it_depends_on_the_pepper(self) -> None:
        assert hash_reference("WORK-10001", b"a") != hash_reference("WORK-10001", b"b")

    def test_different_references_differ(self) -> None:
        assert hash_reference("WORK-10001", b"p") != hash_reference("WORK-10002", b"p")


class TestCorrections:
    def test_a_correction_replaces_value_and_provenance(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.llm(SlotProposed(name="vehicle_id", value="veh-049", customer_text="the red one"))
        d.customer(SlotProposed(name="vehicle_id", value="veh-013"))
        slot = d.active.slots["vehicle_id"]
        assert (slot.value, slot.provenance) == ("veh-013", Provenance.USER)

    def test_changing_the_vehicle_clears_its_slot(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.fill()
        d.customer(SlotProposed(name="vehicle_id", value="veh-002"))
        assert "slot_id" not in d.active.slots
        assert "email" in d.active.slots

    @pytest.mark.parametrize("name", ["dealership_id", "service_type_id"])
    def test_changing_site_or_service_clears_the_slot(self, d: Driver, name: str) -> None:
        d.start(K.WORKSHOP_CREATE)
        d.fill()
        d.customer(SlotProposed(name=name, value="something-else"))
        assert "slot_id" not in d.active.slots

    def test_repeating_the_same_value_changes_nothing(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.fill()
        token = d.token
        value = sample_value("vehicle_id", K.TEST_DRIVE)
        assert not d.customer(SlotProposed(name="vehicle_id", value=value)).accepted
        assert d.token == token

    def test_a_correction_clears_an_old_failure_reason(self, d: Driver) -> None:
        d.ready(K.TEST_DRIVE)
        d.customer(ConfirmAction(token=d.token))
        d.system(PreconditionFailed(reason="slot_gone", clear=("slot_id",)))
        d.customer(SlotProposed(name="slot_id", value="another-slot"))
        assert d.active.failure_reason is None

    def test_a_correction_voids_the_confirmation_and_returns_to_collecting(
        self, d: Driver
    ) -> None:
        d.start(K.TEST_DRIVE)
        d.fill()
        token = d.token
        d.customer(SlotProposed(name="vehicle_id", value="veh-002"))
        assert d.phase is Phase.COLLECTING
        assert d.active.pending is None
        assert not d.customer(ConfirmAction(token=token)).accepted

    def test_a_correction_that_keeps_the_form_complete_needs_a_new_confirmation(
        self, d: Driver
    ) -> None:
        d.start(K.CALLBACK)
        d.fill()
        old = d.token
        d.customer(SlotProposed(name="reason", value="A different reason"))
        assert d.phase is Phase.AWAITING_CONFIRMATION
        assert d.token != old
        assert not d.customer(ConfirmAction(token=old)).accepted

    def test_inferred_cannot_silently_overwrite_user(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.customer(SlotProposed(name="reason", value="Discuss finance"))
        result = d.llm(SlotProposed(name="reason", value="Book a car", customer_text="hmm"))
        assert d.active.slots["reason"].value == "Discuss finance"
        assert d.active.offers["reason"].value == "Book a car"
        assert AskToConfirmSlot(name="reason") in result.effects

    def test_page_context_cannot_silently_overwrite_user(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.customer(SlotProposed(name="vehicle_id", value="veh-001"))
        d.send_page("vehicle_id", "veh-002")
        assert d.active.slots["vehicle_id"].value == "veh-001"

    def test_page_context_replaces_inferred(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.llm(SlotProposed(name="vehicle_id", value="veh-049", customer_text="red"))
        d.send_page("vehicle_id", "veh-001")
        assert d.active.slots["vehicle_id"].value == "veh-001"

    def test_inferred_cannot_overwrite_page_context(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.send_page("vehicle_id", "veh-001")
        d.llm(SlotProposed(name="vehicle_id", value="veh-049", customer_text="red"))
        assert d.active.slots["vehicle_id"].value == "veh-001"

    def test_accepting_an_offer_replaces_the_value_and_clears_dependants(self, d: Driver) -> None:
        d.start(K.TEST_DRIVE)
        d.fill()
        d.llm(SlotProposed(name="vehicle_id", value="veh-002", customer_text="hm"))
        assert d.phase is Phase.COLLECTING
        d.customer(SlotConfirmed(name="vehicle_id"))
        assert d.active.slots["vehicle_id"].value == "veh-002"
        assert d.active.offers == {}
        assert "slot_id" not in d.active.slots

    def test_refusing_an_offer_keeps_the_customers_value(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.customer(SlotProposed(name="reason", value="Discuss finance"))
        d.llm(SlotProposed(name="reason", value="Other", customer_text="hmm"))
        d.customer(SlotRejected(name="reason"))
        assert d.active.slots["reason"].value == "Discuss finance"
        assert d.active.offers == {}

    def test_corrections_are_refused_once_confirmed(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        d.customer(ConfirmAction(token=d.token))
        assert not d.customer(SlotProposed(name="reason", value="Changed")).accepted
        d.system(PreconditionsPassed())
        assert not d.customer(SlotProposed(name="reason", value="Changed")).accepted


class TestWrites:
    def test_a_token_is_single_use(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        token = d.token
        assert d.customer(ConfirmAction(token=token)).accepted
        assert not d.customer(ConfirmAction(token=token)).accepted

    def test_a_wrong_token_is_rejected(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        assert not d.customer(ConfirmAction(token="forged")).accepted
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_the_llm_cannot_confirm_a_write(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        assert not d.llm(ConfirmAction(token=d.token)).accepted
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_the_platform_channel_cannot_confirm_a_write(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        assert not d.platform(ConfirmAction(token=d.token)).accepted

    def test_an_expired_token_issues_a_new_card_without_writing(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        old = d.token
        d.wait(CONFIRMATION_TTL + timedelta(seconds=1))
        result = d.customer(ConfirmAction(token=old))
        assert d.phase is Phase.AWAITING_CONFIRMATION
        assert d.token != old
        assert any(isinstance(item, RequestConfirmation) for item in result.effects)
        assert not d.effects(CheckPreconditions)

    def test_the_write_carries_the_confirmed_key_and_values(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        pending = d.active.pending
        assert pending is not None
        d.customer(ConfirmAction(token=d.token))
        d.system(PreconditionsPassed())
        submit = d.effects(SubmitWrite)[0]
        assert isinstance(submit, SubmitWrite)
        assert submit.idempotency_key == pending.idempotency_key
        assert submit.values["reason"] == "reason-value"

    def test_a_failed_recheck_sends_nothing_and_clears_the_slot(self, d: Driver) -> None:
        d.ready(K.TEST_DRIVE)
        d.customer(ConfirmAction(token=d.token))
        result = d.system(PreconditionFailed(reason="slot_gone", clear=("slot_id",)))
        assert d.phase is Phase.COLLECTING
        assert "slot_id" not in d.active.slots
        assert d.active.failure_reason == "slot_gone"
        assert not any(isinstance(item, SubmitWrite) for item in result.effects)

    def test_a_failed_recheck_with_nothing_to_fix_fails(self, d: Driver) -> None:
        d.ready(K.TEST_DRIVE)
        d.customer(ConfirmAction(token=d.token))
        d.system(PreconditionFailed(reason="vehicle_reserved"))
        assert d.phase is Phase.FAILED
        assert d.active.failure_reason == "vehicle_reserved"

    def test_a_taken_slot_returns_to_collecting(self, d: Driver) -> None:
        d.to_write(K.TEST_DRIVE)
        result = d.platform(SlotLost(slot_id="slot_id-value", unbookable=False))
        assert d.phase is Phase.COLLECTING
        assert "slot_id" not in d.active.slots
        assert d.active.failure_reason == "slot_lost"
        assert not any(isinstance(item, MarkSlotUnbookable) for item in result.effects)

    def test_an_unbookable_slot_is_recorded(self, d: Driver) -> None:
        d.to_write(K.WORKSHOP_CREATE)
        result = d.platform(SlotLost(slot_id="slot_id-value", unbookable=True))
        assert MarkSlotUnbookable(slot_id="slot_id-value") in result.effects
        assert d.phase is Phase.COLLECTING

    def test_rejected_details_clear_only_those_slots(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        d.platform(DetailsRejected(slots=("phone",)))
        assert d.phase is Phase.COLLECTING
        assert "phone" not in d.active.slots
        assert "email" in d.active.slots

    def test_completed_comes_only_from_the_platform(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        assert not d.llm(WriteCompleted()).accepted
        assert not d.customer(WriteCompleted()).accepted
        assert not d.system(WriteCompleted()).accepted
        assert d.phase is Phase.EXECUTING

    def test_an_unknown_write_never_becomes_completed_by_itself(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        d.platform(WriteUnknown(reason="no_response"))
        assert d.phase is Phase.OUTCOME_UNKNOWN
        assert not d.platform(WriteCompleted()).accepted
        assert not d.customer(ConfirmAction(token="x")).accepted
        assert not d.customer(SlotProposed(name="reason", value="New")).accepted

    def test_a_keyed_write_is_resubmitted_identically(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        first = d.effects(SubmitWrite)[0]
        d.platform(WriteUnknown(reason="no_response"))
        d.customer(ResubmitRequested())
        assert d.phase is Phase.EXECUTING
        assert d.effects(SubmitWrite)[0] == first
        d.platform(WriteCompleted())
        assert d.phase is Phase.COMPLETED

    @pytest.mark.parametrize("kind", [K.WORKSHOP_AMEND, K.WORKSHOP_CANCEL])
    def test_an_unkeyed_write_reads_back_and_never_resends(
        self, d: Driver, kind: WorkflowKind
    ) -> None:
        d.to_write(kind)
        d.platform(WriteUnknown(reason="no_response"))
        result = d.customer(ResubmitRequested())
        assert d.phase is Phase.READING_BACK
        assert ReadBackBooking(booking_id="wsb-1") in result.effects
        assert not any(isinstance(item, SubmitWrite) for item in result.effects)

    @pytest.mark.parametrize(
        ("applied", "expected"), [(True, Phase.COMPLETED), (False, Phase.FAILED)]
    )
    def test_a_read_back_settles_it(
        self, d: Driver, applied: bool, expected: Phase
    ) -> None:
        d.to_write(K.WORKSHOP_AMEND)
        d.platform(WriteUnknown(reason="no_response"))
        d.customer(ResubmitRequested())
        d.platform(ReadBackResult(applied=applied))
        assert d.phase is expected

    def test_a_failed_read_back_stays_unknown(self, d: Driver) -> None:
        d.to_write(K.WORKSHOP_AMEND)
        d.platform(WriteUnknown(reason="no_response"))
        d.customer(ResubmitRequested())
        d.platform(WriteUnknown(reason="no_response"))
        assert d.phase is Phase.OUTCOME_UNKNOWN


class TestServiceChange:
    def begin(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        d.customer(SlotProposed(name="target_slot_id", value="ws-slot-9"))
        d.customer(ConfirmAction(token=d.token))
        d.system(PreconditionsPassed(service_change=True))

    def test_the_new_booking_comes_first(self, d: Driver) -> None:
        self.begin(d)
        submit = d.effects(SubmitWrite)[0]
        assert isinstance(submit, SubmitWrite)
        assert submit.step is WriteStep.BOOK_REPLACEMENT

    def test_the_original_is_cancelled_after_one_confirmation(self, d: Driver) -> None:
        self.begin(d)
        d.platform(WriteCompleted())
        submit = d.effects(SubmitWrite)[0]
        assert isinstance(submit, SubmitWrite)
        assert submit.step is WriteStep.CANCEL_ORIGINAL
        assert d.phase is Phase.EXECUTING
        d.platform(WriteCompleted())
        assert d.phase is Phase.COMPLETED

    def test_a_failed_first_step_cancels_nothing(self, d: Driver) -> None:
        self.begin(d)
        d.platform(WriteFailed(reason="not_sent"))
        assert d.phase is Phase.FAILED
        assert not d.effects(SubmitWrite)

    @pytest.mark.parametrize(
        "event", [WriteFailed(reason="not_sent"), WriteUnknown(reason="no_response")]
    )
    def test_a_failed_cancel_ends_in_two_bookings(self, d: Driver, event: object) -> None:
        self.begin(d)
        d.platform(WriteCompleted())
        d.platform(event)  # type: ignore[arg-type]
        assert d.phase is Phase.TWO_BOOKINGS

    def test_a_lost_response_on_the_new_booking_resends_it(self, d: Driver) -> None:
        self.begin(d)
        first = d.effects(SubmitWrite)[0]
        d.platform(WriteUnknown(reason="no_response"))
        d.customer(ResubmitRequested())
        assert d.effects(SubmitWrite)[0] == first


class TestVerification:
    @pytest.mark.parametrize("kind", [K.BOOKING_RETRIEVAL, K.WORKSHOP_AMEND, K.WORKSHOP_CANCEL])
    def test_booking_workflows_start_unverified(self, d: Driver, kind: WorkflowKind) -> None:
        d.start(kind)
        assert d.phase is Phase.VERIFYING

    def test_retrieval_shows_the_booking_only_after_a_match(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        result = d.customer(submitted())
        assert d.phase is Phase.CHECKING_IDENTITY
        assert not any(isinstance(item, ShowBooking) for item in result.effects)
        result = d.platform(IdentityVerified(booking_id="wsb-1", cancelled=False))
        assert ShowBooking(booking_id="wsb-1") in result.effects
        assert d.phase is Phase.COMPLETED

    @pytest.mark.parametrize(
        "send", ["customer", "llm", "system"]
    )
    def test_only_the_platform_can_verify(self, d: Driver, send: str) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.customer(submitted())
        result = getattr(d, send)(IdentityVerified(booking_id="wsb-1", cancelled=False))
        assert not result.accepted
        assert d.session.verification is None

    def test_identity_cannot_come_from_the_llm(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        assert not d.llm(submitted()).accepted

    def test_identity_values_are_not_slots(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        assert not d.customer(SlotProposed(name="phone", value=SECRET)).accepted

    def test_slots_cannot_be_filled_before_verification(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        assert not d.customer(SlotProposed(name="notes", value="x")).accepted

    def test_identity_is_not_in_stored_state_or_stored_effects(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        result = d.customer(submitted())
        lookup = next(item for item in result.effects if isinstance(item, LookUpBooking))
        assert lookup.identity.phone.get_secret_value() == SECRET
        assert SECRET not in result.model_dump_json()
        assert SECRET not in repr(result)
        assert "Taylor" not in d.session.model_dump_json()

    @pytest.mark.parametrize("attempts", range(1, MAX_FAILED_ATTEMPTS))
    def test_failures_below_the_limit_allow_another_try(self, d: Driver, attempts: int) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        for _ in range(attempts):
            d.customer(submitted())
            result = d.platform(IdentityMismatch())
        assert d.phase is Phase.VERIFYING
        assert RecordReferenceFailure(reference_hash=REF_HASH) in result.effects

    def locked(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        for _ in range(MAX_FAILED_ATTEMPTS):
            d.customer(submitted())
            d.platform(IdentityMismatch())

    def test_the_fifth_failure_locks(self, d: Driver) -> None:
        self.locked(d)
        assert d.phase is Phase.LOCKED

    def test_while_locked_no_lookup_is_sent_even_with_correct_details(self, d: Driver) -> None:
        self.locked(d)
        result = d.customer(submitted())
        assert not result.accepted
        assert not any(isinstance(item, LookUpBooking) for item in result.effects)

    def test_abandoning_does_not_reset_the_count(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        for _ in range(4):
            d.customer(submitted())
            d.platform(IdentityMismatch())
        d.customer(AbandonWorkflow())
        d.start(K.WORKSHOP_CANCEL)
        d.customer(submitted())
        d.platform(IdentityMismatch())
        assert d.phase is Phase.LOCKED

    def test_a_new_workflow_starts_locked_during_a_lock(self, d: Driver) -> None:
        self.locked(d)
        d.customer(AbandonWorkflow())
        d.start(K.BOOKING_RETRIEVAL)
        assert d.phase is Phase.LOCKED

    def test_the_lock_expires(self, d: Driver) -> None:
        self.locked(d)
        d.wait(LOCK_DURATION + timedelta(seconds=1))
        result = d.customer(submitted())
        assert any(isinstance(item, LookUpBooking) for item in result.effects)
        assert d.session.failed_attempts == 0

    def test_a_reference_locked_elsewhere_blocks_without_a_lookup(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        until = d.now + timedelta(minutes=5)
        result = d.customer(submitted(locked_until=until))
        assert d.phase is Phase.LOCKED
        assert not any(isinstance(item, LookUpBooking) for item in result.effects)
        assert d.session.locked_until == until

    def test_an_expired_reference_lock_does_not_block(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        result = d.customer(submitted(locked_until=d.now - timedelta(minutes=1)))
        assert any(isinstance(item, LookUpBooking) for item in result.effects)

    def test_a_format_error_does_not_use_an_attempt(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        for _ in range(MAX_FAILED_ATTEMPTS + 2):
            d.customer(submitted())
            d.platform(IdentityFormatError(fields=("phone",)))
        assert d.phase is Phase.VERIFYING
        assert d.session.failed_attempts == 0

    def test_an_outage_does_not_use_an_attempt(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        for _ in range(MAX_FAILED_ATTEMPTS + 2):
            d.customer(submitted())
            d.platform(IdentityCheckUnavailable())
        assert d.phase is Phase.VERIFYING
        assert d.session.failed_attempts == 0

    def test_a_stored_lock_holds_even_if_the_phase_says_otherwise(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        locked = d.session.model_copy(update={"locked_until": d.now + LOCK_DURATION})
        d.session = locked
        result = d.customer(submitted())
        assert not result.accepted
        assert not any(isinstance(item, LookUpBooking) for item in result.effects)

    def test_a_rejected_event_after_expiry_leaves_the_session_exactly_as_it_was(
        self, d: Driver
    ) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        before = d.session
        d.wait(VERIFICATION_IDLE_LIMIT + timedelta(seconds=1))
        result = d.customer(SlotProposed(name="mileage", value=1))
        assert not result.accepted
        assert result.session == before

    def test_identity_is_not_in_a_stored_event(self) -> None:
        event = submitted()
        assert "identity" not in event.model_dump()
        assert "identity" not in event.model_dump_json()
        assert SECRET not in repr(event)

    def test_a_double_submit_sends_one_lookup(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.customer(submitted())
        assert not d.customer(submitted()).accepted

    def test_a_pass_resets_the_count(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.customer(submitted())
        d.platform(IdentityMismatch())
        d.customer(submitted())
        d.platform(IdentityVerified(booking_id="wsb-1", cancelled=False))
        assert d.session.failed_attempts == 0

    def test_verification_carries_to_the_next_workflow_for_that_booking(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.verify()
        d.start(K.WORKSHOP_AMEND)
        assert d.phase is Phase.COLLECTING

    def test_verification_survives_within_the_idle_limit(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        d.wait(VERIFICATION_IDLE_LIMIT - timedelta(seconds=1))
        d.customer(SlotProposed(name="mileage", value=1))
        assert d.phase is Phase.AWAITING_CONFIRMATION

    def test_activity_extends_verification(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        for value in range(1, 4):
            d.wait(VERIFICATION_IDLE_LIMIT - timedelta(seconds=1))
            assert d.customer(SlotProposed(name="mileage", value=value)).accepted
        assert d.session.verification is not None

    def test_idling_past_the_limit_returns_to_verifying(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        d.customer(SlotProposed(name="mileage", value=1))
        token = d.token
        d.wait(VERIFICATION_IDLE_LIMIT + timedelta(seconds=1))
        result = d.customer(ConfirmAction(token=token))
        assert not result.accepted

    def test_after_idling_a_write_needs_a_new_check(self, d: Driver) -> None:
        d.start(K.WORKSHOP_AMEND)
        d.verify()
        d.wait(VERIFICATION_IDLE_LIMIT + timedelta(seconds=1))
        d.customer(SlotProposed(name="mileage", value=1))
        d.customer(submitted())
        assert d.phase is Phase.CHECKING_IDENTITY

    def test_expiry_does_not_interrupt_a_write_in_progress(self, d: Driver) -> None:
        d.to_write(K.WORKSHOP_CANCEL)
        d.wait(VERIFICATION_IDLE_LIMIT + timedelta(seconds=1))
        d.platform(WriteCompleted())
        assert d.phase is Phase.COMPLETED

    def test_a_different_reference_needs_a_new_check(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.verify()
        d.customer(StartWorkflow(kind=K.WORKSHOP_CANCEL, booking_reference_hash=OTHER_REF_HASH))
        assert d.phase is Phase.VERIFYING
        assert d.session.verification is None

    def test_naming_the_same_reference_keeps_verification(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.verify()
        d.customer(StartWorkflow(kind=K.WORKSHOP_CANCEL, booking_reference_hash=REF_HASH))
        assert d.phase is Phase.COLLECTING

    def test_a_cancelled_booking_can_be_viewed(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.customer(submitted())
        result = d.platform(IdentityVerified(booking_id="wsb-1", cancelled=True))
        assert ShowBooking(booking_id="wsb-1") in result.effects
        assert d.phase is Phase.COMPLETED

    @pytest.mark.parametrize("kind", [K.WORKSHOP_AMEND, K.WORKSHOP_CANCEL])
    def test_a_cancelled_booking_cannot_be_changed(self, d: Driver, kind: WorkflowKind) -> None:
        d.start(kind)
        d.verify(cancelled=True)
        assert d.phase is Phase.FAILED
        assert d.active.failure_reason == "booking_cancelled"

    def test_a_cancelled_booking_cannot_be_changed_on_a_later_workflow(self, d: Driver) -> None:
        d.start(K.BOOKING_RETRIEVAL)
        d.verify(cancelled=True)
        d.start(K.WORKSHOP_AMEND)
        assert d.phase is Phase.FAILED

    def test_a_write_targets_the_verified_booking_only(self, d: Driver) -> None:
        d.start(K.WORKSHOP_CANCEL)
        d.verify(booking_id="wsb-77")
        d.fill()
        d.customer(ConfirmAction(token=d.token))
        d.system(PreconditionsPassed())
        submit = d.effects(SubmitWrite)[0]
        assert isinstance(submit, SubmitWrite)
        assert submit.booking_id == "wsb-77"


class TestOneActiveWorkflow:
    def test_starting_another_write_asks_first(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        before = d.session
        result = d.start(K.MESSAGE)
        assert not result.accepted
        assert AskToAbandon(kind=K.CALLBACK) in result.effects
        assert d.session == before

    def test_abandoning_then_starting_works(self, d: Driver) -> None:
        d.start(K.CALLBACK)
        d.customer(AbandonWorkflow())
        assert d.phase is Phase.ABANDONED
        assert d.start(K.MESSAGE).accepted
        assert d.active.kind is K.MESSAGE

    def test_abandoning_voids_the_token(self, d: Driver) -> None:
        d.ready(K.CALLBACK)
        token = d.token
        d.customer(AbandonWorkflow())
        assert not d.customer(ConfirmAction(token=token)).accepted

    @pytest.mark.parametrize("phase_setup", ["recheck", "execute"])
    def test_a_confirmed_write_cannot_be_abandoned(self, d: Driver, phase_setup: str) -> None:
        d.ready(K.CALLBACK)
        d.customer(ConfirmAction(token=d.token))
        if phase_setup == "execute":
            d.system(PreconditionsPassed())
        assert not d.customer(AbandonWorkflow()).accepted

    def test_a_finished_workflow_can_be_replaced(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        d.platform(WriteCompleted())
        assert d.start(K.MESSAGE).accepted

    def test_an_unknown_write_stays_on_record_when_the_customer_moves_on(self, d: Driver) -> None:
        d.to_write(K.CALLBACK)
        d.platform(WriteUnknown(reason="no_response"))
        assert d.start(K.MESSAGE).accepted
        assert [item.kind for item in d.session.unresolved] == [K.CALLBACK]

    def test_the_llm_can_propose_but_not_start_or_abandon(self, d: Driver) -> None:
        assert not d.llm(StartWorkflow(kind=K.CALLBACK)).accepted
        d.start(K.CALLBACK)
        assert not d.llm(AbandonWorkflow()).accepted


class TestStorage:
    def test_a_session_survives_a_json_round_trip(self, d: Driver) -> None:
        d.ready(K.WORKSHOP_AMEND)
        assert Session.model_validate_json(d.session.model_dump_json()) == d.session

    def test_a_terminal_phase_set_is_what_the_lifecycle_says(self) -> None:
        assert Phase.OUTCOME_UNKNOWN not in TERMINAL_PHASES


AT = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


class TestOutcomeMapping:
    @pytest.mark.parametrize(
        ("outcome", "event"),
        [
            (o.BookingNotMatched(), IdentityMismatch()),
            (o.PlatformUnavailable(attempts=3, detail="x"), IdentityCheckUnavailable()),
            (o.MalformedResponse(operation="x", detail="x"), IdentityCheckUnavailable()),
            (o.IntegrationFault(status=401, code="UNAUTHORISED"), IdentityCheckUnavailable()),
            (
                o.InvalidDetails(message="m", field_errors={"phone": "bad"}),
                IdentityFormatError(fields=("phone",)),
            ),
        ],
    )
    def test_lookup_outcomes(self, outcome: o.Outcome, event: object) -> None:
        assert lookup_event(outcome) == event

    def test_a_found_booking_verifies(self) -> None:
        from northstar_webchat.dealership.models import WorkshopBooking

        booking = WorkshopBooking(
            id="wsb-1",
            reference="WORK-1",
            slot_id="s",
            dealership_id="d",
            service_type_id="t",
            status="cancelled",
            cancelled_at=AT,
        )
        assert lookup_event(o.WorkshopBookingFound(booking=booking)) == IdentityVerified(
            booking_id="wsb-1", cancelled=True
        )

    @pytest.mark.parametrize(
        ("outcome", "event"),
        [
            (o.EnquiryReceived(id="i", reference="r", replayed=False), WriteCompleted()),
            (
                o.EstimateWithoutNotice(id="i", reference="r", replayed=False),
                WriteUnknown(reason="notice_missing"),
            ),
            (o.SlotTaken(slot_id="s"), SlotLost(slot_id="s", unbookable=False)),
            (o.SlotInPast(slot_id="s"), SlotLost(slot_id="s", unbookable=False)),
            (o.SlotUnbookable(slot_id="s"), SlotLost(slot_id="s", unbookable=True)),
            (o.VehicleReserved(vehicle_id="v"), WriteFailed(reason="vehicle_reserved")),
            (o.WriteNotSent(operation="x", attempts=3), WriteFailed(reason="not_sent")),
            (o.AmendNotApplied(booking_id="b"), WriteFailed(reason="not_applied")),
            (o.CancelNotApplied(booking_id="b"), WriteFailed(reason="not_applied")),
            (
                o.WriteOutcomeUnknown(operation="x", reason="no_response", attempts=3),
                WriteUnknown(reason="no_response"),
            ),
            (
                o.InvalidDetails(message="m", field_errors={"firstName": "bad"}),
                DetailsRejected(slots=("first_name",)),
            ),
        ],
    )
    def test_write_outcomes(self, outcome: o.Outcome, event: object) -> None:
        assert write_event(outcome) == event
