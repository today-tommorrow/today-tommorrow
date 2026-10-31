from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.intake.models import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("kind = 'intake_question'", name="job_kind"),
        CheckConstraint("document_id IS NULL", name="job_document"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','superseded','cancelled')",
            name="job_status",
        ),
        CheckConstraint("attempt >= 0", name="job_attempt"),
        CheckConstraint("profile_revision >= 0", name="job_profile_revision"),
        ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "profile_id", "user_id"),
        UniqueConstraint("user_id", "kind", "idempotency_key"),
        Index("ix_today_app_jobs_owner", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID | None]
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    phase: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
