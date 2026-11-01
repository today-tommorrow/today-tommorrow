from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.intake.question_models import Base


class SavedCourse(Base):
    __tablename__ = "saved_courses"
    __table_args__ = (
        UniqueConstraint("user_id", "source_course_id", name="uq_saved_source_owner"),
        Index("ix_saved_courses_owner_order", "user_id", "created_at", "course_id"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("today_app.users.id", ondelete="CASCADE"), primary_key=True)
    course_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_course_id: Mapped[str] = mapped_column(Text, nullable=False)
    saved_catalog_version_id: Mapped[UUID] = mapped_column(nullable=False)
    saved_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
