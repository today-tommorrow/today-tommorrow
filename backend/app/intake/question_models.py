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

from app.jobs.models import Base


class IntakeQuestion(Base):
    __tablename__ = "intake_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["job_id", "profile_id", "user_id"],
            [
                "today_app.jobs.id",
                "today_app.jobs.profile_id",
                "today_app.jobs.user_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "profile_id", "user_id"),
        UniqueConstraint("profile_id", "base_revision", "context_hash"),
        CheckConstraint("base_revision >= 0", name="question_revision"),
        CheckConstraint(
            "status IN ('ready','answered','stale')", name="question_status"
        ),
        CheckConstraint(
            "origin IN ('llm','fallback') AND (origin='fallback' OR model_id IS NOT NULL)",
            name="question_origin",
        ),
        Index("ix_questions_owner", "profile_id", "user_id", "base_revision"),
        Index(
            "one_ready_intake_question",
            "profile_id",
            unique=True,
            postgresql_where=text("status='ready'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_hashes_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_slot: Mapped[str] = mapped_column(Text, nullable=False)
    question_signature: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    public_question_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    option_bindings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntakeAnswer(Base):
    __tablename__ = "intake_answers"
    __table_args__ = (
        Index(
            "ix_intake_answers_owner_revision",
            "profile_id",
            "user_id",
            "revision_after",
        ),
        ForeignKeyConstraint(
            ["question_id", "profile_id", "user_id"],
            [
                "today_app.intake_questions.id",
                "today_app.intake_questions.profile_id",
                "today_app.intake_questions.user_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "revision_before >= 0 AND revision_after=revision_before+1",
            name="answer_revision",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    question_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    selected_option_id: Mapped[UUID] = mapped_column(nullable=False)
    applied_patch_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    revision_before: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class IntakeRequest(Base):
    __tablename__ = "intake_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("status_code IN (0,200,202)", name="intake_request_status"),
    )
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
