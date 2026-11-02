"""The last search and the cars it showed, so "the second one" and "make that diesel" work."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_memory",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("search_memory")
