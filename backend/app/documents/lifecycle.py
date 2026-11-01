"""Document removal and dependent snapshot cleanup."""

from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select

from app.documents.models import Document, DocumentChunk
from app.documents.service import DocumentError
from app.intake import store
from app.intake.question_models import IntakeAnswer, IntakeQuestion, IntakeRequest
from app.intake.question_service import invalidate
from app.jobs.models import Job
from app.recommendations.models import RecommendationRun

IDENTIFIER_FIELDS = frozenset(
    {
        "id",
        "document_id",
        "chunk_id",
        "source_item_id",
        "source_item_ids",
        "job_id",
        "question_id",
        "active_question_id",
        "run_id",
        "recommendation_run_id",
    }
)


def contains(value, markers):
    if isinstance(value, str):
        return value in markers
    if isinstance(value, dict):
        return any(
            contains(k, markers) or contains(v, markers) for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains(v, markers) for v in value)
    return False


def references(value, identifiers):
    """Match source or persisted-record identifiers, excluding direct answers."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"answers", "confirmed_answers"}:
                continue
            if key in IDENTIFIER_FIELDS and contains(item, identifiers):
                return True
            if references(item, identifiers):
                return True
    elif isinstance(value, (list, tuple)):
        return any(references(item, identifiers) for item in value)
    return False


def linked_quotes(value, identifiers):
    quotes = set()
    if isinstance(value, dict):
        if references(
            {
                k: v
                for k, v in value.items()
                if not isinstance(v, dict)
                and (
                    not isinstance(v, list) or all(isinstance(item, str) for item in v)
                )
            },
            identifiers,
        ):
            for key in ("quote", "evidence_quote", "text_content", "text", "label"):
                if isinstance(value.get(key), str):
                    quotes.add(value[key])
        for key, item in value.items():
            if key not in {"answers", "confirmed_answers"}:
                quotes.update(linked_quotes(item, identifiers))
    elif isinstance(value, list):
        for item in value:
            quotes.update(linked_quotes(item, identifiers))
    return quotes


def scrub(value, identifiers, quotes, *, source_record=False):
    if isinstance(value, str):
        if value in identifiers or any(quote and quote in value for quote in quotes):
            return None
        return value
    if isinstance(value, list):
        return [
            clean
            for item in value
            if (clean := scrub(item, identifiers, quotes, source_record=source_record))
            is not None
        ]
    if isinstance(value, dict):
        source_ids = [
            value[key]
            for key in ("document_id", "chunk_id", "source_item_id")
            if isinstance(value.get(key), str) and value[key]
        ]
        if isinstance(value.get("source_item_ids"), list):
            source_ids.extend(
                item
                for item in value["source_item_ids"]
                if isinstance(item, str) and item
            )
        if source_record and isinstance(value.get("id"), str) and value["id"]:
            source_ids.append(value["id"])
        if source_ids and not references(value, identifiers):
            return deepcopy(value)
        if any(
            isinstance(v, str) and v in identifiers
            for k, v in value.items()
            if k in {"id", "document_id", "chunk_id", "source_item_id"}
        ):
            return None
        return {
            k: clean
            for k, v in value.items()
            if k not in identifiers
            and (
                clean := (
                    deepcopy(v)
                    if k in {"answers", "confirmed_answers"}
                    else scrub(
                        v,
                        identifiers,
                        set() if k in IDENTIFIER_FIELDS else quotes,
                        source_record=k == "sources",
                    )
                )
            )
            is not None
        }
    return value


def remove_document(db, document_id, user_id, expected_revision):
    initial = db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if initial is None:
        raise DocumentError(404, "document_not_found")
    profile = store.get_profile(db, initial.profile_id, user_id, lock=True)
    row = db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise DocumentError(404, "document_not_found")
    if profile.revision != expected_revision:
        raise DocumentError(409, "revision_conflict")
    redact_document(db, profile, row)
    db.delete(row)
    profile.revision += 1
    profile.status, profile.submitted_revision, profile.updated_at = (
        "draft",
        None,
        datetime.now(UTC),
    )
    invalidate(db, profile.id)


def fields_as_list(fields):
    return list(fields)


def redact_document(db, profile, row):
    chunks = list(
        db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == row.id))
    )
    document_ids = {str(row.id), *(str(chunk.id) for chunk in chunks)}
    markers = set(document_ids)
    affected = [
        item for item in profile.reviewed_items_json if references(item, document_ids)
    ]
    markers.update(str(item["id"]) for item in affected if item.get("id"))
    quotes = {
        item[key]
        for item in affected
        for key in ("evidence_quote", "label")
        if isinstance(item.get(key), str) and item[key]
    }
    profile.reviewed_items_json = [
        item for item in profile.reviewed_items_json if item not in affected
    ]
    questions = list(
        db.scalars(
            select(IntakeQuestion)
            .where(IntakeQuestion.profile_id == profile.id)
            .order_by(IntakeQuestion.id)
            .with_for_update()
        )
    )
    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.profile_id == profile.id)
            .order_by(Job.id)
            .with_for_update()
        )
    )
    runs = list(
        db.scalars(
            select(RecommendationRun)
            .where(RecommendationRun.profile_id == profile.id)
            .order_by(RecommendationRun.id)
            .with_for_update()
        )
    )
    requests = list(
        db.scalars(
            select(IntakeRequest)
            .where(IntakeRequest.profile_id == profile.id)
            .order_by(IntakeRequest.operation, IntakeRequest.idempotency_key)
            .with_for_update()
        )
    )

    def question_values(q):
        return [
            q.source_hashes_json,
            q.draft_json,
            q.public_question_json,
            q.option_bindings_json,
            q.validation_json,
        ]

    def run_values(r):
        return [r.input_json, r.public_json, r.provenance_json]

    linked_jobs = set()
    while True:
        record_ids = (
            {str(job_id) for job_id in linked_jobs}
            | {str(q.id) for q in questions if q.job_id in linked_jobs}
            | {str(r.id) for r in runs if r.job_id in linked_jobs}
        )
        lineage = markers | record_ids
        found = {
            job.id
            for job in jobs
            if references([job.input_json, job.result_json], lineage)
        }
        found.update(
            q.job_id for q in questions if references(question_values(q), lineage)
        )
        found.update(r.job_id for r in runs if references(run_values(r), lineage))
        if found <= linked_jobs:
            break
        linked_jobs.update(found)
    job_quotes = {job_id: set(quotes) for job_id in linked_jobs}
    for job in jobs:
        if job.id in linked_jobs:
            job_quotes[job.id].update(
                linked_quotes([job.input_json, job.result_json], lineage)
            )
    for question in questions:
        if question.job_id in linked_jobs:
            job_quotes[question.job_id].update(
                linked_quotes(question_values(question), lineage)
            )
    for run in runs:
        if run.job_id in linked_jobs:
            job_quotes[run.job_id].update(linked_quotes(run_values(run), lineage))
    for question in questions:
        if question.job_id in linked_jobs:
            question.source_hashes_json = {}
            question.draft_json = {}
            question.public_question_json = {"id": str(question.id), "status": "stale"}
            question.option_bindings_json = {}
            question.validation_json = {}
            question.status = "stale"
            answer = db.scalar(
                select(IntakeAnswer)
                .where(IntakeAnswer.question_id == question.id)
                .with_for_update()
            )
            if answer and contains(answer.applied_patch_json, markers):
                answer.applied_patch_json = {}
    for job in jobs:
        if job.id not in linked_jobs:
            continue
        clean_input = scrub(job.input_json, lineage, job_quotes[job.id]) or {}
        for key in ("answers", "confirmed_answers"):
            if key in job.input_json:
                clean_input[key] = job.input_json[key]
        job.input_json = clean_input
        job.result_json = None
    for run in runs:
        if run.job_id not in linked_jobs:
            continue
        clean_input = scrub(run.input_json, lineage, job_quotes[run.job_id]) or {}
        for key in ("answers", "confirmed_answers"):
            if key in run.input_json:
                clean_input[key] = run.input_json[key]
        run.input_json = clean_input
        run.provenance_json = (
            scrub(run.provenance_json, lineage, job_quotes[run.job_id]) or {}
        )
        result = deepcopy(run.public_json)
        result["needs"] = []
        result["notices"] = []
        run.provenance_json = {}
        for item in result.get("items", []):
            for reason in item.get("reasons", []):
                reason["text"] = (
                    "삭제된 자료를 반영한 설명입니다. 다시 추천을 생성해주세요."
                )
        result.setdefault("notices", []).append(
            "자료가 삭제되어 결과를 다시 생성해야 합니다."
        )
        run.public_json = result
    for request in requests:
        if references(request.response_json, markers | record_ids):
            request.response_json = {"source_deleted": True}
