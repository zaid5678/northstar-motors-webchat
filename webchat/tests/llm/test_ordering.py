"""Per-session ordering (D-024). A customer's turns run one at a time, in the order they arrived."""

from __future__ import annotations

import asyncio
import json

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.llm.types import Message
from northstar_webchat.turns.compose import TextSource
from northstar_webchat.turns.engine import TurnEngine
from northstar_webchat.writes.service import WriteService

from ..writes.conftest import counts, ready_test_drive, token_of
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


class HeldLLM:
    """Stops at each call until released, and records the order of customer messages."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.seen: list[str] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.seen.append(messages[-1].content)
        self.waiting.set()
        await self.release.wait()
        self.active -= 1
        return OTHER if schema is not None else "Anything else?"


def engine(llm: HeldLLM, client: DealershipClient, service: WriteService) -> TurnEngine:  # noqa: F811
    return TurnEngine(llm, client, service, site_url="http://localhost:4173")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


class TestOneTurnAtATime:
    async def test_two_messages_never_overlap(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        turn = engine(llm, client, service)
        tasks = [asyncio.create_task(turn.message("s1", text)) for text in ("first", "second")]
        await llm.waiting.wait()
        await settle()
        assert llm.active == 1
        llm.release.set()
        await asyncio.gather(*tasks)
        assert llm.peak == 1

    async def test_turns_run_in_arrival_order(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        turn = engine(llm, client, service)
        tasks = [asyncio.create_task(turn.message("s1", f"message-{n}")) for n in range(3)]
        await settle()
        llm.release.set()
        await asyncio.gather(*tasks)
        order = [
            next(n for n in range(3) if f"message-{n}" in item)
            for item in llm.seen
            if "message-" in item
        ]
        assert order == sorted(order)

    async def test_other_customers_are_not_blocked(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        turn = engine(llm, client, service)
        tasks = [asyncio.create_task(turn.message(s, "hi")) for s in ("a", "b")]
        await llm.waiting.wait()
        await settle()
        assert llm.active == 2
        llm.release.set()
        await asyncio.gather(*tasks)

    async def test_a_button_waits_its_turn(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        turn = engine(llm, client, service)
        message = asyncio.create_task(turn.message("s1", "first"))
        await llm.waiting.wait()
        button = asyncio.create_task(turn.action("s1", "callback"))
        await settle()
        assert not button.done()
        llm.release.set()
        await asyncio.gather(message, button)


class TestTheQueueIsBounded:
    async def test_too_many_waiting_turns_are_turned_away_honestly(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        turn = engine(llm, client, service)
        tasks = [asyncio.create_task(turn.message("s1", f"m{n}")) for n in range(6)]
        await settle()
        finished = [task for task in tasks if task.done()]
        assert len(finished) == 2
        reply = finished[0].result()
        assert "still working" in reply.text.lower()
        assert reply.source is TextSource.TEMPLATE
        assert "turn_busy" in service.store.kinds("s1")
        llm.release.set()
        await asyncio.gather(*tasks)

    async def test_finished_sessions_leave_nothing_behind(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        llm.release.set()
        turn = engine(llm, client, service)
        await turn.message("s1", "hi")
        assert turn.sessions_waiting == 0

    async def test_a_failed_turn_releases_the_session(
        self, client: DealershipClient, service: WriteService  # noqa: F811
    ) -> None:
        llm = HeldLLM()
        llm.release.set()
        turn = engine(llm, client, service)
        task = asyncio.create_task(turn.message("s1", "hi"))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await turn.message("s1", "again")
        assert turn.sessions_waiting == 0


class TestConfirmationsDoNotQueue:
    async def test_a_click_is_not_held_behind_a_slow_model(
        self,
        client: DealershipClient,  # noqa: F811
        service: WriteService,  # noqa: F811
        clean_platform: str,  # noqa: F811
    ) -> None:
        view = await ready_test_drive(service, client)
        llm = HeldLLM()
        turn = engine(llm, client, service)
        message = asyncio.create_task(turn.message("s1", "are you there?"))
        await llm.waiting.wait()
        result = await asyncio.wait_for(service.confirm("s1", token_of(view)), timeout=5)
        assert result.status == "completed"
        assert counts(clean_platform)["testDriveBookings"] == 1
        llm.release.set()
        await message
