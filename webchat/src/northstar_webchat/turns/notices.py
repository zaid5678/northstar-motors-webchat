"""Compliance notices. Workflow state and tool results trigger them, never the model, and they are
added outside the model's text, so it can neither drop nor reword them."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from ..dealership import outcomes as o
from ..dealership.client import DealershipClient
from ..dealership.models import BusinessInformation
from ..workflows.types import DEFINITIONS, TERMINAL_PHASES, Workflow, WorkflowKind


class NoticeKind(StrEnum):
    FINANCE = "finance"
    PRIVACY = "privacy"
    PART_EXCHANGE = "part_exchange"


def notices_for(workflow: Workflow | None, outcomes: Sequence[o.Outcome]) -> list[NoticeKind]:
    found: list[NoticeKind] = []
    if workflow is not None and workflow.phase not in TERMINAL_PHASES:
        if "email" in DEFINITIONS[workflow.kind].required:
            found.append(NoticeKind.PRIVACY)
        enquiry = workflow.slots.get("enquiry_type")
        topic = enquiry.value if enquiry else None
        if workflow.kind is WorkflowKind.PART_EXCHANGE or topic == "part-exchange":
            found.append(NoticeKind.PART_EXCHANGE)
        if topic == "finance":
            found.append(NoticeKind.FINANCE)
    for outcome in outcomes:
        if isinstance(outcome, (o.OffersListed, o.OfferFound)):
            found.append(NoticeKind.FINANCE)
        if isinstance(outcome, o.EstimateGiven):
            found.append(NoticeKind.PART_EXCHANGE)
    return list(dict.fromkeys(found))


class NoticeBook:
    """Serves the platform's own wording, and keeps the last copy through a platform outage."""

    def __init__(self, client: DealershipClient) -> None:
        self._client = client
        self._information: BusinessInformation | None = None

    async def text(self, kind: NoticeKind) -> str | None:
        found = await self._client.get_business_information()
        if isinstance(found, o.BusinessInformationFound):
            self._information = found.information
        information = self._information
        if information is None:
            return None
        if kind is NoticeKind.FINANCE:
            return f"{information.finance.notice} Applicants must be {information.finance.minimum_age} or over."
        if kind is NoticeKind.PART_EXCHANGE:
            return information.part_exchange.estimate_notice
        return (
            "We'll use your details to handle this request and pass them to the dealership. "
            f"Questions about your data: {information.privacy_contact}."
        )
