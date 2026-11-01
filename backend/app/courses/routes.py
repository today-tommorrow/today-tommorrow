"""Authenticated education detail and comparison without model execution."""

import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import DB, authenticated
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal

from .errors import CourseError
from .repository import CatalogPin, CourseRepository
from .schemas import Comparison, CourseDetail

router = APIRouter(prefix="/api/courses")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def course_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, CourseError)
    messages = {
        "invalid_course_request": "교육 ID와 비교할 교육 2~3개를 확인해주세요.",
        "course_not_found": "요청한 교육을 찾을 수 없습니다.",
        "catalog_unavailable": "교육 정보를 조회할 수 없습니다. 잠시 후 다시 시도해주세요.",
        "catalog_invalid": "교육 원본 정보를 확인할 수 없습니다.",
        "catalog_changed": "설정된 교육 데이터 버전과 현재 데이터가 다릅니다.",
    }
    return JSONResponse({"code": error.code, "message": messages[error.code], "request_id": uuid4().hex},
                        status_code=error.status, headers=PRIVATE)


def identifier(value: str) -> UUID:
    if UUID_TEXT.fullmatch(value) is None:
        raise CourseError(422, "invalid_course_request")
    return UUID(value)


def repository(db: DB) -> CourseRepository:
    return CourseRepository(db.get_bind(), CatalogPin.load())


@router.get("/compare", response_model=Comparison, openapi_extra={
    "parameters": [{"in": "query", "name": "ids", "required": True,
                    "description": "Two or three distinct canonical UUIDs, comma separated; order is preserved.",
                    "schema": {"type": "string", "minLength": 73, "maxLength": 110}}],
})
def compare_courses(request: Request, user: ReadUser, db: DB) -> JSONResponse:
    values = request.query_params.getlist("ids")
    if len(values) != 1 or len(values[0]) > 110:
        raise CourseError(422, "invalid_course_request")
    parts = values[0].split(",")
    if not 2 <= len(parts) <= 3:
        raise CourseError(422, "invalid_course_request")
    ids = [identifier(value) for value in parts]
    if len(set(ids)) != len(ids):
        raise CourseError(422, "invalid_course_request")
    result = Comparison(items=repository(db).details(ids))
    return JSONResponse(result.model_dump(mode="json"), headers=PRIVATE)


@router.get("/{id}", response_model=CourseDetail)
def read_course(id: str, user: ReadUser, db: DB) -> JSONResponse:
    result = repository(db).details([identifier(id)])[0]
    return JSONResponse(result.model_dump(mode="json"), headers=PRIVATE)
