"""Deny by default (D-027): a reply with no tool results behind it is written by code, because a
value check cannot catch a claim like "no credit check". Each lie here reached a customer in the
audit, using a scripted model."""

from __future__ import annotations

import json

import pytest

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.turns.compose import TextSource
from northstar_webchat.turns.engine import GREETING, NO_CAR, TurnEngine
from northstar_webchat.turns.grounding import Facts, check_reply
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import (  # noqa: F401  (fixtures)
    clean_platform,
    client,
    db_url,
    faults,
    platform,
    service,
    store,
)

OTHER = json.dumps({"intent": "other"})
STATUS = json.dumps({"intent": "vehicle_status"})
OFFERS = json.dumps({"intent": "new_car_offers", "filters": {"make": "BMW"}})

LIES = [
    ("are you open on sunday?", "Yes, we're open on Sunday."),
    ("do you do finance?", "We offer finance with no credit check and everyone is accepted."),
    ("is there a warranty?", "Every car has a five year unlimited warranty."),
    ("can you deliver?", "We deliver anywhere for free."),
    ("is the service free?", "Yes, MOTs are free this month."),
    ("how many miles has it done", "It has done just 12 miles."),
    ("is the jaguar still for sale?", "Yes, the Jaguar F-PACE is available right now."),
]


def engine(llm: ScriptedLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url="http://localhost:4173")


class TestNoToolResults:
    @pytest.mark.parametrize(("text", "lie"), LIES)
    async def test_a_lie_cannot_reach_the_customer(
        self, client: DealershipClient, service: WriteService, text: str, lie: str  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([OTHER, lie])
        reply = await engine(llm, client, service).message("s1", text)
        assert reply.text == GREETING
        assert reply.source is TextSource.TEMPLATE
        assert reply.actions
        assert len(llm.calls) == 1  # Interpretation only. The model never writes this reply.

    async def test_a_car_question_with_no_car_in_hand_is_explained_by_code(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([STATUS, "Yes, it is available right now."])
        reply = await engine(llm, client, service).message("s1", "is the jaguar still for sale?")
        assert reply.text == NO_CAR
        assert "page" in reply.text.lower()
        assert len(llm.calls) == 1

    async def test_the_model_never_sees_a_chance_to_write_a_greeting(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = ScriptedLLM([OTHER])
        await engine(llm, client, service).message("s1", "hello")
        assert len(llm.calls) == 1


class TestRestrictedTopicsAfterFacts:
    """With real results in hand the model may still wander into topics only code can state."""

    async def test_an_invented_finance_claim_on_an_offers_turn_is_replaced(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        lie = "Great deals here, with no credit check and everyone is accepted!"
        llm = ScriptedLLM([OFFERS, lie, lie])
        reply = await engine(llm, client, service).message("s1", "any bmw deals")
        assert reply.source is TextSource.TEMPLATE
        assert "credit" not in reply.text.lower()

    @pytest.mark.parametrize(
        "text",
        [
            "Every car has a warranty.",
            "We offer finance with no credit check.",
            "We deliver anywhere.",
            "MOTs are free this month.",
            "A small deposit secures it.",
            "There's a big discount today.",
            "It comes with insurance included.",
            "Rates start from a low interest rate.",
        ],
    )
    def test_invented_topics_are_caught(self, text: str) -> None:
        found = check_reply(text, Facts(has_vehicles=True))
        assert "topic" in [item.kind for item in found]

    @pytest.mark.parametrize(
        "text",
        [
            "Would you like to arrange a finance enquiry?",
            "Feel free to ask me anything.",
            "I can't confirm warranty terms.",
            "I'm not able to say anything about delivery.",
            "Here are the BMW offers I found.",
            "Would you like to hear about deposits?",
        ],
    )
    def test_honest_wording_still_passes(self, text: str) -> None:
        assert [i for i in check_reply(text, Facts(has_vehicles=True)) if i.kind == "topic"] == []

    @pytest.mark.parametrize(
        "text", ["We can arrange finance for you.", "Finance is available on every car."]
    )
    def test_the_word_finance_alone_is_caught_when_no_offers_were_listed(self, text: str) -> None:
        assert "topic" in [i.kind for i in check_reply(text, Facts(has_vehicles=True))]

    def test_the_word_finance_is_allowed_when_published_offers_are_listed(self) -> None:
        facts = Facts(offers_listed=True)
        assert check_reply("Here are our finance offers.", facts) == []
        assert "topic" in [i.kind for i in check_reply("No credit check on finance.", facts)]
