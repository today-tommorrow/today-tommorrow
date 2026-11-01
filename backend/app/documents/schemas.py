from typing import Annotated, Literal

from pydantic import Field

from app.ai.questions import ContractModel


class TextRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2147483646)]
    kind: Literal["resume", "job_description", "portfolio", "other"]
    text: Annotated[str, Field(min_length=1)]


class DocumentResponse(ContractModel):
    id: str
    profile_id: str
    kind: Literal["resume", "job_description", "portfolio", "other"]
    source_type: Literal["text"]
    display_name: str
    source_url: str | None
    mime_type: str | None
    size_bytes: int | None
    status: Literal["queued"]
    extracted_preview: str | None
    job_id: str | None
    error_code: str | None
    content_sha256: str | None
    created_at: str


class DocumentAccepted(ContractModel):
    document: DocumentResponse
    profile_revision: int
