from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.ai.questions import ContractModel
from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.intake import store
from app.intake.question_service import (
    QuestionServiceError,
    answer,
    generate,
    get_question,
)
from app.jobs.store import JobConflict

router = APIRouter(prefix="/api")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


class GenerationRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]


class AnswerRequest(GenerationRequest):
    selected_option_id: UUID


def question_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, QuestionServiceError)
    messages = {
        "idempotency_conflict": "같은 요청 키로 다른 작업을 요청할 수 없습니다.",
        "revision_conflict": "입력이 변경됐습니다. 최신 초안과 질문을 확인해주세요.",
        "question_provider_unavailable": "질문 생성 서버 설정을 확인해주세요.",
        "question_not_found": "질문이나 초안을 찾을 수 없습니다.",
        "invalid_option": "현재 질문의 선택지를 골라주세요.",
        "invalid_question_request": "질문 요청 형식과 요청 키를 확인해주세요.",
        "question_unavailable": "질문 서비스를 사용할 수 없습니다.",
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
        raise QuestionServiceError(404, "question_not_found") from None


def request_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if (
        len(values) != 1
        or not 1 <= len(values[0]) <= 128
        or any(not 33 <= ord(c) <= 126 for c in values[0])
    ):
        raise QuestionServiceError(422, "invalid_question_request")
    return values[0]


async def body(request: Request, model):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 8192:
            raise QuestionServiceError(422, "invalid_question_request")
        data.extend(chunk)
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise QuestionServiceError(422, "invalid_question_request") from None


async def generation_body(request: Request) -> GenerationRequest:
    return await body(request, GenerationRequest)


async def answer_body(request: Request) -> AnswerRequest:
    return await body(request, AnswerRequest)


def request_schema(model):
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        },
        "parameters": [
            {
                "in": "header",
                "name": "Idempotency-Key",
                "required": True,
                "schema": {"type": "string", "minLength": 1, "maxLength": 128},
            }
        ],
    }


@router.post(
    "/profiles/{id}/questions", openapi_extra=request_schema(GenerationRequest)
)
def generate_question(
    id: str,
    user: WriteUser,
    db: DB,
    key: Annotated[str, Depends(request_key)],
    payload: Annotated[GenerationRequest, Depends(generation_body)],
):
    try:
        status, response = generate(
            db, identifier(id), user.user.id, payload.expected_revision, key
        )
        db.commit()
        return JSONResponse(response, status_code=status, headers=PRIVATE)
    except store.ProfileNotFound:
        raise QuestionServiceError(404, "question_not_found") from None
    except JobConflict:
        raise QuestionServiceError(409, "idempotency_conflict") from None
    except SQLAlchemyError:
        db.rollback()
        raise QuestionServiceError(503, "question_unavailable") from None


@router.get("/questions/{id}")
def read_question(id: str, user: ReadUser, db: DB):
    try:
        return JSONResponse(
            get_question(db, identifier(id), user.user.id), headers=PRIVATE
        )
    except SQLAlchemyError:
        db.rollback()
        raise QuestionServiceError(503, "question_unavailable") from None


@router.post(
    "/profiles/{id}/questions/{question_id}/answer",
    openapi_extra=request_schema(AnswerRequest),
)
def apply_answer(
    id: str,
    question_id: str,
    user: WriteUser,
    db: DB,
    key: Annotated[str, Depends(request_key)],
    payload: Annotated[AnswerRequest, Depends(answer_body)],
):
    try:
        status, response = answer(
            db,
            identifier(id),
            user.user.id,
            identifier(question_id),
            payload.selected_option_id,
            payload.expected_revision,
            key,
        )
        db.commit()
        return JSONResponse(response, status_code=status, headers=PRIVATE)
    except store.ProfileNotFound:
        raise QuestionServiceError(404, "question_not_found") from None
    except store.RevisionConflict:
        raise QuestionServiceError(409, "revision_conflict") from None
    except ValidationError:
        raise QuestionServiceError(422, "invalid_option") from None
    except SQLAlchemyError:
        db.rollback()
        raise QuestionServiceError(503, "question_unavailable") from None
