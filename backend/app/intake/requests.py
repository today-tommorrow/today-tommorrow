"""Persist request identity and replay responses within the caller transaction."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.intake.question_models import IntakeRequest


class IdempotencyConflict(Exception):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def reserve(
    db: Session, *, profile, operation: str, key: str, request: dict
) -> tuple[IntakeRequest, bool]:
    request_hash = digest(request)
    created = db.scalar(
        insert(IntakeRequest)
        .values(
            user_id=profile.user_id,
            profile_id=profile.id,
            operation=operation,
            idempotency_key=key,
            request_hash=request_hash,
            status_code=0,
            response_json={},
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "operation", "idempotency_key"]
        )
        .returning(IntakeRequest.user_id)
    )
    row = db.scalars(
        select(IntakeRequest).where(
            IntakeRequest.user_id == profile.user_id,
            IntakeRequest.operation == operation,
            IntakeRequest.idempotency_key == key,
        )
    ).one()
    if row.request_hash != request_hash:
        raise IdempotencyConflict()
    return row, created is not None


def recorded(request: IntakeRequest, status: int, payload: dict) -> tuple[int, dict]:
    request.status_code, request.response_json = status, payload
    return status, payload
