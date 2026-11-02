"""Sessions whose unfinished form is hidden because the customer asked something else (D-034)."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hidden_forms",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hidden_forms")
