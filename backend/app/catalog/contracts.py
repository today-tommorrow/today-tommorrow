"""Immutable retrieval inputs/results, separate from public recommendation DTOs."""

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from types import MappingProxyType
from typing import Literal
from uuid import UUID

type JsonValue = None | bool | int | float | str | tuple[JsonValue, ...] | Mapping[str, JsonValue]
type ConditionStatus = Literal["pass", "fail", "unknown", "not_applicable"]


class CatalogError(ValueError):
    """A safe code, with no query text, database URL or driver response."""


def freeze_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    raise CatalogError("invalid_catalog_metadata")


def json_value(value: object) -> object:
    """Convert immutable result values for a probe, without mutating originals."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, (date, UUID)):
        return str(value)
    return value


def validated_vector(vector: Sequence[float]) -> tuple[float, ...]:
    try:
        if isinstance(vector, (str, bytes)) or len(vector) != 1024:
            raise ValueError
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ValueError
        result = struct.unpack("<1024f", struct.pack("<1024f", *vector))
        norm = math.sqrt(math.fsum(value * value for value in result))
        if not all(math.isfinite(value) for value in result) or abs(norm - 1) > 1e-5:
            raise ValueError
    except (ValueError, TypeError, OverflowError, struct.error):
        raise CatalogError("invalid_query_vector") from None
    # Conversion matches pgvector float32 storage. No normalization or model fallback.
    return result


@dataclass(frozen=True)
class SearchFilters:
    delivery_mode: Literal["online", "offline", "hybrid", "any", "unknown"] | None = None
    start_from: date | None = None
    start_to: date | None = None
    listed_region: str | None = None
    budget_krw: int | None = None
    availability: Literal["weekday_day", "evening_weekend", "flexible", "unknown"] | None = None
    field_code: str | None = None

    def __post_init__(self) -> None:
        if self.delivery_mode not in (None, "online", "offline", "hybrid", "any", "unknown"):
            raise CatalogError("invalid_search_filters")
        if self.availability not in (None, "weekday_day", "evening_weekend", "flexible", "unknown"):
            raise CatalogError("invalid_search_filters")
        if any(value is not None and type(value) is not date for value in (self.start_from, self.start_to)):
            raise CatalogError("invalid_search_filters")
        if self.start_from and self.start_to and self.start_from > self.start_to:
            raise CatalogError("invalid_search_filters")
        if self.budget_krw is not None and (type(self.budget_krw) is not int or self.budget_krw < 0):
            raise CatalogError("invalid_search_filters")
        for value in (self.listed_region, self.field_code):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                raise CatalogError("invalid_search_filters")


def validate_options(as_of: date, filters: SearchFilters, limit: int, evidence_limit: int) -> None:
    if type(as_of) is not date or not isinstance(filters, SearchFilters):
        raise CatalogError("invalid_search_request")
    if type(limit) is not int or not 1 <= limit <= 100 or type(evidence_limit) is not int or not 1 <= evidence_limit <= 10:
        raise CatalogError("invalid_search_limit")


@dataclass(frozen=True)
class CatalogSnapshot:
    profile_id: str
    dataset_id: str
    revision: str
    manifest_sha256: str
    snapshot_created_on: date


@dataclass(frozen=True)
class Condition:
    field: str
    status: ConditionStatus
    reason_code: str


@dataclass(frozen=True)
class UnitEvidence:
    unit_key: str
    unit_name: str
    embed_text: str
    text_sha256: str
    unit_type: str
    ncs_unit_code: str | None
    source: str | None
    hours: float | None
    is_inferred: bool
    is_noise: bool
    provenance: JsonValue
    similarity: float


@dataclass(frozen=True)
class CourseCandidate:
    course_id: UUID
    offering_id: UUID
    source_system: Literal["work24"]
    source_course_id: str
    source_tme: str
    similarity: float
    metadata: Mapping[str, JsonValue]
    evidence: tuple[UnitEvidence, ...]
    conditions: tuple[Condition, ...]
    checked_at: None = None


@dataclass(frozen=True)
class SearchResult:
    snapshot: CatalogSnapshot
    as_of: date
    candidates: tuple[CourseCandidate, ...]
    active_offerings: int
    eligible_courses: int
    excluded_counts: Mapping[str, int]
    unsupported_conditions: tuple[str, ...]
    empty_reason: Literal["no_active_catalog", "all_filtered"] | None
    retrieval_config_version: str = "work24-exact-max-v1"
    retrieval_mode: Literal["exact"] = "exact"
    approximate: bool = False
