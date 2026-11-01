"""Numbered stream events, so a dropped connection can resume from where it stopped."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(16), nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stream_events_session", "stream_events", ["session_id", "id"])


def downgrade() -> None:
    op.drop_table("stream_events")
