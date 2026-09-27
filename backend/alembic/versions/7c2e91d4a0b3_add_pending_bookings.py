"""add pending_bookings

Revision ID: 7c2e91d4a0b3
Revises: 46bfb3b189aa
Create Date: 2026-09-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c2e91d4a0b3"
down_revision: str | Sequence[str] | None = "46bfb3b189aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("pending", "confirming", "confirmed", "declined", "expired", "failed", "unknown")


def upgrade() -> None:
    """Upgrade schema."""
    tz = sa.DateTime(timezone=True)
    op.create_table(
        "pending_bookings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.Integer(), nullable=False),
        sa.Column("venue_name", sa.String(), nullable=False),
        sa.Column("slot_start", tz, nullable=False),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("seating_type", sa.String(), nullable=True),
        sa.Column("book_token", sa.String(), nullable=False),
        sa.Column("book_token_expires", tz, nullable=True),
        sa.Column("cancellation_policy", sa.String(), nullable=True),
        sa.Column("refund_cutoff", tz, nullable=True),
        sa.Column("change_cutoff", tz, nullable=True),
        sa.Column("payment_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", tz, nullable=False),
        sa.Column("reservation_id", sa.BigInteger(), nullable=True),
        sa.Column("resy_token", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", tz, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", tz, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_pending_bookings_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pending_bookings_session_id"), "pending_bookings", ["session_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_pending_bookings_session_id"), table_name="pending_bookings")
    op.drop_table("pending_bookings")
