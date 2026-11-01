from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.intake.models import IntakeProfile
from app.intake.policy import active_conflicts, evaluate


class ProfileNotFound(Exception):
    pass


class RevisionConflict(Exception):
    pass


class DocumentsPresent(Exception):
    pass


def get_profile(
    db: Session, profile_id: UUID, user_id: UUID, *, lock: bool = False
) -> IntakeProfile:
    statement = select(IntakeProfile).where(
        IntakeProfile.id == profile_id, IntakeProfile.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    row = db.scalar(statement)
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
    current = get_profile(db, profile_id, user_id, lock=True)
    if current.revision != expected_revision:
        raise RevisionConflict()
    from app.documents.service import list_documents

    if answers.get("document_decision") == "none" and list_documents(
        db, profile_id, user_id
    ):
        raise DocumentsPresent()
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
    from app.intake.question_service import invalidate

    invalidate(db, profile_id)
    return updated


def selection_summaries(db: Session, row: IntakeProfile) -> list[dict[str, Any]]:
    from app.intake.question_models import IntakeAnswer, IntakeQuestion

    history = db.execute(
        select(IntakeAnswer, IntakeQuestion)
        .join(IntakeQuestion, IntakeQuestion.id == IntakeAnswer.question_id)
        .where(IntakeAnswer.profile_id == row.id, IntakeAnswer.user_id == row.user_id)
        .order_by(IntakeAnswer.revision_after)
    ).all()
    latest = {question.target_slot: (answer, question) for answer, question in history}
    summaries = []
    for answer, question in latest.values():
        if question.target_slot in row.unresolved_conflicts_json or any(
            key not in row.answers_json or row.answers_json[key] != value
            for key, value in answer.applied_patch_json.items()
        ):
            continue
        binding = question.option_bindings_json.get(str(answer.selected_option_id))
        if not isinstance(binding, dict) or not isinstance(binding.get("label"), str):
            continue
        summaries.append(
            {
                "question_id": str(question.id),
                "selected_option_id": str(answer.selected_option_id),
                "target_slot": question.target_slot,
                "label": binding["label"],
                "revision": answer.revision_after,
            }
        )
    return summaries


def intake_state(db: Session, profile: IntakeProfile) -> dict[str, Any]:
    from app.documents.service import list_documents

    documents = list_documents(db, profile.id, profile.user_id)
    ready = bool(documents) and all(
        d["status"] == "ready" and d["review_confirmed"] for d in documents
    )
    return evaluate(profile.answers_json, profile.unresolved_conflicts_json, ready)


def profile_response(row: IntakeProfile, db: Session | None = None) -> dict[str, Any]:
    state = evaluate(row.answers_json, row.unresolved_conflicts_json)
    summaries = []
    documents = []
    if db is not None:
        from app.documents.service import list_documents

        documents = list_documents(db, row.id, row.user_id)
        docs_ready = bool(documents) and all(
            d["status"] == "ready" and d["review_confirmed"] for d in documents
        )
        state = evaluate(row.answers_json, row.unresolved_conflicts_json, docs_ready)
        from app.intake.question_models import IntakeQuestion

        ready = db.scalar(
            select(IntakeQuestion.id).where(
                IntakeQuestion.profile_id == row.id,
                IntakeQuestion.base_revision == row.revision,
                IntakeQuestion.status == "ready",
            )
        )
        state["active_question_id"] = str(ready) if ready else None
        summaries = selection_summaries(db, row)
    from app.recommendations.review import review_token

    return {
        "id": str(row.id),
        "revision": row.revision,
        "status": row.status,
        "answers": row.answers_json,
        "reviewed_items": row.reviewed_items_json,
        "documents": documents,
        "completed_sections": state["completed_sections"],
        "review_token": review_token(row, summaries, documents)
        if db is not None
        else None,
        "updated_at": row.updated_at.isoformat(),
        "slot_origins": row.slot_origins_json,
        "unresolved_conflicts": active_conflicts(
            row.answers_json, row.unresolved_conflicts_json
        ),
        "latest_interpretation_id": None,
        "selection_summaries": summaries,
        "intake_state": state,
    }
