import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.documents.config import max_documents
from app.documents.models import Document, DocumentChunk
from app.intake import store
from app.intake.policy import evaluate
from app.intake.question_service import invalidate
from app.intake.requests import recorded, reserve


class DocumentError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def public(row: Document) -> dict:
    return {
        "id": str(row.id),
        "profile_id": str(row.profile_id),
        "kind": row.kind,
        "source_type": row.source_type,
        "display_name": row.display_name,
        "source_url": None,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "status": row.status,
        "extracted_preview": None,
        "job_id": None,
        "error_code": row.error_code,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat(),
    }


def list_documents(db: Session, profile_id: UUID, user_id: UUID) -> list[dict]:
    rows = db.scalars(
        select(Document)
        .where(Document.profile_id == profile_id, Document.user_id == user_id)
        .order_by(Document.created_at, Document.id)
    )
    return [public(row) for row in rows]


def register(
    db: Session, profile_id: UUID, user_id: UUID, payload, key: str | None
) -> tuple[int, dict]:
    profile = store.get_profile(db, profile_id, user_id, lock=True)
    data = payload.text.encode("utf-8")
    content_hash = hashlib.sha256(data).hexdigest()
    request_data = {
        "profile_id": str(profile_id),
        "expected_revision": payload.expected_revision,
        "kind": payload.kind,
        "content_sha256": content_hash,
    }
    key = (
        key
        or "text:"
        + hashlib.sha256(
            (
                str(profile_id)
                + ":"
                + str(payload.expected_revision)
                + ":"
                + payload.kind
                + ":"
                + content_hash
            ).encode()
        ).hexdigest()
    )
    request, created = reserve(
        db, profile=profile, operation="register_text", key=key, request=request_data
    )
    if not created:
        return request.status_code, request.response_json
    return persist(
        db, profile, profile_id, user_id, payload, request, data, content_hash
    )


def persist(
    db,
    profile,
    profile_id,
    user_id,
    payload,
    request,
    data,
    content_hash,
    *,
    file_info=None,
):
    if profile.revision != payload.expected_revision:
        raise DocumentError(409, "revision_conflict")
    count = db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.profile_id == profile_id, Document.user_id == user_id)
    )
    if count >= max_documents():
        raise DocumentError(422, "document_limit_reached")
    now = datetime.now(UTC)
    row = Document(
        profile_id=profile_id,
        user_id=user_id,
        kind=payload.kind,
        content=payload.text if file_info is None else file_info["text"],
        **(
            {}
            if file_info is None
            else {
                "source_type": "file",
                "display_name": file_info["name"],
                "mime_type": file_info["mime"],
                "status": file_info["status"],
                "error_code": file_info["error"],
                "raw_content": data,
            }
        ),
        content_sha256=content_hash,
        size_bytes=len(data),
        created_at=now,
    )
    db.add(row)
    db.flush()
    chunks = (
        [(payload.text, f"text:chars:0:{len(payload.text)}")]
        if file_info is None
        else file_info["chunks"]
    )
    for ordinal, (chunk_text, locator) in enumerate(chunks):
        db.add(
            DocumentChunk(
                document_id=row.id,
                profile_id=profile_id,
                user_id=user_id,
                ordinal=ordinal,
                text_content=chunk_text,
                content_sha256=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                locator=locator,
            )
        )
    profile.answers_json = {**profile.answers_json, "document_decision": "provided"}
    profile.slot_origins_json = {
        **profile.slot_origins_json,
        "document_decision": "direct",
    }
    profile.completed_sections_json = evaluate(
        profile.answers_json, profile.unresolved_conflicts_json
    )["completed_sections"]
    profile.revision += 1
    profile.status = "draft"
    profile.submitted_revision = None
    profile.updated_at = now
    invalidate(db, profile_id)
    return recorded(
        request, 202, {"document": public(row), "profile_revision": profile.revision}
    )


def register_file(db, profile_id, user_id, payload, key, data, info):
    profile = store.get_profile(db, profile_id, user_id, lock=True)
    content_hash = hashlib.sha256(data).hexdigest()
    request_data = {
        "profile_id": str(profile_id),
        "expected_revision": payload.expected_revision,
        "kind": payload.kind,
        "content_sha256": content_hash,
        "display_name": info["name"],
        "mime_type": info["mime"],
    }
    key = (
        key
        or "file:"
        + hashlib.sha256(
            (
                str(profile_id)
                + ":"
                + str(payload.expected_revision)
                + ":"
                + payload.kind
                + ":"
                + content_hash
                + ":"
                + info["name"]
                + ":"
                + info["mime"]
            ).encode()
        ).hexdigest()
    )
    request, created = reserve(
        db, profile=profile, operation="register_file", key=key, request=request_data
    )
    if not created:
        return request.status_code, request.response_json
    return persist(
        db,
        profile,
        profile_id,
        user_id,
        payload,
        request,
        data,
        content_hash,
        file_info=info,
    )
