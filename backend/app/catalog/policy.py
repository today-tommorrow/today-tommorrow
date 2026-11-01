"""Conservative filtering of observed snapshot values; no personal-cost guessing."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from uuid import UUID, uuid5

from .contracts import Condition, JsonValue, SearchFilters

# Versioned public identity namespace. Never change after publishing identifiers.
IDENTITY_NAMESPACE = UUID("a4b29d71-4f3e-50ab-9e95-dfba02578443")


def course_id(source_id: str) -> UUID:
    return uuid5(IDENTITY_NAMESPACE, json.dumps(["work24", source_id], ensure_ascii=False, separators=(",", ":")))


def offering_id(source_id: str, tme: str) -> UUID:
    return uuid5(IDENTITY_NAMESPACE, json.dumps(["work24", source_id, tme], ensure_ascii=False, separators=(",", ":")))


def source_date(value: JsonValue) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        # Source has calendar dates or naive ISO midnight timestamps, not UTC instants.
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(value).date() if value[10:11] == "T" else None
    except ValueError:
        return None


def evaluate(metadata: Mapping[str, JsonValue], filters: SearchFilters) -> tuple[Condition, ...]:
    conditions: list[Condition] = []
    raw_mode = metadata.get("mode")
    mode = {"온라인": "online", "서울 집체": "offline"}.get(raw_mode) if isinstance(raw_mode, str) else None
    desired = filters.delivery_mode
    if desired in (None, "any"):
        conditions.append(Condition("delivery_mode", "not_applicable", "NO_MODE_RESTRICTION"))
    elif desired == "unknown" or mode is None:
        conditions.append(Condition("delivery_mode", "unknown", "DELIVERY_MODE_UNKNOWN"))
    else:
        conditions.append(Condition("delivery_mode", "pass" if mode == desired else "fail", "LISTED_DELIVERY_MODE"))
    if filters.start_from or filters.start_to:
        start = source_date(metadata.get("start_date"))
        if start is None:
            conditions.append(Condition("start_date", "unknown", "START_DATE_UNKNOWN"))
        else:
            conflict = bool((filters.start_from and start < filters.start_from) or (filters.start_to and start > filters.start_to))
            conditions.append(Condition("start_date", "fail" if conflict else "pass", "EXPLICIT_START_DATE_RANGE"))
    if filters.listed_region is not None:
        region = metadata.get("region")
        if not isinstance(region, str) or not region.strip():
            conditions.append(Condition("listed_region", "unknown", "LISTED_REGION_UNKNOWN"))
        else:
            conditions.append(Condition("listed_region", "pass" if region == filters.listed_region else "fail", "LISTED_REGION_EXACT_ONLY"))
    if filters.budget_krw is not None:
        conditions.append(Condition("budget_krw", "unknown", "PERSONAL_PRICE_UNKNOWN"))
    if filters.availability not in (None, "flexible"):
        conditions.append(Condition("availability", "unknown", "ACTUAL_TIMETABLE_UNKNOWN"))
    if filters.field_code is not None:
        conditions.append(Condition("field_code", "unknown", "UNSUPPORTED_FIELD_MAPPING"))
    return tuple(conditions)
