"""add pending_bookings.config_token and address

Revision ID: a41f6c2d9e57
Revises: 7c2e91d4a0b3
Create Date: 2026-09-27 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a41f6c2d9e57"
down_revision: str | Sequence[str] | None = "7c2e91d4a0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable: stage 9 rows don't have them (they can't refresh an expired book token).
    op.add_column("pending_bookings", sa.Column("config_token", sa.String(), nullable=True))
    op.add_column("pending_bookings", sa.Column("address", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("pending_bookings", "address")
    op.drop_column("pending_bookings", "config_token")
