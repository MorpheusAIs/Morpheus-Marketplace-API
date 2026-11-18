"""
VideoJob model for asynchronous video generation jobs (/v1/videos).

One row per clip. The row is written before the hold and the session, so the
video sweeper owns any row that never reaches `queued`.

Lifecycle: pending -> queued / in_progress -> completed | failed.
Billing:   none -> held -> charged | voided.

All DateTime columns are naive UTC, like routed_sessions and credits_ledger.
video_url is a vendor bearer link: never log it or return it outside /content.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, TEXT, Numeric, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import enum

from .base import Base


class VideoJobStatus(str, enum.Enum):
    """Gateway-side video job states (stored as String)."""
    pending = "pending"
    queued = "queued"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"


class VideoBillingState(str, enum.Enum):
    """Where the clip's fixed hold stands (stored as String)."""
    none = "none"
    held = "held"
    charged = "charged"
    voided = "voided"


VIDEO_JOB_TERMINAL_STATUSES = frozenset({VideoJobStatus.completed.value, VideoJobStatus.failed.value})


class VideoJob(Base):
    """A single video clip requested through /v1/videos."""
    __tablename__ = "video_jobs"

    # "video_<uuid4 hex>"; also the credits_ledger request_id
    id = Column(TEXT, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    # Idempotency-Key header of the POST; unique per user (uq_video_jobs_user_idempotency_key)
    idempotency_key = Column(String(255), nullable=True)

    # Model and request
    model_name = Column(TEXT, nullable=False)  # Requested catalog name
    model_id = Column(String(66), nullable=False)  # Hex32 blockchain model identifier
    prompt = Column(TEXT, nullable=False)

    # Resolved clip parameters (from the quote)
    duration_seconds = Column(Integer, nullable=False)
    resolution = Column(String(16), nullable=False)
    aspect_ratio = Column(String(16), nullable=True)
    generate_audio = Column(Boolean, nullable=False, default=False, server_default="false")
    has_image = Column(Boolean, nullable=False, default=False, server_default="false")

    status = Column(String(20), nullable=False, default=VideoJobStatus.pending.value, index=True)

    # Billing
    price_usd = Column(Numeric(20, 8), nullable=False)
    ledger_entry_id = Column(UUID(as_uuid=True), nullable=False)  # Unique: uq_video_jobs_ledger_entry_id
    billing_state = Column(String(16), nullable=False, default=VideoBillingState.none.value)

    # Session (one per clip in the MVP)
    session_id = Column(TEXT, nullable=True)
    bid_id = Column(TEXT, nullable=True)
    provider = Column(TEXT, nullable=True)
    quote_seconds = Column(Integer, nullable=True)
    session_seconds = Column(Integer, nullable=True)

    # Node job
    node_job_id = Column(TEXT, nullable=True)
    node_misses = Column(Integer, nullable=False, default=0, server_default="0")

    # Result
    video_url = Column(TEXT, nullable=True)  # Vendor bearer link: never log
    expires_at = Column(DateTime, nullable=True)
    error_code = Column(TEXT, nullable=True)
    error_message = Column(TEXT, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    session_closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("ledger_entry_id", name="uq_video_jobs_ledger_entry_id"),
        # A retried POST with the same Idempotency-Key finds the first row
        Index(
            "uq_video_jobs_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Sweeper: terminal rows whose session close still has to be retried
        Index(
            "ix_video_jobs_session_close_pending",
            "id",
            postgresql_where=text("session_id IS NOT NULL AND session_closed_at IS NULL"),
        ),
    )

    @property
    def is_terminal(self) -> bool:
        """True once the clip is completed or failed."""
        return self.status in VIDEO_JOB_TERMINAL_STATUSES

    def __repr__(self) -> str:
        # video_url and prompt are left out on purpose
        return f"<VideoJob(id={self.id}, status={self.status}, billing_state={self.billing_state})>"
