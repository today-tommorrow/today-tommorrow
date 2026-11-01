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


def contains_quote(value, quotes):
    if isinstance(value, str):
        return any(
            quote and (value == quote or (len(quote) > 3 and quote in value))
            for quote in quotes
        )
    if isinstance(value, dict):
        return any(contains_quote(v, quotes) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_quote(v, quotes) for v in value)
    return False


def linked_quotes(value, identifiers):
    quotes = set()
    if isinstance(value, dict):
        if any(isinstance(v, str) and v in identifiers for v in value.values()):
            for key in ("quote", "evidence_quote", "text_content", "text", "label"):
                if isinstance(value.get(key), str):
                    quotes.add(value[key])
        for item in value.values():
            quotes.update(linked_quotes(item, identifiers))
    elif isinstance(value, list):
        for item in value:
            quotes.update(linked_quotes(item, identifiers))
    return quotes


def scrub(value, identifiers, quotes):
    if isinstance(value, str):
        if value in identifiers or any(quote and quote in value for quote in quotes):
            return None
        return value
    if isinstance(value, list):
        return [
            clean
            for item in value
            if (clean := scrub(item, identifiers, quotes)) is not None
        ]
    if isinstance(value, dict):
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
                    else scrub(v, identifiers, quotes)
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
    hashes = {row.content_sha256, *(chunk.content_sha256 for chunk in chunks)}
    markers = set(document_ids)
    affected = [
        item for item in profile.reviewed_items_json if contains(item, document_ids)
    ]
    markers.update(str(item.get("id", "")) for item in affected)
    quotes = {
        str(item.get(key, ""))
        for item in affected
        for key in ("evidence_quote", "label")
    }
    profile.reviewed_items_json = [
        item for item in profile.reviewed_items_json if item not in affected
    ]
    # Collect historical quotes before modifying any snapshot so later rows cannot
    # leave an earlier copied excerpt behind.
    snapshots = []
    for question in db.scalars(
        select(IntakeQuestion).where(IntakeQuestion.profile_id == profile.id)
    ):
        snapshots.extend(
            [
                question.source_hashes_json,
                question.draft_json,
                question.public_question_json,
                question.option_bindings_json,
                question.validation_json,
            ]
        )
    for job in db.scalars(select(Job).where(Job.profile_id == profile.id)):
        snapshots.extend([job.input_json, job.result_json])
    for run in db.scalars(
        select(RecommendationRun).where(RecommendationRun.profile_id == profile.id)
    ):
        snapshots.extend([run.input_json, run.public_json, run.provenance_json])
    for request in db.scalars(
        select(IntakeRequest).where(IntakeRequest.profile_id == profile.id)
    ):
        snapshots.append(request.response_json)
    for snapshot in snapshots:
        quotes.update(linked_quotes(snapshot, markers))
    quotes.discard("")
    for question in db.scalars(
        select(IntakeQuestion)
        .where(IntakeQuestion.profile_id == profile.id)
        .order_by(IntakeQuestion.id)
        .with_for_update()
    ):
        fields = (
            question.source_hashes_json,
            question.draft_json,
            question.public_question_json,
            question.option_bindings_json,
            question.validation_json,
        )
        if (
            contains(question.source_hashes_json, hashes)
            or contains(fields_as_list(fields), markers)
            or contains_quote(fields_as_list(fields), quotes)
        ):
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
    for job in db.scalars(
        select(Job)
        .where(Job.profile_id == profile.id)
        .order_by(Job.id)
        .with_for_update()
    ):
        linked = contains([job.input_json, job.result_json], markers)
        clean_input = scrub(job.input_json, markers, quotes) or {}
        for key in ("answers", "confirmed_answers"):
            if key in job.input_json:
                clean_input[key] = job.input_json[key]
        job.input_json = clean_input
        if linked or contains_quote(job.result_json, quotes):
            job.result_json = None
    for run in db.scalars(
        select(RecommendationRun)
        .where(RecommendationRun.profile_id == profile.id)
        .order_by(RecommendationRun.id)
        .with_for_update()
    ):
        values = [run.input_json, run.public_json, run.provenance_json]
        affected_run = contains(values, markers) or contains_quote(values, quotes)
        clean_input = scrub(run.input_json, markers, quotes) or {}
        for key in ("answers", "confirmed_answers"):
            if key in run.input_json:
                clean_input[key] = run.input_json[key]
        run.input_json = clean_input
        run.provenance_json = scrub(run.provenance_json, markers, quotes) or {}
        result = deepcopy(run.public_json)
        if affected_run:
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
    for request in db.scalars(
        select(IntakeRequest)
        .where(IntakeRequest.profile_id == profile.id)
        .order_by(IntakeRequest.operation, IntakeRequest.idempotency_key)
        .with_for_update()
    ):
        if contains(request.response_json, markers) or contains_quote(
            request.response_json, quotes
        ):
            request.response_json = {"source_deleted": True}
