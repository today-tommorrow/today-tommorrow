"""Freeze user-approved input and persist results inside caller-owned transactions."""

from __future__ import annotations

import hmac
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intake import store
from app.intake.policy import active_conflicts, evaluate
from app.intake.requests import digest, recorded, reserve
from app.jobs import store as jobs
from app.jobs.models import Job
from app.recommendations.models import RecommendationRun
from app.recommendations.review import review_snapshot, review_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.recommendations.contracts import RecommendationRunner


class RecommendationServiceError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def submit(
    db: Session,
    profile_id: UUID,
    user_id: UUID,
    expected_revision: int,
    token: str,
    key: str,
    runner_factory: Callable[[], RecommendationRunner],
) -> tuple[int, dict]:
    profile = store.get_profile(db, profile_id, user_id, lock=True)
    request, created = reserve(
        db,
        profile=profile,
        operation="submit_recommendation",
        key=key,
        request={
            "profile_id": str(profile_id),
            "expected_revision": expected_revision,
            "review_token": token,
        },
    )
    if not created:
        return request.status_code, request.response_json
    if profile.revision != expected_revision:
        raise RecommendationServiceError(409, "revision_conflict")
    selections = store.selection_summaries(db, profile)
    expected = review_token(profile, selections)
    if expected is None:
        state = evaluate(profile.answers_json, profile.unresolved_conflicts_json)
        incomplete = (
            state["missing_slots"]
            and profile.answers_json.get("document_decision") != "provided"
            and not active_conflicts(
                profile.answers_json, profile.unresolved_conflicts_json
            )
        )
        raise RecommendationServiceError(
            422 if incomplete else 409, "profile_not_ready"
        )
    if not hmac.compare_digest(expected, token):
        raise RecommendationServiceError(409, "review_token_invalid")
    job = db.scalar(
        select(Job)
        .where(
            Job.profile_id == profile_id,
            Job.user_id == user_id,
            Job.kind == "recommendation",
            Job.profile_revision == expected_revision,
            Job.status.in_(["queued", "running", "succeeded"]),
        )
        .order_by(Job.created_at.desc(), Job.id)
        .limit(1)
    )
    if job is None:
        try:
            context = runner_factory().capture_context()
            snapshot = deepcopy(review_snapshot(profile, selections))
            snapshot.update(
                context=context,
                as_of=datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
            )
            fingerprint = digest(snapshot)
        except ValueError, TypeError, KeyError:
            raise RecommendationServiceError(
                503, "recommendation_unavailable"
            ) from None
        job = jobs.enqueue(
            db,
            profile_id=profile_id,
            user_id=user_id,
            profile_revision=expected_revision,
            idempotency_key=key,
            request_hash=fingerprint,
            input_json=snapshot,
            kind="recommendation",
            now=datetime.now(UTC),
        )
    profile.status = "submitted"
    profile.submitted_revision = expected_revision
    return recorded(request, 202, jobs.accepted(job))


def current_job(db: Session, profile_id: UUID, user_id: UUID) -> dict:
    profile = store.get_profile(db, profile_id, user_id)
    job = db.scalar(
        select(Job)
        .where(
            Job.profile_id == profile_id,
            Job.user_id == user_id,
            Job.kind == "recommendation",
            Job.profile_revision == profile.revision,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    return {
        "profile_id": str(profile.id),
        "revision": profile.revision,
        "job": jobs.public_job(job) if job is not None else None,
    }


def get_result(db: Session, run_id: UUID, user_id: UUID) -> dict:
    row = db.scalar(
        select(RecommendationRun).where(
            RecommendationRun.id == run_id,
            RecommendationRun.user_id == user_id,
        )
    )
    if row is None:
        raise RecommendationServiceError(404, "recommendation_not_found")
    return deepcopy(row.public_json)
