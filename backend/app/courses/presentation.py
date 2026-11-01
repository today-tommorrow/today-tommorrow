"""Map observed source values without inventing personal eligibility or freshness."""

import math
from datetime import date, datetime
from urllib.parse import urlsplit

from .errors import CourseError
from .identity import course_id, identity, offering_id
from .schemas import CourseDetail, Evidence, Offering


def source_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def source_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CourseError(503, "catalog_invalid")
    return value


def source_number(value: object, *, integer: bool = False) -> int | float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return int(value) if integer and int(value) == value else None if integer else value


def source_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        if value[10:11] == "T":
            return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    return None


def source_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def common_text(metadata: list[dict[str, object]], key: str) -> str | None:
    values = {source_text(item.get(key)) for item in metadata}
    return values.pop() if len(values) == 1 else None


def present_course(source_id: str, courses: list[dict[str, object]], units: list[dict[str, object]], dataset_id: str) -> CourseDetail:
    records = [source_object(row["metadata"]) for row in courses]
    name = source_text(records[0].get("course_name"))
    if name is None:
        raise CourseError(503, "catalog_invalid")
    offerings: list[Offering] = []
    evidence: list[Evidence] = []
    urls: dict[str, str | None] = {}
    for row, metadata in zip(courses, records, strict=True):
        tme = row["tme"]
        if not isinstance(tme, str) or not tme:
            raise CourseError(503, "catalog_invalid")
        nested = metadata.get("metadata")
        nested = nested if isinstance(nested, dict) else {}
        url = source_url(metadata.get("url"))
        urls[tme] = url
        offerings.append(Offering(
            id=offering_id(source_id, tme), external_offering_id=tme, cohort=tme,
            start_date=source_date(metadata.get("start_date")), end_date=source_date(metadata.get("end_date")),
            delivery_mode={"온라인": "online", "서울 집체": "offline"}.get(str(metadata.get("mode")), "unknown"),
            region=source_text(metadata.get("region")), training_hours=source_number(metadata.get("training_hours")),
            tuition_krw=source_number(metadata.get("training_fee"), integer=True),
            listed_self_pay_krw=source_number(metadata.get("displayed_self_pay"), integer=True),
            eligibility_text=source_text(nested.get("훈련대상요건")),
        ))
        fields = {key: metadata.get(key) for key in (
            "course_name", "institution", "objective", "mode", "region", "start_date", "end_date",
            "training_hours", "training_fee", "displayed_self_pay", "prerequisites",
        )}
        fields["metadata.훈련대상요건"] = nested.get("훈련대상요건")
        for key, value in fields.items():
            quote = source_text(value)
            if quote is None and type(value) in (int, float) and math.isfinite(value):
                quote = str(value)
            if quote is not None:
                conflicting = key in {"institution", "objective", "prerequisites"} and common_text(records, key) is None and len(records) > 1
                evidence.append(Evidence(id=identity("evidence", dataset_id, source_id, tme, key), field_path=key,
                                         quote=quote, source_url=url, verification_state="conflicting" if conflicting else "observed"))
    for unit in units:
        tme, key, quote, inferred = unit["tme"], unit["unit_key"], unit["embed_text"], unit["is_inferred"]
        if not isinstance(tme, str) or not isinstance(key, str) or not isinstance(quote, str) or type(inferred) is not bool or unit["is_noise"] is not False:
            raise CourseError(503, "catalog_invalid")
        field = ("inferred_units." if inferred else "units.") + key
        evidence.append(Evidence(id=identity("evidence", dataset_id, source_id, tme, field), field_path=field, quote=quote,
                                 source_url=source_url(unit["source"]) or (None if inferred else urls[tme]),
                                 verification_state="unknown" if inferred else "observed"))
    description = common_text(records, "objective")
    prerequisites = common_text(records, "prerequisites")
    # Units and objectives are not explicit learning outcomes; never derive facts.
    outcomes = records[0].get("learning_outcomes")
    learning_outcomes = outcomes if isinstance(outcomes, list) and all(isinstance(item, str) for item in outcomes) and all(item.get("learning_outcomes") == outcomes for item in records) else []
    missing = ["field_code", "course_type", "checked_at"]
    for field, value in (("description", description), ("prerequisites", prerequisites), ("learning_outcomes", learning_outcomes), ("provider_name", common_text(records, "institution"))):
        if not value:
            missing.append(field)
    for offering in offerings:
        for key, value in offering.model_dump().items():
            if value is None or (key in {"delivery_mode", "recruitment_status"} and value == "unknown"):
                missing.append(f"offerings.{offering.id}.{key}")
    return CourseDetail(id=course_id(source_id), name=name, provider_name=common_text(records, "institution"),
                        detail_url=source_url(records[0].get("url")), description=description, prerequisites=prerequisites,
                        learning_outcomes=learning_outcomes, offerings=offerings, evidence=evidence, missing_fields=missing)
