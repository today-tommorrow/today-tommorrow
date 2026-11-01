from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.intake.question_models import Base


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "profile_id", "user_id"],
            [
                "today_app.jobs.id",
                "today_app.jobs.profile_id",
                "today_app.jobs.user_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("profile_revision >= 0", name="recommendation_revision"),
        Index("ix_recommendation_runs_owner", "user_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    profile_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    public_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
