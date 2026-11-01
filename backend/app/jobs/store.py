"""Transactional queue primitives; callers own every commit and rollback."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.jobs.models import Job

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "superseded", "cancelled"})
ERROR_CODES = frozenset(
    {
        "job_failed",
        "lease_exhausted",
        "model_unavailable",
        "model_failed",
        "model_timeout",
        "invalid_model_output",
        "question_generation_failed",
    }
)


class JobConflict(Exception):
    pass


class JobNotFound(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    id: UUID
    profile_id: UUID
    user_id: UUID
    profile_revision: int
    input_json: dict[str, Any]
    lease_token: UUID
    attempt: int


def _time(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware time required")


def _current_time(now: datetime, clock: Callable[[], datetime] | None) -> datetime:
    current = now if clock is None else clock()
    _time(current)
    return current


def _unexpired(row: Job, now: datetime) -> bool:
    return row.lease_expires_at is not None and row.lease_expires_at > now


def _lease(now: datetime, lease_seconds: int) -> datetime:
    _time(now)
    if type(lease_seconds) is not int or lease_seconds <= 0:
        raise ValueError("positive integer lease duration required")
    return now + timedelta(seconds=lease_seconds)


def enqueue(
    db: Session,
    *,
    profile_id: UUID,
    user_id: UUID,
    profile_revision: int,
    idempotency_key: str,
    request_hash: str,
    input_json: dict[str, Any],
    now: datetime,
) -> Job:
    _time(now)
    if (
        not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 128
        or not idempotency_key.isascii()
    ):
        raise ValueError("idempotency key must contain 1 to 128 ASCII characters")
    if type(profile_revision) is not int or profile_revision < 0:
        raise ValueError("nonnegative integer profile revision required")
    db.execute(
        insert(Job)
        .values(
            profile_id=profile_id,
            user_id=user_id,
            profile_revision=profile_revision,
            kind="intake_question",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_json=deepcopy(input_json),
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "kind", "idempotency_key"])
    )
    row = db.scalars(
        select(Job).where(
            Job.user_id == user_id,
            Job.kind == "intake_question",
            Job.idempotency_key == idempotency_key,
        )
    ).one()
    if row.request_hash != request_hash:
        raise JobConflict
    return row


def get_job(db: Session, job_id: UUID, user_id: UUID) -> Job:
    row = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    if row is None:
        raise JobNotFound
    return row


def accepted(job: Job) -> dict[str, Any]:
    return {"job_id": str(job.id), "status": job.status, "poll_after_ms": 1000}


def public_job(job: Job) -> dict[str, Any]:
    result = None
    if job.status == "succeeded" and job.result_json:
        value = job.result_json.get("question_id")
        if isinstance(value, str):
            try:
                result = {"question_id": str(UUID(value))}
            except ValueError:
                pass
    error = None
    if job.status == "failed":
        error = {
            "code": job.error_code if job.error_code in ERROR_CODES else "job_failed",
            "message": "작업을 완료하지 못했습니다. 다시 시도해주세요.",
            "request_id": str(job.id),
        }
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "phase": job.phase,
        "profile_id": str(job.profile_id),
        "result": result,
        "error": error,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "completed_stages": ["generating_question"]
        if job.status == "succeeded"
        else [],
    }


def claim(
    db: Session,
    *,
    now: datetime,
    lease_seconds: int,
    max_attempts: int = 3,
    clock: Callable[[], datetime] | None = None,
) -> Claim | None:
    _lease(now, lease_seconds)
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("positive integer attempt limit required")
    # Claims and renewals share one transaction lock, even without a job row.
    # This serializes only the short claim transaction, never model execution.
    db.execute(text("SELECT pg_advisory_xact_lock(741201, 1)"))
    now = _current_time(now, clock)
    expires_at = _lease(now, lease_seconds)
    active = db.scalar(
        select(Job.id)
        .where(
            Job.status == "running",
            Job.lease_expires_at > now,
        )
        .limit(1)
    )
    if active is not None:
        return None
    while True:
        row = db.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    and_(
                        Job.status == "running",
                        or_(
                            Job.lease_expires_at <= now, Job.lease_expires_at.is_(None)
                        ),
                    ),
                ),
            )
            .order_by(Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        if row.attempt >= max_attempts:
            _finish(row, "failed", "failed", now)
            row.error_code = "lease_exhausted"
            db.flush()
            continue
        row.status = "running"
        row.phase = "generating_question"
        row.started_at = row.started_at or now
        row.lease_expires_at = expires_at
        token = uuid4()
        row.lease_token = token
        row.attempt += 1
        db.flush()
        return Claim(
            id=row.id,
            profile_id=row.profile_id,
            user_id=row.user_id,
            profile_revision=row.profile_revision,
            input_json=deepcopy(row.input_json),
            lease_token=token,
            attempt=row.attempt,
        )


def owns(
    db: Session,
    claim: Claim,
    *,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> Job | None:
    _time(now)
    row = db.scalar(
        select(Job)
        .where(
            Job.id == claim.id,
            Job.user_id == claim.user_id,
            Job.profile_id == claim.profile_id,
            Job.status == "running",
            Job.lease_token == claim.lease_token,
            Job.attempt == claim.attempt,
            Job.lease_expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or not _unexpired(row, _current_time(now, clock)):
        return None
    return row


def heartbeat(
    db: Session,
    claim: Claim,
    *,
    now: datetime,
    lease_seconds: int,
    clock: Callable[[], datetime] | None = None,
) -> bool:
    _lease(now, lease_seconds)
    # Renewals must serialize with claims before taking the job row lock: an
    # uncommitted renewal otherwise lets SKIP LOCKED claim a second model job.
    db.execute(text("SELECT pg_advisory_xact_lock(741201, 1)"))
    row = owns(db, claim, now=now, clock=clock)
    if row is None:
        return False
    now = _current_time(now, clock)
    if not _unexpired(row, now):
        return False
    row.lease_expires_at = _lease(now, lease_seconds)
    db.flush()
    return True


def _finish(row: Job, status: str, phase: str, now: datetime) -> None:
    row.status = status
    row.phase = phase
    row.finished_at = now
    row.lease_token = None
    row.lease_expires_at = None


def complete(
    db: Session,
    claim: Claim,
    *,
    now: datetime,
    result_json: dict[str, Any],
    clock: Callable[[], datetime] | None = None,
) -> bool:
    row = owns(db, claim, now=now, clock=clock)
    if row is None:
        return False
    now = _current_time(now, clock)
    if not _unexpired(row, now):
        return False
    _finish(row, "succeeded", "completed", now)
    row.result_json = deepcopy(result_json)
    row.error_code = None
    db.flush()
    return True


def fail(
    db: Session,
    claim: Claim,
    *,
    now: datetime,
    code: str,
    clock: Callable[[], datetime] | None = None,
) -> bool:
    row = owns(db, claim, now=now, clock=clock)
    if row is None:
        return False
    now = _current_time(now, clock)
    if not _unexpired(row, now):
        return False
    _finish(row, "failed", "failed", now)
    row.error_code = code if code in ERROR_CODES else "job_failed"
    row.result_json = None
    db.flush()
    return True


def cancel(db: Session, job_id: UUID, user_id: UUID, *, now: datetime) -> Job:
    _time(now)
    row = db.scalar(
        select(Job)
        .where(
            Job.id == job_id,
            Job.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise JobNotFound
    if row.status not in TERMINAL_STATUSES:
        _finish(row, "cancelled", row.phase, now)
        row.result_json = None
        row.error_code = None
        db.flush()
    return row
