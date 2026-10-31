from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.intake.models import IntakeProfile
from app.intake.policy import evaluate


class ProfileNotFound(Exception):
    pass


class RevisionConflict(Exception):
    pass


def get_profile(db: Session, profile_id: UUID, user_id: UUID) -> IntakeProfile:
    row = db.scalar(
        select(IntakeProfile).where(
            IntakeProfile.id == profile_id, IntakeProfile.user_id == user_id
        )
    )
    if row is None:
        raise ProfileNotFound()
    return row


def create_profile(db: Session, user_id: UUID) -> IntakeProfile:
    now = datetime.now(UTC)
    row = IntakeProfile(user_id=user_id, created_at=now, updated_at=now)
    db.add(row)
    db.flush()
    return row


def replace_answers(
    db: Session,
    profile_id: UUID,
    user_id: UUID,
    expected_revision: int,
    answers: dict[str, Any],
) -> IntakeProfile:
    current = get_profile(db, profile_id, user_id)
    if current.revision != expected_revision:
        raise RevisionConflict()
    old = current.answers_json
    conflicts = [
        key
        for key in current.unresolved_conflicts_json
        if key in answers
        and answers[key] == old.get(key)
        and answers[key] not in (None, "")
    ]
    if old.get("field_code") is not None and old.get("field_code") != answers.get(
        "field_code"
    ):
        for key in ("experience", "previous_gap", "learning_goal", "goal_text"):
            if (
                key in old
                and old[key] not in (None, "")
                and answers.get(key) == old[key]
                and key not in conflicts
            ):
                conflicts.append(key)
    state = evaluate(answers, conflicts)
    origins = {
        key: "explicit_unknown" if value in ("unknown", "undecided") else "direct"
        for key, value in answers.items()
        if value is not None
    }
    statement = (
        update(IntakeProfile)
        .where(
            IntakeProfile.id == profile_id,
            IntakeProfile.user_id == user_id,
            IntakeProfile.revision == expected_revision,
        )
        .values(
            answers_json=answers,
            revision=expected_revision + 1,
            status="draft",
            completed_sections_json=state["completed_sections"],
            slot_origins_json=origins,
            unresolved_conflicts_json=conflicts,
            updated_at=datetime.now(UTC),
            submitted_revision=None,
        )
        .returning(IntakeProfile)
    )
    updated = db.scalars(
        statement.execution_options(populate_existing=True)
    ).one_or_none()
    if updated is None:
        raise RevisionConflict()
    return updated


def profile_response(row: IntakeProfile) -> dict[str, Any]:
    state = evaluate(row.answers_json, row.unresolved_conflicts_json)
    return {
        "id": str(row.id),
        "revision": row.revision,
        "status": row.status,
        "answers": row.answers_json,
        "reviewed_items": row.reviewed_items_json,
        "documents": [],
        "completed_sections": state["completed_sections"],
        "review_token": None,
        "updated_at": row.updated_at.isoformat(),
        "slot_origins": row.slot_origins_json,
        "unresolved_conflicts": row.unresolved_conflicts_json,
        "latest_interpretation_id": None,
        "selection_summaries": [],
        "intake_state": state,
    }
