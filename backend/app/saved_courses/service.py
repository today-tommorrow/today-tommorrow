"""Owner-scoped idempotent saves and catalog-fenced keyset pagination."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .catalog import Catalog, canonical
from .models import SavedCourse
from .schemas import CoursePage, CourseSummary, SavedCourseSummary


@dataclass
class SavedCourseError(Exception):
    status: int
    code: str


def save(db: Session, user_id: UUID, identifier: UUID, catalog: Catalog) -> None:
    # Replay keeps original save time/snapshot even if the course disappeared.
    if db.get(SavedCourse, (user_id, identifier)) is not None:
        return
    course = catalog.courses.get(identifier)
    if course is None:
        raise SavedCourseError(404, "course_not_found")
    # Both the public course key and exact source key identify an owner's save.
    # Arbitrating only the primary key can race with uq_saved_source_owner.
    db.execute(insert(SavedCourse).values(
        user_id=user_id, course_id=identifier, source_course_id=course.source_id,
        saved_catalog_version_id=catalog.version,
        saved_snapshot=course.summary.model_dump(mode="json"), created_at=datetime.now(UTC),
    ).on_conflict_do_nothing())


def remove(db: Session, user_id: UUID, identifier: UUID) -> None:
    db.execute(delete(SavedCourse).where(SavedCourse.user_id == user_id, SavedCourse.course_id == identifier))


def encode_cursor(user_id: UUID, catalog: Catalog, last: SavedCourse, signing_key: bytes) -> str:
    payload = canonical({"v": 1, "owner": str(user_id), "catalog": str(catalog.version),
                         "fingerprint": catalog.fingerprint, "at": last.created_at.isoformat(),
                         "id": str(last.course_id)}).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(signing_key, b"saved-courses-cursor-v1:" + body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def decode_cursor(value: str, user_id: UUID, catalog: Catalog, signing_key: bytes) -> tuple[datetime, UUID]:
    try:
        if len(value) > 2048:
            raise ValueError
        body, signature = value.split(".")
        expected = hmac.new(signing_key, b"saved-courses-cursor-v1:" + body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
        if set(data) != {"v", "owner", "catalog", "fingerprint", "at", "id"} or type(data["v"]) is not int or data["v"] != 1 or data["owner"] != str(user_id):
            raise ValueError
        moment, identifier = datetime.fromisoformat(data["at"]), UUID(data["id"])
        if moment.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise SavedCourseError(400, "invalid_saved_cursor") from None
    if data["catalog"] != str(catalog.version) or data["fingerprint"] != catalog.fingerprint:
        raise SavedCourseError(409, "saved_catalog_changed")
    return moment, identifier


def page(db: Session, user_id: UUID, catalog: Catalog, signing_key: bytes,
         *, limit: int = 20, cursor: str | None = None) -> CoursePage:
    query = select(SavedCourse).where(SavedCourse.user_id == user_id)
    if cursor is not None:
        moment, identifier = decode_cursor(cursor, user_id, catalog, signing_key)
        query = query.where(tuple_(SavedCourse.created_at, SavedCourse.course_id) < tuple_(moment, identifier))
    rows = db.scalars(query.order_by(SavedCourse.created_at.desc(), SavedCourse.course_id.desc()).limit(limit + 1)).all()
    visible = rows[:limit]
    items = []
    for row in visible:
        current = catalog.courses.get(row.course_id)
        snapshot = CourseSummary.model_validate(row.saved_snapshot)
        summary = current.summary if current is not None else snapshot
        state = "unavailable" if current is None else (
            "current" if row.saved_catalog_version_id == catalog.version and summary == snapshot else "changed"
        )
        items.append(SavedCourseSummary(**summary.model_dump(), saved_catalog_version_id=row.saved_catalog_version_id, catalog_status=state))
    next_cursor = encode_cursor(user_id, catalog, visible[-1], signing_key) if len(rows) > limit else None
    return CoursePage(items=items, next_cursor=next_cursor, catalog_version_id=catalog.version)
