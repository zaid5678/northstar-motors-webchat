"""The LLM boundary. Everything the model returns is untrusted until validated here."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..dealership.models import BodyStyle, FuelType, Transmission
from ..workflows.types import SlotScalar, WorkflowKind


class FailureKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    SERVER_ERROR = "server_error"
    REJECTED = "rejected"
    NOT_CONFIGURED = "not_configured"


class LLMUnavailable(Exception):
    """The model could not be reached or refused the call. Never raised for bad content."""

    def __init__(self, kind: FailureKind, *, retry_after: float | None = None) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.retry_after = retry_after


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LLMClient(Protocol):
    """`schema` asks for JSON matching it. The result is still untrusted and must be validated."""

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str: ...


class Intent(StrEnum):
    VEHICLE_SEARCH = "vehicle_search"
    VEHICLE_STATUS = "vehicle_status"
    OPENING_HOURS = "opening_hours"
    NEW_CAR_OFFERS = "new_car_offers"
    SERVICE_TYPES = "service_types"
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
    OTHER = "other"


WORKFLOW_INTENTS: dict[Intent, WorkflowKind] = {
    Intent(kind.value): kind for kind in WorkflowKind
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchFilters(Strict):
    q: str | None = None
    make: str | None = None
    fuel_type: FuelType | None = None
    body_style: BodyStyle | None = None
    transmission: Transmission | None = None
    max_price_pounds: int | None = Field(default=None, ge=0)


class SlotGuess(Strict):
    name: str
    value: SlotScalar


class Interpretation(Strict):
    """A proposal only. Nothing here can confirm, write, or state a fact to the customer."""

    intent: Intent
    filters: SearchFilters | None = None
    dealership: Literal["manchester", "stockport", "liverpool", "bolton"] | None = None
    vehicle_handle: str | None = None
    slots: list[SlotGuess] = []


class InterpretationSource(StrEnum):
    MODEL = "model"
    REPAIRED = "repaired"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


class Interpreted(BaseModel):
    interpretation: Interpretation
    source: InterpretationSource
