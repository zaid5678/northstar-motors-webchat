"""HTTP routes for the customer's click. The session comes only from the HTTP-only cookie."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..persistence.store import IN_FLIGHT
from ..turns.engine import MENU, TurnEngine, TurnReply
from ..workflows.types import SlotScalar
from .present import View
from .service import WriteService

COOKIE = "northstar_session"
THIRTY_DAYS = 60 * 60 * 24 * 30
HEARTBEAT_SECONDS = 15.0
RESUME_POLL_SECONDS = 0.1
RESUME_LIMIT_SECONDS = 60.0
TURN_FAILED = "Sorry, something went wrong with that message. Please try again."
FINAL_STATUSES = ("completed", "failed", "unknown", "two_bookings")
WIDGET = Path(__file__).resolve().parent.parent / "static" / "widget.js"


class Click(BaseModel):
    token: str


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    page_vehicle: str | None = None


class Button(BaseModel):
    id: str


class SlotInput(BaseModel):
    name: str
    value: SlotScalar


class IdentityInput(BaseModel):
    reference: str
    last_name: str
    registration: str
    phone: str


def _session(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail="No session.")
    return value


def _frame(event_id: int, name: str, data: dict[str, Any]) -> str:
    return f"id: {event_id}\nevent: {name}\ndata: {json.dumps(data)}\n\n"


def create_app(
    service: WriteService,
    engine: TurnEngine | None = None,
    *,
    allowed_origin: str | None = None,
    inspector: bool = False,
) -> FastAPI:
    store = service.store

    def emit(session: str, name: str, data: dict[str, Any]) -> str:
        """Saved first, so a reconnecting browser can be given exactly what it missed."""
        event_id = store.add_stream_event(session, name, data, datetime.now(timezone.utc))
        return _frame(event_id, name, data)

    def pending(session: str) -> bool:
        messages = store.messages(session)
        return bool(messages) and messages[-1]["role"] == "user"

    def recorded(session: str, view: View) -> View:
        """A write's result joins the transcript, once, so a refresh still shows it."""
        if view.status in FINAL_STATUSES and view.message:
            messages = store.messages(session)
            if not messages or messages[-1].get("text") != view.message:
                store.add_message(
                    session,
                    "assistant",
                    {"text": view.message},
                    datetime.now(timezone.utc),
                )
        return view

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.recover()  # Interrupted writes finish before any new traffic is served.
        yield

    app = FastAPI(title="Northstar Motors webchat", lifespan=lifespan)
    turns: set[asyncio.Task[None]] = set()
    if allowed_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[allowed_origin],
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )

    @app.get("/widget.js")
    async def widget() -> FileResponse:
        if not WIDGET.is_file():
            raise HTTPException(status_code=404, detail="The widget has not been built.")
        return FileResponse(WIDGET, media_type="text/javascript")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/session")
    async def new_session(
        response: Response, northstar_session: str | None = Cookie(default=None)
    ) -> dict[str, bool]:
        """Keeps an existing session, so a returning customer finds their conversation."""
        if not northstar_session:
            response.set_cookie(
                COOKIE, str(uuid.uuid4()), httponly=True, samesite="lax", max_age=THIRTY_DAYS
            )
        return {"ok": True}

    @app.post("/api/confirmations")
    async def confirm(click: Click, northstar_session: str | None = Cookie(default=None)) -> View:
        session = _session(northstar_session)
        return recorded(session, await service.confirm(session, click.token))

    @app.post("/api/confirmations/check-again")
    async def check_again(northstar_session: str | None = Cookie(default=None)) -> View:
        session = _session(northstar_session)
        return recorded(session, await service.check_again(session))

    @app.get("/api/conversation/state")
    async def state(northstar_session: str | None = Cookie(default=None)) -> View:
        return await service.view(_session(northstar_session))

    if engine is not None:

        @app.post("/api/chat/message")
        async def message(
            body: ChatMessage, northstar_session: str | None = Cookie(default=None)
        ) -> TurnReply:
            session = _session(northstar_session)
            return await engine.message(session, body.text, page_vehicle=body.page_vehicle)

        @app.post("/api/chat/stream")
        async def stream(
            body: ChatMessage, northstar_session: str | None = Cookie(default=None)
        ) -> StreamingResponse:
            """Progress, then the reply. The turn runs detached, so a refresh cannot lose it: the
            answer lands in the transcript whether or not this connection is still open."""
            session = _session(northstar_session)
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def progress(stage: str) -> None:
                queue.put_nowait(emit(session, "progress", {"stage": stage}))

            async def run() -> None:
                try:
                    reply = await engine.message(
                        session, body.text, page_vehicle=body.page_vehicle, progress=progress
                    )
                    # No view here: it can hold a token, and this event is stored.
                    data = reply.model_dump(mode="json", exclude={"view"})
                    queue.put_nowait(emit(session, "reply", data))
                except Exception:
                    store.add_message(
                        session, "assistant", {"text": TURN_FAILED}, datetime.now(timezone.utc)
                    )
                    queue.put_nowait(emit(session, "error", {}))
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(run())
            turns.add(task)
            task.add_done_callback(turns.discard)

            async def events() -> AsyncIterator[str]:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item is None:
                        return
                    yield item

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

        @app.get("/api/chat/stream")
        async def resume(
            last_event_id: str | None = Header(default=None),
            northstar_session: str | None = Cookie(default=None),
        ) -> StreamingResponse:
            """Replays what a browser missed, then follows any turn still running."""
            session = _session(northstar_session)
            try:
                after = int(last_event_id or 0)
            except ValueError:
                raise HTTPException(status_code=400, detail="Bad event ID.") from None

            async def events() -> AsyncIterator[str]:
                cursor = after
                waited = 0.0
                while True:
                    running = pending(session)
                    for row in store.stream_events(session, cursor):
                        cursor = row["id"]
                        yield _frame(row["id"], row["name"], row["data"])
                    if not running:
                        return
                    if waited >= RESUME_LIMIT_SECONDS:
                        yield emit(session, "error", {})
                        return
                    await asyncio.sleep(RESUME_POLL_SECONDS)
                    waited += RESUME_POLL_SECONDS

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

        @app.get("/api/chat/transcript")
        async def transcript(
            northstar_session: str | None = Cookie(default=None),
        ) -> dict[str, Any]:
            session = _session(northstar_session)
            messages = store.messages(session)
            return {
                "messages": messages,
                "pending": pending(session),
                "last_event_id": store.last_stream_event_id(session),
                "actions": [] if messages else [item.model_dump() for item in MENU],
                "view": (await service.view(session)).model_dump(mode="json"),
            }

        @app.post("/api/chat/action")
        async def action(
            body: Button, northstar_session: str | None = Cookie(default=None)
        ) -> TurnReply:
            return await engine.action(_session(northstar_session), body.id)

        @app.post("/api/chat/slot")
        async def slot(
            body: SlotInput, northstar_session: str | None = Cookie(default=None)
        ) -> View:
            return await service.fill(_session(northstar_session), body.name, body.value)

        @app.post("/api/chat/identity")
        async def identity(
            body: IdentityInput, northstar_session: str | None = Cookie(default=None)
        ) -> View:
            return await service.submit_identity(
                _session(northstar_session), **body.model_dump()
            )

    if inspector:

        @app.get("/api/dev/inspect")
        async def inspect(northstar_session: str | None = Cookie(default=None)) -> dict[str, Any]:
            """Development only, and absent unless switched on. No tokens or customer details."""
            session = _session(northstar_session)
            state = store.load(session)
            active = state.active
            rows = store.dump(session)

            def recent(kind: str) -> list[dict[str, Any]]:
                return [row["payload"] for row in rows if row["kind"] == kind][-20:]

            return {
                "session": {
                    "workflow": active.kind.value if active else None,
                    "phase": active.phase.value if active else None,
                    "slots": {
                        name: slot.provenance.value for name, slot in active.slots.items()
                    }
                    if active
                    else {},
                    "verified": state.verification is not None,
                    "failed_attempts": state.failed_attempts,
                    "locked": state.locked_until is not None,
                },
                "pending": {
                    "confirmation": bool(active and active.pending),
                    "write_in_flight": bool(active and active.phase in IN_FLIGHT),
                    "step": active.step.value if active else None,
                    "writes": [item["kind"] for item in store.write_requests(session)],
                },
                "tool_calls": recent("tool_call"),
                "verdicts": recent("grounding"),
            }

    return app
