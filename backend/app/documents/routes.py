from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.documents.config import max_text_bytes
from app.documents.models import Document
from app.documents.schemas import DocumentAccepted, DocumentResponse, TextRequest
from app.documents.service import DocumentError, public, register
from app.intake import store
from app.intake.requests import IdempotencyConflict

router = APIRouter(prefix="/api")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


def error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, DocumentError)
    return JSONResponse(
        {
            "code": error.code,
            "message": "자료 요청을 처리할 수 없습니다.",
            "request_id": uuid4().hex,
        },
        status_code=error.status,
        headers=PRIVATE,
    )


def identifier(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise DocumentError(404, "document_not_found") from None


async def body(request: Request) -> TextRequest:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > max_text_bytes() * 6 + 8192:
            raise DocumentError(413, "payload_too_large")
        data.extend(chunk)
    try:
        payload = TextRequest.model_validate_json(data)
        encoded = payload.text.encode("utf-8")
        if "\x00" in payload.text:
            raise ValueError("invalid text")
    except ValidationError, UnicodeError, ValueError:
        raise DocumentError(422, "invalid_document_input") from None
    if len(encoded) > max_text_bytes():
        raise DocumentError(413, "payload_too_large")
    return payload


def request_key(request: Request) -> str | None:
    values = request.headers.getlist("idempotency-key")
    if not values:
        return None
    if (
        len(values) != 1
        or not 1 <= len(values[0]) <= 128
        or any(not 33 <= ord(c) <= 126 for c in values[0])
    ):
        raise DocumentError(422, "invalid_document_input")
    return values[0]


@router.post(
    "/profiles/{id}/documents/texts",
    status_code=202,
    response_model=DocumentAccepted,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": TextRequest.model_json_schema()}
            },
        },
        "parameters": [
            {
                "in": "header",
                "name": "Idempotency-Key",
                "required": False,
                "schema": {"type": "string", "minLength": 1, "maxLength": 128},
            }
        ],
    },
)
def create_text(
    id: str,
    user: WriteUser,
    db: DB,
    payload: Annotated[TextRequest, Depends(body)],
    key: Annotated[str | None, Depends(request_key)],
):
    try:
        status, result = register(db, identifier(id), user.user.id, payload, key)
        db.commit()
        return JSONResponse(result, status_code=status, headers=PRIVATE)
    except store.ProfileNotFound:
        raise DocumentError(404, "document_not_found") from None
    except IdempotencyConflict:
        raise DocumentError(409, "idempotency_conflict") from None
    except SQLAlchemyError:
        db.rollback()
        raise DocumentError(503, "document_unavailable") from None


@router.get("/documents/{id}", response_model=DocumentResponse)
def get_document(id: str, user: ReadUser, db: DB):
    try:
        row = db.scalar(
            select(Document).where(
                Document.id == identifier(id), Document.user_id == user.user.id
            )
        )
        if row is None:
            raise DocumentError(404, "document_not_found")
        return JSONResponse(public(row), headers=PRIVATE)
    except SQLAlchemyError:
        db.rollback()
        raise DocumentError(503, "document_unavailable") from None
