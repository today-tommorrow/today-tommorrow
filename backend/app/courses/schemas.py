"""Explicit public contracts; missing source values remain null."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(PublicModel):
    id: UUID
    field_path: str
    quote: str | None
    source_url: str | None
    source_system: Literal["work24"] = "work24"
    checked_at: None = None
    verification_state: Literal["observed", "unknown", "conflicting"]


class Offering(PublicModel):
    id: UUID
    external_offering_id: str
    cohort: str
    start_date: str | None
    end_date: str | None
    enrollment_start: None = None
    enrollment_end: None = None
    delivery_mode: Literal["online", "offline", "hybrid", "unknown"]
    region: str | None
    schedule: None = None
    training_hours: float | None
    tuition_krw: int | None
    listed_self_pay_krw: int | None
    eligibility_text: str | None
    recruitment_status: Literal["unknown"] = "unknown"
    checked_at: None = None


class CourseDetail(PublicModel):
    id: UUID
    name: str
    provider_name: str | None
    field_code: None = None
    source_system: Literal["work24"] = "work24"
    course_type: Literal["unknown"] = "unknown"
    evidence_level: Literal["listing_only"] = "listing_only"
    detail_url: str | None
    is_demo: Literal[False] = False
    description: str | None
    prerequisites: str | None
    learning_outcomes: list[str]
    offerings: list[Offering]
    evidence: list[Evidence]
    missing_fields: list[str]


class Comparison(PublicModel):
    items: list[CourseDetail]
