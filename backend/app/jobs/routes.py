from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.jobs import store

router = APIRouter(prefix="/api/jobs")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


class JobHTTPError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def job_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, JobHTTPError)
    messages = {
        "job_not_found": "작업을 찾을 수 없습니다.",
        "job_unavailable": "작업 서비스를 사용할 수 없습니다.",
    }
    return JSONResponse(
        {
            "code": error.code,
            "message": messages[error.code],
            "request_id": uuid4().hex,
        },
        status_code=error.status,
        headers=PRIVATE,
    )


def job_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise JobHTTPError(404, "job_not_found") from None


@router.get("/{id}")
def get_job(id: str, user: ReadUser, db: DB):
    try:
        result = store.public_job(store.get_job(db, job_id(id), user.user.id))
        db.commit()
        return JSONResponse(result, headers=PRIVATE)
    except store.JobNotFound:
        db.rollback()
        raise JobHTTPError(404, "job_not_found") from None
    except SQLAlchemyError:
        db.rollback()
        raise JobHTTPError(503, "job_unavailable") from None


@router.post("/{id}/cancel")
def cancel_job(id: str, user: WriteUser, db: DB):
    try:
        result = store.public_job(
            store.cancel(db, job_id(id), user.user.id, now=datetime.now(UTC))
        )
        db.commit()
        return JSONResponse(result, headers=PRIVATE)
    except store.JobNotFound:
        db.rollback()
        raise JobHTTPError(404, "job_not_found") from None
    except SQLAlchemyError:
        db.rollback()
        raise JobHTTPError(503, "job_unavailable") from None
