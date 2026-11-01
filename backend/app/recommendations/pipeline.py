"""Document-free, evidence-bound recommendation processing after final submission."""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import date
from time import monotonic
from uuid import uuid5

from app.catalog.contracts import (
    CatalogSnapshot,
    CourseCandidate,
    SearchFilters,
    SearchResult,
    json_value,
)
from app.catalog.policy import IDENTITY_NAMESPACE, source_date
from app.catalog.service import SearchService

from .contracts import (
    JsonObject,
    JsonValue,
    ModelProvider,
    PhaseReporter,
    RecommendationError,
    score_bounds,
    validate_result,
)

PIPELINE_VERSION = "work24-recommendation-1"
RANKING_VERSION = "grounded-interval-dense-1"
RETRIEVAL_VERSION = "work24-exact-max-v1"
FIELD_LABELS = {
    "ai_data": "인공지능 데이터 분석", "software_cloud": "소프트웨어 클라우드",
    "security_network": "정보보안 네트워크", "robotics_manufacturing": "로봇 제조",
    "semiconductor": "반도체", "digital_content": "디지털 콘텐츠",
}
GOAL_LABELS = {"foundation": "기초 학습", "portfolio": "프로젝트 결과물", "collaboration": "협업 실습"}
CONFIG = {
    "pipeline_version": PIPELINE_VERSION, "ranking_config_version": RANKING_VERSION,
    "retrieval_config_version": RETRIEVAL_VERSION, "candidate_limit": 10,
    "batch_size": 1, "evidence_limit": 3, "excerpt_chars": 160,
    "result_limit": 5, "provider_cap": 2, "repair_attempts": 1,
    "query_version": "confirmed-wording-1",
}


def object_value(value: JsonValue | object) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RecommendationError("invalid_recommendation_input")
    return value


def array_value(value: JsonValue | object) -> list[JsonValue]:
    if not isinstance(value, list):
        raise RecommendationError("invalid_recommendation_input")
    return value


