"""Validated public result contract and the lifecycle/runner boundary."""

import json
import math
from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type PhaseReporter = Callable[[str], None]


class RecommendationError(ValueError):
    """Safe machine code; never include model output or private input."""


class RecommendationRunner(Protocol):
    def capture_context(self) -> JsonObject: ...
    def run(self, snapshot: JsonObject, report_phase: PhaseReporter) -> JsonObject: ...


class ModelProvider(Protocol):
    model_id: str
    runtime_mode: Literal["real_model", "sample"]
    identity: JsonObject

    def generate(self, stage: str, context: JsonObject, schema: JsonObject) -> JsonObject: ...


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Evidence(StrictModel):
    id: str
    field_path: str
    quote: str | None
    source_url: str | None
    source_system: Literal["work24"]
    checked_at: str | None
    verification_state: Literal["observed", "verified", "unknown", "conflicting"]


class Course(StrictModel):
    id: str
    name: str = Field(min_length=1)
    provider_name: str | None
    field_code: str | None
    source_system: Literal["work24"]
    course_type: Literal["online_course", "vocational_training", "kdt", "unknown"]
    evidence_level: Literal["listing_only", "detail_verified"]
    detail_url: str | None
    is_demo: Literal[False]


class Offering(StrictModel):
    id: str
    external_offering_id: str | None
    cohort: str | None
    start_date: str | None
    end_date: str | None
    enrollment_start: str | None
    enrollment_end: str | None
    delivery_mode: Literal["online", "offline", "hybrid", "unknown"]
    region: str | None
    schedule: None
    training_hours: float | None
    tuition_krw: int | None
    listed_self_pay_krw: int | None
    eligibility_text: str | None
    recruitment_status: Literal["open", "closed", "unknown"]
    checked_at: str | None


class ConstraintCheck(StrictModel):
    field: Literal["recruitment", "schedule", "mode", "region", "budget", "eligibility", "start_timing"]
    status: Literal["pass", "fail", "unknown", "not_applicable"]
    reason_code: str
    evidence_ids: list[str]


class AxisAssessment(StrictModel):
    axis: Literal["goal", "prerequisite", "outcome", "preference"]
    value: Literal[0, 0.5, 1] | None
    evidence_ids: list[str]
    reason_code: str

    @field_validator("value", mode="before")
    @classmethod
    def not_boolean(cls, value):
        if type(value) is bool:
            raise ValueError("invalid_axis_value")
        return value


class RankBreakdown(StrictModel):
    goal_basis: Literal["content_supported", "topic_only"]
    score_lower: float = Field(ge=0, le=1)
    score_upper: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    axes: list[AxisAssessment] = Field(min_length=4, max_length=4)
    constraint_checks: list[ConstraintCheck] = Field(min_length=7, max_length=7)
    ranking_config_version: str


class Reason(StrictModel):
    text: str = Field(min_length=1)
    answer_keys: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class RecommendationItem(StrictModel):
    course: Course
    offering: Offering
    rank: int = Field(ge=1, le=5)
    match_state: Literal["verified_conditions", "unknown_conditions"]
    reasons: list[Reason] = Field(min_length=1, max_length=3)
    evidence: list[Evidence]
    missing_fields: list[str]
    rank_breakdown: RankBreakdown


class Need(StrictModel):
    id: str
    label: str
    basis: Literal["user_requested"]
    learner_state: Literal["self_reported_have", "want_to_learn", "unknown"]
    source_item_ids: list[str]
    ncs_reference_ids: list[str]
    mapping_state: Literal["supported", "unmapped"]


class Stage(StrictModel):
    stage: str
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    input_count: int | None
    output_count: int | None
    reason_counts: dict[str, int]
    duration_ms: int | None


