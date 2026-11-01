"""The saved-list contract distinguishes live courses from retained snapshots."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CourseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    name: str
    provider_name: str | None
    field_code: str | None = None
    source_system: Literal["work24"] = "work24"
    course_type: Literal["vocational_training"] = "vocational_training"
    evidence_level: Literal["listing_only"] = "listing_only"
    detail_url: str | None
    is_demo: Literal[False] = False


class SavedCourseSummary(CourseSummary):
    saved_catalog_version_id: UUID
    catalog_status: Literal["current", "changed", "unavailable"]


class CoursePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: list[SavedCourseSummary]
    next_cursor: str | None
    catalog_version_id: UUID
