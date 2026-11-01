"""Leased, source-fenced document analysis worker."""

import argparse
import os
import time
from datetime import UTC, datetime
from threading import Event, Thread
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.ai.document_analysis import (
    DocumentAnalysisError,
    DocumentChunkInput,
    GemmaDocumentAnalyzer,
)
from app.db import SessionLocal
from app.documents.models import Document, DocumentChunk
from app.intake import store
from app.jobs import store as jobs
from app.jobs.execution import execution_slot


def utcnow():
    return datetime.now(UTC)


class LostLease(Exception):
    pass


def run_once(factory, provider, *, lease_seconds=120, max_attempts=3):
    with execution_slot(factory) as acquired:
        if not acquired:
            return False
        return _run_once(
            factory, provider, lease_seconds=lease_seconds, max_attempts=max_attempts
        )


def _run_once(factory, provider, *, lease_seconds, max_attempts):
    with factory() as db, db.begin():
        claim = jobs.claim(
            db,
            now=utcnow(),
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            clock=utcnow,
            kind="document_analysis",
        )
    if claim is None:
        reconcile_failed_jobs(factory)
        return False
    stop, cancelled = Event(), Event()

    def heartbeat():
        while not stop.wait(max(0.25, lease_seconds / 3)):
            try:
                with factory() as db, db.begin():
                    if not jobs.heartbeat(
                        db,
                        claim,
                        now=utcnow(),
                        lease_seconds=lease_seconds,
                        clock=utcnow,
                    ):
                        cancelled.set()
                        return
            except SQLAlchemyError:
                cancelled.set()
                return

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        document_id = UUID(claim.input_json["document_id"])
        with factory() as db, db.begin():
            profile = store.get_profile(db, claim.profile_id, claim.user_id, lock=True)
            row = db.scalar(
                select(Document)
                .where(
                    Document.id == document_id,
                    Document.profile_id == profile.id,
                    Document.user_id == claim.user_id,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.job_id != claim.id
                or jobs.owns(db, claim, now=utcnow(), clock=utcnow) is None
            ):
                raise LostLease()
            row.status = "processing"
            chunks = list(
                db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == row.id)
                    .order_by(DocumentChunk.ordinal)
                )
            )
            source = [
                (str(c.id), c.text_content, c.content_sha256, c.locator) for c in chunks
            ]
            source_hash, kind = row.content_sha256, row.kind
        inputs = tuple(
            DocumentChunkInput(chunk_id=id_, text=text)
            for id_, text, _, _ in source
            if text.strip()
        )
        if not inputs:
            raise ValueError("empty_document")
        analysis = provider.analyze(str(document_id), kind, inputs, cancelled=cancelled)
        if (
            cancelled.is_set()
            or tuple(c.chunk_id for c in inputs) != analysis.processed_chunk_ids
        ):
            raise LostLease()
        stop.set()
        thread.join(timeout=5)
        with factory() as db, db.begin():
            profile = store.get_profile(db, claim.profile_id, claim.user_id, lock=True)
            row = db.scalar(
                select(Document)
                .where(
                    Document.id == document_id,
                    Document.profile_id == profile.id,
                    Document.user_id == claim.user_id,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.job_id != claim.id
                or jobs.owns(db, claim, now=utcnow(), clock=utcnow) is None
            ):
                raise LostLease()
            current = list(
                db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == row.id)
                    .order_by(DocumentChunk.ordinal)
                )
            )
            if (
                row.content_sha256 != source_hash
                or [
                    (str(c.id), c.text_content, c.content_sha256, c.locator)
                    for c in current
                ]
                != source
            ):
                raise ValueError("source_changed")
            lookup = {
                id_: (text, hash_, locator) for id_, text, hash_, locator in source
            }
            items = []
            for proposal in analysis.proposals:
                chunk = lookup.get(proposal.chunk_id)
                if (
                    proposal.document_id != str(row.id)
                    or proposal.document_kind != row.kind
                    or chunk is None
                    or not proposal.evidence_quote
                    or proposal.evidence_quote not in chunk[0]
                    or not proposal.label
                    or proposal.label not in proposal.evidence_quote
                    or proposal.suggested_state
                    not in {"have", "want_to_learn", "unknown"}
                ):
                    raise ValueError("invalid_proposal")
                items.append(
                    {
                        "id": str(uuid4()),
                        "origin": "document",
                        "label": proposal.label,
                        "state": "unknown",
                        "suggested_state": proposal.suggested_state,
                        "document_id": str(row.id),
                        "chunk_id": proposal.chunk_id,
                        "evidence_quote": proposal.evidence_quote,
                        "source_locator": chunk[2],
                        "content_sha256": chunk[1],
                    }
                )
            profile.reviewed_items_json = [
                item
                for item in profile.reviewed_items_json
                if item.get("document_id") != str(row.id)
            ] + items
            row.status, row.error_code, row.review_confirmed = "ready", None, False
            profile.revision += 1
            profile.status, profile.submitted_revision, profile.updated_at = (
                "draft",
                None,
                utcnow(),
            )
            from app.intake.question_service import invalidate

            invalidate(db, profile.id)
            if not jobs.complete(
                db,
                claim,
                now=utcnow(),
                result_json={"document_id": str(row.id)},
                clock=utcnow,
            ):
                raise LostLease()
    except LostLease:
        pass
    except Exception as error:
        category = (
            "document_chunk_limit"
            if isinstance(error, DocumentAnalysisError)
            and error.args == ("document_chunk_limit",)
            else "analysis_failed"
        )
        try:
            with factory() as db, db.begin():
                profile = store.get_profile(
                    db, claim.profile_id, claim.user_id, lock=True
                )
                row = db.scalar(
                    select(Document)
                    .where(
                        Document.id == UUID(claim.input_json["document_id"]),
                        Document.profile_id == profile.id,
                    )
                    .with_for_update()
                )
                if (
                    row is not None
                    and row.job_id == claim.id
                    and jobs.fail(
                        db, claim, now=utcnow(), code="job_failed", clock=utcnow
                    )
                ):
                    row.status, row.error_code, row.review_confirmed = (
                        "failed",
                        category,
                        False,
                    )
        except SQLAlchemyError, store.ProfileNotFound:
            pass
    finally:
        stop.set()
        thread.join(timeout=5)
    return True