def text_value(value: JsonValue | object, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RecommendationError("invalid_recommendation_input")
    return value


def fingerprint(value: JsonObject) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise RecommendationError("invalid_recommendation_input") from None


def identity(*parts: str) -> str:
    return str(uuid5(IDENTITY_NAMESPACE, json.dumps(parts, ensure_ascii=False, separators=(",", ":"))))


def snapshot_json(snapshot: CatalogSnapshot) -> JsonObject:
    return object_value(json_value(snapshot))


def object_schema(properties: JsonObject) -> JsonObject:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def list_schema(items: JsonObject, maximum: int, minimum: int = 0) -> JsonObject:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


STRING: JsonObject = {"type": "string", "minLength": 1}
PROFILE_SCHEMA = object_schema({"needs": list_schema(object_schema({
    "source_id": STRING, "label": STRING,
}), 8, 1)})
ASSESSMENT_SCHEMA = object_schema({"assessments": list_schema(object_schema({
    "course_id": STRING, "offering_id": STRING,
    "goal_basis": {"type": "string", "enum": ["content_supported", "topic_only", "unrelated"]},
    "axes": list_schema(object_schema({
        "axis": {"type": "string", "enum": ["goal", "prerequisite", "outcome"]},
        "value": {"enum": [0, 0.5, 1, None]},
        "evidence_ids": list_schema(STRING, 3), "quotes": list_schema(STRING, 3),
    }), 3, 3),
}), 2, 1)})
EXPLANATION_SCHEMA = object_schema({"explanations": list_schema(object_schema({
    "course_id": STRING, "offering_id": STRING,
    "claims": list_schema(object_schema({
        "source_id": STRING, "evidence_id": STRING, "quote": STRING,
    }), 3, 1),
}), 2, 1)})


def exact_keys(value: JsonObject, keys: set[str]) -> None:
    if set(value) != keys:
        raise RecommendationError("invalid_model_response")


def confirmed_sources(snapshot: JsonObject) -> tuple[list[JsonObject], str]:
    answers = object_value(snapshot.get("answers"))
    if answers.get("document_decision") != "none" or snapshot.get("reviewed_items"):
        raise RecommendationError("recommendation_materials_unsupported")
    parts: list[tuple[str, str]] = []
    for key in ("goal_text", "interest_activity", "custom_field"):
        if answers.get(key):
            parts.append((key, text_value(answers[key])))
    for item in array_value(snapshot.get("selection_summaries", [])):
        selected = object_value(item)
        slot = selected.get("target_slot")
        if slot in {"learning_goal", "field_code", "interest_activity", "custom_field"}:
            parts.append((str(slot), text_value(selected.get("label"))))
    if not any(key in {"field_code", "custom_field"} for key, _ in parts):
        field = FIELD_LABELS.get(str(answers.get("field_code")))
        if field:
            parts.append(("field_code", field))
    goal = GOAL_LABELS.get(str(answers.get("learning_goal")))
    if goal and not any(key == "learning_goal" for key, _ in parts):
        parts.append(("learning_goal", goal))
    if not parts:
        raise RecommendationError("recommendation_goal_missing")
    # Preserve confirmed wording. No generated query, synonym rewrite or stored-vector substitute.
    unique = list(dict.fromkeys(parts))
    sources = [{"id": identity("user-source", str(snapshot.get("profile_id")), str(snapshot.get("profile_revision")), key, text),
                "answer_key": key, "text": text} for key, text in unique]
    query = "\n".join(text for _, text in unique)
    if len(query.encode()) > 10000:
        raise RecommendationError("recommendation_input_too_large")
    return sources, query


def profile_needs(output: JsonObject, sources: list[JsonObject]) -> list[JsonObject]:
    exact_keys(output, {"needs"})
    source_map = {str(item["id"]): item for item in sources}
    rows = array_value(output["needs"])
    if not 1 <= len(rows) <= 8:
        raise RecommendationError("invalid_model_response")
    needs: list[JsonObject] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        item = object_value(row)
        exact_keys(item, {"source_id", "label"})
        source = source_map.get(str(item["source_id"]))
        label = text_value(item["label"], maximum=300)
        if source is None or label not in str(source["text"]) or (str(source["id"]), label) in seen:
            raise RecommendationError("invalid_model_grounding")
        seen.add((str(source["id"]), label))
        needs.append({"id": identity("need", str(source["id"]), label), "label": label,
                      "basis": "user_requested", "learner_state": "want_to_learn",
                      "source_item_ids": [source["id"]], "ncs_reference_ids": [], "mapping_state": "unmapped"})
    return needs


def metadata_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def number_value(value: object, *, integer: bool = False) -> int | float | None:
    if type(value) not in (int, float) or value < 0:
        return None
    if integer:
        return int(value) if int(value) == value else None
    return value


def safe_url(value: object) -> str | None:
    from urllib.parse import urlsplit
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def candidate_view(candidate: CourseCandidate, catalog: CatalogSnapshot) -> JsonObject:
    metadata = candidate.metadata
    name = metadata_text(metadata.get("course_name"))
    if not name:
        raise RecommendationError("invalid_catalog_metadata")
    nested = metadata.get("metadata")
    nested = nested if isinstance(nested, Mapping) else {}
    url = safe_url(metadata.get("url"))
    mode = {"온라인": "online", "서울 집체": "offline"}.get(str(metadata.get("mode")), "unknown")
    evidence: list[JsonObject] = []

    def add_evidence(key: str, text: str | None, kind: str, inferred: bool = False) -> None:
        if not text:
            return
        excerpt = text[:int(CONFIG["excerpt_chars"])]
        evidence.append({
            "id": identity("evidence", catalog.dataset_id, candidate.source_course_id, candidate.source_tme, key),
            "field_path": key, "quote": excerpt, "source_url": url, "source_system": "work24",
            "checked_at": None, "verification_state": "unknown" if inferred else "observed", "kind": kind,
        })

    add_evidence("course_name", name, "title")
    add_evidence("objective", metadata_text(metadata.get("objective")), "content")
    add_evidence("metadata.훈련대상요건", metadata_text(nested.get("훈련대상요건")), "prerequisite")
    add_evidence("mode", metadata_text(metadata.get("mode")), "mode")
    for unit in candidate.evidence:
        prefix = "inferred_units" if unit.is_inferred else "units"
        add_evidence(f"{prefix}.{unit.unit_key}", unit.embed_text, "inferred" if unit.is_inferred else "content", unit.is_inferred)
    start, end = source_date(metadata.get("start_date")), source_date(metadata.get("end_date"))
    return {
        "course": {"id": str(candidate.course_id), "name": name,
                   "provider_name": metadata_text(metadata.get("institution")), "field_code": None,
                   "source_system": "work24", "course_type": "unknown", "evidence_level": "listing_only",
                   "detail_url": url, "is_demo": False},
        "offering": {"id": str(candidate.offering_id), "external_offering_id": candidate.source_tme,
                     "cohort": candidate.source_tme, "start_date": str(start) if start else None,
                     "end_date": str(end) if end else None, "enrollment_start": None, "enrollment_end": None,
                     "delivery_mode": mode, "region": metadata_text(metadata.get("region")), "schedule": None,
                     "training_hours": number_value(metadata.get("training_hours")),
                     "tuition_krw": number_value(metadata.get("training_fee"), integer=True),
                     "listed_self_pay_krw": number_value(metadata.get("displayed_self_pay"), integer=True),
                     "eligibility_text": metadata_text(nested.get("훈련대상요건")),
                     "recruitment_status": "unknown", "checked_at": None},
        "evidence": evidence, "similarity": candidate.similarity,
    }


def conditions(candidate: CourseCandidate, view: JsonObject, answers: JsonObject) -> list[JsonObject]:
    by_field = {item.field: item for item in candidate.conditions}
    mode = by_field.get("delivery_mode")
    evidence = [object_value(row) for row in array_value(view["evidence"])]
    mode_refs = [item["id"] for item in evidence if item["kind"] == "mode"]
    checks: list[JsonObject] = []

    def add(field: str, status: str, reason: str, ids: list[JsonValue] | None = None) -> None:
        checks.append({"field": field, "status": status, "reason_code": reason, "evidence_ids": ids or []})

    add("mode", mode.status if mode else "unknown", mode.reason_code if mode else "DELIVERY_MODE_UNKNOWN",
        mode_refs if mode and mode.status == "pass" else [])
    add("recruitment", "unknown", "CURRENT_RECRUITMENT_UNVERIFIED")
    add("schedule", "not_applicable" if answers.get("availability") == "flexible" else "unknown", "ACTUAL_TIMETABLE_UNKNOWN")
    online = object_value(view["offering"])["delivery_mode"] == "online"
    add("region", "not_applicable" if online else "unknown", "NO_COMMUTE" if online else "COURSE_LOCATION_UNKNOWN")
    add("budget", "not_applicable" if answers.get("budget_status") == "no_limit" else "unknown", "PERSONAL_PRICE_UNKNOWN")
    add("eligibility", "unknown", "PARTICIPATION_ELIGIBILITY_UNVERIFIED")
    add("start_timing", "not_applicable" if answers.get("start_timing") == "browse" else "unknown", "START_PREFERENCE_NOT_HARD_RANGE")
    return checks


def candidate_prompt(view: JsonObject) -> JsonObject:
    return {"course_id": object_value(view["course"])["id"], "offering_id": object_value(view["offering"])["id"],
            "evidence": [{key: value for key, value in object_value(e).items() if key in {"id", "quote", "kind"}}
                         for e in array_value(view["evidence"])]}


def assessments(output: JsonObject, views: list[JsonObject], *, experience_known: bool = False) -> list[JsonObject]:
    exact_keys(output, {"assessments"})
    expected = {str(object_value(view["course"])["id"]): view for view in views}
    rows = array_value(output["assessments"])
    if len(rows) != len(expected):
        raise RecommendationError("invalid_model_grounding")
    validated: list[JsonObject] = []
    seen: set[str] = set()
    for row in rows:
        item = object_value(row)
        exact_keys(item, {"course_id", "offering_id", "goal_basis", "axes"})
        cid = str(item["course_id"])
        view = expected.get(cid)
        if view is None or cid in seen or item["offering_id"] != object_value(view["offering"])["id"]:
            raise RecommendationError("invalid_model_grounding")
        seen.add(cid)
        if item["goal_basis"] not in {"content_supported", "topic_only", "unrelated"}:
            raise RecommendationError("invalid_model_response")
        sources = {str(object_value(e)["id"]): object_value(e) for e in array_value(view["evidence"])}
        axes: dict[str, JsonObject] = {}
        for raw_axis in array_value(item["axes"]):
            axis = object_value(raw_axis)
            exact_keys(axis, {"axis", "value", "evidence_ids", "quotes"})
            name = str(axis["axis"])
            value = axis["value"]
            if name not in {"goal", "prerequisite", "outcome"} or name in axes or type(value) is bool or value not in (None, 0, .5, 1):
                raise RecommendationError("invalid_model_response")
            ids, quotes = array_value(axis["evidence_ids"]), array_value(axis["quotes"])
            if len(ids) != len(quotes) or len(ids) > 3 or len(set(map(str, ids))) != len(ids):
                raise RecommendationError("invalid_model_grounding")
            if (value is None and ids) or (value is not None and not ids):
                raise RecommendationError("invalid_model_grounding")
            for eid, quote in zip(ids, quotes, strict=True):
                source = sources.get(str(eid))
                if source is None or not isinstance(quote, str) or len(quote.strip()) < 4 or quote not in str(source["quote"]):
                    raise RecommendationError("invalid_model_grounding")
                if name == "prerequisite" and source["kind"] != "prerequisite":
                    raise RecommendationError("invalid_model_grounding")
                if name == "prerequisite" and not experience_known:
                    raise RecommendationError("invalid_model_grounding")
            axes[name] = {"axis": name, "value": value, "evidence_ids": ids,
                          "reason_code": "EVIDENCE_MISSING" if value is None else "MODEL_GROUNDED_ASSESSMENT"}
        if set(axes) != {"goal", "prerequisite", "outcome"}:
            raise RecommendationError("invalid_model_response")
        goal = axes["goal"]
        if item["goal_basis"] != "unrelated" and goal["value"] not in (.5, 1):
            raise RecommendationError("invalid_model_grounding")
        if item["goal_basis"] == "content_supported" and not any(
            sources[str(eid)]["kind"] == "content" for eid in array_value(goal["evidence_ids"])
        ):
            raise RecommendationError("invalid_model_grounding")
        checks = [object_value(c) for c in array_value(view["checks"])]
        mode = next(check for check in checks if check["field"] == "mode")
        preference = 1 if mode["status"] == "pass" else None
        axes["preference"] = {"axis": "preference", "value": preference,
                              "evidence_ids": mode["evidence_ids"] if preference is not None else [],
                              "reason_code": "CONFIRMED_MODE_PREFERENCE" if preference else "PREFERENCE_UNKNOWN"}
        bounds = score_bounds({key: axis["value"] for key, axis in axes.items()})
        validated.append({**view, "goal_basis": item["goal_basis"], "axes": list(axes.values()),
                          "score_lower": bounds[0], "score_upper": bounds[1], "coverage": bounds[2],
                          "raw_assessment": item})
    return validated


def ranked_selection(rows: list[JsonObject]) -> tuple[list[JsonObject], dict[str, int]]:
    def sort_key(row: JsonObject) -> tuple:
        return (0 if row["goal_basis"] == "content_supported" else 1,
                -float(row["score_lower"]), -float(row["coverage"]),
                sum(object_value(check)["status"] == "unknown" for check in array_value(row["checks"])),
                -float(row["similarity"]), str(object_value(row["course"])["id"]),
                str(object_value(row["offering"])["id"]))
    kept: list[JsonObject] = []
    reasons: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    courses: set[str] = set()
    for row in sorted(rows, key=sort_key):
        course = object_value(row["course"])
        cid = str(course["id"])
        if row["goal_basis"] == "unrelated":
            reasons["UNRELATED_CONTENT"] += 1
        elif any(object_value(check)["status"] == "fail" for check in array_value(row["checks"])):
            reasons["CONFIRMED_CONDITION_FAILURE"] += 1
        elif cid in courses:
            reasons["DUPLICATE_COURSE"] += 1
        else:
            # An unknown institution is not evidence that different courses share a provider.
            provider = str(course["provider_name"]) if course["provider_name"] else f"unknown:{cid}"
            if providers[provider] >= int(CONFIG["provider_cap"]):
                reasons["PROVIDER_CAP"] += 1
            elif len(kept) < int(CONFIG["result_limit"]):
                providers[provider] += 1
                courses.add(cid)
                kept.append(row)
    return kept, dict(reasons)


def explanation_items(output: JsonObject, selected: list[JsonObject], sources: list[JsonObject]) -> dict[str, list[JsonObject]]:
    exact_keys(output, {"explanations"})
    expected = {str(object_value(row["course"])["id"]): row for row in selected}
    source_map = {str(row["id"]): row for row in sources}
    rows = array_value(output["explanations"])
    if len(rows) != len(expected):
        raise RecommendationError("invalid_model_grounding")
    result: dict[str, list[JsonObject]] = {}
    for raw in rows:
        row = object_value(raw)
        exact_keys(row, {"course_id", "offering_id", "claims"})
        cid = str(row["course_id"])
        candidate = expected.get(cid)
        if candidate is None or cid in result or row["offering_id"] != object_value(candidate["offering"])["id"]:
            raise RecommendationError("invalid_model_grounding")
        evidence = {str(object_value(e)["id"]): object_value(e) for e in array_value(candidate["evidence"])}
        assessed_refs = {
            str(eid) for axis in array_value(candidate["axes"])
            if object_value(axis)["axis"] in {"goal", "outcome"}
            for eid in array_value(object_value(axis)["evidence_ids"])
        }
        claims = array_value(row["claims"])
        if not 1 <= len(claims) <= 3:
            raise RecommendationError("invalid_model_response")
        reasons: list[JsonObject] = []
        for raw_claim in claims:
            claim = object_value(raw_claim)
            exact_keys(claim, {"source_id", "evidence_id", "quote"})
            source, proof = source_map.get(str(claim["source_id"])), evidence.get(str(claim["evidence_id"]))
            quote = text_value(claim["quote"], maximum=int(CONFIG["excerpt_chars"]))
            if source is None or proof is None or str(proof["id"]) not in assessed_refs or len(quote.strip()) < 4 or quote not in str(proof["quote"]):
                raise RecommendationError("invalid_model_grounding")
            # Model selects evidence. The display grammar cannot introduce prices, dates or verified conditions.
            kind = "추정 참고 자료" if proof["kind"] == "inferred" else "교육 원문"
            reasons.append({"text": f'학습 목표 “{source["text"]}”와 관련해 {kind}의 “{quote}”를 참고했습니다.',
                            "answer_keys": [source["answer_key"]], "evidence_ids": [proof["id"]]})
        result[cid] = reasons
    return result


def public_item(row: JsonObject, reasons: list[JsonObject], rank: int) -> JsonObject:
    checks = array_value(row["checks"])
    unknown = [str(object_value(check)["field"]) for check in checks if object_value(check)["status"] == "unknown"]
    return {
        "course": row["course"], "offering": row["offering"], "rank": rank,
        "match_state": "unknown_conditions" if unknown else "verified_conditions",
        "reasons": reasons,
        "evidence": [{key: value for key, value in object_value(item).items() if key != "kind"}
                     for item in array_value(row["evidence"])],
        "missing_fields": unknown,
        "rank_breakdown": {"goal_basis": row["goal_basis"], "score_lower": row["score_lower"],
                           "score_upper": row["score_upper"], "coverage": row["coverage"],
                           "axes": row["axes"], "constraint_checks": checks, "ranking_config_version": RANKING_VERSION},
    }


class RecommendationPipeline:
    def __init__(self, search: SearchService, model: ModelProvider) -> None:
        self.search, self.model = search, model

    def capture_context(self) -> JsonObject:
        snapshot = self.search.repository.prepare()
        return {"catalog": snapshot_json(snapshot), "config": dict(CONFIG),
                "model": dict(self.model.identity), "runtime_mode": self.model.runtime_mode}

    def _generate(self, stage: str, context: JsonObject, schema: JsonObject, validator, report: PhaseReporter):
        for attempt in range(int(CONFIG["repair_attempts"]) + 1):
            report(stage)
            try:
                generated = self.model.generate(stage, context, schema)
                validated = validator(generated)
                report(stage)  # Fence lease/cancellation immediately after blocking HTTP.
                return validated
            except RecommendationError as error:
                if str(error) not in {"invalid_model_response", "invalid_model_grounding", "invalid_recommendation_input"} or attempt:
                    raise
                context = {**context, "validation_error": "Invalid structure or unsupported evidence. Return only the allowed grounded fields."}
        raise RecommendationError("invalid_model_response")

    def run(self, snapshot: JsonObject, report_phase: PhaseReporter) -> JsonObject:
        started = monotonic()
        context = object_value(snapshot.get("context"))
        if fingerprint(context) != fingerprint(self.capture_context()):
            raise RecommendationError("recommendation_context_changed")
        try:
            as_of = date.fromisoformat(str(snapshot["as_of"]))
        except (KeyError, ValueError):
            raise RecommendationError("invalid_recommendation_input") from None
        sources, query = confirmed_sources(snapshot)
        answers = object_value(snapshot["answers"])
        stages: list[JsonObject] = []

        def stage(name: str, count_in: int | None, count_out: int | None, *, skipped: bool = False) -> None:
            stages.append({"stage": name, "status": "skipped" if skipped else "succeeded",
                           "input_count": count_in, "output_count": count_out, "reason_counts": {}, "duration_ms": None})

        report_phase("retrieving")
        filters = SearchFilters(delivery_mode=answers.get("delivery_mode"),
                                budget_krw=answers.get("budget_krw") if answers.get("budget_status") == "amount" else None,
                                availability=answers.get("availability"))
        found: SearchResult = self.search.search(query, as_of=as_of, filters=filters,
                                                limit=int(CONFIG["candidate_limit"]), evidence_limit=int(CONFIG["evidence_limit"]))
        if snapshot_json(found.snapshot) != context["catalog"]:
            raise RecommendationError("recommendation_context_changed")
        report_phase("checking_constraints")
        views = []
        for candidate in found.candidates:
            view = candidate_view(candidate, found.snapshot)
            view["checks"] = conditions(candidate, view, answers)
            views.append(view)
        stage("retrieving", found.active_offerings, len(views))
        stage("checking_constraints", len(views), len(views))
        needs: list[JsonObject] = []
        assessed: list[JsonObject] = []
        selected: list[JsonObject] = []
        explanations: dict[str, list[JsonObject]] = {}
        excluded = dict(found.excluded_counts)
        if views:
            needs = self._generate("profiling", {"sources": sources}, PROFILE_SCHEMA,
                                   lambda output: profile_needs(output, sources), report_phase)
            stage("profiling", len(sources), len(needs))
            report_phase("mapping_needs")
            stage("mapping_needs", len(needs), len(needs))
            # Batch boundaries affect request size only; every candidate uses the same rubric and frozen inputs.
            for offset in range(0, len(views), int(CONFIG["batch_size"])):
                batch = views[offset:offset + int(CONFIG["batch_size"])]
                assessed.extend(self._generate("assessing", {
                    "sources": sources, "needs": needs,
                    "confirmed_experience": answers.get("experience"),
                    "self_description": answers.get("self_description"),
                    "constraints_text": answers.get("constraints_text"),
                    "candidates": [candidate_prompt(view) for view in batch],
                }, ASSESSMENT_SCHEMA, lambda output: assessments(
                    output, batch, experience_known=bool(answers.get("self_description"))
                ), report_phase))
            stage("assessing", len(views), len(assessed))
            report_phase("ranking")
            selected, selection_excluded = ranked_selection(assessed)
            excluded = dict(Counter(excluded) + Counter(selection_excluded))
            stage("ranking", len(assessed), len(selected))
            for offset in range(0, len(selected), int(CONFIG["batch_size"])):
                batch = selected[offset:offset + int(CONFIG["batch_size"])]
                explanations.update(self._generate("explaining", {
                    "sources": sources, "candidates": [candidate_prompt(row) for row in batch],
                }, EXPLANATION_SCHEMA, lambda output: explanation_items(output, batch, sources), report_phase))
            stage("explaining", len(selected), len(explanations), skipped=not selected)
        else:
            for skipped in ("profiling", "mapping_needs", "assessing", "ranking", "explaining"):
                stage(skipped, 0, 0, skipped=True)
        report_phase("validating")
        items = [public_item(row, explanations[str(object_value(row["course"])["id"])], rank)
                 for rank, row in enumerate(selected, 1)]
        state = "unknown_conditions" if any(item["match_state"] == "unknown_conditions" for item in items) else "matches"
        if not items:
            # A bounded candidate set cannot establish that a field is absent from the entire catalog.
            state = "empty"
        stage("validating", len(items), len(items))
        catalog_id = identity("catalog", found.snapshot.dataset_id, found.snapshot.profile_id, found.snapshot.manifest_sha256)
        result: JsonObject = {
            "catalog_version_id": catalog_id, "result_state": state,
            "explanation_mode": "llm" if items else "none", "items": items,
            "notices": ["고용24 저장 자료를 기준으로 검색했습니다. 실제 모집 여부·개인 부담금·참여 자격은 확인이 필요합니다.",
                        "내부 순위 점수는 역량·취업 확률이 아닙니다. 추정 자료는 실제 교육내용과 구분해 표시합니다."],
            "excluded_counts": excluded, "model_id": self.model.model_id, "runtime_mode": self.model.runtime_mode,
            "model_outcome": "succeeded" if views else "not_needed", "pipeline_version": PIPELINE_VERSION,
            "ranking_config_version": RANKING_VERSION, "retrieval_config_version": RETRIEVAL_VERSION,
            "as_of": f"{as_of.isoformat()}T00:00:00Z", "needs": needs, "stages": stages,
            "_snapshot": {"context": context, "query": query, "sources": sources,
                          "search": json_value(found), "assessments": assessed,
                          "explanations": explanations, "model_calls_performed": bool(views),
                          "duration_ms": int((monotonic() - started) * 1000)},
        }
        return validate_result(result)
