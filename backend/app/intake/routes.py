from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.ai.questions import ContractModel
from app.auth.dependencies import DB, authenticated, authenticated_write
from app.auth.routes import PRIVATE
from app.auth.store import SessionPrincipal
from app.documents.config import max_documents
from app.intake import store
from app.intake.question_config import questions_enabled
from app.intake.schemas import ProfilePatch, profile_patch_schema

router = APIRouter(prefix="/api")
ReadUser = Annotated[SessionPrincipal, Depends(authenticated)]
WriteUser = Annotated[SessionPrincipal, Depends(authenticated_write)]


class ProfileError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def profile_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ProfileError)
    messages = {
        "profile_not_found": "초안을 찾을 수 없습니다.",
        "revision_conflict": "다른 곳에서 입력이 변경됐습니다. 최신 초안을 다시 확인해주세요.",
        "invalid_profile_input": "입력 형식과 허용된 값을 확인해주세요.",
        "profile_unavailable": "초안 저장 서비스를 사용할 수 없습니다.",
        "payload_too_large": "입력 크기가 허용 범위를 초과했습니다.",
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


def profile_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise ProfileError(404, "profile_not_found") from None


async def read_patch(request: Request) -> ProfilePatch:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise ProfileError(413, "payload_too_large")
    try:
        return ProfilePatch.model_validate_json(body)
    except ValidationError, ValueError:
        raise ProfileError(422, "invalid_profile_input") from None


@router.post("/profiles", status_code=201)
def create_profile(user: WriteUser, db: DB):
    try:
        result = store.profile_response(store.create_profile(db, user.user.id), db)
        db.commit()
        return JSONResponse(result, status_code=201, headers=PRIVATE)
    except SQLAlchemyError:
        db.rollback()
        raise ProfileError(503, "profile_unavailable") from None


@router.get("/profiles/{id}")
def get_profile(id: str, user: ReadUser, db: DB):
    try:
        return JSONResponse(
            store.profile_response(
                store.get_profile(db, profile_id(id), user.user.id), db
            ),
            headers=PRIVATE,
        )
    except store.ProfileNotFound:
        raise ProfileError(404, "profile_not_found") from None
    except SQLAlchemyError:
        db.rollback()
        raise ProfileError(503, "profile_unavailable") from None


@router.patch(
    "/profiles/{id}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": profile_patch_schema()}},
        }
    },
)
def patch_profile(
    id: str,
    user: WriteUser,
    db: DB,
    patch: Annotated[ProfilePatch, Depends(read_patch)],
):
    try:
        row = store.replace_answers(
            db,
            profile_id(id),
            user.user.id,
            patch.expected_revision,
            patch.answers.model_dump(exclude_unset=True),
        )
        result = store.profile_response(row, db)
        db.commit()
        return JSONResponse(result, headers=PRIVATE)
    except store.ProfileNotFound:
        raise ProfileError(404, "profile_not_found") from None
    except store.RevisionConflict:
        raise ProfileError(409, "revision_conflict") from None
    except store.DocumentsPresent:
        raise ProfileError(422, "invalid_profile_input") from None
    except SQLAlchemyError:
        db.rollback()
        raise ProfileError(503, "profile_unavailable") from None


@router.get("/onboarding/options")
def onboarding_options(user: ReadUser):
    fields = [
        ("ai_data", "AI·데이터"),
        ("software_cloud", "소프트웨어·클라우드"),
        ("security_network", "보안·네트워크"),
        ("robotics_manufacturing", "로봇·제조"),
        ("semiconductor", "반도체"),
        ("digital_content", "디지털 콘텐츠"),
        ("other", "다른 분야"),
        ("undecided", "아직 미정"),
    ]
    intents = [
        ("goal_known", "배우려는 목표가 있어요"),
        ("explore", "관심 분야를 찾아보고 싶어요"),
        ("advance", "기존 경험을 더 발전시키고 싶어요"),
    ]

    def options(values):
        return [
            {"value": value, "label": label, "description": label}
            for value, label in values
        ]

    return JSONResponse(
        {
            "schema_version": "0.8",
            "fields": options(fields),
            "entry_intents": options(intents),
            "allowed_file_extensions": [".txt", ".docx", ".pdf"],
            "max_upload_bytes": 5 * 1024 * 1024,
            "max_profile_documents": max_documents(),
            "upload_limit_note": "TXT, DOCX, 텍스트 PDF를 저장합니다. 분석 기능은 아직 제공되지 않아 대기 상태에서 자동 처리되지 않습니다.",
            "branch_rules_version": "1",
            "intake_policy_version": "1",
            "allowed_catalog_sources": ["work24"],
            "interpretation_enabled": False,
            "question_generation_enabled": questions_enabled(),
            "question_schema_version": "1.0",
        },
        headers=PRIVATE,
    )


class ReviewItemEdit(ContractModel):
    id: str
    label: Annotated[str, Field(min_length=1, max_length=500)]
    state: Literal["have", "want_to_learn", "unknown", "excluded"]


class ReviewPatch(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
    items: list[ReviewItemEdit]


async def read_review_patch(request: Request) -> ReviewPatch:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise ProfileError(413, "payload_too_large")
    try:
        return ReviewPatch.model_validate_json(body)
    except ValidationError, ValueError:
        raise ProfileError(422, "invalid_profile_input") from None


def review_patch_schema():
    schema = ReviewPatch.model_json_schema()
    definitions = schema.pop("$defs")
    schema["properties"]["items"]["items"] = definitions["ReviewItemEdit"]
    return schema


@router.patch(
    "/profiles/{id}/review-items",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": review_patch_schema()}},
        }
    },
)
def patch_review_items(
    id: str,
    payload: Annotated[ReviewPatch, Depends(read_review_patch)],
    user: WriteUser,
    db: DB,
):
    from app.intake.question_service import invalidate

    try:
        profile = store.get_profile(db, profile_id(id), user.user.id, lock=True)
        if profile.revision != payload.expected_revision:
            raise ProfileError(409, "revision_conflict")
        edits = {item.id: item for item in payload.items}
        existing = {item["id"] for item in profile.reviewed_items_json}
        if len(edits) != len(payload.items) or not set(edits).issubset(existing):
            raise ProfileError(422, "invalid_profile_input")
        profile.reviewed_items_json = [
            {**item, "label": edits[item["id"]].label, "state": edits[item["id"]].state}
            if item["id"] in edits
            else item
            for item in profile.reviewed_items_json
        ]
        profile.revision += 1
        profile.status = "draft"
        profile.submitted_revision = None
        profile.updated_at = datetime.now(UTC)
        invalidate(db, profile.id)
        result = store.profile_response(profile, db)
        db.commit()
        return JSONResponse(result, headers=PRIVATE)
    except store.ProfileNotFound:
        raise ProfileError(404, "profile_not_found") from None
    except SQLAlchemyError:
        db.rollback()
        raise ProfileError(503, "profile_unavailable") from None