def reconcile_failed_jobs(factory):
    """Repair documents whose lease exhausted after a worker crashed."""
    from app.jobs.models import Job

    with factory() as db:
        candidates = list(
            db.execute(
                select(Job.profile_id, Job.user_id, Job.document_id, Job.id).where(
                    Job.kind == "document_analysis",
                    Job.status == "failed",
                    Job.error_code == "lease_exhausted",
                )
            )
        )
    for profile_id, user_id, document_id, job_id in candidates:
        try:
            with factory() as db, db.begin():
                profile = store.get_profile(db, profile_id, user_id, lock=True)
                row = db.scalar(
                    select(Document)
                    .where(
                        Document.id == document_id, Document.profile_id == profile.id
                    )
                    .with_for_update()
                )
                job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if (
                    row is not None
                    and row.job_id == job_id
                    and job is not None
                    and job.status == "failed"
                    and row.status in {"queued", "processing"}
                ):
                    row.status, row.error_code, row.review_confirmed = (
                        "failed",
                        "analysis_failed",
                        False,
                    )
        except store.ProfileNotFound:
            continue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        provider = GemmaDocumentAnalyzer(
            os.environ.get("RECOMMENDATION_MODEL_BASE_URL", ""),
            os.environ.get("RECOMMENDATION_MODEL_ID", ""),
            timeout=float(os.environ.get("RECOMMENDATION_MODEL_TIMEOUT_SECONDS", "60")),
            max_tokens=int(os.environ.get("RECOMMENDATION_MODEL_MAX_TOKENS", "2048")),
            max_response_bytes=int(
                os.environ.get("RECOMMENDATION_MODEL_MAX_RESPONSE_BYTES", "131072")
            ),
        )
        lease = int(os.environ.get("WORKER_LEASE_SECONDS", "120"))
        attempts = int(os.environ.get("WORKER_MAX_ATTEMPTS", "3"))
        poll = float(os.environ.get("WORKER_POLL_SECONDS", "1"))
        if not (5 <= lease <= 3600 and 1 <= attempts <= 10 and 0.1 <= poll <= 60):
            raise ValueError()
    except ValueError, TypeError:
        print("문서 분석 모델과 Worker 설정을 확인해주세요.")
        return 1
    while True:
        worked = run_once(
            SessionLocal, provider, lease_seconds=lease, max_attempts=attempts
        )
        if args.once:
            return 0
        if not worked:
            time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