class RunnerResult(StrictModel):
    """Lifecycle supplies IDs, ownership, input snapshot and creation time."""

    catalog_version_id: str
    result_state: Literal["matches", "unknown_conditions", "empty", "unsupported_field"]
    explanation_mode: Literal["llm", "none"]
    items: list[RecommendationItem] = Field(max_length=5)
    notices: list[str]
    excluded_counts: dict[str, int]
    model_id: str
    runtime_mode: Literal["real_model", "sample"]
    model_outcome: Literal["succeeded", "not_needed"]
    pipeline_version: str
    ranking_config_version: str
    retrieval_config_version: str
    as_of: str
    needs: list[Need]
    stages: list[Stage]

    @model_validator(mode="after")
    def consistent(self):
        if bool(self.items) != (self.result_state in {"matches", "unknown_conditions"}):
            raise ValueError("result_state_mismatch")
        if self.model_outcome == "not_needed" and self.items:
            raise ValueError("model_outcome_mismatch")
        if (self.explanation_mode == "none") != (not self.items):
            raise ValueError("explanation_mode_mismatch")
        UUID(self.catalog_version_id)
        seen: set[str] = set()
        has_unknown = False
        for rank, item in enumerate(self.items, 1):
            UUID(item.course.id)
            UUID(item.offering.id)
            if item.rank != rank or item.course.id in seen:
                raise ValueError("invalid_result_order")
            seen.add(item.course.id)
            checks = item.rank_breakdown.constraint_checks
            if len({check.field for check in checks}) != 7 or any(check.status == "fail" for check in checks):
                raise ValueError("invalid_result_conditions")
            unknown = any(check.status == "unknown" for check in checks)
            if unknown != (item.match_state == "unknown_conditions"):
                raise ValueError("invalid_match_state")
            has_unknown |= unknown
            evidence_ids = {e.id for e in item.evidence}
            if len(evidence_ids) != len(item.evidence):
                raise ValueError("duplicate_evidence")
            for evidence_id in evidence_ids:
                UUID(evidence_id)
            references = [*item.reasons, *item.rank_breakdown.axes, *checks]
            if any(not set(ref.evidence_ids) <= evidence_ids for ref in references):
                raise ValueError("invalid_evidence_reference")
            axes = {axis.axis: axis for axis in item.rank_breakdown.axes}
            if set(axes) != set(WEIGHTS):
                raise ValueError("invalid_axes")
            lower, upper, coverage = score_bounds({name: axis.value for name, axis in axes.items()})
            if any(abs(left - right) > 1e-9 for left, right in zip(
                (lower, upper, coverage),
                (item.rank_breakdown.score_lower, item.rank_breakdown.score_upper, item.rank_breakdown.coverage),
                strict=True,
            )):
                raise ValueError("invalid_score")
        if self.items and has_unknown != (self.result_state == "unknown_conditions"):
            raise ValueError("invalid_result_conditions")
        return self


WEIGHTS = {"goal": 0.45, "prerequisite": 0.25, "outcome": 0.2, "preference": 0.1}


def score_bounds(values: dict[str, float | None]) -> tuple[float, float, float]:
    if set(values) != set(WEIGHTS) or any(type(v) is bool or v not in (None, 0, 0.5, 1) for v in values.values()):
        raise RecommendationError("invalid_assessment")
    lower = math.fsum(WEIGHTS[key] * value for key, value in values.items() if value is not None)
    unknown = math.fsum(WEIGHTS[key] for key, value in values.items() if value is None)
    coverage = math.fsum(WEIGHTS[key] for key, value in values.items() if value is not None)
    return round(lower, 10), round(lower + unknown, 10), round(coverage, 10)


def validate_result(result: JsonObject) -> JsonObject:
    """Validate injected runners too; preserve internal provenance separately."""
    try:
        internal = result.get("_snapshot")
        payload = RunnerResult.model_validate({key: value for key, value in result.items() if key != "_snapshot"})
        output = payload.model_dump(mode="json")
        if internal is not None:
            json.dumps(internal, allow_nan=False)
            output["_snapshot"] = internal
        return output
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise RecommendationError("invalid_recommendation_result") from None
