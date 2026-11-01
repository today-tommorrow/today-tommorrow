import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import PurePath
from threading import BoundedSemaphore
from typing import Annotated
from uuid import UUID, uuid4

import psutil
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.documents.config import max_text_bytes
from app.documents.models import Document
from app.documents.schemas import (
    DocumentAccepted,
    DocumentResponse,
    FileRequest,
    TextRequest,
)
from app.documents.service import DocumentError, public, register, register_file
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
        if "\x00" in payload.text or not payload.text.strip():
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


FILE_LIMIT = 5 * 1024 * 1024
FORM_LIMIT = FILE_LIMIT + 65536
PARSER_SLOTS = BoundedSemaphore(2)
FORMATS = {
    ".txt": ("txt", "text/plain"),
    ".pdf": ("pdf", "application/pdf"),
    ".docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


def extract_file(data: bytes, kind: str) -> dict:
    if not PARSER_SLOTS.acquire(timeout=1):
        raise DocumentError(503, "document_unavailable")
    try:
        return _extract_file(data, kind)
    finally:
        PARSER_SLOTS.release()


def _extract_file(data: bytes, kind: str) -> dict:
    try:
        with subprocess.Popen(
            [sys.executable, "-m", "app.documents.extractor", kind],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as child:
            try:
                process = psutil.Process(child.pid)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(child.communicate, data)
                    try:
                        deadline = time.monotonic() + 10
                        while not future.done():
                            if time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired(child.args, 10)
                            try:
                                if process.memory_info().rss > 512 * 1024 * 1024:
                                    raise RuntimeError("parser memory limit")
                            except psutil.NoSuchProcess:
                                pass
                            try:
                                future.result(timeout=0.02)
                            except TimeoutError:
                                pass
                        stdout, _ = future.result()
                    except BaseException:
                        child.kill()
                        raise
            except BaseException:
                child.kill()
                child.wait()
                raise
            if child.returncode:
                raise subprocess.SubprocessError("parser failed")
        result = json.loads(stdout)
    except subprocess.SubprocessError, OSError, ValueError, RuntimeError, psutil.Error:
        return {
            "text": "",
            "chunks": [],
            "status": "failed",
            "error": "extraction_failed",
        }
    if result.get("error") == "limit":
        raise DocumentError(413, "payload_too_large")
    if "error" in result:
        return {
            "text": "",
            "chunks": [],
            "status": "failed",
            "error": "extraction_failed",
        }
    return {**result, "status": "queued", "error": None}


@router.post(
    "/profiles/{id}/documents/files",
    status_code=202,
    response_model=DocumentAccepted,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["expected_revision", "kind", "file"],
                        "properties": {
                            "expected_revision": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2147483646,
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "resume",
                                    "job_description",
                                    "portfolio",
                                    "other",
                                ],
                            },
                            "file": {"type": "string", "format": "binary"},
                        },
                        "additionalProperties": False,
                    }
                }
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
async def create_file(
    id: str,
    request: Request,
    user: WriteUser,
    db: DB,
    key: Annotated[str | None, Depends(request_key)],
):
    if (
        not request.headers.get("content-type", "")
        .lower()
        .startswith("multipart/form-data;")
    ):
        raise DocumentError(422, "invalid_document_input")
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > FORM_LIMIT:
            raise DocumentError(413, "payload_too_large")
        data.extend(part)
    request._body = bytes(data)
    try:
        async with request.form(max_files=1, max_fields=2, max_part_size=65536) as form:
            if len(form.multi_items()) != 3 or set(form.keys()) != {
                "expected_revision",
                "kind",
                "file",
            }:
                raise DocumentError(422, "invalid_document_input")
            upload = form["file"]
            if (
                not isinstance(upload, UploadFile)
                or not isinstance(form["expected_revision"], str)
                or not isinstance(form["kind"], str)
            ):
                raise DocumentError(422, "invalid_document_input")
            revision = form["expected_revision"]
            if not revision.isascii() or not revision.isdecimal() or len(revision) > 10:
                raise DocumentError(422, "invalid_document_input")
            payload = FileRequest.model_validate(
                {"expected_revision": int(revision), "kind": form["kind"]}
            )
            name = upload.filename or ""
            if (
                not name
                or len(name) > 255
                or PurePath(name).name != name
                or "\\" in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
            ):
                raise DocumentError(422, "invalid_document_input")
            spec = FORMATS.get(PurePath(name).suffix.lower())
            if spec is None or upload.content_type not in (
                spec[1],
                "application/octet-stream",
                None,
            ):
                raise DocumentError(415, "unsupported_document_type")
            raw = await upload.read(FILE_LIMIT + 1)
            if len(raw) > FILE_LIMIT:
                raise DocumentError(413, "payload_too_large")
            if not raw:
                raise DocumentError(422, "invalid_document_input")
            if (
                (spec[0] == "pdf" and not raw.startswith(b"%PDF-"))
                or (spec[0] == "docx" and not raw.startswith(b"PK\x03\x04"))
                or (
                    spec[0] == "txt"
                    and (raw.startswith(b"%PDF-") or raw.startswith(b"PK\x03\x04"))
                )
            ):
                raise DocumentError(415, "unsupported_document_type")
            info = await run_in_threadpool(extract_file, raw, spec[0])
            info.update(name=name, mime=spec[1])
    except ValidationError, HTTPException, ValueError:
        raise DocumentError(422, "invalid_document_input") from None
    return await run_in_threadpool(
        save_file, db, id, user.user.id, payload, key, raw, info
    )


def save_file(db, id, user_id, payload, key, raw, info):
    try:
        status, result = register_file(
            db, identifier(id), user_id, payload, key, raw, info
        )
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
