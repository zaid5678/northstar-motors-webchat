"""The service must depend on the repository contract, not on SQLite. A second implementation,
kept deliberately small, runs a real write to prove it."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from northstar_webchat.dealership import DealershipClient
from northstar_webchat.dealership.outcomes import Outcome
from northstar_webchat.persistence.repository import Repository
from northstar_webchat.persistence.store import IN_FLIGHT, Store
from northstar_webchat.workflows.machine import transition
from northstar_webchat.workflows.types import (
    Event,
    Incoming,
    Phase,
    Session,
    Source,
    Transition,
)
from northstar_webchat.writes.service import WriteService

from .conftest import counts, ready_test_drive, token_of


class MemoryRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.times: dict[str, datetime] = {}
        self.requests: dict[str, tuple[str, str, str]] = {}
        self.bad_slots: dict[str, datetime] = {}
        self.failures: dict[str, int] = {}
        self.transcripts: dict[str, list[dict[str, Any]]] = {}
        self.stream: dict[str, list[dict[str, Any]]] = {}
        self.memory: dict[str, dict[str, Any]] = {}
        self.hidden: set[str] = set()
        self.counter = 0

    def _log(self, session_id: str, kind: str, payload: dict[str, Any]) -> None:
        rows = self.events.setdefault(session_id, [])
        rows.append({"seq": len(rows) + 1, "kind": kind, "payload": payload})

    def apply(
        self,
        session_id: str,
        source: Source,
        item: Event,
        *,
        at: datetime | None = None,
        outcome: Outcome | None = None,
        intent: tuple[str, str, str] | None = None,
    ) -> Transition:
        moment = at or datetime.now(timezone.utc)
        with self._lock:
            self.counter += 1
            result = transition(
                self.sessions.get(session_id, Session()),
                Incoming(
                    source=source,
                    event=item,
                    at=moment,
                    token=f"token-{self.counter}",
                    idempotency_key=f"key-{self.counter}",
                ),
            )
            if outcome is not None:
                self._log(
                    session_id,
                    f"write_outcome:{type(outcome).__name__}",
                    json.loads(outcome.model_dump_json()),
                )
            if intent is not None and result.accepted:
                self.requests.setdefault(intent[0], (session_id, intent[1], intent[2]))
            name = type(item).__name__
            self._log(session_id, name if result.accepted else f"{name}:rejected", {})
            self.hidden.discard(session_id)
            if result.accepted:
                self.sessions[session_id] = result.session
                self.times[session_id] = moment
        return result

    def note(self, session_id: str, kind: str, source: Source, at: datetime, **detail: Any) -> None:
        self._log(session_id, kind, detail)

    def record_outcome(self, session_id: str, outcome: Outcome, at: datetime) -> None:
        self._log(
            session_id,
            f"write_outcome:{type(outcome).__name__}",
            json.loads(outcome.model_dump_json()),
        )

    def updated_at(self, session_id: str) -> datetime | None:
        return self.times.get(session_id)

    def load(self, session_id: str) -> Session:
        return self.sessions.get(session_id, Session())

    def phase(self, session_id: str) -> Phase | None:
        active = self.load(session_id).active
        return active.phase if active else None

    def in_flight(self) -> list[str]:
        return [key for key in self.sessions if self.phase(key) in IN_FLIGHT]

    def dump(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.events.get(session_id, []))

    def kinds(self, session_id: str) -> list[str]:
        return [row["kind"] for row in self.dump(session_id)]

    def last_outcome(self, session_id: str) -> dict[str, Any] | None:
        rows = [r for r in self.dump(session_id) if r["kind"].startswith("write_outcome:")]
        return rows[-1]["payload"] if rows else None

    def write_requests(self, session_id: str) -> list[dict[str, str]]:
        return [
            {"key": key, "kind": value[1]}
            for key, value in self.requests.items()
            if value[0] == session_id
        ]

    def write_request(self, key: str) -> str:
        return self.requests[key][2]

    def mark_unbookable(self, slot_id: str, until: datetime) -> None:
        self.bad_slots[slot_id] = until

    def is_unbookable(self, slot_id: str, at: datetime) -> bool:
        return slot_id in self.bad_slots and self.bad_slots[slot_id] > at

    def reference_locked_until(self, reference_hash: str, at: datetime) -> datetime | None:
        return at + timedelta(minutes=15) if self.failures.get(reference_hash, 0) >= 5 else None

    def record_reference_failure(self, reference_hash: str, at: datetime) -> None:
        self.failures[reference_hash] = self.failures.get(reference_hash, 0) + 1

    def clear_reference_failures(self, reference_hash: str) -> None:
        self.failures.pop(reference_hash, None)

    def add_message(
        self, session_id: str, role: str, payload: dict[str, Any], at: datetime
    ) -> None:
        self.transcripts.setdefault(session_id, []).append({"role": role, **payload})

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.transcripts.get(session_id, []))

    def add_stream_event(
        self, session_id: str, name: str, data: dict[str, Any], at: datetime
    ) -> int:
        self.counter += 1
        rows = self.stream.setdefault(session_id, [])
        rows.append({"id": self.counter, "name": name, "data": data})
        return self.counter

    def stream_events(self, session_id: str, after: int) -> list[dict[str, Any]]:
        return [row for row in self.stream.get(session_id, []) if row["id"] > after]

    def last_stream_event_id(self, session_id: str) -> int:
        rows = self.stream.get(session_id, [])
        return rows[-1]["id"] if rows else 0

    def remember(self, session_id: str, data: dict[str, Any], at: datetime) -> None:
        self.memory[session_id] = dict(data)

    def recall(self, session_id: str) -> dict[str, Any] | None:
        return self.memory.get(session_id)

    def hide_form(self, session_id: str, at: datetime) -> None:
        self.hidden.add(session_id)

    def show_form(self, session_id: str) -> None:
        self.hidden.discard(session_id)

    def form_hidden(self, session_id: str) -> bool:
        return session_id in self.hidden


def test_both_implementations_satisfy_the_contract(store: Store) -> None:
    assert isinstance(store, Repository)
    assert isinstance(MemoryRepository(), Repository)


async def test_a_real_write_runs_on_another_repository(
    client: DealershipClient, clean_platform: str
) -> None:
    repository = MemoryRepository()
    service = WriteService(repository, client, pepper=b"x", reconcile_delay=0.0)
    view = await ready_test_drive(service, client)
    result = await service.confirm("s1", token_of(view))
    assert result.status == "completed"
    assert counts(clean_platform)["testDriveBookings"] == 1
    assert "write_outcome:TestDriveBooked" in repository.kinds("s1")


def test_nothing_outside_persistence_imports_sqlalchemy() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "northstar_webchat"
    for path in source.rglob("*.py"):
        if "persistence" not in path.parts and "migrations" not in path.parts:
            assert "sqlalchemy" not in path.read_text(encoding="utf-8")
