from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.intake import store
from app.intake.requests import IdempotencyConflict
from app.jobs.store import JobConflict
from app.recommendations.schemas import SubmitRequest
from app.recommendations.service import RecommendationServiceError, get_result, submit

router = APIRouter(prefix="/api")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


def recommendation_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RecommendationServiceError)
    messages = {
        "recommendation_not_found": "추천 결과나 입력을 찾을 수 없습니다.",
        "revision_conflict": "입력이 변경됐습니다. 최신 내용을 확인해주세요.",
        "review_token_invalid": "확인한 입력이 변경됐습니다. 다시 확인해주세요.",
        "profile_not_ready": "필수 입력과 자료 선택을 확인해주세요.",
        "idempotency_conflict": "같은 요청 키로 다른 작업을 요청할 수 없습니다.",
        "invalid_recommendation_request": "추천 요청 형식과 요청 키를 확인해주세요.",
        "recommendation_unavailable": "추천 서비스를 사용할 수 없습니다.",
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


def identifier(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise RecommendationServiceError(404, "recommendation_not_found") from None


def request_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if (
        len(values) != 1
        or not 1 <= len(values[0]) <= 128
        or any(not 33 <= ord(c) <= 126 for c in values[0])
    ):
        raise RecommendationServiceError(422, "invalid_recommendation_request")
    return values[0]


async def submit_body(request: Request) -> SubmitRequest:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 8192:
            raise RecommendationServiceError(422, "invalid_recommendation_request")
        data.extend(chunk)
    try:
        return SubmitRequest.model_validate_json(data)
    except ValidationError:
        raise RecommendationServiceError(
            422, "invalid_recommendation_request"
        ) from None


def build_runner(engine):
    from app.recommendations.settings import build_runner as configured_runner

    return configured_runner(engine)


@router.post(
    "/profiles/{id}/submit",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": SubmitRequest.model_json_schema()}
            },
        },
        "parameters": [
            {
                "in": "header",
                "name": "Idempotency-Key",
                "required": True,
                "schema": {"type": "string", "minLength": 1, "maxLength": 128},
            }
        ],
    },
)
def submit_profile(
    id: str,
    user: WriteUser,
    db: DB,
    key: Annotated[str, Depends(request_key)],
    payload: Annotated[SubmitRequest, Depends(submit_body)],
):
    try:
        status, response = submit(
            db,
            identifier(id),
            user.user.id,
            payload.expected_revision,
            payload.review_token,
            key,
            lambda: build_runner(db.get_bind()),
        )
        db.commit()
        return JSONResponse(response, status_code=status, headers=PRIVATE)
    except store.ProfileNotFound:
        raise RecommendationServiceError(404, "recommendation_not_found") from None
    except IdempotencyConflict, JobConflict:
        raise RecommendationServiceError(409, "idempotency_conflict") from None
    except SQLAlchemyError:
        db.rollback()
        raise RecommendationServiceError(503, "recommendation_unavailable") from None


@router.get("/recommendations/{id}")
def read_recommendation(id: str, user: ReadUser, db: DB):
    try:
        return JSONResponse(
            get_result(db, identifier(id), user.user.id), headers=PRIVATE
        )
    except SQLAlchemyError:
        db.rollback()
        raise RecommendationServiceError(503, "recommendation_unavailable") from None
