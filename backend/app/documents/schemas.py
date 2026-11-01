from typing import Annotated, Literal

from pydantic import Field

from app.ai.questions import ContractModel


class TextRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
    kind: Literal["resume", "job_description", "portfolio", "other"]
    text: Annotated[str, Field(min_length=1)]


class FileRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
    kind: Literal["resume", "job_description", "portfolio", "other"]


class DocumentResponse(ContractModel):
    id: str
    profile_id: str
    kind: Literal["resume", "job_description", "portfolio", "other"]
    source_type: Literal["text", "file"]
    display_name: str
    source_url: str | None
    mime_type: str | None
    size_bytes: int | None
    status: Literal["queued", "processing", "ready", "failed"]
    extracted_preview: str | None
    job_id: str | None
    review_confirmed: bool
    error_code: str | None
    content_sha256: str | None
    created_at: str


class DocumentAccepted(ContractModel):
    document: DocumentResponse
    profile_revision: int


class RetryRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
