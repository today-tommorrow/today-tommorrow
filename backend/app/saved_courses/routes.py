from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE, settings
from app.auth.store import SessionPrincipal

from . import service
from .catalog import CatalogUnavailable, read_catalog
from .schemas import CoursePage
from .service import SavedCourseError

router = APIRouter(prefix="/api/me/saved-courses")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


def error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, SavedCourseError)
    messages = {
        "course_not_found": "현재 제공되는 교육을 찾을 수 없습니다.",
        "invalid_saved_cursor": "관심 교육 목록 주소가 올바르지 않습니다. 처음부터 다시 조회해주세요.",
        "saved_catalog_changed": "교육 목록이 갱신됐습니다. 관심 교육을 처음부터 다시 조회해주세요.",
        "saved_courses_unavailable": "관심 교육을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.",
    }
    return JSONResponse({"code": error.code, "message": messages[error.code], "request_id": uuid4().hex},
                        status_code=error.status, headers=PRIVATE)


@router.get("", response_model=CoursePage)
def list_courses(user: ReadUser, db: DB, response: Response,
                 limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None) -> CoursePage:
    response.headers.update(PRIVATE)
    try:
        catalog = read_catalog(db.get_bind())
        return service.page(db, user.user.id, catalog, settings().signing_key, limit=limit, cursor=cursor)
    except (SQLAlchemyError, CatalogUnavailable, ValidationError, ValueError):
        db.rollback()
        raise SavedCourseError(503, "saved_courses_unavailable") from None


@router.put("/{course_id}", status_code=204)
def save_course(course_id: UUID, user: WriteUser, db: DB) -> Response:
    try:
        catalog = read_catalog(db.get_bind())
        service.save(db, user.user.id, course_id, catalog)
        db.commit()
    except (SQLAlchemyError, CatalogUnavailable, ValidationError, ValueError):
        db.rollback()
        raise SavedCourseError(503, "saved_courses_unavailable") from None
    return Response(status_code=204, headers=PRIVATE)


@router.delete("/{course_id}", status_code=204)
def remove_course(course_id: UUID, user: WriteUser, db: DB) -> Response:
    try:
        service.remove(db, user.user.id, course_id)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise SavedCourseError(503, "saved_courses_unavailable") from None
    return Response(status_code=204, headers=PRIVATE)
