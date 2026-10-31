from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.ai.questions import AnswerPatch, ContractModel

ShortText = Annotated[str, Field(max_length=2000)]
NULLABLE_ANSWERS = {
    "custom_field",
    "interest_activity",
    "previous_gap",
    "commute_region",
    "budget_krw",
    "goal_text",
    "constraints_text",
    "self_description",
}


class EligibilityContext(ContractModel):
    age: Annotated[int, Field(ge=0, le=120)] | None = None
    employment_status: (
        Literal["employed", "unemployed", "student", "self_employed", "unknown"] | None
    ) = None
    education: ShortText | None = None
    residence_region: ShortText | None = None


class Answers(AnswerPatch):
    custom_field: ShortText | None = None
    interest_activity: ShortText | None = None
    commute_region: ShortText | None = None
    field_confirmed: bool = False
    self_description: Annotated[str, Field(max_length=6000)] | None = None
    eligibility_context: EligibilityContext = Field(default_factory=EligibilityContext)

    @model_validator(mode="after")
    def nonnullable_present_fields(self):
        for name in self.model_fields_set - NULLABLE_ANSWERS:
            if getattr(self, name) is None:
                raise ValueError("null_not_allowed")
        return self


class ProfilePatch(ContractModel):
    expected_revision: Annotated[int, Field(ge=0, le=2_147_483_646)]
    answers: Answers


def profile_patch_schema() -> dict[str, Any]:
    """Expose the manually parsed bounded body without unresolved OpenAPI refs."""
    schema = ProfilePatch.model_json_schema()
    definitions = schema.pop("$defs")
    for key, prop in definitions["Answers"]["properties"].items():
        if key not in NULLABLE_ANSWERS and "anyOf" in prop:
            prop["anyOf"] = [
                item for item in prop["anyOf"] if item.get("type") != "null"
            ]
            prop.pop("default", None)

    def inline(value: Any) -> Any:
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return inline(schema)
