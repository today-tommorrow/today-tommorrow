"""Internal question-generation contract; persistence and HTTP routes belong to A."""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Slot = Literal[
    "entry_intent", "field_code", "custom_field", "experience", "previous_gap",
    "learning_goal", "availability", "delivery_mode", "commute_region",
    "budget_status", "budget_krw", "start_timing", "document_decision",
    "goal_text", "constraints_text", "interest_activity",
]


class QuestionError(ValueError):
    """Safe failure code for the caller; never includes model/user content."""


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnswerPatch(ContractModel):
    entry_intent: Literal["goal_known", "explore", "advance"] | None = None
    field_code: Literal[
        "ai_data", "software_cloud", "security_network", "robotics_manufacturing",
        "semiconductor", "digital_content", "other", "undecided",
    ] | None = None
    custom_field: str | None = None
    experience: Literal["beginner", "basic", "project", "work", "unknown"] | None = None
    previous_gap: Literal["application", "depth", "collaboration", "unknown"] | None = None
    learning_goal: Literal["foundation", "portfolio", "collaboration", "unknown"] | None = None
    availability: Literal["weekday_day", "evening_weekend", "flexible", "unknown"] | None = None
    delivery_mode: Literal["online", "offline", "hybrid", "any", "unknown"] | None = None
    commute_region: str | None = None
    budget_status: Literal["amount", "no_limit", "unknown"] | None = None
    budget_krw: Annotated[int, Field(ge=0)] | None = None
    start_timing: Literal["soon", "one_to_three_months", "browse", "unknown"] | None = None
    document_decision: Literal["provided", "none"] | None = None
    goal_text: Annotated[str, Field(max_length=2000)] | None = None
    constraints_text: Annotated[str, Field(max_length=2000)] | None = None
    interest_activity: str | None = None


class QuestionOption(ContractModel):
    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=160)]
    answer_patch: dict[Slot, str | int | None]


class QuestionDraft(ContractModel):
    schema_version: Literal["1.0"]
    target_slot: Slot
    acknowledgement: Annotated[str, Field(max_length=180)]
    question: Annotated[str, Field(min_length=1, max_length=180)]
    helper_text: Annotated[str, Field(max_length=240)]
    selection_mode: Literal["single"]
    options: Annotated[list[QuestionOption], Field(min_length=2, max_length=5)]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuestionError("invalid_question_json")
        result[key] = value
    return result


def _invalid_constant(value):
    raise QuestionError("invalid_question_json")


def validate_question(
    content: str,
    *,
    eligible_slots: set[str],
    companion_slots: dict[str, set[str]],
    confirmed_slots: set[str],
    resolved_unknown_slots: set[str],
) -> QuestionDraft:
    """Reject schema/policy violations before the caller assigns IDs or saves."""
    try:
        raw = json.loads(
            content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
        )
        draft = QuestionDraft.model_validate(raw)
        for option in draft.options:
            AnswerPatch.model_validate(option.answer_patch)
    except (ValueError, TypeError, ValidationError):
        raise QuestionError("invalid_question_json") from None
    target = draft.target_slot
    blocked = confirmed_slots | resolved_unknown_slots
    if target not in eligible_slots or target in blocked:
        raise QuestionError("ineligible_question_target")
    if not draft.question.strip():
        raise QuestionError("empty_question")
    keys = [option.key for option in draft.options]
    labels = [option.label.strip().casefold() for option in draft.options]
    if len(set(keys)) != len(keys) or len(set(labels)) != len(labels) or "" in labels:
        raise QuestionError("duplicate_or_empty_question_option")
    text = [draft.acknowledgement, draft.question, draft.helper_text]
    for option in draft.options:
        text.extend([option.label, option.description])
        text.extend(value for value in option.answer_patch.values()
                    if isinstance(value, str))
    if any(re.search(r"[a-z][a-z0-9+.-]*://|www\.|<[/!a-z]", value, re.I) for value in text):
        raise QuestionError("forbidden_question_content")
    allowed = {target} | companion_slots.get(target, set())
    nullable = {"custom_field", "previous_gap", "commute_region", "budget_krw",
                "goal_text", "constraints_text", "interest_activity"}
    patches: set[str] = set()
    for option in draft.options:
        patch = option.answer_patch
        if set(patch) - allowed or set(patch) & blocked:
            raise QuestionError("forbidden_question_patch")
        if target not in patch or patch[target] is None or (
            isinstance(patch[target], str) and not patch[target].strip()
        ):
            raise QuestionError("unresolved_question_target")
        if any(value is None and key not in nullable for key, value in patch.items()):
            raise QuestionError("invalid_question_patch")
        budget_status = patch.get("budget_status")
        if budget_status is not None:
            if patch.get("budget_krw") is not None and budget_status != "amount":
                raise QuestionError("inconsistent_budget_patch")
            if (budget_status == "amount" and "budget_krw" in allowed - blocked
                    and patch.get("budget_krw") is None):
                raise QuestionError("missing_budget_amount")
        # A null cap does not distinguish two choices with no budget amount.
        # Keep the original patch: nulls in other slots can explicitly clear data.
        comparison_patch = patch
        if budget_status in {"no_limit", "unknown"} and patch.get("budget_krw") is None:
            comparison_patch = {key: value for key, value in patch.items() if key != "budget_krw"}
        encoded = json.dumps(comparison_patch, sort_keys=True, ensure_ascii=False)
        if encoded in patches:
            raise QuestionError("duplicate_question_patch")
        patches.add(encoded)
    return draft
