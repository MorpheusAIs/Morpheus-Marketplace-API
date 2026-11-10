"""Add video_jobs table and unit pricing columns on credits_ledger

Video clips are billed per second of video by resolution, not by tokens.
The ledger gets nullable unit columns (unit_type, units, unit_price,
usage_metadata); chat and embeddings rows leave them null. The ledger entry
types usage_hold / usage_charge are reused, so the enum is untouched.

video_jobs holds one row per clip: params, price, hold state, the session
opened for it and the node job it maps to. A POST's Idempotency-Key is
unique per user, so a retried request finds the first row.

Revision ID: add_video_jobs
Revises: add_stake_daylock
Create Date: 2026-11-06 00:01:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_video_jobs"
down_revision: Union[str, None] = "add_stake_daylock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("credits_ledger", sa.Column("unit_type", sa.Text(), nullable=True))
    op.add_column("credits_ledger", sa.Column("units", sa.Numeric(20, 6), nullable=True))
    op.add_column("credits_ledger", sa.Column("unit_price", sa.Numeric(20, 8), nullable=True))
    op.add_column(
        "credits_ledger",
        sa.Column("usage_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "video_jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        # Model and request
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(66), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Resolved clip parameters
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("aspect_ratio", sa.String(16), nullable=True),
        sa.Column("generate_audio", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("has_image", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # Billing
        sa.Column("price_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("ledger_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billing_state", sa.String(16), nullable=False, server_default="none"),
        # Session
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("bid_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("quote_seconds", sa.Integer(), nullable=True),
        sa.Column("session_seconds", sa.Integer(), nullable=True),
        # Node job
        sa.Column("node_job_id", sa.Text(), nullable=True),
        sa.Column("node_misses", sa.Integer(), nullable=False, server_default="0"),
        # Result
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("session_closed_at", sa.DateTime(), nullable=True),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("ledger_entry_id", name="uq_video_jobs_ledger_entry_id"),
    )
    op.create_index("ix_video_jobs_user_id", "video_jobs", ["user_id"])
    op.create_index("ix_video_jobs_status", "video_jobs", ["status"])
    op.create_index(
        "uq_video_jobs_user_idempotency_key",
        "video_jobs",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Sweeper: terminal rows whose session close still has to be retried
    op.create_index(
        "ix_video_jobs_session_close_pending",
        "video_jobs",
        ["id"],
        postgresql_where=sa.text("session_id IS NOT NULL AND session_closed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_video_jobs_session_close_pending", table_name="video_jobs")
    op.drop_index("uq_video_jobs_user_idempotency_key", table_name="video_jobs")
    op.drop_index("ix_video_jobs_status", table_name="video_jobs")
    op.drop_index("ix_video_jobs_user_id", table_name="video_jobs")
    op.drop_table("video_jobs")

    op.drop_column("credits_ledger", "usage_metadata")
    op.drop_column("credits_ledger", "unit_price")
    op.drop_column("credits_ledger", "units")
    op.drop_column("credits_ledger", "unit_type")
