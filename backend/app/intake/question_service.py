"""Persist model proposals separately from user-confirmed answers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.gemma import PROMPT_VERSION
from app.ai.questions import QuestionDraft, validate_question
from app.intake import store
from app.intake.policy import evaluate, has_answer
from app.intake.question_config import QuestionSettings
from app.intake.question_models import IntakeAnswer, IntakeQuestion
from app.intake.requests import digest, recorded, reserve
from app.intake.schemas import Answers
from app.jobs import store as jobs
from app.jobs.models import Job

POLICY_VERSION = "1"
COMPANIONS = {
    "field_code": ["custom_field", "interest_activity"],
    "learning_goal": ["goal_text"],
    "budget_status": ["budget_krw"],
}
UNKNOWN = {
    "entry_intent": {"entry_intent": "explore"},
    "field_code": {"field_code": "undecided"},
    "document_decision": {"document_decision": "none"},
    "budget_krw": {"budget_status": "unknown", "budget_krw": None},
}


class QuestionServiceError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def generation_context(db: Session, profile) -> dict:
    state = evaluate(profile.answers_json, profile.unresolved_conflicts_json)
    confirmed = {
        key: value
        for key, value in profile.answers_json.items()
        if has_answer(value) and key not in profile.unresolved_conflicts_json
    }
    unknown = [
        key
        for key, origin in profile.slot_origins_json.items()
        if origin == "explicit_unknown"
    ]
    companions = {
        target: [
            key
            for key in COMPANIONS.get(target, [])
            if key not in confirmed and key not in unknown
        ]
        for target in state["eligible_slots"]
    }
    return {
        "profile_revision": profile.revision,
        "confirmed_answers": confirmed,
        "confirmed_evidence": [],
        "eligible_slots": state["eligible_slots"],
        "allowed_companion_slots": companions,
        "resolved_unknown_slots": unknown,
        "selection_history": [
            {"target_slot": s["target_slot"], "label": s["label"]}
            for s in store.selection_summaries(db, profile)
        ],
    }


def generate(
    db: Session, profile_id: UUID, user_id: UUID, expected_revision: int, key: str
):
    profile = store.get_profile(db, profile_id, user_id, lock=True)
    request, created = reserve(
        db,
        profile=profile,
        operation="generate_question",
        key=key,
        request={"profile_id": str(profile_id), "expected_revision": expected_revision},
    )
    if not created:
        return request.status_code, request.response_json
    if expected_revision != profile.revision:
        raise QuestionServiceError(409, "revision_conflict")
    state = evaluate(profile.answers_json, profile.unresolved_conflicts_json)
    if state["next_action"] != "generate_question" or not state["eligible_slots"]:
        return recorded(request, 200, state)
    try:
        config = QuestionSettings.load()
    except ValueError:
        raise QuestionServiceError(503, "question_provider_unavailable") from None
    context = generation_context(db, profile)
    fingerprint = digest(
        {
            "context": context,
            "model_id": config.model,
            "prompt_version": PROMPT_VERSION,
            "policy_version": POLICY_VERSION,
        }
    )
    ready = db.scalar(
        select(IntakeQuestion).where(
            IntakeQuestion.profile_id == profile_id,
            IntakeQuestion.base_revision == expected_revision,
            IntakeQuestion.context_hash == fingerprint,
            IntakeQuestion.status.in_(["ready", "stale"]),
        )
    )
    if ready is not None:
        if ready.status == "stale":
            invalidate(db, profile_id)
            ready.status = "ready"
        job = jobs.get_job(db, ready.job_id, user_id)
    else:
        job = db.scalar(
            select(Job)
            .where(
                Job.profile_id == profile_id,
                Job.kind == "intake_question",
                Job.profile_revision == expected_revision,
                Job.status.in_(["queued", "running"]),
                Job.request_hash == fingerprint,
            )
            .order_by(Job.created_at)
            .limit(1)
        )
        if job is None:
            # A deliberate new configuration/context invalidates older proposals.
            invalidate(db, profile_id)
            job = jobs.enqueue(
                db,
                profile_id=profile_id,
                user_id=user_id,
                profile_revision=expected_revision,
                idempotency_key=key,
                request_hash=fingerprint,
                input_json={
                    "context": context,
                    "context_hash": fingerprint,
                    "model_id": config.model,
                    "prompt_version": PROMPT_VERSION,
                    "policy_version": POLICY_VERSION,
                },
                now=datetime.now(UTC),
            )
    return recorded(request, 202, jobs.accepted(job))


def invalidate(db: Session, profile_id: UUID) -> None:
    db.execute(
        update(IntakeQuestion)
        .where(
            IntakeQuestion.profile_id == profile_id, IntakeQuestion.status == "ready"
        )
        .values(status="stale")
    )
    db.execute(
        update(Job)
        .where(Job.profile_id == profile_id, Job.status.in_(["queued", "running"]))
        .values(
            status="superseded",
            result_json=None,
            finished_at=datetime.now(UTC),
            lease_token=None,
            lease_expires_at=None,
        )
    )


def save_question(db: Session, claim: jobs.Claim, draft: QuestionDraft) -> UUID:
    context = claim.input_json["context"]
    # Revalidate even when a provider was injected explicitly for local tests.
    draft = validate_question(
        draft.model_dump_json(),
        eligible_slots=set(context["eligible_slots"]),
        companion_slots={
            k: set(v) for k, v in context["allowed_companion_slots"].items()
        },
        confirmed_slots=set(context["confirmed_answers"]),
        resolved_unknown_slots=set(context["resolved_unknown_slots"]),
    )
    now, question_id = datetime.now(UTC), uuid4()
    options, bindings = [], {}
    for option in draft.options:
        Answers.model_validate({**context["confirmed_answers"], **option.answer_patch})
        option_id = str(uuid4())
        options.append(
            {
                "id": option_id,
                "label": option.label,
                "description": option.description,
                "kind": "generated",
            }
        )
        bindings[option_id] = {
            "patch": option.answer_patch,
            "kind": "generated",
            "label": option.label,
        }
    unknown_id = str(uuid4())
    options.append(
        {
            "id": unknown_id,
            "label": "아직 정하지 못했어요",
            "description": "미정으로 남기고 이어가기",
            "kind": "unknown",
        }
    )
    unknown_patch = UNKNOWN.get(draft.target_slot, {draft.target_slot: "unknown"})
    Answers.model_validate({**context["confirmed_answers"], **unknown_patch})
    bindings[unknown_id] = {
        "patch": unknown_patch,
        "kind": "unknown",
        "label": options[-1]["label"],
    }
    public = {
        key: value for key, value in draft.model_dump().items() if key != "options"
    }
    public.update(
        id=str(question_id),
        profile_id=str(claim.profile_id),
        base_revision=claim.profile_revision,
        context_hash=claim.input_json["context_hash"],
        status="ready",
        origin="llm",
        options=options,
        allow_custom_text=False,
        model_id=claim.input_json["model_id"],
        prompt_version=claim.input_json["prompt_version"],
        policy_version=claim.input_json["policy_version"],
        created_at=now.isoformat(),
    )
    row = IntakeQuestion(
        id=question_id,
        profile_id=claim.profile_id,
        user_id=claim.user_id,
        job_id=claim.id,
        base_revision=claim.profile_revision,
        context_hash=claim.input_json["context_hash"],
        source_hashes_json={},
        target_slot=draft.target_slot,
        question_signature=digest(
            {"question": draft.question, "target": draft.target_slot}
        ),
        status="ready",
        origin="llm",
        schema_version=draft.schema_version,
        model_id=claim.input_json["model_id"],
        prompt_version=claim.input_json["prompt_version"],
        policy_version=claim.input_json["policy_version"],
        draft_json=draft.model_dump(),
        public_question_json=public,
        option_bindings_json=bindings,
        validation_json={"schema": "passed", "semantics": "not_evaluated"},
        created_at=now,
    )
    db.add(row)
    db.flush()
    return question_id


def get_question(db: Session, question_id: UUID, user_id: UUID) -> dict:
    row = db.scalar(
        select(IntakeQuestion).where(
            IntakeQuestion.id == question_id, IntakeQuestion.user_id == user_id
        )
    )
    if row is None:
        raise QuestionServiceError(404, "question_not_found")
    return {**row.public_question_json, "status": row.status}


def answer(
    db: Session,
    profile_id: UUID,
    user_id: UUID,
    question_id: UUID,
    selected_option_id: UUID,
    expected_revision: int,
    key: str,
):
    profile = store.get_profile(db, profile_id, user_id, lock=True)
    request, created = reserve(
        db,
        profile=profile,
        operation="answer_question",
        key=key,
        request={
            "profile_id": str(profile_id),
            "question_id": str(question_id),
            "selected_option_id": str(selected_option_id),
            "expected_revision": expected_revision,
        },
    )
    if not created:
        return request.status_code, request.response_json
    question = db.scalar(
        select(IntakeQuestion)
        .where(
            IntakeQuestion.id == question_id,
            IntakeQuestion.profile_id == profile_id,
            IntakeQuestion.user_id == user_id,
        )
        .with_for_update()
    )
    if question is None:
        raise QuestionServiceError(404, "question_not_found")
    if (
        question.status != "ready"
        or question.base_revision != expected_revision
        or profile.revision != expected_revision
    ):
        raise QuestionServiceError(409, "revision_conflict")
    binding = question.option_bindings_json.get(str(selected_option_id))
    if binding is None:
        raise QuestionServiceError(422, "invalid_option")
    previous_answers, origins = (
        dict(profile.answers_json),
        dict(profile.slot_origins_json),
    )
    answers = Answers.model_validate(
        {**previous_answers, **binding["patch"]}
    ).model_dump(exclude_unset=True)
    question.status, question.answered_at = "answered", datetime.now(UTC)
    profile = store.replace_answers(db, profile_id, user_id, expected_revision, answers)
    for name, value in answers.items():
        if name in binding["patch"]:
            origins[name] = (
                "explicit_unknown"
                if binding["kind"] == "unknown"
                else "selected_option"
            )
        elif previous_answers.get(name) != value:
            origins[name] = "direct"
    profile.slot_origins_json = {
        name: origin for name, origin in origins.items() if name in answers
    }
    db.add(
        IntakeAnswer(
            question_id=question_id,
            profile_id=profile_id,
            user_id=user_id,
            selected_option_id=selected_option_id,
            applied_patch_json=binding["patch"],
            revision_before=expected_revision,
            revision_after=expected_revision + 1,
            created_at=datetime.now(UTC),
        )
    )
    db.flush()
    return recorded(request, 200, store.profile_response(profile, db))
