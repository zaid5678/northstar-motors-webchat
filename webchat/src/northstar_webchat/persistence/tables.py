"""Hybrid storage (D-003): `sessions` is current state, `events` is the append-only history."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("state", Text, nullable=False),
    Column("phase", String(32), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_sessions_phase", "phase"),
)

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("kind", String(80), nullable=False),
    Column("source", String(16), nullable=False),
    Column("phase", String(32), nullable=True),
    Column("payload", Text, nullable=False),
    UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
)

write_requests = Table(
    "write_requests",
    metadata,
    Column("idempotency_key", String(100), primary_key=True),
    Column("session_id", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("request", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

unbookable_slots = Table(
    "unbookable_slots",
    metadata,
    Column("slot_id", String(80), primary_key=True),
    Column("until", DateTime(timezone=True), nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), nullable=False),
    Column("role", String(16), nullable=False),
    Column("payload", Text, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Index("ix_messages_session", "session_id"),
)

stream_events = Table(
    "stream_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), nullable=False),
    Column("name", String(16), nullable=False),
    Column("data", Text, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Index("ix_stream_events_session", "session_id", "id"),
)

search_memory = Table(
    "search_memory",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("data", Text, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
)

reference_failures = Table(
    "reference_failures",
    metadata,
    Column("reference_hash", String(64), primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("locked_until", DateTime(timezone=True), nullable=True),
)
