"""Sessions, the event log, stored write requests, and the two safety tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("phase", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_phase", "sessions", ["phase"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
    )
    op.create_table(
        "write_requests",
        sa.Column("idempotency_key", sa.String(100), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("request", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "unbookable_slots",
        sa.Column("slot_id", sa.String(80), primary_key=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "reference_failures",
        sa.Column("reference_hash", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for table in (
        "reference_failures",
        "unbookable_slots",
        "write_requests",
        "events",
        "sessions",
    ):
        op.drop_table(table)
