"""Durable storage. One `apply` is one transaction.

The state change, its events, and any stored write request commit together, so a crash can never
leave a write in flight without a record of it. Each transaction starts with `BEGIN IMMEDIATE`, so
two clicks racing for one token are serialised and exactly one claims it.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event, func, insert, select, update
from sqlalchemy.engine import Engine

from ..dealership.outcomes import Outcome
from ..workflows.machine import transition
from ..workflows.types import (
    LOCK_DURATION,
    MAX_FAILED_ATTEMPTS,
    ConfirmAction,
    Event,
    Incoming,
    Phase,
    Session,
    Source,
    Transition,
)
from .tables import (
    events,
    hidden_forms,
    messages,
    reference_failures,
    search_memory,
    sessions,
    stream_events,
    unbookable_slots,
    write_requests,
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
IN_FLIGHT = (
    Phase.CHECKING_IDENTITY,
    Phase.RECHECKING,
    Phase.EXECUTING,
    Phase.READING_BACK,
    Phase.OUTCOME_UNKNOWN,
)
INTENT = "write_intent"
OUTCOME = "write_outcome"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _payload(item: Event) -> str:
    """Tokens are never logged, and identity values are excluded by the event itself."""
    if isinstance(item, ConfirmAction):
        return json.dumps({"token": "[redacted]"})
    return item.model_dump_json()


def _engine(url: str) -> Engine:
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _: Any) -> None:
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")  # A commit must survive a power cut too.
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


class Store:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = _engine(url)

    def migrate(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS))
        config.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        command.upgrade(config, "head")

    def _read(self, connection: Connection, session_id: str) -> Session:
        state = connection.scalar(select(sessions.c.state).where(sessions.c.id == session_id))
        return Session.model_validate_json(state) if state else Session()

    def _log(
        self,
        connection: Connection,
        session_id: str,
        at: datetime,
        kind: str,
        source: str,
        phase: Phase | None,
        payload: str,
    ) -> None:
        latest = connection.scalar(
            select(func.max(events.c.seq)).where(events.c.session_id == session_id)
        )
        connection.execute(
            insert(events).values(
                session_id=session_id,
                seq=(latest or 0) + 1,
                at=at,
                kind=kind,
                source=source,
                phase=phase,
                payload=payload,
            )
        )

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
        """Runs one transition. `intent` is (key, kind, request): the request is on disk before
        the write can leave this process."""
        moment = at or datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            session = self._read(connection, session_id)
            result = transition(
                session,
                Incoming(
                    source=source,
                    event=item,
                    at=moment,
                    token=secrets.token_urlsafe(24),
                    idempotency_key=str(uuid.uuid4()),
                ),
            )
            phase = result.session.active.phase if result.session.active else None
            if outcome is not None:
                self._log(
                    connection,
                    session_id,
                    moment,
                    f"{OUTCOME}:{type(outcome).__name__}",
                    Source.PLATFORM,
                    phase,
                    outcome.model_dump_json(),
                )
            if intent is not None and result.accepted:
                key, kind, request = intent
                exists = connection.scalar(
                    select(write_requests.c.idempotency_key).where(
                        write_requests.c.idempotency_key == key
                    )
                )
                if exists is None:
                    connection.execute(
                        insert(write_requests).values(
                            idempotency_key=key,
                            session_id=session_id,
                            kind=kind,
                            request=request,
                            created_at=moment,
                        )
                    )
                self._log(
                    connection, session_id, moment, INTENT, source, phase, json.dumps({"kind": kind})
                )
            name = type(item).__name__
            self._log(
                connection,
                session_id,
                moment,
                name if result.accepted else f"{name}:rejected",
                source,
                phase,
                _payload(item),
            )
            # Anything that moves the task on, or is refused by it, needs the form seen (D-034).
            connection.execute(hidden_forms.delete().where(hidden_forms.c.session_id == session_id))
            if result.accepted:
                self._save(connection, session_id, result.session, moment)
        return result

    def _save(
        self, connection: Connection, session_id: str, session: Session, at: datetime
    ) -> None:
        values = {
            "state": session.model_dump_json(),
            "phase": session.active.phase if session.active else None,
            "updated_at": at,
        }
        changed = connection.execute(
            update(sessions).where(sessions.c.id == session_id).values(**values)
        )
        if changed.rowcount == 0:
            connection.execute(insert(sessions).values(id=session_id, **values))

    def note(self, session_id: str, kind: str, source: Source, at: datetime, **detail: Any) -> None:
        with self.engine.begin() as connection:
            self._log(
                connection, session_id, at, kind, source, self._phase(connection, session_id),
                json.dumps(detail),
            )

    def _phase(self, connection: Connection, session_id: str) -> Phase | None:
        value = connection.scalar(select(sessions.c.phase).where(sessions.c.id == session_id))
        return Phase(value) if value else None

    def record_outcome(self, session_id: str, outcome: Outcome, at: datetime) -> None:
        with self.engine.begin() as connection:
            self._log(
                connection,
                session_id,
                at,
                f"{OUTCOME}:{type(outcome).__name__}",
                Source.PLATFORM,
                self._phase(connection, session_id),
                outcome.model_dump_json(),
            )

    def updated_at(self, session_id: str) -> datetime | None:
        with self.engine.begin() as connection:
            value = connection.scalar(
                select(sessions.c.updated_at).where(sessions.c.id == session_id)
            )
        return _utc(value) if value else None

    def load(self, session_id: str) -> Session:
        with self.engine.begin() as connection:
            return self._read(connection, session_id)

    def phase(self, session_id: str) -> Phase | None:
        with self.engine.begin() as connection:
            return self._phase(connection, session_id)

    def in_flight(self) -> list[str]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(sessions.c.id).where(sessions.c.phase.in_([p.value for p in IN_FLIGHT]))
            )
            return [row.id for row in rows]

    def dump(self, session_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(events).where(events.c.session_id == session_id).order_by(events.c.seq)
            )
            return [
                {
                    "seq": row.seq,
                    "kind": row.kind,
                    "source": row.source,
                    "phase": row.phase,
                    "payload": json.loads(row.payload),
                }
                for row in rows
            ]

    def kinds(self, session_id: str) -> list[str]:
        return [row["kind"] for row in self.dump(session_id)]

    def last_outcome(self, session_id: str) -> dict[str, Any] | None:
        found = [row for row in self.dump(session_id) if row["kind"].startswith(f"{OUTCOME}:")]
        return found[-1]["payload"] if found else None

    def write_requests(self, session_id: str) -> list[dict[str, str]]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(write_requests).where(write_requests.c.session_id == session_id)
            )
            return [{"key": row.idempotency_key, "kind": row.kind} for row in rows]

    def write_request(self, key: str) -> str:
        with self.engine.begin() as connection:
            request = connection.scalar(
                select(write_requests.c.request).where(write_requests.c.idempotency_key == key)
            )
        if request is None:
            raise LookupError("no stored request for this key")
        return str(request)

    def mark_unbookable(self, slot_id: str, until: datetime) -> None:
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(unbookable_slots)
                .where(unbookable_slots.c.slot_id == slot_id)
                .values(until=until)
            )
            if changed.rowcount == 0:
                connection.execute(insert(unbookable_slots).values(slot_id=slot_id, until=until))

    def is_unbookable(self, slot_id: str, at: datetime) -> bool:
        with self.engine.begin() as connection:
            until = connection.scalar(
                select(unbookable_slots.c.until).where(unbookable_slots.c.slot_id == slot_id)
            )
        return until is not None and _utc(until) > at

    def reference_locked_until(self, reference_hash: str, at: datetime) -> datetime | None:
        with self.engine.begin() as connection:
            until = connection.scalar(
                select(reference_failures.c.locked_until).where(
                    reference_failures.c.reference_hash == reference_hash
                )
            )
        if until is None or _utc(until) <= at:
            return None
        return _utc(until)

    def record_reference_failure(self, reference_hash: str, at: datetime) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(reference_failures).where(
                    reference_failures.c.reference_hash == reference_hash
                )
            ).first()
            if row is None:
                connection.execute(
                    insert(reference_failures).values(
                        reference_hash=reference_hash, failures=1, locked_until=None
                    )
                )
                return
            expired = row.locked_until is not None and _utc(row.locked_until) <= at
            failures = 1 if expired else row.failures + 1
            locked = at + LOCK_DURATION if failures >= MAX_FAILED_ATTEMPTS else None
            connection.execute(
                update(reference_failures)
                .where(reference_failures.c.reference_hash == reference_hash)
                .values(failures=failures, locked_until=locked)
            )

    def clear_reference_failures(self, reference_hash: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                reference_failures.delete().where(
                    reference_failures.c.reference_hash == reference_hash
                )
            )

    def add_message(
        self, session_id: str, role: str, payload: dict[str, Any], at: datetime
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(messages).values(
                    session_id=session_id, role=role, payload=json.dumps(payload), at=at
                )
            )

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(messages).where(messages.c.session_id == session_id).order_by(messages.c.id)
            )
            return [{"role": row.role, **json.loads(row.payload)} for row in rows]

    def add_stream_event(
        self, session_id: str, name: str, data: dict[str, Any], at: datetime
    ) -> int:
        with self.engine.begin() as connection:
            result = connection.execute(
                insert(stream_events).values(
                    session_id=session_id, name=name, data=json.dumps(data), at=at
                )
            )
            return int(result.inserted_primary_key[0])  # type: ignore[index]

    def stream_events(self, session_id: str, after: int) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(stream_events)
                .where(stream_events.c.session_id == session_id, stream_events.c.id > after)
                .order_by(stream_events.c.id)
            )
            return [{"id": row.id, "name": row.name, "data": json.loads(row.data)} for row in rows]

    def last_stream_event_id(self, session_id: str) -> int:
        with self.engine.begin() as connection:
            latest = connection.scalar(
                select(func.max(stream_events.c.id)).where(stream_events.c.session_id == session_id)
            )
        return int(latest or 0)

    def remember(self, session_id: str, data: dict[str, Any], at: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                search_memory.delete().where(search_memory.c.session_id == session_id)
            )
            connection.execute(
                insert(search_memory).values(session_id=session_id, data=json.dumps(data), at=at)
            )

    def recall(self, session_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            data = connection.scalar(
                select(search_memory.c.data).where(search_memory.c.session_id == session_id)
            )
        return None if data is None else dict(json.loads(data))

    def hide_form(self, session_id: str, at: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(hidden_forms.delete().where(hidden_forms.c.session_id == session_id))
            connection.execute(insert(hidden_forms).values(session_id=session_id, at=at))

    def show_form(self, session_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(hidden_forms.delete().where(hidden_forms.c.session_id == session_id))

    def form_hidden(self, session_id: str) -> bool:
        with self.engine.begin() as connection:
            found = connection.scalar(
                select(hidden_forms.c.session_id).where(hidden_forms.c.session_id == session_id)
            )
        return found is not None

    def reference_failures_total(self) -> int:
        with self.engine.begin() as connection:
            return int(connection.scalar(select(func.coalesce(func.sum(reference_failures.c.failures), 0))) or 0)


