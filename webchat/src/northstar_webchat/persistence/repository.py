"""The storage contract. The service depends on this, so SQLite can be replaced without touching it.

Two rules any implementation must keep:

- `apply` is atomic: the transition, its events, and the stored request commit together, and two
  concurrent calls for one session are serialised. Exactly-once token claims rely on this.
- The stored request is durable before `apply` returns, because the write leaves straight after.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..dealership.outcomes import Outcome
from ..workflows.types import Event, Phase, Session, Source, Transition


@runtime_checkable
class Repository(Protocol):
    def apply(
        self,
        session_id: str,
        source: Source,
        item: Event,
        *,
        at: datetime | None = None,
        outcome: Outcome | None = None,
        intent: tuple[str, str, str] | None = None,
    ) -> Transition: ...

    def note(
        self, session_id: str, kind: str, source: Source, at: datetime, **detail: Any
    ) -> None: ...

    def record_outcome(self, session_id: str, outcome: Outcome, at: datetime) -> None: ...

    def load(self, session_id: str) -> Session: ...

    def phase(self, session_id: str) -> Phase | None: ...

    def updated_at(self, session_id: str) -> datetime | None: ...

    def in_flight(self) -> list[str]: ...

    def dump(self, session_id: str) -> list[dict[str, Any]]: ...

    def kinds(self, session_id: str) -> list[str]: ...

    def last_outcome(self, session_id: str) -> dict[str, Any] | None: ...

    def write_requests(self, session_id: str) -> list[dict[str, str]]: ...

    def write_request(self, key: str) -> str: ...

    def mark_unbookable(self, slot_id: str, until: datetime) -> None: ...

    def is_unbookable(self, slot_id: str, at: datetime) -> bool: ...

    def reference_locked_until(self, reference_hash: str, at: datetime) -> datetime | None: ...

    def record_reference_failure(self, reference_hash: str, at: datetime) -> None: ...

    def clear_reference_failures(self, reference_hash: str) -> None: ...

    def add_message(
        self, session_id: str, role: str, payload: dict[str, Any], at: datetime
    ) -> None: ...

    def messages(self, session_id: str) -> list[dict[str, Any]]: ...

    def add_stream_event(
        self, session_id: str, name: str, data: dict[str, Any], at: datetime
    ) -> int: ...

    def stream_events(self, session_id: str, after: int) -> list[dict[str, Any]]: ...

    def last_stream_event_id(self, session_id: str) -> int: ...
